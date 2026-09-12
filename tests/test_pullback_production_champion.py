"""
Unit Tests for Certified Pullback Production Champion
=====================================================
Validates:
1. PULLBACK_CHALL_D_ADAPTIVE is registered as CHAMPION in champion_challenger_registry.
2. Canonical calculate_pullback_sl_target respects volatility-adaptive multiplier and clamping bounds [3.5%, 8.0%].
3. R:R geometry satisfies minimum 2.5R target requirement.
"""

import pytest
from engine.analytics.pullback_geometry import calculate_pullback_sl_target
from app.champion_challenger_registry import registry, VariantStatus, ScannerFamily

def test_pullback_champion_registration():
    champion = registry.get_champion(ScannerFamily.PULLBACK)
    assert champion is not None, "Champion for PULLBACK must exist"
    assert champion.variant_id == "PULLBACK_CHALL_D_ADAPTIVE", f"Expected PULLBACK_CHALL_D_ADAPTIVE, got {champion.variant_id}"
    assert champion.status == VariantStatus.CHAMPION

def test_pullback_geometry_low_volatility():
    # Low vol: ATR% = 2.0% < 2.5% -> 1.5x ATR
    entry = 1000.0
    atr = 20.0  # 2.0%
    geom = calculate_pullback_sl_target(entry, atr)
    # 1.5 * 20 = 30 -> 3.0% stop clamped to min 3.5%
    assert geom["clamped_stop_pct"] == 0.035
    assert geom["stop_loss"] == 965.0
    assert geom["target_price"] == round(1000.0 + (2.5 * 35.0), 2)
    assert geom["natural_rr"] >= 2.5

def test_pullback_geometry_normal_volatility():
    # Normal vol: ATR% = 3.0% -> 1.8x ATR
    entry = 1000.0
    atr = 30.0  # 3.0%
    geom = calculate_pullback_sl_target(entry, atr)
    # 1.8 * 30 = 54 -> 5.4% stop
    assert round(geom["clamped_stop_pct"], 4) == 0.0540
    assert geom["stop_loss"] == 946.0
    assert geom["target_price"] == round(1000.0 + (2.5 * 54.0), 2)
    assert geom["natural_rr"] >= 2.5

def test_pullback_geometry_high_volatility():
    # High vol: ATR% = 5.0% -> 2.2x ATR
    entry = 1000.0
    atr = 50.0  # 5.0%
    geom = calculate_pullback_sl_target(entry, atr)
    # 2.2 * 50 = 110 -> 11.0% stop clamped to max 8.0%
    assert geom["clamped_stop_pct"] == 0.080
    assert geom["stop_loss"] == 920.0
    assert geom["target_price"] == round(1000.0 + (2.5 * 80.0), 2)
    assert geom["natural_rr"] >= 2.5
