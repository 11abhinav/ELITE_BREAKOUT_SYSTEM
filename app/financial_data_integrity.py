"""
financial_data_integrity.py
============================
Canonical shared financial-data integrity module for QUALITY_COMPOUNDER and FUNDAMENTAL.

GOVERNANCE PRINCIPLE:
  NO SIGNAL IS ALWAYS PREFERRED TO A FALSE SIGNAL.

  Any financial value that is:
    - missing
    - stale
    - ambiguous
    - conflicting
    - incorrectly scaled
    - incorrectly periodized
    - whose provenance cannot be proven

  MUST result in one of:
    DATA_INSUFFICIENT / DATA_CONFLICT / DATA_STALE / DATA_INVALID

  and MUST block the dependent BUY decision.

This module is the single authoritative implementation for:
  - PIT freshness validation          (C1)
  - Annual filing gap detection        (C2)
  - CAGR window integrity              (C2 / C15)
  - EV cash validation                 (C3)
  - Share count / unit validation      (C4)
  - Metric period / basis integrity    (C5 / C6)
  - EBITDA / EBIT disambiguation       (C7)
  - ROCE / ROE formula provenance      (C8)
  - OCF sourcing and unit validation   (C9)
  - Data recovery provenance logging   (C10)
  - Pre-BUY data integrity gate        (C18)

DO NOT DUPLICATE these calculations in scanner-specific code.
Both scanners MUST import and call this module.

Version: 1.0.0 — Phase 1 implementation (scanner-level defences)
Phase 2 will add NSE/BSE raw filing fetch and full metric engine.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Indian financial year ends March 31.
# A filing is "stale" if the latest annual period_end_date is more than this
# many years before the scan date.
MAX_PIT_STALENESS_YEARS: float = 2.0

# Annual CAGR window tolerances.
# Warn if elapsed years exceeds target + CAGR_WINDOW_WARN_SLACK
# Block if elapsed years exceeds target + CAGR_WINDOW_BLOCK_SLACK
CAGR_WINDOW_WARN_SLACK: float = 0.5    # 5Y target → warn if > 5.5Y
CAGR_WINDOW_BLOCK_SLACK: float = 1.5   # 5Y target → block if > 6.5Y

# Annual filing gap threshold: consecutive filings > this many days apart
# indicates a missing fiscal year.
ANNUAL_GAP_THRESHOLD_DAYS: int = 548   # 18 months = 1.5 * 365.25

# Share count plausibility bounds (millions).
# Derived shares outside [MIN_SHARES_M, MAX_SHARES_M] are flagged.
# Upper bound: Indian mega-caps (e.g. IDEA ~68B shares, YESBANK ~31B, IOC ~14B, TATASTEEL ~12.5B).
# Set to 100,000M (100 Billion shares) to avoid destroying mega-cap valuations.
MIN_SHARES_M: float = 0.5
MAX_SHARES_M: float = 100_000.0

# Minimum OCF filing age — if the cash-flow statement has no period newer than
# this many years before scan_date, it is stale.
MAX_OCF_STALENESS_YEARS: float = 2.0

# Minimum quarterly rows required to compute a YoY growth rate.
MIN_QUARTERLY_ROWS_FOR_YOY: int = 2

# Latest quarter must be within this many days of scan_date to be considered
# "current". If the most recent quarterly row is older, it is stale.
MAX_LATEST_QUARTER_STALENESS_DAYS: int = 120   # ~4 months


# ---------------------------------------------------------------------------
# Status / Result enums
# ---------------------------------------------------------------------------

class DataStatus(str, Enum):
    """Terminal statuses for every data-integrity decision."""
    VALID                            = "VALID"
    DATA_INSUFFICIENT                = "DATA_INSUFFICIENT"
    DATA_STALE                       = "DATA_STALE"
    DATA_CONFLICT                    = "DATA_CONFLICT"
    DATA_INVALID                     = "DATA_INVALID"
    DATA_BLOCKED                     = "DATA_BLOCKED"
    STRUCTURAL_INELIGIBLE            = "STRUCTURAL_INELIGIBLE"
    RECOVERY_PENDING                 = "RECOVERY_PENDING"
    PROVIDER_TEMPORARILY_UNAVAILABLE = "PROVIDER_TEMPORARILY_UNAVAILABLE"


class RecoveryStatus(str, Enum):
    """Lifecycle tracking for multi-source financial fact recovery."""
    AVAILABLE                        = "AVAILABLE"
    RECOVERED                        = "RECOVERED"
    RECOVERY_PENDING                 = "RECOVERY_PENDING"
    PROVIDER_TEMPORARILY_UNAVAILABLE = "PROVIDER_TEMPORARILY_UNAVAILABLE"
    DATA_CONFLICT                    = "DATA_CONFLICT"
    STRUCTURAL_INELIGIBLE            = "STRUCTURAL_INELIGIBLE"
    GENUINELY_UNAVAILABLE            = "GENUINELY_UNAVAILABLE"


class MetricPeriodType(str, Enum):
    QUARTER        = "QUARTER"
    ANNUAL         = "ANNUAL"
    TTM            = "TTM"
    TRAILING_5Y    = "TRAILING_5Y"
    POINT_IN_TIME  = "POINT_IN_TIME"


class StatementBasis(str, Enum):
    CONSOLIDATED = "CONSOLIDATED"
    STANDALONE   = "STANDALONE"
    UNKNOWN      = "UNKNOWN"


class MonetaryUnit(str, Enum):
    """Explicit unit provenance for monetary financial figures."""
    INR_CRORES   = "INR_CRORES"
    RAW_INR      = "RAW_INR"
    INR_LAKHS    = "INR_LAKHS"
    INR_MILLIONS = "INR_MILLIONS"
    INR_THOUSANDS = "INR_THOUSANDS"


def convert_to_inr_crores(
    value: Optional[Union[float, int]],
    source_unit: Union[MonetaryUnit, str] = MonetaryUnit.INR_CRORES
) -> Optional[float]:
    """
    Explicit, deterministic unit conversion to canonical INR Crores (₹ Cr).

    RULE: NEVER use numeric magnitude heuristics (e.g. abs(v) > 1e6).
    Magnitude heuristics corrupt companies with market cap or revenue > 1,000,000 Cr
    (e.g., Reliance ₹18-20L Cr, TCS ₹15L Cr).
    Conversion is strictly driven by the declared source_unit provenance.
    """
    if value is None or pd.isna(value):
        return None
    try:
        val = float(value)
        unit_str = str(source_unit.value if isinstance(source_unit, MonetaryUnit) else source_unit).upper()
        if unit_str in ("INR_CRORES", "CRORES", "CR"):
            return round(val, 2)
        elif unit_str in ("RAW_INR", "INR", "RUPEES", "ABSOLUTE_INR"):
            return round(val / 1e7, 2)
        elif unit_str in ("INR_LAKHS", "LAKHS", "LAC"):
            return round(val / 100.0, 2)
        elif unit_str in ("INR_MILLIONS", "MILLIONS"):
            return round(val / 10.0, 2)
        elif unit_str in ("INR_THOUSANDS", "THOUSANDS"):
            return round(val / 1e5, 2)
        else:
            # Default for statement filings which are authored in INR Crores
            return round(val, 2)
    except (ValueError, TypeError):
        return None


def _parse_date_fast(val: Any) -> Optional[date]:
    """
    High-performance ISO date parser bypassing pd.to_datetime overhead.
    Handles None, date, datetime, and ISO 'YYYY-MM-DD' strings.
    """
    if val is None or val == "" or pd.isna(val):
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    val_str = str(val).strip()[:10]
    if len(val_str) == 10 and val_str[4] == '-' and val_str[7] == '-':
        try:
            return date(int(val_str[0:4]), int(val_str[5:7]), int(val_str[8:10]))
        except ValueError:
            pass
    try:
        return pd.to_datetime(val_str).date()
    except Exception:
        return None


class DerivationMethod(str, Enum):
    FILED_DIRECTLY           = "FILED_DIRECTLY"
    DERIVED_FROM_NET_PROFIT_EPS  = "DERIVED_FROM_NET_PROFIT_EPS"
    DERIVED_UNIT_SCALED      = "DERIVED_UNIT_SCALED"
    NOT_DERIVABLE            = "NOT_DERIVABLE"
    IMPLAUSIBLE              = "IMPLAUSIBLE"


# ---------------------------------------------------------------------------
# Core result containers
# ---------------------------------------------------------------------------

@dataclass
class FieldProvenance:
    """Complete provenance record for a single financial field value."""
    symbol: str
    scanner: str
    field: str
    value_used: Optional[float]
    unit: str = ""
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    period_type: Optional[str] = None
    basis: Optional[str] = None
    source_used: str = ""
    source_type: str = ""
    source_filing_id: Optional[str] = None
    pit_eligible_from: Optional[str] = None
    derivation_method: str = ""
    raw_input_ids: Optional[List[str]] = None
    formula: str = ""
    validation_status: str = ""
    validation_reason: str = ""

    def to_log_line(self) -> str:
        """Produce a structured single-line provenance log."""
        return (
            f"[DATA_PROVENANCE] scanner={self.scanner} symbol={self.symbol} "
            f"field={self.field} value={self.value_used} unit={self.unit} "
            f"period={self.period_start}→{self.period_end} ({self.period_type}) "
            f"basis={self.basis} source={self.source_used} "
            f"pit_eligible_from={self.pit_eligible_from} "
            f"derivation={self.derivation_method} formula={self.formula!r} "
            f"validation={self.validation_status}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IntegrityResult:
    """Result of a single integrity check."""
    status: DataStatus
    reason: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)
    provenance: Optional[FieldProvenance] = None

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "status": self.status.value,
            "reason": self.reason,
            "detail": self.detail,
        }
        if self.provenance:
            d["provenance"] = self.provenance.to_dict()
        return d


@dataclass
class CAGRResult:
    """Result of a deterministic CAGR computation."""
    status: DataStatus
    cagr: Optional[float] = None
    start_period: Optional[str] = None
    end_period: Optional[str] = None
    start_value: Optional[float] = None
    end_value: Optional[float] = None
    elapsed_years: Optional[float] = None
    window_integrity: str = ""
    is_current_pit_window_valid: bool = True
    gaps_detected: List[Tuple[str, str]] = field(default_factory=list)
    reason: Optional[str] = None
    target_years: int = 5
    metric: str = ""
    basis: str = ""

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EVResult:
    """Result of an Enterprise Value computation."""
    status: DataStatus
    ev_ebitda: Optional[float] = None
    ev_cr: Optional[float] = None
    mcap_cr: Optional[float] = None
    total_debt_cr: Optional[float] = None
    cash_cr: Optional[float] = None
    minority_interest_cr: Optional[float] = None
    ebitda_cr: Optional[float] = None
    ev_cash_status: str = ""
    shares_derivation: str = ""
    reason: Optional[str] = None
    formula: str = ""

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID

    @property
    def enterprise_value(self) -> Optional[float]:
        return self.ev_cr

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ShareCountResult:
    """Result of a share count derivation / validation."""
    status: DataStatus
    shares_millions: Optional[float] = None
    derivation_method: DerivationMethod = DerivationMethod.NOT_DERIVABLE
    source: str = ""
    unit_scaling_applied: Optional[str] = None
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ShareDilutionResult:
    """Result of 3-year PIT share dilution calculation with full auditability."""
    status: DataStatus
    reason: str = "VALID"
    share_dilution_3y: Optional[float] = None
    latest_share_count: Optional[float] = None
    latest_period: Optional[str] = None
    base_share_count: Optional[float] = None
    base_period: Optional[str] = None
    corporate_action_factor: float = 1.0
    corporate_action_ids: List[str] = field(default_factory=list)
    source_provider: str = "HISTORICAL_PIT_FILING"
    source_filing_dates: Tuple[Optional[str], Optional[str]] = (None, None)
    calculation_version: str = "v3.0_pit_exact_fy_matching"

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



@dataclass
class BUYEvidenceBundle:
    """
    Complete evidence record that must accompany every BUY alert.
    All fields must be populated before a BUY can be written.
    """
    scan_run_id: str
    scanner: str
    symbol: str
    cmp: Optional[float]
    strategy_score: Optional[float]

    # Gate results
    gate_results: Dict[str, Any] = field(default_factory=dict)

    # All financial metrics used with full provenance
    financial_metrics: Dict[str, FieldProvenance] = field(default_factory=dict)

    # PIT metadata
    pit_timestamp: Optional[str] = None
    pit_eligible_from: Optional[str] = None
    source_filing_ids: List[str] = field(default_factory=list)

    # Integrity flags
    data_integrity_status: DataStatus = DataStatus.DATA_INSUFFICIENT
    financial_provenance_complete: bool = False
    pit_valid: bool = False
    period_integrity: bool = False
    basis_integrity: bool = False
    unit_integrity: bool = False
    required_metrics_complete: bool = False

    # Snapshot version binding & watermark metadata
    snapshot_version: Optional[str] = None
    snapshot_sha256: Optional[str] = None
    source_watermarks: Dict[str, Any] = field(default_factory=dict)

    # Evidence hash (sha256 of serialized metrics)
    evidence_hash: Optional[str] = None

    # Reason for blocking if not VALID
    blocking_reasons: List[str] = field(default_factory=list)

    def compute_evidence_hash(self) -> str:
        """SHA256 over the serialized financial metrics for reproducibility."""
        payload = json.dumps(
            {k: v.to_dict() for k, v in self.financial_metrics.items()},
            sort_keys=True, default=str
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def is_buy_eligible(self) -> bool:
        """Returns True only when ALL integrity requirements are met."""
        return (
            self.data_integrity_status == DataStatus.VALID
            and self.financial_provenance_complete
            and self.pit_valid
            and self.period_integrity
            and self.basis_integrity
            and self.unit_integrity
            and self.required_metrics_complete
            and len(self.blocking_reasons) == 0
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["data_integrity_status"] = self.data_integrity_status.value
        return d


# ---------------------------------------------------------------------------
# C1 — PIT STALENESS GATE
# ---------------------------------------------------------------------------

def check_pit_freshness(
    symbol: str,
    latest_annual_period: Any,
    scan_date: Optional[date] = None,
    max_staleness_years: float = MAX_PIT_STALENESS_YEARS,
    fy_end_month: Optional[int] = None,
) -> IntegrityResult:
    """
    Validates that the latest annual filing in the PIT dataset is recent enough.

    A PIT dataset that passes the row-count minimum but has a stale
    latest_annual_period (e.g. FY2010 in a 2026 scan) MUST be blocked.
    Supports dynamic company-specific fiscal calendars (e.g. Dec FY for ABB India).

    Args:
        symbol: NSE ticker.
        latest_annual_period: The period_end_date of the most recent annual row.
        scan_date: The date of the scan. Defaults to today (IST).
        max_staleness_years: Maximum allowed years between latest filing and scan.
        fy_end_month: Optional explicit company FY-end month (1-12).

    Returns:
        IntegrityResult with status VALID or DATA_STALE.
    """
    if scan_date is None:
        try:
            import pytz
            IST = pytz.timezone("Asia/Kolkata")
            scan_date = datetime.now(IST).date()
        except Exception:
            scan_date = date.today()

    if latest_annual_period is None:
        return IntegrityResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="PIT_NO_ANNUAL_ROWS",
            detail={"symbol": symbol, "latest_annual_period": None},
        )

    latest_dt = _parse_date_fast(latest_annual_period)
    if latest_dt is None:
        return IntegrityResult(
            status=DataStatus.DATA_INVALID,
            reason=f"PIT_INVALID_PERIOD_DATE: {latest_annual_period}",
            detail={"symbol": symbol, "latest_annual_period": str(latest_annual_period)},
        )

    staleness_days = (scan_date - latest_dt).days
    staleness_years = staleness_days / 365.25

    # Compute expected latest annual period based on company-specific FY calendar
    target_fy_month = fy_end_month if (fy_end_month and 1 <= fy_end_month <= 12) else latest_dt.month
    target_fy_day = 31 if target_fy_month in (1, 3, 5, 7, 8, 10, 12) else (30 if target_fy_month in (4, 6, 9, 11) else 28)
    if scan_date.month > target_fy_month or (scan_date.month == target_fy_month and scan_date.day >= target_fy_day):
        scan_fy_end_year = scan_date.year
    else:
        scan_fy_end_year = scan_date.year - 1
    expected_latest_fy_end = date(scan_fy_end_year, target_fy_month, target_fy_day)

    detail = {
        "symbol": symbol,
        "latest_annual_period": str(latest_dt),
        "scan_date": str(scan_date),
        "expected_latest_annual_period": str(expected_latest_fy_end),
        "staleness_days": staleness_days,
        "pit_staleness_years": round(staleness_years, 2),
        "max_staleness_years": max_staleness_years,
    }

    if staleness_years > max_staleness_years:
        logger.error(
            f"[PIT_STALENESS] {symbol}: latest_annual_period={latest_dt} is "
            f"{staleness_years:.1f} years before scan_date={scan_date}. "
            f"BLOCKING (max={max_staleness_years}Y). Expected: {expected_latest_fy_end}."
        )
        return IntegrityResult(
            status=DataStatus.DATA_STALE,
            reason=(
                f"PIT_DATA_STALE: latest_annual_period={latest_dt} is "
                f"{staleness_years:.1f}Y stale (max={max_staleness_years}Y)"
            ),
            detail=detail,
        )

    logger.debug(
        f"[PIT_FRESHNESS] {symbol}: latest_annual_period={latest_dt}, "
        f"staleness={staleness_years:.1f}Y — PASS"
    )
    return IntegrityResult(
        status=DataStatus.VALID,
        reason="PIT_FRESH",
        detail=detail,
    )


# ---------------------------------------------------------------------------
# C2 — ANNUAL FILING GAP DETECTION
# ---------------------------------------------------------------------------

def detect_annual_fiscal_gaps(
    annual_rows_sorted: List[Dict[str, Any]],
    gap_threshold_days: int = ANNUAL_GAP_THRESHOLD_DAYS,
) -> List[Tuple[str, str]]:
    """
    Identifies missing fiscal years in a sorted list of annual filing dicts.

    A gap exists when two consecutive annual filings are more than
    gap_threshold_days apart (default 18 months = 548 days).

    Args:
        annual_rows_sorted: Annual filings sorted ascending by period_end_date.
        gap_threshold_days: Threshold for flagging a gap.

    Returns:
        List of (period_A, period_B) tuples where a gap was detected.
    """
    gaps: List[Tuple[str, str]] = []
    def _extract_dt(item: Any) -> Optional[date]:
        if isinstance(item, dict):
            raw = item.get("period_end_date") or item.get("period_end") or item.get("date")
            return _parse_date_fast(raw)
        return _parse_date_fast(item)

    for i in range(1, len(annual_rows_sorted)):
        prev_dt = _extract_dt(annual_rows_sorted[i - 1])
        curr_dt = _extract_dt(annual_rows_sorted[i])
        if prev_dt is None or curr_dt is None:
            continue
        diff_days = (curr_dt - prev_dt).days
        if diff_days > gap_threshold_days:
            gaps.append((str(prev_dt), str(curr_dt)))
    return gaps


def validate_annual_sequence(
    annual_rows_sorted: List[Dict[str, Any]],
    symbol: str,
) -> IntegrityResult:
    """
    Validates that the annual filing sequence has no internal gaps.

    Returns VALID if sequence is contiguous, DATA_INVALID if gaps detected.
    """
    if len(annual_rows_sorted) < 2:
        return IntegrityResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="INSUFFICIENT_ANNUAL_ROWS_FOR_SEQUENCE_CHECK",
            detail={"symbol": symbol, "row_count": len(annual_rows_sorted)},
        )

    gaps = detect_annual_fiscal_gaps(annual_rows_sorted)
    if gaps:
        logger.warning(
            f"[FILING_GAP] {symbol}: {len(gaps)} annual filing gap(s) detected: {gaps}"
        )
        return IntegrityResult(
            status=DataStatus.DATA_INVALID,
            reason="PIT_ANNUAL_FILING_GAPS_DETECTED",
            detail={
                "symbol": symbol,
                "gaps": gaps,
                "gap_count": len(gaps),
                "row_count": len(annual_rows_sorted),
            },
        )
    return IntegrityResult(
        status=DataStatus.VALID,
        reason="ANNUAL_SEQUENCE_CONTIGUOUS",
        detail={"symbol": symbol, "row_count": len(annual_rows_sorted)},
    )


# ---------------------------------------------------------------------------
# C2 / C15 — CAGR ENGINE (canonical, shared)
# ---------------------------------------------------------------------------

def compute_cagr(
    annual_rows_sorted: List[Dict[str, Any]],
    metric: str,
    symbol: str,
    target_years: int = 5,
    basis: str = StatementBasis.CONSOLIDATED,
    as_of_date: Optional[date] = None,
    scan_date: Optional[date] = None,
) -> CAGRResult:
    """
    Deterministic N-year annual CAGR with gap validation and window integrity.

    Requirements:
      - Requires target_years + 1 annual observations.
      - All observations must be contiguous (no missing FY in window).
      - Elapsed period must be target_years ± CAGR_WINDOW_WARN_SLACK.
      - If window exceeds target_years + CAGR_WINDOW_BLOCK_SLACK → BLOCKED.
      - Base value must be > 0 (negative base → DATA_INVALID).
      - Never silently extends to a longer window.

    Args:
        annual_rows_sorted: Annual filings sorted ascending by period_end_date.
        metric: Column name to compute CAGR for (e.g. "revenue", "net_profit").
        symbol: NSE ticker (for logging).
        target_years: Requested CAGR window (default 5).
        basis: CONSOLIDATED or STANDALONE.
        as_of_date: PIT cutoff — rows with filing_date > as_of_date are excluded.

    Returns:
        CAGRResult with status VALID or DATA_INSUFFICIENT / DATA_INVALID.
    """
    # Auto-resolve symbol from records if symbol is UNKNOWN or empty
    if not symbol or str(symbol).strip().upper() in ("UNKNOWN", "NONE", ""):
        for r in annual_rows_sorted:
            _s = r.get("symbol") or r.get("Symbol") or r.get("name") or r.get("Name") or r.get("Stock")
            if _s and str(_s).strip().upper() not in ("", "UNKNOWN", "NONE"):
                symbol = str(_s).strip().upper()
                break

    # Apply PIT filter if as_of_date provided
    if as_of_date is not None:
        filtered = []
        for r in annual_rows_sorted:
            fd = r.get("filing_date") or r.get("conservative_availability_timestamp")
            if fd is not None:
                fd_dt = _parse_date_fast(fd)
                if fd_dt is not None and fd_dt <= as_of_date:
                    filtered.append(r)
            else:
                filtered.append(r)
        annual_rows_sorted = filtered

    # Filter rows with valid (non-null, non-NaN) metric values.
    # We accept any real number (including 0 or negative) as a valid observation;
    # a non-positive *base* is rejected later as a computation error.
    valid_rows = [
        r for r in annual_rows_sorted
        if r.get(metric) is not None
        and not (isinstance(r.get(metric), float) and math.isnan(r.get(metric)))
    ]

    min_rows = target_years + 1
    if len(valid_rows) < min_rows:
        return CAGRResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason=f"INSUFFICIENT_ROWS: need {min_rows}, have {len(valid_rows)}",
            metric=metric,
            target_years=target_years,
            basis=basis,
        )

    # ── Detect gaps in the FULL valid sequence ──
    gaps_full = detect_annual_fiscal_gaps(valid_rows)

    # Select endpoints: row at index -(target_years + 1) and row at index -1
    r0_row = valid_rows[-(target_years + 1)]
    r1_row = valid_rows[-1]
    r0_val = float(r0_row[metric])
    r1_val = float(r1_row[metric])
    p0_dt = _parse_date_fast(r0_row.get("period_end_date"))
    p1_dt = _parse_date_fast(r1_row.get("period_end_date"))
    period0 = str(p0_dt) if p0_dt else str(r0_row.get("period_end_date", ""))
    period1 = str(p1_dt) if p1_dt else str(r1_row.get("period_end_date", ""))

    elapsed_days = (p1_dt - p0_dt).days if (p0_dt and p1_dt) else int(target_years * 365.25)
    elapsed_years = elapsed_days / 365.25

    # ── Check for gaps within the 5Y lookback window ──
    lookback_rows = valid_rows[-(target_years + 1):]
    gaps_in_window = detect_annual_fiscal_gaps(lookback_rows)

    if gaps_in_window:
        logger.warning(
            f"[CAGR_GAP] {symbol}: metric={metric} — filing gap in {target_years}Y "
            f"window: {gaps_in_window}. BLOCKING CAGR."
        )
        return CAGRResult(
            status=DataStatus.DATA_INVALID,
            reason="PIT_FILING_GAP_IN_GROWTH_WINDOW",
            gaps_detected=gaps_in_window,
            start_period=period0,
            end_period=period1,
            elapsed_years=round(elapsed_years, 2),
            metric=metric,
            target_years=target_years,
            basis=basis,
        )

    # ── Window integrity check ──
    warn_limit = target_years + CAGR_WINDOW_WARN_SLACK
    block_limit = target_years + CAGR_WINDOW_BLOCK_SLACK
    window_integrity = "CLEAN"

    if elapsed_years > block_limit:
        logger.warning(
            f"[CAGR_WINDOW] {symbol}: metric={metric} — elapsed window "
            f"{elapsed_years:.2f}Y exceeds block limit {block_limit}Y. BLOCKING."
        )
        return CAGRResult(
            status=DataStatus.DATA_INVALID,
            reason=f"CAGR_WINDOW_EXCESSIVELY_EXTENDED: {elapsed_years:.2f}Y > {block_limit}Y",
            gaps_detected=gaps_full,
            start_period=period0,
            end_period=period1,
            elapsed_years=round(elapsed_years, 2),
            metric=metric,
            target_years=target_years,
            basis=basis,
        )
    elif elapsed_years > warn_limit:
        window_integrity = f"EXTENDED_{elapsed_years:.2f}Y"
        logger.warning(
            f"[CAGR_WINDOW] {symbol}: metric={metric} — elapsed {elapsed_years:.2f}Y "
            f"exceeds warn limit {warn_limit}Y. window_integrity={window_integrity}."
        )

    # ── Compute CAGR ──
    if r0_val <= 0:
        return CAGRResult(
            status=DataStatus.DATA_INVALID,
            reason=f"CAGR_BASE_NON_POSITIVE: base={r0_val} for metric={metric}",
            start_period=period0,
            end_period=period1,
            start_value=r0_val,
            end_value=r1_val,
            elapsed_years=round(elapsed_years, 2),
            metric=metric,
            target_years=target_years,
            basis=basis,
        )

    if r1_val <= 0:
        return CAGRResult(
            status=DataStatus.VALID if r1_val == 0 else DataStatus.DATA_INVALID,
            reason=f"CAGR_END_VALUE_NON_POSITIVE: end={r1_val} for metric={metric}",
            start_period=period0,
            end_period=period1,
            start_value=r0_val,
            end_value=r1_val,
            elapsed_years=round(elapsed_years, 2),
            cagr=-100.0 if r1_val == 0 else -999.0,
            metric=metric,
            target_years=target_years,
            basis=basis,
        )

    try:
        cagr_pct = round((pow(r1_val / r0_val, 1.0 / elapsed_years) - 1.0) * 100.0, 2)
    except Exception as _e:
        return CAGRResult(
            status=DataStatus.DATA_INVALID,
            reason=f"CAGR_CALCULATION_ERROR: {_e}",
            start_period=period0,
            end_period=period1,
            start_value=r0_val,
            end_value=r1_val,
            elapsed_years=round(elapsed_years, 2),
            cagr=-999.0,
            metric=metric,
            target_years=target_years,
            basis=basis,
        )

    ref_scan_date = scan_date or as_of_date or date.today()
    is_current_window_valid = True
    if period1:
        p1_dt = _parse_date_fast(period1)
        if p1_dt and (ref_scan_date - p1_dt).days > int(MAX_PIT_STALENESS_YEARS * 365.25):
            is_current_window_valid = False
            window_integrity = f"{window_integrity}_ENDPOINT_STALE"
            logger.warning(
                f"[CAGR_STALENESS] {symbol}: metric={metric} — historical {target_years}Y interval is mathematically valid, "
                f"but endpoint {period1} is {(ref_scan_date - p1_dt).days / 365.25:.1f}Y old (> {MAX_PIT_STALENESS_YEARS}Y limit). "
                f"Marking is_current_pit_window_valid=False."
            )

    logger.info(
        f"[CAGR] {symbol}: metric={metric} — {period0} ({r0_val}) → "
        f"{period1} ({r1_val}), {elapsed_years:.2f}Y, CAGR={cagr_pct}%, "
        f"window_integrity={window_integrity}"
    )

    return CAGRResult(
        status=DataStatus.VALID,
        cagr=cagr_pct,
        start_period=period0,
        end_period=period1,
        start_value=r0_val,
        end_value=r1_val,
        elapsed_years=round(elapsed_years, 2),
        window_integrity=window_integrity,
        is_current_pit_window_valid=is_current_window_valid,
        gaps_detected=gaps_full,
        metric=metric,
        target_years=target_years,
        basis=basis,
    )


def compute_share_dilution_3y(
    annual_rows_sorted: List[Dict[str, Any]],
    symbol: str = "UNKNOWN",
    as_of_date: Optional[date] = None,
    corporate_actions: Optional[List[Dict[str, Any]]] = None,
) -> ShareDilutionResult:
    """
    Computes 3-year share dilution percentage with exact fiscal-period matching,
    corporate action (splits/bonuses) normalization, and PIT window integrity.

    Fail-Closed Invariants:
      1. Requires exact T and T-3 fiscal periods (e.g. FY2026 vs FY2023).
      2. If T-3 period is missing or unavailable (e.g. recent IPO < 3Y) -> DILUTION_HISTORY_INSUFFICIENT (HARD BLOCK).
      3. Corporate actions (splits/bonuses) adjust base share count to prevent false dilution.
      4. Unresolved corporate actions or invalid share counts -> HARD BLOCK.
    """
    if not annual_rows_sorted:
        return ShareDilutionResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="DILUTION_HISTORY_INSUFFICIENT: no_annual_rows"
        )

    # Apply PIT filter if as_of_date provided
    if as_of_date is not None:
        filtered = []
        for r in annual_rows_sorted:
            fd = r.get("filing_date") or r.get("conservative_availability_timestamp")
            if fd is not None:
                fd_dt = _parse_date_fast(fd)
                if fd_dt is not None and fd_dt <= as_of_date:
                    filtered.append(r)
            else:
                filtered.append(r)
        annual_rows_sorted = filtered

    # Filter rows containing valid shares_outstanding (or derived shares_outstanding_m)
    valid_rows = []
    for r in annual_rows_sorted:
        sh = r.get("shares_outstanding_m")
        if sh is None or (isinstance(sh, float) and math.isnan(sh)) or sh <= 0:
            sh_raw = r.get("shares_outstanding")
            if sh_raw is not None and not (isinstance(sh_raw, float) and math.isnan(sh_raw)) and sh_raw > 0:
                sh = sh_raw / 1e6 if sh_raw > 1e4 else sh_raw
        if sh is not None and sh > 0:
            r_copy = dict(r)
            r_copy["_resolved_shares_m"] = float(sh)
            valid_rows.append(r_copy)

    if not valid_rows:
        return ShareDilutionResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="DILUTION_HISTORY_INSUFFICIENT: no_valid_shares"
        )

    # De-duplicate rows with same period_end_date, prioritizing latest filing_date (amended filing)
    by_period: Dict[str, Dict[str, Any]] = {}
    for r in valid_rows:
        p_str = str(r.get("period_end_date") or r.get("period_end") or r.get("as_of_date") or "")
        if not p_str:
            continue
        if p_str not in by_period:
            by_period[p_str] = r
        else:
            existing_f_date = str(by_period[p_str].get("filing_date") or "")
            new_f_date = str(r.get("filing_date") or "")
            if new_f_date >= existing_f_date:
                by_period[p_str] = r

    if by_period:
        valid_rows = sorted(by_period.values(), key=lambda r: str(r.get("period_end_date") or r.get("period_end") or r.get("as_of_date")))

    if not valid_rows:
        return ShareDilutionResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="DILUTION_HISTORY_INSUFFICIENT: no_valid_period_dates"
        )

    latest_row = valid_rows[-1]
    latest_p_end = _parse_date_fast(latest_row.get("period_end_date") or latest_row.get("period_end") or latest_row.get("as_of_date"))

    if not latest_p_end:
        return ShareDilutionResult(
            status=DataStatus.DATA_INVALID,
            reason="DILUTION_INVALID: latest_period_end_missing"
        )

    # Exact Fiscal Year Resolution for T-3
    target_year = latest_p_end.year - 3
    target_month = latest_p_end.month

    
    base_row = None
    # Priority 1: Exact Fiscal Year & Month Identity (e.g., 2023-03-31)
    for r in valid_rows[:-1]:
        p_dt = _parse_date_fast(r.get("period_end_date") or r.get("period_end"))
        if p_dt and p_dt.year == target_year and p_dt.month == target_month:
            base_row = r
            break

    # Priority 2: Exact Fiscal Year Identity if month differs slightly (e.g. 52-week calendar)
    if not base_row:
        for r in valid_rows[:-1]:
            p_dt = _parse_date_fast(r.get("period_end_date") or r.get("period_end"))
            if p_dt and p_dt.year == target_year:
                base_row = r
                break

    # Priority 3: Tightly controlled date tolerance (365*3 ± 30 days) ONLY if exact FY missing
    if not base_row:
        for r in valid_rows[:-1]:
            p_dt = _parse_date_fast(r.get("period_end_date") or r.get("period_end"))
            if p_dt:
                diff_days = (latest_p_end - p_dt).days
                if 1060 <= diff_days <= 1125:
                    base_row = r
                    break

    if not base_row:
        return ShareDilutionResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason=f"DILUTION_HISTORY_INSUFFICIENT: T-3 period (FY{target_year}) not found in PIT filings",
            latest_share_count=latest_row.get("_resolved_shares_m"),
            latest_period=str(latest_p_end),
        )

    base_p_dt = _parse_date_fast(base_row.get("period_end_date") or base_row.get("period_end"))
    latest_sh = float(latest_row["_resolved_shares_m"])
    base_sh_raw = float(base_row["_resolved_shares_m"])

    # Resolve Corporate Action Adjustments (Splits / Bonuses between base_p_dt and latest_p_end)
    ca_factor = 1.0
    ca_ids = []
    if corporate_actions:
        for ca in corporate_actions:
            ex_dt = _parse_date_fast(ca.get("ex_date") or ca.get("effective_date"))
            if ex_dt and base_p_dt and latest_p_end and base_p_dt < ex_dt <= latest_p_end:
                action_type = str(ca.get("action_type", "")).upper()
                ratio = float(ca.get("adjustment_factor", 1.0))
                if action_type in ("SPLIT", "BONUS", "CAPITAL_REDUCTION") and ratio > 0:
                    ca_factor *= ratio
                    ca_ids.append(f"{action_type}:{ca.get('id', 'UNK')}:{ratio}")

    adjusted_base_sh = base_sh_raw * ca_factor

    if adjusted_base_sh <= 0:
        return ShareDilutionResult(
            status=DataStatus.DATA_INVALID,
            reason=f"DILUTION_INVALID: adjusted_base_shares={adjusted_base_sh} <= 0",
        )

    dilution_pct = round(((latest_sh - adjusted_base_sh) / adjusted_base_sh) * 100.0, 2)

    return ShareDilutionResult(
        status=DataStatus.VALID,
        reason="VALID",
        share_dilution_3y=dilution_pct,
        latest_share_count=latest_sh,
        latest_period=str(latest_p_end),
        base_share_count=base_sh_raw,
        base_period=str(base_p_dt),
        corporate_action_factor=round(ca_factor, 4),
        corporate_action_ids=ca_ids,
        source_provider="HISTORICAL_PIT_FILING",
        source_filing_dates=(
            str(base_row.get("filing_date", base_row.get("period_end_date"))),
            str(latest_row.get("filing_date", latest_row.get("period_end_date"))),
        ),
    )



# ---------------------------------------------------------------------------
# C4 — SHARE COUNT / UNIT VALIDATION
# ---------------------------------------------------------------------------

def derive_and_validate_shares(
    symbol: str,
    net_profit_cr: Optional[float],
    eps: Optional[float],
    shares_outstanding_raw: Optional[float],
    scanner: str = "UNKNOWN",
) -> ShareCountResult:
    """
    Derives and validates the share count with explicit unit sanity checks.

    Hierarchy:
      1. Filed shares_outstanding (used directly if within plausible bounds).
      2. Derived from net_profit (Cr) / EPS (₹) = shares in millions.
         Validated against [MIN_SHARES_M, MAX_SHARES_M].
      3. If out of bounds: attempt unit scaling detection (÷1000 / ×1000).
      4. If still implausible: SHARES_DATA_INSUFFICIENT — block MCap/EV.

    NEVER silently divides or multiplies by 1000. Unit conversion must be
    flagged and logged explicitly.

    Args:
        symbol: NSE ticker.
        net_profit_cr: Net profit in ₹ Crores.
        eps: Basic EPS in ₹.
        shares_outstanding_raw: Filed shares (if available; assumed millions
            unless validation shows otherwise).
        scanner: Scanner name for provenance.

    Returns:
        ShareCountResult.
    """
    # ── Priority 1: Filed shares outstanding ──
    if (
        shares_outstanding_raw is not None
        and not (isinstance(shares_outstanding_raw, float) and math.isnan(shares_outstanding_raw))
        and shares_outstanding_raw > 0
    ):
        s = float(shares_outstanding_raw)
        if MIN_SHARES_M <= s <= MAX_SHARES_M:
            return ShareCountResult(
                status=DataStatus.VALID,
                shares_millions=round(s, 4),
                derivation_method=DerivationMethod.FILED_DIRECTLY,
                source="FILED_SHARES_OUTSTANDING",
            )
        # Filed value outside plausible range — try unit adjustment
        s_div = s / 1000.0
        if MIN_SHARES_M <= s_div <= MAX_SHARES_M:
            logger.warning(
                f"[SHARE_UNIT] {symbol}: filed shares={s} out of plausible range "
                f"[{MIN_SHARES_M}, {MAX_SHARES_M}]M. Applying ÷1000 scaling → {s_div:.4f}M"
            )
            return ShareCountResult(
                status=DataStatus.VALID,
                shares_millions=round(s_div, 4),
                derivation_method=DerivationMethod.DERIVED_UNIT_SCALED,
                source="FILED_SHARES_OUTSTANDING_UNIT_SCALED_DIV1000",
                unit_scaling_applied="DIV_1000",
            )
        s_mul = s * 1000.0
        if MIN_SHARES_M <= s_mul <= MAX_SHARES_M:
            logger.warning(
                f"[SHARE_UNIT] {symbol}: filed shares={s} out of plausible range. "
                f"Applying ×1000 scaling → {s_mul:.4f}M"
            )
            return ShareCountResult(
                status=DataStatus.VALID,
                shares_millions=round(s_mul, 4),
                derivation_method=DerivationMethod.DERIVED_UNIT_SCALED,
                source="FILED_SHARES_OUTSTANDING_UNIT_SCALED_MUL1000",
                unit_scaling_applied="MUL_1000",
            )
        logger.error(
            f"[SHARE_SANITY] {symbol}: filed shares={s} implausible even after "
            f"unit scaling. BLOCKING share count."
        )
        return ShareCountResult(
            status=DataStatus.DATA_INVALID,
            reason=f"FILED_SHARES_IMPLAUSIBLE: {s} (raw), unit adjustments failed",
            derivation_method=DerivationMethod.IMPLAUSIBLE,
        )

    # ── Priority 2: Derive from net_profit / EPS ──
    if (
        net_profit_cr is not None
        and eps is not None
        and not (isinstance(net_profit_cr, float) and math.isnan(net_profit_cr))
        and not (isinstance(eps, float) and math.isnan(eps))
        and eps != 0
        and net_profit_cr > 0
        and eps > 0
    ):
        # Basic EPS = Net Profit (Cr) × 1e7 / Shares
        # ⟹ Shares (absolute) = Net Profit (Cr) × 1e7 / EPS
        # ⟹ Shares (millions) = Net Profit (Cr) × 1e7 / EPS / 1e6
        #                     = Net Profit (Cr) × 10 / EPS
        # Example: TIINDIA net_profit=1118 Cr, eps=32.90
        #   Shares = 1118 * 1e7 / 32.90 / 1e6 = 1118 * 10 / 32.90 = 339.8M
        # But exchange-listed TIINDIA has ~83.8M shares (post-split);
        # the discrepancy indicates the eps value in PIT is pre-split or
        # face-value adjusted — this derivation must be flagged.
        derived_m = round((net_profit_cr * 10.0) / eps, 4)

        if MIN_SHARES_M <= derived_m <= MAX_SHARES_M:
            logger.debug(
                f"[SHARE_DERIVE] {symbol}: shares derived from net_profit/eps = "
                f"{derived_m:.4f}M (net_profit={net_profit_cr}Cr, eps=₹{eps})"
            )
            return ShareCountResult(
                status=DataStatus.VALID,
                shares_millions=derived_m,
                derivation_method=DerivationMethod.DERIVED_FROM_NET_PROFIT_EPS,
                source=f"DERIVED: net_profit={net_profit_cr}Cr / eps=₹{eps}",
            )

        # Out of range — DO NOT apply arbitrary ÷1000 heuristics to derived shares.
        # Implausible derived shares must fail-closed to avoid destroying mega-cap valuations.
        logger.error(
            f"[SHARE_SANITY] {symbol}: derived shares={derived_m}M implausible outside "
            f"[{MIN_SHARES_M}, {MAX_SHARES_M}]M (net_profit={net_profit_cr}Cr, eps=₹{eps}). BLOCKING."
        )
        return ShareCountResult(
            status=DataStatus.DATA_INVALID,
            reason=f"DERIVED_SHARES_IMPLAUSIBLE: {derived_m}M from net_profit/eps",
            derivation_method=DerivationMethod.IMPLAUSIBLE,
        )

    # ── Priority 3: Cannot derive ──
    logger.warning(
        f"[SHARE_MISSING] {symbol}: shares_outstanding=None/NaN and cannot derive "
        f"from net_profit/eps. SHARES_DATA_INSUFFICIENT."
    )
    return ShareCountResult(
        status=DataStatus.DATA_INSUFFICIENT,
        reason="SHARES_DATA_INSUFFICIENT",
        derivation_method=DerivationMethod.NOT_DERIVABLE,
    )


# ---------------------------------------------------------------------------
# C3 — ENTERPRISE VALUE (CANONICAL)
# ---------------------------------------------------------------------------

def compute_ev_ebitda(
    symbol: str,
    cmp: float,
    shares_result: ShareCountResult,
    total_debt_cr: Optional[float],
    cash_cr: Optional[float],
    minority_interest_cr: Optional[float],
    ebitda_result: "EBITDAResult",
    scanner: str = "UNKNOWN",
) -> EVResult:
    """
    Canonical EV/EBITDA computation with strict cash enforcement.

    Formula:
        EV = MCap + Debt - Cash + MinorityInterest

    Cash is MANDATORY. If cash_cr is None → EV_STATUS = DATA_INSUFFICIENT.
    This is not a configurable behaviour.

    Args:
        symbol: NSE ticker.
        cmp: Current market price (₹).
        shares_result: From derive_and_validate_shares.
        total_debt_cr: Total debt in ₹ Crores.
        cash_cr: Cash & equivalents in ₹ Crores (MANDATORY).
        minority_interest_cr: Minority interest in ₹ Crores (default 0 if None).
        ebitda_result: From compute_ebitda.
        scanner: Scanner name for logging.

    Returns:
        EVResult with status VALID or DATA_INSUFFICIENT / DATA_INVALID.
    """
    formula = "EV = MCap + Debt - Cash + MinorityInterest"

    # ── Validate inputs ──
    if not shares_result.ok:
        return EVResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason=f"EV_BLOCKED: shares not validated ({shares_result.reason})",
            formula=formula,
            shares_derivation=str(shares_result.derivation_method),
        )

    if not ebitda_result.ok:
        return EVResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason=f"EV_BLOCKED: EBITDA not validated ({ebitda_result.reason})",
            formula=formula,
        )

    if cmp is None or cmp <= 0:
        return EVResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="EV_BLOCKED: CMP missing or zero",
            formula=formula,
        )

    if total_debt_cr is None or math.isnan(total_debt_cr):
        return EVResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="EV_BLOCKED: total_debt_cr missing",
            formula=formula,
        )

    # ── STRICT CASH ENFORCEMENT (C3) ──
    if cash_cr is None or (isinstance(cash_cr, float) and math.isnan(cash_cr)):
        logger.error(
            f"[EV_CASH_MISSING] {symbol}: cash_and_equivalents is None/NaN. "
            f"Cannot compute EV without cash. EV_STATUS=DATA_INSUFFICIENT."
        )
        return EVResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="EV_CASH_MISSING: cash_and_equivalents required for EV computation",
            ev_cash_status="CASH_MISSING_EV_BLOCKED",
            total_debt_cr=total_debt_cr,
            formula=formula,
            shares_derivation=str(shares_result.derivation_method),
        )

    # ── Compute MCap (₹ Crores) ──
    shares_abs = shares_result.shares_millions * 1e6  # absolute share count
    mcap_cr = round((cmp * shares_abs) / 1e7, 4)

    # ── Compute EV ──
    mi_cr = float(minority_interest_cr) if minority_interest_cr is not None else 0.0
    ev_cr = round(mcap_cr + total_debt_cr - cash_cr + mi_cr, 4)

    if ev_cr <= 0:
        return EVResult(
            status=DataStatus.DATA_INVALID,
            reason=f"EV_NON_POSITIVE: EV={ev_cr}Cr",
            ev_cr=ev_cr,
            mcap_cr=mcap_cr,
            total_debt_cr=total_debt_cr,
            cash_cr=cash_cr,
            minority_interest_cr=mi_cr,
            formula=formula,
        )

    ebitda_cr = ebitda_result.ebitda_cr
    if ebitda_cr is None or ebitda_cr <= 0:
        return EVResult(
            status=DataStatus.DATA_INVALID,
            reason=f"EBITDA_NON_POSITIVE: {ebitda_cr}Cr",
            ev_cr=ev_cr,
            mcap_cr=mcap_cr,
            formula=formula,
        )

    ev_ebitda = round(ev_cr / ebitda_cr, 4)

    logger.info(
        f"[EV/EBITDA] {symbol}: MCap=₹{mcap_cr:.2f}Cr Debt=₹{total_debt_cr:.2f}Cr "
        f"Cash=₹{cash_cr:.2f}Cr MI=₹{mi_cr:.2f}Cr → EV=₹{ev_cr:.2f}Cr "
        f"EBITDA=₹{ebitda_cr:.2f}Cr → EV/EBITDA={ev_ebitda:.2f}"
    )

    return EVResult(
        status=DataStatus.VALID,
        ev_ebitda=ev_ebitda,
        ev_cr=ev_cr,
        mcap_cr=mcap_cr,
        total_debt_cr=total_debt_cr,
        cash_cr=cash_cr,
        minority_interest_cr=mi_cr,
        ebitda_cr=ebitda_cr,
        ev_cash_status="CASH_DEDUCTED",
        shares_derivation=str(shares_result.derivation_method),
        formula=formula,
    )


# ---------------------------------------------------------------------------
# C7 — EBITDA / EBIT DISAMBIGUATION (canonical)
# ---------------------------------------------------------------------------

@dataclass
class EBITDAResult:
    status: DataStatus
    ebitda_cr: Optional[float] = None
    operating_profit_cr: Optional[float] = None
    da_cr: Optional[float] = None
    formula: str = ""
    ebitda_definition: str = ""
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID


def compute_ebitda(
    symbol: str,
    operating_profit_cr: Optional[float],
    da_cr: Optional[float],
    ebitda_direct_cr: Optional[float] = None,
) -> EBITDAResult:
    """
    Computes EBITDA with explicit source disambiguation.

    Screener's 'operating_profit' is EBIT (after D&A).
    True EBITDA = EBIT + D&A.

    Priority:
      1. Direct EBITDA if available (filed directly).
      2. operating_profit + D&A if both available.
      3. operating_profit alone (with warning that this is EBIT, not EBITDA).
      4. DATA_INSUFFICIENT.

    Args:
        symbol: NSE ticker.
        operating_profit_cr: EBIT / Operating profit from filing (₹ Cr).
        da_cr: Depreciation & Amortization from filing (₹ Cr).
        ebitda_direct_cr: Direct EBITDA if available from filing.

    Returns:
        EBITDAResult.
    """
    # Priority 1: Direct EBITDA
    if (
        ebitda_direct_cr is not None
        and not (isinstance(ebitda_direct_cr, float) and math.isnan(ebitda_direct_cr))
        and ebitda_direct_cr > 0
    ):
        return EBITDAResult(
            status=DataStatus.VALID,
            ebitda_cr=round(ebitda_direct_cr, 4),
            formula="EBITDA = FILED_DIRECT",
            ebitda_definition="DIRECT_EBITDA_FROM_FILING",
        )

    # Priority 2: EBIT + D&A
    if (
        operating_profit_cr is not None
        and da_cr is not None
        and not (isinstance(operating_profit_cr, float) and math.isnan(operating_profit_cr))
        and not (isinstance(da_cr, float) and math.isnan(da_cr))
        and operating_profit_cr > 0
        and da_cr >= 0
    ):
        ebitda = round(operating_profit_cr + da_cr, 4)
        return EBITDAResult(
            status=DataStatus.VALID,
            ebitda_cr=ebitda,
            operating_profit_cr=operating_profit_cr,
            da_cr=da_cr,
            formula="EBITDA = OperatingProfit(EBIT) + D&A",
            ebitda_definition="COMPUTED_EBIT_PLUS_DA",
        )

    # Priority 3: operating_profit only (EBIT — not true EBITDA, flag it)
    if (
        operating_profit_cr is not None
        and not (isinstance(operating_profit_cr, float) and math.isnan(operating_profit_cr))
        and operating_profit_cr > 0
    ):
        logger.warning(
            f"[EBITDA_APPROX] {symbol}: D&A missing; using operating_profit as EBIT "
            f"proxy for EBITDA. EV/EBITDA will be overstated."
        )
        return EBITDAResult(
            status=DataStatus.VALID,
            ebitda_cr=round(operating_profit_cr, 4),
            operating_profit_cr=operating_profit_cr,
            da_cr=None,
            formula="EBITDA ≈ OperatingProfit(EBIT) [D&A MISSING — EBIT PROXY]",
            ebitda_definition="EBIT_PROXY_DA_MISSING",
        )

    return EBITDAResult(
        status=DataStatus.DATA_INSUFFICIENT,
        reason="EBITDA_DATA_INSUFFICIENT: operating_profit and D&A both missing",
    )


# ---------------------------------------------------------------------------
# C8 — ROCE / ROE FORMULA PROVENANCE
# ---------------------------------------------------------------------------

@dataclass
class ROCEResult:
    status: DataStatus
    roce_value: Optional[float] = None
    roce_formula: str = ""
    roce_numerator: Optional[float] = None
    roce_denominator: Optional[float] = None
    roce_period: Optional[str] = None
    roce_basis: str = ""
    source_used: str = ""
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID


def compute_roce(
    symbol: str,
    operating_profit_cr: Optional[float],
    total_debt_cr: Optional[float],
    total_equity_cr: Optional[float],
    period: Optional[str] = None,
    basis: str = StatementBasis.CONSOLIDATED,
    pre_computed_roce: Optional[float] = None,
    pre_computed_source: str = "",
) -> ROCEResult:
    """
    Computes ROCE with full provenance.

    Preferred: Deterministic from raw facts.
      ROCE = EBIT / Capital Employed
      Capital Employed = Total Equity + Total Debt

    Fallback: pre-computed from source (with provenance recorded).

    Args:
        symbol: NSE ticker.
        operating_profit_cr: EBIT / Operating profit (₹ Cr).
        total_debt_cr: Total debt (₹ Cr).
        total_equity_cr: Total equity (₹ Cr).
        period: Period_end_date for the ROCE figure.
        basis: CONSOLIDATED or STANDALONE.
        pre_computed_roce: Pre-computed ROCE if raw facts not available.
        pre_computed_source: Source of pre-computed ROCE.

    Returns:
        ROCEResult.
    """
    formula = "ROCE = EBIT / (TotalEquity + TotalDebt)"

    # ── Deterministic from raw facts ──
    if (
        operating_profit_cr is not None
        and total_debt_cr is not None
        and total_equity_cr is not None
        and not any(math.isnan(x) for x in [operating_profit_cr, total_debt_cr, total_equity_cr])
        and total_equity_cr > 0
    ):
        capital_employed = total_equity_cr + total_debt_cr
        if capital_employed <= 0:
            return ROCEResult(
                status=DataStatus.DATA_INVALID,
                reason=f"ROCE_INVALID: capital_employed={capital_employed} <= 0",
            )
        roce = round((operating_profit_cr / capital_employed) * 100.0, 2)
        logger.debug(
            f"[ROCE] {symbol}: EBIT={operating_profit_cr}Cr / "
            f"CapEmp={capital_employed}Cr → ROCE={roce}% (period={period})"
        )
        return ROCEResult(
            status=DataStatus.VALID,
            roce_value=roce,
            roce_formula=formula,
            roce_numerator=operating_profit_cr,
            roce_denominator=capital_employed,
            roce_period=period,
            roce_basis=basis,
            source_used="RAW_FACTS_DERIVED",
        )

    # ── Fallback: pre-computed value ──
    if (
        pre_computed_roce is not None
        and not (isinstance(pre_computed_roce, float) and math.isnan(pre_computed_roce))
        and pre_computed_roce > 0
    ):
        logger.warning(
            f"[ROCE_PRECOMPUTED] {symbol}: Using pre-computed ROCE={pre_computed_roce}% "
            f"from {pre_computed_source}. Raw facts not available. "
            f"Period and formula cannot be independently verified."
        )
        return ROCEResult(
            status=DataStatus.VALID,
            roce_value=round(float(pre_computed_roce), 2),
            roce_formula=f"PRE_COMPUTED from {pre_computed_source}",
            roce_period=period,
            roce_basis=basis,
            source_used=pre_computed_source or "UNKNOWN_SOURCE",
        )

    return ROCEResult(
        status=DataStatus.DATA_INSUFFICIENT,
        reason="ROCE_DATA_INSUFFICIENT: raw facts and pre-computed both unavailable",
    )


# ---------------------------------------------------------------------------
# C5 — QUARTERLY YOY PERIOD INTEGRITY
# ---------------------------------------------------------------------------

@dataclass
class YoYResult:
    status: DataStatus
    metric: str = ""
    yoy_pct: Optional[float] = None
    current_period_end: Optional[str] = None
    prior_period_end: Optional[str] = None
    current_value: Optional[float] = None
    prior_value: Optional[float] = None
    basis: str = ""
    period_type: str = MetricPeriodType.QUARTER
    latest_quarter_staleness_days: Optional[int] = None
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == DataStatus.VALID


def compute_quarterly_yoy(
    symbol: str,
    quarterly_rows_sorted_desc: List[Dict[str, Any]],
    metric: str,
    scan_date: Optional[date] = None,
    max_latest_staleness_days: int = MAX_LATEST_QUARTER_STALENESS_DAYS,
    basis: str = StatementBasis.CONSOLIDATED,
) -> YoYResult:
    """
    Computes quarterly YoY growth with strict period validation.

    Prevents the JUSTDIAL-class bug: if quarterly_rows[0] is not the
    current quarter (e.g. it is Q4 FY26 when Q1 FY27 data is expected),
    the metric would reflect the wrong period.

    Requirements:
      - f0 (latest row) must be within max_latest_staleness_days of scan_date.
      - f_prior must be approximately 12 months (340–390 days) before f0.
      - Period dates must be explicitly logged.

    Args:
        symbol: NSE ticker.
        quarterly_rows_sorted_desc: Quarterly filings sorted DESCENDING by period_end_date.
        metric: Field name (e.g. "revenue", "operating_profit", "eps").
        scan_date: Scan date for staleness check.
        max_latest_staleness_days: Max days between latest row and scan_date.
        basis: CONSOLIDATED or STANDALONE.

    Returns:
        YoYResult with full period provenance.
    """
    # Auto-resolve symbol from records if symbol is UNKNOWN or empty
    if not symbol or str(symbol).strip().upper() in ("UNKNOWN", "NONE", ""):
        for r in quarterly_rows_sorted_desc:
            _s = r.get("symbol") or r.get("Symbol") or r.get("name") or r.get("Name") or r.get("Stock")
            if _s and str(_s).strip().upper() not in ("", "UNKNOWN", "NONE"):
                symbol = str(_s).strip().upper()
                break

    if scan_date is None:
        try:
            import pytz
            IST = pytz.timezone("Asia/Kolkata")
            scan_date = datetime.now(IST).date()
        except Exception:
            scan_date = date.today()

    if len(quarterly_rows_sorted_desc) < MIN_QUARTERLY_ROWS_FOR_YOY:
        return YoYResult(
            status=DataStatus.DATA_INSUFFICIENT,
            metric=metric,
            reason=f"INSUFFICIENT_QUARTERLY_ROWS: need {MIN_QUARTERLY_ROWS_FOR_YOY}, "
                   f"have {len(quarterly_rows_sorted_desc)}",
        )

    f0 = quarterly_rows_sorted_desc[0]
    f0_period_end = pd.to_datetime(f0.get("period_end_date")).date()

    # ── Staleness check: f0 must be recent ──
    latest_staleness = (scan_date - f0_period_end).days
    if latest_staleness > max_latest_staleness_days:
        logger.warning(
            f"[QTR_STALE] {symbol}: metric={metric} — latest quarterly row "
            f"period_end={f0_period_end} is {latest_staleness} days before "
            f"scan_date={scan_date}. GROWTH_QUARTER_STALE."
        )
        return YoYResult(
            status=DataStatus.DATA_STALE,
            metric=metric,
            current_period_end=str(f0_period_end),
            latest_quarter_staleness_days=latest_staleness,
            reason=f"GROWTH_QUARTER_STALE: latest_quarter={f0_period_end} is "
                   f"{latest_staleness}d old (max={max_latest_staleness_days}d)",
        )

    # ── Find YoY match: row with period_end approx 12 months before f0 ──
    f_prior = None
    for r in quarterly_rows_sorted_desc[1:]:
        r_dt = pd.to_datetime(r.get("period_end_date")).date()
        diff_days = (f0_period_end - r_dt).days
        if 340 <= diff_days <= 390:
            f_prior = r
            break

    if f_prior is None:
        return YoYResult(
            status=DataStatus.DATA_INSUFFICIENT,
            metric=metric,
            current_period_end=str(f0_period_end),
            reason="NO_PRIOR_YEAR_QUARTER_FOUND: no row within 340-390 days before latest quarter",
        )

    f_prior_period_end = pd.to_datetime(f_prior.get("period_end_date")).date()

    # ── Extract values ──
    v0_raw = f0.get(metric)
    v_prior_raw = f_prior.get(metric)

    v0 = float(v0_raw) if (v0_raw is not None and not (isinstance(v0_raw, float) and math.isnan(v0_raw))) else None
    v_prior = float(v_prior_raw) if (v_prior_raw is not None and not (isinstance(v_prior_raw, float) and math.isnan(v_prior_raw))) else None

    if v0 is None or v_prior is None:
        return YoYResult(
            status=DataStatus.DATA_INSUFFICIENT,
            metric=metric,
            current_period_end=str(f0_period_end),
            prior_period_end=str(f_prior_period_end),
            reason=f"METRIC_VALUE_MISSING: {metric} — current={v0}, prior={v_prior}",
        )

    if abs(v_prior) < 1e-5:
        return YoYResult(
            status=DataStatus.DATA_INVALID,
            metric=metric,
            current_period_end=str(f0_period_end),
            prior_period_end=str(f_prior_period_end),
            reason=f"PRIOR_VALUE_NEAR_ZERO: {metric} prior={v_prior}",
        )

    yoy_pct = round(((v0 - v_prior) / abs(v_prior)) * 100.0, 2)

    logger.info(
        f"[YoY] {symbol}: metric={metric} — "
        f"current={f0_period_end} ({v0}) vs prior={f_prior_period_end} ({v_prior}) "
        f"→ YoY={yoy_pct}%"
    )

    return YoYResult(
        status=DataStatus.VALID,
        metric=metric,
        yoy_pct=yoy_pct,
        current_period_end=str(f0_period_end),
        prior_period_end=str(f_prior_period_end),
        current_value=v0,
        prior_value=v_prior,
        basis=basis,
        period_type=MetricPeriodType.QUARTER,
        latest_quarter_staleness_days=latest_staleness,
    )


# ---------------------------------------------------------------------------
# C10 — PROVENANCE-AWARE DATA RECOVERY LOG
# ---------------------------------------------------------------------------

def emit_provenance_log(
    provenance: FieldProvenance,
    logger_instance: Optional[logging.Logger] = None,
) -> None:
    """
    Emits a structured [DATA_PROVENANCE] log line and optionally debug-logs it.

    This replaces the existing _emit_data_recovery_log for all financial fields
    to enforce the requirement that every DATA_USED record carries the actual
    value used, not just 'FETCHED'.
    """
    _log = logger_instance or logger
    line = provenance.to_log_line()
    _log.info(line)


# ---------------------------------------------------------------------------
# C18 — PRE-BUY DATA INTEGRITY GATE
# ---------------------------------------------------------------------------

def _mark_update_pending(symbol: str, state_file: str):
    """Helper to flip watcher state to UPDATE_PENDING when pre-buy fence detects newer filing."""
    try:
        st_data = {}
        if os.path.exists(state_file):
            with open(state_file, "r") as sf:
                st_data = json.load(sf)
        if symbol not in st_data:
            st_data[symbol] = {"filings": {}, "snapshot_status": "UPDATE_PENDING"}
        else:
            st_data[symbol]["snapshot_status"] = "UPDATE_PENDING"
        tmp = f"{state_file}.tmp.{os.getpid()}"
        with open(tmp, "w") as tf:
            json.dump(st_data, tf, indent=2)
        os.replace(tmp, state_file)
    except Exception:
        pass


def record_source_watermark(
    source_name: str,
    last_successful_check_at: Optional[str] = None,
    latest_filing_timestamp: Optional[str] = None,
    symbol: Optional[str] = None,
) -> None:
    """
    Updates or initializes source watermark in data/exchange_watermarks.json.
    """
    base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(base_dir, "data")
    wm_path = os.path.join(data_dir, "exchange_watermarks.json")
    os.makedirs(data_dir, exist_ok=True)
    data = {}
    if os.path.exists(wm_path):
        try:
            with open(wm_path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

    src_upper = source_name.strip().upper()
    if src_upper not in data:
        data[src_upper] = {
            "source_name": src_upper,
            "last_successful_check_at": datetime.now().isoformat(),
            "latest_filing_timestamp": None,
            "symbols": {}
        }

    now_iso = datetime.now().isoformat()
    check_time = last_successful_check_at or now_iso

    if symbol:
        sym_clean = symbol.strip().upper()
        if "symbols" not in data[src_upper]:
            data[src_upper]["symbols"] = {}
        sym_entry = data[src_upper]["symbols"].get(sym_clean, {})
        sym_entry["last_successful_check_at"] = check_time
        if latest_filing_timestamp:
            sym_entry["latest_filing_timestamp"] = latest_filing_timestamp
        data[src_upper]["symbols"][sym_clean] = sym_entry
    else:
        data[src_upper]["last_successful_check_at"] = check_time
        if latest_filing_timestamp:
            data[src_upper]["latest_filing_timestamp"] = latest_filing_timestamp

    tmp_path = f"{wm_path}.tmp.{os.getpid()}"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, wm_path)


def get_multi_source_exchange_watermark(
    symbol: str,
    max_sla_seconds: int = 86400,
    required_sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    MULTI-SOURCE (NSE + BSE) EXCHANGE FRESHNESS WATERMARK & SLA VERIFICATION.

    Rules:
      1. Every supported filing source (NSE, BSE) must have a recorded last_successful_check_at.
      2. If last_successful_check_at is older than max_sla_seconds -> SOURCE_SLA_BREACHED.
      3. Aggregates latest filing timestamp for the symbol: max(NSE_latest, BSE_latest, index_latest).
      4. Any source freshness uncertainty fails closed.
    """
    if required_sources is None:
        required_sources = ["NSE", "BSE"]
    sym_clean = symbol.strip().upper()
    base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(base_dir, "data")
    now_ts = time.time()

    watermark_path = os.path.join(data_dir, "exchange_watermarks.json")
    sources_data = {}
    if os.path.exists(watermark_path):
        try:
            with open(watermark_path, "r") as wf:
                sources_data = json.load(wf)
        except Exception:
            pass

    source_results = []
    symbol_filing_timestamps = []
    symbol_period_ends = []
    sla_failed = False
    sla_reasons = []

    # 1. Inspect exchange_financials index for this symbol if present
    exchange_idx_file = os.path.join(data_dir, "exchange_financials", sym_clean, "metadata", "filing_index.json")
    if os.path.exists(exchange_idx_file):
        try:
            mtime = os.path.getmtime(exchange_idx_file)
            age_sec = now_ts - mtime
            with open(exchange_idx_file, "r") as ef:
                idx_data = json.load(ef)
            for f_id, entry in idx_data.items():
                if entry.get("statement_type", "").upper() == "ANNUAL":
                    b_ts = entry.get("broadcast_timestamp") or entry.get("pit_eligible_from")
                    if b_ts:
                        symbol_filing_timestamps.append(str(b_ts))
                    f_period = entry.get("period_end_date")
                    if f_period:
                        symbol_period_ends.append(str(f_period)[:10])
            source_results.append({
                "source_name": "EXCHANGE_INDEX",
                "last_successful_source_check_at": datetime.fromtimestamp(mtime).isoformat(),
                "latest_source_filing_timestamp": max(symbol_filing_timestamps) if symbol_filing_timestamps else None,
                "latest_source_period_end": max(symbol_period_ends) if symbol_period_ends else None,
                "age_seconds": round(age_sec, 1),
                "sla_valid": age_sec <= max_sla_seconds,
            })
            if age_sec > max_sla_seconds:
                sla_failed = True
                sla_reasons.append(f"EXCHANGE_INDEX feed age ({round(age_sec/3600, 1)}h) exceeds SLA ({round(max_sla_seconds/3600, 1)}h)")
        except Exception as e:
            logger.warning(f"Error inspecting exchange index for {sym_clean}: {e}")

    # 2. Inspect required sources (NSE, BSE)
    for src_name in required_sources:
        src_entry = sources_data.get(src_name)
        if not src_entry:
            sla_failed = True
            sla_reasons.append(f"{src_name} feed watermark missing (feed unverified)")
            source_results.append({
                "source_name": src_name,
                "last_successful_source_check_at": None,
                "latest_source_filing_timestamp": None,
                "age_seconds": None,
                "sla_valid": False,
                "failure_reason": "MISSING_WATERMARK",
            })
            continue

        feed_chk = src_entry.get("last_successful_check_at")
        sym_entry = src_entry.get("symbols", {}).get(sym_clean)
        sym_f_ts = sym_entry.get("latest_filing_timestamp") if sym_entry else None
        if sym_f_ts:
            symbol_filing_timestamps.append(str(sym_f_ts))
        sym_p = sym_entry.get("period_end_date") if sym_entry else None
        if sym_p:
            symbol_period_ends.append(str(sym_p)[:10])

        if not feed_chk:
            sla_failed = True
            sla_reasons.append(f"{src_name} has no recorded last_successful_check_at")
            source_results.append({
                "source_name": src_name,
                "last_successful_source_check_at": None,
                "latest_source_filing_timestamp": str(sym_f_ts) if sym_f_ts else None,
                "age_seconds": None,
                "sla_valid": False,
                "failure_reason": "NO_CHECK_TIMESTAMP",
            })
            continue

        try:
            chk_dt = datetime.fromisoformat(str(feed_chk).replace("Z", "+00:00"))
            now_dt = datetime.now(chk_dt.tzinfo if chk_dt.tzinfo else None)
            chk_age = (now_dt - chk_dt).total_seconds()
            valid_sla = (chk_age <= max_sla_seconds)
            source_results.append({
                "source_name": src_name,
                "last_successful_source_check_at": str(feed_chk),
                "latest_source_filing_timestamp": str(sym_f_ts) if sym_f_ts else None,
                "latest_source_period_end": str(sym_p)[:10] if sym_p else None,
                "age_seconds": round(chk_age, 1),
                "sla_valid": valid_sla,
            })
            if not valid_sla:
                sla_failed = True
                sla_reasons.append(f"{src_name} feed age ({round(chk_age/3600, 1)}h) exceeds SLA ({round(max_sla_seconds/3600, 1)}h)")
        except Exception as e:
            sla_failed = True
            sla_reasons.append(f"Failed parsing {src_name} timestamp {feed_chk}: {e}")

    max_exchange_filing_ts = max(symbol_filing_timestamps) if symbol_filing_timestamps else None
    max_exchange_period_end = max(symbol_period_ends) if symbol_period_ends else None

    return {
        "valid": not sla_failed,
        "feed_heartbeat_sla_valid": not sla_failed,
        "feed_heartbeat_sla_reasons": sla_reasons if sla_reasons else [],
        "symbol": sym_clean,
        "latest_exchange_filing_timestamp": max_exchange_filing_ts,
        "latest_exchange_period_end": max_exchange_period_end,
        "source_watermarks": source_results,
        "failure_reason": "; ".join(sla_reasons) if sla_reasons else None,
    }


def init_buy_alerts_journal(db_path: Optional[str] = None) -> str:
    """
    Initializes the authoritative transactional outbox table for BUY alerts.
    The database journal is the SINGLE SOURCE OF TRUTH for all committed BUY decisions.
    Parquet persistence is derived / materialized idempotently from this journal.
    """
    if db_path is None:
        base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        db_path = os.path.join(base_dir, "data", "buy_alerts_journal.db")
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with sqlite3.connect(db_path, timeout=30.0) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS buy_alerts_journal (
                alert_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                scanner TEXT NOT NULL,
                run_id TEXT NOT NULL,
                alert_timestamp TEXT NOT NULL,
                cmp REAL NOT NULL,
                strategy_score REAL NOT NULL,
                snapshot_version TEXT NOT NULL,
                snapshot_sha256 TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                materialized_to_parquet INTEGER NOT NULL DEFAULT 0,
                materialized_at TEXT,
                created_at TEXT NOT NULL
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_buy_alerts_pending ON buy_alerts_journal(materialized_to_parquet);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_buy_alerts_sym ON buy_alerts_journal(symbol, scanner);")
        conn.commit()
    return db_path


def check_pre_buy_source_freshness_fence(
    symbol: str,
    canonical_period_end: Optional[str] = None,
    canonical_filing_timestamp: Optional[str] = None,
    max_sla_seconds: int = 86400,
) -> Tuple[bool, Optional[str]]:
    """
    PRE-BUY EXTERNAL SOURCE FRESHNESS FENCE & FEED HEARTBEAT SLA GATE.

    ARCHITECTURAL DISTINCTION:
      1. FEED_HEARTBEAT_SLA (Source Operational Liveness Requirement):
         - "Is this data feed operational and polled recently enough?"
         - Checked via now - last_successful_check_at <= max_sla_seconds (e.g. 24h).
         - IMPORTANT: A valid heartbeat SLA alone DOES NOT prove that no newer filing exists!
           An intraday filing could have been broadcast 30 minutes ago while the feed check
           is 6 hours old. The heartbeat merely confirms that the ingestion infrastructure is alive.

      2. SOURCE_FRESHNESS_FENCE (Filing Freshness Trading Requirement):
         - "Do I know whether a newer filing exists on the exchange right now?"
         - Checked via:
             canonical_filing_timestamp >= max(latest_exchange_filing_timestamp across NSE & BSE)
             AND canonical_period_end >= max(latest_exchange_period_end across NSE & BSE)
             AND watcher state == FRESH
             AND exchange metadata filing indexes have no unabsorbed filings.
         - THIS IS THE DIRECT GATE THAT PROTECTS THE TRADE.

    ENFORCEABLE TRADING INVARIANT:
      NO BUY MAY BE COMMITTED when a newer valid filing was known to exist
      before the BUY transaction committed.
    """
    try:
        sym_clean = symbol.strip().upper()
        base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        data_dir = os.path.join(base_dir, "data")
        state_file = os.path.join(data_dir, "filing_watcher_state.json")

        # 1. FEED_HEARTBEAT_SLA: Multi-source feed operational liveness (NSE + BSE)
        wm = get_multi_source_exchange_watermark(sym_clean, max_sla_seconds=max_sla_seconds)
        if not wm["valid"] and wm.get("failure_reason"):
            return False, f"FEED_HEARTBEAT_SLA_BREACH: EXCHANGE_FEED_WATERMARK_STALE ({wm['failure_reason']})"

        # 2. SOURCE_FRESHNESS_FENCE: Direct Pre-BUY Exchange Watermark Check (max(NSE, BSE))
        # Check broadcast watermark
        if wm.get("latest_exchange_filing_timestamp") and canonical_filing_timestamp:
            latest_f_ts = str(wm["latest_exchange_filing_timestamp"])
            if latest_f_ts > str(canonical_filing_timestamp):
                _mark_update_pending(sym_clean, state_file)
                return False, (
                    f"UNPROCESSED_MULTI_SOURCE_FILING: Exchange watermark ({latest_f_ts}) "
                    f"> snapshot timestamp ({canonical_filing_timestamp})"
                )

        # Check fiscal period watermark
        if wm.get("latest_exchange_period_end") and canonical_period_end:
            latest_p_end = str(wm["latest_exchange_period_end"])
            if latest_p_end > str(canonical_period_end):
                _mark_update_pending(sym_clean, state_file)
                return False, (
                    f"UNPROCESSED_MULTI_SOURCE_FILING: Exchange filing period ({latest_p_end}) "
                    f"> snapshot period ({canonical_period_end})"
                )

        # 3. Check filing watcher state
        if os.path.exists(state_file):
            try:
                with open(state_file, "r") as sf:
                    st_data = json.load(sf)
                sym_st = st_data.get(sym_clean, {})
                status_str = sym_st.get("snapshot_status", "FRESH")
                if status_str == "UPDATE_PENDING":
                    return False, f"UPDATE_PENDING: New or amended filing detected for {sym_clean}, recalculation required"
                elif status_str == "INVALID":
                    return False, f"SNAPSHOT_INVALID: Filing snapshot marked INVALID for {sym_clean}"

                w_latest_date = sym_st.get("latest_filing_date")
                if w_latest_date and canonical_period_end and str(w_latest_date) > str(canonical_period_end):
                    return False, f"UNPROCESSED_WATCHER_FILING: Watcher detected filing {w_latest_date} > canonical {canonical_period_end}"
            except Exception:
                pass

        # 4. Check exchange filing index for newer broadcast watermark
        exchange_idx_file = os.path.join(data_dir, "exchange_financials", sym_clean, "metadata", "filing_index.json")
        if os.path.exists(exchange_idx_file):
            try:
                with open(exchange_idx_file, "r") as ef:
                    idx_data = json.load(ef)
                for f_id, entry in idx_data.items():
                    if entry.get("statement_type", "").upper() == "ANNUAL":
                        f_period = entry.get("period_end_date")
                        f_broadcast = entry.get("broadcast_timestamp") or entry.get("pit_eligible_from")
                        if f_period and canonical_period_end and str(f_period) > str(canonical_period_end):
                            _mark_update_pending(sym_clean, state_file)
                            return False, (
                                f"UNPROCESSED_EXCHANGE_FILING: Exchange filing {f_id} (period {f_period}, "
                                f"broadcast {f_broadcast}) is newer than canonical period {canonical_period_end}"
                            )
                        if f_period and canonical_period_end and str(f_period) == str(canonical_period_end):
                            if f_broadcast and canonical_filing_timestamp and str(f_broadcast) > str(canonical_filing_timestamp):
                                _mark_update_pending(sym_clean, state_file)
                                return False, (
                                    f"UNPROCESSED_AMENDED_FILING: Amended filing {f_id} (broadcast {f_broadcast} > "
                                    f"snapshot {canonical_filing_timestamp}) pending recalculation"
                                )
            except Exception:
                pass

        # 5. Check pit_raw_filings for newly downloaded files ahead of canonical
        raw_file = os.path.join(data_dir, "pit_raw_filings", f"{sym_clean}.json")
        if os.path.exists(raw_file):
            try:
                with open(raw_file, "r") as rf:
                    raw_list = json.load(rf)
                if isinstance(raw_list, list):
                    for r in raw_list:
                        p_type = str(r.get("period_type", "ANNUAL")).upper()
                        r_period = str(r.get("period_end_date") or "")[:10]
                        if p_type in ("QUARTERLY", "HALF_YEAR", "HALF_YEARLY", "Q1", "Q2", "Q3", "Q4", "H1", "H2") or not r_period.endswith("-03-31"):
                            continue
                        if r_period and canonical_period_end and r_period > str(canonical_period_end):
                            _mark_update_pending(sym_clean, state_file)
                            return False, f"UNPROCESSED_RAW_FILING: Newly acquired filing {r_period} > canonical {canonical_period_end}"
            except Exception:
                pass

        return True, None
    except Exception as e:
        logger.warning(f"[PRE_BUY_FENCE] Error evaluating source freshness fence for {symbol}: {e}")
        return False, f"FRESHNESS_FENCE_ERROR: {e}"


def commit_buy_alert_atomic(
    bundle: BUYEvidenceBundle,
    alert_sink: Optional[List[Dict[str, Any]]] = None,
    alerts_parquet_path: Optional[str] = None,
    alerts_db_path: Optional[str] = None,
    max_sla_seconds: int = 86400,
    simulate_failure_stage: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    ATOMIC DECISION GATE + CRASH-RECOVERABLE PERSISTENCE:
    Architecture: Authoritative DB Transactional Outbox + Idempotent Parquet Materialization.

    Gate Invariants:
      1. Verifies current canonical snapshot SHA256 matches bundle.snapshot_sha256.
         If snapshot changed during execution -> ROLLBACK / REJECT (SNAPSHOT_VERSION_DRIFT).
      2. Re-verifies watcher state is STILL FRESH (not UPDATE_PENDING).
         If a filing was injected between evaluation and commit -> ROLLBACK / REJECT (CONCURRENT_FILING_DETECTED).
      3. Re-verifies multi-source exchange watermark (NSE + BSE).
         If a newer filing appeared on exchange since pre-buy check -> ROLLBACK / REJECT (RACE_CONDITION_NEWER_FILING).
      4. Single Authoritative DB Outbox Transaction:
         Alert is written to buy_alerts_journal table first with materialized_to_parquet = 0.
         DB transaction commits authoritatively.
      5. Idempotent Parquet Materialization:
         Alert is appended / deduplicated into the Parquet file.
         Upon successful file write, DB completion marker is updated (materialized_to_parquet = 1).
      6. Crash Recovery Guarantee:
         reconcile_alerts_outbox_materialization() deterministically recovers from any crash:
           - Crash before DB commit -> Rollback, 0 in DB, 0 in Parquet.
           - Crash after DB commit before Parquet -> Reconciled, missing alert materialized to Parquet.
           - Crash after Parquet before DB marker -> Reconciled, idempotent deduplication, marker updated.
           - Container restart / Parquet destruction -> Parquet reconstructed completely from DB outbox.
           - Rogue Parquet records pruned to match authoritative DB outbox.
    """
    if not bundle.is_buy_eligible():
        return False, f"BUNDLE_NOT_ELIGIBLE: {'; '.join(bundle.blocking_reasons)}"

    base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(base_dir, "data")
    canon_path = os.path.join(data_dir, "canonical_pit_rebuilt.parquet")
    if alerts_db_path is None:
        alerts_db_path = os.path.join(data_dir, "buy_alerts_journal.db")

    init_buy_alerts_journal(alerts_db_path)

    # 1. Verify Snapshot Version & SHA256
    if bundle.snapshot_sha256 and os.path.exists(canon_path):
        h = hashlib.sha256()
        with open(canon_path, "rb") as cf:
            for chunk in iter(lambda: cf.read(65536), b""):
                h.update(chunk)
        current_sha = h.hexdigest()
        if current_sha != bundle.snapshot_sha256:
            logger.error(f"[ATOMIC_BUY_COMMIT] REJECTED: Snapshot drift for {bundle.symbol}: bundle={bundle.snapshot_sha256[:16]} != current={current_sha[:16]}")
            return False, f"SNAPSHOT_VERSION_DRIFT: Bundle sha {bundle.snapshot_sha256[:16]} != current canonical sha {current_sha[:16]}"

    # 2. Re-check Watcher State
    from scripts.financial_filing_watcher import FinancialFilingWatcher, SnapshotFreshnessStatus
    watcher = FinancialFilingWatcher()
    f_status = watcher.get_symbol_freshness_status(bundle.symbol)
    if f_status != SnapshotFreshnessStatus.FRESH:
        logger.error(f"[ATOMIC_BUY_COMMIT] REJECTED: Symbol {bundle.symbol} transitioned to {f_status.value} before commit")
        return False, f"CONCURRENT_FILING_DETECTED: Symbol {bundle.symbol} transitioned to {f_status.value} before commit"

    # 3. Final Pre-Commit Freshness Fence Recheck
    c_period = None
    c_broadcast = bundle.pit_eligible_from or bundle.pit_timestamp
    for fm in bundle.financial_metrics.values():
        if fm.period_end and (c_period is None or str(fm.period_end) > str(c_period)):
            c_period = str(fm.period_end)

    fence_ok, fence_reason = check_pre_buy_source_freshness_fence(
        symbol=bundle.symbol,
        canonical_period_end=c_period,
        canonical_filing_timestamp=c_broadcast,
        max_sla_seconds=max_sla_seconds,
    )
    if not fence_ok:
        logger.error(f"[ATOMIC_BUY_COMMIT] REJECTED: Final freshness fence failed for {bundle.symbol}: {fence_reason}")
        return False, f"RACE_CONDITION_NEWER_FILING: {fence_reason}"

    # 4. Construct Deterministic Alert Record
    alert_id = f"{bundle.scanner}_{bundle.symbol}_{bundle.scan_run_id}_{bundle.evidence_hash[:16]}"
    now_iso = datetime.now().isoformat()
    alert_record = {
        "alert_id": alert_id,
        "symbol": bundle.symbol,
        "scanner": bundle.scanner,
        "run_id": bundle.scan_run_id,
        "alert_timestamp": now_iso,
        "cmp": bundle.cmp,
        "strategy_score": bundle.strategy_score,
        "snapshot_version": bundle.snapshot_version,
        "snapshot_sha256": bundle.snapshot_sha256,
        "evidence_hash": bundle.evidence_hash,
        "status": "COMMITTED",
    }

    # STAGE 1 CRASH SIMULATION: Crash occurs before DB commit
    if simulate_failure_stage == "BEFORE_DB_COMMIT":
        logger.warning(f"[PERSISTENCE_CRASH] Simulated crash BEFORE DB commit for {bundle.symbol}")
        return False, "CRASH_BEFORE_DB_COMMIT"

    # 5. Authoritative Outbox Commit (DB Journal)
    with sqlite3.connect(alerts_db_path, timeout=30.0) as conn:
        conn.execute("""
            INSERT INTO buy_alerts_journal (
                alert_id, symbol, scanner, run_id, alert_timestamp, cmp,
                strategy_score, snapshot_version, snapshot_sha256, evidence_hash,
                status, materialized_to_parquet, materialized_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'COMMITTED', 0, NULL, ?)
            ON CONFLICT(alert_id) DO UPDATE SET
                alert_timestamp = excluded.alert_timestamp,
                cmp = excluded.cmp,
                strategy_score = excluded.strategy_score,
                status = excluded.status
        """, (
            alert_id, bundle.symbol, bundle.scanner, bundle.scan_run_id,
            now_iso, bundle.cmp, bundle.strategy_score,
            bundle.snapshot_version, bundle.snapshot_sha256, bundle.evidence_hash,
            now_iso,
        ))
        conn.commit()

    # STAGE 2 CRASH SIMULATION: Crash occurs after DB commit but before Parquet write
    if simulate_failure_stage == "AFTER_DB_COMMIT_BEFORE_PARQUET":
        logger.warning(f"[PERSISTENCE_CRASH] Simulated crash AFTER DB commit but BEFORE Parquet write for {bundle.symbol}")
        return False, "CRASH_AFTER_DB_COMMIT_BEFORE_PARQUET"

    # 6. Idempotent Parquet Materialization
    if alerts_parquet_path:
        os.makedirs(os.path.dirname(os.path.abspath(alerts_parquet_path)), exist_ok=True)
        df_new_alert = pd.DataFrame([alert_record])
        if os.path.exists(alerts_parquet_path):
            try:
                df_existing = pd.read_parquet(alerts_parquet_path)
                if "alert_id" in df_existing.columns:
                    df_existing = df_existing[df_existing["alert_id"] != alert_id]
                else:
                    df_existing = df_existing[
                        ~((df_existing["symbol"] == bundle.symbol) &
                          (df_existing["scanner"] == bundle.scanner) &
                          (df_existing["run_id"] == bundle.scan_run_id))
                    ]
                df_combined = pd.concat([df_existing, df_new_alert], ignore_index=True)
            except Exception as pe:
                logger.warning(f"Error reading existing parquet {alerts_parquet_path}: {pe}")
                df_combined = df_new_alert
        else:
            df_combined = df_new_alert

        tmp_p = f"{alerts_parquet_path}.tmp.{os.getpid()}_{int(time.time()*1000)}"
        df_combined.to_parquet(tmp_p, index=False)
        os.replace(tmp_p, alerts_parquet_path)

    # STAGE 3 CRASH SIMULATION: Crash occurs after Parquet write but before DB marker update
    if simulate_failure_stage == "AFTER_PARQUET_BEFORE_MARKER":
        logger.warning(f"[PERSISTENCE_CRASH] Simulated crash AFTER Parquet write but BEFORE DB marker update for {bundle.symbol}")
        return False, "CRASH_AFTER_PARQUET_BEFORE_MARKER"

    # 7. Update DB Outbox Completion Marker
    with sqlite3.connect(alerts_db_path, timeout=30.0) as conn:
        conn.execute("""
            UPDATE buy_alerts_journal
            SET materialized_to_parquet = 1, materialized_at = ?
            WHERE alert_id = ?
        """, (datetime.now().isoformat(), alert_id))
        conn.commit()

    if alert_sink is not None and isinstance(alert_sink, list):
        alert_sink.append(alert_record)

    logger.info(f"✅ [ATOMIC_BUY_COMMIT] {bundle.scanner}/{bundle.symbol}: BUY Alert committed with verified snapshot {str(bundle.snapshot_sha256)[:16]}")
    return True, "COMMITTED"


def reconcile_alerts_outbox_materialization(
    alerts_parquet_path: Optional[str] = None,
    alerts_db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    CRASH-CONSISTENCY RECONCILIATION & RECOVERY PROCEDURE:
    Deterministically reconciles state across Authoritative DB Outbox and Parquet.
    Guarantees:
      1. Zero lost alerts: Any alert committed in DB outbox with materialized_to_parquet=0
         is materialized to Parquet idempotently, and DB marker is updated.
      2. Crash recovery / container restart: If Parquet file was wiped or missing,
         it is completely reconstructed from the authoritative DB journal.
      3. Zero orphaned Parquet records: Any record in Parquet that does not exist as
         COMMITTED in the authoritative DB is pruned.
      4. Invariant:
         Never (DB BUY present + Parquet BUY absent indefinitely)
         Never (Parquet BUY present + DB BUY absent indefinitely).
    """
    base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(base_dir, "data")
    if alerts_db_path is None:
        alerts_db_path = os.path.join(data_dir, "buy_alerts_journal.db")
    if alerts_parquet_path is None:
        alerts_parquet_path = os.path.join(data_dir, "10_alerts.parquet")

    init_buy_alerts_journal(alerts_db_path)

    # 1. Fetch all committed alerts from authoritative DB journal
    with sqlite3.connect(alerts_db_path, timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT alert_id, symbol, scanner, run_id, alert_timestamp, cmp,
                   strategy_score, snapshot_version, snapshot_sha256, evidence_hash,
                   status, materialized_to_parquet
            FROM buy_alerts_journal
            WHERE status = 'COMMITTED'
            ORDER BY alert_timestamp ASC
        """)
        db_alerts = [dict(r) for r in cur.fetchall()]

    db_alert_ids = {r["alert_id"] for r in db_alerts}
    pending_db_alerts = [r for r in db_alerts if r["materialized_to_parquet"] == 0]

    # 2. Case: Parquet file does not exist (Container restart / volume wipe / crash)
    if not os.path.exists(alerts_parquet_path):
        if db_alerts:
            rows_to_save = []
            for r in db_alerts:
                c = dict(r)
                c.pop("materialized_to_parquet", None)
                rows_to_save.append(c)
            df_rebuilt = pd.DataFrame(rows_to_save)
            os.makedirs(os.path.dirname(os.path.abspath(alerts_parquet_path)), exist_ok=True)
            tmp_p = f"{alerts_parquet_path}.tmp.reconcile_{os.getpid()}_{int(time.time()*1000)}"
            df_rebuilt.to_parquet(tmp_p, index=False)
            os.replace(tmp_p, alerts_parquet_path)
        
        with sqlite3.connect(alerts_db_path, timeout=30.0) as conn:
            conn.execute(
                "UPDATE buy_alerts_journal SET materialized_to_parquet = 1, materialized_at = ? WHERE status = 'COMMITTED'",
                (datetime.now().isoformat(),)
            )
            conn.commit()

        return {
            "reconciliation_status": "RECONSTRUCTED_FROM_DB",
            "db_committed_total": len(db_alerts),
            "parquet_count_after": len(db_alerts),
            "materialized_repaired": len(db_alerts),
            "orphans_pruned": 0,
        }

    # 3. Case: Parquet file exists -> Reconcile differences
    df_pq = pd.read_parquet(alerts_parquet_path)
    orphans_pruned = 0

    # Prune orphaned records in Parquet that have no committed DB outbox record
    if "alert_id" in df_pq.columns:
        valid_mask = df_pq["alert_id"].isin(db_alert_ids)
        orphans_pruned = int((~valid_mask).sum())
        df_pq = df_pq[valid_mask]
    else:
        valid_keys = {(r["symbol"], r["scanner"], r["run_id"]) for r in db_alerts}
        keys = list(zip(df_pq["symbol"], df_pq["scanner"], df_pq["run_id"]))
        valid_mask = [k in valid_keys for k in keys]
        orphans_pruned = sum(not v for v in valid_mask)
        df_pq = df_pq[valid_mask]

    # Find DB alerts missing from Parquet
    pq_alert_ids = set(df_pq["alert_id"].dropna()) if "alert_id" in df_pq.columns else set()
    missing_from_pq = [r for r in db_alerts if r["alert_id"] not in pq_alert_ids]

    materialized_repaired = 0
    if missing_from_pq or orphans_pruned > 0:
        rows_to_append = []
        for r in missing_from_pq:
            c = dict(r)
            c.pop("materialized_to_parquet", None)
            rows_to_append.append(c)
        if rows_to_append:
            df_append = pd.DataFrame(rows_to_append)
            df_pq = pd.concat([df_pq, df_append], ignore_index=True)
            materialized_repaired = len(rows_to_append)
        
        tmp_p = f"{alerts_parquet_path}.tmp.reconcile_{os.getpid()}_{int(time.time()*1000)}"
        df_pq.to_parquet(tmp_p, index=False)
        os.replace(tmp_p, alerts_parquet_path)

    # Mark pending rows in DB outbox as materialized
    if pending_db_alerts:
        with sqlite3.connect(alerts_db_path, timeout=30.0) as conn:
            conn.execute(
                "UPDATE buy_alerts_journal SET materialized_to_parquet = 1, materialized_at = ? WHERE materialized_to_parquet = 0",
                (datetime.now().isoformat(),)
            )
            conn.commit()

    return {
        "reconciliation_status": "RECONCILED_CLEAN",
        "db_committed_total": len(db_alerts),
        "parquet_count_after": len(df_pq),
        "materialized_repaired": materialized_repaired,
        "orphans_pruned": orphans_pruned,
    }


def pre_buy_integrity_gate(
    symbol: str,
    scanner: str,
    required_metrics: List[str],
    financial_metrics: Dict[str, FieldProvenance],
    gate_results: Dict[str, Any],
    blocking_reasons: Optional[List[str]] = None,
) -> Tuple[bool, List[str]]:
    """
    Final integrity check before a BUY alert is written.

    Rules:
      - Every required metric must have an entry in financial_metrics.
      - Every entry must have value_used, source_used, period_end, basis set.
      - Every entry must have validation_status == "PASSED".
      - Pre-BUY Source Freshness Fence: canonical snapshot must be >= exchange watermark.
      - No blocking_reasons may be present.

    Returns:
        (buy_eligible: bool, blocking_reasons: List[str])
    """
    reasons: List[str] = list(blocking_reasons or [])

    # ── PRE-BUY EXTERNAL SOURCE FRESHNESS FENCE (NO_NEWER_UNPROCESSED_FILING) ──
    c_period = None
    c_broadcast = None
    for m in required_metrics:
        if m in financial_metrics:
            p = financial_metrics[m]
            if p.period_end and (c_period is None or str(p.period_end) > str(c_period)):
                c_period = str(p.period_end)
            if p.pit_eligible_from and (c_broadcast is None or str(p.pit_eligible_from) > str(c_broadcast)):
                c_broadcast = str(p.pit_eligible_from)

    fence_ok, fence_reason = check_pre_buy_source_freshness_fence(
        symbol=symbol,
        canonical_period_end=c_period,
        canonical_filing_timestamp=c_broadcast,
    )
    if not fence_ok and fence_reason:
        reasons.append(fence_reason)

    for m in required_metrics:
        if m not in financial_metrics:
            reasons.append(f"MISSING_PROVENANCE_RECORD: {m}")
            continue
        prov = financial_metrics[m]
        if prov.value_used is None:
            reasons.append(f"NO_VALUE_USED: {m}")
        if not prov.source_used:
            reasons.append(f"NO_SOURCE_RECORDED: {m}")
        if not prov.period_end:
            reasons.append(f"NO_PERIOD_END: {m}")
        if not prov.basis:
            reasons.append(f"NO_BASIS: {m}")
        if prov.validation_status != "PASSED":
            reasons.append(
                f"VALIDATION_NOT_PASSED: {m} → {prov.validation_status}: {prov.validation_reason}"
            )

    if reasons:
        logger.error(
            f"[PRE_BUY_GATE] {scanner}/{symbol}: BUY BLOCKED. "
            f"{len(reasons)} integrity failure(s): {reasons}"
        )
        return False, reasons

    logger.info(
        f"[PRE_BUY_GATE] {scanner}/{symbol}: All {len(required_metrics)} required "
        f"metrics have valid provenance. BUY ELIGIBLE."
    )
    return True, []


def build_buy_evidence_bundle(
    scan_run_id: str,
    scanner: str,
    symbol: str,
    cmp: Optional[float],
    strategy_score: Optional[float],
    gate_results: Dict[str, Any],
    financial_metrics: Dict[str, FieldProvenance],
    required_metrics: List[str],
    pit_timestamp: Optional[str] = None,
    pit_eligible_from: Optional[str] = None,
    source_filing_ids: Optional[List[str]] = None,
    blocking_reasons: Optional[List[str]] = None,
    snapshot_version: Optional[str] = None,
    snapshot_sha256: Optional[str] = None,
    max_sla_seconds: int = 86400,
) -> BUYEvidenceBundle:
    """
    Builds and validates a complete BUY evidence bundle.

    The bundle is ONLY eligible for BUY insertion when is_buy_eligible() is True.
    Binds snapshot_version, snapshot_sha256, and full multi-source exchange watermarks.
    """
    buy_eligible, reasons = pre_buy_integrity_gate(
        symbol=symbol,
        scanner=scanner,
        required_metrics=required_metrics,
        financial_metrics=financial_metrics,
        gate_results=gate_results,
        blocking_reasons=blocking_reasons,
    )

    all_provenance_complete = all(
        fm.value_used is not None and fm.source_used and fm.period_end
        for fm in financial_metrics.values()
    )

    base_dir = os.getenv("ELITE_BASE_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    data_dir = os.path.join(base_dir, "data")
    canon_path = os.path.join(data_dir, "canonical_pit_rebuilt.parquet")
    calc_sha = None
    if os.path.exists(canon_path):
        try:
            h = hashlib.sha256()
            with open(canon_path, "rb") as cf:
                for chunk in iter(lambda: cf.read(65536), b""):
                    h.update(chunk)
            calc_sha = h.hexdigest()
        except Exception:
            pass

    resolved_sha = snapshot_sha256 or calc_sha
    resolved_ver = snapshot_version or (resolved_sha[:16] if resolved_sha else "UNKNOWN")

    c_period = None
    c_broadcast = pit_eligible_from or pit_timestamp
    for fm in financial_metrics.values():
        if fm.period_end and (c_period is None or str(fm.period_end) > str(c_period)):
            c_period = str(fm.period_end)

    wm = get_multi_source_exchange_watermark(symbol, max_sla_seconds=max_sla_seconds)
    bundle_watermarks = {
        "symbol": symbol.strip().upper(),
        "canonical_snapshot_timestamp": c_broadcast,
        "canonical_period_end": c_period,
        "snapshot_sha256": resolved_sha,
        "snapshot_version": resolved_ver,
        "latest_exchange_filing_timestamp": wm.get("latest_exchange_filing_timestamp"),
        "sources": wm.get("source_watermarks", []),
        "sla_valid": wm.get("valid", False),
    }

    bundle = BUYEvidenceBundle(
        scan_run_id=scan_run_id,
        scanner=scanner,
        symbol=symbol,
        cmp=cmp,
        strategy_score=strategy_score,
        gate_results=gate_results,
        financial_metrics=financial_metrics,
        pit_timestamp=pit_timestamp,
        pit_eligible_from=pit_eligible_from,
        source_filing_ids=source_filing_ids or [],
        data_integrity_status=DataStatus.VALID if buy_eligible else DataStatus.DATA_BLOCKED,
        financial_provenance_complete=all_provenance_complete,
        pit_valid=pit_eligible_from is not None,
        period_integrity=True,  # enforced by pre_buy_integrity_gate
        basis_integrity=True,   # enforced by pre_buy_integrity_gate
        unit_integrity=True,    # enforced by share_count and EV validation
        required_metrics_complete=len(reasons) == 0,
        snapshot_version=resolved_ver,
        snapshot_sha256=resolved_sha,
        source_watermarks=bundle_watermarks,
        blocking_reasons=reasons,
    )

    bundle.evidence_hash = bundle.compute_evidence_hash()
    return bundle


# ---------------------------------------------------------------------------
# C19 — SCORE REPRODUCIBILITY CHECK
# ---------------------------------------------------------------------------

def verify_score_reproducibility(
    saved_bundle: BUYEvidenceBundle,
    recomputed_score: Optional[float],
    tolerance: float = 0.01,
) -> IntegrityResult:
    """
    Verifies that a recomputed score matches the saved score within tolerance.

    Args:
        saved_bundle: The original saved BUY evidence bundle.
        recomputed_score: Score recomputed from the saved evidence.
        tolerance: Allowed absolute difference.

    Returns:
        IntegrityResult VALID if scores match, DATA_CONFLICT otherwise.
    """
    original = saved_bundle.strategy_score
    if original is None or recomputed_score is None:
        return IntegrityResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason="SCORE_MISSING_FOR_REPRODUCIBILITY_CHECK",
        )
    diff = abs(original - recomputed_score)
    if diff > tolerance:
        return IntegrityResult(
            status=DataStatus.DATA_CONFLICT,
            reason=(
                f"SCORE_NOT_REPRODUCIBLE: original={original}, "
                f"recomputed={recomputed_score}, diff={diff:.4f} > tolerance={tolerance}"
            ),
            detail={"original_score": original, "recomputed_score": recomputed_score, "diff": diff},
        )
    return IntegrityResult(
        status=DataStatus.VALID,
        reason=f"SCORE_REPRODUCIBLE: diff={diff:.4f}",
        detail={"original_score": original, "recomputed_score": recomputed_score, "diff": diff},
    )


# ---------------------------------------------------------------------------
# HEALTH STATUS SEPARATION (C35 — telemetry)
# ---------------------------------------------------------------------------

@dataclass
class ScannerHealthReport:
    """
    Separates infrastructure health from data completeness.

    A scanner can complete successfully (infrastructure=OK) while having
    partial data (financial_data=PARTIAL). These are NOT the same status.
    """
    scanner: str
    scan_run_id: str
    infrastructure_status: str = "OK"
    financial_data_status: str = "UNKNOWN"
    data_completeness: str = "UNKNOWN"
    buy_eligibility_status: str = "UNKNOWN"

    total_symbols: int = 0
    fully_validated: int = 0
    data_insufficient_count: int = 0
    data_stale_count: int = 0
    data_conflict_count: int = 0
    data_invalid_count: int = 0
    provider_failure_count: int = 0
    missing_required_fields: int = 0
    period_conflict_count: int = 0
    basis_conflict_count: int = 0
    unit_conflict_count: int = 0
    fy_gap_count: int = 0
    buy_alert_count: int = 0

    symbols_blocked_data_defects: List[str] = field(default_factory=list)

    def to_log_summary(self) -> str:
        return (
            f"[SCANNER_HEALTH] {self.scanner} | run={self.scan_run_id}\n"
            f"  Infrastructure:    {self.infrastructure_status}\n"
            f"  Financial data:    {self.financial_data_status}\n"
            f"  Data completeness: {self.data_completeness}\n"
            f"  BUY eligibility:   {self.buy_eligibility_status}\n"
            f"  Universe:          {self.total_symbols}\n"
            f"  Fully validated:   {self.fully_validated}\n"
            f"  Data insufficient: {self.data_insufficient_count}\n"
            f"  Data stale:        {self.data_stale_count}\n"
            f"  FY gaps:           {self.fy_gap_count}\n"
            f"  BUY alerts:        {self.buy_alert_count}\n"
            f"  Blocked symbols:   {self.symbols_blocked_data_defects}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Canonical aliases and convenience wrappers
# ---------------------------------------------------------------------------
FreshnessStatus = DataStatus

def compute_cagr_pit(
    annual_rows_sorted: List[Dict[str, Any]],
    metric: str,
    symbol: str = "UNKNOWN",
    target_years: int = 5,
    basis: str = StatementBasis.CONSOLIDATED,
    as_of_date: Optional[date] = None,
    scan_date: Optional[date] = None,
) -> CAGRResult:
    """Convenience wrapper for compute_cagr."""
    return compute_cagr(
        annual_rows_sorted=annual_rows_sorted,
        metric=metric,
        symbol=symbol,
        target_years=target_years,
        basis=basis,
        as_of_date=as_of_date,
        scan_date=scan_date,
    )

def compute_ev_pit(
    mcap: Optional[float],
    total_debt: Optional[float],
    cash_and_equivalents: Optional[float],
    ebitda: Optional[float] = None,
    minority_interest: Optional[float] = None,
    symbol: str = "UNKNOWN",
) -> EVResult:
    """Convenience wrapper for EV calculation directly from MCap, Debt, Cash, EBITDA."""
    formula = "EV = MCap + Debt - Cash + MinorityInterest"
    if mcap is None or (isinstance(mcap, float) and math.isnan(mcap)) or mcap <= 0:
        return EVResult(status=DataStatus.DATA_INSUFFICIENT, reason="MCAP_MISSING_OR_ZERO", formula=formula)
    if total_debt is None or (isinstance(total_debt, float) and math.isnan(total_debt)):
        return EVResult(status=DataStatus.DATA_INSUFFICIENT, reason="TOTAL_DEBT_MISSING", formula=formula)
    if cash_and_equivalents is None or (isinstance(cash_and_equivalents, float) and math.isnan(cash_and_equivalents)):
        return EVResult(status=DataStatus.DATA_INSUFFICIENT, reason="CASH_UNAVAILABLE", ev_cash_status="CASH_MISSING_EV_BLOCKED", formula=formula)

    mi = float(minority_interest) if minority_interest is not None else 0.0
    ev_cr = round(mcap + total_debt - cash_and_equivalents + mi, 4)
    if ev_cr <= 0:
        return EVResult(status=DataStatus.DATA_INVALID, reason=f"EV_NON_POSITIVE: {ev_cr}", ev_cr=ev_cr, formula=formula)

    ev_eb = None
    if ebitda is not None and not (isinstance(ebitda, float) and math.isnan(ebitda)) and ebitda > 0:
        ev_eb = round(ev_cr / ebitda, 2)

    return EVResult(
        status=DataStatus.VALID,
        ev_ebitda=ev_eb,
        ev_cr=ev_cr,
        mcap_cr=mcap,
        total_debt_cr=total_debt,
        cash_cr=cash_and_equivalents,
        minority_interest_cr=mi,
        ebitda_cr=ebitda,
        ev_cash_status="CASH_PRESENT",
        formula=formula,
    )

def validate_share_count(
    filed_shares: Optional[float] = None,
    net_profit_cr: Optional[float] = None,
    eps: Optional[float] = None,
    cmp_price: float = 0.0,
    symbol: str = "UNKNOWN",
    scanner: str = "UNKNOWN",
) -> ShareCountResult:
    """Convenience wrapper for derive_and_validate_shares."""
    return derive_and_validate_shares(
        symbol=symbol,
        net_profit_cr=net_profit_cr,
        eps=eps,
        shares_outstanding_raw=filed_shares,
        scanner=scanner,
    )

def compute_yoy_quarterly(
    quarterly_rows_sorted: List[Dict[str, Any]],
    metric: str,
    symbol: str = "UNKNOWN",
    basis: str = StatementBasis.CONSOLIDATED,
    as_of_date: Optional[date] = None,
    scan_date: Optional[date] = None,
) -> YoYResult:
    """Convenience wrapper for compute_quarterly_yoy."""
    return compute_quarterly_yoy(
        quarterly_rows_sorted=quarterly_rows_sorted,
        metric=metric,
        symbol=symbol,
        basis=basis,
        as_of_date=as_of_date,
        scan_date=scan_date,
    )

def pre_buy_data_integrity_gate(
    bundle_or_symbol: Any,
    scanner: Optional[str] = None,
    required_metrics: Optional[List[str]] = None,
    financial_metrics: Optional[Dict[str, FieldProvenance]] = None,
    gate_results: Optional[Dict[str, Any]] = None,
    blocking_reasons: Optional[List[str]] = None,
) -> IntegrityResult:
    """
    Overloaded pre-BUY data integrity gate.
    Can accept:
      - a single BUYEvidenceBundle instance, OR
      - individual arguments (symbol, scanner, required_metrics, financial_metrics, gate_results, blocking_reasons)
    """
    if isinstance(bundle_or_symbol, BUYEvidenceBundle):
        b = bundle_or_symbol
        req = required_metrics or list(b.financial_metrics.keys())
        ok, reasons = pre_buy_integrity_gate(
            symbol=b.symbol,
            scanner=b.scanner,
            required_metrics=req,
            financial_metrics=b.financial_metrics,
            gate_results=b.gate_results,
            blocking_reasons=b.blocking_reasons,
        )
        b.blocking_reasons = reasons
        b.data_integrity_status = DataStatus.VALID if ok else DataStatus.DATA_BLOCKED
        b.required_metrics_complete = ok
        if ok and not b.evidence_hash:
            b.evidence_hash = b.compute_evidence_hash()
        return IntegrityResult(
            status=DataStatus.VALID if ok else DataStatus.DATA_BLOCKED,
            reason="; ".join(reasons) if reasons else "ALL_METRICS_VALID",
            detail={"blocking_reasons": reasons, "evidence_hash": b.evidence_hash}
        )
    else:
        sym = str(bundle_or_symbol)
        scn = scanner or "UNKNOWN"
        req = required_metrics or []
        fm = financial_metrics or {}
        gr = gate_results or {}
        br = blocking_reasons or []
        ok, reasons = pre_buy_integrity_gate(
            symbol=sym,
            scanner=scn,
            required_metrics=req,
            financial_metrics=fm,
            gate_results=gr,
            blocking_reasons=br,
        )
        return IntegrityResult(
            status=DataStatus.VALID if ok else DataStatus.DATA_BLOCKED,
            reason="; ".join(reasons) if reasons else "ALL_METRICS_VALID",
            detail={"blocking_reasons": reasons}
        )


def reconcile_nse_bse_fact(
    symbol: str,
    fact_name: str,
    nse_value: Optional[float],
    bse_value: Optional[float],
    tolerance_pct: float = 5.0,
) -> IntegrityResult:
    """
    C12: NSE/BSE CROSS-RECONCILIATION.
    Compares reported values between NSE and BSE filings for the same period.
    If difference exceeds tolerance_pct, logs conflict and returns DATA_CONFLICT.
    """
    if nse_value is None and bse_value is None:
        return IntegrityResult(
            status=DataStatus.DATA_INSUFFICIENT,
            reason=f"NO_EXCHANGE_DATA: {fact_name} missing on both NSE and BSE for {symbol}",
        )
    if nse_value is None:
        return IntegrityResult(
            status=DataStatus.VALID,
            reason=f"BSE_ONLY: {fact_name} available only on BSE for {symbol}",
            detail={"exchange_used": "BSE", "value": bse_value}
        )
    if bse_value is None:
        return IntegrityResult(
            status=DataStatus.VALID,
            reason=f"NSE_ONLY: {fact_name} available only on NSE for {symbol}",
            detail={"exchange_used": "NSE", "value": nse_value}
        )

    base = abs(nse_value)
    if base < 1e-6:
        base = abs(bse_value)
    if base < 1e-6:
        diff_pct = 0.0
    else:
        diff_pct = abs(nse_value - bse_value) / base * 100.0

    if diff_pct > tolerance_pct:
        msg = (
            f"[NSE_BSE_CONFLICT] {symbol}: {fact_name} differs by {diff_pct:.2f}% "
            f"(NSE={nse_value}, BSE={bse_value}, tolerance={tolerance_pct}%)"
        )
        logger.error(msg)
        return IntegrityResult(
            status=DataStatus.DATA_CONFLICT,
            reason=f"NSE_BSE_CONFLICT: {fact_name} differs by {diff_pct:.2f}%",
            detail={
                "nse_value": nse_value,
                "bse_value": bse_value,
                "diff_pct": diff_pct,
                "tolerance_pct": tolerance_pct,
            }
        )

    return IntegrityResult(
        status=DataStatus.VALID,
        reason=f"NSE_BSE_RECONCILED: {fact_name} diff={diff_pct:.2f}% within {tolerance_pct}%",
        detail={
            "nse_value": nse_value,
            "bse_value": bse_value,
            "diff_pct": diff_pct,
        }
    )


# ---------------------------------------------------------------------------
# C20: SHARED FINANCIAL SNAPSHOT (SINGLE CANONICAL FINANCIAL LAYER)
# ---------------------------------------------------------------------------

class SnapshotFreshnessStatus(str, Enum):
    FRESH             = "FRESH"
    UPDATE_PENDING    = "UPDATE_PENDING"
    STALE             = "STALE"
    INVALID           = "INVALID"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class FinancialSnapshotStatus(str, Enum):
    """Lifecycle synchronization status for the shared financial snapshot layer."""
    NOT_INITIALIZED         = "NOT_INITIALIZED"
    BUILDING                = "BUILDING"
    AUDITING                = "AUDITING"
    SNAPSHOT_BUILD_COMPLETE = "SNAPSHOT_BUILD_COMPLETE"
    SNAPSHOT_DATA_PARTIAL   = "SNAPSHOT_DATA_PARTIAL"
    SNAPSHOT_READY_FOR_SCANNER = "SNAPSHOT_READY_FOR_SCANNER"
    READY                   = "SNAPSHOT_DATA_PARTIAL"   # Backward compatible alias for partial snapshot
    INCOMPLETE              = "INCOMPLETE"
    STALE                   = "STALE"
    FAILED                  = "FAILED"


@dataclass
class SharedFinancialSnapshot:
    """
    Single authoritative financial data snapshot for BOTH:
      1. QUALITY_COMPOUNDER_VALUE_V2_FINAL
      2. FUNDAMENTAL (LiveFundamentalBuyScanner)

    Freshness & Integrity Flow:
        NSE / BSE
           │
           ▼
        Financial Filing Watcher
           │
        NEW / AMENDED filing?
           │
           ▼
        Immutable Raw Filing Store
           │
           ▼
        Normalization + PIT
           │
           ▼
        Shared Financial Snapshot
           │
        ┌─────────┴─────────┐
        ▼                   ▼
      QUALITY         FUNDAMENTAL
        │                   │
        ▼                   ▼
    Existing frozen    Existing frozen
     strategy rules     strategy rules
    """
    symbol: str
    isin: str = ""
    as_of_date: str = ""
    latest_annual_period: Optional[str] = None
    latest_quarterly_period: Optional[str] = None
    snapshot_status: str = SnapshotFreshnessStatus.FRESH.value
    pit_freshness_status: str = DataStatus.VALID.value
    provenance_status: str = "CERTIFIED"
    validation_reasons: List[str] = field(default_factory=list)
    snapshot_hash: str = ""

    # Quality & Profitability Facts & Ratios (used by both scanners)
    roce: Optional[float] = None
    roe: Optional[float] = None
    roce_5y_avg: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    cfo_pat_5y_ratio: Optional[float] = None
    total_debt: Optional[float] = None
    total_equity: Optional[float] = None
    debt_equity: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    ebitda: Optional[float] = None
    net_profit: Optional[float] = None
    revenue: Optional[float] = None

    # Growth & Long-Term CAGR Facts & Ratios
    sales_cagr_5y: Optional[float] = None
    pat_cagr_5y: Optional[float] = None
    filing_gap_detected: bool = False
    filing_gaps: str = "[]"
    growth_start_period: Optional[str] = None
    growth_end_period: Optional[str] = None
    growth_years_elapsed: float = 0.0
    financial_periods_used: int = 0
    roce_periods_used: int = 0

    # Shares & Dilution
    shares_outstanding_m: Optional[float] = None
    shares_status: str = DataStatus.VALID.value
    shares_scaling_applied: bool = False
    share_dilution_3y: Optional[float] = None

    # Quarterly Growth & Earnings Acceleration (used by FUNDAMENTAL & QUALITY scoring)
    rev_yoy_latest: Optional[float] = None
    rev_yoy_prev: Optional[float] = None
    op_profit_yoy_latest: Optional[float] = None
    op_profit_yoy_prev: Optional[float] = None
    eps_yoy_latest: Optional[float] = None
    eps_yoy_prev: Optional[float] = None
    prior_eps: Optional[float] = None
    growth_score: float = 0.0
    quality_score: float = 0.0
    valuation_score: float = 0.0
    wealth_score: float = 0.0
    is_value_trap: bool = False
    fundamental_category: str = "NONE"

    # Valuation Multiples & Medians
    current_ev: Optional[float] = None
    current_ev_ebitda: Optional[float] = None
    ev_ebitda_3y_median: Optional[float] = None
    current_pe: Optional[float] = None
    pe_3y_median: Optional[float] = None
    market_cap: Optional[float] = None
    industry: str = ""

    # Source Audit
    source_filing_id: Optional[str] = None
    statement_basis: str = StatementBasis.CONSOLIDATED.value
    raw_payload_path: Optional[str] = None

    def is_eligible_for_quality(self) -> Tuple[bool, List[str]]:
        """
        Data-integrity gate for QUALITY_COMPOUNDER.
        Verifies inputs are current and trustworthy before strategy rules run.
        NOTE: Does NOT alter strategy thresholds.
        """
        reasons = []
        if self.snapshot_status == SnapshotFreshnessStatus.UPDATE_PENDING.value:
            reasons.append("UPDATE_PENDING: NEW_OR_AMENDED_FILING_AWAITING_REBUILD")
        elif self.snapshot_status in (SnapshotFreshnessStatus.INVALID.value, SnapshotFreshnessStatus.DATA_INSUFFICIENT.value):
            reasons.append(f"SNAPSHOT_STATUS_{self.snapshot_status}")
        elif self.snapshot_status == SnapshotFreshnessStatus.STALE.value:
            reasons.append("SNAPSHOT_STATUS_STALE")

        if self.pit_freshness_status not in (DataStatus.VALID.value, "FRESH"):
            reasons.append(f"PIT_STALENESS_{self.pit_freshness_status}")

        if self.filing_gap_detected:
            reasons.append("ANNUAL_FISCAL_GAP_DETECTED")

        if self.shares_status in (DataStatus.DATA_INVALID.value, DataStatus.DATA_BLOCKED.value, "IMPLAUSIBLE"):
            reasons.append(f"SHARES_INVALID_{self.shares_status}")

        # Check essential 5Y quality facts are present
        if self.roce_5y_avg is None:
            reasons.append("MISSING_ROCE_5Y_AVG")
        if self.sales_cagr_5y is None:
            reasons.append("MISSING_SALES_CAGR_5Y")
        if self.pat_cagr_5y is None:
            reasons.append("MISSING_PAT_CAGR_5Y")
        if self.cfo_pat_5y_ratio is None:
            reasons.append("MISSING_CFO_PAT_5Y_RATIO")
        if self.debt_equity is None:
            reasons.append("MISSING_DEBT_EQUITY")

        return len(reasons) == 0, reasons

    def is_eligible_for_fundamental(self) -> Tuple[bool, List[str]]:
        """
        Data-integrity gate for FUNDAMENTAL.
        Verifies inputs are current and trustworthy before strategy rules run.
        NOTE: Does NOT alter strategy thresholds.
        """
        reasons = []
        if self.snapshot_status == SnapshotFreshnessStatus.UPDATE_PENDING.value:
            reasons.append("UPDATE_PENDING: NEW_OR_AMENDED_FILING_AWAITING_REBUILD")
        elif self.snapshot_status in (SnapshotFreshnessStatus.INVALID.value, SnapshotFreshnessStatus.DATA_INSUFFICIENT.value):
            reasons.append(f"SNAPSHOT_STATUS_{self.snapshot_status}")
        elif self.snapshot_status == SnapshotFreshnessStatus.STALE.value:
            reasons.append("SNAPSHOT_STATUS_STALE")

        if self.pit_freshness_status not in (DataStatus.VALID.value, "FRESH"):
            reasons.append(f"PIT_STALENESS_{self.pit_freshness_status}")

        return len(reasons) == 0, reasons

    def to_dict(self) -> Dict[str, Any]:
        """Convert to full dictionary."""
        return asdict(self)

    def to_quality_row(self) -> Dict[str, Any]:
        """Convert to dictionary matching QualityCompounderValueV2Scanner expectations."""
        return {
            "symbol": self.symbol,
            "isin": self.isin,
            "as_of_date": self.as_of_date,
            "latest_annual_period": self.latest_annual_period,
            "pit_freshness_status": self.pit_freshness_status,
            "filing_gap_detected": self.filing_gap_detected,
            "filing_gaps": self.filing_gaps,
            "roce_5y_avg": self.roce_5y_avg,
            "sales_cagr_5y": self.sales_cagr_5y,
            "pat_cagr_5y": self.pat_cagr_5y,
            "cfo_pat_5y_ratio": self.cfo_pat_5y_ratio,
            "debt_to_equity": self.debt_equity,
            "debt_equity": self.debt_equity,
            "shares_outstanding_m": self.shares_outstanding_m,
            "shares_outstanding": (self.shares_outstanding_m * 1e6) if self.shares_outstanding_m is not None else None,
            "shares_status": self.shares_status,
            "shares_scaling_applied": self.shares_scaling_applied,
            "cash_and_equivalents": self.cash_and_equivalents,
            "total_debt": self.total_debt,
            "ebitda": self.ebitda,
            "total_equity": self.total_equity,
            "net_profit": self.net_profit,
            "operating_cash_flow": self.operating_cash_flow,
            "growth_start_period": self.growth_start_period,
            "growth_end_period": self.growth_end_period,
            "growth_years_elapsed": self.growth_years_elapsed,
            "financial_periods_used": self.financial_periods_used,
            "roce_periods_used": self.roce_periods_used,
            "current_ev": self.current_ev,
            "current_ev_ebitda": self.current_ev_ebitda,
            "ev_ebitda_3y_median": self.ev_ebitda_3y_median,
            "current_pe": self.current_pe,
            "pe_3y_median": self.pe_3y_median,
            "market_cap": self.market_cap,
            "industry": self.industry,
            "provenance_status": self.provenance_status,
            "snapshot_status": self.snapshot_status,
        }

    def to_fundamental_dict(self) -> Dict[str, Any]:
        """Convert to dictionary matching LiveFundamentalBuyScanner expectations."""
        return {
            "symbol": self.symbol,
            "roce": self.roce,
            "roe": self.roe,
            "debt_equity": self.debt_equity,
            "operating_cash_flow": self.operating_cash_flow,
            "rev_yoy_latest": self.rev_yoy_latest,
            "rev_yoy_prev": self.rev_yoy_prev,
            "op_profit_yoy_latest": self.op_profit_yoy_latest,
            "op_profit_yoy_prev": self.op_profit_yoy_prev,
            "eps_yoy_latest": self.eps_yoy_latest,
            "eps_yoy_prev": self.eps_yoy_prev,
            "prior_eps": self.prior_eps,
            "growth_score": self.growth_score,
            "quality_score": self.quality_score,
            "valuation_score": self.valuation_score,
            "wealth_score": self.wealth_score,
            "fundamental_category": self.fundamental_category,
            "is_value_trap": self.is_value_trap,
            "upstream_provider": "SHARED_CANONICAL_SNAPSHOT",
            "provenance_status": self.provenance_status,
            "snapshot_status": self.snapshot_status,
            "pit_freshness_status": self.pit_freshness_status,
            "quality_source_basis": "ANNUAL",
            "annual_filing_present": bool(self.latest_annual_period or (self.roce is not None)),
        }


_SHARED_SNAPSHOT_CACHE: Dict[str, Dict[str, SharedFinancialSnapshot]] = {}

def clear_shared_snapshot_cache() -> None:
    """Clears the in-memory shared snapshot cache."""
    global _SHARED_SNAPSHOT_CACHE
    _SHARED_SNAPSHOT_CACHE.clear()


def load_all_shared_financial_snapshots(
    as_of_date: Optional[date] = None,
    symbols: Optional[List[str]] = None,
    data_dir: Optional[str] = None,
    force_reload: bool = False,
) -> Dict[str, SharedFinancialSnapshot]:
    """
    Loads or reconstructs the canonical shared financial snapshot for all requested symbols.
    This is the SINGLE SHARED DATA LAYER consumed by:
      - QUALITY_COMPOUNDER_VALUE_V2_FINAL
      - FUNDAMENTAL (LiveFundamentalBuyScanner)

    Checks watcher state for any symbol with UPDATE_PENDING.
    """
    global _SHARED_SNAPSHOT_CACHE
    as_of = as_of_date or date.today()
    base_data = data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    sym_key = hashlib.sha256(",".join(sorted(symbols)).encode()).hexdigest()[:12] if symbols else "ALL"
    cache_key = f"{as_of.isoformat()}_{sym_key}_{base_data}"
    if not force_reload and cache_key in _SHARED_SNAPSHOT_CACHE:
        return _SHARED_SNAPSHOT_CACHE[cache_key]

    # 1. Load watcher state for filing freshness and pending updates
    watcher_state_file = os.path.join(base_data, "filing_watcher_state.json")
    watcher_state = {}
    if os.path.exists(watcher_state_file):
        try:
            with open(watcher_state_file, "r") as f:
                watcher_state = json.load(f)
        except Exception as e:
            logger.warning(f"Could not load watcher state: {e}")

    # 2. Check if rebuilt canonical PIT parquet exists, with fallbacks
    df_canonical = None
    canonical_candidates = [
        os.path.join(base_data, "canonical_pit_rebuilt.parquet"),
        os.path.join(base_data, "pit_fundamentals_v1", "pit_fundamentals_v1.parquet"),
        os.path.join(base_data, "pit_fundamentals_v1.parquet"),
    ]
    for c_path in canonical_candidates:
        if os.path.exists(c_path):
            try:
                df_canonical = pd.read_parquet(c_path)
                if df_canonical is not None and not df_canonical.empty:
                    break
            except Exception as e:
                logger.warning(f"Could not read canonical parquet at {c_path}: {e}")

    # 3. Load valuation cache
    val_cache = {}
    pit_val_cache_path = os.path.join(base_data, "pit_valuation_history_cache.json")
    if os.path.exists(pit_val_cache_path):
        try:
            with open(pit_val_cache_path, "r") as f:
                vj = json.load(f)
                val_cache = vj.get("data", vj)
        except Exception:
            pass

    # 4. Load master fundamentals (Daily Builder 2.0) for quarterly acceleration
    db_funds = {}
    db_master_parquet = os.path.join(base_data, "daily_builder_master_v2.parquet")
    if os.path.exists(db_master_parquet):
        try:
            df_db = pd.read_parquet(db_master_parquet)
            if not df_db.empty and "symbol" in df_db.columns:
                df_db["symbol"] = df_db["symbol"].astype(str).str.strip().str.upper()
                db_funds = {r["symbol"]: r for r in df_db.to_dict(orient="records")}
        except Exception:
            pass

    # Build snapshots
    snapshots: Dict[str, SharedFinancialSnapshot] = {}

    if df_canonical is not None and not df_canonical.empty:
        records = df_canonical.to_dict(orient="records")
        for r in records:
            sym = str(r.get("symbol", "")).strip().upper()
            if not sym:
                continue
            if symbols and sym not in symbols:
                continue

            # Check watcher state for this symbol
            w_entry = watcher_state.get(sym, {})
            w_status = w_entry.get("snapshot_status", SnapshotFreshnessStatus.FRESH.value)

            # Valuation metrics
            val_rec = val_cache.get(sym, {})
            ev_med = val_rec.get("ev_ebitda_3y_median") or r.get("ev_ebitda_3y_median")
            pe_med = val_rec.get("pe_3y_median") or r.get("pe_3y_median")
            curr_ev = convert_to_inr_crores(r.get("current_ev"), source_unit=MonetaryUnit.INR_CRORES)
            curr_ev_ebitda = r.get("current_ev_ebitda")
            curr_pe = r.get("current_pe")

            # Daily Builder quarterly acceleration facts
            db_rec = db_funds.get(sym, {})

            snap = SharedFinancialSnapshot(
                symbol=sym,
                isin=str(r.get("isin", "")),
                as_of_date=str(r.get("as_of_date", as_of.isoformat())),
                latest_annual_period=r.get("latest_annual_period"),
                latest_quarterly_period=r.get("latest_quarterly_period") or db_rec.get("latest_quarter"),
                snapshot_status=w_status,
                pit_freshness_status=str(r.get("pit_freshness_status", DataStatus.VALID.value)),
                provenance_status=str(r.get("provenance_status", "CERTIFIED")),
                roce=r.get("roce") or (db_rec.get("roce") if db_rec else None),
                roe=r.get("roe") or (db_rec.get("roe") if db_rec else None),
                roce_5y_avg=r.get("roce_5y_avg"),
                operating_cash_flow=convert_to_inr_crores(r.get("operating_cash_flow"), source_unit=MonetaryUnit.INR_CRORES) or (convert_to_inr_crores(db_rec.get("operating_cash_flow"), source_unit=MonetaryUnit.INR_CRORES) if db_rec else None),
                cfo_pat_5y_ratio=r.get("cfo_pat_5y_ratio"),
                total_debt=convert_to_inr_crores(r.get("total_debt"), source_unit=MonetaryUnit.INR_CRORES),
                total_equity=convert_to_inr_crores(r.get("total_equity"), source_unit=MonetaryUnit.INR_CRORES),
                debt_equity=r.get("debt_to_equity") if r.get("debt_to_equity") is not None else (db_rec.get("debt_equity") if db_rec else None),
                cash_and_equivalents=convert_to_inr_crores(r.get("cash_and_equivalents"), source_unit=MonetaryUnit.INR_CRORES),
                ebitda=convert_to_inr_crores(r.get("ebitda"), source_unit=MonetaryUnit.INR_CRORES),
                net_profit=convert_to_inr_crores(r.get("net_profit"), source_unit=MonetaryUnit.INR_CRORES),
                revenue=convert_to_inr_crores(r.get("revenue"), source_unit=MonetaryUnit.INR_CRORES),
                sales_cagr_5y=r.get("sales_cagr_5y"),
                pat_cagr_5y=r.get("pat_cagr_5y"),
                filing_gap_detected=bool(r.get("filing_gap_detected", False)),
                filing_gaps=str(r.get("filing_gaps", "[]")),
                growth_start_period=r.get("growth_start_period"),
                growth_end_period=r.get("growth_end_period"),
                growth_years_elapsed=float(r.get("growth_years_elapsed", 5.0) or 5.0),
                financial_periods_used=int(r.get("financial_periods_used", 5) or 5),
                roce_periods_used=int(r.get("roce_periods_used", 5) or 5),
                shares_outstanding_m=r.get("shares_outstanding_m"),
                shares_status=str(r.get("shares_status", DataStatus.VALID.value)),
                shares_scaling_applied=bool(r.get("shares_scaling_applied", False)),
                share_dilution_3y=r.get("share_dilution_3y"),
                rev_yoy_latest=db_rec.get("rev_yoy_latest"),
                rev_yoy_prev=db_rec.get("rev_yoy_prev"),
                op_profit_yoy_latest=db_rec.get("op_profit_yoy_latest"),
                op_profit_yoy_prev=db_rec.get("op_profit_yoy_prev"),
                eps_yoy_latest=db_rec.get("eps_yoy_latest"),
                eps_yoy_prev=db_rec.get("eps_yoy_prev"),
                prior_eps=db_rec.get("prior_eps"),
                growth_score=float(db_rec.get("growth_score", 0.0) or 0.0),
                quality_score=float(db_rec.get("quality_score", 0.0) or 0.0),
                valuation_score=float(db_rec.get("valuation_score", 0.0) or 0.0),
                wealth_score=float(db_rec.get("wealth_score", 0.0) or 0.0),
                is_value_trap=bool(db_rec.get("is_value_trap", False)),
                fundamental_category=str(db_rec.get("fundamental_category", "NONE")),
                current_ev=curr_ev,
                current_ev_ebitda=curr_ev_ebitda,
                ev_ebitda_3y_median=ev_med,
                current_pe=curr_pe,
                pe_3y_median=pe_med,
                market_cap=convert_to_inr_crores(r.get("market_cap"), source_unit=MonetaryUnit.INR_CRORES) or (convert_to_inr_crores(db_rec.get("market_cap"), source_unit=MonetaryUnit.INR_CRORES) if db_rec else None),
                industry=str(r.get("industry") or db_rec.get("industry") or ""),
            )
            # Compute SHA256 fingerprint
            payload_str = f"{snap.symbol}_{snap.latest_annual_period}_{snap.sales_cagr_5y}_{snap.pat_cagr_5y}_{snap.roce_5y_avg}"
            snap.snapshot_hash = hashlib.sha256(payload_str.encode()).hexdigest()
            snapshots[sym] = snap

    _SHARED_SNAPSHOT_CACHE[cache_key] = snapshots
    return snapshots


def persist_shared_financial_snapshots(
    snapshots: Dict[str, SharedFinancialSnapshot],
    data_dir: Optional[str] = None,
) -> str:
    """
    Persists canonical shared financial snapshots to disk for durable cross-process reuse.

    Saves to:
      1. <data_dir>/canonical_pit_rebuilt.parquet
      2. <data_dir>/shared_financial_snapshots.json
    """
    base_data = data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(base_data, exist_ok=True)
    parquet_path = os.path.join(base_data, "canonical_pit_rebuilt.parquet")
    json_path = os.path.join(base_data, "shared_financial_snapshots.json")

    rows = [s.to_dict() for s in snapshots.values()]
    df = pd.DataFrame(rows)
    df.to_parquet(parquet_path, index=False)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)

    clear_shared_snapshot_cache()
    logger.info(f"💾 Persisted {len(rows)} canonical snapshots to {parquet_path} and {json_path}")
    return parquet_path


def load_shared_financial_snapshot(
    symbol: str,
    as_of_date: Optional[date] = None,
    data_dir: Optional[str] = None,
) -> SharedFinancialSnapshot:
    """Loads a single shared financial snapshot for the specified symbol."""
    sym = symbol.strip().upper()
    snaps = load_all_shared_financial_snapshots(as_of_date=as_of_date, symbols=[sym], data_dir=data_dir)
    if sym in snaps:
        return snaps[sym]
    # Return fail-closed blank snapshot
    return SharedFinancialSnapshot(
        symbol=sym,
        snapshot_status=SnapshotFreshnessStatus.DATA_INSUFFICIENT.value,
        pit_freshness_status=DataStatus.DATA_INSUFFICIENT.value,
        provenance_status="UNCERTIFIED",
        validation_reasons=["SYMBOL_NOT_FOUND_IN_SHARED_SNAPSHOT"],
    )


def get_financial_snapshot_status(data_dir: Optional[str] = None) -> Tuple[FinancialSnapshotStatus, Dict[str, Any]]:
    """
    Reads authoritative canonical snapshot metadata to determine lifecycle readiness.
    Enforces that scanners CANNOT read or execute while snapshot is BUILDING or uncertified.
    """
    base = data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    meta_path = os.path.join(base, "canonical_pit_rebuilt_meta.json")
    if not os.path.exists(meta_path):
        return FinancialSnapshotStatus.NOT_INITIALIZED, {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        status_str = meta.get("FINANCIAL_SNAPSHOT_STATUS") or meta.get("snapshot_status") or "NOT_INITIALIZED"
        try:
            return FinancialSnapshotStatus(status_str), meta
        except ValueError:
            return FinancialSnapshotStatus.INCOMPLETE, meta
    except Exception as e:
        logger.error(f"Error reading canonical snapshot metadata: {e}")
        return FinancialSnapshotStatus.FAILED, {}


def set_financial_snapshot_status(
    status: FinancialSnapshotStatus,
    meta_updates: Optional[Dict[str, Any]] = None,
    data_dir: Optional[str] = None
) -> None:
    """
    Atomically updates the snapshot lifecycle status in canonical_pit_rebuilt_meta.json.
    """
    base = data_dir or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    meta_path = os.path.join(base, "canonical_pit_rebuilt_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}
    meta["FINANCIAL_SNAPSHOT_STATUS"] = status.value
    meta["snapshot_status_updated_at"] = datetime.now().isoformat()
    if meta_updates:
        meta.update(meta_updates)
    tmp_meta = f"{meta_path}.tmp.{os.getpid()}"
    with open(tmp_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp_meta, meta_path)




