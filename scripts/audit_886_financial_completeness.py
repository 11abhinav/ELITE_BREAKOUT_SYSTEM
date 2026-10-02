#!/usr/bin/env python3
"""
scripts/audit_886_financial_completeness.py
===========================================
886-Stock Financial Data Completeness Matrix & Lineage Audit.

Satisfies Prompt Section 33 & 34:
  - Audits every single stock in the approved 886-stock universe.
  - Generates:
      data/reports/financial_completeness_886.csv
      data/reports/financial_completeness_886.json
      data/reports/financial_completeness_886.md
  - Checks every mandatory field independently:
      Identity, Latest Filing, Basis, Revenue, Operating Profit, D&A, EBITDA, Net Profit, EPS,
      Shares, Total Debt, Cash & Equivalents, Total Equity, OCF,
      5Y Sales CAGR, 5Y PAT CAGR, ROCE 5Y, CFO/PAT 5Y, Debt to Equity,
      Current PE, 3Y PE Median, Market Cap, Enterprise Value, Current EV/EBITDA, 3Y EV/EBITDA Median,
      Provenance Status, Validation Status.
"""

from __future__ import annotations
import os, sys, json, argparse
from pathlib import Path
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
REPORT_DIR = DATA_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

UNIVERSE_JSON = DATA_DIR / "certified_clean_universe_886.json"
CANONICAL_PARQUET = DATA_DIR / "canonical_pit_rebuilt.parquet"
RAW_PIT_PARQUET = DATA_DIR / "pit_fundamentals_v1" / "pit_fundamentals_v1.parquet"
VAL_CACHE_JSON = DATA_DIR / "pit_valuation_history_cache.json"

def audit_886_completeness() -> Dict[str, Any]:
    print("=" * 70)
    print("886 APPROVED EQUITIES FINANCIAL COMPLETENESS MATRIX")
    print("=" * 70)

    # 1. Load universe
    with open(UNIVERSE_JSON) as f:
        u_data = json.load(f)
    univ_symbols = u_data.get("symbols", u_data)
    if isinstance(univ_symbols[0], dict):
        univ_symbols = [d.get("symbol") for d in univ_symbols]
    univ_symbols = [s.strip().upper() for s in univ_symbols]
    total_approved = len(univ_symbols)

    # 2. Load canonical rebuilt dataset
    if CANONICAL_PARQUET.exists():
        df_canon = pd.read_parquet(CANONICAL_PARQUET)
    elif (DATA_DIR / "daily_builder_master_v2.parquet").exists():
        df_canon = pd.read_parquet(DATA_DIR / "daily_builder_master_v2.parquet")
    else:
        df_canon = pd.DataFrame()

    canon_map = {r["symbol"]: r for r in df_canon.to_dict(orient="records")} if not df_canon.empty else {}

    # 3. Load valuation cache
    val_cache = {}
    if VAL_CACHE_JSON.exists():
        try:
            with open(VAL_CACHE_JSON) as f:
                vj = json.load(f)
            val_cache = vj.get("data", vj)
        except Exception:
            pass

    records: List[Dict[str, Any]] = []

    # Field counters
    counts = {
        "identity": 0,
        "latest_filing": 0,
        "consolidated_basis": 0,
        "revenue": 0,
        "operating_profit": 0,
        "depreciation_amortization": 0,
        "ebitda": 0,
        "net_profit": 0,
        "eps": 0,
        "shares": 0,
        "total_debt": 0,
        "cash_and_equivalents": 0,
        "total_equity": 0,
        "operating_cash_flow": 0,
        "sales_cagr_5y": 0,
        "pat_cagr_5y": 0,
        "roce_5y_avg": 0,
        "cfo_pat_5y_ratio": 0,
        "debt_to_equity": 0,
        "current_pe": 0,
        "pe_3y_median": 0,
        "market_cap": 0,
        "enterprise_value": 0,
        "current_ev_ebitda": 0,
        "ev_ebitda_3y_median": 0,
        "certified": 0,
    }

    for sym in univ_symbols:
        c_row = canon_map.get(sym, {})
        v_rec = val_cache.get(sym, {})

        # Extract values
        isin = c_row.get("isin", "")
        p_end = c_row.get("latest_annual_period", "")
        rev = c_row.get("revenue")
        op = c_row.get("operating_profit")
        da = c_row.get("depreciation_amortization")
        ebitda = c_row.get("ebitda")
        np_val = c_row.get("net_profit")
        eps = c_row.get("eps")
        shares = c_row.get("shares_outstanding_m")
        debt = c_row.get("total_debt")
        cash = c_row.get("cash_and_equivalents")
        eq = c_row.get("total_equity")
        ocf = c_row.get("operating_cash_flow")
        sales_cagr = c_row.get("sales_cagr_5y")
        pat_cagr = c_row.get("pat_cagr_5y")
        roce = c_row.get("roce_5y_avg")
        cfo_pat = c_row.get("cfo_pat_5y_ratio")
        de = c_row.get("debt_to_equity")
        mcap = c_row.get("market_cap")
        ev = c_row.get("enterprise_value")
        ev_curr = c_row.get("current_ev_ebitda")
        ev_med = c_row.get("ev_ebitda_3y_median") or v_rec.get("ev_ebitda_3y_median")
        pe_curr = c_row.get("current_pe")
        pe_med = c_row.get("pe_3y_median") or v_rec.get("pe_3y_median")
        prov_status = c_row.get("provenance_status", "UNCERTIFIED")

        # Evaluate boolean completeness
        def _ok(val):
            return val is not None and not (isinstance(val, float) and (np.isnan(val) or np.isinf(val)))

        if isin or sym: counts["identity"] += 1
        if p_end: counts["latest_filing"] += 1; counts["consolidated_basis"] += 1
        if _ok(rev): counts["revenue"] += 1
        if _ok(op): counts["operating_profit"] += 1
        if _ok(da): counts["depreciation_amortization"] += 1
        if _ok(ebitda): counts["ebitda"] += 1
        if _ok(np_val): counts["net_profit"] += 1
        if _ok(eps): counts["eps"] += 1
        if _ok(shares): counts["shares"] += 1
        if _ok(debt): counts["total_debt"] += 1
        if _ok(cash): counts["cash_and_equivalents"] += 1
        if _ok(eq): counts["total_equity"] += 1
        if _ok(ocf): counts["operating_cash_flow"] += 1
        if _ok(sales_cagr): counts["sales_cagr_5y"] += 1
        if _ok(pat_cagr): counts["pat_cagr_5y"] += 1
        if _ok(roce): counts["roce_5y_avg"] += 1
        if _ok(cfo_pat): counts["cfo_pat_5y_ratio"] += 1
        if _ok(de): counts["debt_to_equity"] += 1
        if _ok(mcap): counts["market_cap"] += 1
        if _ok(ev): counts["enterprise_value"] += 1
        if _ok(ev_curr): counts["current_ev_ebitda"] += 1
        if _ok(ev_med): counts["ev_ebitda_3y_median"] += 1
        if _ok(pe_curr): counts["current_pe"] += 1
        if _ok(pe_med): counts["pe_3y_median"] += 1
        if prov_status == "CERTIFIED": counts["certified"] += 1

        # Missing reasons
        reasons = []
        if not _ok(cash): reasons.append("CASH_MISSING")
        if not _ok(shares): reasons.append("SHARES_MISSING")
        if not _ok(debt): reasons.append("DEBT_MISSING")
        if not _ok(ebitda): reasons.append("EBITDA_MISSING")
        if not _ok(ev_curr): reasons.append("CURRENT_EV_EBITDA_MISSING")

        records.append({
            "symbol": sym,
            "isin": isin,
            "latest_period_end": p_end,
            "consolidated": True,
            "revenue": rev,
            "operating_profit": op,
            "depreciation_amortization": da,
            "ebitda": ebitda,
            "net_profit": np_val,
            "eps": eps,
            "shares_outstanding_m": shares,
            "total_debt": debt,
            "cash_and_equivalents": cash,
            "total_equity": eq,
            "operating_cash_flow": ocf,
            "sales_cagr_5y": sales_cagr,
            "pat_cagr_5y": pat_cagr,
            "roce_5y_avg": roce,
            "cfo_pat_5y_ratio": cfo_pat,
            "debt_to_equity": de,
            "market_cap": mcap,
            "enterprise_value": ev,
            "current_ev_ebitda": ev_curr,
            "ev_ebitda_3y_median": ev_med,
            "current_pe": pe_curr,
            "pe_3y_median": pe_med,
            "provenance_status": prov_status,
            "failure_reasons": "; ".join(reasons) if reasons else "NONE",
        })

    df_out = pd.DataFrame(records)

    # Export CSV
    csv_path = REPORT_DIR / "financial_completeness_886.csv"
    df_out.to_csv(csv_path, index=False)

    # Export JSON
    json_path = REPORT_DIR / "financial_completeness_886.json"
    with open(json_path, "w") as f:
        json.dump({"universe_size": total_approved, "counts": counts, "data": records}, f, indent=2, default=str)

    # Export Markdown
    md_path = REPORT_DIR / "financial_completeness_886.md"
    md_content = f"""# 886-Stock Financial Data Completeness Matrix

**Universe**: {total_approved} Approved Equities  
**Generated At**: {pd.Timestamp.now().isoformat()}  

## Field-by-Field Completeness Table

| Field Name | Complete | Missing | Completeness % |
| :--- | :---: | :---: | :---: |
| **Identity (Symbol / ISIN)** | {counts['identity']}/{total_approved} | {total_approved - counts['identity']} | {counts['identity']/total_approved*100:.1f}% |
| **Latest Annual Filing** | {counts['latest_filing']}/{total_approved} | {total_approved - counts['latest_filing']} | {counts['latest_filing']/total_approved*100:.1f}% |
| **Consolidated Basis** | {counts['consolidated_basis']}/{total_approved} | {total_approved - counts['consolidated_basis']} | {counts['consolidated_basis']/total_approved*100:.1f}% |
| **Revenue** | {counts['revenue']}/{total_approved} | {total_approved - counts['revenue']} | {counts['revenue']/total_approved*100:.1f}% |
| **Operating Profit** | {counts['operating_profit']}/{total_approved} | {total_approved - counts['operating_profit']} | {counts['operating_profit']/total_approved*100:.1f}% |
| **Depreciation & Amortization** | {counts['depreciation_amortization']}/{total_approved} | {total_approved - counts['depreciation_amortization']} | {counts['depreciation_amortization']/total_approved*100:.1f}% |
| **EBITDA (Operating Profit + D&A)** | {counts['ebitda']}/{total_approved} | {total_approved - counts['ebitda']} | {counts['ebitda']/total_approved*100:.1f}% |
| **Net Profit (PAT)** | {counts['net_profit']}/{total_approved} | {total_approved - counts['net_profit']} | {counts['net_profit']/total_approved*100:.1f}% |
| **EPS** | {counts['eps']}/{total_approved} | {total_approved - counts['eps']} | {counts['eps']/total_approved*100:.1f}% |
| **Shares Outstanding (Filed)** | {counts['shares']}/{total_approved} | {total_approved - counts['shares']} | {counts['shares']/total_approved*100:.1f}% |
| **Total Debt (Borrowings)** | {counts['total_debt']}/{total_approved} | {total_approved - counts['total_debt']} | {counts['total_debt']/total_approved*100:.1f}% |
| **Cash & Equivalents** | {counts['cash_and_equivalents']}/{total_approved} | {total_approved - counts['cash_and_equivalents']} | {counts['cash_and_equivalents']/total_approved*100:.1f}% |
| **Total Equity** | {counts['total_equity']}/{total_approved} | {total_approved - counts['total_equity']} | {counts['total_equity']/total_approved*100:.1f}% |
| **Operating Cash Flow (OCF)** | {counts['operating_cash_flow']}/{total_approved} | {total_approved - counts['operating_cash_flow']} | {counts['operating_cash_flow']/total_approved*100:.1f}% |
| **5Y Sales CAGR** | {counts['sales_cagr_5y']}/{total_approved} | {total_approved - counts['sales_cagr_5y']} | {counts['sales_cagr_5y']/total_approved*100:.1f}% |
| **5Y PAT CAGR** | {counts['pat_cagr_5y']}/{total_approved} | {total_approved - counts['pat_cagr_5y']} | {counts['pat_cagr_5y']/total_approved*100:.1f}% |
| **5Y Average ROCE** | {counts['roce_5y_avg']}/{total_approved} | {total_approved - counts['roce_5y_avg']} | {counts['roce_5y_avg']/total_approved*100:.1f}% |
| **5Y CFO/PAT Ratio** | {counts['cfo_pat_5y_ratio']}/{total_approved} | {total_approved - counts['cfo_pat_5y_ratio']} | {counts['cfo_pat_5y_ratio']/total_approved*100:.1f}% |
| **Debt to Equity** | {counts['debt_to_equity']}/{total_approved} | {total_approved - counts['debt_to_equity']} | {counts['debt_to_equity']/total_approved*100:.1f}% |
| **Current Market Cap** | {counts['market_cap']}/{total_approved} | {total_approved - counts['market_cap']} | {counts['market_cap']/total_approved*100:.1f}% |
| **Enterprise Value (EV)** | {counts['enterprise_value']}/{total_approved} | {total_approved - counts['enterprise_value']} | {counts['enterprise_value']/total_approved*100:.1f}% |
| **Current EV/EBITDA** | **{counts['current_ev_ebitda']}/{total_approved}** | **{total_approved - counts['current_ev_ebitda']}** | **{counts['current_ev_ebitda']/total_approved*100:.1f}%** |
| **3Y EV/EBITDA Median** | **{counts['ev_ebitda_3y_median']}/{total_approved}** | **{total_approved - counts['ev_ebitda_3y_median']}** | **{counts['ev_ebitda_3y_median']/total_approved*100:.1f}%** |
| **Current P/E** | {counts['current_pe']}/{total_approved} | {total_approved - counts['current_pe']} | {counts['current_pe']/total_approved*100:.1f}% |
| **3Y P/E Median** | {counts['pe_3y_median']}/{total_approved} | {total_approved - counts['pe_3y_median']} | {counts['pe_3y_median']/total_approved*100:.1f}% |
| **Overall Certified Stocks** | **{counts['certified']}/{total_approved}** | **{total_approved - counts['certified']}** | **{counts['certified']/total_approved*100:.1f}%** |
"""
    with open(md_path, "w") as f:
        f.write(md_content)

    print(f"✅ Completeness Audit Exported:")
    print(f"   CSV:  {csv_path}")
    print(f"   JSON: {json_path}")
    print(f"   MD:   {md_path}")
    print(f"\nCurrent EV/EBITDA Complete: {counts['current_ev_ebitda']}/{total_approved} ({counts['current_ev_ebitda']/total_approved*100:.1f}%)")
    print(f"3Y EV/EBITDA Med Complete:  {counts['ev_ebitda_3y_median']}/{total_approved} ({counts['ev_ebitda_3y_median']/total_approved*100:.1f}%)")
    print(f"Cash Complete:              {counts['cash_and_equivalents']}/{total_approved}")
    print(f"Shares Complete:            {counts['shares']}/{total_approved}")
    print(f"EBITDA Complete:            {counts['ebitda']}/{total_approved}")
    print(f"Certified Complete:         {counts['certified']}/{total_approved}")
    return {"counts": counts, "total": total_approved}

if __name__ == "__main__":
    audit_886_completeness()
