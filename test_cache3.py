import sys
sys.path.append('app')
import pandas as pd
import os
from config import DATA_DIR

history_dir = os.path.join(DATA_DIR, "history", "1d")
bpcl_path = os.path.join(history_dir, "BPCL.parquet")
havells_path = os.path.join(history_dir, "HAVELLS.parquet")

for path in [bpcl_path, havells_path]:
    if os.path.exists(path):
        df = pd.read_parquet(path)
        print(f"{os.path.basename(path)} cached rows: {len(df)}")
        print(df.tail(2))
    else:
        print(f"{os.path.basename(path)} not in cache.")
