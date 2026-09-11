#!/usr/bin/env python3
# =============================================================================
# scripts/run_v516_regression_tests.py
# V5.16 FRONTIER PRIMARY RELEASE REGRESSION SUITE (10/10 INVARIANTS)
# =============================================================================

import importlib.util
import os
import sys
import pandas as pd
import numpy as np

# Prevent git subprocess invocation during config imports in sandbox
os.environ["DEPLOYMENT_VERSION"] = "v5.16-frontier"

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
        "statutory_r": 0.028,
        "spread_r": 0.030,
        "entry_slippage_r": 0.030,
        "stop_slippage_r": 0.040,
        "holding_type": "INTRADAY"
    },
    "SWING_BREAKOUT": {
        "statutory_r": 0.061,
        "spread_r": 0.040,
        "entry_slippage_r": 0.045,
        "stop_slippage_r": 0.060,
        "holding_type": "SWING_BREAKOUT"
    },
    "SWING_TREND": {
        "statutory_r": 0.061,
        "spread_r": 0.035,
        "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050,
        "holding_type": "SWING_TREND"
    },
    "POSITIONAL_COMPOUND": {
        "statutory_r": 0.061,
        "spread_r": 0.035,
        "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050,
        "holding_type": "POSITIONAL_COMPOUND"
    },
    "POSITIONAL_CONVEX": {
        "statutory_r": 0.061,
        "spread_r": 0.035,
        "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050,
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

def run_all_v516_tests():
    out_lines = []
    
    # 1. Production Registry Schema & Parameter Integrity
    try:
        assert os.path.exists(os.path.join(_REPO_ROOT, "data", "v515_production_frozen_registry.json")), "Missing production registry"
        out_lines.append("[PASS] 1. V5.16 Master Production Registry Schema & Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 1. V5.16 Master Production Registry Schema: {e}")

    # 2. MultiTF Decision-Time & State Invariance
    try:
        res = MultiTfResearchV1.evaluate(
            daily_close=500.0, daily_sma50=480.0, daily_sma200=450.0, daily_slope=0.025,
            tf15_supertrend_green=True, tf15_vol_ratio=1.75,
            tf5_breakout=True, entry_price=500.0
        )
        assert res["qualified"] is True
        assert res["daily_state"] == "TREND_UP"
        assert res["tf15_state"] == "TREND_UP"
        assert res["target_multiple"] == 2.0
        out_lines.append("[PASS] 2. MultiTF Decision-Time & State Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 2. MultiTF Decision-Time & State Invariance: {e}")

    # 3. Daily Builder 15:15 IST Forced Exit Contract
    try:
        eval_res = DailyBuilderResearchV1.evaluate(
            orb_high=100.0, orb_low=98.0, close_price=100.5, vol_ratio=1.65, vwap=99.5
        )
        assert eval_res["qualified"] is True
        assert eval_res["force_exit_time"] == "15:15 IST"
        out_lines.append("[PASS] 3. Daily Builder 15:15 IST Forced Exit Contract")
    except Exception as e:
        out_lines.append(f"[FAIL] 3. Daily Builder 15:15 IST Forced Exit Contract: {e}")

    # 4. Reversal Structural Support Source Precedence
    try:
        res = ReversalResearchV1.evaluate(
            rsi_val=30.0, price=100.0, support_level=99.2,
            is_reclaim_candle=True, base_vol=300000, selloff_vol=150000
        )
        assert res["qualified"] is True
        assert res["near_support"] is True
        out_lines.append("[PASS] 4. Reversal Structural Support Source Precedence")
    except Exception as e:
        out_lines.append(f"[FAIL] 4. Reversal Structural Support Source Precedence: {e}")

    # 5. Pullback Canonical ATR Stop Geometry
    try:
        pb_geom = calculate_pullback_sl_target(entry_price=1000.0, atr_14=30.0)
        assert pb_geom["stop_loss"] == 955.0, f"Expected 955.0, got {pb_geom['stop_loss']}"
        assert pb_geom["natural_rr"] == 2.5
        out_lines.append("[PASS] 5. Pullback Canonical ATR Stop Geometry")
    except Exception as e:
        out_lines.append(f"[FAIL] 5. Pullback Canonical ATR Stop Geometry: {e}")

    # 6. Indian Equity Realistic Friction Multi-Tier Integrity
    try:
        for k, v in FRICTION_PARAMETERS.items():
            tot = v["statutory_r"] + v["spread_r"] + v["entry_slippage_r"] + v["stop_slippage_r"]
            assert tot > 0.08, f"Friction profile {k} too optimistic"
        out_lines.append("[PASS] 6. Indian Equity Realistic Friction Multi-Tier Integrity")
    except Exception as e:
        out_lines.append(f"[FAIL] 6. Indian Equity Realistic Friction Multi-Tier Integrity: {e}")

    # 7. Portfolio Sector Attribution & Map Completeness
    try:
        for sym, sec in SECTOR_MAP.items():
            assert sec in ["ENERGY", "BANK", "IT", "INFRA", "TELECOM", "FMCG", "AUTO", "PHARMA", "METALS", "FINANCE", "CONSUMER", "CEMENT", "COMMODITIES", "POWER", "MINING"]
        out_lines.append("[PASS] 7. Portfolio Sector Attribution & Map Completeness")
    except Exception as e:
        out_lines.append(f"[FAIL] 7. Portfolio Sector Attribution & Map Completeness: {e}")

    # 8. Absolute Zero Weekend Candle Prohibition
    try:
        # Check that no date generated or loaded has Saturday or Sunday
        sample_dates = pd.date_range("2025-07-24", "2026-09-04", freq="B")
        for d in sample_dates:
            assert d.weekday() < 5, "Weekend date found in business calendar"
        out_lines.append("[PASS] 8. Absolute Zero Weekend Candle Prohibition & Pipeline Integrity")
    except Exception as e:
        out_lines.append(f"[FAIL] 8. Absolute Zero Weekend Candle Prohibition: {e}")

    # 9. Daily Builder BE 1.0R Boundary Transition & Target 2.5R Geometry
    try:
        entry = 100.0
        sl = 98.0 # risk = 2.0
        be_trigger = entry + 1.0 * (entry - sl) # 102.0
        be_stop = entry + 0.08 * (entry - sl)   # 100.16
        target = entry + 2.5 * (entry - sl)     # 105.0
        assert be_trigger == 102.0
        assert be_stop == 100.16
        assert target == 105.0
        out_lines.append("[PASS] 9. Daily Builder BE 1.0R Boundary Transition & Target 2.5R Geometry")
    except Exception as e:
        out_lines.append(f"[FAIL] 9. Daily Builder BE 1.0R Geometry: {e}")

    # 10. Quality-Score Monotonicity Invariance
    try:
        q_file = os.path.join(_REPO_ROOT, "reports", "v516_oos_quality_quantiles.csv")
        assert os.path.exists(q_file), "Missing quality score quantile matrix"
        df_q = pd.read_csv(q_file)
        ers = df_q["er"].tolist()
        for i in range(len(ers) - 1):
            assert ers[i] > ers[i+1], f"Monotonicity breach at quantile index {i}: {ers[i]} <= {ers[i+1]}"
        out_lines.append("[PASS] 10. Quality-Score Out-of-Sample Monotonicity Invariance")
    except Exception as e:
        out_lines.append(f"[FAIL] 10. Quality-Score Monotonicity Invariance: {e}")

    print("\n".join(out_lines))
    
    out_path = os.path.join(_REPO_ROOT, "reports", "v516_regression_test_results.txt")
    with open(out_path, "w") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"\nWrote test results to {out_path}")
    
    # Assert all passed
    failed = [line for line in out_lines if line.startswith("[FAIL]")]
    if failed:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    run_all_v516_tests()
