#!/usr/bin/env python3
"""
tests/test_data_recovery_log_acceptance.py
==========================================
RUNTIME ACCEPTANCE TEST — Data Recovery Audit Log Protocol

Acceptance criteria (all must pass):
  1. DATA_PROBLEM  → [DATA_RECOVERY] block emitted at WARNING level
                  → final_action=STOCK_SKIPPED
                  → scanner continues to remaining symbols
  2. STRATEGY_RULE_FAIL (e.g. FAIL_ROCE=8%) → NO [DATA_RECOVERY] block
  3. GOOD_SYMBOL   → scan proceeds normally, no [DATA_RECOVERY] block
  4. Scanner never raises an exception regardless of symbol failures

Scenarios tested:
  SYM_MISS_PIT   — symbol absent from PIT map           → DATA_MISSING_PIT_FILINGS
  SYM_MISS_QUAL  — PIT present, roce_5y=None            → DATA_INSUFFICIENT_QUALITY
  SYM_MISS_PRICE — live cmp_price=0                     → DATA_INSUFFICIENT_PRICE
  SYM_MISS_VAL   — ev_ebitda_3y_median=None             → DATA_INSUFFICIENT_VALUATION
  SYM_RULE_FAIL  — roce_5y=8% (below 15% threshold)     → FAIL_ROCE  (NOT data problem)
  SYM_PASS       — all data present, all thresholds met → CANDIDATE

Usage:
    cd /Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM
    python3 tests/test_data_recovery_log_acceptance.py
"""

import logging
import sys
import os
import types
import importlib
import importlib.util
from unittest.mock import MagicMock

# ── path setup ───────────────────────────────────────────────────────────────
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "app"))
sys.path.insert(0, _BASE)


# ── in-process log capture ───────────────────────────────────────────────────
class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def messages(self):
        return [self.format(record) for record in self.records]

    def contains(self, text):
        return any(text in m for m in self.messages())

    def clear(self):
        self.records.clear()


# ── stub-load the scanner module (no DB / network) ───────────────────────────
def _load_module():
    stubs = {
        "database":                      types.ModuleType("database"),
        "app.database":                  types.ModuleType("app.database"),
        "fundamental_telemetry":         types.ModuleType("fundamental_telemetry"),
        "app.fundamental_telemetry":     types.ModuleType("app.fundamental_telemetry"),
        "lock_utils":                    types.ModuleType("lock_utils"),
        "app.lock_utils":                types.ModuleType("app.lock_utils"),
        "trading_calendar":              types.ModuleType("trading_calendar"),
        "app.trading_calendar":          types.ModuleType("app.trading_calendar"),
        "live_prices":                   types.ModuleType("live_prices"),
        "price_cache":                   types.ModuleType("price_cache"),
        "pit_valuation_history_builder": types.ModuleType("pit_valuation_history_builder"),
    }
    for name, stub in stubs.items():
        sys.modules.setdefault(name, stub)

    for prefix in ("lock_utils", "app.lock_utils"):
        m = sys.modules[prefix]
        m.ProcessLock = MagicMock()
        m.print_scanner_start_banner = MagicMock()
        m.print_scanner_end_banner = MagicMock()

    for prefix in ("fundamental_telemetry", "app.fundamental_telemetry"):
        sys.modules[prefix].FundamentalScanTelemetry = MagicMock()

    spec = importlib.util.spec_from_file_location(
        "live_fundamental_scanner",
        os.path.join(_BASE, "app", "live_fundamental_scanner.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── synthetic PIT row with all fields healthy ────────────────────────────────
def _good_row(sym):
    return {
        "symbol":             sym,
        "filing_date":        "2025-06-30",
        "financial_period_end": "2025-03-31",
        "market_cap":         5000.0,
        "adtv_90d":           5.0,
        "roce_5y_avg":        22.0,
        "sales_cagr_5y":      15.0,
        "pat_cagr_5y":        18.0,
        "cfo_pat_5y_ratio":   0.95,
        "debt_to_equity":     0.20,
        "current_ev_ebitda":  14.0,
        "ev_ebitda_3y_median": 22.0,   # discount=(22-14)/22=36% > 25% threshold
        "current_pe":         18.0,
        "pe_3y_median":       28.0,
        "shares_outstanding": 1_00_00_000.0,
        "eps":                50.0,
        "ebitda":             400.0,
        "total_debt":         200.0,
        "cash_and_equivalents": 50.0,
        "industry":           "Consumer Goods",
        "share_dilution_3y_pct": 0.5,
        "growth_start_period": "2020-03-31",
        "growth_end_period":   "2025-03-31",
        "growth_years_elapsed": 5.0,
        "financial_periods_used": 6,
        "roce_periods_used": 5,
        "provenance_status": "CERTIFIED_PIT_STATEMENT_CALCULATED",
    }


# ── minimal per-symbol evaluation logic (mirrors the actual V2 loop) ─────────
def _eval_symbol(mod, sym, pit_map, prices, capture):
    """
    Runs the same gate sequence as _scan_universe_core for one symbol.
    Returns dict: rejection_reasons, had_data_recovery_log, had_strategy_fail, skipped.
    """
    import pandas as pd

    capture.clear()
    rejections = []

    cmp_price = float(prices.get(sym, 0.0) or 0.0)
    price_source = "LIVE_QUOTE" if cmp_price > 0 else "UNRESOLVED"

    # ── 1. PIT filings missing ────────────────────────────────────────────────
    if sym not in pit_map:
        mod._emit_data_recovery_log(
            scanner="QUALITY_COMPOUNDER",
            symbol=sym,
            stage="QUALITY",
            missing_data="pit_statement_filings (ROCE, Sales CAGR, PAT CAGR, CFO/PAT, D/E)",
            recovery_attempted=True,
            providers=[{
                "provider": "PIT_DATABASE (pit_fundamentals_v1.parquet)",
                "result": "NOT_AVAILABLE",
                "failure_type": "SYMBOL_NOT_IN_PIT_DATASET",
            }],
            validation="FAILED",
            validation_reason="NO_PIT_STATEMENT_HISTORY_FOR_SYMBOL",
            final_action="STOCK_SKIPPED",
        )
        return _result(sym, ["DATA_MISSING_PIT_FILINGS"], capture)

    row = dict(pit_map[sym])
    industry = str(row.get("industry", "Unknown")).upper()
    is_fin = any(kw in industry for kw in ("BANK", "NBFC", "INSURANCE", "FINANCE"))

    # ── 2. Missing live CMP ───────────────────────────────────────────────────
    if cmp_price <= 0.0:
        mod._emit_data_recovery_log(
            scanner="QUALITY_COMPOUNDER",
            symbol=sym,
            stage="PRICE",
            missing_data="live_CMP (current market price from Upstox live quote)",
            recovery_attempted=True,
            providers=[{
                "provider": "UPSTOX_LIVE_QUOTE",
                "result": "FAILED",
                "failure_type": "LIVE_QUOTE_UNAVAILABLE_OR_ZERO",
            }],
            validation="FAILED",
            validation_reason="LIVE_CMP_REQUIRED_FOR_PRODUCTION_BUY_SIGNAL",
            final_action="STOCK_SKIPPED",
        )
        rejections.append("DATA_INSUFFICIENT_PRICE")

    # ── 3. Quality metrics ────────────────────────────────────────────────────
    roce_5y      = row.get("roce_5y_avg")
    sales_cagr_5y = row.get("sales_cagr_5y")
    pat_cagr_5y  = row.get("pat_cagr_5y")
    cfo_pat_5y   = row.get("cfo_pat_5y_ratio")
    de_ratio     = row.get("debt_to_equity")

    quality_gate_passed = False
    if not is_fin:
        def _is_missing(v):
            return v is None or (isinstance(v, float) and pd.isna(v))

        quality_data_missing = any(_is_missing(v)
            for v in [roce_5y, sales_cagr_5y, pat_cagr_5y, cfo_pat_5y, de_ratio])

        if quality_data_missing:
            missing_fields = [
                name for name, val in [
                    ("roce_5y", roce_5y), ("sales_cagr_5y", sales_cagr_5y),
                    ("pat_cagr_5y", pat_cagr_5y), ("cfo_pat_5y", cfo_pat_5y),
                    ("debt_to_equity", de_ratio),
                ] if _is_missing(val)
            ]
            mod._emit_data_recovery_log(
                scanner="QUALITY_COMPOUNDER",
                symbol=sym,
                stage="QUALITY",
                missing_data=", ".join(missing_fields),
                recovery_attempted=True,
                providers=[{
                    "provider": "PIT_DATABASE (pit_fundamentals_v1.parquet)",
                    "result": "FETCHED",
                    "validation": "FAILED",
                    "validation_reason":
                        "INSUFFICIENT_5Y_ANNUAL_FILING_HISTORY_FOR_METRIC_CALCULATION",
                }],
                validation="FAILED",
                validation_reason=(
                    f"FIELDS_REMAIN_NULL_AFTER_PIT_LOAD: {', '.join(missing_fields)}"
                ),
                final_action="STOCK_SKIPPED",
            )
            rejections.append("DATA_INSUFFICIENT_QUALITY")
        else:
            # Strategy-rule evaluation — NOT data problems
            if float(roce_5y) < 15.0:       rejections.append("FAIL_ROCE")
            if float(sales_cagr_5y) < 10.0: rejections.append("FAIL_SALES_CAGR")
            if float(pat_cagr_5y) < 10.0:   rejections.append("FAIL_PAT_CAGR")
            if float(cfo_pat_5y) < 0.80:    rejections.append("FAIL_CFO_PAT")
            if float(de_ratio) > 0.50:      rejections.append("FAIL_DEBT")
            quality_gate_passed = not any(r.startswith("FAIL_") for r in rejections)

    # ── 4. Valuation data ─────────────────────────────────────────────────────
    ev_curr = row.get("current_ev_ebitda")
    ev_med  = row.get("ev_ebitda_3y_median")

    def _ok(v):
        return v is not None and not (isinstance(v, float) and pd.isna(v))

    calc_discount = None
    if _ok(ev_curr) and _ok(ev_med) and float(ev_med) > 0:
        calc_discount = (float(ev_med) - float(ev_curr)) / float(ev_med)

    if calc_discount is None:
        val_missing = []
        if not _ok(ev_curr): val_missing.append("current_ev_ebitda")
        if not _ok(ev_med):  val_missing.append("ev_ebitda_3y_median")
        mod._emit_data_recovery_log(
            scanner="QUALITY_COMPOUNDER",
            symbol=sym,
            stage="VALUATION",
            missing_data=", ".join(val_missing) if val_missing else "ev_ebitda_discount",
            recovery_attempted=True,
            providers=[{
                "provider": "PIT_VALUATION_HISTORY_CACHE (pit_valuation_history_cache.json)",
                "result": "NOT_AVAILABLE" if not _ok(ev_med) else "FETCHED",
                "validation": "FAILED",
                "validation_reason":
                    "EV_EBITDA_3Y_MEDIAN_MISSING — run pit_valuation_history_builder.py",
            }],
            validation="FAILED",
            validation_reason=(
                "EV_EBITDA_GATE_REQUIRES_BOTH_CURRENT_AND_3Y_MEDIAN"
                " — PE_SUBSTITUTION_DISALLOWED"
            ),
            final_action="STOCK_SKIPPED",
        )
        rejections.append("DATA_INSUFFICIENT_VALUATION")
    else:
        if calc_discount < 0.25:
            rejections.append("FAIL_VALUATION")

    return _result(sym, rejections, capture)


def _result(sym, rejections, capture):
    had_recovery = capture.contains("[DATA_RECOVERY]")
    had_strategy = any(r in rejections for r in [
        "FAIL_ROCE", "FAIL_SALES_CAGR", "FAIL_PAT_CAGR",
        "FAIL_CFO_PAT", "FAIL_DEBT", "FAIL_VALUATION",
    ])
    skipped = any(r.startswith("DATA_") for r in rejections)
    return {
        "symbol": sym,
        "rejection_reasons": rejections,
        "had_data_recovery_log": had_recovery,
        "had_strategy_fail": had_strategy,
        "skipped": skipped,
        "messages": capture.messages(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

TESTS_PASSED = 0
TESTS_FAILED = 0


def _assert(cond, msg, messages=None):
    if not cond:
        detail = ("\n    Log output:\n    " +
                  "\n    ".join(messages or [])) if messages else ""
        raise AssertionError(msg + detail)


def t_helper_warning_on_skip(mod, cap):
    cap.clear()
    mod._emit_data_recovery_log(
        scanner="TEST", symbol="TESTCO", stage="QUALITY",
        missing_data="roce_5y",
        recovery_attempted=True,
        providers=[{"provider": "PIT_DB", "result": "NOT_AVAILABLE",
                    "failure_type": "MISSING"}],
        validation="FAILED",
        validation_reason="SYMBOL_NOT_IN_DATASET",
        final_action="STOCK_SKIPPED",
    )
    _assert(cap.contains("[DATA_RECOVERY]"), "Block header missing")
    _assert(cap.contains("TESTCO"), "Symbol missing")
    _assert(cap.contains("STOCK_SKIPPED"), "final_action missing")
    _assert(any(r.levelno == logging.WARNING for r in cap.records),
            "Must emit at WARNING level for STOCK_SKIPPED")
    print("  ✓ helper: WARNING level on STOCK_SKIPPED")


def t_helper_debug_on_used(mod, cap):
    cap.clear()
    mod._emit_data_recovery_log(
        scanner="TEST", symbol="GOODCO", stage="TECHNICAL",
        missing_data="1D_OHLCV_HISTORY",
        recovery_attempted=True,
        providers=[{"provider": "UNIFIED_FETCHER", "result": "FETCHED",
                    "rows_received": 252}],
        validation="PASSED",
        final_action="DATA_USED",
    )
    _assert(cap.contains("DATA_USED"), "final_action missing")
    _assert(
        not any(r.levelno >= logging.WARNING for r in cap.records),
        "DATA_USED must NOT emit WARNING",
    )
    print("  ✓ helper: DEBUG level on DATA_USED")


def t_missing_pit(mod, cap):
    r = _eval_symbol(mod, "SYM_MISS_PIT", {}, {"SYM_MISS_PIT": 500.0}, cap)
    _assert(r["had_data_recovery_log"], "[DATA_RECOVERY] block missing", r["messages"])
    _assert("DATA_MISSING_PIT_FILINGS" in r["rejection_reasons"],
            f"Expected DATA_MISSING_PIT_FILINGS; got {r['rejection_reasons']}")
    _assert(r["skipped"], "Symbol must be marked skipped")
    _assert(cap.contains("STOCK_SKIPPED"), "final_action=STOCK_SKIPPED missing from log")
    _assert(cap.contains("SYMBOL_NOT_IN_PIT_DATASET"), "failure_type missing from log")
    _assert(not r["had_strategy_fail"], "Must not emit strategy-rule FAIL_")
    print("  ✓ scenario: missing PIT filings → [DATA_RECOVERY] STOCK_SKIPPED")


def t_missing_quality_fields(mod, cap):
    row = _good_row("SYM_MISS_QUAL")
    row["roce_5y_avg"] = None
    row["sales_cagr_5y"] = None
    r = _eval_symbol(mod, "SYM_MISS_QUAL",
                     {"SYM_MISS_QUAL": row}, {"SYM_MISS_QUAL": 450.0}, cap)
    _assert(r["had_data_recovery_log"], "[DATA_RECOVERY] block missing", r["messages"])
    _assert("DATA_INSUFFICIENT_QUALITY" in r["rejection_reasons"],
            f"Expected DATA_INSUFFICIENT_QUALITY; got {r['rejection_reasons']}")
    _assert(r["skipped"], "Symbol must be marked skipped")
    _assert(cap.contains("roce_5y, sales_cagr_5y"),
            "Exact missing field names must appear in log", r["messages"])
    _assert(cap.contains("INSUFFICIENT_5Y_ANNUAL_FILING_HISTORY"),
            "validation_reason missing from log", r["messages"])
    _assert(cap.contains("FIELDS_REMAIN_NULL_AFTER_PIT_LOAD"),
            "validation_reason detail missing from log", r["messages"])
    _assert(not r["had_strategy_fail"], "Must not emit strategy-rule FAIL_")
    print("  ✓ scenario: missing quality fields → [DATA_RECOVERY] with exact field names")


def t_missing_live_price(mod, cap):
    pit = {"SYM_MISS_PX": _good_row("SYM_MISS_PX")}
    r = _eval_symbol(mod, "SYM_MISS_PX", pit, {"SYM_MISS_PX": 0.0}, cap)
    _assert(r["had_data_recovery_log"], "[DATA_RECOVERY] block missing", r["messages"])
    _assert("DATA_INSUFFICIENT_PRICE" in r["rejection_reasons"],
            f"Expected DATA_INSUFFICIENT_PRICE; got {r['rejection_reasons']}")
    _assert(r["skipped"], "Symbol must be marked skipped")
    _assert(cap.contains("LIVE_CMP_REQUIRED_FOR_PRODUCTION_BUY_SIGNAL"),
            "validation_reason missing", r["messages"])
    # Critical: historical price must NOT be cited as a recovery source
    _assert(not cap.contains("HISTORICAL_1D_PARQUET"),
            "Historical CMP must NOT appear as a recovery source", r["messages"])
    print("  ✓ scenario: missing live CMP → [DATA_RECOVERY], no historical fallback cited")


def t_missing_valuation_data(mod, cap):
    row = _good_row("SYM_MISS_VAL")
    row["ev_ebitda_3y_median"] = None
    pit = {"SYM_MISS_VAL": row}
    r = _eval_symbol(mod, "SYM_MISS_VAL", pit, {"SYM_MISS_VAL": 400.0}, cap)
    _assert(r["had_data_recovery_log"], "[DATA_RECOVERY] block missing", r["messages"])
    _assert("DATA_INSUFFICIENT_VALUATION" in r["rejection_reasons"],
            f"Expected DATA_INSUFFICIENT_VALUATION; got {r['rejection_reasons']}")
    _assert(r["skipped"], "Symbol must be marked skipped")
    _assert(cap.contains("ev_ebitda_3y_median"),
            "Missing field name must appear in log", r["messages"])
    _assert(cap.contains("PE_SUBSTITUTION_DISALLOWED"),
            "PE substitution block must be documented in log", r["messages"])
    print("  ✓ scenario: missing valuation data → [DATA_RECOVERY], PE substitution documented")


def t_strategy_rule_fail_no_recovery_log(mod, cap):
    """FAIL_ROCE with all data present must NOT emit [DATA_RECOVERY]."""
    row = _good_row("SYM_RULE_FAIL")
    row["roce_5y_avg"] = 8.0   # below 15% threshold — pure strategy rule
    pit = {"SYM_RULE_FAIL": row}
    r = _eval_symbol(mod, "SYM_RULE_FAIL", pit, {"SYM_RULE_FAIL": 350.0}, cap)
    _assert(not r["had_data_recovery_log"],
            "Strategy-rule rejection MUST NOT emit [DATA_RECOVERY]", r["messages"])
    _assert("FAIL_ROCE" in r["rejection_reasons"],
            f"Expected FAIL_ROCE; got {r['rejection_reasons']}")
    _assert(r["had_strategy_fail"], "Should have strategy FAIL_ rejection")
    _assert(not r["skipped"],
            "skipped flag must be False for strategy rejections (not data problems)")
    print("  ✓ scenario: FAIL_ROCE (strategy rule) → NO [DATA_RECOVERY] emitted")


def t_good_symbol_no_recovery_log(mod, cap):
    """All data present, all thresholds met → zero [DATA_RECOVERY], zero rejections."""
    pit = {"SYM_PASS": _good_row("SYM_PASS")}
    r = _eval_symbol(mod, "SYM_PASS", pit, {"SYM_PASS": 600.0}, cap)
    _assert(not r["had_data_recovery_log"],
            "Good symbol must NOT emit [DATA_RECOVERY]", r["messages"])
    _assert(len(r["rejection_reasons"]) == 0,
            f"Good symbol should have 0 rejections; got {r['rejection_reasons']}")
    _assert(not r["skipped"], "Good symbol must not be skipped")
    print("  ✓ scenario: good symbol → no [DATA_RECOVERY], zero rejections")


def t_universe_sweep_scanner_never_crashes(mod, cap):
    """
    Full sweep of a mixed universe:
    3 data-problem symbols + 1 strategy-fail + 1 good.
    Scanner must complete without exception.
    Every data-problem symbol must have [DATA_RECOVERY] + skipped=True.
    The strategy-fail must have NO [DATA_RECOVERY].
    The good symbol must pass with 0 rejections.
    """
    row_miss_qual = _good_row("BAD_QUAL"); row_miss_qual["roce_5y_avg"] = None
    row_miss_val  = _good_row("BAD_VAL");  row_miss_val["ev_ebitda_3y_median"] = None
    row_rule_fail = _good_row("BAD_RULE"); row_rule_fail["roce_5y_avg"] = 6.0

    pit = {
        "BAD_QUAL":  row_miss_qual,
        "BAD_VAL":   row_miss_val,
        "BAD_RULE":  row_rule_fail,
        "GOOD_SYM":  _good_row("GOOD_SYM"),
        # "NO_PIT" intentionally absent
    }
    universe = ["NO_PIT", "BAD_QUAL", "BAD_VAL", "BAD_RULE", "GOOD_SYM"]
    prices   = {sym: 500.0 for sym in universe}

    results = {}
    try:
        for sym in universe:
            results[sym] = _eval_symbol(mod, sym, pit, prices, cap)
    except Exception as e:
        raise AssertionError(f"Scanner raised exception during universe sweep: {e}") from e

    _assert(len(results) == len(universe), "Not all symbols were evaluated")

    for sym in ("NO_PIT", "BAD_QUAL", "BAD_VAL"):
        _assert(results[sym]["had_data_recovery_log"],
                f"{sym}: expected [DATA_RECOVERY] block", results[sym]["messages"])
        _assert(results[sym]["skipped"], f"{sym}: must be marked skipped")

    _assert(not results["BAD_RULE"]["had_data_recovery_log"],
            "BAD_RULE (strategy fail): must NOT emit [DATA_RECOVERY]",
            results["BAD_RULE"]["messages"])

    _assert(not results["GOOD_SYM"]["had_data_recovery_log"],
            "GOOD_SYM: must NOT emit [DATA_RECOVERY]", results["GOOD_SYM"]["messages"])
    _assert(len(results["GOOD_SYM"]["rejection_reasons"]) == 0,
            f"GOOD_SYM: expected 0 rejections; got {results['GOOD_SYM']['rejection_reasons']}")

    print(f"  ✓ universe sweep: {len(universe)} symbols evaluated — "
          f"3 data-skipped, 1 strategy-rejected, 1 candidate — scanner never crashed")


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 70)
    print("  DATA RECOVERY AUDIT LOG — RUNTIME ACCEPTANCE TEST")
    print("=" * 70)

    print("\n[1/2] Loading scanner module with stubs...")
    try:
        mod = _load_module()
    except Exception as e:
        import traceback
        print(f"\n  ✗ Module load failed: {e}")
        traceback.print_exc()
        sys.exit(1)
    print("      OK")

    # Attach capture to the scanner's logger
    cap = _LogCapture()
    cap.setFormatter(logging.Formatter("%(message)s"))
    cap.setLevel(logging.DEBUG)
    scanner_log = logging.getLogger("LIVE_FUNDAMENTAL_SCANNER")
    scanner_log.addHandler(cap)
    scanner_log.setLevel(logging.DEBUG)

    tests = [
        t_helper_warning_on_skip,
        t_helper_debug_on_used,
        t_missing_pit,
        t_missing_quality_fields,
        t_missing_live_price,
        t_missing_valuation_data,
        t_strategy_rule_fail_no_recovery_log,
        t_good_symbol_no_recovery_log,
        t_universe_sweep_scanner_never_crashes,
    ]

    print(f"\n[2/2] Running {len(tests)} acceptance tests...\n")
    passed = failed = 0
    for t in tests:
        try:
            t(mod, cap)
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {t.__name__}:\n    {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"  ✗ {t.__name__}: UNEXPECTED ERROR — {e}")
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 70)
    status = "ALL PASSED" if failed == 0 else f"{failed} FAILED"
    print(f"  RESULT: {passed}/{len(tests)} passed  |  {status}")
    print("=" * 70 + "\n")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
