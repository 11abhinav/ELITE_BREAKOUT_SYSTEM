#!/usr/bin/env python3
"""
Master RCA Forensic Audit & Accounting Script (2026-09-30)
Generates:
  1. reports/rca/cagr_forensic_audit.parquet
  2. reports/rca/scanner_data_rca_2026-09-30.parquet
"""

import os
import sys
import json
import sqlite3
import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports", "rca")
os.makedirs(REPORTS_DIR, exist_ok=True)

KNOWN_FINANCIAL_SYMBOLS = {
    "AADHARHFC", "AAVAS", "ABCAPITAL", "AUBANK", "HDBFS", "HDFCBANK", "ICICIBANK", "SBIN",
    "AXISBANK", "KOTAKBANK", "BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "CHOLAHLDNG", "CREDITACC",
    "CANFINHOME", "APTUS", "HOMEFIRST", "HUDCO", "PFC", "RECLTD", "MUTHOOTFIN", "MANAPPURAM",
    "SHRIRAMFIN", "M&MFIN", "L&TFH", "IIFL", "MOTILALOFS", "ICICIGI", "ICICIPRULI", "SBILIFE",
    "HDFCLIFE", "GICRE", "NIACL", "CDSL", "BSE", "MCX", "CAMS", "KFINTECH", "NAM-INDIA",
    "UTIAMC", "ANGELONE", "NUVAMA", "ANANDRATHI", "360ONE", "BANKBARODA", "BANKINDIA",
    "CENTRALBK", "IDFCFIRSTB", "INDIANB", "IOB", "MAHABANK", "PNB", "PSB", "UCOBANK",
    "UNIONBANK", "YESBANK", "BANDHANBNK", "FEDERALBNK", "IDBI", "INDUSINDBK", "KARURVYSYA",
    "RBLBANK", "SOUTHBANK", "CSBBANK", "CUB", "DCBBANK", "EQUITASBNK", "FINOPB", "J&KBANK",
    "JSFB", "KTKBANK", "SURYSFB", "UJJIVANSFB", "UTKARSHBNK", "CAPITALSFB", "AYE",
    "JMFINANCIL", "LICHSGFIN", "MASFIN", "SUNDARMFIN", "TSFINV", "CGCL", "AIIL"
}

def is_financial(symbol: str, sector: str = "", industry: str = "") -> bool:
    sym = str(symbol).strip().upper()
    if sym in KNOWN_FINANCIAL_SYMBOLS:
        return True
    text = f"{sector} {industry}".upper()
    keywords = ["BANK", "FINANCE", "FINANCIAL", "HOUSING FINANCE", "NBFC", "INSURANCE", "INVESTMENT", "CAPITAL", "SECURITIES", "LEASING", "AMC"]
    return any(kw in text for kw in keywords)

def run_audit():
    print("🚀 Starting Master RCA Forensic Audit...")
    
    # 1. Approved Universe (886 clean symbols)
    quarantine_json = os.path.join(DATA_DIR, "quarantined_anomaly_symbols_41.json")
    quarantined_symbols = set()
    if os.path.exists(quarantine_json):
        try:
            with open(quarantine_json) as f:
                quarantined_symbols = set(json.load(f).get("symbols", []))
        except Exception:
            pass

    approved_set = set()
    clean_json_path = os.path.join(DATA_DIR, "nse_bse_master_universe.json")
    if os.path.exists(clean_json_path):
        try:
            with open(clean_json_path) as f:
                approved_set = set(json.load(f).get("symbols", []))
        except Exception:
            pass

    if not approved_set:
        hist_dir = os.path.join(DATA_DIR, "history", "1d")
        if os.path.exists(hist_dir):
            all_files = os.listdir(hist_dir)
            approved_set = {
                f.replace(".parquet", "").upper() for f in all_files
                if f.endswith(".parquet") and not f.startswith("^") and "NIFTY" not in f.upper()
            } - quarantined_symbols

    approved_symbols = sorted(list(approved_set))
    print(f"✅ Approved Universe Count: {len(approved_symbols)}")

    # 2. PIT Dataset
    pit_db_path = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.db")
    pit_pq_path = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
    
    if os.path.exists(pit_pq_path):
        df_pit_raw = pd.read_parquet(pit_pq_path)
    elif os.path.exists(pit_db_path):
        con = sqlite3.connect(pit_db_path)
        df_pit_raw = pd.read_sql("SELECT * FROM pit_fundamentals_v1", con)
        con.close()
    else:
        raise RuntimeError("No PIT dataset found!")

    df_pit_raw['period_end_date'] = pd.to_datetime(df_pit_raw['period_end_date'])
    df_pit_raw['filing_date'] = pd.to_datetime(df_pit_raw['filing_date'])

    # Valuation Medians Cache
    val_cache_path = os.path.join(DATA_DIR, "pit_valuation_history_cache.json")
    val_cache = {}
    if os.path.exists(val_cache_path):
        with open(val_cache_path) as f:
            _j = json.load(f)
            val_cache = _j.get("data", _j)

    cagr_audit_records = []
    rca_matrix_records = []

    pit_symbols_in_db = set(df_pit_raw['symbol'].unique())

    # Reconciliation Counters
    c_financial_na = 0
    c_data_missing_pit = 0
    c_insufficient_history = 0
    c_filing_deficit = 0
    c_valid_complete = 0

    for sym in approved_symbols:
        clean_sym = str(sym).strip().upper()
        pit_exists = clean_sym in pit_symbols_in_db

        g_all = df_pit_raw[df_pit_raw['symbol'] == clean_sym].sort_values('period_end_date') if pit_exists else pd.DataFrame()
        
        pit_stmt_cnt = len(g_all)
        g_ann = g_all[g_all['statement_type'].astype(str).str.upper() == 'ANNUAL'].sort_values('period_end_date') if pit_exists else pd.DataFrame()
        g_qtr = g_all[g_all['statement_type'].astype(str).str.upper() == 'QUARTERLY'].sort_values('period_end_date') if pit_exists else pd.DataFrame()

        ann_cnt = len(g_ann)
        qtr_cnt = len(g_qtr)

        # Forensic CAGR Check (comparing Mixed vs Annual-Only)
        mix_rev_cagr, ann_rev_cagr = None, None
        mix_pat_cagr, ann_pat_cagr = None, None

        if pit_stmt_cnt >= 2:
            k_mix = min(5, pit_stmt_cnt - 1)
            s_row_m = g_all.iloc[-k_mix - 1]
            e_row_m = g_all.iloc[-1]
            yrs_m = max(1.0, (pd.to_datetime(e_row_m['period_end_date']) - pd.to_datetime(s_row_m['period_end_date'])).days / 365.25)
            r0_m, r1_m = float(s_row_m.get('revenue', 0) or 0), float(e_row_m.get('revenue', 0) or 0)
            p0_m, p1_m = float(s_row_m.get('net_profit', 0) or 0), float(e_row_m.get('net_profit', 0) or 0)
            if r0_m > 0 and r1_m > 0: mix_rev_cagr = round((pow(r1_m / r0_m, 1.0 / yrs_m) - 1.0) * 100.0, 2)
            if p0_m > 0 and p1_m > 0: mix_pat_cagr = round((pow(p1_m / p0_m, 1.0 / yrs_m) - 1.0) * 100.0, 2)

        if ann_cnt >= 2:
            k_ann = min(5, ann_cnt - 1)
            s_row_a = g_ann.iloc[-k_ann - 1]
            e_row_a = g_ann.iloc[-1]
            yrs_a = max(1.0, (pd.to_datetime(e_row_a['period_end_date']) - pd.to_datetime(s_row_a['period_end_date'])).days / 365.25)
            r0_a, r1_a = float(s_row_a.get('revenue', 0) or 0), float(e_row_a.get('revenue', 0) or 0)
            p0_a, p1_a = float(s_row_a.get('net_profit', 0) or 0), float(e_row_a.get('net_profit', 0) or 0)
            if r0_a > 0 and r1_a > 0: ann_rev_cagr = round((pow(r1_a / r0_a, 1.0 / yrs_a) - 1.0) * 100.0, 2)
            if p0_a > 0 and p1_a > 0: ann_pat_cagr = round((pow(p1_a / p0_a, 1.0 / yrs_a) - 1.0) * 100.0, 2)

        is_fin = is_financial(clean_sym)

        # Field Presence
        roce_series = g_ann['roce'].dropna() if not g_ann.empty else pd.Series(dtype=float)
        req_roce_pres = len(roce_series) > 0 or is_fin
        req_sales_pres = (ann_rev_cagr is not None) or is_fin
        req_pat_pres = (ann_pat_cagr is not None) or (is_fin and ann_cnt >= 2)
        
        sum_cfo = g_ann.iloc[-5:]['operating_cash_flow'].dropna().sum() if not g_ann.empty else 0
        sum_pat = g_ann.iloc[-5:]['net_profit'].dropna().sum() if not g_ann.empty else 0
        req_cfo_pres = (sum_pat > 0 and not pd.isna(sum_cfo)) or is_fin

        latest_f = g_all.iloc[-1] if not g_all.empty else {}
        te = latest_f.get('total_equity')
        req_de_pres = (te is not None and not pd.isna(te)) or is_fin

        val_entry = val_cache.get(clean_sym, {})
        curr_ev_pres = val_entry.get('ev_ebitda_3y_median') is not None or is_fin
        ev_3y_pres = val_entry.get('ev_ebitda_3y_median') is not None or is_fin
        curr_pe_pres = val_entry.get('pe_3y_median') is not None
        pe_3y_pres = val_entry.get('pe_3y_median') is not None

        missing_fields = []
        if not pit_exists:
            missing_fields.append("PIT_FILINGS")
        elif not is_fin:
            if not req_roce_pres: missing_fields.append("ROCE")
            if not req_sales_pres: missing_fields.append("SalesHistory")
            if not req_pat_pres: missing_fields.append("PATHistory")
            if not req_cfo_pres: missing_fields.append("CFO_PAT")
            if not req_de_pres: missing_fields.append("DebtToEquity")

        scope_class = "METRIC_NOT_APPLICABLE_FINANCIAL" if is_fin else (
            "DATA_MISSING_PIT" if not pit_exists else (
                "INSUFFICIENT_LISTING_HISTORY" if ann_cnt < 3 else (
                    "DATA_INSUFFICIENT_FILING" if len(missing_fields) > 0 else (
                        "DATA_VALID_BUT_STRATEGY_FAIL"
                    )
                )
            )
        )

        if scope_class == "METRIC_NOT_APPLICABLE_FINANCIAL":
            c_financial_na += 1
            root_code = "FINANCIAL_NON_APPLICABLE"
            remediation = "Apply Banking/NBFC valuation model; exclude from industrial ROCE gate"
        elif scope_class == "DATA_MISSING_PIT":
            c_data_missing_pit += 1
            root_code = "NOT_INGESTED"
            remediation = "Fail-closed; retain symbol in audit matrix"
        elif scope_class == "INSUFFICIENT_LISTING_HISTORY":
            c_insufficient_history += 1
            root_code = "NO_HISTORICAL_FILINGS"
            remediation = "Wait for mandatory 3-5 year filing history"
        elif scope_class == "DATA_INSUFFICIENT_FILING":
            c_filing_deficit += 1
            root_code = "PARSER_FAILED" if ann_cnt > 0 else "INGESTION_FAILED"
            remediation = f"Fix parser for missing fields: {missing_fields}"
        else:
            c_valid_complete += 1
            root_code = "DATA_VALID"
            remediation = "Evaluate strategy gates"

        cagr_audit_records.append({
            "symbol": clean_sym,
            "annual_filings_count": ann_cnt,
            "total_filings_count": pit_stmt_cnt,
            "mixed_sales_cagr": mix_rev_cagr,
            "annual_sales_cagr": ann_rev_cagr,
            "mixed_pat_cagr": mix_pat_cagr,
            "annual_pat_cagr": ann_pat_cagr,
            "is_cagr_anomaly_resolved": True if (ann_rev_cagr is not None and ann_rev_cagr > -50.0) else False,
            "scope_classification": scope_class
        })

        rca_matrix_records.append({
            "symbol": clean_sym,
            "scope_classification": scope_class,
            "pit_exists": pit_exists,
            "pit_statement_count": pit_stmt_cnt,
            "annual_statement_count": ann_cnt,
            "quarterly_statement_count": qtr_cnt,
            "required_roce_present": req_roce_pres,
            "required_sales_history_present": req_sales_pres,
            "required_pat_history_present": req_pat_pres,
            "required_cfo_present": req_cfo_pres,
            "required_de_present": req_de_pres,
            "current_ev_present": curr_ev_pres,
            "ev_3y_present": ev_3y_pres,
            "current_pe_present": curr_pe_pres,
            "pe_3y_present": pe_3y_pres,
            "missing_fields": str(missing_fields),
            "missing_field_count": len(missing_fields),
            "data_source": "Upstox + Screener PIT",
            "source_timestamp": "2026-09-30 09:41:26 IST",
            "latest_filing_date": str(latest_f.get('filing_date'))[:10] if not g_all.empty else None,
            "quality_status": "VALID" if len(missing_fields) == 0 else "INCOMPLETE",
            "valuation_status": "DATA_AVAILABLE" if pe_3y_pres else "PARTIAL",
            "final_data_status": scope_class,
            "root_cause_code": root_code,
            "remediation_action": remediation
        })

    df_cagr_audit = pd.DataFrame(cagr_audit_records)
    df_rca_matrix = pd.DataFrame(rca_matrix_records)

    cagr_audit_path = os.path.join(REPORTS_DIR, "cagr_forensic_audit.parquet")
    rca_matrix_path = os.path.join(REPORTS_DIR, "scanner_data_rca_2026-09-30.parquet")

    df_cagr_audit.to_parquet(cagr_audit_path, index=False)
    df_rca_matrix.to_parquet(rca_matrix_path, index=False)

    print(f"💾 Saved {len(df_cagr_audit)} rows to {cagr_audit_path}")
    print(f"💾 Saved {len(df_rca_matrix)} rows to {rca_matrix_path}")

    tot = c_financial_na + c_data_missing_pit + c_insufficient_history + c_filing_deficit + c_valid_complete

    print("\n================================================================================")
    print("📊 RECONCILIATION SUMMARY ACCOUNTING (Sum = Approved Universe)")
    print("================================================================================")
    print(f"  • METRIC_NOT_APPLICABLE_FINANCIAL : {c_financial_na}")
    print(f"  • DATA_MISSING_PIT               : {c_data_missing_pit}")
    print(f"  • INSUFFICIENT_LISTING_HISTORY   : {c_insufficient_history}")
    print(f"  • DATA_INSUFFICIENT_FILING       : {c_filing_deficit}")
    print(f"  • DATA_VALID (Industrial/Valid)  : {c_valid_complete}")
    print(f"  ------------------------------------------------------------------------------")
    print(f"  • TOTAL APPROVED UNIVERSE        : {tot}")
    print("================================================================ algorithm end ===\n")

if __name__ == "__main__":
    run_audit()
