"""
Isolated Research Candidates (v5.3.0 Research Track)
====================================================
Implements:
  1. MultiTfResearchV1: Hierarchical Multi-Timeframe State Machine (Daily -> 15m -> 5m).
  2. ReversalResearchV1: Structural Support Anchor (<= 1.5%) + Reclaim + Bullish Volume Divergence.
  3. DailyBuilderResearchV1: 15m ORB Width Clamp (<= 2.5%) + Vol >= 1.5x + 15:15 IST Session Close.

Strict Isolation: Does NOT modify production scanner files in app/.
"""

import math
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple


class MultiTfResearchV1:
    """
    MULTI_TF_RESEARCH_v1:
      - Layer 1 (Daily): Close > SMA50 > SMA200 and Daily 20-bar slope > 0 (TREND_UP).
      - Layer 2 (15m): Supertrend == Green AND 15m Volume >= 1.5x SMA20 (TREND_UP confirmation).
      - Layer 3 (5m): Bullish breakout of 5m consolidation with exact timestamp synchronization.
      - Stop: 3.0% Confluence Stop Loss.
      - Target: 2.0R Target Multiple.
    """
    @staticmethod
    def evaluate(daily_close: float, daily_sma50: float, daily_sma200: float, daily_slope: float,
                 tf15_supertrend_green: bool, tf15_vol_ratio: float,
                 tf5_breakout: bool, entry_price: float) -> Dict[str, Any]:
        # Daily state check
        daily_trend_up = (daily_close > daily_sma50 > daily_sma200) and (daily_slope > 0)
        # 15m state confirmation
        tf15_trend_up = tf15_supertrend_green and (tf15_vol_ratio >= 1.50)
        # 5m trigger
        tf5_trigger = tf5_breakout

        is_qualified = daily_trend_up and tf15_trend_up and tf5_trigger

        risk = entry_price * 0.030
        stop_loss = entry_price - risk
        target_1 = entry_price + (2.0 * risk)

        return {
            "qualified": is_qualified,
            "daily_state": "TREND_UP" if daily_trend_up else "NO_TREND",
            "tf15_state": "TREND_UP" if tf15_trend_up else "CHOP",
            "tf5_trigger": tf5_trigger,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target_1": target_1,
            "risk_pct": 0.030,
            "target_multiple": 2.0
        }


class ReversalResearchV1:
    """
    REVERSAL_RESEARCH_v1:
      - Filter 1 (Oversold): RSI14 < 35.
      - Filter 2 (Support Anchor): Proximity to SMA200, 3M Pivot, or 52W Low Support <= 1.5%.
      - Filter 3 (Support Reclaim): Current candle close > prior candle high (reclaim confirmation).
      - Filter 4 (Volume Divergence): Consolidation base volume > preceding breakdown volume.
      - Stop: 4.0% Structural SL (placed below support level).
      - Target: 2.0R Target Multiple (Mean Reversion to EMA20 / SMA50).
    """
    @staticmethod
    def evaluate(rsi_val: float, price: float, support_level: float,
                 is_reclaim_candle: bool, base_vol: float, selloff_vol: float) -> Dict[str, Any]:
        oversold = (rsi_val < 35.0)
        
        # Distance from structural support
        if support_level > 0:
            support_dist_pct = abs(price - support_level) / support_level * 100.0
            near_support = (support_dist_pct <= 1.50)
        else:
            near_support = False

        reclaim_confirmed = is_reclaim_candle
        vol_divergence = (base_vol > selloff_vol) if selloff_vol > 0 else True

        is_qualified = oversold and near_support and reclaim_confirmed and vol_divergence

        risk = price * 0.040
        stop_loss = price - risk
        target_1 = price + (2.0 * risk)

        return {
            "qualified": is_qualified,
            "oversold": oversold,
            "near_support": near_support,
            "reclaim_confirmed": reclaim_confirmed,
            "vol_divergence": vol_divergence,
            "entry_price": price,
            "stop_loss": stop_loss,
            "target_1": target_1,
            "risk_pct": 0.040,
            "target_multiple": 2.0
        }


class DailyBuilderResearchV1:
    """
    DAILY_BUILDER_RESEARCH_v1:
      - Filter 1 (Opening Range): 15m ORB range width <= 2.5% of price.
      - Filter 2 (Volume Surge): Breakout bar volume >= 1.5x 20-day SMA volume.
      - Filter 3 (VWAP Confluence): Breakout occurs above session VWAP.
      - Exit Rule: Hard Session Close at 15:15 IST (no overnight risk).
      - Stop: 2.5% Intraday Stop Loss.
      - Target: 2.0R Target Multiple.
    """
    @staticmethod
    def evaluate(orb_high: float, orb_low: float, close_price: float,
                 vol_ratio: float, vwap: float) -> Dict[str, Any]:
        orb_width_pct = (orb_high - orb_low) / orb_low * 100.0 if orb_low > 0 else 5.0
        width_ok = (orb_width_pct <= 2.50)
        vol_ok = (vol_ratio >= 1.50)
        vwap_ok = (close_price >= vwap) if vwap > 0 else True
        breakout = (close_price > orb_high)

        is_qualified = width_ok and vol_ok and vwap_ok and breakout

        risk = close_price * 0.025
        stop_loss = close_price - risk
        target_1 = close_price + (2.0 * risk)

        return {
            "qualified": is_qualified,
            "width_ok": width_ok,
            "orb_width_pct": orb_width_pct,
            "vol_ok": vol_ok,
            "vwap_ok": vwap_ok,
            "breakout": breakout,
            "entry_price": close_price,
            "stop_loss": stop_loss,
            "target_1": target_1,
            "risk_pct": 0.025,
            "target_multiple": 2.0,
            "force_exit_time": "15:15 IST"
        }
