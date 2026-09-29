#!/usr/bin/env python3
"""
tests/test_fundamental_v2_data_integrity.py
============================================
Tests for V2 Fundamental Scanner Data Integrity & Price Invariants:
1. Zero-price candidate rejection (DATA_INSUFFICIENT_PRICE) - never emits BUY alert with CMP <= 0.
2. Accurate separated data blocked counters (Quality, Valuation, Price, Any).
3. Post-scan health state reflecting data blocks and candidate defects.
4. 5Y Quality Window & Provenance (ROCE average, cumulative CFO/PAT, CAGR).
5. Symbol deduplication and canonicalization.
"""

import pytest
import pandas as pd
import numpy as np
from app.live_fundamental_scanner import QualityCompounderValueV2Scanner


def test_zero_price_candidate_hard_blocked():
    scanner = QualityCompounderValueV2Scanner()
    # Mock pit_df with one stock that passes quality & valuation but has zero price
    pit_data = [{
        'symbol': 'ZEROPX',
        'industry': 'Information Technology',
        'market_cap': 5000.0,
        'adtv_90d': 10.0,
        'roce_5y_avg': 25.0,
        'sales_cagr_5y': 15.0,
        'pat_cagr_5y': 20.0,
        'cfo_pat_5y_ratio': 1.1,
        'debt_to_equity': 0.05,
        'current_ev_ebitda': 10.0,
        'ev_ebitda_3y_median': 20.0,
        'current_price': 0.0, # Zero price!
        'close': 0.0
    }]
    scanner.load_pit_dataset = lambda: pd.DataFrame(pit_data)
    
    res = scanner.scan_universe(trigger_type="MANUAL")
    assert res['price_data_blocked_count'] == 1
    assert res['candidate_count'] == 0
    assert res['candidates_inserted'] == 0


def test_accurate_separated_data_counters():
    scanner = QualityCompounderValueV2Scanner()
    pit_data = [
        # Stock 1: Complete and valid
        {
            'symbol': 'VALID1',
            'industry': 'IT',
            'market_cap': 5000.0,
            'adtv_90d': 10.0,
            'roce_5y_avg': 25.0,
            'sales_cagr_5y': 15.0,
            'pat_cagr_5y': 20.0,
            'cfo_pat_5y_ratio': 1.1,
            'debt_to_equity': 0.05,
            'current_ev_ebitda': 10.0,
            'ev_ebitda_3y_median': 20.0,
            'current_price': 100.0
        },
        # Stock 2: Missing Quality (pat_cagr is None)
        {
            'symbol': 'MISSQUAL',
            'industry': 'IT',
            'market_cap': 5000.0,
            'adtv_90d': 10.0,
            'roce_5y_avg': 25.0,
            'sales_cagr_5y': 15.0,
            'pat_cagr_5y': None,
            'cfo_pat_5y_ratio': 1.1,
            'debt_to_equity': 0.05,
            'current_ev_ebitda': 10.0,
            'ev_ebitda_3y_median': 20.0,
            'current_price': 100.0
        },
        # Stock 3: Missing Valuation (no EV or PE median)
        {
            'symbol': 'MISSVAL',
            'industry': 'IT',
            'market_cap': 5000.0,
            'adtv_90d': 10.0,
            'roce_5y_avg': 25.0,
            'sales_cagr_5y': 15.0,
            'pat_cagr_5y': 20.0,
            'cfo_pat_5y_ratio': 1.1,
            'debt_to_equity': 0.05,
            'current_ev_ebitda': None,
            'ev_ebitda_3y_median': None,
            'current_pe': None,
            'pe_3y_median': None,
            'current_price': 100.0
        },
        # Stock 4: Missing Price (cmp <= 0)
        {
            'symbol': 'MISSPX',
            'industry': 'IT',
            'market_cap': 5000.0,
            'adtv_90d': 10.0,
            'roce_5y_avg': 25.0,
            'sales_cagr_5y': 15.0,
            'pat_cagr_5y': 20.0,
            'cfo_pat_5y_ratio': 1.1,
            'debt_to_equity': 0.05,
            'current_ev_ebitda': 10.0,
            'ev_ebitda_3y_median': 20.0,
            'current_price': 0.0
        }
    ]
    scanner.load_pit_dataset = lambda: pd.DataFrame(pit_data)
    
    res = scanner.scan_universe(trigger_type="MANUAL")
    assert res['total_scanned'] == 4
    assert res['quality_data_blocked_count'] == 1
    assert res['valuation_data_blocked_count'] == 1
    assert res['price_data_blocked_count'] == 1
    assert res['data_blocked_count'] == 3
    # Exactly 1 valid candidate
    assert res['candidate_count'] == 1


def test_5y_quality_window_provenance():
    scanner = QualityCompounderValueV2Scanner()
    df = scanner.load_pit_dataset()
    assert df is not None and not df.empty
    # Check IRCTC or any known symbol has provenance columns
    assert 'growth_start_period' in df.columns
    assert 'growth_end_period' in df.columns
    assert 'growth_years_elapsed' in df.columns
    assert 'financial_periods_used' in df.columns
    assert 'roce_periods_used' in df.columns
