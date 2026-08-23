# =====================================================================================
# app/scanner_telemetry.py
# GLOBAL MANDATORY FULL DECISION TELEMETRY ENGINE
# =====================================================================================
import json
import logging
import math
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd
import numpy as np
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger("GLOBAL_SCANNER_TELEMETRY")

TELEMETRY_LOG_DIR = os.path.abspath("./logs")
TELEMETRY_JSONL_PATH = os.path.join(TELEMETRY_LOG_DIR, "scanner_telemetry.jsonl")


def _safe_float(val: Any, default: Any = None) -> Any:
    if val is None or pd.isna(val):
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (ValueError, TypeError):
        return default


def is_bad_numeric(val: Any) -> Tuple[bool, str]:
    """
    [RULE 67] Centralized, type-safe NaN/Inf classifier.

    Rationale: math.isnan() only handles Python float; it raises TypeError on NumPy
    scalars and fails silently on strings. User explicitly required a single, centralized
    function that handles all four input classes:
      1. Python float
      2. NumPy scalar (np.float64, np.float32, np.floating)
      3. pandas NA / NaT
      4. String sentinels ("nan", "inf", "-inf")

    Returns: (is_bad: bool, raw_label: str)
      - (True, "NaN")  — value is NaN-like
      - (True, "INF")  — value is infinite
      - (False, "")    — value is numerically safe
    """
    # ── Python None ────────────────────────────────────────────────────────────
    if val is None:
        return False, ""  # None is handled as NULL by sanitize_telemetry_value

    # ── pandas NA / NaT ────────────────────────────────────────────────────────
    try:
        if pd.isna(val):
            return True, "NaN"
    except (TypeError, ValueError):
        pass  # pd.isna raises on non-scalar iterables; ignore

    # ── NumPy scalars and Python floats ────────────────────────────────────────
    if isinstance(val, (float, np.floating)):
        try:
            if math.isnan(float(val)):
                return True, "NaN"
            if math.isinf(float(val)):
                return True, "INF"
        except (ValueError, OverflowError):
            pass

    # ── String sentinels ("nan", "inf", "-inf", "infinity", etc.) ─────────────
    if isinstance(val, str):
        stripped = val.strip().lower()
        if stripped in ("nan",):
            return True, "NaN"
        if stripped in ("inf", "+inf", "-inf", "infinity", "-infinity", "+infinity"):
            return True, "INF"

    return False, ""


def sanitize_telemetry_value(val: Any) -> Tuple[Any, Optional[str], str, str]:
    """
    Sanitizes raw value and returns (sanitized_val, raw_value_str, status, reason).

    [RULE 67] Routes all NaN/Inf detection through is_bad_numeric() so that NumPy
    scalars, pandas NA, and string sentinels are all caught by a single centralized
    function rather than scattered isinstance(float) guards.

    NaN/Inf entries preserve raw_value_str ("NaN"/"INF") so the forensic audit can
    distinguish "source contained NaN" from "field was simply absent" (NULL).
    Status kept as "NAN" so data_quality_summary ledger counts nan_fields correctly.
    """
    if val is None:
        return None, None, "NULL", "VALUE_IS_NONE"

    # ── Centralized NaN/Inf check (handles float, NumPy, pandas, strings) ──────
    _is_bad, _raw_label = is_bad_numeric(val)
    if _is_bad:
        # Preserve forensic raw label; keep status="NAN" for data quality ledger
        return None, _raw_label, "NAN", f"VALUE_IS_{_raw_label}"

    if isinstance(val, bool):
        # bool must be checked BEFORE int since bool is a subclass of int
        return bool(val), None, "VALID", "OK"
    if isinstance(val, (int, np.integer)):
        return int(val), None, "VALID", "OK"
    if isinstance(val, (float, np.floating)):
        return round(float(val), 4), None, "VALID", "OK"
    if isinstance(val, str) and val.strip().lower() in ("", "none", "null"):
        # Empty / null-like strings that were NOT caught by is_bad_numeric above
        return val, None, "INVALID", "STRING_EMPTY_OR_NULL_LIKE"
    return str(val), None, "VALID", "OK"


class TelemetryValueEntry:
    """Represents a single captured field with origin, status, and reason."""
    def __init__(self, key: str, value: Any, origin: str = "CALCULATED", group: str = "DERIVED", source_series: str = None, freshness: str = "LIVE"):
        self.key = key
        self.raw_value = value
        self.origin = origin # EXTERNAL_API, CALCULATED, CONFIG, DERIVED, STALE_CACHE
        self.group = group # RAW, INDICATOR, DERIVED, CONFIG, GATE, SCORE, SL_TARGET, DEPENDENCY
        self.source_series = source_series
        self.freshness = freshness
        self.timestamp = time.time()
        
        sanitized, raw_val_str, status, reason = sanitize_telemetry_value(value)
        # Apply STALE precedence if data is otherwise valid but comes from stale cache
        if origin == "STALE_CACHE" and status == "VALID":
            status = "STALE"
            reason = "STALE_DATA_USED"

        self.value = sanitized
        self.raw_value_str = raw_val_str
        self.status = status
        self.reason = reason

    def to_dict(self) -> dict:
        d = {
            "key": self.key,
            "value": self.value,
            "status": self.status,
            "reason": self.reason,
            "origin": self.origin,
            "group": self.group,
            "source_series": self.source_series
        }
        if self.raw_value_str is not None:
            d["raw_value"] = self.raw_value_str
        return d


class DecisionContext:
    """
    Central per-symbol evaluation context capturing raw inputs, calculated indicators,
    derived metrics, configuration thresholds, gate results, score components, SL/targets,
    timeframe states, dependencies, and terminal decisions.
    """
    def __init__(self, symbol: str, scanner_name: str, exchange: str = "NSE", run_id: str = None):
        self.symbol = symbol
        self.scanner_name = scanner_name
        self.exchange = exchange
        self.run_id = run_id or f"run_{int(time.time())}"
        self.timestamp = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        self.start_time = time.time()
        
        self.terminal_decision = "PENDING" # SELECTED / REJECTED / ERROR / NOT_APPLICABLE
        self.evaluation_mode = "FULL" # FULL / SHORT_CIRCUITED
        self.primary_reason = ""
        self.secondary_reasons: List[str] = []
        self.alert_generated = False
        self.alert_id = None
        self.persisted = False
        
        # Captured Telemetry Dictionaries
        self.entries: Dict[str, TelemetryValueEntry] = {}
        self.gate_results: Dict[str, Dict[str, Any]] = {}
        self.score_breakdown: Dict[str, Dict[str, Any]] = {}
        self.configuration: Dict[str, Any] = {}
        self.sl_target: Dict[str, Any] = {}
        self.dependencies: Dict[str, Any] = {}
        self.timeframe_data: Dict[str, Dict[str, Any]] = {}
        self.error_details: Dict[str, Any] = {}
        self.decision_trace: List[Dict[str, Any]] = []
        self.decision_manifest: List[Dict[str, Any]] = []

    def add_decision_input(self, name: str, value: Any, source: str, as_of: str, freshness: str, required: bool, valid: bool):
        """
        Adds a field to the explicit decision manifest for auditing.

        [RULE 67] Uses the centralized is_bad_numeric() helper instead of the previous
        `isinstance(value, float)` guard. This ensures NumPy scalars (np.float64 etc.) and
        pandas NA values are also caught and their raw representation preserved in the
        manifest, not silently discarded.
        """
        _is_bad, _raw_label = is_bad_numeric(value)
        raw_value = _raw_label if _is_bad else None
        if _is_bad:
            value = None
            valid = False

        self.decision_manifest.append({
            "name": name,
            "value": value,
            "raw_value": raw_value,
            "source": source,
            "as_of": as_of,
            "freshness": freshness,
            "required": required,
            "valid": valid
        })

    def capture(self, key: str, value: Any, origin: str = "CALCULATED", group: str = "DERIVED", source_series: str = None):
        """Captures a field into decision context, guaranteeing non-silent retention."""
        entry = TelemetryValueEntry(key=key, value=value, origin=origin, group=group, source_series=source_series)
        self.entries[key] = entry

    def capture_raw_market(self, open_p: Any, high_p: Any, low_p: Any, close_p: Any, volume: Any, prev_close: Any = None, vwap: Any = None, high_52w: Any = None, low_52w: Any = None):
        """Captures standard raw market data fields."""
        self.capture("OPEN", open_p, origin="EXTERNAL_API", group="RAW")
        self.capture("HIGH", high_p, origin="EXTERNAL_API", group="RAW")
        self.capture("LOW", low_p, origin="EXTERNAL_API", group="RAW")
        self.capture("CLOSE", close_p, origin="EXTERNAL_API", group="RAW")
        self.capture("VOLUME", volume, origin="EXTERNAL_API", group="RAW")
        if prev_close is not None:
            self.capture("PREVIOUS_CLOSE", prev_close, origin="EXTERNAL_API", group="RAW")
        if vwap is not None:
            self.capture("VWAP", vwap, origin="EXTERNAL_API", group="RAW")
        if high_52w is not None:
            self.capture("52W_HIGH", high_52w, origin="EXTERNAL_API", group="RAW")
        if low_52w is not None:
            self.capture("52W_LOW", low_52w, origin="EXTERNAL_API", group="RAW")

    def capture_dataframe_row(self, row: Union[pd.Series, dict], is_fallback: bool = False, fallback_fields: set = None):
        """Captures every field available in the evaluation input DataFrame/Series."""
        # Layer 1: Raw/Input Coverage
        if hasattr(row, "to_dict"):
            d = row.to_dict()
        else:
            d = dict(row)
            
        if fallback_fields is None:
            # Assume OHLCV are fallback if is_fallback is true
            fallback_fields = {"OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "DATE", "DATETIME"}
            
        for k, v in d.items():
            # Classify known indicators loosely, but fallback to INPUT if unknown.
            group = "INPUT"
            k_upper = str(k).upper()
            if any(ind in k_upper for ind in ["SMA", "EMA", "RSI", "MACD", "ATR", "ADX", "OBV"]):
                group = "INDICATOR"
            elif k_upper in ["OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"]:
                group = "MARKET_DATA"
            
            # Layer 1 requirement: preserve original field names, don't drop anything.
            self.capture(str(k), v, origin="DATAFRAME", group=group)
            
            # Determine freshness
            freshness = "STALE" if is_fallback and k_upper in fallback_fields else "LIVE"
            if getattr(self.entries[str(k)], 'status', 'VALID') in ["NULL", "NAN", "INVALID"]:
                 freshness = "MISSING"
                 
            # Note: We do NOT automatically call add_decision_input here.
            # Only fields actively consumed will be registered later.
            
            # Update the entry object with freshness for later use
            self.entries[str(k)].freshness = freshness

    def capture_indicators(self, rsi: Any = None, sma20: Any = None, sma50: Any = None, sma100: Any = None, sma200: Any = None, ema9: Any = None, ema15: Any = None, ema20: Any = None, ema50: Any = None, ema200: Any = None, macd: Any = None, macd_signal: Any = None, macd_hist: Any = None, atr: Any = None, adx: Any = None, obv: Any = None, vol_ratio: Any = None, prior_20d_high: Any = None, bb_width_pctile: Any = None, retracement_pct: Any = None):
        """Captures standard calculated indicator fields and adds them to the decision manifest."""
        def _add_ind(name: str, val: Any, group: str = "INDICATOR"):
            if val is not None:
                self.capture(name, val, origin="CALCULATED_FROM_PRICE" if name != "VOLUME_RATIO" else "CALCULATED_FROM_VOLUME", group=group)
                self.add_decision_input(name, val, source="Calculated", as_of="Live", freshness="LIVE", required=True, valid=True)
                
        _add_ind("RSI", rsi)
        _add_ind("SMA20", sma20)
        _add_ind("SMA50", sma50)
        _add_ind("SMA100", sma100)
        _add_ind("SMA200", sma200)
        _add_ind("EMA9", ema9)
        _add_ind("EMA15", ema15)
        _add_ind("EMA20", ema20)
        _add_ind("EMA50", ema50)
        _add_ind("EMA200", ema200)
        _add_ind("MACD", macd)
        _add_ind("MACD_SIGNAL", macd_signal)
        _add_ind("MACD_HISTOGRAM", macd_hist)
        _add_ind("ATR", atr)
        _add_ind("ADX", adx)
        _add_ind("OBV", obv)
        _add_ind("VOLUME_RATIO", vol_ratio)
        _add_ind("PRIOR_20D_HIGH", prior_20d_high)
        _add_ind("BB_WIDTH_PCTILE", bb_width_pctile)
        _add_ind("RETRACEMENT_PCT", retracement_pct)

    def capture_fundamentals(self, roce: Any = None, roe: Any = None, debt_equity: Any = None, peg: Any = None, yoy_revenue: Any = None, yoy_profit: Any = None, piotroski_score: Any = None, promoter_pledge: Any = None, mcap: Any = None, altman_z: Any = None, category: Any = None, sector: Any = None):
        """Captures fundamental evaluation metrics."""
        if roce is not None: self.capture("ROCE_PCT", roce, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if roe is not None: self.capture("ROE_PCT", roe, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if debt_equity is not None: self.capture("DEBT_EQUITY", debt_equity, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if peg is not None: self.capture("PEG_RATIO", peg, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if yoy_revenue is not None: self.capture("YOY_REVENUE_PCT", yoy_revenue, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if yoy_profit is not None: self.capture("YOY_PROFIT_PCT", yoy_profit, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if piotroski_score is not None: self.capture("PIOTROSKI_F_SCORE", piotroski_score, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if promoter_pledge is not None: self.capture("PROMOTER_PLEDGE_PCT", promoter_pledge, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if mcap is not None: self.capture("MARKET_CAP_CR", mcap, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if altman_z is not None: self.capture("ALTMAN_Z", altman_z, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if category is not None: self.capture("CATEGORY", category, origin="EXTERNAL_API", group="FUNDAMENTAL")
        if sector is not None: self.capture("SECTOR", sector, origin="EXTERNAL_API", group="FUNDAMENTAL")

    def capture_config(self, key: str, value: Any):
        """Captures configuration threshold."""
        self.configuration[key] = value
        self.capture(key=f"CONFIG_{key.upper()}", value=value, origin="CONFIG", group="CONFIG")

    def capture_parameter(self, name: str, value: Any):
        """Explicitly registers a configuration constant/threshold used in a decision."""
        self.configuration[name] = value
        self.capture(key=name, value=value, origin="CONFIG", group="CONFIG")

    def capture_value(self, name: str, value: Any):
        """Explicitly registers a derived/local value used in a decision (e.g. support level)."""
        self.capture(key=name, value=value, origin="LOCAL", group="DERIVED")

    def capture_trace(self, stage: str, status: str, inputs: dict = None, result: str = None, reason: str = None):
        """Records a step in the sequential decision path."""
        self.decision_trace.append({
            "stage": stage,
            "status": status,
            "inputs": inputs or {},
            "result": result or status,
            "reason": reason or ""
        })

    def capture_gate(self, gate_name: str, passed: bool, actual_val: Any = None, operator_str: str = None, threshold_val: Any = None, reason: str = "", gate_type: str = "THRESHOLD", **kwargs):
        """Captures gate evaluation result. gate_type can be THRESHOLD, BOOLEAN, RANKING, COMPOSITE."""
        gate_info = {
            "passed": passed,
            "status": "PASS" if passed else "FAIL",
            "gate_type": gate_type,
            "reason": reason
        }
        
        # Explicitly register dict-based actual values into the decision manifest
        if isinstance(actual_val, dict):
            for k, v in actual_val.items():
                k_upper = str(k).upper()
                if k_upper in self.entries:
                    freshness = getattr(self.entries[k_upper], 'freshness', 'LIVE')
                    valid = self.entries[k_upper].status == "VALID"
                else:
                    freshness = "LIVE"
                    valid = v is not None and not (isinstance(v, float) and __import__('math').isnan(v))
                
                self.add_decision_input(name=str(k), value=v, source="GateEvaluation", as_of="Live", freshness=freshness, required=True, valid=valid)
                
        if gate_type == "THRESHOLD":
            gate_info.update({"actual": actual_val, "operator": operator_str, "threshold": threshold_val})
        elif gate_type == "BOOLEAN":
            gate_info.update({"actual": actual_val, "expected": threshold_val, "pattern": reason})
        elif gate_type == "RANKING":
            gate_info.update({"actual_rank": actual_val, "max_rank": threshold_val, "competitors": kwargs.get("competitors"), "ranking_basis": kwargs.get("ranking_basis")})
        else:
            gate_info.update({"actual": actual_val, "operator": operator_str, "threshold": threshold_val})

        self.gate_results[gate_name] = gate_info
        self.capture(key=f"GATE_{gate_name.upper()}", value="PASS" if passed else "FAIL", origin="CALCULATED", group="GATE")

    def capture_score(self, component_name: str, score_points: float, max_points: float, reason: str = ""):
        """Captures scoring breakdown component (Section 10)."""
        self.score_breakdown[component_name] = {
            "points": round(float(score_points), 2),
            "max_points": round(float(max_points), 2),
            "reason": reason
        }
        self.capture(key=f"SCORE_{component_name.upper()}", value=score_points, origin="CALCULATED", group="SCORE")

    def capture_score_component(self, name: str, raw: float, weight: float, contribution: float):
        """Captures a fully transparent weighted score component."""
        self.score_breakdown[name] = {
            "raw": float(raw),
            "weight": float(weight),
            "contribution": float(contribution)
        }
        self.capture(key=f"SCORE_{name.upper()}", value=contribution, origin="CALCULATED", group="SCORE")

    def capture_sl_target(self, entry_price: float, sl_price: float, target_price: float, rr_ratio: float = None, risk_pct: float = None, reward_pct: float = None, min_reward_pct: float = None, target_passed: bool = True):
        """Captures SL & Target geometry details (Section 11)."""
        self.sl_target = {
            "entry_price": entry_price,
            "sl_price": sl_price,
            "target_price": target_price,
            "rr_ratio": rr_ratio,
            "risk_pct": risk_pct,
            "reward_pct": reward_pct,
            "min_reward_pct": min_reward_pct,
            "target_passed": target_passed
        }
        self.capture("ENTRY_PRICE", entry_price, origin="CALCULATED", group="SL_TARGET")
        self.capture("SL_PRICE", sl_price, origin="CALCULATED", group="SL_TARGET")
        self.capture("TARGET_PRICE", target_price, origin="CALCULATED", group="SL_TARGET")
        if rr_ratio is not None: self.capture("RR_RATIO", rr_ratio, origin="CALCULATED", group="SL_TARGET")

    def capture_error(self, error_type: str, stage: str, message: str, provider: str = None, retry_count: int = 0):
        """Captures processing exception details (Section 17)."""
        self.terminal_decision = "ERROR"
        self.error_details = {
            "error_type": error_type,
            "stage": stage,
            "message": message,
            "provider": provider,
            "retry_count": retry_count
        }

    def finalize(self, decision: str, primary_reason: str = "", secondary_reasons: List[str] = None, alert_dict: dict = None, mode: str = "FULL"):
        """Finalizes terminal decision state."""
        if self.terminal_decision != "ERROR":
            self.terminal_decision = decision # SELECTED / REJECTED / NOT_APPLICABLE
        self.evaluation_mode = mode
        self.primary_reason = primary_reason or ("ALL_REQUIRED_GATES_PASSED" if decision in ["SELECTED", "PASS"] else "GATE_FAILED")
        self.secondary_reasons = secondary_reasons or []
        if alert_dict:
            self.alert_generated = True
            self.alert_id = alert_dict.get("alert_id") or alert_dict.get("id") or f"ALT_{self.symbol}_{int(time.time())}"
            self.persisted = alert_dict.get("persisted", True)
        self.capture_trace("FINAL_DECISION", decision, result=decision, reason=self.primary_reason)

    @property
    def data_quality_summary(self) -> dict:
        """Computes deterministic data quality counts using mutually exclusive precedence invariant."""
        expected = len(self.entries)
        null_c = sum(1 for e in self.entries.values() if e.status == "NULL")
        nan_c = sum(1 for e in self.entries.values() if e.status == "NAN")
        inv_c = sum(1 for e in self.entries.values() if e.status == "INVALID")
        stale_c = sum(1 for e in self.entries.values() if e.status == "STALE")
        valid_c = sum(1 for e in self.entries.values() if e.status == "VALID")

        present = valid_c + null_c + nan_c + inv_c + stale_c
        
        # Exact invariant assertion
        if present != expected:
            logger.error(f"Data Quality Invariant Broken! present({present}) != expected({expected})")

        return {
            "expected_fields": expected,
            "present_fields": present,
            "valid_fields": valid_c,
            "null_fields": null_c,
            "nan_fields": nan_c,
            "invalid_fields": inv_c,
            "stale_fields": stale_c,
            "decision_input_fields": len(self.decision_manifest)
        }

    def format_terminal_audit_box(self) -> str:
        """Generates a complete, standardized human-readable ASCII terminal audit box (Section 8)."""
        lines = []
        lines.append("=" * 80)
        lines.append("SCANNER TERMINAL DECISION AUDIT")
        lines.append("=" * 80)
        lines.append(f"Scanner      : {self.scanner_name}")
        lines.append(f"Symbol       : {self.symbol}")
        lines.append(f"Exchange     : {self.exchange}")
        lines.append(f"Run ID       : {self.run_id}")
        lines.append(f"Timestamp    : {self.timestamp}")
        lines.append(f"Mode         : {self.evaluation_mode}")
        lines.append(f"FINAL DECISION: {self.terminal_decision}")
        lines.append("=" * 80)

        # 1. RAW MARKET DATA
        raw_entries = [e for e in self.entries.values() if e.group == "RAW"]
        if raw_entries:
            lines.append("\n[ALL INPUT / SOURCE VALUES]")
            for e in raw_entries:
                val_str = f"{e.value:,}" if isinstance(e.value, (int, float)) and e.value > 1000 else str(e.value)
                lines.append(f"{e.key:<24} = {val_str:<18} (Origin: {e.origin})")

        # 2. INDICATORS
        ind_entries = [e for e in self.entries.values() if e.group == "INDICATOR"]
        if ind_entries:
            lines.append("\n[ALL INDICATORS]")
            for e in ind_entries:
                val_str = f"{e.value:.4f}" if isinstance(e.value, float) else str(e.value)
                lines.append(f"{e.key:<24} = {val_str:<18} (Origin: {e.origin})")

        # 2b. FUNDAMENTALS
        fund_entries = [e for e in self.entries.values() if e.group == "FUNDAMENTAL"]
        if fund_entries:
            lines.append("\n[ALL FUNDAMENTAL METRICS]")
            for e in fund_entries:
                val_str = f"{e.value:.4f}" if isinstance(e.value, float) else str(e.value)
                lines.append(f"{e.key:<24} = {val_str:<18} (Origin: {e.origin})")

        # 3. DERIVED VALUES
        der_entries = [e for e in self.entries.values() if e.group == "DERIVED"]
        if der_entries:
            lines.append("\n[ALL DERIVED VALUES]")
            for e in der_entries:
                val_str = f"{e.value:.4f}" if isinstance(e.value, float) else str(e.value)
                lines.append(f"{e.key:<24} = {val_str:<18} (Origin: {e.origin})")

        # 4. CONFIGURATION VALUES
        if self.configuration:
            lines.append("\n[ALL CONFIGURATION VALUES]")
            for k, v in self.configuration.items():
                lines.append(f"{k:<24} = {v}")

        # 5. ALL GATE RESULTS
        if self.gate_results:
            lines.append("\n[ALL GATE RESULTS]")
            for g_name, g_info in self.gate_results.items():
                res_str = g_info["status"]
                g_type = g_info.get("gate_type", "THRESHOLD")
                lines.append(f"{g_name:<24} : {res_str}")
                # [RULE 67] Print scalar gate fields in compact key=value format as approved by user:
                # "For every scalar gate the terminal audit should expose: actual/operator/threshold/result"
                # Composite/range/boolean gates continue to use their own structured fields.
                if g_type in ("THRESHOLD", "BOOLEAN", None, ""):
                    _actual = g_info.get('actual')
                    _op = g_info.get('operator')
                    _thresh = g_info.get('threshold')
                    if _actual is not None or _op is not None or _thresh is not None:
                        lines.append(f"  actual={_actual} operator={repr(_op)} threshold={_thresh} result={res_str}")
                    else:
                        lines.append(f"  result={res_str}")
                else:
                    # Composite/RANKING/RANGE gates: preserve full structured output
                    for field in ("actual", "operator", "threshold", "actual_rank", "max_rank", "expected"):
                        if g_info.get(field) is not None:
                            lines.append(f"  {field:<20} : {g_info[field]}")
                    lines.append(f"  result={res_str}")

        # 6. SCORE COMPONENTS
        if self.score_breakdown:
            lines.append("\n[ALL SCORE COMPONENTS]")
            total_pts = 0.0
            total_max = 0.0
            for sc_name, sc_info in self.score_breakdown.items():
                pts = sc_info["points"]
                max_p = sc_info["max_points"]
                total_pts += pts
                total_max += max_p
                lines.append(f"{sc_name:<24} = {pts:.1f} / {max_p:.1f}")
            lines.append(f"{'TOTAL SCORE':<24} = {total_pts:.1f} / {total_max:.1f}")

        # 7. SL / TARGET
        if self.sl_target:
            lines.append("\n[SL / TARGET]")
            lines.append(f"Entry Price            = ₹{self.sl_target.get('entry_price', 0.0):.2f}")
            lines.append(f"Stop Loss Price        = ₹{self.sl_target.get('sl_price', 0.0):.2f}")
            lines.append(f"Target Price           = ₹{self.sl_target.get('target_price', 0.0):.2f}")
            if self.sl_target.get("rr_ratio"):
                lines.append(f"Risk / Reward Ratio    = {self.sl_target['rr_ratio']:.2f}")

        # 8. ERROR DETAILS
        if self.error_details:
            lines.append("\n[ERROR DETAILS]")
            for ek, ev in self.error_details.items():
                lines.append(f"{ek:<24} = {ev}")

        # 9. DATA QUALITY
        dq = self.data_quality_summary
        lines.append("\n[DATA QUALITY INVARIANTS]")
        lines.append(f"Expected Fields        : {dq['expected_fields']}")
        lines.append(f"Present Fields         : {dq['present_fields']}")
        lines.append(f"Valid                  : {dq['valid_fields']}")
        lines.append(f"Null                   : {dq['null_fields']}")
        lines.append(f"NaN/Inf                : {dq['nan_fields']}")
        lines.append(f"Invalid                : {dq['invalid_fields']}")
        lines.append(f"Stale                  : {dq['stale_fields']}")
        lines.append(f"Decision Manifest Inputs: {dq['decision_input_fields']}")

        if self.decision_manifest:
            lines.append("\n[DECISION MANIFEST]")
            for dm in self.decision_manifest:
                valid_str = "VALID" if dm["valid"] else "INVALID"
                lines.append(f"  - {dm['name']:<15} | req={dm['required']} | val={dm['value']} | src={dm['source']} | fresh={dm['freshness']} | {valid_str}")

        # 10. FINAL
        lines.append("\n[FINAL DECISION]")
        lines.append(f"Terminal Decision      = {self.terminal_decision}")
        lines.append(f"Primary Reason         = {self.primary_reason}")
        if self.secondary_reasons:
            lines.append(f"Secondary Reasons      = {', '.join(self.secondary_reasons)}")
        lines.append(f"Alert Generated        = {'YES' if self.alert_generated else 'NO'}")
        if self.alert_id:
            lines.append(f"Alert ID               = {self.alert_id}")
        lines.append("=" * 80)
        
        return "\n".join(lines)

    def to_telemetry_json(self) -> dict:
        """Serializes decision context to structured dictionary for JSONL emission."""
        return {
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "scanner": self.scanner_name,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "terminal_decision": self.terminal_decision,
            "evaluation_mode": self.evaluation_mode,
            "primary_reason": self.primary_reason,
            "secondary_reasons": self.secondary_reasons,
            "alert_generated": self.alert_generated,
            "alert_id": self.alert_id,
            "persisted": self.persisted,
            "data_quality": self.data_quality_summary,
            "configuration": self.configuration,
            "gate_results": self.gate_results,
            "score_breakdown": self.score_breakdown,
            "sl_target": self.sl_target,
            "error_details": self.error_details,
            "decision_trace": self.decision_trace,
            "decision_manifest": self.decision_manifest,
            "all_values": {k: e.to_dict() for k, e in self.entries.items()}
        }


class GlobalScannerTelemetryEngine:
    """Thread-safe centralized telemetry engine emitting console ASCII boxes and JSONL stream."""
    def __init__(self, scanner_name: str = None, run_id: str = None, regime: str = None, *args, **kwargs):
        self._lock = threading.Lock()
        self.scanner_name = scanner_name
        self.run_id = run_id
        self.regime = regime
        os.makedirs(TELEMETRY_LOG_DIR, exist_ok=True)
        self.stream_records: List[dict] = []
        self._contexts: Dict[str, DecisionContext] = {}

    def get_or_create_context(self, symbol: str, scanner_name: str = None, run_id: str = None) -> DecisionContext:
        """Retrieves or creates a continuous state DecisionContext for the given symbol."""
        with self._lock:
            key = f"{scanner_name or self.scanner_name}_{symbol}"
            if key not in self._contexts:
                self._contexts[key] = DecisionContext(
                    symbol=symbol,
                    scanner_name=scanner_name or self.scanner_name,
                    run_id=run_id or self.run_id
                )
            return self._contexts[key]

    def emit_terminal(self, ctx: DecisionContext):
        """Emits mandatory terminal telemetry record to console and JSONL stream (Section 1 & 21)."""
        with self._lock:
            # 1. Print Human-Readable ASCII Box
            box_text = ctx.format_terminal_audit_box()
            logger.info(f"\n{box_text}")
            logger.info(f"Scanner={ctx.scanner_name} Symbol={ctx.symbol} Decision={ctx.terminal_decision} Gate={ctx.primary_reason} Reason={ctx.primary_reason}")

            # 2. Serialize JSON record
            record_json = ctx.to_telemetry_json()
            self.stream_records.append(record_json)

            # 3. Append to scanner_telemetry.jsonl
            try:
                with open(TELEMETRY_JSONL_PATH, "a") as f:
                    f.write(json.dumps(record_json, default=str) + "\n")
            except Exception as e:
                logger.error(f"Failed to write to scanner_telemetry.jsonl: {e}")

    def record_reject(self, symbol: str, last_stage: str = "PRE_CHECK", gate: str = "REJECTED", actual: Any = None, required: Any = None, start_time: float = None, scanner_name: str = None, run_id: str = None, gate_type: str = "THRESHOLD", operator: str = None, ctx: DecisionContext = None, **kwargs):
        """Helper recording rejected symbol into DecisionContext and emitting full terminal telemetry."""
        ctx = ctx or self.get_or_create_context(symbol=symbol, scanner_name=scanner_name, run_id=run_id)
        if "raw_market" in kwargs and isinstance(kwargs["raw_market"], dict):
            ctx.capture_raw_market(**kwargs["raw_market"])
        if "indicators" in kwargs and isinstance(kwargs["indicators"], dict):
            ctx.capture_indicators(**kwargs["indicators"])
        if "fundamentals" in kwargs and isinstance(kwargs["fundamentals"], dict):
            ctx.capture_fundamentals(**kwargs["fundamentals"])
        if "sl_target" in kwargs and isinstance(kwargs["sl_target"], dict):
            ctx.capture_sl_target(**kwargs["sl_target"])
        ctx.capture_gate(gate_name=gate, passed=False, actual_val=actual, threshold_val=required, operator_str=operator, reason=f"Rejected at stage {last_stage}", gate_type=gate_type, **kwargs)
        
        invalid_data_gates = ["NO_DATA", "STALE_DATA", "DUPLICATE", "MISSING_COL", "INVALID_TIMESTAMP", "INVALID_SNAPSHOT", "MISSING_SNAPSHOT", "NO_TRADING_ACTIVITY"]
        if gate not in invalid_data_gates:
            ctx.add_decision_input(name=gate, value=actual, source="GateCheck", as_of="Live", freshness="LIVE", required=True, valid=True)
        else:
            ctx.add_decision_input(name=gate, value=actual, source="GateCheck", as_of="Live", freshness="LIVE", required=True, valid=False)
            
        ctx.finalize(decision="REJECTED", primary_reason=f"{gate}_FAIL")
        self.emit_terminal(ctx)

    def record_candidate(self, symbol: str, score: float = 0.0, sl: float = 0.0, target: float = 0.0, scanner_name: str = None, run_id: str = None, **kwargs):
        """Helper recording qualified candidate into DecisionContext and emitting full terminal telemetry."""
        ctx = self.get_or_create_context(symbol=symbol, scanner_name=scanner_name, run_id=run_id)
        if "raw_market" in kwargs and isinstance(kwargs["raw_market"], dict):
            ctx.capture_raw_market(**kwargs["raw_market"])
        if "indicators" in kwargs and isinstance(kwargs["indicators"], dict):
            ctx.capture_indicators(**kwargs["indicators"])
        if "fundamentals" in kwargs and isinstance(kwargs["fundamentals"], dict):
            ctx.capture_fundamentals(**kwargs["fundamentals"])
        ctx.capture_score("TOTAL", score, 100.0)
        ctx.capture_sl_target(0.0, sl, target)
        ctx.finalize(decision="SELECTED", primary_reason="ALL_REQUIRED_GATES_PASSED")
        self.emit_terminal(ctx)

    def record_pass(self, symbol: str, score: float = 0.0, rr_ratio: float = 0.0, metrics: Dict[str, Any] = None, start_time: float = None, scanner_name: str = None, run_id: str = None, **kwargs):
        """Helper recording passed candidate into DecisionContext and emitting full terminal telemetry."""
        ctx = self.get_or_create_context(symbol=symbol, scanner_name=scanner_name, run_id=run_id)
        if "raw_market" in kwargs and isinstance(kwargs["raw_market"], dict):
            ctx.capture_raw_market(**kwargs["raw_market"])
        if "indicators" in kwargs and isinstance(kwargs["indicators"], dict):
            ctx.capture_indicators(**kwargs["indicators"])
        if "fundamentals" in kwargs and isinstance(kwargs["fundamentals"], dict):
            ctx.capture_fundamentals(**kwargs["fundamentals"])
        ctx.capture_score("TOTAL", float(score), 100.0)
        if metrics and isinstance(metrics, dict):
            for k, v in metrics.items():
                ctx.capture(k, v, origin="CALCULATED", group="DERIVED")
        if rr_ratio:
            ctx.capture("RR_RATIO", float(rr_ratio), origin="CALCULATED", group="SL_TARGET")
        ctx.finalize(decision="SELECTED", primary_reason="ALL_REQUIRED_GATES_PASSED")
        self.emit_terminal(ctx)

    def flush(self, *args, **kwargs):
        """Flushes telemetry logs."""
        pass

    def record_summary(self, *args, **kwargs):
        """Legacy summary recorder."""
        pass

    def print_summary(self, *args, **kwargs):
        """Prints overall telemetry summary."""
        logger.info(f"📊 Global Scanner Telemetry Engine: {len(self.stream_records)} per-stock telemetry dumps emitted to {TELEMETRY_JSONL_PATH}")

    def print_system_summary(self, *args, **kwargs):
        """Prints overall system telemetry summary."""
        logger.info(f"📊 Global Scanner Telemetry Engine System Summary: {len(self.stream_records)} records processed across all active scanners.")


# Singleton instance for centralized telemetry emission
telemetry_engine = GlobalScannerTelemetryEngine()


class ScannerDecisionLogger:
    """Wrapper that delegates to the global telemetry singleton while injecting the scanner name."""
    def __init__(self, scanner_name: str = None, run_id: str = None, regime: str = None, *args, **kwargs):
        self.scanner_name = scanner_name
        self.run_id = run_id
        self.regime = regime

    def record_reject(self, symbol: str, last_stage: str = "PRE_CHECK", gate: str = "REJECTED", actual: Any = None, required: Any = None, start_time: float = None, gate_type: str = "THRESHOLD", ctx: DecisionContext = None, **kwargs):
        telemetry_engine.record_reject(symbol=symbol, last_stage=last_stage, gate=gate, actual=actual, required=required, start_time=start_time, scanner_name=self.scanner_name, run_id=self.run_id, gate_type=gate_type, ctx=ctx, **kwargs)

    def record_candidate(self, symbol: str, score: float = 0.0, sl: float = 0.0, target: float = 0.0, **kwargs):
        telemetry_engine.record_candidate(symbol=symbol, score=score, sl=sl, target=target, scanner_name=self.scanner_name, run_id=self.run_id, **kwargs)

    def record_pass(self, symbol: str, score: float = 0.0, rr_ratio: float = 0.0, metrics: Dict[str, Any] = None, start_time: float = None, **kwargs):
        telemetry_engine.record_pass(symbol=symbol, score=score, rr_ratio=rr_ratio, metrics=metrics, start_time=start_time, scanner_name=self.scanner_name, run_id=self.run_id, **kwargs)

    def flush(self, *args, **kwargs):
        telemetry_engine.flush(*args, **kwargs)

    def record_summary(self, *args, **kwargs):
        telemetry_engine.record_summary(*args, **kwargs)

    def print_summary(self, *args, **kwargs):
        telemetry_engine.print_summary(*args, **kwargs)

    def print_system_summary(self, *args, **kwargs):
        telemetry_engine.print_system_summary(*args, **kwargs)

    def get_or_create_context(self, symbol: str) -> DecisionContext:
        return telemetry_engine.get_or_create_context(symbol=symbol, scanner_name=self.scanner_name, run_id=self.run_id)


global_telemetry = telemetry_engine


def certify_final_decision(symbol: str, scanner_name: str, entry_price: float, timestamp: str, decision_manifest: list) -> Tuple[bool, str]:
    """
    FINAL_DECISION_CERTIFICATION: Pre-persistence barrier.
    Rejects the alert if critical decision inputs are missing/invalid, 
    if the entry price is invalid, or if the timestamp is missing/bad.
    """
    if pd.isna(entry_price) or entry_price <= 0:
        return False, "INVALID_ENTRY_PRICE"
    
    if pd.isna(timestamp) or str(timestamp).lower() in ["nan", "none", "null", ""]:
        return False, "INVALID_TIMESTAMP"

    for field in decision_manifest:
        if field.get("required", False):
            if not field.get("valid", False):
                return False, f"REQUIRED_DECISION_INPUT_INVALID ({field.get('name')})"
            if str(field.get("value")).lower() in ["nan", "none", "null", "inf", "-inf"]:
                return False, f"REQUIRED_DECISION_INPUT_MISSING ({field.get('name')})"
            if field.get("freshness") == "STALE_CRITICAL":
                return False, f"STALE_CRITICAL_INPUT ({field.get('name')})"

    return True, "CERTIFIED"

