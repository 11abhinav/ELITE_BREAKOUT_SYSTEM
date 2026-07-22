import pytest
from app.forensic_engine import ForensicEngine, ForensicRiskTier

def test_hard_reject_cfo_pat_below_0_6():
    """Test 1: Hard reject when 3Y Cumulative CFO/PAT < 0.6."""
    data = {
        "cfo_pat_3y": 0.45,
        "fcf_history": [10.0, 15.0, 20.0],
        "capex_sales_ratio": 0.20,
        "revenue_cagr_3y": 0.25,
        "roce": 0.22
    }
    res = ForensicEngine.evaluate_symbol(data)
    assert res["forensic_risk_tier"] == ForensicRiskTier.REJECT
    assert res["forensic_score"] == -30
    assert res["forensic_details"]["cfo_pat_status"] == "HARD_REJECT"

def test_weighted_growth_investment_score():
    """Test 2: Weighted Growth Investment Score calculation."""
    data = {
        "capex_sales_ratio": 0.18, # (0.18/0.15)*100 = 120 -> capped 100 * 0.40 = 40 pts
        "revenue_cagr_3y": 0.21,   # (0.21/0.15)*100 = 140 -> capped 100 * 0.30 = 30 pts
        "roce": 0.24               # (0.24/0.15)*100 = 160 -> capped 100 * 0.30 = 30 pts
    }
    score, is_mode, details = ForensicEngine.calc_growth_investment_score(data)
    assert score >= 90
    assert is_mode is True

def test_reduced_fcf_penalty_in_growth_mode():
    """Test 3: Reduced FCF penalty scale in Growth Mode (-3, -5 pts vs -10, -20 pts)."""
    growth_data = {
        "cfo_pat_3y": 0.95,
        "fcf_history": [-5.0, -10.0, -15.0],
        "capex_sales_ratio": 0.18,
        "revenue_cagr_3y": 0.20,
        "roce": 0.22
    }
    res_growth = ForensicEngine.evaluate_symbol(growth_data)
    assert res_growth["growth_investment_mode"] is True
    assert res_growth["forensic_score"] == -3 # Capped penalty in Growth Mode

    normal_data = {
        "cfo_pat_3y": 0.95,
        "fcf_history": [-5.0, -10.0, -15.0],
        "capex_sales_ratio": 0.05,
        "revenue_cagr_3y": 0.05,
        "roce": 0.08
    }
    res_normal = ForensicEngine.evaluate_symbol(normal_data)
    assert res_normal["growth_investment_mode"] is False
    assert res_normal["forensic_score"] == -10 # Full penalty in Normal Mode

def test_missing_data_returns_unknown_tier():
    """Test 4: Missing data returns explicit UNKNOWN risk tier."""
    res = ForensicEngine.evaluate_symbol({})
    assert res["forensic_risk_tier"] == ForensicRiskTier.UNKNOWN
    assert res["forensic_score"] == 0

def test_capex_heavy_expansion_not_rejected():
    """Test 5: Capex-heavy, negative FCF, high ROCE company -> Growth Mode active, penalty reduced, NOT rejected."""
    data = {
        "cfo_pat_3y": 1.10,
        "fcf_history": [-10.0, -20.0, -30.0],
        "capex_sales_ratio": 0.22,
        "revenue_cagr_3y": 0.25,
        "roce": 0.24
    }
    res = ForensicEngine.evaluate_symbol(data)
    assert res["growth_investment_mode"] is True
    assert res["forensic_risk_tier"] == ForensicRiskTier.LOW
    assert res["forensic_score"] == -3

def test_mature_weak_cash_company_rejected_or_high():
    """Test 6: Mature company, low capex, negative FCF, weak CFO/PAT -> HIGH or REJECT."""
    data = {
        "cfo_pat_3y": 0.55,
        "fcf_history": [-10.0, -20.0, -30.0],
        "capex_sales_ratio": 0.03,
        "revenue_cagr_3y": 0.02,
        "roce": 0.06
    }
    res = ForensicEngine.evaluate_symbol(data)
    assert res["forensic_risk_tier"] == ForensicRiskTier.REJECT
