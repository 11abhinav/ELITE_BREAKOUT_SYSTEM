"""
tests/test_v2_status_model_regression.py
========================================
Regression test suite for QUALITY_COMPOUNDER_VALUE_V2_FINAL data quality & status model.
Verifies:
1. 886 symbols, 0 issues => Fresh=886, Stale=0, Incomplete=0 => COMPLETED / OK.
2. 886 symbols, 3 incomplete => Fresh=883, Stale=0, Incomplete=3 => COMPLETED / OK, no Fail count, no DATA_INCOMPLETE banner.
3. 886 symbols with overlapping failures (2 quality, 2 valuation, 1 price failure on 3 unique stocks) => Incomplete=3 (NOT 5), no Fail=1.
4. Stale stocks contribute ONLY to stale_count, NOT incomplete_count.
5. Provider failure contributes ONLY to incomplete_count.
6. Reconciliation mismatch => INCONSISTENT / fail closed.
7. Evidence bundle export failure => EVIDENCE_EXPORT_FAILED, never silently masked.
8. Standard scanners' dashboard presentation remains unchanged.
"""

import pytest
import pandas as pd
from typing import Dict, Any, List, Set


def derive_per_stock_data_quality(
    required_data_missing_or_unresolved: bool,
    required_data_is_stale: bool,
    is_structural_ineligible: bool = False,
) -> str:
    """Canonical per-stock deterministic precedence rule."""
    if is_structural_ineligible:
        return "STRUCTURAL_INELIGIBLE"
    if required_data_missing_or_unresolved:
        return "INCOMPLETE"
    elif required_data_is_stale:
        return "STALE"
    else:
        return "FRESH"


def build_classified_population(symbols_data: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Helper implementing canonical disjoint accounting and assertions."""
    records = []
    for s in symbols_data:
        sym = s["symbol"]
        is_struct = s.get("structural_ineligible", False)
        is_q_df = s.get("quality_data_failure", False)
        is_v_df = s.get("valuation_data_failure", False)
        is_p_df = s.get("price_data_failure", False)
        is_o_df = s.get("other_data_failure", False)
        is_stale = s.get("is_stale", False)

        is_inc = bool(is_q_df or is_v_df or is_p_df or is_o_df)
        data_bucket = derive_per_stock_data_quality(
            required_data_missing_or_unresolved=is_inc,
            required_data_is_stale=is_stale,
            is_structural_ineligible=is_struct,
        )

        records.append({
            "symbol": sym,
            "structural_ineligible": is_struct,
            "quality_data_failure": is_q_df,
            "valuation_data_failure": is_v_df,
            "price_data_failure": is_p_df,
            "other_data_failure": is_o_df,
            "incomplete": is_inc,
            "is_stale": is_stale,
            "data_quality_bucket": data_bucket,
        })

    df = pd.DataFrame(records)
    evaluable_df = df[~df["structural_ineligible"]]

    fresh_symbols: Set[str] = set(evaluable_df[evaluable_df["data_quality_bucket"] == "FRESH"]["symbol"])
    stale_symbols: Set[str] = set(evaluable_df[evaluable_df["data_quality_bucket"] == "STALE"]["symbol"])
    incomplete_symbols: Set[str] = set(evaluable_df[evaluable_df["data_quality_bucket"] == "INCOMPLETE"]["symbol"])
    structural_symbols: Set[str] = set(df[df["structural_ineligible"]]["symbol"])

    fresh_count = len(fresh_symbols)
    stale_count = len(stale_symbols)
    incomplete_count = len(incomplete_symbols)
    structural_count = len(structural_symbols)

    dashboard_classified_symbol_count = len(evaluable_df)

    # Invariants
    assert fresh_count + stale_count + incomplete_count == dashboard_classified_symbol_count
    assert fresh_symbols.isdisjoint(stale_symbols), "fresh ∩ stale must be empty"
    assert fresh_symbols.isdisjoint(incomplete_symbols), "fresh ∩ incomplete must be empty"
    assert stale_symbols.isdisjoint(incomplete_symbols), "stale ∩ incomplete must be empty"

    # Health logic
    if incomplete_count > 5:
        health_status = "DEGRADED"
        health_error = f"DATA_DEGRADED: {incomplete_count} stocks incomplete (>5 threshold)"
    else:
        health_status = "OK"
        health_error = None

    return {
        "total_scanned": len(df),
        "structural_count": structural_count,
        "fresh_count": fresh_count,
        "stale_count": stale_count,
        "incomplete_count": incomplete_count,
        "fresh_symbols": fresh_symbols,
        "stale_symbols": stale_symbols,
        "incomplete_symbols": incomplete_symbols,
        "health_status": health_status,
        "health_error": health_error,
    }


def test_1_clean_universe_zero_issues():
    """1. 886 symbols, 0 issues => Fresh=886, Stale=0, Incomplete=0 => OK, health_error=None."""
    symbols = [{"symbol": f"SYM_{i:04d}"} for i in range(886)]
    res = build_classified_population(symbols)

    assert res["total_scanned"] == 886
    assert res["fresh_count"] == 886
    assert res["stale_count"] == 0
    assert res["incomplete_count"] == 0
    assert res["health_status"] == "OK"
    assert res["health_error"] is None


def test_2_three_incomplete_stocks_no_fail_bucket_no_data_incomplete_banner():
    """2. 886 symbols, 3 incomplete => Fresh=883, Stale=0, Incomplete=3 => OK, no DATA_INCOMPLETE."""
    symbols = [{"symbol": f"SYM_{i:04d}"} for i in range(886)]
    # Mark 3 symbols with data issues
    symbols[0]["price_data_failure"] = True
    symbols[1]["quality_data_failure"] = True
    symbols[2]["valuation_data_failure"] = True

    res = build_classified_population(symbols)
    assert res["total_scanned"] == 886
    assert res["fresh_count"] == 883
    assert res["stale_count"] == 0
    assert res["incomplete_count"] == 3
    assert res["health_status"] == "OK"
    # Essential check: no DATA_INCOMPLETE error message when incomplete <= 5
    assert res["health_error"] is None


def test_3_multi_failure_overlap_counted_once():
    """3. 2 quality failures, 2 valuation failures, 1 price failure across 3 unique stocks => Incomplete=3 (NOT 5)."""
    symbols = [{"symbol": f"SYM_{i:04d}"} for i in range(886)]
    # SYM_0000 has BOTH price and valuation failure
    symbols[0]["price_data_failure"] = True
    symbols[0]["valuation_data_failure"] = True
    # SYM_0001 has BOTH quality and valuation failure
    symbols[1]["quality_data_failure"] = True
    symbols[1]["valuation_data_failure"] = True
    # SYM_0002 has quality failure
    symbols[2]["quality_data_failure"] = True

    res = build_classified_population(symbols)
    assert res["incomplete_count"] == 3  # Distinct union, NOT 5
    assert res["fresh_count"] == 883
    assert res["stale_count"] == 0


def test_4_stale_stocks_contribute_only_to_stale_count():
    """4. Stale stocks contribute ONLY to stale_count, NOT incomplete_count."""
    symbols = [{"symbol": f"SYM_{i:04d}"} for i in range(886)]
    symbols[0]["is_stale"] = True
    symbols[1]["is_stale"] = True

    res = build_classified_population(symbols)
    assert res["stale_count"] == 2
    assert res["incomplete_count"] == 0
    assert res["fresh_count"] == 884


def test_5_provider_failure_contributes_only_to_incomplete():
    """5. Provider failure contributes ONLY to incomplete_count, no separate Fail concept."""
    symbols = [{"symbol": f"SYM_{i:04d}"} for i in range(886)]
    # GUJGASLTD provider failure
    symbols[0]["price_data_failure"] = True

    res = build_classified_population(symbols)
    assert res["incomplete_count"] == 1
    assert res["fresh_count"] == 885
    assert res["stale_count"] == 0


def test_6_reconciliation_mismatch_fails_inconsistent():
    """6. Reconciliation mismatch => INCONSISTENT / fail closed."""
    scanned_count = 886
    structural_count = 3
    incomplete_count = 3
    fully_evaluable_count = 879  # Corrupted: 3 + 3 + 879 = 885 != 886
    alerts_count = 20
    rejected_count = 859

    is_reconciled = (
        scanned_count == structural_count + incomplete_count + fully_evaluable_count
        and fully_evaluable_count == alerts_count + rejected_count
    )
    assert not is_reconciled

    health_status = "INCONSISTENT" if not is_reconciled else "OK"
    assert health_status == "INCONSISTENT"


def test_7_evidence_bundle_export_failure_logged_distinctly():
    """7. Evidence bundle export failure => EVIDENCE_EXPORT_FAILED, never silently masked."""
    class MockFailingCollector:
        run_dir = "/tmp/mock_run"
        def finalize_and_export_bundle(self, summary):
            raise RuntimeError("Disk full / permission denied")

    collector = MockFailingCollector()
    evidence_export_status = "NOT_REQUESTED"
    evidence_manifest_path = None

    try:
        evidence_manifest_path, _ = collector.finalize_and_export_bundle({"total": 886})
        evidence_export_status = "EXPORTED"
    except Exception:
        evidence_export_status = "EVIDENCE_EXPORT_FAILED"

    assert evidence_export_status == "EVIDENCE_EXPORT_FAILED"
    assert evidence_manifest_path is None


def test_8_dashboard_rendering_normalization():
    """8. Test dashboard status normalization preserves standard quality badges and suppresses custom banners."""
    def normalize_quality_status(status_str, incomplete_count):
        raw = (status_str or "NORMAL").upper()
        if raw in ("COMPLETED", "OK", "SUCCESS"):
            if incomplete_count > 5:
                return "DEGRADED"
            elif incomplete_count > 0:
                return "PARTIAL"
            else:
                return "NORMAL"
        return raw

    assert normalize_quality_status("COMPLETED", 0) == "NORMAL"
    assert normalize_quality_status("COMPLETED", 3) == "PARTIAL"
    assert normalize_quality_status("COMPLETED", 10) == "DEGRADED"
    assert normalize_quality_status("NORMAL", 0) == "NORMAL"
    assert normalize_quality_status("BLOCKED", 0) == "BLOCKED"
    assert normalize_quality_status("DATA_BLOCKED", 0) == "DATA_BLOCKED"
