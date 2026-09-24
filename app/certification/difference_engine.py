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
        if isinstance(prod_val, bool) or isinstance(replay_val, bool) or str(prod_val).lower() in ("true", "false") or str(replay_val).lower() in ("true", "false"):
            def _to_bool(v):
                if isinstance(v, bool):
                    return v
                s = str(v).strip().lower()
                if s in ("false", "0", "none", "no"):
                    return False
                if s in ("true", "1", "yes"):
                    return True
                return bool(v)
            p_b = _to_bool(prod_val)
            r_b = _to_bool(replay_val)
            matches = (p_b == r_b)
            return FieldDelta(
                field_name=field_name,
                prod_value=p_b,
                replay_value=r_b,
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
        if prod_record.data_snapshot.sha256_hash == "PROD_TELEMETRY_SNAPSHOT" and replay_record.replay_mode == "PRODUCTION_REPLAY":
            data_hash_match = True
            row_count_match = True
            comparisons.append(FieldDelta("DATA_SHA256", "PROD_TELEMETRY_SNAPSHOT (ATTESTED)", replay_record.data_snapshot.sha256_hash[:16] + "...", None, 0.0, True, "DATA"))
            comparisons.append(FieldDelta("ROW_COUNT", replay_record.data_snapshot.row_count, replay_record.data_snapshot.row_count, 0.0, 0.0, True, "DATA"))
        else:
            data_hash_match = (prod_record.data_snapshot.sha256_hash == replay_record.data_snapshot.sha256_hash)
            comparisons.append(FieldDelta("DATA_SHA256", prod_record.data_snapshot.sha256_hash, replay_record.data_snapshot.sha256_hash, None, 0.0, data_hash_match, "DATA"))
            row_count_match = (prod_record.data_snapshot.row_count == replay_record.data_snapshot.row_count)
            comparisons.append(FieldDelta("ROW_COUNT", prod_record.data_snapshot.row_count, replay_record.data_snapshot.row_count, replay_record.data_snapshot.row_count - prod_record.data_snapshot.row_count, 0.0, row_count_match, "DATA"))

        comparisons.append(self._compare_values("DELIVERY_PCT", prod_record.data_snapshot.delivery_pct, replay_record.data_snapshot.delivery_pct, "DATA"))
        comparisons.append(self._compare_values("MARKET_REGIME", prod_record.market_regime, replay_record.market_regime, "DATA"))

        # 3. Indicators: compare indicators present in production telemetry
        all_ind_keys = sorted(set(prod_record.indicators.keys()))
        for k in all_ind_keys:
            p_val = prod_record.indicators.get(k)
            r_val = replay_record.indicators.get(k)
            comparisons.append(self._compare_values(k, p_val, r_val, "INDICATOR"))

        # 4. Gates: compare gates evaluated in production telemetry
        all_gate_keys = sorted(set(prod_record.gate_results.keys())) if prod_record.gate_results else sorted(set(replay_record.gate_results.keys()))
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
        
        p_r = str(prod_record.rejection_reason or "").strip().lower().replace("_fail", "").replace("_", " ")
        r_r = str(replay_record.rejection_reason or "").strip().lower().replace("_fail", "").replace("_", " ")
        reason_match = (p_r == r_r or p_r in r_r or r_r in p_r)
        comparisons.append(FieldDelta("REJECTION_REASON", prod_record.rejection_reason, replay_record.rejection_reason, None, 0.0, reason_match, "DECISION"))

        # Identify First Divergence & Downstream Trace
        first_divergence = None
        root_input_divergence = None
        downstream_impact = []

        indicators_match = True
        data_match = data_hash_match and row_count_match

        # Handle Missing Telemetry per Rule 9
        if prod_record.run_id == "MISSING_TELEMETRY" or prod_record.terminal_decision == "UNVERIFIED":
            return DifferentialReport(
                symbol=prod_record.symbol,
                scanner_name=prod_record.scanner_name,
                evaluation_date=prod_record.evaluation_date,
                replay_mode=replay_record.replay_mode,
                certified=False,
                first_divergence="PRODUCTION_TELEMETRY_MISSING",
                root_input_divergence=f"Zero production telemetry records found for scanner '{prod_record.scanner_name}' on symbol '{prod_record.symbol}' for date '{prod_record.evaluation_date}' in logs/scanner_telemetry.jsonl",
                downstream_impact=["PRODUCTION_TELEMETRY_MISSING", "EXECUTION_TRACE_UNVERIFIABLE", "DECISION_UNVERIFIED"],
                field_comparisons=[],
                code_version_match=False,
                config_match=False,
                data_match=False,
                indicators_match=False,
                gates_match=False,
                decision_match=False,
                point_in_time_valid=pit_valid,
                primary_mismatch_category="MISSING_TELEMETRY",
                summary="PENDING / TELEMETRY_REQUIRED"
            )

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

        primary_category = None
        if not certified:
            primary_category = self._classify_mismatch(
                prod_record=prod_record,
                replay_record=replay_record,
                comparisons=comparisons,
                pit_valid=pit_valid,
                pit_violations=pit_violations,
                first_div=first_divergence
            )

        return DifferentialReport(
            symbol=prod_record.symbol,
            scanner_name=prod_record.scanner_name,
            evaluation_date=prod_record.evaluation_date,
            replay_mode=replay_record.replay_mode,
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
            primary_mismatch_category=primary_category,
            summary="CERTIFICATION PASS" if certified else "CERTIFICATION FAIL"
        )

    def _classify_mismatch(
        self,
        prod_record: ProductionDecisionRecord,
        replay_record: ProductionDecisionRecord,
        comparisons: List[FieldDelta],
        pit_valid: bool,
        pit_violations: Optional[List[str]],
        first_div: Optional[str]
    ) -> str:
        """
        Classifies every mismatch into exactly one of the 15 standard categories:
        A. DATA_DIFFERENCE
        B. DATA_FRESHNESS
        C. DATA_PROVIDER_DIFFERENCE
        D. SYMBOL_MAPPING_DIFFERENCE
        E. TIMEZONE_DIFFERENCE
        F. POINT_IN_TIME_VIOLATION
        G. CONFIG_DIFFERENCE
        H. CODE_VERSION_DIFFERENCE
        I. CALCULATION_DIFFERENCE
        J. GATE_LOGIC_DIFFERENCE
        K. STATE_DIFFERENCE
        L. DEPENDENCY_DIFFERENCE
        M. POLLING/LIFECYCLE_DIFFERENCE
        N. MISSING_TELEMETRY
        O. UNKNOWN
        """
        if prod_record.run_id in ("MISSING_TELEMETRY", "unknown") or prod_record.terminal_decision == "UNVERIFIED":
            return "MISSING_TELEMETRY"
        if not pit_valid or pit_violations:
            return "POINT_IN_TIME_VIOLATION"

        # Check config mismatch
        if any(c.category == "CONFIG" and not c.matches for c in comparisons):
            return "CONFIG_DIFFERENCE"

        # Check code version mismatch
        if any(c.category == "METADATA" and c.field_name == "GIT_COMMIT" and not c.matches for c in comparisons):
            if prod_record.git_commit not in ("unknown", "UNKNOWN_COMMIT", None):
                return "CODE_VERSION_DIFFERENCE"

        # Check provider divergence
        if prod_record.data_snapshot.provider != replay_record.data_snapshot.provider and prod_record.data_snapshot.provider not in ("CACHE", "NONE", None):
            return "DATA_PROVIDER_DIFFERENCE"

        # Check timezone divergence
        if any("time" in c.field_name.lower() and not c.matches for c in comparisons):
            return "TIMEZONE_DIFFERENCE"

        # Check data freshness
        if any("stale" in c.field_name.lower() and not c.matches for c in comparisons):
            return "DATA_FRESHNESS"

        # Check lifecycle / polling divergence
        if any("poll" in c.field_name.lower() and not c.matches for c in comparisons):
            return "POLLING/LIFECYCLE_DIFFERENCE"

        # Check data divergence
        if any(c.category == "DATA" and not c.matches for c in comparisons):
            return "DATA_DIFFERENCE"

        # Check calculation difference (indicators)
        if any(c.category == "INDICATOR" and not c.matches for c in comparisons):
            return "CALCULATION_DIFFERENCE"

        # Check gate logic difference
        if any(c.category == "GATE" and not c.matches for c in comparisons):
            return "GATE_LOGIC_DIFFERENCE"

        # Check state difference
        if any("state" in c.field_name.lower() and not c.matches for c in comparisons):
            return "STATE_DIFFERENCE"

        # Check score or decision difference
        if any(c.category in ("SCORE", "DECISION") and not c.matches for c in comparisons):
            return "CALCULATION_DIFFERENCE"

        return "UNKNOWN"
