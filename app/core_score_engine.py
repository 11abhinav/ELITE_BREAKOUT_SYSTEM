import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any, Tuple
import pandas as pd
import numpy as np
import yaml

logger = logging.getLogger(__name__)

# --- Configuration Loader ---
_ENGINE_CONFIG = None

def get_engine_config() -> Dict[str, Any]:
    global _ENGINE_CONFIG
    if _ENGINE_CONFIG is None:
        try:
            with open("app/config/engine_config.yaml", "r") as f:
                _ENGINE_CONFIG = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load engine_config.yaml: {e}")
            _ENGINE_CONFIG = {}
    return _ENGINE_CONFIG

# --- Dataclasses ---

@dataclass(frozen=True)
class CoreFundamentals:
    symbol: str
    sector: str
    canonical_industry: str
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
    peer_count: int
    median_ev_ebitda: Optional[float] = None
    median_div_yield: Optional[float] = None
    median_peg: Optional[float] = None
    dispersion_iqr_median: Optional[float] = None
    source_type: str = "FALLBACK"
    is_complete: bool = False
    missing_critical: bool = True
    missing_minor: bool = False

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
    business_quality_score: float
    financial_quality_score: float
    relative_valuation_score: float
    market_structure_score: float
    improvement_score: float
    bayesian_confidence_score: float # 0.0 to 100.0 (Probability)
    composite_investment_score: float
    rejection_stage: Optional[str] = None
    rejection_reason: Optional[str] = None
    is_buy: bool = False
    
# --- Utility Functions ---

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

# --- Split Quality Scoring ---

def score_business_quality(f: CoreFundamentals) -> float:
    """Scores ROCE, Margins, Market Position, Growth Durability (Max 20 pts)"""
    score = 0.0
    
    # 1. Profitability (Max 10)
    roe = safe_float(f.roe)
    if roe > 0.05: score += min(5.0, 5.0 * (roe - 0.05) / (0.20 - 0.05))
        
    roce = safe_float(f.roce)
    if roce > 0.08: score += min(3.0, 3.0 * (roce - 0.08) / (0.25 - 0.08))
        
    opm = safe_float(f.operating_margin)
    if opm > 0.05: score += min(2.0, 2.0 * (opm - 0.05) / (0.20 - 0.05))

    # 2. Growth Durability (Max 10)
    rev_cagr = first_valid(f.revenue_growth_5y, f.revenue_growth_3y, f.revenue_growth_1y)
    eps_cagr = first_valid(f.eps_growth_5y, f.eps_growth_3y, f.eps_growth_1y)
    if rev_cagr is not None and rev_cagr > 0.05:
        score += min(5.0, 5.0 * (rev_cagr - 0.05) / (0.25 - 0.05))
    if eps_cagr is not None and eps_cagr > 0.05:
        score += min(5.0, 5.0 * (eps_cagr - 0.05) / (0.25 - 0.05))

    return clamp(score, 0.0, 20.0)

def score_financial_quality(f: CoreFundamentals) -> float:
    """Scores Cash Conversion, Debt, Balance Sheet (Max 15 pts)"""
    score = 0.0
    
    if f.is_financial:
        roa = safe_float(f.roa)
        if roa > 0.005: score += min(15.0, 15.0 * (roa - 0.005) / (0.02 - 0.005))
    else:
        # Debt
        de = safe_float(f.debt_equity)
        if de < 1.5: score += min(8.0, 8.0 * (1.5 - de) / (1.5 - 0.1))
            
        # Cash Flow
        fcf_margin = safe_float(f.fcf_margin, None)
        if fcf_margin is not None and fcf_margin > 0: score += 4.0
        
        cfo_pat = safe_float(f.cfo_pat_ratio, None)
        if cfo_pat is not None and cfo_pat > 0.5:
            score += min(3.0, 3.0 * (cfo_pat - 0.5) / (1.0 - 0.5))

    return clamp(score, 0.0, 15.0)

# --- Sector-Aware Valuation ---

def get_sector_val_config(canonical_industry: str) -> dict:
    config = get_engine_config()
    industries = config.get("industries", {})
    return industries.get(canonical_industry, industries.get("DEFAULT", {"pe": 0.4, "pb": 0.4, "peg": 0.2}))

def score_relative_valuation(f: CoreFundamentals, p: PeerMetrics) -> float:
    """Max 30 points based on config."""
    score = 0.0
    config = get_sector_val_config(f.canonical_industry)
    
    for metric, weight in config.items():
        max_pts = 30.0 * weight
        metric_score = 0.0
        
        if metric == "pe" and f.pe is not None and f.pe > 0:
            if p.median_pe is not None and p.median_pe > 0:
                ratio = f.pe / p.median_pe
                if ratio < 1.3: metric_score = min(max_pts, max_pts * (1.3 - ratio) / (1.3 - 0.7))
        elif metric == "pb" and f.pb is not None and f.pb > 0:
            if p.median_pb is not None and p.median_pb > 0:
                ratio = f.pb / p.median_pb
                if ratio < 1.3: metric_score = min(max_pts, max_pts * (1.3 - ratio) / (1.3 - 0.7))
        elif metric == "peg" and f.pe is not None and f.pe > 0:
            g = safe_float(f.eps_growth_1y) * 100
            if g > 0:
                peg = f.pe / g
                if peg < 2.5: metric_score = min(max_pts, max_pts * (2.5 - peg) / (2.5 - 0.5))
        elif metric == "ev_ebitda":
            # Note: ev_ebitda would need f.ev_ebitda but we'll use PE as proxy if missing
            if f.pe is not None and p.median_pe is not None and p.median_pe > 0:
                ratio = f.pe / p.median_pe
                if ratio < 1.3: metric_score = min(max_pts, max_pts * (1.3 - ratio) / (1.3 - 0.7))
        elif metric == "roe" and f.roe is not None:
            if p.median_roe is not None and p.median_roe > -0.5:
                diff = safe_float(f.roe) - p.median_roe
                if diff > 0: metric_score = min(max_pts, max_pts * diff / 0.05)
        elif metric == "dividend" and f.div_yield is not None:
            dy = safe_float(f.div_yield)
            if p.median_div_yield is not None and p.median_div_yield >= 0:
                if dy > p.median_div_yield: metric_score = min(max_pts, max_pts * (dy - p.median_div_yield) / 0.02)
            elif dy > 0.02: metric_score = min(max_pts, max_pts * (dy - 0.02) / 0.03)
            
        score += metric_score

    return clamp(score, 0.0, 30.0)

# --- Percentile Improvement Score ---

def score_improvement() -> float:
    # Requires historical data percentile ranking against industry
    # Hard to calculate perfectly without historical DB, returning a base proxy
    return 5.0 

# --- Bayesian Confidence ---

def score_bayesian_confidence(f: CoreFundamentals, p: PeerMetrics) -> float:
    """Probability that the data is complete and consistent."""
    prob = 1.0
    
    # 1. Missing Critical Metrics (Data Completeness)
    if f.pe is None and f.pb is None: prob *= 0.7
    if f.roe is None and f.roce is None: prob *= 0.8
    if f.operating_margin is None: prob *= 0.9
    
    # 2. Peer Sample Size
    if p.peer_count < 5: prob *= 0.6
    elif p.peer_count < 10: prob *= 0.85
    
    # 3. Metric Consistency (Dispersion)
    if p.dispersion_iqr_median is not None:
        if p.dispersion_iqr_median > 0.8: prob *= 0.8
        elif p.dispersion_iqr_median > 0.5: prob *= 0.9

    return clamp(prob * 100, 0.0, 100.0)

# --- Trend & Market Structure ---

def score_market_structure(p: CorePriceData) -> float:
    score = 0.0
    price = safe_float(p.price)
    if price > safe_float(p.sma_50) and safe_float(p.sma_50) > 0: score += 7.5
    if price > safe_float(p.sma_200) and safe_float(p.sma_200) > 0: score += 7.5
    return clamp(score, 0.0, 15.0)

# --- Hierarchical Orchestration ---

def check_kill_gates(f: CoreFundamentals) -> Tuple[bool, Optional[str]]:
    config = get_engine_config()
    gates = config.get("kill_gates", {})
    
    de = safe_float(f.debt_equity)
    if not f.is_financial and de > gates.get("max_debt_equity", 3.0):
        return False, f"Debt/Equity {de:.1f} > Max {gates.get('max_debt_equity', 3.0)}"
        
    yoy = safe_float(f.yoy_profit_growth, 0.0)
    if yoy < gates.get("max_profit_decline_yoy", -0.3):
        return False, f"YoY Profit {yoy*100:.1f}% < Max Decline {gates.get('max_profit_decline_yoy', -0.3)*100:.1f}%"
        
    if safe_float(f.roe) < gates.get("min_roe", 0.05):
        return False, f"ROE {safe_float(f.roe)*100:.1f}% < Min {gates.get('min_roe', 0.05)*100:.1f}%"
        
    return True, None

def check_valuation_guard(f: CoreFundamentals, rvs: float) -> Tuple[bool, Optional[str]]:
    config = get_engine_config()
    vg = config.get("valuation_guard", {})
    if not vg.get("enabled", True): return True, None
    
    if rvs < vg.get("min_relative_score", 5.0):
        peg = 0.0
        g = safe_float(f.eps_growth_1y) * 100
        if g > 0 and f.pe is not None: peg = f.pe / g
        
        if peg > vg.get("max_peg", 2.5):
            return False, f"Valuation Guard: RVS {rvs:.1f} < {vg.get('min_relative_score')} AND PEG {peg:.1f} > {vg.get('max_peg')}"
            
    return True, None

def generate_core_scores(f: CoreFundamentals, p: PeerMetrics, price_data: CorePriceData, regime: str = "BULL") -> CoreScores:
    # 1. Kill Gates
    passed_kill, kill_reason = check_kill_gates(f)
    if not passed_kill:
        return CoreScores(0, 0, 0, 0, 0, 0, 0, "KILL_GATE", kill_reason, False)
        
    # 2. Quality Scores
    bqs = score_business_quality(f)
    fqs = score_financial_quality(f)
    
    config = get_engine_config()
    qm = config.get("quality_minimums", {})
    if bqs < qm.get("min_business_quality_score", 5.0):
        return CoreScores(bqs, fqs, 0, 0, 0, 0, 0, "QUALITY_GATE", f"BQS {bqs:.1f} < Min", False)
        
    # 3. Valuation Guard
    rvs = score_relative_valuation(f, p)
    passed_val, val_reason = check_valuation_guard(f, rvs)
    if not passed_val:
        return CoreScores(bqs, fqs, rvs, 0, 0, 0, 0, "VALUATION_GUARD", val_reason, False)
        
    # 4. Trend Confirmation
    mss = score_market_structure(price_data) if price_data else 0.0
    
    # 5. Improvement & Confidence
    imp = score_improvement()
    conf = score_bayesian_confidence(f, p)
    
    # 6. Composite Score (Based on Regime Weights)
    weights = config.get("market_regimes", {}).get(regime, config.get("market_regimes", {}).get("BULL", {}))
    cis = (
        (bqs / 20.0) * weights.get("business_quality", 25) +
        (fqs / 15.0) * weights.get("financial_quality", 10) +
        (rvs / 30.0) * weights.get("valuation", 20) +
        (mss / 15.0) * weights.get("trend", 25) +
        (imp / 10.0) * weights.get("improvement", 20)
    )
    
    return CoreScores(
        business_quality_score=round(bqs, 1),
        financial_quality_score=round(fqs, 1),
        relative_valuation_score=round(rvs, 1),
        market_structure_score=round(mss, 1),
        improvement_score=round(imp, 1),
        bayesian_confidence_score=round(conf, 1),
        composite_investment_score=round(cis, 1),
        rejection_stage=None,
        rejection_reason=None,
        is_buy=True
    )
