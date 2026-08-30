"""
Shadow Evaluation Engine for Wave 3A.
Evaluates candidate rules and asymmetric hypotheses in read-only shadow mode,
verifying state invariance (zero side-effects) and producing granular shadow telemetry.
"""

from typing import Dict, Any, List, Optional
import copy
import hashlib
import json
import pandas as pd
import numpy as np


class ShadowEvaluator:
    """
    Read-only shadow evaluator for candidate filter hypotheses and dynamic macro actions.
    Guarantees side-effect free execution by asserting state hash immutability.
    """

    def __init__(
        self,
        dataset_version: str = "3.0.0",
        code_version: str = "wave3-shadow-v1",
        feature_version: str = "v1.0"
    ):
        self.dataset_version = dataset_version
        self.code_version = code_version
        self.feature_version = feature_version

    @staticmethod
    def compute_state_hash(state_dict: Dict[str, Any]) -> str:
        """Computes deterministic hash of state dictionary for immutability verification."""
        encoded = json.dumps(state_dict, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def evaluate_shadow_signal(
        self,
        record: Dict[str, Any],
        headwind_action: str = "BLOCK",
        volume_threshold: float = 1.5,
        macro_action: str = "SIZE_REDUCE_50",
        friction_r: float = 0.05
    ) -> Dict[str, Any]:
        """
        Evaluates a single telemetry candidate against shadow rules without mutating any inputs.
        """
        # 1. State Invariance Guard
        initial_hash = self.compute_state_hash(record)
        rec = copy.deepcopy(record)

        prod_decision = rec.get("terminal_decision", "PASS")
        prod_entry = rec.get("entry_price")
        prod_sl = rec.get("sl_price")
        prod_target = rec.get("target_price")

        sector_status = rec.get("sector_status", "NEUTRAL")
        volume_ratio = float(rec.get("volume_ratio", 1.0)) if rec.get("volume_ratio") is not None else 1.0
        macro_state = rec.get("macro_state", "NEUTRAL")
        macro_drop_pct = float(rec.get("macro_drop_pct", 0.0)) if rec.get("macro_drop_pct") is not None else 0.0

        rules_triggered = []
        shadow_action = "PASS"
        pos_size_mult = 1.0
        sl_mult = 1.0

        shadow_entry = prod_entry
        shadow_sl = prod_sl
        shadow_target = prod_target

        # Evaluate Sector Headwind Hypothesis (W3_SEC_001)
        if sector_status == "HEADWIND":
            rules_triggered.append("RULE_SECTOR_HEADWIND")
            if headwind_action == "BLOCK":
                shadow_action = "BLOCK"

        # Evaluate Breakout Volume Hypothesis (W3_VOL_001)
        if volume_ratio < volume_threshold:
            rules_triggered.append(f"RULE_LOW_VOLUME_RATIO_{volume_threshold}x")
            if shadow_action != "BLOCK":
                shadow_action = "BLOCK"

        # Evaluate Macro Action Hypothesis (W3_MAC_001)
        if macro_drop_pct > 0.5:
            rules_triggered.append("RULE_MACRO_DROP_GT_0.5PCT")
            if macro_action == "BLOCK":
                shadow_action = "BLOCK"
            elif macro_action == "SIZE_REDUCE_50" and shadow_action != "BLOCK":
                shadow_action = "SIZE_REDUCE_50"
                pos_size_mult = 0.5
            elif macro_action == "TIGHTEN_SL_25" and shadow_action != "BLOCK":
                shadow_action = "TIGHTEN_SL_25"
                sl_mult = 0.75
                if prod_entry is not None and prod_sl is not None:
                    initial_risk = prod_entry - prod_sl
                    shadow_sl = prod_entry - (initial_risk * 0.75)

        # Three-tier R metrics
        cf_realized_r = rec.get("cf_realized_r")
        gross_trade_r = float(cf_realized_r) if cf_realized_r is not None else None
        net_trade_r = (gross_trade_r - friction_r) if gross_trade_r is not None else None
        portfolio_weighted_r = (net_trade_r * pos_size_mult) if net_trade_r is not None else None

        # Build Cluster Key for dependence-aware grouping
        decision_date = str(rec.get("decision_timestamp", ""))[:10]
        symbol = str(rec.get("symbol", "UNKNOWN"))
        cluster_id = f"{symbol}_{decision_date}"

        telemetry = {
            "timestamp": rec.get("decision_timestamp"),
            "scanner": rec.get("scanner"),
            "symbol": symbol,
            "cluster_id": cluster_id,
            "production_decision": prod_decision,
            "production_entry": prod_entry,
            "production_sl": prod_sl,
            "production_target": prod_target,
            "shadow_action": shadow_action,
            "shadow_rules_triggered": rules_triggered,
            "shadow_position_size_multiplier": pos_size_mult,
            "shadow_sl_multiplier": sl_mult,
            "shadow_entry": shadow_entry,
            "shadow_sl": shadow_sl,
            "shadow_target": shadow_target,
            "sector_status": sector_status,
            "volume_ratio": volume_ratio,
            "macro_state": macro_state,
            "macro_drop_pct": macro_drop_pct,
            "gross_trade_R": gross_trade_r,
            "net_trade_R": round(net_trade_r, 4) if net_trade_r is not None else None,
            "portfolio_weighted_R": round(portfolio_weighted_r, 4) if portfolio_weighted_r is not None else None,
            "MFE_R": rec.get("cf_mfe_r"),
            "MAE_R": rec.get("cf_mae_r"),
            "data_quality_status": "VALID",
            "replay_eligibility": rec.get("trade_eligibility_status", "NOT_ELIGIBLE"),
            "dataset_version": self.dataset_version,
            "code_version": self.code_version,
            "feature_version": self.feature_version
        }

        # Assert zero side-effects
        final_hash = self.compute_state_hash(record)
        if initial_hash != final_hash:
            raise RuntimeError("CRITICAL ERROR: Input state mutated during shadow evaluation!")

        return telemetry
