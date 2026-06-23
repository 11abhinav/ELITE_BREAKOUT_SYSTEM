import logging
from datetime import datetime
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
    import pandas as pd
    if df is None or df.empty:
        return df
    
    datetime_col = next((c for c in ["Datetime", "Date", "index"] if c in df.columns), None)
    if datetime_col is not None:
        try:
            raw_ts = pd.Timestamp(df.iloc[-1][datetime_col])
            if raw_ts.tzinfo is not None:
                raw_ts = raw_ts.tz_convert("Asia/Kolkata")
            candle_start = raw_ts.replace(tzinfo=None)
            candle_end   = candle_start + pd.Timedelta(minutes=tf_minutes)
            now_naive    = ist_now.replace(tzinfo=None)
            if now_naive < candle_end:
                return df.iloc[:-1].copy()
        except Exception:
            pass
    return df


def get_market_regime():
    try:
        from data_provider import get_fetcher
        fetcher = get_fetcher()
        nifty = fetcher.get_ohlcv("^NSEI", interval="1d", period="1mo")
        if not nifty.empty and len(nifty) >= 20:
            val_now = nifty["Close"].iloc[-1]
            nifty_now = float(val_now.iloc[0]) if hasattr(val_now, 'iloc') else float(val_now)
            val_ago = nifty["Close"].iloc[-20]
            nifty_ago = float(val_ago.iloc[0]) if hasattr(val_ago, 'iloc') else float(val_ago)
            ret = (nifty_now / nifty_ago) - 1
            if ret < -0.05: return "BEAR"
            if ret > 0.05: return "BULL"
    except Exception as e:
        logger.warning(f"Failed to fetch market regime: {e}")
    return "SIDEWAYS"

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
        return

    # 2. Fetch 1H data
    ticker_data = fetch_watchlist_data(watchlist, period="60d", interval="1h")
    
    # Handle rate limiting or fetch failures gracefully - continue with partial data
    if ticker_data is None:
        logger.warning("⚠️ 1H data fetch returned None (rate-limited or API down). Continuing with empty data...")
        ticker_data = {}
    elif not ticker_data:
        logger.warning("⚠️ 1H data fetch returned 0 symbols (likely rate-limited). Continuing with partial data...")
    else:
        logger.info(f"✅ Successfully fetched {len(ticker_data)} symbols for 1H hourly phase")
        try:
            from database import upsert_scanner_health
            upsert_scanner_health("MULTI_TF", "OK", error_msg=None)
        except Exception:
            pass
    
    for idx, row in watchlist.iterrows():
        symbol = row["Stock"]
        category = row["Category"]
        
        df = ticker_data.get(symbol)
        if df is None or df.empty or len(df) < 200:
            continue
            
        if getattr(df, 'attrs', {}).get('is_stale') == True:
            logger.debug(f"⏭️ Skipping {symbol} (1H scan) due to stale data.")
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

        latest = df.iloc[-1]
        
        close = float(latest["Close"])
        if close < MIN_STOCK_PRICE:
            continue
            
        # Extract indicators safely
        e9 = float(latest.get("EMA9", 0) or 0)
        e20 = float(latest.get("EMA20", 0) or 0)
        s50 = float(latest.get("SMA50", 0) or 0)
        s200 = float(latest.get("SMA200", 0) or 0)
        adx = float(latest.get("ADX", 0) or 0)
        prior_high = float(latest.get("PRIOR_20D_HIGH", 0) or 0)
        
        if prior_high <= 0:
            continue
            
        dist_to_breakout = (prior_high - close) / prior_high
        
        # Hourly Trend Permission Logic: 9 > 20 > 50, Price > 200, ADX > 20
        # AND price must be within 0.5% to 3.0% of the breakout level
        if e9 > e20 and e20 > s50 and close > s200 and adx > 20:
            if 0.005 <= dist_to_breakout <= 0.03:
                # We have an hourly approved setup!
                upsert_breakout_watchlist(
                    symbol=symbol,
                    category=category,
                    current_state="HOURLY_APPROVED",
                    h1_status="PASSED",
                    breakout_level=prior_high,
                    trigger_level=prior_high
                )
                logger.info(f"✅ {symbol} upgraded to HOURLY_APPROVED (dist: {dist_to_breakout*100:.2f}%).")

def run_lower_tf_phase(current_regime="BULL"):
    """
    Phase B & C: Sub-hourly updater.
    Iterates active watchlist items and advances them through the signal ladder.
    """
    logger.info("⚡ Starting Phase B/C (Sub-hourly Ladder Updater)...")
    
    active_items = get_active_breakout_watchlist()
    if not active_items:
        logger.info("No active setups to track.")
        return

    # Bucket symbols by required timeframe to minimize downloads
    needs_30m = [i["symbol"] for i in active_items if i["current_state"] == "HOURLY_APPROVED"]
    needs_5m  = [i["symbol"] for i in active_items if i["current_state"] in ("SETUP_ARMED", "ENTRY_READY")]
    
    import pandas as pd
    data_30m = fetch_watchlist_data(pd.DataFrame({"Stock": needs_30m}), period="1mo", interval="30m") if needs_30m else {}
    if data_30m is None:
        data_30m = {}
    data_5m  = fetch_watchlist_data(pd.DataFrame({"Stock": needs_5m}),  period="1mo", interval="5m") if needs_5m  else {}
    if data_5m is None:
        data_5m = {}

    ist_now = datetime.now(IST)

    for item in active_items:
        symbol = item["symbol"]
        state = item["current_state"]
        cat = item["category"]
        breakout_level = item["breakout_level"] or 0

        if breakout_level <= 0:
            continue

        # Failed state check for SETUP_ARMED
        if state == "SETUP_ARMED":
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
                    if (ist_now - state_change_ts).total_seconds() > 3600 * 4: # 4 hours expiry
                        upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="HOURLY_APPROVED")
                        state = "HOURLY_APPROVED"
                        logger.info(f"⏳ {symbol} SETUP_ARMED expired (stale). Downgraded to HOURLY_APPROVED.")
                except Exception:
                    pass

            df = data_30m.get(symbol)
            if df is not None:
                if getattr(df, 'attrs', {}).get('is_stale') == True:
                    logger.debug(f"⏭️ Skipping {symbol} (30m decay check) due to stale data.")
                    continue

                df = strip_forming_candle(df, 30, ist_now)
                if df is not None and len(df) >= 2:
                    close = float(df["Close"].iloc[-1])
                    # If we fall >3% away from resistance, drop back to HOURLY_APPROVED
                    if (breakout_level - close) / breakout_level > 0.03:
                        upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="HOURLY_APPROVED")
                        state = "HOURLY_APPROVED"
                        logger.info(f"⚠️ {symbol} fell >3% from resistance. Downgraded to HOURLY_APPROVED.")

        # ── 30-Min: SETUP_ARMED ───────────────────────────────────────────
        if state == "HOURLY_APPROVED":
            df = data_30m.get(symbol)
            if df is not None:
                if getattr(df, 'attrs', {}).get('is_stale') == True:
                    logger.debug(f"⏭️ Skipping {symbol} (30m upgrade check) due to stale data.")
                    continue

                df = strip_forming_candle(df, 30, ist_now)
                if df is None or df.empty or len(df) < 2:
                    continue
                df = apply_indicators(df, timeframe="30m")
                if df.empty:
                    continue
                latest = df.iloc[-1]
                bb_pctile = float(latest.get("BB_WIDTH_PCTILE", 1.0) or 1.0)
                
                close = float(latest["Close"])
                dist_to_breakout = (breakout_level - close) / breakout_level
                
                # Consolidation formed (tight BB) AND near breakout level
                if bb_pctile < 0.30 and (0.003 <= dist_to_breakout <= 0.02):
                    swing_low = float(latest.get("SWING_LOW", close))
                    ema20 = float(latest.get("EMA20", close))
                    
                    ctx_json = json.dumps({"last_state_change_at": ist_now.strftime('%Y-%m-%d %H:%M:%S')})
                    
                    upsert_breakout_watchlist(
                        symbol=symbol, category=cat, current_state="SETUP_ARMED", m30_status="PASSED",
                        trigger_level=breakout_level,
                        invalidation_level=min(swing_low, ema20),
                        max_extension_atr=0.8,
                        buffer_pct=0.0015,
                        armed_at=ist_now.strftime('%Y-%m-%d %H:%M:%S'),
                        context_json=ctx_json
                    )
                    logger.info(f"🎯 {symbol} upgraded to SETUP_ARMED (bb_pctile={bb_pctile:.2f}, dist={dist_to_breakout*100:.2f}%).")

        # ── 5-Min: FINAL TRIGGER EXECUTION ──────────────────────────────
        elif state == "SETUP_ARMED" or state == "ENTRY_READY":
            df = data_5m.get(symbol)
            if df is not None:
                if getattr(df, 'attrs', {}).get('is_stale') == True:
                    logger.debug(f"⏭️ Skipping {symbol} (5m trigger check) due to stale data.")
                    continue

                df = strip_forming_candle(df, 5, ist_now)
                if df is None or df.empty or len(df) < 2:
                    continue
                df = apply_indicators(df, timeframe="5m")
                if df.empty or "EMA9" not in df.columns or "ATR20" not in df.columns or "Volume" not in df.columns:
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
                    if not check_recent_alert(symbol, "INTRADAY", dedup_key, minutes=390):
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
                            alert_time=ist_now.strftime('%Y-%m-%d %H:%M:%S'),
                            scanner="multi_tf_scanner",
                            category=cat,
                            entry_price=close,
                            stop_loss=final_sl,
                            target_price=calc_target,
                            signals=f"Multi-TF Ladder (1h->30m->5m) | {trigger_type}",
                            score=min(100, int(80 + (vol_ratio * 5))), # Dynamic conviction
                            rsi=float(latest.get("RSI", 0)),
                            volume_ratio=vol_ratio,
                            context_json=ctx,
                            bayesian_regime=current_regime
                        )
                        upsert_breakout_watchlist(
                            symbol=symbol, category=cat, current_state="TRADE_ACTIVE"
                        )
                        mark_breakout_watchlist_cooldown(symbol, "TRADE_ACTIVE", hours=24)
                        logger.info(f"🔔 {symbol} EXECUTED! TRADE_ACTIVE alert generated via {trigger_type}.")

def run_sweeper():
    sweep_stale_breakout_watchlist()
    logger.info("🧹 Swept stale breakout watchlist setups.")

def start(run_once=False):
    from datetime import time as dt_time
    while True:
        try:
            ist_now = datetime.now(IST)
            current_time = ist_now.time()
            weekday = ist_now.weekday()
            
            logger.info("=========================================")
            logger.info(f"🚦 MULTI-TF LADDER START | {ist_now.strftime('%H:%M:%S IST')}")
            
            market_open = dt_time(9, 15) <= current_time <= dt_time(15, 30) and weekday < 5
            is_active_window = market_open or run_once
            
            import database
            if not is_active_window:
                logger.info("Market closed, running in TEST MODE (no db saves).")
                database.DONT_SAVE_ALERTS = True
            else:
                database.DONT_SAVE_ALERTS = False
                
            # Cache regime once per cycle
            current_regime = get_market_regime()
            
            # 1. Sweep old states
            run_sweeper()
            
            # 2. Hourly phase (could be scheduled to only run top/bottom of hour, but we run it to keep it simple or wrapper handles scheduling)
            run_hourly_phase()
            
            # 3. Lower TF updater
            run_lower_tf_phase(current_regime)
            
            logger.info("🚦 MULTI-TF LADDER COMPLETE.")
            
            # Reset DONT_SAVE_ALERTS back to False just in case
            database.DONT_SAVE_ALERTS = False
            
            try:
                upsert_scanner_health(
                    scanner_name="MultiTFScanner",
                    status="OK" if market_open else "IDLE",
                    last_success=datetime.now(IST).isoformat()
                )
            except Exception:
                logger.exception("❌ Failed to update scanner health for MULTI_TF")
            
            if run_once:
                break
                
            logger.info("💤 Sleeping 5 minutes before next Multi-TF ladder run...")
            time.sleep(300)
            
        except Exception as e:
            logger.exception(f"❌ MULTI-TF LADDER CRASHED: {e}")
            try:
                upsert_scanner_health(
                    scanner_name="MULTI_TF",
                    status="DOWN",
                    error_msg=str(e)[:500]
                )
            except Exception:
                pass

            if run_once:
                break
            time.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start(run_once=True)
