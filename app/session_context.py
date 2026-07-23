import time
import logging
import threading
import pandas as pd
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, date
from zoneinfo import ZoneInfo

from telemetry_manager import telemetry

logger = logging.getLogger("session_context")
IST = ZoneInfo("Asia/Kolkata")

@dataclass
class IndicatorBundle:
    ema_50: Optional[pd.Series] = None
    ema_200: Optional[pd.Series] = None
    atr_14: Optional[pd.Series] = None
    rsi_14: Optional[pd.Series] = None
    pivots: Optional[Dict[str, Any]] = None

@dataclass
class SymbolContext:
    symbol: str
    ohlcv: pd.DataFrame
    indicators: IndicatorBundle
    metadata: Dict[str, Any]

class SessionCache:
    def __init__(self):
        self.historical: Dict[str, SymbolContext] = {}
        self.fundamentals: Dict[str, Any] = {}
        self.market_regime: Dict[str, Any] = {}
        self._lock = threading.Lock()

class RuntimeState:
    def __init__(self):
        self.active_scanners: set = set()
        self.scanner_status: Dict[str, str] = {}
        self.active_alerts: int = 0
        self._lock = threading.Lock()

class HistoricalDataManager:
    def __init__(self, cache: SessionCache):
        self.cache = cache
        
    def get_symbol_context(self, symbol: str) -> Optional[SymbolContext]:
        with self.cache._lock:
            return self.cache.historical.get(symbol)
            
    def update_ohlcv(self, symbol: str, new_df: pd.DataFrame):
        with self.cache._lock:
            if symbol not in self.cache.historical:
                self.cache.historical[symbol] = SymbolContext(
                    symbol=symbol,
                    ohlcv=new_df,
                    indicators=IndicatorBundle(),
                    metadata={}
                )
            else:
                self.cache.historical[symbol].ohlcv = new_df

class SessionContext:
    """
    The orchestrator composing specialized managers for the trading session.
    Enforces a strict daily lifecycle and explicit mutability.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(SessionContext, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self.created_at = datetime.now(IST)
        self.session_cache = SessionCache()
        self.runtime_state = RuntimeState()
        
        # Managers
        self.historical = HistoricalDataManager(self.session_cache)
        self.telemetry = telemetry
        
    def get_mutable_copy(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Explicitly requests a mutable copy of the underlying OHLCV DataFrame.
        Scanners should normally operate on immutable references.
        """
        ctx = self.historical.get_symbol_context(symbol)
        if ctx and ctx.ohlcv is not None:
            return ctx.ohlcv.copy(deep=True)
        return None

# Global Singleton
session = SessionContext()
