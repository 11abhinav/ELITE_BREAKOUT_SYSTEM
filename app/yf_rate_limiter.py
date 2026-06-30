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
_MAX_CONCURRENCY = int(os.getenv("YF_CONCURRENCY", "6"))
_MIN_INTERVAL_S = float(os.getenv("YF_MIN_INTERVAL_S", "0.15"))  # minimal spacing between calls
_RATE_WINDOW_S = int(os.getenv("YF_RATE_WINDOW_S", "60"))
_RATE_THRESHOLD = int(os.getenv("YF_RATE_THRESHOLD", "5"))      # trip circuit if >= in window
_COOLDOWN_SCHEDULE_S = [5 * 60, 10 * 60, 15 * 60]
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
            logger.warning(f"Yahoo circuit open. Sleeping for {sleep_time:.0f}s... (Context: {context})")
            time.sleep(sleep_time)
            continue # Try again after sleeping
            
        with _lock:
            now = _now()
            # Enforce minimal interval
            since = now - _last_call_ts
            sleep_for = 0
            if since < _MIN_INTERVAL_S:
                sleep_for = _MIN_INTERVAL_S - since
            if sleep_for > 0:
                time.sleep(sleep_for)
            break # Exit the while True loop!
        # Acquire semaphore (may block)
    ok = _semaphore.acquire(timeout=timeout)
    if ok:
        with _lock:
            _last_call_ts = _now()
    return ok


def release() -> None:
    try:
        _semaphore.release()
    except Exception:
        pass


def record_success() -> None:
    """Reset the cooldown multiplier on successful fetch."""
    global _current_cooldown_idx, _rate_count
    with _lock:
        if _current_cooldown_idx > 0:
            logger.info("YF rate-limit cooldown reset to 5 minutes after successful fetch.")
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
            cooldown = _COOLDOWN_SCHEDULE_S[_current_cooldown_idx]
            _circuit_tripped_until = now + cooldown
            logger.error(f"YF circuit tripped for {cooldown}s due to {_rate_count} rate-limit events (Context: {context})")
            _current_cooldown_idx = min(_current_cooldown_idx + 1, len(_COOLDOWN_SCHEDULE_S) - 1)
            _rate_count = 0


def is_circuit_open() -> bool:
    return _now() < _circuit_tripped_until


def get_backoff_delay(attempt: int) -> float:
    """Return backoff delay for attempt index (0-based) using recommended schedule + jitter.

    Schedule: 5s, 15s, 35s, 75s (then give up)
    """
    schedule = [5.0, 15.0, 35.0, 75.0]
    if attempt < 0:
        attempt = 0
    if attempt >= len(schedule):
        return schedule[-1]
    base = schedule[attempt]
    # jitter +/-20%
    jitter = base * 0.2
    return max(0.0, base + random.uniform(-jitter, jitter))

