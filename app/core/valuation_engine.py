from typing import Dict, Any, Tuple
from core.models import ValuationResult
from core.audit_engine import audit_engine
from core.quality_engine import safe_float
import math

def calculate_wacc(sector: str, mc: float) -> float:
    # Baseline Risk-Free Rate ~7% (India 10Y), Market Risk Premium ~5.5%
    # Beta assumption based on sector and size
    base_wacc = 0.11
    if sector == 'software' or sector == 'fmcg':
        base_wacc = 0.10 # Lower beta / stable
    elif sector == 'cyclical' or sector == 'manufacturing':
        base_wacc = 0.12 # Higher cyclical risk
    elif sector == 'banking' or sector == 'nbfc':
        base_wacc = 0.13 # Higher leverage risk

    # Size premium: Small caps carry more risk
    if mc > 0 and mc < 50000000000: # < 5000 Cr
        base_wacc += 0.015

    return base_wacc

def calculate_multi_stage_dcf(fcf: float, shares: float, wacc: float, rev_growth: float) -> Tuple[float, list]:
    if fcf <= 0 or shares <= 0: return 0.0, ["Negative/Zero FCF"]
    
    fcf_per_share = fcf / shares
    pv = 0.0
    
    # Cap high growth assumptions to realistic bounds (max 25%)
    high_growth = min(max(rev_growth, 0.08), 0.25)
    fade_growth = high_growth * 0.6 # Fade phase growth slows
    terminal_growth = 0.04 # Terminal growth pegged below nominal GDP
    
    current_fcf = fcf_per_share
    
    # Stage 1: High Growth (Years 1-3)
    for i in range(1, 4):
        current_fcf *= (1 + high_growth)
        pv += current_fcf / ((1 + wacc) ** i)
        
    # Stage 2: Fade (Years 4-5)
    for i in range(4, 6):
        current_fcf *= (1 + fade_growth)
        pv += current_fcf / ((1 + wacc) ** i)
        
    # Stage 3: Terminal Value
    terminal_value = (current_fcf * (1 + terminal_growth)) / (wacc - terminal_growth)
    pv += terminal_value / ((1 + wacc) ** 5)
    
    warnings = []
    if high_growth > 0.20:
        warnings.append(f"Aggressive growth assumption ({high_growth*100:.1f}%) in DCF")
        
    return pv, warnings

def calculate_excess_return_financials(bvps: float, roe: float, cost_of_equity: float, expected_growth: float) -> Tuple[float, list]:
    """Excess Return Model (or DDM proxy) for Financials where FCF is not applicable."""
    if bvps <= 0 or roe <= 0: return 0.0, ["Negative Book Value/ROE"]
    
    terminal_growth = 0.04
    # Expected ROE moving forward, capped for safety
    expected_roe = min(max(roe, 0.08), 0.20)
    
    # Excess Return = (ROE - Cost of Equity) * Book Value
    excess_return = (expected_roe - cost_of_equity) * bvps
    
    warnings = []
    if excess_return <= 0:
        # If a bank destroys value (ROE < COE), it's worth roughly its book value (or less)
        warnings.append("ROE < Cost of Equity (Value Destroyer)")
        return bvps * 0.8, warnings
        
    # Value = BV + PV of Excess Returns
    pv_excess_returns = excess_return / (cost_of_equity - terminal_growth)
    fair_value = bvps + pv_excess_returns
    
    return fair_value, warnings

def calculate_graham(eps: float, bvps: float, wacc: float) -> float:
    if eps <= 0 or bvps <= 0: return 0.0
    # Original formula used 22.5 based on AAA bond yields at the time. 
    # Adjusted dynamically based on interest environment (proxied inversely by WACC)
    # Higher WACC -> Lower Multiplier
    multiplier = max(15.0, min(25.0, 22.5 * (0.11 / wacc)))
    return math.sqrt(multiplier * eps * bvps)

def calculate_epv(nopat: float, shares: float, wacc: float) -> float:
    if nopat <= 0 or shares <= 0: return 0.0
    return (nopat / wacc) / shares

def calculate_peer_relative(eps: float, ind_pe: float) -> float:
    if eps <= 0 or ind_pe <= 0: return 0.0
    capped_pe = min(ind_pe, 45.0) # Institutional cap for relative PE
    return eps * capped_pe

def calculate_asset_value(bvps: float) -> float:
    if bvps <= 0: return 0.0
    return bvps

def run_valuation_engine(symbol: str, raw_data: Dict[str, Any], weights: Dict[str, float]) -> ValuationResult:
    """
    Layer 5: Valuation Engine
    """
    missing = []
    reasons = []
    warnings = []

    current_price = safe_float(raw_data.get('price'))
    shares = safe_float(raw_data.get('shares_outstanding'))
    mc = safe_float(raw_data.get('market_cap'))
    sector = raw_data.get('sector', 'default').lower()
    is_fin = raw_data.get('is_financial', False)

    if shares == 0.0:
        if mc > 0 and current_price > 0:
            shares = mc / current_price

    eps = safe_float(raw_data.get('eps'))
    bvps = safe_float(raw_data.get('book_value_per_share'))
    fcf = safe_float(raw_data.get('free_cash_flow'))
    nopat = safe_float(raw_data.get('ebit')) * 0.75 # Assume 25% tax rate
    ind_pe = safe_float(raw_data.get('tt_indpe'))
    rev_growth = safe_float(raw_data.get('revenue_cagr_3y'))
    roe = safe_float(raw_data.get('roce')) # Proxying ROE with ROCE for simplicity if missing
    
    # Calculate Dynamic WACC
    wacc = calculate_wacc(sector, mc)

    # Calculate Individual Models
    if is_fin:
        # Financials use Excess Return instead of DCF
        dcf_val, dcf_warns = calculate_excess_return_financials(bvps, roe, wacc, rev_growth)
        for w in dcf_warns: warnings.append(w)
    else:
        dcf_val, dcf_warns = calculate_multi_stage_dcf(fcf, shares, wacc, rev_growth)
        for w in dcf_warns: warnings.append(w)

    graham_val = calculate_graham(eps, bvps, wacc)
    epv_val = calculate_epv(nopat, shares, wacc)
    peer_val = calculate_peer_relative(eps, ind_pe)
    asset_val = calculate_asset_value(bvps)

    # Weights
    w_dcf = weights.get('dcf', 0.35)
    w_peer = weights.get('peer_relative', 0.25)
    w_graham = weights.get('graham', 0.15)
    w_epv = weights.get('earnings_power', 0.15)
    w_asset = weights.get('asset_value', 0.10)

    total_weight = 0.0
    weighted_fv = 0.0

    if dcf_val > 0:
        weighted_fv += dcf_val * w_dcf
        total_weight += w_dcf
    else: missing.append("dcf_inputs")

    if peer_val > 0:
        weighted_fv += peer_val * w_peer
        total_weight += w_peer
    else: missing.append("peer_inputs")

    if graham_val > 0:
        weighted_fv += graham_val * w_graham
        total_weight += w_graham
    else: missing.append("graham_inputs")

    if epv_val > 0:
        weighted_fv += epv_val * w_epv
        total_weight += w_epv
    else: missing.append("epv_inputs")

    if asset_val > 0:
        weighted_fv += asset_val * w_asset
        total_weight += w_asset
    else: missing.append("asset_inputs")

    confidence = 100.0
    score = 0.0
    fair_value = 0.0
    margin_of_safety = 0.0
    bear_val = 0.0
    bull_val = 0.0

    if total_weight > 0:
        fair_value = weighted_fv / total_weight
        bear_val = fair_value * 0.8 # 20% discount for bear
        bull_val = fair_value * 1.2 # 20% premium for bull

        if current_price > 0:
            margin_of_safety = ((fair_value - current_price) / fair_value) * 100.0
            
            # Score logic: Higher margin of safety = better score
            if margin_of_safety > 20: score = 100.0
            elif margin_of_safety > 0: score = 75.0
            elif margin_of_safety > -20: score = 50.0
            else: score = 20.0
            
            reasons.append(f"Fair Value: {fair_value:.2f}, MoS: {margin_of_safety:.1f}%")
        else:
            missing.append("current_price")

        # Confidence drops if models are missing
        confidence = (total_weight / 1.0) * 100.0
    else:
        confidence = 0.0
        reasons.append("All valuation models failed.")

    audit_engine.log(symbol, "Valuation", "Passed" if score > 50 else "Warning", f"Calculated FV: {fair_value:.2f}", "margin_of_safety", margin_of_safety)

    return ValuationResult(
        score=score,
        confidence=round(confidence, 2),
        missing_metrics=missing,
        warnings=warnings,
        reasons=reasons,
        fair_value=round(fair_value, 2),
        bear_value=round(bear_val, 2),
        bull_value=round(bull_val, 2),
        margin_of_safety=round(margin_of_safety, 2),
        dcf_value=round(dcf_val, 2) if dcf_val > 0 else None,
        peer_relative_value=round(peer_val, 2) if peer_val > 0 else None,
        graham_value=round(graham_val, 2) if graham_val > 0 else None,
        epv_value=round(epv_val, 2) if epv_val > 0 else None,
        asset_value=round(asset_val, 2) if asset_val > 0 else None
    )
