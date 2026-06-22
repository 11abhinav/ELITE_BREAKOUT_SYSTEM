# =====================================================================================
# app/live_scanner.py (ULTIMATE EDITION)
# PURE 1H BREAKOUT SCANNER
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
from concurrent.futures import ThreadPoolExecutor

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


IST        = ZoneInfo("Asia/Kolkata")

TIMEFRAME  = "1h"

ENABLE_REGIME_GATE_1H = False
def start(run_once=False):
    init_db()

    from surveillance import force_refresh_blacklist
    force_refresh_blacklist()

    while True:
        ist_now      = datetime.now(IST)
        current_time = ist_now.time()
        weekday      = ist_now.weekday()

        market_open = dt_time(10, 17) <= current_time <= dt_time(15, 35)

        is_active_window = run_once or (weekday < 5 and market_open)
        if not is_active_window:
            logger.info("⏰ Outside 1H window - running in TEST mode (no db saves)")

        scan_start         = datetime.now(IST)
        total_alerts       = 0

        logger.info("=" * 80)
        logger.info(f"⚡ 1H SCAN START | {scan_start.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 80)

        sleep_time = 300  
        try:
            watchlist = get_watchlist()
            if watchlist is None or watchlist.empty:
                raise ValueError("Watchlist is missing or empty. Cannot run scan.")

            # ── BATCH DOWNLOAD: 1H ONLY ────────────────────────────
            all_ticker_data = {}
            
            with ThreadPoolExecutor(max_workers=1) as pool:
                future_1h = pool.submit(fetch_watchlist_data, watchlist, "60d", "1h")
                all_ticker_data = future_1h.result()

            if not all_ticker_data:
                logger.error("❌ YFinance returned 0 data. API might be down or rate-limited. Aborting 1H scan.")
                try:
                    from database import upsert_scanner_health
                    upsert_scanner_health("1H Breakout", "DOWN", error_msg="CRITICAL: YFinance returned 0 data. Rate limited.")
                except Exception:
                    pass
                return

            rejection_counts = {k: 0 for k in [
                "no_data", "missing_col", "forming_candle_stripped", "insufficient_bars", 
                "indicator_fail", "penny_stock", "trend_fail", "momentum_fail", "volume_fail", "candle_fail",
                "no_breakout", "extended_breakout", "exhaustion_bar", "stale_data", "duplicate"
            ]}

            nifty_intraday_down = False
            if ENABLE_REGIME_GATE_1H:
                try:
                    from price_fetcher import _fetch_history_with_retry
                    nifty_1d = _fetch_history_with_retry("^NSEI", period="5d", interval="15m")
                    if nifty_1d is not None and not nifty_1d.empty:
                        # Get today's open
                        today_str = datetime.now(IST).strftime('%Y-%m-%d')
                        today_data = nifty_1d.loc[today_str] if today_str in nifty_1d.index else nifty_1d
                        if not today_data.empty:
                            nifty_open = float(today_data['Open'].iloc[0])
                            nifty_current = float(today_data['Close'].iloc[-1])
                            intraday_drop = ((nifty_open - nifty_current) / nifty_open) * 100 if nifty_open > 0 else 0.0
                            if intraday_drop > 1.5:
                                logger.warning(f"🚨 REGIME GATE ACTIVE: Nifty is down {intraday_drop:.2f}% today. Suppressing breakouts.")
                                nifty_intraday_down = True
                except Exception as e:
                    pass
            else:
                logger.info("ℹ️ REGIME GATE is configured to OFF. Bypassing Nifty drop checks.")

            if nifty_intraday_down:
                time.sleep(300)
                continue

            for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):
                symbol = "UNKNOWN"
                try:
                    symbol   = row["Stock"]
                    category = row["Category"]

                    from surveillance import get_live_blacklist
                    if symbol in get_live_blacklist():
                        continue

                    if symbol not in all_ticker_data:
                        rejection_counts["no_data"] += 1
                        continue

                    ticker = all_ticker_data[symbol].copy()

                    if ticker.empty:
                        rejection_counts["no_data"] += 1
                        continue

                    if isinstance(ticker.columns, pd.MultiIndex):
                        ticker.columns = ticker.columns.get_level_values(0)

                    ticker = ticker.loc[:, ~ticker.columns.duplicated()]

                    required_cols = ["Open", "High", "Low", "Close", "Volume"]
                    missing_col   = False

                    for col_name in required_cols:
                        if col_name not in ticker.columns:
                            missing_col = True
                            break
                        if isinstance(ticker[col_name], pd.DataFrame):
                            ticker[col_name] = ticker[col_name].iloc[:, 0]
                        ticker[col_name] = pd.Series(ticker[col_name]).astype(float)

                    if missing_col:
                        rejection_counts["missing_col"] += 1
                        continue

                    ticker = ticker.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

                    if ticker.empty:
                        rejection_counts["no_data"] += 1
                        continue

                    ticker = strip_forming_candle(ticker, 60, datetime.now(IST))
                    if ticker is None or ticker.empty:
                        rejection_counts["forming_candle_stripped"] += 1
                        continue

                    if len(ticker) < 100:
                        rejection_counts["insufficient_bars"] += 1
                        continue

                    ticker = apply_indicators(ticker, timeframe="1h")

                    if ticker is None or ticker.empty:
                        rejection_counts["indicator_fail"] += 1
                        continue

                    latest = ticker.iloc[-1]

                    _stale_col = next((c for c in ["Datetime", "Date", "index"] if c in ticker.columns), None)
                    if _stale_col:
                        try:
                            _last_ts = pd.to_datetime(latest[_stale_col])
                            if _last_ts.tzinfo is not None:
                                _last_ts = _last_ts.tz_convert("Asia/Kolkata")
                            if _last_ts.date() != ist_now.date():
                                rejection_counts["stale_data"] += 1
                                continue
                        except Exception:
                            pass 

                    if "RSI" not in ticker.columns or pd.isna(latest["RSI"]):
                        continue

                    latest_volume = float(latest["Volume"])
                    vol_series    = ticker["Volume"].iloc[-21:-1]
                    avg_volume    = float(vol_series.mean())

                    if avg_volume <= 0:
                        continue

                    candle_high  = float(latest["High"])
                    candle_low   = float(latest["Low"])
                    candle_open  = float(latest["Open"])
                    candle_close = float(latest["Close"])
                    candle_range = candle_high - candle_low
                    candle_body  = abs(candle_close - candle_open)
                    upper_wick   = candle_high - candle_close

                    if candle_range <= 0:
                        continue

                    body_ratio     = candle_body / candle_range
                    close_position = (candle_close - candle_low) / candle_range
                    wick_ratio     = upper_wick / candle_range
                    rsi_val        = float(latest["RSI"])

                    if candle_close < MIN_STOCK_PRICE:
                        rejection_counts["penny_stock"] += 1
                        continue

                    volume_ratio = latest_volume / avg_volume

                    # ── STRICT 1H CONTINUATION RULES ──────────────────────────────────────
                    e20 = float(latest.get("EMA20", 0) or 0)
                    s50 = float(latest.get("SMA50", 0) or 0)
                    s200 = float(latest.get("SMA200", 0) or 0)
                    
                    trend_ok = candle_close > e20 and candle_close > s50 and s50 > s200
                    if not trend_ok:
                        rejection_counts["trend_fail"] += 1
                        continue

                    adx = float(latest.get("ADX", 0) or 0)
                    momentum_ok = 55 <= rsi_val <= 78 and adx >= 20
                    if not momentum_ok:
                        rejection_counts["momentum_fail"] += 1
                        continue

                    volume_ok = volume_ratio >= 1.5
                    if not volume_ok:
                        rejection_counts["volume_fail"] += 1
                        continue

                    candle_ok = body_ratio >= 0.50 and close_position >= 0.60 and wick_ratio < 0.30
                    if not candle_ok:
                        rejection_counts["candle_fail"] += 1
                        continue

                    breakout_level = float(latest.get("PRIOR_20D_HIGH", 0) or 0)
                    if breakout_level <= 0:
                        continue
                        
                    breakout_ok = candle_close > breakout_level
                    if not breakout_ok:
                        rejection_counts["no_breakout"] += 1
                        continue
                        
                    atr = float(latest.get("ATR", 0) or 0)
                    if atr <= 0:
                        continue
                        
                    extension_ok = candle_close <= breakout_level + 0.8 * atr
                    if not extension_ok:
                        rejection_counts["extended_breakout"] += 1
                        continue
                        
                    prev_close = float(ticker["Close"].iloc[-2]) if len(ticker) >= 2 else candle_close
                    single_bar_pct = abs(candle_close - prev_close) / prev_close * 100
                    not_exhausted = single_bar_pct <= 5.5
                    if not not_exhausted:
                        rejection_counts["exhaustion_bar"] += 1
                        continue

                    # Alert execution details
                    score = min(100, int(80 + (volume_ratio * 5)))
                    model_version = "pure_1h_breakout_v1"

                    signal_str = "1H_QUALITY_BREAKOUT"
                    today_str  = datetime.now(IST).strftime("%Y-%m-%d")
                    dedup_key  = f"{category}|{signal_str}|{symbol}|{today_str}|1H"

                    from database import check_recent_alert
                    if check_recent_alert(symbol, "LIVE", dedup_key, 240):
                        rejection_counts["duplicate"] += 1
                        continue

                    # ── DETERMINISTIC STRUCTURAL SL & TARGET ──
                    suggested_stop = min(candle_low, e20) - (0.2 * atr)
                    if suggested_stop >= candle_close:
                        suggested_stop = candle_close - (0.5 * atr)
                        
                    calc_target = candle_close + ((candle_close - suggested_stop) * 2)

                    context = {
                        "technicals": {
                            "volume_ratio":     round(volume_ratio, 2),
                            "rsi":              round(rsi_val, 1)
                        },
                        "execution": {
                            "breakout_level":   round(breakout_level, 2),
                            "atr":              round(atr, 2),
                            "stop_basis":       "min(candle_low, e20) - 0.2*ATR"
                        }
                    }

                    if is_active_window:
                        saved, cap_alloc, shares = save_alert_if_new(
                            symbol,
                            dedup_key,
                            datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                            scanner="1H",
                            category=category,
                            entry_price=round(candle_close, 2),
                            signals=signal_str,
                            score=score,
                            rsi=round(float(latest["RSI"]), 1),
                            volume_ratio=round(volume_ratio, 2),
                            stop_loss=round(suggested_stop, 2),
                            target_price=round(calc_target, 2),
                            context=context,
                            model_version=model_version,
                            bayesian_regime="INDEPENDENT",
                            bayesian_weights={},
                        )
                    else:
                        logger.info(f"🧪 [TEST MODE] Alert generated for {symbol} - {signal_str}")
                        saved, cap_alloc, shares = True, 0.0, 0
                    if not saved:
                        rejection_counts["duplicate"] += 1
                        continue

                    total_alerts += 1

                except Exception as e:
                    logger.exception(f"❌ UNHANDLED ERROR processing {symbol}")
                    rejection_counts["indicator_fail"] = rejection_counts.get("indicator_fail", 0) + 1
                    continue
            
            elapsed    = (datetime.now(IST) - scan_start).total_seconds()
            sleep_time = max(0, 300 - elapsed)

            rejection_summary = " | ".join(f"{k}={v}" for k, v in rejection_counts.items() if v > 0)
            
            if total_alerts == 0:
                logger.info("📭 No 1H alerts this cycle")
            logger.info("=" * 80)
            logger.info(f"✅ 1H SCAN COMPLETE | {elapsed:.2f}s | Alerts={total_alerts}/{len(watchlist)}")
            
            # ✅ CRITICAL: Verify alerts were actually saved to database (2026-06-17)
            from database import upsert_scanner_health, verify_alerts_saved_today
            if total_alerts > 0 and is_active_window:
                if not verify_alerts_saved_today("1H", total_alerts):
                    logger.critical(f"🚨 CRITICAL ERROR: 1H scanner generated {total_alerts} alerts but save failed!")
                    upsert_scanner_health(
                        scanner_name="1H",
                        status="DOWN",
                        error_msg=f"CRITICAL: {total_alerts} alerts failed to save to database"
                    )
                    raise RuntimeError("Alert save verification failed - database connectivity issue")
            
            try:
                upsert_scanner_health(
                    scanner_name="1H",
                    status="OK" if is_active_window else "IDLE",
                    last_success=datetime.now(IST).isoformat(),
                    today_alerts=total_alerts if is_active_window else 0
                )
            except Exception:
                logger.exception("❌ Failed to update scanner health for 1H")

            if rejection_summary:
                logger.info(f"   Rejections: {rejection_summary}")

        except Exception as e:
            if isinstance(e, RuntimeError) and "interpreter shutdown" in str(e).lower():
                logger.info("Interpreter shutting down, ignoring 1H scan future error.")
                break
            logger.exception("❌ CRITICAL 1H SCAN ERROR — will retry next cycle")
            try:
                from database import upsert_scanner_health
                upsert_scanner_health("1H", "DOWN", error_msg=str(e))
            except Exception:
                pass
            elapsed    = (datetime.now(IST) - scan_start).total_seconds()
            sleep_time = max(0, 300 - elapsed)

        time.sleep(sleep_time)
