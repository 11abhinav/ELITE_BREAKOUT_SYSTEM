# =====================================================================================
# app/candidate_analytics_engine.py
# V2 POST-SIGNAL OUTCOME ANALYTICS ENGINE
# =====================================================================================
#
# Computes post-event outcome analytics for both:
#
#   1. scanner_candidates (MISSED/CONFIRMED) — directly from the candidate row
#   2. near_misses         — via near_miss_outcomes table (SEPARATE from near_misses)
#
# Outcomes measured from the original hypothetical entry price at time of signal/rejection.
# Never from a later price.
#
# Rejection verdicts (specification §4):
#   GOOD_REJECTION    — SL hit before T1 (scanner was right to reject)
#   BAD_REJECTION     — T1 hit before SL (scanner missed a winner)
#   NEUTRAL           — neither T1 nor SL hit within 60-day window
#   UNRESOLVED_DATA   — insufficient data, or same-candle ambiguity (T1 and SL both
#                       inside the same candle's High-Low range)
#
# Same-candle ambiguity rule (specification §4 CAUTION):
#   When T1 and SL are both within the High-Low of the same daily candle:
#     → If reliable intraday (minute-bar) data is available: use it.
#     → Otherwise: UNRESOLVED_DATA — never fabricate chronological ordering.
#
# Near-miss isolation rule (specification §2.10):
#   This engine READS from near_misses but NEVER modifies it.
#   All new analytics go into near_miss_outcomes only.
# =====================================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from database import get_connection, IST

logger = logging.getLogger("candidate_analytics_engine")

# -------------------------------------------------------------------------------------
# CONSTANTS
# -------------------------------------------------------------------------------------

OUTCOME_HORIZONS = [1, 3, 5, 10, 20, 60]   # calendar days for return computation
VERDICT_GOOD      = "GOOD_REJECTION"
VERDICT_BAD       = "BAD_REJECTION"
VERDICT_NEUTRAL   = "NEUTRAL"
VERDICT_UNRESOLVED = "UNRESOLVED_DATA"

# -------------------------------------------------------------------------------------
# DATACLASSES
# -------------------------------------------------------------------------------------

@dataclass
class OutcomeResult:
    """Full post-signal outcome record for one candidate/near-miss."""
    return_1d:          Optional[float] = None
    return_3d:          Optional[float] = None
    return_5d:          Optional[float] = None
    return_10d:         Optional[float] = None
    return_20d:         Optional[float] = None
    return_60d:         Optional[float] = None
    mfe:                Optional[float] = None   # Maximum Favourable Excursion (%)
    mae:                Optional[float] = None   # Maximum Adverse Excursion (%)
    hypothetical_r:     Optional[float] = None   # (T1 - entry) / (entry - SL)
    rejection_verdict:  str = VERDICT_UNRESOLVED

    def as_dict(self) -> Dict[str, Any]:
        return {
            "return_1d":         self.return_1d,
            "return_3d":         self.return_3d,
            "return_5d":         self.return_5d,
            "return_10d":        self.return_10d,
            "return_20d":        self.return_20d,
            "return_60d":        self.return_60d,
            "mfe":               self.mfe,
            "mae":               self.mae,
            "hypothetical_r":    self.hypothetical_r,
            "rejection_verdict": self.rejection_verdict,
        }


# -------------------------------------------------------------------------------------
# OHLCV FETCH HELPER
# -------------------------------------------------------------------------------------

def _fetch_ohlcv_after(
    symbol: str,
    from_date: date,
    days: int = 65,
    conn=None,
) -> Optional[pd.DataFrame]:
    """
    Fetches OHLCV data for `symbol` from `from_date` onwards (up to `days` calendar days).
    Reads from the existing daily_ohlcv / stock_analysis_master tables.

    Returns a DataFrame with columns: Date, Open, High, Low, Close, Volume
    or None if insufficient data is found.
    """
    to_date = from_date + timedelta(days=days)
    should_close = conn is None

    try:
        if conn is None:
            ctx = get_connection()
            conn = ctx.__enter__()

        with conn.cursor() as cur:
            # Try the canonical daily OHLCV table first
            cur.execute("""
                SELECT trade_date AS "Date",
                       open  AS "Open",
                       high  AS "High",
                       low   AS "Low",
                       close AS "Close",
                       volume AS "Volume"
                  FROM daily_ohlcv
                 WHERE symbol = %s
                   AND trade_date >= %s
                   AND trade_date <= %s
                 ORDER BY trade_date ASC
            """, (symbol, from_date, to_date))
            rows = cur.fetchall()
            if rows:
                cols = [desc[0] for desc in cur.description]
                df = pd.DataFrame(rows, columns=cols)
                df["Date"] = pd.to_datetime(df["Date"])
                return df

        # Fallback: check stock_analysis_master / cached data
        logger.debug(
            f"[analytics] No daily_ohlcv rows for {symbol} after {from_date}; "
            "returning None (UNRESOLVED_DATA)"
        )
        return None

    except Exception as exc:
        logger.warning(f"[analytics] OHLCV fetch failed for {symbol}: {exc}")
        return None
    finally:
        if should_close and conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass


# -------------------------------------------------------------------------------------
# CORE COMPUTATION
# -------------------------------------------------------------------------------------

def _pct(numerator: float, denominator: float) -> Optional[float]:
    if not denominator or denominator == 0:
        return None
    return round((numerator / denominator) * 100, 2)


def compute_outcome(
    symbol: str,
    entry_price: float,
    stop_loss: float,
    target_1: float,
    signal_date: date,
    ohlcv_df: Optional[pd.DataFrame] = None,
    intraday_df: Optional[pd.DataFrame] = None,
) -> OutcomeResult:
    """
    Computes the complete OutcomeResult for one signal.

    Args:
        symbol:       Stock symbol
        entry_price:  Hypothetical entry at time of signal (immutable anchor)
        stop_loss:    Hypothetical SL
        target_1:     Hypothetical T1
        signal_date:  Date of the original signal / rejection
        ohlcv_df:     Pre-fetched daily OHLCV (Date, Open, High, Low, Close, Volume).
                      If None, fetched from DB.
        intraday_df:  Optional minute-bar data for same-candle ambiguity resolution.
                      If None and ambiguity occurs, defaults to UNRESOLVED_DATA.

    Returns OutcomeResult with all fields populated.
    """
    result = OutcomeResult()

    # Compute hypothetical_r (static, based on entry/SL/T1 geometry)
    if stop_loss and entry_price and stop_loss < entry_price and target_1 > entry_price:
        risk_pts = entry_price - stop_loss
        reward_pts = target_1 - entry_price
        result.hypothetical_r = round(reward_pts / risk_pts, 2) if risk_pts > 0 else None

    # Fetch OHLCV if not provided
    if ohlcv_df is None:
        ohlcv_df = _fetch_ohlcv_after(symbol, signal_date)

    if ohlcv_df is None or ohlcv_df.empty:
        logger.debug(f"[analytics] No OHLCV data for {symbol} — UNRESOLVED_DATA")
        result.rejection_verdict = VERDICT_UNRESOLVED
        return result

    # Ensure Date column is datetime
    if not pd.api.types.is_datetime64_any_dtype(ohlcv_df["Date"]):
        ohlcv_df["Date"] = pd.to_datetime(ohlcv_df["Date"])

    # Filter to post-signal rows only
    signal_dt = pd.Timestamp(signal_date)
    post = ohlcv_df[ohlcv_df["Date"] > signal_dt].copy().reset_index(drop=True)

    if post.empty:
        result.rejection_verdict = VERDICT_UNRESOLVED
        return result

    closes = post["Close"].values
    highs = post["High"].values
    lows = post["Low"].values

    # ── Multi-horizon returns ──────────────────────────────────────────────────────
    for horizon in OUTCOME_HORIZONS:
        if len(closes) >= horizon:
            ret = _pct(closes[horizon - 1] - entry_price, entry_price)
            setattr(result, f"return_{horizon}d", ret)

    # ── MFE and MAE (over 60 calendar days) ───────────────────────────────────────
    window = post.head(60)
    if not window.empty:
        max_high = window["High"].max()
        min_low = window["Low"].min()
        result.mfe = _pct(max_high - entry_price, entry_price)
        result.mae = _pct(min_low - entry_price, entry_price)

    # ── Rejection verdict ──────────────────────────────────────────────────────────
    result.rejection_verdict = _determine_verdict(
        post=post,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        intraday_df=intraday_df,
    )

    return result


def _determine_verdict(
    post: pd.DataFrame,
    entry_price: float,
    stop_loss: float,
    target_1: float,
    intraday_df: Optional[pd.DataFrame],
) -> str:
    """
    Determines GOOD_REJECTION / BAD_REJECTION / NEUTRAL / UNRESOLVED_DATA
    from the post-signal price series.

    Same-candle ambiguity rule:
      If both SL and T1 are inside the same candle's High-Low range:
        → Use intraday_df if available (look for which level was hit first in minute bars)
        → Otherwise: UNRESOLVED_DATA (never fabricate ordering)
    """
    sl_hit_idx = None
    t1_hit_idx = None

    for i, row in post.iterrows():
        high = float(row["High"])
        low = float(row["Low"])

        sl_touched = (stop_loss is not None and low <= stop_loss)
        t1_touched = (target_1 is not None and high >= target_1)

        if sl_touched and t1_touched:
            # ── Same-candle ambiguity ──────────────────────────────────────────────
            resolved = _resolve_same_candle(
                candle_date=row["Date"],
                stop_loss=stop_loss,
                target_1=target_1,
                intraday_df=intraday_df,
            )
            if resolved == "SL_FIRST":
                return VERDICT_GOOD
            elif resolved == "T1_FIRST":
                return VERDICT_BAD
            else:
                return VERDICT_UNRESOLVED

        if sl_touched and sl_hit_idx is None:
            sl_hit_idx = i
        if t1_touched and t1_hit_idx is None:
            t1_hit_idx = i

    if sl_hit_idx is None and t1_hit_idx is None:
        return VERDICT_NEUTRAL

    if sl_hit_idx is not None and t1_hit_idx is None:
        return VERDICT_GOOD

    if t1_hit_idx is not None and sl_hit_idx is None:
        return VERDICT_BAD

    # Both hit on different candles — order by index
    if sl_hit_idx < t1_hit_idx:
        return VERDICT_GOOD
    elif t1_hit_idx < sl_hit_idx:
        return VERDICT_BAD
    else:
        # Same index, different rows — should not happen but safe default
        return VERDICT_UNRESOLVED


def _resolve_same_candle(
    candle_date: Any,
    stop_loss: float,
    target_1: float,
    intraday_df: Optional[pd.DataFrame],
) -> str:
    """
    Resolves same-candle ambiguity using intraday data.

    Returns:
        "SL_FIRST"  — SL was hit before T1 on intraday bars
        "T1_FIRST"  — T1 was hit before SL
        "UNRESOLVED" — intraday data not available or inconclusive
    """
    if intraday_df is None or intraday_df.empty:
        logger.debug(
            f"[analytics] Same-candle ambiguity on {candle_date} — "
            "no intraday data → UNRESOLVED_DATA"
        )
        return "UNRESOLVED"

    try:
        candle_dt = pd.Timestamp(candle_date)
        # Support both DatetimeIndex (default) and a "Datetime" column
        if "Datetime" in intraday_df.columns:
            time_series = pd.to_datetime(intraday_df["Datetime"])
            day_bars = intraday_df[time_series.dt.date == candle_dt.date()].copy()
        else:
            # Index-based — DatetimeIndex doesn't have .dt, use direct comparison
            idx = pd.DatetimeIndex(intraday_df.index)
            day_bars = intraday_df[idx.date == candle_dt.date()].copy()

        if day_bars.empty:
            return "UNRESOLVED"

        sl_time = None
        t1_time = None

        for idx, bar in day_bars.iterrows():
            bar_low = float(bar.get("Low", bar.get("low", float("inf"))))
            bar_high = float(bar.get("High", bar.get("high", float("-inf"))))
            bar_time = idx if isinstance(idx, pd.Timestamp) else pd.Timestamp(bar.get("Datetime", idx))

            if bar_low <= stop_loss and sl_time is None:
                sl_time = bar_time
            if bar_high >= target_1 and t1_time is None:
                t1_time = bar_time

            if sl_time and t1_time:
                break

        if sl_time and t1_time:
            return "SL_FIRST" if sl_time <= t1_time else "T1_FIRST"
        elif sl_time:
            return "SL_FIRST"
        elif t1_time:
            return "T1_FIRST"
        else:
            return "UNRESOLVED"

    except Exception as exc:
        logger.warning(f"[analytics] Intraday resolution failed for {candle_date}: {exc}")
        return "UNRESOLVED"


# -------------------------------------------------------------------------------------
# NEAR-MISS OUTCOMES WRITER
# -------------------------------------------------------------------------------------

def compute_and_store_near_miss_outcome(
    near_miss_id: int,
    symbol: str,
    entry_price: float,
    stop_loss: float,
    target_1: float,
    signal_date: date,
    conn,
    *,
    overwrite: bool = False,
) -> Optional[OutcomeResult]:
    """
    Computes the outcome for one near_miss record and upserts into near_miss_outcomes.

    ISOLATION INVARIANT: This function reads near_misses.id but NEVER modifies near_misses.
    All writes go to near_miss_outcomes only.

    Args:
        near_miss_id: PK from near_misses table
        overwrite:    If True, re-evaluate even if an outcome already exists.
                      Default False (skip already-evaluated records).

    Returns the OutcomeResult if computed, None if skipped.
    """
    # Check if already evaluated
    if not overwrite:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM near_miss_outcomes WHERE near_miss_id = %s",
                (near_miss_id,)
            )
            if cur.fetchone():
                logger.debug(f"[analytics] near_miss_id={near_miss_id} already evaluated — skip")
                return None

    # Fetch OHLCV
    ohlcv_df = _fetch_ohlcv_after(symbol, signal_date, conn=conn)

    result = compute_outcome(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        signal_date=signal_date,
        ohlcv_df=ohlcv_df,
    )

    now = datetime.now(IST)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO near_miss_outcomes (
                near_miss_id,
                return_1d, return_3d, return_5d, return_10d, return_20d, return_60d,
                mfe, mae, hypothetical_r, rejection_verdict, evaluated_at
            )
            VALUES (
                %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (near_miss_id) DO UPDATE SET
                return_1d        = EXCLUDED.return_1d,
                return_3d        = EXCLUDED.return_3d,
                return_5d        = EXCLUDED.return_5d,
                return_10d       = EXCLUDED.return_10d,
                return_20d       = EXCLUDED.return_20d,
                return_60d       = EXCLUDED.return_60d,
                mfe              = EXCLUDED.mfe,
                mae              = EXCLUDED.mae,
                hypothetical_r   = EXCLUDED.hypothetical_r,
                rejection_verdict = EXCLUDED.rejection_verdict,
                evaluated_at     = EXCLUDED.evaluated_at
        """, (
            near_miss_id,
            result.return_1d, result.return_3d, result.return_5d,
            result.return_10d, result.return_20d, result.return_60d,
            result.mfe, result.mae, result.hypothetical_r,
            result.rejection_verdict, now,
        ))

    logger.info(
        f"[analytics] near_miss_id={near_miss_id} {symbol} → "
        f"verdict={result.rejection_verdict} | R={result.hypothetical_r}"
    )
    return result


# -------------------------------------------------------------------------------------
# BATCH RUNNER: NEAR-MISS OUTCOMES
# -------------------------------------------------------------------------------------

def run_near_miss_outcomes_batch(
    conn,
    *,
    min_days_old: int = 65,
    limit: int = 500,
    overwrite: bool = False,
) -> Dict[str, int]:
    """
    Processes a batch of near_misses records that are old enough to have outcomes
    (>= min_days_old calendar days since logged_date).

    ISOLATION INVARIANT: Reads near_misses. Never writes to it.

    Returns a summary dict:
        {"processed": N, "good": N, "bad": N, "neutral": N, "unresolved": N, "skipped": N}
    """
    cutoff = date.today() - timedelta(days=min_days_old)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT nm.id, nm.symbol, nm.entry_price, nm.stop_loss, nm.target_1, nm.logged_date
              FROM near_misses nm
         LEFT JOIN near_miss_outcomes nmo ON nmo.near_miss_id = nm.id
             WHERE nm.logged_date <= %s
               AND nm.entry_price IS NOT NULL
               AND nm.entry_price > 0
               AND nm.stop_loss   IS NOT NULL
               AND nm.target_1    IS NOT NULL
               AND (%s OR nmo.id IS NULL)
             ORDER BY nm.logged_date ASC
             LIMIT %s
        """, (cutoff, overwrite, limit))
        rows = cur.fetchall()

    summary = {"processed": 0, "good": 0, "bad": 0, "neutral": 0, "unresolved": 0, "skipped": 0}

    for row in rows:
        nm_id, symbol, entry_price, stop_loss, target_1, logged_date = row

        if not entry_price or not stop_loss or not target_1:
            summary["skipped"] += 1
            continue

        try:
            result = compute_and_store_near_miss_outcome(
                near_miss_id=nm_id,
                symbol=symbol,
                entry_price=float(entry_price),
                stop_loss=float(stop_loss),
                target_1=float(target_1),
                signal_date=logged_date,
                conn=conn,
                overwrite=overwrite,
            )
            if result is None:
                summary["skipped"] += 1
            else:
                summary["processed"] += 1
                verdict_key = {
                    VERDICT_GOOD:       "good",
                    VERDICT_BAD:        "bad",
                    VERDICT_NEUTRAL:    "neutral",
                    VERDICT_UNRESOLVED: "unresolved",
                }.get(result.rejection_verdict, "unresolved")
                summary[verdict_key] += 1
        except Exception as exc:
            logger.exception(f"[analytics] Failed for near_miss_id={nm_id} {symbol}: {exc}")
            summary["skipped"] += 1

    conn.commit()
    logger.info(f"[analytics] Batch complete: {summary}")
    return summary


# -------------------------------------------------------------------------------------
# CANDIDATE OUTCOME ANNOTATOR
# -------------------------------------------------------------------------------------

def annotate_candidate_outcome(
    candidate_id: int,
    symbol: str,
    entry_price: float,
    stop_loss: float,
    target_1: float,
    signal_date: date,
    conn,
) -> OutcomeResult:
    """
    Computes the outcome for a CONFIRMED or MISSED candidate and stores it in
    candidate_snapshots with snapshot_reason='OUTCOME' — or as JSONB in the
    candidate row's metadata field.

    This does not write to a separate outcome table (candidates have their full history
    in candidate_snapshots). The OutcomeResult is returned to the caller.
    """
    ohlcv_df = _fetch_ohlcv_after(symbol, signal_date, conn=conn)
    result = compute_outcome(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_1=target_1,
        signal_date=signal_date,
        ohlcv_df=ohlcv_df,
    )

    import json as _json
    outcome_json = _json.dumps(result.as_dict())
    now = datetime.now(IST)

    with conn.cursor() as cur:
        # Store outcome in metadata JSONB on the candidate row
        cur.execute("""
            UPDATE scanner_candidates
               SET metadata   = COALESCE(metadata, '{}'::jsonb) || %s::jsonb,
                   updated_at = %s
             WHERE candidate_id = %s
        """, (
            _json.dumps({"outcome": result.as_dict()}),
            now,
            candidate_id,
        ))

    logger.info(
        f"[analytics] candidate_id={candidate_id} {symbol} outcome annotated → "
        f"verdict={result.rejection_verdict} | R={result.hypothetical_r}"
    )
    return result
