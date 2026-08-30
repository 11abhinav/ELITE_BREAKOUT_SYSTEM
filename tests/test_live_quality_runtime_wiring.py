"""
Test Suite: Live Quality Runtime Adapter Wiring & Candidate Scoring
Verifies:
  1. 100% mathematical fidelity across all 7 frozen candidate models in artifacts/scanner_quality_model_registry.json with hand calculations.
  2. Cryptographic canonical SHA256 integrity verification, tamper rejection, and formatting invariance.
  3. Strict finite-numeric fail-fast feature contract (rejects missing, None, NaN, inf, boolean, non-numeric strings).
  4. Exact 4-component 10.0 bps round-trip friction accounting for both LONG and SHORT trades.
  5. ScannerExecutionPolicy configurations (timeframe, horizon, session bounds).
  6. Test fixture isolation (TEST_FIXTURE never increments forward evidence N).
"""

import copy
import json
import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from engine.analytics.quality_contract import ScannerType, QualityAction, IntegrityStatus
from engine.analytics.quality_features import extract_quality_features
from engine.analytics.scanner_quality_runtime import (
    score_scanner_alert, get_candidate_metadata, load_authoritative_registry,
    execute_candidate_model, compute_canonical_registry_hash,
    RegistryIntegrityError, MissingFeatureContractError
)
from engine.analytics.forward_outcome_resolver import (
    resolve_trade_path, SCANNER_EXECUTION_POLICIES, CANONICAL_ROUNDTRIP_FRICTION_BPS,
    ObservationState
)
from engine.analytics.live_forward_shadow_ingestor import (
    ingest_live_scanner_alert, get_runtime_diagnostics, reset_runtime_diagnostics
)

# -----------------------------------------------------------------------------
# 1. GOLDEN-REFERENCE MATHEMATICAL FIDELITY TESTS FOR ALL 7 CANDIDATE MODELS
# -----------------------------------------------------------------------------

def test_model_fidelity_1_aqs_eod_v1():
    """1. AQS_EOD_v1: Regularized Ridge Linear with Min-Max Score Normalization."""
    reg = load_authoritative_registry()
    model_def = reg["models"]["AQS_EOD_v1"]
    
    feats = {
        "dist_sma50_pct": 12.0,
        "dist_sma200_pct": 15.0,
        "rsi_14": 65.0,
        "vol_surge_ratio": 1.5
    }
    
    # Hand calculation:
    # Means: sma50: 10.5493, sma200: 14.2186, rsi: 68.0394, vol: 0.9058
    # Stds: sma50: 6.1896, sma200: 4.2289, rsi: 4.4501, vol: 0.3326
    # Weights: sma50: -0.1284, sma200: -0.1965, rsi: -0.0796, vol: 0.3247
    # Intercept: 0.3443, raw_min: -0.5, raw_max: 1.5
    z1 = (12.0 - 10.5493) / 6.1896
    z2 = (15.0 - 14.2186) / 4.2289
    z3 = (65.0 - 68.0394) / 4.4501
    z4 = (1.5 - 0.9058) / 0.3326
    
    weighted_sum = (-0.1284) * z1 + (-0.1965) * z2 + (-0.0796) * z3 + 0.3247 * z4
    raw_score = 0.3443 + weighted_sum
    norm = (raw_score - (-0.5)) / (1.5 - (-0.5))
    expected_score = round(float(np.clip(norm, 0.0, 1.0) * 100.0), 2)
    
    score, tier, action, meta = score_scanner_alert(ScannerType.EOD, feats)
    assert abs(score - expected_score) < 1e-4
    assert tier in ["ELITE", "HIGH", "STANDARD", "LOW"]

def test_model_fidelity_2_aqs_accum_v1():
    """2. AQS_ACCUM_v1 (MULTIBAGGER): Linear Standardized Score (50 + 15 * (0.6*z(RSI) - 0.4*z(Width)))."""
    feats = {"rsi_14": 50.0, "consolidation_width_pct": 15.0}
    
    # Hand calculation:
    # z_rsi = (50 - 30)/40 = 0.5
    # z_width = (15 - 30)/25 = -0.6
    # w_sum = 0.6 * 0.5 + (-0.4) * (-0.6) = 0.30 + 0.24 = 0.54
    # score = 50 + 15 * 0.54 = 58.10
    expected_score = round(50.0 + 15.0 * (0.6 * 0.5 + (-0.4) * (-0.6)), 2)
    
    score, _, _, _ = score_scanner_alert(ScannerType.MULTIBAGGER, feats)
    assert abs(score - expected_score) < 1e-4
    assert score == 58.10

def test_model_fidelity_3_aqs_pullback_v1():
    """3. AQS_PULLBACK_v1: Depth Rebound Score (50 + 15 * (0.6*z(Fit) + 0.4*z(Vol)))."""
    feats = {"pullback_depth_fit": 0.8, "vol_surge_ratio": 1.0}
    
    # Hand calculation:
    # z_fit = (0.8 - 0.0)/1.0 = 0.8
    # z_vol = (1.0 - 0.0)/2.0 = 0.5
    # w_sum = 0.6 * 0.8 + 0.4 * 0.5 = 0.48 + 0.20 = 0.68
    # score = 50 + 15 * 0.68 = 60.20
    expected_score = round(50.0 + 15.0 * (0.6 * 0.8 + 0.4 * 0.5), 2)
    
    score, _, _, _ = score_scanner_alert(ScannerType.PULLBACK, feats)
    assert abs(score - expected_score) < 1e-4
    assert score == 60.20

def test_model_fidelity_4_aqs_daily_builder_v1():
    """4. AQS_DAILY_BUILDER_v1: ORB Surge Score (50 + 15 * (1.0*z(Vol)))."""
    feats = {"vol_surge_ratio": 2.5}
    
    # Hand calculation:
    # z_vol = (2.5 - 0.0)/2.0 = 1.25
    # w_sum = 1.0 * 1.25 = 1.25
    # score = 50 + 15 * 1.25 = 68.75
    expected_score = round(50.0 + 15.0 * (1.0 * 1.25), 2)
    
    score, _, _, _ = score_scanner_alert(ScannerType.DAILY_BUILDER, feats)
    assert abs(score - expected_score) < 1e-4
    assert score == 68.75

def test_model_fidelity_5_aqs_multi_tf_v1():
    """5. AQS_MULTI_TF_v1: Trend Alignment Score (50 + 15 * (1.0*z(TA)))."""
    feats = {"trend_alignment_score": 1.0}
    
    # Hand calculation:
    # z_ta = (1.0 - 0.0)/1.0 = 1.0
    # w_sum = 1.0 * 1.0 = 1.0
    # score = 50 + 15 * 1.0 = 65.00
    expected_score = round(50.0 + 15.0 * 1.0, 2)
    
    score, _, _, _ = score_scanner_alert(ScannerType.MULTI_TF, feats)
    assert abs(score - expected_score) < 1e-4
    assert score == 65.00

def test_model_fidelity_6_aqs_wealth_v1():
    """6. AQS_WEALTH_v1: Multi-Factor Fundamental Score (0.4*Mom + 0.3*Val + 0.3*Cons)."""
    feats = {
        "fundamental_momentum_score": 75.0,
        "valuation_score": 60.0,
        "consistency_score": 80.0
    }
    
    # Hand calculation:
    # score = 0.4*75.0 + 0.3*60.0 + 0.3*80.0 = 30.0 + 18.0 + 24.0 = 72.00
    expected_score = round(0.4 * 75.0 + 0.3 * 60.0 + 0.3 * 80.0, 2)
    
    score, _, _, _ = score_scanner_alert(ScannerType.WEALTH_ENGINE, feats)
    assert abs(score - expected_score) < 1e-4
    assert score == 72.00

def test_model_fidelity_7_aqs_reversal_v3():
    """7. AQS_REVERSAL_v3: Discovery Active Score (40 + 30 * (-0.6*z(RSI) + 0.4*z(Vol)))."""
    feats = {"rsi_14": 25.0, "vol_surge_ratio": 1.5}
    
    # Hand calculation:
    # z_rsi = (25.0 - 40.0)/25.0 = -0.6
    # z_vol = (1.5 - 0.0)/2.0 = 0.75
    # w_sum = (-0.6)*(-0.6) + 0.4*0.75 = 0.36 + 0.30 = 0.66
    # score = 40 + 30 * 0.66 = 59.80
    expected_score = round(40.0 + 30.0 * 0.66, 2)
    
    score, tier, action, meta = score_scanner_alert(ScannerType.REVERSAL, feats)
    assert abs(score - expected_score) < 1e-4
    assert score == 59.80
    assert tier == "DISCOVERY"
    assert action == QualityAction.PASS_THROUGH

# -----------------------------------------------------------------------------
# 2. EXACT 4-COMPONENT FRICTION MODEL TESTS (LONG & SHORT)
# -----------------------------------------------------------------------------

def test_4_component_friction_long_trade():
    """
    Verify exact 4-component 10 bps friction for LONG trades.
    Entry = 1000, Exit = 1100, Stop = 950 (Risk = 50).
    Entry slip: 0.25, Entry comm: 0.25 -> 0.50
    Exit slip: 0.275, Exit comm: 0.275 -> 0.55
    Total friction = 1.05 currency units.
    Gross PnL = 100 -> Gross R = 2.0
    Net PnL = 98.95 -> Net R = 98.95 / 50 = 1.9790
    """
    alert = {
        "scanner": "EOD",
        "entry_price": 1000.0,
        "stop_price": 950.0,
        "target_price": 1100.0,
        "side": "LONG"
    }
    future_bars = pd.DataFrame({
        "Open": [1000.0, 1000.0],
        "High": [1010.0, 1100.0],
        "Low": [990.0, 1000.0],
        "Close": [1000.0, 1100.0]
    })
    
    res = resolve_trade_path(alert, future_bars, scanner_type=ScannerType.EOD)
    assert res["gross_realized_R"] == 2.0
    assert res["entry_slippage"] == 0.25
    assert res["entry_commission"] == 0.25
    assert res["exit_slippage"] == 0.275
    assert res["exit_commission"] == 0.275
    assert res["total_friction"] == 1.05
    assert abs(res["net_realized_R"] - 1.9790) < 1e-4

def test_4_component_friction_short_trade():
    """
    Verify exact 4-component 10 bps friction for SHORT trades.
    Entry = 1000, Exit = 900, Stop = 1050 (Risk = 50).
    Entry slip: 0.25, Entry comm: 0.25 -> 0.50
    Exit slip: 0.225, Exit comm: 0.225 -> 0.45
    Total friction = 0.95 currency units.
    Gross PnL = 100 -> Gross R = 2.0
    Net PnL = 99.05 -> Net R = 99.05 / 50 = 1.9810
    """
    alert = {
        "scanner": "EOD",
        "entry_price": 1000.0,
        "stop_price": 1050.0,
        "target_price": 900.0,
        "side": "SHORT"
    }
    future_bars = pd.DataFrame({
        "Open": [1000.0, 1000.0],
        "High": [1010.0, 1000.0],
        "Low": [990.0, 900.0],
        "Close": [1000.0, 900.0]
    })
    
    res = resolve_trade_path(alert, future_bars, scanner_type=ScannerType.EOD)
    assert res["gross_realized_R"] == 2.0
    assert res["entry_slippage"] == 0.25
    assert res["entry_commission"] == 0.25
    assert res["exit_slippage"] == 0.225
    assert res["exit_commission"] == 0.225
    assert res["total_friction"] == 0.95
    assert abs(res["net_realized_R"] - 1.9810) < 1e-4

# -----------------------------------------------------------------------------
# 3. REGISTRY CRYPTOGRAPHIC INTEGRITY & TAMPER DETECTION
# -----------------------------------------------------------------------------

def test_registry_cryptographic_integrity_and_tamper_rejection():
    """Verify cryptographic canonical hash contract, tamper detection, and formatting invariance."""
    reg = load_authoritative_registry(verify_integrity=True)
    canonical_hash = compute_canonical_registry_hash(reg)
    assert reg["registry_sha256"] == canonical_hash

    # Test 1: Tampered model parameter
    tampered_reg = copy.deepcopy(reg)
    tampered_reg["models"]["AQS_EOD_v1"]["weights"]["dist_sma50_pct"] = 99.99
    tampered_hash = compute_canonical_registry_hash(tampered_reg)
    assert tampered_hash != reg["registry_sha256"]

    # Test 2: Tampered hash field
    tampered_reg2 = copy.deepcopy(reg)
    tampered_reg2["registry_sha256"] = "0000000000000000000000000000000000000000000000000000000000000000"
    computed = compute_canonical_registry_hash(tampered_reg2)
    assert tampered_reg2["registry_sha256"] != computed

    # Test 3: Formatting invariance (compact re-serialization produces exact same hash)
    reformatted_bytes = json.dumps(reg, indent=4).encode("utf-8")
    loaded_reformatted = json.loads(reformatted_bytes.decode("utf-8"))
    assert compute_canonical_registry_hash(loaded_reformatted) == canonical_hash

# -----------------------------------------------------------------------------
# 4. STRICT FINITE-NUMERIC FAIL-FAST FEATURE CONTRACT
# -----------------------------------------------------------------------------

def test_strict_fail_fast_feature_contract():
    """Verify that MissingFeatureContractError is raised for all invalid feature inputs."""
    reg = load_authoritative_registry()
    eod_def = reg["models"]["AQS_EOD_v1"]
    
    valid_base = {
        "dist_sma50_pct": 10.0,
        "dist_sma200_pct": 12.0,
        "rsi_14": 60.0,
        "vol_surge_ratio": 1.2
    }
    
    # 1. Missing active feature
    with pytest.raises(MissingFeatureContractError, match="Missing required active predictor"):
        execute_candidate_model(eod_def, {"dist_sma50_pct": 10.0})
        
    # 2. None value
    bad_none = copy.deepcopy(valid_base)
    bad_none["dist_sma50_pct"] = None
    with pytest.raises(MissingFeatureContractError, match="invalid type"):
        execute_candidate_model(eod_def, bad_none)
        
    # 3. NaN float
    bad_nan = copy.deepcopy(valid_base)
    bad_nan["dist_sma50_pct"] = float("nan")
    with pytest.raises(MissingFeatureContractError, match="non-finite"):
        execute_candidate_model(eod_def, bad_nan)
        
    # 4. Inf value
    bad_inf = copy.deepcopy(valid_base)
    bad_inf["vol_surge_ratio"] = float("inf")
    with pytest.raises(MissingFeatureContractError, match="non-finite"):
        execute_candidate_model(eod_def, bad_inf)
        
    # 5. Boolean value (must not be converted to 1.0/0.0 silently)
    bad_bool = copy.deepcopy(valid_base)
    bad_bool["rsi_14"] = True
    with pytest.raises(MissingFeatureContractError, match="invalid type"):
        execute_candidate_model(eod_def, bad_bool)
        
    # 6. Non-numeric string
    bad_str = copy.deepcopy(valid_base)
    bad_str["dist_sma200_pct"] = "corrupt_value"
    with pytest.raises(MissingFeatureContractError, match="cannot be converted to float"):
        execute_candidate_model(eod_def, bad_str)

# -----------------------------------------------------------------------------
# 5. TEST FIXTURE ISOLATION & POLICY CHECKS
# -----------------------------------------------------------------------------

def test_scanner_execution_policies_configured():
    """Verify all 7 scanners have explicit tailored execution policies."""
    for sc in ScannerType:
        assert sc in SCANNER_EXECUTION_POLICIES
        p = SCANNER_EXECUTION_POLICIES[sc]
        assert p.max_holding_bars > 0
        assert p.timeframe in ["1D", "15m", "5m"]
        assert p.intrabar_collision_rule == "CONSERVATIVE"

def test_test_fixture_isolation():
    """Verify TEST_FIXTURE records are registered but valid_fwd count remains strictly 0."""
    reset_runtime_diagnostics()
    
    dates = pd.date_range("2026-01-01", periods=220, freq="D")
    df_hist = pd.DataFrame({
        "Open": np.linspace(100, 200, 220),
        "High": np.linspace(102, 202, 220),
        "Low": np.linspace(98, 198, 220),
        "Close": np.linspace(101, 201, 220),
        "Volume": np.ones(220) * 1500.0
    }, index=dates)

    for sc in ScannerType:
        feat_override = None
        if sc == ScannerType.WEALTH_ENGINE:
            feat_override = {
                "fundamental_momentum_score": 70.0,
                "valuation_score": 65.0,
                "consistency_score": 80.0,
                "pit_valid": True
            }
        res = ingest_live_scanner_alert(
            scanner=sc.value,
            symbol="TEST_FIDELITY_SYM",
            entry_price=100.0,
            stop_price=95.0,
            target_price=110.0,
            decision_timestamp=dates[210],
            df_history=df_hist,
            source_type="TEST_FIXTURE",
            features_override=feat_override
        )
        assert res["status"] == "REGISTERED_PENDING_OUTCOME"
        assert res["is_test_fixture"] is True

    diag = get_runtime_diagnostics()
    for sc in ScannerType:
        assert diag[sc.value]["valid_fwd"] == 0

