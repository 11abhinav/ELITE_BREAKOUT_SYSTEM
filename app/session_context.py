import logging
import threading
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

logger = logging.getLogger("SessionContext")

# [VERSION: SESSION_ARCH_v2B_0]
# SessionContext rewritten to support Phase 2B global cache migration.
# Key additions:
#   - Session generation counter (process-lifetime, monotonically increasing)
#   - Real cache slots on each manager (populated during Step Xa migrations)
#   - Centralised CacheManager.get() / CacheManager.all_caches() accessor API
#   - No pandas import at module level (deferred to IndicatorBundle usage)


# ─────────────────────────────────────────────────────────────────────────────
# Session Generation Counter
# Increments each time a new SessionContext is created.  Exposed in all
# telemetry so stale references after midnight rotation are immediately visible.
# ─────────────────────────────────────────────────────────────────────────────
_generation_lock = threading.Lock()
_session_generation: int = 0


def _next_generation() -> int:
    global _session_generation
    with _generation_lock:
        _session_generation += 1
        return _session_generation


# ─────────────────────────────────────────────────────────────────────────────
# SessionState
# ─────────────────────────────────────────────────────────────────────────────

class SessionState(Enum):
    CREATED = auto()
    WARMING = auto()
    READY = auto()
    MARKET_OPEN = auto()
    POST_MARKET = auto()
    SHUTTING_DOWN = auto()
    DESTROYED = auto()


# ─────────────────────────────────────────────────────────────────────────────
# CachePolicy  (declarative, carries no execution logic)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CachePolicy:
    """
    Declarative policy object that describes the lifecycle of a cache,
    rather than embedding execution logic.
    """
    owner: str            # e.g. "MarketRegimeManager"
    persistence: str      # "SESSION" | "PERSISTENT"
    refresh_policy: str   # "EVERY_5_MIN" | "DAILY" | "ON_DEMAND" | "EVERY_60_MIN"
    expiration_policy: str  # "CONSUMER_DRIVEN" | "END_OF_DAY"
    estimated_size_mb: float = 0.0
    consumer_count: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# IndicatorBundle  (deferred import of pandas)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class IndicatorBundle:
    ema_50: Optional[Any] = None    # pd.Series
    ema_200: Optional[Any] = None
    atr_14: Optional[Any] = None
    rsi_14: Optional[Any] = None
    pivots: Optional[Dict[str, Any]] = None


# ─────────────────────────────────────────────────────────────────────────────
# Managers
# ─────────────────────────────────────────────────────────────────────────────

class IntradayStore:
    def __init__(self):
        self.data = {}
        self.policy = CachePolicy(
            owner="HistoricalDataManager.IntradayStore",
            persistence="SESSION",
            refresh_policy="EVERY_5_MIN",
            expiration_policy="CONSUMER_DRIVEN",
        )


class DailyStore:
    def __init__(self):
        self.data = {}
        self.policy = CachePolicy(
            owner="HistoricalDataManager.DailyStore",
            persistence="SESSION",
            refresh_policy="DAILY",
            expiration_policy="CONSUMER_DRIVEN",
        )


class DeliveryStore:
    def __init__(self):
        self.data = {}
        self.policy = CachePolicy(
            owner="HistoricalDataManager.DeliveryStore",
            persistence="SESSION",
            refresh_policy="ON_DEMAND",
            expiration_policy="CONSUMER_DRIVEN",
        )


class HistoricalDataManager:
    def __init__(self):
        self.intraday = IntradayStore()
        self.daily = DailyStore()
        self.delivery = DeliveryStore()


class IndicatorManager:
    def __init__(self):
        self.store: Dict[str, IndicatorBundle] = {}   # symbol → IndicatorBundle
        self.policy = CachePolicy(
            owner="IndicatorManager",
            persistence="SESSION",
            refresh_policy="ON_DEMAND",
            expiration_policy="CONSUMER_DRIVEN",
        )

    def get(self, symbol: str) -> Optional[IndicatorBundle]:
        return self.store.get(symbol)

    def put(self, symbol: str, bundle: IndicatorBundle) -> None:
        self.store[symbol] = bundle


class MarketRegimeManager:
    def __init__(self):
        # [VERSION: SESSION_ARCH_v2B_S1] Session-owned slot for _nifty_cache.
        # Populated during Step 1a migration. The dict identity is stable —
        # callers must use .update(), never full reassignment.
        self.cache: Dict[str, Any] = {"ret_6m": None, "dist_52w": None, "ts": None}
        self.policy = CachePolicy(
            owner="MarketRegimeManager",
            persistence="SESSION",
            refresh_policy="EVERY_60_MIN",
            expiration_policy="END_OF_DAY",
        )


class CacheManager:
    """
    Centralised accessor for all named session caches.

    Phase 2B migrates one global at a time into the named slots below.
    Callers use CacheManager.get(name) instead of per-module accessor helpers,
    keeping the number of nearly-identical helper functions to zero.
    """

    def __init__(self):
        self._policies: Dict[str, CachePolicy] = {}

        # ── Named cache slots (Phase 2B migrations populate these) ──────────
        # [VERSION: SESSION_ARCH_v2B_S1] _nifty_cache lives on MarketRegimeManager.
        # These slots are for the remaining 5 globals.
        self.dead_symbols: Dict[str, float] = {}        # Step 2: _dead_symbols_cache
        self.push_throttle: Dict[str, float] = {}       # Step 3: _push_throttle_cache
        self.indices: Dict[str, Any] = {                # Step 4: _indices_cache
            "data": None, "timestamp": 0
        }
        self.news: Dict[str, Any] = {}                  # Step 5: _news_cache
        self.wealth_payload: Dict[str, Any] = {         # Step 6: _wealth_cache
            "mtime": 0, "payload": None
        }

        # Internal name → slot mapping for centralised access
        self._named: Dict[str, dict] = {
            "dead_symbols":   self.dead_symbols,
            "push_throttle":  self.push_throttle,
            "indices":        self.indices,
            "news":           self.news,
            "wealth_payload": self.wealth_payload,
        }

    def get(self, name: str) -> Optional[dict]:
        """
        Return the named cache dict, or None if the name is unknown.
        Callers that previously accessed _wealth_cache directly now call
        session.cache_manager.get("wealth_payload") instead.
        """
        return self._named.get(name)

    def all_caches(self) -> Dict[str, dict]:
        """Return all named caches for telemetry/debugging."""
        return dict(self._named)

    def register_policy(self, name: str, policy: CachePolicy) -> None:
        self._policies[name] = policy

    def get_policy(self, name: str) -> Optional[CachePolicy]:
        return self._policies.get(name)


# ─────────────────────────────────────────────────────────────────────────────
# SessionContext
# ─────────────────────────────────────────────────────────────────────────────

class SessionContext:
    """
    Non-singleton orchestrator. Owned by ApplicationContext.
    Delegates entirely to specialized managers. Exposes services, not raw data.

    Each instance carries a monotonically increasing `generation` integer.
    Include this in telemetry to detect stale references after midnight rotation.
    """

    def __init__(self):
        self.generation: int = _next_generation()
        self.state = SessionState.CREATED

        # ── Managers ─────────────────────────────────────────────────────────
        self.historical = HistoricalDataManager()
        self.indicators = IndicatorManager()
        self.market_regime = MarketRegimeManager()
        self.cache_manager = CacheManager()

        # Register policies
        self.cache_manager.register_policy("intraday",      self.historical.intraday.policy)
        self.cache_manager.register_policy("daily",         self.historical.daily.policy)
        self.cache_manager.register_policy("delivery",      self.historical.delivery.policy)
        self.cache_manager.register_policy("indicators",    self.indicators.policy)
        self.cache_manager.register_policy("market_regime", self.market_regime.policy)

        logger.info(
            f"✅ SessionContext initialised | "
            f"generation={self.generation} | "
            f"state={self.state.name}"
        )

    # ── State machine ─────────────────────────────────────────────────────────

    def transition_to(self, target_state_name: str) -> None:
        """Enforces valid state machine transitions."""
        target_state = SessionState[target_state_name]

        valid_transitions = {
            SessionState.CREATED:       [SessionState.WARMING,      SessionState.SHUTTING_DOWN],
            SessionState.WARMING:       [SessionState.READY,         SessionState.SHUTTING_DOWN],
            SessionState.READY:         [SessionState.MARKET_OPEN,   SessionState.POST_MARKET,  SessionState.SHUTTING_DOWN],
            SessionState.MARKET_OPEN:   [SessionState.POST_MARKET,   SessionState.SHUTTING_DOWN],
            SessionState.POST_MARKET:   [SessionState.SHUTTING_DOWN],
            SessionState.SHUTTING_DOWN: [SessionState.DESTROYED],
            SessionState.DESTROYED:     [],
        }

        if target_state not in valid_transitions[self.state]:
            raise ValueError(
                f"Illegal transition: {self.state.name} → {target_state_name} "
                f"(generation={self.generation})"
            )

        self.state = target_state
        logger.info(
            f"Session state → {self.state.name} | generation={self.generation}"
        )

    # ── Destroy ───────────────────────────────────────────────────────────────

    def destroy(self) -> None:
        """Release all managers to prepare for garbage collection."""
        if self.state != SessionState.SHUTTING_DOWN:
            self.transition_to("SHUTTING_DOWN")

        logger.info(
            f"🗑️ Releasing SessionContext managers | generation={self.generation}"
        )
        self.historical = None
        self.indicators = None
        self.market_regime = None
        self.cache_manager = None

        self.transition_to("DESTROYED")
