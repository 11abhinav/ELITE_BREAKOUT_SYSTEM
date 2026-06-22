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
    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3) -> pd.DataFrame:
        """Fetch OHLCV data for a single symbol."""
        pass

    @abstractmethod
    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3) -> dict[str, pd.DataFrame]:
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

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3) -> pd.DataFrame:
        ns_sym = self._normalize_symbol(symbol)
        for attempt in range(retries):
            try:
                # Respect global Yahoo rate limiter (may raise CircuitOpenError)
                yf_acquire()
                try:
                    df = yf.download(ns_sym, interval=interval, period=period, progress=False, auto_adjust=True, threads=False)
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
                # Detect Yahoo rate-limiting patterns
                if 'too many requests' in msg or 'rate limit' in msg or 'yf' in msg and 'rate' in msg:
                    record_rate_limit()
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

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3) -> dict[str, pd.DataFrame]:
        # Use centralized PriceProvider batching to minimize calls and share caching across scanners
        provider = _price_provider
        normalized_map = {self._normalize_symbol(s): s for s in symbols}
        ns_symbols = list(normalized_map.keys())

        try:
            fetched = provider.fetch_batch(ns_symbols, period=period, interval=interval)
        except Exception as e:
            logger.warning(f"Batch provider fetch failed: {e}")
            fetched = {}

        all_data = {}
        for ns_sym, orig_sym in normalized_map.items():
            df = fetched.get(ns_sym)
            if df is not None and not df.empty:
                # ensure a consistent format (reset index)
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df = df.reset_index().copy()
                    # preserve any stale marker set by provider
                    if getattr(df, 'attrs', {}).get('is_stale'):
                        try:
                            df.attrs['is_stale'] = True
                        except Exception:
                            pass
                    all_data[orig_sym] = df
                except Exception:
                    all_data[orig_sym] = df

        # Do NOT perform aggressive single-symbol fallbacks. If a symbol is missing from the batch
        # response it will be treated as missing for this scan cycle. This avoids generating a storm
        return all_data

    def get_quote(self, symbol: str) -> dict:
        ns_sym = self._normalize_symbol(symbol)
        try:
            yf_acquire()
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
                record_rate_limit()
            logger.error(f"Failed to fetch quote for {symbol}: {e}")
            return {}

# ── Factory ─────────────────────────────────────────────────────────────────

def get_fetcher() -> DataFetcher:
    from config import DATA_PROVIDER
    if DATA_PROVIDER == "kite":
        from data_providers.kite_fetcher import KiteFetcher
        return KiteFetcher()
    return YFinanceFetcher()
