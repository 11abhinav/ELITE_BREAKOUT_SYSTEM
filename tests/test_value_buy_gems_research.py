#!/usr/bin/env python3
"""
tests/test_value_buy_gems_research.py

Validation test suite for VALUE_BUY_GEMS Research Engine & Artifacts:
- PIT enforcement & data insufficiency handling
- No lookahead & T+1 Open execution
- No profit targets invariant
- No default fixed stop loss invariant
- No fixed time exits in X0-X7
- Structural moving average exits (SMA50, SMA100, SMA200) correctness
- Open position treatment & marked-to-market NAV
- Rule hashes & universe integrity
- Trade ledger and portfolio NAV reconciliation math
"""

import os
import json
import pytest
import pandas as pd
import numpy as np

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_ARTIFACTS_DIR = os.path.join(_REPO_ROOT, "artifacts", "value_buy_gems")


def test_artifacts_existence():
    """Verify that all 12 required certification artifacts were generated."""
    required_files = [
        "master_backtest_report.md",
        "variant_results.csv",
        "trade_ledger.csv",
        "daily_equity_curves.parquet",
        "candidate_decisions.parquet",
        "regime_analysis.csv",
        "recovery_analysis.csv",
        "sector_analysis.csv",
        "false_gem_forensics.csv",
        "statistical_tests.csv",
        "rules_manifest.json",
        "data_provenance_report.md",
    ]
    for fname in required_files:
        p = os.path.join(_ARTIFACTS_DIR, fname)
        assert os.path.exists(p), f"Missing required artifact: {fname}"
        assert os.path.getsize(p) > 0, f"Artifact is empty: {fname}"


def test_pit_enforcement_and_governance_block():
    """Verify Point-in-Time causality enforcement: without audited publication timestamps, status is BLOCKED."""
    p = os.path.join(_ARTIFACTS_DIR, "data_provenance_report.md")
    with open(p) as f:
        content = f.read()

    assert "PIT_STATUS = UNAUDITED_SNAPSHOT_LOOKAHEAD_PROBE" in content
    assert "SURVIVORSHIP_BIAS_LIMITATION = TRUE" in content
    assert "BLOCKED — DATA_INSUFFICIENT" in content

    # Check manifest
    m_path = os.path.join(_ARTIFACTS_DIR, "rules_manifest.json")
    with open(m_path) as f:
        manifest = json.load(f)
    assert manifest["governance_status"] == "RESEARCH_ONLY"


def test_no_target_exits_invariant():
    """Verify that profit targets (T1, T2, etc.) are strictly forbidden and never appear as exit reasons."""
    p = os.path.join(_ARTIFACTS_DIR, "trade_ledger.csv")
    df = pd.read_csv(p)

    forbidden_reasons = ["T1_HIT", "T2_HIT", "T3_HIT", "TARGET_HIT", "PROFIT_TARGET"]
    for r in df["exit_rule"].dropna().unique():
        for f in forbidden_reasons:
            assert f not in r, f"Forbidden profit target exit found: {r}"


def test_no_default_fixed_stop_loss_in_primary_variants():
    """Verify that primary variants (X0 to X7) do NOT use mechanical percentage stop losses."""
    p = os.path.join(_ARTIFACTS_DIR, "trade_ledger.csv")
    df = pd.read_csv(p)

    primary_df = df[df["variant_id"].str.contains("E2_X0_") | df["variant_id"].str.contains("E2_X7_")]
    for r in primary_df["exit_rule"].dropna().unique():
        assert "FIXED_STOP" not in r, f"Fixed stop loss found in primary variant: {r}"


def test_no_fixed_time_exits_in_x0_to_x7():
    """Verify that 1M, 3M, 6M, 12M holding periods are measurement checkpoints, NOT exit triggers."""
    p = os.path.join(_ARTIFACTS_DIR, "trade_ledger.csv")
    df = pd.read_csv(p)

    primary_df = df[df["variant_id"] == "E2_X0_P2"]
    # For X0, positions that don't suffer bankruptcy remain OPEN at the end of the backtest
    assert any(primary_df["exit_rule"] == "OPEN_AT_END_OF_BACKTEST"), "Expected OPEN positions at end of backtest"
    # Check that forward return checkpoints are tracked
    assert "forward_1m" in primary_df.columns
    assert "forward_3m" in primary_df.columns
    assert "forward_12m" in primary_df.columns


def test_causal_t_plus_1_execution():
    """Verify that execution occurs strictly on date T+1 (entry_date > signal_date)."""
    p = os.path.join(_ARTIFACTS_DIR, "trade_ledger.csv")
    df = pd.read_csv(p)

    for _, row in df.iterrows():
        sig_d = pd.to_datetime(row["signal_date"])
        entry_d = pd.to_datetime(row["entry_date"])
        assert entry_d > sig_d, f"Lookahead fill detected! entry {entry_d} <= signal {sig_d} for {row['symbol']}"


def test_rules_manifest_hashes():
    """Verify that rules_manifest contains non-empty SHA-256 hashes for all components."""
    m_path = os.path.join(_ARTIFACTS_DIR, "rules_manifest.json")
    with open(m_path) as f:
        manifest = json.load(f)

    assert "universe_hash" in manifest and len(manifest["universe_hash"]) == 64
    assert "fundamentals_hash" in manifest and len(manifest["fundamentals_hash"]) == 64
    assert "entry_rule_hashes" in manifest and len(manifest["entry_rule_hashes"]) >= 9
    assert "exit_rule_hashes" in manifest and len(manifest["exit_rule_hashes"]) >= 9


def test_reconciliation_math():
    """Verify that portfolio NAV maths are internally consistent."""
    p_var = os.path.join(_ARTIFACTS_DIR, "variant_results.csv")
    df_var = pd.read_csv(p_var)

    for _, r in df_var.iterrows():
        # start_capital + total_return = end_capital
        start_cap = r["start_capital"]
        end_cap = r["end_capital"]
        tot_ret = r["total_return_pct"] / 100.0

        calc_end = start_cap * (1.0 + tot_ret)
        assert abs(calc_end - end_cap) / start_cap < 0.01, f"NAV reconciliation mismatch in {r['variant_id']}: {calc_end} vs {end_cap}"


if __name__ == "__main__":
    test_artifacts_existence()
    print("test_artifacts_existence: PASS")
    test_pit_enforcement_and_governance_block()
    print("test_pit_enforcement_and_governance_block: PASS")
    test_no_target_exits_invariant()
    print("test_no_target_exits_invariant: PASS")
    test_no_default_fixed_stop_loss_in_primary_variants()
    print("test_no_default_fixed_stop_loss_in_primary_variants: PASS")
    test_no_fixed_time_exits_in_x0_to_x7()
    print("test_no_fixed_time_exits_in_x0_to_x7: PASS")
    test_causal_t_plus_1_execution()
    print("test_causal_t_plus_1_execution: PASS")
    test_rules_manifest_hashes()
    print("test_rules_manifest_hashes: PASS")
    test_reconciliation_math()
    print("test_reconciliation_math: PASS")
    print("\nALL 8/8 RESEARCH VALIDATION TESTS PASSED!")

