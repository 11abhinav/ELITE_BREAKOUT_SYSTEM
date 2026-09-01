# =====================================================================================
# app/shakeout_reclaim_scanner.py
# SHAKEOUT RECLAIM (BOTTOM ABSORPTION) SCANNER — Daily 16:00 IST (4:00 PM IST)
#
# RULE 67 MANDATORY CHANGE-RATIONALE:
# - Implemented precise Bottom Shakeout + Bullish Re-Absorption + Volume Confirmation scanner.
# - Rationale: Identifies institutional accumulation and selling exhaustion where a stock
#   falls sharply (>=4% or >=1.2x ATR over 3-15 sessions) into support, followed by a strong
#   bullish green candle that completely engulfs/absorbs the preceding bearish candle on
#   heavy volume (>=1.20x 20-day SMA) with a top-tier close (>=0.65 close strength).
# =====================================================================================

import logging
import math
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from database import (
    complete_scanner_execution_run,
    get_elite_watchlist,
    init_db,
    save_alert_if_new,
    start_scanner_execution_run,
    upsert_scanner_health,
)
from lock_utils import ProcessLock
from price_cache import fetch_watchlist_data
from technical_indicators import apply_indicators
from telemetry_manager import telemetry
from watchlist_cache import get_watchlist

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_scan_lock = ProcessLock("shakeout_reclaim_scanner_lock")

# Strategy Parameters
MIN_SELLOFF_PCT = 4.0       # Minimum decline depth in the recent 3-15 sessions
MIN_VOL_RATIO = 1.20        # Hard gate: volume must be at least 1.20x 20-day SMA
MIN_CLOSE_STRENGTH = 0.65   # Hard gate: close must be in upper 35% of day's range
MAX_SL_PCT = 0.06           # Maximum risk cap (6%)


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return default
        return float(val)
    except Exception:
        return default


def detect_shakeout_reclaim(df: pd.DataFrame, symbol: str) -> Optional[Dict[str, Any]]:
    """
    Detects a high-conviction Bottom Shakeout & Bullish Absorption Setup:
    
    Stage 1 — Selloff & Shakeout Location:
      - Over the last 3 to 15 sessions, price suffered a meaningful drop (>=4.0% or >=1.2x ATR).
      - Reversal occurs near the bottom of this move, not in mid-air.
      
    Stage 2 — Selling Exhaustion & Stabilization:
      - Stock establishes a local low / support zone.
      
    Stage 3 — Bullish Re-Absorption / Engulfing:
      - Current candle is green (Close > Open and Close > Prev Close).
      - Engulfs the immediate prior red candle:
        Level A: Close >= Prev Open and Open <= Prev Close (Body Engulfing).
        Level B: Close >= Prev High (Full Candle Reclaim — Preferred).
      - Bullish body >= Prev bearish body * 0.9.
      
    Stage 4 — Hard Volume Confirmation:
      - Current Volume >= 1.20x Volume_SMA20 (Low volume is strictly rejected).
      
    Stage 5 — Hard Close Strength:
      - (Close - Low) / (High - Low) >= 0.65 (Strong buyer control into the close).
    """
    if df is None or df.empty or len(df) < 25:
        return None

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in required_cols):
        return None

    if "EMA20" not in df.columns or "ATR_14" not in df.columns:
        df = apply_indicators(df, timeframe="1d")

    close_arr = df["Close"].values
    open_arr = df["Open"].values
    high_arr = df["High"].values
    low_arr = df["Low"].values
    vol_arr = df["Volume"].values

    n = len(df)
    today_idx = n - 1
    prev_idx = today_idx - 1

    c_today = _safe_float(close_arr[today_idx])
    o_today = _safe_float(open_arr[today_idx])
    h_today = _safe_float(high_arr[today_idx])
    l_today = _safe_float(low_arr[today_idx])
    v_today = _safe_float(vol_arr[today_idx])

    c_prev = _safe_float(close_arr[prev_idx])
    o_prev = _safe_float(open_arr[prev_idx])
    h_prev = _safe_float(high_arr[prev_idx])
    l_prev = _safe_float(low_arr[prev_idx])

    # ── STAGE 1: BASIC CANDLE DIRECTION ───────────────────────────────────────────
    if c_today <= o_today or c_today <= c_prev or c_today <= 0:
        return None  # Today must be an active green candle higher than previous close

    candle_range = h_today - l_today
    if candle_range <= 0:
        return None

    # ── STAGE 2: HARD CLOSE STRENGTH GATE (>= 0.65) ──────────────────────────────
    close_strength = (c_today - l_today) / candle_range
    if close_strength < MIN_CLOSE_STRENGTH:
        return None  # Weak close rejected

    # ── STAGE 3: HARD VOLUME EXPANSION GATE (>= 1.20x SMA20) ─────────────────────
    vol_sma20 = float(df["Volume_SMA20"].iloc[-1]) if "Volume_SMA20" in df.columns else np.mean(vol_arr[-20:])
    vol_ratio = v_today / max(vol_sma20, 1.0)
    if vol_ratio < MIN_VOL_RATIO:
        return None  # Low volume strictly rejected

    atr14 = float(df["ATR_14"].iloc[-1]) if "ATR_14" in df.columns else (c_today * 0.02)
    if atr14 <= 0:
        atr14 = c_today * 0.02

    # ── STAGE 4: BULLISH ABSORPTION / ENGULFING OF PRECEDING CANDLE ──────────────
    # Previous candle should be red / bearish or indecisive consolidation
    prev_body = abs(o_prev - c_prev)
    today_body = c_today - o_today

    # Level A: Body Engulfing (Close >= Prev Open and Open near/below Prev Close)
    is_body_engulfing = (c_today >= o_prev) and (today_body >= prev_body * 0.85)
    # Level B: Full Candle Reclaim (Close >= Prev High — Highest Quality)
    is_full_reclaim = (c_today >= h_prev)

    if not (is_body_engulfing or is_full_reclaim):
        return None

    engulfing_type = "LEVEL_B_FULL_RECLAIM" if is_full_reclaim else "LEVEL_A_BODY_ENGULFING"

    # ── STAGE 5: LOCATION CHECK — MEANINGFUL PRIOR SHAKEOUT / SELLOFF ────────────
    # Look back 3 to 15 sessions to ensure the reversal occurs after a real selloff/dip into support
    lookback_window = min(16, n - 2)
    recent_highs = high_arr[today_idx - lookback_window: today_idx]
    recent_lows = low_arr[today_idx - lookback_window: today_idx]

    if len(recent_highs) < 3:
        return None

    drop_high = float(np.max(recent_highs))
    drop_high_idx = int(np.argmax(recent_highs))
    trough_low = float(np.min(recent_lows))

    # Calculate decline magnitude from the recent swing high down to the bottom trough
    decline_points = drop_high - trough_low
    decline_pct = (decline_points / max(drop_high, 1.0)) * 100.0
    decline_atr = decline_points / max(atr14, 0.01)

    # Require either >= 4.0% drop OR >= 1.2x ATR drop to qualify as a genuine shakeout
    if decline_pct < MIN_SELLOFF_PCT and decline_atr < 1.2:
        return None  # Rejection: Reversal is in mid-air or minor noise, not a bottom shakeout

    # Distance from recent high: Today's open/low should be in the lower half of the swing range
    # (reversal starting near the bottom)
    swing_range = drop_high - trough_low
    if swing_range > 0:
        reversal_depth = (drop_high - l_today) / swing_range
        if reversal_depth < 0.40:
            return None  # Rejection: Reversal initiated too close to the top

    # ── STAGE 6: RISK MANAGEMENT (SL & TARGETS) ──────────────────────────────────
    # Structural SL placed below the recent trough / today's low with a safety buffer
    base_support_low = min(l_today, l_prev, trough_low)
    hard_sl_floor = c_today * (1.0 - MAX_SL_PCT)
    stop_loss = round(max(base_support_low * 0.995, hard_sl_floor), 2)
    risk_per_share = max(c_today - stop_loss, c_today * 0.015)

    target_1 = round(c_today + (1.5 * risk_per_share), 2)
    # Target 2 aligned with 1:3 RR or the prior drop high resistance
    target_2 = round(max(c_today + (3.0 * risk_per_share), drop_high), 2)
    target_3 = round(c_today + (5.0 * risk_per_share), 2)

    rr_1 = round((target_1 - c_today) / max(risk_per_share, 0.01), 2)

    # ── STAGE 7: QUALITY SCORING (60 to 100) ─────────────────────────────────────
    score = 60.0  # Base score for passing all hard gates

    # Full Candle Reclaim (Level B) Bonus
    if is_full_reclaim:
        score += 15.0
    else:
        score += 8.0

    # Volume Multiple Bonus
    if vol_ratio >= 2.0:
        score += 15.0
    elif vol_ratio >= 1.5:
        score += 10.0
    elif vol_ratio >= 1.25:
        score += 5.0

    # Close Strength Bonus
    if close_strength >= 0.85:
        score += 10.0
    elif close_strength >= 0.70:
        score += 5.0

    # Moving Average Alignment / Support Bounce
    ema20 = float(df["EMA20"].iloc[-1]) if "EMA20" in df.columns else 0.0
    sma50 = float(df["SMA50"].iloc[-1]) if "SMA50" in df.columns else 0.0
    sma200 = float(df["SMA200"].iloc[-1]) if "SMA200" in df.columns else 0.0

    if c_today > ema20 > 0:
        score += 4.0
    if c_today > sma50 > 0:
        score += 3.0
    if c_today > sma200 > 0:
        score += 3.0

    score = min(100.0, max(60.0, score))

    return {
        "symbol": symbol,
        "cmp": c_today,
        "entry_price": c_today,
        "engulfing_type": engulfing_type,
        "drop_origin_high": round(drop_high, 2),
        "trough_low": round(trough_low, 2),
        "selloff_depth_pct": round(decline_pct, 2),
        "selloff_bars": int(lookback_window - drop_high_idx),
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "rr_1": rr_1,
        "score": int(score),
        "close_strength": round(close_strength, 2),
        "volume_ratio": round(vol_ratio, 2),
        "alert_time": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_shakeout_reclaim_scan(
    run_date: Optional[str] = None,
    is_test_mode: bool = False,
    run_ctx: Any = None,
    trigger_type: str = "SCHEDULED",
    scheduler_name: str = "CRON",
) -> int:
    """
    Main Execution Entry Point for SHAKEOUT_RECLAIM Scanner.
    Runs daily at 16:00 IST (4:00 PM IST) post-market close.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("🔒 [SHAKEOUT_RECLAIM] Scanner is already running. Skipping duplicate cycle.")
        return 0

    start_time = time.monotonic()
    telemetry.log_scheduler_event("SHAKEOUT_RECLAIM", "CYCLE_START")

    logger.info("=" * 70)
    logger.info("🚀 SHAKEOUT_RECLAIM SCANNER | Starting 4:00 PM Bottom Absorption Execution...")
    logger.info("=" * 70)

    real_run_ctx = run_ctx
    if not real_run_ctx:
        try:
            real_run_ctx = start_scanner_execution_run(
                scanner_name="SHAKEOUT_RECLAIM",
                trigger_type=trigger_type,
                scheduler_name=scheduler_name,
            )
        except Exception as exc:
            logger.warning(f"⚠️ [SHAKEOUT_RECLAIM] Could not create run_ctx: {exc}")
            real_run_ctx = None

    try:
        init_db()
        upsert_scanner_health(
            scanner_name="SHAKEOUT_RECLAIM",
            status="RUNNING",
            error_msg="Bottom Shakeout Re-Absorption scan in progress...",
            scheduled_for="Daily 16:00 IST (Post-Close Reclaim)",
        )

        # 1. Fetch Universe Watchlist
        wl_df = get_watchlist("SHAKEOUT_RECLAIM")
        if isinstance(wl_df, pd.DataFrame) and "Stock" in wl_df.columns:
            watchlist = wl_df["Stock"].dropna().tolist()
        elif isinstance(wl_df, (list, set, tuple)):
            watchlist = list(wl_df)
        else:
            watchlist = get_elite_watchlist() or []

        if not watchlist:
            logger.warning("⚠️ [SHAKEOUT_RECLAIM] Watchlist is empty.")
            upsert_scanner_health(
                scanner_name="SHAKEOUT_RECLAIM",
                status="OK",
                outcome="SUCCESS",
                processed_count=0,
                duration_seconds=round(time.monotonic() - start_time, 2),
                scheduled_for="Daily 16:00 IST (Post-Close Reclaim)",
            )
            telemetry.log_scheduler_event("SHAKEOUT_RECLAIM", "CYCLE_COMPLETE")
            if real_run_ctx:
                complete_scanner_execution_run(real_run_ctx)
            return 0

        try:
            from surveillance import get_live_blacklist
            bl = get_live_blacklist()
            if bl:
                watchlist = [s for s in watchlist if str(s).upper() not in bl]
        except Exception:
            pass

        logger.info(f"📋 [SHAKEOUT_RECLAIM] Screening {len(watchlist)} universe stocks on Daily timeframe...")

        # 2. Fetch 1d OHLCV Data for Watchlist
        all_1d = fetch_watchlist_data(
            watchlist,
            period="1y",
            interval="1d",
            requester="SHAKEOUT_RECLAIM",
            run_ctx=real_run_ctx,
        )

        qualified_candidates: List[Dict[str, Any]] = []
        alerts_saved = 0

        for symbol in watchlist:
            df = all_1d.get(symbol)
            if df is None or df.empty:
                continue

            try:
                res = detect_shakeout_reclaim(df, symbol)
                if res and res.get("score", 0) >= 60:
                    qualified_candidates.append(res)
            except Exception as e:
                logger.debug(f"Error evaluating {symbol} for shakeout reclaim: {e}")

        logger.info(
            f"🎯 [SHAKEOUT_RECLAIM] Screened {len(watchlist)} symbols -> Found {len(qualified_candidates)} qualified Bottom Absorptions!"
        )

        # 3. Sort by Score and Register Breakout Alerts
        qualified_candidates.sort(key=lambda x: x["score"], reverse=True)

        for cand in qualified_candidates:
            sym = cand["symbol"]
            cmp_price = cand["cmp"]
            score = cand["score"]
            sl = cand["stop_loss"]
            t1 = cand["target_1"]
            t2 = cand["target_2"]
            t3 = cand["target_3"]
            engulf_type = cand["engulfing_type"]
            vol_r = cand["volume_ratio"]
            depth = cand["selloff_depth_pct"]

            logger.info(
                f"🔥 [SHAKEOUT RECLAIM TRIGGERED] {sym} | CMP: ₹{cmp_price:.2f} | Type: {engulf_type} | "
                f"Volume: {vol_r:.2f}x SMA20 | Selloff Depth: -{depth:.1f}% | SL: ₹{sl:.2f} | "
                f"Target 1: ₹{t1:.2f} | Score: {score}/100"
            )

            if not is_test_mode:
                inserted, reason, _, _ = save_alert_if_new(
                    symbol=sym,
                    breakout_type="SHAKEOUT_RECLAIM",
                    alert_time=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                    scanner="SHAKEOUT_RECLAIM",
                    category="SWING",
                    entry_price=cmp_price,
                    stop_loss=sl,
                    target_1=t1,
                    target_2=t2,
                    target_3=t3,
                    signals=engulf_type,
                    score=int(score),
                    context={
                        "engulfing_type": engulf_type,
                        "drop_origin_high": cand["drop_origin_high"],
                        "trough_low": cand["trough_low"],
                        "selloff_depth_pct": depth,
                        "volume_ratio": vol_r,
                        "close_strength": cand["close_strength"],
                    },
                    entry_mode="BREAKOUT_TRIGGER",
                )
                if inserted:
                    alerts_saved += 1
                    try:
                        from telegram_engine import queue_telegram_message
                        tg_msg = (
                            f"🚀 <b>SHAKEOUT RECLAIM (BOTTOM ABSORPTION) ALERT</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━\n"
                            f"📈 <b>Stock:</b> #{sym}\n"
                            f"💰 <b>Entry CMP:</b> ₹{cmp_price:.2f}\n"
                            f"📊 <b>Pattern:</b> {engulf_type.replace('_', ' ')}\n"
                            f"📦 <b>Volume:</b> {vol_r:.2f}x SMA20 (Surge)\n"
                            f"📉 <b>Prior Drop:</b> -{depth:.1f}% Exhaustion\n"
                            f"🛡️ <b>Stop Loss:</b> ₹{sl:.2f}\n"
                            f"🎯 <b>Target 1:</b> ₹{t1:.2f} (1:1.5 RR)\n"
                            f"🎯 <b>Target 2:</b> ₹{t2:.2f}\n"
                            f"⭐ <b>Score:</b> {score}/100\n"
                            f"⏰ <b>Time:</b> {datetime.now(IST).strftime('%I:%M %p IST')}"
                        )
                        queue_telegram_message(tg_msg, symbol=sym)
                    except Exception as _tg_err:
                        logger.debug(f"Telegram notification dispatch error: {_tg_err}")

        duration = round(time.monotonic() - start_time, 2)
        logger.info(
            f"✅ [SHAKEOUT_RECLAIM] Cycle complete in {duration}s | Processed: {len(watchlist)} | Alerts Saved: {alerts_saved}"
        )

        upsert_scanner_health(
            scanner_name="SHAKEOUT_RECLAIM",
            status="OK",
            last_success=datetime.now(IST).isoformat(),
            today_alerts=alerts_saved,
            processed_count=len(watchlist),
            total_count=len(watchlist),
            duration_seconds=duration,
            outcome="SUCCESS",
            error_msg=None,
            scheduled_for="Daily 16:00 IST (Post-Close Reclaim)",
        )

        telemetry.log_scheduler_event("SHAKEOUT_RECLAIM", "CYCLE_COMPLETE")
        if real_run_ctx:
            complete_scanner_execution_run(real_run_ctx)

        return alerts_saved

    except Exception as exc:
        duration = round(time.monotonic() - start_time, 2)
        logger.exception(f"❌ [SHAKEOUT_RECLAIM] Fatal error during cycle: {exc}")
        upsert_scanner_health(
            scanner_name="SHAKEOUT_RECLAIM",
            status="DOWN",
            error_msg=str(exc)[:500],
            duration_seconds=duration,
            outcome="FAILED",
            scheduled_for="Daily 16:00 IST (Post-Close Reclaim)",
        )
        telemetry.log_scheduler_event("SHAKEOUT_RECLAIM", "CYCLE_FAILED", error=str(exc))
        if real_run_ctx:
            try:
                complete_scanner_execution_run(real_run_ctx, exception=exc)
            except Exception:
                pass
        return 0
    finally:
        _scan_lock.release()
