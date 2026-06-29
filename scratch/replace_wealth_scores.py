import re

def main():
    with open('app/wealth_engine.py', 'r') as f:
        content = f.read()

    # We want to replace the old calculate_* functions with apply_core_engine_scores
    new_funcs = """
def apply_core_engine_scores(r, sector_stats: dict = None) -> pd.Series:
    from core_score_engine import CoreFundamentals, PeerMetrics, CorePriceData, generate_core_scores
    
    def _safe_float(val, default=0.0):
        if val is None: return default
        try:
            f = float(val)
            return default if pd.isna(f) else f
        except (ValueError, TypeError):
            return default

    def _safe_bool(val):
        if val is None or pd.isna(val): return False
        return bool(val)
        
    symbol = str(r.get("Stock", ""))
    
    f = CoreFundamentals(
        symbol=symbol,
        sector=str(r.get("Sector", "")),
        pe=_safe_float(r.get("P/E Ratio"), None),
        pb=_safe_float(r.get("P/B Ratio"), None),
        roe=_safe_float(r.get("ROE %"), None) / 100.0 if r.get("ROE %") is not None else None,
        roce=_safe_float(r.get("ROCE %"), None) / 100.0 if r.get("ROCE %") is not None else None,
        debt_equity=_safe_float(r.get("Debt/Equity"), None),
        operating_margin=_safe_float(r.get("OPM %"), None) / 100.0 if r.get("OPM %") is not None else None,
        revenue_growth_3y=_safe_float(r.get("3Y Revenue %"), None) / 100.0 if r.get("3Y Revenue %") is not None else None,
        revenue_growth_5y=_safe_float(r.get("5Y Revenue %"), None) / 100.0 if r.get("5Y Revenue %") is not None else None,
        eps_growth_3y=_safe_float(r.get("3Y EPS %"), None) / 100.0 if r.get("3Y EPS %") is not None else None,
        eps_growth_5y=_safe_float(r.get("5Y EPS %"), None) / 100.0 if r.get("5Y EPS %") is not None else None,
        revenue_growth_1y=_safe_float(r.get("YOY Revenue %"), None) / 100.0 if r.get("YOY Revenue %") is not None else None,
        eps_growth_1y=_safe_float(r.get("YOY EPS %"), None) / 100.0 if r.get("YOY EPS %") is not None else None,
        fcf_margin=_safe_float(r.get("FCF Margin %"), None) / 100.0 if r.get("FCF Margin %") is not None else None,
        cfo_pat_ratio=_safe_float(r.get("CFO/PAT"), None),
        operating_cash_flow=_safe_float(r.get("Operating Cash Flow"), None),
        yoy_profit_growth=_safe_float(r.get("YOY Profit %"), None) / 100.0 if r.get("YOY Profit %") is not None else None,
        net_losses_3y=_safe_bool(r.get("Net Losses 3Y")),
        div_yield=_safe_float(r.get("Div Yield %"), 0.0) / 100.0 if r.get("Div Yield %") is not None else 0.0,
        eps=_safe_float(r.get("EPS"), None),
        bvps=_safe_float(r.get("BVPS"), None),
        roa=_safe_float(r.get("ROA %"), None) / 100.0 if r.get("ROA %") is not None else None,
        is_financial=(str(r.get("Path", "")) == "Financial")
    )
    
    ps = sector_stats.get(symbol, {}) if sector_stats else {}
    
    p = PeerMetrics(
        median_pe=ps.get("effective_pe"),
        median_pb=ps.get("effective_pb"),
        median_roe=ps.get("median_roe", 0) / 100.0 if ps.get("median_roe") is not None else None,
        median_peg=ps.get("median_peg"),
        peer_count=ps.get("peer_count", 0),
        dispersion_iqr_median=ps.get("dispersion_iqr_median"),
        source_type=ps.get("source_type", "FALLBACK"),
        is_complete=(ps.get("peer_count", 0) >= 15),
        missing_critical=(ps.get("effective_pe") is None and ps.get("effective_pb") is None),
        missing_minor=(ps.get("dispersion_iqr_median") is None)
    )
    
    pd_data = CorePriceData(
        price=_safe_float(r.get("cmp")),
        sma_50=_safe_float(r.get("sma_50")),
        sma_200=_safe_float(r.get("sma_200")),
        high_20d=_safe_float(r.get("high_20d")),
        latest_volume=_safe_float(r.get("Volume")),
        volume_sma20=_safe_float(r.get("volume_sma20"))
    )
    
    ai_conf = r.get("AI_Confidence", 0)
    overlays = 0.0
    if ai_conf >= 8: overlays = 5.0
    elif ai_conf == 7: overlays = 2.0
    elif 1 <= ai_conf <= 4: overlays = -5.0
    
    scores = generate_core_scores(f, p, pd_data, strategic_overlays=overlays)
    
    return pd.Series({
        "CIS": scores.composite_investment_score,
        "RVS": scores.relative_valuation_score,
        "BQS": scores.business_quality_score,
        "Reliability": scores.reliability_score,
        "Base_FV": scores.base_fair_value,
        "Bull_FV": scores.bull_fair_value
    })
"""
    
    # regex replace the old calculate functions up to determine_portfolio_bucket
    pattern = re.compile(r'def calculate_valuation_score\(r, sector_stats: dict = None\) -> int:.*?def determine_portfolio_bucket', re.DOTALL)
    
    new_content = pattern.sub(new_funcs + '\n\ndef determine_portfolio_bucket', content)
    
    # replace the apply block around line 800
    apply_old = '''        # Apply 100-point score
        wealth_df["FM_Score"] = wealth_df.apply(lambda r: calculate_100_point_score(r, sector_stats), axis=1)
        
        # Calculate valuation & consistency score separately for dashboard visibility
        wealth_df["Valuation_Score"] = wealth_df.apply(lambda r: calculate_valuation_score(r, sector_stats), axis=1)
        wealth_df["Consistency_Score"] = wealth_df.apply(calculate_consistency_score, axis=1)
        wealth_df["Portfolio_Bucket"] = wealth_df.apply(lambda r: determine_portfolio_bucket(r, nifty_dist_52w), axis=1)'''
        
    apply_new = '''        # Apply Unified Core Engine Scores
        scores_df = wealth_df.apply(lambda r: apply_core_engine_scores(r, sector_stats), axis=1)
        wealth_df["FM_Score"] = scores_df["CIS"]
        wealth_df["Valuation_Score"] = scores_df["RVS"]
        wealth_df["Consistency_Score"] = scores_df["BQS"]  # Mapped to BQS for dashboard
        wealth_df["Reliability"] = scores_df["Reliability"]
        wealth_df["Base_FV"] = scores_df["Base_FV"]
        wealth_df["Bull_FV"] = scores_df["Bull_FV"]
        
        wealth_df["Portfolio_Bucket"] = wealth_df.apply(lambda r: determine_portfolio_bucket(r, nifty_dist_52w), axis=1)'''
        
    new_content = new_content.replace(apply_old, apply_new)
    
    with open('app/wealth_engine.py', 'w') as f:
        f.write(new_content)

if __name__ == "__main__":
    main()
