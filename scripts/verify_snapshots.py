import os
import sys
import json
import hashlib
import shutil
import tempfile
from typing import Dict, Any

# Add project root and app to sys.path
root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, root_dir)
sys.path.insert(0, os.path.join(root_dir, "app"))

from app.core.events import EventPublisher
from app.core.snapshot_collector import GoldenSnapshotCollector
from app.core.invariant_engine import InvariantEngine
from app.core.policies import StrictTestPolicy
from app.pipeline_runner import PipelineRunner
from tests.golden.datasets.dataset_registry import DatasetRegistry

SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "golden", "snapshots")
CANDIDATES_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "golden", "candidates")

DATASET_NAMES = [
    "healthy_breakout", "failed_breakout", "false_breakout", "gap_up_continuation",
    "gap_up_failure", "low_volume_breakout", "corporate_action", "ipo_limited_history",
    "provider_failure", "missing_fundamentals", "delisted_symbol", "stale_cache",
    "multi_tf_confirmation", "reversal_candidate", "holiday_schedule", "split_adjusted_data"
]

def generate_into_dir(target_dir: str):
    for dataset_name in DATASET_NAMES:
        try:
            df = DatasetRegistry.load(dataset_name)
        except Exception:
            continue
        if df is None or df.empty:
            continue
            
        symbol = "MOCKSYM"
        category = "TestCategory"
        sector = "TestSector"
        nifty_ret_20d = 2.5
        regime_ctx = {"trend": "BULL"}
        
        out_dir = os.path.join(target_dir, dataset_name)
        os.makedirs(out_dir, exist_ok=True)
        
        publisher = EventPublisher()
        collector = GoldenSnapshotCollector(dataset_name=dataset_name, output_dir=out_dir)
        invariant_engine = InvariantEngine(policy=StrictTestPolicy())
        
        publisher.subscribe(collector)
        publisher.subscribe(invariant_engine)
        
        PipelineRunner.execute(
            symbol=symbol,
            category=category,
            sector=sector,
            ticker=df,
            delivery_pct=65.0,
            pledge_pct=0.0,
            nifty_ret_20d=nifty_ret_20d,
            regime_ctx=regime_ctx,
            bayesian_weights=None,
            bayesian_version="v1",
            publisher=publisher
        )

def hash_directory(directory: str) -> str:
    """Returns a hash of all json files in a directory."""
    hasher = hashlib.md5()
    for root, _, files in sorted(os.walk(directory)):
        for f in sorted(files):
            if f.endswith(".json"):
                path = os.path.join(root, f)
                with open(path, "rb") as file:
                    hasher.update(file.read())
    return hasher.hexdigest()

def test_determinism():
    print("🔍 Running Determinism Test...")
    with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
        generate_into_dir(d1)
        generate_into_dir(d2)
        h1 = hash_directory(d1)
        h2 = hash_directory(d2)
        if h1 != h2:
            print("❌ CRITICAL: Pipeline is non-deterministic! Hash mismatch between identical runs.")
            sys.exit(1)
    print("✅ Determinism verified.")

def diff_dicts(expected: Dict, actual: Dict, path="") -> list:
    diffs = []
    for k in expected:
        if k not in actual:
            diffs.append((path + k, expected[k], "<MISSING>"))
        elif isinstance(expected[k], dict) and isinstance(actual[k], dict):
            diffs.extend(diff_dicts(expected[k], actual[k], path + k + "."))
        elif expected[k] != actual[k]:
            diffs.append((path + k, expected[k], actual[k]))
            
    for k in actual:
        if k not in expected:
            diffs.append((path + k, "<MISSING>", actual[k]))
            
    return diffs

def validate_schema(snapshot: Dict) -> list:
    errors = []
    if "_metadata" not in snapshot:
        errors.append("Missing '_metadata' block")
    else:
        meta = snapshot["_metadata"]
        required_meta = ["snapshot_version", "dataset", "stage", "schema_version", "pipeline_version"]
        for key in required_meta:
            if key not in meta:
                errors.append(f"Missing metadata key: {key}")
                
    if "data" not in snapshot:
        errors.append("Missing 'data' block")
        
    return errors

def verify_against_baseline():
    print("🔍 Generating candidate snapshots...")
    if os.path.exists(CANDIDATES_DIR):
        shutil.rmtree(CANDIDATES_DIR)
    os.makedirs(CANDIDATES_DIR)
    
    generate_into_dir(CANDIDATES_DIR)
    
    if not os.path.exists(SNAPSHOTS_DIR):
        print("❌ CRITICAL: Baseline snapshots directory not found. Please run generate_snapshots.py first.")
        sys.exit(1)
        
    print("🔍 Verifying against baseline...")
    mismatches = 0
    
    for dataset_name in DATASET_NAMES:
        snap_dir = os.path.join(SNAPSHOTS_DIR, dataset_name)
        cand_dir = os.path.join(CANDIDATES_DIR, dataset_name)
        
        if not os.path.exists(snap_dir):
            continue
            
        for f in sorted(os.listdir(snap_dir)):
            if not f.endswith(".json"):
                continue
                
            snap_path = os.path.join(snap_dir, f)
            cand_path = os.path.join(cand_dir, f)
            
            if not os.path.exists(cand_path):
                print(f"❌ Missing file in candidate: {dataset_name}/{f}")
                mismatches += 1
                continue
                
            with open(snap_path) as sp, open(cand_path) as cp:
                snap_json = json.load(sp)
                cand_json = json.load(cp)
                
            schema_errors = validate_schema(cand_json)
            if schema_errors:
                print(f"\n❌ SCHEMA VALIDATION FAILED")
                print(f"Dataset: {dataset_name} | Stage: {f}")
                for err in schema_errors:
                    print(f"  - {err}")
                mismatches += 1
                continue
                
            diffs = diff_dicts(snap_json.get("data", {}), cand_json.get("data", {}))
            if diffs:
                print(f"\n❌ BEHAVIORAL DRIFT DETECTED")
                print(f"Dataset: {dataset_name}")
                print(f"Stage:   {f}")
                for d in diffs:
                    print(f"  Field:    {d[0]}")
                    print(f"  Expected: {d[1]}")
                    print(f"  Actual:   {d[2]}")
                mismatches += 1
                
    if mismatches > 0:
        print(f"\n❌ Failed: Found {mismatches} stage mismatches. Golden snapshot behavior diverged.")
        print("To resolve, either fix the regression, or explicitly approve the behavior change using a Behavior Manifest.")
        sys.exit(1)
    else:
        print("\n✅ Verification Successful: 0 behavioral changes detected.")
        sys.exit(0)

if __name__ == "__main__":
    test_determinism()
    verify_against_baseline()
