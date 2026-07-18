import pytest
import json
import os
import pandas as pd
from app.sl_target_helper import compute_sl_and_target

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
os.makedirs(FIXTURE_DIR, exist_ok=True)

def _normalize_data(data):
    if isinstance(data, dict):
        return {k: _normalize_data(v) for k, v in sorted(data.items())}
    elif isinstance(data, list):
        # We can't sort everything (e.g. lists of dicts without a clear key)
        # But we can at least normalize elements. We will attempt to sort if it's a list of dicts with 'cluster_id' or 'price'.
        normalized_list = [_normalize_data(x) for x in data]
        try:
            if all(isinstance(x, dict) and "cluster_id" in x for x in normalized_list):
                return sorted(normalized_list, key=lambda x: x["cluster_id"])
            elif all(isinstance(x, dict) and "price" in x for x in normalized_list):
                return sorted(normalized_list, key=lambda x: (x.get("price", 0), x.get("source", "")))
        except:
            pass
        return normalized_list
    elif isinstance(data, float):
        return round(data, 2)
    return data

@pytest.fixture
def sample_ticker():
    # Deterministic historical candles
    df = pd.DataFrame({
        "Close": [90, 92, 95, 94, 98, 100],
        "High":  [91, 93, 96, 95, 99, 101],
        "Low":   [89, 90, 93, 92, 96, 98],
        "SWING_LOW": [None, 90, None, 92, None, None],
        "SWING_HIGH": [None, None, 96, None, 99, None],
    })
    return df

@pytest.fixture
def base_context(sample_ticker):
    return {
        "entry_price": 100.0, "entry": 100.0, "candle_range": 3.0,
        "ticker": sample_ticker,
        "adx": 35.0,
        "volume_ratio": 2.5,
        "vwap": 98.0,
        "macro_regime": "BULL",
        "swing_low": 92.0,
        "swing_high": 99.0,
        "swing_low_raw": 92.0,
        "swing_high_raw": 99.0,
        "r1": 102.0,
        "r2": 105.0,
        "bb_upper": 103.0,
        "prior_20d_high": 104.0,
        "high_52w": 110.0,
        "prev_day_high": 101.0,
        "sma50": 90.0,
        "sma200": 85.0,
        "atr": 2.0,
        "atr_pct": 0.02
    }

def run_snapshot_test(scanner_name, context, snapshot_filename):
    res = compute_sl_and_target(mode=scanner_name, engine_version="v7.0", **context)
    normalized = _normalize_data(res)
    
    filepath = os.path.join(FIXTURE_DIR, snapshot_filename)
    if not os.path.exists(filepath):
        with open(filepath, "w") as f:
            json.dump(normalized, f, indent=4)
    
    with open(filepath, "r") as f:
        expected = json.load(f)
    
    assert normalized == expected, f"Snapshot mismatch for {scanner_name}. Delete {filepath} to update snapshot."

def test_multi_tf_snapshot(base_context):
    run_snapshot_test("MULTI_TF", base_context, "multi_tf_snapshot.json")

def test_eod_snapshot(base_context):
    run_snapshot_test("EOD", base_context, "eod_snapshot.json")

def test_reversal_snapshot(base_context):
    base_context["macro_regime"] = "BEAR" # Reversals usually act differently in different regimes
    run_snapshot_test("REVERSAL", base_context, "reversal_snapshot.json")

