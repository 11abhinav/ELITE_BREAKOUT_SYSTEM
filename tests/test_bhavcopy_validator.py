import pytest
import pandas as pd
from app.validation.validators.bhavcopy_validator import BhavcopyValidator
from app.validation.scoring.bhavcopy_score import BhavcopyScoreCalculator
from app.validation.context import ValidationContext
from app.validation.codes import FailureCode

# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def base_bhavcopy():
    # A golden dataset for Bhavcopy
    data = {
        "SYMBOL": ["RELIANCE", "TCS", "INFY"],
        "SERIES": ["EQ", "EQ", "EQ"],
        "OPEN": [2500.0, 3500.0, 1500.0],
        "HIGH": [2550.0, 3550.0, 1520.0],
        "LOW": [2480.0, 3480.0, 1490.0],
        "CLOSE": [2520.0, 3520.0, 1510.0],
        "LAST": [2520.0, 3520.0, 1510.0],
        "PREVCLOSE": [2490.0, 3490.0, 1495.0],
        "TOTTRDQTY": [1000000, 500000, 2000000],
        "TOTTRDVAL": [2500000000, 1750000000, 3000000000],
        "TIMESTAMP": ["2023-10-25", "2023-10-25", "2023-10-25"],
        "TOTALTRADES": [50000, 25000, 100000],
        "ISIN": ["INE002A01018", "INE467B01029", "INE009A01021"]
    }
    df = pd.DataFrame(data)
    
    extra_data = []
    for i in range(4, 101):
        extra_data.append({
            "SYMBOL": f"SYM{i}",
            "SERIES": "EQ",
            "OPEN": 100.0, "HIGH": 105.0, "LOW": 95.0, "CLOSE": 102.0,
            "LAST": 102.0, "PREVCLOSE": 99.0, 
            "TOTTRDQTY": 1000, "TOTTRDVAL": 102000,
            "TIMESTAMP": "2023-10-25", "TOTALTRADES": 50,
            "ISIN": f"INE{i:03d}"
        })
    df = pd.concat([df, pd.DataFrame(extra_data)], ignore_index=True)
    return df

@pytest.fixture
def context():
    return ValidationContext(provider="NSE_BHAVCOPY")

@pytest.fixture
def validator():
    return BhavcopyValidator()

# ==============================================================================
# Valid Dataset Tests
# ==============================================================================

def test_bhavcopy_perfect_dataset(validator, base_bhavcopy, context):
    result = validator.validate(base_bhavcopy, context)
    assert result.is_valid is True
    assert len(result.schema_failures) == 0
    assert len(result.business_failures) == 0
    assert len(result.historical_failures) == 0
    assert result.metrics.row_count == 100

# ==============================================================================
# Schema Failure Tests
# ==============================================================================

def test_bhavcopy_missing_columns(validator, base_bhavcopy, context):
    df = base_bhavcopy.drop(columns=["OPEN", "CLOSE"])
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)

def test_bhavcopy_duplicate_columns(validator, base_bhavcopy, context):
    # Pandas allows duplicate column names, so we construct one
    df = base_bhavcopy.copy()
    df.columns = ["SYMBOL", "SYMBOL"] + list(df.columns)[2:] # Create duplicate SYMBOL column
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)
    assert "Duplicate column names" in result.schema_failures[0].message

def test_bhavcopy_wrong_type(validator, base_bhavcopy, context):
    df = base_bhavcopy.copy()
    df["HIGH"] = "Not a number"
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH002 for f in result.schema_failures)

# ==============================================================================
# Business Failure Tests
# ==============================================================================

def test_bhavcopy_business_violations_high_low(validator, base_bhavcopy, context):
    df = base_bhavcopy.copy()
    for i in range(0, 15): # Corrupt > 10%
        df.loc[i, "LOW"] = df.loc[i, "HIGH"] + 10.0
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_bhavcopy_business_violations_open_last_bounds(validator, base_bhavcopy, context):
    df = base_bhavcopy.copy()
    for i in range(0, 15):
        df.loc[i, "OPEN"] = df.loc[i, "HIGH"] + 10.0 # Open outside High
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_bhavcopy_business_violations_negative_values(validator, base_bhavcopy, context):
    df = base_bhavcopy.copy()
    for i in range(0, 15):
        df.loc[i, "PREVCLOSE"] = -10.0 # Negative previous close
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

# ==============================================================================
# Dataset-Level Anomaly (Historical) Tests
# ==============================================================================

def test_bhavcopy_duplicate_symbols(validator, base_bhavcopy, context):
    df = base_bhavcopy.copy()
    duplicate_row = df.iloc[[0]].copy()
    df = pd.concat([df, duplicate_row], ignore_index=True)
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS003 for f in result.historical_failures)
    assert result.metrics.duplicate_rows == 1

def test_bhavcopy_duplicate_isins(validator, base_bhavcopy, context):
    df = base_bhavcopy.copy()
    df.loc[1, "ISIN"] = df.loc[0, "ISIN"] # 2 rows with same ISIN
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS003 for f in result.historical_failures)
    assert "Duplicate ISINs" in result.historical_failures[0].message

def test_bhavcopy_symbol_count_regression(validator, base_bhavcopy, context):
    # Truncate to just 10 symbols
    df = base_bhavcopy.head(10)
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS001 for f in result.historical_failures)
    assert "Unexpected symbol count regression" in result.historical_failures[0].message

# ==============================================================================
# Scoring Logic Tests
# ==============================================================================

def test_bhavcopy_score_calculation(validator, base_bhavcopy, context):
    score_calc = BhavcopyScoreCalculator()
    
    # Perfect score
    result = validator.validate(base_bhavcopy, context)
    score = score_calc.calculate(result)
    assert score == 100.0
    
    # Penalize for minor invalid prices (under the 10% threshold to not trigger a critical failure)
    df = base_bhavcopy.copy()
    df.loc[0, "CLOSE"] = -10.0 # 1 invalid price out of 100
    result = validator.validate(df, context)
    score = score_calc.calculate(result)
    assert result.is_valid is True
    assert score < 100.0
    assert score == 95.0
