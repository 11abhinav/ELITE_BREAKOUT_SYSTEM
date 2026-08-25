import os
import pandas as pd
from abc import ABC, abstractmethod
import yfinance as yf
import time
import random
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

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


def _generate_synthetic_df(symbol: str, candles: int = 450) -> pd.DataFrame:
    import numpy as np
    end_date = datetime.now(IST)
    dates = pd.date_range(end=end_date, periods=candles, freq="B")
    np.random.seed(abs(hash(symbol)) % (2**32))
    base_price = 100.0 + (abs(hash(symbol)) % 500)
    returns = np.random.normal(0.0005, 0.015, candles)
    price_series = base_price * np.exp(np.cumsum(returns))
    
    df = pd.DataFrame({
        "Open": price_series * (1 + np.random.uniform(-0.005, 0.005, candles)),
        "High": price_series * (1 + np.random.uniform(0.001, 0.015, candles)),
        "Low": price_series * (1 - np.random.uniform(0.001, 0.015, candles)),
        "Close": price_series,
        "Volume": np.random.randint(10000, 1000000, candles)
    }, index=dates)
    return df

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
        return MarketData(df if (report and (report.is_valid or range_from)) else None, source, report, False, used_fallback, None if (report and report.is_valid) else "Quality Check Failed")


    # [VERSION: YF_DYNAMIC_BSE_FALLBACK_v1.0] Helper to perform the raw batch download
    def _fetch_batch_raw(self, ns_symbols: list[str], period: str, interval: str, range_from: str = None, range_to: str = None) -> dict:
        provider = _price_provider
        try:


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
                elif report is not None and (report.is_valid or range_from):
                    final_results[orig_sym] = MarketData(df, source, report, False, used_fallback, None if report.is_valid else "Quality Warning")
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
        if ts and (time.time() - ts < 300):  # 5-minute degradation cooldown for temporary errors
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
            logger.warning("Fyers API skipped: FyersFetcher module is not initialized.")
            return False
        try:
            import fyers_auth
            import config
            import os
            if not config.FYERS_CLIENT_ID or not config.FYERS_SECRET_KEY:
                logger.warning("Fyers API skipped: FYERS_CLIENT_ID or FYERS_SECRET_KEY missing in environment/config.")
                return False
            token = fyers_auth.get_access_token()
            
            if not token:
                logger.warning("Fyers API skipped: No valid access token for today (auto-login failed or credentials missing). Falling back to YFinance.")
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
                    
                    with open(ping_file, "w") as f:
                        f.write(today_str)
                        
            return token is not None
        except Exception as e:
            logger.warning(f"Fyers API availability check failed with exception: {e}")
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
            return mapped if mapped else ["fyers", "yfinance"]
        except Exception as e:
            logger.warning(f"Error resolving ProviderSelector route: {e}")
            return ["fyers", "yfinance"]

    def _get_fetcher_by_name(self, name: str) -> DataFetcher:
        if name == "upstox":
            if not hasattr(self, "upstox_fetcher") or self.upstox_fetcher is None:
                try:
                    from market_data.providers.upstox_provider import UpstoxProvider
                    self.upstox_fetcher = UpstoxProvider(auth_service=None)
                except Exception as e:
                    logger.warning(f"UpstoxProvider initialization failed: {e}")
                    self.upstox_fetcher = None
            return self.upstox_fetcher
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

    # [VERSION: LOAD_BALANCED_BATCH_V1.0] Scatter-Gather load balancing with fallback telemetry
    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:
        if os.getenv("SIX_SCANNER_ALLOW_NETWORK") == "0":
            out = {}
            for s in symbols:
                df = _generate_synthetic_df(s, candles=450 if interval == "1d" else 50)
                out[s] = MarketData(df, "SYNTHETIC_MOCK", None, True, False, "Success")
            return out

        providers = self._get_providers(interval)
        
        results = {}
        fallback_results = {}
        missing_symbols = list(symbols)
        provider_telemetry = {}
        
        # 1. Identify active premium providers for load-balancing
        premium_names = [p for p in providers if p not in ("yfinance", "yahoo", "bse")]
        fallback_names = [p for p in providers if p in ("yfinance", "yahoo", "bse")]
        
        active_premiums = []
        for p in premium_names:
            fetcher = self._get_fetcher_by_name(p)
            if fetcher:
                active_premiums.append((p, fetcher))
            else:
                provider_telemetry[p] = {
                    "requested": 0, "succeeded": 0, "failed": 0,
                    "reasons": {"provider_unavailable": 0}, "latency_s": 0.0
                }
                
        # [VERSION: SYMBOL_ROUTER_V1.0] Partition missing symbols into routing buckets based on (symbol, interval) capability state
        if active_premiums and missing_symbols:
            from symbol_router import symbol_router, RoutingState
            
            upstox_fetcher = self._get_fetcher_by_name("upstox")
            fyers_fetcher = self._get_fetcher_by_name("fyers")
            
            # [VERSION: SYMBOL_ROUTER_PARTITIONING_v1.0] Batch Partitioning via SymbolRouter
            # RATIONALE:
            #   - Bypasses known-incompatible brokers for sticky symbols (e.g. Fyers failure on BSE/SME symbols).
            #   - Directs sticky symbols straight to the working broker (UPSTOX_ONLY / FYERS_ONLY) on 100% of future runs.
            #   - Dual-working equities remain in LOAD_BALANCED to split traffic 50/50 and prevent 429 rate-limits.
            upstox_only_symbols = []
            fyers_only_symbols = []
            balanced_symbols = []
            
            for s in missing_symbols:
                route = symbol_router.get_route(s, interval)
                if route == RoutingState.UPSTOX_ONLY and upstox_fetcher:
                    upstox_only_symbols.append(s)
                elif route == RoutingState.FYERS_ONLY and fyers_fetcher:
                    fyers_only_symbols.append(s)
                else:
                    balanced_symbols.append(s)
                    
            if upstox_only_symbols or fyers_only_symbols:
                logger.info(
                    f"📌 [SYMBOL_ROUTER] Batch Partitioned | Balanced: {len(balanced_symbols)} | "
                    f"Upstox-Only: {len(upstox_only_symbols)} | Fyers-Only: {len(fyers_only_symbols)}"
                )

            import concurrent.futures

            def fetch_chunk(p_name, fetcher, chunk):
                start_t = time.time()
                prov_stats = {
                    "requested": len(chunk), "succeeded": 0, "failed": 0,
                    "reasons": {"missing": 0, "timeout": 0, "rate_limit": 0, "quality_rejected": 0, "malformed": 0, "provider_unavailable": 0}
                }
                actual_chunk = chunk
                if p_name == "fyers":
                    actual_chunk = [s for s in chunk if not self._is_fyers_degraded(s)]
                    known_degraded = len(chunk) - len(actual_chunk)
                    if known_degraded:
                        logger.info(f"⚡ [FYERS DEGRADATION CACHE] Bypassing Fyers for {known_degraded} degraded symbols in batch.")
                        prov_stats["reasons"]["provider_unavailable"] += known_degraded
                        prov_stats["failed"] += known_degraded
                        
                prov_results = {}
                if actual_chunk:
                    try:
                        prov_results = fetcher.get_batch_ohlcv(actual_chunk, interval, period, retries, range_from, range_to, caller=caller)
                    except Exception as e:
                        logger.warning(f"{p_name} batch fetch exception: {e}.")
                        prov_stats["failed"] = len(chunk)
                        prov_stats["reasons"]["provider_unavailable"] = len(chunk)
                
                prov_stats["latency_s"] = round(time.time() - start_t, 3)
                return p_name, prov_results, prov_stats, actual_chunk

            futures = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, len(active_premiums) + 2)) as executor:
                # Dispatch sticky UPSTOX_ONLY bucket directly to Upstox
                if upstox_only_symbols and upstox_fetcher:
                    futures.append(executor.submit(fetch_chunk, "upstox", upstox_fetcher, upstox_only_symbols))
                    
                # Dispatch sticky FYERS_ONLY bucket directly to Fyers
                if fyers_only_symbols and fyers_fetcher:
                    futures.append(executor.submit(fetch_chunk, "fyers", fyers_fetcher, fyers_only_symbols))
                    
                # [VERSION: DUAL_BROKER_LOAD_BALANCER_v2.0] Concurrently load-balance 50/50 across Fyers and Upstox
                # RATIONALE: User directive - fetch using Fyers + Upstox in parallel to maximize speed.
                if balanced_symbols:
                    if len(active_premiums) > 1:
                        mid = (len(balanced_symbols) + 1) // 2
                        chunk1 = balanced_symbols[:mid]
                        chunk2 = balanced_symbols[mid:]
                        p1_name, p1_fetcher = active_premiums[0]
                        p2_name, p2_fetcher = active_premiums[1]
                        if chunk1:
                            futures.append(executor.submit(fetch_chunk, p1_name, p1_fetcher, chunk1))
                        if chunk2:
                            futures.append(executor.submit(fetch_chunk, p2_name, p2_fetcher, chunk2))
                    else:
                        primary_name, primary_fetcher = active_premiums[0]
                        futures.append(executor.submit(fetch_chunk, primary_name, primary_fetcher, balanced_symbols))

            for future in concurrent.futures.as_completed(futures):
                try:
                    p_name, prov_results, prov_stats, actual_chunk = future.result()
                    
                    for s in actual_chunk:
                        res = prov_results.get(s)
                        
                        # [VERSION: STALENESS_FALLBACK_v1.0] Reject if data is considered stale by market_utils
                        is_stale = False
                        if res and res.dataframe is not None and interval in ("1d", "daily"):
                            try:
                                from market_utils import evaluate_data_staleness
                                last_ts = pd.to_datetime(res.dataframe['Date'].iloc[-1]) if 'Date' in res.dataframe.columns else pd.to_datetime(res.dataframe.index[-1])
                                is_stale = evaluate_data_staleness(last_ts).get("is_stale", False)
                            except Exception:
                                pass

                        if res and res.dataframe is not None and getattr(res, 'quality_report', None) and res.quality_report.is_valid and not is_stale:
                            results[s] = res
                            if s in missing_symbols:
                                missing_symbols.remove(s)
                            prov_stats["succeeded"] += 1
                            symbol_router.record_result(s, interval, p_name, is_success=True)
                        else:
                            prov_stats["failed"] += 1
                            err_msg = str(getattr(res, 'error', '') or '').lower() if res else "No data returned"
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
                                
                            if p_name == "fyers":
                                self._mark_fyers_degraded(s)
                            symbol_router.record_result(s, interval, p_name, is_success=False, error_msg=err_msg)
                    
                    if p_name in provider_telemetry:
                        for k, v in prov_stats.items():
                            if k == "reasons":
                                for rk, rv in v.items():
                                    provider_telemetry[p_name]["reasons"][rk] = provider_telemetry[p_name]["reasons"].get(rk, 0) + rv
                            elif isinstance(v, (int, float)):
                                provider_telemetry[p_name][k] = provider_telemetry[p_name].get(k, 0) + v
                    else:
                        provider_telemetry[p_name] = prov_stats
                except Exception as e:
                    logger.error(f"Error processing load-balanced future: {e}")
                    
        # 2.5 Premium Fallback Phase: Process missing symbols through OTHER premium providers before Yahoo
        if missing_symbols and len(active_premiums) > 1:
            from symbol_router import symbol_router
            for prov_name, fetcher in active_premiums:
                if not missing_symbols:
                    break
                
                current_batch = list(missing_symbols)
                start_t = time.time()
                try:
                    if prov_name in provider_telemetry:
                        provider_telemetry[prov_name]["requested"] += len(current_batch)
                    prov_results = fetcher.get_batch_ohlcv(current_batch, interval, period, retries=1, range_from=range_from, range_to=range_to, caller=caller)
                    succeeded_count = 0
                    for s in current_batch:
                        res = prov_results.get(s)
                        
                        is_stale = False
                        if res and res.dataframe is not None and interval in ("1d", "daily"):
                            try:
                                from market_utils import evaluate_data_staleness
                                last_ts = pd.to_datetime(res.dataframe['Date'].iloc[-1]) if 'Date' in res.dataframe.columns else pd.to_datetime(res.dataframe.index[-1])
                                is_stale = evaluate_data_staleness(last_ts).get("is_stale", False)
                            except Exception:
                                pass

                        if res and res.dataframe is not None and res.quality_report and res.quality_report.is_valid and not is_stale:
                            results[s] = res
                            if s in missing_symbols:
                                missing_symbols.remove(s)
                            succeeded_count += 1
                            symbol_router.record_fallback_event()
                            symbol_router.record_result(s, interval, prov_name, is_success=True)
                            
                            # [VERSION: STICKY_RECOVERY_v1.0] Mark symbol sticky to working broker for all future runs
                            from symbol_router import RoutingState, ProviderErrorCode, RouteEntry
                            target_state = RoutingState.FYERS_ONLY if prov_name == "fyers" else RoutingState.UPSTOX_ONLY
                            key = symbol_router._normalize_key(s, interval)
                            with symbol_router._lock:
                                symbol_router._routes[key] = RouteEntry(
                                    state=target_state,
                                    reason=ProviderErrorCode.UNSUPPORTED_SYMBOL,
                                    confidence="HIGH",
                                    learned_at=time.monotonic(),
                                    session_date=datetime.now(IST).strftime("%Y-%m-%d")
                                )
                            symbol_router._persist_routes_async()
                            
                            if prov_name in provider_telemetry:
                                provider_telemetry[prov_name]["succeeded"] += 1
                            logger.info(f"✅ [Premium Fallback & Sticky Learner] {prov_name.upper()} successfully recovered data for {s} — Set sticky route to {target_state.value}")
                        elif prov_name in provider_telemetry:
                            provider_telemetry[prov_name]["failed"] += 1
                            err_msg = str(getattr(res, 'error', '') or '').lower() if res else "Fallback missing"
                            symbol_router.record_result(s, interval, prov_name, is_success=False, error_msg=err_msg)
                    if succeeded_count > 0:
                        logger.info(f"🔄 [Premium Fallback] {prov_name} recovered {succeeded_count} missing symbols in total!")
                except Exception as e:
                    logger.warning(f"⚠️ {prov_name} premium fallback batch fetch exception: {e}.")

        # 3. Fallback Phase: Process any missing symbols through fallback providers (yfinance, etc)
        for prov_name in fallback_names:
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
            start_t = time.time()
            prov_stats = {
                "requested": len(current_batch), "succeeded": 0, "failed": 0,
                "reasons": {"missing": 0, "timeout": 0, "rate_limit": 0, "quality_rejected": 0, "malformed": 0, "provider_unavailable": 0}
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
                
        if missing_symbols:
            logger.error(f"❌ Completely missing data for {len(missing_symbols)} symbols after trying ALL providers: {list(missing_symbols)}")
            try:
                from database import insert_notification
                sym_str = ", ".join(list(missing_symbols)[:15])
                if len(missing_symbols) > 15:
                    sym_str += f" and {len(missing_symbols)-15} more"
                insert_notification(
                    "error",
                    f"❌ DATA MISSING: {len(missing_symbols)} symbols failed",
                    f"Failed to fetch data for {sym_str} across ALL providers (Fyers, Upstox, Yahoo). They will be skipped."
                )
            except Exception:
                pass
                
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


