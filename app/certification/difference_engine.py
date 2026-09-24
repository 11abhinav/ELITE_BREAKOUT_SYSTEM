# app/certification/difference_engine.py
"""
Differential Comparison Engine for Exact Production Replay & Deterministic Backtest Certification.
"""
from typing import Any, Dict, List, Optional, Tuple
import math
try:
    from certification.models import (
        ProductionDecisionRecord,
        FieldDelta,
        DifferentialReport,
        Tolerances
    )
except ImportError:
    from app.certification.models import (
        ProductionDecisionRecord,
        FieldDelta,
        DifferentialReport,
        Tolerances
    )


class DifferenceEngine:
    """
    Compares a Production Decision Record against a Replay Decision Record field-by-field
    under strict numerical tolerances and exact categorical rules.
    """
    def __init__(self, tolerances: Optional[Tolerances] = None):
        self.tolerances = tolerances or Tolerances()

    def _determine_tolerance(self, field_name: str) -> Optional[float]:
        name_lower = field_name.lower()
        if any(w in name_lower for w in ("pct", "percent", "percentage", "margin")):
            return self.tolerances.percentage
        if any(w in name_lower for w in ("ratio", "rvol", "expansion", "mult", "factor", "wick_ratio", "body_ratio", "close_pos")):
            return self.tolerances.ratio
        if any(w in name_lower for w in ("score", "points", "rating")):
            return self.tolerances.score
        if any(w in name_lower for w in ("price", "close", "open", "high", "low", "atr", "ema", "sma", "vwap", "resistance", "stop_loss", "target")):
            return self.tolerances.price
        return 0.0001

    def _compare_values(self, field_name: str, prod_val: Any, replay_val: Any, category: str = "INDICATOR") -> FieldDelta:
        # Exact matching for None
        if prod_val is None or replay_val is None:
            matches = (prod_val == replay_val)
            return FieldDelta(
                field_name=field_name,
                prod_value=prod_val,
                replay_value=replay_val,
                delta=None,
                tolerance=0.0,
                matches=matches,
                category=category
            )

        # Exact matching for Booleans
        if isinstance(prod_val, bool) or isinstance(replay_val, bool):
            matches = (bool(prod_val) == bool(replay_val))
            return FieldDelta(
                field_name=field_name,
                prod_value=prod_val,
                replay_value=replay_val,
                delta=None,
                tolerance=0.0,
                matches=matches,
                category=category
            )

        # Numeric comparison with tolerance
        try:
            p_float = float(prod_val)
            r_float = float(replay_val)
            tol = self._determine_tolerance(field_name)
            delta = round(r_float - p_float, 6)
            matches = abs(delta) <= tol
            return FieldDelta(
                field_name=field_name,
                prod_value=p_float,
                replay_value=r_float,
                delta=delta,
                tolerance=tol,
                matches=matches,
                category=category
            )
        except (ValueError, TypeError):
            # String or categorical comparison
            p_str = str(prod_val).strip()
            r_str = str(replay_val).strip()
            matches = (p_str == r_str)
            return FieldDelta(
                field_name=field_name,
                prod_value=p_str,
                replay_value=r_str,
                delta=None,
                tolerance=0.0,
                matches=matches,
                category=category
            )

    def compare(
        self,
        prod_record: ProductionDecisionRecord,
        replay_record: ProductionDecisionRecord,
        pit_valid: bool = True,
        pit_violations: Optional[List[str]] = None
    ) -> DifferentialReport:
        """
        Executes deep field-by-field differential comparison.
        """
        comparisons: List[FieldDelta] = []

        # 1. Config and Code Provenance
        code_match = (prod_record.git_commit == replay_record.git_commit or prod_record.git_commit in ("unknown", "UNKNOWN_COMMIT"))
        comparisons.append(FieldDelta("GIT_COMMIT", prod_record.git_commit, replay_record.git_commit, None, 0.0, code_match, "METADATA"))
        
        config_match = (prod_record.config_hash == replay_record.config_hash)
        comparisons.append(FieldDelta("CONFIG_HASH", prod_record.config_hash, replay_record.config_hash, None, 0.0, config_match, "CONFIG"))

        # 2. Data Snapshot Provenance
        data_hash_match = (prod_record.data_snapshot.sha256_hash == replay_record.data_snapshot.sha256_hash)
        comparisons.append(FieldDelta("DATA_SHA256", prod_record.data_snapshot.sha256_hash, replay_record.data_snapshot.sha256_hash, None, 0.0, data_hash_match, "DATA"))
        
        row_count_match = (prod_record.data_snapshot.row_count == replay_record.data_snapshot.row_count)
        comparisons.append(FieldDelta("ROW_COUNT", prod_record.data_snapshot.row_count, replay_record.data_snapshot.row_count, replay_record.data_snapshot.row_count - prod_record.data_snapshot.row_count, 0.0, row_count_match, "DATA"))

        comparisons.append(self._compare_values("DELIVERY_PCT", prod_record.data_snapshot.delivery_pct, replay_record.data_snapshot.delivery_pct, "DATA"))
        comparisons.append(self._compare_values("MARKET_REGIME", prod_record.market_regime, replay_record.market_regime, "DATA"))

        # 3. Indicators
        all_ind_keys = sorted(set(prod_record.indicators.keys()) | set(replay_record.indicators.keys()))
        for k in all_ind_keys:
            p_val = prod_record.indicators.get(k)
            r_val = replay_record.indicators.get(k)
            comparisons.append(self._compare_values(k, p_val, r_val, "INDICATOR"))

        # 4. Gates
        all_gate_keys = sorted(set(prod_record.gate_results.keys()) | set(replay_record.gate_results.keys()))
        gates_match = True
        for gk in all_gate_keys:
            p_gate = prod_record.gate_results.get(gk)
            r_gate = replay_record.gate_results.get(gk)
            p_pass = p_gate.passed if p_gate else None
            r_pass = r_gate.passed if r_gate else None
            p_status = p_gate.status if p_gate else "MISSING"
            r_status = r_gate.status if r_gate else "MISSING"
            
            g_match = (p_pass == r_pass and p_status == r_status)
            if not g_match:
                gates_match = False

            comparisons.append(FieldDelta(
                field_name=f"GATE_{gk}",
                prod_value=f"{p_status} (actual={getattr(p_gate, 'actual', None)})",
                replay_value=f"{r_status} (actual={getattr(r_gate, 'actual', None)})",
                delta=None,
                tolerance=0.0,
                matches=g_match,
                category="GATE"
            ))

        # 5. Score
        score_comp = self._compare_values("FINAL_SCORE", prod_record.final_score, replay_record.final_score, "SCORE")
        comparisons.append(score_comp)

        # 6. Terminal Decision & Rejection Reason
        decision_match = (prod_record.terminal_decision == replay_record.terminal_decision)
        comparisons.append(FieldDelta("TERMINAL_DECISION", prod_record.terminal_decision, replay_record.terminal_decision, None, 0.0, decision_match, "DECISION"))
        
        reason_match = (str(prod_record.rejection_reason).strip().lower() == str(replay_record.rejection_reason).strip().lower())
        comparisons.append(FieldDelta("REJECTION_REASON", prod_record.rejection_reason, replay_record.rejection_reason, None, 0.0, reason_match, "DECISION"))

        # Identify First Divergence & Downstream Trace
        first_divergence = None
        root_input_divergence = None
        downstream_impact = []

        indicators_match = True
        data_match = data_hash_match and row_count_match

        for c in comparisons:
            if not c.matches:
                if c.category == "INDICATOR":
                    indicators_match = False
                if first_divergence is None:
                    first_divergence = c.field_name

        if not data_match:
            root_input_divergence = f"Input DataFrame mismatch: Row count ({prod_record.data_snapshot.row_count} vs {replay_record.data_snapshot.row_count}) or SHA256 ({prod_record.data_snapshot.sha256_hash} vs {replay_record.data_snapshot.sha256_hash})"
        elif not config_match:
            root_input_divergence = f"Configuration mismatch: Hash {prod_record.config_hash} vs {replay_record.config_hash}"
        elif not pit_valid:
            root_input_divergence = f"Point-in-time causality failure: {', '.join(pit_violations or [])}"
        elif first_divergence:
            root_input_divergence = f"Calculation logic / parameter drift at {first_divergence}"

        # Downstream trace
        if first_divergence:
            downstream_impact.append(first_divergence)
            if any(c.category == "GATE" and not c.matches for c in comparisons):
                downstream_impact.append("GATE_EVALUATION")
            if not score_comp.matches:
                downstream_impact.append("FINAL_SCORE")
            if not decision_match:
                downstream_impact.append("FINAL_DECISION")

        certified = (
            config_match
            and data_match
            and indicators_match
            and gates_match
            and decision_match
            and pit_valid
        )

        return DifferentialReport(
            symbol=prod_record.symbol,
            scanner_name=prod_record.scanner_name,
            evaluation_date=prod_record.evaluation_date,
            certified=certified,
            first_divergence=first_divergence,
            root_input_divergence=root_input_divergence,
            downstream_impact=downstream_impact,
            field_comparisons=comparisons,
            code_version_match=code_match,
            config_match=config_match,
            data_match=data_match,
            indicators_match=indicators_match,
            gates_match=gates_match,
            decision_match=decision_match,
            point_in_time_valid=pit_valid,
            summary="CERTIFICATION PASS" if certified else "CERTIFICATION FAIL"
        )
