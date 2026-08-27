# =====================================================================================
# app/signal_contract.py
# V2 SIGNAL CONTRACT — UNIVERSAL DATA VALIDATION & CHECKLIST STATE MACHINE
# =====================================================================================
#
# This module is the foundation for the V2 candidate lifecycle. Every scanner MUST
# route through these primitives before writing to scanner_candidates.
#
# Key guarantees enforced here:
#   1. validate_signal_data()   — universal pre-condition gate, populates data_quality
#                                 provenance block (price_source, volume_source, etc.)
#   2. ChecklistStatus enum     — exactly one status per criterion: CLEARED / PENDING /
#                                 FAILED / WARNING / NOT_APPLICABLE
#   3. evaluate_checklist()     — single source of truth; scanners must NOT build their
#                                 own overlapping arrays
#   4. build_primary_blocker()  — applies priority hierarchy:
#                                   1. Hard Blocker
#                                   2. Trigger Blocker
#                                   3. Risk/R:R Blocker
#                                   4. Quality Deficit
#   5. WatchExplanation         — standardised payload all scanner reason engines return
# =====================================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("signal_contract")
IST = ZoneInfo("Asia/Kolkata")

# -------------------------------------------------------------------------------------
# CHECKLIST STATE MACHINE
# -------------------------------------------------------------------------------------

class ChecklistStatus(Enum):
    """Exactly one of these statuses applies to every criterion, every scan run."""
    CLEARED         = "CLEARED"          # Gate explicitly passed
    PENDING         = "PENDING"          # Gate not yet evaluable (pre-trigger) or awaiting event
    FAILED          = "FAILED"           # Gate explicitly failed
    WARNING         = "WARNING"          # Gate passed but with a caution flag
    NOT_APPLICABLE  = "NOT_APPLICABLE"   # Scanner does not evaluate this criterion


# -------------------------------------------------------------------------------------
# PRIMARY BLOCKER PRIORITY
# -------------------------------------------------------------------------------------

class BlockerPriority(Enum):
    """
    Priority hierarchy for primary_blocker selection.
    Lower integer = higher priority.
    """
    HARD_BLOCKER    = 1   # Structural failure, data invalid, lower-low, support break
    TRIGGER_BLOCKER = 2   # Actionable event (breakout, hourly trigger) has not yet occurred
    RISK_BLOCKER    = 3   # R:R too low, extension too wide, gap too large
    QUALITY_DEFICIT = 4   # Numerically below threshold but setup otherwise intact


# Mapping from commonly-used criterion codes to their blocker category
BLOCKER_CATEGORY: Dict[str, BlockerPriority] = {
    # Hard blockers
    "DATA_INVALID":        BlockerPriority.HARD_BLOCKER,
    "DATA":                BlockerPriority.HARD_BLOCKER,   # scanner criterion code
    "LOWER_LOW":           BlockerPriority.HARD_BLOCKER,
    "NO_LOWER_LOW":        BlockerPriority.HARD_BLOCKER,   # reversal criterion code
    "LOWER_HIGH":          BlockerPriority.HARD_BLOCKER,
    "SUPPORT_FAILURE":     BlockerPriority.HARD_BLOCKER,
    "TREND_INVALID":       BlockerPriority.HARD_BLOCKER,
    "STRUCTURAL_FAILURE":  BlockerPriority.HARD_BLOCKER,
    # Fundamental hard gates (Multibagger)
    "CFO_PAT":             BlockerPriority.HARD_BLOCKER,
    "FCF":                 BlockerPriority.HARD_BLOCKER,
    "PIOTROSKI":           BlockerPriority.HARD_BLOCKER,
    "PLEDGE":              BlockerPriority.HARD_BLOCKER,
    # Trigger blockers
    "BREAKOUT":            BlockerPriority.TRIGGER_BLOCKER,
    "HOURLY_TRIGGER":      BlockerPriority.TRIGGER_BLOCKER,
    "HIGHER_HIGH":         BlockerPriority.TRIGGER_BLOCKER,
    "SUPPORT_RETEST":      BlockerPriority.TRIGGER_BLOCKER,
    "VALUATION_ZONE":      BlockerPriority.TRIGGER_BLOCKER,
    "PRICE_EXPANSION":     BlockerPriority.TRIGGER_BLOCKER,
    "PORTFOLIO_TRIGGER":   BlockerPriority.TRIGGER_BLOCKER,
    # Risk / R:R blockers
    "RR_TOO_LOW":          BlockerPriority.RISK_BLOCKER,
    "GAP_TOO_WIDE":        BlockerPriority.RISK_BLOCKER,
    "EXTENDED":            BlockerPriority.RISK_BLOCKER,
    "EXTENSION":           BlockerPriority.RISK_BLOCKER,   # Multi-TF criterion code
    "ATR_EXTENSION":       BlockerPriority.RISK_BLOCKER,
    "RISK":                BlockerPriority.RISK_BLOCKER,   # generic RISK criterion code
    # Quality deficits
    "VOLUME":              BlockerPriority.QUALITY_DEFICIT,
    "CANDLE":              BlockerPriority.QUALITY_DEFICIT,
    "WEAK_RS":             BlockerPriority.QUALITY_DEFICIT,
    "RS":                  BlockerPriority.QUALITY_DEFICIT,
    "WEAK_SECTOR":         BlockerPriority.QUALITY_DEFICIT,
    "SECTOR":              BlockerPriority.QUALITY_DEFICIT,
    "SCORE_DEFICIT":       BlockerPriority.QUALITY_DEFICIT,
    "DELIVERY_WEAK":       BlockerPriority.QUALITY_DEFICIT,
    "FII_ABSENT":          BlockerPriority.QUALITY_DEFICIT,
    "DELIVERY_5D":         BlockerPriority.QUALITY_DEFICIT,
    "DELIVERY_20D":        BlockerPriority.QUALITY_DEFICIT,
    "FII_DII_FLOW":        BlockerPriority.QUALITY_DEFICIT,
    "ABSORPTION":          BlockerPriority.QUALITY_DEFICIT,
    "BUSINESS_QUALITY":    BlockerPriority.QUALITY_DEFICIT,
    "SECTOR_CAP":          BlockerPriority.RISK_BLOCKER,   # structural portfolio constraint
    "PORTFOLIO_FIT":       BlockerPriority.QUALITY_DEFICIT,
}


def _blocker_priority(blocker_type: str) -> BlockerPriority:
    """Returns the priority category for a blocker type code."""
    return BLOCKER_CATEGORY.get(blocker_type.upper(), BlockerPriority.QUALITY_DEFICIT)


@dataclass
class BlockerCandidate:
    """Represents one failed criterion that could become the primary blocker."""
    blocker_type: str            # e.g. "BREAKOUT", "VOLUME"
    current: Optional[float]     # Observed value
    required: Optional[float]    # Threshold value
    gap_absolute: Optional[float] = None
    gap_pct: Optional[float] = None
    label: Optional[str] = None  # Human-readable label, e.g. "Trigger Blocker"

    @property
    def priority(self) -> BlockerPriority:
        return _blocker_priority(self.blocker_type)

    def to_dict(self) -> Dict[str, Any]:
        cat = self.priority
        return {
            "type":         self.blocker_type,
            "priority":     cat.value,
            "label":        self.label or cat.name.replace("_", " ").title(),
            "current":      self.current,
            "required":     self.required,
            "gap_absolute": self.gap_absolute,
            "gap_pct":      self.gap_pct,
        }


def build_primary_blocker(candidates: List[BlockerCandidate]) -> Optional[Dict[str, Any]]:
    """
    Selects the single primary blocker from a list of failing criteria.

    Priority rule (from specification invariant 2.9):
      1. Hard Blocker      — structural failure, data invalid
      2. Trigger Blocker   — actionable event not yet occurred
      3. Risk/R:R Blocker  — risk parameters not met
      4. Quality Deficit   — numerically below threshold but setup intact

    Within the same priority tier, selects the candidate with the largest |gap_pct|.

    Returns None if candidates list is empty.
    """
    if not candidates:
        return None

    sorted_candidates = sorted(
        candidates,
        key=lambda b: (b.priority.value, -(abs(b.gap_pct) if b.gap_pct is not None else 0.0))
    )
    return sorted_candidates[0].to_dict()


# -------------------------------------------------------------------------------------
# CHECKLIST EVALUATION
# -------------------------------------------------------------------------------------

def evaluate_checklist(
    criterion: str,
    evaluator: Callable[[], Tuple[ChecklistStatus, Optional[float], Optional[float], Optional[str]]]
) -> Tuple[ChecklistStatus, Optional[BlockerCandidate]]:
    """
    Single source of truth for per-criterion classification.

    Scanners must call this function for every criterion — they must NOT manually
    construct cleared/pending/failed arrays in parallel.

    Args:
        criterion:  The criterion code (e.g. "BREAKOUT", "VOLUME", "TREND")
        evaluator:  Callable that returns (ChecklistStatus, current_value, required_value, note)

    Returns:
        (status, blocker_candidate_or_None)
        A BlockerCandidate is returned only when status is FAILED or a risk trigger.
        The caller places the criterion in exactly one list based on the returned status.

    Invariant: a criterion appears in at most ONE of:
        cleared_checklists / pending_checklists / failed_checklists /
        warning_checklists / not_applicable_checklists
    """
    try:
        status, current, required, note = evaluator()
    except Exception as exc:
        logger.warning(f"[evaluate_checklist] evaluator crashed for '{criterion}': {exc}")
        status, current, required, note = ChecklistStatus.FAILED, None, None, str(exc)

    blocker = None
    if status in (ChecklistStatus.FAILED, ChecklistStatus.PENDING):
        gap_absolute = None
        gap_pct = None
        if current is not None and required is not None and required != 0:
            gap_absolute = round(required - current, 4)
            gap_pct = round(abs(gap_absolute) / abs(required) * 100, 2)
        blocker = BlockerCandidate(
            blocker_type=criterion.upper(),
            current=current,
            required=required,
            gap_absolute=gap_absolute,
            gap_pct=gap_pct,
            label=note,
        )

    return status, blocker


def partition_checklists(
    results: Dict[str, Tuple[ChecklistStatus, Optional[BlockerCandidate]]]
) -> Dict[str, List[str]]:
    """
    Converts per-criterion results into the five mutually-exclusive checklist arrays.

    Returns a dict with keys:
        cleared, pending, failed, warning, not_applicable
    """
    partitioned: Dict[str, List[str]] = {
        "cleared":        [],
        "pending":        [],
        "failed":         [],
        "warning":        [],
        "not_applicable": [],
    }
    for criterion, (status, _) in results.items():
        if status == ChecklistStatus.CLEARED:
            partitioned["cleared"].append(criterion)
        elif status == ChecklistStatus.PENDING:
            partitioned["pending"].append(criterion)
        elif status == ChecklistStatus.FAILED:
            partitioned["failed"].append(criterion)
        elif status == ChecklistStatus.WARNING:
            partitioned["warning"].append(criterion)
        elif status == ChecklistStatus.NOT_APPLICABLE:
            partitioned["not_applicable"].append(criterion)
    return partitioned


# -------------------------------------------------------------------------------------
# WATCH EXPLANATION PAYLOAD
# -------------------------------------------------------------------------------------

@dataclass
class WatchExplanation:
    """
    Standard output of all scanner-specific build_*_watch_explanation() functions.
    The dashboard layer is scanner-agnostic — it always reads this dataclass.
    """
    cleared:            List[str] = field(default_factory=list)
    pending:            List[str] = field(default_factory=list)
    failed:             List[str] = field(default_factory=list)
    warning:            List[str] = field(default_factory=list)
    not_applicable:     List[str] = field(default_factory=list)
    primary_blocker:    Optional[Dict[str, Any]] = None
    primary_blocker_type: Optional[str] = None
    next_required_event: Optional[str] = None
    health_status:      Optional[str] = None   # IMPROVING | STABLE | DETERIORATING | INVALIDATED
    health_reason:      Optional[str] = None
    last_change_summary: Optional[str] = None

    @property
    def checks_cleared(self) -> int:
        return len(self.cleared)

    @property
    def checks_total(self) -> int:
        return len(self.cleared) + len(self.pending) + len(self.failed) + len(self.warning)


# -------------------------------------------------------------------------------------
# DATA VALIDATION CONTRACT
# -------------------------------------------------------------------------------------

@dataclass
class SignalValidationResult:
    """
    Result of validate_signal_data(). Passed to every scanner before Watch/BUY evaluation.

    data_quality is a dict that MUST contain all five provenance fields:
        price_source, volume_source, fundamental_source, feature_timestamp, data_provider_mix
    """
    is_valid: bool
    reason: str
    data_quality: Dict[str, Any] = field(default_factory=dict)

    def _check_provenance(self) -> None:
        required_keys = {
            "price_source", "volume_source", "fundamental_source",
            "feature_timestamp", "data_provider_mix"
        }
        missing = required_keys - set(self.data_quality.keys())
        if missing:
            logger.warning(
                f"[signal_contract] data_quality missing provenance fields: {missing}. "
                "All five provenance fields are mandatory per V2 specification."
            )


def validate_signal_data(
    symbol: str,
    scanner_name: str,
    ohlcv_df,           # pd.DataFrame expected
    price_source: str = "unknown",
    volume_source: str = "unknown",
    fundamental_source: str = "unknown",
    feature_timestamp: Optional[datetime] = None,
    extra_checks: Optional[Callable] = None,
) -> SignalValidationResult:
    """
    Universal data validation gate — must be called by every scanner before any
    Watch or BUY evaluation. Equivalent to 'Data Valid' in the Day-0 Fast Track.

    Checks:
      1. DataFrame is not None / empty
      2. Required OHLCV columns present
      3. At least 50 rows of history
      4. No NaN in recent Close, High, Low
      5. Latest Close > 0, Volume > 0
      6. Close within plausible range (0 < Close < 1,000,000)

    Feature provenance block (mandatory in data_quality):
        price_source, volume_source, fundamental_source, feature_timestamp, data_provider_mix
    """
    now_ist = feature_timestamp or datetime.now(IST)

    base_provenance = {
        "price_source":       price_source,
        "volume_source":      volume_source,
        "fundamental_source": fundamental_source,
        "feature_timestamp":  now_ist.isoformat(),
        "data_provider_mix":  (
            f"price={price_source}, volume={volume_source}, "
            f"fundamentals={fundamental_source}"
        ),
    }

    def _fail(reason: str) -> SignalValidationResult:
        result = SignalValidationResult(is_valid=False, reason=reason, data_quality=base_provenance)
        result._check_provenance()
        return result

    # 1. Null / empty check
    if ohlcv_df is None:
        return _fail("DataFrame is None")

    try:
        is_empty = ohlcv_df.empty
    except Exception:
        return _fail("DataFrame object is invalid")

    if is_empty:
        return _fail("DataFrame is empty")

    # 2. Required columns
    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    missing_cols = required_cols - set(ohlcv_df.columns)
    if missing_cols:
        return _fail(f"Missing OHLCV columns: {missing_cols}")

    # 3. Minimum history
    if len(ohlcv_df) < 50:
        return _fail(f"Insufficient history: {len(ohlcv_df)} rows (minimum 50 required)")

    # 4. Recent NaN check (last 5 rows)
    tail = ohlcv_df.tail(5)
    for col in ("Close", "High", "Low"):
        if tail[col].isna().any():
            return _fail(f"NaN values in recent {col} column")

    # 5. Latest bar sanity
    try:
        latest_close  = float(ohlcv_df["Close"].iloc[-1])
        latest_volume = float(ohlcv_df["Volume"].iloc[-1])
    except Exception as exc:
        return _fail(f"Cannot read latest bar: {exc}")

    if latest_close <= 0:
        return _fail(f"Latest Close is non-positive: {latest_close}")
    if latest_close >= 1_000_000:
        return _fail(f"Latest Close implausibly large: {latest_close}")
    if latest_volume < 0:
        return _fail(f"Latest Volume is negative: {latest_volume}")

    # 6. Optional scanner-specific checks
    if extra_checks is not None:
        try:
            extra_result = extra_checks(ohlcv_df)
            if extra_result is not None and not extra_result:
                return _fail("Scanner-specific data check failed")
        except Exception as exc:
            return _fail(f"Scanner-specific data check raised: {exc}")

    result = SignalValidationResult(
        is_valid=True,
        reason="Valid",
        data_quality={
            **base_provenance,
            "row_count":     len(ohlcv_df),
            "latest_close":  round(latest_close, 2),
            "latest_volume": round(latest_volume, 0),
        }
    )
    result._check_provenance()
    return result


# -------------------------------------------------------------------------------------
# STATE MACHINE GUARD
# -------------------------------------------------------------------------------------

# Allowed state transitions only. All other transitions are forbidden by specification.
ALLOWED_TRANSITIONS: Dict[str, set] = {
    "WATCH":     {"CANDIDATE", "EXPIRED"},
    "CANDIDATE": {"CONFIRMED", "MISSED", "EXPIRED"},
    # Terminal states — no outgoing transitions
    "CONFIRMED": set(),
    "MISSED":    set(),
    "EXPIRED":   set(),
}


class ForbiddenStateTransitionError(Exception):
    """Raised when code attempts an invalid state transition."""
    pass


def assert_valid_transition(current_state: str, new_state: str, setup_id: str = "") -> None:
    """
    Enforces the frozen state machine.

    Raises ForbiddenStateTransitionError for any forbidden transition, including:
        WATCH   → MISSED          (forbidden: MISSED requires a trigger / CANDIDATE state)
        WATCH   → CONFIRMED       (forbidden: must pass through CANDIDATE)
        EXPIRED → any active state (terminal)
        MISSED  → any active state (terminal)
        CONFIRMED → any state     (terminal)
    """
    allowed = ALLOWED_TRANSITIONS.get(current_state, set())
    if new_state not in allowed:
        raise ForbiddenStateTransitionError(
            f"[state_machine] FORBIDDEN transition: {current_state!r} → {new_state!r} "
            f"for setup_id={setup_id!r}. "
            f"Allowed from {current_state!r}: {allowed or 'none (terminal state)'}"
        )
