#!/usr/bin/env python3
"""
scripts/explain_financial_value.py
==================================
Formal Financial Value & Lineage Diagnostic CLI.

Satisfies Prompt Section 54 & 55:
  Usage:
    python3 scripts/explain_financial_value.py --symbol TCS --field current_ev_ebitda
"""

from __future__ import annotations
import os, sys, json, hashlib, argparse
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
UNIVERSE_JSON = DATA_DIR / "certified_clean_universe_886.json"
CANONICAL_PARQUET = DATA_DIR / "canonical_pit_rebuilt.parquet"
RAW_CKPT_DIR = DATA_DIR / "pit_raw_filings"
EXCHANGE_DIR = DATA_DIR / "exchange_financials"

def explain_symbol_field(symbol: str, field: str = "current_ev_ebitda"):
    sym_u = symbol.strip().upper()
    print("=" * 70)
    print(f"FINANCIAL DATA LINEAGE & VALUE DIAGNOSTIC: {sym_u} -> {field}")
    print("=" * 70)

    # 1. Universe membership
    in_universe = False
    if UNIVERSE_JSON.exists():
        with open(UNIVERSE_JSON) as f:
            u_data = json.load(f)
        u_syms = u_data.get("symbols", u_data)
        if isinstance(u_syms[0], dict):
            u_syms = [d.get("symbol") for d in u_syms]
        in_universe = sym_u in [s.strip().upper() for s in u_syms]
    print(f"1.  Universe Membership:          {'PASS (Approved 886)' if in_universe else 'FAIL (Not in universe)'}")

    # 2. Raw filing existence & SHA256
    raw_ckpt_file = RAW_CKPT_DIR / f"{sym_u}.json"
    raw_exists = raw_ckpt_file.exists()
    payload_hash = "N/A"
    raw_filings = []
    if raw_exists:
        try:
            with open(raw_ckpt_file, "rb") as rf:
                r_bytes = rf.read()
            raw_filings = json.loads(r_bytes)
            payload_hash = hashlib.sha256(r_bytes).hexdigest()
        except Exception as e:
            print(f"    Raw payload read error: {e}")

    print(f"2.  Raw Payload Exists:           {'PASS (' + str(len(raw_filings)) + ' filings)' if raw_exists else 'FAIL (Missing)'}")
    print(f"3.  Payload SHA256:               {payload_hash[:16]}... ({payload_hash})")

    # 3. Facts in latest annual filing
    ann_filings = [f for f in raw_filings if f.get("statement_type") == "ANNUAL"]
    latest_ann = ann_filings[-1] if ann_filings else {}

    p_end = latest_ann.get("period_end_date", "N/A")
    rev = latest_ann.get("revenue")
    op = latest_ann.get("operating_profit")
    da = latest_ann.get("depreciation_amortization")
    np_val = latest_ann.get("net_profit")
    eps = latest_ann.get("eps")
    debt = latest_ann.get("total_debt")
    cash = latest_ann.get("cash_and_equivalents")
    shares = latest_ann.get("shares_outstanding")

    ebitda = (op + da) if (op is not None and da is not None) else op

    print(f"4.  Latest Annual Period:         {p_end}")
    print(f"5.  Revenue:                      ₹{rev} Cr" if rev is not None else "5.  Revenue:                      MISSING")
    print(f"6.  Operating Profit:             ₹{op} Cr" if op is not None else "6.  Operating Profit:             MISSING")
    print(f"7.  Depreciation & Amort:         ₹{da} Cr" if da is not None else "7.  Depreciation & Amort:         MISSING")
    print(f"8.  EBITDA (OP + D&A):            ₹{ebitda} Cr" if ebitda is not None else "8.  EBITDA:                       MISSING")
    print(f"9.  Net Profit (PAT):             ₹{np_val} Cr" if np_val is not None else "9.  Net Profit (PAT):             MISSING")
    print(f"10. EPS:                          ₹{eps}" if eps is not None else "10. EPS:                          MISSING")
    print(f"11. Total Debt (Borrowings):      ₹{debt} Cr" if debt is not None else "11. Total Debt:                   MISSING")
    print(f"12. Cash & Equivalents:           ₹{cash} Cr" if cash is not None else "12. Cash & Equivalents:           MISSING")
    print(f"13. Shares Outstanding:           {shares:,.0f} shares ({shares/1e6:.2f}M)" if shares is not None else "13. Shares Outstanding:           MISSING")

    # 4. Live CMP & Market Cap
    price_file = DATA_DIR / "history" / "1d" / f"{sym_u}.parquet"
    cmp_px = None
    if price_file.exists():
        try:
            df_px = pd.read_parquet(price_file)
            c_col = "close" if "close" in df_px.columns else ("Close" if "Close" in df_px.columns else None)
            if c_col:
                cmp_px = float(df_px[c_col].iloc[-1])
        except Exception:
            pass

    mcap = round((shares * cmp_px) / 1e7, 2) if (shares and cmp_px) else None
    ev = round(mcap + (debt or 0) - cash, 2) if (mcap and cash is not None and debt is not None) else None
    ev_ebitda = round(ev / ebitda, 2) if (ev and ebitda and ebitda > 0) else None

    print(f"14. Latest CMP (Close):           ₹{cmp_px}" if cmp_px is not None else "14. Latest CMP:                   MISSING")
    print(f"15. Market Cap (CMP × Shares):    ₹{mcap} Cr" if mcap is not None else "15. Market Cap:                   MISSING")
    print(f"16. Enterprise Value (MCap+D-C):  ₹{ev} Cr" if ev is not None else "16. Enterprise Value:             MISSING")
    print(f"17. Deterministic EV/EBITDA:      {ev_ebitda}x" if ev_ebitda is not None else "17. Deterministic EV/EBITDA:      DATA_INSUFFICIENT_VALUATION")

    # 5. Canonical Parquet State
    canon_val = None
    if CANONICAL_PARQUET.exists():
        try:
            df_c = pd.read_parquet(CANONICAL_PARQUET)
            r = df_c[df_c["symbol"] == sym_u]
            if not r.empty:
                canon_val = r.iloc[0].to_dict()
        except Exception:
            pass

    print("\n--- CANONICAL SNAPSHOT RECONCILIATION ---")
    if canon_val:
        stored_ev = canon_val.get("current_ev_ebitda")
        stored_ev_med = canon_val.get("ev_ebitda_3y_median")
        stored_pe = canon_val.get("current_pe")
        stored_pe_med = canon_val.get("pe_3y_median")
        stored_prov = canon_val.get("provenance_status")
        print(f"Snapshot Field '{field}':         {canon_val.get(field)}")
        print(f"Snapshot Current EV/EBITDA:       {stored_ev}")
        print(f"Snapshot 3Y EV/EBITDA Median:     {stored_ev_med}")
        print(f"Snapshot Current PE:              {stored_pe}")
        print(f"Snapshot 3Y PE Median:            {stored_pe_med}")
        print(f"Snapshot Provenance Status:       {stored_prov}")
    else:
        print(f"⚠️ Symbol {sym_u} not found in canonical_pit_rebuilt.parquet")

    print("=" * 70)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Financial Data Value & Lineage Diagnostic")
    parser.add_argument("--symbol", type=str, required=True, help="Equity ticker (e.g. TCS)")
    parser.add_argument("--field", type=str, default="current_ev_ebitda", help="Field name to diagnose")
    args = parser.parse_args()
    explain_symbol_field(args.symbol, args.field)
