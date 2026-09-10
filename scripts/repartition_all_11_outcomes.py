#!/usr/bin/env python3
"""
scripts/repartition_all_11_outcomes.py
Standardizes all 11 scanner outcome CSVs to the strict institutional Three-Way Partition:
1. DEV (Development / Initial Calibration): 2025-07-24 -> 2025-12-31
2. VAL (Validation / Challenger Tuning):   2026-01-01 -> 2026-05-31
3. HOLDOUT (Locked Untouched Holdout):     2026-06-01 -> 2026-09-04
"""

import os
import glob
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

DEV_END = "2026-01-01"
VAL_END = "2026-06-01"

def assign_partition(d_str: str) -> str:
    d = str(d_str)[:10]
    if d < DEV_END:
        return "DEV"
    elif d < VAL_END:
        return "VAL"
    else:
        return "HOLDOUT"

def repartition_all():
    files = glob.glob(os.path.join(_REPORTS_DIR, "*outcomes.csv"))
    print(f"Standardizing partitions across {len(files)} outcome files...")
    
    for fpath in sorted(files):
        df = pd.read_csv(fpath)
        if "scan_date" not in df.columns:
            continue
        
        df["partition"] = df["scan_date"].apply(assign_partition)
        df.to_csv(fpath, index=False)
        
        counts = df["partition"].value_counts().to_dict()
        print(f"[{os.path.basename(fpath)}] Total: {len(df)} | DEV: {counts.get('DEV', 0)} | VAL: {counts.get('VAL', 0)} | HOLDOUT: {counts.get('HOLDOUT', 0)}")

if __name__ == "__main__":
    repartition_all()
