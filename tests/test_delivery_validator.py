import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from app.validation.validators.delivery_validator import DeliveryValidator
from app.validation.scoring.delivery_score import DeliveryScoreCalculator
from app.validation.context import ValidationContext
from app.validation.codes import FailureCode

# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def base_delivery():
    # A golden dataset for Delivery Time-Series
    data = {
        "SYMBOL": ["RELIANCE"] * 10,
        "SERIES": ["EQ"] * 10,
        "DATE": pd.date_range(end=datetime.now(), periods=10).strftime("%Y-%m-%d"),
        "TRADED_QTY": [1000, 1500, 2000, 1000, 3000, 1000, 2500, 1200, 4000, 1500],
        "DELIV_QTY": [500, 750, 800, 500, 1000, 400, 1500, 600, 2000, 500],
        "DELIV_PCT": [50.0, 50.0, 40.0, 50.0, 33.33, 40.0, 60.0, 50.0, 50.0, 33.33]
    }
    return pd.DataFrame(data)

@pytest.fixture
def context():
    return ValidationContext(provider="NSE_DELIVERY")

@pytest.fixture
def validator():
    return DeliveryValidator()

# ==============================================================================
# Valid Dataset Tests
# ==============================================================================

def test_delivery_perfect_dataset(validator, base_delivery, context):
    result = validator.validate(base_delivery, context)
    assert result.is_valid is True
    assert len(result.schema_failures) == 0
    assert len(result.business_failures) == 0
    assert len(result.historical_failures) == 0
    assert result.metrics.row_count == 10

# ==============================================================================
# Schema Failure Tests
# ==============================================================================

def test_delivery_missing_columns(validator, base_delivery, context):
    df = base_delivery.drop(columns=["DELIV_QTY"])
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)

def test_delivery_duplicate_columns(validator, base_delivery, context):
    df = base_delivery.copy()
    df.columns = ["TRADED_QTY"] + list(df.columns)[1:] 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)

def test_delivery_wrong_type(validator, base_delivery, context):
    df = base_delivery.copy()
    df["TRADED_QTY"] = "StringValue"
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH002 for f in result.schema_failures)

# ==============================================================================
# Business Failure Tests
# ==============================================================================

def test_delivery_negative_quantity(validator, base_delivery, context):
    df = base_delivery.copy()
    df.loc[0, "DELIV_QTY"] = -500 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_delivery_deliv_greater_than_traded(validator, base_delivery, context):
    df = base_delivery.copy()
    df.loc[0, "DELIV_QTY"] = df.loc[0, "TRADED_QTY"] + 100 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_delivery_deliv_pct_out_of_bounds(validator, base_delivery, context):
    df = base_delivery.copy()
    df.loc[0, "DELIV_PCT"] = 150.0 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_delivery_percentage_mismatch(validator, base_delivery, context):
    df = base_delivery.copy()
    # Correct is 50%, setting to 55% which is > 0.5% tolerance
    df.loc[0, "DELIV_PCT"] = 55.0 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)
    
def test_delivery_zero_traded_quantity_edge_case(validator, base_delivery, context):
    df = base_delivery.copy()
    # If Traded Qty is 0, Deliv Qty must be 0
    df.loc[0, "TRADED_QTY"] = 0
    df.loc[0, "DELIV_QTY"] = 100
    df.loc[0, "DELIV_PCT"] = np.nan
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)
    
    # But if both are 0, it should be valid
    df.loc[0, "DELIV_QTY"] = 0
    result = validator.validate(df, context)
    assert result.is_valid is True

# ==============================================================================
# Historical/Time-Series Tests
# ==============================================================================

def test_delivery_duplicate_timestamps(validator, base_delivery, context):
    df = base_delivery.copy()
    duplicate_row = df.iloc[[0]].copy()
    df = pd.concat([df, duplicate_row], ignore_index=True)
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS003 for f in result.historical_failures)

def test_delivery_non_monotonic_dates(validator, base_delivery, context):
    df = base_delivery.copy()
    # Swap dates to break monotonicity
    temp = df.loc[1, "DATE"]
    df.loc[1, "DATE"] = df.loc[8, "DATE"]
    df.loc[8, "DATE"] = temp
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS002 for f in result.historical_failures)
    
def test_delivery_future_dates(validator, base_delivery, context):
    df = base_delivery.copy()
    future_date = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    df.loc[9, "DATE"] = future_date
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS001 for f in result.historical_failures)

# ==============================================================================
# Quality Degradation Tests (Scoring)
# ==============================================================================

def test_delivery_missing_optional_percentage(validator, base_delivery, context):
    """
    Test that missing DELIV_PCT (optional to recompute, but missing cell)
    causes a score penalty but DOES NOT fail validation.
    """
    df = base_delivery.copy()
    df.loc[0, "DELIV_PCT"] = np.nan
    
    result = validator.validate(df, context)
    assert result.is_valid is True
    
    score_calc = DeliveryScoreCalculator()
    score = score_calc.calculate(result)
    assert score < 100.0
    assert result.metrics.missing_pct > 0

def test_delivery_small_completeness_degradation(validator, base_delivery, context):
    """
    Test a few missing DELIV_QTY values. 
    Actually, DELIV_QTY is required in our schema, so if it's missing, it will schema-fail?
    No, schema validation checks if the COLUMN is present.
    Missing values in the cells are handled by the metric calculator and scored.
    """
    df = base_delivery.copy()
    df.loc[0, "DELIV_QTY"] = np.nan
    
    result = validator.validate(df, context)
    # The equality checks (e.g. qty > traded) use pandas vectorized ops which handle NaN
    # by returning False. So it shouldn't trigger business failures.
    assert result.is_valid is True
    
    score_calc = DeliveryScoreCalculator()
    score = score_calc.calculate(result)
    assert score < 100.0
