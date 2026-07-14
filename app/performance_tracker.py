# =====================================================================================
# app/performance_tracker.py
# Builds performance_data.json from the Postgres alerts table + live yfinance prices.
# Called every 5 minutes from main.py.
#
# SL / TARGET DETECTION LOGIC
# ────────────────────────────
# Both SL and Target are detected using intraday (1h) bars filtered to >= alert_time.
# This means:
#   • Any low printed BEFORE the alert on the same day is IGNORED for SL.
#   • Any high printed BEFORE the alert on the same day is IGNORED for Target.
#
# Priority:
#   1. SL hit first  → status = LOSS  (locked at stop_loss price)
#   2. Target hit first → status = WIN  (locked at target_price)
#   3. Neither hit   → mark-to-market vs current close
#
# To determine which hit first, we compare the timestamps of the first SL-breach
# candle and the first Target-breach candle.
# =====================================================================================

import os
import json
import logging
import pandas as pd
from typing import Union, Optional, Tuple
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
from price_cache import fetch_watchlist_data


from config import MIN_STOCK_PRICE
from database import get_all_alerts, update_alert_outcome, upsert_scanner_health, save_system_state

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

try:
    from config import DATA_DIR
except ImportError:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    DATA_DIR = os.path.join(BASE_DIR, "data")

PERF_JSON_PATH = os.path.join(DATA_DIR, "performance_data.json")

# Time-based auto-exit is disabled; holdings are kept open until SL or Target is hit.
# HOLD_DAYS = 5


# =====================================================================================
# HELPERS
# =====================================================================================

from data_provider import get_fetcher

def _parse_dedup_key(breakout_type: str) -> tuple[str, str, str]:
    parts = breakout_type.split("|")
    if len(parts) >= 4:
        return parts[0].strip(), parts[1].strip(), parts[3].strip()
    if len(parts) == 3:
        return parts[0].strip(), parts[1].strip(), "UNKNOWN"
    return "Unknown", breakout_type, "UNKNOWN"


def _fetch_current_prices(symbols: list[str]) -> dict[str, float]:
    """Batch-fetch latest prices using Fyers with Yahoo fallback."""
    if not symbols:
        return {}
    
    from live_prices import get_live_prices
    prices = get_live_prices(symbols)
    
    try:
        from data_fetch_status import mark_success
        mark_success('performance_tracker')
    except Exception:
        pass
        
    return prices


def _fetch_post_alert_bars(symbol: str, alert_time_val: Union[str, datetime], prefetched_hist: pd.DataFrame = None) -> Optional[pd.DataFrame]:
    """
    Fetch 5m bars for *symbol* from the alert date to today using DataFetcher.
    Filters out any bars that occurred before the alert_time.
    """
    try:
        if isinstance(alert_time_val, datetime):
            # If it's timezone aware, convert to IST.
            if alert_time_val.tzinfo is not None:
                alert_dt_ist = alert_time_val.astimezone(IST)
            else:
                # Naive datetime from DB — treat as IST (our DB session is SET TIME ZONE 'Asia/Kolkata')
                alert_dt_ist = alert_time_val.replace(tzinfo=IST)
        else:
            alert_time_str = str(alert_time_val).replace("Z", "+00:00")
            alert_dt_naive = datetime.fromisoformat(alert_time_str)
            if alert_dt_naive.tzinfo is not None:
                alert_dt_ist = alert_dt_naive.astimezone(IST)
            else:
                alert_dt_ist = alert_dt_naive.replace(tzinfo=IST)
                
        alert_date = alert_dt_ist.date()

        # If the alert was recorded after market close (e.g. delayed run or EOD),
        # resetting the filter to market open ensures we evaluate that day's candles
        # instead of dropping them all and never triggering the SL.
        if alert_dt_ist.time() >= time(15, 30):
            alert_dt_ist = alert_dt_ist.replace(hour=9, minute=15, second=0, microsecond=0)

        # Guard: if alert is from today and market hasn't opened yet (before 09:15 IST),
        # no 5m bars exist — return None immediately to avoid yfinance "delisted" noise.
        now_ist = datetime.now(IST)
        market_open_ist = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        if alert_date == now_ist.date() and now_ist < market_open_ist:
            logger.debug(f"⏳ {symbol} | Alert is from today but market not open yet — skipping bar fetch")
            return None

        # Determine period since alert
        days_since = (now_ist.date() - alert_date).days
        period_days = max(days_since + 2, 5)
        period_str = f"{period_days}d"

        # Yahoo Finance limits: 5m data is only available for the last 60 days.
        # We use 5m for maximum precision on SL/Target tracking if possible.
        if period_days <= 59:
            interval = "5m"
        elif period_days <= 720:
            interval = "1h"
        else:
            interval = "1d"

        if prefetched_hist is not None:
            if isinstance(prefetched_hist, pd.DataFrame) and not prefetched_hist.empty:
                hist = prefetched_hist.copy()
            else:
                return None
        else:
            # Route fallback through global cache
            df_request = pd.DataFrame({"Stock": [symbol]})
            raw_dict = fetch_watchlist_data(df_request, interval=interval, period=period_str, requester="performance_tracker")
            hist = raw_dict.get(symbol)

        if hist is None or hist.empty:
            return None

        if not {"High", "Low", "Close"}.issubset(hist.columns):
            return None

        # Find datetime column
        date_col = next((c for c in ["Datetime", "Date", "index"] if c in hist.columns), None)
        if date_col is None:
            return None

        hist[date_col] = pd.to_datetime(hist[date_col])
        hist = hist.set_index(date_col)

        # Localise index to IST
        idx = hist.index
        if idx.tzinfo is None:
            idx = idx.tz_localize("Asia/Kolkata")
        else:
            idx = idx.tz_convert("Asia/Kolkata")
        hist.index = idx

        # Drop all candles that opened before the alert timestamp
        hist = hist[hist.index >= alert_dt_ist].copy()

        return hist if not hist.empty else None

    except Exception as e:
        logger.exception(f"⚠️ Could not fetch bars for {symbol} (alert={alert_time_val}): {e}")
        try:
            from data_fetch_status import mark_failure
            mark_failure('performance_tracker', f"{symbol} bars fetch failed: {str(e)}")
        except Exception:
            pass
        return None



import json

def process_trade_history(t: dict, hist: pd.DataFrame, cur_p: float):
    """
    State Machine Evaluator for Partial Exits.
    Walks forward through historical ticks and current price to execute trailing SLs and partial limits.
    Mutates 't' in-memory and triggers database writes via database.py helpers.
    """
    from database import update_partial_exit, update_alert_outcome
    
    t1 = t.get("target_1") or t.get("target_price")
    t2 = t.get("target_2") or (t1 * 1.05 if t1 else None)
    t3 = t.get("target_3") or (t1 * 1.10 if t1 else None)
    
    if not t1 or not t2 or not t3: return  # Sanity check
    
    shares_bought = t.get("shares_bought", 0)
    if shares_bought == 0: return

    # Build sequence of ticks to evaluate (history + live)
    ticks = []
    if hist is not None and not hist.empty:
        for ts, row in hist.iterrows():
            ticks.append((ts.strftime("%Y-%m-%d %H:%M:%S"), float(row["Open"]), float(row["Low"]), float(row["High"])))
    if cur_p:
        now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        ticks.append((now_str, cur_p, cur_p, cur_p))
        
    for ts_str, open_p, low, high in ticks:
        if t["status"] in ("WIN", "LOSS", "CLOSED", "REJECTED"):
            break
            
        sl = t["stop_loss"]
        status = t["status"]
        rem_shares = t.get("remaining_shares")
        if rem_shares is None: rem_shares = shares_bought
        
        # 1. Evaluate Stop Loss First (Protective)
        if low <= sl:
            exit_p = open_p if open_p < sl else sl
            pnl_rs_event = rem_shares * (exit_p - t["entry_price"])
            event = {"type": "SL_HIT", "price": exit_p, "shares": rem_shares, "pnl": round(pnl_rs_event, 2), "time": ts_str}
            
            final_status = "WIN" if "PARTIAL" in status else "LOSS"
            
            eh = t.get("exit_history")
            hist_list = eh if isinstance(eh, list) else json.loads(eh or "[]")
            hist_list.append(event)
            total_pnl_rs = sum(e["pnl"] for e in hist_list)
            cap = t.get("capital_allocated") or 0.0
            total_pnl_pct = round((total_pnl_rs / cap) * 100, 2) if cap else 0.0
            
            update_partial_exit(t["id"], final_status, sl, rem_shares, 0, pnl_rs_event, event)
            update_alert_outcome(t["id"], final_status, exit_p, total_pnl_pct, pnl_rs=total_pnl_rs, closed_at=ts_str, exit_signal="STOP_LOSS")
            
            t["status"] = final_status
            t["remaining_shares"] = 0
            t["exit_history"] = json.dumps(hist_list)
            t["pnl_pct"] = total_pnl_pct
            t["pnl_rs"] = total_pnl_rs
            continue
            
        # 2. Evaluate T1
        if status == "OPEN" and high >= t1:
            exit_p = open_p if open_p > t1 else t1
            if not t.get("target_1"):
                shares_to_sell = rem_shares
            else:
                shares_to_sell = int(shares_bought * 0.25)
                if shares_to_sell == 0: shares_to_sell = rem_shares
            
            pnl_rs_event = shares_to_sell * (exit_p - t["entry_price"])
            event = {"type": "T1_HIT", "price": exit_p, "shares": shares_to_sell, "pnl": round(pnl_rs_event, 2), "time": ts_str}
            
            new_rem = rem_shares - shares_to_sell
            new_sl = t["entry_price"]  # Raise to Breakeven
            new_status = "PARTIAL_WIN_1"
            
            update_partial_exit(t["id"], new_status, new_sl, shares_to_sell, new_rem, pnl_rs_event, event)
            
            t["status"] = new_status
            t["stop_loss"] = new_sl
            t["remaining_shares"] = new_rem
            eh = t.get("exit_history")
            hist_list = eh if isinstance(eh, list) else json.loads(eh or "[]")
            hist_list.append(event)
            t["exit_history"] = json.dumps(hist_list)
            
            if new_rem <= 0:
                t["status"] = "WIN"
                total_pnl_rs = sum(e["pnl"] for e in hist_list)
                cap = t.get("capital_allocated") or 0.0
                p_pct = round((total_pnl_rs / cap) * 100, 2) if cap else 0.0
                update_alert_outcome(t["id"], "WIN", exit_p, p_pct, pnl_rs=total_pnl_rs, closed_at=ts_str, exit_signal="TARGET_HIT")
            continue
            
        # 3. Evaluate T2
        if status == "PARTIAL_WIN_1" and high >= t2:
            exit_p = open_p if open_p > t2 else t2
            shares_to_sell = int(shares_bought * 0.35)
            if shares_to_sell > rem_shares: shares_to_sell = rem_shares
            if shares_to_sell == 0: shares_to_sell = rem_shares
            
            pnl_rs_event = shares_to_sell * (exit_p - t["entry_price"])
            event = {"type": "T2_HIT", "price": exit_p, "shares": shares_to_sell, "pnl": round(pnl_rs_event, 2), "time": ts_str}
            
            new_rem = rem_shares - shares_to_sell
            new_sl = t1  # Raise SL to T1
            new_status = "PARTIAL_WIN_2"
            
            update_partial_exit(t["id"], new_status, new_sl, shares_to_sell, new_rem, pnl_rs_event, event)
            
            t["status"] = new_status
            t["stop_loss"] = new_sl
            t["remaining_shares"] = new_rem
            eh = t.get("exit_history")
            hist_list = eh if isinstance(eh, list) else json.loads(eh or "[]")
            hist_list.append(event)
            t["exit_history"] = json.dumps(hist_list)
            
            if new_rem <= 0:
                t["status"] = "WIN"
                total_pnl_rs = sum(e["pnl"] for e in hist_list)
                cap = t.get("capital_allocated") or 0.0
                p_pct = round((total_pnl_rs / cap) * 100, 2) if cap else 0.0
                update_alert_outcome(t["id"], "WIN", exit_p, p_pct, pnl_rs=total_pnl_rs, closed_at=ts_str, exit_signal="TARGET_HIT")
            continue
            
        # 4. Evaluate T3
        if status == "PARTIAL_WIN_2" and high >= t3:
            exit_p = open_p if open_p > t3 else t3
            shares_to_sell = rem_shares
            
            pnl_rs_event = shares_to_sell * (exit_p - t["entry_price"])
            event = {"type": "T3_HIT", "price": exit_p, "shares": shares_to_sell, "pnl": round(pnl_rs_event, 2), "time": ts_str}
            
            update_partial_exit(t["id"], "WIN", t2, shares_to_sell, 0, pnl_rs_event, event)
            
            eh = t.get("exit_history")
            hist_list = eh if isinstance(eh, list) else json.loads(eh or "[]")
            hist_list.append(event)
            total_pnl_rs = sum(e["pnl"] for e in hist_list)
            cap = t.get("capital_allocated") or 0.0
            total_pnl_pct = round((total_pnl_rs / cap) * 100, 2) if cap else 0.0
            
            update_alert_outcome(t["id"], "WIN", exit_p, total_pnl_pct, pnl_rs=total_pnl_rs, closed_at=ts_str, exit_signal="TARGET_HIT")
            
            t["status"] = "WIN"
            t["remaining_shares"] = 0
            t["exit_history"] = json.dumps(hist_list)
            t["pnl_pct"] = total_pnl_pct
            t["pnl_rs"] = total_pnl_rs
            continue
            
    # Calculate MTM (Mark-to-Market) for floating shares
    if t["status"] in ("OPEN", "PARTIAL_WIN_1", "PARTIAL_WIN_2"):
        if cur_p:
            eh = t.get("exit_history")
            hist_list = eh if isinstance(eh, list) else json.loads(eh or "[]")
            realized_pnl = sum(e["pnl"] for e in hist_list)
            rem = t.get("remaining_shares")
            if rem is None: rem = shares_bought
            unrealized_pnl = rem * (cur_p - t["entry_price"])
            total_pnl = realized_pnl + unrealized_pnl
            
            cap = t.get("capital_allocated") or 0.0
            t["pnl_pct"] = round((total_pnl / cap) * 100, 2) if cap else 0.0
            t["pnl_rs"] = round(total_pnl, 2)


def _days_held(alert_date_str: str) -> int:
    try:
        return (datetime.now(IST).date() - date.fromisoformat(alert_date_str)).days
    except Exception:
        return 0


def _trade_status(
    pnl_pct: Optional[float],
    days: int,
    stopped_out: bool,
    target_hit: bool,
) -> str:
    if stopped_out:
        return "LOSS"
    if target_hit:
        return "WIN"
    # Keep positions open until SL or Target is hit (no time-based auto-exit)
    return "OPEN"


# =====================================================================================
# MAIN BUILD FUNCTION
# =====================================================================================

def build_performance_data(fast_mode=False, force_live_fetch=False):
    logger.info("=" * 70)
    logger.info("📊 PERFORMANCE TRACKER | Building performance data...")
    logger.info("=" * 70)

    try:
        raw_alerts = get_all_alerts()
    except Exception:
        logger.exception("❌ Could not load alerts from database")
        _write_empty()
        return

    if not raw_alerts:
        logger.warning("⚠️ No alerts in database yet.")
        _write_empty()
        return

    logger.info(f"📋 {len(raw_alerts)} total alerts in database")

    # ── 1. Build trade objects ───────────────────────────────────────────────────────
    trades = []
    for row in raw_alerts:
        symbol      = row["symbol"]
        alert_time  = row.get("alert_time") or ""
        alert_date  = row.get("alert_date") or (alert_time[:10] if alert_time else "")
        # Cast to float immediately — psycopg2 returns REAL/NUMERIC as decimal.Decimal
        # and mixing Decimal with float in arithmetic raises TypeError.
        def _f(v):
            return float(v) if v is not None else None

        entry_price = _f(row.get("entry_price"))

        cat_stored     = row.get("category")
        scanner_stored = row.get("scanner")
        sig_stored     = row.get("signals")

        category, signals, scanner = _parse_dedup_key(row["breakout_type"])
        if cat_stored:     category = cat_stored
        if scanner_stored: scanner  = scanner_stored
        if sig_stored:     signals  = sig_stored

        trades.append({
            "id":            row["id"],          # needed for write-back
            "symbol":        symbol,
            "scanner":       scanner,
            "category":      category,
            "signals":       signals,
            "entry_date":    alert_date,
            "alert_time":    alert_time,
            "entry_price":   entry_price,
            "stop_loss":     _f(row.get("stop_loss")),
            "initial_stop_loss": _f(row.get("initial_stop_loss")),
            "target_price":  _f(row.get("target_price")),
            "target_1":      _f(row.get("target_1")),
            "target_2":      _f(row.get("target_2")),
            "target_3":      _f(row.get("target_3")),
            "current_price": _f(row.get("current_price")),
            "exit_price":    _f(row.get("exit_price")),   # pre-filled if already closed
            "pnl_pct":       _f(row.get("pnl_pct")),      # pre-filled if already closed
            "stopped_out":   row.get("status") == "LOSS",
            "target_hit":    row.get("status") == "WIN",
            "days_held":     _days_held(alert_date),
            "status":        row.get("status") or "OPEN",
            "shares_bought": row.get("shares_bought", 0),
            "capital_allocated": _f(row.get("capital_allocated")),
            "pnl_rs":        _f(row.get("pnl_rs")),
            "score":         row.get("score"),
            "rsi":           _f(row.get("rsi")),
            "volume_ratio":  _f(row.get("volume_ratio")),
            "closed_at":     row.get("closed_at"),        # ISO timestamp when SL/Target locked
            "remaining_shares": row.get("remaining_shares"),
            "exit_history":  row.get("exit_history"),
            "context":       row.get("context"),          # Diagnostic filters and context
            "is_rejected":   row.get("is_rejected", False),
            "_db_closed":    row.get("status") in ("WIN", "LOSS"),  # internal flag
        })

    # ── 2. Fetch current prices ──────────────────────────────────────────────────────
    from market_utils import is_market_open
    is_open = is_market_open() or force_live_fetch
    
    unique_symbols = list({t["symbol"] for t in trades})
    if is_open:
        logger.info(f"📈 Fetching current prices for {len(unique_symbols)} symbols...")
        current_prices = _fetch_current_prices(unique_symbols)
    else:
        logger.info(f"⏸️ Market is closed. Skipping live quote fetch for {len(unique_symbols)} symbols.")
        current_prices = {}

    # ── 3. Per-trade SL + Target detection via post-alert intraday bars ─────────────
    if is_open and not fast_mode:
        logger.info("📉 Checking SL / Target levels via post-alert intraday bars...")
    else:
        logger.info("⏭️ Skipping SL/Target intraday bar checks (fast_mode or market closed)")

    # [OPTIMIZATION] Pre-fetch all required intraday histories in big batches to avoid individual API hits
    fetch_groups = {}
    now_ist = datetime.now(IST)
    market_open_ist = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    for t in trades:
        if t["_db_closed"] or t["entry_price"] is None or not t["stop_loss"] or not t["target_price"] or not t["alert_time"]:
            continue
            
        alert_time_val = t["alert_time"]
        if isinstance(alert_time_val, datetime):
            alert_dt_ist = alert_time_val.astimezone(IST) if alert_time_val.tzinfo else alert_time_val.replace(tzinfo=IST)
        else:
            alert_dt_naive = datetime.fromisoformat(str(alert_time_val).replace("Z", "+00:00"))
            alert_dt_ist = alert_dt_naive.astimezone(IST) if alert_dt_naive.tzinfo else alert_dt_naive.replace(tzinfo=IST)
            
        alert_date = alert_dt_ist.date()
        if alert_date == now_ist.date() and now_ist < market_open_ist:
            continue
            
        days_since = (now_ist.date() - alert_date).days
        period_days = max(days_since + 2, 5)
        period_str = f"{period_days}d"
        interval = "5m" if period_days <= 59 else ("1h" if period_days <= 720 else "1d")
        
        key = (interval, period_str)
        fetch_groups.setdefault(key, []).append(t["symbol"])

    prefetched_data = {}
    if is_open and not fast_mode and fetch_groups:
        for (interval, period_str), syms in fetch_groups.items():
            logger.info(f"📦 Pre-fetching batch history for {len(syms)} active trades ({interval}/{period_str}) to prevent API spam...")
            df_request = pd.DataFrame({"Stock": syms})
            batch_res = fetch_watchlist_data(df_request, interval=interval, period=period_str, requester="performance_tracker")
            if batch_res:
                prefetched_data.update(batch_res)

    for t in trades:
        sym        = t["symbol"]
        ep         = t["entry_price"]
        sl         = t["stop_loss"]
        tp         = t["target_price"]
        alert_time = t["alert_time"]
        cur_p = current_prices.get(sym)
        if cur_p:
            t["current_price"] = round(cur_p, 2)

        # ── Already closed in DB — no bar download needed ────────────────────────
        if t["_db_closed"]:
            # pnl_pct and exit_price already populated from DB above
            # Just refresh current_price for display; status stays locked
            logger.debug(f"⏭️  {sym} already closed ({t['status']}) — skipping bar fetch")
            continue

        # FIX: use `is None` (not falsy check) so ep=0.0 doesn't misfire.
        # When ep is None we cannot compute any P&L — mark status and move on.
        if ep is None:
            t["pnl_pct"] = None
            t["status"]  = _trade_status(None, t["days_held"], False, False)
            continue

        if sl and alert_time and (t.get("target_1") or t.get("target_price")):
            # ── V2 Multi-Stage Target & Trail Processing ─────────────────────────
            hist = None
            if is_open and not fast_mode:
                pre_hist = prefetched_data.get(sym) if sym in prefetched_data else None
                hist = _fetch_post_alert_bars(sym, alert_time, prefetched_hist=pre_hist)

            process_trade_history(t, hist, cur_p)

        elif sl and alert_time:
            # SL only (no target stored — legacy or partial row)
            hist = None
            if not fast_mode:
                hist = _fetch_post_alert_bars(sym, alert_time)
            if hist is not None and not hist.empty:
                lowest_low = float(hist["Low"].min())
                if lowest_low <= sl:
                    t["stopped_out"] = True
                    t["exit_price"]  = sl
                    t["pnl_pct"]     = round((sl - ep) / ep * 100, 2)
                    # Find the first candle that breached the Stop Loss
                    hit_row = hist[hist["Low"] <= sl]
                    hit_time = hit_row.index[0].strftime("%Y-%m-%d %H:%M:%S") if not hit_row.empty else None
                    t["pnl_rs"]      = t["shares_bought"] * (sl - ep) if t["shares_bought"] else 0.0
                    t["closed_at"]   = hit_time
                    update_alert_outcome(t["id"], "LOSS", sl, t["pnl_pct"], pnl_rs=t["pnl_rs"], closed_at=hit_time, exit_signal="STOP_LOSS")
                elif cur_p and cur_p <= sl:
                    t["stopped_out"] = True
                    t["exit_price"]  = sl
                    t["pnl_pct"]     = round((sl - ep) / ep * 100, 2)
                    t["pnl_rs"]      = t["shares_bought"] * (sl - ep) if t["shares_bought"] else 0.0
                    hit_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                    t["closed_at"]   = hit_time
                    logger.debug(f"🛑 {sym} SL HIT (LIVE) | entry={ep} sl={sl} pnl={t['pnl_pct']}%")
                    update_alert_outcome(t["id"], "LOSS", sl, t["pnl_pct"], pnl_rs=t["pnl_rs"], closed_at=hit_time, exit_signal="STOP_LOSS")
                elif cur_p:
                    t["pnl_pct"] = round((cur_p - ep) / ep * 100, 2)
            elif cur_p and cur_p <= sl:
                t["stopped_out"] = True
                t["exit_price"]  = sl
                t["pnl_pct"]     = round((sl - ep) / ep * 100, 2)
                t["pnl_rs"]      = t["shares_bought"] * (sl - ep) if t["shares_bought"] else 0.0
                hit_time = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
                t["closed_at"]   = hit_time
                logger.debug(f"🛑 {sym} SL HIT (LIVE) | entry={ep} sl={sl} pnl={t['pnl_pct']}%")
                update_alert_outcome(t["id"], "LOSS", sl, t["pnl_pct"], pnl_rs=t["pnl_rs"], closed_at=hit_time, exit_signal="STOP_LOSS")
            elif cur_p:
                t["pnl_pct"] = round((cur_p - ep) / ep * 100, 2)

        elif cur_p:
            # Legacy alert — no SL/Target at all
            t["pnl_pct"] = round((cur_p - ep) / ep * 100, 2)

        t["status"] = _trade_status(
            t["pnl_pct"], t["days_held"], t["stopped_out"], t["target_hit"]
        )
        if t.get("is_rejected"):
            t["status"] = "REJECTED"

    # ── 4. Summary stats ────────────────────────────────────────────────────────────
    judged  = [t for t in trades if t["status"] in ("WIN", "LOSS", "NEUTRAL")]
    winners = [t for t in judged if t["status"] == "WIN"]
    losers  = [t for t in judged if t["status"] == "LOSS"]
    open_p  = [t for t in trades if t["status"] == "OPEN"]

    pnls    = [t["pnl_pct"] for t in judged if t["pnl_pct"] is not None]
    win_pnl = [t["pnl_pct"] for t in winners if t["pnl_pct"] is not None]
    los_pnl = [t["pnl_pct"] for t in losers  if t["pnl_pct"] is not None]

    n_judged  = len(judged)
    wr        = round(len(winners) / n_judged * 100, 1) if n_judged else 0
    avg_ret   = round(sum(pnls) / len(pnls), 2)          if pnls     else 0
    avg_win   = round(sum(win_pnl) / len(win_pnl), 2)    if win_pnl  else 0
    avg_loss  = round(sum(los_pnl) / len(los_pnl), 2)    if los_pnl  else 0
    best      = round(max(pnls), 2)                       if pnls     else 0
    worst     = round(min(pnls), 2)                       if pnls     else 0
    expectancy = round((wr / 100) * avg_win + (1 - wr / 100) * avg_loss, 2)

    # SL vs Target breakdown
    sl_closed     = [t for t in judged if t["stopped_out"]]
    target_closed = [t for t in judged if t["target_hit"]]

    summary = {
        "total_alerts":      len(trades),
        "judged":            n_judged,
        "winners":           len(winners),
        "losers":            len(losers),
        "open_positions":    len(open_p),
        "sl_triggered":      len(sl_closed),
        "target_hit":        len(target_closed),
        "win_rate":          wr,
        "avg_return_pct":    avg_ret,
        "avg_win_pct":       avg_win,
        "avg_loss_pct":      avg_loss,
        "best_trade_pct":    best,
        "worst_trade_pct":   worst,
        "expectancy":        expectancy,
    }

    # ── 5. Equity curve ─────────────────────────────────────────────────────────────
    sorted_judged = sorted(judged, key=lambda t: t["entry_date"])
    cum = 0.0
    equity_curve = []
    for i, t in enumerate(sorted_judged):
        if t["pnl_pct"] is not None:
            cum += t["pnl_pct"]
            equity_curve.append({
                "date":              t["entry_date"],
                "symbol":            t["symbol"],
                "trade_return":      t["pnl_pct"],
                "cumulative_return": round(cum / (i + 1), 2),
                "close_reason":      "SL" if t["stopped_out"] else ("TARGET" if t["target_hit"] else "TIME"),
            })

    # ── 6. Monthly breakdown ────────────────────────────────────────────────────────
    mmap: dict[str, dict] = {}
    for t in judged:
        ed = t.get("entry_date")
        if isinstance(ed, (datetime, date)):
            m = ed.isoformat()[:7]
        else:
            m = str(ed)[:7]
        if m not in mmap:
            mmap[m] = {"alerts": 0, "wins": 0, "pnls": []}
        mmap[m]["alerts"] += 1
        if t["status"] == "WIN":
            mmap[m]["wins"] += 1
        if t["pnl_pct"] is not None:
            mmap[m]["pnls"].append(t["pnl_pct"])

    monthly = [
        {
            "month":    m,
            "alerts":   v["alerts"],
            "wins":     v["wins"],
            "win_rate": round(v["wins"] / v["alerts"] * 100, 1) if v["alerts"] else 0,
            "avg_return": round(sum(v["pnls"]) / len(v["pnls"]), 2) if v["pnls"] else 0,
        }
        for m in sorted(mmap)
        for v in [mmap[m]]
    ]

    # ── 7. By scanner ────────────────────────────────────────────────────────────────
    all_scanners = {t["scanner"] for t in trades}
    by_scanner = {}
    for sc in all_scanners:
        sc_judged = [t for t in judged  if t["scanner"] == sc]
        sc_wins   = [t for t in sc_judged if t["status"] == "WIN"]
        sc_pnls   = [t["pnl_pct"] for t in sc_judged if t["pnl_pct"] is not None]
        by_scanner[sc] = {
            "total":      len([t for t in trades if t["scanner"] == sc]),
            "judged":     len(sc_judged),
            "win_rate":   round(len(sc_wins) / len(sc_judged) * 100, 1) if sc_judged else 0,
            "avg_return": round(sum(sc_pnls) / len(sc_pnls), 2) if sc_pnls else 0,
        }

    # ── 8. By category ───────────────────────────────────────────────────────────────
    all_cats = {t["category"] for t in trades}
    by_category = {}
    for cat in all_cats:
        cat_judged = [t for t in judged  if t["category"] == cat]
        cat_wins   = [t for t in cat_judged if t["status"] == "WIN"]
        cat_pnls   = [t["pnl_pct"] for t in cat_judged if t["pnl_pct"] is not None]
        by_category[cat] = {
            "total":      len([t for t in trades if t["category"] == cat]),
            "judged":     len(cat_judged),
            "win_rate":   round(len(cat_wins) / len(cat_judged) * 100, 1) if cat_judged else 0,
            "avg_return": round(sum(cat_pnls) / len(cat_pnls), 2) if cat_pnls else 0,
        }

    # Strip internal tracking flag before serialising
    for t in trades:
        t.pop("_db_closed", None)

    # ── 9. Write scanner health to Postgres (source of truth) ──────────────────
    today_str = datetime.now(IST).date().isoformat()
    for sc in all_scanners:
        sc_today = [t for t in trades if t["scanner"] == sc and t["entry_date"] == today_str]
        try:
            # We pass last_success=None so that the DB preserves the actual heartbeat
            # timestamps updated directly by the scanner loops.
            upsert_scanner_health(
                scanner_name  = sc,
                status        = None,
                last_success  = None,
                today_alerts  = len(sc_today),
                error_msg     = None,
            )
        except Exception:
            logger.warning(f"⚠️ Could not update scanner_health for {sc}")

    # ── 10. Write DB State ───────────────────────────────────────────────────────────
    payload = {
        "generated_at": datetime.now(IST).isoformat(),
        "summary":      summary,
        "trades":       sorted(trades, key=lambda t: t["entry_date"], reverse=True),
        "equity_curve": equity_curve,
        "monthly":      monthly,
        "by_scanner":   by_scanner,
        "by_category":  by_category,
    }

    try:
        payload_str = json.dumps(payload, default=str)
        save_system_state("performance_data", payload_str)
        logger.info("✅ PERFORMANCE TRACKER | Stored performance metrics in PostgreSQL")
    except Exception:
        logger.exception("❌ PERFORMANCE TRACKER | Failed to store performance metrics in DB")

    logger.info(
        f"✅ PERFORMANCE TRACKER | {len(trades)} alerts | "
        f"{len(winners)}W / {len(losers)}L / {len(open_p)} OPEN | "
        f"SL triggers={len(sl_closed)} | Target hits={len(target_closed)}"
    )


# =====================================================================================
# EMPTY FALLBACK
# =====================================================================================

def _write_empty():
    payload = {
        "generated_at": datetime.now(IST).isoformat(),
        "trades":       [],
        "summary": {
            "total_alerts": 0, "judged": 0, "winners": 0, "losers": 0,
            "open_positions": 0, "sl_triggered": 0, "target_hit": 0,
            "win_rate": 0, "avg_return_pct": 0, "avg_win_pct": 0,
            "avg_loss_pct": 0, "best_trade_pct": 0, "worst_trade_pct": 0,
            "expectancy": 0,
        },
        "equity_curve": [],
        "monthly":      [],
        "by_scanner":   {},
        "by_category":  {},
        "scanner_stats": {},
    }
    try:
        save_system_state("performance_data", json.dumps(payload, default=str))
        logger.info("✅ PERFORMANCE TRACKER | Stored empty performance metrics in PostgreSQL")
    except Exception:
        logger.exception("❌ PERFORMANCE TRACKER | Failed to store empty metrics in DB")


# =====================================================================================
# STANDALONE RUN
# =====================================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    build_performance_data()
