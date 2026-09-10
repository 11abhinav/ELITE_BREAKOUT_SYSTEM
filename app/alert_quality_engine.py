# =====================================================================================
# app/alert_quality_engine.py
# INSTITUTIONAL ALERT QUALITY & SIGNAL ATTRIBUTION MEASUREMENT ENGINE (Release 2)
# =====================================================================================

import math
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger("alert_quality_engine")

# Scanner-Specific Post-SL Recovery Observation Horizons (in trading days/bars)
SCANNER_RECOVERY_HORIZONS: Dict[str, int] = {
    "MULTI_TF": 2,           # Intraday to next session
    "EOD": 20,               # 10–20 trading days
    "EOD_BREAKOUT": 20,
    "PULLBACK": 20,          # 15–20 trading days
    "REVERSAL": 10,          # 7–10 trading days
    "ACCUMULATION": 40,      # 20–40 trading days
    "MULTIBAGGER": 120,      # 60–120 trading days
    "SHORT_COVERING": 10,
    "DEFAULT": 20
}


class AlertQualityEngine:
    """
    Forensic outcome measurement and empirical signal attribution engine.
    
    Responsibilities:
      1. Same-Bar Conflict & Intrabar Path Resolution (conservatively handles collisions).
      2. The +1.0R / +1.5R / +2.0R Sequential Quality Ladder.
      3. Maximum Favorable (MFE) and Adverse Excursion (MAE) in R.
      4. Scanner-Specific Post-SL Recovery Profiling (min excursion past SL, recovery to Entry/T1, max recovery R).
      5. Execution Distortion Decomposition (Slippage, Gap, Circuit Distance, Turnover).
      6. Sector & Earnings Context Tracking without Hard-Blocking Signal Generation.
    """

    @staticmethod
    def get_recovery_horizon(scanner: str) -> int:
        clean_scanner = str(scanner).upper().strip()
        return SCANNER_RECOVERY_HORIZONS.get(clean_scanner, SCANNER_RECOVERY_HORIZONS["DEFAULT"])

    @staticmethod
    def build_signal_snapshot(
        symbol: str,
        scanner: str,
        score: float,
        regime: str,
        sector_name: str = "",
        sector_regime: str = "NEUTRAL",
        sector_rs_pctile: float = 50.0,
        stock_vs_sector_rs: float = 0.0,
        days_to_earnings: int = 999,
        vol_ratio: float = 1.0,
        atr_pct: float = 2.5,
        bb_width_pctile: float = 0.5,
        raw_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Captures the pure signal state at the exact moment of generation.
        Preserves research signal even when event_risk is triggered.
        """
        is_event_risk = (days_to_earnings is not None and 0 <= days_to_earnings <= 3)
        live_trade_allowed = not is_event_risk

        return {
            "symbol": symbol.strip().upper(),
            "scanner": scanner.strip().upper(),
            "score": float(score),
            "regime": str(regime).upper(),
            "sector_name": str(sector_name),
            "sector_regime": str(sector_regime).upper(),
            "sector_relative_strength": float(sector_rs_pctile),
            "stock_vs_sector_rs": float(stock_vs_sector_rs),
            "days_to_earnings": int(days_to_earnings) if days_to_earnings is not None else 999,
            "event_risk": is_event_risk,
            "live_trade_allowed": live_trade_allowed,
            "signal_generated": True,
            "vol_ratio": float(vol_ratio),
            "atr_pct": float(atr_pct),
            "bb_width_pctile": float(bb_width_pctile),
            "snapshot_timestamp": datetime.utcnow().isoformat(),
            "raw_metadata": raw_metadata or {}
        }

    @staticmethod
    def build_execution_snapshot(
        trigger_level: float,
        fill_price: float,
        prev_close: float,
        upper_circuit: float = 0.0,
        turnover_cr: float = 0.0
    ) -> Dict[str, Any]:
        """
        Decomposes execution quality and friction (slippage, opening gap, circuit distance).
        """
        trigger = float(trigger_level)
        fill = float(fill_price) if fill_price > 0 else trigger
        p_close = float(prev_close) if prev_close > 0 else fill

        slippage_pct = ((fill - trigger) / trigger * 100.0) if trigger > 0 else 0.0
        gap_pct = ((fill - p_close) / p_close * 100.0) if p_close > 0 else 0.0
        circuit_dist_pct = ((upper_circuit - fill) / fill * 100.0) if (upper_circuit > fill and fill > 0) else 999.0

        return {
            "trigger_level": trigger,
            "fill_price": fill,
            "slippage_pct": round(slippage_pct, 2),
            "gap_pct": round(gap_pct, 2),
            "circuit_dist_pct": round(circuit_dist_pct, 2),
            "turnover_cr": round(float(turnover_cr), 2)
        }

    @classmethod
    def evaluate_trade_outcome(
        cls,
        entry_price: float,
        stop_loss: float,
        target_1: float,
        target_2: Optional[float],
        price_df: pd.DataFrame,
        scanner: str = "EOD",
        intraday_df: Optional[pd.DataFrame] = None
    ) -> Dict[str, Any]:
        """
        Evaluates the chronological trade path, quality ladder, same-bar conflicts, and post-SL recovery.
        """
        entry = float(entry_price)
        sl = float(stop_loss)
        t1 = float(target_1)
        risk_dist = max(0.01, entry - sl)
        target_dist = max(0.01, t1 - entry)

        if price_df is None or price_df.empty:
            return {
                "status": "INSUFFICIENT_DATA",
                "realized_rr": 0.0,
                "exit_reason": "NO_DATA",
                "r1_hit_before_sl": False,
                "r1_5_hit_before_sl": False,
                "r2_hit_before_sl": False
            }

        # Calculate target levels in R
        price_1_0r = entry + (1.0 * risk_dist)
        price_1_5r = entry + (1.5 * risk_dist)
        price_2_0r = entry + (2.0 * risk_dist)

        highs = price_df["High"].values
        lows = price_df["Low"].values
        opens = price_df["Open"].values
        closes = price_df["Close"].values
        num_bars = len(price_df)

        running_mfe_r = 0.0
        running_mae_r = 0.0

        r1_hit = False
        r1_5_hit = False
        r2_hit = False

        exit_reason = "OPEN"
        exit_bar_idx = -1
        exit_date = None
        realized_rr = 0.0
        same_bar_conflict = False

        for idx in range(num_bars):
            b_open = float(opens[idx])
            b_high = float(highs[idx])
            b_low = float(lows[idx])
            b_close = float(closes[idx])
            bar_date = str(price_df.index[idx])[:10]

            # Update running MFE and MAE
            mfe_here = (b_high - entry) / risk_dist
            mae_here = (entry - b_low) / risk_dist
            if mfe_here > running_mfe_r:
                running_mfe_r = mfe_here
            if mae_here > running_mae_r:
                running_mae_r = mae_here

            # Check sequential R milestones prior to SL
            if b_high >= price_1_0r:
                r1_hit = True
            if b_high >= price_1_5r:
                r1_5_hit = True
            if b_high >= price_2_0r:
                r2_hit = True

            hit_target = (b_high >= t1)
            hit_sl = (b_low <= sl)

            # Intrabar collision resolution
            if hit_target and hit_sl:
                same_bar_conflict = True
                # Check intraday resolution if provided
                resolved_order = "UNKNOWN"
                if intraday_df is not None and not intraday_df.empty:
                    # Search intraday bars for bar_date
                    day_ticks = intraday_df[intraday_df.index.astype(str).str.startswith(bar_date)]
                    if not day_ticks.empty:
                        for _, tick in day_ticks.iterrows():
                            t_high = float(tick.get("High", tick["Close"]))
                            t_low = float(tick.get("Low", tick["Close"]))
                            if t_low <= sl:
                                resolved_order = "STOP_FIRST"
                                break
                            if t_high >= t1:
                                resolved_order = "TARGET_FIRST"
                                break

                if resolved_order == "TARGET_FIRST":
                    exit_reason = "T1_HIT"
                    exit_bar_idx = idx
                    exit_date = bar_date
                    realized_rr = round((t1 - entry) / risk_dist, 2)
                    break
                else:
                    # Conservative assignment: STOP_FIRST or UNKNOWN -> -1.0R loss
                    exit_reason = "SAME_BAR_CONFLICT_SL"
                    exit_bar_idx = idx
                    exit_date = bar_date
                    realized_rr = -1.0
                    break

            elif hit_sl:
                exit_reason = "SL_HIT"
                exit_bar_idx = idx
                exit_date = bar_date
                # Gap-through-SL slippage check
                if b_open < sl:
                    realized_rr = round((b_open - entry) / risk_dist, 2)
                else:
                    realized_rr = -1.0
                break

            elif hit_target:
                exit_reason = "T1_HIT"
                exit_bar_idx = idx
                exit_date = bar_date
                realized_rr = round((t1 - entry) / risk_dist, 2)
                break

        # If trade remains open after max horizon, record unrealized R
        if exit_reason == "OPEN":
            last_close = float(closes[-1])
            unrealized_rr = round((last_close - entry) / risk_dist, 2)
            exit_reason = "EXPIRED_POS" if unrealized_rr >= 0 else "EXPIRED_NEG"
            exit_date = str(price_df.index[-1])[:10]
            realized_rr = unrealized_rr
            exit_bar_idx = num_bars - 1

        # Post-SL Recovery Decomposition
        post_sl_metrics = {
            "post_sl_min_excursion_r": 0.0,
            "post_sl_recovered_entry": False,
            "post_sl_recovered_t1": False,
            "post_sl_max_recovery_r": 0.0,
            "post_sl_recovery_bars": 0
        }

        if exit_reason in ["SL_HIT", "SAME_BAR_CONFLICT_SL"] and exit_bar_idx >= 0:
            horizon = cls.get_recovery_horizon(scanner)
            excursion_df = price_df.iloc[exit_bar_idx : exit_bar_idx + 1 + horizon]
            post_sl_df = price_df.iloc[exit_bar_idx + 1 : exit_bar_idx + 1 + horizon]

            if not excursion_df.empty:
                deepest_low = excursion_df["Low"].values.min()
                adverse_past_sl_r = max(0.0, (sl - deepest_low) / risk_dist)
                post_sl_metrics["post_sl_min_excursion_r"] = round(-1.0 - adverse_past_sl_r, 2)

            if not post_sl_df.empty:
                post_highs = post_sl_df["High"].values

                # Highest price after SL
                highest_post = post_highs.max()
                recovery_r = (highest_post - entry) / risk_dist
                post_sl_metrics["post_sl_max_recovery_r"] = round(recovery_r, 2)

                # Check if price recovered to entry
                recovered_entry_idx = np.where(post_highs >= entry)[0]
                if len(recovered_entry_idx) > 0:
                    post_sl_metrics["post_sl_recovered_entry"] = True
                    post_sl_metrics["post_sl_recovery_bars"] = int(recovered_entry_idx[0] + 1)

                # Check if price recovered to target 1
                if highest_post >= t1:
                    post_sl_metrics["post_sl_recovered_t1"] = True

        return {
            "exit_reason": exit_reason,
            "exit_date": exit_date,
            "realized_rr": realized_rr,
            "holding_period_bars": exit_bar_idx + 1 if exit_bar_idx >= 0 else num_bars,
            "max_favorable_excursion_r": round(running_mfe_r, 2),
            "max_adverse_excursion_r": round(running_mae_r, 2),
            "r1_hit_before_sl": r1_hit,
            "r1_5_hit_before_sl": r1_5_hit,
            "r2_hit_before_sl": r2_hit,
            "same_bar_conflict": same_bar_conflict,
            **post_sl_metrics
        }
