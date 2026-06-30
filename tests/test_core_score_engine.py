import pytest
from app.core.deprecated.core_score_engine import (
    CoreFundamentals, 
    PeerMetrics, 
    CorePriceData, 
    generate_core_scores
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
        is_financial=False,
        market_cap=1000000000.0
    )
    if updates:
        base.update(updates)
    return CoreFundamentals(**base)

def get_base_peers(updates=None):
    base = dict(
        median_pe=22.0,
        median_pb=4.5,
        median_roe=0.18,
        peer_count=20,
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

def test_new_engine_scoring_structure():
    """Verify that the new engine outputs the 6-pillar structure."""
    f = get_base_fundamentals()
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    
    assert scores.overall_score > 0
    assert scores.institutional_rating in ["AAA", "AA", "A", "BBB", "BB", "B", "C"]
    
    # Check backwards compatibility mappings
    assert scores.business_quality_score > 0
    assert scores.relative_valuation_score > 0

def test_missing_growth_inputs():
    """Verify that entirely missing growth inputs do not crash and use default 0.5 scores."""
    f = get_base_fundamentals(dict(
        revenue_growth_1y=None, revenue_growth_3y=None, revenue_growth_5y=None,
        eps_growth_1y=None, eps_growth_3y=None, eps_growth_5y=None,
        yoy_profit_growth=None
    ))
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert scores.growth.score == 0.5 # Default missing value mapped to average

def test_missing_eps_and_bvps():
    """Verify that missing absolute EPS and BVPS still yields a valid score."""
    f = get_base_fundamentals(dict(eps=None, bvps=None))
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert scores.overall_score > 0

def test_missing_peer_multiples():
    """Verify missing peer PE and PB reduces coverage but does not crash."""
    f = get_base_fundamentals()
    p = get_base_peers(dict(median_pe=None, median_pb=None, peer_count=2))
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert scores.value.coverage < 1.0

def test_kill_gate_rejection():
    """Verify that failing kill gates marks the score as KILL_GATE rejected."""
    # Example: Altman Z very poor or Auditor flags (if they were implemented)
    f = get_base_fundamentals(dict(altman_z=1.0)) # < 1.8 is kill gate usually
    p = get_base_peers()
    pd_data = get_base_price()
    
    scores = generate_core_scores(f, p, pd_data)
    assert len(scores.warnings) > 0
    assert scores.rejection_stage == "KILL_GATE"
