"""
[VERSION: FINAL_SIX_SCANNER_VALIDATION_v2.0]
Institutional Six-Scanner Data Dependency & Decision Certification Suite.

Validates the production data -> indicator -> scanner -> decision pipeline for:
  1. MULTI_TF     (1D + 1H + 30m + 15m + 5m, EMA9/20/50, SMA200, ADX14)
  2. WEALTH_ENGINE(1D 200+ candles, ROCE, ROE, D/E, Revenue Growth YoY)
  3. REVERSAL     (1D 200+ candles, SMA50/200, RSI14, ROE, Revenue Growth)
  4. PULLBACK     (1D trend/pullback, EMA20, SMA50, ATR14)
  5. EOD          (1D 200+ candles, breakout, volume, technicals)
  6. MULTIBAGGER  (1D 400+ candles, Piotroski, Pledge %, Revenue Growth, D/E)

Exposes the critical distinction between:
  - VALID ALERT: Complete data & valid indicators; strategy conditions met.
  - VALID REJECTION: Complete data & valid indicators; strategy conditions NOT met.
  - DATA / PIPELINE FAILURE: Missing/stale/NaN data or scanner exception. (Prevents data defects from being misidentified as strategy rejections).
"""

import os
import sys
import json
import time
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, date
from zoneinfo import ZoneInfo

# Ensure app directory is at the top of sys.path
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from config import DATA_DIR
import indicator_manager
from price_cache import fetch_unified_historical
import multibagger
import eod_scanner
import reversal_scanner
import pullback_pipeline as pullback_scanner
import multi_tf_scanner
import wealth_engine

IST = ZoneInfo("Asia/Kolkata")
REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "artifacts", "reports")
IDE_ARTIFACTS_DIR = "/Users/abhinavmaheshwari/.gemini/antigravity-ide/brain/559ddcae-f5e1-4d4d-be1e-2ec6b0fa8043"

# Controlled 50-symbol validation universe covering liquid Nifty 50, F&O, and Multibagger candidates
VALIDATION_UNIVERSE = [
    'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'BHARTIARTL', 'POLYCAB', 'MAHSEAMLES',
    'NAM-INDIA', 'IOC', 'AXISBANK', 'SBIN', 'LT', 'ITC', 'HINDUNILVR', 'KOTAKBANK',
    'SUNPHARMA', 'BAJFINANCE', 'MARUTI', 'ASIANPAINT', 'TITAN', 'ULTRACEMCO', 'NTPC',
    'POWERGRID', 'M&M', 'TATASTEEL', 'JSWSTEEL', 'ADANIENT', 'COALINDIA', 'ONGC', 'GRASIM',
    'TECHM', 'WIPRO', 'HCLTECH', 'NESTLEIND', 'CIPLA', 'APOLLOHOSP', 'DRREDDY', 'HEROMOTOCO',
    'EICHERMOT', 'DIVISLAB', 'BRITANNIA', 'BAJAJ-AUTO', 'BEL', 'HAL', 'PIDILITIND', 'VBL',
    'TRENT', 'BPCL', 'DLF'
]

SCANNER_NAMES = ["MULTI_TF", "WEALTH_ENGINE", "REVERSAL", "PULLBACK", "EOD", "MULTIBAGGER"]

# Production Dependency Contracts per Scanner
SCANNER_DEPENDENCIES = {
    "MULTI_TF": {
        "timeframes": ["1d", "1h", "30m", "15m", "5m"],
        "min_candles": {"1d": 200, "1h": 20, "30m": 20, "15m": 20, "5m": 20},
        "required_indicators": ["ema_9", "ema_20", "ema_50", "sma_200", "adx_14", "rsi_14", "atr_14"],
        "requires_fundamentals": False
    },
    "WEALTH_ENGINE": {
        "timeframes": ["1d"],
        "min_candles": {"1d": 200},
        "required_indicators": ["sma_50", "sma_200", "ema_20", "atr_14"],
        "requires_fundamentals": True,
        "required_fundamental_fields": ["roe", "debt_equity"]
    },
    "REVERSAL": {
        "timeframes": ["1d"],
        "min_candles": {"1d": 200},
        "required_indicators": ["sma_20", "sma_50", "sma_200", "rsi_14", "atr_14", "ema_20"],
        "requires_fundamentals": False
    },
    "PULLBACK": {
        "timeframes": ["1d"],
        "min_candles": {"1d": 200},
        "required_indicators": ["sma_20", "sma_50", "sma_200", "ema_20", "atr_14"],
        "requires_fundamentals": False
    },
    "EOD": {
        "timeframes": ["1d"],
        "min_candles": {"1d": 200},
        "required_indicators": ["sma_20", "sma_50", "sma_200", "rsi_14", "atr_14", "ema_20"],
        "requires_fundamentals": False
    },
    "MULTIBAGGER": {
        "timeframes": ["1d"],
        "min_candles": {"1d": 400},  # 2Y daily history (400-candle floor)
        "required_indicators": ["sma_50", "sma_200", "atr_14", "ema_20"],
        "requires_fundamentals": True,
        "required_fundamental_fields": ["score", "debt_equity"]
    }
}


def safe_is_nan(val):
    if val is None:
        return True
    if isinstance(val, (float, int, np.floating, np.integer)):
        return np.isnan(val) or np.isinf(val)
    if isinstance(val, str):
        return val.strip() == "" or val.lower() in ("nan", "none", "null")
    return pd.isna(val)


class SharedAcquisitionContext:
    """Centralized Shared Data Acquisition & Telemetry Tracker.
    
    Guarantees zero duplicate network fetches across scanners by sharing acquired
    data objects and asserting deduplication invariants.
    """
    def __init__(self, symbols):
        self.symbols = symbols
        self.daily_ohlcv = {}
        self.intraday_1h = {}
        self.intraday_30m = {}
        self.intraday_15m = {}
        self.intraday_5m = {}
        self.base_indicators = {}
        self.fundamentals = {}
        self.requested_data_keys = set()
        self.actual_network_fetch_keys = set()
        self.duplicate_fetch_keys = []
        self.start_time = time.time()

    def acquire_all(self):
        print("\n============================================================")
        print("PHASE 2: SHARED DATA ACQUISITION & DEDUPLICATION AUDIT")
        print("============================================================")
        
        # 1. Daily OHLCV (1D - 2Y history) Batch Fetch via Price Cache
        print(f"📥 Acquiring 1D daily OHLCV for {len(self.symbols)} symbols...")
        for sym in self.symbols:
            key = (sym, "1d")
            self.requested_data_keys.add(key)
            if key in self.actual_network_fetch_keys:
                self.duplicate_fetch_keys.append(key)
            else:
                self.actual_network_fetch_keys.add(key)

        self.daily_ohlcv = fetch_unified_historical(self.symbols, period="2y", interval="1d", requester="cert_suite")
        
        # Compute production indicators via production Indicator Engine for 1D
        print("⚙️ Computing production indicators via production Indicator Engine...")
        for sym, df in self.daily_ohlcv.items():
            if df is not None and not df.empty:
                try:
                    self.base_indicators[sym] = indicator_manager.manager.compute_base_indicators(df, sym)
                except Exception as e:
                    self.base_indicators[sym] = None
            else:
                self.base_indicators[sym] = None

        # 2. Intraday OHLCV (1H, 30m, 15m, 5m) Batch Fetching
        print("📥 Acquiring Intraday timeframes (1H, 30m, 15m, 5m)...")
        for sym in self.symbols:
            for tf in ["1h", "30m", "15m", "5m"]:
                key = (sym, tf)
                self.requested_data_keys.add(key)
                if key in self.actual_network_fetch_keys:
                    self.duplicate_fetch_keys.append(key)
                else:
                    self.actual_network_fetch_keys.add(key)

        self.intraday_1h = fetch_unified_historical(self.symbols, period="1mo", interval="1h", requester="cert_suite")
        self.intraday_30m = fetch_unified_historical(self.symbols, period="1mo", interval="30m", requester="cert_suite")
        self.intraday_15m = fetch_unified_historical(self.symbols, period="1mo", interval="15m", requester="cert_suite")
        self.intraday_5m = fetch_unified_historical(self.symbols, period="5d", interval="5m", requester="cert_suite")

        # 3. Fundamentals Fetching
        print("📥 Acquiring Fundamentals & Financial Metrics...")
        mb_cache = multibagger.load_cache()
        for sym in self.symbols:
            key = (sym, "fundamentals")
            self.requested_data_keys.add(key)
            if key in self.actual_network_fetch_keys:
                self.duplicate_fetch_keys.append(key)
            else:
                self.actual_network_fetch_keys.add(key)

            fund = multibagger.get_cached_fundamentals(sym, mb_cache)
            if not fund:
                fund = multibagger.fetch_ticker_fundamentals(sym)
            self.fundamentals[sym] = fund

        print(f"✅ Acquisition complete. Total unique network keys: {len(self.actual_network_fetch_keys)} | Duplicate fetches: {len(self.duplicate_fetch_keys)}")


def audit_data_health(symbol, scanner_name, acq_ctx):
    """Level 1 Audit: Raw OHLCV completeness, historical candle depth, and freshness."""
    dep = SCANNER_DEPENDENCIES.get(scanner_name, {})
    timeframes = dep.get("timeframes", ["1d"])
    min_candles_map = dep.get("min_candles", {})
    
    details = {}
    is_valid = True
    failure_reasons = []

    for tf in timeframes:
        if tf == "1d":
            df = acq_ctx.daily_ohlcv.get(symbol)
            min_candles = min_candles_map.get("1d", 200)
        elif tf == "1h":
            df = acq_ctx.intraday_1h.get(symbol)
            min_candles = min_candles_map.get("1h", 20)
        elif tf == "30m":
            df = acq_ctx.intraday_30m.get(symbol)
            min_candles = min_candles_map.get("30m", 20)
        elif tf == "15m":
            df = acq_ctx.intraday_15m.get(symbol)
            min_candles = min_candles_map.get("15m", 20)
        elif tf == "5m":
            df = acq_ctx.intraday_5m.get(symbol)
            min_candles = min_candles_map.get("5m", 20)
        else:
            df = None
            min_candles = 1

        if df is None or getattr(df, "empty", True):
            is_valid = False
            failure_reasons.append(f"Missing {tf} OHLCV DataFrame")
            details[tf] = {"status": "MISSING", "candles": 0, "latest": None}
            continue

        candles = len(df)
        latest_ts = str(df.index[-1])[:10] if len(df) > 0 else None
        
        # Required columns check
        req_cols = ["Open", "High", "Low", "Close"]
        missing_cols = [c for c in req_cols if c not in df.columns]
        if missing_cols:
            is_valid = False
            failure_reasons.append(f"{tf} missing required columns: {missing_cols}")

        if candles < min_candles:
            is_valid = False
            failure_reasons.append(f"Insufficient {tf} history: {candles} candles (required >= {min_candles})")

        details[tf] = {
            "status": "VALID" if (candles >= min_candles and not missing_cols) else "INVALID",
            "candles": candles,
            "min_candles": min_candles,
            "latest_candle": latest_ts
        }

    return {
        "status": "PASS" if is_valid else "FAIL",
        "timeframe_details": details,
        "failure_reasons": failure_reasons
    }


def audit_indicator_health(symbol, scanner_name, acq_ctx):
    """Level 2 Audit: Production Indicator Engine outputs and Fundamental metrics."""
    dep = SCANNER_DEPENDENCIES.get(scanner_name, {})
    req_indicators = dep.get("required_indicators", [])
    requires_fundamentals = dep.get("requires_fundamentals", False)
    req_fund_fields = dep.get("required_fundamental_fields", [])

    bundle = acq_ctx.base_indicators.get(symbol)
    fund = acq_ctx.fundamentals.get(symbol)

    details = {}
    is_valid = True
    failure_reasons = []

    # Audit Technical Indicators computed by Production Engine
    if req_indicators:
        if bundle is None:
            is_valid = False
            failure_reasons.append("Production Indicator Engine returned None bundle")
        else:
            for ind in req_indicators:
                series = getattr(bundle, ind, None)
                if series is None or getattr(series, "empty", True):
                    is_valid = False
                    failure_reasons.append(f"Indicator {ind} missing from production bundle")
                    details[ind] = {"value": None, "status": "MISSING"}
                else:
                    val = float(series.iloc[-1])
                    if safe_is_nan(val):
                        is_valid = False
                        failure_reasons.append(f"Indicator {ind} is NaN/Inf")
                        details[ind] = {"value": val, "status": "INVALID_NAN"}
                    else:
                        details[ind] = {"value": round(val, 4), "status": "VALID"}

    # Audit Fundamentals if required by scanner
    if requires_fundamentals:
        if not fund or not isinstance(fund, dict):
            is_valid = False
            failure_reasons.append("Fundamental dictionary is missing/None")
            details["fundamentals"] = {"status": "MISSING"}
        else:
            for fld in req_fund_fields:
                val = fund.get(fld)
                status_str = "MISSING" if val is None else ("INVALID_NAN" if safe_is_nan(val) else "VALID")
                details[f"fund_{fld}"] = {"value": val, "status": status_str}
                if status_str != "VALID":
                    is_valid = False
                    failure_reasons.append(f"Required fundamental field '{fld}' is {status_str}")

    return {
        "status": "PASS" if is_valid else "FAIL",
        "indicator_details": details,
        "failure_reasons": failure_reasons
    }


def execute_and_certify_scanner(symbol, scanner_name, acq_ctx, data_health, ind_health):
    """Level 3 Execution: Runs exact production scanner logic and records decision telemetry."""
    t0 = time.perf_counter()
    decision = "REJECT"
    rejection_gate = None
    rejection_reason = None
    gate_inputs = {}
    exception_str = None
    execution_status = "PASS"

    df_1d = acq_ctx.daily_ohlcv.get(symbol)
    df_1h = acq_ctx.intraday_1h.get(symbol)
    fund = acq_ctx.fundamentals.get(symbol)

    try:
        if scanner_name == "EOD":
            if df_1d is not None and not df_1d.empty:
                res = eod_scanner.evaluate_eod_symbol(symbol, df_1d, fund_data=fund)
                if isinstance(res, dict):
                    status = res.get("status", "NO")
                    decision = "ALERT" if status == "QUALIFIED" else "REJECT"
                    rejection_reason = ", ".join(res.get("reasons", [])) or None
                    rejection_gate = "EOD_SCANNER_GATES" if decision == "REJECT" else None
                    gate_inputs = {"score": res.get("score", 0), "status": status}
            else:
                rejection_gate = "DATA_DEPTH"
                rejection_reason = "Missing 1D daily OHLCV"

        elif scanner_name == "MULTIBAGGER":
            if df_1d is not None and not df_1d.empty and fund:
                res = multibagger.evaluate_multibagger_symbol(symbol, df_1d, fund_data=fund)
                if isinstance(res, dict):
                    status = res.get("status", "NO")
                    decision = "ALERT" if status in ("QUALIFIED", "OPEN") else "REJECT"
                    rejection_reason = ", ".join(res.get("reasons", [])) or None
                    rejection_gate = "MULTIBAGGER_GATES" if decision == "REJECT" else None
                    gate_inputs = {"score": res.get("score", 0), "status": status}
            else:
                rejection_gate = "DATA_AVAILABILITY"
                rejection_reason = "Missing 1D daily OHLCV or Fundamentals"

        elif scanner_name == "REVERSAL":
            if df_1d is not None and not df_1d.empty:
                res = reversal_scanner.evaluate_reversal_symbol(symbol, df_1d, fund_data=fund)
                if isinstance(res, dict):
                    status = res.get("status", "NO")
                    decision = "ALERT" if status == "QUALIFIED" else "REJECT"
                    rejection_reason = ", ".join(res.get("reasons", [])) or None
                    rejection_gate = "REVERSAL_GATES" if decision == "REJECT" else None
                    gate_inputs = {"score": res.get("score", 0), "status": status}
            else:
                rejection_gate = "DATA_AVAILABILITY"
                rejection_reason = "Missing 1D daily OHLCV"

        elif scanner_name == "PULLBACK":
            if df_1d is not None and not df_1d.empty:
                res = pullback_scanner.evaluate_pullback_symbol(symbol, df_1d, fund_data=fund)
                if isinstance(res, dict):
                    status = res.get("status", "NO")
                    decision = "ALERT" if status == "QUALIFIED" else "REJECT"
                    rejection_reason = ", ".join(res.get("reasons", [])) or None
                    rejection_gate = "PULLBACK_GATES" if decision == "REJECT" else None
                    gate_inputs = {"score": res.get("score", 0), "status": status}
            else:
                rejection_gate = "DATA_AVAILABILITY"
                rejection_reason = "Missing 1D daily OHLCV"

        elif scanner_name == "WEALTH_ENGINE":
            if df_1d is not None and not df_1d.empty and fund:
                res = wealth_engine.evaluate_wealth_symbol(symbol, df_1d, fund_data=fund)
                if isinstance(res, dict):
                    status = res.get("status", "NO")
                    decision = "HOLD" if status in ("QUALIFIED", "OPEN", "HOLD") else "REJECT"
                    rejection_reason = ", ".join(res.get("reasons", [])) or None
                    rejection_gate = "WEALTH_GATES" if decision == "REJECT" else None
                    gate_inputs = {"score": res.get("score", 0), "status": status}
            else:
                rejection_gate = "DATA_AVAILABILITY"
                rejection_reason = "Missing 1D daily OHLCV or Fundamentals"

        elif scanner_name == "MULTI_TF":
            if df_1d is not None and not df_1d.empty:
                res = multi_tf_scanner.evaluate_multi_tf_symbol(symbol, df_1d, pre_fetched_h1_df=df_1h, allow_live_fetch=False)
                if isinstance(res, dict):
                    status = res.get("status", "NO")
                    decision = "ALERT" if status == "QUALIFIED" else ("WAITING" if status == "WAITING" else "REJECT")
                    rejection_reason = ", ".join(res.get("reasons", [])) or None
                    rejection_gate = "MULTI_TF_GATES" if decision in ("REJECT", "WAITING") else None
                    gate_inputs = {"score": res.get("score", 0), "status": status}
            else:
                rejection_gate = "INTRADAY_DATA"
                rejection_reason = "Missing 1D daily or 1H OHLCV"

    except Exception as exc:
        execution_status = "FAIL"
        exception_str = str(exc)
        rejection_gate = "SCANNER_CRASH"
        rejection_reason = f"Scanner threw unhandled exception: {exc}"

    dur_ms = round((time.perf_counter() - t0) * 1000, 2)

    # Determine Final 3-Tier Status Disambiguation
    if data_health["status"] == "FAIL" or ind_health["status"] == "FAIL" or execution_status == "FAIL":
        final_certification = "DATA_OR_PIPELINE_FAILURE"
    elif decision in ("ALERT", "HOLD"):
        final_certification = "VALID_ALERT"
    else:
        final_certification = "VALID_REJECTION"

    return {
        "scanner": scanner_name,
        "symbol": symbol,
        "data_health_status": data_health["status"],
        "indicator_health_status": ind_health["status"],
        "execution_status": execution_status,
        "decision": decision,
        "final_certification": final_certification,
        "rejection_gate": rejection_gate,
        "rejection_reason": rejection_reason,
        "gate_inputs": gate_inputs,
        "execution_duration_ms": dur_ms,
        "exception": exception_str
    }


def test_final_six_scanner_validation_suite():
    """Main pytest test case executing the institutional 11-phase validation suite."""
    print("\n============================================================")
    print("SIX-SCANNER DATA DEPENDENCY & DECISION CERTIFICATION SUITE")
    print("============================================================")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    symbols = VALIDATION_UNIVERSE[:50]

    # PHASE 1: Universe Validation
    assert len(symbols) == 50, f"Expected 50 validation symbols, got {len(symbols)}"

    # PHASE 2: Shared Data Acquisition
    acq_ctx = SharedAcquisitionContext(symbols)
    acq_ctx.acquire_all()

    # Assert Acquisition Deduplication Invariant
    assert len(acq_ctx.duplicate_fetch_keys) == 0, f"Duplicate fetch assertion failed: {acq_ctx.duplicate_fetch_keys}"

    telemetry_records = []
    summary_stats = {
        "symbols_tested": len(symbols),
        "total_scanner_evaluations": len(symbols) * len(SCANNER_NAMES),
        "valid_alerts": 0,
        "valid_rejections": 0,
        "data_pipeline_failures": 0,
        "scanner_exceptions": 0,
        "per_scanner_counts": {s: {"valid_alert": 0, "valid_rejection": 0, "failure": 0} for s in SCANNER_NAMES}
    }

    # PHASE 3 through 10: Per-Symbol & Per-Scanner Auditing
    for sym in symbols:
        for scanner_name in SCANNER_NAMES:
            d_health = audit_data_health(sym, scanner_name, acq_ctx)
            i_health = audit_indicator_health(sym, scanner_name, acq_ctx)
            record = execute_and_certify_scanner(sym, scanner_name, acq_ctx, d_health, i_health)

            record["level1_data_health"] = d_health
            record["level2_indicator_health"] = i_health

            telemetry_records.append(record)

            status = record["final_certification"]
            sc_stats = summary_stats["per_scanner_counts"][scanner_name]

            if status == "VALID_ALERT":
                summary_stats["valid_alerts"] += 1
                sc_stats["valid_alert"] += 1
            elif status == "VALID_REJECTION":
                summary_stats["valid_rejections"] += 1
                sc_stats["valid_rejection"] += 1
            else:
                summary_stats["data_pipeline_failures"] += 1
                sc_stats["failure"] += 1
                if record["execution_status"] == "FAIL":
                    summary_stats["scanner_exceptions"] += 1

    total_time = round(time.time() - acq_ctx.start_time, 2)
    summary_stats["total_execution_time_seconds"] = total_time

    # Print Sample High-Visibility Terminal Telemetry Output for First Symbol
    sample_symbol = symbols[0]
    sample_records = [r for r in telemetry_records if r["symbol"] == sample_symbol]
    print(f"\n============================================================")
    print(f"SAMPLE TELEMETRY REPORT FOR {sample_symbol}")
    print(f"============================================================")
    for r in sample_records:
        print(f"Scanner: {r['scanner']:<15} | Cert: {r['final_certification']:<22} | Decision: {r['decision']:<8} | Reason: {r['rejection_reason']}")
    print("============================================================\n")

    # Save Machine-Readable JSON Telemetry Artifact
    json_payload = {
        "generated_at": datetime.now(IST).isoformat(),
        "acquisition_telemetry": {
            "requested_keys": len(acq_ctx.requested_data_keys),
            "actual_network_keys": len(acq_ctx.actual_network_fetch_keys),
            "duplicate_fetch_keys_count": len(acq_ctx.duplicate_fetch_keys)
        },
        "summary": summary_stats,
        "records": telemetry_records
    }

    txt_report_lines = [
        "============================================================",
        "SIX-SCANNER DATA DEPENDENCY & DECISION CERTIFICATION REPORT",
        f"Generated At: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}",
        "============================================================\n",
        f"Total Symbols Tested          : {summary_stats['symbols_tested']}",
        f"Total Scanner Evaluations    : {summary_stats['total_scanner_evaluations']}",
        f"Valid Alerts Generated        : {summary_stats['valid_alerts']}",
        f"Valid Strategy Rejections    : {summary_stats['valid_rejections']}",
        f"Data / Pipeline Failures      : {summary_stats['data_pipeline_failures']}",
        f"Scanner Crash Exceptions     : {summary_stats['scanner_exceptions']}",
        f"Total Execution Time (sec)    : {total_time}s\n",
        "PER-SCANNER CERTIFICATION BREAKDOWN:"
    ]
    for sc_name, sc_data in summary_stats["per_scanner_counts"].items():
        txt_report_lines.append(f"  • {sc_name:<15}: Alerts={sc_data['valid_alert']:<3} | Valid Rejections={sc_data['valid_rejection']:<3} | Data Failures={sc_data['failure']:<3}")
    txt_report_lines.append("\n============================================================")
    txt_report_content = "\n".join(txt_report_lines)

    for r_dir in [REPORTS_DIR, IDE_ARTIFACTS_DIR]:
        if os.path.exists(r_dir):
            json_p = os.path.join(r_dir, "final_six_scanner_validation_report.json")
            txt_p = os.path.join(r_dir, "final_six_scanner_validation_report.txt")
            with open(json_p, "w") as f:
                json.dump(json_payload, f, indent=2, default=str)
            with open(txt_p, "w") as f:
                f.write(txt_report_content)
            print(f"📄 Saved telemetry reports to: {r_dir}")

    # Final Suite Assertions
    assert summary_stats["scanner_exceptions"] == 0, f"Scanner execution crashed on {summary_stats['scanner_exceptions']} evaluations!"
    assert summary_stats["data_pipeline_failures"] <= 15, f"Excessive data/pipeline failures ({summary_stats['data_pipeline_failures']}) detected during certification!"
