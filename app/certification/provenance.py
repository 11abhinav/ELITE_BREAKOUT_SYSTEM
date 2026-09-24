# app/certification/provenance.py
"""
Code, Configuration, and Data Provenance tracker for deterministic certification.
"""
import hashlib
import json
import os
import subprocess
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple
import pandas as pd


@lru_cache(maxsize=1)
def get_git_commit() -> str:
    """Retrieves current Git HEAD commit hash."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
        return commit
    except Exception:
        return "UNKNOWN_COMMIT"


@lru_cache(maxsize=256)
def get_file_hash(file_path: str) -> str:
    """Computes SHA-256 hash of a file."""
    if not os.path.exists(file_path):
        return "FILE_NOT_FOUND"
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()[:16]


def get_config_hash(config_dict: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """
    Normalizes configuration keys and values, producing a deterministic SHA-256 hash.
    Returns (config_hash, normalized_dict).
    """
    # Filter out functions/modules/non-serializables
    clean_cfg = {}
    for k, v in config_dict.items():
        if isinstance(v, (int, float, str, bool, list, tuple, dict)) or v is None:
            clean_cfg[str(k)] = v
        else:
            clean_cfg[str(k)] = str(v)

    serialized = json.dumps(clean_cfg, sort_keys=True)
    cfg_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
    return cfg_hash, clean_cfg


def get_dataframe_hash(df: pd.DataFrame) -> str:
    """
    Computes a deterministic cryptographic hash of an OHLCV DataFrame.
    Normalizes column names, sorts by timestamp, formats numeric values to 4 decimals.
    """
    if df is None or df.empty:
        return "EMPTY_DATAFRAME"

    hasher = hashlib.sha256()
    cols = [c for c in ["Datetime", "Date", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    sub_df = df[cols].copy()
    
    # Sort deterministically
    time_col = "Datetime" if "Datetime" in sub_df.columns else ("Date" if "Date" in sub_df.columns else None)
    if time_col:
        sub_df[time_col] = sub_df[time_col].astype(str)
        sub_df = sub_df.sort_values(time_col).reset_index(drop=True)

    # Format numeric cols
    for c in ["Open", "High", "Low", "Close", "Volume"]:
        if c in sub_df.columns:
            sub_df[c] = pd.to_numeric(sub_df[c], errors="coerce").fillna(0.0).apply(lambda x: f"{x:.4f}")

    data_bytes = sub_df.to_csv(index=False, header=True).encode("utf-8")
    hasher.update(data_bytes)
    return hasher.hexdigest()[:16]
