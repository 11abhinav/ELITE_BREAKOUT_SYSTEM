"""
Unit tests for engine/analytics/alert_quality_model.py.
Verifies TrainFittedScaler isolation, RidgeAQSModel regularization, and scoring bounds.
"""

import pytest
import pandas as pd
import numpy as np
from engine.analytics.alert_quality_model import (
    TrainFittedScaler,
    RidgeAQSModel,
    ScalerParameters
)


def test_train_fitted_scaler_isolation():
    # Fit scaler on training data
    df_train = pd.DataFrame({
        "feat_a": [10.0, 20.0, 30.0],
        "feat_b": [100.0, 200.0, 300.0]
    })
    scaler = TrainFittedScaler().fit(df_train)

    assert scaler.parameters.means["feat_a"] == 20.0
    assert scaler.parameters.means["feat_b"] == 200.0

    # Transform out-of-sample data using frozen training parameters
    df_test = pd.DataFrame({
        "feat_a": [40.0],
        "feat_b": [400.0]
    })
    df_scaled = scaler.transform(df_test)

    # Standard deviation of [10, 20, 30] is 10.0
    assert df_scaled["feat_a"].iloc[0] == pytest.approx(2.0, rel=1e-2)


def test_ridge_aqs_model_training_and_bounding():
    np.random.seed(42)
    n = 35
    X_train = pd.DataFrame({
        "f1": np.random.normal(0, 1, n),
        "f2": np.random.normal(0, 1, n),
        "f3": np.random.normal(0, 1, n)
    })
    y_train = pd.Series(0.5 * X_train["f1"] + 0.3 * X_train["f2"] + np.random.normal(0.4, 0.2, n))

    model = RidgeAQSModel(model_id="AQS_TEST_v1", l2_lambda=10.0)
    model.fit(X_train, y_train)

    assert model.is_fitted is True
    assert len(model.weights) == 3

    # Generate predictions on test data
    X_test = pd.DataFrame({
        "f1": [-5.0, 0.0, 5.0],
        "f2": [-5.0, 0.0, 5.0],
        "f3": [-5.0, 0.0, 5.0]
    })
    scores = model.predict_score(X_test)

    # All scores must be strictly bounded in [0, 100]
    assert all(0.0 <= s <= 100.0 for s in scores)
    assert scores.iloc[2] > scores.iloc[0] # Positive monotonicity
