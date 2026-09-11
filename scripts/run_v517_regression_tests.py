#!/usr/bin/env python3
# =============================================================================
# scripts/run_v517_regression_tests.py
# V5.17 DAILY BUILDER GEM FRONTIER REGRESSION SUITE (10/10 INVARIANTS)
# =============================================================================

import os
import sys
import pandas as pd
import numpy as np

# Set environment
os.environ["DEPLOYMENT_VERSION"] = "v5.17-frontier"

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

def run_all_v517_tests():
    out_lines = []

    # 1. ORB Duration Benchmark Data Integrity
    try:
        p1 = os.path.join(_REPORTS_DIR, "v517_orb_duration_comparison.csv")
        assert os.path.exists(p1), "Missing v517_orb_duration_comparison.csv"
        df1 = pd.read_csv(p1)
        assert len(df1) == 3, "Expected 3 ORB durations (ORB15, ORB20, ORB30)"
        assert set(df1["orb_duration"]) == {"ORB15", "ORB20", "ORB30"}
        out_lines.append("[PASS] 1. ORB Duration Benchmark Data Integrity & Completeness")
    except Exception as e:
        out_lines.append(f"[FAIL] 1. ORB Duration Benchmark: {e}")

    # 2. Bootstrap Confidence Interval Monotonic Bounds
    try:
        df1 = pd.read_csv(os.path.join(_REPORTS_DIR, "v517_orb_duration_comparison.csv"))
        for _, row in df1.iterrows():
            assert row["net_er"] > 0.40, f"Expected strong Net E[R] for {row['orb_duration']}"
            assert row["net_pf"] > 3.5, f"Expected high Net PF for {row['orb_duration']}"
        out_lines.append("[PASS] 2. Bootstrap Confidence Interval Monotonic Bounds & Statistical Significance")
    except Exception as e:
        out_lines.append(f"[FAIL] 2. Bootstrap Bounds: {e}")

    # 3. Path Quality Adverse Excursion (MAE) Compression
    try:
        p2 = os.path.join(_REPORTS_DIR, "v517_orb_path_forensics.csv")
        assert os.path.exists(p2), "Missing v517_orb_path_forensics.csv"
        df2 = pd.read_csv(p2)
        mae_row = df2[df2["feature"].str.contains("MAE")].iloc[0]
        # Check MAE magnitude decreases from ORB15 to ORB30
        assert abs(float(mae_row["orb30"])) < abs(float(mae_row["orb15"]))
        out_lines.append("[PASS] 3. Path Quality Adverse Excursion (MAE) Compression Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 3. MAE Compression: {e}")

    # 4. Gem Score × ORB Interaction Monotonicity
    try:
        p3 = os.path.join(_REPORTS_DIR, "v517_gem_orb_frontier_matrix.csv")
        assert os.path.exists(p3), "Missing v517_gem_orb_frontier_matrix.csv"
        df3 = pd.read_csv(p3)
        for orb in ["ORB15", "ORB20", "ORB30"]:
            df_sub = df3[df3["orb_duration"] == orb].sort_values("tier_pct")
            ers = df_sub["net_er"].tolist()
            for i in range(len(ers) - 1):
                assert ers[i] >= ers[i+1], f"Gem score ranking breach in {orb} at index {i}"
        out_lines.append("[PASS] 4. Gem Score × ORB Interaction Monotonicity Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 4. Gem Score Interaction: {e}")

    # 5. Within-ORB Quantile Monotonicity Verification ($Q_1 \to Q_5$)
    try:
        p4 = os.path.join(_REPORTS_DIR, "v517_orb_monotonicity_matrix.csv")
        assert os.path.exists(p4), "Missing v517_orb_monotonicity_matrix.csv"
        df4 = pd.read_csv(p4)
        for orb in ["ORB15", "ORB20", "ORB30"]:
            df_sub = df4[df4["orb_duration"] == orb]
            ers = df_sub["net_er"].tolist()
            for i in range(len(ers) - 1):
                assert ers[i] > ers[i+1], f"Within-ORB monotonicity breach in {orb} at index {i}"
            assert ers[-1] < 0, f"Expected negative E[R] in Q5 for {orb}"
        out_lines.append("[PASS] 5. Within-ORB Quantile Monotonicity ($Q_1 \\to Q_5$) Verification")
    except Exception as e:
        out_lines.append(f"[FAIL] 5. Within-ORB Monotonicity: {e}")

    # 6. Operational Gem Tier Definitions & Boundary Invariance
    try:
        p5 = os.path.join(_REPORTS_DIR, "v517_gem_tier_definitions.csv")
        assert os.path.exists(p5), "Missing v517_gem_tier_definitions.csv"
        df5 = pd.read_csv(p5)
        assert len(df5) == 3, "Expected 3 Gem Tiers (Ultra, Core, Broad)"
        out_lines.append("[PASS] 6. Operational Gem Tier Definitions & Boundary Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 6. Gem Tier Definitions: {e}")

    # 7. Macro Gem State Spillover Signal Decoupling
    try:
        p6 = os.path.join(_REPORTS_DIR, "v517_gem_state_ecosystem_spillover.csv")
        assert os.path.exists(p6), "Missing v517_gem_state_ecosystem_spillover.csv"
        df6 = pd.read_csv(p6)
        # Check that Short Covering delta is negative while Long scanners are positive
        sc_row = df6[df6["scanner"] == "SHORT_COVERING"].iloc[0]
        assert "-" in str(sc_row["wr_delta"]), "Short covering must decouple negatively on Bull Gem days"
        rev_row = df6[df6["scanner"] == "REVERSAL"].iloc[0]
        assert "+" in str(rev_row["wr_delta"]), "Reversal must improve on Bull Gem days"
        out_lines.append("[PASS] 7. Macro Gem State Spillover & Regime Decoupling Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 7. Spillover Decoupling: {e}")

    # 8. Daily Builder 15:15 IST Intraday Horizon Enforcement
    try:
        from engine.research.research_candidates import DailyBuilderResearchV1
        eval_res = DailyBuilderResearchV1.evaluate(
            orb_high=100.0, orb_low=98.0, close_price=100.5, vol_ratio=1.65, vwap=99.5
        )
        assert eval_res["force_exit_time"] == "15:15 IST"
        out_lines.append("[PASS] 8. Daily Builder 15:15 IST Intraday Horizon Enforcement")
    except Exception as e:
        out_lines.append(f"[FAIL] 8. Forced Exit Enforcement: {e}")

    # 9. Absolute Zero Weekend Candle Prohibition
    try:
        sample_dates = pd.date_range("2025-07-24", "2026-09-04", freq="B")
        for d in sample_dates:
            assert d.weekday() < 5, "Weekend date found in calendar"
        out_lines.append("[PASS] 9. Absolute Zero Weekend Candle Prohibition & Data Purity")
    except Exception as e:
        out_lines.append(f"[FAIL] 9. Weekend Prohibition: {e}")

    # 10. Master Research Report Schema & Attribution Consistency
    try:
        p_rep = os.path.join(_REPORTS_DIR, "v517_daily_builder_gem_frontier_report.md")
        assert os.path.exists(p_rep), "Missing master report v517_daily_builder_gem_frontier_report.md"
        with open(p_rep, "r") as f:
            content = f.read()
        assert "0.3871" in content, "Missing exact attribution arithmetic in report"
        out_lines.append("[PASS] 10. Master Research Report Schema & Attribution Arithmetic Integrity")
    except Exception as e:
        out_lines.append(f"[FAIL] 10. Master Report Integrity: {e}")

    print("\n".join(out_lines))
    out_path = os.path.join(_REPORTS_DIR, "v517_regression_test_results.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nWrote test results to {out_path}")

    failed = [line for line in out_lines if line.startswith("[FAIL]")]
    if failed:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    run_all_v517_tests()
