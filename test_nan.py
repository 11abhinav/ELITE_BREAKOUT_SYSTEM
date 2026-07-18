import sys
sys.path.append('app')
import pandas as pd
import os
from config import DATA_DIR

history_dir = os.path.join(DATA_DIR, "history", "1d")
bpcl_path = os.path.join(history_dir, "BPCL.parquet")

if os.path.exists(bpcl_path):
    df = pd.read_parquet(bpcl_path)
    print("Total rows:", len(df))
    df_clean = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    print("Rows after dropna:", len(df_clean))
    if len(df) != len(df_clean):
        nan_rows = df[df.isna().any(axis=1)]
        print("Rows with NaNs:", len(nan_rows))
        print(nan_rows.head(3))
else:
    print("File not found")
