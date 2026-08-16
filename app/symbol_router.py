"""
[VERSION: SYMBOL_ROUTER_V1.0] Capability-Aware Intelligent Symbol Router
RATIONALE:
  - Dynamically learns provider capabilities at the (symbol, interval) tuple level.
  - Distinguishes permanent/semantic failures (UNSUPPORTED_SYMBOL, INVALID_INSTRUMENT, NOT_FOUND)
    from transient failures (TIMEOUT, 429 RATE_LIMIT, 5XX SERVER_ERROR, NETWORK_ERROR).
  - Permanent failures set sticky routes (UPSTOX_ONLY / FYERS_ONLY), avoiding redundant failed queries.
  - Transient failures remain LOAD_BALANCED to avoid permanent topology skew during temporary provider outages.
  - Session-aware + 24-hour TTL revalidation allows self-healing route recovery if upstream providers fix issues.
  - All read/write operations are thread-safe under a lightweight mutex.
"""

import time
import logging
from enum import Enum
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
from threading import Lock
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

class RoutingState(str, Enum):
    LOAD_BALANCED = "LOAD_BALANCED"
    UPSTOX_ONLY = "UPSTOX_ONLY"
    FYERS_ONLY = "FYERS_ONLY"

class ProviderErrorCode(str, Enum):
    # Sticky / Permanent Failure Codes
    UNSUPPORTED_SYMBOL = "UNSUPPORTED_SYMBOL"
    INVALID_INSTRUMENT = "INVALID_INSTRUMENT"
    NOT_FOUND = "NOT_FOUND"

    # Non-Sticky / Transient Failure Codes
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"

@dataclass
class RouteEntry:
    state: RoutingState
    reason: ProviderErrorCode
    confidence: str  # "HIGH" (Permanent/Sticky) or "LOW" (Transient)
    learned_at: float
    session_date: str

class SymbolRouter:
    """
    Thread-safe Capability-Aware Provider Router.
    Routes queries based on capability key: (clean_symbol, interval).
    """
    def __init__(self, sticky_ttl_seconds: float = 86400.0):  # 24-hour TTL
        self._lock = Lock()
        self._routes: Dict[Tuple[str, str], RouteEntry] = {}
        self.sticky_ttl_seconds = sticky_ttl_seconds
        
        # Telemetry metrics
        self.avoided_failed_requests = 0
        self.routing_fallbacks = 0

    def _normalize_key(self, symbol: str, interval: str) -> Tuple[str, str]:
        """Normalize symbol and interval into canonical tuple key."""
        clean_sym = str(symbol or "").strip().upper()
        if clean_sym.endswith(".NS"): clean_sym = clean_sym[:-3]
        if clean_sym.endswith(".BO"): clean_sym = clean_sym[:-3]
        clean_interval = str(interval or "1d").strip().lower()
        return (clean_sym, clean_interval)

    def get_route(self, symbol: str, interval: str) -> RoutingState:
        """
        Thread-safe lookup of routing state for (symbol, interval).
        Handles session-boundary and 24h TTL expiration automatically.
        """
        key = self._normalize_key(symbol, interval)
        now_mono = time.monotonic()
        today_ist = datetime.now(IST).strftime("%Y-%m-%d")

        with self._lock:
            entry = self._routes.get(key)
            if entry is None:
                return RoutingState.LOAD_BALANCED

            # Check expiration: if session date changed AND age > 24 hours, expire route for self-healing
            is_expired = (entry.session_date != today_ist) and ((now_mono - entry.learned_at) >= self.sticky_ttl_seconds)
            if is_expired:
                logger.info(
                    f"🔄 [ROUTING_REVALIDATION] Expired sticky route for {key[0]} [{key[1]}] "
                    f"(was {entry.state.value} due to {entry.reason.value}). Re-probing via LOAD_BALANCED."
                )
                del self._routes[key]
                return RoutingState.LOAD_BALANCED

            if entry.state in (RoutingState.UPSTOX_ONLY, RoutingState.FYERS_ONLY):
                self.avoided_failed_requests += 1

            return entry.state

    def classify_error_code(self, error_msg: str, http_code: Optional[int] = None) -> ProviderErrorCode:
        """
        Structured taxonomy classification of error message and HTTP status code.
        """
        msg = str(error_msg or "").lower()
        if http_code == 404 or "not found" in msg or "invalid symbol" in msg or "unsupported" in msg or "symbol miss" in msg:
            if "invalid symbol" in msg or "unsupported" in msg:
                return ProviderErrorCode.UNSUPPORTED_SYMBOL
            elif "not found" in msg or http_code == 404:
                return ProviderErrorCode.NOT_FOUND
            return ProviderErrorCode.INVALID_INSTRUMENT
        
        if http_code == 429 or "rate" in msg or "too many" in msg:
            return ProviderErrorCode.RATE_LIMIT
        if http_code in (500, 502, 503, 504) or "server error" in msg or "503" in msg:
            return ProviderErrorCode.SERVER_ERROR
        if "timeout" in msg or "timed out" in msg:
            return ProviderErrorCode.TIMEOUT
        if "connection" in msg or "reset" in msg or "socket" in msg:
            return ProviderErrorCode.NETWORK_ERROR
            
        return ProviderErrorCode.UNKNOWN

    def record_result(
        self,
        symbol: str,
        interval: str,
        provider: str,
        is_success: bool,
        error_msg: str = None,
        http_code: Optional[int] = None,
        err_code: Optional[ProviderErrorCode] = None
    ):
        """
        Record fetch attempt outcome for (symbol, interval).
        If permanent/sticky failure occurs, updates state to UPSTOX_ONLY / FYERS_ONLY.
        If revalidation succeeds, restores state to LOAD_BALANCED.
        """
        key = self._normalize_key(symbol, interval)
        now_mono = time.monotonic()
        today_ist = datetime.now(IST).strftime("%Y-%m-%d")
        prov = str(provider or "").lower()

        if err_code is None and not is_success:
            err_code = self.classify_error_code(error_msg, http_code)

        with self._lock:
            existing = self._routes.get(key)

            if is_success:
                # If a previously sticky route succeeds during revalidation, restore to LOAD_BALANCED
                if existing and existing.state != RoutingState.LOAD_BALANCED:
                    logger.info(
                        f"✅ [ROUTING_RECOVERY] Provider '{prov}' successfully fetched {key[0]} [{key[1]}]. "
                        f"Restoring route state from {existing.state.value} -> LOAD_BALANCED."
                    )
                    del self._routes[key]
                return

            # Handle Failure: ONLY sticky/permanent error codes trigger state change
            sticky_codes = (
                ProviderErrorCode.UNSUPPORTED_SYMBOL,
                ProviderErrorCode.INVALID_INSTRUMENT,
                ProviderErrorCode.NOT_FOUND
            )

            if err_code in sticky_codes:
                target_state = RoutingState.UPSTOX_ONLY if "fyers" in prov else RoutingState.FYERS_ONLY
                
                # Check if state is changing
                if existing is None or existing.state != target_state:
                    self._routes[key] = RouteEntry(
                        state=target_state,
                        reason=err_code,
                        confidence="HIGH",
                        learned_at=now_mono,
                        session_date=today_ist
                    )
                    logger.warning(
                        f"📌 [ROUTING_LEARN] Capability Override Learned | Key=({key[0]}, {key[1]}) | "
                        f"FailedProvider={prov.upper()} | Error={err_code.value} | TargetState={target_state.value}"
                    )
            else:
                logger.debug(
                    f"ℹ️ [ROUTING_TRANSIENT] Non-sticky transient error for {key[0]} [{key[1]}] on {prov.upper()}: "
                    f"{err_code.value if err_code else 'TRANSIENT'} (State remains LOAD_BALANCED)"
                )

    def record_fallback_event(self):
        """Track runtime fallbacks from primary to secondary provider."""
        with self._lock:
            self.routing_fallbacks += 1

    def get_telemetry_summary(self) -> dict:
        """Return snapshot of provider routing telemetry."""
        with self._lock:
            upstox_only = sum(1 for e in self._routes.values() if e.state == RoutingState.UPSTOX_ONLY)
            fyers_only = sum(1 for e in self._routes.values() if e.state == RoutingState.FYERS_ONLY)
            total_sticky = len(self._routes)
            return {
                "upstox_only_count": upstox_only,
                "fyers_only_count": fyers_only,
                "total_sticky_routes": total_sticky,
                "avoided_failed_requests": self.avoided_failed_requests,
                "routing_fallbacks": self.routing_fallbacks
            }

    def reset_telemetry(self):
        """Reset telemetry counters for new run."""
        with self._lock:
            self.avoided_failed_requests = 0
            self.routing_fallbacks = 0

# Singleton Global Router Instance
symbol_router = SymbolRouter()
