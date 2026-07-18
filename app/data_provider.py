import pandas as pd
from abc import ABC, abstractmethod
import yfinance as yf
import time
import random
import logging

from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, get_backoff_delay, CircuitOpenError

from price_provider import PriceProvider
from config import BATCH_DOWNLOAD_SIZE, PRICE_CACHE_TTL_SECONDS

logger = logging.getLogger(__name__)

# Module-level shared provider to ensure cache is reused across fetcher instances
_price_provider = PriceProvider(batch_size=BATCH_DOWNLOAD_SIZE, cache_ttl=PRICE_CACHE_TTL_SECONDS)

class DataFetcher(ABC):
    @abstractmethod
    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        """Fetch OHLCV data for a single symbol."""
        pass

    @abstractmethod
    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        """Fetch OHLCV data for multiple symbols simultaneously."""
        pass

    @abstractmethod
    def get_quote(self, symbol: str) -> dict:
        """Fetch current quote for a symbol."""
        pass


class YFinanceFetcher(DataFetcher):
    def _normalize_symbol(self, symbol: str) -> str:
        # [VERSION: DATA_PROV_SYMBOL_FIX_v1.2] Support persistent BSE symbol mappings to avoid redundant NSE failures
        try:
            from bse_mapping_utils import load_bse_mappings
            mappings = load_bse_mappings()
            upper_sym = symbol.strip().upper()
            if upper_sym in mappings:
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
        
        # Fix ampersand symbols.
        from daily_builder import SYMBOL_CORRECTIONS
        STALE_MAP = {
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
                    # Reset index so 'Date' or 'Datetime' is a column
                    df = df.reset_index().copy()
                    return df
            except CircuitOpenError as ce:
                logger.error(f"YFinance circuit open; aborting fetch for {ns_sym}: {ce}")
                return None
            except Exception as e:
                msg = str(e).lower()
                if 'too many requests' in msg or 'rate limit' in msg or 'yf' in msg and 'rate' in msg:
                    record_rate_limit(context=f"DataFetcher._get_ohlcv_raw | {ns_sym}")
                    delay = get_backoff_delay(attempt)
                    logger.warning(f"⚠️ Single fetch rate-limited for {ns_sym} (Attempt {attempt+1}/{retries}). Backing off {delay:.1f}s")
                    time.sleep(delay)
                else:
                    logger.warning(f"⚠️ Single fetch failed for {ns_sym} (Attempt {attempt+1}/{retries}): {e}")
                    wait = (2 ** attempt) * random.uniform(0.5, 1.5)
                    time.sleep(wait)
        logger.error(f"❌ Exhausted retries fetching {ns_sym}")
        return None

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        ns_sym = self._normalize_symbol(symbol)
        logger.debug(f"📥 Fetching OHLCV for {symbol} ({interval}, {period}) via YFinance...")
        df = self._get_ohlcv_raw(ns_sym, interval, period, retries, range_from, range_to)
        
        # If NSE query failed, retry once using the BSE (.BO) equivalent!
        if (df is None or df.empty) and ns_sym.endswith(".NS"):
            bse_sym = ns_sym[:-3] + ".BO"
            logger.info(f"🔄 NSE fetch failed or returned empty for {symbol}. Retrying with BSE symbol {bse_sym}...")
            df = self._get_ohlcv_raw(bse_sym, interval, period, retries, range_from, range_to)
            if df is not None and not df.empty:
                try:
                    from bse_mapping_utils import save_bse_mapping
                    save_bse_mapping(symbol, bse_sym)
                except Exception as e:
                    logger.warning(f"Failed to save BSE mapping inside get_ohlcv: {e}")
            
        # [VERSION: POISONED_MAPPING_FIX_v1.0] Reverse Fallback for poisoned BSE mappings
        if (df is None or df.empty) and ns_sym.endswith(".BO"):
            try:
                from bse_mapping_utils import load_bse_mappings, invalidate_bse_mapping
                mappings = load_bse_mappings()
                orig_clean = symbol.strip().upper()
                if orig_clean in mappings or (orig_clean.endswith(".NS") and orig_clean[:-3] in mappings):
                    logger.info(f"🗑️ Invalidating poisoned BSE mapping for {symbol} and retrying via NSE...")
                    clean_orig = orig_clean[:-3] if orig_clean.endswith(".NS") or orig_clean.endswith(".BO") else orig_clean
                    invalidate_bse_mapping(clean_orig)
                    recovery_sym = (orig_clean[:-3] + ".NS") if (orig_clean.endswith(".NS") or orig_clean.endswith(".BO")) else (orig_clean + ".NS")
                    df = self._get_ohlcv_raw(recovery_sym, interval, period, retries, range_from, range_to)
            except Exception as e:
                logger.warning(f"Failed during poisoned mapping recovery in get_ohlcv: {e}")

        return df

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

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        prefix = f"[{caller}] " if caller else ""
        logger.info(f"{prefix}📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via YFinance...")
        
        normalized_map = {}
        for s in symbols:
            ns_sym = self._normalize_symbol(s)
            normalized_map.setdefault(ns_sym, []).append(s)
            
        ns_symbols = list(normalized_map.keys())
        fetched = self._fetch_batch_raw(ns_symbols, period, interval, range_from, range_to)
        
        all_data = {}
        # NOTE: Price Provider (price_provider.py) handles BSE fallback, poisoned mapping
        # recovery, and rate limiting centrally. Symbols missing from `fetched` below are
        # those that failed after all retries in price_provider — they are set to None here
        # so callers can handle them gracefully.
        
        for ns_sym, orig_syms in normalized_map.items():
            df = fetched.get(ns_sym)
            if df is not None and not df.empty:
                df_clean = self._clean_df(df)
                for orig_sym in orig_syms:
                    all_data[orig_sym] = df_clean.copy() if len(orig_syms) > 1 else df_clean


        for s in symbols:
            all_data.setdefault(s, None)

        return all_data

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

class AutoSwitchingFetcher(DataFetcher):
    """Fetcher that uses Fyers as primary if authenticated, falling back to YFinance on any failure."""
    def __init__(self):
        self.yfinance_fetcher = YFinanceFetcher()
        self.fyers_fetcher = None
        try:
            from data_providers.fyers_fetcher import FyersFetcher
            self.fyers_fetcher = FyersFetcher()
        except Exception as e:
            logger.warning(f"FyersFetcher could not be loaded: {e}. Falling back completely to YFinance.")

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
                        "Please login to authorize: <a href='https://elitebreakoutsystem-production.up.railway.app/fyers/login'>Authorize Fyers</a>"
                    )
                    send_telegram_message(msg)
                    
                    try:
                        from database import insert_notification
                        insert_notification(
                            notif_type="error",
                            title="Fyers Auth Failed",
                            message="Token expired. System fell back to Yahoo. Click here to <a href='/fyers/login' style='text-decoration:underline'>Authorize</a>.",
                            symbol="SYSTEM"
                        )
                    except Exception as e:
                        logger.exception(f"Failed to insert dashboard notification")
                        
                    with open(ping_file, "w") as f:
                        f.write(today_str)
                        
            return token is not None
        except Exception:
            return False

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        if self._should_use_fyers():
            try:
                df = self.fyers_fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)
                if df is not None and not df.empty:
                    return df
                logger.warning(f"Fyers fetch returned empty/failed for {symbol}. Falling back to YFinance.")
            except Exception as e:
                logger.error(f"Fyers fetch exception for {symbol}: {e}. Falling back to YFinance.")
                try:
                    from database import insert_notification
                    insert_notification("error", f"Fyers Fetch Failed ({symbol})", f"Error: {e}. Falling back to Yahoo Finance.", symbol)
                except Exception:
                    pass
        return self.yfinance_fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        if self._should_use_fyers():
            try:
                results = self.fyers_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)
                missing_symbols = [s for s in symbols if results.get(s) is None or results[s].empty]
                if missing_symbols:
                    if len(missing_symbols) == len(symbols):
                        try:
                            from database import insert_notification
                            insert_notification("error", "Fyers Batch API Silent Failure", f"Fyers returned empty data for ALL {len(symbols)} symbols. Falling back to Yahoo Finance.", "SYSTEM")
                        except Exception:
                            pass
                    logger.warning(f"Fyers batch fetch returned empty/missing data for {len(missing_symbols)} symbols. Querying YFinance for these.")
                    yf_results = self.yfinance_fetcher.get_batch_ohlcv(missing_symbols, interval, period, retries, range_from, range_to, caller=caller)
                    for s in missing_symbols:
                        if s in yf_results:
                            results[s] = yf_results[s]
                for s in symbols:
                    results.setdefault(s, None)
                return results
            except Exception as e:
                logger.error(f"Fyers batch fetch exception: {e}. Falling back to YFinance.")
                try:
                    from database import insert_notification
                    insert_notification("error", "Fyers Batch Fetch Exception", f"Error: {e}. Falling back to Yahoo Finance.", "SYSTEM")
                except Exception:
                    pass
        
        yf_fallback_results = self.yfinance_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)
        for s in symbols:
            yf_fallback_results.setdefault(s, None)
        return yf_fallback_results

    def get_quote(self, symbol: str) -> dict:
        if self._should_use_fyers():
            try:
                quote = self.fyers_fetcher.get_quote(symbol)
                if quote:
                    return quote
            except Exception as e:
                logger.error(f"Fyers quote fetch exception for {symbol}: {e}. Falling back to YFinance.")
                try:
                    from database import insert_notification
                    insert_notification("error", f"Fyers Quote Failed ({symbol})", f"Error: {e}. Falling back to Yahoo Finance.", symbol)
                except Exception:
                    pass
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


