#!/usr/bin/env python3
import glob, os, time
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_HISTORY_1H_DIR = os.path.join(_REPO_ROOT, "data", "history", "1h")

print("Checking 1D parquet sample...")
f1d = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))[0]
df1d = pd.read_parquet(f1d)
print("1D sample:", os.path.basename(f1d), "Rows:", len(df1d))

print("Checking 1H parquet sample...")
f1h = sorted(glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet")))[0]
df1h = pd.read_parquet(f1h)
print("1H sample:", os.path.basename(f1h), "Rows:", len(df1h))
