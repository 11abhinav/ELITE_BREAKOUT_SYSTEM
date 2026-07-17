#!/usr/bin/env python3
import os
import sys
import json
import argparse
from datetime import datetime, timedelta
import pandas as pd
import yfinance as yf


# Append project root to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import get_connection
import config

ACTIVE_VERSION = "SL_ENGINE_V6"

LOOKAHEAD_WINDOWS = {
    "MULTI_TF": 1,
    "LIVE_1H": 5,
    "EOD": 15,
    "REVERSAL": 20
}

def fetch_forward_data(symbol, start_date, lookahead_days):
    """Fetches forward data to calculate MFE and MAE for rejected alerts."""
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=lookahead_days * 2 + 5) # Buffer for weekends
    
    ticker = yf.Ticker(symbol + ".NS")
    df = ticker.history(start=start_dt.strftime("%Y-%m-%d"), end=end_dt.strftime("%Y-%m-%d"))
    if df.empty or len(df) == 0:
        return None
    
    # Restrict to exactly `lookahead_days` trading days
    df = df.head(lookahead_days)
    return df

def calculate_mfe_mae(df, entry_price):
    if df is None or df.empty or entry_price <= 0:
        return None, None, None, None
        
    highs = df["High"].values
    lows = df["Low"].values
    
    mfe_idx = highs.argmax()
    mae_idx = lows.argmin()
    
    mfe_price = highs[mfe_idx]
    mae_price = lows[mae_idx]
    
    mfe_pct = (mfe_price - entry_price) / entry_price * 100
    mae_pct = (mae_price - entry_price) / entry_price * 100
    
    return mfe_pct, mae_pct, int(mfe_idx + 1), int(mae_idx + 1)

def run_report(start_date=None, end_date=None, mode="scheduled"):
    print("=" * 80)
    print(f"📊 V6 ARCHITECTURE VALIDATION REPORT ({mode.upper()} MODE) 📊")
    print(f"Engine: {ACTIVE_VERSION}")
    print("=" * 80)
    
    date_filter = ""
    params = []
    if start_date:
        date_filter += " AND DATE(created_at) >= %s"
        params.append(start_date)
    if end_date:
        date_filter += " AND DATE(created_at) <= %s"
        params.append(end_date)
        
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Funnel Leakage (Rejections)
            cur.execute(f"SELECT scanner, rejection_reason FROM rejected_alerts WHERE engine_version = %s {date_filter}", [ACTIVE_VERSION] + params)
            rejections = cur.fetchall()
            
            # 2. Executed Alerts
            alert_filter = date_filter.replace("created_at", "alert_date")
            cur.execute(f"SELECT id, scanner, symbol, entry_price, target_1, stop_loss, status, closed_at, alert_date, context, exit_history FROM alerts WHERE engine_version = %s {alert_filter}", [ACTIVE_VERSION] + params)
            alerts = cur.fetchall()
            
    print("\n[1] 🚪 FUNNEL LEAKAGE & REJECTION DISTRIBUTION")
    
    gates = ["GATE_NATURAL_RR", "GATE_REWARD_POTENTIAL", "GATE_TARGET_QUALITY", "GATE_SCORE_FILTER"]
    rejection_counts = {gate: 0 for gate in gates}
    rejection_counts["OTHER"] = 0
    
    scanner_rejections = {}
    for r in rejections:
        scanner = r[0]
        reason = r[1] or ""
        scanner_rejections[scanner] = scanner_rejections.get(scanner, [])
        scanner_rejections[scanner].append(r)
        
        matched = False
        for gate in gates:
            if gate in reason:
                rejection_counts[gate] += 1
                matched = True
                break
        if not matched:
            rejection_counts["OTHER"] += 1
            
    total_rejections = len(rejections)
    total_alerts = len(alerts)
    total_entered = total_rejections + total_alerts
    
    print(f"{'Gate':<25} | {'Passed':<8} | {'Rejected':<8} | {'Pass %'}")
    print("-" * 60)
    
    running_total = total_entered
    for gate in gates:
        rejected = rejection_counts[gate]
        passed = running_total - rejected
        pass_pct = round((passed / running_total) * 100, 1) if running_total > 0 else 0
        print(f"{gate:<25} | {passed:<8} | {rejected:<8} | {pass_pct}%")
        running_total = passed
        
    print(f"{'Final Alerts':<25} | {total_alerts:<8} | {'-':<8} | -")

    print("\n[2] 🎯 TARGET DISTRIBUTION & AVERAGES")
    
    scanner_stats = {}
    
    score_brackets = {
        "<30": {"count": 0, "wins": 0, "rr": [], "rp": [], "hold_days": []},
        "30-40": {"count": 0, "wins": 0, "rr": [], "rp": [], "hold_days": []},
        "40-60": {"count": 0, "wins": 0, "rr": [], "rp": [], "hold_days": []},
        ">60": {"count": 0, "wins": 0, "rr": [], "rp": [], "hold_days": []}
    }
    
    for a in alerts:
        scanner = a[1]
        entry = float(a[3])
        t1 = float(a[4]) if a[4] else entry
        sl = float(a[5]) if a[5] else entry
        status = a[6]
        ctx_str = a[9]
        ctx = json.loads(ctx_str) if isinstance(ctx_str, str) else (ctx_str or {})
        
        hist_str = a[10] if len(a) > 10 else "[]"
        hist = json.loads(hist_str) if isinstance(hist_str, str) else (hist_str or [])
        
        sl_res = ctx.get("sl_result", {})
        rr = sl_res.get("natural_rr", 0.0)
        rp = ((t1 - entry) / entry * 100) if entry > 0 else 0.0
        stop_pct = ((entry - sl) / entry * 100) if entry > 0 else 0.0
        
        buf_val = sl_res.get("buffer_value", 0.0)
        buf_pct = (buf_val / entry * 100) if entry > 0 else 0.0
        anchor_type = sl_res.get("anchor_type", "UNKNOWN")
        anchor_score = sl_res.get("anchor_score", 0)
        
        b = "UNKNOWN"
        if anchor_score > 0:
            if anchor_score < 30: b = "<30"
            elif anchor_score <= 40: b = "30-40"
            elif anchor_score <= 60: b = "40-60"
            else: b = ">60"
            
            score_brackets[b]["count"] += 1
            score_brackets[b]["rr"].append(rr)
            score_brackets[b]["rp"].append(rp)
            if status == "WIN":
                score_brackets[b]["wins"] += 1
                
            closed_at = a[7]
            alert_date = a[8]
            if closed_at and alert_date:
                try:
                    fmt = "%Y-%m-%d %H:%M:%S"
                    # Handle possible string formats or datetime objects
                    import datetime
                    c_dt = closed_at if isinstance(closed_at, datetime.datetime) else datetime.datetime.strptime(str(closed_at)[:19], fmt)
                    a_dt = alert_date if isinstance(alert_date, datetime.datetime) else datetime.datetime.strptime(str(alert_date)[:19], fmt)
                    days = (c_dt - a_dt).days + ((c_dt - a_dt).seconds / 86400.0)
                    score_brackets[b]["hold_days"].append(max(0, days))
                except Exception as e:
                    pass
        
        # Check gap loss
        is_gap_loss = any(e.get("type") == "GAP_LOSS" for e in hist)
        is_normal_loss = status == "LOSS" and not is_gap_loss
        
        if scanner not in scanner_stats:
            scanner_stats[scanner] = {
                "rr": [], "rp": [], "stop": [], "buffer_pct": [], 
                "wins": 0, "losses": 0, "gap_losses": 0, "normal_losses": 0,
                "anchors": {}
            }
            
        scanner_stats[scanner]["rr"].append(rr)
        scanner_stats[scanner]["rp"].append(rp)
        scanner_stats[scanner]["stop"].append(stop_pct)
        if buf_pct > 0: scanner_stats[scanner]["buffer_pct"].append(buf_pct)
        
        if anchor_type not in scanner_stats[scanner]["anchors"]:
            scanner_stats[scanner]["anchors"][anchor_type] = {"count": 0, "wins": 0, "losses": 0}
            
        scanner_stats[scanner]["anchors"][anchor_type]["count"] += 1
        
        if status == "WIN": 
            scanner_stats[scanner]["wins"] += 1
            scanner_stats[scanner]["anchors"][anchor_type]["wins"] += 1
        elif status == "LOSS": 
            scanner_stats[scanner]["losses"] += 1
            scanner_stats[scanner]["anchors"][anchor_type]["losses"] += 1
            if is_gap_loss: scanner_stats[scanner]["gap_losses"] += 1
            else: scanner_stats[scanner]["normal_losses"] += 1

    print(f"{'Scanner':<12} | {'Avg RR':<8} | {'Avg Reward%':<12} | {'Avg Stop%':<10} | {'Win Rate':<10}")
    print("-" * 70)
    for sc, stats in scanner_stats.items():
        avg_rr = round(sum(stats['rr'])/len(stats['rr']), 2) if stats['rr'] else 0
        avg_rp = round(sum(stats['rp'])/len(stats['rp']), 2) if stats['rp'] else 0
        avg_stop = round(sum(stats['stop'])/len(stats['stop']), 2) if stats['stop'] else 0
        total_closed = stats['wins'] + stats['losses']
        win_rate = f"{round((stats['wins']/total_closed)*100,1)}%" if total_closed > 0 else "N/A"
        
        print(f"{sc:<12} | {avg_rr:<8} | {avg_rp:<11}% | {avg_stop:<9}% | {win_rate:<10}")

    print("\n[3] 🛡️ EXECUTION SUMMARY")
    for sc, stats in scanner_stats.items():
        if stats['operator_saves'] > 0:
            print(f"- {sc}: {stats['operator_saves']} trades saved by Operator Trap volume logic.")
            
    # MANUAL MODE (HEAVY REPLAY)
    if mode == "manual":
        print("\n[5] 🔮 FALSE REJECTION & MFE/MAE ANALYSIS (MANUAL MODE)")
        print("Note: This pulls historical data for rejected trades and may take several minutes.")
        
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT scanner, symbol, rejection_reason, alert_date, context FROM rejected_alerts WHERE engine_version = %s {date_filter} ORDER BY id DESC LIMIT 50", [ACTIVE_VERSION] + params)
                sample_rejects = cur.fetchall()
                
        false_rejects_by_gate = {gate: {"total": 0, "would_have_won": 0} for gate in gates}
        
        for r in sample_rejects:
            scanner = r[0]
            symbol = r[1]
            reason = r[2] or ""
            alert_date = r[3]
            ctx_str = r[4]
            ctx = json.loads(ctx_str) if isinstance(ctx_str, str) else (ctx_str or {})
            
            sl_res = ctx.get("sl_result", {})
            entry = float(ctx.get("session", {}).get("open", 0.0))
            if entry == 0:
                entry = float(sl_res.get("stop_loss", 0.0)) * 1.05 # Fallback estimate
            
            t1 = float(sl_res.get("target_1", 0.0))
            if entry <= 0 or t1 <= 0: continue
            
            lookahead = LOOKAHEAD_WINDOWS.get(scanner, 5)
            df = fetch_forward_data(symbol, alert_date, lookahead)
            mfe_pct, mae_pct, days_mfe, days_mae = calculate_mfe_mae(df, entry)
            
            if mfe_pct is None: continue
            
            target_pct = (t1 - entry) / entry * 100
            would_have_won = mfe_pct >= target_pct
            
            for gate in gates:
                if gate in reason:
                    false_rejects_by_gate[gate]["total"] += 1
                    if would_have_won:
                        false_rejects_by_gate[gate]["would_have_won"] += 1
                    break
                    
        print(f"{'Gate':<25} | {'Sampled':<8} | {'Would Have Won':<15} | {'False Reject %'}")
        print("-" * 75)
        for gate, stats in false_rejects_by_gate.items():
            tot = stats["total"]
            won = stats["would_have_won"]
            fr_pct = round((won / tot) * 100, 1) if tot > 0 else 0
            print(f"{gate:<25} | {tot:<8} | {won:<15} | {fr_pct}%")
            
        print("\n[6] 💡 THRESHOLD RECOMMENDATIONS")
        for gate, stats in false_rejects_by_gate.items():
            tot = stats["total"]
            won = stats["would_have_won"]
            fr_pct = round((won / tot) * 100, 1) if tot > 0 else 0
            if fr_pct > 15:
                print(f"⚠️ Recommendation: {gate} has a high false reject rate ({fr_pct}%). Consider relaxing the threshold.")
            elif fr_pct < 2 and tot > 5:
                print(f"✅ Recommendation: {gate} is highly efficient. Consider tightening the threshold further.")
            else:
                print(f"➖ Recommendation: {gate} is properly balanced.")
                
    print("\n" + "=" * 80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V6 Architecture Validation Report")
    parser.add_argument("--from", dest="start_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="end_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--manual", action="store_true", help="Run full historical replay and False Rejection analysis")
    args = parser.add_argument_group()
    
    parsed = parser.parse_args()
    mode = "manual" if parsed.manual else "scheduled"
    run_report(parsed.start_date, parsed.end_date, mode)
