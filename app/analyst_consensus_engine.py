# =====================================================================================
# app/analyst_consensus_engine.py
# INSTITUTIONAL ANALYST CONSENSUS, REVISION MOMENTUM & DISPERSION ENGINE
# =====================================================================================

import logging
import statistics
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST_ZONE = ZoneInfo("Asia/Kolkata")


def calculate_analyst_consensus(
    reports: List[Dict[str, Any]],
    current_price: Optional[float] = None
) -> Dict[str, Any]:
    """
    Synthesizes multiple institutional broker reports into consensus metrics,
    calculates target price dispersion, and derives ANALYST_REVISION_SCORE.
    """
    if not reports:
        return {
            "total_covering_analysts": 0,
            "buy_count": 0,
            "hold_count": 0,
            "sell_count": 0,
            "median_target_price": None,
            "upside_pct": None,
            "target_dispersion_pct": 0.0,
            "target_revision_30d_pct": 0.0,
            "target_revision_90d_pct": 0.0,
            "analyst_revision_score": 50,
            "consensus_verdict": "NO_COVERAGE",
            "recent_reports_summary": []
        }

    now_ist = datetime.now(IST_ZONE)
    cutoff_30d = now_ist - timedelta(days=30)
    cutoff_90d = now_ist - timedelta(days=90)

    # Group by broker to keep only latest report per firm
    latest_by_firm: Dict[str, Dict[str, Any]] = {}
    for r in reports:
        firm = r.get("broker_firm", "UNKNOWN").strip().upper()
        r_date_str = str(r.get("report_date", ""))
        try:
            r_dt = datetime.fromisoformat(r_date_str) if "T" in r_date_str else datetime.strptime(r_date_str[:10], "%Y-%m-%d")
            if r_dt.tzinfo is None:
                r_dt = r_dt.replace(tzinfo=IST_ZONE)
        except Exception:
            r_dt = now_ist

        r["_dt"] = r_dt
        if firm not in latest_by_firm or r_dt > latest_by_firm[firm]["_dt"]:
            latest_by_firm[firm] = r

    unique_reports = list(latest_by_firm.values())
    total_covering = len(unique_reports)

    buy_count = 0
    hold_count = 0
    sell_count = 0
    targets = []

    for r in unique_reports:
        rating = str(r.get("rating", "")).upper()
        if any(b in rating for b in ("BUY", "ACCUMULATE", "OUTPERFORM", "OVERWEIGHT")):
            buy_count += 1
        elif any(s in rating for s in ("SELL", "REDUCE", "UNDERPERFORM", "UNDERWEIGHT")):
            sell_count += 1
        else:
            hold_count += 1

        tp = r.get("target_price")
        if tp and isinstance(tp, (int, float)) and tp > 0:
            targets.append(float(tp))

    median_tp = statistics.median(targets) if targets else None
    
    # Calculate Upside
    upside_pct = None
    if median_tp and current_price and current_price > 0:
        upside_pct = round(((median_tp - current_price) / current_price) * 100.0, 2)

    # Calculate Dispersion: (Max - Min) / Median
    dispersion_pct = 0.0
    if len(targets) >= 2 and median_tp and median_tp > 0:
        dispersion_pct = round(((max(targets) - min(targets)) / median_tp) * 100.0, 2)

    # Target revisions over 30d & 90d
    revisions_30d = []
    revisions_90d = []
    for r in unique_reports:
        dt = r["_dt"]
        tc = r.get("target_change_pct")
        if tc is not None:
            if dt >= cutoff_30d:
                revisions_30d.append(float(tc))
            if dt >= cutoff_90d:
                revisions_90d.append(float(tc))

    rev_30d_avg = round(statistics.mean(revisions_30d), 2) if revisions_30d else 0.0
    rev_90d_avg = round(statistics.mean(revisions_90d), 2) if revisions_90d else 0.0

    # ── Derive ANALYST_REVISION_SCORE (0 to 100) ──────────────────────────────────
    score = 50.0  # Neutral baseline
    
    # 1. Buy/Sell Ratio Momentum
    if total_covering > 0:
        buy_ratio = buy_count / total_covering
        sell_ratio = sell_count / total_covering
        score += (buy_ratio * 20.0) - (sell_ratio * 30.0)

    # 2. Target Revision Velocity
    if rev_30d_avg > 0:
        score += min(15.0, rev_30d_avg * 1.5)
    elif rev_30d_avg < 0:
        score -= min(20.0, abs(rev_30d_avg) * 2.0)

    if rev_90d_avg > 0:
        score += min(10.0, rev_90d_avg * 0.8)

    # 3. High Dispersion Penalty (Uncertainty Spread > 25% slashes confidence)
    if dispersion_pct > 30.0:
        score -= min(20.0, (dispersion_pct - 30.0) * 0.5)
    elif dispersion_pct < 12.0 and total_covering >= 3:
        score += 5.0  # Tight consensus bonus

    final_score = int(max(0, min(100, round(score))))

    # Consensus verdict
    if final_score >= 75:
        verdict = "STRONG_INSTITUTIONAL_ACCUMULATION"
    elif final_score >= 60:
        verdict = "BULLISH_REVISION_MOMENTUM"
    elif final_score <= 35:
        verdict = "BEARISH_DOWNGRADE_PRESSURE"
    else:
        verdict = "NEUTRAL_MIXED_CONSENSUS"

    recent_summary = []
    for r in sorted(unique_reports, key=lambda x: x["_dt"], reverse=True)[:5]:
        recent_summary.append({
            "broker_firm": r.get("broker_firm"),
            "analyst_name": r.get("analyst_name"),
            "date": r["_dt"].strftime("%Y-%m-%d"),
            "rating": r.get("rating"),
            "target_price": r.get("target_price"),
            "target_change_pct": r.get("target_change_pct"),
            "thesis": r.get("thesis", "")[:120]
        })

    return {
        "total_covering_analysts": total_covering,
        "buy_count": buy_count,
        "hold_count": hold_count,
        "sell_count": sell_count,
        "median_target_price": median_tp,
        "upside_pct": upside_pct,
        "target_dispersion_pct": dispersion_pct,
        "target_revision_30d_pct": rev_30d_avg,
        "target_revision_90d_pct": rev_90d_avg,
        "analyst_revision_score": final_score,
        "consensus_verdict": verdict,
        "recent_reports_summary": recent_summary
    }
