# =====================================================================================
# app/multitf/data.py
# MULTI_TF V2 — Data Loading, Freshness Validation, Session Normalisation, Provenance
#
# Responsibility: fetch and validate ALL timeframe data for one symbol. No indicator
# computation here. Everything produced here is passed unchanged to downstream engines.
#
# Key rules:
#   - Never substitute stale data for fresh data silently.
#   - Never infer freshness from DataFrame length alone.
#   - strip_closed_candles() MUST be used before structural calculations.
#   - live_5m is the ONLY forming candle — kept separate from df_5m_closed.
#   - Per-TF provenance is mandatory on every returned object.
# =====================================================================================

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from zoneinfo import ZoneInfo

logger = logging.getLogger("multitf.data")
IST = ZoneInfo("Asia/Kolkata")

# NSE regular session
_NSE_OPEN_H  = 9
_NSE_OPEN_M  = 15
_NSE_CLOSE_H = 15
_NSE_CLOSE_M = 30

# Minutes per timeframe — used for staleness headroom
_TF_MINUTES = {
    "1d":  390,
    "1h":  60,
    "30m": 30,
    "15m": 15,
    "5m":  5,
}


# ─── Result dataclasses ──────────────────────────────────────────────────────

@dataclass
class TFProvenance:
    """Mandatory provenance block per timeframe."""
    interval:        str
    symbol:          str
    source:          str          # provider name / cache layer
    last_candle_ts:  Optional[str] = None   # ISO timestamp of last bar
    retrieved_at:    Optional[str] = None   # ISO timestamp of fetch
    is_stale:        bool = False
    coverage_start:  Optional[str] = None
    coverage_end:    Optional[str] = None
    row_count:       int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interval":       self.interval,
            "symbol":         self.symbol,
            "source":         self.source,
            "last_candle_ts": self.last_candle_ts,
            "retrieved_at":   self.retrieved_at,
            "is_stale":       self.is_stale,
            "coverage_start": self.coverage_start,
            "coverage_end":   self.coverage_end,
            "row_count":      self.row_count,
        }


@dataclass
class MultitfDataBundle:
    """All timeframes for one symbol, fully validated and provenance-tagged."""
    symbol: str

    # Closed candles only (strip_forming_candle applied)
    df_1h:         Optional[pd.DataFrame] = None
    df_30m:        Optional[pd.DataFrame] = None
    df_15m_closed: Optional[pd.DataFrame] = None
    df_5m_closed:  Optional[pd.DataFrame] = None
    df_1d:         Optional[pd.DataFrame] = None   # Daily — for target layer

    # Live (forming) 5m candle — only used for ATTEMPT detection
    live_5m:       Optional[pd.Series] = None

    # Per-TF provenance
    prov_1h:  Optional[TFProvenance] = None
    prov_30m: Optional[TFProvenance] = None
    prov_15m: Optional[TFProvenance] = None
    prov_5m:  Optional[TFProvenance] = None
    prov_1d:  Optional[TFProvenance] = None

    # Freshness flags (set by validate_bundle)
    fresh_15m: bool = False
    fresh_5m:  bool = False
    fresh_1h:  bool = False
    fresh_30m: bool = False

    # Overall data quality
    data_sufficient: bool = False    # True only when 15m + 5m are both fresh
    insufficiency_reason: str = ""

    # IST now at fetch time
    fetched_at: Optional[str] = None


# ─── Core public functions ────────────────────────────────────────────────────

def load_multitf_data(
    symbol: str,
    ist_now: datetime,
    all_1h_data:  Optional[Dict[str, pd.DataFrame]] = None,
    all_30m_data: Optional[Dict[str, pd.DataFrame]] = None,
    all_15m_data: Optional[Dict[str, pd.DataFrame]] = None,
    all_5m_data:  Optional[Dict[str, pd.DataFrame]] = None,
    all_1d_data:  Optional[Dict[str, pd.DataFrame]] = None,
) -> MultitfDataBundle:
    """
    Assembles and validates a MultitfDataBundle from pre-fetched bulk data dicts.

    The caller (scanner.py) is responsible for bulk-fetching all timeframes
    before the symbol loop. This function only reads from those dicts —
    it never makes network calls.
    """
    bundle = MultitfDataBundle(symbol=symbol, fetched_at=ist_now.isoformat())

    def _get(data_dict: Optional[Dict], interval: str) -> Optional[pd.DataFrame]:
        if not data_dict:
            return None
        raw = data_dict.get(symbol)
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return None
        return raw.copy()

    raw_1h  = _get(all_1h_data,  "1h")
    raw_30m = _get(all_30m_data, "30m")
    raw_15m = _get(all_15m_data, "15m")
    raw_5m  = _get(all_5m_data,  "5m")
    raw_1d  = _get(all_1d_data,  "1d")

    # ── Session normalisation + strip forming candle ────────────────────────
    df_1h_cl  = strip_closed_candles(raw_1h,  60,  ist_now) if raw_1h  is not None else None
    df_30m_cl = strip_closed_candles(raw_30m, 30,  ist_now) if raw_30m is not None else None
    df_15m_cl = strip_closed_candles(raw_15m, 15,  ist_now) if raw_15m is not None else None
    df_5m_cl  = strip_closed_candles(raw_5m,  5,   ist_now) if raw_5m  is not None else None
    df_1d_cl  = strip_closed_candles(raw_1d,  390, ist_now) if raw_1d  is not None else None

    # ── Live (forming) 5m candle ────────────────────────────────────────────
    live_5m = _extract_live_candle(raw_5m, ist_now)

    # ── Provenance ──────────────────────────────────────────────────────────
    bundle.prov_1h  = _build_provenance(df_1h_cl,  "1h",  symbol)
    bundle.prov_30m = _build_provenance(df_30m_cl, "30m", symbol)
    bundle.prov_15m = _build_provenance(df_15m_cl, "15m", symbol)
    bundle.prov_5m  = _build_provenance(df_5m_cl,  "5m",  symbol)
    bundle.prov_1d  = _build_provenance(df_1d_cl,  "1d",  symbol)

    # ── Freshness validation ─────────────────────────────────────────────────
    bundle.fresh_1h  = validate_freshness(df_1h_cl,  "1h",  ist_now)
    bundle.fresh_30m = validate_freshness(df_30m_cl, "30m", ist_now)
    bundle.fresh_15m = validate_freshness(df_15m_cl, "15m", ist_now)
    bundle.fresh_5m  = validate_freshness(df_5m_cl,  "5m",  ist_now)

    # ── Assign closed frames ─────────────────────────────────────────────────
    bundle.df_1h         = df_1h_cl
    bundle.df_30m        = df_30m_cl
    bundle.df_15m_closed = df_15m_cl
    bundle.df_5m_closed  = df_5m_cl
    bundle.df_1d         = df_1d_cl
    bundle.live_5m       = live_5m

    # ── Data sufficiency gate ────────────────────────────────────────────────
    # A CONFIRMED_ALERT requires both 15m and 5m to be fresh.
    # WATCHLIST updates only require 15m.
    if not bundle.fresh_15m:
        bundle.data_sufficient = False
        bundle.insufficiency_reason = "STALE_15M"
    elif df_15m_cl is None or len(df_15m_cl) < 24:
        bundle.data_sufficient = False
        bundle.insufficiency_reason = "INSUFFICIENT_15M_BARS"
    else:
        bundle.data_sufficient = True
        bundle.insufficiency_reason = ""

    logger.debug(
        "[%s] data bundle: 15m=%s(%d bars) 5m=%s 1h=%s 30m=%s sufficient=%s",
        symbol,
        "FRESH" if bundle.fresh_15m else "STALE",
        len(df_15m_cl) if df_15m_cl is not None else 0,
        "FRESH" if bundle.fresh_5m  else "STALE",
        "FRESH" if bundle.fresh_1h  else "STALE",
        "FRESH" if bundle.fresh_30m else "STALE",
        bundle.data_sufficient,
    )

    return bundle


def validate_freshness(
    df: Optional[pd.DataFrame],
    interval: str,
    ist_now: datetime,
) -> bool:
    """
    Returns True only when:
      1. df is not None and not empty
      2. df.attrs.get('is_stale') is not True
      3. The most recent bar's timestamp is within the expected recency window

    Never substitutes stale data. Never infers freshness from row count alone.
    """
    if df is None or df.empty:
        return False

    if df.attrs.get("is_stale", False):
        return False

    # During market hours, the most recent bar must be within tf_minutes + 5 min headroom
    if not _is_market_open(ist_now):
        # Outside market hours, any non-empty non-stale frame is acceptable
        return True

    tf_min = _TF_MINUTES.get(interval, 60)
    max_staleness_min = tf_min + 5  # 1 extra period of headroom

    try:
        last_idx = df.index[-1]
        if hasattr(last_idx, "tzinfo") and last_idx.tzinfo is None:
            last_idx = last_idx.tz_localize("Asia/Kolkata")
        elif hasattr(last_idx, "tzinfo") and last_idx.tzinfo is not None:
            last_idx = last_idx.astimezone(IST)
        age_min = (ist_now - last_idx).total_seconds() / 60.0
        return age_min <= max_staleness_min
    except Exception as exc:
        logger.warning("[freshness] %s %s: failed to compute age — %s", interval, df.index[-1], exc)
        return False


def normalize_sessions(
    df: pd.DataFrame,
    interval: str,
    ist_now: datetime,
) -> pd.DataFrame:
    """
    Tags each row with:
      - session_date (IST date)
      - bar_start (IST timestamp)
      - minutes_from_open (minutes since 09:15)

    Used downstream to detect overnight gaps and to enforce session-aware
    consolidation windows.
    """
    if df is None or df.empty:
        return df

    out = df.copy()

    try:
        idx = out.index
        if hasattr(idx[0], "tzinfo") and idx[0].tzinfo is None:
            idx = idx.tz_localize("Asia/Kolkata")
        elif hasattr(idx[0], "tzinfo") and idx[0].tzinfo is not None:
            idx = idx.tz_convert(IST)

        out.index = idx
        out["session_date"] = idx.date
        out["bar_start"]    = idx
        open_minutes = _NSE_OPEN_H * 60 + _NSE_OPEN_M
        out["minutes_from_open"] = (
            idx.hour * 60 + idx.minute - open_minutes
        )
    except Exception as exc:
        logger.warning("[normalize_sessions] %s %s: %s", interval, df.index[0], exc)

    return out


def strip_closed_candles(
    df: Optional[pd.DataFrame],
    tf_minutes: int,
    ist_now: datetime,
) -> Optional[pd.DataFrame]:
    """
    Removes the currently forming (incomplete) candle from the DataFrame.

    Rules:
      - The last bar is considered forming if its expected end time has not yet passed.
      - If we cannot determine with confidence, we strip the last bar (conservative).
      - Returns None if df is None or empty. Returns an empty copy if stripping leaves nothing.
    """
    if df is None or df.empty:
        return df

    try:
        last_idx = df.index[-1]
        if hasattr(last_idx, "tzinfo") and last_idx.tzinfo is None:
            last_idx = pd.Timestamp(last_idx).tz_localize("Asia/Kolkata")
        elif hasattr(last_idx, "tzinfo") and last_idx.tzinfo is not None:
            last_idx = pd.Timestamp(last_idx).tz_convert(IST)

        # A bar that started at T is closed at T + tf_minutes
        bar_close_ts = last_idx + timedelta(minutes=tf_minutes)
        now_ts = ist_now if ist_now.tzinfo else ist_now.replace(tzinfo=IST)

        if now_ts < bar_close_ts:
            # Candle is still forming — remove it
            return df.iloc[:-1].copy()
        else:
            return df.copy()
    except Exception as exc:
        logger.warning("[strip_closed] tf=%dm: %s — stripping last bar conservatively", tf_minutes, exc)
        return df.iloc[:-1].copy() if len(df) > 1 else df.copy()


def build_provenance(
    df: Optional[pd.DataFrame],
    interval: str,
    source: str = "cache",
) -> Dict[str, Any]:
    """Returns a flat provenance dict for inclusion in alert payloads."""
    prov = _build_provenance(df, interval, "?", source)
    return prov.to_dict()


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _build_provenance(
    df: Optional[pd.DataFrame],
    interval: str,
    symbol: str,
    source: str = "cache",
) -> TFProvenance:
    if df is None or df.empty:
        return TFProvenance(interval=interval, symbol=symbol, source=source, is_stale=True)

    try:
        last_ts = str(df.index[-1])
        first_ts = str(df.index[0])
    except Exception:
        last_ts = first_ts = None

    is_stale = bool(df.attrs.get("is_stale", False))
    source_attr = df.attrs.get("source", source)

    return TFProvenance(
        interval=interval,
        symbol=symbol,
        source=str(source_attr),
        last_candle_ts=last_ts,
        retrieved_at=datetime.now(IST).isoformat(),
        is_stale=is_stale,
        coverage_start=first_ts,
        coverage_end=last_ts,
        row_count=len(df),
    )


def _extract_live_candle(
    raw_df: Optional[pd.DataFrame],
    ist_now: datetime,
) -> Optional[pd.Series]:
    """
    Extracts the currently forming 5m candle.

    Returns None if raw_df is None, empty, or market is outside hours.
    The live candle MUST be used exclusively for ATTEMPT detection — never
    for closed-candle structural calculations.
    """
    if raw_df is None or raw_df.empty:
        return None

    if not _is_market_open(ist_now):
        return None

    try:
        last_row = raw_df.iloc[-1]
        last_idx = raw_df.index[-1]
        if hasattr(last_idx, "tzinfo") and last_idx.tzinfo is None:
            last_idx = pd.Timestamp(last_idx).tz_localize("Asia/Kolkata")
        elif hasattr(last_idx, "tzinfo") and last_idx.tzinfo is not None:
            last_idx = pd.Timestamp(last_idx).tz_convert(IST)

        # Only return if the last bar started in the current/recent 5m slot
        bar_start_delta = (ist_now.replace(tzinfo=IST) - last_idx).total_seconds()
        if 0 <= bar_start_delta < 300:
            return last_row
        return None
    except Exception as exc:
        logger.debug("[live_5m] extraction failed: %s", exc)
        return None


def _is_market_open(ist_now: datetime) -> bool:
    """Returns True during NSE regular session (09:15–15:30 Mon–Fri)."""
    if ist_now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    h, m = ist_now.hour, ist_now.minute
    after_open  = (h, m) >= (_NSE_OPEN_H,  _NSE_OPEN_M)
    before_close = (h, m) < (_NSE_CLOSE_H, _NSE_CLOSE_M)
    return after_open and before_close
