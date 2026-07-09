from typing import Dict, Any, Tuple
from core.models import EngineResult
from core.audit_engine import audit_engine
from core.quality_engine import safe_float, evaluate_metric

def run_growth_engine(symbol: str, raw_data: Dict[str, Any]) -> EngineResult:
    """
    Layer 3: Growth & Capital Allocation
    """
    total_score = 0.0
    total_confidence = 0.0
    missing = []
    reasons = []

    # Hardcoded sub-weights for Growth (can be moved to config later if needed)
    w_rev = 30.0
    w_eps = 30.0
    w_fcf = 20.0
    w_reinv = 20.0

    # 1. Revenue CAGR (3Y or 5Y)
    rev_cagr = safe_float(raw_data.get('revenue_cagr_3y'))
    # Smooth base effects (if 1Y is absurdly high but 3Y is missing, we must be careful)
    if rev_cagr == 0.0:
        rev_1y = safe_float(raw_data.get('revenue_growth_1y'))
        if rev_1y > 0.0:
            rev_cagr = min(rev_1y, 0.20) # Cap at 20% to smooth low base effects if only 1Y is available
        else:
            missing.append("revenue_cagr_3y")

    s, c = evaluate_metric(symbol, "Revenue CAGR", rev_cagr, (0.15, 0.10, 0.05), w_rev)
    total_score += s
    total_confidence += c * (w_rev / 100)
    reasons.append(f"Revenue Growth: {s:.1f}/{w_rev:.1f}")

    # 2. PAT/EPS CAGR (renamed from eps_cagr_3y to pat_cagr_3y — checks both for backwards compat)
    _pat_cagr_raw = raw_data.get('pat_cagr_3y')
    if _pat_cagr_raw is None:
        _pat_cagr_raw = raw_data.get('eps_cagr_3y')  # legacy cache compat
    eps_cagr = safe_float(_pat_cagr_raw)
    if eps_cagr == 0.0: missing.append("pat_cagr_3y")
    s, c = evaluate_metric(symbol, "EPS CAGR", eps_cagr, (0.18, 0.12, 0.05), w_eps)
    total_score += s
    total_confidence += c * (w_eps / 100)
    reasons.append(f"EPS Growth: {s:.1f}/{w_eps:.1f}")

    # 3. FCF CAGR
    fcf_cagr = safe_float(raw_data.get('fcf_cagr_3y'))
    if fcf_cagr == 0.0: missing.append("fcf_cagr_3y")
    s, c = evaluate_metric(symbol, "FCF CAGR", fcf_cagr, (0.20, 0.10, 0.0), w_fcf)
    total_score += s
    total_confidence += c * (w_fcf / 100)
    reasons.append(f"FCF Growth: {s:.1f}/{w_fcf:.1f}")

    # 4. Reinvestment Rate & Sustainable Growth Rate (SGR)
    reinv = safe_float(raw_data.get('reinvestment_rate'))
    roe = safe_float(raw_data.get('roce')) # Proxy
    if reinv == 0.0: missing.append("reinvestment_rate")
    
    # Calculate Sustainable Growth Rate = ROE * Retention Ratio
    sgr = roe * reinv
    
    # Evaluate SGR explicitly instead of purely looking at reinvestment rate isolated
    s, c = evaluate_metric(symbol, "Sustainable Growth Rate", sgr, (0.15, 0.10, 0.05), w_reinv)
    
    # Quality of Growth Check: If historical EPS growth is massively higher than SGR, it's likely debt-fueled
    # Or if Debt YoY > Asset YoY
    debt_growth = safe_float(raw_data.get('debt_yoy_growth'))
    asset_growth = safe_float(raw_data.get('asset_yoy_growth'))
    if debt_growth > 0.20 and debt_growth > (asset_growth + 0.10):
        # Heavy penalty for debt-fueled asset growth
        s *= 0.5
        reasons.append(f"Growth heavily debt-fueled (Debt Growth {debt_growth*100:.1f}%)")

    total_score += s
    total_confidence += c * (w_reinv / 100)
    reasons.append(f"SGR / Reinvestment: {s:.1f}/{w_reinv:.1f}")

    # Penalize confidence if missing metrics
    if missing:
        total_confidence -= (len(missing) * 20.0)
    total_confidence = max(0.0, min(100.0, total_confidence))

    return EngineResult(
        score=round(total_score, 2),
        confidence=round(total_confidence, 2),
        missing_metrics=missing,
        reasons=reasons
    )
