import logging
from typing import Dict, List
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

import time

logger = logging.getLogger(__name__)

_dead_symbols_cache = {}
_DEAD_TTL = 3600 * 24  # 24 hours

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
                        continue
                        
                    f_sym = fyers_fetcher._normalize_symbol(sym)
                    if f_sym:
                        fyers_symbols.append(f_sym)
                        reverse_map[f_sym] = sym
                
                if not fyers_symbols:
                    continue
                
                try:
                    response = fyers.quotes({"symbols": ",".join(fyers_symbols)})
                    if response and isinstance(response, dict) and response.get("s") == "ok":
                        data_list = response.get("d", [])
                        for item in data_list:
                            if item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                                f_sym = item.get("n", "")
                                original_sym = reverse_map.get(f_sym)
                                lp = item["v"]["lp"]
                                if original_sym and lp and lp > 0:
                                    prices[original_sym] = float(lp)
                                    if original_sym in missing:
                                        missing.remove(original_sym)
                except Exception as e:
                    logger.warning(f"⚠️ Fyers quote fetch failed for batch {i//chunk_size}: {e}")
                    
                # ── Dynamic Fyers -BE Fallback for missing symbols ──────────
                missing_fyers = [s for s in chunk if s in missing]
                be_symbols = []
                be_reverse_map = {}
                
                for m_sym in missing_fyers:
                    f_sym = fyers_fetcher._normalize_symbol(m_sym)
                    if f_sym and f_sym.endswith("-EQ"):
                        be_sym = f_sym.replace("-EQ", "-BE")
                        be_symbols.append(be_sym)
                        be_reverse_map[be_sym] = m_sym
                        
                if be_symbols:
                    try:
                        response_be = fyers.quotes({"symbols": ",".join(be_symbols)})
                        if response_be and isinstance(response_be, dict) and response_be.get("s") == "ok":
                            for item in response_be.get("d", []):
                                if item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                                    be_sym = item.get("n", "")
                                    original_sym = be_reverse_map.get(be_sym)
                                    lp = item["v"]["lp"]
                                    if original_sym and lp and lp > 0:
                                        prices[original_sym] = float(lp)
                                        if original_sym in missing:
                                            missing.remove(original_sym)
                                        # Persist -BE mapping
                                        try:
                                            from data_providers.fyers_mapping_utils import save_fyers_mapping
                                            orig_clean = original_sym.strip().upper()
                                            if orig_clean.endswith(".NS"): orig_clean = orig_clean[:-3]
                                            save_fyers_mapping(orig_clean, be_sym)
                                        except Exception: pass
                    except Exception as e:
                        logger.warning(f"⚠️ Fyers -BE fallback failed: {e}")
                    
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
            is_bse = (
                clean_upper in mappings or
                (clean_upper.endswith(".NS") and clean_upper[:-3] in mappings) or
                sym.isdigit() or sym.endswith(".BO") or sym.startswith("BSE:")
            )
            clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
            suffix = ".BO" if is_bse else ".NS"
            yf_sym = f"{clean}{suffix}"
            yf_symbols.append(yf_sym)
            yf_reverse_map[yf_sym] = sym
            
        try:
            # Batch fetch all at once, chunking by 100 to be safe
            import yfinance as yf
            chunk_size = 100
            for i in range(0, len(yf_symbols), chunk_size):
                chunk = yf_symbols[i:i+chunk_size]
                
                # [VERSION: LIVE_PRICES_RATE_LIMIT_FIX_v1.0]
                # Wrap yf.download with the global rate limiter so that this fallback
                # path is visible to the circuit breaker. Use threads=False to prevent
                # yfinance from spawning its own threads that bypass the semaphore.
                from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit
                try:
                    yf_acquire(context=f"LivePrices.yfinance_fallback | batch {i//chunk_size}")
                    df = yf.download(" ".join(chunk), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True)
                except Exception as dl_err:
                    msg = str(dl_err).lower()
                    if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                        record_rate_limit(context=f"LivePrices.yfinance_fallback | batch {i//chunk_size}")
                    logger.warning(f"⚠️ YFinance download failed for live_prices batch {i//chunk_size}: {dl_err}")
                    continue
                finally:
                    yf_release()
                
                if len(chunk) == 1:
                    # Single ticker returns flat columns: Open, High, Low, Close
                    if not df.empty and "Close" in df.columns:
                        val = float(df["Close"].iloc[-1])
                        if val > 0:
                            prices[yf_reverse_map[chunk[0]]] = val
                else:
                    # Multiple tickers: yfinance returns MultiIndex columns
                    # [BUG-5 FIX] Guard against flat columns when all tickers fail
                    if not hasattr(df.columns, 'levels'):
                        logger.warning(f"⚠️ yf.download returned flat (non-MultiIndex) columns for multi-ticker batch {i//chunk_size}. All symbols may have failed.")
                    else:
                        for y_sym in chunk:
                            try:
                                if y_sym in df.columns.levels[0]:
                                    val = float(df[y_sym]["Close"].iloc[-1])
                                    if val > 0:
                                        prices[yf_reverse_map[y_sym]] = val
                            except Exception as inner_e:
                                logger.warning(f"Failed to parse yf batch price for {y_sym}: {inner_e}")
                                
            # ── Dynamic BSE Fallback for missing symbols ──────────────────────────
            still_missing = [s for s in missing if s not in prices]
            bse_fallback_symbols = []
            bse_reverse_map = {}
            for sym in still_missing:
                clean = sym.replace("BSE:", "").replace("NSE:", "").replace(".NS", "").replace(".BO", "")
                bse_sym = f"{clean}.BO"
                bse_fallback_symbols.append(bse_sym)
                bse_reverse_map[bse_sym] = sym
                
            if bse_fallback_symbols:
                logger.info(f"🔄 YFinance BSE fallback triggered for {len(bse_fallback_symbols)} unreturned symbols...")
                try:
                    from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit
                    try:
                        yf_acquire(context="LivePrices.yfinance_fallback_BSE")
                        df_bse = yf.download(" ".join(bse_fallback_symbols), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True)
                    except Exception as dl_err:
                        msg = str(dl_err).lower()
                        if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                            record_rate_limit(context="LivePrices.yfinance_fallback_BSE")
                        df_bse = pd.DataFrame()
                    finally:
                        yf_release()
                        
                    if len(bse_fallback_symbols) == 1:
                        if not df_bse.empty and "Close" in df_bse.columns:
                            val = float(df_bse["Close"].iloc[-1])
                            if val > 0:
                                orig_sym = bse_reverse_map[bse_fallback_symbols[0]]
                                prices[orig_sym] = val
                                try:
                                    from bse_mapping_utils import save_bse_mapping
                                    save_bse_mapping(orig_sym, bse_fallback_symbols[0])
                                except Exception: pass
                    else:
                        if hasattr(df_bse.columns, 'levels'):
                            for y_sym in bse_fallback_symbols:
                                try:
                                    if y_sym in df_bse.columns.levels[0]:
                                        val = float(df_bse[y_sym]["Close"].iloc[-1])
                                        if val > 0:
                                            orig_sym = bse_reverse_map[y_sym]
                                            prices[orig_sym] = val
                                            try:
                                                from bse_mapping_utils import save_bse_mapping
                                                save_bse_mapping(orig_sym, y_sym)
                                            except Exception: pass
                                except Exception: pass
                except Exception as e:
                    logger.warning(f"BSE fallback block failed: {e}")
                            
        except Exception as e:
            logger.exception(f"⚠️ YFinance batch fallback failed: {e}")
            
    final_missing = [s for s in missing if s not in prices]
    for s in final_missing:
        _dead_symbols_cache[s] = time.time()
        logger.warning(f"🚫 Marking {s} as completely DEAD for 24h (failed Fyers, YFinance NS, and YFinance BO)")
            
    return prices
