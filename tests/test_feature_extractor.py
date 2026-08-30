"""
Unit tests for engine/analytics/feature_extractor.py.
Verifies feature extraction correctness, provenance classification, and dependency audit.
"""

import pytest
import pandas as pd
import numpy as np
from engine.analytics.feature_extractor import (
    extract_features,
    audit_feature_dependencies,
    FEATURE_PROVENANCE,
    FEATURE_COLUMNS
)


def test_extract_features_provenance_and_columns():
    df_raw = pd.DataFrame({
        "close_price": [100.0, 200.0, 150.0],
        "sma50": [95.0, 190.0, 155.0],
        "sma200": [90.0, 180.0, 140.0],
        "rsi": [65.0, 55.0, 70.0],
        "sector_blended_score": [5.2, -1.0, 8.4],
        "sector_status": ["TAILWIND", "NEUTRAL", "TAILWIND"],
        "volume": [100000, 200000, 150000],
        "rr_ratio": [2.5, 1.8, 3.0]
    })

    df_feat, med_vol = extract_features(df_raw)

    assert list(df_feat.columns) == FEATURE_COLUMNS
    assert len(df_feat) == 3
    assert med_vol == 150000.0

    # Verify provenance dictionary covers all columns
    for col in FEATURE_COLUMNS:
        assert col in FEATURE_PROVENANCE
        assert FEATURE_PROVENANCE[col] in ["PRE_DECISION_FEATURE", "POST_SETUP_FEATURE", "DERIVED_FROM_EXECUTION"]


def test_audit_feature_dependencies():
    np.random.seed(42)
    data = {
        "dist_sma50_pct": np.random.normal(5, 2, 20),
        "dist_sma200_pct": np.random.normal(10, 3, 20),
        "rsi": np.random.uniform(40, 80, 20),
        "sector_blended_score": np.random.normal(2, 5, 20),
        "is_tailwind": np.random.choice([0.0, 1.0], 20),
        "volume_ratio": np.random.uniform(0.8, 3.0, 20),
        "rr_ratio": np.random.uniform(1.5, 3.5, 20)
    }
    df_feat = pd.DataFrame(data)

    audit = audit_feature_dependencies(df_feat)

    assert "feature_statistics" in audit
    assert "correlation_matrix" in audit
    assert len(audit["feature_statistics"]) == len(FEATURE_COLUMNS)
    assert audit["feature_statistics"]["rr_ratio"]["provenance"] == "DERIVED_FROM_EXECUTION"
