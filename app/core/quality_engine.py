import pandas as pd
from typing import Dict, Any, Tuple
from core.models import EngineResult
from core.audit_engine import audit_engine

def safe_float(val: Any, default=0.0) -> float:
    try:
        import pandas as pd
        if val is None or pd.isna(val):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

def evaluate_metric(symbol: str, metric_name: str, val: float, thresholds: Tuple[float, float, float], weight: float, higher_is_better: bool = True) -> Tuple[float, float]:
    """Returns (score_points, confidence). Score is out of `weight`."""
    if val == 0.0 and metric_name not in ["fcf_margin", "pct_from_52w_high", "rs_rating"]: # 0 is often missing data unless explicitly 0
        audit_engine.log(symbol, "Quality", "Warning", f"Missing/Zero {metric_name}", metric_name, val)
        return (weight * 0.5, 0.0) # 50% score for missing, 0 confidence

    exc, med, poor = thresholds
    confidence = 100.0
    
    if higher_is_better:
        if val >= exc: 
            score = weight
            reason = f"Excellent (>= {exc})"
        elif val >= med: 
            score = weight * 0.75
            reason = f"Good (>= {med})"
        elif val >= poor: 
            score = weight * 0.5
            reason = f"Average (>= {poor})"
        else: 
            score = weight * 0.2
            reason = f"Poor (< {poor})"
    else:
        if val <= exc: 
            score = weight
            reason = f"Excellent (<= {exc})"
        elif val <= med: 
            score = weight * 0.75
            reason = f"Good (<= {med})"
        elif val <= poor: 
            score = weight * 0.5
            reason = f"Average (<= {poor})"
        else: 
            score = weight * 0.2
            reason = f"Poor (> {poor})"

    audit_engine.log(symbol, "Quality", "Passed", reason, metric_name, val)
    return score, confidence

def run_quality_engine(symbol: str, raw_data: Dict[str, Any], weights: Dict[str, float]) -> EngineResult:
    """
    Layer 2: Business Quality
    Splits into: Profitability, Moat, Capital Efficiency, Cash Conversion, Earnings Quality
    """
    total_score = 0.0
    total_confidence = 0.0
    max_score = 100.0
    missing = []
    reasons = []

    # 1. Profitability (Operating Margin)
    w_prof = weights.get('profitability', 0.30) * 100
    opm = safe_float(raw_data.get('operating_margin_ttm'))
    if opm == 0.0: missing.append("operating_margin_ttm")
    
    sector = raw_data.get('sector', 'default').lower()
    if sector in ['software', 'pharma', 'fmcg']:
        prof_thresholds = (0.20, 0.15, 0.10)
    elif sector in ['retail', 'manufacturing', 'cyclical']:
        prof_thresholds = (0.12, 0.08, 0.04)
    else:
        prof_thresholds = (0.15, 0.10, 0.05)
        
    s, c = evaluate_metric(symbol, "Operating Margin", opm, prof_thresholds, w_prof)
    total_score += s
    total_confidence += c * (w_prof / 100)
    reasons.append(f"Profitability: {s:.1f}/{w_prof:.1f}")

    # 2. Moat (Gross Margin Stability / ROIC Consistency)
    w_moat = weights.get('moat', 0.20) * 100
    gm_stab = safe_float(raw_data.get('gross_margin_stability'))
    s, c = evaluate_metric(symbol, "Gross Margin Stability", gm_stab, (0.02, 0.05, 0.10), w_moat, higher_is_better=False)
    if gm_stab == 0.0: missing.append("gross_margin_stability")
    total_score += s
    total_confidence += c * (w_moat / 100)
    reasons.append(f"Moat: {s:.1f}/{w_moat:.1f}")

    # 3. Capital Efficiency (ROCE vs Asset Turnover)
    w_cap = weights.get('capital_efficiency', 0.20) * 100
    roce = safe_float(raw_data.get('roce'))
    if roce == 0.0: missing.append("roce")
    
    # Capital heavy / cyclicals focus heavily on asset turns
    if sector in ['cyclical', 'manufacturing', 'capital goods']:
        asset_turnover = safe_float(raw_data.get('asset_turnover', 1.0))
        # If asset turn is improving or high, give bonus or evaluate that instead
        s1, c1 = evaluate_metric(symbol, "ROCE", roce, (0.15, 0.10, 0.05), w_cap * 0.5)
        s2, c2 = evaluate_metric(symbol, "Asset Turnover", asset_turnover, (1.5, 1.0, 0.6), w_cap * 0.5)
        s = s1 + s2
        c = (c1 + c2) / 2
        reasons.append(f"Capital Efficiency (Asset Turn focus): {s:.1f}/{w_cap:.1f}")
    else:
        s, c = evaluate_metric(symbol, "ROCE", roce, (0.20, 0.15, 0.10), w_cap)
        reasons.append(f"Capital Efficiency: {s:.1f}/{w_cap:.1f}")
        
    total_score += s
    total_confidence += c * (w_cap / 100)

    # 4. Cash Conversion (CFO / PAT)
    w_cash = weights.get('cash_conversion', 0.15) * 100
    cfo_pat = safe_float(raw_data.get('cfo_pat_ratio'))
    if cfo_pat == 0.0: missing.append("cfo_pat_ratio")
    s, c = evaluate_metric(symbol, "Cash Conversion (CFO/PAT)", cfo_pat, (1.2, 0.8, 0.5), w_cash)
    total_score += s
    total_confidence += c * (w_cash / 100)
    reasons.append(f"Cash Conversion: {s:.1f}/{w_cash:.1f}")

    # 5. Earnings Quality (FCF Margin)
    w_earn = weights.get('earnings_quality', 0.15) * 100
    fcf_m = safe_float(raw_data.get('fcf_margin'))
    # FCF margin can be valid 0.0, but let's assume missing if None
    if raw_data.get('fcf_margin') is None or pd.isna(raw_data.get('fcf_margin')): missing.append("fcf_margin")
    s, c = evaluate_metric(symbol, "FCF Margin", fcf_m, (0.15, 0.08, 0.0), w_earn)
    total_score += s
    total_confidence += c * (w_earn / 100)
    reasons.append(f"Earnings Quality: {s:.1f}/{w_earn:.1f}")

    # Penalize confidence if missing metrics
    if missing:
        total_confidence -= (len(missing) * 15.0)
    total_confidence = max(0.0, min(100.0, total_confidence))

    return EngineResult(
        score=round(total_score, 2),
        confidence=round(total_confidence, 2),
        missing_metrics=missing,
        reasons=reasons
    )
