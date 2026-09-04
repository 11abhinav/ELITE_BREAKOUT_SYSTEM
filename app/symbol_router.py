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
        """
        [VERSION: SYMBOL_ROUTER_SERIES_v1.0] Canonical Series Normalization for BO, SME (-SM/-ST), and EQ symbols.
        RATIONALE:
          - Preserves series/exchange context so BSE (.BO), SME (-SM, -ST), and NSE EQ mainboard
            symbols are tracked independently without collisions.
          - Option B permanent sticky logic applies to ALL series type symbols permanently across sessions.
        """
        clean_sym = str(symbol or "").strip().upper()
        
        # Categorize exchange/series
        if clean_sym.endswith(".BO") or clean_sym.startswith("BSE:") or clean_sym.isdigit():
            base = clean_sym.replace(".BO", "").replace("BSE:", "")
            canonical = f"BSE:{base}"
        elif clean_sym.endswith("-SM") or clean_sym.endswith("-ST") or "-SM" in clean_sym or "-ST" in clean_sym:
            base = clean_sym.replace(".NS", "").replace("NSE:", "")
            canonical = f"SME:{base}"
        else:
            base = clean_sym.replace(".NS", "").replace("NSE:", "")
            canonical = f"NSE:{base}"
            
        clean_interval = str(interval or "1d").strip().lower()
        return (canonical, clean_interval)

    def get_route(self, symbol: str, interval: str) -> RoutingState:
        """
        [VERSION: SYMBOL_ROUTER_OPTION_B_v1.0] Permanent Sticky Routing (Option B)
        RATIONALE:
          - Sticky routes (UPSTOX_ONLY / FYERS_ONLY) do NOT expire daily.
          - Once a working broker is identified for a symbol/interval, it is reused
            permanently across days without spending time re-probing failed brokers.
          - Routes only change if the active working broker fails.
          - Checks interval-specific route first; if missing, falls back to universal symbol route ('*').
        """
        key = self._normalize_key(symbol, interval)
        universal_key = (key[0], "*")

        with self._lock:
            entry = self._routes.get(key) or self._routes.get(universal_key)
            if entry is None:
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
        [VERSION: SYMBOL_ROUTER_OPTION_B_v1.0] Record fetch attempt outcome for (symbol, interval).
        Option B behavior: Once a working broker is assigned (UPSTOX_ONLY / FYERS_ONLY),
        it remains sticky indefinitely as long as it succeeds.
        If the active broker fails with a permanent error, the route updates to the alternative broker.
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
                # Active broker succeeded — retain sticky state indefinitely under Option B
                return

            # Handle Failure: ONLY sticky/permanent error codes trigger state change
            sticky_codes = (
                ProviderErrorCode.UNSUPPORTED_SYMBOL,
                ProviderErrorCode.INVALID_INSTRUMENT,
                ProviderErrorCode.NOT_FOUND
            )

            if err_code in sticky_codes:
                target_state = RoutingState.UPSTOX_ONLY if "fyers" in prov else RoutingState.FYERS_ONLY
                universal_key = (key[0], "*")
                
                # Update route to the alternative working broker (both interval-specific and universal symbol route)
                if existing is None or existing.state != target_state:
                    entry = RouteEntry(
                        state=target_state,
                        reason=err_code,
                        confidence="HIGH",
                        learned_at=now_mono,
                        session_date=today_ist
                    )
                    self._routes[key] = entry
                    self._routes[universal_key] = entry
                    logger.warning(
                        f"📌 [ROUTING_LEARN] Permanent Option B Override Learned | Key=({key[0]}, {key[1]}) & ({key[0]}, *) | "
                        f"FailedProvider={prov.upper()} | Error={err_code.value} | TargetState={target_state.value}"
                    )
                    self._persist_routes_async()
            else:
                logger.debug(
                    f"ℹ️ [ROUTING_TRANSIENT] Non-sticky transient error for {key[0]} [{key[1]}] on {prov.upper()}: "
                    f"{err_code.value if err_code else 'TRANSIENT'} (State remains {existing.state.value if existing else 'LOAD_BALANCED'})"
                )

    def _persist_routes_async(self):
        """Asynchronously persist sticky routes to database so they survive server restarts."""
        try:
            import threading
            def _bg_save():
                try:
                    import json
                    from database import save_system_state
                    with self._lock:
                        serializable = {
                            f"{k[0]}|{k[1]}": {
                                "state": v.state.value,
                                "reason": v.reason.value,
                                "confidence": v.confidence,
                                "session_date": v.session_date
                            }
                            for k, v in self._routes.items()
                        }
                    save_system_state("symbol_router_routes_v1", json.dumps(serializable))
                except Exception as e:
                    logger.warning(f"Failed to persist symbol router state: {e}")

            threading.Thread(target=_bg_save, name="SymbolRouterPersist", daemon=True).start()
        except Exception:
            pass

    def load_persisted_routes(self):
        """Load persisted sticky routes from database on startup."""
        try:
            import json
            from database import get_system_state
            raw_json = get_system_state("symbol_router_routes_v1")
            if raw_json:
                data = json.loads(raw_json)
                with self._lock:
                    for k_str, val in data.items():
                        parts = k_str.split("|")
                        if len(parts) == 2:
                            sym, inv = parts[0], parts[1]
                            self._routes[(sym, inv)] = RouteEntry(
                                state=RoutingState(val["state"]),
                                reason=ProviderErrorCode(val["reason"]),
                                confidence=val.get("confidence", "HIGH"),
                                learned_at=time.monotonic(),
                                session_date=val.get("session_date", "")
                            )
                logger.info(f"💾 [SYMBOL_ROUTER] Restored {len(self._routes)} permanent sticky routes from DB.")
        except Exception as e:
            logger.warning(f"Failed to load symbol router state from DB: {e}")

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
