import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time

from technical_indicators import apply_indicators
from watchlist_cache import get_watchlist
from price_cache import fetch_watchlist_data
from database import (
    upsert_breakout_watchlist,
    get_active_breakout_watchlist,
    sweep_stale_breakout_watchlist,
    check_recent_alert,
    save_alert_if_new,
    mark_breakout_watchlist_cooldown,
    upsert_scanner_health
)
import json
from config import MIN_STOCK_PRICE

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

def strip_forming_candle(df, tf_minutes, ist_now):
    """Remove the forming (incomplete) candle from dataframe if it's still being built.
    
    Returns DataFrame with last row removed if incomplete, or original df if complete.
    CALLER MUST ALWAYS CHECK: if df is None or df.empty
    """
    import pandas as pd
    if df is None or df.empty:
        return df
    try:
        raw_ts = pd.Timestamp(df.index[-1])
        if raw_ts.tzinfo is not None:
            raw_ts = raw_ts.tz_convert(IST)
        else:
            raw_ts = raw_ts.tz_localize(IST)
            
        candle_start = raw_ts.replace(tzinfo=None)
        candle_end   = candle_start + pd.Timedelta(minutes=tf_minutes)
        now_naive    = ist_now.replace(tzinfo=None)
        
        if now_naive < candle_end:
            return df.iloc[:-1].copy()
    except Exception:
        pass
    return df


from macro_utils import get_macro_regime, get_nifty_20d_return

def run_hourly_phase():
    """
    Phase A: Scans the entire fundamental universe on a 1H timeframe.
    Goal: Identify trend permission (Price > 200 EMA, 9 > 20 > 50 EMA, ADX > 20).
    Adds to breakout_watchlist as HOURLY_APPROVED.
    """
    logger.info("🕒 Starting Phase A (1H Trend Scanner)...")
    
    # 1. Get fundamental universe
    watchlist = get_watchlist()
    if watchlist.empty:
        logger.warning("No watchlist found.")
        return {"fetched": 0, "total": 0, "stale": 0}

    # 2. Fetch 1H data
    ticker_data = fetch_watchlist_data(watchlist, period="60d", interval="1h")
    
    # Handle rate limiting or fetch failures gracefully - continue with partial data
    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 7] Standardized missing data fallback if fetch_watchlist_data fails
    if ticker_data is None:
        ticker_data = {}
        
    fetched_count = len(ticker_data)
    required_count = int(len(watchlist) * 0.70)
    
    if fetched_count < required_count:
        logger.warning(f"⚠️ 1H data fetch returned {fetched_count}/{len(watchlist)} symbols (70% minimum required). Aborting Phase A.")
        raise Exception(f"STALE DATA/INCOMPLETE DATA ERROR: Fetched {fetched_count}/{len(watchlist)} symbols (70% minimum required)")
    else:
        logger.info(f"✅ Successfully fetched {fetched_count} symbols for 1H hourly phase")
        
    stale_count = 0

    # ── FUNNEL STATS: measure how many stocks pass each gate ──────────────
    funnel = {"total": 0, "data_ok": 0, "indicators_ok": 0, "price_ok": 0,
              "ema_pass": 0, "adx_pass": 0, "dist_pass": 0, "approved": 0}
    
    for idx, row in watchlist.iterrows():
        symbol = row["Stock"]
        category = row["Category"]
        funnel["total"] += 1
        
        df = ticker_data.get(symbol)
        if df is None or df.empty or len(df) < 200:
            continue
            
        if getattr(df, 'attrs', {}).get('is_stale') == True:
            logger.debug(f"⏭️ Skipping {symbol} (1H scan) due to stale data.")
            stale_count += 1
            continue

        df = strip_forming_candle(df, 60, datetime.now(IST))
        if df is None or df.empty or len(df) < 2:
            continue
        df = apply_indicators(df, timeframe="1h")
        if df is None or df.empty:
            continue
            
        # Validate indicator columns
        required_cols = ["EMA9", "EMA20", "SMA50", "SMA200", "ADX", "PRIOR_20D_HIGH"]
        if not all(col in df.columns for col in required_cols):
            logger.warning(f"⚠️ {symbol} missing required indicators. Skipping.")
            continue

        funnel["data_ok"] += 1
        latest = df.iloc[-1]
        
        close = float(latest["Close"])
        if close < MIN_STOCK_PRICE:
            continue
            
        # Extract indicators safely — NaN = indicator not ready, hard skip
        def _safe_val(series_val):
            """Return float or None if value is missing/NaN."""
            # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 4] Wrapped in try/except to catch ValueError on unparseable string data
            try:
                if series_val is None:
                    return None
                v = float(series_val)
                if math.isnan(v) or v == 0.0:
                    return None
                return v
            except (TypeError, ValueError):
                return None
        
        e9 = _safe_val(latest.get("EMA9"))
        e20 = _safe_val(latest.get("EMA20"))
        s50 = _safe_val(latest.get("SMA50"))
        s200 = _safe_val(latest.get("SMA200"))
        adx_val = _safe_val(latest.get("ADX"))
        prior_high = _safe_val(latest.get("PRIOR_20D_HIGH"))
        
        # Any uncomputed indicator = hard skip (not silently pass)
        if any(v is None for v in (e9, e20, s50, s200, adx_val, prior_high)):
            logger.debug(f"⏭️ {symbol} skipped — indicator NaN/missing "
                         f"(e9={e9}, e20={e20}, s50={s50}, s200={s200}, adx={adx_val}, prior_high={prior_high})")
            continue
        
        funnel["indicators_ok"] += 1
        
        if prior_high <= 0:
            continue

        funnel["price_ok"] += 1
            
        dist_to_breakout = (prior_high - close) / prior_high
        
        # Hourly Trend Permission Logic: 9 > 20 > 50, Price > 200, ADX > 20
        # AND price must be within 0.5% to 3.0% of the breakout level
        ema_ok = e9 > e20 and e20 > s50 and close > s200
        adx_ok = adx_val > 20
        dist_ok = 0.00 <= dist_to_breakout <= 0.05
        
        if ema_ok:
            funnel["ema_pass"] += 1
        if ema_ok and adx_ok:
            funnel["adx_pass"] += 1
        if ema_ok and adx_ok and dist_ok:
            funnel["dist_pass"] += 1
            # We have an hourly approved setup!
            now_dt = datetime.now(IST)
            end_of_session = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            if now_dt > end_of_session:
                end_of_session = now_dt
            upsert_breakout_watchlist(
                symbol=symbol,
                category=category,
                current_state="HOURLY_APPROVED",
                h1_status="PASSED",
                breakout_level=prior_high,
                trigger_level=prior_high,
                signal_timestamp=now_dt.isoformat(),
                expires_at=end_of_session.isoformat(),
                timeframe="1h"
            )
            funnel["approved"] += 1
            logger.info(f"✅ {symbol} upgraded to HOURLY_APPROVED (dist: {dist_to_breakout*100:.2f}%).")

    # ── Log the funnel so we can see exactly where stocks drop off ────────
    logger.info(f"📊 Phase A Funnel: total={funnel['total']} → data_ok={funnel['data_ok']} → "
                f"indicators_ok={funnel['indicators_ok']} → price_ok={funnel['price_ok']} → "
                f"ema_pass={funnel['ema_pass']} → adx_pass={funnel['adx_pass']} → "
                f"dist_pass={funnel['dist_pass']} → approved={funnel['approved']}")
            
    return {"fetched": len(ticker_data), "total": len(watchlist), "stale": stale_count}

def run_lower_tf_phase(current_regime="BULL"):
    """
    Phase B, C & D: Sub-hourly updater.
    Iterates active watchlist items and advances them through the 4-phase signal ladder:
      HOURLY_APPROVED → (30m) SETUP_ARMED → (15m) ENTRY_READY → (5m) TRADE_ACTIVE
    """
    logger.info("⚡ Starting Phase B/C/D (Sub-hourly Ladder Updater)...")
    
    active_items = get_active_breakout_watchlist()
    if not active_items:
        logger.info("No active setups to track.")
        return {"fetched": 0, "total": 0, "stale": 0}

    # Bucket symbols by required timeframe to minimize downloads
    # SETUP_ARMED and ENTRY_READY also need 30m data for the 3% decay safety check
    needs_30m = list(set(
        i["symbol"] for i in active_items
        if i["current_state"] in ("HOURLY_APPROVED", "SETUP_ARMED", "ENTRY_READY")
    ))
    needs_15m = [i["symbol"] for i in active_items if i["current_state"] == "SETUP_ARMED"]
    needs_5m  = [i["symbol"] for i in active_items if i["current_state"] == "ENTRY_READY"]
    
    import pandas as pd
    data_30m = fetch_watchlist_data(pd.DataFrame({"Stock": needs_30m}), period="1mo", interval="30m") if needs_30m else {}
    if data_30m is None:
        data_30m = {}
    data_15m = fetch_watchlist_data(pd.DataFrame({"Stock": needs_15m}), period="5d", interval="15m") if needs_15m else {}
    if data_15m is None:
        data_15m = {}
    data_5m  = fetch_watchlist_data(pd.DataFrame({"Stock": needs_5m}),  period="1mo", interval="5m") if needs_5m  else {}
    if data_5m is None:
        data_5m = {}
        
    def _check_fetch(data_dict, needed_list, tf_label):
        if not needed_list: return
        req_len = len(needed_list)
        f_len = len(data_dict) if data_dict else 0
        if f_len < int(req_len * 0.70):
            raise Exception(f"STALE DATA/INCOMPLETE DATA ERROR: Fetched {f_len}/{req_len} symbols for {tf_label} (70% minimum required)")

    _check_fetch(data_30m, needs_30m, "30m")
    _check_fetch(data_15m, needs_15m, "15m")
    _check_fetch(data_5m, needs_5m, "5m")

    stale_count = 0

    ist_now = datetime.now(IST)
    end_of_session = ist_now.replace(hour=15, minute=30, second=0, microsecond=0)
    if ist_now > end_of_session:
        end_of_session = ist_now
    
    # Funnel stats for Phase B/C/D
    lower_funnel = {"armed_candidates": 0, "bb_pass": 0, "armed": 0,
                    "entry_candidates": 0, "ema15_pass": 0, "entry_ready": 0,
                    "trigger_candidates": 0, "triggered": 0}

    for item in active_items:
        symbol = item["symbol"]
        state = item["current_state"]
        cat = item["category"]
        breakout_level = item["breakout_level"] or 0

        if breakout_level <= 0:
            continue

        # ── EXPIRY + DECAY: applies to both SETUP_ARMED and ENTRY_READY ──
        if state in ("SETUP_ARMED", "ENTRY_READY"):
            state_change_str = None
            
            # Try to get it from context_json first
            ctx_str = item.get("context_json")
            if ctx_str:
                try:
                    ctx_dict = json.loads(ctx_str)
                    state_change_str = ctx_dict.get("last_state_change_at")
                except Exception:
                    pass
                    
            # Fallback to armed_at if not in context
            if not state_change_str:
                state_change_str = item.get("armed_at")
                
            if state_change_str:
                try:
                    state_change_ts = datetime.strptime(state_change_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=IST)
                except Exception:
                    state_change_ts = None
            else:
                state_change_ts = None

            # Decay & Smart Expiry check
            df = data_30m.get(symbol)
            if df is not None:
                if getattr(df, 'attrs', {}).get('is_stale') == True:
                    logger.debug(f"⏭️ Skipping {symbol} (30m decay check) due to stale data.")
                    stale_count += 1
                    continue

                df = strip_forming_candle(df, 30, ist_now)
                if df is not None and len(df) >= 2:
                    close = float(df["Close"].iloc[-1])
                    drift = (breakout_level - close) / breakout_level
                    
                    is_expired = False
                    if state_change_ts:
                        age_seconds = (ist_now - state_change_ts).total_seconds()
                        if age_seconds > 3600 * 4 and drift > 0.015:
                            is_expired = True

                    if drift > 0.03:
                        upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="HOURLY_APPROVED")
                        state = "HOURLY_APPROVED"
                        logger.info(f"⚠️ {symbol} fell >3% from resistance. Downgraded to HOURLY_APPROVED.")
                    elif is_expired:
                        upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="HOURLY_APPROVED")
                        state = "HOURLY_APPROVED"
                        logger.info(f"⏳ {symbol} {item['current_state']} expired (stale >4h + drifted >1.5%). Downgraded.")

        # ── Phase B (30m): HOURLY_APPROVED → SETUP_ARMED ─────────────────
        if state == "HOURLY_APPROVED":
            lower_funnel["armed_candidates"] += 1
            df = data_30m.get(symbol)
            if df is not None:
                if getattr(df, 'attrs', {}).get('is_stale') == True:
                    logger.debug(f"⏭️ Skipping {symbol} (30m upgrade check) due to stale data.")
                    stale_count += 1
                    continue

                # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 12] Added defensive checks on strip_forming_candle return value
                df = strip_forming_candle(df, 30, ist_now)
                # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 11] Added explicit debug logging for empty dataframes rather than silently skipping
                if df is None or df.empty or len(df) < 2:
                    logger.debug(f"⏭️ {symbol} phase B: insufficient 30m data")
                    continue
                df = apply_indicators(df, timeframe="30m")
                if df.empty:
                    logger.debug(f"⏭️ {symbol} phase B: indicators failed to compute")
                    continue
                latest = df.iloc[-1]
                bb_pctile = float(latest.get("BB_WIDTH_PCTILE", 1.0) or 1.0)
                
                close = float(latest["Close"])
                dist_to_breakout = (breakout_level - close) / breakout_level
                
                # Consolidation formed (tight BB) AND near breakout level
                if bb_pctile < 0.30 and (0.003 <= dist_to_breakout <= 0.02):
                    lower_funnel["bb_pass"] += 1
                    swing_low = float(latest.get("SWING_LOW", close))
                    ema20 = float(latest.get("EMA20", close))
                    
                    ctx_json = json.dumps({"last_state_change_at": ist_now.strftime('%Y-%m-%d %H:%M:%S')})
                    now_iso = ist_now.isoformat()
                    expires_iso = min(ist_now + timedelta(minutes=60), end_of_session).isoformat()
                    
                    upsert_breakout_watchlist(
                        symbol=symbol, category=cat, current_state="SETUP_ARMED", m30_status="PASSED",
                        trigger_level=breakout_level,
                        invalidation_level=min(swing_low, ema20),
                        max_extension_atr=0.8,
                        buffer_pct=0.0015,
                        armed_at=ist_now.strftime('%Y-%m-%d %H:%M:%S'),
                        context_json=ctx_json,
                        signal_timestamp=now_iso,
                        expires_at=expires_iso,
                        timeframe="30m"
                    )
                    lower_funnel["armed"] += 1
                    state = "SETUP_ARMED"
                    logger.info(f"🎯 {symbol} upgraded to SETUP_ARMED (bb_pctile={bb_pctile:.2f}, dist={dist_to_breakout*100:.2f}%).")

        # ── Phase C (15m): SETUP_ARMED → ENTRY_READY ─────────────────────
        if state == "SETUP_ARMED":
            lower_funnel["entry_candidates"] += 1
            df = data_15m.get(symbol)
            if df is not None:
                if getattr(df, 'attrs', {}).get('is_stale') == True:
                    logger.debug(f"⏭️ Skipping {symbol} (15m entry check) due to stale data.")
                    stale_count += 1
                    continue

                # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 12] Defensive check against strip_forming_candle None return
                df = strip_forming_candle(df, 15, ist_now)
                # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 11] Added explicit debug logging for empty dataframes rather than silently skipping
                if df is None or df.empty or len(df) < 2:
                    logger.debug(f"⏭️ {symbol} phase C: insufficient 15m data")
                    continue
                df = apply_indicators(df, timeframe="15m")
                if df.empty:
                    logger.debug(f"⏭️ {symbol} phase C: indicators failed to compute")
                    continue

                latest = df.iloc[-1]
                e9_15 = float(latest.get("EMA9", 0) or 0)
                e20_15 = float(latest.get("EMA20", 0) or 0)
                close = float(latest["Close"])

                if e9_15 <= 0 or e20_15 <= 0:
                    continue

                dist_to_breakout = (breakout_level - close) / breakout_level

                # 15m must show micro-alignment: EMA9 > EMA20, price still near level
                if e9_15 > e20_15 and (0.002 <= dist_to_breakout <= 0.02):
                    lower_funnel["ema15_pass"] += 1
                    ctx_json = json.dumps({
                        "last_state_change_at": ist_now.strftime('%Y-%m-%d %H:%M:%S'),
                        "15m_e9": round(e9_15, 2),
                        "15m_e20": round(e20_15, 2)
                    })
                    now_iso = ist_now.isoformat()
                    expires_iso = min(ist_now + timedelta(minutes=30), end_of_session).isoformat()
                    
                    upsert_breakout_watchlist(
                        symbol=symbol, category=cat, current_state="ENTRY_READY",
                        m15_status="PASSED",
                        context_json=ctx_json,
                        signal_timestamp=now_iso,
                        expires_at=expires_iso,
                        timeframe="15m"
                    )
                    lower_funnel["entry_ready"] += 1
                    state = "ENTRY_READY"
                    logger.info(f"🟡 {symbol} promoted to ENTRY_READY "
                                f"(15m e9={e9_15:.2f} > e20={e20_15:.2f}, "
                                f"dist={dist_to_breakout*100:.2f}%)")

        # ── Phase D (5m): ENTRY_READY → TRADE_ACTIVE (Final Trigger) ─────
        if state == "ENTRY_READY":
            lower_funnel["trigger_candidates"] += 1
            df = data_5m.get(symbol)
            if df is not None:
                if getattr(df, 'attrs', {}).get('is_stale') == True:
                    logger.debug(f"⏭️ Skipping {symbol} (5m trigger check) due to stale data.")
                    continue

                # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 12] Defensive check against strip_forming_candle None return
                df = strip_forming_candle(df, 5, ist_now)
                # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 11] Added explicit debug logging for empty dataframes rather than silently skipping
                if df is None or df.empty or len(df) < 2:
                    logger.debug(f"⏭️ {symbol} phase D: insufficient 5m data")
                    continue
                df = apply_indicators(df, timeframe="5m")
                if df.empty or "EMA9" not in df.columns or "ATR20" not in df.columns or "Volume" not in df.columns:
                    logger.debug(f"⏭️ {symbol} phase D: missing required 5m indicators")
                    continue
                    
                latest = df.iloc[-1]
                prev = df.iloc[-2]
                
                trigger_level = float(item.get("trigger_level") or breakout_level)
                max_ext_atr = float(item.get("max_extension_atr") or 0.8)
                buffer_val = trigger_level * float(item.get("buffer_pct") or 0.0015)
                
                e9 = float(latest.get("EMA9", 0))
                close = float(latest["Close"])
                low = float(latest["Low"])
                open_px = float(latest["Open"])
                atr20 = float(latest.get("ATR20", 0.0) or 0.0)
                
                if atr20 <= 0:
                    continue
                
                if len(df) >= 22:
                    mean_vol = max(float(df["Volume"].iloc[-21:-1].mean() or 1.0), 1.0)
                else:
                    mean_vol = max(float(df["Volume"].iloc[:-1].mean() or 1.0), 1.0)
                vol_ratio = float(latest["Volume"]) / mean_vol
                
                # Extension limit strict check
                if close > trigger_level + (max_ext_atr * atr20):
                    continue

                is_ready = False
                trigger_type = ""
                
                candle_range = float(latest["High"]) - float(latest["Low"])
                if candle_range > 0:
                    close_position = (close - low) / candle_range
                    upper_wick_ratio = (float(latest["High"]) - close) / candle_range
                else:
                    close_position = 0.5
                    upper_wick_ratio = 0.0
                
                # Thrust/Continuation Trigger
                # Price breaks local high while still close to level, with volume
                if close > float(prev["High"]) and close > (trigger_level + buffer_val) and vol_ratio > 1.2:
                    if close_position >= 0.6 and upper_wick_ratio < 0.35:
                        is_ready = True
                        trigger_type = "thrust"
                    
                # Pullback Trigger
                # Breakout level or EMA9 is defended, and price reclaims with volume and strong rejection
                elif low <= max(trigger_level, e9):
                    if close >= trigger_level and close > float(prev["High"]) and close > open_px and vol_ratio > 1.0:
                        if close_position >= 0.6:  # strong interaction/engulfing
                            is_ready = True
                            trigger_type = "pullback"
                
                if is_ready:
                    # Do not generate new buy alerts on stale data returned by provider
                    if getattr(df, 'attrs', {}).get('is_stale'):
                        logger.info(f"Skipping buy alert for {symbol} because data is stale")
                        continue
                    # Idempotency check before alert using stricter symbol-trigger key
                    dedup_key = f"{cat}|MULTI_TF|{symbol}|{trigger_type}|{ist_now.strftime('%Y-%m-%d')}"
                    if not check_recent_alert(symbol, "INTRADAY", dedup_key, lookback_minutes=390):
                        # Direct structural stop using max for tighter stop
                        invalidation_level = float(item.get("invalidation_level") or (low - atr20))
                        structure_sl = min(low, float(prev["Low"])) - (0.2 * atr20)
                        final_sl = max(structure_sl, invalidation_level)
                        if final_sl >= close:
                            final_sl = close - (0.5 * atr20) # Fallback if invalidation is too high
                            
                        calc_target = close + ((close - final_sl) * 2)
                        
                        ctx = json.dumps({
                            "ladder": "TRADE_ACTIVE",
                            "breakout_level": round(trigger_level, 2),
                            "trigger": trigger_type,
                            "vol_ratio": round(vol_ratio, 2),
                            "final_sl": round(final_sl, 2),
                            "invalidation_level": round(invalidation_level, 2)
                        })
                        
                        save_alert_if_new(
                            symbol=symbol,
                            breakout_type="INTRADAY",
                            alert_time=ist_now.strftime('%Y-%m-%d %H:%M:%S+05:30'),
                            scanner="multi_tf_scanner",
                            category=cat,
                            entry_price=close,
                            stop_loss=final_sl,
                            target_price=calc_target,
                            signals=f"Multi-TF Ladder (1h→30m→15m→5m) | {trigger_type}",
                            score=min(100, int(80 + (vol_ratio * 5))), # Dynamic conviction
                            rsi=float(latest.get("RSI", 0)),
                            volume_ratio=vol_ratio,
                            context_json=ctx,
                            bayesian_regime=current_regime
                        )
                        upsert_breakout_watchlist(
                            symbol=symbol, category=cat, current_state="TRADE_ACTIVE",
                            m5_status="PASSED"
                        )
                        mark_breakout_watchlist_cooldown(symbol, "TRADE_ACTIVE", hours=24)
                        lower_funnel["triggered"] += 1
                        logger.info(f"🔔 {symbol} EXECUTED! TRADE_ACTIVE alert generated via {trigger_type}.")

    # ── Log the funnel so we can see exactly where stocks drop off ────────
    logger.info(f"📊 Phase B/C/D Funnel: "
                f"30m_candidates={lower_funnel['armed_candidates']} → bb_pass={lower_funnel['bb_pass']} → armed={lower_funnel['armed']} | "
                f"15m_candidates={lower_funnel['entry_candidates']} → ema15_pass={lower_funnel['ema15_pass']} → entry_ready={lower_funnel['entry_ready']} | "
                f"5m_candidates={lower_funnel['trigger_candidates']} → triggered={lower_funnel['triggered']}")

    unique_needed = set(needs_30m) | set(needs_15m) | set(needs_5m)
    unique_fetched = set(data_30m.keys()) | set(data_15m.keys()) | set(data_5m.keys())
    return {"fetched": len(unique_fetched), "total": len(unique_needed), "stale": stale_count}

def run_sweeper():
    counts = sweep_stale_breakout_watchlist()
    if counts:
        counts_str = ", ".join(f"{k}: {v}" for k, v in counts.items())
        logger.info(f"🧹 Swept stale breakout watchlist setups. Expired -> {counts_str}")
    else:
        logger.info("🧹 Swept stale breakout watchlist setups. No expirations.")

from lock_utils import ProcessLock
_scan_lock = ProcessLock("multi_tf_scanner")

def start(run_once=False):
    if run_once:
        if not _scan_lock.acquire(blocking=False):
            raise RuntimeError("Scanner is already actively running!")
    else:
        while not _scan_lock.acquire(blocking=False):
            import time
            time.sleep(60)
    try:
        return _start_wrapper(run_once)
    finally:
        _scan_lock.release()

def _start_wrapper(run_once=False):
    from datetime import time as dt_time
    while True:
        try:
            ist_now = datetime.now(IST)
            current_time = ist_now.time()
            weekday = ist_now.weekday()
            
            scan_start = datetime.now(IST)
            logger.info("=========================================")
            logger.info(f"🚀 [START] MULTI-TF LADDER INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')}")

            from market_utils import is_market_open
            market_open = is_market_open(ist_now)
            is_active_window = market_open or run_once
            
            import database
            if not is_active_window:
                logger.info("Market closed. Scanner pausing until next market session...")
                if not getattr(database, "DONT_SAVE_ALERTS", False):
                    try:
                        upsert_scanner_health(
                            scanner_name="MULTI_TF",
                            status="IDLE",
                            scheduled_for="Every 5min (10:17 AM - 3:30 PM)"
                        )
                    except Exception:
                        pass
                time.sleep(300)
                continue
            else:
                pass
                
            # Cache regime once per cycle
            # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 1] Pass nifty_ret explicitly to get_macro_regime to avoid redundant API calls
            try:
                nifty_ret = get_nifty_20d_return()
                current_regime = get_macro_regime(nifty_ret)
            except Exception as e:
                logger.warning(f"⚠️ Failed to compute macro regime: {e}. Defaulting to BULL.")
                current_regime = "BULL"
            
            # 1. Sweep old states
            run_sweeper()
            
            # 2. Hourly phase (could be scheduled to only run top/bottom of hour, but we run it to keep it simple or wrapper handles scheduling)
            metrics_a = run_hourly_phase()
            
            # 3. Lower TF updater
            metrics_b = run_lower_tf_phase(current_regime)
            
            elapsed_time = (datetime.now(IST) - scan_start).total_seconds()
            logger.info("=========================================")
            logger.info(f"✅ [COMPLETE] MULTI-TF LADDER DONE | {elapsed_time:.2f}s | Status=OK")
            logger.info("=========================================")

            status = "OK" if market_open else "IDLE"
            error_msg = None
            
            total_stale = (metrics_a.get("stale", 0) + metrics_b.get("stale", 0))
            total_symbols = (metrics_a.get("total", 0) + metrics_b.get("total", 0))
            
            if total_symbols > 0 and total_stale / total_symbols > 0.1:
                status = "DEGRADED"
                error_msg = f"Stale Data: {total_stale}/{total_symbols} symbols"
                
            total_fetched_a = metrics_a.get("fetched", 0)
            total_fetched_b = metrics_b.get("fetched", 0)
            total_expected_a = metrics_a.get("total", 0)
            total_expected_b = metrics_b.get("total", 0)
            
            if (total_expected_a > 0 and total_fetched_a < total_expected_a) or (total_expected_b > 0 and total_fetched_b < total_expected_b):
                status = "DEGRADED"
                error_msg = f"Partial Fetch: {total_fetched_a + total_fetched_b}/{total_expected_a + total_expected_b} symbols"
            
            if not getattr(database, "DONT_SAVE_ALERTS", False):
                try:
                    upsert_scanner_health(
                        scanner_name="MULTI_TF",
                        status=status,
                        last_success=datetime.now(IST).isoformat(),
                        total_count=metrics_a.get("total", 0),
                        error_msg=error_msg,
                        scheduled_for="Every 5min (10:17 AM - 3:30 PM)"
                    )
                except Exception:
                    logger.exception("❌ Failed to update scanner health for MULTI_TF")
            
            if run_once:
                break
                
            logger.info("💤 Sleeping 5 minutes before next Multi-TF ladder run...")
            time.sleep(300)
            
        except Exception as e:
            logger.exception("❌ MULTI-TF LADDER CRASHED")
            if not getattr(database, "DONT_SAVE_ALERTS", False):
                try:
                    upsert_scanner_health(
                        scanner_name="MULTI_TF",
                        status="DOWN",
                        error_msg=str(e)[:500],
                        scheduled_for="Every 5min (10:17 AM - 3:30 PM)"
                    )
                except Exception as ex:
                    logger.exception("Failed to update scanner health to DOWN")

            if run_once:
                raise
            time.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start(run_once=True)
