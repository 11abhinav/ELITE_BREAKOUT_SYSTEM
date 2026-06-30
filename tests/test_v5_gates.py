import pytest
from app.core.gate_engine import run_gates

def test_run_gates_positive_equity():
    data = {"total_equity": 100.0}
    passed, reason = run_gates("TEST", data)
    assert passed is True
    assert reason == ""

def test_run_gates_negative_equity():
    data = {"total_equity": -5.0}
    passed, reason = run_gates("TEST", data)
    assert passed is False
    assert "Negative Equity" in reason

def test_run_gates_missing_equity_fallback_to_market_cap():
    data = {"market_cap": 1000.0}
    passed, reason = run_gates("TEST", data)
    assert passed is True

def test_run_gates_high_debt():
    data = {"total_equity": 100.0, "debt_equity": 3.5}
    passed, reason = run_gates("TEST", data)
    assert passed is False
    assert "Extreme Debt" in reason

def test_run_gates_auditor_flags():
    data = {"total_equity": 100.0, "auditor_flags": True}
    passed, reason = run_gates("TEST", data)
    assert passed is False
    assert "Auditor Issues" in reason

def test_run_gates_negative_ocf_for_non_financial():
    data = {
        "total_equity": 100.0,
        "is_financial": False,
        "operating_cash_flow_ttm": -100.0,
        "free_cash_flow": -50.0
    }
    passed, reason = run_gates("TEST", data)
    assert passed is False
    assert "Negative Operating Cash Flow" in reason

def test_run_gates_negative_ocf_for_financial():
    data = {
        "total_equity": 100.0,
        "is_financial": True,
        "operating_cash_flow_ttm": -100.0
    }
    passed, reason = run_gates("TEST", data)
    assert passed is True
