import os
import json
import pandas as pd
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "golden"

def load_golden_dataframe(dataset: str, scenario: str) -> pd.DataFrame:
    """
    Loads a golden dataset as a Pandas DataFrame.
    Example: load_golden_dataframe("price", "healthy")
    Looks for tests/golden/price/healthy.parquet or healthy.csv
    """
    base_path = GOLDEN_DIR / dataset / scenario
    
    parquet_path = base_path.with_suffix(".parquet")
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
        
    csv_path = base_path.with_suffix(".csv")
    if csv_path.exists():
        return pd.read_csv(csv_path)
        
    raise FileNotFoundError(f"Golden dataset not found: {base_path}.*(parquet|csv)")

def load_golden_json(dataset: str, scenario: str) -> dict:
    """
    Loads a golden dataset as a dictionary.
    Example: load_golden_json("scanner", "breakout_expected")
    Looks for tests/golden/scanner/breakout_expected.json
    """
    json_path = (GOLDEN_DIR / dataset / scenario).with_suffix(".json")
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
            
    raise FileNotFoundError(f"Golden dataset not found: {json_path}")

def save_golden_dataframe(df: pd.DataFrame, dataset: str, scenario: str, format="parquet"):
    """
    Saves a DataFrame as a golden dataset (used during initial fixture creation).
    """
    dir_path = GOLDEN_DIR / dataset
    dir_path.mkdir(parents=True, exist_ok=True)
    
    file_path = dir_path / f"{scenario}.{format}"
    if format == "parquet":
        df.to_parquet(file_path, index=False)
    elif format == "csv":
        df.to_csv(file_path, index=False)
    else:
        raise ValueError(f"Unsupported format: {format}")

def save_golden_json(data: dict, dataset: str, scenario: str):
    """
    Saves a dict as a golden dataset.
    """
    dir_path = GOLDEN_DIR / dataset
    dir_path.mkdir(parents=True, exist_ok=True)
    
    file_path = dir_path / f"{scenario}.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
