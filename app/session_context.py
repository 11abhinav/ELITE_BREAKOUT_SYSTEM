import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
import pandas as pd

logger = logging.getLogger("SessionContext")


class SessionState(Enum):
    CREATED = auto()
    WARMING = auto()
    READY = auto()
    MARKET_OPEN = auto()
    POST_MARKET = auto()
    SHUTTING_DOWN = auto()
    DESTROYED = auto()


@dataclass
class CachePolicy:
    """
    Declarative policy object that describes the lifecycle of a cache, 
    rather than embedding execution logic.
    """
    owner: str  # e.g., "HistoricalDataManager.IntradayStore"
    persistence: str  # e.g., "SESSION", "PERSISTENT"
    refresh_policy: str  # e.g., "EVERY_5_MIN", "DAILY", "ON_DEMAND"
    expiration_policy: str  # e.g., "CONSUMER_DRIVEN", "END_OF_DAY"
    estimated_size_mb: float = 0.0
    consumer_count: int = 0


@dataclass
class IndicatorBundle:
    ema_50: Optional[pd.Series] = None
    ema_200: Optional[pd.Series] = None
    atr_14: Optional[pd.Series] = None
    rsi_14: Optional[pd.Series] = None
    pivots: Optional[Dict[str, Any]] = None


# -------------------------------------------------------------------------
# Manager Interfaces
# -------------------------------------------------------------------------

class IntradayStore:
    def __init__(self):
        self.data = {}
        self.policy = CachePolicy(
            owner="HistoricalDataManager.IntradayStore",
            persistence="SESSION",
            refresh_policy="EVERY_5_MIN",
            expiration_policy="CONSUMER_DRIVEN"
        )

class DailyStore:
    def __init__(self):
        self.data = {}
        self.policy = CachePolicy(
            owner="HistoricalDataManager.DailyStore",
            persistence="SESSION",
            refresh_policy="DAILY",
            expiration_policy="CONSUMER_DRIVEN"
        )

class DeliveryStore:
    def __init__(self):
        self.data = {}
        self.policy = CachePolicy(
            owner="HistoricalDataManager.DeliveryStore",
            persistence="SESSION",
            refresh_policy="ON_DEMAND",
            expiration_policy="CONSUMER_DRIVEN"
        )

class HistoricalDataManager:
    def __init__(self):
        self.intraday = IntradayStore()
        self.daily = DailyStore()
        self.delivery = DeliveryStore()


class IndicatorManager:
    def __init__(self):
        self.policy = CachePolicy(
            owner="IndicatorManager",
            persistence="SESSION",
            refresh_policy="ON_DEMAND",
            expiration_policy="CONSUMER_DRIVEN"
        )


class MarketRegimeManager:
    def __init__(self):
        self.policy = CachePolicy(
            owner="MarketRegimeManager",
            persistence="SESSION",
            refresh_policy="EVERY_5_MIN",
            expiration_policy="END_OF_DAY"
        )


class CacheManager:
    def __init__(self):
        self.managed_policies: Dict[str, CachePolicy] = {}

    def register_policy(self, name: str, policy: CachePolicy):
        self.managed_policies[name] = policy


# -------------------------------------------------------------------------
# SessionContext 
# -------------------------------------------------------------------------

class SessionContext:
    """
    Non-singleton orchestrator. Owned by ApplicationContext.
    Delegates entirely to specialized managers. Exposes services, not raw data.
    """
    def __init__(self):
        self.state = SessionState.CREATED
        
        # Initialize Managers
        self.historical = HistoricalDataManager()
        self.indicators = IndicatorManager()
        self.market_regime = MarketRegimeManager()
        self.cache_manager = CacheManager()
        
        # Register policies
        self.cache_manager.register_policy("intraday", self.historical.intraday.policy)
        self.cache_manager.register_policy("daily", self.historical.daily.policy)
        self.cache_manager.register_policy("delivery", self.historical.delivery.policy)
        self.cache_manager.register_policy("indicators", self.indicators.policy)
        self.cache_manager.register_policy("market_regime", self.market_regime.policy)
        
        logger.info(f"SessionContext Initialized in state: {self.state.name}")

    def transition_to(self, target_state_name: str):
        """Enforces valid state machine transitions."""
        target_state = SessionState[target_state_name]
        
        valid_transitions = {
            SessionState.CREATED: [SessionState.WARMING, SessionState.SHUTTING_DOWN],
            SessionState.WARMING: [SessionState.READY, SessionState.SHUTTING_DOWN],
            SessionState.READY: [SessionState.MARKET_OPEN, SessionState.POST_MARKET, SessionState.SHUTTING_DOWN],
            SessionState.MARKET_OPEN: [SessionState.POST_MARKET, SessionState.SHUTTING_DOWN],
            SessionState.POST_MARKET: [SessionState.SHUTTING_DOWN],
            SessionState.SHUTTING_DOWN: [SessionState.DESTROYED],
            SessionState.DESTROYED: []
        }
        
        if target_state not in valid_transitions[self.state]:
            raise ValueError(f"Illegal transition: {self.state.name} -> {target_state_name}")
            
        self.state = target_state
        logger.info(f"Session state transitioned to: {self.state.name}")

    def destroy(self):
        """Releases all managers to prepare for garbage collection."""
        if self.state != SessionState.SHUTTING_DOWN:
            self.transition_to("SHUTTING_DOWN")
            
        # Clear references
        self.historical = None
        self.indicators = None
        self.market_regime = None
        self.cache_manager = None
        
        self.transition_to("DESTROYED")
