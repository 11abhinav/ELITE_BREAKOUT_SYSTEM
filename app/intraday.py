# =====================================================================================
# app/intraday.py (ULTIMATE EDITION)
# EARLY MOMENTUM SCANNER — 15M SETUP + 3x5M TRIGGER + FAKEOUT DEFENSE
# =====================================================================================

import pandas as pd
# Ensure tzcache writable location before importing yfinance (robust import to support different cwd)
try:
    import app.yf_bootstrap
except Exception:
    try:
        import yf_bootstrap
    except Exception:
        pass
import time
import logging
# [BUG FIX 2026-06-24] ThreadPoolExecutor removed — was used for fake parallelism
# with price_cache's global _fetch_lock making it sequential anyway. See fetch section below.

from zoneinfo import ZoneInfo
from datetime import datetime, time as dt_time

from technical_indicators import apply_indicators
from database import init_db, save_alert_if_new, upsert_fetch_error
from price_cache import fetch_watchlist_data  
from watchlist_cache import get_watchlist

from config import (
    MIN_STOCK_PRICE,
)

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

def normalize_index(df, col_candidates=("Datetime", "Date")):
    if df is None or df.empty:
        return df
    df = df.copy()
    col = next((c for c in col_candidates if c in df.columns), None)
    if col:
        ts = pd.to_datetime(df[col])
        df.index = ts.dt.tz_localize(IST) if ts.dt.tz is None else ts.dt.tz_convert(IST)
    else:
        idx = pd.to_datetime(df.index)
        df.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    return df.sort_index()

def strip_forming_candle(df, tf_minutes, ist_now):
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

def evaluate_15m_setup(df15: pd.DataFrame, ist_now: datetime) -> dict | None:
    if df15 is None or len(df15) < 60:
        return None

    df15 = strip_forming_candle(df15, 15, ist_now)
    if df15 is None or len(df15) < 60:
        return None

    last = df15.iloc[-1]
    prev_20 = df15.iloc[-21:-1]
    if prev_20.empty:
        return None
        
    last_ts = last.name
    if not isinstance(last_ts, pd.Timestamp):
        return None

    age_minutes = (ist_now - last_ts).total_seconds() / 60
    if age_minutes > 35 or last_ts.date() != ist_now.date():
        return None

    candle_range = last["High"] - last["Low"]
    if candle_range <= 0:
        return None

    body = abs(last["Close"] - last["Open"])
    body_pct = body / candle_range
    close_pos = (last["Close"] - last["Low"]) / candle_range
    upper_wick = last["High"] - max(last["Open"], last["Close"])
    upper_wick_pct = upper_wick / candle_range

    vol_ma20 = prev_20["Volume"].mean()
    vol_ratio = last["Volume"] / vol_ma20 if vol_ma20 and vol_ma20 > 0 else 0

    breakout_resistance = prev_20["High"].max()
    
    ema50 = last.get("EMA50", 0)
    atr5 = last.get("ATR", 0)
    rsi = last.get("RSI", 50)
    
    if pd.isna(ema50) or pd.isna(atr5) or pd.isna(rsi):
        return None

    checks = {
        "trend": last["Close"] > ema50,
        "range": candle_range >= 0.4 * atr5,
        "momentum": rsi >= 60,
        "volume": vol_ratio >= 2.5,
        "body": body_pct >= 0.55,
        "close_pos": close_pos >= 0.70,
        "upper_wick": upper_wick_pct < 0.25,
        "breakout_close": last["Close"] > breakout_resistance,
        "min_price": last["Close"] >= MIN_STOCK_PRICE
    }

    if not all(checks.values()):
        return None

    return {
        "breakout_resistance": breakout_resistance,
        "close_pos": close_pos,
        "vol_ratio": vol_ratio,
        "rsi": rsi,
        "last_15m_close": last["Close"],
        "last_15m_time": last_ts,
        "atr5": atr5,
    }


def evaluate_5m_trigger(df5: pd.DataFrame, setup: dict, ist_now: datetime) -> dict | None:
    if df5 is None or len(df5) < 40:
        return None

    df5 = strip_forming_candle(df5, 5, ist_now)
    if df5 is None or len(df5) < 40:
        return None

    block_close = setup["last_15m_time"]
    
    seq = df5.loc[(df5.index > block_close - pd.Timedelta(minutes=15)) & (df5.index <= block_close)].copy()
    if len(seq) != 3:
        return None

    resistance = setup["breakout_resistance"]

    closes_above_vwap = (seq["Close"] > seq["VWAP"]).all()
    green_count = (seq["Close"] > seq["Open"]).sum()

    def get_upper_wick_pct(row):
        r = row["High"] - row["Low"]
        if r <= 0:
            return 1.0
        uw = row["High"] - max(row["Open"], row["Close"])
        return uw / r

    wick_ok = seq.apply(get_upper_wick_pct, axis=1).max() <= 0.40
    breakout_candle_wick_ok = get_upper_wick_pct(seq.iloc[-1]) <= 0.30

    closes_above_level = (seq["Close"] > resistance).sum()
    latest_close_above = seq.iloc[-1]["Close"] > resistance

    zone_lo = resistance - 0.05 * setup["atr5"]
    zone_hi = resistance + 0.05 * setup["atr5"]
    
    touched_retest = (
        ((seq["Low"] <= zone_hi) & (seq["High"] >= zone_lo) & (seq["Close"] > resistance)).any()
    )

    recent = df5.loc[df5.index <= block_close - pd.Timedelta(minutes=15)].copy()
    if len(recent) < 20:
        return None

    seq_vol = seq["Volume"].sum()
    rolling_3_avg = recent["Volume"].rolling(3).sum().dropna().tail(20).mean()
    breakout_bar_vol_avg20 = recent["Volume"].tail(20).mean()
    breakout_bar_vol_ok = seq.iloc[-1]["Volume"] >= 1.5 * breakout_bar_vol_avg20
    vol_ok = rolling_3_avg and rolling_3_avg > 0 and seq_vol >= 2.0 * rolling_3_avg

    extension_guard = seq.iloc[-1]["Close"] <= resistance + 0.35 * setup["atr5"]

    checks = {
        "vwap_support": closes_above_vwap,
        "bullish_persistence": green_count >= 2,
        "wick_rejection": wick_ok,
        "breakout_candle_wick": breakout_candle_wick_ok,
        "latest_close_above": latest_close_above,
        "persistence_above_level": closes_above_level >= 2,
        "retest_hold": touched_retest,
        "volume_surge": vol_ok,
        "breakout_bar_volume": breakout_bar_vol_ok,
        "not_overextended": extension_guard,
    }

    if not all(checks.values()):
        return None

    return {
        "green_count": int(green_count),
        "closes_above_level": int(closes_above_level),
        "seq_vol": float(seq_vol),
        "latest_5m_close": float(seq.iloc[-1]["Close"]),
        "latest_5m_low": float(seq.iloc[-1]["Low"]),
    }

def compute_score(setup: dict) -> int:
    score = 75
    if setup["rsi"] > 65:
        score += 5
    if setup["vol_ratio"] > 3.0:
        score += 5
    if setup["close_pos"] > 0.80:
        score += 5
    return min(score, 90)

def seconds_to_next_15m(now):
    next_minute = ((now.minute // 15) + 1) * 15
    next_hour = now.hour
    if next_minute == 60:
        next_minute = 0
        next_hour += 1
    next_run = now.replace(hour=next_hour % 24, minute=next_minute, second=5, microsecond=0)
    if next_hour >= 24:
        next_run = next_run + pd.Timedelta(days=1)
    return max(0, (next_run - now).total_seconds())

def start(run_once=False):
    init_db()

    from surveillance import force_refresh_blacklist
    force_refresh_blacklist()

    while True:
        ist_now      = datetime.now(IST)
        current_time = ist_now.time()
        weekday      = ist_now.weekday()
        
        from market_utils import is_within_custom_hours
        is_active_window = run_once or is_within_custom_hours(dt_time(9, 45), dt_time(15, 35), ist_now)
        
        if not is_active_window:
            logger.info("📅 Outside market hours. Scanner pausing until next market session...")
            try:
                from database import upsert_scanner_health
                upsert_scanner_health("INTRADAY", "IDLE", last_success=datetime.now(IST).isoformat())
            except Exception:
                pass
            sleep_time = seconds_to_next_15m(datetime.now(IST))
            time.sleep(sleep_time)
            continue
            
        scan_start = datetime.now(IST)
        logger.info("=" * 80)
        logger.info(f"⚡ INTRADAY SCAN START (15m + 5m Multi-TF) | {scan_start.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

        try:
            watchlist = get_watchlist()
            if watchlist is None or watchlist.empty:
                raise ValueError("Watchlist is missing or empty. Cannot run scan.")
            
            # ── DUAL BATCH DOWNLOAD ──────────────────────
            # [BUG FIX 2026-06-24] Previously used ThreadPoolExecutor(max_workers=2) to
            # fetch 15m and 5m data "in parallel". However, price_cache.py uses a single
            # global _fetch_lock that serializes ALL API fetches. So the two threads were
            # never actually running in parallel — they were queueing behind the same lock,
            # adding thread overhead with zero benefit. Changed to sequential fetches.
            data_15m_raw = fetch_watchlist_data(watchlist, "10d", "15m", requester="intraday_15m")
            data_5m_raw  = fetch_watchlist_data(watchlist, "5d",  "5m",  requester="intraday_5m")

            # Handle rate limit / partial data gracefully
            if not data_15m_raw or not data_5m_raw:
                missing = []
                if not data_15m_raw:
                    missing.append("15m")
                if not data_5m_raw:
                    missing.append("5m")
                logger.warning(f"⚠️ YFinance returned 0 data for {','.join(missing)} (likely rate-limited). Continuing with partial data...")
                try:
                    from database import upsert_scanner_health
                    upsert_scanner_health("INTRADAY", "DEGRADED", error_msg=f"Rate-limited: {','.join(missing)} returned 0 symbols")
                except Exception:
                    pass
                # Ensure we have at least empty dicts instead of None
                if not data_15m_raw:
                    data_15m_raw = {}
                if not data_5m_raw:
                    data_5m_raw = {}
            else:
                logger.info(f"✅ Data downloaded | 15m: {len(data_15m_raw)} | 5m: {len(data_5m_raw)}")
                # We will update OK at the end of the loop, no need to do it here
                pass

            
            stale_count = 0
            
            # ── PRECOMPUTE INDICATORS ONCE PER DATASET ──
            for sym, df in data_15m_raw.items():
                try:
                    if getattr(df, 'attrs', {}).get('is_stale') == True:
                        stale_count += 1
                        continue
                    norm_df = normalize_index(df)
                    if not norm_df.empty:
                        ind_df = apply_indicators(norm_df, timeframe="15m")
                        if ind_df is not None and not ind_df.empty:
                            data_15m[sym] = ind_df
                except Exception:
                    pass
                    
            for sym, df in data_5m_raw.items():
                try:
                    if getattr(df, 'attrs', {}).get('is_stale') == True:
                        # Only count stale once per symbol (checked in 15m)
                        continue
                    norm_df = normalize_index(df)
                    if not norm_df.empty:
                        ind_df = apply_indicators(norm_df, timeframe="5m")
                        if ind_df is not None and not ind_df.empty:
                            data_5m[sym] = ind_df
                except Exception:
                    pass
            
            total_alerts = 0
            
            # Collect prices to update open positions with fresh data
            position_prices = {}

            for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):
                symbol = "UNKNOWN"
                try:
                    symbol   = row["Stock"]
                    category = row["Category"]
                    
                    from surveillance import get_live_blacklist
                    if symbol in get_live_blacklist():
                        continue

                    if symbol not in data_15m or symbol not in data_5m:
                        continue

                    df15 = data_15m[symbol].copy()
                    df5 = data_5m[symbol].copy()

                    setup = evaluate_15m_setup(df15, ist_now)
                    if not setup:
                        continue

                    trigger = evaluate_5m_trigger(df5, setup, ist_now)
                    if not trigger:
                        continue

                    score = compute_score(setup)
                    if trigger.get("closes_above_level", 0) == 3:
                        score = min(90, score + 3)

                    signal_str = "15M_5M_CONFIRMED_BREAKOUT"
                    today_str  = ist_now.strftime("%Y-%m-%d")
                    dedup_key  = f"{category}|{signal_str}|{symbol}|{today_str}|INTRADAY"

                    from database import check_recent_alert
                    if check_recent_alert(symbol, "INTRADAY", dedup_key, 90):
                        continue

                    candle_close = trigger["latest_5m_close"]
                    
                    # Capture price for batch position update (5 min refresh for open positions)
                    if symbol and candle_close > 0:
                        position_prices[symbol] = {"price": candle_close, "score": None}
                    
                    atr5 = setup["atr5"]
                    suggested_stop = trigger["latest_5m_low"] - (0.2 * atr5)
                    if suggested_stop >= candle_close:
                        suggested_stop = candle_close - (0.5 * atr5)
                        
                    calc_target = candle_close + ((candle_close - suggested_stop) * 2)

                    context = {
                        "technicals": {
                            "volume_ratio":     round(setup["vol_ratio"], 2),
                            "rsi":              round(setup["rsi"], 1)
                        },
                        "execution": {
                            "breakout_level":   round(setup["breakout_resistance"], 2),
                            "atr":              round(atr5, 2),
                            "stop_basis":       "min(candle_low, prev_low) - 0.2*ATR"
                        }
                    }

                    if is_active_window:
                        saved, cap_alloc, shares = save_alert_if_new(
                            symbol,
                            dedup_key,
                            ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                            scanner="INTRADAY",
                            category=category,
                            entry_price=round(candle_close, 2),
                            signals=signal_str,
                            score=score,
                            rsi=round(setup["rsi"], 1),
                            volume_ratio=round(setup["vol_ratio"], 2),
                            stop_loss=round(suggested_stop, 2),
                            target_price=round(calc_target, 2),
                            context=context,
                            model_version="multi_tf_intraday_v2",
                            bayesian_regime="INDEPENDENT",
                            bayesian_weights={},
                        )
                    else:
                        logger.info(f"🧪 [TEST MODE] Alert generated for {symbol} - {signal_str}")
                        saved, cap_alloc, shares = True, 0.0, 0
                        
                    if saved:
                        total_alerts += 1

                except Exception as e:
                    logger.exception(f"❌ UNHANDLED ERROR processing {symbol}")
                    try:
                        upsert_fetch_error('yfinance', 'INTRADAY', symbol, '15m/5m', 'processing_error', str(e))
                    except Exception:
                        pass
                    continue
            
            # Batch update open positions with fresh prices from this scan
            if position_prices:
                try:
                    from database import update_position_real_time_prices
                    updated_count = update_position_real_time_prices(position_prices)
                    logger.info(f"📊 Updated {updated_count} position prices from INTRADAY scan data")
                except Exception as e:
                    logger.warning(f"⚠️  Failed to update position prices: {e}")
            
            if total_alerts == 0:
                logger.info("📭 No INTRADAY alerts this cycle")
            
            duration = (datetime.now(IST) - scan_start).total_seconds()
            logger.info("=" * 80)
            logger.info(f"✅ INTRADAY SCAN COMPLETE | {round(duration, 2)}s | Alerts={total_alerts}/{len(watchlist)}")
            
            from database import upsert_scanner_health, verify_alerts_saved_today
            if total_alerts > 0 and is_active_window:
                if not verify_alerts_saved_today("INTRADAY", total_alerts):
                    logger.critical(f"🚨 CRITICAL ERROR: Intraday generated {total_alerts} alerts but save failed!")
                    upsert_scanner_health(
                        scanner_name="INTRADAY",
                        status="DOWN",
                        error_msg=f"CRITICAL: {total_alerts} alerts failed to save to database"
                    )
                    raise RuntimeError("Alert save verification failed - database connectivity issue")
            
            status = "OK" if is_active_window else "IDLE"
            error_msg = None
            
            stale_pct = stale_count / len(watchlist) if len(watchlist) > 0 else 0
            if stale_pct > 0.1:
                status = "DEGRADED"
                error_msg = f"Stale Data: {stale_count}/{len(watchlist)} symbols"
                
            if len(data_15m_raw) < len(watchlist):
                status = "DEGRADED"
                error_msg = f"Partial Fetch: {len(data_15m_raw)}/{len(watchlist)} symbols"

            try:
                upsert_scanner_health(
                    scanner_name="INTRADAY",
                    status=status,
                    last_success=datetime.now(IST).isoformat(),
                    today_alerts=total_alerts if is_active_window else 0,
                    error_msg=error_msg
                )
            except Exception:
                logger.exception("❌ Failed to update scanner health for INTRADAY")

            if run_once:
                logger.info("🧪 TEST RUN COMPLETE. Exiting loop.")
                break
            
            # Loop sleeps until exactly 5 seconds past the next 15-minute clock boundary.
            sleep_time  = seconds_to_next_15m(datetime.now(IST))
            time.sleep(sleep_time)

        except Exception as e:
            if isinstance(e, RuntimeError) and "interpreter shutdown" in str(e).lower():
                logger.info("Interpreter shutting down, ignoring INTRADAY scan future error.")
                break
            logger.exception("❌ CRITICAL SCAN ERROR")
            try:
                from database import upsert_scanner_health
                upsert_scanner_health("INTRADAY", "DOWN", error_msg=str(e))
            except Exception:
                pass
            
            if run_once:
                break
                
            time.sleep(seconds_to_next_15m(datetime.now(IST)))
