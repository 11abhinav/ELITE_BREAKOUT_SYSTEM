"""
FUNDAMENTAL_GEM_RECOVERY — PHASE E LIVE FORWARD TRACKER
======================================================
Frozen Phase E Forward Tracker Module for Fundamental Undervaluations Watchlist.

Gating & Scoring Rules (Frozen):
- Layer 1 (Quality): 5Y Avg ROCE >= 15%, 5Y Sales CAGR >= 10%, 5Y PAT CAGR >= 10%,
                    Cumulative 5Y CFO/PAT >= 0.80, Debt/Equity <= 0.50.
- Layer 2 (Forensics): 3Y Share Dilution <= 10.0%.
- Layer 3 (Valuation vs Own History): EV/EBITDA <= 0.75 * Stock's own 3Y PIT Median EV/EBITDA.
- Classification:
  - Tier A: L1+L2+L3 Pass AND Res_DD <= 10% vs Nifty 500 TRI (Quality + Cheap + Systemic Dislocation)
  - Tier B: L1+L2+L3 Pass AND Res_DD > 10% vs Nifty 500 TRI (Quality + Cheap Only)
- Pre-Buy Verification: 7-point manual checklist per candidate before live capital deployment.
- Log: Append-only ledger saved to research/fundamental_gem_recovery_v5/FORWARD_TRACKER_LOG.json.
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Any, Optional

# File Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARQUET_PATH = os.path.join(BASE_DIR, "data", "pit_fundamentals_v1.parquet")
TRACKER_LOG_JSON = os.path.join(BASE_DIR, "research", "fundamental_gem_recovery_v5", "FORWARD_TRACKER_LOG.json")
TRACKER_LOG_CSV = os.path.join(BASE_DIR, "research", "fundamental_gem_recovery_v5", "FORWARD_TRACKER_LOG.csv")

# Financial Sector Keywords to Exclude from Primary EV/EBITDA Funnel
FINANCIAL_KEYWORDS = [
    "BANK", "FINANCE", "FINANCIAL", "HOUSING FINANCE", "NBFC", 
    "INSURANCE", "INVESTMENT", "CAPITAL", "SECURITIES", "LEASING"
]

def is_financial_sector(industry_str: str) -> bool:
    """Return True if industry name matches financial sector keywords."""
    if not isinstance(industry_str, str):
        return False
    ind_upper = industry_str.upper()
    return any(kw in ind_upper for kw in FINANCIAL_KEYWORDS)

def compute_100pt_score(row: pd.Series, ev_ebitda_discount: float, pe_discount: float, res_dd: float) -> float:
    """
    Compute 100-Point Quality & Valuation Score:
    1. EV/EBITDA Discount Depth (Max 30 pts)
    2. 5-Year Average ROCE (Max 25 pts)
    3. P/E_norm Discount Depth (Max 20 pts)
    4. CFO / PAT Cash Conversion (Max 15 pts)
    5. Residual Drawdown Bonus (Max 10 pts)
    """
    # 1. EV/EBITDA Discount Depth (0.25 to 0.50 => 0 to 30 pts)
    ev_pts = 30.0 * min(max((ev_ebitda_discount - 0.25) / 0.25, 0.0), 1.0)
    
    # 2. 5Y ROCE (15% to 40% => 0 to 25 pts)
    roce_val = float(row.get("roce_5y_avg", 15.0))
    roce_pts = 25.0 * min(max((roce_val - 15.0) / 25.0, 0.0), 1.0)
    
    # 3. PE_norm Discount Depth (0% to 40% => 0 to 20 pts)
    pe_pts = 20.0 * min(max(pe_discount / 0.40, 0.0), 1.0)
    
    # 4. CFO / PAT Cash Quality (0.80 to 1.50 => 0 to 15 pts)
    cfo_pat_val = float(row.get("cfo_pat_5y_ratio", 0.80))
    cfo_pts = 15.0 * min(max((cfo_pat_val - 0.80) / 0.70, 0.0), 1.0)
    
    # 5. Residual Drawdown Bonus (Res_DD <= 10% gets full 10 pts, up to 25% gets partial)
    if res_dd <= 0.10:
        res_pts = 10.0
    else:
        res_pts = 10.0 * min(max((0.25 - res_dd) / 0.15, 0.0), 1.0)
        
    total_score = round(ev_pts + roce_pts + pe_pts + cfo_pts + res_pts, 2)
    return total_score

def build_why_cheap_attribution(row: pd.Series, ev_ebitda_discount: float, pe_discount: float, res_dd: float) -> str:
    """Generate human-readable attribution for why the stock is flagged as cheap."""
    reasons = []
    if ev_ebitda_discount >= 0.25:
        reasons.append(f"EV/EBITDA {ev_ebitda_discount*100:.1f}% below own 3Y median")
    if pe_discount >= 0.20:
        reasons.append(f"P/E {pe_discount*100:.1f}% below own 3Y median")
    if res_dd <= 0.10:
        reasons.append(f"Deep stock dislocation (Res_DD={res_dd*100:.1f}%)")
    elif res_dd <= 0.25:
        reasons.append(f"Moderate stock dislocation (Res_DD={res_dd*100:.1f}%)")
    else:
        reasons.append(f"Valuation compression without market crash (Res_DD={res_dd*100:.1f}%)")
        
    return " | ".join(reasons)

def generate_forward_watchlist(
    as_of_date_str: str = None, 
    top_n: int = 15
) -> Dict[str, Any]:
    """
    Run the frozen L1+L2+L3 screen on the point-in-time fundamentals database
    and produce Tier A and Tier B forward watchlists with complete 100-pt scores.
    """
    if not os.path.exists(PARQUET_PATH):
        raise FileNotFoundError(f"PIT parquet dataset not found at {PARQUET_PATH}")

    df = pd.read_parquet(PARQUET_PATH)
    df['filing_date'] = pd.to_datetime(df['filing_date'])

    if as_of_date_str is None:
        eval_dt = df['filing_date'].max()
    else:
        eval_dt = pd.to_datetime(as_of_date_str)

    # Filter to most recent PIT filing per symbol on or before eval_dt
    df_pit = df[df['filing_date'] <= eval_dt].sort_values('filing_date').groupby('symbol').last().reset_index()

    candidates = []

    for _, row in df_pit.iterrows():
        symbol = str(row['symbol'])
        industry = str(row.get('industry', 'Unknown'))
        
        # Exclude Financials from Primary EV/EBITDA Arm
        if is_financial_sector(industry):
            continue

        # -------------------------------------------------------------
        # LAYER 1: Quality Filters (5-Year Lookbacks)
        # -------------------------------------------------------------
        roce_5y = float(row.get('roce_5y_avg', 0.0))
        sales_cagr_5y = float(row.get('sales_cagr_5y', 0.0))
        pat_cagr_5y = float(row.get('pat_cagr_5y', 0.0))
        cfo_pat_5y = float(row.get('cfo_pat_5y_ratio', 0.0))
        de_ratio = float(row.get('debt_to_equity', 99.0))

        if not (roce_5y >= 15.0 and sales_cagr_5y >= 10.0 and pat_cagr_5y >= 10.0 and cfo_pat_5y >= 0.80 and de_ratio <= 0.50):
            continue

        # -------------------------------------------------------------
        # LAYER 2: Dilution Forensics
        # -------------------------------------------------------------
        share_dilution_3y = float(row.get('share_dilution_3y', 0.0))
        if share_dilution_3y > 10.0:  # >10% share dilution fails
            continue

        # -------------------------------------------------------------
        # LAYER 3: EV/EBITDA Valuation vs Own 3Y PIT Median
        # -------------------------------------------------------------
        ev_ebitda_curr = float(row.get('ev_to_ebitda', np.nan))
        ev_ebitda_med = float(row.get('ev_to_ebitda_3y_median', np.nan))
        
        pe_curr = float(row.get('pe_ratio', np.nan))
        pe_med = float(row.get('pe_ratio_3y_median', np.nan))

        if pd.isna(ev_ebitda_curr) or pd.isna(ev_ebitda_med) or ev_ebitda_med <= 0:
            continue

        # Require EV/EBITDA <= 0.75 * 3Y Median (>=25% discount)
        ev_discount = (ev_ebitda_med - ev_ebitda_curr) / ev_ebitda_med
        if ev_discount < 0.25:
            continue

        pe_discount = 0.0
        if not pd.isna(pe_curr) and not pd.isna(pe_med) and pe_med > 0:
            pe_discount = max((pe_med - pe_curr) / pe_med, 0.0)

        # -------------------------------------------------------------
        # LAYER 4: Residual Drawdown Features (Scored Feature — Not Gate)
        # -------------------------------------------------------------
        dd_stock = float(row.get('drawdown_252d', 0.15))
        dd_nifty = float(row.get('nifty_drawdown_252d', 0.10))
        res_dd = max(dd_stock - dd_nifty, 0.0)

        # Tier Classification
        tier = "Tier A" if res_dd <= 0.10 else "Tier B"

        # Calculate Score
        score = compute_100pt_score(row, ev_discount, pe_discount, res_dd)
        why_cheap = build_why_cheap_attribution(row, ev_discount, pe_discount, res_dd)
        staleness_days = (eval_dt - row['filing_date']).days

        candidate_obj = {
            "eval_date": eval_dt.strftime("%Y-%m-%d"),
            "symbol": symbol,
            "industry": industry,
            "tier": tier,
            "score_100": score,
            "staleness_days": int(staleness_days),
            "ev_ebitda_current": round(ev_ebitda_curr, 2),
            "ev_ebitda_3y_median": round(ev_ebitda_med, 2),
            "ev_ebitda_discount_pct": round(ev_discount * 100, 1),
            "pe_norm_current": round(pe_curr, 2) if not pd.isna(pe_curr) else None,
            "pe_norm_3y_median": round(pe_med, 2) if not pd.isna(pe_med) else None,
            "pe_discount_pct": round(pe_discount * 100, 1),
            "roce_5y_avg": round(roce_5y, 2),
            "sales_cagr_5y": round(sales_cagr_5y, 2),
            "pat_cagr_5y": round(pat_cagr_5y, 2),
            "cfo_pat_5y_ratio": round(cfo_pat_5y, 2),
            "debt_to_equity": round(de_ratio, 2),
            "share_dilution_3y_pct": round(share_dilution_3y, 2),
            "dd_stock_pct": round(dd_stock * 100, 1),
            "res_dd_pct": round(res_dd * 100, 1),
            "why_cheap_attribution": why_cheap,
            "manual_checklist": {
                "1_promoter_pledge_lt_5pct": "PENDING_VERIFICATION",
                "2_promoter_holding_stable": "PENDING_VERIFICATION",
                "3_auditor_clean_opinion": "PENDING_VERIFICATION",
                "4_receivables_trend_healthy": "PENDING_VERIFICATION",
                "5_cwip_ageing_lt_3y": "PENDING_VERIFICATION",
                "6_rpt_lt_10pct": "PENDING_VERIFICATION",
                "7_concall_commentary_positive": "PENDING_VERIFICATION"
            }
        }
        candidates.append(candidate_obj)

    # Sort candidates by 100-pt Score descending (tie-break: higher 5Y ROCE)
    candidates = sorted(candidates, key=lambda x: (x['score_100'], x['roce_5y_avg']), reverse=True)
    top_candidates = candidates[:top_n]

    tier_a_list = [c for c in top_candidates if c['tier'] == 'Tier A']
    tier_b_list = [c for c in top_candidates if c['tier'] == 'Tier B']

    result_summary = {
        "as_of_date": eval_dt.strftime("%Y-%m-%d"),
        "total_qualifying_candidates": len(candidates),
        "watchlist_top_candidates": len(top_candidates),
        "tier_a_count": len(tier_a_list),
        "tier_b_count": len(tier_b_list),
        "tier_a_names": [c['symbol'] for c in tier_a_list],
        "tier_b_names": [c['symbol'] for c in tier_b_list],
        "top_candidates": top_candidates
    }

    return result_summary

def append_to_tracker_ledger(watchlist_result: Dict[str, Any]):
    """Append new monthly watchlist results to the append-only JSON & CSV ledger."""
    os.makedirs(os.path.dirname(TRACKER_LOG_JSON), exist_ok=True)
    
    # JSON Append
    ledger = []
    if os.path.exists(TRACKER_LOG_JSON):
        try:
            with open(TRACKER_LOG_JSON, 'r') as f:
                ledger = json.load(f)
        except Exception:
            ledger = []

    ledger.append(watchlist_result)
    with open(TRACKER_LOG_JSON, 'w') as f:
        json.dump(ledger, f, indent=2)

    # CSV Append
    rows = []
    for c in watchlist_result.get("top_candidates", []):
        flat_c = c.copy()
        chk = flat_c.pop("manual_checklist", {})
        for k, v in chk.items():
            flat_c[f"chk_{k}"] = v
        rows.append(flat_c)

    if rows:
        df_new = pd.DataFrame(rows)
        if os.path.exists(TRACKER_LOG_CSV):
            df_new.to_csv(TRACKER_LOG_CSV, mode='a', header=False, index=False)
        else:
            df_new.to_csv(TRACKER_LOG_CSV, index=False)

if __name__ == "__main__":
    res = generate_forward_watchlist()
    append_to_tracker_ledger(res)
    print(f"Watchlist Generated As Of {res['as_of_date']}: {res['watchlist_top_candidates']} Top Candidates")
    print(f"Tier A ({res['tier_a_count']} names): {res['tier_a_names']}")
    print(f"Tier B ({res['tier_b_count']} names): {res['tier_b_names']}")
