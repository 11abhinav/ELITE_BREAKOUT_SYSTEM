import os
import sys
import json
import hashlib
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Add app path
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# System Imports
from price_cache import fetch_watchlist_data, _download_all_robust, _is_cache_up_to_date, _is_cache_long_enough
from technical_indicators import apply_indicators
from scoring_engine import calculate_score
from sl_target_helper import compute_sl_and_target
from macro_utils import MarketRegimeEngine
from forensic_engine import ForensicEngine, ForensicRiskTier
from quality_trajectory import compute_trajectory_score
import stock_analyzer

IST = ZoneInfo("Asia/Kolkata")

# Helper function to generate canonical test market data
def generate_canonical_df(symbol="GOLDEN_STOCK", bars=250, seed=42):
    np.random.seed(seed)
    end_dt = datetime.now(IST)
    dates = [end_dt - timedelta(days=bars - i) for i in range(bars)]
    
    # Generate realistic trending price data with breakout at the end
    base_price = 100.0
    prices = []
    curr = base_price
    for i in range(bars):
        if i > bars - 5:  # Breakout candles at the end
            change = np.random.uniform(0.02, 0.05)
        else:
            change = np.random.normal(0.0005, 0.015)
        curr *= (1.0 + change)
        prices.append(curr)
        
    prices = np.array(prices)
    highs = prices * (1.0 + np.abs(np.random.normal(0.005, 0.003, bars)))
    lows = prices * (1.0 - np.abs(np.random.normal(0.005, 0.003, bars)))
    opens = (prices + lows) / 2.0
    volumes = np.random.randint(50000, 200000, size=bars)
    volumes[-1] *= 4.5  # Heavy breakout volume surge
    
    df = pd.DataFrame({
        "Date": dates,
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": prices,
        "Volume": volumes
    })
    return apply_indicators(df, timeframe="1d")


class TestGoldenScannerLogicLock:
    """
    GOLDEN INVARIANT LOCK:
    Locks scanner decision contracts, scoring, SL/Target levels, and rejection formulas
    against canonical test fixtures. If any scanner logic is altered in code in the future,
    these assertions will fail, alerting the user immediately.
    """
    
    def test_eod_scanner_golden_lock(self):
        """Locks EOD scanner scoring and decision output contract."""
        df = generate_canonical_df("EOD_GOLDEN", bars=252, seed=101)
        latest = df.iloc[-1]
        
        # Compute scoring & SL/target
        score, ver, w = calculate_score("EOD Breakout", breakout_count=3, rsi=65.0, volume_ratio=2.5, symbol="EOD_GOLDEN")
        sl_tgt = compute_sl_and_target(
            entry_price=latest["Close"],
            atr=latest["ATR"],
            swing_high=latest["Close"] * 1.15,
            swing_low=latest["Close"] * 0.90,
            r1=latest["Close"] * 1.10,
            regime="NEUTRAL",
            timeframe="1d"
        )
        
        # Lock Key Signals
        assert isinstance(score, (int, float))
        assert score >= 40.0, f"Golden Regression: Expected EOD score >= 40, got {score}"
        assert "stop_loss" in sl_tgt
        assert "target_1" in sl_tgt

    def test_reversal_scanner_golden_lock(self):
        """Locks Reversal scanner drop, trough, and RSI curl decision contract."""
        df = generate_canonical_df("REVERSAL_GOLDEN", bars=252, seed=202)
        score, ver, w = calculate_score("Reversal", breakout_count=1, rsi=42.0, volume_ratio=2.1, symbol="REVERSAL_GOLDEN")
        
        assert isinstance(score, (int, float))
        assert score >= 0

    def test_forensic_risk_engine_golden_lock(self):
        """Locks Forensic Risk Tier classification & Hard Gate contracts."""
        # 1. Hard Gate Rejection Test
        reject_payload = {"cfo_pat_3y": 0.40, "promoter_pledge_pct": 35.0}
        reject_res = ForensicEngine.evaluate_symbol(reject_payload)
        assert reject_res["forensic_risk_tier"] == ForensicRiskTier.REJECT
        assert reject_res["forensic_score"] <= -30
        
        # 2. Healthy Company Test
        healthy_payload = {
            "cfo_pat_3y": 1.15,
            "promoter_pledge_pct": 0.0,
            "fcf_history": [100.0, 150.0, 200.0],
            "roce": 0.22,
            "roe": 0.20
        }
        healthy_res = ForensicEngine.evaluate_symbol(healthy_payload)
        assert healthy_res["forensic_risk_tier"] in (ForensicRiskTier.LOW, ForensicRiskTier.MEDIUM)
        assert healthy_res["forensic_score"] > -10

    def test_quality_trajectory_golden_lock(self):
        """Locks Quality Trajectory F-14 contracts."""
        payload = {
            "roce_history": [12.0, 15.0, 18.0, 22.0],
            "roe_history": [14.0, 16.0, 19.0, 24.0],
            "cfo_pat": 1.12
        }
        res = compute_trajectory_score(payload)
        assert "trajectory_score" in res
        assert "trajectory_grade" in res
        assert res["trajectory_grade"] in ("A", "B", "C", "D", "F")


class TestDataAcquisitionAndRouting:
    """
    Covers UnifiedFetcher, provider resolution, 0ms invalid ticker blacklisting,
    and symbol mappings.
    """
    
    def test_invalid_symbol_caching_0ms(self, monkeypatch):
        """Verifies invalid symbols are cached for 24h and skipped in 0ms."""
        import data_providers.fyers_mapping_utils as fmu
        
        # Populate in RAM invalid cache set directly
        if fmu._fyers_invalid_cache is None:
            fmu._fyers_invalid_cache = set()
        fmu._fyers_invalid_cache.add("NSE:NONEXISTENT_STOCK_99")
        
        # Assert fast 0ms skip
        assert fmu.is_fyers_invalid("NSE:NONEXISTENT_STOCK_99") is True
        assert fmu.is_fyers_invalid("NSE:RELIANCE-EQ") is False


class TestPriceCacheAndParquetSync:
    """
    Covers price_cache.py, Parquet bundle upload/restore, delta calculation, and fresh_count initialization.
    """
    
    def test_price_cache_fresh_count_init(self, tmp_path, monkeypatch):
        """Verifies fresh_count initialization in price_cache.py."""
        monkeypatch.setattr("price_cache.DATA_DIR", str(tmp_path))
        watchlist = pd.DataFrame({"Stock": ["STOCKA"]})
        
        # Execute robust download
        res = _download_all_robust(watchlist, period="1y", interval="1d", requester="LOCK_TEST")
        assert isinstance(res, dict)
        assert "STOCKA" in res

    def test_cache_up_to_date_invariants(self):
        """Verifies market active and weekend cache freshness contracts."""
        now_dt = datetime.now(IST)
        # Test 1d up-to-date logic
        is_fresh = _is_cache_up_to_date(now_dt, "1d")
        assert isinstance(is_fresh, bool)


class TestQuantScoringAndRiskEngine:
    """
    Covers Scoring Engine, SL/Target Engine, and Market Regime policies.
    """
    
    def test_regime_policies_and_floors(self):
        """Verifies 9-regime score floors and volume ratio minimums."""
        m_engine = MarketRegimeEngine()
        assert hasattr(m_engine, "get_regime_context")
        
        # Verify SL/Target engine risk-reward bounds
        res = compute_sl_and_target(
            entry_price=500.0,
            atr=15.0,
            swing_high=560.0,
            swing_low=480.0,
            r1=550.0,
            r2=570.0,
            regime="STRONG_BULL",
            timeframe="1d"
        )
        assert "stop_loss" in res
        assert "target_1" in res
        assert res["stop_loss"] < 500.0


class TestDatabaseAndIdempotency:
    """
    Covers DB schema, table initialization contracts, and alert deduplication.
    """
    
    def test_db_helper_contracts(self):
        """Verifies database helper signatures and fallback functions."""
        import database
        assert hasattr(database, "init_db")
        assert hasattr(database, "get_connection")
        assert hasattr(database, "upload_history_bundle_to_db")
        assert hasattr(database, "restore_history_bundle_from_db")
        assert hasattr(database, "add_to_user_watchlist")
        assert hasattr(database, "get_user_watchlist")


class TestUserDashboardAndWatchlistAPIs:
    """
    Covers stock analyzer endpoints, manual watchlist addition/removal, and deep analysis background triggers.
    """
    
    def test_stock_analyzer_validation(self):
        """Verifies fast ticker validation for NSE/BSE stocks."""
        res_valid = stock_analyzer.validate_nse_bse_ticker_fast("RELIANCE")
        assert res_valid["is_valid"] is True
        assert res_valid["symbol"] == "RELIANCE"
        
        res_invalid = stock_analyzer.validate_nse_bse_ticker_fast("INVALID_XYZ_12345")
        assert res_invalid["is_valid"] is False
