"""
app/price_provider.py

Lightweight centralized price provider adapter.

Goals:
- Provide batched yfinance downloads to minimize number of API hits.
- Provide an in-memory TTL cache shared across callers.
- Provide a simple rolling-window rate limiter (50/s, 500/min, 2000/30min by default).

Usage:
    from app.price_provider import PriceProvider

    provider = PriceProvider()
    data = provider.fetch_batch(tickers, period="5d", interval="5m")

Returned value: dict mapping ticker -> pandas.DataFrame of OHLCV for the requested interval.
"""

import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple
import random
import logging

import yfinance as yf

try:
    import pandas as pd
except Exception:
    pd = None

logger = logging.getLogger(__name__)


class RateLimiter:
    """Very small rolling-window limiter using timestamp deques.

    This is intentionally simple and good-enough for local single-process usage.
   """

    def __init__(self, per_second=50, per_minute=500, per_30min=2000):
        self.per_second = per_second
        self.per_minute = per_minute
        self.per_30min = per_30min
        self.lock = threading.Lock()
        self.ts = deque()  # timestamps of requests

    def _cleanup(self, now: float):
        # remove entries older than 30min
        cutoff = now - 1800
        while self.ts and self.ts[0] < cutoff:
            self.ts.popleft()

    def allow(self, n: int = 1) -> bool:
        """Return True if we can allow n additional requests now."""
        now = time.time()
        with self.lock:
            self._cleanup(now)
            # counts in windows
            total_30 = len(self.ts)
            # count last minute and last second
            cutoff_min = now - 60
            cutoff_sec = now - 1
            count_min = 0
            count_sec = 0
            for t in reversed(self.ts):
                if t >= cutoff_sec:
                    count_sec += 1
                    count_min += 1
                elif t >= cutoff_min:
                    count_min += 1
                else:
                    break

            if (count_sec + n) > self.per_second:
                return False
            if (count_min + n) > self.per_minute:
                return False
            if (total_30 + n) > self.per_30min:
                return False
            # allow: record timestamps
            for _ in range(n):
                self.ts.append(now)
            return True

    def wait_for_slot(self, n: int = 1, timeout: float = 10.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.allow(n):
                return True
            time.sleep(0.05)
        return False


class PriceProvider:
    def __init__(self,
                 batch_size: int = 200,
                 cache_ttl: int = 60,
                 yf_threads: bool = False,
                 rate_limiter: RateLimiter = None,
                 cooldown_seconds: int = 15 * 60,
                 max_retries: int = 3):
        self.batch_size = batch_size
        self.cache_ttl = cache_ttl
        self.yf_threads = yf_threads
        # cache keyed per-symbol: (symbol, period, interval) -> (expiry_ts, value)
        self.cache: Dict[Tuple[str, str, str], Tuple[float, object]] = {}
        self.cache_lock = threading.Lock()
        self.rate_limiter = rate_limiter or RateLimiter()
        # Reactive circuit breaker timestamp (when set, no live calls until > this)
        self.cooldown_until = 0.0
        self.cooldown_seconds = cooldown_seconds
        # retry policy
        self.max_retries = max_retries

    def _cache_get(self, key, allow_stale: bool = False):
        """Return (value, is_stale). If not present, (None, False). If expired and allow_stale True,
        return the value with is_stale=True instead of deleting it.
        """
        with self.cache_lock:
            item = self.cache.get(key)
            if not item:
                return None, False
            expiry, value = item
            now = time.time()
            if now > expiry:
                if allow_stale:
                    return value, True
                # keep stale around as fallback, but report as missing
                return None, False
            return value, False

    def _cache_set(self, key, value, ttl: int = None):
        if ttl is None:
            ttl = self.cache_ttl
        with self.cache_lock:
            self.cache[key] = (time.time() + ttl, value)

    def _download_batch(self, tickers: List[str], period: str, interval: str, start: str = None, end: str = None):
        """Download a batch of tickers via yfinance and return mapping ticker->DataFrame."""
        if not tickers:
            return {}
        now = time.time()
        # If circuit breaker is open, raise to let caller handle fallback
        if now < self.cooldown_until:
            raise RuntimeError("Circuit open: cooling down due to recent rate limits")

        # Attempt with retries + jittered backoff for transient errors (including 429 patterns)
        attempts = 0
        backoffs = [5, 15, 35]
        last_exc = None
        while attempts < self.max_retries:
            # rate-limit the call as a single request
            if not self.rate_limiter.wait_for_slot(1, timeout=5.0):
                raise RuntimeError("Rate limit exceeded; try again later")

            tickers_arg = " ".join(tickers)
            try:
                if start and end:
                    df = yf.download(tickers=tickers_arg, start=start, end=end, interval=interval, group_by='ticker', threads=self.yf_threads, progress=False, timeout=60)
                else:
                    df = yf.download(tickers=tickers_arg, period=period, interval=interval, group_by='ticker', threads=self.yf_threads, progress=False, timeout=60)
                # success
                last_exc = None
                break
            except Exception as e:
                last_exc = e
                msg = str(e).lower()
                attempts += 1
                is_rate = ('too many requests' in msg) or ('rate limit' in msg) or ('429' in msg)
                logger.warning(f"yfinance batch download failed (attempt {attempts}/{self.max_retries}) for {len(tickers)} tickers: {e}")
                if is_rate:
                    # If this is a rate limit, and we've exhausted retries, open circuit
                    if attempts >= self.max_retries:
                        self.cooldown_until = time.time() + self.cooldown_seconds
                        logger.error("Tripping circuit breaker due to repeated rate limits")
                        raise
                    # otherwise backoff and retry
                # general backoff before retrying
                if attempts < self.max_retries:
                    delay = backoffs[min(attempts - 1, len(backoffs) - 1)]
                    # add jitter
                    jitter = random.uniform(0.5, 1.5)
                    sleep_for = delay * jitter
                    logger.info(f"Retrying batch download after {sleep_for:.1f}s")
                    time.sleep(sleep_for)
                    continue
        if last_exc is not None:
            # retries exhausted -- propagate to caller so fetch_batch can return stale values
            raise last_exc

        result = {}
        # yfinance returns different shapes depending on number of tickers
        if isinstance(df, dict) or (pd is not None and isinstance(df, pd.DataFrame) and isinstance(df.columns, pd.MultiIndex)):
            # multi-ticker result
            try:
                for t in tickers:
                    if isinstance(df, dict):
                        result[t] = df.get(t)
                    else:
                        # extract columns for ticker (tickers are in level 0)
                        if t in df.columns.get_level_values(0):
                            result[t] = df[t].dropna(how='all')
                        else:
                            result[t] = None
            except Exception:
                # best-effort fallback
                for t in tickers:
                    result[t] = None
        else:
            # single ticker DataFrame
            if isinstance(df, type(None)):
                for t in tickers:
                    result[t] = None
            else:
                # assume all tickers map to same frame (rare)
                result[tickers[0]] = df

        return result

    def fetch_batch(self, tickers: List[str], period: str = "5d", interval: str = "5m", start: str = None, end: str = None) -> Dict[str, object]:
        """Fetch OHLCV data for tickers in batches. Returns ticker->DataFrame mapping.

        This function will batch the tickers into groups of `batch_size`, consult cache per-batch
        and call yfinance only for missing/expired batches.
        """
        if not tickers:
            return {}

        try:
            from bse_mapping_utils import load_bse_mappings
            mappings = load_bse_mappings()
        except Exception as e:
            logger.error(f"Failed to load bse mappings in price_provider: {e}")
            mappings = {}

        # Map each input ticker to its resolved yfinance ticker, preserving the original ticker key
        resolved_to_orig = {}
        resolved_tickers = []
        for t in tickers:
            clean_sym = t.strip().upper()
            if clean_sym in mappings:
                res_sym = mappings[clean_sym]
            elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
                res_sym = mappings[clean_sym[:-3]]
            else:
                res_sym = t if t.endswith(".NS") or t.endswith(".BO") or t.startswith("^") else f"{t}.NS"
            
            resolved_tickers.append(res_sym)
            resolved_to_orig[res_sym] = t

        # normalize resolved_tickers order and dedupe
        resolved_tickers = list(dict.fromkeys(resolved_tickers))

        outputs: Dict[str, object] = {}
        missing = []
        stale_map = {}

        now = time.time()
        # If circuit is open, we will not attempt live downloads; instead return stale values where possible
        circuit_open = now < self.cooldown_until

        # First consult per-symbol cache. If stale exists, keep it in stale_map and schedule for refresh.
        for t in resolved_tickers:
            key = (t, period, interval, start, end)
            val, is_stale = self._cache_get(key, allow_stale=True)
            if val is not None and not is_stale:
                outputs[t] = val
            else:
                # either missing or stale -> we will attempt a live refresh for these
                missing.append(t)
                if val is not None and is_stale:
                    stale_map[t] = val

        # If circuit open, return stale values where available and None for the rest
        if circuit_open:
            for t in resolved_tickers:
                if outputs.get(t) is None:
                    stale_val = stale_map.get(t)
                    if stale_val is not None:
                        try:
                            if hasattr(stale_val, 'attrs'):
                                stale_val.attrs['is_stale'] = True
                        except Exception:
                            pass
                        outputs[t] = stale_val
                    else:
                        outputs[t] = None
        else:
            # Batch missing symbols and download
            batches = [missing[i:i + self.batch_size] for i in range(0, len(missing), self.batch_size)]
            if batches:
                try:
                    with ThreadPoolExecutor(max_workers=min(4, len(batches))) as ex:
                        futures = {ex.submit(self._download_batch, batch, period, interval, start, end): idx for idx, batch in enumerate(batches)}
                        for fut in as_completed(futures, timeout=300):
                            idx = futures[fut]
                            batch = batches[idx]
                            try:
                                res = fut.result()
                                # cache per-symbol and merge
                                for t, frame in res.items():
                                    # if frame is None and we had a stale fallback, preserve stale
                                    if frame is None:
                                        if t in stale_map:
                                            stale_val = stale_map[t]
                                            try:
                                                if hasattr(stale_val, 'attrs'):
                                                    stale_val.attrs['is_stale'] = True
                                            except Exception:
                                                pass
                                            outputs[t] = stale_val
                                        else:
                                            outputs[t] = None
                                        # don't overwrite cache in this case
                                    else:
                                        self._cache_set((t, period, interval, start, end), frame)
                                        outputs[t] = frame
                            except Exception as e:
                                # On failure (possibly rate limit), return stale values for this batch where available
                                logger.warning(f"Batch download failed for batch of {len(batch)} tickers: {e}")
                                for t in batch:
                                    if t in stale_map:
                                        stale_val = stale_map[t]
                                        try:
                                            if hasattr(stale_val, 'attrs'):
                                                stale_val.attrs['is_stale'] = True
                                        except Exception:
                                            pass
                                        outputs[t] = stale_val
                                    else:
                                        outputs[t] = None
                except concurrent.futures.TimeoutError:
                    logger.error("❌ Timeout during batch download in price_provider. Aborting remaining batches to prevent deadlock.")

        # ensure all resolved tickers present in outputs (None if missing)
        for t in resolved_tickers:
            outputs.setdefault(t, None)

        # Batch-level .BO Fallback
        missing_ns_to_bo = {}
        for t, val in outputs.items():
            # Only fallback if it's completely missing (or stale but we want to try BO for fresh)
            is_stale_df = hasattr(val, 'attrs') and val.attrs.get('is_stale', False)
            is_missing_df = val is None or (hasattr(val, 'empty') and val.empty)
            if (is_missing_df or is_stale_df) and t.endswith(".NS"):
                bo_sym = t[:-3] + ".BO"
                missing_ns_to_bo[bo_sym] = t

        if missing_ns_to_bo and not circuit_open:
            bo_symbols = list(missing_ns_to_bo.keys())
            logger.info(f"🔄 price_provider: {len(bo_symbols)} .NS symbols missing. Attempting .BO fallback...")
            try:
                bo_res = self._download_batch(bo_symbols, period, interval, start, end)
                if bo_res:
                    from bse_mapping_utils import save_bse_mapping
                    for bo_sym, frame in bo_res.items():
                        if frame is not None and not frame.empty:
                            orig_ns = missing_ns_to_bo[bo_sym]
                            orig_req = resolved_to_orig.get(orig_ns, orig_ns)
                            
                            # Clean up the original request symbol for mapping (remove any trailing .NS or .BO)
                            clean_orig = orig_req
                            if clean_orig.endswith(".NS") or clean_orig.endswith(".BO"):
                                clean_orig = clean_orig[:-3]
                                
                            save_bse_mapping(clean_orig, bo_sym)
                            logger.info(f"✅ price_provider: Recovered {orig_ns} via {bo_sym}")
                            
                            self._cache_set((bo_sym, period, interval, start, end), frame)
                            outputs[orig_ns] = frame
            except Exception as e:
                logger.warning(f"Failed during .BO batch fallback: {e}")

        # Map output keys back to original requested tickers
        final_outputs = {}
        for res_sym, frame in outputs.items():
            orig = resolved_to_orig.get(res_sym, res_sym)
            final_outputs[orig] = frame

        # ensure all originally requested tickers are present in the final output
        for t in tickers:
            final_outputs.setdefault(t, None)

        return final_outputs


__all__ = ["PriceProvider", "RateLimiter"]

