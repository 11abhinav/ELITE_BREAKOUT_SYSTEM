"""
scripts/test_lifecycle_and_freshness_invariants.py
===================================================
Rigorous Lifecycle, Universe Expansion & Freshness Verification Suite:
  TEST 1: New Symbol Universe Expansion (V1 886 -> V2 887)
  TEST 2: Same-Period Amended Filing PIT Selection (Original May 15 vs Amended Sep 20)
  TEST 3: Filing Detection Delay & Lifecycle Transition Audit
"""
import os, sys, json, hashlib, shutil, tempfile
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.data_providers.fundamental_models import (
    ConsolidationType,
    FundamentalStatus,
    RawFinancialRecord,
    ReconciledCanonicalMetrics,
)
from app.data_providers.fundamental_source_router import FundamentalSourceRouter
from scripts.canonical_pit_publisher import (
    publish_canonical_pit,
    validate_canonical_snapshot_exact,
    CANONICAL_PATH,
    MASTER_V2_PATH,
)
from scripts.financial_filing_watcher import (
    FinancialFilingWatcher,
    SnapshotFreshnessStatus,
    FilingEventType,
)
from app.financial_data_integrity import (
    pre_buy_integrity_gate,
    FieldProvenance,
)

def run_test_1_universe_expansion():
    print("\n" + "=" * 80)
    print("TEST 1: NEW SYMBOL UNIVERSE EXPANSION & ONBOARDING (886 -> 887)")
    print("=" * 80)

    # 1. Baseline Universe V1 (886 symbols)
    univ_path = os.path.join(BASE_DIR, "data", "certified_clean_universe_886.json")
    with open(univ_path) as f:
        v1_data = json.load(f)
    v1_symbols = set(s.upper() for s in v1_data["symbols"])
    assert len(v1_symbols) == 886, f"Expected 886, got {len(v1_symbols)}"
    v1_hash = hashlib.sha256(json.dumps(sorted(list(v1_symbols))).encode()).hexdigest()
    print(f"  [1] Universe V1 Loaded: {len(v1_symbols)} symbols | Hash: {v1_hash[:16]}...")

    # 2. Legitimate Universe Expansion V1 -> V2 (Add approved symbol NEWCO887)
    NEW_SYMBOL = "NEWCO887"
    v2_symbols = set(v1_symbols) | {NEW_SYMBOL}
    assert len(v2_symbols) == 887
    v2_hash = hashlib.sha256(json.dumps(sorted(list(v2_symbols))).encode()).hexdigest()
    assert v1_hash != v2_hash, "Universe hash must change upon expansion"
    print(f"  [2] Universe V2 Approved: {len(v2_symbols)} symbols | New Hash: {v2_hash[:16]}... (Changed)")

    # 3. Simulate Candidate Data Generation for NEWCO887
    # Fetch historical filings & persist raw provenance
    router = FundamentalSourceRouter()
    mock_records = [
        RawFinancialRecord(
            symbol=NEW_SYMBOL,
            source="NSE_XBRL",
            period_end_date=f"202{i}-03-31",
            period_type="ANNUAL",
            consolidation=ConsolidationType.CONSOLIDATED,
            revenue=1000.0 * (1.15 ** i),
            net_profit=120.0 * (1.18 ** i),
            operating_cash_flow=130.0 * (1.18 ** i),
            total_debt=200.0,
            total_equity=800.0 * (1.15 ** i),
            ebit=180.0 * (1.16 ** i),
            capital_employed=1000.0 * (1.15 ** i),
            eps=12.0 * (1.18 ** i),
            broadcast_timestamp=f"202{i}-05-15T18:00:00",
            version="v1",
            validation_status="VALID",
            unit="cr",
            currency="INR"
        )
        for i in range(1, 6)
    ]
    # Persist raw filings with cryptographic provenance
    router._persist_raw_filings(NEW_SYMBOL, mock_records)
    raw_path = os.path.join(BASE_DIR, "data", "pit_raw_filings", f"{NEW_SYMBOL}.json")
    assert os.path.exists(raw_path)
    with open(raw_path) as rf:
        saved_raw = json.load(rf)
    assert len(saved_raw) == 5
    assert all("source_record_hash" in r and len(r["source_record_hash"]) == 64 for r in saved_raw)
    print(f"  [3] Raw Filings Persisted: 5 annual records with SHA-256 hashes to {raw_path}")

    # 4. Compute PIT metrics for NEWCO887
    metrics = router._single_source_metrics(NEW_SYMBOL, mock_records, "NSE_XBRL", as_of_timestamp="2026-10-03T23:59:59")
    assert metrics.overall_status == FundamentalStatus.VERIFIED_SINGLE_SOURCE
    assert metrics.roce_5y is not None and metrics.roce_5y >= 15.0
    assert metrics.sales_cagr_5y is not None and metrics.sales_cagr_5y >= 10.0
    print(f"  [4] PIT Metrics Computed: ROCE={metrics.roce_5y}% | Sales CAGR={metrics.sales_cagr_5y}% | D/E={metrics.debt_to_equity}")

    # 5. Build Candidate Delta & Publish to Canonical under Universe V2 (887)
    df_existing_canon = pd.read_parquet(CANONICAL_PATH)
    assert len(df_existing_canon) == 886

    # Create candidate containing the newly recovered row
    new_row = {
        "symbol": NEW_SYMBOL,
        "roce_5y_avg": metrics.roce_5y,
        "sales_cagr_5y": metrics.sales_cagr_5y,
        "pat_cagr_5y": metrics.pat_cagr_5y,
        "cfo_pat_5y_ratio": metrics.cfo_pat_5y,
        "debt_to_equity": metrics.debt_to_equity,
        "current_ev_ebitda": 15.5,
        "ev_ebitda_3y_median": 22.0,
        "current_pe": 18.0,
        "pe_3y_median": 25.0,
        "revenue": 1749.0,
        "ebitda": 350.0,
        "net_profit": 220.0,
        "operating_cash_flow": 240.0,
        "total_debt": 200.0,
        "cash_and_equivalents": 80.0,
        "shares_outstanding_m": 100.0,
        "annual_filing_count": 5,
        "certified_provenance_count": 5,
        "pit_freshness_status": "FRESH",
        "filing_gap_detected": False,
    }
    # Test: Publishing against OLD 886 universe MUST FAIL exact validation
    with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp_cand:
        tmp_cand_path = tmp_cand.name
    
    # Candidate with 887 symbols
    df_cand_887 = pd.concat([df_existing_canon, pd.DataFrame([new_row])], ignore_index=True)
    df_cand_887.to_parquet(tmp_cand_path, index=False)

    # Old universe validation (886) rejects 887 candidate (EXTRA 1 symbol)
    val_ok_old, val_reason_old = validate_canonical_snapshot_exact(df_cand_887, v1_symbols, len(v1_symbols))
    assert not val_ok_old, "Expected old universe validation to reject 887 candidate!"
    assert "EXTRA 1 unapproved symbols" in val_reason_old
    print(f"  [5] Anti-Drift Check PASS: Candidate rejected under Old V1 886 Universe ({val_reason_old})")

    meta_path = CANONICAL_PATH.replace(".parquet", "_meta.json")
    with open(meta_path, "r") as mf:
        original_meta_content = mf.read()

    # Publishing under NEW Universe V2 (887) SUCCEEDS
    val_ok_new, val_reason_new = validate_canonical_snapshot_exact(df_cand_887, v2_symbols, len(v2_symbols))
    assert val_ok_new, f"Expected validation to pass under V2: {val_reason_new}"

    pub_res = publish_canonical_pit(
        candidate_path=tmp_cand_path,
        reason="TEST_UNIVERSE_EXPANSION_887",
        publisher_version="v3.2_test",
        is_delta_merge=True,
        required_universe_override=v2_symbols,
    )
    assert pub_res.get("publication_decision") == "PUBLISHED"
    
    # Verify new canonical state
    df_updated_canon = pd.read_parquet(CANONICAL_PATH)
    assert len(df_updated_canon) == 887
    assert NEW_SYMBOL in df_updated_canon["symbol"].values
    assert v1_symbols.issubset(set(df_updated_canon["symbol"]))
    print(f"  [6] Publication PASS: Canonical updated to 887 symbols. All 886 preserved + NEWCO887 integrated.")

    # Cleanup: restore canonical back to certified 886 snapshot & metadata
    df_existing_canon.to_parquet(CANONICAL_PATH, index=False)
    df_existing_canon.to_parquet(MASTER_V2_PATH, index=False)
    with open(meta_path, "w") as mf:
        mf.write(original_meta_content)
    if os.path.exists(raw_path):
        os.remove(raw_path)
    if os.path.exists(tmp_cand_path):
        os.remove(tmp_cand_path)
    print("  [7] Test State Cleaned: Restored certified 886 canonical snapshot.")
    print("  ✅ TEST 1 PASSED: Strict Universe Expansion Protocol Verified!")


def run_test_2_amended_filing_pit():
    print("\n" + "=" * 80)
    print("TEST 2: SAME-PERIOD AMENDED FILING POINT-IN-TIME SELECTION")
    print("=" * 80)

    symbol = "TEST_CORP"
    router = FundamentalSourceRouter()

    # Create two versions of FY2026 annual filing:
    # 1. Original filing broadcast on 2026-05-15 (v1)
    original_f26 = RawFinancialRecord(
        symbol=symbol,
        source="NSE_XBRL",
        period_end_date="2026-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=1000.0,
        net_profit=100.0,
        operating_cash_flow=110.0,
        total_debt=200.0,
        total_equity=800.0,
        ebit=150.0,
        capital_employed=1000.0,
        eps=10.0,
        broadcast_timestamp="2026-05-15T14:30:00",
        version="v1",
        validation_status="VALID",
        unit="cr",
        currency="INR"
    )

    # 2. Amended filing broadcast on 2026-09-20 (v2) - restated net profit and revenue
    amended_f26 = RawFinancialRecord(
        symbol=symbol,
        source="NSE_XBRL",
        period_end_date="2026-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=1150.0,      # Restated higher
        net_profit=140.0,    # Restated higher
        operating_cash_flow=130.0,
        total_debt=200.0,
        total_equity=840.0,
        ebit=190.0,          # Restated higher
        capital_employed=1040.0,
        eps=14.0,
        broadcast_timestamp="2026-09-20T17:00:00",
        version="v2",
        validation_status="VALID",
        unit="cr",
        currency="INR"
    )

    records = [original_f26, amended_f26]

    # CASE A: Scanner as_of = 2026-08-01 (BEFORE amendment broadcast on 2026-09-20)
    as_of_aug = "2026-08-01T23:59:59"
    active_aug = router._select_active_pit_records(records, as_of_timestamp=as_of_aug, statement_type="ANNUAL")
    assert len(active_aug) == 1, f"Expected 1 active record, got {len(active_aug)}"
    rec_aug = active_aug[0]
    assert rec_aug.version == "v1", f"Expected v1 for August scan, got {rec_aug.version}"
    assert rec_aug.revenue == 1000.0, f"Expected 1000.0, got {rec_aug.revenue}"
    assert rec_aug.net_profit == 100.0
    print(f"  [Case A: as_of=2026-08-01] Active Record: Version={rec_aug.version} | Rev={rec_aug.revenue} | PAT={rec_aug.net_profit} [PASS: Original Used]")

    # CASE B: Scanner as_of = 2026-10-03 (AFTER amendment broadcast on 2026-09-20)
    as_of_oct = "2026-10-03T23:59:59"
    active_oct = router._select_active_pit_records(records, as_of_timestamp=as_of_oct, statement_type="ANNUAL")
    assert len(active_oct) == 1, f"Expected 1 active record, got {len(active_oct)}"
    rec_oct = active_oct[0]
    assert rec_oct.version == "v2", f"Expected v2 for October scan, got {rec_oct.version}"
    assert rec_oct.revenue == 1150.0, f"Expected 1150.0, got {rec_oct.revenue}"
    assert rec_oct.net_profit == 140.0
    print(f"  [Case B: as_of=2026-10-03] Active Record: Version={rec_oct.version} | Rev={rec_oct.revenue} | PAT={rec_oct.net_profit} [PASS: Amended Used]")

    # CASE C: Future Filing Broadcast Exclusion (Future broadcast 2026-11-01)
    future_f26 = RawFinancialRecord(
        symbol=symbol,
        source="NSE_XBRL",
        period_end_date="2026-03-31",
        period_type="ANNUAL",
        consolidation=ConsolidationType.CONSOLIDATED,
        revenue=9999.0,
        broadcast_timestamp="2026-11-01T10:00:00",
        version="v3",
        validation_status="VALID"
    )
    records_with_future = [original_f26, amended_f26, future_f26]
    active_oct_with_future = router._select_active_pit_records(records_with_future, as_of_timestamp=as_of_oct, statement_type="ANNUAL")
    assert active_oct_with_future[0].version == "v2", "Future filing v3 must be excluded from October scan!"
    print("  [Case C: Future Filing Leakage Prevention] v3 broadcast in November excluded from October scan. [PASS]")
    print("  ✅ TEST 2 PASSED: Point-in-Time Broadcast & Version Selection Fully Verified!")


def run_test_3_filing_detection_delay():
    print("\n" + "=" * 80)
    print("TEST 3: FILING DETECTION DELAY & SYSTEM GUARANTEES AUDIT")
    print("=" * 80)

    # Simulation timeline:
    # 10:30 - New filing published on exchange for SYMBOL_A
    # 10:30-11:00 - Watcher polling window (not yet polled)
    # 10:45 - Scanner executes
    # 11:00 - Watcher discovers filing
    # 11:05 - Next scan executes
    # 11:10 - Canonical rebuild finishes
    # 11:15 - Final scan executes

    watcher = FinancialFilingWatcher()
    TEST_SYM = "WATCHER_TEST"

    # Setup baseline state: FRESH
    watcher.state[TEST_SYM] = {
        "filings": {"F_2025": {"period_end_date": "2025-03-31", "source_hash": "hash_old"}},
        "latest_filing_date": "2025-03-31",
        "snapshot_status": SnapshotFreshnessStatus.FRESH.value
    }
    watcher._save_state()

    # Timeline 1: 10:45 (Before watcher discovers filing)
    st_1045 = watcher.get_symbol_freshness_status(TEST_SYM)
    assert st_1045 == SnapshotFreshnessStatus.FRESH
    # Pre-buy gate passes on certified snapshot
    dummy_prov = {
        "roce_5y": FieldProvenance(symbol=TEST_SYM, scanner="QUALITY_COMPOUNDER", field="roce_5y", value_used=18.0, source_used="PIT", period_end="2025-03-31", basis="CONSOLIDATED", validation_status="PASSED"),
    }
    buy_ok_1045, reasons_1045 = pre_buy_integrity_gate(
        symbol=TEST_SYM,
        scanner="QUALITY_COMPOUNDER",
        required_metrics=["roce_5y"],
        financial_metrics=dummy_prov,
        gate_results={},
        blocking_reasons=[]
    )
    assert buy_ok_1045, f"Expected pass at 10:45 on certified snapshot, got: {reasons_1045}"
    print(f"  [10:45 Pre-Detection Scan] Freshness Status: {st_1045.value} | Buy Gate: ELIGIBLE (Evaluates certified baseline)")

    # Timeline 2: 11:00 (Watcher detects new filing broadcast at 10:30)
    mock_incoming = {
        "filing_id": "F_2026_ANNUAL",
        "period_end_date": "2026-03-31",
        "statement_type": "ANNUAL",
        "basis": "CONSOLIDATED",
        "broadcast_timestamp": "2026-10-03T10:30:00",
    }
    mock_payload = b"{\"facts\": {\"revenue\": 2500.0, \"net_profit\": 300.0}}"
    event = watcher.detect_filing_changes(TEST_SYM, mock_incoming, mock_payload)
    assert event.event_type == FilingEventType.UNSEEN_PERIOD
    
    st_1100 = watcher.get_symbol_freshness_status(TEST_SYM)
    assert st_1100 == SnapshotFreshnessStatus.UPDATE_PENDING
    print(f"  [11:00 Watcher Discovery] Event: {event.event_type.value} | Status Flipped to: {st_1100.value}")

    # Timeline 3: 11:05 (Next scan executes while UPDATE_PENDING)
    buy_ok_1105, reasons_1105 = pre_buy_integrity_gate(
        symbol=TEST_SYM,
        scanner="QUALITY_COMPOUNDER",
        required_metrics=["roce_5y"],
        financial_metrics=dummy_prov,
        gate_results={},
        blocking_reasons=[]
    )
    assert not buy_ok_1105, "Expected pre-buy gate to STRICTLY BLOCK when UPDATE_PENDING!"
    assert any("UPDATE_PENDING" in r for r in reasons_1105)
    print(f"  [11:05 In-Flight Scan] Freshness Status: {st_1100.value} | Buy Gate: BLOCKED ({reasons_1105[0]}) [PASS]")

    # Timeline 4: 11:10 (Rebuild completes and marks FRESH)
    watcher.state[TEST_SYM]["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
    watcher._save_state()
    st_1115 = watcher.get_symbol_freshness_status(TEST_SYM)
    assert st_1115 == SnapshotFreshnessStatus.FRESH
    
    # Pre-buy gate passes on refreshed snapshot
    dummy_prov_new = {
        "roce_5y": FieldProvenance(symbol=TEST_SYM, scanner="QUALITY_COMPOUNDER", field="roce_5y", value_used=21.0, source_used="PIT", period_end="2026-03-31", basis="CONSOLIDATED", validation_status="PASSED"),
    }
    buy_ok_1115, reasons_1115 = pre_buy_integrity_gate(
        symbol=TEST_SYM,
        scanner="QUALITY_COMPOUNDER",
        required_metrics=["roce_5y"],
        financial_metrics=dummy_prov_new,
        gate_results={},
        blocking_reasons=[]
    )
    assert buy_ok_1115, f"Expected pass after refresh, got: {reasons_1115}"
    print(f"  [11:15 Post-Rebuild Scan] Freshness Status: {st_1115.value} | Buy Gate: ELIGIBLE with updated FY2026 metrics")

    # Cleanup test state
    if TEST_SYM in watcher.state:
        del watcher.state[TEST_SYM]
        watcher._save_state()
    print("  [Cleanup] Removed test symbol state.")
    print("  ✅ TEST 3 PASSED: Detection Delay Lifecycle & Invalidation Guarantees Verified!")


def run_test_4_pre_buy_source_freshness_fence():
    print("\n" + "=" * 80)
    print("TEST 4: PRE-BUY SOURCE FRESHNESS FENCE (NO_NEWER_UNPROCESSED_FILING)")
    print("=" * 80)

    TEST_SYM = "FENCE_TEST_STOCK"
    watcher = FinancialFilingWatcher()

    # 1. State in Watcher says FRESH based on FY2025
    watcher.state[TEST_SYM] = {
        "filings": {"F_2025": {"period_end_date": "2025-03-31", "source_hash": "h25"}},
        "latest_filing_date": "2025-03-31",
        "snapshot_status": SnapshotFreshnessStatus.FRESH.value
    }
    watcher._save_state()

    # 2. Company publishes FY2026 result on exchange at 10:30
    # Simulate exchange filing index having this newer filing before watcher runs
    sym_dir = os.path.join(BASE_DIR, "data", "exchange_financials", TEST_SYM, "metadata")
    os.makedirs(sym_dir, exist_ok=True)
    idx_file = os.path.join(sym_dir, "filing_index.json")
    with open(idx_file, "w") as ef:
        json.dump({
            "F_2026_ANNUAL": {
                "statement_type": "ANNUAL",
                "period_end_date": "2026-03-31",
                "broadcast_timestamp": "2026-10-03T10:30:00",
                "pit_eligible_from": "2026-10-03T10:30:00"
            }
        }, ef, indent=2)

    # 3. Scanner evaluates BUY at 10:45 using old FY2025 snapshot numbers
    dummy_prov_old = {
        "roce_5y": FieldProvenance(symbol=TEST_SYM, scanner="QUALITY_COMPOUNDER", field="roce_5y", value_used=18.0, source_used="PIT", period_end="2025-03-31", basis="CONSOLIDATED", validation_status="PASSED"),
    }
    buy_ok_1045, reasons_1045 = pre_buy_integrity_gate(
        symbol=TEST_SYM,
        scanner="QUALITY_COMPOUNDER",
        required_metrics=["roce_5y"],
        financial_metrics=dummy_prov_old,
        gate_results={},
        blocking_reasons=[]
    )
    # The fence MUST detect the exchange filing and HARD BLOCK the BUY alert!
    assert not buy_ok_1045, "Pre-BUY fence failed to block candidate with newer exchange filing!"
    assert any("UNPROCESSED_EXCHANGE_FILING" in r for r in reasons_1045)
    print(f"  [10:45 Pre-BUY Fence Interception] BUY Gate: BLOCKED ({reasons_1045[0]})")

    # Verify watcher state was automatically flipped to UPDATE_PENDING
    st_flipped = watcher.get_symbol_freshness_status(TEST_SYM)
    assert st_flipped == SnapshotFreshnessStatus.UPDATE_PENDING
    print(f"  [Automatic State Invalidation] Watcher status flipped to: {st_flipped.value}")

    # 4. Once canonical snapshot incorporates FY2026:
    dummy_prov_updated = {
        "roce_5y": FieldProvenance(symbol=TEST_SYM, scanner="QUALITY_COMPOUNDER", field="roce_5y", value_used=22.0, source_used="PIT", period_end="2026-03-31", basis="CONSOLIDATED", validation_status="PASSED"),
    }
    # Reset status to FRESH after rebuild
    watcher.state[TEST_SYM]["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
    watcher._save_state()

    buy_ok_updated, reasons_updated = pre_buy_integrity_gate(
        symbol=TEST_SYM,
        scanner="QUALITY_COMPOUNDER",
        required_metrics=["roce_5y"],
        financial_metrics=dummy_prov_updated,
        gate_results={},
        blocking_reasons=[]
    )
    assert buy_ok_updated, f"Expected pass after canonical updated to FY2026, got: {reasons_updated}"
    print(f"  [Post-Incorporation Gate] BUY Gate: ELIGIBLE with FY2026 canonical data [PASS]")

    # Cleanup test exchange metadata and watcher state
    shutil.rmtree(os.path.join(BASE_DIR, "data", "exchange_financials", TEST_SYM), ignore_errors=True)
    if TEST_SYM in watcher.state:
        del watcher.state[TEST_SYM]
        watcher._save_state()
    print("  ✅ TEST 4 PASSED: Pre-BUY External Source Freshness Fence Verified!")


def run_test_5_real_world_new_stock_recovery():
    print("\n" + "=" * 80)
    print("TEST 5: REAL-WORLD RECENTLY LISTED NSE STOCK LIVE RECOVERY PROOF")
    print("=" * 80)

    # Real-world test on MEDIASSIST (IPO listed Jan 2024, recent listing)
    REAL_SYMBOL = "MEDIASSIST"
    router = FundamentalSourceRouter()

    # Step 1: Real ISIN Resolution
    real_isin = router._resolve_isin(REAL_SYMBOL)
    assert real_isin == "INE456Z01021", f"Expected INE456Z01021, got {real_isin}"
    print(f"  [1] Live Exchange ISIN Resolution: {REAL_SYMBOL} -> {real_isin} [PASS]")

    # Step 2: Live Progressive Recovery & Field Completeness
    metrics = router.execute_progressive_recovery(REAL_SYMBOL, as_of_timestamp="2026-10-03T23:59:59")
    assert metrics.overall_status == FundamentalStatus.VERIFIED
    assert metrics.roce_5y is not None and metrics.roce_5y > 15.0
    assert metrics.sales_cagr_5y is not None and metrics.sales_cagr_5y > 10.0
    assert metrics.cfo_pat_5y is not None and metrics.cfo_pat_5y > 0.0

    print(f"  [2] Live Progressive Recovery: Status={metrics.overall_status.name}")
    print(f"      - 5Y ROCE Average  : {metrics.roce_5y}% (Passes >= 15% gate)")
    print(f"      - 5Y Sales CAGR    : {metrics.sales_cagr_5y}% (Passes >= 10% gate)")
    print(f"      - 5Y CFO/PAT Ratio : {metrics.cfo_pat_5y} (Passes > 0 gate)")
    print("  ✅ TEST 5 PASSED: Real-World Recently Listed Stock Recovery Empirically Verified!")


if __name__ == "__main__":
    run_test_1_universe_expansion()
    run_test_2_amended_filing_pit()
    run_test_3_filing_detection_delay()
    run_test_4_pre_buy_source_freshness_fence()
    run_test_5_real_world_new_stock_recovery()
    print("\n" + "=" * 80)
    print("ALL 5 MANDATORY LIFECYCLE & FRESHNESS TESTS PASSED PERFECTLY!")
    print("=" * 80)
