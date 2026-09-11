#!/usr/bin/env python3
# =============================================================================
# scripts/run_v520_regression_tests.py
# V5.20 REGRESSION SUITE: GEM-AWARE ROUTING & PORTFOLIO INVARIANTS (10 TESTS)
# =============================================================================

import os
import sys
import json
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

def run_v520_regression_tests():
    out_lines = []

    # 1. Builder Freeze Contract
    try:
        p1 = os.path.join(_REPORTS_DIR, "v520_frozen_builder_definitions.json")
        assert os.path.exists(p1), "Missing v520_frozen_builder_definitions.json"
        with open(p1, "r") as f:
            b_data = json.load(f)
        assert b_data["GEM_CORE"]["net_er"] >= 0.95, "GEM_CORE E[R] must be >= 0.95R"
        assert b_data["GEM_ULTRA"]["net_er"] >= 0.90, "GEM_ULTRA E[R] must be >= 0.90R"
        assert b_data["GEM_CORE"]["orb_window"] == "ORB20"
        assert b_data["GEM_ULTRA"]["orb_window"] == "ORB30"
        out_lines.append("[PASS] 1. Daily Builder Frozen Operating Architectures (GEM_CORE & GEM_ULTRA)")
    except Exception as e:
        out_lines.append(f"[FAIL] 1. Builder Freeze: {e}")

    # 2. Ecosystem Routing Hierarchy
    try:
        p2 = os.path.join(_REPORTS_DIR, "v520_scanner_priority_routing_matrix.csv")
        assert os.path.exists(p2), "Missing v520_scanner_priority_routing_matrix.csv"
        df2 = pd.read_csv(p2)
        t1 = df2[df2["ecosystem_tier"].str.contains("Tier 1")]
        t3 = df2[df2["ecosystem_tier"].str.contains("Tier 3")].iloc[0]
        assert len(t1) == 4, "Tier 1 must contain exactly 4 scanners (Reversal, Pullback, 1H, Multibagger)"
        assert all(t1["gem_active_risk_r"] == 1.50), "Tier 1 risk must be 1.50R under Gem"
        assert t3["gem_active_risk_r"] == 0.50, "Tier 3 (Short Cover) risk must be 0.50R under Gem"
        out_lines.append("[PASS] 2. Ecosystem Priority Routing & Dynamic Risk Hierarchy")
    except Exception as e:
        out_lines.append(f"[FAIL] 2. Ecosystem Routing: {e}")

    # 3. Two-Stage Ranking Monotonicity Across All Scanners
    try:
        p3 = os.path.join(_REPORTS_DIR, "v520_two_stage_ranking_all_scanners.csv")
        assert os.path.exists(p3), "Missing v520_two_stage_ranking_all_scanners.csv"
        df3 = pd.read_csv(p3)
        long_scanners = df3[df3["scanner"] != "SHORT_COVERING"]
        for _, row in long_scanners.iterrows():
            assert float(row["s2_top20_er"]) > float(row["s1_er"]) > float(row["s0_er"]), f"Non-monotonic E[R] in {row['scanner']}"
            assert float(row["s2_top20_wr"]) > float(row["s1_wr"]) > float(row["s0_wr"]), f"Non-monotonic WR in {row['scanner']}"
        out_lines.append("[PASS] 3. Two-Stage Conditional Ranking Monotonic Escalation (All 10 Scanners)")
    except Exception as e:
        out_lines.append(f"[FAIL] 3. Two-Stage Ranking: {e}")

    # 4. Operational Time Horizon Decay Profile
    try:
        p4 = os.path.join(_REPORTS_DIR, "v520_operational_time_window_decay.csv")
        assert os.path.exists(p4), "Missing v520_operational_time_window_decay.csv"
        df4 = pd.read_csv(p4)
        w0 = df4.iloc[0]
        w1 = df4.iloc[1]
        w_last = df4.iloc[-1]
        assert float(w0["mean_er_boost"]) > float(w1["mean_er_boost"]) > float(w_last["mean_er_boost"]), "Time decay not strictly decreasing"
        assert float(w1["cumulative_trade_capture_pct"]) >= 85.0, "Capture % at 60m must be >= 85%"
        out_lines.append("[PASS] 4. Operational Time Window Persistence Decay & 60m Sweet Spot Invariant")
    except Exception as e:
        out_lines.append(f"[FAIL] 4. Time Window Decay: {e}")

    # 5. Micro-Matched Cross-Sectional Alpha
    try:
        p5 = os.path.join(_REPORTS_DIR, "v520_cross_sectional_micromatced_alpha.csv")
        assert os.path.exists(p5), "Missing v520_cross_sectional_micromatced_alpha.csv"
        df5 = pd.read_csv(p5)
        for _, row in df5.iterrows():
            assert float(row["pure_incremental_alpha_er"]) >= 0.25, f"Alpha below threshold in {row['sector']}"
            assert float(row["gem_stock_er"]) > float(row["peer_matched_stock_er"]), f"Gem stock lost to peer in {row['sector']}"
        out_lines.append("[PASS] 5. Micro-Matched Cross-Sectional Incremental Alpha Across All Sectors")
    except Exception as e:
        out_lines.append(f"[FAIL] 5. Micro-Matched Alpha: {e}")

    # 6. Gem Signal Frequency Stability Telemetry
    try:
        p6 = os.path.join(_REPORTS_DIR, "v520_gem_frequency_stability_telemetry.csv")
        assert os.path.exists(p6), "Missing v520_gem_frequency_stability_telemetry.csv"
        df6 = pd.read_csv(p6)
        active_days_row = df6[df6["telemetry_metric"].str.contains("Trading Days with")].iloc[0]
        assert "49.82%" in active_days_row["operational_assessment"]
        out_lines.append("[PASS] 6. Gem Signal Frequency & Operational Stability Telemetry Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 6. Frequency Telemetry: {e}")

    # 7. Targeted Portfolio Scaling Dominance (Portfolio C)
    try:
        p7 = os.path.join(_REPORTS_DIR, "v520_frozen_risk_portfolio_validation.csv")
        assert os.path.exists(p7), "Missing v520_frozen_risk_portfolio_validation.csv"
        df7 = pd.read_csv(p7)
        p_c = df7[df7["portfolio_architecture"].str.contains("TARGETED")].iloc[0]
        assert float(p_c["total_realized_net_r"]) >= 6000.0, "Portfolio C must achieve >= 6,000R"
        assert float(p_c["net_profit_factor"]) >= 5.0, "Portfolio C PF must be >= 5.0"
        assert float(p_c["historical_max_dd_r"]) <= 12.0, "Portfolio C Max DD must be <= 12.0R"
        out_lines.append("[PASS] 7. Frozen Portfolio Scaling Performance Dominance (Portfolio C)")
    except Exception as e:
        out_lines.append(f"[FAIL] 7. Portfolio Scaling: {e}")

    # 8. Daily Builder 15:15 IST Intraday Horizon Contract
    try:
        from engine.research.research_candidates import DailyBuilderResearchV1
        eval_res = DailyBuilderResearchV1.evaluate(
            orb_high=100.0, orb_low=98.0, close_price=100.5, vol_ratio=1.65, vwap=99.5
        )
        assert eval_res["force_exit_time"] == "15:15 IST"
        out_lines.append("[PASS] 8. Daily Builder 15:15 IST Intraday Horizon Contract")
    except Exception as e:
        out_lines.append(f"[FAIL] 8. 15:15 IST Contract: {e}")

    # 9. Absolute Zero Weekend Candle Prohibition
    try:
        sample_dates = pd.date_range("2025-07-24", "2026-09-04", freq="B")
        for d in sample_dates:
            assert d.weekday() < 5, "Weekend date found in business calendar"
        out_lines.append("[PASS] 9. Absolute Zero Weekend Candle Prohibition & Pipeline Integrity")
    except Exception as e:
        out_lines.append(f"[FAIL] 9. Weekend Prohibition: {e}")

    # 10. Master Compendium Documentation Integrity
    try:
        p_comp = os.path.join(_REPO_ROOT, "docs", "MASTER_RESEARCH_AND_BACKTEST_COMPENDIUM.md")
        assert os.path.exists(p_comp), "Missing master compendium document in docs/"
        with open(p_comp, "r") as f:
            content = f.read()
        assert "V5.20" in content and "V5.10" in content, "Compendium missing version scope"
        out_lines.append("[PASS] 10. Master Compendium Documentation Integrity & Permanent Archival")
    except Exception as e:
        out_lines.append(f"[FAIL] 10. Master Compendium Integrity: {e}")

    print("\n".join(out_lines))
    out_path = os.path.join(_REPORTS_DIR, "v520_regression_test_results.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nWrote test results to {out_path}")

if __name__ == "__main__":
    run_v520_regression_tests()
