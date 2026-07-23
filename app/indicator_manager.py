import pandas as pd
import numpy as np
from typing import Dict, Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger("indicator_manager")

@dataclass
class IndicatorBundle:
    ema_20: Optional[pd.Series] = None
    ema_50: Optional[pd.Series] = None
    ema_200: Optional[pd.Series] = None
    sma_20: Optional[pd.Series] = None
    sma_50: Optional[pd.Series] = None
    sma_200: Optional[pd.Series] = None
    atr_14: Optional[pd.Series] = None
    rsi_14: Optional[pd.Series] = None
    pivots: Optional[Dict[str, Any]] = None

class IndicatorManager:
    """
    Computes and manages indicators incrementally to avoid full DataFrame rebuilds.
    """
    def __init__(self):
        from data_registry import registry
        self.registry = registry

    def compute_base_indicators(self, df: pd.DataFrame, symbol: str) -> IndicatorBundle:
        """
        Computes the base indicators (EMA, ATR, RSI, Pivots) for a symbol's historical dataframe.
        """
        bundle = IndicatorBundle()
        if df.empty or len(df) < 200:
            return bundle
            
        try:
            # 1. EMAs & SMAs
            bundle.ema_20 = df['Close'].ewm(span=20, adjust=False).mean()
            bundle.ema_50 = df['Close'].ewm(span=50, adjust=False).mean()
            bundle.ema_200 = df['Close'].ewm(span=200, adjust=False).mean()
            bundle.sma_20 = df['Close'].rolling(window=20).mean()
            bundle.sma_50 = df['Close'].rolling(window=50).mean()
            bundle.sma_200 = df['Close'].rolling(window=200).mean()
            
            # 2. ATR 14
            prev_close = df['Close'].shift()
            high_low = df['High'] - df['Low']
            high_close = np.abs(df['High'] - prev_close)
            low_close = np.abs(df['Low'] - prev_close)
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            bundle.atr_14 = tr.rolling(window=14).mean()
            
            # 3. RSI 14
            delta = df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            bundle.rsi_14 = 100 - (100 / (1 + rs))
            
            # 4. Save to registry
            self.registry.put(f"indicator_{symbol}", bundle)
            
        except Exception as e:
            logger.error(f"Error computing base indicators for {symbol}: {e}")
            
        return bundle

    def update_last_row(self, symbol: str, bar: Dict[str, float]) -> None:
        """
        Incremental intraday update to avoid full recomputation.
        """
        bundle = self.registry.get(f"indicator_{symbol}")
        if not bundle:
            return # Cannot update incrementally without base
            
        # Implementation for incremental update goes here.
        # This requires tracking the previous EWM values, which we skip in this stub.
        pass

    def guard_recompute(self, phase: str):
        if phase == "INTRADAY":
            raise RuntimeError("Full indicator recompute attempted during INTRADAY phase! Use update_last_row instead.")

# Global instance
manager = IndicatorManager()
