#!/usr/bin/env python3
"""
scripts/audit_724_raw_data_availability.py
===========================================
Forensic Raw Data Availability Audit for the 724 Un-certified Equities.
Classifies each equity by its exact bottleneck:
  - RAW_FACTS_COMPLETE_DERIVABLE: All raw facts present on disk (CMP, Shares, Debt, Cash, OP, D&A), ready to derive.
  - CASH_SUB_SCHEDULE_NEEDED: Balance sheet & P&L present, but Cash is None because it is in the Other Assets schedule.
  - SHARES_RECONCILIATION_NEEDED: Financial statements present, but shares count needs reconciliation from Equity Capital.
  - CASH_AND_SHARES_NEEDED: Both Cash schedule and Shares reconciliation needed.
  - STRUCTURAL_INELIGIBLE: Confirmed < 5 years exchange existence (IPO / young company).
  - INCOMPLETE_RAW_FACTS: Company disclosures genuinely lack core filing records.
"""

from __future__ import annotations

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, List, Any

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("audit_724_raw_data")

BUCKET_A_JSON = BASE_DIR / "data" / "reports" / "bucket_a_724_symbols.json"
EXCHANGE_DIR = BASE_DIR / "data" / "exchange_financials"
PIT_RAW_DIR = BASE_DIR / "data" / "pit_raw_filings"
PRICE_DIR = BASE_DIR / "data" / "history" / "1d"
V2_POP_AUDIT_CSV = BASE_DIR / "data" / "v2_health_population_audit.csv"
OUTPUT_CSV = BASE_DIR / "data" / "reports" / "raw_data_availability_audit_724.csv"
OUTPUT_JSON = BASE_DIR / "data" / "reports" / "raw_data_availability_audit_724.json"


def run_audit():
    if not BUCKET_A_JSON.exists():
        logger.error(f"Missing {BUCKET_A_JSON}")
        return

    with open(BUCKET_A_JSON) as f:
        symbols = json.load(f)["symbols"]

    # Load structural ineligibles from verified population audit
    structural_set = set()
    if V2_POP_AUDIT_CSV.exists():
        try:
            df_pop = pd.read_csv(V2_POP_AUDIT_CSV)
            struct_syms = df_pop[df_pop["structural_ineligible"] == True]["symbol"].tolist()
            structural_set = set(struct_syms)
        except Exception as e:
            logger.warning(f"Could not load population audit: {e}")

    logger.info(f"Auditing raw financial data availability for {len(symbols)} equities...")

    audit_rows = []

    for sym in symbols:
        ep = EXCHANGE_DIR / sym / "raw" / f"{sym}_filings_v1.payload.json"
        rp = PIT_RAW_DIR / f"{sym}.json"
        cp = PRICE_DIR / f"{sym}.parquet"

        is_structural = sym in structural_set
        has_ep = ep.exists() and ep.stat().st_size > 0
        has_rp = rp.exists() and rp.stat().st_size > 0
        has_cmp = cp.exists() and cp.stat().st_size > 0

        # Load filings: prioritize exchange payload, fallback to raw filings
        filings = []
        source_used = "NONE"
        if has_ep:
            try:
                with open(ep) as f:
                    filings = json.load(f)
                source_used = "EXCHANGE_PAYLOAD"
            except Exception:
                pass
        if not filings and has_rp:
            try:
                with open(rp) as f:
                    filings = json.load(f)
                source_used = "PIT_RAW_FILINGS"
            except Exception:
                pass

        annual_filings = [f for f in filings if str(f.get("statement_type", "")).upper() == "ANNUAL"]
        latest_ann = annual_filings[-1] if annual_filings else {}

        # Extract raw financial facts
        latest_period = latest_ann.get("period_end_date")
        basis = latest_ann.get("basis")
        sales = latest_ann.get("revenue") or latest_ann.get("sales")
        pat = latest_ann.get("net_profit")
        op = latest_ann.get("operating_profit")
        da = latest_ann.get("depreciation_amortization")
        debt = latest_ann.get("total_debt")
        cash = latest_ann.get("cash_and_equivalents")
        shares = latest_ann.get("shares_outstanding")

        # Determine EBITDA derivability: OP + D&A (or OP if DA is None/0)
        can_derive_ebitda = bool(op is not None and (da is not None or op > 0))

        # Determine EV derivability: Market Cap (Shares * CMP) + Debt - Cash
        can_derive_ev = bool(has_cmp and shares is not None and debt is not None and cash is not None)

        can_derive_ev_ebitda = bool(can_derive_ev and can_derive_ebitda)

        # Classify root cause / bottleneck
        if is_structural:
            category = "STRUCTURAL_INELIGIBLE"
            bottleneck = "LIMITED_HISTORICAL_EXISTENCE"
            action = "EXEMPT_YOUNG_COMPANY"
        elif can_derive_ev_ebitda:
            category = "RAW_FACTS_COMPLETE_DERIVABLE"
            bottleneck = "NONE_COMPLETE"
            action = "DERIVE_EV_EBITDA"
        elif not annual_filings:
            category = "ANNUAL_FILINGS_ABSENT"
            bottleneck = "ACQUISITION_PENDING"
            action = "FETCH_ANNUAL_STATEMENTS"
        elif cash is None and shares is None:
            category = "CASH_AND_SHARES_NEEDED"
            bottleneck = "SUB_SCHEDULE_AND_EQUITY"
            action = "EXTRACT_CASH_SCHEDULE_AND_SHARES"
        elif cash is None and shares is not None:
            category = "CASH_SUB_SCHEDULE_NEEDED"
            bottleneck = "SUB_SCHEDULE_PARSER"
            action = "EXTRACT_OTHER_ASSETS_SCHEDULE"
        elif shares is None and cash is not None:
            category = "SHARES_RECONCILIATION_NEEDED"
            bottleneck = "SHARES_DERIVATION"
            action = "DERIVE_SHARES_FROM_EQUITY_CAPITAL"
        elif debt is None or op is None:
            category = "INCOMPLETE_CORE_STATEMENTS"
            bottleneck = "STATEMENT_LINE_ITEM_MISSING"
            action = "INSPECT_RAW_DISCLOSURES"
        else:
            category = "INCOMPLETE_RAW_FACTS"
            bottleneck = "DISCLOSURE_GAP"
            action = "AUDIT_COMPANY_FILINGS"

        audit_rows.append({
            "symbol": sym,
            "is_structural": is_structural,
            "source_used": source_used,
            "annual_filings_count": len(annual_filings),
            "latest_period": latest_period,
            "basis": basis,
            "has_cmp": has_cmp,
            "has_sales": sales is not None,
            "has_pat": pat is not None,
            "has_op": op is not None,
            "has_da": da is not None,
            "has_debt": debt is not None,
            "has_cash": cash is not None,
            "has_shares": shares is not None,
            "can_derive_ebitda": can_derive_ebitda,
            "can_derive_ev": can_derive_ev,
            "can_derive_ev_ebitda": can_derive_ev_ebitda,
            "category": category,
            "bottleneck": bottleneck,
            "action": action,
        })

    df = pd.DataFrame(audit_rows)
    df.to_csv(OUTPUT_CSV, index=False)

    summary = {
        "total_audited": len(df),
        "category_breakdown": df["category"].value_counts().to_dict(),
        "bottleneck_breakdown": df["bottleneck"].value_counts().to_dict(),
        "source_breakdown": df["source_used"].value_counts().to_dict(),
        "fact_availability": {
            "cmp_available": int(df["has_cmp"].sum()),
            "sales_available": int(df["has_sales"].sum()),
            "pat_available": int(df["has_pat"].sum()),
            "op_available": int(df["has_op"].sum()),
            "da_available": int(df["has_da"].sum()),
            "debt_available": int(df["has_debt"].sum()),
            "cash_available": int(df["has_cash"].sum()),
            "shares_available": int(df["has_shares"].sum()),
            "can_derive_ebitda": int(df["can_derive_ebitda"].sum()),
            "can_derive_ev": int(df["can_derive_ev"].sum()),
            "can_derive_ev_ebitda": int(df["can_derive_ev_ebitda"].sum()),
        },
    }

    with open(OUTPUT_JSON, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("=" * 70)
    logger.info("FORENSIC RAW DATA AVAILABILITY AUDIT RESULTS (724 EQUITIES)")
    logger.info("=" * 70)
    for cat, cnt in summary["category_breakdown"].items():
        logger.info(f"  {cat:<40}: {cnt:>4} ({cnt/len(df)*100:>5.1f}%)")
    logger.info("-" * 70)
    logger.info("FACT AVAILABILITY:")
    for fact, cnt in summary["fact_availability"].items():
        logger.info(f"  {fact:<30}: {cnt:>4} / {len(df)} ({cnt/len(df)*100:>5.1f}%)")
    logger.info("=" * 70)
    logger.info(f"Saved CSV:  {OUTPUT_CSV}")
    logger.info(f"Saved JSON: {OUTPUT_JSON}")


if __name__ == "__main__":
    run_audit()
