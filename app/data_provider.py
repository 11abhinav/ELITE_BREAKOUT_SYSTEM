import pandas as pd
from abc import ABC, abstractmethod
import yfinance as yf
import time
import random
import logging

from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, get_backoff_delay, CircuitOpenError

from price_provider import PriceProvider
from config import BATCH_DOWNLOAD_SIZE, PRICE_CACHE_TTL_SECONDS
from core_enums import ProviderResult
from validation import ValidationEngine, MarketData, ValidationContext, registry as val_registry, DatasetType
logger = logging.getLogger(__name__)

# Module-level shared provider to ensure cache is reused across fetcher instances
_price_provider = PriceProvider(batch_size=BATCH_DOWNLOAD_SIZE, cache_ttl=PRICE_CACHE_TTL_SECONDS, yf_threads=True)

class DataFetcher(ABC):
    @abstractmethod
    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        """Fetch OHLCV data for a single symbol."""
        pass

    @abstractmethod
    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict:
        """Fetch OHLCV data for multiple symbols simultaneously. Returns dict of symbol to DataFrame or ProviderResult."""
        pass

    @abstractmethod
    def get_quote(self, symbol: str) -> dict:
        """Fetch current quote for a symbol."""
        pass


class YFinanceFetcher(DataFetcher):
    def _normalize_symbol(self, symbol: str) -> str:
        # [VERSION: NULL_POINTER_FIX_v1.0]
        if not symbol:
            return ""
        # [VERSION: DATA_PROV_SYMBOL_FIX_v1.2] Support persistent BSE symbol mappings to avoid redundant NSE failures
        try:
            from bse_mapping_utils import load_bse_mappings
            mappings = load_bse_mappings()
            upper_sym = str(symbol).strip().upper()
            if upper_sym in mappings and mappings[upper_sym]:
                return mappings[upper_sym]
            if upper_sym.endswith(".NS") and upper_sym[:-3] in mappings:
                return mappings[upper_sym[:-3]]
        except Exception as e:
            logger.warning(f"Error loading BSE mappings in _normalize_symbol: {e}")

        # [VERSION: DATA_PROV_SYMBOL_FIX_v1.1] Support both NSE and BSE symbols dynamically.
        # Check if the symbol is a BSE symbol (ends with .BO, starts with BSE:, or is completely numeric)
        is_bse = symbol.endswith(".BO") or symbol.startswith("BSE:")
        
        if symbol.endswith(".NS"):
            base_sym = symbol[:-3]
        elif symbol.endswith(".BO"):
            base_sym = symbol[:-3]
            is_bse = True
        else:
            base_sym = symbol

        if base_sym.startswith("BSE:"):
            base_sym = base_sym[4:]
            is_bse = True
        elif base_sym.startswith("NSE:"):
            base_sym = base_sym[4:]
            is_bse = False

        if base_sym.isdigit():
            is_bse = True
            
        # Strip broker series suffixes if present before appending .NS or .BO
        for suffix in ("-EQ", "-BE", "-SM", "-ST", "-A", "-B", "-T", "-M", "-X", "-XC", "-XD", "-XT"):
            if base_sym.endswith(suffix):
                base_sym = base_sym[:-len(suffix)]
                break
                
        if base_sym.isdigit():
            is_bse = True
        
        # Fix ampersand symbols.
        from daily_builder import SYMBOL_CORRECTIONS
        STALE_MAP = {
            "TATAMOTORS": "TMCV",
            "M-M": "M&M",
            "M-MFIN": "M&MFIN",
            "J-KBANK": "J&KBANK",
            "GVT-D": "GVT&D",
            "L-TFH": "L&TFH",
            "T-IPOWER": "T&IPOWER",
        }
        
        if base_sym in SYMBOL_CORRECTIONS:
            base_sym = SYMBOL_CORRECTIONS[base_sym]
        elif base_sym in STALE_MAP:
            base_sym = STALE_MAP[base_sym]
        else:
            # handle underscore to hyphen conversion commonly needed for yfinance
            base_sym = base_sym.replace("_", "-")
            
        if base_sym.startswith("^"):
            return base_sym
        return f"{base_sym}.BO" if is_bse else f"{base_sym}.NS"

    # [VERSION: YF_DYNAMIC_BSE_FALLBACK_v1.0] Helper to perform the raw single download
    def _get_ohlcv_raw(self, ns_sym: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        for attempt in range(retries):
            try:
                # Add night buffer to avoid rate limits at 1 AM
                from zoneinfo import ZoneInfo
                from datetime import datetime
                IST = ZoneInfo("Asia/Kolkata")
                now = datetime.now(IST)
                if 0 <= now.hour <= 6:
                    time.sleep(1.5)

                # Respect global Yahoo rate limiter (may raise CircuitOpenError)
                yf_acquire(context=f"DataFetcher._get_ohlcv_raw | {ns_sym}")
                try:
                    if range_from and range_to:
                        from datetime import datetime, timedelta
                        try:
                            start_date = range_from
                            end_dt = datetime.strptime(range_to, "%Y-%m-%d") + timedelta(days=1)
                            end_date = end_dt.strftime("%Y-%m-%d")
                            df = yf.download(ns_sym, interval=interval, start=start_date, end=end_date, progress=False, auto_adjust=True, threads=False, timeout=60)
                        except Exception as e:
                            df = yf.download(ns_sym, interval=interval, period=period, progress=False, auto_adjust=True, threads=False, timeout=60)
                    else:
                        df = yf.download(ns_sym, interval=interval, period=period, progress=False, auto_adjust=True, threads=False, timeout=60)
                finally:
                    yf_release()

                if df is not None and not df.empty:
                    # Flatten MultiIndex if it exists
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                        
                    # [VERSION: DOWNCAST] Force float32 precision for memory savings
                    import numpy as np
                    for numeric_col in df.columns:
                        if pd.api.types.is_numeric_dtype(df[numeric_col]) and df[numeric_col].dtype == 'float64':
                            df[numeric_col] = df[numeric_col].astype(np.float32)
                            
                    # Reset index so 'Date' or 'Datetime' is a column
                    df = df.reset_index().copy()
                    return df
            except CircuitOpenError as ce:
                logger.error(f"YFinance circuit open; aborting fetch for {ns_sym}: {ce}")
                return None
            except Exception as e:
                msg = str(e).lower()
                is_rate = ('too many requests' in msg) or ('rate limit' in msg) or ('429' in msg)
                logger.warning(f"Error fetching OHLCV for {ns_sym} (attempt {attempt+1}/{retries}): {e}")
                
                if is_rate and attempt == retries - 1:
                    return ProviderResult.RATE_LIMIT
                    
                import random
                time.sleep(random.uniform(1.0, 3.0))
        logger.error(f"❌ Exhausted retries fetching {ns_sym}")
        return ProviderResult.NETWORK_ERROR

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> MarketData:
        if not symbol:
            return MarketData(None, "UNKNOWN", None, False, False, "No symbol")
        ns_sym = self._normalize_symbol(symbol)
        if not ns_sym:
            return MarketData(None, "UNKNOWN", None, False, False, "Normalization failed")
            
        logger.debug(f"📥 Fetching OHLCV for {symbol} ({interval}, {period}) via YFinance...")
        df = self._get_ohlcv_raw(ns_sym, interval, period, retries, range_from, range_to)
        
        used_fallback = False
        source = "NSE" if not ns_sym.endswith(".BO") else "BSE"
        report = None
        
        should_fallback = False
        if isinstance(df, ProviderResult) or df is None or getattr(df, 'empty', True):
            should_fallback = True
        else:
            pipeline = val_registry.get_pipeline(DatasetType.PRICE)
            engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
            ctx = ValidationContext(provider="NSE", period=period, interval=interval, range_from=range_from, range_to=range_to, fetch_mode="DELTA" if range_from else "FULL")
            report = engine.validate(df, ctx)
            if not report.is_valid:
                logger.warning(f"NSE Data Quality Rejected for {symbol} (Score: {report.quality_score}, Reasons: {report.critical_failures})")
                should_fallback = True

        if should_fallback and ns_sym.endswith(".NS"):
            bse_sym = ns_sym[:-3] + ".BO"
            logger.info(f"🔄 NSE fetch failed or poor quality for {symbol}. Retrying with BSE symbol {bse_sym}...")
            bse_df = self._get_ohlcv_raw(bse_sym, interval, period, retries, range_from, range_to)
            used_fallback = True
            
            if not isinstance(bse_df, ProviderResult) and bse_df is not None and not getattr(bse_df, 'empty', True):
                pipeline = val_registry.get_pipeline(DatasetType.PRICE)
                engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
                ctx = ValidationContext(provider="BSE", period=period, interval=interval, range_from=range_from, range_to=range_to, fetch_mode="DELTA" if range_from else "FULL")
                bse_report = engine.validate(bse_df, ctx)
                if bse_report.is_valid:
                    df = bse_df
                    report = bse_report
                    source = "BSE"
                    try:
                        from bse_mapping_utils import save_bse_mapping
                        save_bse_mapping(symbol, bse_sym)
                    except Exception:
                        pass
                else:
                    logger.warning(f"BSE Fallback Quality Rejected for {symbol} (Score: {bse_report.quality_score})")
            
        if isinstance(df, ProviderResult):
            return MarketData(None, source, None, False, used_fallback, error=df.name)
            
        if report is None:
            pipeline = val_registry.get_pipeline(DatasetType.PRICE)
            engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
            ctx = ValidationContext(provider=source, period=period, interval=interval, range_from=range_from, range_to=range_to, fetch_mode="DELTA" if range_from else "FULL")
            report = engine.validate(df, ctx)
            
        return MarketData(df if report.is_valid else None, source, report, False, used_fallback, None if report.is_valid else "Quality Check Failed")

    # [VERSION: YF_DYNAMIC_BSE_FALLBACK_v1.0] Helper to perform the raw batch download
    def _fetch_batch_raw(self, ns_symbols: list[str], period: str, interval: str, range_from: str = None, range_to: str = None) -> dict:
        provider = _price_provider
        try:
            from zoneinfo import ZoneInfo
            from datetime import datetime
            IST = ZoneInfo("Asia/Kolkata")
            now = datetime.now(IST)
            if 0 <= now.hour <= 6:
                time.sleep(1.5)

            start_date = None
            end_date = None
            if range_from and range_to:
                from datetime import datetime, timedelta
                try:
                    start_date = range_from
                    end_dt = datetime.strptime(range_to, "%Y-%m-%d") + timedelta(days=1)
                    end_date = end_dt.strftime("%Y-%m-%d")
                except Exception as e:
                    logger.warning(f"Error parsing range dates: {e}")
                    start_date, end_date = None, None

            return provider.fetch_batch(ns_symbols, period=period, interval=interval, start=start_date, end=end_date)
        except Exception as e:
            if "Circuit open" in str(e):
                logger.warning(f"🚫 YFinance Circuit Breaker is OPEN. Skipping YFinance batch fetch for {len(ns_symbols)} symbols: {e}")
            else:
                logger.error(f"❌ Raw batch provider fetch failed for YFinance: {e}", exc_info=True)
            return {}

    # [VERSION: YF_DYNAMIC_BSE_FALLBACK_v1.0] Helper to format/clean retrieved df
    def _clean_df(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(1)
            df = df.reset_index().copy()
            if getattr(df, 'attrs', {}).get('is_stale'):
                try:
                    df.attrs['is_stale'] = True
                except Exception:
                    pass
        except Exception:
            pass
        return df

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:
        prefix = f"[{caller}] " if caller else ""
        logger.info(f"{prefix}📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via YFinance...")
        
        normalized_map = {}
        for s in symbols:
            if not s: continue
            ns_sym = self._normalize_symbol(s)
            if not ns_sym: continue
            if ns_sym not in normalized_map:
                normalized_map[ns_sym] = []
            normalized_map[ns_sym].append(s)
            
        ns_symbols = list(normalized_map.keys())
        results = self._fetch_batch_raw(ns_symbols, period, interval, range_from, range_to)
        
        reports = {}
        missing_symbols = []
        for ns_sym in ns_symbols:
            df = results.get(ns_sym)
            if isinstance(df, ProviderResult) or df is None or getattr(df, 'empty', True):
                missing_symbols.append(ns_sym)
            else:
                pipeline = val_registry.get_pipeline(DatasetType.PRICE)
                engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
                ctx = ValidationContext(provider="NSE", period=period, interval=interval, range_from=range_from, range_to=range_to, fetch_mode="DELTA" if range_from else "FULL")
                report = engine.validate(df, ctx)
                reports[ns_sym] = report
                if not report.is_valid:
                    missing_symbols.append(ns_sym)
                
        if missing_symbols:
            bse_fetch_list = []
            bse_to_ns_map = {}
            for ns_sym in missing_symbols:
                if ns_sym.endswith(".NS"):
                    bse_sym = ns_sym[:-3] + ".BO"
                    bse_fetch_list.append(bse_sym)
                    bse_to_ns_map[bse_sym] = ns_sym
                    
            if bse_fetch_list:
                logger.info(f"🔄 {len(bse_fetch_list)} NSE symbols failed/poor quality. Attempting bulk BSE fallback...")
                bse_results = self._fetch_batch_raw(bse_fetch_list, period, interval, range_from, range_to)
                for bse_sym, df in bse_results.items():
                    ns_sym = bse_to_ns_map[bse_sym]
                    if not isinstance(df, ProviderResult) and df is not None and not getattr(df, 'empty', True):
                        pipeline = val_registry.get_pipeline(DatasetType.PRICE)
                        engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
                        ctx = ValidationContext(provider="BSE", period=period, interval=interval, range_from=range_from, range_to=range_to, fetch_mode="DELTA" if range_from else "FULL")
                        bse_report = engine.validate(df, ctx)
                        reports[ns_sym] = bse_report
                        if bse_report.is_valid:
                            results[ns_sym] = df
                            try:
                                from bse_mapping_utils import save_bse_mapping
                                for orig in normalized_map.get(ns_sym, []):
                                    save_bse_mapping(orig, bse_sym)
                            except Exception:
                                pass
                            
        final_results = {}
        for ns_sym, df in results.items():
            used_fallback = ns_sym in missing_symbols
            source = "BSE" if used_fallback else "NSE"
            report = reports.get(ns_sym)
            for orig_sym in normalized_map.get(ns_sym, []):
                if isinstance(df, ProviderResult):
                    final_results[orig_sym] = MarketData(None, source, None, False, used_fallback, error=df.name)
                elif report is not None and report.is_valid:
                    final_results[orig_sym] = MarketData(df, source, report, False, used_fallback, None)
                else:
                    final_results[orig_sym] = MarketData(None, source, report, False, used_fallback, "Quality Rejected")
                
        for s in symbols:
            if s not in final_results:
                final_results[s] = MarketData(None, "UNKNOWN", None, False, False, "Missing")
            
        return final_results
    def get_quote(self, symbol: str) -> dict:
        ns_sym = self._normalize_symbol(symbol)
        logger.info(f"📥 Fetching quote for {symbol} via YFinance...")
        try:
            yf_acquire(context=f"DataFetcher.get_quote | {ns_sym}")
            try:
                ticker = yf.Ticker(ns_sym)
                info = ticker.info
                if info and 'regularMarketPrice' in info:
                    return info
            except Exception:
                pass
            finally:
                yf_release()
                
            # If quote failed, retry once with BSE symbol equivalent
            if ns_sym.endswith(".NS"):
                bse_sym = ns_sym[:-3] + ".BO"
                logger.info(f"🔄 Quote fetch failed for {ns_sym}. Retrying with BSE symbol {bse_sym}...")
                yf_acquire(context=f"DataFetcher.get_quote | {bse_sym}")
                try:
                    ticker = yf.Ticker(bse_sym)
                    info = ticker.info
                    if info and 'regularMarketPrice' in info:
                        try:
                            from bse_mapping_utils import save_bse_mapping
                            save_bse_mapping(symbol, bse_sym)
                        except Exception as e:
                            logger.warning(f"Failed to save BSE mapping inside get_quote: {e}")
                        return info
                except Exception:
                    pass
                finally:
                    yf_release()
            # [VERSION: POISONED_MAPPING_FIX_v1.0] Reverse Fallback for poisoned BSE mappings
            elif ns_sym.endswith(".BO"):
                try:
                    from bse_mapping_utils import load_bse_mappings, invalidate_bse_mapping
                    mappings = load_bse_mappings()
                    orig_clean = symbol.strip().upper()
                    if orig_clean in mappings or (orig_clean.endswith(".NS") and orig_clean[:-3] in mappings):
                        logger.info(f"🗑️ Invalidating poisoned BSE mapping for {symbol} and retrying via NSE (quote)...")
                        clean_orig = orig_clean[:-3] if orig_clean.endswith(".NS") or orig_clean.endswith(".BO") else orig_clean
                        invalidate_bse_mapping(clean_orig)
                        recovery_sym = (orig_clean[:-3] + ".NS") if (orig_clean.endswith(".NS") or orig_clean.endswith(".BO")) else (orig_clean + ".NS")
                        yf_acquire(context=f"DataFetcher.get_quote | {recovery_sym}")
                        try:
                            ticker = yf.Ticker(recovery_sym)
                            info = ticker.info
                            if info and 'regularMarketPrice' in info:
                                return info
                        except Exception:
                            pass
                        finally:
                            yf_release()
                except Exception as e:
                    logger.warning(f"Failed during poisoned mapping recovery in get_quote: {e}")
            return {}
        except CircuitOpenError as ce:
            logger.error(f"YFinance circuit open; abort quote fetch for {ns_sym}: {ce}")
            return {}
        except Exception as e:
            msg = str(e).lower()
            if 'too many requests' in msg or 'rate limit' in msg:
                record_rate_limit(context=f"DataFetcher.get_quote | {ns_sym}")
            logger.exception(f"Failed to fetch quote for {symbol}")
            return {}

# ── Auto Switching & Fallback Fetcher ───────────────────────────────────────

# [VERSION: FYERS_DEGRADATION_CACHE_v1.0] Module-level 24-hour Fyers degradation cache
_fyers_degradation_cache: dict[str, float] = {}

class AutoSwitchingFetcher(DataFetcher):
    """Fetcher that uses interval-based routing policy with fallback capabilities."""
    def __init__(self):
        self.yfinance_fetcher = YFinanceFetcher()
        self.fyers_fetcher = None
        try:
            from data_providers.fyers_fetcher import FyersFetcher
            self.fyers_fetcher = FyersFetcher()
        except Exception as e:
            logger.warning(f"FyersFetcher could not be loaded: {e}. Falling back completely to YFinance.")

    def _is_fyers_degraded(self, symbol: str) -> bool:
        if not symbol: return False
        clean = symbol.strip().upper()
        if clean.endswith(".NS"): clean = clean[:-3]
        if clean.endswith(".BO"): clean = clean[:-3]
        
        ts = _fyers_degradation_cache.get(clean)
        if ts and (time.time() - ts < 86400):  # 24-hour degradation cooldown
            return True
        elif ts:
            _fyers_degradation_cache.pop(clean, None)
        return False

    def _mark_fyers_degraded(self, symbol: str):
        if not symbol: return
        clean = symbol.strip().upper()
        if clean.endswith(".NS"): clean = clean[:-3]
        if clean.endswith(".BO"): clean = clean[:-3]
        _fyers_degradation_cache[clean] = time.time()

    def _should_use_fyers(self) -> bool:
        if not self.fyers_fetcher:
            return False
        try:
            import fyers_auth
            import config
            import os
            if not config.FYERS_CLIENT_ID or not config.FYERS_SECRET_KEY:
                return False
            token = fyers_auth.get_access_token()
            
            if not token:
                # Debounce logic for Telegram ping (once per day)
                from datetime import datetime
                from zoneinfo import ZoneInfo
                IST = ZoneInfo("Asia/Kolkata")
                today_str = datetime.now(IST).strftime("%Y-%m-%d")
                ping_file = os.path.join(config.DATA_DIR, "fyers_ping.lock")
                
                last_ping = ""
                if os.path.exists(ping_file):
                    with open(ping_file, "r") as f:
                        last_ping = f.read().strip()
                        
                if last_ping != today_str:
                    from telegram_engine import send_telegram_message
                    msg = (
                        "🚨 <b>Fyers Authentication Failed</b>\n\n"
                        "The daily Fyers token is missing or expired. "
                        "The system is currently falling back to Yahoo Finance.\n\n"
                        "🔗 <b>Action Required:</b>\n"
                        "Please login to authorize: <a href='/fyers/login'>Authorize Fyers</a>"
                    )
                    send_telegram_message(msg)
                    
                    logger.warning("Fyers Auth Failed: Token expired. System fell back to Yahoo.")
                        
                    with open(ping_file, "w") as f:
                        f.write(today_str)
                        
            return token is not None
        except Exception:
            return False

    # [VERSION: V5_ACQUISITION_ROUTING_V1.0] Delegate provider selection to ProviderSelector
    def _get_providers(self, interval: str, fetch_type: str = "historical") -> list:
        try:
            from data_providers.provider_selector import selector
            resolved = selector.get_providers(interval, fetch_type=fetch_type)
            mapped = []
            for p in resolved:
                name = "yfinance" if p in ("yahoo", "bse") else p
                if name not in mapped:
                    mapped.append(name)
            return mapped if mapped else ["yfinance", "fyers"]
        except Exception as e:
            logger.warning(f"Error resolving ProviderSelector route: {e}")
            return ["yfinance", "fyers"]

    def _get_fetcher_by_name(self, name: str) -> DataFetcher:
        if name in ("yfinance", "yahoo", "bse"):
            return self.yfinance_fetcher
        if name == "fyers" and self._should_use_fyers():
            return self.fyers_fetcher
        return None

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> MarketData:
        providers = self._get_providers(interval)
        
        last_md = None
        for prov_name in providers:
            fetcher = self._get_fetcher_by_name(prov_name)
            if not fetcher:
                continue
                
            if prov_name == "fyers" and self._is_fyers_degraded(symbol):
                continue
                
            try:
                md = fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)
                last_md = md
                if md and md.dataframe is not None and md.quality_report and md.quality_report.is_valid:
                    return md
                    
                if prov_name == "fyers":
                    logger.warning(f"Fyers fetch returned poor quality for {symbol}. Marking symbol degraded & falling back.")
                    self._mark_fyers_degraded(symbol)
            except Exception as e:
                logger.warning(f"{prov_name} fetch exception for {symbol}: {e}.")
                if prov_name == "fyers":
                    self._mark_fyers_degraded(symbol)
                    
        return last_md if last_md else MarketData(None, "UNKNOWN", None, False, False, "Missing")

    # [VERSION: V5_ACQUISITION_ROUTING_V1.0] Partial fallback with taxonomy telemetry
    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:
        providers = self._get_providers(interval)
        
        results = {}
        fallback_results = {}  # Store the most recent result for missing symbols
        missing_symbols = list(symbols)
        provider_telemetry = {}
        
        for prov_name in providers:
            if not missing_symbols:
                break
                
            fetcher = self._get_fetcher_by_name(prov_name)
            if not fetcher:
                provider_telemetry[prov_name] = {
                    "requested": len(missing_symbols), "succeeded": 0, "failed": len(missing_symbols),
                    "reasons": {"provider_unavailable": len(missing_symbols)}, "latency_s": 0.0
                }
                continue
                
            current_batch = list(missing_symbols)
            if prov_name == "fyers":
                current_batch = [s for s in missing_symbols if not self._is_fyers_degraded(s)]
                known_degraded = [s for s in missing_symbols if self._is_fyers_degraded(s)]
                if known_degraded:
                    logger.info(f"⚡ [FYERS DEGRADATION CACHE] Bypassing Fyers for {len(known_degraded)} degraded symbols.")
                
            if current_batch:
                start_t = time.time()
                prov_stats = {
                    "requested": len(current_batch),
                    "succeeded": 0,
                    "failed": 0,
                    "reasons": {
                        "missing": 0, "timeout": 0, "rate_limit": 0,
                        "quality_rejected": 0, "malformed": 0, "provider_unavailable": 0
                    }
                }
                try:
                    prov_results = fetcher.get_batch_ohlcv(current_batch, interval, period, retries, range_from, range_to, caller=caller)
                    
                    for s in current_batch:
                        res = prov_results.get(s)
                        if res:
                            fallback_results[s] = res
                            if res.dataframe is not None and res.quality_report and res.quality_report.is_valid:
                                results[s] = res
                                if s in missing_symbols:
                                    missing_symbols.remove(s)
                                prov_stats["succeeded"] += 1
                            else:
                                prov_stats["failed"] += 1
                                err_msg = str(getattr(res, 'error', '') or '').lower()
                                if "timeout" in err_msg:
                                    prov_stats["reasons"]["timeout"] += 1
                                elif "rate" in err_msg or "429" in err_msg or "circuit" in err_msg:
                                    prov_stats["reasons"]["rate_limit"] += 1
                                elif "quality" in err_msg or "reject" in err_msg:
                                    prov_stats["reasons"]["quality_rejected"] += 1
                                elif "malformed" in err_msg or "format" in err_msg:
                                    prov_stats["reasons"]["malformed"] += 1
                                else:
                                    prov_stats["reasons"]["missing"] += 1
                                    
                                if prov_name == "fyers":
                                    self._mark_fyers_degraded(s)
                        else:
                            prov_stats["failed"] += 1
                            prov_stats["reasons"]["missing"] += 1
                except Exception as e:
                    prov_stats["failed"] = len(current_batch)
                    prov_stats["reasons"]["provider_unavailable"] = len(current_batch)
                    logger.warning(f"{prov_name} batch fetch exception: {e}.")
                    
                prov_stats["latency_s"] = round(time.time() - start_t, 3)
                provider_telemetry[prov_name] = prov_stats

        # Log detailed per-provider telemetry summary
        telemetry_summary = " | ".join([
            f"{p}: {data.get('succeeded', 0)}/{data.get('requested', 0)} ok ({data.get('latency_s', 0.0)}s)"
            for p, data in provider_telemetry.items()
        ])
        logger.info(f"📊 [BATCH_TELEMETRY] Caller={caller or 'Unknown'} | {telemetry_summary}")
        
        for s in symbols:
            if s not in results:
                results[s] = fallback_results.get(s, MarketData(None, "UNKNOWN", None, False, False, "Missing"))
                
        return results

    def get_quote(self, symbol: str) -> dict:
        providers = self._get_providers("quote")
        
        for prov_name in providers:
            fetcher = self._get_fetcher_by_name(prov_name)
            if not fetcher:
                continue
                
            if prov_name == "fyers" and self._is_fyers_degraded(symbol):
                continue
                
            try:
                quote = fetcher.get_quote(symbol)
                if quote:
                    return quote
            except Exception as e:
                logger.warning(f"{prov_name} quote fetch exception for {symbol}: {e}.")
                
        return self.yfinance_fetcher.get_quote(symbol)


# ── Factory ─────────────────────────────────────────────────────────────────

def get_fetcher() -> DataFetcher:
    from config import DATA_PROVIDER
    if DATA_PROVIDER == "kite":
        from data_providers.kite_fetcher import KiteFetcher
        return KiteFetcher()
    elif DATA_PROVIDER == "fyers":
        # Force AutoSwitchingFetcher so it gracefully falls back to YFinance if Fyers auth is missing
        return AutoSwitchingFetcher()
    elif DATA_PROVIDER == "yfinance":
        return YFinanceFetcher()
    
    # "auto" or default uses the AutoSwitchingFetcher
    return AutoSwitchingFetcher()


