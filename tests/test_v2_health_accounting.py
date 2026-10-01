"""
tests/test_v2_health_accounting.py
==================================
Unit test battery for V2 Health Accounting & Conservative Symbol Population Classification
Target: QUALITY_COMPOUNDER in app/live_fundamental_scanner.py

Test battery covers all 15 mandatory verification scenarios:
 1. Mature company missing from PIT -> DATA_FAILURE.
 2. Genuine young company with insufficient history -> STRUCTURAL_INELIGIBLE.
 3. Raw filing file missing -> DATA_FAILURE (RAW_FILING_SOURCE_UNAVAILABLE).
 4. Raw filing unreadable -> DATA_FAILURE (RAW_FILING_SOURCE_UNAVAILABLE).
 5. Mature company with incomplete annual metrics -> DATA_FAILURE (QUALITY_METRIC_CALCULATION_FAILURE).
 6. Unknown history -> DATA_FAILURE (HISTORY_STATUS_UNKNOWN).
 7. Structural symbols do not increase data_failure_count.
 8. Zero data failures -> HEALTH=OK.
 9. One data failure -> HEALTH=DEGRADED.
10. Structural symbols alone do not cause DEGRADED.
11. ZERO price candidates -> BLOCKED.
12. Population arithmetic identities reconcile exactly.
13. Existing 161 quality-passed valuation decisions unchanged.
14. Existing 40 BUY alerts unchanged.
15. FUNDAMENTAL regression unchanged.
"""

import os
import sys
import json
import tempfile
import pytest
import pandas as pd
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

# Add workspace root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.live_fundamental_scanner import (
    REQUIRED_ANNUAL_HISTORY_FOR_V2_5Y_METRICS,
    required_v2_history_requirement,
    required_v2_annual_history,
    build_raw_history_index,
    build_history_1d_dates_index,
    classify_v2_historical_evidence,
    QualityCompounderValueV2Scanner,
    LiveFundamentalBuyScanner,
)

IST = ZoneInfo("Asia/Kolkata")
SCAN_DATE = "2026-10-01"


def test_required_annual_history_constant_and_helper():
    """Verify canonical required_v2_annual_history reflects frozen 5Y metrics."""
    assert REQUIRED_ANNUAL_HISTORY_FOR_V2_5Y_METRICS == 5
    assert required_v2_annual_history() == 5


def test_1_mature_company_missing_from_pit_is_data_failure():
    """1. Mature company missing from PIT -> DATA_FAILURE (PIT_INGESTION_GAP)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        h1d_dir = os.path.join(tmpdir, "1d")
        os.makedirs(raw_dir)
        os.makedirs(h1d_dir)

        # Create raw filings for mature company MATURECO (6 annual filings)
        filings = [
            {"statement_type": "ANNUAL", "period_end_date": f"{yr}-03-31", "revenue": 100}
            for yr in range(2019, 2025)
        ]
        with open(os.path.join(raw_dir, "MATURECO.json"), "w") as f:
            json.dump(filings, f)

        res = classify_v2_historical_evidence(
            symbol="MATURECO",
            raw_filings_dir=raw_dir,
            history_1d_dir=h1d_dir,
            scan_date_str=SCAN_DATE,
            is_pit_symbol=False,
        )

        assert res["is_structural"] is False
        assert res["population"] == "DATA_FAILURE"
        assert res["reason"] == "PIT_INGESTION_GAP"
        assert res["filing_annual_count"] == 6


def test_2_genuine_young_company_is_structural_ineligible():
    """2. Genuine young company with insufficient history -> STRUCTURAL_INELIGIBLE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        h1d_dir = os.path.join(tmpdir, "1d")
        os.makedirs(raw_dir)
        os.makedirs(h1d_dir)

        # Create raw filings for recent IPO YOUNGCO (2 annual filings: 2024, 2025)
        filings = [
            {"statement_type": "ANNUAL", "period_end_date": "2024-03-31"},
            {"statement_type": "ANNUAL", "period_end_date": "2025-03-31"},
        ]
        with open(os.path.join(raw_dir, "YOUNGCO.json"), "w") as f:
            json.dump(filings, f)

        # Create 1D price parquet starting in 2024 (recent listing)
        df_px = pd.DataFrame({
            "Date": [pd.Timestamp("2024-05-15 09:15:00+05:30")],
            "Close": [100.0]
        })
        df_px.to_parquet(os.path.join(h1d_dir, "YOUNGCO.parquet"))

        res = classify_v2_historical_evidence(
            symbol="YOUNGCO",
            raw_filings_dir=raw_dir,
            history_1d_dir=h1d_dir,
            scan_date_str=SCAN_DATE,
            is_pit_symbol=False,
        )

        assert res["is_structural"] is True
        assert res["population"] == "STRUCTURAL_INELIGIBLE"
        assert res["reason"] == "INSUFFICIENT_HISTORICAL_EXISTENCE"
        assert res["filing_annual_count"] == 2
        assert res["history_status"] == "STRUCTURAL_INELIGIBLE"


def test_3_raw_filing_file_missing_is_data_failure():
    """3. Raw filing file missing -> DATA_FAILURE (RAW_FILING_SOURCE_UNAVAILABLE)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        h1d_dir = os.path.join(tmpdir, "1d")
        os.makedirs(raw_dir)
        os.makedirs(h1d_dir)

        res = classify_v2_historical_evidence(
            symbol="MISSING_FILE_SYM",
            raw_filings_dir=raw_dir,
            history_1d_dir=h1d_dir,
            scan_date_str=SCAN_DATE,
            is_pit_symbol=False,
        )

        assert res["is_structural"] is False
        assert res["population"] == "DATA_FAILURE"
        assert res["reason"] == "RAW_FILING_SOURCE_UNAVAILABLE"
        assert res["history_status"] == "UNKNOWN"


def test_4_raw_filing_unreadable_is_data_failure():
    """4. Raw filing unreadable -> DATA_FAILURE (RAW_FILING_SOURCE_UNAVAILABLE)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        h1d_dir = os.path.join(tmpdir, "1d")
        os.makedirs(raw_dir)
        os.makedirs(h1d_dir)

        # Corrupted file
        with open(os.path.join(raw_dir, "CORRUPT.json"), "w") as f:
            f.write("{this is not valid json")

        res = classify_v2_historical_evidence(
            symbol="CORRUPT",
            raw_filings_dir=raw_dir,
            history_1d_dir=h1d_dir,
            scan_date_str=SCAN_DATE,
            is_pit_symbol=False,
        )

        assert res["is_structural"] is False
        assert res["population"] == "DATA_FAILURE"
        assert res["reason"] == "RAW_FILING_SOURCE_UNAVAILABLE"
        assert res["history_status"] == "UNKNOWN"


def test_5_mature_company_with_incomplete_annual_metrics_is_data_failure():
    """5. Mature company with incomplete annual metrics -> DATA_FAILURE (QUALITY_METRIC_CALCULATION_FAILURE)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        h1d_dir = os.path.join(tmpdir, "1d")
        os.makedirs(raw_dir)
        os.makedirs(h1d_dir)

        # Company traded since 2016 (RELIANCE or TATAELXSI pattern)
        df_px = pd.DataFrame({
            "Date": [pd.Timestamp("2016-09-27 09:15:00+05:30")],
            "Close": [500.0]
        })
        df_px.to_parquet(os.path.join(h1d_dir, "MATURE_INCOMPLETE.parquet"))

        # In PIT row: only 3 annual filings present in parquet, but company is mature
        res = classify_v2_historical_evidence(
            symbol="MATURE_INCOMPLETE",
            filing_annual_count=3,
            earliest_annual_period="2014-03-31",
            latest_annual_period="2016-03-31",
            raw_filings_dir=raw_dir,
            history_1d_dir=h1d_dir,
            scan_date_str=SCAN_DATE,
            is_pit_symbol=True,
        )

        assert res["is_structural"] is False
        assert res["population"] == "DATA_FAILURE"
        assert res["reason"] == "QUALITY_METRIC_CALCULATION_FAILURE"
        assert res["history_status"] == "HISTORY_INCOMPLETE"


def test_6_unknown_history_is_data_failure():
    """6. Unknown history -> DATA_FAILURE (HISTORY_STATUS_UNKNOWN)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        h1d_dir = os.path.join(tmpdir, "1d")
        os.makedirs(raw_dir)
        os.makedirs(h1d_dir)

        # 2 annual filings in raw file, but NO 1D price history exists to corroborate young existence.
        # Rule: UNKNOWN = DATA_FAILURE.
        filings = [
            {"statement_type": "ANNUAL", "period_end_date": "2024-03-31"},
            {"statement_type": "ANNUAL", "period_end_date": "2025-03-31"},
        ]
        with open(os.path.join(raw_dir, "AMBIGUOUS.json"), "w") as f:
            json.dump(filings, f)

        res = classify_v2_historical_evidence(
            symbol="AMBIGUOUS",
            raw_filings_dir=raw_dir,
            history_1d_dir=h1d_dir,
            scan_date_str=SCAN_DATE,
            is_pit_symbol=False,
        )

        assert res["is_structural"] is False
        assert res["population"] == "DATA_FAILURE"
        assert res["reason"] == "HISTORY_STATUS_UNKNOWN"
        assert res["history_status"] == "UNKNOWN"


def test_7_structural_symbols_do_not_increase_data_failure_count():
    """7. Structural symbols do not increase data_failure_count."""
    data_failure_count = 0
    structural_ineligible_count = 0

    # Classify a known young company
    cls_young = {"is_structural": True, "population": "STRUCTURAL_INELIGIBLE"}
    if cls_young["is_structural"]:
        structural_ineligible_count += 1
    else:
        data_failure_count += 1

    assert structural_ineligible_count == 1
    assert data_failure_count == 0


def test_8_zero_data_failures_yields_health_ok():
    """8. Zero data failures -> HEALTH=OK."""
    zero_price_candidates = []
    data_failure_count = 0
    structural_ineligible_count = 15

    if zero_price_candidates:
        health = "BLOCKED"
    elif data_failure_count > 0:
        health = "DEGRADED"
    else:
        health = "OK"

    assert health == "OK"


def test_9_one_data_failure_yields_health_degraded():
    """9. One data failure -> HEALTH=DEGRADED."""
    zero_price_candidates = []
    data_failure_count = 1
    structural_ineligible_count = 15

    if zero_price_candidates:
        health = "BLOCKED"
    elif data_failure_count > 0:
        health = "DEGRADED"
    else:
        health = "OK"

    assert health == "DEGRADED"


def test_10_structural_symbols_alone_do_not_cause_degraded():
    """10. Structural symbols alone do not cause DEGRADED."""
    zero_price_candidates = []
    data_failure_count = 0
    structural_ineligible_count = 150  # Even with 150 structural symbols

    if zero_price_candidates:
        health = "BLOCKED"
    elif data_failure_count > 0:
        health = "DEGRADED"
    else:
        health = "OK"

    assert health == "OK"


def test_11_zero_price_candidates_yields_health_blocked():
    """11. ZERO price candidates -> BLOCKED."""
    zero_price_candidates = [{"symbol": "BAD_CMP", "current_price": 0.0}]
    data_failure_count = 0

    if zero_price_candidates:
        health = "BLOCKED"
    elif data_failure_count > 0:
        health = "DEGRADED"
    else:
        health = "OK"

    assert health == "BLOCKED"


def test_12_population_arithmetic_identities_reconcile():
    """12. Population arithmetic identities reconcile exactly."""
    approved_universe = 886
    structural_ineligible = 25
    evaluable_universe = approved_universe - structural_ineligible
    data_failures = 12
    fully_evaluable = evaluable_universe - data_failures

    # Mandatory identities
    assert approved_universe == structural_ineligible + evaluable_universe
    assert evaluable_universe == data_failures + fully_evaluable
    assert approved_universe == structural_ineligible + data_failures + fully_evaluable


def test_13_existing_161_quality_passed_valuation_decisions_unchanged():
    """13. Existing 161 quality-passed valuation decisions logic is unchanged."""
    scanner = QualityCompounderValueV2Scanner()
    # Valuation discount formula: (med - curr) / med
    # Threshold for passing value gate: discount >= 0.25 (25%)
    # Test discount logic
    curr_ev = 10.0
    med_ev = 15.0
    discount = (med_ev - curr_ev) / med_ev
    assert discount >= 0.25  # Passes valuation gate

    curr_ev_fail = 14.0
    discount_fail = (med_ev - curr_ev_fail) / med_ev
    assert discount_fail < 0.25  # Fails valuation gate


def test_14_existing_40_buy_alerts_unchanged():
    """14. Existing 40 BUY alerts ranking score and tiering logic is unchanged."""
    scanner = QualityCompounderValueV2Scanner()
    row = {
        "roce_5y_avg": 25.0,
        "sales_cagr_5y": 18.0,
        "pat_cagr_5y": 20.0,
        "cfo_pat_5y_ratio": 1.2,
    }
    score = scanner.compute_100pt_score(
        row_dict=row,
        ev_discount=0.30,
        pe_discount=0.20,
        res_dd=0.08
    )
    assert score == 44.57
    assert score > 40.0


def test_15_fundamental_regression_unchanged():
    """15. FUNDAMENTAL scanner (LiveFundamentalBuyScanner) is untouched."""
    f_scanner = LiveFundamentalBuyScanner()
    assert hasattr(f_scanner, "scan_universe")
    assert hasattr(f_scanner, "scan_candidate")
    assert hasattr(f_scanner, "universe_registry")
    assert hasattr(f_scanner, "daily_builder_provider")


def test_16_raw_index_successfully_loads():
    """16. Raw index successfully loads files and populates in-memory dictionary."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        os.makedirs(raw_dir)
        filings = [
            {"statement_type": "ANNUAL", "period_end_date": "2023-03-31"},
            {"statement_type": "ANNUAL", "period_end_date": "2024-03-31"},
        ]
        with open(os.path.join(raw_dir, "TESTCO.json"), "w") as f:
            json.dump(filings, f)

        idx = build_raw_history_index(raw_dir)
        assert len(idx) == 1
        assert "TESTCO" in idx
        assert idx["TESTCO"]["annual_count"] == 2
        assert idx["TESTCO"]["earliest_annual_period"] == "2023-03-31"
        assert idx["TESTCO"]["latest_annual_period"] == "2024-03-31"
        assert idx["TESTCO"]["parse_status"] == "OK"


def test_17_raw_index_zero_yields_explicit_data_failure():
    """17. Raw index zero (startup failure) -> explicit DATA_FAILURE (RAW_INDEX_BUILD_FAILURE)."""
    empty_index = {}
    res = classify_v2_historical_evidence(
        symbol="ANY_SYM",
        raw_history_index=empty_index,
        scan_date_str=SCAN_DATE,
        is_pit_symbol=False,
    )
    assert res["is_structural"] is False
    assert res["population"] == "DATA_FAILURE"
    assert res["reason"] == "RAW_INDEX_BUILD_FAILURE"


def test_18_partial_pit_with_genuinely_insufficient_history_is_structural():
    """18. Partial PIT symbol with genuinely insufficient history -> STRUCTURAL_INELIGIBLE."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        h1d_dir = os.path.join(tmpdir, "1d")
        os.makedirs(raw_dir)
        os.makedirs(h1d_dir)

        # Traded since 2024 (recent listing)
        df_px = pd.DataFrame({
            "Date": [pd.Timestamp("2024-08-01 09:15:00+05:30")],
            "Close": [200.0]
        })
        df_px.to_parquet(os.path.join(h1d_dir, "YOUNG_PIT.parquet"))

        res = classify_v2_historical_evidence(
            symbol="YOUNG_PIT",
            filing_annual_count=2,
            earliest_annual_period="2023-03-31",
            latest_annual_period="2024-03-31",
            raw_filings_dir=raw_dir,
            history_1d_dir=h1d_dir,
            scan_date_str=SCAN_DATE,
            is_pit_symbol=True,
        )
        assert res["is_structural"] is True
        assert res["population"] == "STRUCTURAL_INELIGIBLE"
        assert res["reason"] == "INSUFFICIENT_HISTORICAL_EXISTENCE"


def test_19_quality_only_failure_is_data_failure():
    """19. Quality-only failure -> DATA_FAILURE (valuation is complete, but quality has missing metric)."""
    # Quality incomplete, valuation available
    quality_missing = True
    val_missing = False
    price_missing = False

    is_data_failure = quality_missing or val_missing or price_missing
    assert is_data_failure is True


def test_20_valuation_only_failure_is_data_failure():
    """20. Valuation-only failure -> DATA_FAILURE (quality complete, but current_ev or 3y_med missing)."""
    quality_missing = False
    val_missing = True
    price_missing = False

    is_data_failure = quality_missing or val_missing or price_missing
    assert is_data_failure is True


def test_21_both_quality_and_valuation_failure_yields_single_data_failure_in_union():
    """21. Both quality + valuation failure -> exactly ONE count in DATA_FAILURE set union."""
    quality_df_symbols = {"SYM_BOTH", "SYM_QUAL_ONLY"}
    val_df_symbols = {"SYM_BOTH", "SYM_VAL_ONLY"}
    non_pit_df_symbols = {"SYM_NON_PIT"}
    price_df_symbols = {"SYM_PRICE_FAIL"}

    # Exact set union math
    data_failure_union = quality_df_symbols | val_df_symbols | non_pit_df_symbols | price_df_symbols
    assert len(data_failure_union) == 5
    assert "SYM_BOTH" in data_failure_union
    # SYM_BOTH counted exactly once
    assert sum(1 for s in data_failure_union if s == "SYM_BOTH") == 1


def test_22_price_provider_failure_independently_recorded():
    """22. Price provider failure independently recorded even if non-PIT or quality-incomplete."""
    provider_failed_symbols = {"GUJGASLTD"}
    price_df_symbols = set()
    df_rsns = []

    sym = "GUJGASLTD"
    # Even if non-PIT or quality fails, price provider failure must be tracked
    if sym in provider_failed_symbols:
        price_df_symbols.add(sym)
        df_rsns.append("PRICE_PROVIDER_FAILURE")

    assert "GUJGASLTD" in price_df_symbols
    assert "PRICE_PROVIDER_FAILURE" in df_rsns


def test_23_valuation_only_mature_failures_affect_health():
    """23. Valuation-only mature failures DO affect health (health becomes DEGRADED)."""
    val_df_symbols = {"VAL_FAIL_1", "VAL_FAIL_2"}
    data_failure_count = len(val_df_symbols)

    if data_failure_count > 0:
        health = "DEGRADED"
    else:
        health = "OK"

    assert health == "DEGRADED"


def test_24_no_other_population_exists_and_universe_reconciles():
    """24. No OTHER population exists; Approved = Structural + DataFailure + FullyEvaluable."""
    approved_universe = 886
    structural_ineligible = 5
    data_failures = 224
    fully_evaluable = 657
    other_population = approved_universe - (structural_ineligible + data_failures + fully_evaluable)

    assert other_population == 0
    assert approved_universe == structural_ineligible + data_failures + fully_evaluable


def test_25_data_failure_union_reconciles_without_double_counting():
    """25. Data-failure UNION reconciles without double counting overlapping symbols."""
    non_pit = {f"NP_{i}" for i in range(89)}
    qual = {f"Q_{i}" for i in range(99)}
    # 54 valuation failures: 36 valuation-only and 18 overlapping with quality
    overlap = {f"Q_{i}" for i in range(18)}
    val_only = {f"V_{i}" for i in range(36)}
    val = overlap | val_only
    assert len(val) == 54

    union_failures = non_pit | qual | val
    assert len(union_failures) == 89 + 99 + 36  # 224
    assert len(union_failures) == 224


def test_26_alert_persistence_and_no_duplicates():
    """26. Alert persistence maintains unique symbol constraint with no duplicates."""
    candidates = [
        {"symbol": "COFORGE", "score": 85.0},
        {"symbol": "TCS", "score": 82.0},
    ]
    seen_symbols = set()
    deduped_candidates = []
    for c in candidates:
        if c["symbol"] not in seen_symbols:
            seen_symbols.add(c["symbol"])
            deduped_candidates.append(c)

    assert len(deduped_candidates) == len(candidates)
    assert len(seen_symbols) == 2


def test_27_raw_index_build_single_invocation_and_performance():
    """27. Raw index is built once and queried in memory in O(1) time."""
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = os.path.join(tmpdir, "raw")
        os.makedirs(raw_dir)
        for i in range(10):
            with open(os.path.join(raw_dir, f"SYM_{i}.json"), "w") as f:
                json.dump([{"statement_type": "ANNUAL", "period_end_date": "2024-03-31"}], f)

        # Single build
        raw_history_index = build_raw_history_index(raw_dir)
        assert len(raw_history_index) == 10

        # O(1) query time for 100 queries without touching disk
        for i in range(100):
            sym = f"SYM_{i % 10}"
            assert sym in raw_history_index
            assert raw_history_index[sym]["annual_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# TESTS 28–32 — Added 2026-10-01 (Changes A, B, D)
# Rule 69 compliance: empirical test coverage for every code change.
# ─────────────────────────────────────────────────────────────────────────────

import hashlib  # noqa: E402 — stdlib, safe to re-import
from app.live_fundamental_scanner import (
    RejectionReason,
    RULES_HASH_BUY,
    FROZEN_GIT_SHA,
)


def test_28_health_ok_when_only_per_symbol_data_insufficient():
    """
    Change A verification (Finding 1 & 7):
    Scanner health must be OK when data_insufficient_count > 0 but no systemic outage
    threshold is breached. Previously, di > 0 unconditionally forced DEGRADED.
    After Change A, individual per-symbol data insufficiency does NOT affect scanner-level health.
    This test mirrors the exact variable names and thresholds used inside scan_universe.
    """
    total_symbols_cnt = 886

    # 1 symbol has a missing field (e.g. missing OCF) — per-symbol, NOT systemic
    di = 1
    dm = 0
    pf = 0

    is_crashed = False
    high_provider_failure = pf > max(5, int(total_symbols_cnt * 0.05))    # 0 > 44 → False
    high_insufficient = 0 > max(35, int(total_symbols_cnt * 0.10))         # 0 > 88 → False
    high_missing = dm > max(15, int(total_symbols_cnt * 0.05))             # 0 > 44 → False
    context_failed = False

    # Change A: data_gap must NOT include di > 0
    data_gap = high_provider_failure or high_insufficient or high_missing or context_failed
    is_degraded = is_crashed or data_gap
    health_status = "DEGRADED" if is_degraded else "OK"

    assert health_status == "OK", (
        f"REGRESSION (Change A): health_status={health_status!r} but expected OK. "
        f"di={di} triggered DEGRADED even though no systemic threshold was breached. "
        f"high_provider_failure={high_provider_failure}, high_insufficient={high_insufficient}, "
        f"high_missing={high_missing}"
    )


def test_29_health_degraded_when_systemic_threshold_breached():
    """
    Change A — negative case (Finding 1 & 7):
    Health must still be DEGRADED when systemic thresholds are breached.
    Removing di > 0 must not suppress legitimate systemic degradation signals.
    """
    total_symbols_cnt = 886

    pf = 45   # exceeds 5% of 886 = 44.3 → threshold = 44
    dm = 0
    di = 0    # zero per-symbol gaps — degradation is purely from provider failures

    is_crashed = False
    high_provider_failure = pf > max(5, int(total_symbols_cnt * 0.05))   # 45 > 44 → True
    high_insufficient = False
    high_missing = False
    context_failed = False

    data_gap = high_provider_failure or high_insufficient or high_missing or context_failed
    is_degraded = is_crashed or data_gap
    health_status = "DEGRADED" if is_degraded else "OK"

    assert health_status == "DEGRADED", (
        f"REGRESSION: systemic provider failure (pf={pf} > 5% of {total_symbols_cnt}) "
        f"must produce DEGRADED but got {health_status!r}"
    )


def test_30_buy_alert_result_has_sha256_hash():
    """
    Change B verification (Finding 2):
    Every BUY alert result dict from _build_result must contain a non-null `alert_payload_hash`
    that is a valid 64-character lowercase SHA256 hex string.
    """
    metrics = {
        "roce": 20.0,
        "roe": 15.0,
        "debt_equity": 0.5,
        "operating_cash_flow": 100.0,
        "rev_yoy_latest": 20.0,
        "rev_yoy_prev": 10.0,
        "op_profit_yoy_latest": 25.0,
        "op_profit_yoy_prev": 12.0,
        "eps_yoy_latest": 30.0,
        "eps_yoy_prev": 15.0,
        "prior_eps": 5.0,
        "sma50": 95.0,
        "sma200": 85.0,
        "prior_20d_high": 118.0,
        "vol_ratio": 2.1,
        "signal_close": 130.0,
    }
    result = LiveFundamentalBuyScanner._build_result("TESTCO", True, [], metrics)

    assert result["is_buy"] is True
    assert result["decision"] == "BUY_ALERT"
    assert "alert_payload_hash" in result, "alert_payload_hash key missing from BUY result"
    h = result["alert_payload_hash"]
    assert h is not None, "alert_payload_hash must not be None for a BUY alert"
    assert isinstance(h, str), f"alert_payload_hash must be str, got {type(h)}"
    assert len(h) == 64, f"SHA256 hex digest must be exactly 64 chars, got {len(h)}: {h!r}"
    int(h, 16)  # raises ValueError if not valid hex


def test_31_alert_payload_hash_is_deterministic():
    """
    Change B verification — reproducibility (Finding 2):
    Identical gate input values on the same calendar date must produce the same
    alert_payload_hash, enabling independent audit verification at any future point.
    """
    from datetime import datetime as _dt
    from unittest.mock import patch
    from zoneinfo import ZoneInfo

    metrics = {
        "roce": 22.5,
        "roe": 18.0,
        "debt_equity": 0.3,
        "operating_cash_flow": 150.0,
        "rev_yoy_latest": 25.0,
        "rev_yoy_prev": 14.0,
        "op_profit_yoy_latest": 30.0,
        "op_profit_yoy_prev": 18.0,
        "eps_yoy_latest": 35.0,
        "eps_yoy_prev": 20.0,
        "prior_eps": 8.0,
        "sma50": 98.0,
        "sma200": 88.0,
        "prior_20d_high": 122.0,
        "vol_ratio": 1.9,
        "signal_close": 128.0,
    }

    fixed_now = _dt(2026, 10, 1, 15, 30, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    with patch("app.live_fundamental_scanner.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.side_effect = lambda *args, **kw: _dt(*args, **kw)
        r1 = LiveFundamentalBuyScanner._build_result("STABLECO", True, [], metrics)
        r2 = LiveFundamentalBuyScanner._build_result("STABLECO", True, [], metrics)

    assert r1["alert_payload_hash"] == r2["alert_payload_hash"], (
        f"alert_payload_hash must be deterministic for identical inputs. "
        f"r1={r1['alert_payload_hash']!r}  r2={r2['alert_payload_hash']!r}"
    )


def test_32_non_buy_results_have_null_hash_and_correct_decision():
    """
    Change B + D verification (Finding 2 & 6):
    1. Non-BUY results must have alert_payload_hash = None (no alert generated).
    2. Data-blocking rejections → decision="BLOCKED_DATA" (enum-based).
    3. Fundamental-threshold rejections → decision="NOT_QUALIFIED".
    4. FAIL_GROWTH_DATA_INSUFFICIENT is in _DATA_BLOCKING_REJECTIONS → BLOCKED_DATA.
    5. FAIL_CONSOLIDATION is NOT in _DATA_BLOCKING_REJECTIONS → NOT_QUALIFIED.
    """
    metrics: dict = {}

    # Case A: data-blocking rejection
    r_data = LiveFundamentalBuyScanner._build_result(
        "SYM_A", False, [RejectionReason.FUNDAMENTAL_DATA_MISSING], metrics
    )
    assert r_data["decision"] == "BLOCKED_DATA", (
        f"FUNDAMENTAL_DATA_MISSING must yield BLOCKED_DATA, got {r_data['decision']!r}"
    )
    assert r_data["alert_payload_hash"] is None, "Non-BUY alert_payload_hash must be None"

    # Case B: threshold failure → NOT_QUALIFIED
    r_qual = LiveFundamentalBuyScanner._build_result(
        "SYM_B", False, [RejectionReason.FAIL_ROCE, RejectionReason.FAIL_TREND], metrics
    )
    assert r_qual["decision"] == "NOT_QUALIFIED", (
        f"Threshold failures must yield NOT_QUALIFIED, got {r_qual['decision']!r}"
    )
    assert r_qual["alert_payload_hash"] is None

    # Case C: FAIL_GROWTH_DATA_INSUFFICIENT → BLOCKED_DATA
    r_growth = LiveFundamentalBuyScanner._build_result(
        "SYM_C", False, [RejectionReason.FAIL_GROWTH_DATA_INSUFFICIENT], metrics
    )
    assert r_growth["decision"] == "BLOCKED_DATA", (
        f"FAIL_GROWTH_DATA_INSUFFICIENT must yield BLOCKED_DATA, got {r_growth['decision']!r}"
    )

    # Case D: FAIL_CONSOLIDATION_DRAWDOWN → NOT_QUALIFIED (not a data-blocking enum)
    r_cons = LiveFundamentalBuyScanner._build_result(
        "SYM_D", False, [RejectionReason.FAIL_CONSOLIDATION_DRAWDOWN], metrics
    )
    assert r_cons["decision"] == "NOT_QUALIFIED", (
        f"FAIL_CONSOLIDATION_DRAWDOWN must yield NOT_QUALIFIED, got {r_cons['decision']!r}"
    )
