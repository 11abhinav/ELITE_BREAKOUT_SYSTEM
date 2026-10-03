#!/usr/bin/env python3
"""
scripts/canonical_pit_publisher.py
===================================
Single-writer, gated Canonical PIT Publisher.

Exactly ONE function in the entire system is allowed to atomically publish
canonical_pit_rebuilt.parquet to production:

    publish_canonical_pit(candidate_path, reason)

All other components (FILING_WATCHER, PRE_RECOVERY, valuation builder)
must produce a *candidate* parquet and call this function to promote it.

NEVER-DOWNGRADE GATE (18 dimensions):
  1.  Symbol universe – exact set equality (no unexplained removals)
  2.  Row count       – no unexplained loss
  3.  current_ev_ebitda completeness
  4.  ev_ebitda_3y_median completeness
  5.  current_pe completeness
  6.  pe_3y_median completeness
  7.  revenue completeness
  8.  ebitda completeness
  9.  net_profit completeness
  10. operating_cash_flow completeness
  11. total_debt completeness
  12. cash_and_equivalents completeness
  13. shares_outstanding_m completeness
  14. roce_5y_avg completeness
  15. annual_filing_count completeness (filing coverage)
  16. CERTIFIED provenance count
  17. VALID PIT freshness count  (stale must not increase)
  18. filing_gap_detected count  (unresolved gaps must not increase)

CONCURRENT PUBLICATION PROTECTION:
  - A filesystem lock (canonical_pit_rebuilt.parquet.lock) guards the
    read-compare-write sequence so two simultaneous FILING_WATCHER runs
    cannot race each other.
  - Optimistic hash check: if the canonical file changed between the
    gate read and the publish moment, the publish is aborted.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd

BASE_DIR = os.getenv(
    "ELITE_BASE_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from app.database import upload_parquet_to_db
except ImportError:
    try:
        from database import upload_parquet_to_db
    except ImportError:
        upload_parquet_to_db = None

logger = logging.getLogger("canonical_pit_publisher")

CANONICAL_PATH = os.path.join(BASE_DIR, "data", "canonical_pit_rebuilt.parquet")
MASTER_V2_PATH = os.path.join(BASE_DIR, "data", "daily_builder_master_v2.parquet")
LOCK_FILE      = CANONICAL_PATH + ".lock"
META_FILE      = CANONICAL_PATH.replace(".parquet", "_meta.json")
HISTORY_LOG    = CANONICAL_PATH.replace(".parquet", "_publication_history.jsonl")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _notna_count(df: pd.DataFrame, col: str) -> int:
    return int(df[col].notna().sum()) if col in df.columns else 0


def _value_count(df: pd.DataFrame, col: str, val: Any) -> int:
    return int((df[col] == val).sum()) if col in df.columns else 0


# ---------------------------------------------------------------------------
# 18-Dimension Never-Downgrade Gate
# ---------------------------------------------------------------------------

def _run_never_downgrade_gate(
    df_old: pd.DataFrame,
    df_new: pd.DataFrame,
    publisher_reason: str,
) -> Tuple[bool, List[str]]:
    """
    Returns (gate_passed: bool, reasons: List[str]).
    reasons is empty on pass; contains all violation descriptions on fail.
    """
    reasons: List[str] = []

    def _chk_completeness(col: str, label: str):
        old_v = _notna_count(df_old, col)
        new_v = _notna_count(df_new, col)
        if new_v < old_v:
            reasons.append(f"{label}: {old_v} -> {new_v}")

    # 1. Symbol-set equality
    old_syms: Set[str] = set(df_old["symbol"].str.upper()) if "symbol" in df_old.columns else set()
    new_syms: Set[str] = set(df_new["symbol"].str.upper()) if "symbol" in df_new.columns else set()
    lost = old_syms - new_syms
    gained = new_syms - old_syms
    if lost:
        reasons.append(f"Symbol set: {len(lost)} symbols lost ({', '.join(sorted(lost)[:10])}{'...' if len(lost) > 10 else ''})")
    if gained:
        # gained is informational only – not a downgrade
        logger.info(f"[GATE] {len(gained)} new symbols in candidate: {', '.join(sorted(gained)[:5])}...")

    # 2. Row count
    if len(df_new) < len(df_old):
        reasons.append(f"Row count: {len(df_old)} -> {len(df_new)}")

    # 3-16. Completeness dimensions
    _chk_completeness("current_ev_ebitda",    "Current EV/EBITDA complete")
    _chk_completeness("ev_ebitda_3y_median",  "3Y EV/EBITDA median complete")
    _chk_completeness("current_pe",           "Current PE complete")
    _chk_completeness("pe_3y_median",         "3Y PE median complete")
    _chk_completeness("revenue",              "Revenue complete")
    _chk_completeness("ebitda",               "EBITDA complete")
    _chk_completeness("net_profit",           "Net Profit complete")
    _chk_completeness("operating_cash_flow",  "CFO complete")
    _chk_completeness("total_debt",           "Total Debt complete")
    _chk_completeness("cash_and_equivalents", "Cash complete")
    _chk_completeness("shares_outstanding_m", "Shares complete")
    _chk_completeness("roce_5y_avg",          "ROCE 5Y complete")
    _chk_completeness("annual_filing_count",  "Filing coverage complete")

    # 16. Certified provenance count
    old_cert = _value_count(df_old, "provenance_status", "CERTIFIED")
    new_cert = _value_count(df_new, "provenance_status", "CERTIFIED")
    if new_cert < old_cert:
        reasons.append(f"CERTIFIED provenance: {old_cert} -> {new_cert}")

    # 17. Valid PIT freshness (stale must not increase)
    old_stale = _value_count(df_old, "pit_freshness_status", "DATA_STALE")
    new_stale = _value_count(df_new, "pit_freshness_status", "DATA_STALE")
    if new_stale > old_stale:
        reasons.append(f"Stale records increased: {old_stale} -> {new_stale}")

    # 18. Filing gaps must not increase
    old_gaps = _notna_count(df_old, "filing_gap_detected") and int(df_old["filing_gap_detected"].sum()) if "filing_gap_detected" in df_old.columns else 0
    new_gaps = _notna_count(df_new, "filing_gap_detected") and int(df_new["filing_gap_detected"].sum()) if "filing_gap_detected" in df_new.columns else 0
    if new_gaps > old_gaps:
        reasons.append(f"Filing gaps increased: {old_gaps} -> {new_gaps}")

    return len(reasons) == 0, reasons


# ---------------------------------------------------------------------------
# Strict Exact Universe Set Validation Contract
# ---------------------------------------------------------------------------

REQUIRED_UNIVERSE_PATH = os.path.join(BASE_DIR, "data", "certified_clean_universe_886.json")
REQUIRED_SCHEMA_COLS = [
    "symbol", "roce_5y_avg", "sales_cagr_5y", "pat_cagr_5y",
    "cfo_pat_5y_ratio", "current_ev_ebitda", "ev_ebitda_3y_median"
]

def load_required_universe(universe_path: Optional[str] = None) -> Set[str]:
    path = universe_path or os.getenv("REQUIRED_UNIVERSE_PATH", REQUIRED_UNIVERSE_PATH)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return set(s.upper() for s in json.load(f)["symbols"])
        except Exception as e:
            logger.warning(f"Could not load required universe from {path}: {e}")
    return set()

def validate_canonical_snapshot_exact(
    df: pd.DataFrame,
    required_universe: Set[str],
    expected_row_count: Optional[int] = None
) -> Tuple[bool, str]:
    if expected_row_count is None:
        expected_row_count = len(required_universe)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return False, "EMPTY_OR_NONE"
    if "symbol" not in df.columns:
        return False, "MISSING_SYMBOL_COLUMN"
        
    if df["symbol"].isna().any():
        return False, f"NULL_SYMBOLS_DETECTED: {df['symbol'].isna().sum()} nulls"
        
    symbols_raw = df["symbol"].astype(str)
    if (symbols_raw.str.strip() == "").any():
        return False, "BLANK_SYMBOLS_DETECTED"
        
    symbols_norm = symbols_raw.str.strip().str.upper()
    if symbols_norm.duplicated().any():
        dups = symbols_norm[symbols_norm.duplicated()].unique()
        return False, f"DUPLICATE_SYMBOLS_DETECTED: {len(dups)} duplicates ({list(dups)[:5]})"
        
    import re
    malformed = [s for s in symbols_norm if not re.match(r"^[A-Z0-9\-_&]+$", s)]
    if malformed:
        return False, f"MALFORMED_SYMBOLS_DETECTED: {malformed[:5]}"
        
    cand_symbols = set(symbols_norm)
    missing = required_universe - cand_symbols
    extra = cand_symbols - required_universe
    
    if cand_symbols != required_universe:
        reasons = []
        if missing: reasons.append(f"MISSING {len(missing)} required symbols")
        if extra:   reasons.append(f"EXTRA {len(extra)} unapproved symbols ({sorted(list(extra))[:5]})")
        return False, f"EXACT_UNIVERSE_MISMATCH: {'; '.join(reasons)}"
        
    if len(df) != expected_row_count:
        return False, f"ROW_COUNT_MISMATCH: got {len(df)}, expected {expected_row_count}"
        
    missing_cols = [c for c in REQUIRED_SCHEMA_COLS if c not in df.columns]
    if missing_cols:
        return False, f"MISSING_SCHEMA_COLS: {missing_cols}"
        
    return True, "CERTIFIED_EXACT_EQUAL"


# ---------------------------------------------------------------------------
# Snapshot metadata builder
# ---------------------------------------------------------------------------

def _build_snapshot_meta(
    df: pd.DataFrame,
    file_hash: str,
    previous_hash: Optional[str],
    publication_decision: str,
    publication_reason: str,
    publisher_version: str = "v3.1",
    required_universe: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    snapshot_id = str(uuid.uuid4())
    univ_syms = sorted(list(required_universe or set(df["symbol"].str.upper())))
    univ_sym_hash = hashlib.sha256(json.dumps(univ_syms, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert file_hash != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert univ_sym_hash != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    return {
        "snapshot_id":                      snapshot_id,
        "snapshot_created_at":              datetime.now().isoformat(),
        "publisher_version":                publisher_version,
        "dataset_sha256":                   file_hash,
        "previous_dataset_sha256":          previous_hash,
        "universe_symbol_hash":             univ_sym_hash,
        "publication_decision":             publication_decision,
        "publication_reason":               publication_reason,
        "FINANCIAL_SNAPSHOT_STATUS":        "SNAPSHOT_READY_FOR_SCANNER" if publication_decision == "PUBLISHED" else "BUILDING",
        "snapshot_status":                  "READY" if publication_decision == "PUBLISHED" else "BUILDING",

        "total_symbols":                    len(df),
        "pit_complete_count":               _value_count(df, "pit_freshness_status", "VALID"),
        "pit_incomplete_count":             _value_count(df, "pit_freshness_status", "DATA_STALE"),

        "current_ev_ebitda_complete":       _notna_count(df, "current_ev_ebitda"),
        "ev_ebitda_3y_median_complete":     _notna_count(df, "ev_ebitda_3y_median"),
        "current_pe_complete":              _notna_count(df, "current_pe"),
        "pe_3y_median_complete":            _notna_count(df, "pe_3y_median"),
        "revenue_complete":                 _notna_count(df, "revenue"),
        "ebitda_complete":                  _notna_count(df, "ebitda"),
        "net_profit_complete":              _notna_count(df, "net_profit"),
        "cfo_complete":                     _notna_count(df, "operating_cash_flow"),
        "debt_complete":                    _notna_count(df, "total_debt"),
        "cash_complete":                    _notna_count(df, "cash_and_equivalents"),
        "shares_complete":                  _notna_count(df, "shares_outstanding_m"),
        "roce_complete":                    _notna_count(df, "roce_5y_avg"),

        "certified_provenance_count":       _value_count(df, "provenance_status", "CERTIFIED"),
        "stale_count":                      _value_count(df, "pit_freshness_status", "DATA_STALE"),
        "gap_count":                        int(df["filing_gap_detected"].sum()) if "filing_gap_detected" in df.columns else 0,
    }


# ---------------------------------------------------------------------------
# Main publisher
# ---------------------------------------------------------------------------

def publish_canonical_pit(
    candidate_path: str,
    reason: str = "UNKNOWN",
    publisher_version: str = "v3.1",
    allow_new_symbols: bool = True,
    is_delta_merge: bool = True,
    required_universe_override: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """
    The SINGLE authorised entry-point for publishing canonical_pit_rebuilt.parquet.

    Steps:
      1. Acquire filesystem lock (blocks concurrent publishers).
      2. Read candidate parquet.
      3. Read existing canonical (if exists) and record its SHA256.
      4. Missing canonical + partial candidate -> STRICTLY BLOCKED.
      5. Existing canonical + partial candidate -> ATOMIC DELTA MERGE (preserve 886).
      6. Run strict exact set equality & Never-Downgrade Gate.
      7. Atomic os.replace over canonical + master_v2.
      8. Write rich snapshot metadata + append to publication history log.
      9. Upload published snapshot to database parquet_cache.
      10. Release lock.

    Returns a dict with publication_decision and full metrics.
    """
    os.makedirs(os.path.dirname(CANONICAL_PATH), exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        # Exclusive filesystem lock (blocks other processes)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # --- Step 2: Load candidate ---
        try:
            df_new = pd.read_parquet(candidate_path)
        except Exception as e:
            return {"publication_decision": "ERROR", "reason": f"Cannot read candidate: {e}"}

        # Load required universe
        required_universe = required_universe_override if required_universe_override is not None else load_required_universe()
        cand_symbols = set(df_new["symbol"].astype(str).str.strip().str.upper()) if "symbol" in df_new.columns else set()
        is_candidate_full = bool(required_universe and cand_symbols == required_universe)

        # --- Step 3: Read existing canonical + capture hash for optimistic check ---
        previous_hash: Optional[str] = None
        df_old: Optional[pd.DataFrame] = None
        if os.path.exists(CANONICAL_PATH):
            try:
                previous_hash = _sha256(CANONICAL_PATH)
                df_old = pd.read_parquet(CANONICAL_PATH)
            except Exception as e:
                logger.warning(f"[PUBLISHER] Could not read existing canonical: {e}")

        # --- Step 4: Governance Gate & Delta Merge Routing ---
        df_target: pd.DataFrame
        gate_passed = True
        gate_reasons: List[str] = []

        if df_old is None or df_old.empty:
            # INVARIANT 1: Missing canonical + partial candidate -> PUBLISH BLOCKED
            if not is_candidate_full:
                fail_reason = (
                    f"CANONICAL_MISSING_PARTIAL_CANDIDATE_BLOCKED: candidate has {len(cand_symbols)} symbols, "
                    f"requires {len(required_universe)}"
                )
                logger.error(f"❌ [CANONICAL_PUBLISHER] {fail_reason}")
                return {"publication_decision": "BLOCKED", "reason": fail_reason}
            df_target = df_new
        else:
            # INVARIANT 2: Existing canonical exists
            if not is_candidate_full:
                if not is_delta_merge:
                    fail_reason = f"PARTIAL_CANDIDATE_TRUNCATION_BLOCKED: candidate has {len(cand_symbols)} < {len(df_old)}"
                    logger.error(f"❌ [CANONICAL_PUBLISHER] {fail_reason}")
                    return {"publication_decision": "BLOCKED", "reason": fail_reason}

                # ATOMIC DELTA MERGE: update existing canonical without dropping any symbols
                df_target = df_old.copy()
                df_target["symbol_norm"] = df_target["symbol"].str.upper()
                df_cand_copy = df_new.copy()
                df_cand_copy["symbol_norm"] = df_cand_copy["symbol"].str.upper()
                cand_indexed = df_cand_copy.set_index("symbol_norm")
                update_cols = [c for c in df_new.columns if c in df_target.columns and c not in ("symbol", "symbol_norm")]

                new_symbol_rows = []
                for sym, row in cand_indexed.iterrows():
                    if sym in df_target["symbol_norm"].values:
                        idx = df_target[df_target["symbol_norm"] == sym].index[0]
                        for col in update_cols:
                            val = row[col]
                            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                                df_target.at[idx, col] = val
                    else:
                        # Append newly added symbol
                        new_row = {c: row[c] for c in df_target.columns if c in row and c != "symbol_norm"}
                        new_row["symbol"] = sym
                        new_row["symbol_norm"] = sym
                        new_symbol_rows.append(new_row)

                if new_symbol_rows:
                    df_target = pd.concat([df_target, pd.DataFrame(new_symbol_rows)], ignore_index=True)

                df_target.drop(columns=["symbol_norm"], inplace=True)
                logger.info(f"⚡ [CANONICAL_PUBLISHER] Delta merged {len(cand_symbols)} candidate symbols into {len(df_target)} canonical symbols")
            else:
                df_target = df_new
                gate_passed, gate_reasons = _run_never_downgrade_gate(df_old, df_target, reason)

        # --- Step 5: Strict Exact Set Equality Validation ---
        if required_universe:
            is_valid, val_reason = validate_canonical_snapshot_exact(df_target, required_universe, len(required_universe))
            if not is_valid:
                gate_passed = False
                gate_reasons.append(f"EXACT_VALIDATION_FAILED: {val_reason}")

        # --- Step 6: Gate failed → write to rejected/ ---
        if not gate_passed:
            rejected_dir = os.path.join(os.path.dirname(CANONICAL_PATH), "canonical_rejected")
            os.makedirs(rejected_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            rejected_path = os.path.join(rejected_dir, f"rejected_{ts}.parquet")
            df_new.to_parquet(rejected_path, index=False)
            failure_summary = {
                "publication_decision": "BLOCKED",
                "gate_reasons":         gate_reasons,
                "rejected_path":        rejected_path,
                "canonical_unchanged":  True,
                "canonical_sha256":     previous_hash,
            }
            logger.error(
                f"❌ [CANONICAL_PUBLISHER] NEVER-DOWNGRADE GATE FAILED ({len(gate_reasons)} violations): "
                + " | ".join(gate_reasons)
            )
            _append_history(failure_summary, reason)
            return failure_summary

        # --- Step 7: Optimistic concurrency check + atomic write ---
        if os.path.exists(CANONICAL_PATH) and previous_hash:
            current_hash_now = _sha256(CANONICAL_PATH)
            if current_hash_now != previous_hash:
                return {
                    "publication_decision": "ABORTED_CONCURRENT_CHANGE",
                    "reason": "Canonical file changed during gate evaluation. Aborting to prevent race condition.",
                }

        tmp = f"{CANONICAL_PATH}.tmp.{os.getpid()}"
        df_target.to_parquet(tmp, index=False)
        os.replace(tmp, CANONICAL_PATH)

        # Also update daily_builder_master_v2
        tmp_v2 = f"{MASTER_V2_PATH}.tmp.{os.getpid()}"
        df_target.to_parquet(tmp_v2, index=False)
        os.replace(tmp_v2, MASTER_V2_PATH)

        # --- Step 8: Rich snapshot metadata ---
        new_hash = _sha256(CANONICAL_PATH)
        meta = _build_snapshot_meta(
            df_target, new_hash, previous_hash,
            publication_decision="PUBLISHED",
            publication_reason=reason,
            publisher_version=publisher_version,
            required_universe=required_universe,
        )
        with open(META_FILE, "w") as mf:
            json.dump(meta, mf, indent=2)

        _append_history(meta, reason)

        # --- Step 9: Database Parquet Cache Upload ---
        if upload_parquet_to_db is not None:
            try:
                upload_parquet_to_db("canonical_pit_rebuilt", CANONICAL_PATH)
                upload_parquet_to_db("daily_builder_master_v2", MASTER_V2_PATH)
                logger.info("💾 [CANONICAL_PUBLISHER] Uploaded canonical_pit_rebuilt and daily_builder_master_v2 to DB parquet_cache")
            except Exception as _db_err:
                logger.warning(f"[CANONICAL_PUBLISHER] DB parquet cache upload warning: {_db_err}")

        logger.info(
            f"✅ [CANONICAL_PUBLISHER] Published {len(df_target)} symbols | "
            f"EV/EBITDA: {meta['current_ev_ebitda_complete']}/{len(df_target)} | "
            f"Certified: {meta['certified_provenance_count']} | "
            f"SHA256: {new_hash[:16]}..."
        )
        return {"publication_decision": "PUBLISHED", **meta}

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _append_history(record: Dict[str, Any], reason: str) -> None:
    try:
        with open(HISTORY_LOG, "a") as hf:
            hf.write(json.dumps({"reason": reason, **record}) + "\n")
    except Exception as e:
        logger.debug(f"[PUBLISHER] Could not append publication history: {e}")


# ---------------------------------------------------------------------------
# Regression Test Helpers (Fix #7 & #8)
# ---------------------------------------------------------------------------

def run_downgrade_regression_test(
    good_canonical_path: str,
    degraded_candidate_path: str,
) -> bool:
    """
    Fix #7: Negative test.
    Proves the gate BLOCKS a degraded candidate and leaves canonical unchanged.
    """
    logger.info("[REGRESSION TEST] Running negative (downgrade) test...")
    canonical_hash_before = _sha256(good_canonical_path) if os.path.exists(good_canonical_path) else None

    result = publish_canonical_pit(degraded_candidate_path, reason="REGRESSION_TEST_NEGATIVE")

    canonical_hash_after = _sha256(good_canonical_path) if os.path.exists(good_canonical_path) else None
    unchanged = canonical_hash_before == canonical_hash_after

    passed = result["publication_decision"] == "BLOCKED" and unchanged
    logger.info(
        f"[REGRESSION TEST] Negative test {'PASSED ✅' if passed else 'FAILED ❌'}: "
        f"decision={result['publication_decision']} | canonical_unchanged={unchanged}"
    )
    return passed


def run_upgrade_regression_test(
    improved_candidate_path: str,
) -> bool:
    """
    Fix #8: Positive test.
    Proves the gate PUBLISHES an improved candidate.
    """
    logger.info("[REGRESSION TEST] Running positive (upgrade) test...")
    result = publish_canonical_pit(improved_candidate_path, reason="REGRESSION_TEST_POSITIVE")
    passed = result["publication_decision"] == "PUBLISHED"
    logger.info(
        f"[REGRESSION TEST] Positive test {'PASSED ✅' if passed else 'FAILED ❌'}: "
        f"decision={result['publication_decision']}"
    )
    return passed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Canonical PIT Publisher")
    parser.add_argument("--candidate", required=True, help="Path to candidate parquet")
    parser.add_argument("--reason", default="MANUAL_CLI", help="Publication reason")
    parser.add_argument("--test-negative", metavar="DEGRADED_CANDIDATE", help="Run negative regression test")
    parser.add_argument("--test-positive", metavar="IMPROVED_CANDIDATE", help="Run positive regression test")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.test_negative:
        run_downgrade_regression_test(CANONICAL_PATH, args.test_negative)
    elif args.test_positive:
        run_upgrade_regression_test(args.test_positive)
    else:
        result = publish_canonical_pit(args.candidate, reason=args.reason)
        print(json.dumps(result, indent=2))
