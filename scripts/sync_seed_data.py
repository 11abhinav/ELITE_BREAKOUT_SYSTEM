#!/usr/bin/env python3
"""
Seed Data Synchronization Protocol for Elite Breakout System
Safely synchronizes certified git seed data (/app/data_seed) into the persistent volume (/app/data).
Guarantees anti-downgrade: preserves more complete volume data while updating outdated volume data.
"""

import os
import sys
import json
import shutil
from pathlib import Path

def sync_seed_data():
    base_dir = Path(os.getenv("ELITE_BASE_DIR", Path(__file__).resolve().parent.parent))
    seed_dir = Path("/app/data_seed") if Path("/app/data_seed").exists() else (base_dir / "data_seed")
    data_dir = Path("/app/data") if Path("/app/data").exists() else (base_dir / "data")

    if not seed_dir.exists():
        print(f"ℹ️ [SEED_SYNC] No seed directory found at {seed_dir}. Skipping.")
        return

    data_dir.mkdir(parents=True, exist_ok=True)

    # 1. Synchronize canonical_pit_rebuilt.parquet
    seed_parquet = seed_dir / "canonical_pit_rebuilt.parquet"
    target_parquet = data_dir / "canonical_pit_rebuilt.parquet"

    if seed_parquet.exists():
        should_copy = False
        if not target_parquet.exists() or target_parquet.stat().st_size == 0:
            should_copy = True
            reason = "target missing or empty"
        else:
            try:
                import pandas as pd
                df_seed = pd.read_parquet(seed_parquet)
                df_target = pd.read_parquet(target_parquet)

                seed_q2 = int((df_seed["latest_quarterly_period"].astype(str) >= "2026-09-30").sum()) if "latest_quarterly_period" in df_seed.columns else 0
                target_q2 = int((df_target["latest_quarterly_period"].astype(str) >= "2026-09-30").sum()) if "latest_quarterly_period" in df_target.columns else 0

                if seed_q2 >= target_q2 and len(df_seed) >= len(df_target):
                    should_copy = True
                    reason = f"seed Q2 count ({seed_q2}) >= target ({target_q2})"
                else:
                    reason = f"target is newer (target Q2: {target_q2} > seed: {seed_q2})"
            except Exception as e:
                # If target is corrupt, copy seed
                should_copy = True
                reason = f"target inspection error: {e}"

        if should_copy:
            shutil.copy2(seed_parquet, target_parquet)
            seed_meta = seed_dir / "canonical_pit_rebuilt_meta.json"
            if seed_meta.exists():
                shutil.copy2(seed_meta, data_dir / "canonical_pit_rebuilt_meta.json")
            print(f"✅ [SEED_SYNC] Copied seed canonical snapshot to volume ({reason}).")
        else:
            print(f"ℹ️ [SEED_SYNC] Preserved volume canonical snapshot ({reason}).")

    # 2. Synchronize pit_raw_filings/*.json
    seed_raw_dir = seed_dir / "pit_raw_filings"
    target_raw_dir = data_dir / "pit_raw_filings"

    if seed_raw_dir.exists():
        target_raw_dir.mkdir(parents=True, exist_ok=True)
        synced_count = 0
        preserved_count = 0

        for seed_file in seed_raw_dir.glob("*.json"):
            target_file = target_raw_dir / seed_file.name
            if not target_file.exists() or target_file.stat().st_size == 0:
                shutil.copy2(seed_file, target_file)
                synced_count += 1
                continue

            # Both exist: compare content count and latest period
            try:
                with open(seed_file, "r", encoding="utf-8") as f:
                    seed_data = json.load(f)
                with open(target_file, "r", encoding="utf-8") as f:
                    target_data = json.load(f)

                if not isinstance(target_data, list) or len(target_data) == 0:
                    shutil.copy2(seed_file, target_file)
                    synced_count += 1
                    continue

                if not isinstance(seed_data, list) or len(seed_data) == 0:
                    preserved_count += 1
                    continue

                def _get_max_period(items):
                    periods = [str(r.get("period_end_date") or r.get("period_end") or "")[:10] for r in items if isinstance(r, dict)]
                    return max(periods) if periods else ""

                seed_max = _get_max_period(seed_data)
                target_max = _get_max_period(target_data)

                if len(seed_data) > len(target_data) or seed_max > target_max:
                    shutil.copy2(seed_file, target_file)
                    synced_count += 1
                else:
                    preserved_count += 1
            except Exception:
                shutil.copy2(seed_file, target_file)
                synced_count += 1

        print(f"✅ [SEED_SYNC] Raw filings synchronized: {synced_count} updated/added, {preserved_count} preserved on volume.")

    # 3. Synchronize manifest
    seed_manifest = seed_dir / "pit_raw_filings_manifest.json"
    target_manifest = data_dir / "pit_raw_filings_manifest.json"
    if seed_manifest.exists() and (not target_manifest.exists() or seed_manifest.stat().st_mtime > target_manifest.stat().st_mtime):
        shutil.copy2(seed_manifest, target_manifest)
        print("✅ [SEED_SYNC] Copied pit_raw_filings_manifest.json to volume.")

if __name__ == "__main__":
    sync_seed_data()
