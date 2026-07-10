from typing import Dict, Any, Tuple
from core.models import EngineResult
from core.audit_engine import audit_engine
from core.quality_engine import safe_float, evaluate_metric

def run_financial_strength_engine(symbol: str, raw_data: Dict[str, Any]) -> EngineResult:
    import pandas as pd
    """
    Layer 4: Financial Strength Engine
    """
    total_score = 0.0
    total_confidence = 0.0
    missing = []
    reasons = []

    # Weights
    w_debt_eq = 25.0
    w_int_cov = 25.0
    w_debt_trend = 20.0
    w_altman = 20.0
    w_curr_ratio = 10.0

    # For banks/NBFCs, we handle this slightly differently or rely on config overrides.
    # We will assume config overrides are applied to the Final Composite weight, but we can also zero out debt metrics for banks.
    is_financial = raw_data.get("is_financial", False)

    # 1. Debt/Equity or CAR (for financials)
    if is_financial:
        car = safe_float(raw_data.get('capital_adequacy_ratio', 0.15)) # Assume 15% if missing
        car = safe_float(raw_data.get('capital_adequacy_ratio', 0.15)) # Assume 15% if missing
        if raw_data.get('capital_adequacy_ratio') is None or pd.isna(raw_data.get('capital_adequacy_ratio')): missing.append("capital_adequacy_ratio")
        s, c = evaluate_metric(symbol, "Capital Adequacy Ratio (CAR)", car, (0.18, 0.15, 0.12), w_debt_eq)
        reasons.append(f"CAR (Financials): {s:.1f}/{w_debt_eq:.1f}")
    else:
        debt_eq = safe_float(raw_data.get('debt_equity'))
        debt_eq = safe_float(raw_data.get('debt_equity'))
        if raw_data.get('debt_equity') is None or pd.isna(raw_data.get('debt_equity')): missing.append("debt_equity")
        s, c = evaluate_metric(symbol, "Debt/Equity", debt_eq, (0.2, 0.5, 1.0), w_debt_eq, higher_is_better=False)
        reasons.append(f"Debt/Equity: {s:.1f}/{w_debt_eq:.1f}")
    total_score += s
    total_confidence += c * (w_debt_eq / 100)

    # 2. Interest Coverage Ratio or Gross NPA (for financials)
    if is_financial:
        gnpa = safe_float(raw_data.get('gross_npa', 0.02)) # Assume 2% if missing
        gnpa = safe_float(raw_data.get('gross_npa', 0.02)) # Assume 2% if missing
        if raw_data.get('gross_npa') is None or pd.isna(raw_data.get('gross_npa')): missing.append("gross_npa")
        s, c = evaluate_metric(symbol, "Gross NPA", gnpa, (0.015, 0.03, 0.05), w_int_cov, higher_is_better=False)
        reasons.append(f"Gross NPA (Financials): {s:.1f}/{w_int_cov:.1f}")
    else:
        int_cov = safe_float(raw_data.get('interest_coverage_ratio'))
        int_cov = safe_float(raw_data.get('interest_coverage_ratio'))
        if raw_data.get('interest_coverage_ratio') is None or pd.isna(raw_data.get('interest_coverage_ratio')): missing.append("interest_coverage_ratio")
        s, c = evaluate_metric(symbol, "Interest Coverage", int_cov, (10.0, 5.0, 2.0), w_int_cov)
        reasons.append(f"Interest Coverage: {s:.1f}/{w_int_cov:.1f}")
    total_score += s
    total_confidence += c * (w_int_cov / 100)

    # 3. Debt Trend or Net NPA (for financials)
    if is_financial:
        nnpa = safe_float(raw_data.get('net_npa', 0.01))
        nnpa = safe_float(raw_data.get('net_npa', 0.01))
        if raw_data.get('net_npa') is None or pd.isna(raw_data.get('net_npa')): missing.append("net_npa")
        s, c = evaluate_metric(symbol, "Net NPA", nnpa, (0.005, 0.015, 0.03), w_debt_trend, higher_is_better=False)
        reasons.append(f"Net NPA (Financials): {s:.1f}/{w_debt_trend:.1f}")
    else:
        debt_trend = safe_float(raw_data.get('debt_yoy_growth'))
        debt_eq = safe_float(raw_data.get('debt_equity'))
        if debt_eq == 0.0:
            s, c = w_debt_trend, 100.0
            reasons.append(f"Debt Trend (Zero Debt): {s:.1f}/{w_debt_trend:.1f}")
        else:
            if raw_data.get('debt_yoy_growth') is None or pd.isna(raw_data.get('debt_yoy_growth')): missing.append("debt_yoy_growth")
            s, c = evaluate_metric(symbol, "Debt Trend YoY", debt_trend, (-0.10, 0.0, 0.15), w_debt_trend, higher_is_better=False)
            reasons.append(f"Debt Trend: {s:.1f}/{w_debt_trend:.1f}")
    total_score += s
    total_confidence += c * (w_debt_trend / 100)

    # 4. Altman Z-Score
    altman = safe_float(raw_data.get('altman_z'))
    if is_financial:
        s, c = w_altman, 100.0
        reasons.append(f"Altman Z (Skipped for Financials): {s:.1f}/{w_altman:.1f}")
    else:
        if raw_data.get('altman_z') is None or pd.isna(raw_data.get('altman_z')): missing.append("altman_z")
        s, c = evaluate_metric(symbol, "Altman Z-Score", altman, (3.0, 2.6, 1.8), w_altman)
        reasons.append(f"Altman Z-Score: {s:.1f}/{w_altman:.1f}")
    total_score += s
    total_confidence += c * (w_altman / 100)

    # 5. Current Ratio / Off-Balance Sheet Risk
    curr_ratio = safe_float(raw_data.get('current_ratio'))
    if is_financial:
        # Liquidity Coverage Ratio (LCR) proxy for banks
        lcr = safe_float(raw_data.get('liquidity_coverage_ratio', 120.0))
        lcr = safe_float(raw_data.get('liquidity_coverage_ratio', 120.0))
        if raw_data.get('liquidity_coverage_ratio') is None or pd.isna(raw_data.get('liquidity_coverage_ratio')): missing.append("liquidity_coverage_ratio")
        s, c = evaluate_metric(symbol, "Liquidity Coverage", lcr, (130.0, 110.0, 90.0), w_curr_ratio)
        reasons.append(f"Liquidity Coverage (Financials): {s:.1f}/{w_curr_ratio:.1f}")
    else:
        if raw_data.get('current_ratio') is None or pd.isna(raw_data.get('current_ratio')): missing.append("current_ratio")
        s, c = evaluate_metric(symbol, "Current Ratio", curr_ratio, (2.0, 1.5, 1.0), w_curr_ratio)
        
        # Off-balance sheet risk proxy: Working Capital Cycle bloat
        wc_days = safe_float(raw_data.get('working_capital_days'))
        if wc_days > 180:
            s *= 0.5
            reasons.append(f"Working Capital Bloat ({wc_days} days)")
        
        reasons.append(f"Current Ratio / Liquidity: {s:.1f}/{w_curr_ratio:.1f}")
    total_score += s
    total_confidence += c * (w_curr_ratio / 100)

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
