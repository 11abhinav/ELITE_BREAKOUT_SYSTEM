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
    assert any("UNPROCESSED_MULTI_SOURCE_FILING" in r or "UNPROCESSED_EXCHANGE_FILING" in r for r in reasons_1045)
    print(f"  [10:45 Pre-BUY Fence Interception] BUY Gate: BLOCKED ({reasons_1045[0]})")

    # Verify watcher state was automatically flipped to UPDATE_PENDING
    st_flipped = watcher.get_symbol_freshness_status(TEST_SYM)
    assert st_flipped == SnapshotFreshnessStatus.UPDATE_PENDING
    print(f"  [Automatic State Invalidation] Watcher status flipped to: {st_flipped.value}")

    # 4. Once canonical snapshot incorporates FY2026:
    dummy_prov_updated = {
        "roce_5y": FieldProvenance(symbol=TEST_SYM, scanner="QUALITY_COMPOUNDER", field="roce_5y", value_used=22.0, source_used="PIT", period_end="2026-03-31", basis="CONSOLIDATED", validation_status="PASSED", pit_eligible_from="2026-10-03T10:30:00"),
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


def run_test_6_freshness_to_buy_commit_race():
    print("\n" + "=" * 80)
    print("TEST 6: FRESHNESS CHECK TO BUY COMMIT RACE, SOURCE SLA & MULTI-SOURCE GATES")
    print("=" * 80)

    from app.financial_data_integrity import (
        build_buy_evidence_bundle,
        commit_buy_alert_atomic,
        record_source_watermark,
        get_multi_source_exchange_watermark,
        check_pre_buy_source_freshness_fence,
    )

    RACE_SYM = "RACE_CO_887"
    watcher = FinancialFilingWatcher()

    # -------------------------------------------------------------------------
    # PART A: RACE CONDITION INJECTION BETWEEN FENCE CHECK AND BUY COMMIT
    # -------------------------------------------------------------------------
    print("  [Part A: Concurrency Race Condition Rejection]")
    # T0: Canonical snapshot is fresh, watcher is FRESH, feed watermarks are valid
    record_source_watermark("NSE", last_successful_check_at="2026-10-03T22:30:00+05:30")
    record_source_watermark("BSE", last_successful_check_at="2026-10-03T22:30:00+05:30")
    watcher.state[RACE_SYM] = {
        "filings": {"F_2025": {"period_end_date": "2025-03-31", "source_hash": "hash2025"}},
        "latest_filing_date": "2025-03-31",
        "snapshot_status": SnapshotFreshnessStatus.FRESH.value,
    }
    watcher._save_state()

    # T1: Pre-BUY freshness fence evaluated and passes
    prov_metrics = {
        "roce_5y": FieldProvenance(symbol=RACE_SYM, scanner="QUALITY_COMPOUNDER", field="roce_5y", value_used=19.5, source_used="PIT", period_end="2025-03-31", basis="CONSOLIDATED", validation_status="PASSED", pit_eligible_from="2025-05-15T18:00:00"),
    }
    bundle = build_buy_evidence_bundle(
        scan_run_id="scan_run_race_001",
        scanner="QUALITY_COMPOUNDER",
        symbol=RACE_SYM,
        cmp=450.0,
        strategy_score=88.5,
        gate_results={"trend": "BULL", "pivot": "PASSED"},
        financial_metrics=prov_metrics,
        required_metrics=["roce_5y"],
        pit_timestamp="2025-05-15T18:00:00",
        pit_eligible_from="2025-05-15T18:00:00",
    )
    assert bundle.is_buy_eligible(), f"Expected pre-buy gate to PASS at T1, got: {bundle.blocking_reasons}"
    assert bundle.snapshot_sha256 is not None, "Bundle must bind to canonical snapshot SHA-256"
    assert bundle.snapshot_version is not None, "Bundle must bind to snapshot version"
    assert "sources" in bundle.source_watermarks, "Bundle must record multi-source watermarks"
    print(f"    T0-T1: Pre-BUY Gate PASSED (Snapshot SHA={bundle.snapshot_sha256[:16]}, Version={bundle.snapshot_version})")

    # T2: Concurrent event! Exchange publishes FY2026 filing at 10:45
    # T3: Symbol becomes UPDATE_PENDING in watcher state before BUY commit completes
    watcher.state[RACE_SYM]["snapshot_status"] = SnapshotFreshnessStatus.UPDATE_PENDING.value
    watcher._save_state()
    print(f"    T2-T3: Concurrent filing published on exchange -> Watcher transitioned {RACE_SYM} to UPDATE_PENDING")

    # T4: Attempt BUY persistence via commit_buy_alert_atomic
    sink = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        alerts_pq = os.path.join(tmp_dir, "10_alerts.parquet")
        commit_ok, commit_msg = commit_buy_alert_atomic(bundle, alert_sink=sink, alerts_parquet_path=alerts_pq)
        
        # BUY MUST BE REJECTED
        assert not commit_ok, "CRITICAL DEFECT: BUY alert was committed despite concurrent filing!"
        assert len(sink) == 0, "Alert sink must be empty upon rejection!"
        assert not os.path.exists(alerts_pq), "Alert parquet must not be created upon rejection!"
        assert "CONCURRENT_FILING_DETECTED" in commit_msg, f"Expected CONCURRENT_FILING_DETECTED, got: {commit_msg}"
        print(f"    T4: Atomic Persistence Gate -> REJECTED ({commit_msg}) [PASS]")
        print("    ✅ Part A Verified: Zero race window. BUY rejected when filing detected during execution.")

    # -------------------------------------------------------------------------
    # PART B: SNAPSHOT DRIFT REJECTION (OPTIMISTIC LOCKING)
    # -------------------------------------------------------------------------
    print("  [Part B: Snapshot Drift Rejection (Optimistic Lock)]")
    watcher.state[RACE_SYM]["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
    watcher._save_state()

    stale_bundle = build_buy_evidence_bundle(
        scan_run_id="scan_run_drift_002",
        scanner="QUALITY_COMPOUNDER",
        symbol=RACE_SYM,
        cmp=450.0,
        strategy_score=88.5,
        gate_results={"trend": "BULL"},
        financial_metrics=prov_metrics,
        required_metrics=["roce_5y"],
        pit_timestamp="2025-05-15T18:00:00",
        pit_eligible_from="2025-05-15T18:00:00",
        snapshot_sha256="0000000000000000deadbeef0000000000000000deadbeef0000000000000000",
    )
    sink_drift = []
    commit_drift_ok, commit_drift_msg = commit_buy_alert_atomic(stale_bundle, alert_sink=sink_drift)
    assert not commit_drift_ok, "Expected rejection when bundle snapshot SHA does not match current canonical snapshot!"
    assert "SNAPSHOT_VERSION_DRIFT" in commit_drift_msg
    assert len(sink_drift) == 0
    print(f"    Snapshot Drift Gate -> REJECTED ({commit_drift_msg}) [PASS]")
    print("    ✅ Part B Verified: Transaction tied to exact snapshot SHA256; drift triggers immediate rollback.")

    # -------------------------------------------------------------------------
    # PART C: SOURCE WATERMARK FRESHNESS SLA BREACH (FAIL CLOSED)
    # -------------------------------------------------------------------------
    print("  [Part C: Source Watermark SLA Enforcement]")
    old_time = (datetime.now() - pd.Timedelta(hours=30)).isoformat()
    record_source_watermark("NSE", last_successful_check_at=old_time)
    
    fence_ok, fence_reason = check_pre_buy_source_freshness_fence(
        symbol=RACE_SYM,
        canonical_period_end="2025-03-31",
        canonical_filing_timestamp="2025-05-15T18:00:00",
        max_sla_seconds=86400,
    )
    assert not fence_ok, "Source freshness fence failed to block when feed SLA is breached!"
    assert "EXCHANGE_FEED_WATERMARK_STALE" in fence_reason
    assert "exceeds SLA" in fence_reason
    print(f"    Feed SLA Gate -> BLOCKED ({fence_reason}) [PASS]")
    print("    ✅ Part C Verified: 'Latest known filing' is rejected if exchange feed poll age > SLA window.")

    # Restore NSE feed watermark to fresh
    record_source_watermark("NSE", last_successful_check_at=datetime.now().isoformat())

    # -------------------------------------------------------------------------
    # PART D: MULTI-SOURCE WATERMARK (max(NSE, BSE) COVERAGE)
    # -------------------------------------------------------------------------
    print("  [Part D: Multi-Source Combined Watermark (max(NSE, BSE))]")
    record_source_watermark("NSE", last_successful_check_at=datetime.now().isoformat(), latest_filing_timestamp="2025-05-15T18:00:00", symbol=RACE_SYM)
    record_source_watermark("BSE", last_successful_check_at=datetime.now().isoformat(), latest_filing_timestamp="2026-10-03T11:30:00", symbol=RACE_SYM)

    multi_wm = get_multi_source_exchange_watermark(RACE_SYM)
    assert multi_wm["latest_exchange_filing_timestamp"] == "2026-10-03T11:30:00", f"Expected max(NSE, BSE) to select BSE timestamp, got: {multi_wm}"
    
    fence_multi_ok, fence_multi_reason = check_pre_buy_source_freshness_fence(
        symbol=RACE_SYM,
        canonical_period_end="2025-03-31",
        canonical_filing_timestamp="2025-05-15T18:00:00",
    )
    assert not fence_multi_ok, "Freshness fence must block when BSE has newer filing even if NSE is clean!"
    assert "UNPROCESSED_MULTI_SOURCE_FILING" in fence_multi_reason
    print(f"    Multi-Source Watermark Gate -> BLOCKED ({fence_multi_reason}) [PASS]")
    print("    ✅ Part D Verified: max(NSE, BSE) protects against exchange-specific disclosure delays.")

    # -------------------------------------------------------------------------
    # PART E: CLEAN ATOMIC COMMIT WITH FULL DATA INTEGRITY
    # -------------------------------------------------------------------------
    print("  [Part E: Clean Atomic Commit Verified]")
    record_source_watermark("BSE", last_successful_check_at=datetime.now().isoformat(), latest_filing_timestamp="2025-05-15T18:00:00", symbol=RACE_SYM)
    watcher.state[RACE_SYM]["snapshot_status"] = SnapshotFreshnessStatus.FRESH.value
    watcher._save_state()

    clean_bundle = build_buy_evidence_bundle(
        scan_run_id="scan_run_clean_003",
        scanner="QUALITY_COMPOUNDER",
        symbol=RACE_SYM,
        cmp=450.0,
        strategy_score=92.0,
        gate_results={"trend": "BULL"},
        financial_metrics=prov_metrics,
        required_metrics=["roce_5y"],
        pit_timestamp="2025-05-15T18:00:00",
        pit_eligible_from="2025-05-15T18:00:00",
    )
    assert clean_bundle.is_buy_eligible()
    clean_sink = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        alerts_pq = os.path.join(tmp_dir, "10_alerts.parquet")
        commit_clean_ok, commit_clean_msg = commit_buy_alert_atomic(clean_bundle, alert_sink=clean_sink, alerts_parquet_path=alerts_pq)
        assert commit_clean_ok, f"Expected successful commit, got: {commit_clean_msg}"
        assert len(clean_sink) == 1
        assert clean_sink[0]["status"] == "COMMITTED"
        assert clean_sink[0]["snapshot_sha256"] == clean_bundle.snapshot_sha256
        assert os.path.exists(alerts_pq)
        df_saved = pd.read_parquet(alerts_pq)
        assert len(df_saved) == 1
        assert df_saved.iloc[0]["symbol"] == RACE_SYM
        print(f"    Atomic Commit -> SUCCESS: Persisted alert to sink and parquet with verified snapshot {clean_bundle.snapshot_sha256[:16]} [PASS]")
        print("    ✅ Part E Verified: Clean atomic BUY commit verified end-to-end.")

    # Cleanup
    if RACE_SYM in watcher.state:
        del watcher.state[RACE_SYM]
        watcher._save_state()
    # Restore fresh feed timestamps
    record_source_watermark("NSE", last_successful_check_at=datetime.now().isoformat())
    record_source_watermark("BSE", last_successful_check_at=datetime.now().isoformat())
    print("  ✅ TEST 6 PASSED: Concurrency Race Condition, Feed SLA, and Multi-Source Watermarks 100% Certified!")


def run_test_7_crash_consistency_outbox_reconciliation():
    print("\n" + "=" * 80)
    print("TEST 7: CRASH CONSISTENCY & AUTHORITATIVE OUTBOX RECONCILIATION AUDIT")
    print("=" * 80)
    import sqlite3
    from app.financial_data_integrity import (
        commit_buy_alert_atomic,
        reconcile_alerts_outbox_materialization,
        init_buy_alerts_journal,
        build_buy_evidence_bundle,
        record_source_watermark,
        check_pre_buy_source_freshness_fence,
    )

    CRASH_SYM_1 = "CRASHTEST1"
    CRASH_SYM_2 = "CRASHTEST2"
    ROGUE_SYM = "ROGUEBUY99"

    # Set up fresh watermarks for test symbols
    record_source_watermark("NSE", last_successful_check_at=datetime.now().isoformat(), latest_filing_timestamp="2025-05-15T18:00:00", symbol=CRASH_SYM_1)
    record_source_watermark("BSE", last_successful_check_at=datetime.now().isoformat(), latest_filing_timestamp="2025-05-15T18:00:00", symbol=CRASH_SYM_1)
    record_source_watermark("NSE", last_successful_check_at=datetime.now().isoformat(), latest_filing_timestamp="2025-05-15T18:00:00", symbol=CRASH_SYM_2)
    record_source_watermark("BSE", last_successful_check_at=datetime.now().isoformat(), latest_filing_timestamp="2025-05-15T18:00:00", symbol=CRASH_SYM_2)

    watcher = FinancialFilingWatcher()
    watcher.state[CRASH_SYM_1] = {"snapshot_status": SnapshotFreshnessStatus.FRESH.value, "latest_filing_date": "2025-03-31"}
    watcher.state[CRASH_SYM_2] = {"snapshot_status": SnapshotFreshnessStatus.FRESH.value, "latest_filing_date": "2025-03-31"}
    watcher._save_state()

    prov_metric_1 = {
        "roce_5y": FieldProvenance(
            symbol=CRASH_SYM_1,
            scanner="QUALITY_COMPOUNDER",
            field="roce_5y",
            value_used=24.5,
            source_used="UPSTOX",
            period_end="2025-03-31",
            basis="CONSOLIDATED",
            validation_status="PASSED",
            pit_eligible_from="2025-05-15T18:00:00",
        )
    }

    prov_metric_2 = {
        "roce_5y": FieldProvenance(
            symbol=CRASH_SYM_2,
            scanner="QUALITY_COMPOUNDER",
            field="roce_5y",
            value_used=26.0,
            source_used="UPSTOX",
            period_end="2025-03-31",
            basis="CONSOLIDATED",
            validation_status="PASSED",
            pit_eligible_from="2025-05-15T18:00:00",
        )
    }

    bundle1 = build_buy_evidence_bundle(
        scan_run_id="run_crash_001",
        scanner="QUALITY_COMPOUNDER",
        symbol=CRASH_SYM_1,
        cmp=500.0,
        strategy_score=95.0,
        gate_results={"trend": "BULL"},
        financial_metrics=prov_metric_1,
        required_metrics=["roce_5y"],
        pit_timestamp="2025-05-15T18:00:00",
        pit_eligible_from="2025-05-15T18:00:00",
    )
    assert bundle1.is_buy_eligible()

    bundle2 = build_buy_evidence_bundle(
        scan_run_id="run_crash_002",
        scanner="QUALITY_COMPOUNDER",
        symbol=CRASH_SYM_2,
        cmp=750.0,
        strategy_score=91.0,
        gate_results={"trend": "BULL"},
        financial_metrics=prov_metric_2,
        required_metrics=["roce_5y"],
        pit_timestamp="2025-05-15T18:00:00",
        pit_eligible_from="2025-05-15T18:00:00",
    )
    assert bundle2.is_buy_eligible()

    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = os.path.join(tmp_dir, "test_buy_alerts_journal.db")
        test_pq = os.path.join(tmp_dir, "test_10_alerts.parquet")
        init_buy_alerts_journal(test_db)

        # ---------------------------------------------------------------------
        # PART A: FAILURE STAGE 1 (CRASH BEFORE DB COMMIT)
        # ---------------------------------------------------------------------
        print("  [Part A: Simulated Crash BEFORE DB Commit]")
        ok_a, msg_a = commit_buy_alert_atomic(
            bundle1,
            alerts_parquet_path=test_pq,
            alerts_db_path=test_db,
            simulate_failure_stage="BEFORE_DB_COMMIT",
        )
        assert not ok_a, "Expected commit to fail during simulated crash before DB commit!"
        assert msg_a == "CRASH_BEFORE_DB_COMMIT"

        # Verify DB is completely untouched
        with sqlite3.connect(test_db) as conn:
            cnt_db = conn.execute("SELECT count(*) FROM buy_alerts_journal").fetchone()[0]
        assert cnt_db == 0, f"Expected 0 DB rows after crash before DB commit, got {cnt_db}"
        assert not os.path.exists(test_pq), "Parquet must not exist after crash before DB commit!"

        # Run reconciliation on empty state
        rec_a = reconcile_alerts_outbox_materialization(test_pq, test_db)
        assert rec_a["db_committed_total"] == 0
        assert rec_a["materialized_repaired"] == 0
        print("    Crash Before DB Commit -> Rollback verified (0 in DB, 0 in Parquet) [PASS]")
        print("    ✅ Part A Verified: Zero persistence corruption when crash occurs before DB commit.")

        # ---------------------------------------------------------------------
        # PART B: FAILURE STAGE 2 (AFTER DB COMMIT, BEFORE PARQUET MATERIALIZATION)
        # ---------------------------------------------------------------------
        print("  [Part B: Simulated Crash AFTER DB Commit, BEFORE Parquet Write]")
        ok_b, msg_b = commit_buy_alert_atomic(
            bundle1,
            alerts_parquet_path=test_pq,
            alerts_db_path=test_db,
            simulate_failure_stage="AFTER_DB_COMMIT_BEFORE_PARQUET",
        )
        assert not ok_b
        assert msg_b == "CRASH_AFTER_DB_COMMIT_BEFORE_PARQUET"

        # Verify DB has committed record with materialized_to_parquet == 0
        with sqlite3.connect(test_db) as conn:
            conn.row_factory = sqlite3.Row
            row_b = conn.execute("SELECT * FROM buy_alerts_journal WHERE symbol = ?", (CRASH_SYM_1,)).fetchone()
        assert row_b is not None, "DB outbox must contain committed alert!"
        assert row_b["status"] == "COMMITTED"
        assert row_b["materialized_to_parquet"] == 0, "DB marker must indicate pending parquet materialization!"
        assert not os.path.exists(test_pq), "Parquet must not yet exist!"
        print(f"    DB Outbox State: Alert {row_b['alert_id']} committed, materialized_to_parquet=0")

        # Now trigger crash reconciliation
        rec_b = reconcile_alerts_outbox_materialization(test_pq, test_db)
        print(f"    Reconciliation Result: {rec_b}")
        assert rec_b["materialized_repaired"] == 1
        assert rec_b["parquet_count_after"] == 1
        assert os.path.exists(test_pq), "Parquet must now be materialized by reconciler!"

        df_pq_b = pd.read_parquet(test_pq)
        assert len(df_pq_b) == 1
        assert df_pq_b.iloc[0]["symbol"] == CRASH_SYM_1

        # Check that DB marker was updated to 1
        with sqlite3.connect(test_db) as conn:
            m_flag = conn.execute("SELECT materialized_to_parquet FROM buy_alerts_journal WHERE symbol = ?", (CRASH_SYM_1,)).fetchone()[0]
        assert m_flag == 1, "DB outbox marker must be set to 1 after reconciliation!"
        print("    Crash After DB Commit -> Reconciled cleanly (DB marker=1, Parquet row count=1) [PASS]")
        print("    ✅ Part B Verified: 'DB BUY present + Parquet BUY absent' window is automatically repaired.")

        # ---------------------------------------------------------------------
        # PART C: FAILURE STAGE 3 (AFTER PARQUET WRITE, BEFORE DB MARKER UPDATE)
        # ---------------------------------------------------------------------
        print("  [Part C: Simulated Crash AFTER Parquet Write, BEFORE DB Marker Update]")
        ok_c, msg_c = commit_buy_alert_atomic(
            bundle2,
            alerts_parquet_path=test_pq,
            alerts_db_path=test_db,
            simulate_failure_stage="AFTER_PARQUET_BEFORE_MARKER",
        )
        assert not ok_c
        assert msg_c == "CRASH_AFTER_PARQUET_BEFORE_MARKER"

        # Verify DB has bundle2 with materialized_to_parquet == 0
        with sqlite3.connect(test_db) as conn:
            row_c = conn.execute("SELECT materialized_to_parquet FROM buy_alerts_journal WHERE symbol = ?", (CRASH_SYM_2,)).fetchone()
        assert row_c[0] == 0, "DB marker should be 0 because crash happened before marker update!"

        # But Parquet ALREADY has bundle2 written!
        df_pq_c1 = pd.read_parquet(test_pq)
        assert len(df_pq_c1) == 2, f"Parquet must already contain 2 alerts, got {len(df_pq_c1)}"
        assert set(df_pq_c1["symbol"]) == {CRASH_SYM_1, CRASH_SYM_2}

        # Run reconciliation: must be IDEMPOTENT (no duplicate bundle2 added)
        rec_c = reconcile_alerts_outbox_materialization(test_pq, test_db)
        print(f"    Reconciliation Result: {rec_c}")
        assert rec_c["parquet_count_after"] == 2
        df_pq_c2 = pd.read_parquet(test_pq)
        assert len(df_pq_c2) == 2, "Reconciliation must NOT create duplicate rows in Parquet!"

        with sqlite3.connect(test_db) as conn:
            m_flag_2 = conn.execute("SELECT materialized_to_parquet FROM buy_alerts_journal WHERE symbol = ?", (CRASH_SYM_2,)).fetchone()[0]
        assert m_flag_2 == 1, "DB marker must now be 1!"
        print("    Crash After Parquet Write -> Idempotent reconciliation (no duplicates, DB marker=1) [PASS]")
        print("    ✅ Part C Verified: Parquet deduplication prevents duplicate alerts on crash retry.")

        # ---------------------------------------------------------------------
        # PART D: FAILURE STAGE 4 (CONTAINER RESTART / PARQUET STORAGE DESTRUCTION)
        # ---------------------------------------------------------------------
        print("  [Part D: Container Restart & Parquet Storage Destruction]")
        # Simulate local ephemeral disk wipe during container restart
        os.remove(test_pq)
        assert not os.path.exists(test_pq), "Simulated container restart wiped Parquet file!"

        # Trigger recovery upon container boot
        rec_d = reconcile_alerts_outbox_materialization(test_pq, test_db)
        print(f"    Reconstruction Result: {rec_d}")
        assert rec_d["reconciliation_status"] == "RECONSTRUCTED_FROM_DB"
        assert rec_d["parquet_count_after"] == 2
        assert os.path.exists(test_pq)

        df_pq_d = pd.read_parquet(test_pq)
        assert len(df_pq_d) == 2
        assert set(df_pq_d["symbol"]) == {CRASH_SYM_1, CRASH_SYM_2}
        print("    Container Restart -> Complete Parquet reconstruction from Authoritative DB Journal [PASS]")
        print("    ✅ Part D Verified: Parquet alerts file is fully reconstructed from authoritative DB outbox.")

        # ---------------------------------------------------------------------
        # PART E: ORPHANED PARQUET RECORD PRUNING (NEVER PARQUET BUY WITHOUT DB)
        # ---------------------------------------------------------------------
        print("  [Part E: Rogue/Orphaned Parquet Record Pruning]")
        # Inject an unauthorized/uncommitted alert directly into Parquet
        rogue_record = {
            "alert_id": f"QUALITY_COMPOUNDER_{ROGUE_SYM}_fake_run_deadbeef12345678",
            "symbol": ROGUE_SYM,
            "scanner": "QUALITY_COMPOUNDER",
            "run_id": "fake_run",
            "alert_timestamp": datetime.now().isoformat(),
            "cmp": 100.0,
            "strategy_score": 99.0,
            "snapshot_version": "V1",
            "snapshot_sha256": "fake",
            "evidence_hash": "deadbeef12345678",
            "status": "COMMITTED",
        }
        df_corrupt = pd.concat([df_pq_d, pd.DataFrame([rogue_record])], ignore_index=True)
        df_corrupt.to_parquet(test_pq, index=False)
        assert len(pd.read_parquet(test_pq)) == 3

        # Run reconciliation: DB does NOT have ROGUE_SYM -> Reconciler must PRUNE it!
        rec_e = reconcile_alerts_outbox_materialization(test_pq, test_db)
        print(f"    Orphan Pruning Result: {rec_e}")
        assert rec_e["orphans_pruned"] == 1
        assert rec_e["parquet_count_after"] == 2

        df_pruned = pd.read_parquet(test_pq)
        assert len(df_pruned) == 2
        assert ROGUE_SYM not in df_pruned["symbol"].values
        print("    Orphaned Row Pruning -> Rogue Parquet row eliminated to preserve DB authority [PASS]")
        print("    ✅ Part E Verified: Parquet BUY present + DB BUY absent indefinitely is IMPOSSIBLE.")

        # ---------------------------------------------------------------------
        # PART F: ARCHITECTURAL DISTINCTION (FEED_HEARTBEAT_SLA vs. SOURCE_FRESHNESS_FENCE)
        # ---------------------------------------------------------------------
        print("  [Part F: Feed Heartbeat SLA vs. Filing Freshness Fence Distinction]")
        fresh_time = datetime.now().isoformat()
        record_source_watermark("NSE", last_successful_check_at=fresh_time, symbol=CRASH_SYM_1)
        record_source_watermark("BSE", last_successful_check_at=fresh_time, symbol=CRASH_SYM_1)
        
        # Scenario: Feed SLA passes (last check 1 min ago), but BSE has newer intraday filing!
        record_source_watermark("BSE", last_successful_check_at=fresh_time, latest_filing_timestamp="2026-10-03T16:00:00", symbol=CRASH_SYM_1)
        
        fence_ok, fence_reason = check_pre_buy_source_freshness_fence(
            symbol=CRASH_SYM_1,
            canonical_period_end="2025-03-31",
            canonical_filing_timestamp="2025-05-15T18:00:00",
        )
        assert not fence_ok, "Filing freshness fence must BLOCK even when feed SLA is 100% fresh!"
        assert "UNPROCESSED_MULTI_SOURCE_FILING" in fence_reason
        assert "Exchange watermark (2026-10-03T16:00:00) > snapshot timestamp" in fence_reason
        print(f"    Fresh Feed SLA + Newer Intraday Filing -> BLOCKED ({fence_reason}) [PASS]")
        print("    ✅ Part F Verified: FEED_HEARTBEAT_SLA (daemon health) != SOURCE_FRESHNESS_FENCE (trade protection).")

    # Cleanup watcher state
    for sym in [CRASH_SYM_1, CRASH_SYM_2]:
        if sym in watcher.state:
            del watcher.state[sym]
    watcher._save_state()
    # Restore fresh watermarks and remove test symbols
    wm_path = os.path.join(BASE_DIR, "data", "exchange_watermarks.json")
    if os.path.exists(wm_path):
        try:
            with open(wm_path, "r") as f:
                wm_data = json.load(f)
            for src in ["NSE", "BSE"]:
                if src in wm_data and "symbols" in wm_data[src]:
                    for sym in [CRASH_SYM_1, CRASH_SYM_2, "RACE_CO_887"]:
                        wm_data[src]["symbols"].pop(sym, None)
            with open(wm_path, "w") as f:
                json.dump(wm_data, f, indent=2)
        except Exception:
            pass
    record_source_watermark("NSE", last_successful_check_at=datetime.now().isoformat())
    record_source_watermark("BSE", last_successful_check_at=datetime.now().isoformat())
    print("\n  ✅ TEST 7 PASSED: Authoritative DB Outbox, Parquet Crash Consistency, and SLA Separation 100% Certified!")


if __name__ == "__main__":
    run_test_1_universe_expansion()
    run_test_2_amended_filing_pit()
    run_test_3_filing_detection_delay()
    run_test_4_pre_buy_source_freshness_fence()
    run_test_5_real_world_new_stock_recovery()
    run_test_6_freshness_to_buy_commit_race()
    run_test_7_crash_consistency_outbox_reconciliation()
    print("\n" + "=" * 80)
    print("ALL 7 MANDATORY LIFECYCLE, CONCURRENCY, PERSISTENCE & FRESHNESS TESTS PASSED PERFECTLY!")
    print("=" * 80)

