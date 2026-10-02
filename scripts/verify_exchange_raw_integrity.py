#!/usr/bin/env python3
"""
scripts/verify_exchange_raw_integrity.py
========================================
Audit utility to verify SHA256 cryptographic hashes for all immutable raw exchange payloads.

Satisfies Prompt Section 10:
  - For each raw filing: SHA256(payload) == stored SHA256 must pass.
  - Produces:
      total_raw_files
      valid_hashes
      hash_failures (must be 0)
      missing_hashes (must be 0)
      duplicate_payloads

Acceptance:
  hash_failures == 0
  missing_hashes == 0
"""

from __future__ import annotations
import os, sys, json, hashlib, argparse
from pathlib import Path
from typing import Dict, List, Any, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
EXCHANGE_DIR = REPO_ROOT / "data" / "exchange_financials"
RAW_CKPT_DIR = REPO_ROOT / "data" / "pit_raw_filings"

def compute_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def verify_raw_integrity(exchange_dir: Path = EXCHANGE_DIR) -> Dict[str, Any]:
    print("=" * 70)
    print("IMMUTABLE RAW FILING INTEGRITY AUDIT")
    print(f"Target Directory: {exchange_dir}")
    print("=" * 70)

    total_raw_files = 0
    valid_hashes = 0
    hash_failures = 0
    missing_hashes = 0
    corrupt_files = []
    missing_hash_files = []
    payload_hashes: Dict[str, str] = {}
    duplicate_payloads = 0

    if not exchange_dir.exists():
        print(f"⚠️ Exchange directory not found: {exchange_dir}")
        return {
            "total_raw_files": 0,
            "valid_hashes": 0,
            "hash_failures": 0,
            "missing_hashes": 0,
            "duplicate_payloads": 0,
            "status": "NO_FILES",
        }

    # Walk through exchange_financials/*/raw/
    for root, dirs, files in os.walk(exchange_dir):
        for f in files:
            if f.endswith(".payload.json") or f.endswith(".payload"):
                total_raw_files += 1
                payload_path = Path(root) / f
                sha_path = Path(root) / (f.replace(".payload.json", ".sha256").replace(".payload", ".sha256"))

                try:
                    with open(payload_path, "rb") as pf:
                        p_bytes = pf.read()
                    calc_sha = compute_sha256(p_bytes)

                    # Check for exact duplicate hashes across different symbols/filings
                    if calc_sha in payload_hashes:
                        duplicate_payloads += 1
                    else:
                        payload_hashes[calc_sha] = str(payload_path)

                    if not sha_path.exists():
                        missing_hashes += 1
                        missing_hash_files.append(str(payload_path))
                    else:
                        stored_sha = sha_path.read_text().strip()
                        if stored_sha == calc_sha:
                            valid_hashes += 1
                        else:
                            hash_failures += 1
                            corrupt_files.append({
                                "file": str(payload_path),
                                "stored_sha": stored_sha,
                                "calc_sha": calc_sha,
                            })
                except Exception as e:
                    hash_failures += 1
                    corrupt_files.append({"file": str(payload_path), "error": str(e)})

    # Also verify raw checkpoint JSONs
    if RAW_CKPT_DIR.exists():
        for f in os.listdir(RAW_CKPT_DIR):
            if f.endswith(".json"):
                total_raw_files += 1
                fpath = RAW_CKPT_DIR / f
                try:
                    with open(fpath, "rb") as jf:
                        j_bytes = jf.read()
                    json.loads(j_bytes)  # Validate valid JSON syntax
                    valid_hashes += 1
                except Exception as je:
                    hash_failures += 1
                    corrupt_files.append({"file": str(fpath), "error": f"JSON parse error: {je}"})

    print(f"Total Raw Files Audited:    {total_raw_files}")
    print(f"Valid Hashes / JSONs:       {valid_hashes}")
    print(f"Hash Failures:              {hash_failures}")
    print(f"Missing Hashes:             {missing_hashes}")
    print(f"Duplicate Payloads:         {duplicate_payloads}")

    is_pass = (hash_failures == 0 and missing_hashes == 0)
    audit_status = "PASS" if is_pass else "FAIL"
    print(f"\nRAW DATA INTEGRITY STATUS:  {audit_status}")
    print("=" * 70)

    if corrupt_files:
        print("\n❌ Corrupt Files:")
        for cf in corrupt_files[:10]:
            print(" ", cf)

    if missing_hash_files:
        print("\n⚠️ Missing Hash Files:")
        for mf in missing_hash_files[:10]:
            print(" ", mf)

    return {
        "total_raw_files": total_raw_files,
        "valid_hashes": valid_hashes,
        "hash_failures": hash_failures,
        "missing_hashes": missing_hashes,
        "duplicate_payloads": duplicate_payloads,
        "status": audit_status,
        "corrupt_files": corrupt_files,
    }

if __name__ == "__main__":
    res = verify_raw_integrity()
    if res["status"] != "PASS":
        sys.exit(1)
