"""
app/accumulation_contracts.py

Path-Scoped Input Contracts for ACCUMULATION_SCANNER_V1.
Enforces that all fields consumed by accumulation decision gates and SL/Target calculation
are explicitly declared and validated in telemetry manifests.
"""

from typing import Dict, List, Set, Tuple

ACCUMULATION_INPUT_CONTRACT: Dict[str, Set[str]] = {
    "REQUIRED": {
        "Close", "Open", "High", "Low", "Volume",
        "SMA20", "SMA50", "SMA200", "EMA20",
        "ATR", "RSI", "ADX", "OBV", "OBV_SLOPE", "BB_WIDTH", "ATR_PERCENTILE",
        "RS_NIFTY_20D", "RS_NIFTY_60D",
        "HIGH_52W", "HIGH_200D", "RESISTANCE", "DISTANCE_TO_RESISTANCE",
        "ROE", "ROCE", "DEBT_EQUITY", "SALES_GROWTH", "PAT_GROWTH"
    },
    "OPTIONAL": {
        "DeliveryPct", "DeliveryChange", "UpVolumeRatio", "DownVolumeRatio",
        "RS_SECTOR_20D", "RS_SECTOR_60D", "RESISTANCE_TEST_COUNT", "PledgePct"
    },
    "NOT_USED": {
        "MACD_Hist", "MarketCap"
    }
}

def validate_accumulation_manifest(manifest: List[Dict[str, str]]) -> Tuple[bool, List[str], Dict[str, int]]:
    """
    Validates a decision manifest for ACCUMULATION scanner against the input contract.
    Returns: (is_valid, missing_required_fields, stats_dict)
    """
    required_fields = ACCUMULATION_INPUT_CONTRACT["REQUIRED"]
    captured_valid = {entry.get("name") for entry in manifest if entry.get("valid", False) is True}
    
    missing_fields = [f for f in required_fields if f not in captured_valid]
    
    stats = {
        "expected_required": len(required_fields),
        "captured_total": len(manifest),
        "valid_required": len(required_fields) - len(missing_fields),
        "missing_required": len(missing_fields)
    }
    
    is_valid = (len(missing_fields) == 0)
    return is_valid, missing_fields, stats
