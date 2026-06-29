import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

def normalize_id(x: str) -> str:
    if pd.isna(x) or not isinstance(x, str):
        return ""
    if ":" in x:
        x = x.split(":")[-1]
    return x.upper().replace("-", "").replace("_", "").replace("&", "").strip()

# ... testing logic ...
