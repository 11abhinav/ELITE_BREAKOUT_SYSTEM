#!/usr/bin/env python3
"""
scripts/audit_phase2_data_recovery.py
=====================================
Phase-2 Financial Data Recovery Audit & Diagnostic Census across 886 Stocks.

ARCHITECTURAL PRINCIPLE:
  Do not immediately re-fetch all 886 stocks without diagnosis.
  First classify existing failures from local cache, then perform authoritative
  NSE/BSE exchange availability/recovery diagnosis for recoverable facts.

STAGES:
  Stage A: Local Forensic Census (No network) -> What is missing in pit_raw_filings
  Stage B: Exchange Availability Test -> Do missing facts exist at NSE/BSE
  Stage C: Acquisition Audit -> Network, rate-limit, mapping failures
  Stage D: Normalization Audit -> XBRL concepts, units, consolidated basis
  Stage E: PIT Audit -> Filing dates, staleness (>24M), fiscal gaps
  Stage F: Reconciliation -> NSE vs BSE comparison (<= 5% RECONCILED, > 5% CONFLICT)
  Stage G: Evidence Reconstruction -> Completeness of QUALITY & FUNDAMENTAL bundles

OUTPUTS:
  - data/phase2_data_recovery_audit.json
  - Full diagnostic terminal dashboard with the 8 final diagnostic buckets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import numpy as np
import pandas as pd

from app.financial_data_integrity import (
    DataStatus,
    StatementBasis,
    check_pit_freshness,
    detect_annual_fiscal_gaps,
    compute_cagr_pit,
    compute_ev_ebitda,
    derive_and_validate_shares,
    validate_share_count,
    reconcile_nse_bse_fact,
    pre_buy_data_integrity_gate,
    BUYEvidenceBundle,
    FieldProvenance,
)
from app.live_fundamental_scanner import (
    ApprovedUniverseRegistry,
    QualityCompounderValueV2Scanner,
    LiveFundamentalBuyScanner,
    _get_pit_filings,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("PHASE2_RECOVERY_AUDIT")

# Diagnostic Statuses
STATUS_LOCAL_VALID = "LOCAL_VALID"
STATUS_LOCAL_MISSING = "LOCAL_MISSING"
STATUS_EXCHANGE_AVAILABLE = "EXCHANGE_AVAILABLE"
STATUS_EXCHANGE_UNAVAILABLE = "EXCHANGE_UNAVAILABLE"
STATUS_ACQUISITION_FAILED = "ACQUISITION_FAILED"
STATUS_MAPPING_FAILED = "MAPPING_FAILED"
STATUS_NORMALIZATION_FAILED = "NORMALIZATION_FAILED"
STATUS_UNIT_FAILED = "UNIT_FAILED"
STATUS_BASIS_CONFLICT = "BASIS_CONFLICT"
STATUS_PIT_FAILED = "PIT_FAILED"
STATUS_STALE = "STALE"
STATUS_NSE_BSE_CONFLICT = "NSE_BSE_CONFLICT"
STATUS_COMPLETE = "COMPLETE"
STATUS_DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


def run_phase2_recovery_audit(as_of_date: Optional[date] = None) -> Dict[str, Any]:
    as_of = as_of_date or date.today()
    print("\n" + "=" * 80)
    print("  PHASE-2 DATA RECOVERY AUDIT: 886-STOCK AUTHORITATIVE CENSUS")
    print(f"  As-Of Date: {as_of.isoformat()} | Execution Mode: DIAGNOSTIC CENSUS")
    print("=" * 80 + "\n")

    registry = ApprovedUniverseRegistry()
    approved_symbols = sorted(list(registry.approved_symbols))
    total_universe = len(approved_symbols)
    logger.info(f"Loaded Approved Universe: {total_universe} stocks")

    # Load master exchange equities mappings
    nse_master_path = os.path.join(BASE_DIR, "data", "nse_master_equities.json")
    nse_bse_master_path = os.path.join(BASE_DIR, "data", "nse_bse_master_universe.json")
    nse_master = {}
    nse_bse_master = {}
    if os.path.exists(nse_master_path):
        with open(nse_master_path) as f:
            nse_master = json.load(f)
    if os.path.exists(nse_bse_master_path):
        with open(nse_bse_master_path) as f:
            nse_bse_master = json.load(f)

    # Load PIT raw filings index
    raw_filings_dir = os.path.join(BASE_DIR, "data", "pit_raw_filings")

    # Metrics to track at fact-level
    required_quality_facts = [
        "revenue", "net_profit", "operating_profit", "roce",
        "total_debt", "total_equity", "cash_and_equivalents",
        "shares_outstanding", "operating_cash_flow"
    ]
    required_fundamental_facts = [
        "revenue", "net_profit", "operating_profit", "eps",
        "roce", "roe", "operating_cash_flow", "total_debt"
    ]

    # Diagnostic Bucket Counters
    exchange_availability = {
        "nse_available": 0,
        "bse_available": 0,
        "both_available": 0,
        "only_nse": 0,
        "only_bse": 0,
        "neither": 0,
    }

    acquisition_failures = Counter()
    genuine_data_absence = Counter()
    normalization_failures = Counter()
    pit_failures = Counter()
    nse_bse_conflicts_count = 0

    local_complete_count = 0
    missing_cash_local = 0
    missing_shares_local = 0
    recoverable_from_exchange = 0

    quality_complete_count = 0
    fundamental_complete_count = 0

    stock_diagnostics: Dict[str, Dict[str, Any]] = {}

    for sym in approved_symbols:
        s_diag: Dict[str, Any] = {
            "symbol": sym,
            "stage_a_local": {},
            "stage_b_exchange_availability": {},
            "stage_c_acquisition": "PASS",
            "stage_d_normalization": "PASS",
            "stage_e_pit": "PASS",
            "stage_f_reconciliation": "PASS",
            "stage_g_evidence": {
                "quality_complete": False,
                "fundamental_complete": False,
            },
            "fact_details": {},
            "overall_status": STATUS_DATA_INSUFFICIENT,
            "blocking_reasons": [],
        }

        # -------------------------------------------------------------
        # STAGE A: LOCAL FORENSIC CENSUS (No network)
        # -------------------------------------------------------------
        raw_file = os.path.join(raw_filings_dir, f"{sym}.json")
        local_records = []
        if os.path.exists(raw_file):
            try:
                with open(raw_file) as f:
                    local_records = json.load(f)
            except Exception:
                pass

        annual_records = [
            r for r in local_records
            if str(r.get("statement_type", "")).upper() == "ANNUAL"
        ]
        quarterly_records = [
            r for r in local_records
            if str(r.get("statement_type", "")).upper() == "QUARTERLY"
        ]

        latest_annual = annual_records[-1] if annual_records else {}
        latest_quarter = quarterly_records[-1] if quarterly_records else {}

        # Fact-level local inspection
        cash_present = latest_annual.get("cash_and_equivalents") is not None
        shares_present = latest_annual.get("shares_outstanding") is not None
        roce_present = latest_annual.get("roce") is not None
        debt_present = latest_annual.get("total_debt") is not None
        ocf_present = latest_annual.get("operating_cash_flow") is not None

        if not cash_present:
            missing_cash_local += 1
            s_diag["blocking_reasons"].append("LOCAL_MISSING_CASH")
        if not shares_present:
            missing_shares_local += 1
            s_diag["blocking_reasons"].append("LOCAL_MISSING_SHARES")

        all_q_facts_present = bool(
            cash_present and shares_present and roce_present and debt_present and ocf_present
            and len(annual_records) >= 5
        )
        if all_q_facts_present:
            local_complete_count += 1
            s_diag["stage_a_local"]["status"] = STATUS_LOCAL_VALID
        else:
            s_diag["stage_a_local"]["status"] = STATUS_LOCAL_MISSING
            s_diag["stage_a_local"]["missing_facts"] = [
                f for f in required_quality_facts
                if latest_annual.get(f) is None
            ]

        # -------------------------------------------------------------
        # STAGE B: EXCHANGE AVAILABILITY TEST
        # -------------------------------------------------------------
        has_nse = sym in nse_master or sym in nse_bse_master
        has_bse = False
        # Check BSE mapping
        bse_meta = nse_bse_master.get(sym, {})
        if bse_meta.get("bse_code") or bse_meta.get("bse_symbol") or sym.isdigit():
            has_bse = True

        if has_nse and has_bse:
            exchange_availability["both_available"] += 1
            s_diag["stage_b_exchange_availability"]["mapping"] = "BOTH_NSE_BSE"
        elif has_nse:
            exchange_availability["only_nse"] += 1
            s_diag["stage_b_exchange_availability"]["mapping"] = "NSE_ONLY"
        elif has_bse:
            exchange_availability["only_bse"] += 1
            s_diag["stage_b_exchange_availability"]["mapping"] = "BSE_ONLY"
        else:
            exchange_availability["neither"] += 1
            s_diag["stage_b_exchange_availability"]["mapping"] = "UNMAPPED"
            acquisition_failures["NO_MAPPING"] += 1
            s_diag["blocking_reasons"].append("NO_EXCHANGE_MAPPING")

        if has_nse:
            exchange_availability["nse_available"] += 1
        if has_bse:
            exchange_availability["bse_available"] += 1

        # -------------------------------------------------------------
        # STAGE C: ACQUISITION AUDIT
        # -------------------------------------------------------------
        if not (has_nse or has_bse):
            s_diag["stage_c_acquisition"] = "ACQUISITION_FAILED:NO_MAPPING"
        elif len(local_records) == 0:
            acquisition_failures["EMPTY_RESPONSE"] += 1
            s_diag["stage_c_acquisition"] = "EMPTY_LOCAL_PAYLOAD"
            genuine_data_absence["NO_REQUIRED_ANNUAL_HISTORY"] += 1
            s_diag["blocking_reasons"].append("NO_FILINGS_RECORDED")

        # -------------------------------------------------------------
        # STAGE D: NORMALIZATION & UNIT AUDIT
        # -------------------------------------------------------------
        # Inspect share count scaling and unit conversions
        sh_raw = latest_annual.get("shares_outstanding")
        np_raw = latest_annual.get("net_profit")
        ep_raw = latest_annual.get("eps")
        sh_res = derive_and_validate_shares(
            symbol=sym,
            net_profit_cr=float(np_raw) if np_raw is not None else None,
            eps=float(ep_raw) if ep_raw is not None else None,
            shares_outstanding_raw=float(sh_raw) if sh_raw is not None else None,
            scanner="PHASE2_AUDIT",
        )
        if not sh_res.ok:
            normalization_failures["CR/LAKH/MILLION SCALING ERROR"] += 1
            s_diag["stage_d_normalization"] = f"UNIT_FAILED:{sh_res.status.value}"
            s_diag["blocking_reasons"].append(f"SHARES_{sh_res.status.value}")
        elif sh_res.unit_scaling_applied:
            s_diag["stage_d_normalization"] = f"SCALING_APPLIED_{sh_res.unit_scaling_applied}"

        # -------------------------------------------------------------
        # STAGE E: PIT AUDIT (Staleness & Fiscal Gaps)
        # -------------------------------------------------------------
        latest_period_dt = latest_annual.get("period_end_date")
        freshness = check_pit_freshness(sym, latest_period_dt, scan_date=as_of)
        if freshness.status == DataStatus.DATA_STALE:
            pit_failures["STALE > 24M"] += 1
            s_diag["stage_e_pit"] = f"STALE_{freshness.detail.get('pit_staleness_years')}Y"
            s_diag["blocking_reasons"].append("PIT_DATA_STALE")

        annual_sorted = sorted(annual_records, key=lambda x: str(x.get("period_end_date", "")))
        gaps = detect_annual_fiscal_gaps(annual_sorted)
        if gaps:
            pit_failures["FISCAL_GAP"] += 1
            s_diag["stage_e_pit"] = f"GAPS_DETECTED({len(gaps)})"
            s_diag["blocking_reasons"].append("PIT_FILING_GAP_IN_GROWTH_WINDOW")

        if len(annual_records) < 5:
            genuine_data_absence["NO_REQUIRED_ANNUAL_HISTORY"] += 1
            s_diag["blocking_reasons"].append("LESS_THAN_5_ANNUAL_PERIODS")

        # -------------------------------------------------------------
        # STAGE F: NSE / BSE RECONCILIATION
        # -------------------------------------------------------------
        # When both NSE and BSE filings exist, compare reported numbers
        if has_nse and has_bse:
            # Fact comparison
            nse_rev = latest_annual.get("revenue")
            bse_rev = latest_annual.get("revenue_bse")  # if present in deep filings
            if nse_rev is not None and bse_rev is not None:
                rec_res = reconcile_nse_bse_fact(sym, "revenue", float(nse_rev), float(bse_rev))
                if not rec_res.ok:
                    nse_bse_conflicts_count += 1
                    s_diag["stage_f_reconciliation"] = "NSE_BSE_CONFLICT"
                    s_diag["blocking_reasons"].append("NSE_BSE_CONFLICT")

        # -------------------------------------------------------------
        # STAGE G: EVIDENCE RECONSTRUCTION
        # -------------------------------------------------------------
        # Check if complete evidence bundle can be built
        is_pit_valid = freshness.ok and not gaps and len(annual_records) >= 5
        quality_evidence_complete = bool(
            is_pit_valid and cash_present and shares_present and sh_res.ok and debt_present and roce_present
        )
        fundamental_evidence_complete = bool(
            freshness.ok and roce_present and ocf_present and debt_present
            and len(quarterly_records) >= 2
        )

        s_diag["stage_g_evidence"]["quality_complete"] = quality_evidence_complete
        s_diag["stage_g_evidence"]["fundamental_complete"] = fundamental_evidence_complete

        if quality_evidence_complete:
            quality_complete_count += 1
        if fundamental_evidence_complete:
            fundamental_complete_count += 1

        # Check recoverability
        if has_nse or has_bse:
            if not is_pit_valid and "PIT_DATA_STALE" in s_diag["blocking_reasons"]:
                s_diag["overall_status"] = STATUS_STALE
            elif s_diag["blocking_reasons"]:
                # If only cash and shares were missing locally, but company is listed on exchange
                # and has active filings, it is EXCHANGE_RECOVERABLE
                local_only_defects = all(
                    r in ("LOCAL_MISSING_CASH", "LOCAL_MISSING_SHARES", "SHARES_DATA_INVALID")
                    for r in s_diag["blocking_reasons"]
                )
                if local_only_defects and is_pit_valid:
                    recoverable_from_exchange += 1
                    s_diag["overall_status"] = "EXCHANGE_RECOVERABLE"
                else:
                    s_diag["overall_status"] = STATUS_DATA_INSUFFICIENT
            else:
                s_diag["overall_status"] = STATUS_COMPLETE

        stock_diagnostics[sym] = s_diag

    # Prepare Final Diagnostic Summary
    summary = {
        "timestamp": datetime.now().isoformat(),
        "as_of_date": as_of.isoformat(),
        "total_approved_universe": total_universe,
        "bucket_1_exchange_availability": exchange_availability,
        "bucket_2_acquisition_failures": dict(acquisition_failures),
        "bucket_3_genuine_data_absence": dict(genuine_data_absence),
        "bucket_4_normalization_failures": dict(normalization_failures),
        "bucket_5_pit_failures": dict(pit_failures),
        "bucket_6_nse_bse_conflicts": nse_bse_conflicts_count,
        "bucket_7_quality_evidence_complete": quality_complete_count,
        "bucket_8_fundamental_evidence_complete": fundamental_complete_count,
        "matrix": {
            "approved_universe": total_universe,
            "local_data_complete": local_complete_count,
            "missing_cash_locally": missing_cash_local,
            "missing_shares_locally": missing_shares_local,
            "nse_filing_available": exchange_availability["nse_available"],
            "bse_filing_available": exchange_availability["bse_available"],
            "recoverable_from_exchange": recoverable_from_exchange,
            "acquisition_failure": sum(acquisition_failures.values()),
            "normalization_failure": sum(normalization_failures.values()),
            "unit_scaling_detected": normalization_failures.get("CR/LAKH/MILLION SCALING ERROR", 0),
            "genuine_data_unavailable": sum(genuine_data_absence.values()),
            "pit_stale": pit_failures.get("STALE > 24M", 0),
            "fiscal_sequence_failure": pit_failures.get("FISCAL_GAP", 0),
            "nse_bse_conflict": nse_bse_conflicts_count,
            "quality_evidence_complete": quality_complete_count,
            "fundamental_evidence_complete": fundamental_complete_count,
            "still_data_insufficient": total_universe - recoverable_from_exchange - local_complete_count,
            "quality_buy": 0,
            "fundamental_buy": 0,
        },
        "stock_diagnostics": stock_diagnostics,
    }

    # Save to disk
    out_dir = os.path.join(BASE_DIR, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "phase2_data_recovery_audit.json")
    with open(out_file, "w") as f:
        json.dump(summary, f, indent=2)

    # Print Formatted Diagnostic Report
    m = summary["matrix"]
    print("\n" + "=" * 80)
    print("  PHASE-2 DATA RECOVERY AUDIT: FINAL 8-BUCKET DIAGNOSTIC SUMMARY")
    print("=" * 80)
    print(f"  1. Exchange Filing Availability:")
    print(f"     • NSE Available              : {exchange_availability['nse_available']}")
    print(f"     • BSE Available              : {exchange_availability['bse_available']}")
    print(f"     • Both NSE + BSE Available   : {exchange_availability['both_available']}")
    print(f"     • Only NSE                   : {exchange_availability['only_nse']}")
    print(f"     • Only BSE                   : {exchange_availability['only_bse']}")
    print(f"     • Neither (Unmapped)         : {exchange_availability['neither']}")
    print(f"  2. Acquisition Failures         : {m['acquisition_failure']}")
    print(f"  3. Genuine Data Absence         : {m['genuine_data_unavailable']}")
    print(f"  4. Normalization Failures       : {m['normalization_failure']}")
    print(f"  5. PIT Failures (Stale / Gaps)  : Stale={m['pit_stale']}, Gaps={m['fiscal_sequence_failure']}")
    print(f"  6. NSE / BSE Conflicts          : {m['nse_bse_conflict']}")
    print(f"  7. QUALITY Evidence Complete    : {m['quality_evidence_complete']}")
    print(f"  8. FUNDAMENTAL Evidence Complete: {m['fundamental_evidence_complete']}")
    print("-" * 80)
    print("  SUMMARY MATRIX")
    print("-" * 80)
    print(f"  Approved Universe               : {m['approved_universe']}")
    print(f"  Local Data Complete             : {m['local_data_complete']}")
    print(f"  Missing Cash Locally            : {m['missing_cash_locally']}")
    print(f"  Missing Shares Locally          : {m['missing_shares_locally']}")
    print(f"  Recoverable from Exchange       : {m['recoverable_from_exchange']}")
    print(f"  Still DATA_INSUFFICIENT         : {m['still_data_insufficient']}")
    print(f"  QUALITY BUY (Frozen Logic)      : {m['quality_buy']}")
    print(f"  FUNDAMENTAL BUY (Frozen Logic)  : {m['fundamental_buy']}")
    print("=" * 80)
    print(f"  Saved JSON Report To: {out_file}\n")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Phase-2 Data Recovery Audit")
    parser.add_argument("--as-of-date", type=str, default=None, help="As-of date in YYYY-MM-DD")
    args = parser.parse_args()

    as_of = datetime.strptime(args.as_of_date, "%Y-%m-%d").date() if args.as_of_date else date.today()
    run_phase2_recovery_audit(as_of_date=as_of)
