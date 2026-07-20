import logging
from typing import Dict, List
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from enum import Enum
import pandas as pd

logger = logging.getLogger(__name__)

class FetchFailureType(Enum):
    SUCCESS = "SUCCESS"
    INVALID_SYMBOL = "INVALID_SYMBOL"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    RATE_LIMIT = "RATE_LIMIT"
    NETWORK_ERROR = "NETWORK_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    PROVIDER_INCOMPLETE_RESPONSE = "PROVIDER_INCOMPLETE_RESPONSE"
    INVALID_BSE_FALLBACK = "INVALID_BSE_FALLBACK"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"

from dataclasses import dataclass

@dataclass
class ProviderResult:
    provider: str
    status: FetchFailureType

class DeadSymbolDecision(Enum):
    ALIVE = "ALIVE"
    RETRY = "RETRY"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"

def classify_symbol(results: list[ProviderResult]) -> DeadSymbolDecision:
    explicit_invalid_providers = {
        p.provider for p in results if p.status == FetchFailureType.INVALID_SYMBOL
    }
    
    if len(explicit_invalid_providers) >= 2:
        return DeadSymbolDecision.DEAD
        
    if any(p.status == FetchFailureType.SUCCESS for p in results):
        return DeadSymbolDecision.ALIVE
        
    attempted = [p for p in results if p.status != FetchFailureType.NOT_ATTEMPTED]
    if len(attempted) > 0:
        return DeadSymbolDecision.RETRY
        
    return DeadSymbolDecision.UNKNOWN

_dead_symbols_cache = {}
_DEAD_TTL = 3600 * 24  # 24 hours
_MAX_DEAD_CACHE_SIZE = 1000

def _cleanup_dead_symbols_cache():
    now = time.time()
    # 1. Remove expired
    expired_keys = [k for k, v in _dead_symbols_cache.items() if now - v > _DEAD_TTL]
    for k in expired_keys:
        del _dead_symbols_cache[k]
        
    # 2. If still over limit, remove oldest (Python 3.7+ dicts preserve insertion order)
    if len(_dead_symbols_cache) > _MAX_DEAD_CACHE_SIZE:
        excess = len(_dead_symbols_cache) - _MAX_DEAD_CACHE_SIZE
        oldest_keys = list(_dead_symbols_cache.keys())[:excess]
        for k in oldest_keys:
            del _dead_symbols_cache[k]
        logger.info(f"🧹 Evicted {len(expired_keys)} expired and {len(oldest_keys)} oldest entries from _dead_symbols_cache.")

def _parse_yf_error(err_str: str) -> FetchFailureType:
    err_str = str(err_str).lower()
    if "delisted" in err_str or "no data found" in err_str or "no timezone found" in err_str:
        return FetchFailureType.INVALID_SYMBOL
    if "rate limit" in err_str or "429" in err_str or "too many requests" in err_str:
        return FetchFailureType.RATE_LIMIT
    return FetchFailureType.UNKNOWN_ERROR

def get_live_prices(symbols: List[str]) -> Dict[str, float]:
    """
    Fetches real-time Last Traded Price (CMP) for a list of standard NSE symbols (e.g., ['CAMS', 'KRBL']).
    Primary Source: Fyers Broker API (batch quotes up to 50 symbols).
    Fallback Source: yfinance fast_info engine (concurrent execution).
    """
    if not symbols:
        return {}

    now = time.time()
    valid_symbols = []
    for s in symbols:
        if s in _dead_symbols_cache and (now - _dead_symbols_cache[s]) < _DEAD_TTL:
            continue
        valid_symbols.append(s)
        
    if not valid_symbols:
        return {}

    prices = {}
    missing = list(valid_symbols)
    symbol_status = {s: {} for s in valid_symbols}

    # ── 1. Attempt Primary Fetch (Fyers) ────────────────────────────────────────────────
    try:
        from fyers_auth import get_fyers_client
        fyers = get_fyers_client()
        if fyers is not None:
            # Chunk symbols into blocks of 50 (Fyers API Limit)
            chunk_size = 50
            for i in range(0, len(symbols), chunk_size):
                chunk = symbols[i:i + chunk_size]
                
                try:
                    from bse_mapping_utils import load_bse_mappings
                    mappings = load_bse_mappings()
                except Exception:
                    mappings = {}

                from data_providers.fyers_fetcher import FyersFetcher
                try:
                    from data_providers.fyers_mapping_utils import is_fyers_invalid
                except Exception:
                    is_fyers_invalid = lambda s: False

                fyers_fetcher = FyersFetcher()
                fyers_symbols = []
                reverse_map = {}
                
                for sym in chunk:
                    orig_clean = sym.strip().upper()
                    if orig_clean.endswith(".NS"): orig_clean = orig_clean[:-3]
                    
                    if is_fyers_invalid(orig_clean):
                        symbol_status[sym]["Fyers"] = FetchFailureType.INVALID_SYMBOL
                        continue
                        
                    f_sym = fyers_fetcher._normalize_symbol(sym)
                    if f_sym:
                        fyers_symbols.append(f_sym)
                        reverse_map[f_sym] = sym
                    else:
                        symbol_status[sym]["Fyers"] = FetchFailureType.INVALID_SYMBOL
                
                if not fyers_symbols:
                    continue
                
                try:
                    missing_count_before = len(missing)
                    response = fyers.quotes({"symbols": ",".join(fyers_symbols)})
                    if response and isinstance(response, dict) and response.get("s") == "ok":
                        data_list = response.get("d", [])
                        for item in data_list:
                            f_sym = item.get("n", "")
                            original_sym = reverse_map.get(f_sym)
                            if not original_sym: continue
                            
                            if item.get("s") == "error":
                                if item.get("v", {}).get("code") == -300:
                                    symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.INVALID_SYMBOL
                                else:
                                    symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.PROVIDER_INCOMPLETE_RESPONSE
                            elif item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                                lp = item["v"]["lp"]
                                if lp and lp > 0:
                                    prices[original_sym] = float(lp)
                                    symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.SUCCESS
                                    if original_sym in missing:
                                        missing.remove(original_sym)
                                else:
                                    symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.PROVIDER_INCOMPLETE_RESPONSE
                            else:
                                symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.PROVIDER_INCOMPLETE_RESPONSE
                        
                        for f_sym in fyers_symbols:
                            original_sym = reverse_map.get(f_sym)
                            if original_sym and symbol_status[original_sym].get("Fyers NSE", FetchFailureType.NOT_ATTEMPTED) == FetchFailureType.NOT_ATTEMPTED:
                                symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.PROVIDER_INCOMPLETE_RESPONSE
                        
                        missing_chunk = [s for s in chunk if s in missing]
                        if len(missing_chunk) > 0:
                            # Some were missing. Log the raw response so we can RCA.
                            logger.warning(f"⚠️ Fyers quote returned 'ok' but missing data/prices for {missing_chunk}. Raw Fyers response: {response}")
                    else:
                        is_auth_error = False
                        if isinstance(response, dict):
                            err_msg = str(response.get("message", "")).lower()
                            if response.get("code") == -15 or "valid token" in err_msg:
                                is_auth_error = True
                                
                        if is_auth_error:
                            logger.error("⚠️ Fyers authentication failed. All Fyers requests are expected to fail until the token is refreshed.")
                            try:
                                import fyers_auth
                                fyers_auth.clear_token()
                            except Exception:
                                pass
                            for f_sym in fyers_symbols:
                                original_sym = reverse_map.get(f_sym)
                                if original_sym: symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.AUTHENTICATION_ERROR
                        else:
                            logger.error(f"❌ Fyers API returned error payload for batch {i//chunk_size}: {response}")
                            for f_sym in fyers_symbols:
                                original_sym = reverse_map.get(f_sym)
                                if original_sym: symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.UNKNOWN_ERROR
                except Exception as e:
                    logger.error(f"⚠️ Fyers quote fetch failed for batch {i//chunk_size}: {e}")
                    for f_sym in fyers_symbols:
                        original_sym = reverse_map.get(f_sym)
                        if original_sym: symbol_status[original_sym]["Fyers NSE"] = FetchFailureType.NETWORK_ERROR
                    
    except ImportError:
        logger.warning("⚠️ fyers_auth not found, falling back strictly to yfinance.")
    except Exception as e:
        logger.warning(f"⚠️ Fyers client initialization failed: {e}")

    # ── 2. Attempt Fallback Fetch (Yahoo Finance fast_info) ───────────────────────────
    if missing:
        logger.info(f"🔄 Fyers fallback triggered. Fetching {len(missing)} symbols via yfinance batch download for {missing}...")
        try:
            from bse_mapping_utils import load_bse_mappings
            mappings = load_bse_mappings()
        except Exception:
            mappings = {}
            
        yf_symbols = []
        yf_reverse_map = {}
        for sym in missing:
            clean_upper = sym.strip().upper()
            
            # Determine mapping logic
            mapped_reason = "Default NSE"
            is_bse = False
            
            if clean_upper in mappings or (clean_upper.endswith(".NS") and clean_upper[:-3] in mappings):
                is_bse = True
                mapped_reason = "BSE Mapping Cache"
            elif sym.isdigit() or sym.endswith(".BO") or sym.startswith("BSE:"):
                is_bse = True
                mapped_reason = "Explicit BSE Suffix"

            clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
            suffix = ".BO" if is_bse else ".NS"
            yf_sym = f"{clean}{suffix}"
            
            logger.info(f"Mapping Decision | Orig: {sym} -> Selected: {yf_sym} | Reason: {mapped_reason}")
            
            yf_symbols.append(yf_sym)
            yf_reverse_map[yf_sym] = sym
            
        try:
            import yfinance as yf
            chunk_size = 100
            for i in range(0, len(yf_symbols), chunk_size):
                chunk = yf_symbols[i:i+chunk_size]
                
                if hasattr(yf, 'shared') and hasattr(yf.shared, '_ERRORS'):
                    yf.shared._ERRORS.clear()
                    
                from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit
                is_rate_limit = False
                dl_exception = False
                try:
                    yf_acquire(context=f"LivePrices.yfinance_fallback | batch {i//chunk_size}")
                    df = yf.download(" ".join(chunk), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True)
                except Exception as dl_err:
                    msg = str(dl_err).lower()
                    if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                        record_rate_limit(context=f"LivePrices.yfinance_fallback | batch {i//chunk_size}")
                        is_rate_limit = True
                    logger.warning(f"⚠️ YFinance download failed for live_prices batch {i//chunk_size}: {dl_err}")
                    dl_exception = True
                    df = pd.DataFrame()
                finally:
                    yf_release()
                
                if hasattr(yf, 'shared') and hasattr(yf.shared, '_ERRORS'):
                    for yf_err_sym, err_msg in yf.shared._ERRORS.items():
                        orig = yf_reverse_map.get(yf_err_sym)
                        if orig: 
                            symbol_status[orig]["YF_NS"] = _parse_yf_error(err_msg)
                            # [VERSION: INVALIDATION_FIX] Invalidate mapping if yfinance directly reports failure
                            if yf_err_sym.endswith(".BO"):
                                logger.info(f"Provider: Yahoo Finance | Ticker: {yf_err_sym} | Result: {err_msg}. Invalidating BSE mapping.")
                                try:
                                    from bse_mapping_utils import invalidate_bse_mapping
                                    invalidate_bse_mapping(orig)
                                except Exception:
                                    pass
                
                if dl_exception:
                    for y_sym in chunk:
                        orig = yf_reverse_map.get(y_sym)
                        if orig and "YF_NS" not in symbol_status[orig]:
                            symbol_status[orig]["YF_NS"] = FetchFailureType.RATE_LIMIT if is_rate_limit else FetchFailureType.NETWORK_ERROR
                else:
                    if len(chunk) == 1:
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(1)
                        if not df.empty and "Close" in df.columns:
                            val = float(df["Close"].iloc[-1])
                            if val > 0:
                                prices[yf_reverse_map[chunk[0]]] = val
                                logger.info(f"✅ Successfully fetched Live Price for {chunk[0]} via Yahoo Finance (NS)")
                                symbol_status[yf_reverse_map[chunk[0]]]["YF_NS"] = FetchFailureType.SUCCESS
                            else:
                                logger.info(f"Provider: Yahoo NS | Ticker: {chunk[0]} | df.empty: {df.empty} | shape: {df.shape} | all NaN: {df.isna().all().all()} | error: 'Close <= 0'")
                        else:
                            logger.info(f"Provider: Yahoo NS | Ticker: {chunk[0]} | df.empty: {df.empty} | shape: {df.shape} | all NaN: {df.isna().all().all() if not df.empty else 'N/A'} | error: 'Empty DataFrame'")
                            if chunk[0].endswith(".BO"):
                                logger.info(f"Provider: Yahoo Finance | Ticker: {chunk[0]} | Result: Empty DataFrame. Invalidating BSE mapping.")
                                try:
                                    from bse_mapping_utils import invalidate_bse_mapping
                                    orig = yf_reverse_map.get(chunk[0])
                                    if orig: invalidate_bse_mapping(orig)
                                except Exception: pass
                    else:
                        if not hasattr(df.columns, 'levels'):
                            logger.warning(f"⚠️ yf.download returned flat (non-MultiIndex) columns for multi-ticker batch {i//chunk_size}.")
                        else:
                            for y_sym in chunk:
                                try:
                                    if y_sym in df.columns.levels[0]:
                                        if df[y_sym].empty:
                                            logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | Result: Empty Dataset (0 rows). Invalidating if cached.")
                                            try:
                                                from bse_mapping_utils import invalidate_bse_mapping
                                                orig = yf_reverse_map.get(y_sym)
                                                if orig: invalidate_bse_mapping(orig)
                                            except Exception: pass
                                            continue
                                        
                                        val = float(df[y_sym]["Close"].iloc[-1])
                                        if val > 0:
                                            prices[yf_reverse_map[y_sym]] = val
                                            logger.info(f"✅ Successfully fetched Live Price for {y_sym} via Yahoo Finance (NS)")
                                            symbol_status[yf_reverse_map[y_sym]]["YF_NS"] = FetchFailureType.SUCCESS
                                        else:
                                            logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | df.empty: {df[y_sym].empty} | shape: {df[y_sym].shape} | error: 'Close <= 0'")
                                    else:
                                        logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | df.empty: True | error: 'Missing in MultiIndex'")
                                except Exception as parse_e:
                                    logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | Parse Error: {parse_e}")
                                    pass
                                    
                for y_sym in chunk:
                    orig = yf_reverse_map.get(y_sym)
                    if orig and orig not in prices and "YF_NS" not in symbol_status[orig]:
                        symbol_status[orig]["YF_NS"] = FetchFailureType.EMPTY_RESPONSE
                                
            # ── Dynamic BSE Fallback for missing symbols ──────────────────────────
            still_missing = [s for s in missing if s not in prices]
            bse_fallback_symbols = []
            bse_reverse_map = {}
            for sym in still_missing:
                first_tested = None
                for y_sym, orig in yf_reverse_map.items():
                    if orig == sym:
                        first_tested = y_sym
                        break
                clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
                bse_sym = f"{clean}.NS" if first_tested and first_tested.endswith(".BO") else f"{clean}.BO"
                bse_fallback_symbols.append(bse_sym)
                bse_reverse_map[bse_sym] = sym
                
            if bse_fallback_symbols:
                logger.info(f"🔄 YFinance BSE fallback triggered for {len(bse_fallback_symbols)} unreturned symbols...")
                try:
                    from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit
                    if hasattr(yf, 'shared') and hasattr(yf.shared, '_ERRORS'):
                        yf.shared._ERRORS.clear()
                        
                    is_rate_limit = False
                    dl_exception = False
                    try:
                        yf_acquire(context="LivePrices.yfinance_fallback_BSE")
                        df_bse = yf.download(" ".join(bse_fallback_symbols), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True)
                    except Exception as dl_err:
                        msg = str(dl_err).lower()
                        if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                            record_rate_limit(context="LivePrices.yfinance_fallback_BSE")
                            is_rate_limit = True
                        dl_exception = True
                        df_bse = pd.DataFrame()
                    finally:
                        yf_release()
                        
                    if hasattr(yf, 'shared') and hasattr(yf.shared, '_ERRORS'):
                        for yf_err_sym, err_msg in yf.shared._ERRORS.items():
                            orig = bse_reverse_map.get(yf_err_sym)
                            if orig: symbol_status[orig]["YF_BO"] = _parse_yf_error(err_msg)
                            
                    if dl_exception:
                        for y_sym in bse_fallback_symbols:
                            orig = bse_reverse_map.get(y_sym)
                            if orig and "YF_BO" not in symbol_status[orig]:
                                symbol_status[orig]["YF_BO"] = FetchFailureType.RATE_LIMIT if is_rate_limit else FetchFailureType.NETWORK_ERROR
                    else:
                        if len(bse_fallback_symbols) == 1:
                            if isinstance(df_bse.columns, pd.MultiIndex):
                                df_bse.columns = df_bse.columns.get_level_values(1)
                            if not df_bse.empty and "Close" in df_bse.columns:
                                val = float(df_bse["Close"].iloc[-1])
                                if val > 0:
                                    orig_sym = bse_reverse_map[bse_fallback_symbols[0]]
                                    prices[orig_sym] = val
                                    logger.info(f"✅ Successfully fetched Live Price for {bse_fallback_symbols[0]} via Yahoo Finance (BO)")
                                    symbol_status[orig_sym]["YF_BO"] = FetchFailureType.SUCCESS
                                    try:
                                        from bse_mapping_utils import save_bse_mapping
                                        save_bse_mapping(orig_sym, bse_fallback_symbols[0])
                                    except Exception: pass
                                else:
                                    logger.info(f"Provider: Yahoo BO | Ticker: {bse_fallback_symbols[0]} | df.empty: {df_bse.empty} | shape: {df_bse.shape} | all NaN: {df_bse.isna().all().all()} | error: 'Close <= 0'")
                            else:
                                logger.info(f"Provider: Yahoo BO | Ticker: {bse_fallback_symbols[0]} | df.empty: {df_bse.empty} | shape: {df_bse.shape} | all NaN: {df_bse.isna().all().all() if not df_bse.empty else 'N/A'} | error: 'Empty DataFrame'")
                        else:
                            if hasattr(df_bse.columns, 'levels'):
                                for y_sym in bse_fallback_symbols:
                                    try:
                                        if y_sym in df_bse.columns.levels[0]:
                                            if df_bse[y_sym].empty:
                                                logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | Result: Empty Dataset (0 rows). Invalidating if cached.")
                                                try:
                                                    from bse_mapping_utils import invalidate_bse_mapping
                                                    orig_sym = bse_reverse_map[y_sym]
                                                    if orig_sym: invalidate_bse_mapping(orig_sym)
                                                except Exception: pass
                                                continue
                                                
                                            val = float(df_bse[y_sym]["Close"].iloc[-1])
                                            if val > 0:
                                                orig_sym = bse_reverse_map[y_sym]
                                                prices[orig_sym] = val
                                                logger.info(f"✅ Successfully fetched Live Price for {y_sym} via Yahoo Finance (BO)")
                                                symbol_status[orig_sym]["YF_BO"] = FetchFailureType.SUCCESS
                                                try:
                                                    from bse_mapping_utils import save_bse_mapping
                                                    save_bse_mapping(orig_sym, y_sym)
                                                except Exception: pass
                                            else:
                                                logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | df.empty: {df_bse[y_sym].empty} | shape: {df_bse[y_sym].shape} | error: 'Close <= 0'")
                                        else:
                                            logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | df.empty: True | error: 'Missing in MultiIndex'")
                                    except Exception as parse_e:
                                        logger.info(f"Provider: Yahoo Finance | Ticker: {y_sym} | Parse Error: {parse_e}")
                                        pass
                                    
                    for y_sym in bse_fallback_symbols:
                        orig = bse_reverse_map.get(y_sym)
                        if orig and orig not in prices and "YF_BO" not in symbol_status[orig]:
                            symbol_status[orig]["YF_BO"] = FetchFailureType.EMPTY_RESPONSE
                except Exception as e:
                    logger.warning(f"BSE fallback block failed: {e}")
                            
        except Exception as e:
            logger.exception(f"⚠️ YFinance batch fallback failed: {e}")
            
    # ── 3. Decision Logic for Missing Symbols ─────────────────────────────────────────
    final_missing = [s for s in missing if s not in prices]
    for s in final_missing:
        statuses = symbol_status.get(s, {})
        fyers_status = statuses.get("Fyers", FetchFailureType.UNKNOWN_ERROR)
        yf_ns_status = statuses.get("YF_NS", FetchFailureType.UNKNOWN_ERROR)
        yf_bo_status = statuses.get("YF_BO", FetchFailureType.UNKNOWN_ERROR)
        
        # Determine if failure is permanent
        invalid_count = sum(1 for st in (fyers_status, yf_ns_status, yf_bo_status) if st == FetchFailureType.INVALID_SYMBOL)

        if invalid_count >= 2:
            _cleanup_dead_symbols_cache()
            _dead_symbols_cache[s] = time.time()
            logger.warning(
                f"🚫 Marking {s} as completely DEAD for 24h\n"
                f"  Decision: DEAD_SYMBOL\n"
                f"  Fyers: {fyers_status.name}\n"
                f"  YFinance NS: {yf_ns_status.name}\n"
                f"  YFinance BO: {yf_bo_status.name}"
            )
        else:
            logger.warning(
                f"⚠️ {s} fetch failed, but NOT marking as DEAD due to temporary errors.\n"
                f"  Decision: Temporary Failure (Retry later)\n"
                f"  Fyers: {fyers_status.name}\n"
                f"  YFinance NS: {yf_ns_status.name}\n"
                f"  YFinance BO: {yf_bo_status.name}"
            )
            
    return prices
