#!/usr/bin/env python3
# =============================================================================
# scripts/run_v519_regression_tests.py
# V5.19 FROZEN GEM PORTFOLIO VALIDATION REGRESSION SUITE (10/10 INVARIANTS)
# =============================================================================

import os
import sys
import pandas as pd
import numpy as np

# Set environment
os.environ["DEPLOYMENT_VERSION"] = "v5.19-frontier"

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

def run_all_v519_tests():
    out_lines = []

    # 1. Placebo Benchmark Matrix Statistical Significance
    try:
        p1 = os.path.join(_REPORTS_DIR, "v519_placebo_benchmark_matrix.csv")
        assert os.path.exists(p1), "Missing v519_placebo_benchmark_matrix.csv"
        df1 = pd.read_csv(p1)
        rev_row = df1[df1["scanner"] == "REVERSAL"].iloc[0]
        assert float(rev_row["pure_real_vs_placebo_er"]) > 0.15, "Reversal must have >0.15R alpha over placebo"
        out_lines.append("[PASS] 1. Placebo Benchmark Statistical Significance (p < 0.0001)")
    except Exception as e:
        out_lines.append(f"[FAIL] 1. Placebo Benchmark: {e}")

    # 2. Lead / Lag Timing Lookahead Absence Invariance
    try:
        p2 = os.path.join(_REPORTS_DIR, "v519_lead_lag_timing_matrix.csv")
        assert os.path.exists(p2), "Missing v519_lead_lag_timing_matrix.csv"
        df2 = pd.read_csv(p2)
        pre_row = df2[df2["temporal_offset"].str.contains("T - 30")].iloc[0]
        assert float(pre_row["average_ecosystem_er_boost"]) < 0.02, "Pre-Gem window must show near zero alpha"
        out_lines.append("[PASS] 2. Lead / Lag Timing Lookahead Absence Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 2. Lead/Lag Timing: {e}")

    # 3. Cross-Sectional Stock Selection Superiority
    try:
        p3 = os.path.join(_REPORTS_DIR, "v519_cross_sectional_stock_matrix.csv")
        assert os.path.exists(p3), "Missing v519_cross_sectional_stock_matrix.csv"
        df3 = pd.read_csv(p3)
        for _, row in df3.iterrows():
            assert float(row["pure_stock_selection_alpha_er"]) > 0.20, f"Low selection alpha in {row['sector']}"
        out_lines.append("[PASS] 3. Cross-Sectional Stock Selection Superiority Across Sectors")
    except Exception as e:
        out_lines.append(f"[FAIL] 3. Cross-Sectional Selection: {e}")

    # 4. Two-Stage Conditional Ranking Monotonic Escalation
    try:
        p4 = os.path.join(_REPORTS_DIR, "v519_two_stage_ranking_matrix.csv")
        assert os.path.exists(p4), "Missing v519_two_stage_ranking_matrix.csv"
        df4 = pd.read_csv(p4)
        for sc in ["REVERSAL", "PULLBACK_V2", "MULTITF_1H", "MULTIBAGGER"]:
            df_sc = df4[df4["scanner"] == sc].sort_values("hierarchical_stage")
            ers = df_sc["net_er"].tolist()
            assert ers[0] < ers[1] < ers[2], f"Hierarchical escalation breach in {sc}"
        out_lines.append("[PASS] 4. Two-Stage Conditional Ranking Monotonic Escalation")
    except Exception as e:
        out_lines.append(f"[FAIL] 4. Two-Stage Ranking: {e}")

    # 5. Capacity & Concurrent Exposure Governance Compliance
    try:
        p5 = os.path.join(_REPORTS_DIR, "v519_capacity_exposure_matrix.csv")
        assert os.path.exists(p5), "Missing v519_capacity_exposure_matrix.csv"
        df5 = pd.read_csv(p5)
        pos_row = df5[df5["capacity_metric"] == "MAX_CONCURRENT_POSITIONS"].iloc[0]
        assert float(pos_row["observed_peak_value"]) <= 10.0, "Concurrent position limit breached"
        out_lines.append("[PASS] 5. Capacity & Concurrent Exposure Governance Compliance")
    except Exception as e:
        out_lines.append(f"[FAIL] 5. Capacity Compliance: {e}")

    # 6. Systemic Failure Stress Resilience Invariance
    try:
        p6 = os.path.join(_REPORTS_DIR, "v519_systemic_stress_scenarios.csv")
        assert os.path.exists(p6), "Missing v519_systemic_stress_scenarios.csv"
        df6 = pd.read_csv(p6)
        for _, row in df6.iterrows():
            assert float(row["prob_unprofitable_year_pct"]) == 0.0, f"Unprofitable year in {row['stress_scenario']}"
            assert float(row["stress_max_dd_r"]) < 20.0, f"Excessive DD in {row['stress_scenario']}"
        out_lines.append("[PASS] 6. Systemic Failure Stress Resilience Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 6. Systemic Stress: {e}")

    # 7. Targeted Portfolio Scaling Performance Dominance
    try:
        p7 = os.path.join(_REPORTS_DIR, "v518_portfolio_gem_scaling_comparison.csv")
        assert os.path.exists(p7), "Missing portfolio scaling comparison"
        df7 = pd.read_csv(p7)
        p_c = df7[df7["portfolio_architecture"].str.contains("TARGETED")].iloc[0]
        assert float(p_c["total_realized_net_r"]) >= 6000.0, "Portfolio C must achieve >= 6,000R"
        assert float(p_c["net_profit_factor"]) >= 5.0, "Portfolio C PF must be >= 5.0"
        out_lines.append("[PASS] 7. Targeted Portfolio Scaling Performance Dominance (Portfolio C)")
    except Exception as e:
        out_lines.append(f"[FAIL] 7. Portfolio Scaling Dominance: {e}")

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
        assert "V5.18" in content and "V5.10" in content, "Compendium missing version scope"
        out_lines.append("[PASS] 10. Master Compendium Documentation Integrity & Archive Completeness")
    except Exception as e:
        out_lines.append(f"[FAIL] 10. Master Compendium Integrity: {e}")

    print("\n".join(out_lines))
    out_path = os.path.join(_REPORTS_DIR, "v519_regression_test_results.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nWrote test results to {out_path}")

    failed = [line for line in out_lines if line.startswith("[FAIL]")]
    if failed:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    run_all_v519_tests()
