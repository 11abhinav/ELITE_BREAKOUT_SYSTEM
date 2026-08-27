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
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Tuple
import random
import logging

import yfinance as yf
from core_enums import ProviderResult

import pandas as pd

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
        self.cooldown_seconds = 15  # 15 seconds fast cooldown instead of 15 mins (900s)
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
        # If circuit breaker is open, allow small fallback batches (<= 5 tickers) to attempt recovery with backoff
        if now < self.cooldown_until and len(tickers) > 5:
            raise RuntimeError("Circuit open: cooling down due to recent rate limits")

        # Attempt with retries + jittered backoff for transient errors (including 429 patterns)
        attempts = 0
        backoffs = [5, 15, 35]
        last_exc = None
        while attempts < self.max_retries:
            # rate-limit the call based on the number of tickers we are fetching concurrently
            if not self.rate_limiter.wait_for_slot(len(tickers), timeout=5.0):
                raise RuntimeError("Rate limit exceeded; try again later")

            tickers_arg = " ".join(tickers)
            try:
                start_time = time.monotonic()
                if start and end:
                    df = yf.download(tickers=tickers_arg, start=start, end=end, interval=interval, group_by='ticker', threads=self.yf_threads, progress=False, timeout=60, auto_adjust=True)
                else:
                    df = yf.download(tickers=tickers_arg, period=period, interval=interval, group_by='ticker', threads=self.yf_threads, progress=False, timeout=60, auto_adjust=True)
                
                duration = time.monotonic() - start_time
                bytes_dl = df.memory_usage(deep=False).sum() if (pd is not None and hasattr(df, 'memory_usage')) else 0
                if isinstance(bytes_dl, pd.Series):
                    bytes_dl = bytes_dl.sum()
                
                # success
                last_exc = None
                break
            except Exception as e:
                duration = time.monotonic() - start_time
                last_exc = e
                msg = str(e).lower()
                attempts += 1
                is_rate = ('too many requests' in msg) or ('rate limit' in msg) or ('429' in msg)
                
                status_code = 429 if is_rate else (403 if '403' in msg else 500)
                is_timeout = 'timeout' in msg
                
                logger.warning(f"yfinance batch download failed (attempt {attempts}/{self.max_retries}) for {len(tickers)} tickers: {e}")
                if is_rate:
                    # If this is a rate limit, and we've exhausted retries, open circuit
                    if attempts >= self.max_retries:
                        self.cooldown_until = time.time() + self.cooldown_seconds
                        logger.error("Tripping circuit breaker due to repeated rate limits")
                        return {t: ProviderResult.RATE_LIMIT for t in tickers}
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
            # retries exhausted -- propagate as network error
            msg = str(last_exc).lower()
            res = ProviderResult.RATE_LIMIT if ('429' in msg or 'too many' in msg or 'rate limit' in msg) else ProviderResult.NETWORK_ERROR
            return {t: res for t in tickers}

        result = {}
        errors_dict = getattr(yf.shared, '_ERRORS', {}) if hasattr(yf, 'shared') else {}
        
        # yfinance returns different shapes depending on number of tickers
        if isinstance(df, dict) or (pd is not None and isinstance(df, pd.DataFrame) and isinstance(df.columns, pd.MultiIndex)):
            # multi-ticker result
            try:
                for t in tickers:
                    if isinstance(df, dict):
                        f = df.get(t)
                    else:
                        # extract columns for ticker (tickers are in level 0)
                        if t in df.columns.get_level_values(0):
                            f = df[t].dropna(how='all')
                        else:
                            f = None
                            
                    if f is None or f.empty:
                        err_msg = str(errors_dict.get(t, '')).lower()
                        if 'too many requests' in err_msg or 'rate limit' in err_msg or '429' in err_msg or 'yfratelimiterror' in err_msg:
                            result[t] = ProviderResult.RATE_LIMIT
                            if self.cooldown_until < time.time():
                                self.cooldown_until = time.time() + self.cooldown_seconds
                                logger.warning(f"🚫 Yahoo Finance rate limit detected for {t}. Tripping circuit breaker for {self.cooldown_seconds}s.")
                        elif 'delisted' in err_msg or 'not found' in err_msg or 'no timezone' in err_msg:
                            result[t] = ProviderResult.NOT_FOUND
                        else:
                            result[t] = ProviderResult.EMPTY_DATA
                    else:
                        result[t] = f
            except Exception:
                # best-effort fallback
                for t in tickers:
                    result[t] = ProviderResult.EMPTY_DATA
        else:
            # single ticker DataFrame
            if isinstance(df, type(None)) or (hasattr(df, 'empty') and df.empty):
                for t in tickers:
                    err_msg = str(errors_dict.get(t, '')).lower()
                    if 'too many requests' in err_msg or 'rate limit' in err_msg or '429' in err_msg or 'yfratelimiterror' in err_msg:
                        result[t] = ProviderResult.RATE_LIMIT
                        if self.cooldown_until < time.time():
                            self.cooldown_until = time.time() + self.cooldown_seconds
                            logger.warning(f"🚫 Yahoo Finance rate limit detected for {t}. Tripping circuit breaker for {self.cooldown_seconds}s.")
                    elif 'delisted' in err_msg or 'not found' in err_msg or 'no timezone' in err_msg:
                        result[t] = ProviderResult.NOT_FOUND
                    else:
                        result[t] = ProviderResult.EMPTY_DATA
            else:
                # assume all tickers map to same frame (rare)
                result[tickers[0]] = df

        return result

    def _normalize_symbol(self, symbol: str) -> str:
        upper_sym = symbol.strip().upper()
        
        # Map index symbols to YFinance ticker format
        if upper_sym in ("NIFTY 50", "NIFTY", "NIFTY-50", "NIFTY50", "^NSEI", "NSEI", "NIFTY 50.NS", "NIFTY 50.BO", "NIFTY-50.NS", "NIFTY-50.BO", "NIFTY50.NS", "NIFTY50.BO"):
            return "^NSEI"
        if upper_sym in ("BANKNIFTY", "BANK NIFTY", "BANK-NIFTY", "^NSEBANK", "NSEBANK", "BANKNIFTY.NS", "BANKNIFTY.BO", "BANK-NIFTY.NS", "BANK-NIFTY.BO"):
            return "^NSEBANK"
        if upper_sym in ("SENSEX", "^BSESN", "BSESN", "BSE:SENSEX-INDEX", "SENSEX.BO", "SENSEX.NS"):
            return "^BSESN"

        # Check DB mappings first
        try:
            from bse_mapping_utils import load_bse_mappings
            mappings = load_bse_mappings()
            if upper_sym in mappings:
                return mappings[upper_sym]
            if upper_sym.endswith(".NS") and upper_sym[:-3] in mappings:
                return mappings[upper_sym[:-3]]
        except Exception:
            pass

        # Handle suffix stripping and is_bse logic
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

        # Apply corrections
        try:
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
                base_sym = base_sym.replace("_", "-")
        except Exception:
            base_sym = base_sym.replace("_", "-")

        if base_sym.startswith("^"):
            return base_sym
        yf_sym = f"{base_sym}.BO" if is_bse else f"{base_sym}.NS"

        # ── FORMAT GATE: validate Yahoo Finance symbol format before returning ──────────
        try:
            from symbol_format_validator import validate_yahoo_symbol
            yf_sym = validate_yahoo_symbol(yf_sym)
        except Exception:
            pass
        return yf_sym

    def fetch_batch(self, tickers: List[str], period: str = "5d", interval: str = "5m", start: str = None, end: str = None) -> Dict[str, object]:
        """Fetch OHLCV data for tickers in batches. Returns ticker->DataFrame mapping.

        This function will batch the tickers into groups of `batch_size`, consult cache per-batch
        and call yfinance only for missing/expired batches.
        """
        if not tickers:
            return {}

        # Map each input ticker to its resolved yfinance ticker, preserving all original ticker keys (1:N)
        resolved_to_orig = {}
        resolved_tickers = []
        for t in tickers:
            res_sym = self._normalize_symbol(t)
            resolved_tickers.append(res_sym)
            resolved_to_orig.setdefault(res_sym, []).append(t)

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

        # If circuit open, return stale values where available and RATE_LIMIT for the rest
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
                        outputs[t] = ProviderResult.RATE_LIMIT
        else:
            # Batch missing symbols and download
            batches = [missing[i:i + self.batch_size] for i in range(0, len(missing), self.batch_size)]
            # Run batches concurrently but staggered to prevent Yahoo rate limiting
            if batches:
                try:
                    with ThreadPoolExecutor(max_workers=3) as ex:
                        futures = {}
                        for idx, batch in enumerate(batches):
                            if idx > 0:
                                time.sleep(2.0)  # Delay between batches to prevent WAF trip
                            futures[ex.submit(self._download_batch, batch, period, interval, start, end)] = idx
                        for fut in as_completed(futures, timeout=1800):
                            idx = futures[fut]
                            batch = batches[idx]
                            try:
                                res = fut.result()
                                # cache per-symbol and merge
                                for t, frame in res.items():
                                    # if frame is ProviderResult and we had a stale fallback, preserve stale
                                    if isinstance(frame, ProviderResult):
                                        if t in stale_map:
                                            stale_val = stale_map[t]
                                            try:
                                                if hasattr(stale_val, 'attrs'):
                                                    stale_val.attrs['is_stale'] = True
                                            except Exception:
                                                pass
                                            outputs[t] = stale_val
                                        else:
                                            outputs[t] = frame
                                        # Cache the result to prevent infinite polling spam
                                        self._cache_set((t, period, interval, start, end), frame)
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
                                        outputs[t] = ProviderResult.NETWORK_ERROR
                                    # Cache to prevent infinite polling spam
                                    self._cache_set((t, period, interval, start, end), ProviderResult.NETWORK_ERROR)
                except concurrent.futures.TimeoutError:
                    logger.error("❌ Timeout during batch download in price_provider. Aborting remaining batches to prevent deadlock.")

        # ensure all resolved tickers present in outputs
        for t in resolved_tickers:
            outputs.setdefault(t, ProviderResult.EMPTY_DATA)

        # Batch-level .BO Fallback & Poisoned Mapping Fix
        missing_ns_to_bo = {}
        poisoned_bo_symbols = []
        for t, val in outputs.items():
            if isinstance(val, ProviderResult):
                if val in (ProviderResult.NOT_FOUND, ProviderResult.EMPTY_DATA):
                    if t.endswith(".NS"):
                        bo_sym = t[:-3] + ".BO"
                        missing_ns_to_bo[bo_sym] = t
                    elif t.endswith(".BO"):
                        poisoned_bo_symbols.append(t)
            else:
                is_stale_df = hasattr(val, 'attrs') and val.attrs.get('is_stale', False)
                is_missing_df = val is None or (hasattr(val, 'empty') and val.empty)
                if is_missing_df or is_stale_df:
                    if t.endswith(".NS"):
                        bo_sym = t[:-3] + ".BO"
                        missing_ns_to_bo[bo_sym] = t
                    elif t.endswith(".BO"):
                        poisoned_bo_symbols.append(t)

        # [VERSION: POISONED_MAPPING_FIX_v1.0] Reverse Fallback (BSE -> NSE)
        if poisoned_bo_symbols:
            logger.info(f"🗑️ price_provider: Handling {len(poisoned_bo_symbols)} poisoned BSE mappings and retrying via NSE...")
            try:
                from bse_mapping_utils import mark_bse_invalid
                ns_symbols = []
                for bo_sym in poisoned_bo_symbols:
                    ns_sym = bo_sym[:-3] + ".NS"
                    ns_symbols.append(ns_sym)
                    
                    # Invalidate in DB for each mapped original ticker IF it was a true NOT_FOUND
                    if isinstance(outputs.get(bo_sym), ProviderResult) and outputs.get(bo_sym) == ProviderResult.NOT_FOUND:
                        orig_list = resolved_to_orig.get(bo_sym, [bo_sym])
                        for orig in orig_list:
                            clean_orig = orig[:-3] if orig.endswith(".NS") or orig.endswith(".BO") else orig
                            mark_bse_invalid(clean_orig)
                        
                ns_res = self._download_batch(ns_symbols, period, interval, start, end)
                if ns_res:
                    for ns_sym, frame in ns_res.items():
                        bo_sym = ns_sym[:-3] + ".BO"
                        if isinstance(frame, ProviderResult):
                            outputs[bo_sym] = frame
                            self._cache_set((ns_sym, period, interval, start, end), frame)
                        elif frame is not None and not frame.empty:
                            logger.info(f"✅ price_provider: Recovered {bo_sym} via {ns_sym}")
                            self._cache_set((ns_sym, period, interval, start, end), frame)
                            outputs[bo_sym] = frame
                        else:
                            self._cache_set((ns_sym, period, interval, start, end), ProviderResult.EMPTY_DATA)
                            outputs[bo_sym] = ProviderResult.EMPTY_DATA
            except Exception as e:
                logger.warning(f"Failed during Reverse Fallback: {e}")

        # Normal NSE -> BSE Fallback
        if missing_ns_to_bo:
            bo_symbols = list(missing_ns_to_bo.keys())
            logger.info(f"🔄 price_provider: {len(bo_symbols)} .NS symbols missing. Attempting .BO fallback...")
            try:
                bo_res = self._download_batch(bo_symbols, period, interval, start, end)
                if bo_res:
                    from bse_mapping_utils import save_bse_mapping
                    for bo_sym, frame in bo_res.items():
                        orig_ns = missing_ns_to_bo[bo_sym]
                        if isinstance(frame, ProviderResult):
                            outputs[orig_ns] = frame
                            self._cache_set((bo_sym, period, interval, start, end), frame)
                        elif frame is not None and not frame.empty:
                            orig_list = resolved_to_orig.get(orig_ns, [orig_ns])
                            for orig in orig_list:
                                clean_orig = orig[:-3] if orig.endswith(".NS") or orig.endswith(".BO") else orig
                                save_bse_mapping(clean_orig, bo_sym)
                                
                            logger.info(f"✅ price_provider: Recovered {orig_ns} via {bo_sym}")
                            
                            self._cache_set((bo_sym, period, interval, start, end), frame)
                            outputs[orig_ns] = frame
                        else:
                            # Cache the missed fallback so we don't hammer yfinance
                            self._cache_set((bo_sym, period, interval, start, end), ProviderResult.EMPTY_DATA)
                            outputs[orig_ns] = ProviderResult.EMPTY_DATA
            except Exception as e:
                logger.warning(f"Failed during .BO batch fallback: {e}")

        # Map output keys back to all original requested tickers (1:N)
        final_outputs = {}
        for res_sym, frame in outputs.items():
            orig_list = resolved_to_orig.get(res_sym, [res_sym])
            for orig in orig_list:
                final_outputs[orig] = frame

        # ensure all originally requested tickers are present in the final output
        for t in tickers:
            final_outputs.setdefault(t, ProviderResult.EMPTY_DATA)

        return final_outputs


__all__ = ["PriceProvider", "RateLimiter"]

