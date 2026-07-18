import pytest
import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath('app'))
from data_quality import DataQualityValidator, ExpectedRowEstimator, MarketData
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

def test_expected_rows():
    assert ExpectedRowEstimator.estimate("1y", "1d") == 252
    assert ExpectedRowEstimator.estimate("3mo", "1d") == 63
    
def test_schema_validation():
    # Missing Volume
    df = pd.DataFrame({"Open": [1], "High": [2], "Low": [1], "Close": [2]})
    report = DataQualityValidator.validate(df, "1mo", "1d")
    assert report.is_valid == False
    assert report.schema_valid == False
    assert "Missing required columns" in report.reason

def test_type_validation():
    df = pd.DataFrame({
        "Open": ["str"], "High": [2.0], "Low": [1.0], "Close": [2.0], "Volume": [100],
        "Date": [pd.Timestamp.now()]
    })
    report = DataQualityValidator.validate(df, "1mo", "1d")
    assert report.is_valid == False
    assert report.types_valid == False

def test_quality_score_good_data():
    dates = pd.date_range(end=pd.Timestamp.now(), periods=250, freq="B").tz_localize(IST)
    df = pd.DataFrame({
        "Date": dates,
        "Open": np.random.uniform(100, 200, 250),
        "High": np.random.uniform(200, 250, 250),
        "Low": np.random.uniform(50, 100, 250),
        "Close": np.random.uniform(100, 200, 250),
        "Volume": np.random.randint(1000, 5000, 250)
    })
    report = DataQualityValidator.validate(df, "1y", "1d")
    assert report.is_valid == True
    assert report.quality_score > 80

def test_quality_score_poor_data():
    dates = pd.date_range(start="2023-01-01", periods=1, freq="B").tz_localize(IST)
    df = pd.DataFrame({
        "Date": dates,
        "Open": [100.0],
        "High": [120.0],
        "Low": [90.0],
        "Close": [110.0],
        "Volume": [1000]
    })
    # 1 row for 1y request -> should fail completely on completeness
    report = DataQualityValidator.validate(df, "1y", "1d")
    print("Poor data score:", report.quality_score)
    # The freshness penalty will be huge because the date is 2023, while now is 2026.
    assert report.quality_score < 40

if __name__ == "__main__":
    pytest.main(["-v", "tests/test_data_quality.py"])
