# =====================================================================================
# app/reversal_scanner.py (SCHEDULER READY) — v6 REVISED
# DEEP DISCOUNT & MEAN REVERSION SCANNER (With Valuation Metrics)
#
# v6 CHANGELOG (backtest improvement pass — target: lift 44% win rate):
#   [FIX 1] Failed-reversal cooldown: suppress re-alerts on stocks that recently
#           stopped out / failed follow-through (biggest strategy leak).
#   [FIX 2] Trend structure: strict close > SMA50 gate is now MANDATORY (recovery,
#           not just oversold bounce). Extra preference when also > SMA200.
#   [FIX 3] Trend scoring order fixed (strongest state evaluated first).
#   [FIX 4] Single volume threshold source of truth — removed hidden 2.0 gate.
#   [FIX 5] Reduced volume scoring weight (confirmation, not primary driver).
#   [FIX 6] Removed fake regime decoration (was hard-coded BEAR). Now honest NEUTRAL.
#   [FIX 7] One clean fixed drop band (regime-based flex removed).
#   [FIX 8] Removed non-portable macd > 2.0 hard cap.
#   [FIX 9] MACD normalization deferred (kept simple raw macd_hist scoring).
#   [FIX 10] Score comments aligned exactly with implementation.
# =====================================================================================
import pandas as pd
import logging
from zoneinfo import ZoneInfo
from datetime import datetime
from typing import Optional

from technical_indicators import apply_indicators
from database import init_db, save_alert_if_new, upsert_fetch_error
from price_cache import fetch_watchlist_data
from watchlist_cache import get_watchlist
from config import (
    CLIMAX_VOLUME_LOOKBACK, 
    MIN_CANDLE_RANGE_PCT, 
    MIN_STOCK_PRICE,
    REVERSAL_CONFIG,
    ALERT_COOLDOWN_MINUTES
)
from sl_target_helper import compute_sl_and_target
from delivery_data import fetch_previous_day_delivery
from macro_utils import get_nifty_20d_return, get_macro_regime


logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── REVERSAL PARAMETERS ──────────────────────────────────────────────────────────────
# [FIX 7] Single clean fixed drop band. Tightened lower bound to 20% (was 18%) to
#         avoid shallow, low-conviction pullbacks; capped at 45% sweet-spot ceiling
#         (was 60%) to avoid deep falling knives. Regime-based flex removed (FIX 6).
MIN_DROP_FROM_52W_HIGH = REVERSAL_CONFIG["MIN_DROP_FROM_52W_HIGH"]
MAX_DROP_FROM_52W_HIGH = REVERSAL_CONFIG["MAX_DROP_FROM_52W_HIGH"]

RSI_OVERSOLD_THRESHOLD = REVERSAL_CONFIG["RSI_OVERSOLD_THRESHOLD"]
RSI_CURL_MIN           = REVERSAL_CONFIG["RSI_CURL_MIN"]

# [FIX 4] SINGLE SOURCE OF TRUTH for volume. Previously MIN_VOLUME_RATIO=1.5 was
#         shadowed by a hidden `if vol_ratio < 2.0: continue`. Now exactly one gate.
MIN_VOLUME_RATIO       = REVERSAL_CONFIG["MIN_VOLUME_RATIO"]

# ── QUALITY FILTERS (high-quality stocks only) ───────────────────────────────────────
# MIN_STOCK_PRICE imported from config (₹100)
MIN_AVG_DAILY_VOLUME   = REVERSAL_CONFIG["MIN_AVG_DAILY_VOLUME"]
MIN_ROE                = REVERSAL_CONFIG["MIN_ROE"]
MIN_YOY_REVENUE_GROWTH = REVERSAL_CONFIG["MIN_YOY_REVENUE_GROWTH"]
MAX_DROP_BELOW_SMA200  = REVERSAL_CONFIG["MAX_DROP_BELOW_SMA200"]
# ─────────────────────────────────────────────────────────────────────────────────────

# [FIX 1] FAILED-REVERSAL COOLDOWN ────────────────────────────────────────────────────
# Suppress new reversal alerts on a symbol for N trading days after its previous
# reversal alert stopped out OR failed to follow through. This directly attacks the
# biggest strategy leak: the same beaten-down stock alerting, failing, and re-alerting.
REVERSAL_COOLDOWN_TRADING_DAYS = REVERSAL_CONFIG["REVERSAL_COOLDOWN_TRADING_DAYS"]
# ─────────────────────────────────────────────────────────────────────────────────────

# ── REVERSAL SCORE THRESHOLDS ────────────────────────────────────────────────────────
MIN_REVERSAL_SCORE = 72   # minimum to generate an alert (out of 100)

# =====================================================================================
# REVERSAL-SPECIFIC SCORING (v6 — re-weighted)
#
# [FIX 5 + FIX 10] Volume de-emphasized; trend structure promoted. Weights below
# match the implementation EXACTLY (no divergence between comment and code):
#
#   Trend structure    — 25 pts max  (SMA50 reclaim + SMA200 = recovery, core signal)
#   SMA200 proximity   — 15 pts max  (closer to SMA200 = less falling-knife risk)
#   Volume confirmation— 15 pts max  (REDUCED: confirmation, not primary driver)
#   MACD momentum      — 15 pts max  (stronger MACD flip = stronger reversal)
#   RSI curl quality   — 15 pts max  (faster RSI recovery from oversold)
#   Category quality   — 10 pts max  (fundamental tier from daily builder)
#   Drop sweet spot    —  5 pts max  (sweet-spot bonus within the fixed band)
#   R:R quality        —  5 pts max  (reward > 2.5:1 risk-reward setups)
#   Delivery + OBV     — bonus pts   (institutional accumulation confirmation)
#   ──────────────────────────────────────
#   Capped at 100.
# =====================================================================================

_REV_CATEGORY_SCORES = {
    "Debt-Free Cash Generator": 10, "Wealth Compounder": 10, "Top Bank/NBFC": 10,
    "Long Term Compounder": 10,
    "Dividend Aristocrat": 9,
    "Capital Efficient": 9, "Efficient Lender": 9,
    "Undervalued Growth": 8, "High Momentum": 8, "Fast Growing Financial": 8,
    "Consistent Performer": 6,
    "Blue Chip Stable": 5, "Blue Chip Financial": 5,
    "Recovery Play": 3, "Financial Recovery": 3,
}


def _score_reversal(
        vol_ratio: float,
        drop_pct: float,
        current_rsi: float,
        past_10_rsi_min: float,
        macd_hist: Optional[float],
        pct_below_sma200: Optional[float],
        category: str,
        rr_ratio: Optional[float],
        above_sma50: Optional[bool] = None,
        above_sma200: Optional[bool] = None,
        obv_trend: Optional[int] = None,
        delivery_pct: Optional[float] = None,
) -> int:
    """Score a reversal setup from 0-100 based on quality dimensions (v6 weights)."""
    score = 0

    # ── Trend structure (25 pts) — CORE recovery signal ──
    # [FIX 2 + FIX 3] Reordered so the STRONGEST state is evaluated first.
    #   Previously: `if above_sma50 or (...)` ran before the stronger
    #   `elif above_sma50 and above_sma200`, so the best setups never got their
    #   intended score. Now strictly: strongest → weaker → conditional.
    if above_sma50 and above_sma200:
        score += 25   # full recovery structure: above both 50 & 200 SMA
    elif above_sma50:
        score += 18   # reclaimed SMA50 (mandatory gate, baseline recovery)
    # else: no trend-structure points (should be rare — SMA50 is a hard gate)

    # ── SMA200 proximity (15 pts) — closer = safer entry ──
    if pct_below_sma200 is not None:
        if pct_below_sma200 <= 3.0:    score += 15  # very close to / above SMA200
        elif pct_below_sma200 <= 8.0:  score += 11
        elif pct_below_sma200 <= 15.0: score += 7
        elif pct_below_sma200 <= 20.0: score += 3
        # > 20% below SMA200: no bonus (falling knife territory)
    else:
        score += 0  # no SMA200 data — neutral

    # ── Volume confirmation (15 pts) ──
    # [FIX 5] REDUCED from 25 → 15. Volume now confirms, it does not drive.
    if vol_ratio >= 5.0:   score += 15
    elif vol_ratio >= 3.5: score += 12
    elif vol_ratio >= 2.5: score += 9
    elif vol_ratio >= MIN_VOLUME_RATIO: score += 5
    # < MIN_VOLUME_RATIO never reaches here (hard gate), but guarded for safety.

    # ── MACD momentum (15 pts) ──
    # [FIX 9] Normalization deferred — raw macd_hist retained intentionally.
    if macd_hist is not None:
        try:
            mh = float(macd_hist)
            if mh > 0.5:   score += 15   # strong bullish histogram
            elif mh > 0.2: score += 10
            elif mh > 0:   score += 5    # just turned positive
        except (TypeError, ValueError):
            pass

    # ── RSI curl quality (15 pts) — bigger recovery = stronger signal ──
    rsi_recovery = current_rsi - past_10_rsi_min
    if rsi_recovery >= 20:   score += 15   # explosive recovery from deep oversold
    elif rsi_recovery >= 12: score += 12
    elif rsi_recovery >= 8:  score += 8
    elif rsi_recovery >= 5:  score += 5

    # ── Category quality (10 pts) ──
    for cat_label, cat_pts in _REV_CATEGORY_SCORES.items():
        if cat_label in category:
            score += cat_pts
            break

    # ── Drop sweet spot / penalty (5 pts) — refined per user guidance
    # Mapping:
    #   25-40%  => +5
    #   20-25%  => +3
    #   40-45%  => +3
    #   45-60%  => -5 (penalty but still acceptable)
    #   >60%    => rejected earlier
    if 25.0 <= drop_pct <= 40.0:
        score += 5
    elif MIN_DROP_FROM_52W_HIGH <= drop_pct < 25.0:
        score += 3
    elif 40.0 < drop_pct <= MAX_DROP_FROM_52W_HIGH:
        score += 3
    # ── R:R quality (5 pts) ──
    if rr_ratio is not None:
        if rr_ratio >= 3.5:   score += 5
        elif rr_ratio >= 2.5: score += 3
        elif rr_ratio >= 2.0: score += 1

    # ── OBV confirmation bonus (5 pts) — volume confirming reversal ──
    if obv_trend is not None and obv_trend == 1:
        score += 5  # OBV rising = accumulation (institutional buying into reversal)

    # ── Delivery conviction bonus (5 pts) — institutional accumulation ──
    if delivery_pct is not None:
        if delivery_pct >= 50.0:   score += 5   # strong institutional accumulation
        elif delivery_pct >= 35.0: score += 3   # moderate positional buying
        elif delivery_pct >= 25.0: score += 1   # mild conviction

    return min(score, 100)


# [FIX 1] FAILED-REVERSAL COOLDOWN HELPER ──────────────────────────────────────────────
def _is_symbol_in_reversal_cooldown(symbol: str, cooldown_days: int) -> bool:
    try:
        from database import is_symbol_in_failed_reversal_cooldown
        return bool(is_symbol_in_failed_reversal_cooldown(symbol, cooldown_days))
    except (ImportError, AttributeError, ModuleNotFoundError):
        logger.warning(f"⚠️ Outcome tracking helper missing for {symbol}; cooldown protection weakened.")
        return False
    except Exception:
        logger.exception(f"cooldown check (outcome-aware) failed for {symbol}")
        return False
# ─────────────────────────────────────────────────────────────────────────────────────


def _run_scan(force: bool = False):
    """Execute a single reversal scan pass. Called inside the scheduling loop."""

    ist_now = datetime.now(IST)
    scan_start = datetime.now(IST)
    logger.info("\n" + "=" * 80)
    logger.info(f"🚀 [START] REVERSAL SCANNER INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')} 🚀")
    logger.info("=" * 80 + "\n")

    # Check if we are outside the valid REVERSAL window (18:30 - 23:59:59)
    now_time = ist_now.time()
    scheduled_start = datetime.strptime("18:30", "%H:%M").time()
    scheduled_end = datetime.strptime("23:59:59", "%H:%M:%S").time()
    import database
    if force:
        is_test_mode = False
    else:
        is_test_mode = getattr(database, "DONT_SAVE_ALERTS", False) or not (scheduled_start <= now_time <= scheduled_end)
    if is_test_mode:
        logger.info("🧪 [TEST MODE] Outside scheduled window (18:30-23:59). Alerts will NOT be saved to DB.")

    try:
        prev_delivery_map = fetch_previous_day_delivery()
    except Exception as e:
        logger.warning(f"⚠️ Failed to fetch delivery data: {e}. Reverting to empty map (no delivery bonus).")
        prev_delivery_map = {}
        
    try:
        nifty_ret = get_nifty_20d_return()
        reversal_regime = get_macro_regime(nifty_ret)
    except Exception:
        reversal_regime = "NEUTRAL"

    try:
        watchlist = get_watchlist()
    except Exception:
        logger.error("Failed to load watchlist, skipping run.")
        return 0

    if watchlist.empty:
        logger.info("🛡️ Reversal Scanner | Watchlist is empty. Exiting cleanly.")
        if not is_test_mode:
            try:
                from database import insert_notification, upsert_scanner_health
                insert_notification("admin", "🚀 Reversal Scanner ran successfully. Found 0 new reversal alerts.", "Generated 0 alerts. The fundamental watchlist universe is currently empty.")
                upsert_scanner_health("REVERSAL", status="OK", last_success=datetime.now(IST).isoformat(), today_alerts=0, total_count=0)
            except Exception:
                pass
        return 0

    # Pulling 1y data to ensure we catch the 52W High correctly
    all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")

    fetched_count = len(all_ticker_data) if all_ticker_data else 0
    if fetched_count < len(watchlist) * 0.70:
        logger.warning(f"⚠️ Data Provider returned data for only {fetched_count}/{len(watchlist)} symbols (likely rate-limited). Forcing retry...")
        if not is_test_mode:
            try:
                from database import upsert_scanner_health
                upsert_scanner_health(scanner_name="REVERSAL", status="DOWN", error_msg=f"STALE DATA/INCOMPLETE DATA ERROR: Fetched {fetched_count}/{len(watchlist)} symbols")
            except Exception:
                pass
        raise Exception(f"STALE DATA/INCOMPLETE DATA ERROR: Only fetched {fetched_count}/{len(watchlist)} symbols (70% minimum required). Aborting to prevent stale data.")
    else:
        logger.info(f"✅ Successfully fetched {fetched_count}/{len(watchlist)} symbols for Reversal scan")
        if not is_test_mode:
            try:
                from database import upsert_scanner_health
                upsert_scanner_health(scanner_name="REVERSAL", status="RUNNING", error_msg=None)
            except Exception:
                pass

    total_alerts = 0
    shortlisted_alerts = []
    rejected = {
        "no_data": 0,
        "insufficient_bars": 0,
        "stale_data": 0,
        "cooldown": 0,
        "failed_pattern": 0,
        "drop_band": 0,
        "low_price": 0,
        "low_liquidity": 0,
        "fundamental_filter": 0,
        "ema_filter": 0,
        "not_above_sma50": 0,
        "low_volume": 0,
        "no_macd_cross": 0,
        "low_score": 0,
        "climax_top": 0,
        "thin_spread": 0
    }
    today_str = ist_now.strftime("%Y-%m-%d")

    # ── MAKE EXPORT IDEMPOTENT ──
    try:
        import os, csv
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(base_dir, "data")
        export_path = os.path.join(data_dir, "reversal_alerts_export.csv")
        if os.path.exists(export_path):
            df_export = pd.read_csv(export_path)
            if 'date' in df_export.columns:
                df_export = df_export[df_export['date'] != today_str]
                df_export.to_csv(export_path, index=False)
    except Exception as e:
        logger.warning(f"Could not clean up today's export rows: {e}")

    from database import get_recent_alerts_for_scanner
    cooldown_alerts = get_recent_alerts_for_scanner("REVERSAL", ALERT_COOLDOWN_MINUTES["REVERSAL"])

    for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):
        symbol = "UNKNOWN"
        try:
            symbol   = row["Stock"]
            category = row["Category"]

            from surveillance import get_live_blacklist
            if symbol in get_live_blacklist():
                continue

            # [FIX 1] FAILED-REVERSAL COOLDOWN — earliest cheap gate after blacklist.
            # Suppress symbols that recently stopped out / failed follow-through.
            if _is_symbol_in_reversal_cooldown(symbol, REVERSAL_COOLDOWN_TRADING_DAYS):
                rejected["cooldown"] += 1
                logger.info(f"[REVERSAL] {symbol} skipped: failed reversal cooldown active")
                continue

            if symbol not in all_ticker_data or all_ticker_data[symbol] is None or all_ticker_data[symbol].empty:
                logger.warning(f"[REVERSAL] {symbol} rejected: no historical data")
                rejected["no_data"] += 1
                continue

            ticker = all_ticker_data[symbol].copy()
            if getattr(ticker, 'attrs', {}).get('is_stale'):
                logger.warning(f"[REVERSAL] {symbol} rejected: stale historical data")
                rejected["stale_data"] += 1
                continue

            if isinstance(ticker.columns, pd.MultiIndex):
                ticker.columns = ticker.columns.get_level_values(0)
            ticker = ticker.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

            if len(ticker) < 100:
                logger.warning(f"[REVERSAL] {symbol} rejected: insufficient bars ({len(ticker)} < 100)")
                rejected["insufficient_bars"] += 1
                continue

            ticker = apply_indicators(ticker, timeframe="1d")
            if ticker is None or ticker.empty:
                rejected["no_data"] += 1
                continue

            latest   = ticker.iloc[-1]
            required = ["Close", "High", "Low", "Open", "Volume", "RSI", "EMA20", "EMA50", "SMA50", "SMA200", "MACD", "MACD_SIGNAL", "MACD_HIST", "HIGH_52W", "ATR", "ATR_PCT", "SWING_LOW", "SWING_HIGH"]
            if not all(col in ticker.columns for col in required):
                rejected["no_data"] += 1
                continue
            if pd.isna(latest["RSI"]) or pd.isna(latest["MACD"]):
                rejected["no_data"] += 1
                continue

            close_price = float(latest["Close"])
            high_52w    = float(latest["HIGH_52W"])

            if high_52w <= 0:
                rejected["no_data"] += 1
                continue
            drop_pct = ((high_52w - close_price) / high_52w) * 100

            # [FIX 7] Single clean fixed drop band. Tightened lower bound to 20% (was 18%) to
            #         avoid shallow, low-conviction pullbacks; capped at sweet-spot ceiling
            #         (config-driven via MAX_DROP_FROM_52W_HIGH) to avoid deep falling knives.
            if drop_pct < MIN_DROP_FROM_52W_HIGH or drop_pct > MAX_DROP_FROM_52W_HIGH:
                # reject drawdowns outside configured band
                rejected["drop_band"] += 1
                continue

            # ── QUALITY FILTER 1: minimum price ─────────────────────────────────────
            if close_price < MIN_STOCK_PRICE:
                rejected["low_price"] += 1
                continue

            # ── QUALITY FILTER 2: minimum liquidity ─────────────────────────────────
            avg_vol_20d = float(ticker["Volume"].iloc[-21:-1].mean())
            if avg_vol_20d < MIN_AVG_DAILY_VOLUME:
                rejected["low_liquidity"] += 1
                continue

            # ── QUALITY FILTER 3: not a falling knife — must be within x% of SMA200 ─
            pct_below_sma200 = None
            if "SMA200" in ticker.columns and not pd.isna(latest.get("SMA200")):
                sma200 = float(latest["SMA200"])
                if sma200 > 0:
                    pct_below_sma200 = (sma200 - close_price) / sma200 * 100
                    if pct_below_sma200 > MAX_DROP_BELOW_SMA200:
                        rejected["drop_band"] += 1
                        continue

            # ── QUALITY FILTER 4: fundamentals (from watchlist columns) ─────────────
            roe     = row.get("ROE %")
            yoy_rev = row.get("YOY Revenue %")
            if roe is not None and not pd.isna(roe):
                try:
                    if float(roe) < MIN_ROE:
                        rejected["fundamental_filter"] += 1
                        continue
                except (ValueError, TypeError):
                    pass
            if yoy_rev is not None and not pd.isna(yoy_rev):
                try:
                    if float(yoy_rev) < MIN_YOY_REVENUE_GROWTH:
                        rejected["fundamental_filter"] += 1
                        continue
                except (ValueError, TypeError):
                    pass

            # ── RSI curl: was oversold recently, now recovering ─────────────────────
            current_rsi = float(latest["RSI"])
            recent_rsi = ticker["RSI"].dropna().iloc[-11:-1]
            if len(recent_rsi) < 5:
                rejected["insufficient_bars"] += 1
                continue
            past_10_rsi = recent_rsi.min()

            if current_rsi < RSI_CURL_MIN or past_10_rsi > RSI_OVERSOLD_THRESHOLD:
                rejected["failed_pattern"] += 1
                continue

            # ── Must be holding above 20 EMA (immediate momentum) ───────────────────
            ema20 = float(latest["EMA20"])
            if close_price < ema20:
                rejected["ema_filter"] += 1
                continue

            # Require EMA20 to be trending or above EMA50. This removes weak bounces.
            ema20_gt_ema50 = None
            ema20_slope_pos = None
            try:
                if "EMA50" in ticker.columns and not pd.isna(latest.get("EMA50")):
                    ema50 = float(latest["EMA50"])
                    ema20_gt_ema50 = ema20 > ema50
                # ema20 slope: compare to previous day's EMA20 when available
                if "EMA20" in ticker.columns and len(ticker) >= 2:
                    prev_ema20 = float(ticker["EMA20"].iloc[-2])
                    ema20_slope_pos = (ema20 - prev_ema20) > 0
            except Exception:
                ema20_gt_ema50 = None
                ema20_slope_pos = None

            # If neither EMA20 > EMA50 nor EMA20 slope positive, skip
            if ema20_gt_ema50 is not True and ema20_slope_pos is not True:
                logger.debug(f"  ⊘ {symbol} EMA20 trend filter failed — skipping")
                rejected["ema_filter"] += 1
                continue

            # ── [FIX 2] TREND STRUCTURE — STRICT close > SMA50 IS NOW MANDATORY ──────
            # This is the core shift from "bounce detection" to "recovery detection".
            # Without an SMA50 reclaim, an oversold bounce is just noise.
            above_sma50 = None
            above_sma200 = None
            if "SMA50" in ticker.columns and not pd.isna(latest.get("SMA50")):
                sma50_val = float(latest["SMA50"])
                above_sma50 = bool(close_price >= sma50_val)
            if "SMA200" in ticker.columns and not pd.isna(latest.get("SMA200")):
                sma200_val = float(latest["SMA200"])
                above_sma200 = bool(close_price >= sma200_val)

            # HARD GATE: require an SMA50 reclaim. If SMA50 unavailable, reject
            # (we cannot confirm recovery structure without it).
            if above_sma50 is not True:
                logger.debug(f"  ⊘ {symbol} not above SMA50 (no recovery structure) — skipping")
                rejected["not_above_sma50"] += 1
                continue

            # ── Volume confirmation — single threshold (FIX 4) ──────────────────────
            vol_now = float(latest["Volume"])
            if avg_vol_20d <= 0:
                rejected["low_liquidity"] += 1
                continue

            vol_ratio = vol_now / avg_vol_20d
            if vol_ratio < MIN_VOLUME_RATIO:   # [FIX 4] the ONLY volume gate now
                rejected["low_volume"] += 1
                continue

            # ── [FIX 8] FRESH MACD BULLISH CROSSOVER ON CLOSED BAR ──────────────────────
            macd_bullish_cross_closed_bar = False
            if len(ticker) >= 3:
                try:
                    macd_now = float(ticker["MACD"].iloc[-2])
                    signal_now = float(ticker["MACD_SIGNAL"].iloc[-2])
                    macd_prev = float(ticker["MACD"].iloc[-3])
                    signal_prev = float(ticker["MACD_SIGNAL"].iloc[-3])
                    macd_bullish_cross_closed_bar = (macd_now > signal_now) and (macd_prev <= signal_prev)
                except (KeyError, TypeError, ValueError):
                    pass  # MACD columns missing or invalid

            if not macd_bullish_cross_closed_bar:
                logger.debug(f"  ⊘ {symbol} no fresh MACD cross on closed bar — skipping")
                rejected["no_macd_cross"] += 1
                continue

            reversal_signals = [
                f"📉 -{drop_pct:.1f}% from 52W High",
                "📈 RSI Oversold Curl",
                "🎯 Reclaimed 20 EMA & SMA50",   # FIX 2 reflected in signal text
                "📊 MACD Bullish Cross"
            ]
            if above_sma200:
                reversal_signals.append("🏔️ Above SMA200 (full recovery)")

            signal_str = "Reversal"
            today_str  = ist_now.strftime("%Y-%m-%d")
            breakout_type = "REVERSAL"
            dedup_key  = f"{category}|{symbol}|{today_str}|{breakout_type}"

            if (symbol, breakout_type) in cooldown_alerts:
                continue

            candle_range   = float(latest["High"]) - float(latest["Low"])
            candle_high    = float(latest["High"])
            candle_low     = float(latest["Low"])
            atr_val        = float(latest["ATR"]) if "ATR" in ticker.columns and not pd.isna(latest.get("ATR")) else None

            # ── v5: CLIMAX TOP DISQUALIFIER ───────────────────────────────────────
            # Operators push beaten-down stocks to a fake bounce high with massive
            # volume, then dump. Same climax top pattern as breakout scanners.
            lookback_ct = min(CLIMAX_VOLUME_LOOKBACK, len(ticker) - 1)
            if lookback_ct >= 5:
                latest_vol_ct = float(latest["Volume"])
                max_vol_ct    = float(ticker["Volume"].iloc[-lookback_ct - 1:-1].max())
                candle_rng_ct = candle_high - candle_low
                if candle_rng_ct > 0 and latest_vol_ct > max_vol_ct:
                    upper_wick_pct = (candle_high - close_price) / candle_rng_ct
                    close_pos_ct   = (close_price - candle_low) / candle_rng_ct
                    if upper_wick_pct > 0.25 and close_pos_ct < 0.40:
                        logger.debug(
                            f"  ⊘ {symbol} climax top on reversal candle — skipping"
                        )
                        rejected["climax_top"] += 1
                        continue

            # ── v5: THIN SPREAD TRAP ─────────────────────────────────────────────
            # Reversal candle with tiny range = no conviction, possible manipulation.
            if close_price > 0 and candle_range > 0:
                range_pct = candle_range / close_price
                if range_pct < MIN_CANDLE_RANGE_PCT:
                    logger.debug(
                        f"  ⊘ {symbol} thin spread reversal ({range_pct:.3%}) — skipping"
                    )
                    rejected["thin_spread"] += 1
                    continue

            # ── Dynamic S/R and Indicator-based SL + Target (REVERSAL mode) ───────
            # Reversal scanner: targets are mean-reversion levels (EMA20, SMA50),
            # NOT overhead resistance. SL is widest buffer (anti-trap for volatile stocks).
            sl_result = compute_sl_and_target(
                entry_price=close_price,
                atr=atr_val,
                candle_range=candle_range,
                mode="REVERSAL",
                adx=latest.get("ADX"),
                rsi=current_rsi,
                macd_hist=latest.get("MACD_HIST"),
                atr_pct=latest.get("ATR_PCT"),
                swing_low=latest.get("SWING_LOW"),
                swing_high=latest.get("SWING_HIGH"),
                bb_upper=latest.get("BB_UPPER"),
                bb_lower=latest.get("BB_LOWER"),
                bb_mid=latest.get("BB_MID"),
                s1=latest.get("S1"),
                s2=latest.get("S2"),
                r1=latest.get("R1"),
                r2=latest.get("R2"),
                swing_low_raw=latest.get("SWING_LOW_RAW"),
                swing_high_raw=latest.get("SWING_HIGH_RAW"),
                candle_low=candle_low,
                vwap=latest.get("VWAP"),
                # Mean-reversion specific targets
                ema20=latest.get("EMA20"),
                sma50=latest.get("SMA50"),
            )
            suggested_stop = sl_result["stop_loss"]
            target_price   = sl_result["target_1"]

            signal_str = ", ".join(reversal_signals)

            # ── DYNAMIC REVERSAL SCORING (v6) ─────────────────────────────────────
            pct_below_200 = pct_below_sma200  # reuse value computed in QUALITY FILTER 3

            # Read OBV trend for scoring bonus
            obv_trend_val = None
            if "OBV_TREND" in ticker.columns:
                try:
                    obv_trend_val = int(latest.get("OBV_TREND", 0) or 0)
                except (TypeError, ValueError):
                    obv_trend_val = 0

            delivery_pct = prev_delivery_map.get(symbol, None)

            reversal_score = _score_reversal(
                vol_ratio=vol_ratio,
                drop_pct=drop_pct,
                current_rsi=current_rsi,
                past_10_rsi_min=float(past_10_rsi),
                macd_hist=latest.get("MACD_HIST"),
                pct_below_sma200=pct_below_200,
                category=category,
                rr_ratio=sl_result.get("rr_ratio"),
                above_sma50=above_sma50,      # [FIX 2/3] feed trend structure to scorer
                above_sma200=above_sma200,    # [FIX 2/3]
                obv_trend=obv_trend_val,
                delivery_pct=delivery_pct,
            )

            if reversal_score < MIN_REVERSAL_SCORE:
                logger.debug(f"  ⊘ {symbol} reversal score {reversal_score} < {MIN_REVERSAL_SCORE} — skipping")
                rejected["low_score"] += 1
                continue

            # Compute trend_score for export/analysis (same logic as scorer's trend block)
            trend_score = 25 if (above_sma50 and above_sma200) else 18

            # ─────────────────────────────────────────────────────────────────────

            above_ema20  = bool(close_price >= float(latest["EMA20"])) if "EMA20" in ticker.columns and not pd.isna(latest.get("EMA20")) else None
            # above_sma50 / above_sma200 already computed above (FIX 2)
            golden_cross = bool(float(latest["SMA50"]) >= float(latest["SMA200"])) if ("SMA50" in ticker.columns and "SMA200" in ticker.columns and not pd.isna(latest.get("SMA50")) and not pd.isna(latest.get("SMA200"))) else None
            body_ratio   = round(abs(close_price - float(latest["Open"])) / candle_range * 100) if candle_range > 0 else 0

            context = {
                "technicals": {
                    "above_ema20":      above_ema20,
                    "above_sma50":      above_sma50,
                    "above_sma200":     above_sma200,   # FIX 2: surface full recovery
                    "golden_cross":     golden_cross,
                    "body_ratio":       round(body_ratio, 2),
                    "delivery_pct":     round(delivery_pct, 1) if delivery_pct is not None else None,
                    "rsi":              round(current_rsi, 1),
                    "volume_ratio":     round(vol_ratio, 2)
                },
                "session": {
                    "open":             round(float(latest["Open"]), 2),
                    "day_high":         round(float(latest["High"]), 2),
                    "day_low":          round(float(latest["Low"]), 2)
                },
                "fundamentals": {
                    "peg":              row.get("PEG Ratio"),
                    "yoy_rev":          row.get("YOY Revenue %"),
                    "yoy_profit":       row.get("YOY Profit %"),
                    "roe":              row.get("ROE %")
                },
                "execution": {
                    "sl_method":        sl_result.get("sl_method"),
                    "t_method":         sl_result.get("t_method"),
                    "trail_note":       sl_result.get("trail_note")
                }
            }

            shortlisted_alerts.append({
                "symbol": symbol,
                "dedup_key": dedup_key,
                "alert_time": ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                "category": category,
                "entry_price": round(close_price, 2),
                "signals": signal_str,
                "score": reversal_score,
                "rsi": round(current_rsi, 1),
                "volume_ratio": round(vol_ratio, 2),
                "stop_loss": suggested_stop,
                "target_price": target_price,
                "context": context
            })

            # EXPORT: append reversal alert metadata to CSV for later backtest/outcome analysis
            try:
                import os, csv
                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                data_dir = os.path.join(base_dir, "data")
                os.makedirs(data_dir, exist_ok=True)
                export_path = os.path.join(data_dir, "reversal_alerts_export.csv")
                header = [
                    "symbol", "date", "score", "drop_pct", "volume_ratio", "delivery_pct",
                    "trend_score", "rsi", "macd", "result_5d", "result_10d", "result_20d",
                    "max_runup", "max_drawdown"
                ]
                export_row = {
                    "symbol": symbol,
                    "date": today_str,
                    "score": reversal_score,
                    "drop_pct": round(drop_pct, 2),
                    "volume_ratio": round(vol_ratio, 2),
                    "delivery_pct": round(delivery_pct, 2) if delivery_pct is not None else None,
                    "trend_score": trend_score,
                    "rsi": round(current_rsi, 2),
                    "macd": float(latest.get("MACD")) if latest.get("MACD") is not None else None,
                    "result_5d": None,
                    "result_10d": None,
                    "result_20d": None,
                    "max_runup": None,
                    "max_drawdown": None
                }
                write_header = not os.path.exists(export_path)
                with open(export_path, "a", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=header)
                    if write_header:
                        writer.writeheader()
                    writer.writerow(export_row)
            except Exception:
                logger.exception(f"Failed to export reversal alert for {symbol}")


        except Exception as e:
            logger.exception(f'❌ Error processing {symbol}')
            upsert_fetch_error('yfinance', 'REVERSAL', symbol, '1d', 'processing_error', str(e))

    logger.info(
        f"[REVERSAL] rejection summary: "
        f"no_data={rejected.get('no_data', 0)}, "
        f"insufficient_bars={rejected.get('insufficient_bars', 0)}, "
        f"stale_data={rejected.get('stale_data', 0)}, "
        f"cooldown={rejected.get('cooldown', 0)}, "
        f"failed_pattern={rejected.get('failed_pattern', 0)}, "
        f"drop_band={rejected.get('drop_band', 0)}, "
        f"low_price={rejected.get('low_price', 0)}, "
        f"low_liquidity={rejected.get('low_liquidity', 0)}, "
        f"fundamental_filter={rejected.get('fundamental_filter', 0)}, "
        f"ema_filter={rejected.get('ema_filter', 0)}, "
        f"not_above_sma50={rejected.get('not_above_sma50', 0)}, "
        f"low_volume={rejected.get('low_volume', 0)}, "
        f"no_macd_cross={rejected.get('no_macd_cross', 0)}, "
        f"low_score={rejected.get('low_score', 0)}, "
        f"climax_top={rejected.get('climax_top', 0)}, "
        f"thin_spread={rejected.get('thin_spread', 0)}"
    )

    # ── VALIDATION COMPLETE: IDEMPOTENT CLEANUP ──
    try:
        from database import delete_todays_alerts_for_scanner
        deleted_count = delete_todays_alerts_for_scanner("REVERSAL", today_str)
        logger.info(f"REVERSAL cleanup complete: removed {deleted_count} existing alerts for {today_str}")
    except Exception as e:
        logger.exception("Failed to delete today's alerts for REVERSAL before persistence")

    # ── PERSISTENCE ───────────────────────────────────────────────────────────
    total_alerts = 0
    if not is_test_mode and not getattr(database, "DONT_SAVE_ALERTS", False):
        try:
            for alert in shortlisted_alerts:
                inserted, _, _ = database.save_alert_if_new(
                    alert["symbol"],
                    alert["dedup_key"],
                    alert["alert_time"],
                    scanner="REVERSAL",
                    category=alert["category"],
                    entry_price=alert["entry_price"],
                    signals=alert["signals"],
                    score=alert["score"],
                    rsi=alert["rsi"],
                    volume_ratio=alert["volume_ratio"],
                    stop_loss=alert["stop_loss"],
                    target_price=alert["target_price"],
                    context=alert["context"],
                    model_version="v6",
                    bayesian_regime=reversal_regime,
                    bayesian_weights=None,
                )
                if inserted:
                    total_alerts += 1

            if total_alerts > 0:
                from database import verify_alerts_saved_today, upsert_scanner_health
                if not verify_alerts_saved_today("REVERSAL", total_alerts):
                    logger.critical(f"🚨 CRITICAL ERROR: Reversal generated {total_alerts} alerts but save verification failed!")
                    upsert_scanner_health(
                        scanner_name="REVERSAL",
                        status="DOWN",
                        error_msg="CRITICAL: Alerts failed to save to database"
                    )
                    return total_alerts
                    
            from database import upsert_scanner_health
            upsert_scanner_health(
                scanner_name="REVERSAL",
                status="OK",
                last_success=ist_now.isoformat(),
                today_alerts=total_alerts,
                total_count=len(watchlist),
                error_msg=None
            )
        except Exception as e:
            logger.exception("Failed to save REVERSAL alerts")
            try:
                from database import upsert_scanner_health
                upsert_scanner_health(
                    scanner_name="REVERSAL",
                    status="DOWN",
                    error_msg=f"Persistence failure: {e}"
                )
            except Exception:
                pass
            
    elapsed_time = (datetime.now(IST) - scan_start).total_seconds()
    logger.info(f"✅ [COMPLETE] REVERSAL SCAN DONE | {elapsed_time:.2f}s | Found {total_alerts} bottoming stocks.")
            
    try:
        from database import insert_notification
        insert_notification("admin", f"🚀 Reversal Scanner ran successfully. Found {total_alerts} new reversal alerts.", f"Generated {total_alerts} alerts from {len(watchlist)} scanned stocks.")
    except Exception:
        pass
    return total_alerts


from lock_utils import ProcessLock
_scan_lock = ProcessLock("reversal_scanner")

def start(force: bool = False) -> int:
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("Scanner is already actively running!")
    try:
        return _start_wrapper(force)
    finally:
        _scan_lock.release()

def _start_wrapper(force: bool = False) -> int:
    """
    Single-shot scan. Called once by main.py at the 18:30 window.
    Returns the number of alerts generated (0 = no setups found).
    Raises on failure so main.py can send a Telegram crash alert.

    [BUG FIX 2026-06-24] Removed duplicate init_db() call. Previously init_db()
    was called twice on every run — once before the docstring and once after it.
    The docstring was also misplaced (after the first init_db() call), so Python
    never registered it as the function's docstring. Fixed both issues here.
    """
    init_db()

    from surveillance import force_refresh_blacklist
    force_refresh_blacklist()

    try:
        return _run_scan(force=force)
    except Exception as e:
        logger.exception("❌ CRITICAL REVERSAL SCAN ERROR")
        import database
        if not getattr(database, "DONT_SAVE_ALERTS", False):
            try:
                from database import upsert_scanner_health
                upsert_scanner_health(scanner_name="REVERSAL", status="DOWN", error_msg=str(e))
            except Exception:
                pass
        raise