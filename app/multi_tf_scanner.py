from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union
from scanner_telemetry import ScannerDecisionLogger, global_telemetry
import time as _time
# =====================================================================================
# app/multi_tf_scanner.py (SCHEDULER READY)
# MULTI-TIMEFRAME INTRADAY BREAKOUT SCANNER
#
# RULE 67 MANDATORY CHANGE-RATIONALE:
# - Verified and enhanced GlobalScannerTelemetryEngine logging across Multi-TF ladder phases (1H, 30m, 15m, 5m).
# - Rationale: Tracks exact timeframe alignment states, squeeze releases, entry ready triggers, and risk management parameters
#   in scanner_telemetry.jsonl to maintain 100% operational transparency.
# =====================================================================================
import os
import logging
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import time
import pandas as pd

from technical_indicators import apply_indicators
_last_mtf_parquet_upload = 0
from memory_profiler import MemoryProfiler
from core_enums import ProviderResult
from price_cache import fetch_watchlist_data
from database import (
    upsert_breakout_watchlist,
    batch_upsert_breakout_watchlist,
    get_active_breakout_watchlist,
    sweep_stale_breakout_watchlist,
    check_recent_alert,
    save_alert_if_new, save_candidate,
    mark_breakout_watchlist_cooldown,
    upsert_scanner_health,
    get_mtf_target_universe
)
import json
from config import MIN_STOCK_PRICE, SCANNER_MULTI_TF, ACTIVE_ALGO_VERSION, MULTI_TF_CONFIG, LIVE_1H_CONFIG
# [AUDIT-M1] LIVE_1H_CONFIG is imported but not yet applied to any Phase A/B/C/D gate.
# Currently all Multi-TF gates use MULTI_TF_CONFIG or hardcoded values.
# To wire: apply LIVE_1H_CONFIG thresholds (MIN_RSI, MIN_VOLUME_RATIO, etc.) in the live
# intraday scan path when interval="1h" and is_live_session=True.

# [VERSION: PERF_PROFILER_v1.0] Stage timing + per-filter rejection observability
# profile_timing logs wall-clock duration + RSS delta for each Multi-TF phase run.
# FilterStats captures per-filter rejection breakdowns to artifacts/profiling/.
from perf_utils import profile_timing, FilterStats

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _safe_float(val, default=0.0):
    try:
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)
    except Exception:
        return default

def evaluate_multi_tf_symbol(symbol: str, df: pd.DataFrame, regime_ctx: dict = None, pre_fetched_h1_df: pd.DataFrame = None, allow_live_fetch: bool = True, fund_data: dict = None, **kwargs) -> dict:
    """
    Evaluates a single symbol against the production Multi-TF Intraday scanner rules.
    Attempts to fetch true 1H intraday data for Phase A trend permission verification when daily bars are supplied.
    """
    ticker = None
    if df is not None and not df.empty and len(df) >= 50:
        # Check if df contains 1H intraday data or daily data
        time_diffs = (df.index[1:] - df.index[:-1]).seconds / 60 if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1 else [1440]
        med_tf = pd.Series(time_diffs).median() if len(time_diffs) > 0 else 1440
        if med_tf < 300:
            ticker = df.copy()

    if ticker is None and pre_fetched_h1_df is not None and not pre_fetched_h1_df.empty:
        ticker = pre_fetched_h1_df.copy()

    # [VERSION: QUICK_DIAGNOSTIC_v1.0] Allow skipping live API fetch for quick UI diagnostics
    if ticker is None and allow_live_fetch:
        try:
            h1_data = fetch_watchlist_data(pd.DataFrame([{"Stock": symbol}]), period="1mo", interval="1h")
            if h1_data and symbol in h1_data and isinstance(h1_data[symbol], pd.DataFrame) and not h1_data[symbol].empty:
                ticker = h1_data[symbol].copy()
        except Exception as _e:
            logger.debug(f"Could not fetch 1H intraday data for {symbol}: {_e}")

    if ticker is None:
        ticker = df.copy() if df is not None and not df.empty else None

    if ticker is None or ticker.empty or len(ticker) < 50:
        return {
            "status": "NO",
            "reasons": [f"Insufficient historical price data ({len(ticker) if ticker is not None else 0} bars < 50 minimum)"],
            "score": 0.0,
            "qualified": False
        }

    if isinstance(ticker.columns, pd.MultiIndex):
        ticker.columns = ticker.columns.get_level_values(0)
    ticker = ticker.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    if len(ticker) < 50:
        return {"status": "NO", "reasons": [f"Insufficient valid bars ({len(ticker)} < 50)"], "score": 0.0, "qualified": False}

    ticker = apply_indicators(ticker, timeframe="1h" if "EMA9" not in ticker.columns else "1d")
    if ticker is None or ticker.empty:
        return {"status": "NO", "reasons": ["Failed to calculate technical indicators"], "score": 0.0, "qualified": False}

    latest = ticker.iloc[-1]
    close_price = float(latest["Close"])

    if close_price < MIN_STOCK_PRICE:
        return {
            "status": "NO",
            "reasons": [f"Close ₹{close_price:.2f} < ₹{MIN_STOCK_PRICE:.0f} price floor"],
            "score": 0.0,
            "qualified": False,
            "entry_price": close_price
        }

    e9 = _safe_float(latest.get("EMA9"))
    e20 = _safe_float(latest.get("EMA20"))
    s50 = _safe_float(latest.get("SMA50"))
    s200 = _safe_float(latest.get("SMA200"))
    adx_val = _safe_float(latest.get("ADX", 25.0))
    prior_high = _safe_float(latest.get("PRIOR_20D_HIGH", ticker["High"].iloc[-21:-1].max() if len(ticker) >= 21 else ticker["High"].max()))

    checks = []
    if s200 > 0:
        ema_ok = (close_price > e20 and e20 > s50 and close_price > s200)
        if not ema_ok:
            checks.append(f"Trend Permission Fail: Requires Close ({close_price:.2f}) > EMA20 ({e20:.2f}) > SMA50 ({s50:.2f}) & Close > SMA200 ({s200:.2f})")
    else:
        ema_ok = (close_price > e20 and e20 > s50)
        if not ema_ok:
            checks.append(f"Trend Permission Fail: Requires Close ({close_price:.2f}) > EMA20 ({e20:.2f}) > SMA50 ({s50:.2f})")

    # [VERSION: MULTI_TF_PATCH_v1.9] ADX hard gate removed in favor of scoring model

    # Rule: MTF-001
    # Hourly RSI Confirmation
    hourly_rsi = _safe_float(latest.get("RSI"))
    min_rsi = MULTI_TF_CONFIG.get("MIN_RSI", 52)
    max_rsi = MULTI_TF_CONFIG.get("MAX_RSI", 87)
    if hourly_rsi is not None:
        if hourly_rsi < min_rsi or hourly_rsi > max_rsi:
            checks.append(f"Hourly RSI Confirmation Fail: RSI {hourly_rsi:.1f} outside configured band [{min_rsi}, {max_rsi}]")
    else:
        checks.append("Hourly RSI is missing or NaN")

    if prior_high <= 0:
        checks.append("Invalid prior 20-day high level")
    else:
        dist_to_breakout = (prior_high - close_price) / prior_high
        if not (-0.06 <= dist_to_breakout <= 0.08):
            checks.append(f"Distance to breakout level {dist_to_breakout*100:.1f}% outside -6.0% to +8.0% window")

    if checks:
        try:
            from scanner_telemetry import DecisionContext, telemetry_engine
            ctx = DecisionContext(symbol=symbol, scanner_name="MULTI_TF")
            ctx.capture_raw_market(
                open_p=_safe_float(latest.get("Open")),
                high_p=_safe_float(latest.get("High")),
                low_p=_safe_float(latest.get("Low")),
                close_p=_safe_float(latest.get("Close")),
                volume=_safe_float(latest.get("Volume"))
            )
            ctx.capture_indicators(
                rsi=_safe_float(latest.get("RSI")),
                ema20=e20,
                sma50=s50,
                sma200=s200,
                adx=adx_val,
                prior_20d_high=prior_high
            )
            ctx.capture_gate("1H_PermissionGate", False, actual_val=close_price, reason="; ".join(checks))
            ctx.finalize(decision="REJECTED", primary_reason=checks[0])
            telemetry_engine.emit_terminal(ctx)
        except Exception:
            pass
        return {
            "status": "NO",
            "reasons": checks,
            "score": 0.0,
            "qualified": False,
            "entry_price": close_price,
            "atr_20": float(latest.get("ATR", close_price * 0.025))
        }

    # Dynamic Scoring Model
    score = 75.0
    if adx_val > 30.0:
        score += 5.0
    elif 20.0 <= adx_val <= 30.0:
        pass # Neutral
    elif 15.0 <= adx_val < 20.0:
        score -= 5.0
    else:
        score -= 10.0
        
    if e9 > e20:
        score += 5.0
    atr_val = float(latest.get("ATR", close_price * 0.025))

    # Evaluate 30m (Phase B), 15m (Phase C), and 5m (Phase D) intraday timeframes if available
    phase_details = [f"1H Trend Permission Met (EMA Alignment | ADX {adx_val:.1f} | Close ₹{close_price:.2f} near Breakout ₹{prior_high:.2f})"]
    has_30m_pass = False
    has_15m_pass = False
    has_5m_pass = False

    try:
        m30_data = fetch_watchlist_data(pd.DataFrame([{"Stock": symbol}]), period="5d", interval="30m")
        if m30_data and symbol in m30_data and isinstance(m30_data[symbol], pd.DataFrame) and not m30_data[symbol].empty:
            df_30 = apply_indicators(m30_data[symbol].copy(), timeframe="30m")
            if df_30 is not None and len(df_30) >= 2:
                # [FIX P2-5] Look back 6-8 bars for recent squeeze, not just prior bar
                bb_recent_squeeze = False
                for _lb in range(1, min(8, len(df_30))):
                    _idx = len(df_30) - 1 - _lb
                    if _idx < 0:
                        break
                    _raw_p = df_30.iloc[_idx].get("BB_WIDTH_PCTILE", 1.0)
                    _p = float(_raw_p) if pd.notna(_raw_p) else 1.0
                    if _p < 0.45:
                        bb_recent_squeeze = True
                        break
                dist_to_bo = (prior_high - close_price) / prior_high
                if bb_recent_squeeze or dist_to_bo < -0.015:
                    has_30m_pass = True
                    phase_details.append(f"30m Phase B Squeeze-Released Met (recent squeeze detected)")
                else:
                    phase_details.append(f"30m Phase B Pending (no recent squeeze in lookback)")
    except Exception as exc:
        # [FIX MTF-24] Log diagnostic failures instead of silently discarding
        logger.exception(f"{symbol}: diagnostic 30m evaluation failed: {exc}")
        phase_details.append("30m Phase B unavailable due to processing error")

    try:
        m15_data = fetch_watchlist_data(pd.DataFrame([{"Stock": symbol}]), period="5d", interval="15m")
        if m15_data and symbol in m15_data and isinstance(m15_data[symbol], pd.DataFrame) and not m15_data[symbol].empty:
            df_15 = apply_indicators(m15_data[symbol].copy(), timeframe="15m")
            if df_15 is not None and len(df_15) >= 2:
                lat_15 = df_15.iloc[-1]
                ema15 = float(lat_15.get("EMA15", lat_15.get("EMA20", close_price)))
                # [FIX MTF-23] Compare 15m close with 15m EMA (was incorrectly using 1h close_price)
                close_15 = _safe_float(lat_15.get("Close"))
                if close_15 >= ema15:
                    has_15m_pass = True
                    phase_details.append(f"15m Phase C Entry Ready (Close ₹{close_15:.2f} ≥ EMA15 ₹{ema15:.2f})")
                else:
                    phase_details.append(f"15m Phase C Pending (Close ₹{close_15:.2f} < EMA15 ₹{ema15:.2f})")
    except Exception as exc:
        # [FIX MTF-24] Log diagnostic failures instead of silently discarding
        logger.exception(f"{symbol}: diagnostic 15m evaluation failed: {exc}")
        phase_details.append("15m Phase C unavailable due to processing error")

    try:
        m5_data = fetch_watchlist_data(pd.DataFrame([{"Stock": symbol}]), period="5d", interval="5m")
        if m5_data and symbol in m5_data and isinstance(m5_data[symbol], pd.DataFrame) and not m5_data[symbol].empty:
            df_5 = apply_indicators(m5_data[symbol].copy(), timeframe="5m")
            if df_5 is not None and len(df_5) >= 2:
                lat_5 = df_5.iloc[-1]
                close_5 = float(lat_5.get("Close"))
                vol_5 = float(lat_5.get("Volume", 0))
                mean_vol_5 = float(df_5["Volume"].iloc[-21:-1].mean()) if len(df_5) >= 22 else float(df_5["Volume"].mean())
                vr_5 = (vol_5 / mean_vol_5) if mean_vol_5 > 0 else 1.0
                if close_5 >= prior_high and vr_5 >= 1.2:
                    has_5m_pass = True
                    phase_details.append(f"5m Phase D Trigger Active! (Breakout Close ₹{close_5:.2f} ≥ ₹{prior_high:.2f} | 5m Vol {vr_5:.2f}x ≥ 1.2x)")
                else:
                    phase_details.append(f"5m Phase D Pending (Close ₹{close_5:.2f} vs Breakout ₹{prior_high:.2f} | 5m Vol {vr_5:.2f}x)")
    except Exception as exc:
        # [FIX MTF-24] Log diagnostic failures instead of silently discarding
        logger.exception(f"{symbol}: diagnostic 5m evaluation failed: {exc}")
        phase_details.append("5m Phase D unavailable due to processing error")

    if has_30m_pass and has_15m_pass and has_5m_pass:
        status_tag = "CORE MET (Phase A+B+C+D Trigger Ready)"
    elif has_30m_pass and has_15m_pass:
        status_tag = "CORE MET (Phase A+B+C Entry Ready)"
    elif has_30m_pass:
        status_tag = "CORE MET (Phase A+B Squeeze Armed)"
    else:
        status_tag = "CORE MET (1H Setup Approved)"

    from sl_target_helper import compute_sl_and_target
    sl_result = compute_sl_and_target(entry_price=close_price, atr=atr_val, mode="MULTI_TF", ticker=ticker)

    # ── PER-STOCK TERMINAL TELEMETRY DUMP (Section 4 & 8) ──
    try:
        from scanner_telemetry import DecisionContext, telemetry_engine
        ctx = DecisionContext(symbol=symbol, scanner_name="MULTI_TF")
        ctx.capture_raw_market(
            open_p=_safe_float(latest.get("Open")),
            high_p=_safe_float(latest.get("High")),
            low_p=_safe_float(latest.get("Low")),
            close_p=_safe_float(latest.get("Close")),
            volume=_safe_float(latest.get("Volume"))
        )
        ctx.capture_indicators(
            rsi=_safe_float(latest.get("RSI")),
            ema20=e20,
            sma50=s50,
            sma200=s200,
            adx=adx_val,
            atr=atr_val
        )
        ctx.capture_score("TOTAL", score, 100.0)
        ctx.capture_sl_target(close_price, sl_result.get("stop_loss", 0.0), sl_result.get("target_1", 0.0))
        
        ctx.finalize(decision="SELECTED", primary_reason="ALL_MULTI_TF_PHASES_PASSED")
        telemetry_engine.emit_terminal(ctx)
    except Exception as telemetry_err:
        logger.debug(f"Telemetry recording skipped: {telemetry_err}")

    return {
        "status": status_tag,
        "reasons": phase_details,
        "score": score,
        "qualified": True,
        "entry_price": close_price,
        "stop_loss": sl_result.get("stop_loss"),
        "target_1": sl_result.get("target_1"),
        "target_2": sl_result.get("target_2"),
        "target_3": sl_result.get("target_3"),
        "target_4": sl_result.get("target_4"),
        "atr_20": atr_val
    }

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
                # [VERSION: MEMORY_OPTIMIZATION_v1.0] Removed redundant .copy() to save memory
                return df.iloc[:-1]
    except Exception as e:
        logger.warning(f"Failed to strip forming candle: {e}")
        pass
    return df


from opportunity_manager import OpportunityManager
from trade_ranking_engine import TradeRankingEngine
from macro_utils import MarketRegimeEngine, get_macro_regime, get_nifty_20d_return
from strategy_policy import StrategyPolicyEngine

# [VERSION: PERF_PROFILER_v1.0] Wrap Phase A so every run reports wall-clock time,
# RSS delta, and any top-level exception via structured log — no behavioral change.
@profile_timing("multi_tf_scanner.run_hourly_phase", log_to_file=True)
def run_hourly_phase(is_test_mode=False, run_once=False, session=None, run_ctx=None):
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
    
    # 1. Get targeted intraday timing universe (Open Alerts + Wealth + Master)
    watchlist = get_mtf_target_universe()
    if watchlist.empty:
        logger.warning("No target universe found for Multi-TF scanner.")
        return {"fetched": 0, "total": 0, "stale": 0}

    # [VERSION: SCANNER_DIAG_LOG_v1.0] Watchlist fingerprint for cross-run comparison
    import hashlib
    _wl_stocks = sorted(watchlist["Stock"].tolist())
    _wl_hash = hashlib.md5("|".join(_wl_stocks).encode()).hexdigest()[:12]
    logger.info(f"📋 [MULTI_TF] Targeted universe fingerprint: {len(watchlist)} stocks | hash={_wl_hash}")

    import gc
    BATCH_SIZE = int(os.environ.get("MULTI_TF_FETCH_BATCH_SIZE", "200"))
    
    stale_1h = 0
    fetched_count = 0
    
    # ── FUNNEL STATS: measure how many stocks pass each gate ──────────────
    funnel = {"total": 0, "data_ok": 0, "indicators_ok": 0, "price_filtered": 0, "price_ok": 0,
              "ema_only_pass": 0, "adx_only_pass": 0, "ema_and_adx_pass": 0, "dist_pass": 0, "approved": 0, "reduced_trend_fallback": 0}
              
    logger.info(f"📥 Processing 1H phase in chunks of {BATCH_SIZE}...")
    
    from memory_profiler import chunk_iterable, BatchMemoryTracker
    total_batches = (len(watchlist) + BATCH_SIZE - 1) // BATCH_SIZE

    # [VERSION: BULK_PREFETCH_OPT_v1.0] Single-pass bulk fetch for all watchlist symbols.
    # PriceCache handles provider-level batching internally while populating per-symbol RAM cache.
    # Requesting 10d (50+ 1H bars) is sufficient for 50-bar indicator calculation while reducing network payload by 80%.
    logger.info(f"📥 [MULTI_TF] Bulk pre-fetching 1H data (10d) for {len(watchlist)} symbols...")
    all_1h_ticker_data = fetch_watchlist_data(watchlist, period="10d", interval="1h", requester="MULTI_TF_1H")

    _last_hb = time.monotonic()
    for batch_num, chunk_df in enumerate(chunk_iterable(watchlist, BATCH_SIZE), start=1):
        if run_ctx and (time.monotonic() - _last_hb) >= 10.0:
            try:
                run_ctx.heartbeat()
                _last_hb = time.monotonic()
            except Exception: pass
        with BatchMemoryTracker(SCANNER_MULTI_TF, batch_num, total_batches, len(chunk_df), collect_gc=True) as tracker:
            
            # Slice chunk symbols from bulk pre-fetched dictionary
            chunk_symbols = chunk_df["Stock"].tolist()
            ticker_data = {s: all_1h_ticker_data[s] for s in chunk_symbols if s in all_1h_ticker_data}
            
            import pandas as pd
            valid_fetches = sum(1 for v in ticker_data.values() if isinstance(v, pd.DataFrame) and not v.empty)
            fetched_count += valid_fetches
            rows_fetched = sum(len(df) for df in ticker_data.values() if df is not None and not isinstance(df, ProviderResult))
            tracker.mark_fetch_complete(row_count=rows_fetched)
        
        # 2. Process chunk
            batch_upserts = []
            for row_tuple in chunk_df.itertuples(index=False):
                try:
                    row = row_tuple._asdict() if hasattr(row_tuple, '_asdict') else (row_tuple if isinstance(row_tuple, dict) else {})
                    symbol = row.get("Stock", "UNKNOWN")
                    category = row.get("Category", "MIDCAP")
                    funnel["total"] += 1
            
                    df = ticker_data.get(symbol)
                    # [VERSION: MTF_BAR_LIMIT_FIX] Reduced from 200 to 50 to allow YFinance fallback data to process safely
                    if df is None or df.empty or len(df) < 50:
                        continue
                
                    if getattr(df, 'attrs', {}).get('is_stale') == True:
                        logger.debug(f"⏭️ Skipping {symbol} (1H scan) due to stale data.")
                        stale_1h += 1
                        continue
    
                    df = strip_forming_candle(df, 60, datetime.now(IST))
                    if df is None or df.empty or len(df) < 2:
                        continue
                    # [PERFORMANCE_FIX] Pre-calculated by price_cache.py
                    # df = apply_indicators(df, timeframe="1h")
                    if df is None or df.empty:
                        # rejections["indicator_fail"] += 1
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
            
                    # Any uncomputed core indicator = hard skip (not silently pass)
                    if any(v is None for v in (e9, e20, s50, adx_val, prior_high)):
                        logger.debug(f"⏭️ {symbol} skipped — core indicator NaN/missing "
                                     f"(e9={e9}, e20={e20}, s50={s50}, adx={adx_val}, prior_high={prior_high})")
                        continue
                        
                    is_fallback = False
                    if s200 is None or pd.isna(s200):
                        is_fallback = True
                        funnel["reduced_trend_fallback"] += 1
            
                    funnel["indicators_ok"] += 1
            
                    if prior_high <= 0:
                        continue
    
                    funnel["price_ok"] += 1
                
                    dist_to_breakout = (prior_high - close) / prior_high
            
                    # Hourly Trend Permission Logic: 9 > 20 > 50, Price > 200, ADX > 20
                    # AND price must be within -2.0% (above) to +5.0% (below) of the breakout level
                    trend_type = "NONE"
                    if not is_fallback:
                        strict_trend = (e9 > e20 and e20 > s50 and close > s200)
                        recovery_trend = (e9 > e20 and close > s50 and close > s200 and e20 <= s50)
                        ema_ok = strict_trend or recovery_trend
                        if strict_trend: trend_type = "STRICT"
                        elif recovery_trend: trend_type = "RECOVERY"
                    else:
                        # Reduced trend gate for 50-199 bar symbols
                        strict_trend = (e9 > e20 and e20 > s50)
                        recovery_trend = (e9 > e20 and close > s50 and e20 <= s50)
                        ema_ok = strict_trend or recovery_trend
                        if strict_trend: trend_type = "STRICT"
                        elif recovery_trend: trend_type = "RECOVERY"
                        
                    from config import ADX_MIN_THRESHOLD
                    adx_ok = adx_val >= ADX_MIN_THRESHOLD
                    # Widened distance gate to allow stocks from -6% (above) to +8% (below) breakout level
                    dist_ok = -0.06 <= dist_to_breakout <= 0.08
            
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
                            # [FIX MTF-18] Removed force=True — the SQL CASE in batch_upsert
                            # already protects SETUP_ARMED/ENTRY_READY/TRADE_ACTIVE when
                            # force=FALSE. Using force=True caused Phase A to silently
                            # downgrade stocks back to HOURLY_APPROVED every 15 minutes,
                            # breaking the 1h→30m→15m→5m ladder.
                            batch_upserts.append({
                                'symbol': symbol,
                                'category': category,
                                'current_state': "HOURLY_APPROVED",
                                'h1_status': "PASSED",
                                'breakout_level': prior_high,
                                'clear_context': True,
                                'trigger_level': prior_high,
                                'signal_timestamp': now_dt.isoformat(),
                                'expires_at': end_of_session.isoformat(),
                                'timeframe': "1h",
                                'force': False
                            })
                        funnel["approved"] += 1
                        logger.info(f"📍 PICKED [MULTI-TF: HOURLY_APPROVED]: {symbol} @ ₹{close:.2f} (Dist: {dist_to_breakout*100:.2f}%, ADX: {adx_val:.1f}, Trend: {trend_type})")
                    else:
                        logger.debug(f"REJECTION: {symbol} (Phase: 1H_TREND_PERMISSION, Reason: Failed 1H EMA/ADX/Distance gate (ema_ok={ema_ok}, adx_ok={adx_ok}, dist_ok={dist_ok}, Trend={trend_type}))")
    
                except Exception as e:
                    logger.exception(f"Fault isolation caught exception for Phase A: {e}")
                    continue
            
            if batch_upserts:
                try:
                    batch_upsert_breakout_watchlist(batch_upserts)
                except Exception as e:
                    logger.exception(f"Failed to execute batch upsert for Phase A: {e}")
                
            logger.info(f"⏳ [MULTI-TF SCANNER] Evaluated Batch {batch_num}/{total_batches} ({min(batch_num * BATCH_SIZE, len(watchlist))}/{len(watchlist)} stocks) | Approved so far: {funnel['approved']}")
        del ticker_data
        locals().pop('df', None)
            
    # Post-run requirement check
    required_count = int(len(watchlist) * 0.70)
    if fetched_count < required_count:
        logger.warning(f"⚠️ 1H data fetch returned {fetched_count}/{len(watchlist)} symbols (70% minimum required). Phase A results may be incomplete.")
        return {"fetched": fetched_count, "total": len(watchlist), "stale": 0, "approved": 0, "abort": True}
    else:
        logger.info(f"✅ Successfully fetched {fetched_count} symbols for 1H hourly phase")

    summary_lines = [
        "======================================================================",
        "=== [MULTI-TF PHASE A PIPELINE SUMMARY] ===",
        "======================================================================",
        f"Total Evaluated       : {funnel['total']} symbols",
        f"Hourly Approved       : {funnel['approved']}",
        "Funnel Stage Breakdown:",
        f"  • Data Available      : {funnel['data_ok']}",
        f"  • Indicators OK       : {funnel['indicators_ok']}",
        f"  • EMA Filter Pass     : {funnel['ema_only_pass']}",
        f"  • ADX Filter Pass     : {funnel['adx_only_pass']}",
        f"  • Distance Gate Pass  : {funnel['dist_pass']}",
        "======================================================================"
    ]
    logger.info("\n".join(summary_lines))

    if stale_1h > 0:
        logger.info(f"📊 Stale data summary | 1H: {stale_1h}")

    # Defensive memory purge after Phase A execution
    try:
        from memory_profiler import run_purge_with_telemetry
        run_purge_with_telemetry("MultiTF Phase A Complete")
    except Exception as me:
        logger.debug(f"Phase A memory purge failed: {me}")

    return {"fetched": fetched_count, "total": len(watchlist), "stale": stale_1h, "approved": funnel.get("approved", 0), "save_failures": 0}

# [VERSION: PERF_PROFILER_v1.0] Wrap Phase B/C/D so each sub-hourly ladder cycle
# reports its own timing + memory profile separately from Phase A.
@profile_timing("multi_tf_scanner.run_lower_tf_phase", log_to_file=True)
def run_lower_tf_phase(regime_ctx=None, is_test_mode=False, run_once=False, session=None, run_ctx=None):
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

    regime_str = regime_ctx.get("trend", "NEUTRAL") if regime_ctx else "NEUTRAL"
    # Fetch pledge map to pass to scoring engine
    try:
        from database import get_pledge_map, get_latest_weights
        symbols = list(set(i["symbol"] for i in active_items))
        pledge_map = get_pledge_map(symbols)
        logger.info(f"🛡️ Fetched pledge data for {len(pledge_map)} symbols")
        
        latest_db_weights = get_latest_weights(regime_str)
        bayesian_weights = latest_db_weights.get("weights") if latest_db_weights else None
    except Exception as e:
        logger.exception("Failed to fetch pledge map or weights")
        pledge_map = {}
        bayesian_weights = None

    # [FIX MTF-20] Fetch every required timeframe for every symbol that might advance
    # during this cycle. Without this, a symbol promoted Phase A→B in this cycle has
    # no 15m data for Phase C, and one promoted Phase B→C has no 5m data for Phase D.
    # This forces a 3-cycle wait (or the symbol gets reset by Phase A before reaching D).
    ladder_symbols = list({
        i["symbol"] for i in active_items
        if i["current_state"] in ("HOURLY_APPROVED", "SETUP_ARMED", "ENTRY_READY")
    })
    # =====================================================================================
    # RULE 67 MANDATORY CHANGE-RATIONALE:
    # - Optimized needs_5m bulk pre-fetch to include HOURLY_APPROVED candidates alongside SETUP_ARMED and ENTRY_READY.
    # - Rationale: Pre-fetching 5m data in parallel bulk chunking at the start of the lower TF phase eliminates
    #   1-by-1 on-demand network calls during symbol processing loop, cutting lower TF scan time from >370s to <10s.
    # =====================================================================================
    needs_30m = list({i["symbol"] for i in active_items if i["current_state"] in ("HOURLY_APPROVED", "SETUP_ARMED", "ENTRY_READY")})
    needs_15m = list({i["symbol"] for i in active_items if i["current_state"] in ("HOURLY_APPROVED", "SETUP_ARMED", "ENTRY_READY")})
    needs_5m  = list({i["symbol"] for i in active_items if i["current_state"] in ("HOURLY_APPROVED", "SETUP_ARMED", "ENTRY_READY")})

    
    import concurrent.futures
    import pandas as pd

    def _fetch_tf(tf_label, period_val, interval_val, symbols_list):
        if not symbols_list:
            return {}
        try:
            res = fetch_watchlist_data(pd.DataFrame({"Stock": symbols_list}), period=period_val, interval=interval_val, requester=f"MULTI_TF_{tf_label}")
            return res if res is not None else {}
        except Exception as _e:
            logger.warning(f"Parallel fetch warning for {tf_label}: {_e}")
            return {}

    logger.info(f"⚡ [MULTI_TF] Pre-fetching 30m ({len(needs_30m)}), 15m ({len(needs_15m)}), and 5m ({len(needs_5m)}) timeframes...")
    _t_start_fetch = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="MTF_Fetch") as fetch_exec:
        f_30m = fetch_exec.submit(_fetch_tf, "30m", "5d", "30m", needs_30m)
        f_15m = fetch_exec.submit(_fetch_tf, "15m", "3d", "15m", needs_15m)
        f_5m  = fetch_exec.submit(_fetch_tf, "5m", "2d", "5m", needs_5m)
        data_30m = f_30m.result()
        if run_ctx: run_ctx.heartbeat()
        data_15m = f_15m.result()
        if run_ctx: run_ctx.heartbeat()
        data_5m  = f_5m.result()
        if run_ctx: run_ctx.heartbeat()
    _t_fetch_intraday = time.perf_counter() - _t_start_fetch
    _t_30m = _t_fetch_intraday
    _t_15m = _t_fetch_intraday
    _t_5m_1d = _t_fetch_intraday

    # Load 1D daily data directly from local RAM/disk price cache (zero network requests)
    data_daily = {}
    if needs_5m:
        if session is not None:
            data_daily = {
                s: session.get(s).ohlcv_df
                for s in needs_5m
                if session.get(s) is not None and getattr(session.get(s), "ohlcv_df", None) is not None
            }
        else:
            from price_cache import get_cached_df
            for s in needs_5m:
                cdf = get_cached_df(s, "1d", "1y")
                if cdf is not None and not cdf.empty:
                    data_daily[s] = cdf
            missing_1d = [s for s in needs_5m if s not in data_daily]
            if missing_1d:
                res_1d = _fetch_tf("1d", "1y", "1d", missing_1d)
                data_daily.update(res_1d)
        
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
    stale_30m = 0
    stale_15m = 0
    stale_5m = 0
    db_save_failures = 0
    ist_now = datetime.now(IST)

    # [VERSION: MULTI_TF_DELIVERY_FIX_v1.0] Fetch latest delivery data map for delivery_pct enrichment
    delivery_map = {}
    try:
        from delivery_data import fetch_latest_available_delivery_data
        delivery_map, _ = fetch_latest_available_delivery_data(ist_now.date())
    except Exception as _del_e:
        logger.warning(f"⚠️ Could not fetch delivery data in Multi-TF: {_del_e}")

    # Instantiate the in-memory opportunity pool for this scan cycle
    opportunity_manager = OpportunityManager(policy=regime_ctx.get("policy", {}) if regime_ctx else {})
    end_of_session = ist_now.replace(hour=15, minute=30, second=0, microsecond=0)
    if ist_now > end_of_session:
        end_of_session = ist_now + timedelta(minutes=15)

    
    # Funnel stats for Phase B/C/D
    lower_funnel = {"armed_candidates": 0, "bb_pass": 0, "armed": 0,
                    "entry_candidates": 0, "ema15_pass": 0, "entry_ready": 0,
                    "trigger_candidates": 0, "pb_fail_engulf": 0, "pb_fail_vol": 0, 
                    "rr_rejections": 0, "triggered": 0, "demoted": 0}

    profiler_proc2 = MemoryProfiler("MTF Process Symbols")
    profiler_proc2.__enter__()
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    _batch_lock = threading.Lock()
    def _process_item(item):
        nonlocal stale_30m, stale_15m, stale_5m, db_save_failures
        try:
            symbol = item["symbol"]
            state = item["current_state"]
            cat = item["category"]
            breakout_level = item["breakout_level"] or 0
            # [FIX-M2] Track which phases actually passed in THIS cycle (independent of persisted state).
            # Using `state` after it is set to TRADE_ACTIVE always grants all phase bonuses incorrectly.
            passed_phase_b = False  # Set True only when Phase B (30m squeeze) is confirmed this cycle
            passed_phase_c = False  # Set True only when Phase C (15m alignment) is confirmed this cycle

            if breakout_level <= 0:
                return

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
                        with _batch_lock:
                            stale_30m += 1
                        return

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
                                # [VERSION: BATCH_COOLDOWN_v1.0] Accumulate cooldown instead of calling DB inside parallel worker.
                                # mark_breakout_watchlist_cooldown was issuing a live UPDATE+commit per symbol.
                                # Now collected in cooldown_list and flushed post-loop to avoid per-worker DB round-trips.
                                with _batch_lock:
                                    batch_upsert_list.append({'symbol': symbol, 'category': cat, 'current_state': "HOURLY_APPROVED", 'clear_context': True, 'force': True})
                                    cooldown_list.append((symbol, "HOURLY_APPROVED", 2))
                            state = "HOURLY_APPROVED"
                            with _batch_lock:
                                lower_funnel["demoted"] += 1
                            logger.info(f"⚠️ {symbol} fell >3% from resistance. Downgraded to HOURLY_APPROVED.")
                        elif is_expired:
                            if not is_test_mode:
                                with _batch_lock:
                                    batch_upsert_list.append({'symbol': symbol, 'category': cat, 'current_state': "HOURLY_APPROVED", 'clear_context': True, 'force': True})
                            state = "HOURLY_APPROVED"
                            with _batch_lock:
                                lower_funnel["demoted"] += 1
                            logger.info(f"⏳ {symbol} {item['current_state']} expired (stale >4h + drifted >1.5%). Downgraded.")

            # ── Phase B (30m): HOURLY_APPROVED → SETUP_ARMED ─────────────────
            if state == "HOURLY_APPROVED" and data_30m.get(symbol) is not None and ok_30m:
                with _batch_lock:
                    lower_funnel["armed_candidates"] += 1
                df = data_30m.get(symbol)
                if df is None:
                    logger.debug(f"⏭️ {symbol} Phase B: no data returned from fetch")
                if df is not None:
                    if getattr(df, 'attrs', {}).get('is_stale') == True:
                        logger.debug(f"⏭️ Skipping {symbol} (30m upgrade check) due to stale data.")
                        with _batch_lock:
                            stale_30m += 1
                        return

                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 12] Added defensive checks on strip_forming_candle return value
                    df = strip_forming_candle(df, 30, ist_now)
                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 11] Added explicit debug logging for empty dataframes rather than silently skipping
                    if df is None or df.empty or len(df) < 2:
                        logger.debug(f"⏭️ {symbol} phase B: insufficient 30m data")
                        return
                    # [PERFORMANCE_FIX] Pre-calculated by price_cache.py
                    # df = apply_indicators(df, timeframe="30m")
                    if df.empty:
                        logger.debug(f"⏭️ {symbol} phase B: indicators failed to compute")
                        return
                    latest = df.iloc[-1]
                    # [VERSION: MTF_BB_TIMING_FIX] Evaluate consolidation on previous candle (iloc[-2])
                    # so a breakout on the current candle doesn't invalidate its own base via BB expansion.
                    prev = df.iloc[-2] if len(df) >= 2 else latest
                    _raw_bb = prev.get("BB_WIDTH_PCTILE", 1.0)
                    bb_pctile = float(_raw_bb) if pd.notna(_raw_bb) else 1.0
            
                    close = _safe_float(latest.get("Close"))
                    dist_to_breakout = (breakout_level - close) / breakout_level
            
                    # Add 30m Volume Baseline for Fast Breakout Override
                    vol_ratio = 1.0
                    if "Volume" in latest and len(df) > 1:
                        mean_vol = df["Volume"].iloc[-21:-1].mean() if len(df) >= 22 else df["Volume"].iloc[:-1].mean()
                        mean_vol = max(float(mean_vol or 1.0), 1.0)
                        vol_ratio = _safe_float(latest.get("Volume")) / mean_vol
            
                    # [FIX P2-5] Changed from "squeeze now" to "squeeze recently released".
                    # Previously required bb_pctile < 0.45 on the immediately prior candle.
                    # Now looks back 6-8 bars for a recent squeeze (BB Width Pctile < 0.45)
                    # that is now expanding (current bb_pctile > prev bb_pctile), which
                    # captures the coiling→expansion transition more reliably.
                    bb_pctile_recent_squeeze = False
                    lookback_squeeze = min(8, len(df) - 1)
                    for _lb in range(1, lookback_squeeze + 1):
                        _idx = len(df) - 1 - _lb
                        if _idx < 0:
                            break
                        _raw_p = df.iloc[_idx].get("BB_WIDTH_PCTILE", 1.0)
                        _p = float(_raw_p) if pd.notna(_raw_p) else 1.0
                        if _p < 0.45:
                            bb_pctile_recent_squeeze = True
                            break

                    is_consolidation = (bb_pctile_recent_squeeze or bb_pctile < 0.50) and (-0.05 <= dist_to_breakout <= 0.06)
                    is_fast_breakout = dist_to_breakout < -0.005 and vol_ratio > 1.1
            
                    if is_consolidation or is_fast_breakout:
                        with _batch_lock:
                            lower_funnel["bb_pass"] += 1
                        swing_low = float(latest.get("SWING_LOW", close))
                        ema20 = float(latest.get("EMA20", close))
                
                        ctx_json = json.dumps({"last_state_change_at": ist_now.strftime('%Y-%m-%d %H:%M:%S')})
                        now_iso = ist_now.isoformat()
                        expires_iso = min(ist_now + timedelta(minutes=60), end_of_session).isoformat()
                
                        if not is_test_mode:
                            with _batch_lock:
                                batch_upsert_list.append({
                                    'symbol': symbol, 'category': cat, 'current_state': "SETUP_ARMED", 'm30_status': "PASSED",
                                    'trigger_level': breakout_level,
                                    'invalidation_level': min(swing_low, ema20),
                                    'max_extension_atr': 0.8,
                                    'buffer_pct': 0.0015,
                                    'armed_at': ist_now.strftime('%Y-%m-%d %H:%M:%S'),
                                    'context_json': ctx_json,
                                    'signal_timestamp': now_iso,
                                    'expires_at': expires_iso,
                                    'timeframe': "30m"
                                })
                        with _batch_lock:
                            lower_funnel["armed"] += 1
                        state = "SETUP_ARMED"
                        passed_phase_b = True  # [FIX-M2] Phase B confirmed this cycle — eligible for +10 bonus
                        logger.info(f"🎯 {symbol} upgraded to SETUP_ARMED (bb_pctile={bb_pctile:.2f}, dist={dist_to_breakout*100:.2f}%).")

            # ── Phase C (15m): SETUP_ARMED → ENTRY_READY ─────────────────────
            if state == "SETUP_ARMED" and ok_15m:
                df = data_15m.get(symbol)
                if df is None:
                    # Single-stock 15m fallback if candidate reached SETUP_ARMED during this cycle
                    try:
                        res_15m = fetch_watchlist_data(pd.DataFrame({"Stock": [symbol]}), period="3d", interval="15m", requester=f"MTF_ON_DEMAND_15M_{symbol}")
                        df = res_15m.get(symbol) if isinstance(res_15m, dict) else None
                    except Exception:
                        df = None
                if df is not None:
                    with _batch_lock:
                        lower_funnel["entry_candidates"] += 1
                if df is None:
                    logger.debug(f"⏭️ {symbol} Phase C: no data returned from fetch")
                if df is not None:
                    if getattr(df, 'attrs', {}).get('is_stale') == True:
                        logger.debug(f"⏭️ Skipping {symbol} (15m entry check) due to stale data.")
                        with _batch_lock:
                            stale_15m += 1
                        return

                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 12] Defensive check against strip_forming_candle None return
                    df = strip_forming_candle(df, 15, ist_now)
                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 11] Added explicit debug logging for empty dataframes rather than silently skipping
                    if df is None or df.empty or len(df) < 2:
                        logger.debug(f"⏭️ {symbol} phase C: insufficient 15m data")
                        return
                    # [PERFORMANCE_FIX] Pre-calculated by price_cache.py
                    # df = apply_indicators(df, timeframe="15m")
                    if df.empty:
                        logger.debug(f"⏭️ {symbol} phase C: indicators failed to compute")
                        return

                    latest = df.iloc[-1]
                    e9_15 = float(latest.get("EMA9", 0) or 0)
                    e20_15 = float(latest.get("EMA20", 0) or 0)
                    close = _safe_float(latest.get("Close"))

                    if e9_15 <= 0 or e20_15 <= 0:
                        return

                    dist_to_breakout = (breakout_level - close) / breakout_level

                    # 15m micro-alignment: EMA9 >= EMA20 or Close >= EMA20, price near level (aligned with Phase A bounds)
                    ema_tolerance = 0.998 * e20_15
                    condition_ema = e9_15 >= ema_tolerance
                    condition_close = close >= e20_15
                    
                    if (condition_ema or condition_close) and (-0.06 <= dist_to_breakout <= 0.06):
                        selected_condition = "EMA_TOLERANCE | CLOSE_ABOVE_EMA20" if (condition_ema and condition_close) else ("EMA_TOLERANCE" if condition_ema else "CLOSE_ABOVE_EMA20")
                        
                        with _batch_lock:
                            lower_funnel["ema15_pass"] += 1
                        ctx_json = json.dumps({
                            "last_state_change_at": ist_now.strftime('%Y-%m-%d %H:%M:%S'),
                            "15m_e9": round(e9_15, 2),
                            "15m_e20": round(e20_15, 2),
                            "selected_condition": selected_condition
                        })
                        now_iso = ist_now.isoformat()
                        expires_iso = min(ist_now + timedelta(minutes=30), end_of_session).isoformat()
                
                        if not is_test_mode:
                            with _batch_lock:
                                batch_upsert_list.append({
                                    'symbol': symbol, 'category': cat, 'current_state': "ENTRY_READY",
                                    'm15_status': "PASSED",
                                    'context_json': ctx_json,
                                    'signal_timestamp': now_iso,
                                    'expires_at': expires_iso,
                                    'timeframe': "15m"
                                })
                        with _batch_lock:
                            lower_funnel["entry_ready"] += 1
                        state = "ENTRY_READY"
                        passed_phase_c = True  # [FIX-M2] Phase C confirmed this cycle — eligible for +5 bonus
                        logger.info(f"🟡 {symbol} promoted to ENTRY_READY "
                                    f"[gate_type=COMPOSITE e9={e9_15:.2f} e20={e20_15:.2f} "
                                    f"ema_threshold={ema_tolerance:.2f} close={close:.2f} "
                                    f"condition_ema={condition_ema} condition_close={condition_close} "
                                    f"selected_condition={selected_condition} dist={dist_to_breakout*100:.2f}%]")

            # ── Phase D (5m): ENTRY_READY → TRADE_ACTIVE (Final Trigger) ─────
            # Late-Session Entry Cutoff: Do not generate new intraday entries after 14:15 IST
            if ist_now.time() >= datetime.strptime("14:15", "%H:%M").time() and not run_once:
                logger.debug(f"⏳ Late-session cutoff (14:15 IST) reached — skipping new Phase D entry for {symbol}")
                return

            if state == "ENTRY_READY" and ok_5m:
                df = data_5m.get(symbol)
                if df is None:
                    # [VERSION: MTF_ON_DEMAND_5M_v2.0] Single-stock 5m fallback: If symbol reached ENTRY_READY 
                    # during this same cycle (and was not pre-fetched in bulk needs_5m), fetch 5m data on-demand
                    # ONLY for this candidate (~0.5s cost), avoiding 15+ minute bulk pre-fetch delays across all 130 stocks.
                    try:
                        res_5m = fetch_watchlist_data(pd.DataFrame({"Stock": [symbol]}), period="2d", interval="5m", requester=f"MTF_ON_DEMAND_{symbol}")
                        df = res_5m.get(symbol) if isinstance(res_5m, dict) else None
                    except Exception:
                        df = None

                if df is not None:
                    with _batch_lock:
                        lower_funnel["trigger_candidates"] += 1
                if df is None:
                    logger.debug(f"⏭️ {symbol} Phase D: no data returned from fetch")
                if df is not None:
                    if getattr(df, 'attrs', {}).get('is_stale') == True:
                        logger.debug(f"⏭️ Skipping {symbol} (5m trigger check) due to stale data.")
                        with _batch_lock:
                            stale_5m += 1
                        return

                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 12] Defensive check against strip_forming_candle None return
                    df = strip_forming_candle(df, 5, ist_now)
                    # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 11] Added explicit debug logging for empty dataframes rather than silently skipping
                    if df is None or df.empty or len(df) < 2:
                        logger.debug(f"⏭️ {symbol} phase D: insufficient 5m data")
                        return
                    daily_df = data_daily.get(symbol)
                    # =====================================================================================
                    # RULE 67 MANDATORY CHANGE-RATIONALE:
                    # - Guarded apply_indicators(5m) call so pre-computed indicators from price_cache.py are reused.
                    # - Rationale: Eliminates 181s (3 min) of redundant pandas rolling indicator calculations during lower TF evaluation loop.
                    # =====================================================================================
                    if "EMA9" not in df.columns or "ATR20" not in df.columns:
                        df = apply_indicators(df, timeframe="5m", daily_ohlc=daily_df)
                    if df.empty or "EMA9" not in df.columns or "ATR20" not in df.columns or "Volume" not in df.columns:
                        logger.debug(f"⏭️ {symbol} phase D: missing required 5m indicators")
                        return
                
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
                        return

                    # [FIX MTF-14] Over-extension cap: use daily ATR (or 3% fallback) to measure
                    # extension against the breakout level. The 5m ATR is ~1/17th of daily ATR,
                    # so using it against a daily-defined breakout level creates a cap of ~0.2%
                    # — practically every stock that entered through Phase C (+2.5% band) fails.
                    # The extension gate must be consistent with the admission band.
                    ref_atr = 0.0
                    if daily_df is not None and not daily_df.empty and "ATR" in daily_df.columns:
                        ref_atr = _safe_float(daily_df["ATR"].iloc[-1])
                    if ref_atr <= 0:
                        # Fallback: scale 5m ATR up ~17x (5-min bars per day) or use 2% of price
                        ref_atr = max(atr20 * 17.0, close * 0.02)

                    # Micro-buffer uses 5m ATR (appropriate for intraday noise filtering)
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

                    # [FIX MTF-14] Extension limit uses daily-scale ref_atr (consistent with Phase C admission band)
                    if close > trigger_level + (max_ext_atr * ref_atr):
                        dist_atr = (close - trigger_level) / ref_atr if ref_atr > 0 else 0
                        logger.info(f"🚫 {symbol} PhaseD Reject | Reason=PD01_OVER_EXTENDED | Trigger={trigger_level:.2f} Close={close:.2f} PrevHigh={float(prev['High']):.2f} RefATR={ref_atr:.2f} ATR_Extension={dist_atr:.2f} VolRatio={vol_ratio:.2f} ClosePos={close_position:.2f} Pattern=N/A")
                        return

                    is_ready = False
                    trigger_type = ""
            
                    # Thrust/Continuation Trigger
                    # Price breaks local high while still close to level, with mandatory volume expansion (>= 1.25x)
                    min_thrust_vol = float(MULTI_TF_CONFIG.get("MIN_VOLUME_RATIO", 1.25))
                    if close > float(prev["High"]) and close > (trigger_level + buffer_val) and vol_ratio >= min_thrust_vol:
                        if close_position >= 0.60:
                            is_ready = True
                            trigger_type = "thrust"
                
                    # [VERSION: MULTI_TF_PATCH_v1.2] High-Conviction Pullback Trigger
                    # Breakout level or EMA9 is defended, requiring minimum volume expansion (>= 1.15x) and strong close (>= 0.60)
                    defense_level = trigger_level
                    if not is_ready and low <= defense_level + (0.15 * atr20):
                        trigger_mode = MULTI_TF_CONFIG.get("PULLBACK_TRIGGER_MODE", "PREVIOUS_CLOSE")
                    
                        if trigger_mode == "PREVIOUS_CLOSE":
                            c_engulf = close > float(prev["Close"])
                        elif trigger_mode == "PREVIOUS_BODY":
                            c_engulf = close > max(float(prev["Open"]), float(prev["Close"]))
                        elif trigger_mode == "PREVIOUS_HIGH":
                            c_engulf = close > float(prev["High"])
                        elif trigger_mode == "PREVIOUS_OPEN":
                            c_engulf = close > float(prev["Open"])
                        elif trigger_mode == "INSIDE_BAR":
                            mother = df.iloc[-3]
                            is_inside = (
                                float(prev["High"]) <= float(mother["High"])
                                and float(prev["Low"]) >= float(mother["Low"])
                            )
                            c_engulf = is_inside and close > float(prev["High"])
                        else:
                            c_engulf = close > float(prev["Close"])
                        
                        # High-Conviction Trigger (Reject low-volume < 1.15x traps)
                        # 1. High-Volume Pullback: Volume ratio >= 1.15 AND Close position >= 0.60
                        # 2. Strong Surge Pullback: Volume ratio >= 1.25 AND Close position >= 0.55 AND price holds trigger level
                        c_std_trig = (vol_ratio >= 1.15 and close_position >= 0.60)
                        c_strong_price_trig = (vol_ratio >= 1.25 and close_position >= 0.55 and close >= trigger_level)
                        
                        if close >= trigger_level and c_engulf and close > open_px and (c_std_trig or c_strong_price_trig):
                            is_ready = True
                            trigger_type = "pullback"

                    if not is_ready:
                        # Log reasons only if stock has touched/entered the trigger zone
                        if close >= (trigger_level - buffer_val) or low <= trigger_level + (0.15 * atr20):
                            # Boolean evaluations for decision trace
                            trigger_mode = MULTI_TF_CONFIG.get("PULLBACK_TRIGGER_MODE", "PREVIOUS_CLOSE")
                            if trigger_mode == "PREVIOUS_CLOSE":
                                c_engulf = close > float(prev["Close"])
                            elif trigger_mode == "PREVIOUS_BODY":
                                c_engulf = close > max(float(prev["Open"]), float(prev["Close"]))
                            elif trigger_mode == "PREVIOUS_HIGH":
                                c_engulf = close > float(prev["High"])
                            elif trigger_mode == "PREVIOUS_OPEN":
                                c_engulf = close > float(prev["Open"])
                            else:
                                c_engulf = close > float(prev["Close"])
                        
                            c_vol = vol_ratio >= 0.80
                            c_close_pos = close_position >= 0.50
                            c_bull_body = close > open_px
                            c_defended = low <= trigger_level + (0.15 * atr20)
                            c_above_trig = close >= trigger_level
                        
                            reasons = []
                            if not c_engulf:
                                reasons.append("PD02")
                                with _batch_lock:
                                    lower_funnel["pb_fail_engulf"] += 1
                            if not c_vol:
                                reasons.append("PD03")
                                with _batch_lock:
                                    lower_funnel["pb_fail_vol"] += 1
                            if not c_close_pos:
                                reasons.append("PD04")
                            if not reasons:
                                reasons.append("PD05")
                            
                            trace = f"Engulf={c_engulf} BullBody={c_bull_body} Defended={c_defended} AboveTrig={c_above_trig} Vol={c_vol} StrongClose={c_close_pos}"
                            reason_str = f"{'|'.join(reasons)} [{trace}]"

                            dist_pct = ((trigger_level - close) / trigger_level) * 100.0 if close < trigger_level else 0.0
                            if 0.0 <= dist_pct <= 0.5:
                                logger.info(f"👀 {symbol} ENTRY_NEAR_MISS | Distance to Trigger: {dist_pct:.2f}% | Close=₹{close:.2f} | Trigger=₹{trigger_level:.2f}")

                            dist_atr = ((close - trigger_level) / atr20) if atr20 > 0 else 0.0
                            logger.info(f"🚫 {symbol} PhaseD Reject | Reason={reason_str} | Trigger={trigger_level:.2f} Close={close:.2f} PrevHigh={float(prev['High']):.2f} ATR={atr20:.2f} ATR_Extension={dist_atr:.2f} NearMiss={dist_pct:.2f}% VolRatio={vol_ratio:.2f} ClosePos={close_position:.2f} Pattern=EVAL")
            
                    if is_ready:
                        # [FIX MTF-19] Promote state to TRADE_ACTIVE immediately so
                        # the phase_score calculation below grants the +10 Phase D bonus.
                        # Without this, state remains ENTRY_READY and the scoring
                        # condition `if state == "TRADE_ACTIVE"` is always false.
                        state = "TRADE_ACTIVE"

                        # Do not generate new buy alerts on stale data returned by provider
                        if getattr(df, 'attrs', {}).get('is_stale'):
                            logger.info(f"Skipping buy alert for {symbol} because data is stale")
                            return
                        # Idempotency check before alert
                        if not check_recent_alert(symbol, scanner=SCANNER_MULTI_TF, breakout_type=SCANNER_MULTI_TF, lookback_minutes=390):
                            from sl_target_helper import compute_sl_and_target
                        
                            # [VERSION: MTF_VWAP_FALLBACK_FIX] Fallback to EMA20 if VWAP is missing due to lack of intraday volume
                            vwap_val = latest.get("VWAP")
                            if vwap_val is None or pd.isna(vwap_val) or vwap_val <= 0:
                                vwap_val = _safe_float(latest.get("EMA20", close))
                            
                            sl_result = compute_sl_and_target(
                                entry_price=close,
                                atr=atr20,
                                candle_range=_safe_float(latest.get("High")) - _safe_float(latest.get("Low")),
                                mode=SCANNER_MULTI_TF,
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
                                swing_low_15m=_safe_float(data_15m[symbol].iloc[-1].get("SWING_LOW")) if symbol in data_15m and isinstance(data_15m[symbol], pd.DataFrame) and not data_15m[symbol].empty else None,
                                swing_high_15m=_safe_float(data_15m[symbol].iloc[-1].get("SWING_HIGH")) if symbol in data_15m and isinstance(data_15m[symbol], pd.DataFrame) and not data_15m[symbol].empty else None,
                                swing_low_30m=_safe_float(data_30m[symbol].iloc[-1].get("SWING_LOW")) if symbol in data_30m and isinstance(data_30m[symbol], pd.DataFrame) and not data_30m[symbol].empty else None,
                                swing_high_30m=_safe_float(data_30m[symbol].iloc[-1].get("SWING_HIGH")) if symbol in data_30m and isinstance(data_30m[symbol], pd.DataFrame) and not data_30m[symbol].empty else None,
                                swing_low_1h=None,
                                swing_high_1h=None,
                            )
                            final_sl = sl_result["stop_loss"]
                            calc_target = sl_result["target_1"]

                            if sl_result.get("is_rejected"):
                                with _batch_lock:
                                    lower_funnel["rr_rejections"] += 1
                                from database import save_rejected_alert
                                if not is_test_mode:
                                    save_rejected_alert(
                                        symbol=symbol,
                                        scanner=SCANNER_MULTI_TF,
                                        rejection_reason=sl_result.get("rejection_reason", "V7 Engine Reject"),
                                        engine_version=sl_result.get("engine_version", "SL_ENGINE_V7.1"),
                                        context={"category": cat, "score": 0, "sl_result": sl_result}
                                    )
                                logger.info(f"🚫 {symbol} alert SUPPRESSED: {sl_result.get('rejection_reason')}")
                                return

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
                                # [FIX P2-6] [FIX-M2] Weighted phase scoring: base 60 + phase bonuses.
                                # Phase A (mandatory): base = 60
                                # Phase B (30m squeeze): +10 — only if Phase B passed THIS cycle or already persisted
                                # Phase C (15m alignment): +5  — only if Phase C passed THIS cycle or already persisted
                                # Phase D (5m trigger): +10   — always (we are executing Phase D by definition)
                                # Total max = 85
                                phase_score = 60
                                # Phase B: awarded if confirmed this cycle OR if stock arrived pre-validated (persisted SETUP_ARMED/ENTRY_READY)
                                if passed_phase_b or item.get("m30_status") == "PASSED":
                                    phase_score += 10  # Phase B bonus
                                # Phase C: awarded if confirmed this cycle OR if stock arrived pre-validated (persisted ENTRY_READY)
                                if passed_phase_c or item.get("m15_status") == "PASSED":
                                    phase_score += 5   # Phase C bonus
                                phase_score += 10      # Phase D bonus (always — Phase D is executing by definition)
                                base_score = phase_score
                            
                                # ── Bayesian Pledge Penalty ──
                                promoter_pledge_pct = pledge_map.get(symbol)
                                if promoter_pledge_pct is not None and bayesian_weights and "PLEDGE_PENALTY" in bayesian_weights:
                                    max_penalty = float(bayesian_weights["PLEDGE_PENALTY"])
                                    if promoter_pledge_pct > 10.0:
                                        scale = min(1.0, (promoter_pledge_pct - 10.0) / 40.0)
                                        # [FIX MTF-25] PLEDGE_PENALTY is stored as a positive magnitude.
                                        # The original code computed pledge_penalty >= 0 then checked
                                        # `if pledge_penalty < 0` — a condition that can never be true,
                                        # so the penalty was never actually applied.
                                        pledge_penalty = int(abs(max_penalty) * scale)
                                        if pledge_penalty > 0:
                                            base_score -= pledge_penalty
                                            logger.warning(f"  -{pledge_penalty} [{symbol}] Promoter Pledge Penalty ({promoter_pledge_pct:.1f}% pledge)")
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
                                    "breakout_type": SCANNER_MULTI_TF,
                                    "scanner": SCANNER_MULTI_TF,
                                    "category": cat,
                                    "technical_score": final_score,
                                    "volume_ratio": vol_ratio,
                                    "delivery_pct": float(delivery_map.get(symbol, 0.0) or 0.0),
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
                                    with _batch_lock:
                                        batch_upsert_list.append({
                                            'symbol': symbol, 'category': cat, 'current_state': "TRADE_ACTIVE",
                                            'm5_status': "PASSED"
                                        })
                                    with _batch_lock:
                                        cooldown_list.append((symbol, "TRADE_ACTIVE", 24))
                                with _batch_lock:
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
                                    f"entry=₹{close:.2f} | sl=₹{round(float(final_sl or 0.0))} | t1=₹{round(float(sl_result.get('target_1') or 0.0))} | "
                                    f"last_bar={_last_bar_date} | category={cat}"
                                )
                                logger.info(f"🔔 {symbol} EXECUTED! TRADE_ACTIVE alert generated via {trigger_type}.")
                            else:
                                logger.info(f"🚫 {symbol} alert SUPPRESSED: {reason}")
                                # [VERSION: MTF_DB_SAVE_FIX] Track silent DB save failures and flip health to DEGRADED
                                if reason != "ALREADY_EXISTS" and reason != "RECENT_ALERT_EXISTS" and "CONFLICT" not in str(reason).upper():
                                    with _batch_lock:
                                        db_save_failures += 1
                                # Do NOT advance state or set cooldown so it can try again if data freshness recovers

        except Exception as e:
            logger.exception(f"Fault isolation caught exception in Phase B/C/D: {e}")
            return

    _eval_start_t = time.perf_counter()
    batch_upsert_list = []
    # [VERSION: BATCH_COOLDOWN_v1.0] Collect cooldown updates here; flushed post-loop to avoid per-worker DB calls.
    cooldown_list = []  # List of (symbol, state, hours) tuples
    has_errors = False
    with ThreadPoolExecutor(max_workers=10, thread_name_prefix="MTF_Worker") as executor:
        future_to_item = {executor.submit(_process_item, item): item for item in active_items}
        for f in as_completed(future_to_item):
            try:
                f.result()
            except Exception as e:
                item = future_to_item[f]
                sym = item.get("symbol", "UNKNOWN")
                logger.error(f"❌ [Worker Crash] Error processing {sym}: {e}", exc_info=True)
                has_errors = True
    _eval_dur = time.perf_counter() - _eval_start_t

    if batch_upsert_list:
        try:
            batch_upsert_breakout_watchlist(batch_upsert_list)
        except Exception as _b_err:
            logger.warning(f"⚠️ Failed to batch upsert breakout watchlist in Phase B/C/D: {_b_err}")

    # [VERSION: BATCH_COOLDOWN_v1.0] Flush accumulated cooldown updates post-loop (avoids live DB call inside parallel workers).
    for _cd_sym, _cd_state, _cd_hours in cooldown_list:
        try:
            mark_breakout_watchlist_cooldown(_cd_sym, _cd_state, hours=_cd_hours)
        except Exception as _cd_err:
            logger.warning(f"⚠️ Failed to flush cooldown for {_cd_sym}: {_cd_err}")
    
    logger.info(
        f"⏱️ [MULTI_TF] Lower TF Phase Timing | "
        f"Fetch 30m: {_t_30m:.2f}s | "
        f"Fetch 15m: {_t_15m:.2f}s | "
        f"Fetch 5m+1d: {_t_5m_1d:.2f}s | "
        f"Evaluation: {_eval_dur:.2f}s"
    )
    # ── Log the funnel so we can see exactly where stocks drop off ────────
    logger.info(f"📊 Phase B/C/D Funnel: "
                f"30m_cands={lower_funnel['armed_candidates']} → bb_pass={lower_funnel['bb_pass']} → armed={lower_funnel['armed']} | "
                f"15m_cands={lower_funnel['entry_candidates']} → ema15_pass={lower_funnel['ema15_pass']} → entry_ready={lower_funnel['entry_ready']} | "
                f"5m_cands={lower_funnel['trigger_candidates']} → pb_fail_engulf={lower_funnel['pb_fail_engulf']} → pb_fail_vol={lower_funnel['pb_fail_vol']} → "
                f"rr_rejects={lower_funnel['rr_rejections']} → triggered={lower_funnel['triggered']}")
    
    try:
        from funnel_telemetry import log_funnel_metrics
        # Calculate unique needed size for the universe count
        unique_needed_size = len(set(needs_30m + needs_15m + needs_5m))
        log_funnel_metrics("MULTI_TF", regime_str, unique_needed_size, lower_funnel, lower_funnel['triggered'])
    except Exception as e:
        logger.warning(f"Failed to log funnel telemetry: {e}")

    # ── Global Ranking & Allocation (end-of-sweep, in-memory) ─────────────
    if not is_test_mode:
        try:
            opportunity_manager.process()
        except Exception as e:
            logger.error(f"OpportunityManager failed to process: {e}")

    # ── Memory Cleanup Phase ──────────────────────────────────────────────
    unique_needed = set(needs_30m + needs_15m + needs_5m)
    unique_fetched = set(data_30m.keys()) | set(data_15m.keys()) | set(data_5m.keys())

    try:
        from memory_profiler import run_purge_with_telemetry
        run_purge_with_telemetry("MultiTF Phase B/C/D Complete")
    except Exception as e:
        logger.debug(f"MultiTF lower TF memory purge failed: {e}")

    if any([stale_30m, stale_15m, stale_5m]):
        logger.info(
            f"📊 Stale data summary\n"
            f"30m: {stale_30m}\n"
            f"15m: {stale_15m}\n"
            f"5m : {stale_5m}"
        )
    # [VERSION: MULTI_TF_DB_PERSISTENCE_v1.0] Export parquet artifact and upload to DB for instant restart recovery
    import database
    if not is_test_mode and not getattr(database, "DONT_SAVE_WEALTH", False):
        try:
            from database import upload_parquet_to_db, get_connection
            from psycopg2.extras import RealDictCursor
            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM breakout_watchlist WHERE is_active = TRUE;")
                    bw_rows = cur.fetchall()
            if bw_rows:
                import os
                from config import DATA_DIR
                bw_df = pd.DataFrame(bw_rows)
                MULTI_TF_PATH = os.path.join(DATA_DIR, "multi_tf_system.parquet")
                os.makedirs(os.path.dirname(MULTI_TF_PATH), exist_ok=True)
                bw_df.to_parquet(MULTI_TF_PATH)
                
                # Upload intraday history bundles and parquet to DB in background for instant cold-start boot recovery (<0.5s)
                from database import upload_history_bundle_to_db, upload_parquet_to_db
                import threading
                
                def bg_upload():
                    global _last_mtf_parquet_upload
                    t_name = threading.current_thread().name
                    logger.info(f"🚀 [BACKGROUND WORKER START] Worker='{t_name}' | InitiatedBy='MultiTFScanner' | Action='Uploading intraday history bundles to DB'")
                    _t_start = time.perf_counter()
                    
                    import time as tm
                    now = tm.time()
                    try:
                        ok = upload_parquet_to_db("multi_tf_system", MULTI_TF_PATH)
                        if ok:
                            logger.info("💾 [MULTI_TF] Successfully exported and uploaded multi_tf_system.parquet to DB.")
                        else:
                            logger.error("❌ [MULTI_TF] Failed to upload multi_tf_system.parquet to DB.")
                        _last_mtf_parquet_upload = now
                    except Exception as up_e:
                        logger.error(f"❌ [MULTI_TF] Error uploading parquet: {up_e}", exc_info=True)

                    for _tf in ("1h", "30m", "15m", "5m"):
                        try:
                            upload_history_bundle_to_db(_tf)
                        except Exception as e:
                            logger.error(f"❌ [MULTI_TF] Error uploading history bundle for {_tf}: {e}", exc_info=True)
                    dur_s = time.perf_counter() - _t_start
                    logger.info(f"✅ [BACKGROUND WORKER COMPLETE] Worker='{t_name}' | Action='Uploaded intraday history bundles to DB' | Duration={dur_s:.2f}s")
                
                from database import submit_background_upload
                submit_background_upload(bg_upload)
        except Exception as _mtf_pe:
            logger.warning(f"Failed to export multi_tf_system to DB: {_mtf_pe}")

    # [RULE 67] Return lower-TF funnel metrics so _start_wrapper can compute structured breakdown.
    # unique_needed is the set union of all 3 TF symbol lists — no double-counting.
    return {
        "fetched": len(unique_fetched),
        "total": len(unique_needed),
        "stale": stale_30m + stale_15m + stale_5m,
        "save_failures": db_save_failures,
        "has_errors": has_errors,
        "entry_ready": lower_funnel.get("entry_ready", 0),
        "trigger_candidates": lower_funnel.get("trigger_candidates", 0),
        "triggered": lower_funnel.get("triggered", 0)
    }


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
_global_lock = ProcessLock("global_scanner_lock")

def start(run_once=False, is_test_mode=False, run_ctx=None, trigger_type="SCHEDULED", scheduler_name="CRON", session=None):
    from database import is_scanner_stopped, upsert_scanner_health, complete_scanner_execution_run
    from lock_utils import print_scanner_start_banner, print_scanner_end_banner
    if is_scanner_stopped("MULTI_TF"):
        logger.info("🛑 Multi-TF Scanner is STOPPED by Admin. Skipping execution.")
        if run_ctx:
            complete_scanner_execution_run(run_ctx, status_override="STOPPED", stop_reason="Scanner stopped by admin")
        return None

    if _scan_lock.locked():
        logger.warning("🛑 [DUPLICATE GUARD] Multi-TF Scanner is ALREADY actively running in thread lock. Skipping duplicate trigger.")
        if run_ctx:
            complete_scanner_execution_run(run_ctx, status_override="SKIPPED_DUPLICATE", stop_reason="Scanner lock busy")
        return None

    acquired_global = False
    acquired_scan = False
    _scan_start = None

    try:
        if run_ctx is None:
            try:
                from database import start_scanner_execution_run
                run_ctx = start_scanner_execution_run(scanner_name="MULTI_TF", trigger_type=trigger_type, scheduler_name=scheduler_name)
            except Exception as exc:
                logger.warning(f"⚠️ [MULTI_TF] Could not create run_ctx: {exc}")

        queued_at = None
        if not _global_lock.acquire(blocking=False, owner_scanner="MULTI_TF", operation="FULL_SCAN"):
            queued_at = time.monotonic()
            logger.info("⏳ [MULTI_TF] Global scanner lock busy — marking QUEUED and waiting in queue...")
            if run_ctx:
                from database import update_scanner_run_lifecycle
                update_scanner_run_lifecycle(run_ctx.run_id, "QUEUED")
            upsert_scanner_health("MULTI_TF", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")
            
            try:
                acquired_global = _global_lock.acquire(blocking=True, owner_scanner="MULTI_TF", operation="FULL_SCAN", run_ctx=run_ctx)
            except Exception as lock_err:
                logger.error(f"❌ [MULTI_TF] Error acquiring global lock: {lock_err}")
                acquired_global = False

            if not acquired_global:
                logger.error("❌ [MULTI_TF] Failed to acquire global scanner lock after queue wait.")
                if run_ctx:
                    complete_scanner_execution_run(run_ctx, status_override="FAILED", stop_reason="Global lock acquire timeout")
                upsert_scanner_health("MULTI_TF", "IDLE", error_msg="Lock acquisition timed out")
                return None
        else:
            acquired_global = True

        if run_ctx:
            from database import update_scanner_run_lifecycle
            update_scanner_run_lifecycle(run_ctx.run_id, "RUNNING")
        if queued_at is not None:
            logger.info(f"✅ [MULTI_TF] Global lock acquired after {round(time.monotonic()-queued_at,1)}s wait. Starting scan...")

        if not _scan_lock.acquire(blocking=False):
            logger.warning("🛑 Multi-TF Scanner is ALREADY actively running. Skipping duplicate execution.")
            if run_ctx:
                complete_scanner_execution_run(run_ctx, status_override="SKIPPED_DUPLICATE", stop_reason="Scanner already actively running")
            upsert_scanner_health("MULTI_TF", "IDLE", error_msg="Duplicate trigger skipped")
            return None
        acquired_scan = True

        _scan_start = print_scanner_start_banner("multi_tf_scanner", queued_at=queued_at, run_id=run_ctx.run_id if run_ctx else None)
        stats = _start_wrapper(run_once, is_test_mode=is_test_mode, session=session, run_ctx=run_ctx)
        
        final_status = "COMPLETED"
        if isinstance(stats, dict) and (stats.get("has_errors") or (stats.get("metrics_b", {}).get("has_errors"))):
            final_status = "COMPLETED_WITH_ERRORS"
            
        if run_ctx and isinstance(stats, dict) and "today_alerts" in stats:
            run_ctx.add_alert(stats.get("today_alerts", 0))
        if run_ctx:
            complete_scanner_execution_run(run_ctx, status_override=final_status)
        return stats
    except Exception as e:
        logger.exception(f"❌ [MULTI_TF] Unhandled exception during scan: {e}")
        if run_ctx:
            try:
                complete_scanner_execution_run(run_ctx, status_override="FAILED", exception=e)
            except Exception: pass
        try:
            upsert_scanner_health("MULTI_TF", status="DOWN", error_msg=f"Scan crashed: {str(e)[:300]}", run_id=run_ctx.run_id if run_ctx else None)
            from database import insert_notification
            insert_notification("error", "🚨 MULTI_TF Scanner CRASHED", f"Error: {str(e)[:400]}")
        except Exception: pass
        raise e
    finally:
        if _scan_start is not None:
            print_scanner_end_banner("multi_tf_scanner", _scan_start, run_id=run_ctx.run_id if run_ctx else None)

        if acquired_scan:
            try: _scan_lock.release()
            except Exception: pass
        if acquired_global:
            try: _global_lock.release()
            except Exception: pass

def _start_wrapper(run_once=False, is_test_mode=False, session=None, run_ctx=None):
    from datetime import time as dt_time
    from perf_utils import ScannerStageTracker
    stage_tracker = ScannerStageTracker("MULTI_TF_SCANNER")
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
                            scanner_name=SCANNER_MULTI_TF,
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
                upsert_scanner_health(SCANNER_MULTI_TF, "RUNNING", error_msg="Multi-TF Scan in progress...")
            except Exception:
                pass
                
            # Cache regime once per cycle
            # [VERSION: MULTI_TF_PATCH_v1.0] [BUG FIX 1] Pass nifty_ret explicitly to get_macro_regime to avoid redundant API calls
            stage_tracker.start_stage(1, "Macro Regime & Policy Evaluation", "Fetching Nifty 20D return")
            try:
                nifty_ret = get_nifty_20d_return()
                regime_ctx = MarketRegimeEngine.get_regime_context(nifty_ret)
                policy = StrategyPolicyEngine.get_policy(regime_ctx, SCANNER_MULTI_TF)
                regime_ctx["policy"] = policy
            except Exception as e:
                logger.warning(f"⚠️ Failed to compute macro regime: {e}. Defaulting to NEUTRAL.")
                regime_ctx = {"trend": "NEUTRAL", "biases": {}}
            stage_tracker.end_stage(f"Macro regime: {regime_ctx.get('regime', regime_ctx.get('trend', 'SIDEWAYS'))} | Policy: {policy.get('policy_name', 'NEUTRAL') if isinstance(policy, dict) else 'NEUTRAL'}")
            
            from memory_profiler import MemoryProfiler
            with MemoryProfiler("MULTI_TF_SCANNER", force_gc_cleanup=True):
                # 1. Sweep old states
                stage_tracker.start_stage(2, "Stale Watchlist Sweep", "Sweeping expired breakout setups")
                run_sweeper(is_test_mode=is_test_mode)
                stage_tracker.end_stage("State sweep completed")
                
                # 2. Hourly phase (Phase A): Only runs on 15-min candle boundaries or manual trigger
                stage_tracker.start_stage(3, "Hourly Phase A (1H Trend Scanner)", "Scanning 1H trend permission")
                # [VERSION: HEARTBEAT_MULTI_TF_v1.0] Pulse heartbeat before long Phase A so watchdog
                # doesn't mark this run TIMEOUT_STALE during 30-min scans.
                if run_ctx:
                    run_ctx.heartbeat(force=True)
                if run_once or (ist_now.minute % 15 == 0):
                    metrics_a = run_hourly_phase(is_test_mode=is_test_mode, run_once=run_once, session=session, run_ctx=run_ctx)
                else:
                    metrics_a = {"fetched": 0, "total": 0, "stale": 0, "approved": 0, "skipped": True}
                if run_ctx:
                    run_ctx.heartbeat()
                stage_tracker.end_stage(f"1H Approved: {metrics_a.get('approved', 0)}")
                
                # 3. Lower TF updater (Phases B, C & D): Runs EVERY 5 mins to deliver fast 5m breakout alerts
                stage_tracker.start_stage(4, "Lower TF Phase B/C/D Scanner", "Evaluating 30m/15m/5m triggers")
                # [VERSION: HEARTBEAT_MULTI_TF_v1.0] Pulse heartbeat before Phase B/C/D.
                if run_ctx:
                    run_ctx.heartbeat(force=True)
                metrics_b = run_lower_tf_phase(regime_ctx=regime_ctx, is_test_mode=is_test_mode, run_once=run_once, session=session)
                if run_ctx:
                    run_ctx.heartbeat()
                stage_tracker.end_stage(f"Lower TF Triggered: {metrics_b.get('triggered', 0) if isinstance(metrics_b, dict) else 0}")
            
            elapsed_time = (datetime.now(IST) - scan_start).total_seconds()
            stage_tracker.print_summary(alerts_found=metrics_b.get("triggered", 0) if isinstance(metrics_b, dict) else 0)
            logger.info("=========================================")
            logger.info(f"📊 Hourly Phase: {dict(metrics_a)}")
            logger.info(f"📊 Lower TF Phase: {dict(metrics_b)}")
            try:
                from symbol_router import symbol_router
                router_telemetry = symbol_router.get_telemetry_summary()
                logger.info(
                    f"📊 [PROVIDER_ROUTING] Total Sticky Routes: {router_telemetry.get('total_sticky_routes', 0)} "
                    f"(Upstox-Only: {router_telemetry.get('upstox_only_count', 0)}, Fyers-Only: {router_telemetry.get('fyers_only_count', 0)}) | "
                    f"Avoided Failures: {router_telemetry.get('avoided_failed_requests', 0)} | "
                    f"Fallbacks: {router_telemetry.get('routing_fallbacks', 0)}"
                )
            except Exception:
                pass
            logger.info(f"✅ [COMPLETE] MULTI-TF LADDER DONE | {elapsed_time:.2f}s | Status=OK")
            logger.info("=========================================")

            status = "OK" if market_open else "IDLE"
            error_msg = None
            
            total_stale = (metrics_a.get("stale", 0) + metrics_b.get("stale", 0))
            # [RULE 67] Unique symbol count — do NOT sum phases (symbols overlap between hourly and lower TF).
            # max() gives the true unique participating count when lower-TF is a subset of hourly universe.
            total_symbols = max(metrics_a.get("total", 0), metrics_b.get("total", 0))
            total_fetched = max(metrics_a.get("fetched", 0), metrics_b.get("fetched", 0))
            
            if run_ctx:
                run_ctx.set_total_stocks(total_symbols)
                run_ctx.fresh_count = total_fetched
                run_ctx.stale_count = total_stale
                
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
            if total_fetched_a == 0 and total_fetched_b == 0 and (total_expected_a > 0 or total_expected_b > 0):
                outcome = "FAILED"
                status = "DOWN"
                error_msg = f"🚫 CRITICAL BLOCKER: Multi-TF scanner failed to fetch any data (0/{total_expected_a + total_expected_b})"
                logger.error(f"🚨 {error_msg}")
                try:
                    from telegram_engine import send_telegram_message
                    send_telegram_message(f"🚨 <b>CRITICAL BLOCKER: MULTI-TF SCANNER FAILED</b>\n0/{total_expected_a + total_expected_b} symbols were unfetched / missing data.")
                except Exception:
                    pass

            if not getattr(database, "DONT_SAVE_ALERTS", False):
                try:
                    from database import upsert_scanner_health
                    upsert_scanner_health(
                        scanner_name=SCANNER_MULTI_TF,
                        status=status,
                        last_success=datetime.now(IST).isoformat() if outcome != "FAILED" else None,
                        total_count=total_symbols,
                        error_msg=error_msg,
                        scheduled_for="Every 5min (9:15 AM - 3:30 PM)",
                        outcome=outcome,
                        duration_seconds=elapsed_time,
                        provider_stats={
                            "SUCCESS": total_fetched,
                            "NOT_FOUND": total_symbols - total_fetched - total_stale,
                            "STALE": total_stale,
                            "RATE_LIMIT": 0,
                            "NETWORK_ERROR": 0,
                            "TIMEOUT": 0,
                            "EMPTY_DATA": 0,
                            "1H_COUNT": metrics_a.get("total", 0),
                            "LOWER_TF_COUNT": metrics_b.get("total", 0)
                        }
                    )
                except Exception:
                    logger.exception("❌ Failed to update scanner health for MULTI_TF")
            
            # [RULE 67] Unique symbol count — do NOT sum hourly + lower TF (symbols overlap).
            # User explicitly required: "use unique participating symbols instead of summing phases".
            # Phase A (hourly) processes the hourly approved universe.
            # Phase B/C/D processes the same HOURLY_APPROVED + SETUP_ARMED + ENTRY_READY set.
            # The set union is the true unique count of distinct symbols touched in this cycle.
            hourly_symbols = metrics_a.get("total", 0)
            hourly_approved = metrics_a.get("approved", 0)
            lower_tf_symbols = metrics_b.get("total", 0)
            entry_ready_count = metrics_b.get("entry_ready", 0)
            trigger_candidates_count = metrics_b.get("trigger_candidates", 0)
            alerts_count = metrics_b.get("triggered", 0) if isinstance(metrics_b, dict) else 0

            # Unique symbols = max of either phase (all lower-TF symbols came from hourly universe)
            # When both phases ran, lower_tf_symbols is the unique set already (set union of 30m+15m+5m).
            symbols_scanned = max(hourly_symbols, lower_tf_symbols)

            logger.info(
                f"📊 [MULTI_TF] Symbol Count Breakdown:\n"
                f"  hourly_symbols     = {hourly_symbols}\n"
                f"  hourly_approved    = {hourly_approved}\n"
                f"  lower_tf_symbols   = {lower_tf_symbols}\n"
                f"  entry_ready        = {entry_ready_count}\n"
                f"  trigger_candidates = {trigger_candidates_count}\n"
                f"  alerts             = {alerts_count}\n"
                f"  symbols_scanned (unique) = {symbols_scanned}"
            )

            total_scanned = symbols_scanned
            
            try:
                from database import insert_notification
                from push_service import send_push_to_all
                if status == "OK" and run_once and (metrics_b.get("triggered", 0) > 0):
                    insert_notification("admin", f"🚀 MULTI_TF Scanner ran successfully. Found {metrics_b.get('triggered', 0)} alerts.", f"Evaluated {total_scanned} setups across multiple timeframes.")
                elif status == "DEGRADED":
                    insert_notification("admin", f"⚠️ MULTI_TF Scanner finished with DEGRADED status", error_msg or f"Evaluated {total_scanned} setups but data was degraded.")
                    send_push_to_all("⚠️ MULTI_TF Scanner DEGRADED", error_msg or "Stale data exceeded limit.")
            except Exception:
                pass
                
            if run_once:
                has_errors = False
                if isinstance(metrics_a, dict) and metrics_a.get("has_errors"): has_errors = True
                if isinstance(metrics_b, dict) and metrics_b.get("has_errors"): has_errors = True
                return {"total_count": total_symbols, "today_alerts": metrics_b.get("triggered", 0) if isinstance(metrics_b, dict) else 0, "has_errors": has_errors}
                
            logger.info("💤 Sleeping 5 minutes before next Multi-TF ladder run...")
            time.sleep(300)
            
        except Exception as e:
            logger.exception("❌ MULTI-TF LADDER CRASHED")
            if not getattr(database, "DONT_SAVE_ALERTS", False):
                try:
                    from database import upsert_scanner_health
                    upsert_scanner_health(
                        scanner_name=SCANNER_MULTI_TF,
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

def _run_multi_tf_v2_pipeline():
    """
    Parallel Isolated Execution for Phase 2C Multi-TF Scanner V2.
    Evaluates Weekly Thesis, Daily Setup, and Hourly Trigger states.
    Zero side effects on V1 execution. Writes exclusively to V2 schema tables.
    """
    try:
        logger.info("🚀 [MULTI_TF V2] Starting parallel isolated execution cycle...")
        from multi_tf_schema import init_multi_tf_v2_schema
        from multi_tf_engine import evaluate_multi_tf_v2_symbol
        from candidate_tracker import save_candidate
        
        init_multi_tf_v2_schema()

        # Load ELITE and Near-Qualified V2 Parquets
        elite_path = "data/elite_universe_v2.parquet"
        nq_path = "data/near_qualified_v2.parquet"

        elite_syms = set()
        nq_syms = set()

        if os.path.exists(elite_path):
            df_e = pd.read_parquet(elite_path)
            col = "Stock" if "Stock" in df_e.columns else df_e.columns[0]
            elite_syms = {str(s).upper() for s in df_e[col].dropna()}

        if os.path.exists(nq_path):
            df_nq = pd.read_parquet(nq_path)
            col = "Stock" if "Stock" in df_nq.columns else df_nq.columns[0]
            nq_syms = {str(s).upper() for s in df_nq[col].dropna()}

        target_syms = sorted(list(elite_syms | nq_syms))
        if not target_syms:
            logger.info("ℹ️ [MULTI_TF V2] No target universe found. Skipping cycle.")
            return

        logger.info(f"📋 [MULTI_TF V2] Processing {len(target_syms)} universe symbols ({len(elite_syms)} ELITE, {len(nq_syms)} NQ)...")

        # Load price data via price cache
        watchlist_df = pd.DataFrame([{"Stock": s} for s in target_syms])
        hourly_map = fetch_watchlist_data(watchlist_df, period="1mo", interval="1h", requester="MULTI_TF_V2") or {}
        daily_map = fetch_watchlist_data(watchlist_df, period="1y", interval="1d", requester="MULTI_TF_V2") or {}

        v2_results = {"WATCH": 0, "TRIGGER": 0, "CONFIRMED": 0, "MISSED": 0, "NQ_OBSERVATION_ONLY": 0}

        for sym in target_syms:
            h_df = hourly_map.get(sym)
            d_df = daily_map.get(sym)

            if d_df is None or d_df.empty or len(d_df) < 50:
                continue

            # Resample daily data to weekly dataframe
            w_df = d_df.resample("W-FRI").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            }).dropna()

            is_nq = sym in nq_syms and sym not in elite_syms

            res_v2 = evaluate_multi_tf_v2_symbol(
                symbol=sym,
                weekly_df=w_df,
                daily_df=d_df,
                hourly_df=h_df,
                is_nq_universe=is_nq,
                provisional_vol_threshold=1.8
            )

            state = res_v2.get("state", "NO_VALID_SETUP")
            if state in v2_results:
                v2_results[state] += 1

            if state in ("WATCH", "CONFIRMED", "IMMEDIATE_TRIGGER_ZONE"):
                # Save V2 Candidate snapshot using Phase-1 tracker
                try:
                    save_candidate(
                        symbol=sym,
                        scanner_name="MULTI_TF_V2",
                        state=state,
                        entry_price=res_v2.get("entry_price", 0.0),
                        stop_loss=res_v2.get("entry_price", 0.0) * 0.965,
                        target=res_v2.get("entry_price", 0.0) * 1.07,
                        score=res_v2.get("score", 70.0),
                        quality_grade=res_v2.get("quality_grade", "B"),
                        setup_id=res_v2.get("setup_id", f"PFC_{sym}_MULTI_TF_{date.today()}")
                    )
                except Exception as save_err:
                    logger.debug(f"V2 candidate save error for {sym}: {save_err}")

        logger.info(
            f"✅ [MULTI_TF V2] Cycle Complete: WATCH={v2_results['WATCH']} | "
            f"CONFIRMED={v2_results['CONFIRMED']} | MISSED={v2_results['MISSED']} | "
            f"NQ_OBSERVATION={v2_results['NQ_OBSERVATION_ONLY']}"
        )
    except Exception as e:
        logger.exception(f"❌ [MULTI_TF V2] Pipeline execution error: {e}")


if __name__ == "__main__":

    logging.basicConfig(level=logging.INFO)
    start(run_once=True)