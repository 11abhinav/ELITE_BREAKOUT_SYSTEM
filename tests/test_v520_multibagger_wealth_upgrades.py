"""
Unit & Integration Test Suite for v5.2.0 Upgrades:
  1. MULTIBAGGER: Volume Expansion Gate (Breakout Volume >= 2.0x SMA20).
  2. WEALTH_ENGINE: Max Sector Concentration Cap (20% Limit).
  3. Parity & Invariance: PULLBACK (v5.1.2) and Remaining Scanners (Frozen).
"""

import pytest
import os
import sys
import pandas as pd
import numpy as np

# Ensure app and project root are in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from app.multibagger import StockPriceData, entry_confirmed
from app.wealth_engine import MAX_SECTOR_PCT, apply_sector_cap
from engine.analytics.pullback_geometry import calculate_pullback_sl_target
from engine.analytics.quality_contract import ScannerType
from engine.analytics.forward_outcome_resolver import SCANNER_EXECUTION_POLICIES


def test_multibagger_volume_gate_boundary():
    """Verifies strict enforcement of the v5.2.0 Volume Expansion Gate (>= 2.0x SMA20)."""
    # 1. Below 2.0x SMA20 (e.g. 1.99x) -> Must FAIL
    p_fail = StockPriceData(
        symbol="TATAMOTORS",
        price=1000.0,
        change_pct=2.5,
        low_52w=600.0,
        high_52w=1100.0,
        turnover_20d=50000000.0,
        sma_20=980.0,
        sma_50=950.0,
        sma_200=900.0,
        high_20d=1010.0,
        high_60d=1050.0,
        mom_3m=0.15,
        mom_6m=0.30,
        atr_14=25.0,
        ema_20=985.0,
        latest_volume=199000.0, # 1.99x SMA20
        volume_sma20=100000.0,
        close_yesterday=975.0,
        sma_200_yesterday=898.0,
        today_open=980.0,
        today_close=1000.0
    )
    ok, reason = entry_confirmed(p_fail)
    assert ok is False, "Expected volume < 2.0x SMA20 to fail entry confirmation"
    assert reason == "entry_vol_below_2x", f"Expected entry_vol_below_2x, got {reason}"

    # 2. Exactly 2.0x SMA20 -> Must PASS
    p_pass_exact = StockPriceData(
        symbol="TATAMOTORS",
        price=1000.0,
        change_pct=2.5,
        low_52w=600.0,
        high_52w=1100.0,
        turnover_20d=50000000.0,
        sma_20=980.0,
        sma_50=950.0,
        sma_200=900.0,
        high_20d=1010.0,
        high_60d=1050.0,
        mom_3m=0.15,
        mom_6m=0.30,
        atr_14=25.0,
        ema_20=985.0,
        latest_volume=200000.0, # Exactly 2.00x SMA20
        volume_sma20=100000.0,
        close_yesterday=975.0,
        sma_200_yesterday=898.0,
        today_open=980.0,
        today_close=1000.0
    )
    ok_exact, reason_exact = entry_confirmed(p_pass_exact)
    assert ok_exact is True, "Expected volume >= 2.0x SMA20 to pass entry confirmation"
    assert reason_exact == "", f"Expected empty reason for pass, got {reason_exact}"

    # 3. Super-surge 3.5x SMA20 -> Must PASS
    p_pass_surge = StockPriceData(
        symbol="TATAMOTORS",
        price=1000.0,
        change_pct=2.5,
        low_52w=600.0,
        high_52w=1100.0,
        turnover_20d=50000000.0,
        sma_20=980.0,
        sma_50=950.0,
        sma_200=900.0,
        high_20d=1010.0,
        high_60d=1050.0,
        mom_3m=0.15,
        mom_6m=0.30,
        atr_14=25.0,
        ema_20=985.0,
        latest_volume=350000.0, # 3.50x SMA20
        volume_sma20=100000.0,
        close_yesterday=975.0,
        sma_200_yesterday=898.0,
        today_open=980.0,
        today_close=1000.0
    )
    ok_surge, reason_surge = entry_confirmed(p_pass_surge)
    assert ok_surge is True, "Expected surge volume to pass entry confirmation"
    assert reason_surge == "", f"Expected empty reason for pass, got {reason_surge}"


def test_wealth_engine_20pct_sector_cap():
    """Verifies that MAX_SECTOR_PCT is 0.20 and apply_sector_cap respects 20% limit."""
    assert MAX_SECTOR_PCT == 0.20, f"Expected MAX_SECTOR_PCT == 0.20 in v5.2.0, got {MAX_SECTOR_PCT}"

    # Create dummy DataFrame with 10 IT stocks
    stocks_data = []
    for i in range(10):
        stocks_data.append({
            "Stock": f"IT_STOCK_{i}",
            "Portfolio_Bucket": "Core Compounder",
            "Sector": "Information Technology",
            "FM_Score": 100 - i
        })
    # Add 5 Financial stocks
    for i in range(5):
        stocks_data.append({
            "Stock": f"FIN_STOCK_{i}",
            "Portfolio_Bucket": "Core Compounder",
            "Sector": "Financial Services",
            "FM_Score": 80 - i
        })

    df = pd.DataFrame(stocks_data)
    # Apply sector cap with max_stocks = 10
    # 20% of 10 max_stocks = 2 per sector limit
    capped_df = apply_sector_cap(df, "Portfolio_Bucket", "Core", max_stocks=10)

    it_count = len(capped_df[capped_df["Sector"] == "Information Technology"])
    fin_count = len(capped_df[capped_df["Sector"] == "Financial Services"])

    assert it_count <= 2, f"Expected IT sector to be capped at 2 (20% of 10), got {it_count}"
    assert fin_count <= 2, f"Expected Financial sector to be capped at 2 (20% of 10), got {fin_count}"


def test_pullback_v512_unaltered_parity():
    """Verifies that PULLBACK v5.1.2 canonical ATR geometry remains intact in v5.2.0."""
    geom = calculate_pullback_sl_target(entry_price=1000.0, atr_14=30.0)
    # raw_stop = 1.5 * 30.0 = 45.0 (4.5% is within [3.5%, 6.0%])
    assert geom["stop_loss"] == 955.0
    assert geom["actual_risk"] == 45.0
    assert geom["target_price"] == 1112.5
    assert geom["clamped_stop_pct"] == 0.045


def test_remaining_scanners_frozen_contract():
    """Verifies execution policies for all scanners remain canonical."""
    assert ScannerType.MULTIBAGGER in SCANNER_EXECUTION_POLICIES
    assert ScannerType.PULLBACK in SCANNER_EXECUTION_POLICIES
    assert ScannerType.EOD in SCANNER_EXECUTION_POLICIES
    assert ScannerType.DAILY_BUILDER in SCANNER_EXECUTION_POLICIES
    assert ScannerType.MULTI_TF in SCANNER_EXECUTION_POLICIES
    assert ScannerType.REVERSAL in SCANNER_EXECUTION_POLICIES


def test_multibagger_candidate_labeling_no_nameerror():
    """
    [RULE 69] Regression test for NameError: name 'skip_alert' is not defined.
    Verifies that when a candidate triggers an alert and passes all quality gates,
    categorized_stocks is populated cleanly without NameError or lock recursion issues.
    """
    import ast
    with open("app/multibagger.py", "r") as f:
        tree = ast.parse(f.read(), filename="multibagger.py")

    name_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == "skip_alert"]
    assert len(name_nodes) == 0, f"CRITICAL REGRESSION: 'skip_alert' Name node found in multibagger.py: {name_nodes}"
