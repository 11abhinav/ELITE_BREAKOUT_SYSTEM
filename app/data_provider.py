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
        # yfinance requires .NS suffix; KiteConnect uses raw NSE symbol
        # also handle underscore to hyphen conversion commonly needed for yfinance
        sym = symbol.replace("_", "-")
        if sym.startswith("^"):
            return sym
        return f"{sym}.NS" if not sym.endswith(".NS") else sym

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        ns_sym = self._normalize_symbol(symbol)
        logger.debug(f"📥 Fetching OHLCV for {symbol} ({interval}, {period}) via YFinance...")
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
                yf_acquire(context=f"DataFetcher.get_ohlcv | {ns_sym}")
                try:
                    if range_from and range_to:
                        from datetime import datetime, timedelta
                        try:
                            start_date = range_from
                            end_dt = datetime.strptime(range_to, "%Y-%m-%d") + timedelta(days=1)
                            end_date = end_dt.strftime("%Y-%m-%d")
                            # [VERSION: EOD_PATCH_v1.1] [BUG FIX 7 REGRESSION FIX] Added timeout=60 to yfinance calls to prevent thread hangs
                            df = yf.download(ns_sym, interval=interval, start=start_date, end=end_date, progress=False, auto_adjust=True, threads=False, timeout=60)
                        except Exception as e:
                            df = yf.download(ns_sym, interval=interval, period=period, progress=False, auto_adjust=True, threads=False, timeout=60)
                    else:
                        df = yf.download(ns_sym, interval=interval, period=period, progress=False, auto_adjust=True, threads=False, timeout=60)
                finally:
                    yf_release()

                if df is not None and not df.empty:
                    # Flatten MultiIndex if it exists
                    # For default yfinance (no group_by): Level 0 = Price types, Level 1 = Tickers
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
                # Detect Yahoo rate-limiting patterns
                if 'too many requests' in msg or 'rate limit' in msg or 'yf' in msg and 'rate' in msg:
                    record_rate_limit(context=f"DataFetcher.get_ohlcv | {ns_sym}")
                    # Use aggressive backoff schedule for 429s
                    delay = get_backoff_delay(attempt)
                    logger.warning(f"⚠️ Single fetch rate-limited for {ns_sym} (Attempt {attempt+1}/{retries}). Backing off {delay:.1f}s")
                    time.sleep(delay)
                else:
                    logger.warning(f"⚠️ Single fetch failed for {ns_sym} (Attempt {attempt+1}/{retries}): {e}")
                    wait = (2 ** attempt) * random.uniform(0.5, 1.5)
                    time.sleep(wait)
        logger.error(f"❌ Exhausted retries fetching {symbol}")
        return None

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        prefix = f"[{caller}] " if caller else ""
        logger.info(f"{prefix}📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via YFinance...")
        # Use centralized PriceProvider batching to minimize calls and share caching across scanners
        provider = _price_provider
        normalized_map = {}
        for s in symbols:
            ns_sym = self._normalize_symbol(s)
            if ns_sym not in normalized_map:
                normalized_map[ns_sym] = []
            normalized_map[ns_sym].append(s)
            
        ns_symbols = list(normalized_map.keys())

        try:
            # Add night buffer to avoid rate limits at 1 AM
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

            fetched = provider.fetch_batch(ns_symbols, period=period, interval=interval, start=start_date, end=end_date)
        except Exception as e:
            logger.warning(f"Batch provider fetch failed: {e}")
            fetched = {}

        all_data = {}
        for ns_sym, orig_syms in normalized_map.items():
            df = fetched.get(ns_sym)
            if df is not None and not df.empty:
                # ensure a consistent format (reset index)
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        # For group_by='ticker' (from PriceProvider): Level 0 = Tickers, Level 1 = Price types
                        # So we need to flatten to the price types (level 1)
                        df.columns = df.columns.get_level_values(1)
                    df = df.reset_index().copy()
                    # preserve any stale marker set by provider
                    if getattr(df, 'attrs', {}).get('is_stale'):
                        try:
                            df.attrs['is_stale'] = True
                        except Exception:
                            pass
                    for orig_sym in orig_syms:
                        all_data[orig_sym] = df.copy() if len(orig_syms) > 1 else df
                except Exception:
                    for orig_sym in orig_syms:
                        all_data[orig_sym] = df.copy() if len(orig_syms) > 1 else df

        # Do NOT perform aggressive single-symbol fallbacks. If a symbol is missing from the batch
        # response it will be treated as missing for this scan cycle. This avoids generating a storm
        return all_data

    def get_quote(self, symbol: str) -> dict:
        ns_sym = self._normalize_symbol(symbol)
        logger.info(f"📥 Fetching quote for {symbol} via YFinance...")
        try:
            yf_acquire(context=f"DataFetcher.get_quote | {ns_sym}")
            try:
                ticker = yf.Ticker(ns_sym)
                return ticker.info
            finally:
                yf_release()
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
        return self.yfinance_fetcher.get_ohlcv(symbol, interval, period, retries, range_from, range_to)

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        if self._should_use_fyers():
            try:
                results = self.fyers_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)
                missing_symbols = [s for s in symbols if results.get(s) is None or results[s].empty]
                if missing_symbols:
                    logger.warning(f"Fyers batch fetch returned empty/missing data for {len(missing_symbols)} symbols. Querying YFinance for these.")
                    yf_results = self.yfinance_fetcher.get_batch_ohlcv(missing_symbols, interval, period, retries, range_from, range_to, caller=caller)
                    for s in missing_symbols:
                        if s in yf_results:
                            results[s] = yf_results[s]
                return results
            except Exception as e:
                logger.error(f"Fyers batch fetch exception: {e}. Falling back to YFinance.")
        return self.yfinance_fetcher.get_batch_ohlcv(symbols, interval, period, retries, range_from, range_to, caller=caller)

    def get_quote(self, symbol: str) -> dict:
        if self._should_use_fyers():
            try:
                quote = self.fyers_fetcher.get_quote(symbol)
                if quote:
                    return quote
            except Exception as e:
                logger.error(f"Fyers quote fetch exception for {symbol}: {e}. Falling back to YFinance.")
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


