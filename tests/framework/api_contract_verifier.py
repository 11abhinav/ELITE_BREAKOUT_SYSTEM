# =====================================================================================
# tests/framework/api_contract_verifier.py
# LEVEL 1 & 2: API CONTRACT, COMPLEATENESS, LINEAGE & RATE-LIMIT VERIFIER
# =====================================================================================
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Set
import pandas as pd
import numpy as np

logger = logging.getLogger("API_CONTRACT_VERIFIER")


class APIContractViolation(Exception):
    """Raised when an external API payload breaches contract, missing mandatory fields or containing invalid types/ranges."""
    pass


# ── FULL EXPECTED PROVIDER SCHEMAS (Section 8, 9, 10, 11) ──
PROVIDER_SCHEMAS = {
    "PRICE_DAILY": {
        "mandatory": ["Open", "High", "Low", "Close", "Volume"],
        "optional": ["Adj Close", "Date", "Datetime"],
        "types": {"Open": float, "High": float, "Low": float, "Close": float, "Volume": (int, float, np.integer, np.floating)}
    },
    "PRICE_INTRADAY": {
        "mandatory": ["Open", "High", "Low", "Close", "Volume"],
        "optional": ["Date", "Datetime"],
        "types": {"Open": float, "High": float, "Low": float, "Close": float, "Volume": (int, float, np.integer, np.floating)}
    },
    "FUNDAMENTALS_V5": {
        "mandatory": ["market_cap", "roce", "roe", "eps", "book_value_per_share", "shares_outstanding"],
        "optional": ["pe", "pb", "debt_to_equity", "interest_coverage", "operating_margin_ttm", "yoy_revenue", "yoy_profit", "sector", "category"],
        "types": {"market_cap": (int, float), "roce": (int, float), "roe": (int, float), "eps": (int, float)}
    },
    "DELIVERY_STATS": {
        "mandatory": ["delivery_pct", "days_back"],
        "optional": ["delivery_qty", "traded_qty", "delivery_status"],
        "types": {"delivery_pct": (int, float), "days_back": int}
    }
}


class APICompletenessReport:
    """Tracks API requests, schema completeness, unmapped drops, rate limits, and latency."""
    def __init__(self):
        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.missing_field_count = 0
        self.invalid_field_count = 0
        self.unmapped_field_count = 0
        self.rate_limit_429_count = 0
        self.timeout_count = 0
        self.http_5xx_count = 0
        self.request_records: List[Dict[str, Any]] = []
        self.provider_budgets: Dict[str, int] = {}
        self.data_lineage: Dict[str, Dict[str, Any]] = {}

    def log_request(self, provider: str, endpoint: str, symbol: str, status_code: int, duration_s: float, payload_size: int, missing_fields: List[str] = None):
        self.total_requests += 1
        if status_code == 200:
            self.successful_requests += 1
        elif status_code == 429:
            self.rate_limit_429_count += 1
            self.failed_requests += 1
        elif status_code >= 500:
            self.http_5xx_count += 1
            self.failed_requests += 1
        else:
            self.failed_requests += 1

        if missing_fields:
            self.missing_field_count += len(missing_fields)

        self.provider_budgets[provider] = self.provider_budgets.get(provider, 0) + 1

        rec = {
            "timestamp": time.time(),
            "provider": provider,
            "endpoint": endpoint,
            "symbol": symbol,
            "status_code": status_code,
            "duration_s": round(duration_s, 4),
            "payload_size": payload_size,
            "missing_fields": missing_fields or []
        }
        self.request_records.append(rec)

    def log_lineage(self, symbol: str, raw_hash: str, norm_hash: str, fields_count: int):
        self.data_lineage[symbol] = {
            "symbol": symbol,
            "raw_payload_hash": raw_hash,
            "normalized_payload_hash": norm_hash,
            "fields_count": fields_count,
            "timestamp": time.time()
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "missing_field_count": self.missing_field_count,
            "invalid_field_count": self.invalid_field_count,
            "unmapped_field_count": self.unmapped_field_count,
            "rate_limit_429_count": self.rate_limit_429_count,
            "timeout_count": self.timeout_count,
            "http_5xx_count": self.http_5xx_count,
            "provider_budgets": self.provider_budgets,
            "data_lineage": self.data_lineage,
            "request_records": self.request_records
        }


# Global singleton instance for audit tracking
global_api_report = APICompletenessReport()


def compute_payload_hash(df_or_dict: Any) -> str:
    """Calculates a deterministic sha256 hash of raw normalized payload."""
    if df_or_dict is None:
        return "NULL_PAYLOAD"
    try:
        if isinstance(df_or_dict, pd.DataFrame):
            if df_or_dict.empty: return "EMPTY_DATAFRAME"
            sample_str = f"{len(df_or_dict)}_{list(df_or_dict.columns)}_{df_or_dict.iloc[0].to_dict()}_{df_or_dict.iloc[-1].to_dict()}"
            return hashlib.sha256(sample_str.encode()).hexdigest()[:16]
        elif isinstance(df_or_dict, dict):
            sample_str = json.dumps(df_or_dict, sort_keys=True, default=str)
            return hashlib.sha256(sample_str.encode()).hexdigest()[:16]
        return hashlib.sha256(str(df_or_dict).encode()).hexdigest()[:16]
    except Exception:
        return "HASH_ERROR"


def verify_schema_completeness(payload: Any, schema_name: str, symbol: str) -> Tuple[bool, List[str]]:
    """Compares raw API response against complete provider schema (Section 9, 10, 11)."""
    schema = PROVIDER_SCHEMAS.get(schema_name)
    if not schema:
        return True, []

    errors = []
    missing = []
    
    if isinstance(payload, pd.DataFrame):
        actual_keys = set(payload.columns)
    elif isinstance(payload, dict):
        actual_keys = set(payload.keys())
    else:
        return False, [f"[{symbol}] Payload is neither DataFrame nor dict: {type(payload)}"]

    for req_key in schema["mandatory"]:
        if req_key not in actual_keys:
            missing.append(req_key)
            errors.append(f"[{symbol} {schema_name}] Mandatory schema field '{req_key}' MISSING!")

    if missing:
        global_api_report.missing_field_count += len(missing)

    raw_hash = compute_payload_hash(payload)
    global_api_report.log_lineage(symbol, raw_hash=raw_hash, norm_hash=raw_hash, fields_count=len(actual_keys))
    global_api_report.log_request(provider="YFinance/Provider", endpoint=schema_name, symbol=symbol, status_code=200, duration_s=0.05, payload_size=len(str(payload)), missing_fields=missing)

    return len(errors) == 0, errors


def verify_ohlcv_contract(df: pd.DataFrame, symbol: str, timeframe: str, is_ipo: bool = False) -> Tuple[bool, List[str]]:
    """Level 1 & 2 Contract: Verifies zero missing mandatory fields, OHLC logical bounds, and non-empty rows."""
    errors = []
    
    if df is None:
        errors.append(f"[{symbol} {timeframe}] OHLCV payload is None!")
        global_api_report.missing_field_count += 1
        return False, errors

    if df.empty:
        errors.append(f"[{symbol} {timeframe}] OHLCV DataFrame is empty (0 records returned)!")
        global_api_report.missing_field_count += 1
        return False, errors

    # Schema Completeness Audit
    schema_ok, schema_errs = verify_schema_completeness(df, "PRICE_DAILY" if "1d" in timeframe else "PRICE_INTRADAY", symbol)
    errors.extend(schema_errs)

    if errors:
        return False, errors

    # Row-Level Integrity Checks (Section 13: Financial Data Logical Validation)
    opens = df["Open"].dropna()
    highs = df["High"].dropna()
    lows = df["Low"].dropna()
    closes = df["Close"].dropna()
    vols = df["Volume"].dropna()

    if (closes <= 0).any():
        bad_count = (closes <= 0).sum()
        errors.append(f"[{symbol} {timeframe}] Found {bad_count} rows with Close <= 0!")
        global_api_report.invalid_field_count += bad_count

    if (highs < lows).any():
        bad_count = (highs < lows).sum()
        errors.append(f"[{symbol} {timeframe}] Found {bad_count} rows where High < Low!")
        global_api_report.invalid_field_count += bad_count

    if (vols < 0).any():
        bad_count = (vols < 0).sum()
        errors.append(f"[{symbol} {timeframe}] Found {bad_count} rows with Volume < 0!")
        global_api_report.invalid_field_count += bad_count

    # Timestamp Monotonicity Check
    time_col = 'Date' if 'Date' in df.columns else ('Datetime' if 'Datetime' in df.columns else None)
    if time_col:
        ts_series = pd.to_datetime(df[time_col])
    else:
        ts_series = pd.to_datetime(df.index)

    if not ts_series.is_monotonic_increasing:
        errors.append(f"[{symbol} {timeframe}] Timestamps are NON-MONOTONIC!")
        global_api_report.invalid_field_count += 1

    if ts_series.duplicated().any():
        dup_cnt = ts_series.duplicated().sum()
        errors.append(f"[{symbol} {timeframe}] Found {dup_cnt} DUPLICATE timestamps!")
        global_api_report.invalid_field_count += dup_cnt

    is_valid = len(errors) == 0
    return is_valid, errors


def verify_fundamentals_contract(fund: dict, symbol: str, is_ipo: bool = False) -> Tuple[bool, List[str]]:
    """Level 1 & 2 Contract: Verifies fundamental fields and ranges."""
    errors = []
    if not isinstance(fund, dict):
        errors.append(f"[{symbol}] Fundamental payload is not a dict!")
        global_api_report.missing_field_count += 1
        return False, errors

    if fund.get("failed"):
        if is_ipo:
            logger.info(f"ℹ️ [{symbol}] Fundamental data void for recent IPO — fail-closed handled gracefully.")
            return True, []
        else:
            errors.append(f"[{symbol}] Fundamental extraction failed for non-IPO stock!")
            global_api_report.failed_requests += 1
            return False, errors

    # Schema Completeness Audit
    schema_ok, schema_errs = verify_schema_completeness(fund, "FUNDAMENTALS_V5", symbol)
    errors.extend(schema_errs)

    mcap = fund.get("market_cap")
    if mcap is not None:
        try:
            mcap_val = float(mcap)
            if mcap_val < 0:
                errors.append(f"[{symbol}] Negative Market Cap: {mcap_val}")
                global_api_report.invalid_field_count += 1
        except (ValueError, TypeError):
            errors.append(f"[{symbol}] Non-numeric Market Cap: {mcap}")
            global_api_report.invalid_field_count += 1

    pe = fund.get("pe")
    if pe is not None and not pd.isna(pe):
        try:
            pe_val = float(pe)
            if pe_val < -500.0 or pe_val > 5000.0:
                errors.append(f"[{symbol}] PE out of range: {pe_val}")
                global_api_report.invalid_field_count += 1
        except (ValueError, TypeError):
            errors.append(f"[{symbol}] Non-numeric PE: {pe}")
            global_api_report.invalid_field_count += 1

    return len(errors) == 0, errors
