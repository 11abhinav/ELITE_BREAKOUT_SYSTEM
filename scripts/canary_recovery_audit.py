#!/usr/bin/env python3
"""
REAL-PROVIDER READ-ONLY CANARY — Fundamental Data-Recovery Certification
=======================================================================

Exercises the REAL NSE / BSE / Upstox providers (FYERS adapter classification and
Screener operator-attested reference included) against a frozen 25-symbol cohort,
and emits per-field evidence:

    provider | HTTP/result status | raw records | usable records | annual periods
    parser status | metric status | final classification

READ-ONLY GUARANTEES (layered, independent):
  G1. QUARANTINE_DB_SYNC_ENABLED=false set before any app import.
  G2. FundamentalSourceRouter._persist_raw_filings replaced with a no-op counter.
  G3. app.database write entry points (upload_parquet_to_db, submit_background_upload,
      insert_notification) replaced with guards that RECORD + BLOCK any call.
  G4. DataAvailabilityAuditor used via classify_field() only (no audit()/_persist/
      _notify_admin/_write_reports), persist_to_db=False.
  G5. No PitRecoveryStatusStore is instantiated.
  G6. Filesystem tripwire: every file under data/ (excluding the canary's own output
      dir) is fingerprinted (size+mtime) before and after; ANY change => READ_ONLY_VIOLATION.

Canary verdict is never "certified" unless:
  - READ_ONLY intact (G3 blocked calls == 0, G6 changes == 0),
  - the P0 parser cohort (ADOR/BASF/GABRIEL/GLOBUSSPR/STYRENIX) shows no
    "raw > 0, usable == 0" on any exchange provider,
  - every reachable expected final state was observed at least once.

Usage:
    ./.venv/bin/python3 scripts/canary_recovery_audit.py
"""
import os
import sys

# ── G1: must precede any app import ──────────────────────────────────────────
os.environ["QUARANTINE_DB_SYNC_ENABLED"] = "false"

import csv
import json
import hashlib
import time
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "app"))

IST = ZoneInfo("Asia/Kolkata")
DATA_DIR = os.path.join(BASE_DIR, "data")
RUN_TS = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
OUT_DIR = os.path.join(DATA_DIR, "reports", "canary", RUN_TS)

# ── Frozen cohort (defined BEFORE observing results) ─────────────────────────
COHORT = [
    # (symbol, category, expected-state hypothesis — informational only)
    ("RELIANCE",   "LARGE_CAP / NSE+BSE / consolidated-vs-standalone", "RECOVERED"),
    ("TCS",        "LARGE_CAP / NSE+BSE",                              "RECOVERED"),
    ("INFY",       "LARGE_CAP / NSE+BSE",                              "RECOVERED"),
    ("HDFCBANK",   "LARGE_CAP / BANK (ROCE structurally N/A)",         "PARTIAL"),
    ("ICICIBANK",  "LARGE_CAP / BANK",                                 "PARTIAL"),
    ("SBIN",       "LARGE_CAP / PSU BANK",                             "PARTIAL"),
    ("ADOR",       "P0 PARSER COHORT / small-cap",                     "RECOVERED"),
    ("BASF",       "P0 PARSER COHORT / consolidated-vs-standalone",    "RECOVERED"),
    ("GABRIEL",    "P0 PARSER COHORT / mid-cap",                       "RECOVERED"),
    ("GLOBUSSPR",  "P0 PARSER COHORT / mid-cap",                       "RECOVERED"),
    ("STYRENIX",   "P0 PARSER COHORT / RENAMED (ex-INEOS Styrolution)", "RECOVERED"),
    ("MAHLIFE",    "MID_CAP / screener-reference case",                "REFERENCE_ONLY?"),
    ("MOLDTKPAC",  "MID_CAP",                                          "RECOVERED"),
    ("JUSTDIAL",   "MID_CAP",                                          "RECOVERED"),
    ("KENNAMET",   "SMALL_CAP / June FY-end",                          "RECOVERED"),
    ("VIMTALABS",  "SMALL_CAP",                                        "RECOVERED"),
    ("MANYAVAR",   "RECENT LISTING (Feb-2022) / short history",        "SHORT_HISTORY"),
    ("ABSLAMC",    "RECENT LISTING (Oct-2021) / short history",        "SHORT_HISTORY"),
    ("HEXT",       "RECENT LISTING (Jan-2021) / demerger",             "SHORT_HISTORY"),
    ("VERANDA",    "RECENT LISTING / historical-gap candidate",        "GAP/SHORT"),
    ("IDEA",       "NEGATIVE NUMBERS (persistent losses)",            "INVALID_BASE"),
    ("YESBANK",    "NEGATIVE NUMBERS (loss years in window)",         "INVALID_BASE"),
    ("TATAMOTORS", "CONSOLIDATED-vs-STANDALONE divergence / demerger", "RECOVERED"),
    ("ETERNAL",    "RENAMED (ex-ZOMATO) / symbol mapping",             "MAPPING?"),
    ("ZZZNOTREAL", "DELIBERATE SYMBOL MAPPING FAILURE (control)",      "SYMBOL_MAPPING_FAILURE"),
]
P0_PARSER_COHORT = {"ADOR", "BASF", "GABRIEL", "GLOBUSSPR", "STYRENIX"}

FIELDS = [
    # (metric attribute on ReconciledCanonicalMetrics, audit field name)
    ("roce_5y", "ROCE"),
    ("sales_cagr_5y", "sales_cagr_5y"),
    ("pat_cagr_5y", "pat_cagr_5y"),
    ("cfo_pat_5y", "cfo_pat_5y"),
    ("debt_to_equity", "debt"),
]

EXPECTED_STATES = [
    "RECOVERED_FROM_NSE",
    "RECOVERED_FROM_BSE",
    "RECOVERED_FROM_UPSTOX",
    "RECOVERED_FROM_FYERS",
    "REFERENCE_ONLY_AVAILABLE",
    "CONFIRMED_NO_DATA_ANYWHERE",
    "CONFIRMED_SHORT_HISTORY",
    "HISTORICAL_FILING_GAP",
    "PARSER_OR_FIELD_MAPPING_FAILURE",
    "SYMBOL_MAPPING_FAILURE",
]
# Auditor enum → certification state vocabulary
STATE_ALIASES = {
    "INSUFFICIENT_HISTORICAL_DEPTH": "CONFIRMED_SHORT_HISTORY",
    "SCREENER_ONLY_DATA_SOURCE": "REFERENCE_ONLY_AVAILABLE",
}
# States that cannot occur by design in the current provider surface.
UNREACHABLE_BY_DESIGN = {
    "RECOVERED_FROM_FYERS": (
        "FYERS adapter classifies fields as UNSUPPORTED_FIELD when the API surface "
        "available to the application does not expose the required fundamental field."
    ),
}


# ── G6: filesystem tripwire ──────────────────────────────────────────────────
def _fs_snapshot():
    snap = {}
    canary_root = os.path.join(DATA_DIR, "reports", "canary")
    for root, _dirs, files in os.walk(DATA_DIR):
        if root.startswith(canary_root):
            continue
        for fn in files:
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
                snap[p] = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
    return snap


def _fs_diff(before, after):
    changed = []
    for p, sig in after.items():
        if p not in before:
            changed.append(("CREATED", p))
        elif before[p] != sig:
            changed.append(("MODIFIED", p))
    for p in before:
        if p not in after:
            changed.append(("DELETED", p))
    return changed


# ── G2/G3: write guards ──────────────────────────────────────────────────────
BLOCKED_CALLS = []
PERSIST_SUPPRESSED = []


def _install_write_guards():
    import app.database as db

    def _guard(name):
        def _blocked(*a, **k):
            BLOCKED_CALLS.append({"fn": name, "args": [str(x)[:80] for x in a]})
            return None
        return _blocked

    for name in ("upload_parquet_to_db", "submit_background_upload", "insert_notification"):
        if hasattr(db, name):
            setattr(db, name, _guard(name))

    from app.data_providers import fundamental_source_router as fsr
    import app.data_providers.data_availability_auditor as daa
    # The auditor imports insert_notification/get_connection at module level
    daa.insert_notification = _guard("auditor.insert_notification")

    def _noop_persist(self, symbol, records):
        PERSIST_SUPPRESSED.append({"symbol": symbol, "records": len(records or [])})

    fsr.FundamentalSourceRouter._persist_raw_filings = _noop_persist
    return fsr, daa


def _metric_status(val):
    if val is None:
        return "METRIC_UNAVAILABLE"
    if val == -999.0:
        return "INVALID_BASE_SENTINEL"
    return "AVAILABLE"


def _recovered_from(trace):
    """Attribute recovery to approved providers that produced usable records."""
    srcs = []
    if trace.get("nse_records", 0) > 0:
        srcs.append("NSE")
    if trace.get("bse_records", 0) > 0:
        srcs.append("BSE")
    if trace.get("upstox_records", 0) > 0:
        srcs.append("UPSTOX")
    if not srcs and trace.get("local_records", 0) > 0:
        srcs.append("LOCAL_CERTIFIED_CACHE")
    return srcs


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    started = time.time()
    fs_before = _fs_snapshot()

    fsr, daa = _install_write_guards()
    router = fsr.FundamentalSourceRouter()
    auditor = daa.DataAvailabilityAuditor(persist_to_db=False)

    rows = []
    provider_rows = []
    errors = []

    for sym, category, hypothesis in COHORT:
        print(f"[CANARY] {sym:<11} {category}", flush=True)
        try:
            metrics = router.execute_progressive_recovery(sym)
            trace = dict(router.last_trace.get(sym, {}))
        except Exception as e:
            errors.append({"symbol": sym, "error": repr(e), "tb": traceback.format_exc()[-1500:]})
            metrics, trace = None, {"symbol_mapping_failure": True, "exception": repr(e)}

        # Provider-level evidence
        for prov, raw_k, usable_k, ann_k, st_k in (
            ("NSE", "nse_raw_count", "nse_usable_count", "nse_annual", "nse_parser_status"),
            ("BSE", "bse_raw_count", "bse_usable_count", "bse_annual", "bse_status"),
            ("UPSTOX", "upstox_records", "upstox_records", "upstox_annual", None),
        ):
            raw = int(trace.get(raw_k, 0) or 0)
            usable = int(trace.get(usable_k, 0) or 0)
            if prov == "UPSTOX":
                status = "ISIN_UNRESOLVED" if not trace.get("isin_resolved") else (
                    "AVAILABLE" if usable else "NO_DATA")
            else:
                status = trace.get(st_k, "NOT_CHECKED")
            provider_rows.append({
                "symbol": sym, "provider": prov, "result_status": status,
                "raw_records": raw, "usable_records": usable,
                "annual_periods": int(trace.get(ann_k, 0) or 0),
                "raw_present_usable_zero": bool(prov != "UPSTOX" and raw > 0 and usable == 0),
            })
        provider_rows.append({
            "symbol": sym, "provider": "FYERS",
            "result_status": trace.get("fyers_api_classification", "UNSUPPORTED_FIELD"),
            "raw_records": 0, "usable_records": 0, "annual_periods": 0,
            "raw_present_usable_zero": False,
        })

        recovered = _recovered_from(trace)
        for attr, audit_field in FIELDS:
            val = getattr(metrics, attr, None) if metrics is not None else None
            mstat = _metric_status(val)
            if mstat == "AVAILABLE" and recovered:
                final = f"RECOVERED_FROM_{recovered[0]}"
                rec_screener = "N/A"
                rec_fyers = trace.get("fyers_api_classification", "UNSUPPORTED_FIELD")
            else:
                t = dict(trace)
                t.setdefault("fyers_status", trace.get("fyers_api_classification", "UNSUPPORTED_FIELD"))
                if mstat == "INVALID_BASE_SENTINEL":
                    t["invalid_base"] = True
                rec = auditor.classify_field(sym, audit_field, t)
                final = STATE_ALIASES.get(rec.classification, rec.classification)
                rec_screener = rec.screener_status
                rec_fyers = rec.fyers_status
                assert rec.production_value_written is False and rec.buy_allowed is False
            rows.append({
                "symbol": sym,
                "category": category,
                "field": attr,
                "value": val,
                "metric_status": mstat,
                "providers_with_usable_records": "+".join(recovered) or "NONE",
                "nse_raw": trace.get("nse_raw_count", 0),
                "nse_usable": trace.get("nse_usable_count", 0),
                "nse_parser_status": trace.get("nse_parser_status", "NOT_CHECKED"),
                "bse_raw": trace.get("bse_raw_count", 0),
                "bse_usable": trace.get("bse_usable_count", 0),
                "bse_status": trace.get("bse_status", "NOT_CHECKED"),
                "upstox_records": trace.get("upstox_records", 0),
                "max_annual_periods": max(
                    int(trace.get(k, 0) or 0)
                    for k in ("nse_annual", "bse_annual", "upstox_annual", "local_annual")
                ),
                "fyers_status": rec_fyers,
                "screener_status": rec_screener,
                "router_status": getattr(getattr(metrics, "overall_status", None), "name", "EXCEPTION"),
                "final_classification": final,
                "hypothesis": hypothesis,
            })

    fs_after = _fs_snapshot()
    fs_changes = _fs_diff(fs_before, fs_after)

    # ── Verdict computation ──────────────────────────────────────────────────
    observed = sorted({r["final_classification"] for r in rows})
    coverage = {}
    for st in EXPECTED_STATES:
        if st in observed:
            coverage[st] = "OBSERVED"
        elif st in UNREACHABLE_BY_DESIGN:
            coverage[st] = "UNREACHABLE_BY_DESIGN"
        else:
            coverage[st] = "NOT_OBSERVED"

    p0 = {}
    for sym in sorted(P0_PARSER_COHORT):
        prs = [p for p in provider_rows if p["symbol"] == sym and p["provider"] in ("NSE", "BSE")]
        fr = [r for r in rows if r["symbol"] == sym]
        p0[sym] = {
            "raw_present_usable_zero_on": [p["provider"] for p in prs if p["raw_present_usable_zero"]],
            "nse": next((f'{p["result_status"]} raw={p["raw_records"]} usable={p["usable_records"]}'
                         for p in prs if p["provider"] == "NSE"), "-"),
            "bse": next((f'{p["result_status"]} raw={p["raw_records"]} usable={p["usable_records"]}'
                         for p in prs if p["provider"] == "BSE"), "-"),
            "metrics_available": sum(1 for r in fr if r["metric_status"] == "AVAILABLE"),
            "metrics_total": len(fr),
        }
    p0_parser_pass = all(not v["raw_present_usable_zero_on"] for v in p0.values())
    p0_metrics_pass = all(v["metrics_available"] > 0 for v in p0.values())

    read_only_intact = (len(BLOCKED_CALLS) == 0 and len(fs_changes) == 0)
    coverage_pass = all(v != "NOT_OBSERVED" for v in coverage.values())
    all_providers_failed = all(
        p["result_status"] in ("BSE_HTTP_ERROR", "HTTP_ERROR", "NO_DATA_RETURNED", "NOT_CHECKED")
        for p in provider_rows if p["provider"] in ("NSE", "BSE") and p["symbol"] != "ZZZNOTREAL"
    )

    if not read_only_intact:
        verdict = "INVALID — READ_ONLY_VIOLATION"
    elif all_providers_failed:
        verdict = "NON_CERTIFIABLE — PROVIDERS_UNREACHABLE (network/auth)"
    elif p0_parser_pass and p0_metrics_pass and coverage_pass and not errors:
        verdict = "CANARY_PASS"
    else:
        verdict = "CANARY_FAIL"

    summary = {
        "run_ts_ist": RUN_TS,
        "duration_s": round(time.time() - started, 1),
        "verdict": verdict,
        "production_certified": False,  # Governance decision; never set by the script
        "read_only": {
            "intact": read_only_intact,
            "blocked_db_write_calls": BLOCKED_CALLS,
            "raw_filing_persist_suppressed": PERSIST_SUPPRESSED,
            "filesystem_changes_under_data": fs_changes[:200],
            "quarantine_db_sync_env": os.environ.get("QUARANTINE_DB_SYNC_ENABLED"),
        },
        "p0_parser_remediation": {
            "pass_no_raw_present_usable_zero": p0_parser_pass,
            "pass_metrics_extracted": p0_metrics_pass,
            "per_symbol": p0,
        },
        "state_machine_coverage": coverage,
        "unreachable_by_design_notes": UNREACHABLE_BY_DESIGN,
        "observed_states": observed,
        "errors": errors,
        "cohort_size": len(COHORT),
    }

    with open(os.path.join(OUT_DIR, "canary_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    for name, data in (("canary_field_evidence.csv", rows), ("canary_provider_evidence.csv", provider_rows)):
        if data:
            with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
                w.writeheader()
                w.writerows(data)
    digest = hashlib.sha256(
        json.dumps({"rows": rows, "providers": provider_rows}, sort_keys=True, default=str).encode()
    ).hexdigest()
    with open(os.path.join(OUT_DIR, "evidence.sha256"), "w") as f:
        f.write(digest + "\n")

    # ── Console report ───────────────────────────────────────────────────────
    print("\n" + "=" * 78)
    print(f"CANARY VERDICT: {verdict}   (production_certified=False)")
    print(f"READ-ONLY intact: {read_only_intact} | blocked DB calls={len(BLOCKED_CALLS)} "
          f"| fs changes={len(fs_changes)} | persist suppressed={len(PERSIST_SUPPRESSED)}")
    print("-" * 78)
    print("P0 PARSER COHORT:")
    for s, v in p0.items():
        print(f"  {s:<10} NSE[{v['nse']}]  BSE[{v['bse']}]  "
              f"metrics={v['metrics_available']}/{v['metrics_total']}  "
              f"raw>0&usable=0 on={v['raw_present_usable_zero_on'] or 'none'}")
    print("-" * 78)
    print("STATE-MACHINE COVERAGE:")
    for st, c in coverage.items():
        print(f"  {st:<34} {c}")
    if errors:
        print(f"-- {len(errors)} symbol exception(s); see canary_summary.json")
    print(f"Evidence: {OUT_DIR}  sha256={digest[:16]}…")
    print("=" * 78)
    return 0 if verdict == "CANARY_PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
