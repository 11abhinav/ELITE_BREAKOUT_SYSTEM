"""
Scanner Input Contracts — Phase 4B
Defines path-scoped input contracts for each scanner and investment strategy.
Enforces the certification invariant: USED_BY_DECISION ∈ manifest.
"""

from typing import Dict, List, Set, Tuple

# Scanner Input Contract Definitions
SCANNER_INPUT_CONTRACTS: Dict[str, Dict[str, Set[str]]] = {
    "EOD": {
        "REQUIRED": {"Close", "Open", "High", "Low", "Volume", "RSI", "EMA20", "SMA50", "SMA200", "ATR", "ADX", "VolumeRatio", "PRIOR_20D_HIGH"},
        "OPTIONAL": {"DeliveryPct", "PledgePct", "EarningsFlag", "EarningsDate"},
        "NOT_USED": {"ROE", "ROCE", "DebtEquity", "SalesGrowth"}
    },
    "REVERSAL": {
        "REQUIRED": {"Close", "Open", "High", "Low", "Volume", "RSI", "EMA20", "SMA50", "SMA200", "ATR", "VolumeRatio", "High52W", "Low52W", "DropPct"},
        "OPTIONAL": {"MACD", "MACDSignal", "MACDHist", "DeliveryPct", "PledgePct"},
        "NOT_USED": {"ROE", "ROCE", "SalesGrowth", "PATGrowth"}
    },
    "MULTIBAGGER": {
        "REQUIRED": {"Close", "Open", "High", "Low", "Volume", "ROE", "ROCE", "DebtEquity", "MarketCap", "PE", "PromoterPledge", "OperatingCashFlowTTM", "SalesGrowth", "PATGrowth", "EBITDAMargin", "ValuationScore", "QualityScore", "TrendScore"},
        "OPTIONAL": {"FCF", "AuditorFlags", "RS_Rating"},
        "NOT_USED": {"MACDHist"}
    },
    "PULLBACK": {
        "REQUIRED": {"Close", "Open", "High", "Low", "Volume", "EMA9", "EMA20", "EMA50", "SMA50", "SMA200", "RSI", "ATR", "NaturalRR", "RiskPct"},
        "OPTIONAL": {"DeliveryPct", "NiftyRSRating", "SectorRank"},
        "NOT_USED": {"ROE", "ROCE"}
    },
    "MULTI_TF": {
        "REQUIRED": {"Close", "Open", "High", "Low", "Volume", "RSI", "EMA20", "SMA50", "SMA200", "ADX", "ATR", "PriorHigh"},
        "OPTIONAL": {"EMA15", "BBWidthPctile", "VolumeRatio5m"},
        "NOT_USED": {"ROE", "ROCE", "SalesGrowth"}
    },
    # Path-scoped contracts for Wealth Engine
    "WEALTH_CORE_COMPOUNDER": {
        "REQUIRED": {"Close", "ROE", "ROCE", "DebtEquity", "MarketCap", "PromoterPledge", "OperatingCashFlowTTM", "SalesGrowth", "PATGrowth", "SMA200"},
        "OPTIONAL": {"PE", "EBITDAMargin"},
        "NOT_USED": {"RSI", "MACD"}
    },
    "WEALTH_GROWTH": {
        "REQUIRED": {"Close", "SalesGrowth", "PATGrowth", "ROE", "ROCE", "MarketCap", "EMA20", "SMA50"},
        "OPTIONAL": {"DebtEquity", "PE"},
        "NOT_USED": {"MACD"}
    },
    "WEALTH_QUALITY_ON_SALE": {
        "REQUIRED": {"Close", "ROE", "ROCE", "High52W", "DropPct", "PE", "MarketCap", "SMA200"},
        "OPTIONAL": {"PromoterPledge"},
        "NOT_USED": {"ADX"}
    },
    "WEALTH_OPPORTUNISTIC": {
        "REQUIRED": {"Close", "RSI", "VolumeRatio", "SMA50", "MarketCap"},
        "OPTIONAL": {"ROE", "ROCE"},
        "NOT_USED": {"AuditorFlags"}
    },
    "WEALTH_TECHNICAL_OVERLAY": {
        "REQUIRED": {"Close", "Open", "High", "Low", "Volume", "SMA50", "SMA200", "EMA20", "ATR"},
        "OPTIONAL": {"RSI", "ADX"},
        "NOT_USED": {"ROE", "ROCE"}
    }
}

def validate_manifest_against_contract(scanner_name: str, manifest: List[Dict[str, str]], path_name: str = None) -> Tuple[bool, List[str], Dict[str, int]]:
    """
    Validates a decision manifest against the path-scoped contract.
    Invariant: REQUIRED_FOR_PATH inputs must be present and valid in manifest.
    Returns: (is_valid, list_of_missing_or_invalid_fields, stats_dict)
    """
    contract_key = path_name if (path_name and path_name in SCANNER_INPUT_CONTRACTS) else scanner_name
    contract = SCANNER_INPUT_CONTRACTS.get(contract_key)
    if not contract:
        return True, [], {"expected": 0, "captured": len(manifest), "valid": len(manifest), "missing": 0}

    required_fields = contract["REQUIRED"]
    captured_names = {entry.get("name") for entry in manifest}
    valid_names = {entry.get("name") for entry in manifest if entry.get("valid", False) is True}

    missing_fields = [f for f in required_fields if f not in valid_names]

    stats = {
        "expected_required": len(required_fields),
        "captured_total": len(manifest),
        "valid_required": len(required_fields) - len(missing_fields),
        "missing_required": len(missing_fields),
        "uncaptured_count": len(missing_fields)
    }

    is_valid = (len(missing_fields) == 0)
    return is_valid, missing_fields, stats
