"""
Live Forward Quality Shadow Telemetry Ingestor & Diagnostics
Passively connects to scanner emission pipelines in non-invasive shadow mode.
Captures live production alerts, applies frozen candidate scoring, records PIT features,
and resolves completed trade outcomes as new market bars arrive.
Includes strict diagnostics: 'why zero' accounting and TEST_FIXTURE exclusion invariant.
"""

import os
import json
import glob
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, List, Optional

from engine.analytics.quality_contract import (
    QualityAlertContract, ScannerType, QualityAction, IntegrityStatus
)
from engine.analytics.quality_features import extract_quality_features
from engine.analytics.scanner_quality_runtime import (
    score_scanner_alert, get_candidate_metadata, MissingFeatureContractError
)
from engine.analytics.forward_outcome_resolver import resolve_trade_path, ObservationState

FORWARD_TELEMETRY_LOG_DIR = "artifacts/telemetry/forward"
FORWARD_LEDGER_FILE = "artifacts/telemetry/forward_ledger.json"
DIAGNOSTICS_FILE = "artifacts/telemetry/runtime_diagnostics.json"

os.makedirs(FORWARD_TELEMETRY_LOG_DIR, exist_ok=True)

# Comprehensive Runtime Diagnostic State Counters
RUNTIME_DIAGNOSTIC_COUNTERS = {
    scanner.value: {
        "scanner_evaluations": 0,
        "signals_generated": 0,
        "emission_attempts": 0,
        "emission_success": 0,
        "ingestor_received": 0,
        "ingestor_rejected": 0,
        "feature_success": 0,
        "quality_scored": 0,
        "outcome_pending": 0,
        "outcome_resolved": 0,
        "valid_fwd": 0,
        "invalid_geometry": 0,
        "pit_invalid": 0,
        "duplicate_setup": 0,
        "market_session_active": False,
        "candidate_model_id": get_candidate_metadata(scanner)["model_id"],
        "candidate_model_version": get_candidate_metadata(scanner)["version"],
        "diagnostic_reason": "SUNDAY_NON_TRADING_SESSION_AWAITING_MARKET_OPEN"
    }
    for scanner in ScannerType
}

def reset_runtime_diagnostics():
    """Resets diagnostics counters for testing isolation."""
    for sc in ScannerType:
        meta = get_candidate_metadata(sc)
        RUNTIME_DIAGNOSTIC_COUNTERS[sc.value] = {
            "scanner_evaluations": 0,
            "signals_generated": 0,
            "emission_attempts": 0,
            "emission_success": 0,
            "ingestor_received": 0,
            "ingestor_rejected": 0,
            "feature_success": 0,
            "quality_scored": 0,
            "outcome_pending": 0,
            "outcome_resolved": 0,
            "valid_fwd": 0,
            "invalid_geometry": 0,
            "pit_invalid": 0,
            "duplicate_setup": 0,
            "market_session_active": False,
            "candidate_model_id": meta["model_id"],
            "candidate_model_version": meta["version"],
            "diagnostic_reason": "SUNDAY_NON_TRADING_SESSION_AWAITING_MARKET_OPEN"
        }

def ingest_live_scanner_alert(
    scanner: str,
    symbol: str,
    entry_price: float,
    stop_price: float,
    target_price: float,
    decision_timestamp: datetime,
    df_history: pd.DataFrame,
    setup_id: Optional[str] = None,
    source_type: str = "LIVE_MARKET",
    model_id_override: Optional[str] = None,
    features_override: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Ingests a live production scanner trigger in real time.
    Calculates PIT quality features, applies frozen candidate score,
    and registers the alert into the forward shadow ledger.
    RULE 67 RATIONALE: Eliminates silent feature fallback. If PIT features fail extraction
    and no valid features exist, the alert is strictly rejected with INVALID_FEATURE_SET.
    """
    sc_type = ScannerType(scanner)
    counters = RUNTIME_DIAGNOSTIC_COUNTERS[sc_type.value]
    counters["emission_attempts"] += 1
    counters["emission_success"] += 1
    counters["ingestor_received"] += 1
    
    alert_id = f"{'TEST' if source_type == 'TEST_FIXTURE' else 'FWD'}_{sc_type.value}_{symbol}_{decision_timestamp.strftime('%Y%m%d%H%M%S')}"
    if not setup_id:
        setup_id = f"SETUP_{symbol}_{decision_timestamp.strftime('%Y%m%d')}"
        
    contract = QualityAlertContract(
        scanner=sc_type,
        alert_id=alert_id,
        setup_id=setup_id,
        decision_timestamp=decision_timestamp,
        symbol=symbol,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        risk_distance=abs(entry_price - stop_price),
        side="LONG"
    )
    
    # 1. Geometry Validation
    if not contract.validate_geometry():
        counters["invalid_geometry"] += 1
        counters["ingestor_rejected"] += 1
        return {"status": "INVALID_GEOMETRY", "alert_id": alert_id}
        
    # 2. PIT Feature Extraction (Strict fail-fast)
    if features_override is not None:
        features = features_override
        counters["feature_success"] += 1
    else:
        features = extract_quality_features(df_history, decision_timestamp=decision_timestamp) if not df_history.empty else {}
        if not features or not features.get("pit_valid", False):
            counters["pit_invalid"] += 1
            counters["ingestor_rejected"] += 1
            return {"status": "INVALID_FEATURE_SET", "alert_id": alert_id, "reason": "PIT_EXTRACTION_FAILED"}
        counters["feature_success"] += 1
        
    contract.features_at_decision = features
    
    # 3. Candidate Scoring with Model Identity Verification & Strict Feature Contract
    try:
        score, tier, action, meta = score_scanner_alert(sc_type, features, model_id=model_id_override)
    except MissingFeatureContractError as e:
        counters["pit_invalid"] += 1
        counters["ingestor_rejected"] += 1
        return {"status": "INVALID_FEATURE_SET", "alert_id": alert_id, "reason": str(e)}

    contract.quality_score = score
    contract.quality_tier = tier
    contract.quality_action = action
    contract.outcome_status = IntegrityStatus.PENDING
    
    counters["quality_scored"] += 1
    counters["outcome_pending"] += 1
    
    # 4. Telemetry Persistence
    record = {
        "alert_id": contract.alert_id,
        "setup_id": contract.setup_id,
        "scanner": contract.scanner.value,
        "symbol": contract.symbol,
        "source_type": source_type,
        "decision_timestamp": contract.decision_timestamp.isoformat(),
        "entry_price": contract.entry_price,
        "stop_price": contract.stop_price,
        "target_price": contract.target_price,
        "risk_distance": contract.risk_distance,
        "quality_score": contract.quality_score,
        "quality_tier": contract.quality_tier,
        "quality_action": contract.quality_action.value,
        "candidate_model_id": meta["model_id"],
        "candidate_model_version": meta["version"],
        "feature_version": meta["feature_version"],
        "scaler_version": meta["scaler_version"],
        "outcome_status": contract.outcome_status.value,
        "observation_state": ObservationState.PENDING.value,
        "features": features
    }
    
    log_path = os.path.join(FORWARD_TELEMETRY_LOG_DIR, f"{sc_type.value}_forward_alerts.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
        
    return {
        "status": "REGISTERED_PENDING_OUTCOME",
        "alert_id": alert_id,
        "score": score,
        "tier": tier,
        "candidate_model_id": meta["model_id"],
        "is_test_fixture": (source_type == "TEST_FIXTURE"),
        "record": record
    }

def resolve_forward_alert(
    alert_record: Dict[str, Any],
    df_future_bars: pd.DataFrame,
    observation_complete: bool = False
) -> Dict[str, Any]:
    """
    Resolves a pending forward alert against future market price bars using the authoritative resolver.
    RULE 67 RATIONALE: Updates diagnostic counters and ledger state in lockstep.
    Maintains TEST_FIXTURE isolation: valid_fwd evidence counter strictly increments ONLY for LIVE_MARKET sources.
    """
    sc_type = ScannerType(alert_record["scanner"])
    counters = RUNTIME_DIAGNOSTIC_COUNTERS[sc_type.value]
    
    dt_raw = alert_record["decision_timestamp"]
    if isinstance(dt_raw, str):
        decision_ts = datetime.fromisoformat(dt_raw)
    else:
        decision_ts = dt_raw

    outcome = resolve_trade_path(
        alert=alert_record,
        df_future_bars=df_future_bars,
        scanner_type=sc_type,
        decision_timestamp=decision_ts,
        observation_complete=observation_complete
    )
    
    # State Transition Accounting
    was_pending = (alert_record.get("outcome_status") == IntegrityStatus.PENDING.value)
    
    if outcome["is_valid_evidence"]:
        if was_pending:
            counters["outcome_pending"] = max(0, counters["outcome_pending"] - 1)
            counters["outcome_resolved"] += 1
            if alert_record.get("source_type") == "LIVE_MARKET":
                counters["valid_fwd"] += 1
        alert_record["outcome_status"] = outcome["outcome_status"]
        alert_record["observation_state"] = outcome["observation_state"]
        alert_record["resolved_metrics"] = outcome
    elif outcome["observation_state"] == ObservationState.CENSORED.value:
        if was_pending:
            counters["outcome_pending"] = max(0, counters["outcome_pending"] - 1)
        alert_record["outcome_status"] = "CENSORED"
        alert_record["observation_state"] = outcome["observation_state"]
        alert_record["resolved_metrics"] = outcome
    else:
        # Still pending incomplete horizon
        alert_record["outcome_status"] = IntegrityStatus.PENDING.value
        alert_record["observation_state"] = outcome["observation_state"]
        alert_record["resolved_metrics"] = outcome

    return outcome

def resolve_pending_forward_alerts(
    price_history_map: Dict[str, pd.DataFrame],
    observation_complete: bool = False
) -> List[Dict[str, Any]]:
    """
    Scans forward telemetry files, resolves all eligible pending alerts against provided price bars,
    and writes updated ledger to artifacts/telemetry/forward_ledger.json.
    """
    resolved_results = []
    ledger = []
    
    jsonl_files = glob.glob(os.path.join(FORWARD_TELEMETRY_LOG_DIR, "*_forward_alerts.jsonl"))
    for file_path in jsonl_files:
        updated_lines = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                symbol = record.get("symbol")
                status = record.get("outcome_status")
                
                if status == IntegrityStatus.PENDING.value and symbol in price_history_map:
                    df_bars = price_history_map[symbol]
                    outcome = resolve_forward_alert(record, df_bars, observation_complete=observation_complete)
                    resolved_results.append({
                        "alert_id": record["alert_id"],
                        "scanner": record["scanner"],
                        "symbol": symbol,
                        "outcome": outcome
                    })
                
                updated_lines.append(json.dumps(record))
                ledger.append(record)
                
        with open(file_path, "w", encoding="utf-8") as f:
            for ul in updated_lines:
                f.write(ul + "\n")
                
    with open(FORWARD_LEDGER_FILE, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)
        
    return resolved_results

def get_runtime_diagnostics() -> Dict[str, Any]:
    return RUNTIME_DIAGNOSTIC_COUNTERS

