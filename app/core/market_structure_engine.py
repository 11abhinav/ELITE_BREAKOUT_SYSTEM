from typing import Dict, Any, Tuple
from core.models import EngineResult
from core.audit_engine import audit_engine
from core.quality_engine import safe_float, evaluate_metric

def run_market_structure_engine(symbol: str, raw_data: Dict[str, Any]) -> EngineResult:
    """
    Layer 6: Market Structure Engine
    Scores based on RS, Volume, and Trend.
    """
    total_score = 0.0
    total_confidence = 0.0
    missing = []
    reasons = []

    # Weights
    w_rs = 40.0
    w_vol = 30.0
    w_trend = 30.0

    # 1. Relative Strength (RS) - e.g. outperforming Nifty by x%
    rs = safe_float(raw_data.get('rs_rating'))
    if raw_data.get('rs_rating') is None: missing.append("rs_rating")
    s, c = evaluate_metric(symbol, "Relative Strength", rs, (85.0, 70.0, 50.0), w_rs)
    total_score += s
    total_confidence += c * (w_rs / 100)
    reasons.append(f"RS Rating: {s:.1f}/{w_rs:.1f}")

    # 2. Relative Volume & Volume Profiling
    rel_vol = safe_float(raw_data.get('relative_volume_10d'))
    dist_high = safe_float(raw_data.get('pct_from_52w_high'))
    
    if raw_data.get('relative_volume_10d') is None: missing.append("relative_volume_10d")
    s, c = evaluate_metric(symbol, "Relative Volume", rel_vol, (1.5, 1.0, 0.8), w_vol)
    
    # Volume Profiling: High volume on uptrend/consolidation = Accumulation. High volume on drawdown = Distribution.
    if rel_vol > 1.2:
        if dist_high > -0.10:
            # Near highs with high volume -> Accumulation
            s = min(w_vol, s * 1.2)
            reasons.append(f"Volume Profiling: Strong Accumulation")
        elif dist_high < -0.20:
            # Far from highs with high volume -> Distribution / Panic Selling
            s *= 0.5
            reasons.append(f"Volume Profiling: Distribution / Selling Pressure")

    total_score += s
    total_confidence += c * (w_vol / 100)
    reasons.append(f"Rel Volume: {s:.1f}/{w_vol:.1f}")

    # 3. Distance from 52W High (Trend) - negative is worse (drawdown)
    dist_high = safe_float(raw_data.get('pct_from_52w_high'))
    if raw_data.get('pct_from_52w_high') is None: missing.append("pct_from_52w_high")
    s, c = evaluate_metric(symbol, "Distance from 52W High", dist_high, (-0.05, -0.15, -0.30), w_trend)
    total_score += s
    total_confidence += c * (w_trend / 100)
    reasons.append(f"Trend (Drawdown): {s:.1f}/{w_trend:.1f}")

    if missing:
        total_confidence -= (len(missing) * 20.0)
    total_confidence = max(0.0, min(100.0, total_confidence))

    return EngineResult(
        score=round(total_score, 2),
        confidence=round(total_confidence, 2),
        missing_metrics=missing,
        reasons=reasons
    )
