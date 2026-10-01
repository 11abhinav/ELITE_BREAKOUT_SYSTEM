import json
import os
import pandas as pd

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
CLEAN_UNIVERSE_JSON = os.path.join(DATA_DIR, "certified_clean_universe_886.json")
PIT_PARQUET_PATH = os.path.join(DATA_DIR, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet")
RAW_CHECKPOINT_DIR = os.path.join(DATA_DIR, "pit_raw_filings")

with open(CLEAN_UNIVERSE_JSON, "r") as f:
    univ_data = json.load(f)
symbols_univ = univ_data.get("symbols", []) if isinstance(univ_data, dict) else univ_data
symbols_univ = [str(s).strip().upper() for s in symbols_univ]

df_pit = pd.read_parquet(PIT_PARQUET_PATH)
pit_symbols = set(df_pit["symbol"].str.strip().str.upper().unique())

raw_files = {f.replace(".json", "").strip().upper() for f in os.listdir(RAW_CHECKPOINT_DIR) if f.endswith(".json")}

missing_pit = [s for s in symbols_univ if s not in pit_symbols]
print(f"Total Approved Universe: {len(symbols_univ)}")
print(f"Total PIT Symbols in Parquet: {len(pit_symbols)}")
print(f"Approved Symbols Missing from PIT Parquet: {len(missing_pit)}")

in_raw_checkpoint = [s for s in missing_pit if s in raw_files]
not_in_raw = [s for s in missing_pit if s not in raw_files]

print(f"  - Present in pit_raw_filings/ checkpoint: {len(in_raw_checkpoint)}")
print(f"  - Missing from pit_raw_filings/: {len(not_in_raw)}")
print(f"Sample not in raw filings (first 10): {not_in_raw[:10]}")

# Now analyze annual filing count for the symbols in PIT parquet
filing_counts = df_pit.groupby("symbol")["period_end_date"].nunique()
less_than_5y = filing_counts[filing_counts < 5].to_dict()
print(f"PIT Symbols with < 5 unique annual/quarterly period ends: {len(less_than_5y)}")

# Check date ranges for those with < 5
print(f"Sample with < 5 filings: {list(less_than_5y.items())[:10]}")
