import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
import pandas as pd

from technical_indicators import apply_indicators
from watchlist_cache import get_watchlist
from price_cache import fetch_watchlist_data
from database import (
    upsert_breakout_watchlist,
    get_active_breakout_watchlist,
    sweep_stale_breakout_watchlist,
    check_recent_alert,
    save_alert_if_new, save_candidate,
    mark_breakout_watchlist_cooldown,
    upsert_scanner_health
)
import json
from config import MIN_STOCK_PRICE, ACTIVE_ALGO_VERSION, MULTI_TF_CONFIG, LIVE_1H_CONFIG

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)
    except Exception:
        return default

def strip_forming_candle(df, tf_minutes, ist_now):
    """Remove the forming (incomplete) candle from dataframe if it's still being built.
    
    Returns DataFrame with last row removed if incomplete, or original df if complete.
    CALLER MUST ALWAYS CHECK: if df is None or df.empty
    """
    import pandas as pd
    if df is None or df.empty:
        return df
    
    try:
        raw_ts = None
        datetime_col = next((c for c in ["Datetime", "Date", "index"] if c in df.columns), None)
        
        if datetime_col is not None:
            raw_ts = pd.Timestamp(df.iloc[-1][datetime_col])
        elif isinstance(df.index, pd.DatetimeIndex) or isinstance(df.index[-1], (pd.Timestamp, pd.DatetimeIndex)):
            raw_ts = pd.Timestamp(df.index[-1])
        else:
            # Fallback for naive RangeIndex parsing issues
            return df
            
        if raw_ts is not None:
            # [VERSION: MTF_CANDLE_STRIP_FIX] Robust timezone handling for forming candle strip
            if raw_ts.tzinfo is not None:
                raw_ts = raw_ts.tz_convert(IST)
                
            candle_start = raw_ts.replace(tzinfo=None)
            now_naive = ist_now.replace(tzinfo=None)
            
            # If the naive timestamp looks like UTC (e.g. 5+ hours behind IST), auto-correct it
            if now_naive - candle_start > pd.Timedelta(hours=4):
                candle_start = candle_start + pd.Timedelta(hours=5, minutes=30)
                
            candle_end = candle_start + pd.Timedelta(minutes=tf_minutes)
            
            if now_naive < candle_end:
                return df.iloc[:-1].copy()
    except Exception as e:
        logger.warning(f"Failed to strip forming candle: {e}")
        pass
    return df


from opportunity_manager import OpportunityManager
from trade_ranking_engine import TradeRankingEngine
from macro_utils import MarketRegimeEngine, get_macro_regime, get_nifty_20d_return
from strategy_policy import StrategyPolicyEngine

def run_hourly_phase(is_test_mode=False, run_once=False):
    """
    Phase A: Scans the entire fundamental universe on a 1H timeframe.
    Goal: Identify trend permission (Price > 200 EMA, 9 > 20 > 50 EMA, ADX > 20).
    Adds to breakout_watchlist as HOURLY_APPROVED.
    """
    from market_utils import is_market_open
    if not run_once and not is_market_open():
        logger.info("Market is closed. Skipping 1H phase.")
        return {"fetched": 0, "total": 0, "stale": 0, "approved": 0}
    logger.info("🕒 Starting Phase A (1H Trend Scanner)...")
    
    # 1. Get fundamental universe
    watchlist = get_watchlist()
    if watchlist.empty:
        logger.warning("No watchlist found.")
        return {"fetched": 0, "total": 0, "stale": 0}

    # [VERSION: SCANNER_DIAG_LOG_v1.0] Watchlist fingerprint for cross-run comparison
    import hashlib
    _wl_stocks = sorted(watchlist["Stock"].tolist())
    _wl_hash = hashlib.md5("|".join(_wl_stocks).encode()).hexdigest()[:12]
    logger.info(f"📋 [MULTI_TF] Watchlist fingerprint: {len(watchlist)} stocks | hash={_wl_hash}")

    # 2. Fetch 1H data
    ticker_data = fetch_watchlist_data(watchlist, period="60d", interval="1h")
    
    # Handle rate limiting or fetch failures gracefully - continue with partial data
    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 7] Standardized missing data fallback if fetch_watchlist_data fails
    if ticker_data is None:
        ticker_data = {}
        
    fetched_count = len(ticker_data)
    required_count = int(len(watchlist) * 0.70)
    
    if fetched_count < required_count:
        # [VERSION: MTF_FETCH_ABORT_FIX] Catch failure gracefully and skip only Phase A, instead of crashing the whole cycle
        logger.warning(f"⚠️ 1H data fetch returned {fetched_count}/{len(watchlist)} symbols (70% minimum required). Skipping Phase A processing.")
        return {"fetched": fetched_count, "total": len(watchlist), "stale": 0, "approved": 0, "abort": True}
    else:
        logger.info(f"✅ Successfully fetched {fetched_count} symbols for 1H hourly phase")
        
    stale_count = 0

    # ── FUNNEL STATS: measure how many stocks pass each gate ──────────────
    funnel = {"total": 0, "data_ok": 0, "indicators_ok": 0, "price_filtered": 0, "price_ok": 0,
              "ema_only_pass": 0, "adx_only_pass": 0, "ema_and_adx_pass": 0, "dist_pass": 0, "approved": 0}
    
    for idx, row in watchlist.iterrows():
        try:
            symbol = row["Stock"]
            category = row["Category"]
            funnel["total"] += 1
        
            df = ticker_data.get(symbol)
            # [VERSION: MTF_BAR_LIMIT_FIX] Reduced from 200 to 50 to allow YFinance fallback data to process safely
            if df is None or df.empty or len(df) < 50:
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
        
            close = _safe_float(latest.get("Close"))
            if close < MIN_STOCK_PRICE:
                funnel["price_filtered"] += 1
                continue
            
            # Extract indicators safely — NaN = indicator not ready, hard skip
            def _safe_val(series_val):
                """Return float or None if value is missing/NaN."""
                # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 4] Wrapped in try/except to catch ValueError on unparseable string data
                try:
                    if series_val is None:
                        return None
                    v = float(series_val)
                    if math.isnan(v):
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
            # [VERSION: MTF_DIST_GATE_FIX] Widened distance gate to allow stocks up to 2% ABOVE the breakout level to catch live momentum
            dist_ok = -0.02 <= dist_to_breakout <= 0.05
        
            if ema_ok:
                funnel["ema_only_pass"] += 1
            if adx_ok:
                funnel["adx_only_pass"] += 1
            if ema_ok and adx_ok:
                funnel["ema_and_adx_pass"] += 1
            if ema_ok and adx_ok and dist_ok:
                funnel["dist_pass"] += 1
                # We have an hourly approved setup!
                now_dt = datetime.now(IST)
                end_of_session = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
                if now_dt > end_of_session:
                    from datetime import timedelta
                    end_of_session = now_dt + timedelta(minutes=15)
                if not is_test_mode:
                    # [VERSION: MTF_PHASE_B_DROP_FIX] Force clear old context (expires_at, invalidated_at) so Phase B doesn't silently ignore this stock due to yesterday's stale state
                    upsert_breakout_watchlist(
                        symbol=symbol,
                        category=category,
                        current_state="HOURLY_APPROVED",
                        h1_status="PASSED",
                        breakout_level=prior_high,
                        clear_context=True,
                        trigger_level=prior_high,
                        signal_timestamp=now_dt.isoformat(),
                        expires_at=end_of_session.isoformat(),
                        timeframe="1h"
                    )
                funnel["approved"] += 1
                logger.info(f"✅ {symbol} upgraded to HOURLY_APPROVED (dist: {dist_to_breakout*100:.2f}%).")

        except Exception as e:
            logger.exception(f"Fault isolation caught exception for Phase A: {e}")
            continue
    # ── Log the funnel so we can see exactly where stocks drop off ────────
    logger.info(
        f"📊 Phase A Funnel: total={funnel['total']} → data_ok={funnel['data_ok']} → "
        f"indicators_ok={funnel['indicators_ok']} → price_ok={funnel['price_ok']} → "
        f"ema_only_pass={funnel['ema_only_pass']} → adx_only_pass={funnel['adx_only_pass']} → "
        f"ema_and_adx_pass={funnel['ema_and_adx_pass']} → dist_pass={funnel['dist_pass']} → "
        f"approved={funnel['approved']}"
    )
            
    return {"fetched": len(ticker_data), "total": len(watchlist), "stale": stale_count, "save_failures": 0}

def run_lower_tf_phase(regime_ctx=None, is_test_mode=False, run_once=False):
    """
    Phase B, C & D: Sub-hourly updater.
    Iterates active watchlist items and advances them through the 4-phase signal ladder:
      HOURLY_APPROVED → (30m) SETUP_ARMED → (15m) ENTRY_READY → (5m) TRADE_ACTIVE
    """
    from market_utils import is_market_open
    if not run_once and not is_market_open():
        logger.info("Market is closed. Skipping lower TF phase.")
        return {"fetched": 0, "total": 0, "stale": 0}
    logger.info("⚡ Starting Phase B/C/D (Sub-hourly Ladder Updater)...")
    
    active_items = get_active_breakout_watchlist()
    if not active_items:
        logger.info("No active setups to track.")
        return {"fetched": 0, "total": 0, "stale": 0}

    # Fetch pledge map to pass to scoring engine
    try:
        from database import get_pledge_map, get_latest_weights
        symbols = list(set(i["symbol"] for i in active_items))
        pledge_map = get_pledge_map(symbols)
        logger.info(f"🛡️ Fetched pledge data for {len(pledge_map)} symbols")
        
        regime_str = regime_ctx.get("trend", "NEUTRAL") if regime_ctx else "NEUTRAL"
        latest_db_weights = get_latest_weights(regime_str)
        bayesian_weights = latest_db_weights.get("weights") if latest_db_weights else None
    except Exception as e:
        logger.exception("Failed to fetch pledge map or weights")
        pledge_map = {}
        bayesian_weights = None

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
        
    # [VERSION: MTF_DAILY_PIVOTS_FIX] Fetch daily data for 5m pivot points
    data_daily = fetch_watchlist_data(pd.DataFrame({"Stock": needs_5m}), period="5d", interval="1d") if needs_5m else {}
    if data_daily is None:
        data_daily = {}
        
    def _check_fetch(data_dict, needed_list, tf_label):
        if not needed_list: return True
        req_len = len(needed_list)
        f_len = len(data_dict) if data_dict else 0
        if f_len < int(req_len * 0.70):
            logger.error(f"STALE DATA ERROR: Fetched {f_len}/{req_len} symbols for {tf_label}. Skipping this TF.")
            return False
        return True

    # [VERSION: MTF_FETCH_CHECK_FIX] Capture the fetch validation to gracefully skip failed timeframes
    ok_30m = _check_fetch(data_30m, needs_30m, "30m")
    ok_15m = _check_fetch(data_15m, needs_15m, "15m")
    ok_5m  = _check_fetch(data_5m, needs_5m, "5m")
    stale_count = 0
    db_save_failures = 0
    ist_now = datetime.now(IST)

    # Instantiate the in-memory opportunity pool for this scan cycle
    opportunity_manager = OpportunityManager(policy=regime_ctx.get("policy", {}) if regime_ctx else {})
    end_of_session = ist_now.replace(hour=15, minute=30, second=0, microsecond=0)
    if ist_now > end_of_session:
        end_of_session = ist_now + timedelta(minutes=15)

    
    # Funnel stats for Phase B/C/D
    lower_funnel = {"armed_candidates": 0, "bb_pass": 0, "armed": 0,
                    "entry_candidates": 0, "ema15_pass": 0, "entry_ready": 0,
                    "trigger_candidates": 0, "triggered": 0, "demoted": 0}

    for item in active_items:
        try:
            symbol = item["symbol"]
            state = item["current_state"]
            cat = item["category"]
            breakout_level = item["breakout_level"] or 0

            if breakout_level <= 0:
                continue

            # ── EXPIRY + DECAY: applies to both SETUP_ARMED and ENTRY_READY ──
            df_30_decay = data_30m.get(symbol)
            if state in ("SETUP_ARMED", "ENTRY_READY") and df_30_decay is not None:
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
                df = df_30_decay
                if df is None:
                    logger.debug(f"⏭️ {symbol} Decay check: no data returned from fetch")
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
                            if not is_test_mode:
                                # [VERSION: MTF_FLAPPING_FIX] Apply a 2-hour cooldown on drift demotion to prevent choppy day flapping
                                upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="HOURLY_APPROVED", clear_context=True, force=True)
                                mark_breakout_watchlist_cooldown(symbol, "HOURLY_APPROVED", hours=2)
                            state = "HOURLY_APPROVED"
                            lower_funnel["demoted"] += 1
                            logger.info(f"⚠️ {symbol} fell >3% from resistance. Downgraded to HOURLY_APPROVED.")
                        elif is_expired:
                            if not is_test_mode:
                                upsert_breakout_watchlist(symbol=symbol, category=cat, current_state="HOURLY_APPROVED", clear_context=True, force=True)
                            state = "HOURLY_APPROVED"
                            lower_funnel["demoted"] += 1
                            logger.info(f"⏳ {symbol} {item['current_state']} expired (stale >4h + drifted >1.5%). Downgraded.")

            # ── Phase B (30m): HOURLY_APPROVED → SETUP_ARMED ─────────────────
            if state == "HOURLY_APPROVED" and data_30m.get(symbol) is not None and ok_30m:
                lower_funnel["armed_candidates"] += 1
                df = data_30m.get(symbol)
                if df is None:
                    logger.debug(f"⏭️ {symbol} Phase B: no data returned from fetch")
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
                
                    close = _safe_float(latest.get("Close"))
                    dist_to_breakout = (breakout_level - close) / breakout_level
                
                    # Add 30m Volume Baseline for Fast Breakout Override
                    vol_ratio = 1.0
                    if "Volume" in latest and len(df) > 1:
                        mean_vol = df["Volume"].iloc[-21:-1].mean() if len(df) >= 22 else df["Volume"].iloc[:-1].mean()
                        mean_vol = max(float(mean_vol or 1.0), 1.0)
                        vol_ratio = _safe_float(latest.get("Volume")) / mean_vol
                
                    # Consolidation formed OR Fast Breakout override
                    # [FINDING-E FIX] Widened bb_pctile from 0.30 to 0.45. A stock in the
                    # lower 45th percentile of BB width is still consolidating. 30th pctile
                    # was extreme squeeze only — rejected most valid coiling setups.
                    is_consolidation = bb_pctile < 0.45 and (-0.015 <= dist_to_breakout <= 0.025)
                    is_fast_breakout = dist_to_breakout < -0.015 and vol_ratio > 1.2
                
                    if is_consolidation or is_fast_breakout:
                        lower_funnel["bb_pass"] += 1
                        swing_low = float(latest.get("SWING_LOW", close))
                        ema20 = float(latest.get("EMA20", close))
                    
                        ctx_json = json.dumps({"last_state_change_at": ist_now.strftime('%Y-%m-%d %H:%M:%S')})
                        now_iso = ist_now.isoformat()
                        expires_iso = min(ist_now + timedelta(minutes=60), end_of_session).isoformat()
                    
                        if not is_test_mode:
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
            if state == "SETUP_ARMED" and data_15m.get(symbol) is not None and ok_15m:
                lower_funnel["entry_candidates"] += 1
                df = data_15m.get(symbol)
                if df is None:
                    logger.debug(f"⏭️ {symbol} Phase C: no data returned from fetch")
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
                    close = _safe_float(latest.get("Close"))

                    if e9_15 <= 0 or e20_15 <= 0:
                        continue

                    dist_to_breakout = (breakout_level - close) / breakout_level

                    # 15m must show micro-alignment: EMA9 > EMA20, price near level (widened floors to allow coiling on resistance)
                    if e9_15 > e20_15 and (-0.015 <= dist_to_breakout <= 0.025):
                        lower_funnel["ema15_pass"] += 1
                        ctx_json = json.dumps({
                            "last_state_change_at": ist_now.strftime('%Y-%m-%d %H:%M:%S'),
                            "15m_e9": round(e9_15, 2),
                            "15m_e20": round(e20_15, 2)
                        })
                        now_iso = ist_now.isoformat()
                        expires_iso = min(ist_now + timedelta(minutes=30), end_of_session).isoformat()
                    
                        if not is_test_mode:
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
            if state == "ENTRY_READY" and data_5m.get(symbol) is not None and ok_5m:
                lower_funnel["trigger_candidates"] += 1
                df = data_5m.get(symbol)
                if df is None:
                    logger.debug(f"⏭️ {symbol} Phase D: no data returned from fetch")
                if df is not None:
                    if getattr(df, 'attrs', {}).get('is_stale') == True:
                        logger.debug(f"⏭️ Skipping {symbol} (5m trigger check) due to stale data.")
                        stale_count += 1
                        continue

                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 12] Defensive check against strip_forming_candle None return
                    df = strip_forming_candle(df, 5, ist_now)
                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 11] Added explicit debug logging for empty dataframes rather than silently skipping
                    if df is None or df.empty or len(df) < 2:
                        logger.debug(f"⏭️ {symbol} phase D: insufficient 5m data")
                        continue
                    # [VERSION: MTF_DAILY_PIVOTS_FIX] Inject daily_ohlc into indicator engine to correctly anchor intraday S/R pivots
                    daily_df = data_daily.get(symbol)
                    df = apply_indicators(df, timeframe="5m", daily_ohlc=daily_df)
                    if df.empty or "EMA9" not in df.columns or "ATR20" not in df.columns or "Volume" not in df.columns:
                        logger.debug(f"⏭️ {symbol} phase D: missing required 5m indicators")
                        continue
                    
                    latest = df.iloc[-1]
                    prev = df.iloc[-2]
                
                    trigger_level = float(item.get("trigger_level") or breakout_level)
                    max_ext_atr = float(item.get("max_extension_atr") or 0.8)
                
                    e9 = float(latest.get("EMA9", 0))
                    close = _safe_float(latest.get("Close"))
                    low = _safe_float(latest.get("Low"))
                    open_px = _safe_float(latest.get("Open"))
                    atr20 = float(latest.get("ATR20", 0.0) or 0.0)
                
                    if atr20 <= 0:
                        continue
                        
                    # [VERSION: MULTI_TF_THRUST_FIX_v1.0] Dynamic ATR-relative buffer (15% of ATR) to prevent mathematical contradiction
                    # with the max_ext_atr (which is typically 80% of ATR).
                    buffer_val = 0.15 * atr20
                
                    if len(df) >= 22:
                        mean_vol = _safe_float(df["Volume"].iloc[-21:-1].mean()) or 1.0
                    else:
                        mean_vol = _safe_float(df["Volume"].iloc[:-1].mean()) or 1.0
                    mean_vol = max(mean_vol, 1.0)
                    vol_ratio = _safe_float(latest.get("Volume")) / mean_vol
                
                    candle_range = _safe_float(latest.get("High")) - _safe_float(latest.get("Low"))
                    if candle_range > 0:
                        close_position = (close - low) / candle_range
                        upper_wick_ratio = (_safe_float(latest.get("High")) - close) / candle_range
                    else:
                        close_position = 0.5
                        upper_wick_ratio = 0.0

                    # Extension limit strict check
                    if close > trigger_level + (max_ext_atr * atr20):
                        if atr20 > 0:
                            dist_atr = (close - trigger_level) / atr20
                            logger.info(f"🚫 {symbol} PhaseD Reject | Reason=PD01_OVER_EXTENDED | Trigger={trigger_level:.2f} Close={close:.2f} PrevHigh={float(prev['High']):.2f} ATR={atr20:.2f} ATR_Extension={dist_atr:.2f} VolRatio={vol_ratio:.2f} ClosePos={close_position:.2f} Pattern=N/A")
                        continue

                    is_ready = False
                    trigger_type = ""
                
                    # Thrust/Continuation Trigger
                    # Price breaks local high while still close to level, with volume
                    if close > float(prev["High"]) and close > (trigger_level + buffer_val) and vol_ratio > 1.2:
                        if close_position >= 0.6 and upper_wick_ratio < 0.35:
                            is_ready = True
                            trigger_type = "thrust"
                    
                    # [VERSION: MULTI_TF_PATCH_v1.1] Decoupled Pullback Trigger from Thrust Trigger
                    # Breakout level or EMA9 is defended, and price reclaims with volume and strong rejection
                    if not is_ready and low <= max(trigger_level, e9):
                        if close >= trigger_level and close > float(prev["High"]) and close > open_px and vol_ratio > 1.0:
                            if close_position >= 0.6:  # strong interaction/engulfing
                                is_ready = True
                                trigger_type = "pullback"

                    if not is_ready:
                        # Log reasons only if stock has touched/entered the trigger zone
                        if close >= (trigger_level - buffer_val) or low <= max(trigger_level, e9):
                            # Boolean evaluations for decision trace
                            c_engulf = close > float(prev["High"])
                            c_vol = vol_ratio > 1.0
                            c_close_pos = close_position >= 0.6
                            c_bull_body = close > open_px
                            c_defended = low <= max(trigger_level, e9)
                            c_above_trig = close >= trigger_level
                            
                            reasons = []
                            if not c_engulf:
                                reasons.append("PD02")
                            if not c_vol:
                                reasons.append("PD03")
                            if not c_close_pos:
                                reasons.append("PD04")
                            if not reasons:
                                reasons.append("PD05")
                                
                            trace = f"Engulf={c_engulf} BullBody={c_bull_body} Defended={c_defended} AboveTrig={c_above_trig} Vol={c_vol} StrongClose={c_close_pos}"
                            reason_str = f"{'|'.join(reasons)} [{trace}]"
                            
                            dist_atr = ((close - trigger_level) / atr20) if atr20 > 0 else 0.0
                            logger.info(f"🚫 {symbol} PhaseD Reject | Reason={reason_str} | Trigger={trigger_level:.2f} Close={close:.2f} PrevHigh={float(prev['High']):.2f} ATR={atr20:.2f} ATR_Extension={dist_atr:.2f} VolRatio={vol_ratio:.2f} ClosePos={close_position:.2f} Pattern=EVAL")
                
                    if is_ready:
                        # Do not generate new buy alerts on stale data returned by provider
                        if getattr(df, 'attrs', {}).get('is_stale'):
                            logger.info(f"Skipping buy alert for {symbol} because data is stale")
                            continue
                        # Idempotency check before alert
                        if not check_recent_alert(symbol, scanner="multi_tf_scanner", breakout_type="MULTI_TF", lookback_minutes=390):
                            from sl_target_helper import compute_sl_and_target
                            
                            # [VERSION: MTF_VWAP_FALLBACK_FIX] Fallback to EMA20 if VWAP is missing due to lack of intraday volume
                            vwap_val = latest.get("VWAP")
                            if vwap_val is None or pd.isna(vwap_val) or vwap_val <= 0:
                                vwap_val = _safe_float(latest.get("EMA20", close))
                                
                            sl_result = compute_sl_and_target(
                                entry_price=close,
                                atr=atr20,
                                candle_range=_safe_float(latest.get("High")) - _safe_float(latest.get("Low")),
                                mode="MULTI_TF",
                                adx=latest.get("ADX"),
                                rsi=_safe_float(latest.get("RSI", 0)),
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
                                candle_low=low,
                                vwap=vwap_val,
                                ticker=df,
                                # [MTF_TF_VAR_FIX_v1.0] BUG-3 FIX: tf_15m/tf_30m/tf_1h were undefined (stale refactor
                                # variable names). The actual data lives in data_15m[symbol] and data_30m[symbol] dicts.
                                # Using safe pd.DataFrame() fallback if symbol not in the dict or data is missing.
                                swing_low_15m=_safe_float(data_15m[symbol].iloc[-1].get("SWING_LOW")) if symbol in data_15m and data_15m[symbol] is not None and not data_15m[symbol].empty else None,
                                swing_high_15m=_safe_float(data_15m[symbol].iloc[-1].get("SWING_HIGH")) if symbol in data_15m and data_15m[symbol] is not None and not data_15m[symbol].empty else None,
                                swing_low_30m=_safe_float(data_30m[symbol].iloc[-1].get("SWING_LOW")) if symbol in data_30m and data_30m[symbol] is not None and not data_30m[symbol].empty else None,
                                swing_high_30m=_safe_float(data_30m[symbol].iloc[-1].get("SWING_HIGH")) if symbol in data_30m and data_30m[symbol] is not None and not data_30m[symbol].empty else None,
                                swing_low_1h=None,
                                swing_high_1h=None,
                            )
                            final_sl = sl_result["stop_loss"]
                            calc_target = sl_result["target_1"]

                            if sl_result.get("is_rejected"):
                                from database import save_rejected_alert
                                if not is_test_mode:
                                    save_rejected_alert(
                                        symbol=symbol,
                                        scanner="MULTI_TF",
                                        rejection_reason=sl_result.get("rejection_reason", "V7 Engine Reject"),
                                        engine_version=sl_result.get("engine_version", "SL_ENGINE_V7.1"),
                                        context={"category": cat, "score": 0, "sl_result": sl_result}
                                    )
                                logger.info(f"🚫 {symbol} alert SUPPRESSED: {sl_result.get('rejection_reason')}")
                                continue

                            invalidation_level = float(item.get("invalidation_level") or (low - atr20))
                            ctx = {
                                "ladder": "TRADE_ACTIVE",
                                "breakout_level": round(trigger_level, 2),
                                "trigger": trigger_type,
                                "vol_ratio": round(vol_ratio, 2),
                                "final_sl": round(final_sl, 2),
                                "invalidation_level": round(invalidation_level, 2),
                                "stop_basis": sl_result.get("sl_method", "Structural SL"),
                                "sl_result": sl_result,
                                "algo_version": ACTIVE_ALGO_VERSION,
                                "algo_params": {
                                    **MULTI_TF_CONFIG,
                                    "MIN_BREAKOUT_MARGIN": 0.003, # 15m default
                                    "MAX_TARGET_ATR": 5.0
                                }
                            }
                        
                            if is_test_mode:
                                inserted, reason = True, "TEST_MODE"
                                logger.info(f"🧪 [TEST MODE] Skipping save_alert_if_new for {symbol}")
                            else:
                                base_score = int(80 + (vol_ratio * 5))
                                base_score = max(0, min(100, base_score))
                                
                                # ── Bayesian Pledge Penalty ──
                                promoter_pledge_pct = pledge_map.get(symbol)
                                if promoter_pledge_pct is not None and bayesian_weights and "PLEDGE_PENALTY" in bayesian_weights:
                                    max_penalty = float(bayesian_weights["PLEDGE_PENALTY"])
                                    if promoter_pledge_pct > 10.0:
                                        scale = min(1.0, (promoter_pledge_pct - 10.0) / 40.0)
                                        pledge_penalty = int(max_penalty * scale)
                                        if pledge_penalty < 0:
                                            base_score += pledge_penalty
                                            logger.warning(f"  {pledge_penalty} [{symbol}] Promoter Pledge Penalty ({promoter_pledge_pct:.1f}% pledge)")
                                            base_score = max(0, base_score)
                                
                                try:
                                    from block_deal_detector import compute_inst_bonus
                                    inst_bonus = compute_inst_bonus(symbol, base_score)
                                except Exception as e:
                                    logger.warning(f"Error checking institutional footprints in Multi-TF: {e}")
                                    inst_bonus = 0
                                final_score = min(100, base_score + inst_bonus)

                                # Queue as QUALIFIED candidate — OpportunityManager
                                # handles freshness, ranking, allocation, and persistence
                                opportunity_manager.add({

                                    "symbol": symbol,
                                    "breakout_type": "MULTI_TF",
                                    "scanner": "MULTI_TF",
                                    "category": cat,
                                    "technical_score": final_score,
                                    "volume_ratio": vol_ratio,
                                    "delivery_pct": 0.0,
                                    "rr_ratio": sl_result.get("natural_rr", sl_result.get("rr_ratio", 0.0)) if sl_result else 0.0,
                                    "market_context": regime_ctx,
                                    "entry_price": close,
                                    "stop_loss": final_sl,
                                    "target_1": sl_result.get("target_1"),
                                    "target_2": sl_result.get("target_2"),
                                    "target_3": sl_result.get("target_3"),
                                    "structural_failure_stop": sl_result.get("structural_failure_stop"),
                                    "target_quality_score": sl_result.get("target_quality"),
                                    "signals": f"MULTI_TF Ladder (1h→30m→15m→5m) | {trigger_type}",
                                    "rsi": _safe_float(latest.get("RSI", 0)),
                                    "context": ctx,
                                    "item_category": cat,
                                    "trigger_type": trigger_type,
                                    "symbol_for_watchlist": symbol,
                                })
                                inserted, reason = True, "CANDIDATE_QUEUED"

                            if inserted or reason == "CANDIDATE_QUEUED":
                                if not is_test_mode:
                                    upsert_breakout_watchlist(
                                        symbol=symbol, category=cat, current_state="TRADE_ACTIVE",
                                        m5_status="PASSED"
                                    )
                                    mark_breakout_watchlist_cooldown(symbol, "TRADE_ACTIVE", hours=24)
                                lower_funnel["triggered"] += 1
                                # [VERSION: SCANNER_DIAG_LOG_v1.0] Log full diagnostic for every triggered trade
                                _last_bar_date = "unknown"
                                try:
                                    if isinstance(df.index, pd.DatetimeIndex):
                                        _last_bar_date = str(df.index[-1])[:16]
                                    elif "Datetime" in df.columns:
                                        _last_bar_date = str(df["Datetime"].iloc[-1])[:16]
                                except Exception:
                                    pass
                                logger.info(
                                    f"✅ [MULTI_TF] PASSED ALL FILTERS: {symbol} | "
                                    f"trigger={trigger_type} | vol_ratio={vol_ratio:.2f} | "
                                    f"entry=₹{close:.2f} | sl=₹{final_sl:.2f} | t1=₹{sl_result.get('target_1')} | "
                                    f"last_bar={_last_bar_date} | category={cat}"
                                )
                                logger.info(f"🔔 {symbol} EXECUTED! TRADE_ACTIVE alert generated via {trigger_type}.")
                            else:
                                logger.info(f"🚫 {symbol} alert SUPPRESSED: {reason}")
                                # [VERSION: MTF_DB_SAVE_FIX] Track silent DB save failures and flip health to DEGRADED
                                if reason != "ALREADY_EXISTS" and reason != "RECENT_ALERT_EXISTS" and "CONFLICT" not in str(reason).upper():
                                    db_save_failures += 1
                                # Do NOT advance state or set cooldown so it can try again if data freshness recovers

        except Exception as e:
            logger.exception(f"Fault isolation caught exception in Phase B/C/D: {e}")
            continue
    # ── Log the funnel so we can see exactly where stocks drop off ────────
    logger.info(f"📊 Phase B/C/D Funnel: "
                f"30m_candidates={lower_funnel['armed_candidates']} → bb_pass={lower_funnel['bb_pass']} → armed={lower_funnel['armed']} | "
                f"15m_candidates={lower_funnel['entry_candidates']} → ema15_pass={lower_funnel['ema15_pass']} → entry_ready={lower_funnel['entry_ready']} | "
                f"5m_candidates={lower_funnel['trigger_candidates']} → triggered={lower_funnel['triggered']}")

    # ── Global Ranking & Allocation (end-of-sweep, in-memory) ─────────────
    if not is_test_mode:
        try:
            opportunity_manager.process()
        except Exception as e:
            logger.error(f"OpportunityManager failed to process: {e}")

    unique_needed = set(needs_30m) | set(needs_15m) | set(needs_5m)
    unique_fetched = set(data_30m.keys()) | set(data_15m.keys()) | set(data_5m.keys())
    return {"fetched": len(unique_fetched), "total": len(unique_needed), "stale": stale_count, "save_failures": db_save_failures}

def run_sweeper(is_test_mode=False):
    if is_test_mode:
        logger.info("🧪 [TEST MODE] Skipping sweep_stale_breakout_watchlist")
        counts = {}
    else:
        counts = sweep_stale_breakout_watchlist()
    if counts:
        counts_str = ", ".join(f"{k}: {v}" for k, v in counts.items())
        logger.info(f"🧹 Swept stale breakout watchlist setups. Expired -> {counts_str}")
    else:
        logger.info("🧹 Swept stale breakout watchlist setups. No expirations.")

from lock_utils import ProcessLock
_scan_lock = ProcessLock("multi_tf_scanner")

def start(run_once=False, is_test_mode=False):
    if run_once:
        if not _scan_lock.acquire(blocking=False):
            raise RuntimeError("Scanner is already actively running!")
    else:
        while not _scan_lock.acquire(blocking=False):
            import time
            time.sleep(60)
    try:
        return _start_wrapper(run_once, is_test_mode=is_test_mode)
    finally:
        _scan_lock.release()

def _start_wrapper(run_once=False, is_test_mode=False):
    from datetime import time as dt_time
    while True:
        try:
            ist_now = datetime.now(IST)
            current_time = ist_now.time()
            
            scan_start = datetime.now(IST)
            from market_utils import is_market_open
            market_open = is_market_open(ist_now)
            # Admin manual triggers pass run_once=True, which should bypass the market closed check
            is_active_window = market_open or run_once
            
            import database
            if not is_active_window:
                # We optionally log this, but since it loops every 5 mins we don't want to spam the console
                # logger.info("Market closed. Scanner pausing until next market session...")
                if not getattr(database, "DONT_SAVE_ALERTS", False):
                    try:
                        from database import upsert_scanner_health
                        upsert_scanner_health(
                            scanner_name="MULTI_TF",
                            status="IDLE",
                            scheduled_for="Every 5min (9:15 AM - 3:30 PM)"
                        )
                    except Exception:
                        pass
                time.sleep(300)
                continue
                
            logger.info("=========================================")
            logger.info(f"🚀 [START] MULTI-TF LADDER INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')}")
            
            try:
                from database import upsert_scanner_health
                upsert_scanner_health("MULTI_TF", "RUNNING", error_msg="Multi-TF Scan in progress...")
            except Exception:
                pass
                
            # Cache regime once per cycle
            # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 1] Pass nifty_ret explicitly to get_macro_regime to avoid redundant API calls
            try:
                nifty_ret = get_nifty_20d_return()
                regime_ctx = MarketRegimeEngine.get_regime_context(nifty_ret)
                policy = StrategyPolicyEngine.get_policy(regime_ctx, "MULTI_TF")
                regime_ctx["policy"] = policy
            except Exception as e:
                logger.warning(f"⚠️ Failed to compute macro regime: {e}. Defaulting to NEUTRAL.")
                regime_ctx = {"trend": "NEUTRAL", "biases": {}}
            
            # 1. Sweep old states
            run_sweeper(is_test_mode=is_test_mode)
            
            # 2. Hourly phase (could be scheduled to only run top/bottom of hour, but we run it to keep it simple or wrapper handles scheduling)
            metrics_a = run_hourly_phase(is_test_mode=is_test_mode, run_once=run_once)
            
            # 3. Lower TF updater
            metrics_b = run_lower_tf_phase(regime_ctx=regime_ctx, is_test_mode=is_test_mode, run_once=run_once)
            
            elapsed_time = (datetime.now(IST) - scan_start).total_seconds()
            logger.info("=========================================")
            logger.info(f"✅ [COMPLETE] MULTI-TF LADDER DONE | {elapsed_time:.2f}s | Status=OK")
            logger.info("=========================================")

            status = "OK" if market_open else "IDLE"
            error_msg = None
            
            total_stale = (metrics_a.get("stale", 0) + metrics_b.get("stale", 0))
            total_symbols = (metrics_a.get("total", 0) + metrics_b.get("total", 0))
            
            if total_symbols > 0 and total_stale / total_symbols > 0.05:
                status = "DEGRADED"
                error_msg = f"Stale Data: {total_stale}/{total_symbols} symbols"
                
            total_fetched_a = metrics_a.get("fetched", 0)
            total_fetched_b = metrics_b.get("fetched", 0)
            total_expected_a = metrics_a.get("total", 0)
            total_expected_b = metrics_b.get("total", 0)
            
            if (total_expected_a > 0 and total_fetched_a < total_expected_a * 0.95) or (total_expected_b > 0 and total_fetched_b < total_expected_b * 0.95):
                status = "DEGRADED"
                error_msg = f"Partial Fetch: {total_fetched_a + total_fetched_b}/{total_expected_a + total_expected_b} symbols"
                
            total_save_failures = metrics_a.get("save_failures", 0) + metrics_b.get("save_failures", 0)
            if total_save_failures > 0:
                status = "DEGRADED"
                error_msg = f"DB Save Failures: {total_save_failures} drops"
            
            # Map overall outcome
            outcome = "SUCCESS"
            if total_expected_a > 0 and total_fetched_a < total_expected_a * 0.70:
                outcome = "PARTIAL"
            if total_fetched_a == 0 and total_fetched_b == 0:
                outcome = "FAILED"

            if not getattr(database, "DONT_SAVE_ALERTS", False):
                try:
                    from database import upsert_scanner_health
                    upsert_scanner_health(
                        scanner_name="MULTI_TF",
                        status=status if outcome != "FAILED" else "DEGRADED",
                        last_success=datetime.now(IST).isoformat() if outcome != "FAILED" else None,
                        total_count=metrics_a.get("total", 0),
                        error_msg=error_msg,
                        scheduled_for="Every 5min (9:15 AM - 3:30 PM)",
                        outcome=outcome,
                        duration_seconds=elapsed_time
                    )
                except Exception:
                    logger.exception("❌ Failed to update scanner health for MULTI_TF")
            
            total_scanned = metrics_a.get("total", 0) + metrics_b.get("total", 0)
            
            try:
                from database import insert_notification
                from push_service import send_push_to_all
                if status == "OK" and run_once:
                    insert_notification("admin", "🚀 MULTI_TF Scanner ran successfully.", f"Evaluated {total_scanned} setups across multiple timeframes.")
                elif status == "DEGRADED":
                    insert_notification("admin", f"⚠️ MULTI_TF Scanner finished with DEGRADED status", error_msg or f"Evaluated {total_scanned} setups but data was degraded.")
                    send_push_to_all("⚠️ MULTI_TF Scanner DEGRADED", error_msg or "Stale data exceeded limit.")
            except Exception:
                pass
                
            if run_once:
                return {"total_count": total_symbols}
                
            logger.info("💤 Sleeping 5 minutes before next Multi-TF ladder run...")
            time.sleep(300)
            
        except Exception as e:
            logger.exception("❌ MULTI-TF LADDER CRASHED")
            if not getattr(database, "DONT_SAVE_ALERTS", False):
                try:
                    from database import upsert_scanner_health
                    upsert_scanner_health(
                        scanner_name="MULTI_TF",
                        status="DOWN",
                        error_msg=str(e)[:500],
                        scheduled_for="Every 5min (9:15 AM - 3:30 PM)"
                    )
                    from database import insert_notification
                    from push_service import send_push_to_all
                    insert_notification("admin", f"❌ MULTI_TF Scanner CRASHED (DOWN)", f"Error: {str(e)[:200]}")
                    send_push_to_all("❌ MULTI_TF Scanner DOWN", f"Crash: {str(e)[:100]}")
                except Exception as ex:
                    logger.exception("Failed to update scanner health to DOWN")

            if run_once:
                raise
            time.sleep(60)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start(run_once=True)