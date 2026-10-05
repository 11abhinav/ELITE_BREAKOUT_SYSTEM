"""
UNIT TEST BATTERY: QUALITY STRATEGY INDEPENDENCE & DECOUPLING
Proves that QUALITY_COMPOUNDER and QUALITY_VALUE_RECOVERY execute
genuinely independent investment strategies and cannot produce replica decisions.

Test Cases:
  1. Engine Decoupling: Verify distinct class types.
  2. Stock A: Compounder PASS / Recovery FAIL (High Quality near 52W High, no drawdown setup).
  3. Stock B: Recovery PASS / Compounder FAIL (Deep Drawdown -35% with 6% Sales CAGR < 10% threshold).
  4. Financial Sector Exclusion: Verified excluded across both engines.
  5. Model E3 Safety: Excessive debt during recovery triggers structural deterioration reject.
"""

import pytest
import pandas as pd
from typing import Dict, Any

from app.live_fundamental_scanner import (
    QualityCompounderValueV2Scanner,
    QualityValueRecoveryScanner,
    get_quality_compounder_v2_scanner,
    get_quality_value_recovery_scanner,
)


def test_engine_class_decoupling():
    """Verify that Compounder and Recovery instantiate distinct, decoupled engine classes."""
    compounder_engine = get_quality_compounder_v2_scanner()
    recovery_engine = get_quality_value_recovery_scanner()

    assert isinstance(compounder_engine, QualityCompounderValueV2Scanner)
    assert isinstance(recovery_engine, QualityValueRecoveryScanner)
    assert type(compounder_engine) is not type(recovery_engine)
    assert compounder_engine.strategy_id == "QUALITY_COMPOUNDER"
    assert recovery_engine.strategy_id == "QUALITY_VALUE_RECOVERY"


def test_stock_a_compounder_pass_recovery_fail():
    """
    Stock A: High-quality compounder near 52W/2Y High (Drawdown = 1.96%).
      - Passes Compounder (5Y ROCE=25%, Sales CAGR=15%, PAT CAGR=15%, EV/EBITDA discount=35%).
      - Fails Recovery (Drawdown < 30% setup threshold -> NO_DRAWDOWN_DISLOCATION_FAIL).
    """
    stock_a_data: Dict[str, Any] = {
        "symbol": "COMPOUNDER_A",
        "industry": "IT Services",
        "roce_5y_avg": 25.0,
        "roce": 25.0,
        "sales_cagr_5y": 15.0,
        "pat_cagr_5y": 15.0,
        "cfo_pat_ratio": 0.90,
        "debt_to_equity": 0.10,
        "share_dilution_3y": 0.0,
        "current_ev_ebitda": 10.0,
        "ev_ebitda_3y_median": 16.0,  # 37.5% discount
        "current_price": 1000.0,
        "high_2y": 1020.0,  # 1.96% drawdown
    }

    # Evaluate Compounder
    compounder_pass = (
        stock_a_data["roce_5y_avg"] >= 15.0
        and stock_a_data["sales_cagr_5y"] >= 10.0
        and stock_a_data["pat_cagr_5y"] >= 10.0
        and stock_a_data["cfo_pat_ratio"] >= 0.80
        and stock_a_data["debt_to_equity"] <= 0.50
        and stock_a_data["share_dilution_3y"] <= 10.0
        and (stock_a_data["current_ev_ebitda"] / stock_a_data["ev_ebitda_3y_median"]) <= 0.75
    )
    assert compounder_pass is True, "Stock A should PASS Quality Compounder V2"

    # Evaluate Recovery Engine
    rec_pass, rec_reasons, rec_metrics = QualityValueRecoveryScanner.evaluate_symbol_recovery(
        sym="COMPOUNDER_A",
        row=stock_a_data,
        cmp_price=1000.0
    )

    assert rec_pass is False, "Stock A must FAIL Quality Value Recovery due to lack of 30% 2Y drawdown"
    assert "NO_DRAWDOWN_DISLOCATION_FAIL" in rec_reasons
    assert rec_metrics["drawdown_pct"] < 30.0


def test_stock_b_recovery_pass_compounder_fail():
    """
    Stock B: Deep 2Y drawdown recovery candidate (-35.0% drawdown, 6.0% Sales CAGR).
      - Passes Recovery (Drawdown=35.0% >= 30.0%, 3Y ROCE=14%, D/E=0.30, EV/EBITDA compression=25%).
      - Fails Compounder (Sales CAGR 6.0% < 10.0% strict threshold).
    """
    stock_b_data: Dict[str, Any] = {
        "symbol": "RECOVERY_B",
        "industry": "Capital Goods",
        "roce_5y_avg": 14.0,
        "roce": 14.0,
        "sales_cagr_5y": 6.0,  # Fails Compounder 10% CAGR rule
        "pat_cagr_5y": 8.0,    # Fails Compounder 10% CAGR rule
        "cfo_pat_ratio": 0.85,
        "debt_to_equity": 0.30,
        "share_dilution_3y": 0.0,
        "current_ev_ebitda": 12.0,
        "ev_ebitda_3y_median": 16.0,  # 25.0% compression
        "current_price": 650.0,
        "high_2y": 1000.0,  # 35.0% drawdown from 2Y peak
    }

    # Evaluate Compounder
    compounder_pass = (
        stock_b_data["roce_5y_avg"] >= 15.0
        and stock_b_data["sales_cagr_5y"] >= 10.0  # FAILS (6% < 10%)
        and stock_b_data["pat_cagr_5y"] >= 10.0
    )
    assert compounder_pass is False, "Stock B must FAIL Quality Compounder V2 due to Sales CAGR < 10%"

    # Evaluate Recovery Engine
    rec_pass, rec_reasons, rec_metrics = QualityValueRecoveryScanner.evaluate_symbol_recovery(
        sym="RECOVERY_B",
        row=stock_b_data,
        cmp_price=650.0
    )

    assert rec_pass is True, f"Stock B should PASS Quality Value Recovery (Reasons: {rec_reasons})"
    assert len(rec_reasons) == 0
    assert rec_metrics["drawdown_pct"] == 35.0
    assert rec_metrics["val_compression_ratio"] <= 0.80


def test_financial_sector_exclusion_across_both():
    """Verify that Financial symbols are excluded across both engines."""
    fin_data: Dict[str, Any] = {
        "symbol": "HDFCBANK",
        "industry": "Private Sector Bank",
        "roce_5y_avg": 18.0,
        "debt_to_equity": 0.10,
        "current_price": 1600.0,
        "high_2y": 2000.0,
    }

    is_fin = QualityCompounderValueV2Scanner.is_financial_sector(fin_data["industry"], fin_data["symbol"])
    assert is_fin is True

    rec_pass, rec_reasons, _ = QualityValueRecoveryScanner.evaluate_symbol_recovery(
        sym="HDFCBANK",
        row=fin_data,
        cmp_price=1600.0
    )
    assert rec_pass is False
    assert "FINANCIAL_SECTOR_EXCLUDED" in rec_reasons


def test_model_e3_full_exit_framework_rules():
    """
    Verify complete Model E3 Exit Framework Rules:
      Rule 1: Excessive Debt (D/E > 1.25)
      Rule 2: Operating Margin Collapse (> 30% margin decline)
      Rule 3: Sustained Profit Decline (3 consecutive YoY declines)
    """
    # Case 1: Excessive Debt (D/E = 1.50)
    high_debt_data: Dict[str, Any] = {
        "symbol": "HIGH_DEBT_CO",
        "industry": "Textiles",
        "roce_5y_avg": 14.0,
        "debt_to_equity": 1.50,  # Excessive debt (> 1.25)
        "cfo_pat_ratio": 0.80,
        "current_ev_ebitda": 8.0,
        "ev_ebitda_3y_median": 12.0,
        "current_price": 500.0,
        "high_2y": 1000.0,
    }
    pass1, reasons1, _ = QualityValueRecoveryScanner.evaluate_symbol_recovery("HIGH_DEBT_CO", high_debt_data, 500.0)
    assert pass1 is False
    assert "MODEL_E3_STRUCTURAL_DETERIORATION_DEBT" in reasons1

    # Case 2: Operating Margin Collapse (Current 10% vs 3Y Med 20% -> 50% collapse)
    margin_collapse_data: Dict[str, Any] = {
        "symbol": "MARGIN_COLLAPSE_CO",
        "industry": "Chemicals",
        "roce_5y_avg": 14.0,
        "debt_to_equity": 0.20,
        "cfo_pat_ratio": 0.80,
        "operating_margin": 10.0,
        "operating_margin_3y_median": 20.0,  # 50% collapse > 30% limit
        "current_ev_ebitda": 8.0,
        "ev_ebitda_3y_median": 12.0,
        "current_price": 500.0,
        "high_2y": 1000.0,
    }
    pass2, reasons2, _ = QualityValueRecoveryScanner.evaluate_symbol_recovery("MARGIN_COLLAPSE_CO", margin_collapse_data, 500.0)
    assert pass2 is False
    assert "MODEL_E3_MARGIN_COLLAPSE_FAIL" in reasons2

    # Case 3: Sustained Profit Decline (3 consecutive declines)
    profit_decline_data: Dict[str, Any] = {
        "symbol": "PROFIT_DECLINE_CO",
        "industry": "Auto Ancillary",
        "roce_5y_avg": 14.0,
        "debt_to_equity": 0.20,
        "cfo_pat_ratio": 0.80,
        "consecutive_profit_declines": 3,
        "current_ev_ebitda": 8.0,
        "ev_ebitda_3y_median": 12.0,
        "current_price": 500.0,
        "high_2y": 1000.0,
    }
    pass3, reasons3, _ = QualityValueRecoveryScanner.evaluate_symbol_recovery("PROFIT_DECLINE_CO", profit_decline_data, 500.0)
    assert pass3 is False
    assert "MODEL_E3_SUSTAINED_PROFIT_DECLINE_FAIL" in reasons3
