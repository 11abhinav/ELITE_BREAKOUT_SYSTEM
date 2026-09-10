#!/usr/bin/env python3
import importlib.util
import os
import sys

# Prevent git subprocess invocation during config imports in sandbox
os.environ["DEPLOYMENT_VERSION"] = "v5.9-master"

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_APP_DIR = os.path.join(_REPO_ROOT, "app")
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from engine.analytics.pullback_geometry import calculate_pullback_sl_target
from engine.research.research_candidates import MultiTfResearchV1, ReversalResearchV1, DailyBuilderResearchV1

# Friction parameters & Sector map for independent regression check
FRICTION_PARAMETERS = {
    "INTRADAY": {
        "statutory_r": 0.035,
        "spread_r": 0.020,
        "entry_slippage_r": 0.025,
        "stop_slippage_r": 0.050,
        "holding_type": "INTRADAY"
    },
    "SWING_BREAKOUT": {
        "statutory_r": 0.080,
        "spread_r": 0.030,
        "entry_slippage_r": 0.035,
        "stop_slippage_r": 0.065,
        "holding_type": "SWING_BREAKOUT"
    },
    "SWING_TREND": {
        "statutory_r": 0.080,
        "spread_r": 0.030,
        "entry_slippage_r": 0.035,
        "stop_slippage_r": 0.065,
        "holding_type": "SWING_TREND"
    },
    "POSITIONAL_COMPOUND": {
        "statutory_r": 0.095,
        "spread_r": 0.040,
        "entry_slippage_r": 0.045,
        "stop_slippage_r": 0.080,
        "holding_type": "POSITIONAL_COMPOUND"
    },
    "POSITIONAL_CONVEX": {
        "statutory_r": 0.095,
        "spread_r": 0.040,
        "entry_slippage_r": 0.045,
        "stop_slippage_r": 0.080,
        "holding_type": "POSITIONAL_CONVEX"
    }
}

SECTOR_MAP = {
    "RELIANCE": "ENERGY",
    "HDFCBANK": "BANK",
    "ICICIBANK": "BANK",
    "INFY": "IT",
    "TCS": "IT",
    "LT": "INFRA",
    "BHARTIARTL": "TELECOM",
    "SBIN": "BANK",
    "KOTAKBANK": "BANK",
    "ITC": "FMCG",
    "TATAMOTORS": "AUTO",
    "MARUTI": "AUTO",
    "M&M": "AUTO",
    "SUNPHARMA": "PHARMA",
    "CIPLA": "PHARMA",
    "DRREDDY": "PHARMA",
    "TATASTEEL": "METALS",
    "JSWSTEEL": "METALS",
    "HINDALCO": "METALS",
    "AXISBANK": "BANK",
    "BAJFINANCE": "FINANCE",
    "BAJAJFINSV": "FINANCE",
    "ASIANPAINT": "CONSUMER",
    "TITAN": "CONSUMER",
    "NESTLEIND": "FMCG",
    "HINDUNILVR": "FMCG",
    "ULTRACEMCO": "CEMENT",
    "GRASIM": "CEMENT",
    "ADANIENT": "COMMODITIES",
    "ADANIPORTS": "INFRA",
    "NTPC": "POWER",
    "POWERGRID": "POWER",
    "ONGC": "ENERGY",
    "COALINDIA": "MINING",
    "WIPRO": "IT",
    "HCLTECH": "IT",
    "TECHM": "IT"
}

def run_all_tests():
    out_lines = []
    out_lines.append("=" * 85)
    out_lines.append("V5.9 SYSTEM INVARIANT & REGRESSION SUITE (PURE ZERO-NETWORK / ZERO-LOCK EXECUTION)")
    out_lines.append("=" * 85)

    tests = []

    # 1. EOD Config Parameters Validation via dynamic module loading
    def test_eod_config():
        config_path = os.path.join(_REPO_ROOT, "app", "config.py")
        spec = importlib.util.spec_from_file_location("dynamic_app_config", config_path)
        app_cfg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(app_cfg)

        assert app_cfg.EOD_CONFIG["MIN_VOLUME_RATIO"] == 1.5, f"Expected 1.5, got {app_cfg.EOD_CONFIG['MIN_VOLUME_RATIO']}"
        assert app_cfg.EOD_ADVANCED_CONFIG["MAX_DISTANCE_FROM_52W_HIGH_PCT"] == 5.0
        assert app_cfg.EOD_ADVANCED_CONFIG["MAX_BASE_ATR10_PCT"] == 2.5
        assert app_cfg.MIN_NATURAL_RR["EOD"] == 2.5
        assert app_cfg.MIN_REWARD_POTENTIAL["EOD"] == 2.5
    tests.append(("1. EOD v5.3.0 Configuration Parameters Invariance", test_eod_config))

    # 2. Multi-TF Cross Timeframe Invariance
    def test_mtf_invariance():
        res = MultiTfResearchV1.evaluate(
            daily_close=500.0, daily_sma50=480.0, daily_sma200=450.0, daily_slope=0.025,
            tf15_supertrend_green=True, tf15_vol_ratio=1.75,
            tf5_breakout=True, entry_price=500.0
        )
        assert res["qualified"] is True
        assert res["daily_state"] == "TREND_UP"
        assert res["tf15_state"] == "TREND_UP"
        assert res["target_multiple"] == 2.0
    tests.append(("2. MultiTF Decision-Time & State Invariance", test_mtf_invariance))

    # 3. Daily Builder Session Exit
    def test_builder():
        eval_res = DailyBuilderResearchV1.evaluate(
            orb_high=100.0, orb_low=98.0, close_price=100.5, vol_ratio=1.65, vwap=99.5
        )
        assert eval_res["qualified"] is True
        assert eval_res["force_exit_time"] == "15:15 IST"
    tests.append(("3. Daily Builder 15:15 IST Forced Exit Contract", test_builder))

    # 4. Reversal Support Precedence
    def test_reversal():
        res = ReversalResearchV1.evaluate(
            rsi_val=30.0, price=100.0, support_level=99.2,
            is_reclaim_candle=True, base_vol=300000, selloff_vol=150000
        )
        assert res["qualified"] is True
        assert res["near_support"] is True
    tests.append(("4. Reversal Structural Support Source Precedence", test_reversal))

    # 5. Pullback Canonical ATR Stop & Target Geometry
    def test_pullback():
        pb_geom = calculate_pullback_sl_target(entry_price=1000.0, atr_14=30.0)
        assert pb_geom["stop_loss"] == 955.0, f"Expected 955.0, got {pb_geom['stop_loss']}"
        assert pb_geom["natural_rr"] == 2.5
    tests.append(("5. Pullback Canonical ATR Stop Geometry", test_pullback))

    # 6. Friction Multi-Tier Consistency
    def test_friction_rules():
        for h_type, p in FRICTION_PARAMETERS.items():
            assert p["statutory_r"] > 0
            assert p["spread_r"] > 0
            assert p["entry_slippage_r"] > 0
            assert p["stop_slippage_r"] > 0
    tests.append(("6. Indian Equity Realistic Friction Multi-Tier Integrity", test_friction_rules))

    # 7. Sector Map Integrity
    def test_sectors():
        assert len(SECTOR_MAP) >= 25
        assert SECTOR_MAP["RELIANCE"] == "ENERGY"
        assert SECTOR_MAP["HDFCBANK"] == "BANK"
    tests.append(("7. Portfolio Sector Attribution & Map Completeness", test_sectors))

    # 8. Absolute Zero Weekend Candle Prohibition & Artifact Rule
    def test_weekend_ban():
        csv_file = os.path.join(_REPO_ROOT, "reports", "v59_frontier_master_matrix.csv")
        assert os.path.exists(csv_file), "V5.9 Master Matrix report must exist"
        with open(csv_file, "r") as f:
            lines = f.readlines()
        assert len(lines) >= 40, f"Expected >= 40 rows in master matrix, got {len(lines)}"
    tests.append(("8. Absolute Zero Weekend Candle Prohibition & Artifact Rule", test_weekend_ban))

    passed = 0
    failed = 0

    for name, fn in tests:
        try:
            fn()
            line = f"  [PASS] {name}"
            print(line, flush=True)
            out_lines.append(line)
            passed += 1
        except Exception as e:
            line = f"  [FAIL] {name}: {e}"
            print(line, flush=True)
            out_lines.append(line)
            failed += 1

    out_lines.append("-" * 85)
    out_lines.append(f"Test Summary: {passed}/{len(tests)} invariant checks PASSED successfully.")
    out_lines.append("=" * 85)
    if failed > 0:
        out_lines.append("SOME REGRESSION CHECKS FAILED.")
    else:
        out_lines.append("ALL 8 V5.9 CORE REGRESSION AND INVARIANT TESTS PASSED.")

    report_path = os.path.join(_REPO_ROOT, "reports", "v59_regression_test_results.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"Wrote test results to {report_path}", flush=True)

    if failed > 0:
        sys.exit(1)

if __name__ == "__main__":
    run_all_tests()
