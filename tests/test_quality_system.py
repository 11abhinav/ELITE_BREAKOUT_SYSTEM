"""
Unit Test Suite for Quality Infrastructure:
- test_quality_contract
- test_quality_features
- test_failure_anatomy
- test_scanner_quality_orchestrator
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime

from engine.analytics.quality_contract import (
    QualityAlertContract, ScannerType, QualityAction, IntegrityStatus
)
from engine.analytics.quality_features import (
    extract_quality_features, FEATURE_REGISTRY, FeatureTiming
)
from engine.analytics.failure_anatomy import (
    compute_failure_anatomy, compute_2x2_matrix
)
from engine.analytics.scanner_quality_orchestrator import (
    evaluate_forward_gate, ScannerLifecycleState, ScannerGateRequirements
)

# 1. Test Quality Contract
def test_quality_contract_valid_geometry():
    alert = QualityAlertContract(
        scanner=ScannerType.PULLBACK,
        alert_id="ALT_001",
        setup_id="SETUP_001",
        decision_timestamp=datetime(2026, 8, 30, 9, 30),
        symbol="RELIANCE",
        entry_price=2500.0,
        stop_price=2450.0,
        target_price=2600.0,
        risk_distance=50.0,
        side="LONG"
    )
    assert alert.validate_geometry() is True
    assert alert.geometry_status == IntegrityStatus.VALID

def test_quality_contract_invalid_geometry():
    # Stop above entry for LONG
    alert = QualityAlertContract(
        scanner=ScannerType.PULLBACK,
        alert_id="ALT_002",
        setup_id="SETUP_002",
        decision_timestamp=datetime(2026, 8, 30, 9, 30),
        symbol="RELIANCE",
        entry_price=2500.0,
        stop_price=2550.0,  # Invalid
        target_price=2600.0,
        risk_distance=50.0,
        side="LONG"
    )
    assert alert.validate_geometry() is False
    assert alert.geometry_status == IntegrityStatus.INVALID_GEOMETRY

# 2. Test Quality Features PIT Extraction
def test_quality_features_extraction():
    # Construct synthetic 100-bar dataframe
    np.random.seed(42)
    prices = 100.0 + np.cumsum(np.random.randn(100))
    df = pd.DataFrame({
        'Open': prices,
        'High': prices + 1.0,
        'Low': prices - 1.0,
        'Close': prices,
        'Volume': np.random.randint(1000, 5000, size=100)
    })
    features = extract_quality_features(df, decision_idx=80)
    assert "rsi_14" in features
    assert "vol_surge_ratio" in features
    assert "consolidation_width_pct" in features
    assert "trend_alignment_score" in features
    assert features["rsi_14"] >= 0 and features["rsi_14"] <= 100

# 3. Test Failure Anatomy & 2x2 Matrix
def test_failure_anatomy_computations():
    df_outcomes = pd.DataFrame({
        'net_r': [+1.5, -1.0, +2.0, -0.8, -1.0, +0.5],
        'mae_r': [0.2, 1.0, 0.3, 0.9, 1.0, 0.4],
        'mfe_r': [1.8, 0.1, 2.2, 0.2, 0.0, 0.8],
        'aqs_score': [70.0, 30.0, 85.0, 40.0, 35.0, 60.0]
    })
    fa = compute_failure_anatomy(df_outcomes, "PULLBACK")
    assert fa.total_alerts == 6
    assert fa.failures_count == 3
    assert fa.failure_frequency_pct == 50.0
    
    matrix = compute_2x2_matrix(df_outcomes, score_col='aqs_score', score_cutoff=50.0)
    assert matrix["hq_win_count"] == 3
    assert matrix["lq_loss_count"] == 3
    assert matrix["winner_retention_pct"] == 100.0
    assert matrix["loser_recall_pct"] == 100.0

# 4. Test Scanner Quality Orchestrator Forward Gate
def test_scanner_quality_orchestrator_gate():
    req = ScannerGateRequirements(min_forward_n=50)
    # 1. Accumulating state
    assert evaluate_forward_gate(
        forward_n=30, unique_symbols=18, trading_days=8, max_concentration_pct=15.0,
        delta_net_er=0.10, bca_lower_ci=0.02, delta_maxdd=-0.5, req=req
    ) == "ACCUMULATING"
    
    # 2. Gate Pass
    assert evaluate_forward_gate(
        forward_n=52, unique_symbols=20, trading_days=10, max_concentration_pct=12.0,
        delta_net_er=0.12, bca_lower_ci=0.03, delta_maxdd=-0.4, req=req
    ) == "PASS"
    
    # 3. Gate Fail (Confidence Interval crosses zero)
    assert evaluate_forward_gate(
        forward_n=55, unique_symbols=20, trading_days=10, max_concentration_pct=12.0,
        delta_net_er=0.02, bca_lower_ci=-0.05, delta_maxdd=0.1, req=req
    ) == "FAIL"
