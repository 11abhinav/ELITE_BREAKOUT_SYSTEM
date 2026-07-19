import pytest
import pandas as pd
from datetime import datetime, timedelta
from app.validation.validators.corporate_actions_validator import CorporateActionsValidator
from app.validation.scoring.corporate_actions_score import CorporateActionsScoreCalculator
from app.validation.context import ValidationContext
from app.validation.codes import FailureCode

# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def base_actions():
    # A golden normalized dataset for Corporate Actions (Time-Series for 1 symbol)
    data = [
        {"EX_DATE": "2020-05-10", "PURPOSE": "DIVIDEND", "NUMERATOR": 5.0, "DENOMINATOR": 1.0, "SYMBOL": "RELIANCE"},
        {"EX_DATE": "2021-09-08", "PURPOSE": "BONUS", "NUMERATOR": 1.0, "DENOMINATOR": 1.0, "SYMBOL": "RELIANCE"},
        {"EX_DATE": "2023-10-25", "PURPOSE": "SPLIT", "NUMERATOR": 10.0, "DENOMINATOR": 2.0, "SYMBOL": "RELIANCE"}
    ]
    return pd.DataFrame(data)

@pytest.fixture
def context():
    return ValidationContext(provider="NSE_CORP_ACT")

@pytest.fixture
def validator():
    return CorporateActionsValidator()

# ==============================================================================
# Valid Dataset Tests
# ==============================================================================

def test_actions_perfect_dataset(validator, base_actions, context):
    result = validator.validate(base_actions, context)
    assert result.is_valid is True
    assert len(result.schema_failures) == 0
    assert len(result.business_failures) == 0
    assert len(result.historical_failures) == 0
    assert result.metrics.row_count == 3

# ==============================================================================
# Schema Failure Tests
# ==============================================================================

def test_actions_missing_columns(validator, base_actions, context):
    df = base_actions.drop(columns=["NUMERATOR"])
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)

def test_actions_duplicate_columns(validator, base_actions, context):
    df = base_actions.copy()
    cols = list(df.columns)
    cols[1] = cols[0]
    df.columns = cols
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)

# ==============================================================================
# Business Failure Tests
# ==============================================================================

def test_actions_zero_denominator(validator, base_actions, context):
    df = base_actions.copy()
    df.loc[0, "DENOMINATOR"] = 0.0 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_actions_invalid_purpose(validator, base_actions, context):
    df = base_actions.copy()
    df.loc[1, "PURPOSE"] = "UNKNOWN_ACTION" 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

# ==============================================================================
# Historical/Time-Series Tests
# ==============================================================================

def test_actions_duplicate_ex_date_and_purpose(validator, base_actions, context):
    df = base_actions.copy()
    duplicate_row = df.iloc[[0]].copy()
    df = pd.concat([df, duplicate_row], ignore_index=True)
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS003 for f in result.historical_failures)

def test_actions_non_monotonic_dates(validator, base_actions, context):
    df = base_actions.copy()
    # Swap dates to break monotonicity
    temp = df.loc[0, "EX_DATE"]
    df.loc[0, "EX_DATE"] = df.loc[2, "EX_DATE"]
    df.loc[2, "EX_DATE"] = temp
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS002 for f in result.historical_failures)

def test_actions_future_dates_allowed(validator, base_actions, context):
    """
    Test that future EX_DATEs are valid for corporate actions.
    """
    df = base_actions.copy()
    future_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    
    new_row = pd.DataFrame([{
        "EX_DATE": future_date, 
        "PURPOSE": "DIVIDEND", 
        "NUMERATOR": 2.0, 
        "DENOMINATOR": 1.0, 
        "SYMBOL": "RELIANCE"
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    
    result = validator.validate(df, context)
    assert result.is_valid is True
    assert len(result.historical_failures) == 0

# ==============================================================================
# Quality Degradation Tests
# ==============================================================================

def test_actions_missing_optional_row(validator, base_actions, context):
    """
    Test that missing SYMBOL (optional to framework) drops score.
    """
    df = base_actions.copy()
    df.loc[0, "SYMBOL"] = pd.NA
    
    result = validator.validate(df, context)
    assert result.is_valid is True
    
    score_calc = CorporateActionsScoreCalculator()
    score = score_calc.calculate(result)
    assert score < 100.0
