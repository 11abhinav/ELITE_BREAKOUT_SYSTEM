import pytest
from app.core_score_engine import (
    CoreFundamentals, 
    PeerMetrics, 
    CorePriceData, 
    generate_core_scores,
    get_engine_config
)

def get_base_fundamentals(updates=None):
    base = dict(
        symbol="TEST",
        sector="IT",
        canonical_industry="IT_SERVICES",
        pe=20.0,
        pb=5.0,
        roe=0.20,
        roce=0.25,
        debt_equity=0.1,
        operating_margin=0.15,
        revenue_growth_3y=0.15,
        revenue_growth_5y=0.12,
        eps_growth_3y=0.18,
        eps_growth_5y=0.15,
        revenue_growth_1y=0.20,
        eps_growth_1y=0.25,
        fcf_margin=0.10,
        cfo_pat_ratio=1.2,
        operating_cash_flow=100.0,
        yoy_profit_growth=0.25,
        net_losses_3y=False,
        div_yield=0.01,
        eps=10.0,
        bvps=40.0,
        roa=0.12,
        is_financial=False
    )
    if updates:
        base.update(updates)
    return CoreFundamentals(**base)

def get_base_peers(updates=None):
    base = dict(
        median_pe=22.0,
        median_pb=4.5,
        median_roe=0.18,
        median_peg=1.2,
        peer_count=20,
        dispersion_iqr_median=0.1,
        source_type="REFINED",
        is_complete=True,
        missing_critical=False,
        missing_minor=False
    )
    if updates:
        base.update(updates)
    return PeerMetrics(**base)

def get_base_price(updates=None):
    base = dict(
        price=200.0,
        sma_50=180.0,
        sma_200=150.0,
        high_20d=210.0,
        latest_volume=100000.0,
        volume_sma20=80000.0
    )
    if updates:
        base.update(updates)
    return CorePriceData(**base)

def test_decimal_vs_percent_growth_input_peg():
    """Verify that PEG handles the 0.25 (decimal) inputs safely."""
    f = get_base_fundamentals(dict(pe=25.0, eps_growth_1y=0.25))
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    
    assert scores.relative_valuation_score > 0
    assert scores.business_quality_score > 0
    assert scores.financial_quality_score > 0

def test_missing_growth_inputs():
    """Verify that entirely missing growth inputs do not crash or erroneously reward."""
    f = get_base_fundamentals(dict(
        revenue_growth_1y=None, revenue_growth_3y=None, revenue_growth_5y=None,
        eps_growth_1y=None, eps_growth_3y=None, eps_growth_5y=None,
        yoy_profit_growth=None
    ))
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert isinstance(scores.business_quality_score, float)
    assert isinstance(scores.relative_valuation_score, float)
    assert scores.is_buy is True

def test_missing_eps_and_bvps():
    """Verify that missing absolute EPS and BVPS still yields a valid score (though fair values may degrade)."""
    f = get_base_fundamentals(dict(eps=None, bvps=None))
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert scores.is_buy is True
    assert scores.bayesian_confidence_score > 0

def test_missing_peer_multiples():
    """Verify missing peer PE and PB reduces reliability but does not crash."""
    f = get_base_fundamentals()
    p = get_base_peers(dict(median_pe=None, median_pb=None, missing_critical=True, peer_count=2, source_type="FALLBACK"))
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    # With missing critical metrics and peer count < 5, confidence should drop significantly
    assert scores.bayesian_confidence_score < 100.0

def test_kill_gate_rejection():
    """Verify that failing kill gates marks the score as KILL_GATE rejected."""
    f = get_base_fundamentals(dict(debt_equity=4.0)) # > max 3.0
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert scores.is_buy is False
    assert scores.rejection_stage == "KILL_GATE"
    assert "Debt/Equity" in scores.rejection_reason

def test_quality_gate_rejection():
    """Verify that failing quality minimums marks the score as QUALITY_GATE rejected."""
    # ROE 0.05 (passes kill gate), OPM low, growth low -> fails Quality Gate
    f = get_base_fundamentals(dict(roe=0.05, roce=0.01, operating_margin=0.01, revenue_growth_3y=0.01, eps_growth_3y=0.01))
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert scores.is_buy is False
    assert scores.rejection_stage == "QUALITY_GATE"

def test_valuation_guard_rejection():
    """Verify that failing valuation guard marks the score as VALUATION_GUARD rejected."""
    # IT Services weights PE and PEG. Give it terrible PE (e.g. 150) and median peer PE = 20. PEG = 150/10 = 15.
    f = get_base_fundamentals(dict(pe=150.0, eps_growth_1y=0.10))
    p = get_base_peers(dict(median_pe=20.0))
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert scores.is_buy is False
    assert scores.rejection_stage == "VALUATION_GUARD"
