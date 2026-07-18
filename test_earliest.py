import sys
sys.path.append('app')
import pandas as pd
import os
from config import DATA_DIR

history_dir = os.path.join(DATA_DIR, "history", "1d")
bpcl_path = os.path.join(history_dir, "BPCL.parquet")
if os.path.exists(bpcl_path):
    df = pd.read_parquet(bpcl_path)
    print(df.head(2))
