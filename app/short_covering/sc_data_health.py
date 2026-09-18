"""
app/short_covering/sc_data_health.py

SC Data Health Gate — Explicit ingestion health assessment for the Short Covering scanner.

Eliminates the silent failure mode where DATA_INSUFFICIENT = 100% is indistinguishable
from a legitimate zero-signal market day.

Architecture:
  ParquetCacheHealth    — trading-session-based freshness (FRESH/STALE/MISSING/CORRUPT)
  ProviderHealthCheck   — ordered 9-step probe: F&O membership → contract resolution →
                          auth → 5M API → response fields → OI_VALID → timestamp
                          freshness → DataFrame normalisation → OIDataService E2E
  SCDataHealthGate      — aggregates both into GREEN / DEGRADED_REDUNDANCY / DEGRADED / BLOCKED
  _select_probe_symbols — dynamic, deterministic, multi-sector sample from live F&O universe

Locked design decisions (do not change without governance sign-off):
  PROBE_COUNT                  = 5 (+ 1 live SC candidate when available)
  SYSTEMIC_DATA_THRESHOLD      = 0.90
  MIN_SYSTEMIC_SAMPLE          = 20
  Parquet-only mode            → BLOCKED (no live OI = scanner cannot operate)
  Parquet FRESH                = last completed trading session only
  Strategy thresholds          = FROZEN (this file is observability-only)

[VERSION: SC_DATA_HEALTH_GATE_v1.1]
"""

import os
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

# ── Locked constants ────────────────────────────────────────────────────────
PROBE_COUNT                  = 5      # Number of F&O symbols in deterministic probe sample
SYSTEMIC_DATA_THRESHOLD      = 0.90   # 90% DATA_INSUFFICIENT → INGESTION_FAILURE
MIN_SYSTEMIC_SAMPLE          = 20     # Only apply systemic check when universe ≥ 20 symbols

# Parquet session-gap thresholds (trading days, not calendar days)
PARQUET_FRESH_MAX_SESSIONS   = 0      # 0 missing sessions = FRESH (last completed session is present)
PARQUET_STALE_MAX_SESSIONS   = 5      # 1–5 missing sessions = STALE
# > 5 missing sessions = MISSING (functionally absent)

# OI series minimum observations for OI_VALID
OI_MIN_OBSERVATIONS          = 2

# Market close time (IST) — used to determine whether today's session is complete
_MARKET_CLOSE                = datetime.strptime("15:30", "%H:%M").time()

# ── Enums ────────────────────────────────────────────────────────────────────
class CacheStatus(str, Enum):
    FRESH   = "FRESH"    # Last completed trading session is present in parquet
    STALE   = "STALE"    # 1–5 trading sessions missing
    MISSING = "MISSING"  # >5 sessions missing OR file absent
    CORRUPT = "CORRUPT"  # File exists but is unreadable / structurally invalid


class ProviderStatus(str, Enum):
    GREEN    = "GREEN"    # All critical probe steps passed
    DEGRADED = "DEGRADED" # Partial — auth + candles returned but OI_VALID or count marginal
    RED      = "RED"      # Any critical step failed
    SKIPPED  = "SKIPPED"  # Provider not configured


class SCDataHealth(str, Enum):
    GREEN               = "GREEN"               # ≥1 provider fully healthy, live OI valid, data fresh
    DEGRADED_REDUNDANCY = "DEGRADED_REDUNDANCY" # Scanner operates, but one provider is unavailable
    DEGRADED            = "DEGRADED"            # Both providers partially working; scanner continues with warning
    BLOCKED             = "BLOCKED"             # No usable live provider + no valid live-OI source


# ── Exhaustive provider decision matrix (locked — 9 rows, covers all (Fyers×Upstox) states) ──
# Do not use loose boolean logic in the aggregator; use this table as the single source of truth.
# Fyers row × Upstox col → SC_DATA_HEALTH outcome
_PROVIDER_DECISION_MATRIX: Dict[Tuple[str, str], SCDataHealth] = {
    # Fyers GREEN
    ("GREEN",    "GREEN"):    SCDataHealth.GREEN,
    ("GREEN",    "RED"):      SCDataHealth.DEGRADED_REDUNDANCY,
    ("GREEN",    "DEGRADED"): SCDataHealth.DEGRADED,
    # Fyers RED
    ("RED",      "GREEN"):    SCDataHealth.DEGRADED_REDUNDANCY,
    ("RED",      "RED"):      SCDataHealth.BLOCKED,
    ("RED",      "DEGRADED"): SCDataHealth.DEGRADED,
    # Fyers DEGRADED
    ("DEGRADED", "GREEN"):    SCDataHealth.DEGRADED,
    ("DEGRADED", "RED"):      SCDataHealth.DEGRADED,
    ("DEGRADED", "DEGRADED"): SCDataHealth.DEGRADED,
    # SKIPPED rows — treat SKIPPED as RED for matrix purposes
    ("SKIPPED",  "GREEN"):    SCDataHealth.DEGRADED_REDUNDANCY,
    ("SKIPPED",  "RED"):      SCDataHealth.BLOCKED,
    ("SKIPPED",  "DEGRADED"): SCDataHealth.DEGRADED,
    ("GREEN",    "SKIPPED"):  SCDataHealth.DEGRADED_REDUNDANCY,
    ("RED",      "SKIPPED"):  SCDataHealth.BLOCKED,
    ("DEGRADED", "SKIPPED"):  SCDataHealth.DEGRADED,
    ("SKIPPED",  "SKIPPED"):  SCDataHealth.BLOCKED,
}


# ── Data classes ─────────────────────────────────────────────────────────────
@dataclass
class ParquetCacheResult:
    symbol:                 str
    parquet_path:           str
    exists:                 bool               = False
    status:                 CacheStatus        = CacheStatus.MISSING
    last_date:              Optional[date]     = None   # Date of last row in parquet
    required_session:       Optional[date]     = None   # Last completed trading session
    missing_sessions:       int                = 0      # Trading sessions absent from cache
    wall_clock_age_minutes: Optional[float]    = None   # Minutes since last row (wall clock)
    total_rows:             int                = 0
    candles_today:          int                = 0      # Rows matching target_date
    error:                  Optional[str]      = None


@dataclass
class ProbeStep:
    name:       str
    passed:     bool
    detail:     str   = ""
    latency_ms: float = 0.0


@dataclass
class OIValidResult:
    """
    Separates OI_DATA_VALID from OI_MOVEMENT_VALID.

    OI_DATA_VALID (data_valid):
      The health gate decision. OI data exists, is numeric, finite, non-negative, and non-zero.
      A flat OI series (all identical values) is still OI_DATA_VALID — it is real data.
      The scanner's own OI-delta logic decides whether flat OI is a tradeable signal.

    OI_MOVEMENT_VALID (movement_valid):
      Informational only. True when ≥2 distinct OI values exist (delta is calculable).
      This is NOT used by the health gate to block/pass the scanner.
    """
    data_valid:      bool             # OI_DATA_VALID — used by health gate
    data_reason:     str              # Explanation of data_valid
    movement_valid:  bool    = False  # OI_MOVEMENT_VALID — informational, not used by gate
    movement_reason: str     = ""     # Explanation of movement_valid
    obs_count:       int     = 0
    oi_max:          Optional[float] = None
    oi_min:          Optional[float] = None


@dataclass
class ProviderHealthResult:
    provider:    str
    status:      ProviderStatus          = ProviderStatus.RED
    steps:       List[ProbeStep]         = field(default_factory=list)
    candle_count: int                    = 0
    oi_valid:    OIValidResult           = field(default_factory=lambda: OIValidResult(data_valid=False, data_reason="not_checked"))
    last_ts:     Optional[datetime]      = None
    error:       Optional[str]           = None
    latency_ms:  float                   = 0.0


@dataclass
class SCDataHealthResult:
    status:               SCDataHealth
    reason:               str
    recommendation:       str
    fyers_result:         Optional[ProviderHealthResult]  = None
    upstox_result:        Optional[ProviderHealthResult]  = None
    parquet_results:      List[ParquetCacheResult]        = field(default_factory=list)
    probe_symbols:        List[str]                       = field(default_factory=list)
    sc_candidate_sym:     Optional[str]                   = None   # The SC candidate symbol selected
    sc_candidate_included: bool                           = False  # True if SC candidate is in probe_symbols
    assessed_at:          datetime                        = field(default_factory=lambda: datetime.now(IST))
    total_latency_ms:     float                           = 0.0

    def is_blocked(self) -> bool:
        return self.status == SCDataHealth.BLOCKED

    def as_log_lines(self) -> List[str]:
        icon = "✅" if self.status == SCDataHealth.GREEN else (
               "⚠️" if self.status in (SCDataHealth.DEGRADED_REDUNDANCY, SCDataHealth.DEGRADED) else "🚨")
        lines = [
            "",
            f"{icon} SC_DATA_HEALTH_GATE  [{self.assessed_at.strftime('%H:%M:%S IST')}]:",
            f"  Overall Health     : {self.status.value}",
            f"  Reason             : {self.reason}",
        ]
        if self.fyers_result:
            oi = self.fyers_result.oi_valid
            oi_str = (f"oi_max={oi.oi_max:.0f}  data_valid={oi.data_valid}  movement_valid={oi.movement_valid}"
                      if oi.oi_max is not None else "oi=N/A")
            lines.append(
                f"  Fyers              : {self.fyers_result.status.value}"
                f"  candles={self.fyers_result.candle_count}  {oi_str}"
                f"  latency={self.fyers_result.latency_ms:.0f}ms"
            )
            if self.fyers_result.error:
                lines.append(f"      └── {self.fyers_result.error}")
        if self.upstox_result:
            oi = self.upstox_result.oi_valid
            oi_str = (f"oi_max={oi.oi_max:.0f}  data_valid={oi.data_valid}  movement_valid={oi.movement_valid}"
                      if oi.oi_max is not None else "oi=N/A")
            lines.append(
                f"  Upstox             : {self.upstox_result.status.value}"
                f"  candles={self.upstox_result.candle_count}  {oi_str}"
                f"  latency={self.upstox_result.latency_ms:.0f}ms"
            )
            if self.upstox_result.error:
                lines.append(f"      └── {self.upstox_result.error}")
        for pr in self.parquet_results:
            age_str = f"{pr.wall_clock_age_minutes:.0f}m ago" if pr.wall_clock_age_minutes is not None else "unknown age"
            lines.append(
                f"  Parquet [{pr.symbol:<12}]: {pr.status.value}"
                f"  gap={pr.missing_sessions}sessions"
                f"  wall_clock={age_str}"
                f"  candles_today={pr.candles_today}"
            )
        if self.sc_candidate_sym:
            included_tag = "included" if self.sc_candidate_included else "not included (duplicate)"
            lines.append(f"  SC Candidate Probe : {self.sc_candidate_sym} ({included_tag})")
        lines.append(f"  Recommendation     : {self.recommendation}")
        lines.append(f"  Total Probe Time   : {self.total_latency_ms:.0f}ms")
        lines.append("")
        return lines


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _last_completed_trading_session(ref: Optional[date] = None) -> date:
    """
    Returns the most recent trading session that has fully completed.
    If today is a trading day AND the market has closed (≥15:30 IST), returns today.
    Otherwise returns the previous trading day.
    """
    try:
        try:
            from app.trading_calendar import is_trading_day, get_previous_trading_date
        except ImportError:
            from trading_calendar import is_trading_day, get_previous_trading_date
        now  = datetime.now(IST)
        today = ref or now.date()
        if is_trading_day(today) and (ref is not None or now.time() >= _MARKET_CLOSE):
            return today
        return get_previous_trading_date(today)
    except Exception:
        # Fallback: walk back to the nearest weekday
        d = ref or datetime.now(IST).date()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d


def _count_missing_trading_sessions(last_date: date, required_session: date) -> int:
    """
    Counts how many completed trading sessions are absent from the parquet cache.
    Returns 0 if last_date >= required_session (cache is fresh).
    Caps the walk at 8 calendar weeks to avoid unbounded loops on very stale files.
    """
    if last_date >= required_session:
        return 0
    try:
        try:
            from app.trading_calendar import is_trading_day
        except ImportError:
            from trading_calendar import is_trading_day
        missing  = 0
        current  = last_date + timedelta(days=1)
        deadline = required_session + timedelta(days=1)    # inclusive upper bound
        max_walk = required_session + timedelta(weeks=8)   # safety cap
        while current < deadline and current <= max_walk:
            if is_trading_day(current):
                missing += 1
            current += timedelta(days=1)
        return missing
    except Exception:
        # Fallback: approximate via calendar arithmetic (5/7 rule, no holiday awareness)
        gap = (required_session - last_date).days
        return max(0, int(gap * 5 / 7))


def _validate_oi_series(oi_series: pd.Series) -> OIValidResult:
    """
    Returns OIValidResult with two independent flags:

    data_valid (OI_DATA_VALID) — used by the health gate:
      1. Series non-empty
      2. Numeric dtype
      3. ≥ OI_MIN_OBSERVATIONS non-NaN values
      4. All values finite (no ±inf)
      5. All values non-negative
      6. max > 0 (not an all-zero placeholder)

    movement_valid (OI_MOVEMENT_VALID) — informational only, NOT used by gate:
      7. ≥ 2 distinct values exist (OI delta is calculable)

    IMPORTANT architectural boundary:
      A flat OI sequence (all identical values) is OI_DATA_VALID = True.
      The scanner's own OI-delta signal condition decides whether flat OI is actionable.
      The health gate must not make that trading decision.
    """
    if oi_series is None or len(oi_series) == 0:
        return OIValidResult(data_valid=False, data_reason="OI series empty")
    if not pd.api.types.is_numeric_dtype(oi_series):
        return OIValidResult(data_valid=False, data_reason=f"OI non-numeric dtype={oi_series.dtype}")
    clean = oi_series.dropna()
    if len(clean) < OI_MIN_OBSERVATIONS:
        return OIValidResult(data_valid=False,
                             data_reason=f"OI insufficient observations: {len(clean)} < {OI_MIN_OBSERVATIONS}",
                             obs_count=len(clean))
    if not np.isfinite(clean.values).all():
        n_inf = (~np.isfinite(clean.values)).sum()
        return OIValidResult(data_valid=False,
                             data_reason=f"OI contains {n_inf} non-finite (inf/-inf) values",
                             obs_count=len(clean))
    if (clean < 0).any():
        return OIValidResult(data_valid=False,
                             data_reason=f"OI contains negative values (min={clean.min():.0f})",
                             obs_count=len(clean), oi_min=float(clean.min()))
    if clean.max() == 0:
        return OIValidResult(data_valid=False,
                             data_reason="OI is all-zero (placeholder/missing data, not real OI)",
                             obs_count=len(clean), oi_max=0.0)

    # OI_DATA_VALID passed — now check OI_MOVEMENT_VALID separately (informational)
    movement_valid  = clean.nunique() >= 2
    movement_reason = (
        f"OI_MOVEMENT_VALID: {clean.nunique()} distinct values across {len(clean)} obs"
        if movement_valid
        else f"OI flat — all {len(clean)} observations equal {clean.iloc[0]:.0f} (delta=0)"
    )
    return OIValidResult(
        data_valid      = True,
        data_reason     = f"OI_DATA_VALID: {len(clean)} obs, max={clean.max():.0f}",
        movement_valid  = movement_valid,
        movement_reason = movement_reason,
        obs_count       = len(clean),
        oi_max          = float(clean.max()),
        oi_min          = float(clean.min()),
    )


def _select_probe_symbols(n: int = PROBE_COUNT) -> Tuple[List[str], Optional[str], bool]:
    """
    Selects a deterministic, multi-sector representative sample from the live F&O universe.

    Strategy:
      1. Load live F&O universe (or fallback list).
      2. Sort by symbol name — stable ordering across repeated diagnostic calls.
      3. Evenly-space n samples — covers different liquidity tiers / alphabet buckets.
      4. Attempt to add one active Short Covering candidate (tests scanner-specific path).
      5. Deduplicate while preserving order — invariant: unique symbols only.

    Returns (probe_symbols, sc_candidate_sym_or_None, sc_candidate_included).
    Invariant: len(probe_symbols) == min(n, len(available_symbols)); all unique.
    """
    # ── Load live universe ────────────────────────────────────────────────────
    all_syms: List[str] = []
    try:
        try:
            from app.short_covering.fno_universe import fno_universe_manager
        except ImportError:
            from short_covering.fno_universe import fno_universe_manager
        all_syms = sorted(fno_universe_manager.get_fno_symbols())
    except Exception as e:
        logger.debug("_select_probe_symbols: fno_universe unavailable (%s), using fallback", e)

    if not all_syms:
        try:
            try:
                from app.short_covering.fno_universe import NSE_FNO_FALLBACK_UNIVERSE
            except ImportError:
                from short_covering.fno_universe import NSE_FNO_FALLBACK_UNIVERSE
            all_syms = sorted(NSE_FNO_FALLBACK_UNIVERSE)
        except Exception:
            all_syms = ["HDFCBANK", "INFY", "RELIANCE", "TATASTEEL", "SUNPHARMA"]

    # ── Deterministic evenly-spaced base sample ───────────────────────────────
    m         = max(1, len(all_syms) // n)
    base_syms = [all_syms[i * m] for i in range(min(n, len(all_syms)))]

    # ── Attempt to fetch one live SC candidate (best-effort) ─────────────────
    sc_candidate: Optional[str] = None
    try:
        try:
            from app.database import get_connection
        except ImportError:
            from database import get_connection
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT symbol FROM short_covering_watchlist
                WHERE is_active = TRUE
                ORDER BY quality_score DESC NULLS LAST, symbol
                LIMIT 1
                """
            )
            row = cur.fetchone()
            if row and row[0]:
                sc_candidate = str(row[0])
        conn.close()
    except Exception:
        pass

    # ── Deduplicate while preserving order ────────────────────────────────────
    # Merge base sample + SC candidate, then deduplicate, then trim to n.
    # Invariant: all symbols in probe are unique; len <= n.
    candidates_ordered = base_syms + ([sc_candidate] if sc_candidate else [])
    seen: set           = set()
    unique: List[str]   = []
    for sym in candidates_ordered:
        if sym not in seen:
            seen.add(sym)
            unique.append(sym)

    probe                = unique[:n]
    sc_candidate_included = (sc_candidate is not None) and (sc_candidate in probe)

    logger.debug(
        "_select_probe_symbols: universe=%d  base=%s  sc_candidate=%s  included=%s  final=%s",
        len(all_syms), base_syms, sc_candidate, sc_candidate_included, probe
    )
    return probe, sc_candidate, sc_candidate_included


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — ParquetCacheHealth
# ══════════════════════════════════════════════════════════════════════════════
class ParquetCacheHealth:
    """
    Classifies the 5M parquet cache using trading-session gap, not wall-clock days.
    Friday → Monday: 0 missing sessions → FRESH.
    A stale fallback must never make the live scanner appear healthy.
    """

    def __init__(self, parquet_dir: Optional[str] = None):
        if parquet_dir is None:
            _here = os.path.dirname(os.path.abspath(__file__))
            _repo = os.path.abspath(os.path.join(_here, "..", ".."))
            parquet_dir = os.path.join(_repo, "data", "history", "5m")
        self.parquet_dir = parquet_dir

    def classify(self, symbol: str, target_date: Optional[date] = None) -> ParquetCacheResult:
        """Full freshness classification for the symbol's parquet file."""
        now          = datetime.now(IST)
        target_date  = target_date or now.date()
        path         = os.path.join(self.parquet_dir, f"{symbol}.parquet")
        result       = ParquetCacheResult(symbol=symbol, parquet_path=path)

        # Required: last completed trading session on or before target_date
        required_session = _last_completed_trading_session(target_date)
        result.required_session = required_session

        if not os.path.exists(path):
            result.status = CacheStatus.MISSING
            result.error  = f"File not found: {path}"
            return result

        result.exists = True
        try:
            df = pd.read_parquet(path)
            if df.empty:
                result.status = CacheStatus.CORRUPT
                result.error  = "Parquet file is empty"
                return result

            result.total_rows = len(df)

            # ── Normalise index to IST-aware DatetimeIndex ────────────────────
            if not isinstance(df.index, pd.DatetimeIndex):
                for col in ("Datetime", "Date", "datetime", "date", "timestamp"):
                    if col in df.columns:
                        df.index = pd.to_datetime(df[col], errors="coerce", utc=True).dt.tz_convert(IST)
                        break
                else:
                    result.status = CacheStatus.CORRUPT
                    result.error  = "No datetime column found in parquet"
                    return result
            elif df.index.tzinfo is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert(IST)

            df = df[df.index.notna()]
            if df.empty:
                result.status = CacheStatus.CORRUPT
                result.error  = "All rows have NaT timestamps"
                return result

            last_ts              = df.index.max()
            result.last_date     = last_ts.date()
            result.wall_clock_age_minutes = (now - last_ts).total_seconds() / 60.0

            # Candles for target date
            target_str         = target_date.strftime("%Y-%m-%d")
            result.candles_today = len(df[df.index.strftime("%Y-%m-%d") == target_str])

            # ── Trading-session gap ───────────────────────────────────────────
            missing = _count_missing_trading_sessions(result.last_date, required_session)
            result.missing_sessions = missing

            if missing <= PARQUET_FRESH_MAX_SESSIONS:
                result.status = CacheStatus.FRESH
            elif missing <= PARQUET_STALE_MAX_SESSIONS:
                result.status = CacheStatus.STALE
            else:
                result.status = CacheStatus.MISSING  # Functionally absent

        except Exception as e:
            result.status = CacheStatus.CORRUPT
            result.error  = str(e)

        return result


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — ProviderHealthCheck
# ══════════════════════════════════════════════════════════════════════════════
class ProviderHealthCheck:
    """
    Full ordered 9-step probe for each broker.

    Probe order (critical — a stale contract mapping must not look like an auth failure):
      1. F&O universe membership
      2. FUT contract resolution        ← catches stale contract mappings
      3. Token / auth present
      4. Client / token accepted
      5. 5M API request succeeds
      6. Response fields present
      7. OI_VALID (not just OI > 0)
      8. Timestamp freshness (current session)
      9. OIDataService E2E returns usable DataFrame
    """

    # ── Shared helpers ────────────────────────────────────────────────────────
    @staticmethod
    def _check_fno_membership(symbol: str) -> ProbeStep:
        t = time.time()
        try:
            try:
                from app.short_covering.fno_universe import fno_universe_manager
            except ImportError:
                from short_covering.fno_universe import fno_universe_manager
            syms = fno_universe_manager.get_fno_symbols()
            ok   = symbol in syms
            return ProbeStep("fno_membership", ok,
                             f"{'in' if ok else 'NOT in'} F&O universe ({len(syms)} symbols)",
                             (time.time() - t) * 1000)
        except Exception as e:
            return ProbeStep("fno_membership", False, str(e), (time.time() - t) * 1000)

    @staticmethod
    def _check_contract(symbol: str, target_date: date) -> Tuple[ProbeStep, str]:
        """Returns (step, near_trading_symbol). Falls back to <SYMBOL>FUT on failure."""
        t        = time.time()
        fallback = f"{symbol}FUT"
        try:
            try:
                from app.short_covering.fno_contract_resolver import fno_contract_resolver
            except ImportError:
                from short_covering.fno_contract_resolver import fno_contract_resolver
            info = fno_contract_resolver.resolve(symbol, target_date)
            near = info.near_trading_symbol
            return (ProbeStep("contract_resolution", True,
                              f"near={near}  expiry={getattr(info, 'near_expiry', '?')}",
                              (time.time() - t) * 1000),
                    near)
        except Exception as e:
            return (ProbeStep("contract_resolution", False,
                              f"Resolver error: {e}",
                              (time.time() - t) * 1000),
                    fallback)

    @staticmethod
    def _check_oi_data_service(symbol: str, target_date: date) -> ProbeStep:
        t = time.time()
        try:
            try:
                from app.short_covering.oi_data_service import OIDataService
            except ImportError:
                from short_covering.oi_data_service import OIDataService
            svc = OIDataService()
            df  = svc.get_intraday_5m_data(symbol, target_date)
            ok  = df is not None and len(df) >= 2
            return ProbeStep("oi_data_service_e2e", ok,
                             f"rows={len(df) if df is not None else 0}",
                             (time.time() - t) * 1000)
        except Exception as e:
            return ProbeStep("oi_data_service_e2e", False, str(e), (time.time() - t) * 1000)

    # ── Fyers probe ───────────────────────────────────────────────────────────
    def probe_fyers(self, symbol: str, target_date: Optional[date] = None) -> ProviderHealthResult:
        t0          = time.time()
        now         = datetime.now(IST)
        target_date = target_date or now.date()
        result      = ProviderHealthResult(provider="FYERS")
        steps       = result.steps

        # Step 1: F&O membership
        steps.append(self._check_fno_membership(symbol))

        # Step 2: Contract resolution
        contract_step, near_sym = self._check_contract(symbol, target_date)
        steps.append(contract_step)
        # Non-fatal: probe continues with fallback symbol

        # Step 3: Token present
        t = time.time()
        try:
            try:
                from app.fyers_auth import get_access_token
            except ImportError:
                from fyers_auth import get_access_token
            token = get_access_token()
            ok    = bool(token)
            steps.append(ProbeStep("token_present", ok,
                                   "OK" if ok else "get_access_token() returned None/empty",
                                   (time.time() - t) * 1000))
            if not ok:
                result.status = ProviderStatus.RED
                result.error  = "No valid Fyers token for today (token DB/file empty)"
                result.latency_ms = (time.time() - t0) * 1000
                return result
        except Exception as e:
            steps.append(ProbeStep("token_present", False, str(e), (time.time() - t) * 1000))
            result.status = ProviderStatus.RED
            result.error  = f"Fyers token import error: {e}"
            result.latency_ms = (time.time() - t0) * 1000
            return result

        # Step 4: Client available (token accepted by SDK)
        t = time.time()
        try:
            try:
                from app.fyers_auth import get_fyers_client
            except ImportError:
                from fyers_auth import get_fyers_client
            client = get_fyers_client()
            ok     = client is not None
            steps.append(ProbeStep("client_available", ok,
                                   "OK" if ok else "get_fyers_client() returned None",
                                   (time.time() - t) * 1000))
            if not ok:
                result.status = ProviderStatus.RED
                result.error  = "Fyers client is None — token may be malformed"
                result.latency_ms = (time.time() - t0) * 1000
                return result
        except Exception as e:
            steps.append(ProbeStep("client_available", False, str(e), (time.time() - t) * 1000))
            result.status = ProviderStatus.RED
            result.error  = f"Fyers client init error: {e}"
            result.latency_ms = (time.time() - t0) * 1000
            return result

        # Step 5: 5M API request
        date_str = target_date.strftime("%Y-%m-%d")
        fyers_candidates = [
            f"NSE:{near_sym}",
            f"NSE:{symbol}-EQ"
        ]
        
        response = None
        http_ok = False
        detail = ""
        
        for fyers_sym in fyers_candidates:
            try:
                response = client.history(data={
                    "symbol":      fyers_sym,
                    "resolution":  "5",
                    "date_format": "1",
                    "range_from":  date_str,
                    "range_to":    date_str,
                    "cont_flag":   "1",
                    "oi_flag":     "1",
                })
                http_ok = bool(response and response.get("s") == "ok")
                detail  = (f"s={response.get('s') if response else 'None'}"
                           f"  code={response.get('code') if response else 'None'}")
                if http_ok:
                    break
            except Exception as e:
                detail = str(e)
                
        t        = time.time()
        steps.append(ProbeStep("api_request", http_ok, detail, (time.time() - t) * 1000))
        if not http_ok:
            result.status = ProviderStatus.RED
            result.error  = f"Fyers API rejected: {detail}"
            result.latency_ms = (time.time() - t0) * 1000
            return result
            steps.append(ProbeStep("api_request", False, str(e), (time.time() - t) * 1000))
            result.status = ProviderStatus.RED
            result.error  = f"Fyers request exception: {e}"
            result.latency_ms = (time.time() - t0) * 1000
            return result

        candles            = response.get("candles", [])
        result.candle_count = len(candles)

        # Step 6: Response fields present (≥7 columns: ts, O, H, L, C, vol, OI)
        t         = time.time()
        has_fields = len(candles) > 0 and len(candles[0]) >= 7
        steps.append(ProbeStep("response_fields", has_fields,
                                f"candle_count={len(candles)}  cols={len(candles[0]) if candles else 0}",
                                (time.time() - t) * 1000))

        # Step 7: OI_VALID
        t = time.time()
        if has_fields:
            oi_raw = pd.Series([float(c[6]) for c in candles if len(c) >= 7])
            result.oi_valid = _validate_oi_series(oi_raw)
        else:
            result.oi_valid = OIValidResult(data_valid=False, data_reason="No fields to extract OI from")
        steps.append(ProbeStep("oi_valid", result.oi_valid.data_valid,
                                result.oi_valid.data_reason, (time.time() - t) * 1000))

        # Step 8: Timestamp freshness (last candle belongs to target_date)
        t     = time.time()
        ts_ok = False
        if candles:
            try:
                last_epoch = candles[-1][0]
                last_ts    = pd.to_datetime(last_epoch, unit="s", utc=True).tz_convert(IST)
                result.last_ts = last_ts.to_pydatetime()
                ts_ok          = last_ts.date() == target_date
                steps.append(ProbeStep("timestamp_freshness", ts_ok,
                                        f"last={last_ts.strftime('%Y-%m-%d %H:%M')}  target={target_date}",
                                        (time.time() - t) * 1000))
            except Exception as e:
                steps.append(ProbeStep("timestamp_freshness", False, str(e), (time.time() - t) * 1000))
        else:
            steps.append(ProbeStep("timestamp_freshness", False, "No candles", (time.time() - t) * 1000))

        # Step 9: OIDataService E2E
        steps.append(self._check_oi_data_service(symbol, target_date))

        # ── Aggregate ─────────────────────────────────────────────────────────
        _CRITICAL = {"token_present", "client_available", "api_request",
                     "oi_data_service_e2e"}
        critical_all_pass = all(s.passed for s in steps if s.name in _CRITICAL)
        if critical_all_pass:
            result.status = (ProviderStatus.GREEN
                             if result.oi_valid.data_valid and result.candle_count >= 2
                             else ProviderStatus.DEGRADED)
        else:
            result.status = ProviderStatus.RED
            failures      = [s.name for s in steps if s.name in _CRITICAL and not s.passed]
            result.error  = f"Critical steps failed: {failures}"

        result.latency_ms = (time.time() - t0) * 1000
        return result

    # ── Upstox probe ──────────────────────────────────────────────────────────
    def probe_upstox(self, symbol: str, target_date: Optional[date] = None) -> ProviderHealthResult:
        t0          = time.time()
        now         = datetime.now(IST)
        target_date = target_date or now.date()
        result      = ProviderHealthResult(provider="UPSTOX")
        steps       = result.steps

        # Step 1: F&O membership
        steps.append(self._check_fno_membership(symbol))

        # Step 2: Contract resolution
        contract_step, near_sym = self._check_contract(symbol, target_date)
        steps.append(contract_step)

        # Step 3: Token present
        t = time.time()
        token: Optional[str] = None
        try:
            try:
                from app import config
            except ImportError:
                import config  # type: ignore
            token = getattr(config, "UPSTOX_ACCESS_TOKEN", None) or os.environ.get("UPSTOX_ACCESS_TOKEN")
            ok    = bool(token)
            steps.append(ProbeStep("token_present", ok,
                                   f"prefix={token[:12]}…" if ok and token and len(token) > 12 else "MISSING",
                                   (time.time() - t) * 1000))
            if not ok:
                result.status = ProviderStatus.RED
                result.error  = "UPSTOX_ACCESS_TOKEN not set in config/environment"
                result.latency_ms = (time.time() - t0) * 1000
                return result
        except Exception as e:
            steps.append(ProbeStep("token_present", False, str(e), (time.time() - t) * 1000))
            result.status = ProviderStatus.RED
            result.error  = f"Upstox token config error: {e}"
            result.latency_ms = (time.time() - t0) * 1000
            return result

        # Step 4: Token accepted (lightweight profile check)
        t = time.time()
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request(
                "https://api.upstox.com/v2/user/profile",
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                profile = _json.loads(resp.read())
            auth_ok = profile.get("status") == "success"
            steps.append(ProbeStep("token_accepted", auth_ok,
                                   f"api_status={profile.get('status')}",
                                   (time.time() - t) * 1000))
            if not auth_ok:
                result.status = ProviderStatus.RED
                result.error  = f"Upstox token rejected (api_status={profile.get('status')})"
                result.latency_ms = (time.time() - t0) * 1000
                return result
        except Exception as e:
            # Network unavailable in offline/dev mode — non-fatal, continue
            steps.append(ProbeStep("token_accepted", False,
                                   f"profile check unavailable: {e}",
                                   (time.time() - t) * 1000))

        # Steps 5–8: UpstoxProvider.fetch_ohlcv
        t = time.time()
        norm = None
        try:
            try:
                from app.market_data.providers.upstox_provider import UpstoxProvider
            except ImportError:
                from market_data.providers.upstox_provider import UpstoxProvider  # type: ignore
            upstox     = UpstoxProvider()
            range_from = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=IST)
            range_to   = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=IST)
            
            # Use base symbol (equity) for Upstox historical candles; OI validation relies on oi_data_service_e2e
            norm       = upstox.fetch_ohlcv(symbol, "5m", range_from, range_to)
        except Exception as e:
            steps.append(ProbeStep("api_request", False, str(e), (time.time() - t) * 1000))
            result.status = ProviderStatus.RED
            result.error  = f"Upstox fetch_ohlcv exception: {e}"
            result.latency_ms = (time.time() - t0) * 1000
            return result

        # Step 5: API request succeeded
        req_ok = norm is not None and (not norm.error)
        steps.append(ProbeStep("api_request", req_ok,
                                norm.error if norm and norm.error else "OK",
                                (time.time() - t) * 1000))
        if not req_ok or norm.dataframe is None or norm.dataframe.empty:
            result.status = ProviderStatus.RED
            result.error  = f"Upstox: {norm.error if norm else 'No response'}"
            result.latency_ms = (time.time() - t0) * 1000
            return result

        df                  = norm.dataframe
        result.candle_count = len(df)

        # Step 6: Response fields
        expected   = {"Open", "High", "Low", "Close", "Volume"}
        has_fields = expected.issubset(df.columns) and len(df) >= 2
        steps.append(ProbeStep("response_fields", has_fields,
                                f"rows={len(df)}  cols={list(df.columns)}",
                                (time.time() - t) * 1000))

        # Step 7: OI_VALID
        t = time.time()
        oi_col = next((c for c in ("OI", "oi", "OpenInterest") if c in df.columns), None)
        if oi_col:
            result.oi_valid = _validate_oi_series(df[oi_col])
        else:
            result.oi_valid = OIValidResult(data_valid=False, data_reason="OI column absent from UpstoxProvider dataframe")
        steps.append(ProbeStep("oi_valid", result.oi_valid.data_valid,
                                result.oi_valid.data_reason, (time.time() - t) * 1000))

        # Step 8: Timestamp freshness
        t = time.time()
        ts_ok = False
        try:
            last_ts = df.index[-1]
            if hasattr(last_ts, "date"):
                ts_ok         = last_ts.date() == target_date
                result.last_ts = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else None
            steps.append(ProbeStep("timestamp_freshness", ts_ok,
                                    f"last={last_ts}  target={target_date}",
                                    (time.time() - t) * 1000))
        except Exception as e:
            steps.append(ProbeStep("timestamp_freshness", False, str(e), (time.time() - t) * 1000))

        # Step 9: OIDataService E2E
        steps.append(self._check_oi_data_service(symbol, target_date))

        # ── Aggregate ─────────────────────────────────────────────────────────
        _CRITICAL = {"token_present", "api_request", "oi_data_service_e2e"}
        critical_all_pass = all(s.passed for s in steps if s.name in _CRITICAL)
        if critical_all_pass:
            result.status = (ProviderStatus.GREEN
                             if result.oi_valid.data_valid and result.candle_count >= 2
                             else ProviderStatus.DEGRADED)
        else:
            result.status = ProviderStatus.RED
            failures      = [s.name for s in steps if s.name in _CRITICAL and not s.passed]
            result.error  = f"Critical steps failed: {failures}"

        result.latency_ms = (time.time() - t0) * 1000
        return result


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — SCDataHealthGate  (aggregator)
# ══════════════════════════════════════════════════════════════════════════════
class SCDataHealthGate:
    """
    Aggregates Fyers + Upstox provider probes + parquet cache health into a
    single SC_DATA_HEALTH decision.

    Decision matrix (locked):
      ≥1 provider GREEN                     → GREEN
      1 GREEN + 1 RED                       → DEGRADED_REDUNDANCY
      Both DEGRADED (partial)               → DEGRADED  (proceed with warning)
      1 DEGRADED + 1 RED                    → DEGRADED  (proceed with warning)
      Both RED  (regardless of parquet)     → BLOCKED   (parquet ≠ live OI)

    Parquet-only mode is BLOCKED — parquet does not provide live OI.
    """

    def __init__(self, parquet_dir: Optional[str] = None):
        self._provider_check = ProviderHealthCheck()
        self._parquet_check  = ParquetCacheHealth(parquet_dir=parquet_dir)

    def assess(
        self,
        target_date: Optional[date] = None,
        probe_symbols: Optional[List[str]] = None,
    ) -> SCDataHealthResult:
        """
        Full data health assessment. Probes all layers and returns the gate decision.
        To be called before the scanner's candidate loop.
        """
        t0          = time.time()
        now         = datetime.now(IST)
        target_date = target_date or now.date()

        # ── Select probe symbols ──────────────────────────────────────────────
        sc_candidate:          Optional[str] = None
        sc_candidate_included: bool          = False
        if probe_symbols:
            symbols = probe_symbols
            # When caller provides symbols, check if any is an SC candidate
            # (best-effort — no DB query here, flag is informational)
        else:
            symbols, sc_candidate, sc_candidate_included = _select_probe_symbols(PROBE_COUNT)

        probe_sym = symbols[0] if symbols else "RELIANCE"

        logger.info(
            "🔍 [SC_DATA_HEALTH] Assessing %s | probe=%s | sc_candidate=%s (included=%s)",
            target_date, probe_sym, sc_candidate or "none", sc_candidate_included
        )

        # ── Provider probes (use first probe symbol as the representative) ────
        fyers_result  = self._provider_check.probe_fyers(probe_sym, target_date)
        upstox_result = self._provider_check.probe_upstox(probe_sym, target_date)

        # ── Parquet health (all probe symbols) ───────────────────────────────
        parquet_results = [self._parquet_check.classify(sym, target_date) for sym in symbols]

        # ── Aggregate via exhaustive decision matrix ───────────────────────────
        # Normalise SKIPPED to RED for matrix key lookup
        f_key = fyers_result.status.value  if fyers_result.status  != ProviderStatus.SKIPPED else "SKIPPED"
        u_key = upstox_result.status.value if upstox_result.status != ProviderStatus.SKIPPED else "SKIPPED"
        matrix_key = (f_key, u_key)

        status = _PROVIDER_DECISION_MATRIX.get(matrix_key, SCDataHealth.BLOCKED)
        if matrix_key not in _PROVIDER_DECISION_MATRIX:
            logger.error(
                "[SC_DATA_HEALTH] Unhandled provider matrix key %s — defaulting to BLOCKED. "
                "Update _PROVIDER_DECISION_MATRIX.", matrix_key
            )

        # Build human-readable reason/recommendation per outcome
        f_s = fyers_result.status.value
        u_s = upstox_result.status.value
        parquet_summary = ", ".join(f"{pr.symbol}={pr.status.value}" for pr in parquet_results)

        if status == SCDataHealth.GREEN:
            reason         = f"Both Fyers ({f_s}) and Upstox ({u_s}) fully healthy with valid live OI"
            recommendation = "Scan proceeds normally"

        elif status == SCDataHealth.DEGRADED_REDUNDANCY:
            live = "Fyers" if f_s == "GREEN" else "Upstox"
            dead = "Upstox" if f_s == "GREEN" else "Fyers"
            dead_s = u_s if f_s == "GREEN" else f_s
            reason         = f"{live} is GREEN; {dead} is {dead_s}. Redundancy reduced."
            recommendation = f"Proceed with {live} only. Restore {dead} to restore full redundancy."

        elif status == SCDataHealth.BLOCKED:
            reason         = (
                f"Both Fyers ({f_s}) and Upstox ({u_s}) provide no usable live OI. "
                f"Parquet: [{parquet_summary}]. "
                f"Short Covering requires live 5M OI — parquet cannot substitute."
            )
            recommendation = (
                "Restore at least one broker token: "
                "UPSTOX_ACCESS_TOKEN (env) or Fyers /login OAuth."
            )

        else:  # DEGRADED
            degrad = [p for p, s in (("Fyers", f_s), ("Upstox", u_s)) if s == "DEGRADED"]
            red    = [p for p, s in (("Fyers", f_s), ("Upstox", u_s)) if s == "RED"]
            parts  = []
            if degrad: parts.append(f"{', '.join(degrad)} DEGRADED")
            if red:    parts.append(f"{', '.join(red)} RED")
            reason         = f"{'; '.join(parts)}. Scanner proceeds with explicit data-confidence warning."
            recommendation = (
                "Monitor DATA_INSUFFICIENT rate. "
                "If >25% in any cycle, investigate the DEGRADED/RED provider immediately."
            )

        total_ms = (time.time() - t0) * 1000
        result   = SCDataHealthResult(
            status                = status,
            reason                = reason,
            recommendation        = recommendation,
            fyers_result          = fyers_result,
            upstox_result         = upstox_result,
            parquet_results       = parquet_results,
            probe_symbols         = symbols,
            sc_candidate_sym      = sc_candidate,
            sc_candidate_included = sc_candidate_included,
            assessed_at           = now,
            total_latency_ms      = total_ms,
        )

        for line in result.as_log_lines():
            if result.is_blocked():
                logger.warning(line)
            else:
                logger.info(line)

        return result

    @staticmethod
    def is_systemic_data_failure(
        data_insufficient_count: int,
        total_candidates: int,
        threshold: float  = SYSTEMIC_DATA_THRESHOLD,
        min_sample: int   = MIN_SYSTEMIC_SAMPLE,
    ) -> bool:
        """
        Post-scan retroactive check: if DATA_INSUFFICIENT / total >= threshold AND
        total >= min_sample, classify the run as INGESTION_FAILURE.

        The pre-scan BLOCKED status is the authoritative gate. This is a safety net
        for the edge case where the probe symbol passed but the broader universe failed.

        Requires total_candidates >= min_sample to avoid false positives on tiny samples
        (e.g. 2/2 = 100% would wrongly trigger on a unit test or tiny watchlist).
        """
        if total_candidates < min_sample:
            logger.debug(
                "[SC_DATA_HEALTH] Systemic check skipped: total_candidates=%d < min_sample=%d",
                total_candidates, min_sample
            )
            return False
        rate       = data_insufficient_count / total_candidates
        is_systemic = rate >= threshold
        if is_systemic:
            logger.warning(
                "🚨 [SC_DATA_HEALTH] Retroactive INGESTION_FAILURE: "
                "%.1f%% (%d/%d) DATA_INSUFFICIENT ≥ %.0f%% threshold "
                "(min_sample=%d satisfied).",
                rate * 100, data_insufficient_count, total_candidates,
                threshold * 100, min_sample
            )
        return is_systemic


# ── Module-level singleton ────────────────────────────────────────────────────
sc_data_health_gate = SCDataHealthGate()
