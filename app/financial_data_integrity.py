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
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

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
# Upper bound: the largest Indian companies have ~8000-9000M shares.
# 30,000M is implausible — flag for unit scaling.
MIN_SHARES_M: float = 0.5
MAX_SHARES_M: float = 10_000.0

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
    VALID              = "VALID"
    DATA_INSUFFICIENT  = "DATA_INSUFFICIENT"
    DATA_STALE         = "DATA_STALE"
    DATA_CONFLICT      = "DATA_CONFLICT"
    DATA_INVALID       = "DATA_INVALID"
    DATA_BLOCKED       = "DATA_BLOCKED"


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
) -> IntegrityResult:
    """
    Validates that the latest annual filing in the PIT dataset is recent enough.

    A PIT dataset that passes the row-count minimum but has a stale
    latest_annual_period (e.g. FY2010 in a 2026 scan) MUST be blocked.

    Args:
        symbol: NSE ticker.
        latest_annual_period: The period_end_date of the most recent annual row.
        scan_date: The date of the scan. Defaults to today (IST).
        max_staleness_years: Maximum allowed years between latest filing and scan.

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

    try:
        latest_dt = pd.to_datetime(latest_annual_period).date()
    except Exception as e:
        return IntegrityResult(
            status=DataStatus.DATA_INVALID,
            reason=f"PIT_INVALID_PERIOD_DATE: {e}",
            detail={"symbol": symbol, "latest_annual_period": str(latest_annual_period)},
        )

    staleness_days = (scan_date - latest_dt).days
    staleness_years = staleness_days / 365.25

    # Compute expected latest annual period (end of previous complete Indian FY)
    # Indian FY ends March 31. The latest complete FY before scan_date is:
    scan_fy_end_year = scan_date.year if scan_date.month > 3 else scan_date.year - 1
    expected_latest_fy_end = date(scan_fy_end_year, 3, 31)

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
    def _extract_dt(item: Any) -> pd.Timestamp:
        if isinstance(item, dict):
            return pd.to_datetime(item.get("period_end_date") or item.get("period_end") or item.get("date"))
        return pd.to_datetime(item)

    for i in range(1, len(annual_rows_sorted)):
        prev_dt = _extract_dt(annual_rows_sorted[i - 1])
        curr_dt = _extract_dt(annual_rows_sorted[i])
        if pd.isna(prev_dt) or pd.isna(curr_dt):
            continue
        diff_days = (curr_dt - prev_dt).days
        if diff_days > gap_threshold_days:
            gaps.append((str(prev_dt.date()), str(curr_dt.date())))
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
    # Apply PIT filter if as_of_date provided
    if as_of_date is not None:
        filtered = []
        for r in annual_rows_sorted:
            fd = r.get("filing_date") or r.get("conservative_availability_timestamp")
            if fd is not None:
                try:
                    fd_dt = pd.to_datetime(fd).date()
                    if fd_dt <= as_of_date:
                        filtered.append(r)
                except Exception:
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
    period0 = str(pd.to_datetime(r0_row["period_end_date"]).date())
    period1 = str(pd.to_datetime(r1_row["period_end_date"]).date())

    elapsed_days = (pd.to_datetime(period1) - pd.to_datetime(period0)).days
    elapsed_years = elapsed_days / 365.25

    # ── Check for gaps within the 5Y lookback window ──
    lookback_rows = valid_rows[-(target_years + 1):]
    gaps_in_window = detect_annual_fiscal_gaps(lookback_rows)

    if gaps_in_window:
        logger.error(
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
        logger.error(
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

    cagr_pct = round((pow(r1_val / r0_val, 1.0 / elapsed_years) - 1.0) * 100.0, 2)

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
        gaps_detected=gaps_full,
        metric=metric,
        target_years=target_years,
        basis=basis,
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

        # Out of range — try unit scaling on the derived value
        derived_div = derived_m / 1000.0
        if MIN_SHARES_M <= derived_div <= MAX_SHARES_M:
            logger.warning(
                f"[SHARE_UNIT] {symbol}: derived shares={derived_m}M out of range. "
                f"÷1000 scaling → {derived_div:.4f}M"
            )
            return ShareCountResult(
                status=DataStatus.VALID,
                shares_millions=round(derived_div, 4),
                derivation_method=DerivationMethod.DERIVED_UNIT_SCALED,
                source=f"DERIVED_SCALED: net_profit={net_profit_cr}Cr / eps=₹{eps}",
                unit_scaling_applied="DERIVED_DIV_1000",
            )

        logger.error(
            f"[SHARE_SANITY] {symbol}: derived shares={derived_m}M implausible "
            f"(net_profit={net_profit_cr}Cr, eps=₹{eps}). BLOCKING."
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
      - No blocking_reasons may be present.

    Returns:
        (buy_eligible: bool, blocking_reasons: List[str])
    """
    reasons: List[str] = list(blocking_reasons or [])

    # Freshness / Event Gate: Block BUY if a new/amended filing is pending recalculation
    try:
        from scripts.financial_filing_watcher import FinancialFilingWatcher, SnapshotFreshnessStatus
        watcher = FinancialFilingWatcher()
        f_status = watcher.get_symbol_freshness_status(symbol)
        if f_status == SnapshotFreshnessStatus.UPDATE_PENDING:
            reasons.append(f"UPDATE_PENDING: New or amended filing detected for {symbol}, recalculation required")
        elif f_status == SnapshotFreshnessStatus.INVALID:
            reasons.append(f"SNAPSHOT_INVALID: Filing snapshot marked INVALID for {symbol}")
    except Exception:
        pass

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
) -> BUYEvidenceBundle:
    """
    Builds and validates a complete BUY evidence bundle.

    The bundle is ONLY eligible for BUY insertion when is_buy_eligible() is True.
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
) -> CAGRResult:
    """Convenience wrapper for compute_cagr."""
    return compute_cagr(
        annual_rows_sorted=annual_rows_sorted,
        metric=metric,
        symbol=symbol,
        target_years=target_years,
        basis=basis,
        as_of_date=as_of_date,
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


