import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime
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
    # Old compatibility fields (some might be unused by the new engine)
    yoy_profit_growth: Optional[float] = None
    net_losses_3y: bool = False
    
    # Raw Data
    pe: Optional[float] = None
    pb: Optional[float] = None
    ev_ebitda: Optional[float] = None
    roe: Optional[float] = None
    roce: Optional[float] = None
    debt_equity: Optional[float] = None
    operating_margin: Optional[float] = None
    gross_margin: Optional[float] = None
    fcf_margin: Optional[float] = None
    
    # Growth
    revenue_growth_1y: Optional[float] = None
    revenue_growth_3y: Optional[float] = None
    revenue_growth_5y: Optional[float] = None
    eps_growth_1y: Optional[float] = None
    eps_growth_3y: Optional[float] = None
    eps_growth_5y: Optional[float] = None
    fcf_growth_1y: Optional[float] = None
    fcf_growth_3y: Optional[float] = None
    fcf_growth_5y: Optional[float] = None
    
    # Cash & Efficiency
    cfo_pat_ratio: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    asset_turnover: Optional[float] = None
    cash_conversion_cycle: Optional[float] = None
    interest_coverage: Optional[float] = None
    
    # Capital Allocation & Stability
    div_yield: Optional[float] = None
    reinvestment_rate: Optional[float] = None
    buyback_yield: Optional[float] = None
    promoter_holding_change: Optional[float] = None
    gross_margin_stability: Optional[float] = None
    roic_stability: Optional[float] = None
    
    # Risk & Quality
    altman_z: Optional[float] = None
    promoter_pledge: Optional[float] = None
    auditor_flags: bool = False
    
    # Intrinsic & EPS
    eps: Optional[float] = None
    market_cap: Optional[float] = None
    bvps: Optional[float] = None
    fcf_yield: Optional[float] = None
    owner_earnings_yield: Optional[float] = None
    
    roa: Optional[float] = None
    is_financial: bool = False
    data_freshness: str = "UNKNOWN" # LIVE, <90 days, <180 days, STALE

@dataclass(frozen=True)
class PeerMetrics:
    median_pe: Optional[float] = None
    median_pb: Optional[float] = None
    median_ev_ebitda: Optional[float] = None
    median_roe: Optional[float] = None
    median_asset_turnover: Optional[float] = None
    median_debt_equity: Optional[float] = None
    median_capital_intensity: Optional[float] = None
    peer_count: int = 0
    
    # For percentile rankings within industry
    percentiles: Dict[str, float] = field(default_factory=dict)
    
    source_type: str = "FALLBACK"
    is_complete: bool = False
    missing_critical: bool = True
    missing_minor: bool = False
    dispersion_iqr_median: Optional[float] = None

@dataclass(frozen=True)
class CorePriceData:
    price: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    high_52w: Optional[float] = None
    high_20d: Optional[float] = None
    latest_volume: Optional[float] = None
    volume_sma20: Optional[float] = None
    rs_nifty: Optional[float] = None
    rs_sector: Optional[float] = None
    eps_revision_score: Optional[float] = None 

@dataclass
class PillarResult:
    score: float # 0 to 1.0
    coverage: float # 0.0 to 1.0
    confidence: str # HIGH, MEDIUM, LOW, UNKNOWN
    freshness: str
    explanations: List[str]

@dataclass
class ValuationResult:
    fair_value: Optional[float]
    bear_value: Optional[float]
    bull_value: Optional[float]
    method: str

@dataclass
class CoreScoreResult:
    overall_score: float
    institutional_rating: str
    
    quality: PillarResult
    growth: PillarResult
    value: PillarResult
    risk: PillarResult
    capital_allocation: PillarResult
    momentum: PillarResult
    
    warnings: List[str]
    valuation: ValuationResult
    version: str
    data_version: str
    generated_at: str

    # Legacy attributes mapping for backward compatibility
    @property
    def business_quality_score(self) -> float:
        return self.quality.score * 20.0 # Map to 20 pts scale

    @property
    def financial_quality_score(self) -> float:
        return self.capital_allocation.score * 15.0

    @property
    def relative_valuation_score(self) -> float:
        return self.value.score * 30.0

    @property
    def market_structure_score(self) -> float:
        return self.momentum.score * 15.0

    @property
    def improvement_score(self) -> float:
        return self.growth.score * 10.0

    @property
    def bayesian_confidence_score(self) -> float:
        return self.quality.coverage * 100.0

    @property
    def composite_investment_score(self) -> float:
        return self.overall_score
        
    @property
    def rejection_stage(self) -> Optional[str]:
        return "KILL_GATE" if self.warnings else None

    @property
    def rejection_reason(self) -> Optional[str]:
        return self.warnings[0] if self.warnings else None

    @property
    def is_buy(self) -> bool:
        return self.overall_score >= 60.0 and len(self.warnings) == 0

# --- Utility Functions ---

def clamp(val, min_val, max_val):
    if val is None or pd.isna(val): return min_val
    return max(min_val, min(float(val), max_val))

def safe_float(val, default=None):
    if val is None or pd.isna(val): return default
    try:
        return float(val)
    except:
        return default

class MetricState:
    GOOD = "GOOD"
    AVERAGE = "AVERAGE"
    BAD = "BAD"
    MISSING = "MISSING"

def score_percentile(percentile: Optional[float]) -> Tuple[float, str]:
    if percentile is None:
        return 0.5, "Missing (Neutral)"
    if percentile >= 0.90:
        return 1.0, "Top 10%"
    elif percentile >= 0.75:
        return 0.8, "Top 25%"
    elif percentile >= 0.25:
        return 0.5, "Median"
    else:
        return 0.2, "Bottom 25%"

def evaluate_metric(val: Optional[float], thresholds: Tuple[float, float, float], higher_is_better: bool = True) -> Tuple[float, str]:
    if val is None:
        return 0.5, "Missing (Neutral)"
    exc, med, poor = thresholds
    if higher_is_better:
        if val >= exc: return 1.0, f">= {exc}"
        elif val >= med: return 0.75, f">= {med}"
        elif val >= poor: return 0.5, f">= {poor}"
        else: return 0.2, f"< {poor}"
    else:
        if val <= exc: return 1.0, f"<= {exc}"
        elif val <= med: return 0.75, f"<= {med}"
        elif val <= poor: return 0.5, f"<= {poor}"
        else: return 0.2, f"> {poor}"

def calculate_pillar(metrics: List[Tuple[float, float, str]], freshness: str) -> PillarResult:
    total_weight = 0.0
    achieved = 0.0
    valid_count = 0
    total_count = len(metrics)
    explanations = []
    
    for score, weight, desc in metrics:
        total_weight += weight
        achieved += (score * weight)
        if "Missing" not in desc:
            valid_count += 1
            explanations.append(f"{desc}: +{score * weight:.2f} pts")
        else:
            explanations.append(f"{desc}: +{score * weight:.2f} pts")
            
    final_score = achieved / total_weight if total_weight > 0 else 0.0
    coverage = valid_count / total_count if total_count > 0 else 0.0
    
    confidence = "HIGH" if coverage >= 0.8 else "MEDIUM" if coverage >= 0.5 else "LOW"
    if coverage == 0: confidence = "UNKNOWN"
    
    return PillarResult(
        score=final_score,
        coverage=coverage,
        confidence=confidence,
        freshness=freshness,
        explanations=explanations
    )

# --- Pillars ---

def score_quality(f: CoreFundamentals, p: PeerMetrics) -> PillarResult:
    metrics = []
    
    if 'roic' in p.percentiles:
        s, exp = score_percentile(p.percentiles['roic'])
        metrics.append((s, 3.0, f"ROIC ({exp})"))
    else:
        roce = safe_float(f.roce)
        s, exp = evaluate_metric(roce, (0.20, 0.12, 0.08))
        metrics.append((s, 3.0, f"ROCE ({exp})"))
        
    fcf_m = safe_float(f.fcf_margin)
    s, exp = evaluate_metric(fcf_m, (0.15, 0.08, 0.0))
    metrics.append((s, 2.0, f"FCF Margin ({exp})"))
    
    if 'asset_turnover' in p.percentiles:
        s, exp = score_percentile(p.percentiles['asset_turnover'])
        metrics.append((s, 2.0, f"Asset Turnover ({exp})"))
    else:
        ato = safe_float(f.asset_turnover)
        s, exp = evaluate_metric(ato, (1.2, 0.8, 0.5))
        metrics.append((s, 2.0, f"Asset Turnover ({exp})"))
        
    cfo_pat = safe_float(f.cfo_pat_ratio)
    s, exp = evaluate_metric(cfo_pat, (1.2, 0.8, 0.5))
    metrics.append((s, 2.0, f"Cash Conversion ({exp})"))
    
    gm_stab = safe_float(f.gross_margin_stability)
    s, exp = evaluate_metric(gm_stab, (0.02, 0.05, 0.10), higher_is_better=False)
    metrics.append((s, 1.0, f"Margin Stability ({exp})"))
    
    return calculate_pillar(metrics, f.data_freshness)

def score_growth(f: CoreFundamentals) -> PillarResult:
    metrics = []
    
    rev_cagr = safe_float(f.revenue_growth_5y)
    s, exp = evaluate_metric(rev_cagr, (0.20, 0.10, 0.05))
    metrics.append((s, 2.0, f"5Y Rev CAGR ({exp})"))
    
    eps_cagr = safe_float(f.eps_growth_5y)
    s, exp = evaluate_metric(eps_cagr, (0.20, 0.10, 0.05))
    metrics.append((s, 2.0, f"5Y EPS CAGR ({exp})"))
    
    fcf_cagr = safe_float(f.fcf_growth_5y)
    s, exp = evaluate_metric(fcf_cagr, (0.15, 0.08, 0.0))
    metrics.append((s, 1.0, f"5Y FCF CAGR ({exp})"))
    
    rev_accel = None
    if f.revenue_growth_1y is not None and f.revenue_growth_5y is not None:
        rev_accel = f.revenue_growth_1y - f.revenue_growth_5y
    s, exp = evaluate_metric(rev_accel, (0.05, 0.0, -0.05))
    metrics.append((s, 2.0, f"Sales Accel ({exp})"))
    
    # Margin Expansion proxy (1y opm vs 3y opm if available, using placeholder)
    metrics.append((0.5, 2.0, "Margin Expansion (Missing)"))
    
    return calculate_pillar(metrics, f.data_freshness)

def score_value(f: CoreFundamentals, p: PeerMetrics) -> PillarResult:
    metrics = []
    
    pe = safe_float(f.pe)
    med_pe = safe_float(p.median_pe)
    if pe and med_pe and med_pe > 0:
        ratio = pe / med_pe
        s, exp = evaluate_metric(ratio, (0.7, 1.0, 1.3), higher_is_better=False)
        metrics.append((s, 2.0, f"Relative PE ({exp})"))
    else:
        metrics.append((0.5, 2.0, "Relative PE (Missing)"))
        
    fcf_y = safe_float(f.fcf_yield)
    s, exp = evaluate_metric(fcf_y, (0.06, 0.03, 0.0))
    metrics.append((s, 2.0, f"FCF Yield ({exp})"))
    
    metrics.append((0.5, 2.0, "Intrinsic Value Discount (Missing)"))
    
    return calculate_pillar(metrics, f.data_freshness)

def score_risk(f: CoreFundamentals, p: PeerMetrics) -> Tuple[PillarResult, List[str]]:
    warnings = []
    metrics = []
    
    altman = safe_float(f.altman_z)
    if altman is not None and altman < 1.8 and not f.is_financial:
        warnings.append("⚠️ KILL-GATE: Altman Z < 1.8 (Financial Distress)")
        metrics.append((0.0, 3.0, "Altman Z (< 1.8)"))
    else:
        s, exp = evaluate_metric(altman, (3.0, 2.6, 1.8))
        metrics.append((s, 3.0, f"Altman Z ({exp})"))
        
    pledge = safe_float(f.promoter_pledge)
    if pledge is not None and pledge > 0.5:
        warnings.append("⚠️ KILL-GATE: Promoter Pledge > 50%")
        metrics.append((0.0, 3.0, "Pledge (> 50%)"))
    else:
        s, exp = evaluate_metric(pledge, (0.0, 0.1, 0.25), higher_is_better=False)
        metrics.append((s, 3.0, f"Pledge ({exp})"))
        
    if getattr(f, 'auditor_flags', False):
        warnings.append("⚠️ KILL-GATE: Auditor Issues")
        metrics.append((0.0, 4.0, "Auditor Issues"))
    else:
        metrics.append((1.0, 4.0, "No Auditor Issues"))
        
    if 'debt_percentile' in p.percentiles:
        s, exp = score_percentile(p.percentiles['debt_percentile'])
        metrics.append((s, 2.0, f"Debt Percentile ({exp})"))
    else:
        de = safe_float(f.debt_equity)
        s, exp = evaluate_metric(de, (0.2, 0.5, 1.0), higher_is_better=False)
        metrics.append((s, 2.0, f"Debt/Equity ({exp})"))
        
    return calculate_pillar(metrics, f.data_freshness), warnings

def score_capital_allocation(f: CoreFundamentals) -> PillarResult:
    metrics = []
    dy = safe_float(f.div_yield)
    s, exp = evaluate_metric(dy, (0.03, 0.01, 0.0))
    metrics.append((s, 2.0, f"Div Yield ({exp})"))
    
    rr = safe_float(f.reinvestment_rate)
    s, exp = evaluate_metric(rr, (0.6, 0.3, 0.1))
    metrics.append((s, 2.0, f"Reinvestment ({exp})"))
    
    pht = safe_float(f.promoter_holding_change)
    s, exp = evaluate_metric(pht, (0.01, 0.0, -0.01))
    metrics.append((s, 2.0, f"Promoter Holding ({exp})"))
    
    metrics.append((0.5, 2.0, "Acquisition Quality (Missing)"))
    
    return calculate_pillar(metrics, f.data_freshness)

def score_momentum(p_data: CorePriceData) -> PillarResult:
    if not p_data:
        return calculate_pillar([(0.5, 1.0, "Price Data (Missing)")], "UNKNOWN")
    metrics = []
    rs_nifty = safe_float(p_data.rs_nifty)
    s, exp = evaluate_metric(rs_nifty, (0.2, 0.0, -0.1))
    metrics.append((s, 3.0, f"RS Nifty ({exp})"))
    
    high_52 = safe_float(p_data.high_52w)
    price = safe_float(p_data.price)
    dist_52 = None
    if high_52 and price:
        dist_52 = (price / high_52) - 1
    s, exp = evaluate_metric(dist_52, (-0.05, -0.15, -0.30))
    metrics.append((s, 3.0, f"52W High Dist ({exp})"))
    
    eps_rev = safe_float(p_data.eps_revision_score)
    s, exp = evaluate_metric(eps_rev, (1.0, 0.0, -1.0))
    metrics.append((s, 2.0, f"EPS Revisions ({exp})"))
    
    return calculate_pillar(metrics, "LIVE")

def get_institutional_rating(score: float) -> str:
    if score >= 95: return "AAA"
    if score >= 90: return "AA+"
    if score >= 85: return "AA"
    if score >= 80: return "A+"
    if score >= 75: return "A"
    if score >= 70: return "BBB"
    if score >= 60: return "BB"
    if score >= 50: return "B"
    return "C"

# --- Main Engine Entry ---

def generate_core_scores(f: CoreFundamentals, p: PeerMetrics, price_data: CorePriceData, regime: str = "BULL") -> CoreScoreResult:
    q_res = score_quality(f, p)
    g_res = score_growth(f)
    v_res = score_value(f, p)
    r_res, warnings = score_risk(f, p)
    ca_res = score_capital_allocation(f)
    m_res = score_momentum(price_data)
    
    # Weighting: 30/20/20/10/10/10
    total = (
        (q_res.score * 30.0) +
        (g_res.score * 20.0) +
        (v_res.score * 20.0) +
        (r_res.score * 10.0) +
        (ca_res.score * 10.0) +
        (m_res.score * 10.0)
    )
    
    if warnings:
        total = min(total, 49.0) # Force C rating if kill-gate triggered
        
    # Multi-Scenario DCF
    dcf = None
    if f.operating_cash_flow is not None and f.revenue_growth_5y is not None:
        try:
            from valuation_utils import multi_scenario_dcf
            dcf = multi_scenario_dcf(
                fcf=float(f.operating_cash_flow), 
                growth_rate=float(f.revenue_growth_5y),
                shares=1.0 # Assuming per-share basis or market cap basis depending on caller, but FCF/share is usually needed
            )
        except ImportError:
            pass

    if dcf:
        val_res = ValuationResult(
            fair_value=round(dcf["fair_value"], 2) if dcf["fair_value"] else None,
            bear_value=round(dcf["bear_value"], 2) if dcf["bear_value"] else None,
            bull_value=round(dcf["bull_value"], 2) if dcf["bull_value"] else None,
            method="MULTI_SCENARIO_DCF"
        )
    else:
        val_res = ValuationResult(
            fair_value=None,
            bear_value=None,
            bull_value=None,
            method="MISSING"
        )
    
    return CoreScoreResult(
        overall_score=round(total, 1),
        institutional_rating=get_institutional_rating(total),
        quality=q_res,
        growth=g_res,
        value=v_res,
        risk=r_res,
        capital_allocation=ca_res,
        momentum=m_res,
        warnings=warnings,
        valuation=val_res,
        version="3.0.0",
        data_version="YF-LIVE",
        generated_at=datetime.utcnow().isoformat()
    )

# Backward compatibility alias
score_business_quality = generate_core_scores
