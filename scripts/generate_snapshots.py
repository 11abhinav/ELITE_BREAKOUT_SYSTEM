import os
import sys
import json
from datetime import datetime

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

DATASET_NAMES = [
    "healthy_breakout", "failed_breakout", "false_breakout", "gap_up_continuation",
    "gap_up_failure", "low_volume_breakout", "corporate_action", "ipo_limited_history",
    "provider_failure", "missing_fundamentals", "delisted_symbol", "stale_cache",
    "multi_tf_confirmation", "reversal_candidate", "holiday_schedule", "split_adjusted_data"
]

def generate_all_snapshots():
    print("🚀 Generating Golden Snapshots...")
    
    # Track the metadata
    pipeline_version = "Constitution v1.0"
    schema_version = "1.0"
    generated_date = datetime.now().strftime("%Y-%m-%d")
    
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    
    for dataset_name in DATASET_NAMES:
        print(f"  → Processing dataset: {dataset_name}")
        
        # 1. Load data
        try:
            df = DatasetRegistry.load(dataset_name)
        except Exception as e:
            print(f"    ⚠️ Warning: Failed to load {dataset_name}: {e}")
            continue
        if df is None or df.empty:
            print(f"    ⚠️ Warning: {dataset_name} returned empty data. Skipping.")
            continue
            
        # Mocked boundaries
        symbol = "MOCKSYM"
        category = "TestCategory"
        sector = "TestSector"
        nifty_ret_20d = 2.5
        regime_ctx = {"trend": "BULL"}
        
        # 2. Setup isolated infrastructure
        out_dir = os.path.join(SNAPSHOTS_DIR, dataset_name)
        os.makedirs(out_dir, exist_ok=True)
        
        publisher = EventPublisher()
        collector = GoldenSnapshotCollector(dataset_name=dataset_name, output_dir=out_dir)
        collector.pipeline_version = pipeline_version
        collector.schema_version = schema_version
        
        invariant_engine = InvariantEngine(policy=StrictTestPolicy())
        
        publisher.subscribe(collector)
        publisher.subscribe(invariant_engine)
        
        # 3. Execute core business logic
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
        
    import hashlib
    def hash_directory(directory: str) -> str:
        hasher = hashlib.md5()
        for root, _, files in sorted(os.walk(directory)):
            for f in sorted(files):
                if f.endswith(".json") and f != "manifest.json":
                    path = os.path.join(root, f)
                    with open(path, "rb") as file:
                        hasher.update(file.read())
        return hasher.hexdigest()
        
    snapshot_hash = hash_directory(SNAPSHOTS_DIR)
    
    # 4. Generate manifest.json
    manifest = {
        "snapshot_set": "1.0",
        "generated": generated_date,
        "datasets": len(DATASET_NAMES),
        "snapshots": len(DATASET_NAMES) * 7,
        "pipeline_stages": 7,
        "pipeline_version": pipeline_version,
        "schema_version": schema_version,
        "snapshot_hash": snapshot_hash
    }
    
    manifest_path = os.path.join(SNAPSHOTS_DIR, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        
    print(f"✅ Generated snapshots for {len(DATASET_NAMES)} datasets.")
    print(f"✅ Manifest written to {manifest_path}")

if __name__ == "__main__":
    generate_all_snapshots()
