import re

with open("app/multibagger.py", "r") as f:
    content = f.read()

# Replace passes_kill_gates(f) with check_kill_gates
# Wait, let's just do a manual string replace.

# Remove the old passes_kill_gates function
content = re.sub(r'def passes_kill_gates.*?return True, None\n', '', content, flags=re.DOTALL)

# Let's fix the loop logic
old_loop_start = """    for f in fundamentals_list:
        sym = f.symbol
        price_data = price_data_map.get(sym)"""

new_loop = """    import json
    import os
    from datetime import datetime
    
    # Init Rejection Log
    log_date = datetime.now().strftime('%Y-%m-%d')
    rejection_log_path = f"logs/rejections_{log_date}.jsonl"
    os.makedirs("logs", exist_ok=True)
    
    for f in fundamentals_list:
        sym = f.symbol
        price_data = price_data_map.get(sym)
        if not price_data:
            continue
            
        alert_triggered = False
        alert_reason = ""
        cp = None
        is_fallback = True
        
        # Prepare Core Fundamentals
        cf = CoreFundamentals(
            symbol=sym,
            sector=f.sector,
            canonical_industry=f.canonical_industry,
            pe=f.pe,
            pb=f.pb,
            roe=f.roe,
            roce=None, # Ticker tape data doesn't give ROCE easily here
            debt_equity=f.debt_equity,
            operating_margin=f.operating_margin,
            revenue_growth_3y=None,
            revenue_growth_5y=None,
            eps_growth_3y=None,
            eps_growth_5y=None,
            revenue_growth_1y=f.revenue_growth,
            eps_growth_1y=f.earnings_growth,
            fcf_margin=None,
            cfo_pat_ratio=None,
            operating_cash_flow=f.operating_cash_flow,
            yoy_profit_growth=f.earnings_growth,
            net_losses_3y=False,
            div_yield=f.div_yield,
            eps=f.eps,
            bvps=f.bvps,
            roa=f.roa,
            is_financial=is_financial_sector(f.sector)
        )
        
        p_data = peer_medians.get(sym, {})
        cp = PeerMetrics(
            median_pe=p_data.get("median_pe"),
            median_pb=p_data.get("median_pb"),
            median_roe=p_data.get("median_roe", 0) / 100.0 if p_data.get("median_roe") else None,
            median_ev_ebitda=p_data.get("median_ev_ebitda"),
            median_div_yield=p_data.get("median_div_yield", 0) / 100.0 if p_data.get("median_div_yield") else None,
            median_peg=p_data.get("median_peg"),
            peer_count=p_data.get("peer_count", 0),
            dispersion_iqr_median=p_data.get("dispersion_iqr_median"),
            source_type=p_data.get("source_type", "FALLBACK"),
            is_complete=(p_data.get("median_pe") is not None and p_data.get("median_pb") is not None),
            missing_critical=(p_data.get("median_pe") is None),
            missing_minor=False
        )
        is_fallback = (cp.source_type == "FALLBACK")
        
        c_price = CorePriceData(
            price=price_data.price,
            sma_50=price_data.sma_50,
            sma_200=price_data.sma_200,
            high_20d=price_data.high_20d,
            latest_volume=price_data.latest_volume,
            volume_sma20=price_data.volume_sma20
        )
        
        # Generate Unified Scores with new Hierarchical Engine
        scores = generate_core_scores(cf, cp, c_price, regime=market_regime)
        
        if not scores.is_buy:
            # Log rejection
            rej_data = {
                "symbol": sym,
                "timestamp": datetime.now().isoformat(),
                "phase": scores.rejection_stage,
                "reason": scores.rejection_reason,
                "scores": {
                    "bqs": scores.business_quality_score,
                    "fqs": scores.financial_quality_score,
                    "rvs": scores.relative_valuation_score,
                    "trend": scores.market_structure_score
                }
            }
            with open(rejection_log_path, "a") as rf:
                rf.write(json.dumps(rej_data) + "\\n")
                
            status = "INVALIDATED"
            bucket = "Invalidated"
            notes = f"{scores.rejection_stage}: {scores.rejection_reason}"
            cqs = 0.0
            pas = 0.0
            trend = 0.0
            total = 0.0
            buy_low = 0
            buy_high = 0
        else:
            cqs = scores.business_quality_score
            pas = scores.relative_valuation_score
            trend = scores.market_structure_score
            total = scores.composite_investment_score
            
            # Use Configured Buy Zone
            from core_score_engine import get_engine_config
            cfg = get_engine_config().get("buy_zone", {})
            buffer = cfg.get("breakout_buffer", 0.02)
            
            buy_low = price_data.sma_200 if price_data.sma_200 > 0 else (price_data.price * 0.5)
            buy_high = min(price_data.price * (1 + buffer), price_data.high_20d)
            if buy_low >= buy_high:
                buy_low = buy_high * 0.9
                
            alert_triggered, alert_reason = should_trigger_alert(price_data, scores)
            
            if alert_triggered:
                status = "ALERT_TRIGGERED"
                bucket = "Value Breakout"
            else:
                status = "WAITING_BUY_ZONE"
                bucket = "Watchlist Waiting"
                
            notes = alert_reason"""

content = content.replace(old_loop_start, new_loop)

# Fix where it says 'fair_val_result = FairValueResult(...)' to be deleted if it exists.
# We will just write it and check.

with open("app/multibagger.py", "w") as f:
    f.write(content)

