"""
Scanner Quality Runtime Scoring Engine
Authoritative runtime execution engine for frozen candidate models.
Directly loads and executes frozen model weights, scalers, and calibration parameters
from artifacts/scanner_quality_model_registry.json.
Guarantees 100% mathematical fidelity to frozen research artifacts.
"""

import os
import json
import hashlib
from typing import Dict, Any, Tuple, Optional
import numpy as np
from engine.analytics.quality_contract import ScannerType, QualityAction

REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "artifacts", "scanner_quality_model_registry.json"
)

SCANNER_TO_CANDIDATE_MAP = {
    ScannerType.EOD: "AQS_EOD_v1",
    ScannerType.MULTIBAGGER: "AQS_ACCUM_v1",
    ScannerType.PULLBACK: "AQS_PULLBACK_v1",
    ScannerType.DAILY_BUILDER: "AQS_DAILY_BUILDER_v1",
    ScannerType.MULTI_TF: "AQS_MULTI_TF_v1",
    ScannerType.WEALTH_ENGINE: "AQS_WEALTH_v1",
    ScannerType.REVERSAL: "AQS_REVERSAL_v3"
}

class RegistryIntegrityError(Exception):
    """Raised when the registry cryptographic SHA256 signature verification fails."""
    pass

class MissingFeatureContractError(ValueError):
    """Raised when an active predictor is missing, non-numeric, or non-finite."""
    pass

def compute_canonical_registry_hash(registry_data: Dict[str, Any]) -> str:
    """
    Computes deterministic SHA256 digest of canonical compact JSON representation.
    RULE 67 RATIONALE: Excludes 'registry_sha256' key, sorts keys recursively, uses compact separators
    (',', ':') and UTF-8 encoding. This ensures formatting/whitespace changes do not invalidate
    the underlying cryptographic digest while strictly catching any semantic parameter modification.
    """
    payload = {k: v for k, v in registry_data.items() if k != "registry_sha256"}
    canonical_bytes = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical_bytes).hexdigest()

def update_registry_hash_file(registry_filepath: Optional[str] = None) -> str:
    """
    Utility to recompute and persist the canonical SHA256 digest directly into the registry JSON artifact.
    Prevents manual copy-paste errors during model governance freezes.
    """
    target_path = registry_filepath or REGISTRY_PATH
    if not os.path.exists(target_path):
        raise FileNotFoundError(f"Registry file not found at: {target_path}")
    with open(target_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    canonical_hash = compute_canonical_registry_hash(data)
    data["registry_sha256"] = canonical_hash
    
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return canonical_hash

def load_authoritative_registry(verify_integrity: bool = True) -> Dict[str, Any]:
    """
    Loads the canonical model registry artifact and verifies cryptographic integrity.
    RULE 67 RATIONALE: Enforces active cryptographic verification of the frozen candidate models.
    """
    if not os.path.exists(REGISTRY_PATH):
        raise FileNotFoundError(f"Authoritative model registry not found at: {REGISTRY_PATH}")
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        reg = json.load(f)

    if verify_integrity:
        embedded_hash = reg.get("registry_sha256")
        if not embedded_hash or len(embedded_hash) != 64:
            raise RegistryIntegrityError(f"Registry missing valid 64-character SHA256 digest: found '{embedded_hash}'")
        computed_hash = compute_canonical_registry_hash(reg)
        if embedded_hash != computed_hash:
            raise RegistryIntegrityError(
                f"Authoritative model registry hash mismatch! Embedded: {embedded_hash}, Computed: {computed_hash}. "
                "The model registry has been tampered with or modified without canonical re-signing."
            )
    return reg

def get_candidate_metadata(scanner: ScannerType) -> Dict[str, Any]:
    """Retrieves frozen candidate metadata and verified freeze hash from the registry."""
    if scanner not in SCANNER_TO_CANDIDATE_MAP:
        raise ValueError(f"Unsupported scanner type: {scanner}")
    model_id = SCANNER_TO_CANDIDATE_MAP[scanner]
    reg = load_authoritative_registry(verify_integrity=True)
    if model_id not in reg["models"]:
        raise ValueError(f"Model ID {model_id} not found in authoritative registry.")
    meta = dict(reg["models"][model_id])
    meta["registry_sha256"] = reg.get("registry_sha256", "UNKNOWN")
    return meta

def execute_candidate_model(model_def: Dict[str, Any], features: Dict[str, Any]) -> float:
    """
    Executes the exact mathematical candidate model from frozen parameters in the registry.
    RULE 67 RATIONALE: Enforces strict finite-numeric validation on all active predictors.
    Zero silent mean substitution: missing, None, NaN, +/-inf, non-numeric strings, or booleans
    immediately raise MissingFeatureContractError.
    """
    active_features = model_def["active_features"]
    scaler_means = model_def["scaler_parameters"]["means"]
    scaler_stds = model_def["scaler_parameters"]["stds"]
    weights = model_def["weights"]
    intercept = float(model_def.get("intercept", 50.0))
    multiplier = float(model_def.get("multiplier", 1.0))
    calib = model_def["calibration_parameters"]
    raw_min = float(calib["raw_min"])
    raw_max = float(calib["raw_max"])
    family = model_def.get("model_family")
    model_id = model_def.get("model_id", "UNKNOWN_MODEL")

    # Strict feature presence and finite-numeric validation
    z_scores = {}
    for feat in active_features:
        if feat not in features:
            raise MissingFeatureContractError(
                f"Missing required active predictor '{feat}' for model '{model_id}'."
            )
        raw_val = features[feat]
        # Reject booleans (bool is a subclass of int in Python, so check explicitly)
        if isinstance(raw_val, bool) or raw_val is None:
            raise MissingFeatureContractError(
                f"Predictor '{feat}' has invalid type {type(raw_val)} (booleans and None strictly rejected) for model '{model_id}'."
            )
        try:
            val = float(raw_val)
        except (ValueError, TypeError):
            raise MissingFeatureContractError(
                f"Predictor '{feat}' with non-numeric value '{raw_val}' cannot be converted to float for model '{model_id}'."
            )
        if not np.isfinite(val):
            raise MissingFeatureContractError(
                f"Predictor '{feat}' has non-finite value '{val}' (NaN or Inf rejected) for model '{model_id}'."
            )

        mean = float(scaler_means[feat])
        std = float(scaler_stds[feat])
        if std <= 1e-6:
            std = 1.0
            
        z_scores[feat] = (val - mean) / std

    # Linear dot product
    weighted_sum = sum(float(weights[f]) * z_scores[f] for f in active_features)

    if family == "REGULARIZED_RIDGE_LINEAR":
        raw = intercept + weighted_sum
        norm = (raw - raw_min) / max(raw_max - raw_min, 1e-6)
        score = float(np.clip(norm, 0.0, 1.0) * 100.0)
    elif family in ("LINEAR_STANDARDIZED_SCORE", "DEPTH_REBOUND_SCORE", "ORB_SURGE_SCORE", "TREND_ALIGNMENT_SCORE", "DISCOVERY_ACTIVE"):
        score = intercept + multiplier * weighted_sum
    elif family == "MULTI_FACTOR_FUNDAMENTAL_SCORE":
        score = sum(float(weights[f]) * float(features[f]) for f in active_features)
    else:
        score = intercept + multiplier * weighted_sum

    return round(float(np.clip(score, 0.0, 100.0)), 2)

def score_scanner_alert(
    scanner: ScannerType,
    features: Dict[str, Any],
    model_id: Optional[str] = None
) -> Tuple[float, str, QualityAction, Dict[str, Any]]:
    """
    Authoritative master scoring entry point.
    Executes the frozen candidate model from the authoritative registry.
    """
    meta = get_candidate_metadata(scanner)
    target_model_id = model_id or meta["model_id"]

    if target_model_id != meta["model_id"]:
        raise ValueError(f"Model ID mismatch: requested '{target_model_id}', expected frozen '{meta['model_id']}'")

    score = execute_candidate_model(meta, features)

    if meta.get("status") == "DISCOVERY_ONLY":
        tier = "DISCOVERY"
        action = QualityAction.PASS_THROUGH
    else:
        tier = "ELITE" if score >= 75.0 else "HIGH" if score >= 65.0 else "STANDARD" if score >= 50.0 else "LOW"
        action = QualityAction.RANK_BOOST if score >= 65.0 else QualityAction.PASS_THROUGH

    return score, tier, action, meta

