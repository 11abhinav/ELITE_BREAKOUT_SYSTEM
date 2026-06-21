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
    mark_breakout_watchlist_cooldown
)
import json
from config import MIN_STOCK_PRICE

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

def get_market_regime():
    try:
        import yfinance as yf
        nifty = yf.download("^NSEI", period="1mo", interval="1d", progress=False)
        if not nifty.empty and len(nifty) >= 20:
            ret = (float(nifty["Close"].iloc[-1]) / float(nifty["Close"].iloc[-20])) - 1
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
    symbols = watchlist["Stock"].tolist()
    ticker_data = fetch_watchlist_data(symbols, "1h", "60d", force_fresh=True)

    for idx, row in watchlist.iterrows():
        symbol = row["Stock"]
        category = row["Category"]
        
        df = ticker_data.get(symbol)
        if df is None or df.empty or len(df) < 200:
            continue

        df = apply_indicators(df, timeframe="1h")
        if df is None or df.empty:
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
        
        # Hourly Trend Permission Logic: 9 > 20 > 50, Price > 200, ADX > 20
        if e9 > e20 and e20 > s50 and close > s200 and adx > 20:
            # We have an hourly approved setup!
            prior_high = float(latest.get("PRIOR_20D_HIGH", 0) or 0)
            
            # Upsert into breakout_watchlist
            upsert_breakout_watchlist(
                symbol=symbol,
                category=category,
                current_state="HOURLY_APPROVED",
                h1_status="PASSED",
                breakout_level=prior_high
            )
            logger.info(f"✅ {symbol} upgraded to HOURLY_APPROVED.")

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
    needs_15m = [i["symbol"] for i in active_items if i["current_state"] == "SETUP_ARMED"]
    needs_5m  = [i["symbol"] for i in active_items if i["current_state"] in ("BREAKOUT_CONFIRMED", "ENTRY_READY")]
    
    data_30m = fetch_watchlist_data(needs_30m, "30m", "1mo", force_fresh=True) if needs_30m else {}
    data_15m = fetch_watchlist_data(needs_15m, "15m", "1mo", force_fresh=True) if needs_15m else {}
    data_5m  = fetch_watchlist_data(needs_5m,  "5m",  "1mo", force_fresh=True) if needs_5m  else {}

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
            df = data_30m.get(symbol)
            if df is not None and not df.empty:
                close = float(df["Close"].iloc[-1])
                # If we fall >3% away from resistance, drop back to HOURLY_APPROVED
                if (breakout_level - close) / breakout_level > 0.03:
                    upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="HOURLY_APPROVED")
                    state = "HOURLY_APPROVED"
                    logger.info(f"⚠️ {symbol} fell >3% from resistance. Downgraded to HOURLY_APPROVED.")

        # Failed state check for BREAKOUT_CONFIRMED
        if state == "BREAKOUT_CONFIRMED":
            df = data_5m.get(symbol)
            if df is not None and not df.empty:
                close = float(df["Close"].iloc[-1])
                # If we lose the breakout level entirely, drop back to SETUP_ARMED
                if close < breakout_level * 0.995:
                    upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="SETUP_ARMED")
                    state = "SETUP_ARMED"
                    logger.info(f"⚠️ {symbol} lost breakout level. Downgraded to SETUP_ARMED.")

        # ── 30-Min: SETUP_ARMED ───────────────────────────────────────────
        if state == "HOURLY_APPROVED":
            df = data_30m.get(symbol)
            if df is not None and not df.empty:
                df = apply_indicators(df, timeframe="30m")
                latest = df.iloc[-1]
                bb_pctile = float(latest.get("BB_WIDTH_PCTILE", 1.0) or 1.0)
                
                # Consolidation formed (tight BB) OR near breakout level
                if bb_pctile < 0.30 or (0 < (breakout_level - float(latest["Close"])) / breakout_level < 0.03):
                    upsert_breakout_watchlist(
                        symbol=symbol, category=cat, current_state="SETUP_ARMED", m30_status="PASSED"
                    )
                    logger.info(f"🎯 {symbol} upgraded to SETUP_ARMED.")

        # ── 15-Min: BREAKOUT_CONFIRMED ────────────────────────────────────
        elif state == "SETUP_ARMED":
            df = data_15m.get(symbol)
            if df is not None and len(df) >= 22:
                df = apply_indicators(df, timeframe="15m")
                latest = df.iloc[-1]
                close = float(latest["Close"])
                open_px = float(latest["Open"])
                high = float(latest["High"])
                low = float(latest["Low"])
                
                # Clean break of resistance and candle quality
                min_buffer = breakout_level * 1.002
                candle_range = high - low
                upper_wick = high - max(open_px, close)
                upper_wick_ratio = upper_wick / candle_range if candle_range > 0 else 0
                close_in_upper_half = close > (low + candle_range / 2)
                
                if close > min_buffer and close_in_upper_half and upper_wick_ratio < 0.3:
                    mean_vol = max(float(df["Volume"].iloc[-21:-1].mean() or 1.0), 1.0)
                    vol_ratio = float(latest["Volume"]) / mean_vol
                    if vol_ratio > 1.2:
                        upsert_breakout_watchlist(
                            symbol=symbol, category=cat, current_state="BREAKOUT_CONFIRMED", m15_status="PASSED"
                        )
                        logger.info(f"🔥 {symbol} upgraded to BREAKOUT_CONFIRMED.")

        # ── 5-Min: ENTRY_READY & TRADE_ACTIVE ──────────────────────────────
        elif state == "BREAKOUT_CONFIRMED" or state == "ENTRY_READY":
            df = data_5m.get(symbol)
            if df is not None and len(df) >= 22:
                df = apply_indicators(df, timeframe="5m")
                latest = df.iloc[-1]
                prev = df.iloc[-2]
                
                e9 = float(latest.get("EMA9", 0))
                close = float(latest["Close"])
                low = float(latest["Low"])
                atr20 = float(latest.get("ATR20", 0.0) or 0.0)
                mean_vol = max(float(df["Volume"].iloc[-21:-1].mean() or 1.0), 1.0)
                vol_ratio = float(latest["Volume"]) / mean_vol
                
                # Extension limit
                if close > breakout_level + (1.5 * atr20):
                    continue

                is_ready = False
                trigger_type = ""
                
                if close > float(prev["High"]) and vol_ratio > 1.0:  # Continuation
                    is_ready = True
                    trigger_type = "continuation"
                elif low <= e9 and close >= e9:  # Micro pullback
                    if close > float(latest["Open"]) and (vol_ratio > 1.0 or close > float(prev["High"])):
                        is_ready = True
                        trigger_type = "pullback"
                
                if is_ready and state == "BREAKOUT_CONFIRMED":
                    upsert_breakout_watchlist(
                        symbol=symbol, category=cat, current_state="ENTRY_READY", m5_status="PASSED"
                    )
                    state = "ENTRY_READY"
                    logger.info(f"🚀 {symbol} upgraded to ENTRY_READY.")
                
                # Phase C: Final Execution Logic
                if state == "ENTRY_READY":
                    # Idempotency check before alert
                    dedup_key = f"{cat}|MULTI_TF|{ist_now.strftime('%Y-%m-%d')}"
                    if not check_recent_alert(symbol, "INTRADAY", dedup_key, minutes=390):
                        from sl_target_helper import compute_sl_and_target
                        sl_result = compute_sl_and_target(
                            entry_price=close,
                            atr=atr20,
                            candle_range=float(latest["High"]) - float(latest["Low"]),
                            mode="INTRADAY"
                        )
                        
                        ctx = json.dumps({
                            "ladder": "TRADE_ACTIVE",
                            "breakout_level": round(breakout_level, 2),
                            "trigger": trigger_type,
                            "vol_ratio": round(vol_ratio, 2)
                        })
                        
                        save_alert_if_new(
                            symbol=symbol,
                            breakout_type="INTRADAY",
                            scanner="multi_tf_scanner",
                            category=cat,
                            entry_price=close,
                            stop_loss=sl_result["stop_loss"],
                            signals=f"Multi-TF Ladder (1h->30m->15m->5m) | {trigger_type}",
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
                        logger.info(f"🔔 {symbol} EXECUTED! TRADE_ACTIVE alert generated.")

def run_sweeper():
    sweep_stale_breakout_watchlist()
    logger.info("🧹 Swept stale breakout watchlist setups.")

def start(run_once=False):
    while True:
        try:
            logger.info("=========================================")
            logger.info(f"🚦 MULTI-TF LADDER START | {datetime.now(IST).strftime('%H:%M:%S IST')}")
            
            # Cache regime once per cycle
            current_regime = get_market_regime()
            
            # 1. Sweep old states
            run_sweeper()
            
            # 2. Hourly phase (could be scheduled to only run top/bottom of hour, but we run it to keep it simple or wrapper handles scheduling)
            run_hourly_phase()
            
            # 3. Lower TF updater
            run_lower_tf_phase(current_regime)
            
            logger.info("🚦 MULTI-TF LADDER COMPLETE.")
            
            if run_once:
                break
                
            logger.info("💤 Sleeping 5 minutes before next Multi-TF ladder run...")
            time.sleep(300)
            
        except Exception as e:
            logger.exception(f"❌ MULTI-TF LADDER CRASHED: {e}")
            if run_once:
                break
            time.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start(run_once=True)
