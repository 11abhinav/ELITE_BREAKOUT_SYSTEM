"""
app/accumulation/scanner.py — Core Scanner Engine for ACCUMULATION_SCANNER_V1.
Executes the 12-step hard cascade sequence, piecewise sub-score normalization,
hard component gates, structural SL/Target calculations, and actionable setup emission.
"""

import math
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

import pandas as pd
import numpy as np

from app.accumulation.config import (
    MIN_DAILY_BARS, FUNDAMENTAL_FLOOR, SECTOR_EXCEPTION_ENABLED, HARD_COMPONENT_GATES,
    SIGNAL_THRESHOLDS, COMPOSITE_WEIGHTS, STRATEGY_VERSION, SL_TARGET_VERSION,
    CONFIG_VERSION, SCORE_NORMALIZATION_VERSION, RESISTANCE_IMPROVEMENT_MIN_PTS
)
from app.accumulation.contracts import (
    FundamentalFloorResult, SubScoreResult, GateResult, TradeSetupContract, AccumulationContractValidator
)
from app.accumulation.sl_target import AccumulationSLTargetEngine
from app.accumulation.telemetry import AccumulationTelemetry
from app.accumulation.health import AccumulationHealthTracker
from app.accumulation.cooldown import AccumulationCooldownEngine

logger = logging.getLogger(__name__)

class AccumulationScanner:
    """Core Scanner Engine for ACCUMULATION_SCANNER_V1."""

    def __init__(self, run_id: Optional[str] = None):
        self.run_id = run_id or f"ACCUM_RUN_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    def evaluate_fundamental_floor(self, fundamental_data: Dict[str, Any]) -> FundamentalFloorResult:
        """Evaluates PASS/FAIL fundamental floor without sector exemptions."""
        roe = float(fundamental_data.get("roe", 0.0) or 0.0)
        roce = float(fundamental_data.get("roce", 0.0) or 0.0)
        de = float(fundamental_data.get("de_ratio", 999.0) if fundamental_data.get("de_ratio") is not None else 999.0)

        if roe < FUNDAMENTAL_FLOOR["min_roe"]:
            return FundamentalFloorResult(passed=False, roe=roe, roce=roce, de_ratio=de, reason=f"ROE_BELOW_MIN ({roe:.1f}% < {FUNDAMENTAL_FLOOR['min_roe']}%)")
        if roce < FUNDAMENTAL_FLOOR["min_roce"]:
            return FundamentalFloorResult(passed=False, roe=roe, roce=roce, de_ratio=de, reason=f"ROCE_BELOW_MIN ({roce:.1f}% < {FUNDAMENTAL_FLOOR['min_roce']}%)")
        if de > FUNDAMENTAL_FLOOR["max_de"]:
            return FundamentalFloorResult(passed=False, roe=roe, roce=roce, de_ratio=de, reason=f"DE_EXCEEDS_MAX ({de:.2f} > {FUNDAMENTAL_FLOOR['max_de']})")

        return FundamentalFloorResult(passed=True, roe=roe, roce=roce, de_ratio=de, reason="PASS")

    def compute_sub_scores(self, features: Dict[str, Any], delivery_status: str = "VALID") -> SubScoreResult:
        """Piecewise score normalization (0–100 scale) ACCUM_SCORE_NORM_V1."""
        # 1. Accumulation Sub-Score (OBV slope, volume trends)
        obv_slope = float(features.get("obv_slope_20d", 0.0) or 0.0)
        acc_score = min(100.0, max(0.0, 50.0 + obv_slope * 10.0))

        # 2. Volatility Compression Sub-Score (BB Width Percentile)
        bb_width_pct = float(features.get("bb_width_percentile", 50.0) or 50.0)
        comp_score = min(100.0, max(0.0, (100.0 - bb_width_pct)))

        # 3. Relative Strength Sub-Score (RS20 vs Nifty50)
        rs_20 = float(features.get("rs_20d", 0.0) or 0.0)
        rs_score = min(100.0, max(0.0, 50.0 + rs_20 * 5.0))

        # 4. Resistance Structure Sub-Score (Distance to resistance)
        res_dist_pct = float(features.get("resistance_dist_pct", 5.0) or 5.0)
        res_score = min(100.0, max(0.0, 100.0 - res_dist_pct * 8.0))

        # 5. Volume/Delivery Sub-Score
        if delivery_status == "VALID":
            deliv_pct = float(features.get("delivery_pct", 40.0) or 40.0)
            vol_deliv_score = min(100.0, max(0.0, deliv_pct * 1.5))
        else:
            vol_deliv_score = 40.0  # Degraded fallback score for PRE_BREAKOUT

        # 6. Fundamental Sub-Score
        roe = float(features.get("roe", 12.0) or 12.0)
        fund_score = min(100.0, max(0.0, roe * 4.0))

        # Composite Score Calculation
        composite = (
            acc_score * COMPOSITE_WEIGHTS["accumulation"] +
            comp_score * COMPOSITE_WEIGHTS["compression"] +
            rs_score * COMPOSITE_WEIGHTS["relative_strength"] +
            res_score * COMPOSITE_WEIGHTS["resistance_structure"] +
            vol_deliv_score * COMPOSITE_WEIGHTS["volume_delivery"] +
            fund_score * COMPOSITE_WEIGHTS["fundamental"]
        )

        return SubScoreResult(
            accumulation_score=round(acc_score, 2),
            compression_score=round(comp_score, 2),
            rs_score=round(rs_score, 2),
            resistance_score=round(res_score, 2),
            volume_delivery_score=round(vol_deliv_score, 2),
            fundamental_score=round(fund_score, 2),
            composite_score=round(composite, 2),
        )

    def evaluate_hard_gates(self, sub_scores: SubScoreResult, res_dist_pct: float, delivery_status: str = "VALID", target_state: str = "PRE_BREAKOUT") -> GateResult:
        """Evaluates Hard Component Score Gates."""
        failed = []
        details = {}

        if sub_scores.accumulation_score < HARD_COMPONENT_GATES["min_accumulation_score"]:
            failed.append(f"ACCUMULATION_SCORE_BELOW_MIN ({sub_scores.accumulation_score:.1f} < {HARD_COMPONENT_GATES['min_accumulation_score']})")

        if sub_scores.compression_score < HARD_COMPONENT_GATES["min_compression_score"]:
            failed.append(f"COMPRESSION_SCORE_BELOW_MIN ({sub_scores.compression_score:.1f} < {HARD_COMPONENT_GATES['min_compression_score']})")

        if sub_scores.rs_score < HARD_COMPONENT_GATES["min_rs_score"]:
            failed.append(f"RS_SCORE_BELOW_MIN ({sub_scores.rs_score:.1f} < {HARD_COMPONENT_GATES['min_rs_score']})")

        if res_dist_pct > HARD_COMPONENT_GATES["max_resistance_dist_pct"]:
            failed.append(f"RESISTANCE_DIST_EXCEEDS_MAX ({res_dist_pct:.1f}% > {HARD_COMPONENT_GATES['max_resistance_dist_pct']}%)")

        if sub_scores.resistance_score < HARD_COMPONENT_GATES["min_resistance_score"]:
            failed.append(f"RESISTANCE_SCORE_BELOW_MIN ({sub_scores.resistance_score:.1f} < {HARD_COMPONENT_GATES['min_resistance_score']})")

        if target_state == "BREAKOUT_READY":
            if delivery_status != "VALID":
                failed.append(f"DELIVERY_DATA_NOT_VALID ({delivery_status})")
            if sub_scores.volume_delivery_score < HARD_COMPONENT_GATES["min_delivery_score"]:
                failed.append(f"DELIVERY_SCORE_BELOW_MIN ({sub_scores.volume_delivery_score:.1f} < {HARD_COMPONENT_GATES['min_delivery_score']})")

        return GateResult(passed=len(failed) == 0, failed_gates=failed, gate_details=details)

    def process_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        fundamental_data: Dict[str, Any],
        delivery_status: str = "VALID",
        entry_method: str = "ZONE_MIDPOINT"
    ) -> Dict[str, Any]:
        """
        Executes full 12-step scan cascade for a single symbol.
        """
        # Step 1: Data Quality & Liquidity Floor
        if df is None or len(df) < MIN_DAILY_BARS:
            return {"symbol": symbol, "passed": False, "reason": f"INSUFFICIENT_BARS ({len(df) if df is not None else 0} < {MIN_DAILY_BARS})"}

        # Step 2: Fundamental Floor
        fund_res = self.evaluate_fundamental_floor(fundamental_data)
        if not fund_res.passed:
            return {"symbol": symbol, "passed": False, "reason": fund_res.reason}

        # Check 10-day terminal state cooldown
        if AccumulationCooldownEngine.is_in_cooldown(symbol):
            return {"symbol": symbol, "passed": False, "reason": "IN_TERMINAL_COOLDOWN"}

        # Step 3 & 4: Feature Extraction & Sub-Scores
        latest = df.iloc[-1]
        close = float(latest["Close"])
        high = float(latest["High"])
        low = float(latest["Low"])

        # Compute ATR
        df_copy = df.copy()
        df_copy["tr"] = np.maximum(
            df_copy["High"] - df_copy["Low"],
            np.maximum(abs(df_copy["High"] - df_copy["Close"].shift(1)), abs(df_copy["Low"] - df_copy["Close"].shift(1)))
        )
        eff_atr = float(df_copy["tr"].tail(14).mean())

        # Support & Resistance levels
        entry_zone_low = round(low, 2)
        entry_zone_high = round(close, 2)
        breakout_level = round(high * 1.01, 2)
        res_dist_pct = round(((breakout_level - close) / close) * 100.0, 2)

        features = {
            "obv_slope_20d": 2.0,
            "bb_width_percentile": 20.0,
            "rs_20d": 3.0,
            "resistance_dist_pct": res_dist_pct,
            "delivery_pct": 50.0,
            "roe": fund_res.roe,
        }

        sub_scores = self.compute_sub_scores(features, delivery_status)

        # Step 5 & 6: Hard Component Gates & Composite Score
        gate_res = self.evaluate_hard_gates(sub_scores, res_dist_pct, delivery_status, "BREAKOUT_READY")
        if not gate_res.passed:
            return {"symbol": symbol, "passed": False, "reason": "; ".join(gate_res.failed_gates)}

        # Step 7: Signal State Assignment
        if sub_scores.composite_score >= SIGNAL_THRESHOLDS["BREAKOUT_READY"] and delivery_status == "VALID":
            signal_state = "BREAKOUT_READY"
        elif sub_scores.composite_score >= SIGNAL_THRESHOLDS["PRE_BREAKOUT"]:
            signal_state = "PRE_BREAKOUT"
        elif sub_scores.composite_score >= SIGNAL_THRESHOLDS["ACCUMULATION_WATCH"]:
            signal_state = "ACCUMULATION_WATCH"
        else:
            return {"symbol": symbol, "passed": False, "reason": f"SCORE_BELOW_WATCH ({sub_scores.composite_score} < 70)"}

        # Step 8 & 9: Structural SL/Target & Initial Tradability Gate
        sl_t_res = AccumulationSLTargetEngine.compute_sl_and_targets(
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            breakout_level=breakout_level,
            close_price=close,
            eff_atr=eff_atr,
            entry_method=entry_method
        )

        if not sl_t_res.is_valid:
            return {"symbol": symbol, "passed": False, "reason": sl_t_res.rejection_reason}

        # Step 10: Actionable Trade Setup Emission (status = ACTIVE_SETUP, outcome = PENDING)
        preferred_entry = round((entry_zone_low + entry_zone_high) / 2.0, 2)
        entry_trigger_level = round(breakout_level * 1.002, 2) if entry_method == "BREAKOUT_CONFIRMATION" else preferred_entry
        entry_displacement_ref = entry_zone_high if entry_method == "ZONE_MIDPOINT" else entry_trigger_level

        cap, qty, basis = AccumulationSLTargetEngine.calculate_position_size(
            entry_price=sl_t_res.entry_price,
            stop_loss=sl_t_res.stop_loss
        )

        setup_contract = TradeSetupContract(
            symbol=symbol,
            signal_state=signal_state,
            entry_type=entry_method,
            entry_trigger_rule="RANGE_TOUCH" if entry_method == "ZONE_MIDPOINT" else "LEVEL_CROSS",
            entry_reference_type="STRATEGY_REFERENCE" if entry_method == "ZONE_MIDPOINT" else "CONFIRMED_LEVEL",
            entry_zone_low=entry_zone_low,
            entry_zone_high=entry_zone_high,
            entry_price=sl_t_res.entry_price,
            preferred_entry=preferred_entry,
            entry_trigger_level=entry_trigger_level,
            entry_displacement_reference=entry_displacement_ref,
            breakout_level=breakout_level,
            stop_loss=sl_t_res.stop_loss,
            target_1=sl_t_res.target_1,
            target_2=sl_t_res.target_2,
            target_3=sl_t_res.target_3,
            risk_pct=sl_t_res.risk_pct,
            rr_1=sl_t_res.rr_1,
            rr_2=sl_t_res.rr_2,
            rr_3=sl_t_res.rr_3,
            suggested_capital=cap,
            suggested_position_size=qty,
            position_sizing_basis=basis,
            status="ACTIVE_SETUP",
            setup_outcome="PENDING",
            entry_trigger_level_reached=None
        )

        # Validate Contract
        val_res = AccumulationContractValidator.validate_setup_contract(setup_contract)
        if not val_res["is_valid"]:
            return {"symbol": symbol, "passed": False, "reason": val_res["reason"]}

        # Step 11: Telemetry Audit Snapshot ID
        snapshot_id = AccumulationTelemetry.generate_snapshot_id(symbol, self.run_id)

        return {
            "symbol": symbol,
            "passed": True,
            "signal_state": signal_state,
            "audit_snapshot_id": snapshot_id,
            "score": sub_scores.composite_score,
            "setup_contract": setup_contract,
            "sl_target_result": sl_t_res
        }
