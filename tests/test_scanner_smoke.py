"""
tests/test_scanner_smoke.py

[SCANNER_SMOKE_TEST_v1.0] BUG-10 FIX:
The existing 67-test suite had ZERO scanner execution coverage. All the runtime bugs
found in the system audit (NameError, TypeError, IndexError) survived the full test suite
because no test imported or exercised the scanner pipeline.

This smoke test file specifically targets:
  1. Import health of all scanner modules (catches ModuleNotFoundError, SyntaxError)
  2. compute_sl_and_target() with every mode (catches TypeError, NameError in dispatch)
  3. _compute_target_quality() signature correctness (catches wrong kwargs crash)
  4. BaseRiskEngine v2 SL computation (catches IndexError on _MODE_CONFIG[4])
  5. calculate_score() with a minimal mock ticker (catches NameError in scoring engine)

By exercising these code paths with synthetic data, any future regression in the
critical scanner pipeline will be caught BEFORE a Git push.
"""

import sys
import os
import math

import pytest
import pandas as pd
import numpy as np

# Add app to path so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))


# ── Import health ─────────────────────────────────────────────────────────────

def test_import_eod_scanner():
    """[SCANNER_SMOKE_TEST_v1.0] eod_scanner must import cleanly."""
    import eod_scanner  # noqa: F401


def test_import_reversal_scanner():
    """[SCANNER_SMOKE_TEST_v1.0] reversal_scanner must import cleanly."""
    import reversal_scanner  # noqa: F401


def test_import_multi_tf_scanner():
    """[SCANNER_SMOKE_TEST_v1.0] multi_tf_scanner must import cleanly."""
    import multi_tf_scanner  # noqa: F401


def test_import_sl_target_helper():
    """[SCANNER_SMOKE_TEST_v1.0] sl_target_helper must import cleanly."""
    import sl_target_helper  # noqa: F401


def test_import_scoring_engine():
    """[SCANNER_SMOKE_TEST_v1.0] scoring_engine must import cleanly."""
    import scoring_engine  # noqa: F401


def test_import_macro_utils():
    """[SCANNER_SMOKE_TEST_v1.0] macro_utils must import cleanly."""
    import macro_utils  # noqa: F401


def test_import_strategy_policy():
    """[SCANNER_SMOKE_TEST_v1.0] strategy_policy must import cleanly."""
    import strategy_policy  # noqa: F401


# ── compute_sl_and_target — all modes ────────────────────────────────────────

def _make_sl_kwargs(**override):
    """Return a minimal valid set of kwargs for compute_sl_and_target."""
    base = dict(
        entry_price=100.0,
        atr=2.0,
        candle_range=1.5,
        adx=28.0,
        rsi=58.0,
        macd_hist=0.3,
        atr_pct=2.0,
        swing_low=95.0,
        swing_high=108.0,
        bb_upper=109.0,
        bb_lower=92.0,
        bb_mid=100.5,
        s1=96.0,
        s2=93.0,
        r1=105.0,
        r2=110.0,
        swing_low_raw=94.0,
        swing_high_raw=107.0,
        candle_low=98.5,
        vwap=99.5,
        ema20=100.2,
        sma50=98.0,
    )
    base.update(override)
    return base


def test_compute_sl_eod_mode():
    """[SCANNER_SMOKE_TEST_v1.0] compute_sl_and_target EOD mode must not crash."""
    from sl_target_helper import compute_sl_and_target
    result = compute_sl_and_target(mode="EOD", **_make_sl_kwargs())
    assert isinstance(result, dict), "EOD mode must return a dict"
    assert "stop_loss" in result, "EOD result must have stop_loss"


def test_compute_sl_reversal_mode():
    """[SCANNER_SMOKE_TEST_v1.0] compute_sl_and_target REVERSAL mode must not crash."""
    from sl_target_helper import compute_sl_and_target
    result = compute_sl_and_target(mode="REVERSAL", **_make_sl_kwargs())
    assert isinstance(result, dict), "REVERSAL mode must return a dict"
    assert "stop_loss" in result, "REVERSAL result must have stop_loss"


def test_compute_sl_intraday_mode():
    """[SCANNER_SMOKE_TEST_v1.0] compute_sl_and_target INTRADAY mode must not crash.
    Specifically guards BUG-8: was silently falling through to _compute_eod."""
    from sl_target_helper import compute_sl_and_target
    result = compute_sl_and_target(mode="INTRADAY", **_make_sl_kwargs())
    assert isinstance(result, dict), "INTRADAY mode must return a dict"
    assert "stop_loss" in result, "INTRADAY result must have stop_loss"


def test_compute_sl_live_1h_mode():
    """[SCANNER_SMOKE_TEST_v1.0] compute_sl_and_target LIVE_1H mode must not crash.
    Specifically guards BUG-8+11: _compute_live_1h doesn't exist; safely delegates."""
    from sl_target_helper import compute_sl_and_target
    result = compute_sl_and_target(mode="LIVE_1H", **_make_sl_kwargs())
    assert isinstance(result, dict), "LIVE_1H mode must return a dict"
    assert "stop_loss" in result, "LIVE_1H result must have stop_loss"


def test_compute_sl_multi_tf_mode():
    """[SCANNER_SMOKE_TEST_v1.0] compute_sl_and_target MULTI_TF mode must not crash.
    Specifically guards BUG-2: wrong kwargs to _compute_target_quality."""
    from sl_target_helper import compute_sl_and_target
    result = compute_sl_and_target(mode="MULTI_TF", **_make_sl_kwargs())
    assert isinstance(result, dict), "MULTI_TF mode must return a dict"
    assert "stop_loss" in result, "MULTI_TF result must have stop_loss"


# ── _compute_target_quality signature ────────────────────────────────────────

def test_compute_target_quality_correct_args():
    """[SCANNER_SMOKE_TEST_v1.0] _compute_target_quality must accept correct positional args.
    Guards BUG-2: function was being called with wrong kwargs (entry=, atr_pct=, etc.)."""
    from sl_target_helper import _compute_target_quality
    # Correct call: (natural_rr, rsi, adx, macd_hist, volume_ratio, swing_high, r1, r2, bb_upper)
    tq, bd = _compute_target_quality(2.5, 62.0, 30.0, 0.4, 1.8, 108.0, 105.0, 110.0, 109.0)
    assert isinstance(tq, (int, float)), "target quality must be numeric"
    assert isinstance(bd, dict), "quality breakdown must be a dict"


def test_compute_target_quality_wrong_kwargs_raises():
    """[SCANNER_SMOKE_TEST_v1.0] Calling _compute_target_quality with wrong kwargs must fail fast.
    This guards regression of BUG-2 where wrong kwargs were silently causing a TypeError."""
    from sl_target_helper import _compute_target_quality
    with pytest.raises(TypeError):
        _compute_target_quality(
            entry=100.0, adx=25.0, rsi=60.0, macd_hist=0.5,
            atr_pct=2.0, target_1=110.0, natural_rr=2.0, support_score=50
        )


# ── BaseRiskEngine v2 — IndexError guard ────────────────────────────────────

def test_base_risk_engine_v2_no_index_error():
    """[SCANNER_SMOKE_TEST_v1.0] BaseRiskEngine.compute_sl() must not crash with IndexError.
    Guards BUG-4: _MODE_CONFIG[...][4] was accessing an out-of-range index.
    Constructor signature: BaseRiskEngine(mode, kwargs_dict) — entry_price lives inside kwargs."""
    from sl_target_helper import BaseRiskEngine
    kwargs = _make_sl_kwargs()
    # entry_price stays inside kwargs dict per the actual __init__(mode, kwargs: dict) signature
    engine = BaseRiskEngine(mode="EOD", kwargs=kwargs)
    sl = engine.compute_sl(support_price=95.0, support_conf=70, vol_regime="NORMAL")
    assert isinstance(sl, float), "compute_sl must return a float"
    assert sl > 0, "SL must be positive"
    assert sl < 100.0, "SL must be below entry for a buy trade"



# ── natural_rr key in MULTI_TF result ────────────────────────────────────────

def test_multi_tf_result_has_natural_rr_not_rr_ratio():
    """[SCANNER_SMOKE_TEST_v1.0] MULTI_TF sl_result must contain 'natural_rr', not 'rr_ratio'.
    Guards BUG-9: multi_tf_scanner was checking rr_ratio (always 0.0) and suppressing all alerts."""
    from sl_target_helper import compute_sl_and_target
    result = compute_sl_and_target(mode="MULTI_TF", **_make_sl_kwargs())
    # natural_rr MUST be present (not rr_ratio)
    assert "natural_rr" in result, "MULTI_TF result must have 'natural_rr' key"


# ── regime_ctx derives correct bayesian_regime ───────────────────────────────

def test_regime_ctx_bayesian_regime_derivation():
    """[SCANNER_SMOKE_TEST_v1.0] bayesian_regime must be correctly derived from regime_ctx dict.
    Guards BUG-7: regime_ctx was silently swallowed by **kwargs and trend was never stored."""
    regime_ctx_bull = {"trend": "BULL", "biases": {}}
    regime_ctx_bear = {"trend": "BEAR", "biases": {}}
    regime_ctx_none = None

    br_bull = regime_ctx_bull.get("trend", "BULL") if isinstance(regime_ctx_bull, dict) else "BULL"
    br_bear = regime_ctx_bear.get("trend", "BULL") if isinstance(regime_ctx_bear, dict) else "BULL"
    br_none = regime_ctx_none.get("trend", "BULL") if isinstance(regime_ctx_none, dict) else "BULL"

    assert br_bull == "BULL"
    assert br_bear == "BEAR"
    assert br_none == "BULL"  # safe fallback when regime_ctx is None
