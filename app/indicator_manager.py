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
        Indicators are computed dynamically based on each indicator's specific minimum history length:
          - ATR14, RSI14: len(df) >= 14
          - EMA20, SMA20: len(df) >= 20
          - EMA50, SMA50: len(df) >= 50
          - EMA200, SMA200: len(df) >= 200
        """
        bundle = IndicatorBundle()
        if df is None or df.empty or len(df) < 14:
            return bundle
            
        try:
            n_bars = len(df)
            cols = set(df.columns)

            # 1. Short-window indicators (>= 14 bars)
            if n_bars >= 14 and 'High' in cols and 'Low' in cols and 'Close' in cols:
                # ATR 14
                if 'ATR' in cols:
                    bundle.atr_14 = df['ATR']
                else:
                    prev_close = df['Close'].shift()
                    high_low = df['High'] - df['Low']
                    high_close = np.abs(df['High'] - prev_close)
                    low_close = np.abs(df['Low'] - prev_close)
                    tr = np.maximum(high_low, np.maximum(high_close, low_close))
                    bundle.atr_14 = tr.rolling(window=14).mean()

                # RSI 14
                if 'RSI' in cols:
                    bundle.rsi_14 = df['RSI']
                else:
                    delta = df['Close'].diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                    rs = gain / loss
                    bundle.rsi_14 = 100 - (100 / (1 + rs))

            # 2. Medium-window indicators (>= 20 bars)
            if n_bars >= 20 and 'Close' in cols:
                bundle.ema_20 = df['EMA20'] if 'EMA20' in cols else df['Close'].ewm(span=20, adjust=False).mean()
                bundle.sma_20 = df['SMA20'] if 'SMA20' in cols else df['Close'].rolling(window=20).mean()

            # 3. 50-bar indicators (>= 50 bars)
            if n_bars >= 50 and 'Close' in cols:
                bundle.ema_50 = df['EMA50'] if 'EMA50' in cols else df['Close'].ewm(span=50, adjust=False).mean()
                bundle.sma_50 = df['SMA50'] if 'SMA50' in cols else df['Close'].rolling(window=50).mean()

            # 4. Long-window indicators (>= 200 bars)
            if n_bars >= 200 and 'Close' in cols:
                bundle.ema_200 = df['EMA200'] if 'EMA200' in cols else df['Close'].ewm(span=200, adjust=False).mean()
                bundle.sma_200 = df['SMA200'] if 'SMA200' in cols else df['Close'].rolling(window=200).mean()
            
            # 5. Save to registry (Dynamically register indicator dataset if not already registered)
            registry_key = f"indicator_{symbol}"
            if not self.registry.get_entry(registry_key):
                from data_registry import DatasetEntry, StorageTier
                self.registry.register_dataset(DatasetEntry(
                    id=registry_key, owner="IndicatorManager",
                    tier=StorageTier.EPHEMERAL, cadence=86400
                ))
            self.registry.put(registry_key, bundle)
            
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
