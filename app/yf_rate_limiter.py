"""
Global Yahoo Finance client-side rate limiter, backoff scheduler and circuit breaker.

Provides a small, dependency-free coordinator to protect the app from
YFinance 429 rate limits by:
  - limiting concurrent calls (semaphore)
  - enforcing a minimal interval between calls
  - tracking recent 429 events and tripping a circuit breaker when threshold exceeded
  - exposing helper backoff timings for retries (5s,15s,35s,75s) with jitter

This module is intentionally simple and safe-by-default.
"""
from __future__ import annotations
import threading
import time
import os
import random
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Concurrency & throttling tuning via env
_MAX_CONCURRENCY = int(os.getenv("YF_CONCURRENCY", "2"))
_MIN_INTERVAL_S = float(os.getenv("YF_MIN_INTERVAL_S", "2.5"))  # minimal spacing between calls
_RATE_WINDOW_S = int(os.getenv("YF_RATE_WINDOW_S", "60"))
_RATE_THRESHOLD = int(os.getenv("YF_RATE_THRESHOLD", "8"))      # trip circuit if >= in window
_COOLDOWN_SCHEDULE_S = [10, 20, 30, 45, 60]  # Capped at 60s max cooldown
_current_cooldown_idx = 0

_semaphore = threading.BoundedSemaphore(_MAX_CONCURRENCY)
_last_call_ts = 0.0
_lock = threading.Lock()

# Rate-limit tracking
_rate_count = 0
_rate_window_start = 0.0
# Circuit tripped until timestamp (0 = not tripped)
_circuit_tripped_until = 0.0


class CircuitOpenError(RuntimeError):
    pass


class YahooSessionScraper:
    """Maintains an authenticated Cookie + Crumb session for Yahoo Finance."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._session = None
                cls._instance._crumb = None
                cls._instance._last_warmup = 0.0
            return cls._instance

    def get_authenticated_session(self):
        with self._lock:
            now = time.time()
            if self._session is not None and self._crumb is not None and (now - self._last_warmup) < 3600:
                return self._session, self._crumb

            try:
                import requests
                s = requests.Session()
                s.headers.update({
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                })
                s.get('https://fc.yahoo.com', timeout=5)
                r_crumb = s.get('https://query1.finance.yahoo.com/v1/test/getcrumb', timeout=5)
                if r_crumb.status_code == 200 and r_crumb.text.strip():
                    self._session = s
                    self._crumb = r_crumb.text.strip()
                    self._last_warmup = now
                    logger.info(f"✅ [YahooSessionScraper] Authenticated Cookie+Crumb session ready (Crumb: {self._crumb[:5]}...).")
                    return self._session, self._crumb
            except Exception as e:
                logger.warning(f"⚠️ [YahooSessionScraper] Session warmup warning: {e}")

            return None, None

yahoo_scraper = YahooSessionScraper()


def _now() -> float:
    return time.monotonic()


def acquire(timeout: Optional[float] = None, context: str = "Unknown") -> bool:
    """Acquire permission to call Yahoo. Blocks if circuit is tripped."""
    global _last_call_ts
    while True:
        now = _now()
        sleep_time = 0
        with _lock:
            if _circuit_tripped_until and now < _circuit_tripped_until:
                sleep_time = _circuit_tripped_until - now
                
        if sleep_time > 0:
            logger.warning(f"🚦 [YF_RATE_LIMIT] Yahoo circuit open — cooldown active. Sleeping {sleep_time:.0f}s before retry. (Context: {context})")
            time.sleep(sleep_time)
            continue  # Try again after sleeping

        # NEW: Enforce min-interval BEFORE taking a semaphore slot
        with _lock:
            now = _now()
            if _circuit_tripped_until and now < _circuit_tripped_until:
                continue
                
            since = now - _last_call_ts
            sleep_for = 0
            if since < _MIN_INTERVAL_S:
                sleep_for = _MIN_INTERVAL_S - since
                _last_call_ts = now + sleep_for
            else:
                _last_call_ts = now

        if sleep_for > 0:
            logger.debug(f"⏱️ [YF_RATE_LIMIT] Enforcing min-interval spacing — sleeping {sleep_for*1000:.0f}ms. (Context: {context})")
            time.sleep(sleep_for)

        # Try to acquire semaphore — with heartbeat logging if we have to wait
        sem_start = _now()
        last_logged_sem = 0
        while True:
            acquired = _semaphore.acquire(timeout=1.0)
            if acquired:
                break
            sem_waited = int(_now() - sem_start)
            if sem_waited >= last_logged_sem + 30:
                last_logged_sem = sem_waited
                logger.debug(f"⏳ [YF_RATE_LIMIT] Yahoo semaphore full ({_MAX_CONCURRENCY} slots busy) — queued for {sem_waited}s. (Context: {context})")
            if timeout is not None and (_now() - sem_start) >= timeout:
                return False
        # We got the semaphore. Check circuit one more time in case it tripped while we waited.
        with _lock:
            now = _now()
            if _circuit_tripped_until and now < _circuit_tripped_until:
                _semaphore.release()
                continue

        return True


def release() -> None:
    try:
        _semaphore.release()
    except Exception as e:
        logger.warning(f"⚠️ [YF_RATE_LIMIT] Semaphore release error (possible double-release): {e}")


def record_success() -> None:
    """Reset the cooldown multiplier on successful fetch."""
    global _current_cooldown_idx, _rate_count
    with _lock:
        if _current_cooldown_idx > 0:
            logger.info("YF rate-limit cooldown reset to 30 seconds after successful fetch.")
        _current_cooldown_idx = 0
        _rate_count = 0


def record_rate_limit(context: str = "Unknown") -> None:
    """Record a 429 event. If events exceed threshold within window, trip the circuit."""
    global _rate_count, _rate_window_start, _circuit_tripped_until, _current_cooldown_idx
    now = _now()
    with _lock:
        if now - _rate_window_start > _RATE_WINDOW_S:
            _rate_window_start = now
            _rate_count = 0
        _rate_count += 1
        logger.warning(f"YF rate-limit event recorded ({_rate_count}/{_RATE_THRESHOLD}) in window (Context: {context})")
        if _rate_count >= _RATE_THRESHOLD:
            base_cooldown = _COOLDOWN_SCHEDULE_S[_current_cooldown_idx]
            jitter = random.uniform(1.0, 5.0)
            cooldown = round(base_cooldown + jitter, 1)
            _circuit_tripped_until = now + cooldown
            logger.warning(f"YF circuit tripped for {cooldown}s due to {_rate_count} rate-limit events (Context: {context})")
            _current_cooldown_idx = min(_current_cooldown_idx + 1, len(_COOLDOWN_SCHEDULE_S) - 1)
            _rate_count = 0


def is_circuit_open() -> bool:
    return _now() < _circuit_tripped_until


def get_backoff_delay(attempt: int) -> float:
    """Return backoff delay for attempt index (0-based) using fast schedule + jitter.

    Schedule: 1.0s, 2.0s, 4.0s (then give up)
    """
    schedule = [1.0, 2.0, 4.0]
    if attempt < 0:
        attempt = 0
    if attempt >= len(schedule):
        return schedule[-1]
    base = schedule[attempt]
    # jitter +/-20%
    jitter = base * 0.2
    return max(0.0, base + random.uniform(-jitter, jitter))


def safe_yf_call(fetch_fn, symbol: str = "", context: str = "Unknown", max_retries: int = 3):
    """
    Centralized Gateway for ALL Yahoo Finance API Calls.
    Enforces concurrency limits, minimal spacing, circuit breaker,
    429 detection, exponential backoff, and guaranteed semaphore release.
    """
    if is_circuit_open():
        logger.warning(f"🚦 [YF_GATEWAY] Yahoo circuit OPEN — skipping request for {symbol} ({context}).")
        return None

    last_exception = None
    for attempt in range(max_retries):
        if is_circuit_open():
            logger.warning(f"🚦 [YF_GATEWAY] Circuit tripped during retries — aborting {symbol} ({context}).")
            return None

        if not acquire(context=f"{context} | {symbol}"):
            logger.warning(f"⏳ [YF_GATEWAY] Could not acquire YF lock for {symbol} ({context}).")
            return None

        try:
            res = fetch_fn()
            record_success()
            return res
        except Exception as e:
            last_exception = e
            msg = str(e).lower()
            if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                record_rate_limit(context=f"{context} | {symbol}")
                if attempt < max_retries - 1:
                    delay = get_backoff_delay(attempt)
                    logger.warning(f"⚠️ [YF_GATEWAY] Rate limit event on {symbol} ({context}) [attempt {attempt+1}/{max_retries}]. Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    logger.warning(f"⚠️ [YF_GATEWAY] Max retries reached for {symbol} ({context}) due to rate limit: {e}")
                    return None
            else:
                logger.warning(f"⚠️ [YF_GATEWAY] Fetch error for {symbol} ({context}) [attempt {attempt+1}/{max_retries}]: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1.0 + random.uniform(0.1, 0.5))
                else:
                    return None
        finally:
            release()

    return None


