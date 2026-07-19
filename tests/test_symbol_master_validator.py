import pytest
import pandas as pd
from datetime import datetime, timedelta
from app.validation.validators.symbol_master_validator import SymbolMasterValidator
from app.validation.scoring.symbol_master_score import SymbolMasterScoreCalculator
from app.validation.context import ValidationContext
from app.validation.codes import FailureCode

# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def base_symbol_master():
    # A golden dataset for Symbol Master Snapshot
    data = []
    for i in range(1, 101):
        data.append({
            "SYMBOL": f"SYM{i}",
            "ISIN": f"INE{i:03d}",
            "SERIES": "EQ",
            "LOT_SIZE": 1,
            "FACE_VALUE": 10.0,
            "LISTING_DATE": "2010-01-01"
        })
    return pd.DataFrame(data)

@pytest.fixture
def context():
    return ValidationContext(provider="NSE_MASTER")

@pytest.fixture
def validator():
    return SymbolMasterValidator()

# ==============================================================================
# Valid Dataset Tests
# ==============================================================================

def test_master_perfect_dataset(validator, base_symbol_master, context):
    result = validator.validate(base_symbol_master, context)
    assert result.is_valid is True
    assert len(result.schema_failures) == 0
    assert len(result.business_failures) == 0
    assert len(result.historical_failures) == 0
    assert result.metrics.row_count == 100

# ==============================================================================
# Schema Failure Tests
# ==============================================================================

def test_master_missing_mandatory_values(validator, base_symbol_master, context):
    df = base_symbol_master.copy()
    df.loc[0, "SYMBOL"] = "" # Empty symbol
    df.loc[1, "ISIN"] = pd.NA
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)

def test_master_duplicate_columns(validator, base_symbol_master, context):
    df = base_symbol_master.copy()
    # Force the second column to have the same name as the first
    cols = list(df.columns)
    cols[1] = cols[0]
    df.columns = cols
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.SCH001 for f in result.schema_failures)

# ==============================================================================
# Business Failure Tests
# ==============================================================================

def test_master_negative_face_value(validator, base_symbol_master, context):
    df = base_symbol_master.copy()
    df.loc[0:15, "FACE_VALUE"] = -5.0 # Corrupt 15% to trigger BUS002
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_master_invalid_series(validator, base_symbol_master, context):
    df = base_symbol_master.copy()
    df.loc[0:15, "SERIES"] = "INVALID_SERIES_XX" 
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

def test_master_absurd_listing_date(validator, base_symbol_master, context):
    df = base_symbol_master.copy()
    future_date = (datetime.now() + timedelta(days=5000)).strftime("%Y-%m-%d")
    df.loc[0, "LISTING_DATE"] = future_date
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.BUS002 for f in result.business_failures)

# ==============================================================================
# Cross-Section / Historical Failure Tests
# ==============================================================================

def test_master_duplicate_symbols(validator, base_symbol_master, context):
    # Same SYMBOL, Different ISIN (e.g. corruption)
    df = base_symbol_master.copy()
    duplicate_row = df.iloc[[0]].copy()
    duplicate_row["ISIN"] = "INE_NEW_FAKE"
    df = pd.concat([df, duplicate_row], ignore_index=True)
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS003 for f in result.historical_failures)

def test_master_dataset_size_anomaly(validator, base_symbol_master, context):
    # Only 10 symbols
    df = base_symbol_master.head(10)
    result = validator.validate(df, context)
    assert result.is_valid is False
    assert any(f.code == FailureCode.HIS001 for f in result.historical_failures)

# ==============================================================================
# Rename Scenario & Scoring Tests
# ==============================================================================

def test_master_duplicate_isin_renaming_scenario(validator, base_symbol_master, context):
    """
    Test renaming scenario: Same ISIN, Different SYMBOL.
    Should NOT fail validation, but should reduce score.
    """
    df = base_symbol_master.copy()
    
    # Old symbol: ABC, New symbol: XYZ. Same ISIN.
    df.loc[0, "SYMBOL"] = "ABC"
    df.loc[0, "ISIN"] = "INE_SHARED"
    
    new_row = df.iloc[[0]].copy()
    new_row["SYMBOL"] = "XYZ"
    
    df = pd.concat([df, new_row], ignore_index=True)
    
    result = validator.validate(df, context)
    # Does not fail structural or business checks
    assert result.is_valid is True
    
    # Score should degrade
    score_calc = SymbolMasterScoreCalculator()
    score = score_calc.calculate(result)
    assert score < 100.0
