import logging
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# --- Dataclasses ---

@dataclass(frozen=True)
class CoreFundamentals:
    symbol: str
    sector: str
    pe: Optional[float]
    pb: Optional[float]
    roe: Optional[float]
    roce: Optional[float]
    debt_equity: Optional[float]
    operating_margin: Optional[float]
    revenue_growth_3y: Optional[float]
    revenue_growth_5y: Optional[float]
    eps_growth_3y: Optional[float]
    eps_growth_5y: Optional[float]
    revenue_growth_1y: Optional[float]
    eps_growth_1y: Optional[float]
    fcf_margin: Optional[float]
    cfo_pat_ratio: Optional[float]
    operating_cash_flow: Optional[float]
    yoy_profit_growth: Optional[float]
    net_losses_3y: bool
    div_yield: Optional[float]
    eps: Optional[float]
    bvps: Optional[float]
    roa: Optional[float]
    is_financial: bool

@dataclass(frozen=True)
class PeerMetrics:
    median_pe: Optional[float]
    median_pb: Optional[float]
    median_roe: Optional[float]
    median_peg: Optional[float]
    peer_count: int
    dispersion_iqr_median: Optional[float]
    source_type: str  # "REFINED", "INDUSTRY", "FALLBACK"
    is_complete: bool
    missing_critical: bool
    missing_minor: bool

@dataclass(frozen=True)
class CorePriceData:
    price: Optional[float]
    sma_50: Optional[float]
    sma_200: Optional[float]
    high_20d: Optional[float]
    latest_volume: Optional[float]
    volume_sma20: Optional[float]

@dataclass(frozen=True)
class CoreScores:
    business_quality_score: float  # 0-30
    relative_valuation_score: float # 0-30
    reliability_score: float       # 0-20
    market_structure_score: float  # 0-15
    base_fair_value: float
    bull_fair_value: float
    bear_fair_value: float
    target_multiple: float
    valuation_anchor: str
    composite_investment_score: float = 0.0
    
# --- Scoring Functions ---

def clamp(val, min_val, max_val):
    if val is None or pd.isna(val): return min_val
    return max(min_val, min(float(val), max_val))

def safe_float(val, default=0.0):
    if val is None or pd.isna(val): return default
    try:
        return float(val)
    except:
        return default

def first_valid(*vals):
    for v in vals:
        if v is not None and not pd.isna(v):
            return float(v)
    return None

def score_business_quality(f: CoreFundamentals) -> float:
    score = 0.0
    
    # 1. Profitability (8 pts) - Smooth scale
    roe = safe_float(f.roe)
    if roe > 0.05:
        score += min(4.0, 4.0 * (roe - 0.05) / (0.15 - 0.05))
        
    roce = safe_float(f.roce)
    if roce > 0.08:
        score += min(2.0, 2.0 * (roce - 0.08) / (0.18 - 0.08))
        
    opm = safe_float(f.operating_margin)
    if opm > 0.05:
        score += min(2.0, 2.0 * (opm - 0.05) / (0.15 - 0.05))

    # 2. Balance Sheet (6 pts)
    if f.is_financial:
        roa = safe_float(f.roa)
        if roa > 0.005:
            score += min(6.0, 6.0 * (roa - 0.005) / (0.015 - 0.005))
    else:
        de = safe_float(f.debt_equity)
        if de < 1.5:
            score += min(6.0, 6.0 * (1.5 - de) / (1.5 - 0.3))
            
    # 3. Growth Durability (10 pts)
    growth_score = 0.0
    rev_cagr = first_valid(f.revenue_growth_5y, f.revenue_growth_3y, f.revenue_growth_1y)
    eps_cagr = first_valid(f.eps_growth_5y, f.eps_growth_3y, f.eps_growth_1y)
    
    # Base growth up to 15% gets up to 5 points
    if rev_cagr is not None and rev_cagr > 0.05:
        growth_score += min(2.5, 2.5 * (rev_cagr - 0.05) / (0.15 - 0.05))
    if eps_cagr is not None and eps_cagr > 0.05:
        growth_score += min(2.5, 2.5 * (eps_cagr - 0.05) / (0.15 - 0.05))
        
    # Hyper growth bonus (15% to 35%) gets another 5 points
    if rev_cagr is not None and rev_cagr > 0.15:
        growth_score += min(2.5, 2.5 * (rev_cagr - 0.15) / (0.35 - 0.15))
    if eps_cagr is not None and eps_cagr > 0.15:
        growth_score += min(2.5, 2.5 * (eps_cagr - 0.15) / (0.35 - 0.15))
        
    has_long_term = pd.notna(f.revenue_growth_3y) or pd.notna(f.revenue_growth_5y)
    if not has_long_term and (rev_cagr is not None or eps_cagr is not None):
        growth_score *= 0.70 # 30% penalty applied after raw score
        
    score += clamp(growth_score, 0.0, 10.0)

    # 4. Cash Conversion (4 pts)
    cash_score = 0.0
    fcf_margin = safe_float(f.fcf_margin, None)
    if fcf_margin is not None:
        if fcf_margin > 0:
            target_fcf = opm * 0.5
            if target_fcf > 0:
                cash_score += min(2.0, 2.0 * min(fcf_margin / target_fcf, 1.0))
            else:
                cash_score += 2.0
    
    cfo_pat = safe_float(f.cfo_pat_ratio, None)
    if cfo_pat is not None and cfo_pat > 0.5:
        cash_score += min(2.0, 2.0 * (cfo_pat - 0.5) / (1.0 - 0.5))
        
    ocf = safe_float(f.operating_cash_flow, None)
    if not f.is_financial and ocf is not None and ocf < 0:
        cash_score -= min(3.0, 3.0 * (abs(ocf) / (abs(ocf) + 1000000)))
        
    score += clamp(cash_score, 0.0, 4.0)

    # 5. Consistency (2 pts)
    cons_score = 0.0
    yoy_profit = first_valid(f.yoy_profit_growth)
    if yoy_profit is not None and yoy_profit > 0:
        cons_score += 1.0
    if not f.net_losses_3y:
        cons_score += 1.0
        
    score += clamp(cons_score, 0.0, 2.0)
        
    return clamp(score, 0.0, 30.0)

def score_relative_valuation(f: CoreFundamentals, p: PeerMetrics) -> float:
    """
    Computes Relative Valuation Score.
    Note: Growth values (e.g. eps_growth_1y) are expected to be decimals (e.g. 0.15 for 15%).
    When computing PEG, growth is multiplied by 100 to convert to percentage points.
    """
    score = 0.0
    
    pe = safe_float(f.pe, None)
    pb = safe_float(f.pb, None)
    dy = safe_float(f.div_yield, 0.0)
    
    # Yield Support (4 pts)
    if dy > 0.005:
        score += min(4.0, 4.0 * (dy - 0.005) / (0.015 - 0.005))
    
    if f.is_financial:
        # P/B vs Peer Median (16 pts) - Only if median_pb is sane
        if pb is not None and p.median_pb is not None and p.median_pb > 0.1:
            ratio = pb / p.median_pb
            if ratio < 1.5:
                score += min(16.0, 16.0 * (1.5 - ratio) / (1.5 - 0.8))
                
        # ROE Premium/Discount (8 pts)
        roe = safe_float(f.roe)
        if roe > 0 and p.median_roe is not None and p.median_roe > -0.5:
            diff = roe - p.median_roe
            if diff > 0:
                score += min(8.0, 8.0 * (diff) / 0.05)
                
        # Growth Normalization Bonus (2 pts)
        eg = safe_float(f.eps_growth_1y, None)
        if eg is not None and eg > 0.05 and eg < 1.0 and safe_float(f.pe) > 0: # Ensure growth is sane and positive
            peg = safe_float(f.pe) / (eg * 100)
            if peg < 1.0:
                score += 2.0
    else:
        # P/E vs Peer Median (12 pts) - Only if median_pe is sane
        if pe is not None and p.median_pe is not None and p.median_pe > 2.0:
            ratio = pe / p.median_pe
            if ratio < 1.5:
                score += min(12.0, 12.0 * (1.5 - ratio) / (1.5 - 0.8))
                
        # P/B vs Peer Median (8 pts) - Only if median_pb is sane
        if pb is not None and p.median_pb is not None and p.median_pb > 0.1:
            ratio = pb / p.median_pb
            if ratio < 1.5:
                score += min(8.0, 8.0 * (1.5 - ratio) / (1.5 - 0.8))
                
        # Growth Normalization (PEG) (6 pts)
        eg = first_valid(f.eps_growth_5y, f.eps_growth_3y, f.eps_growth_1y)
        if eg is not None and eg > 0 and pe is not None and pe > 0:
            peg = pe / (eg * 100)
            if peg < 1.8:
                score += min(6.0, 6.0 * (1.8 - peg) / (1.8 - 0.8))

    return clamp(score, 0.0, 30.0)

def score_reliability(p: PeerMetrics) -> float:
    score = 0.0
    
    # 1. Peer Count Quality (6 pts)
    if p.peer_count >= 15: score += 6.0
    elif p.peer_count >= 8: score += 3.0
    
    # 2. Peer Dispersion (4 pts)
    disp = p.dispersion_iqr_median
    if disp is not None:
        if disp < 0.4: score += 4.0
        elif disp < 0.8: score += 2.0
        
    # 3. Source Confidence (6 pts)
    if p.source_type == "REFINED": score += 6.0
    elif p.source_type == "INDUSTRY": score += 4.0
    
    # 4. Data Completeness (4 pts)
    if p.is_complete: score += 4.0
    elif p.missing_minor and not p.missing_critical: score += 2.0
    
    return clamp(score, 0.0, 20.0)

def compute_relative_value_band(f: CoreFundamentals, p: PeerMetrics, reliability: float, current_price: float = None):
    """
    Computes a Multi-Factor Intrinsic Valuation using:
    1. Absolute Value (Graham Formula)
    2. Relative Value (Quality-adjusted Peer Multiples)
    3. Asset Value (Book Value)
    """
    base_fv, bull_fv, bear_fv = 0.0, 0.0, 0.0
    target_mult = 0.0
    anchor = ""
    
    roe = safe_float(f.roe)
    pe = safe_float(f.pe)
    pb = safe_float(f.pb)
    eps = safe_float(f.eps)
    bvps = safe_float(f.bvps)
    roce = safe_float(f.roce)
    
    # 1. Evaluate Earnings Growth (Capped at 20% to avoid extreme extrapolation)
    raw_growth = first_valid(f.eps_growth_5y, f.eps_growth_3y, f.eps_growth_1y, f.revenue_growth_3y, 0.0)
    growth_pct = clamp(raw_growth, 0.0, 0.20)
    
    # 2. Compute Absolute Intrinsic Value (Benjamin Graham Formula)
    # V = EPS * (8.5 + 2g)
    v_earnings = 0.0
    if eps > 0:
        v_earnings = eps * (8.5 + 2 * (growth_pct * 100))
        
    # 3. Compute Relative Peer Value (Quality Adjusted)
    v_peer = 0.0
    quality_premium = 0.0
    
    # Calculate Quality Premium based on ROE/ROCE vs Sector Median
    if p.median_roe is not None and p.median_roe > 0 and roe > p.median_roe:
        quality_premium += (roe - p.median_roe) / p.median_roe
    if roce > 0.15: quality_premium += 0.1
    if roce > 0.25: quality_premium += 0.1
    quality_premium = clamp(quality_premium, 0.0, 0.5) # Cap premium at 50%
    
    if p.median_pe is not None and p.median_pe > 0 and eps > 0:
        target_mult = p.median_pe * (1.0 + quality_premium)
        target_mult = clamp(target_mult, 5.0, 60.0) # Sane PE bounds
        v_peer = eps * target_mult
        
    # 4. Compute Asset/Book Value (Baseline)
    v_book = 0.0
    if bvps > 0:
        median_pb = p.median_pb if p.median_pb is not None and p.median_pb > 0 else 1.0
        v_book = bvps * median_pb * (1.0 + quality_premium)
        
    # --- Blending Logic ---
    if f.is_financial:
        anchor = "COMPOSITE (P/B Heavily Weighted)"
        # Financials are heavily weighted on Book Value
        if v_book > 0:
            if eps > 0 and v_peer > 0:
                base_fv = (v_book * 0.7) + (v_peer * 0.3)
            else:
                base_fv = v_book
        else:
            # Absolute fallback if BVPS missing
            base_fv = current_price * 0.5 if current_price else 0.0
    else:
        anchor = "COMPOSITE (Earnings + Peer Blend)"
        if eps <= 0:
            # Loss making companies (e.g. IDEA) cannot use Earnings or PE
            base_fv = v_book * 0.8 if v_book > 0 else (current_price * 0.3 if current_price else 0.0)
            anchor = "ASSET BASELINE (Loss-Making)"
        else:
            # Profitable Non-Financials
            if v_earnings > 0 and v_peer > 0:
                base_fv = (v_earnings + v_peer) / 2.0
            elif v_earnings > 0:
                base_fv = v_earnings
            elif v_peer > 0:
                base_fv = v_peer
            else:
                base_fv = v_book
                
    # --- Reliability Spread & Sanity Bounds ---
    if current_price and current_price > 0:
        # Prevent "Too Low" (Max downside 70%)
        if base_fv < 0.3 * current_price:
            base_fv = 0.3 * current_price
            
        # Prevent "Too High" (Max upside 100%)
        if base_fv > 2.0 * current_price:
            base_fv = 2.0 * current_price
            
    # Reliability Penalties
    if reliability < 10: base_fv *= 0.90
    spread = 0.05 if reliability >= 16 else (0.10 if reliability >= 10 else 0.15)
    
    bull_fv = base_fv * (1.0 + spread)
    bear_fv = base_fv * (1.0 - spread)
            
    return base_fv, bull_fv, bear_fv, target_mult, anchor

def compute_composite_investment_score(bqs: float, rvs: float, mss: float, reliability: float, strategic_overlays: float) -> float:
    score = (bqs / 30.0) * 35.0 + (rvs / 30.0) * 30.0 + (mss / 15.0) * 15.0 + (reliability / 20.0) * 10.0 + strategic_overlays
    return clamp(score, 0.0, 100.0)

def score_market_structure(p: CorePriceData) -> float:
    score = 0.0
    price = safe_float(p.price)
    
    # 1. Broad Accumulation (Max 9 pts)
    if price > safe_float(p.sma_50) and safe_float(p.sma_50) > 0:
        score += 4.5
    if price > safe_float(p.sma_200) and safe_float(p.sma_200) > 0:
        score += 4.5
        
    # 2. Breakout strength (Max 6 pts)
    if safe_float(p.latest_volume) > (1.5 * safe_float(p.volume_sma20)):
        score += 3.0
    if price >= safe_float(p.high_20d) and price > 0:
        score += 3.0
        
    return clamp(score, 0.0, 15.0)

def generate_core_scores(f: CoreFundamentals, p: PeerMetrics, price_data: CorePriceData = None, strategic_overlays: float = 0.0) -> CoreScores:
    bqs = score_business_quality(f)
    rvs = score_relative_valuation(f, p)
    reliability = score_reliability(p)
    mss = score_market_structure(price_data) if price_data else 0.0
    
    current_price = safe_float(price_data.price) if price_data else None
    base_fv, bull_fv, bear_fv, target_mult, anchor = compute_relative_value_band(f, p, reliability, current_price)
    
    cis = compute_composite_investment_score(bqs, rvs, mss, reliability, strategic_overlays)
    
    return CoreScores(
        business_quality_score=round(bqs, 1),
        relative_valuation_score=round(rvs, 1),
        reliability_score=round(reliability, 1),
        market_structure_score=round(mss, 1),
        base_fair_value=round(base_fv, 2),
        bull_fair_value=round(bull_fv, 2),
        bear_fair_value=round(bear_fv, 2),
        target_multiple=round(target_mult, 2),
        valuation_anchor=anchor,
        composite_investment_score=round(cis, 1)
    )
