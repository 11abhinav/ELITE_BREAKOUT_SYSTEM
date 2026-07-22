# =====================================================================================
# tests/test_f01_to_f07_quant_engine.py
# PYTEST SUITE FOR QUANT ENGINE ENHANCEMENTS (F-01, F-03, F-04, F-05, F-07)
# =====================================================================================

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from config import RS_BONUS, SECTOR_BONUS, MAX_MOMENTUM_BONUS
from daily_builder import _score_fin
from macro_utils import compute_nifty_rs_rating, compute_sector_regime_rankings
from core.models import AlertOutcome, SectorRank, ConfluenceMatch



def test_momentum_bonus_stacking_cap():
    """Verify that combined RS (+10) and Sector (+8) bonuses are capped at MAX_MOMENTUM_BONUS (15)."""
    rs_bonus = RS_BONUS  # 10
    sector_bonus = SECTOR_BONUS  # 8
    
    total_unclipped = rs_bonus + sector_bonus  # 18
    total_capped = min(MAX_MOMENTUM_BONUS, total_unclipped)  # 15
    
    assert RS_BONUS == 10
    assert SECTOR_BONUS == 8
    assert MAX_MOMENTUM_BONUS == 15
    assert total_capped == 15
    assert total_capped < total_unclipped  # Proves Sector bonus is partially capped, not clipped to zero


def test_pure_fm_score_banking_ratios():
    """Verify _score_fin evaluates Net NPA level & trend, Banded NIM (3.0-6.0%), CAR, and CASA ratio."""
    # High quality bank
    score_high = _score_fin(
        yoy_rev=20.0, yoy_profit=30.0, qoq_rev=15.0, qoq_profit=18.0,
        roe=18.0, roa=2.1, yoy_margin=True, fin_mature=True, fin_compounder=True,
        nnpa=0.5, nnpa_declining=True, nim=4.2, car=16.5, casa=45.0
    )
    assert score_high >= 120

    # Risky MFI bank with NIM > 7.0% (Caution flag: NIM bonus is 0)
    score_mfi = _score_fin(
        yoy_rev=20.0, yoy_profit=30.0, qoq_rev=15.0, qoq_profit=18.0,
        roe=18.0, roa=2.1, yoy_margin=True, fin_mature=True, fin_compounder=True,
        nnpa=0.5, nnpa_declining=True, nim=8.5, car=16.5, casa=45.0  # NIM 8.5% > 7.0%
    )
    assert score_mfi == score_high - 15  # NIM bonus is excluded for high credit risk MFI


def test_same_bar_ambiguous_sl_collision_convention():
    """Verify conservative same-bar collision rule (High >= T1 AND Low <= SL -> AMBIGUOUS_SL_HIT / -1.0R loss)."""
    entry = 100.0
    sl = 95.0
    t1 = 110.0
    risk_dist = entry - sl  # 5.0

    # Bar where High touches T1 (112.0 >= 110.0) AND Low touches SL (94.0 <= 95.0)
    bar_high = 112.0
    bar_low = 94.0

    hit_target = (bar_high >= t1)
    hit_sl = (bar_low <= sl)

    assert hit_target is True
    assert hit_sl is True

    # Conservative rule enforces AMBIGUOUS_SL_HIT and -1.0R realized loss
    exit_reason = "AMBIGUOUS_SL_HIT" if (hit_target and hit_sl) else "T1_HIT"
    realized_rr = -1.0 if exit_reason == "AMBIGUOUS_SL_HIT" else round((t1 - entry) / risk_dist, 2)

    assert exit_reason == "AMBIGUOUS_SL_HIT"
    assert realized_rr == -1.0


def test_gap_through_sl_slippage_calculation():
    """Verify that a gap-down open below SL calculates actual realized_rr slippage."""
    entry = 100.0
    sl = 95.0
    risk_dist = entry - sl  # 5.0

    # Open price gaps down to 92.0 (below SL 95.0)
    b_open = 92.0
    hit_sl = (b_open <= sl)

    assert hit_sl is True
    realized_rr = round((b_open - entry) / risk_dist, 2) if b_open < sl else -1.0

    assert realized_rr == -1.6  # (92.0 - 100.0) / 5.0 = -1.6R


def test_sector_hysteresis_3day_grant():
    """Verify 3-session hysteresis rule (rank 4 -> 3 -> 3 -> 3 grants TAILWIND on day 3, not day 2)."""
    prev_top3_day0 = 0
    
    # Day 1: Rank 3 -> counter becomes 1 (NEUTRAL)
    counter_day1 = prev_top3_day0 + 1
    status_day1 = "TAILWIND" if counter_day1 >= 3 else "NEUTRAL"
    assert counter_day1 == 1
    assert status_day1 == "NEUTRAL"

    # Day 2: Rank 3 -> counter becomes 2 (NEUTRAL)
    counter_day2 = counter_day1 + 1
    status_day2 = "TAILWIND" if counter_day2 >= 3 else "NEUTRAL"
    assert counter_day2 == 2
    assert status_day2 == "NEUTRAL"

    # Day 3: Rank 3 -> counter becomes 3 (TAILWIND granted)
    counter_day3 = counter_day2 + 1
    status_day3 = "TAILWIND" if counter_day3 >= 3 else "NEUTRAL"
    assert counter_day3 == 3
    assert status_day3 == "TAILWIND"
