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


def sanitize_telemetry_value(val: Any) -> Tuple[Any, str, str]:
    """Sanitizes raw value and returns (sanitized_val, status, reason)."""
    if val is None:
        return "NONE", "MISSING", "VALUE_IS_NONE"
    if isinstance(val, float):
        if math.isnan(val) or np.isnan(val):
            return "NaN", "INVALID", "VALUE_IS_NAN"
        if math.isinf(val) or np.isinf(val):
            return "INF", "INVALID", "VALUE_IS_INF"
        return round(val, 4), "VALID", "OK"
    if isinstance(val, (int, np.integer)):
        return int(val), "VALID", "OK"
    if isinstance(val, bool):
        return bool(val), "VALID", "OK"
    return str(val), "VALID", "OK"


class TelemetryValueEntry:
    """Represents a single captured field with origin, status, and reason."""
    def __init__(self, key: str, value: Any, origin: str = "CALCULATED", group: str = "DERIVED", source_series: str = None):
        self.key = key
        self.raw_value = value
        self.origin = origin # EXTERNAL_API, CALCULATED, CONFIG, DERIVED
        self.group = group # RAW, INDICATOR, DERIVED, CONFIG, GATE, SCORE, SL_TARGET, DEPENDENCY
        self.source_series = source_series
        self.timestamp = time.time()
        
        sanitized, status, reason = sanitize_telemetry_value(value)
        self.value = sanitized
        self.status = status
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "status": self.status,
            "reason": self.reason,
            "origin": self.origin,
            "group": self.group,
            "source_series": self.source_series
        }


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
        self.timestamp = time.strftime("%Y-%m-%d %H:%M:%S IST")
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

    def capture_indicators(self, rsi: Any = None, sma20: Any = None, sma50: Any = None, sma100: Any = None, sma200: Any = None, ema9: Any = None, ema15: Any = None, ema20: Any = None, ema50: Any = None, ema200: Any = None, macd: Any = None, macd_signal: Any = None, macd_hist: Any = None, atr: Any = None, adx: Any = None, obv: Any = None, vol_ratio: Any = None, prior_20d_high: Any = None, bb_width_pctile: Any = None, retracement_pct: Any = None):
        """Captures standard calculated indicator fields."""
        if rsi is not None: self.capture("RSI", rsi, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if sma20 is not None: self.capture("SMA20", sma20, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if sma50 is not None: self.capture("SMA50", sma50, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if sma100 is not None: self.capture("SMA100", sma100, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if sma200 is not None: self.capture("SMA200", sma200, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if ema9 is not None: self.capture("EMA9", ema9, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if ema15 is not None: self.capture("EMA15", ema15, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if ema20 is not None: self.capture("EMA20", ema20, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if ema50 is not None: self.capture("EMA50", ema50, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if ema200 is not None: self.capture("EMA200", ema200, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if macd is not None: self.capture("MACD", macd, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if macd_signal is not None: self.capture("MACD_SIGNAL", macd_signal, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if macd_hist is not None: self.capture("MACD_HISTOGRAM", macd_hist, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if atr is not None: self.capture("ATR", atr, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if adx is not None: self.capture("ADX", adx, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if obv is not None: self.capture("OBV", obv, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if vol_ratio is not None: self.capture("VOLUME_RATIO", vol_ratio, origin="CALCULATED_FROM_VOLUME", group="INDICATOR")
        if prior_20d_high is not None: self.capture("PRIOR_20D_HIGH", prior_20d_high, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if bb_width_pctile is not None: self.capture("BB_WIDTH_PCTILE", bb_width_pctile, origin="CALCULATED_FROM_PRICE", group="INDICATOR")
        if retracement_pct is not None: self.capture("RETRACEMENT_PCT", retracement_pct, origin="CALCULATED_FROM_PRICE", group="INDICATOR")

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

    def capture_gate(self, gate_name: str, passed: bool, actual_val: Any = None, operator_str: str = ">=", threshold_val: Any = None, reason: str = ""):
        """Captures gate evaluation result with operator and expected threshold (Section 9)."""
        self.gate_results[gate_name] = {
            "passed": passed,
            "status": "PASS" if passed else "FAIL",
            "actual": actual_val,
            "operator": operator_str,
            "threshold": threshold_val,
            "reason": reason
        }
        self.capture(key=f"GATE_{gate_name.upper()}", value="PASS" if passed else "FAIL", origin="CALCULATED", group="GATE")

    def capture_score(self, component_name: str, score_points: float, max_points: float, reason: str = ""):
        """Captures scoring breakdown component (Section 10)."""
        self.score_breakdown[component_name] = {
            "points": round(float(score_points), 2),
            "max_points": round(float(max_points), 2),
            "reason": reason
        }
        self.capture(key=f"SCORE_{component_name.upper()}", value=score_points, origin="CALCULATED", group="SCORE")

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

    @property
    def data_quality_summary(self) -> dict:
        """Computes data quality counts."""
        missing = sum(1 for e in self.entries.values() if e.status == "MISSING")
        invalid = sum(1 for e in self.entries.values() if e.status == "INVALID")
        valid = sum(1 for e in self.entries.values() if e.status == "VALID")
        return {
            "expected_values": len(self.entries),
            "captured_values": len(self.entries),
            "valid_count": valid,
            "missing_count": missing,
            "invalid_count": invalid,
            "none_count": missing,
            "nan_count": invalid,
            "fallback_count": sum(1 for e in self.entries.values() if e.origin == "FALLBACK"),
            "stale_count": sum(1 for e in self.entries.values() if e.origin == "STALE_CACHE")
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
                lines.append(f"{g_name:<24} : {res_str}")
                lines.append(f"  Actual               : {g_info.get('actual')}")
                lines.append(f"  Operator             : {g_info.get('operator')}")
                lines.append(f"  Threshold            : {g_info.get('threshold')}")
                lines.append(f"  Result               : {res_str}")

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
        lines.append("\n[DATA QUALITY]")
        lines.append(f"Expected Values        : {dq['expected_values']}")
        lines.append(f"Captured Values        : {dq['captured_values']}")
        lines.append(f"Missing                : {dq['missing_count']}")
        lines.append(f"None                   : {dq['none_count']}")
        lines.append(f"NaN                    : {dq['nan_count']}")
        lines.append(f"Invalid                : {dq['invalid_count']}")

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

    def record_reject(self, symbol: str, last_stage: str = "PRE_CHECK", gate: str = "REJECTED", actual: Any = None, required: Any = None, start_time: float = None, **kwargs):
        """Helper recording rejected symbol into DecisionContext and emitting full terminal telemetry."""
        ctx = DecisionContext(symbol=symbol, scanner_name=self.scanner_name or "PULLBACK", run_id=self.run_id)
        if "raw_market" in kwargs and isinstance(kwargs["raw_market"], dict):
            ctx.capture_raw_market(**kwargs["raw_market"])
        if "indicators" in kwargs and isinstance(kwargs["indicators"], dict):
            ctx.capture_indicators(**kwargs["indicators"])
        if "fundamentals" in kwargs and isinstance(kwargs["fundamentals"], dict):
            ctx.capture_fundamentals(**kwargs["fundamentals"])
        if "sl_target" in kwargs and isinstance(kwargs["sl_target"], dict):
            ctx.capture_sl_target(**kwargs["sl_target"])
        ctx.capture_gate(gate_name=gate, passed=False, actual_val=actual, threshold_val=required, reason=f"Rejected at stage {last_stage}")
        ctx.finalize(decision="REJECTED", primary_reason=f"{gate}_FAIL")
        self.emit_terminal(ctx)

    def record_candidate(self, symbol: str, score: float = 0.0, sl: float = 0.0, target: float = 0.0, **kwargs):
        """Helper recording qualified candidate into DecisionContext and emitting full terminal telemetry."""
        ctx = DecisionContext(symbol=symbol, scanner_name=self.scanner_name or "PULLBACK", run_id=self.run_id)
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

    def record_pass(self, symbol: str, score: float = 0.0, rr_ratio: float = 0.0, metrics: Dict[str, Any] = None, start_time: float = None, **kwargs):
        """Helper recording passed candidate into DecisionContext and emitting full terminal telemetry."""
        ctx = DecisionContext(symbol=symbol, scanner_name=self.scanner_name or "EOD", run_id=self.run_id)
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

# Backward compatibility aliases for existing scanner imports
ScannerDecisionLogger = GlobalScannerTelemetryEngine
global_telemetry = telemetry_engine

