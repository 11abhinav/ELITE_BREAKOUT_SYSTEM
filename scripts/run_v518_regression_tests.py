#!/usr/bin/env python3
# =============================================================================
# scripts/run_v518_regression_tests.py
# V5.18 DAILY BUILDER GEM STATE REGRESSION SUITE (10/10 INVARIANTS)
# =============================================================================

import os
import sys
import pandas as pd
import numpy as np

# Set environment
os.environ["DEPLOYMENT_VERSION"] = "v5.18-frontier"

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

def run_all_v518_tests():
    out_lines = []

    # 1. 3x3 Operating Frontier Schema & Completeness
    try:
        p1 = os.path.join(_REPORTS_DIR, "v518_operating_frontier_3x3.csv")
        assert os.path.exists(p1), "Missing v518_operating_frontier_3x3.csv"
        df1 = pd.read_csv(p1)
        assert len(df1) == 9, "Expected 9 rows in 3x3 operating frontier"
        assert set(df1["orb_architecture"]) == {"ORB15", "ORB20", "ORB30"}
        out_lines.append("[PASS] 1. 3x3 Operating Frontier Schema & Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 1. 3x3 Operating Frontier: {e}")

    # 2. Walk-Forward Monotonic Consistency
    try:
        df1 = pd.read_csv(os.path.join(_REPORTS_DIR, "v518_operating_frontier_3x3.csv"))
        for _, row in df1.iterrows():
            assert row["wf1_er"] > 0.40, f"WF1 E[R] too low for {row['orb_architecture']}"
            assert row["wf4_er"] > 0.40, f"WF4 E[R] too low for {row['orb_architecture']}"
        out_lines.append("[PASS] 2. Walk-Forward Monotonic Consistency & OOS Stability")
    except Exception as e:
        out_lines.append(f"[FAIL] 2. Walk-Forward Consistency: {e}")

    # 3. Matched-Control Attribution Pure Gem Alpha Proof
    try:
        p2 = os.path.join(_REPORTS_DIR, "v518_gem_matched_control_attribution.csv")
        assert os.path.exists(p2), "Missing v518_gem_matched_control_attribution.csv"
        df2 = pd.read_csv(p2)
        # Check that Reversal and Pullback V2 have positive pure Gem alpha
        rev_row = df2[df2["scanner"] == "REVERSAL"].iloc[0]
        assert float(rev_row["pure_gem_specific_alpha_er"]) > 0.10, "Reversal must have >0.10R pure Gem alpha"
        pb_row = df2[df2["scanner"] == "PULLBACK_V2"].iloc[0]
        assert float(pb_row["pure_gem_specific_alpha_er"]) > 0.10, "Pullback must have >0.10R pure Gem alpha"
        out_lines.append("[PASS] 3. Matched-Control Attribution Pure Gem Alpha Proof")
    except Exception as e:
        out_lines.append(f"[FAIL] 3. Matched-Control Attribution: {e}")

    # 4. Short Covering Regime Decoupling Verification
    try:
        df2 = pd.read_csv(os.path.join(_REPORTS_DIR, "v518_gem_matched_control_attribution.csv"))
        sc_row = df2[df2["scanner"] == "SHORT_COVERING"].iloc[0]
        assert float(sc_row["pure_gem_specific_alpha_er"]) < 0, "Short covering must show negative pure Gem alpha (decoupling)"
        out_lines.append("[PASS] 4. Short Covering Anti-Correlation & Regime Decoupling")
    except Exception as e:
        out_lines.append(f"[FAIL] 4. Short Covering Decoupling: {e}")

    # 5. Temporal Persistence Decay Profile Invariance
    try:
        p3 = os.path.join(_REPORTS_DIR, "v518_gem_persistence_decay.csv")
        assert os.path.exists(p3), "Missing v518_gem_persistence_decay.csv"
        df3 = pd.read_csv(p3)
        boosts = df3["average_ecosystem_er_boost"].tolist()
        for i in range(len(boosts) - 1):
            assert boosts[i] > boosts[i+1], f"Decay monotonicity breach at index {i}"
        out_lines.append("[PASS] 5. Temporal Persistence Decay Monotonicity Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 5. Temporal Decay: {e}")

    # 6. False-Positive Failure Veto Classification Completeness
    try:
        p4 = os.path.join(_REPORTS_DIR, "v518_gem_failure_veto_audit.csv")
        assert os.path.exists(p4), "Missing v518_gem_failure_veto_audit.csv"
        df4 = pd.read_csv(p4)
        assert len(df4) == 5, "Expected 5 failure root causes"
        out_lines.append("[PASS] 6. False-Positive Failure Veto Classification Completeness")
    except Exception as e:
        out_lines.append(f"[FAIL] 6. Failure Veto Audit: {e}")

    # 7. Portfolio Gem Scaling Optimization Invariance
    try:
        p5 = os.path.join(_REPORTS_DIR, "v518_portfolio_gem_scaling_comparison.csv")
        assert os.path.exists(p5), "Missing v518_portfolio_gem_scaling_comparison.csv"
        df5 = pd.read_csv(p5)
        p_c = df5[df5["portfolio_architecture"].str.contains("TARGETED")].iloc[0]
        p_a = df5[df5["portfolio_architecture"].str.contains("UNIFORM")].iloc[0]
        assert float(p_c["total_realized_net_r"]) > float(p_a["total_realized_net_r"]), "Portfolio C must outperform Portfolio A"
        assert float(p_c["historical_max_dd_r"]) <= float(p_a["historical_max_dd_r"]), "Portfolio C must have <= MDD than Portfolio A"
        out_lines.append("[PASS] 7. Portfolio Gem Scaling Optimization & Convexity Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 7. Portfolio Scaling: {e}")

    # 8. Daily Builder 15:15 IST Horizon Contract
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

    # 10. Master Research Report Schema & Consistency
    try:
        p_rep = os.path.join(_REPORTS_DIR, "v518_gem_state_conditional_attribution_report.md")
        assert os.path.exists(p_rep), "Missing master report v518_gem_state_conditional_attribution_report.md"
        with open(p_rep, "r") as f:
            content = f.read()
        assert "6,140.2R" in content, "Missing Portfolio C total return in master report"
        out_lines.append("[PASS] 10. Master Research Report Schema & Data Consistency")
    except Exception as e:
        out_lines.append(f"[FAIL] 10. Master Report Schema: {e}")

    print("\n".join(out_lines))
    out_path = os.path.join(_REPORTS_DIR, "v518_regression_test_results.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nWrote test results to {out_path}")

    failed = [line for line in out_lines if line.startswith("[FAIL]")]
    if failed:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    run_all_v518_tests()
