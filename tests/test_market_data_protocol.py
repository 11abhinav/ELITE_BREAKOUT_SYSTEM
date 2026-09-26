#!/usr/bin/env python3
"""
UNIT TEST: MANDATORY REAL-MARKET-DATA BACKTEST PROTOCOL
=======================================================
Validates the data provenance and integrity verification gates in MarketDataProtocol.
"""

import os
import sys
import pytest
import pandas as pd

# Add repo root to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.production.market_data_protocol import MarketDataProtocol, AUTHORIZED_DATA_PROVIDER


def test_valid_upstox_dataset_verification():
    """Verify that a compliant Upstox dataset passes provenance checks and generates audit block."""
    df = pd.DataFrame({
        "timestamp": ["2026-09-22 09:15:00", "2026-09-23 09:15:00", "2026-09-24 09:15:00", "2026-09-25 09:15:00"],
        "open": [100.0, 102.0, 105.0, 108.0],
        "high": [103.0, 106.0, 109.0, 112.0],
        "low": [99.0, 101.0, 104.0, 107.0],
        "close": [102.0, 105.0, 108.0, 111.0],
        "volume": [500000, 600000, 550000, 750000],
        "open_interest": [10000, 11000, 12000, 13000]
    })

    audit_meta = MarketDataProtocol.verify_dataset_provenance(
        symbol="SBIN",
        filepath_or_df=df,
        timeframe="1d",
        provider="UPSTOX",
        allow_certified_cache=True
    )

    assert audit_meta["provider"] == "UPSTOX"
    assert audit_meta["provenance_status"] == "CERTIFIED"
    assert audit_meta["row_count"] == 4
    assert audit_meta["synthetic_data"] is False
    assert len(audit_meta["dataset_hash"]) == 64

    # Verify audit report section generation
    audit_report = MarketDataProtocol.generate_audit_report_section(audit_meta)
    assert "### DATA PROVENANCE" in audit_report
    assert "Provider: UPSTOX" in audit_report
    assert "PROVENANCE_STATUS = CERTIFIED" in audit_report


def test_rejection_of_non_upstox_providers():
    """Ensure any non-Upstox provider is strictly blocked from backtesting."""
    df = pd.DataFrame({
        "timestamp": ["2026-09-25 09:15:00"],
        "open": [100.0], "high": [105.0], "low": [98.0], "close": [102.0], "volume": [1000]
    })

    for bad_provider in ["YAHOO", "YAHOO_FINANCE", "TRADINGVIEW", "SYNTHETIC", "SIMULATED", "OTHER"]:
        with pytest.raises(RuntimeError, match="CERTIFICATION BLOCKED: NON-UPSTOX DATA"):
            MarketDataProtocol.verify_dataset_provenance(
                symbol="TCS",
                filepath_or_df=df,
                provider=bad_provider
            )


def test_rejection_of_missing_native_fields():
    """Ensure missing native exchange fields trigger immediate backtest failure."""
    # Missing 'volume'
    df_no_vol = pd.DataFrame({
        "timestamp": ["2026-09-25 09:15:00"],
        "open": [100.0], "high": [105.0], "low": [98.0], "close": [102.0]
    })
    with pytest.raises(RuntimeError, match="Missing required native exchange fields"):
        MarketDataProtocol.verify_dataset_provenance(
            symbol="INFY",
            filepath_or_df=df_no_vol,
            provider="UPSTOX"
        )


def test_rejection_of_duplicate_timestamps():
    """Ensure duplicate bars trigger immediate backtest failure."""
    df_dups = pd.DataFrame({
        "timestamp": ["2026-09-25 09:15:00", "2026-09-25 09:15:00"],
        "open": [100.0, 100.0], "high": [105.0, 105.0],
        "low": [98.0, 98.0], "close": [102.0, 102.0], "volume": [1000, 1000]
    })
    with pytest.raises(RuntimeError, match="duplicate timestamps detected"):
        MarketDataProtocol.verify_dataset_provenance(
            symbol="LT",
            filepath_or_df=df_dups,
            provider="UPSTOX"
        )


def test_rejection_of_fabricated_open_interest():
    """Ensure negative / synthetic open interest values are rejected."""
    df_bad_oi = pd.DataFrame({
        "timestamp": ["2026-09-25 09:15:00"],
        "open": [100.0], "high": [105.0], "low": [98.0], "close": [102.0],
        "volume": [1000],
        "open_interest": [-500]  # Impossible native OI
    })
    with pytest.raises(RuntimeError, match="Negative open interest detected"):
        MarketDataProtocol.verify_dataset_provenance(
            symbol="NIFTY_FUT",
            filepath_or_df=df_bad_oi,
            provider="UPSTOX"
        )


if __name__ == "__main__":
    pytest.main(["-v", __file__])
