import sys
sys.path.append('app')
import pandas as pd
import os
from config import DATA_DIR

cache_path = os.path.join(DATA_DIR, "price_cache_1d.parquet")
if os.path.exists(cache_path):
    print(f"Reading cache: {cache_path}")
    df = pd.read_parquet(cache_path)
    
    # Check BPCL
    if "BPCL" in df.index.get_level_values("Symbol"):
        print("BPCL cached rows:", len(df.xs("BPCL", level="Symbol")))
    else:
        print("BPCL not in cache.")
        
    # Check HAVELLS
    if "HAVELLS" in df.index.get_level_values("Symbol"):
        print("HAVELLS cached rows:", len(df.xs("HAVELLS", level="Symbol")))
    else:
        print("HAVELLS not in cache.")
else:
    print(f"Cache not found at {cache_path}")

