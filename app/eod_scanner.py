# =====================================================================================
# app/eod_scanner.py (SCHEDULER READY)
# EOD BREAKOUT SCANNER WITH CONSOLIDATED MAIL AUTOMATION
# =====================================================================================

import os
import pandas as pd
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from zoneinfo import ZoneInfo
from datetime import datetime, timedelta

from technical_indicators import apply_indicators
from memory_profiler import MemoryProfiler
from breakout_engine import detect_breakouts
from scoring_engine import calculate_score
from sector_rotation import get_sector_scores, SectorRotationResult
from surveillance import get_live_blacklist, force_refresh_blacklist
from trade_ranking_engine import TradeRankingEngine
from macro_utils import MarketRegimeEngine, get_nifty_20d_return, get_macro_regime
from strategy_policy import StrategyPolicyEngine
from database import (
    init_db, save_alert_if_new, save_candidate, upsert_fetch_error,
    upsert_scanner_health, insert_notification,
    get_recent_alerts_for_scanner, verify_alerts_saved_today
)
from core_enums import ProviderResult
from core_models import ScanFailure
from delivery_data import fetch_delivery_data
from price_cache import fetch_watchlist_data
from sl_target_helper import compute_sl_and_target
from watchlist_cache import get_watchlist
import time
import database

from config import (
    EOD_CONFIG,
    EOD_ADVANCED_CONFIG,
    ACTIVE_ALGO_VERSION,
    ALERT_COOLDOWN_MINUTES,
    ADX_MIN_THRESHOLD,
    MIN_STOCK_PRICE,
    SCORE_THRESHOLDS,
    MIN_BREAKOUT_MARGIN,
    MIN_BREAKOUT_VOLUME_RATIO,
    BASE_TIGHTNESS_THRESHOLD,
)

logger = logging.getLogger(__name__)

IST        = ZoneInfo("Asia/Kolkata")

MIN_SIGNALS             = EOD_CONFIG["MIN_SIGNALS"]
MIN_BODY_RATIO          = EOD_CONFIG["MIN_BODY_RATIO"]
MIN_CLOSE_POSITION      = EOD_CONFIG["MIN_CLOSE_POSITION"]
MAX_UPPER_WICK_RATIO    = EOD_CONFIG["MAX_UPPER_WICK"]
MIN_VOLUME_RATIO        = EOD_CONFIG["MIN_VOLUME_RATIO"]    
MIN_AVG_VOLUME_SHARES   = EOD_CONFIG["MIN_VOLUME_AVG"]      
MIN_RSI                 = EOD_CONFIG["MIN_RSI"]             
MAX_RSI                 = EOD_CONFIG["MAX_RSI"]                   

# MIN_STOCK_PRICE imported from config (₹100)
MAX_DISTANCE_FROM_52W_HIGH_PCT = EOD_ADVANCED_CONFIG["MAX_DISTANCE_FROM_52W_HIGH_PCT"]

from lock_utils import ProcessLock
_scan_lock = ProcessLock("eod_scanner")


def _safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)
    except Exception:
        return default

def start(force: bool = False):
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("Scanner is already actively running!")
    try:
        return _start_wrapper(force)
    finally:
        _scan_lock.release()

def _start_wrapper(force: bool = False):
    is_test_mode = True  # Safe default
    init_db()
    
    try:
        upsert_scanner_health("EOD", "RUNNING", error_msg="EOD Scan in progress...")
    except Exception:
        logger.warning("⚠️ Could not mark EOD as RUNNING")
    
    force_refresh_blacklist()
    
    nifty_ret_20d = get_nifty_20d_return()

    
    ist_now = datetime.now(IST)
    logger.info("\n" + "=" * 80)
    logger.info(f"🚀🚀🚀 [START] EOD SCANNER INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')} 🚀🚀🚀")
    logger.info("=" * 80 + "\n")
    
    start_time = datetime.now(IST)

    # Check if we are outside the valid EOD window (21:00 - 23:59:59)
    now_time = ist_now.time()
    scan_start = datetime.strptime("21:00", "%H:%M").time()
    scan_end = datetime.strptime("23:59:59", "%H:%M:%S").time()
    
    if force:
        is_test_mode = False
    else:
        is_test_mode = getattr(database, "DONT_SAVE_ALERTS", False) or not (scan_start <= now_time <= scan_end)
    if is_test_mode:
        logger.info("🧪 [TEST MODE] Outside scheduled window (21:00-23:59). Alerts will NOT be saved to DB.")

    try:
        from memory_profiler import StageTimelineTracker
        with StageTimelineTracker("EOD", "1. Watchlist Universe Load"):
            try:
                watchlist = get_watchlist()
                logger.info(f"🛡️ EOD Scanner running on full fundamental watchlist: {len(watchlist)} stocks")
            except Exception as e:
                logger.exception("❌ Failed to load watchlist")
                if not is_test_mode:
                    try:
                        upsert_scanner_health(scanner_name="EOD", status="DOWN", error_msg=f"Watchlist load failed: {str(e)[:200]}")
                    except Exception:
                        pass
                return 0

        if watchlist.empty:
            logger.info("🛡️ EOD Scanner | Universe is empty (no stocks passed Wealth Engine BUY signals). Exiting cleanly.")
            if not is_test_mode:
                try:
                    insert_notification("admin", "🚀 EOD Scanner ran successfully. Found 0 new breakout alerts.", "Generated 0 alerts. The fundamental watchlist universe is currently empty.")
                    upsert_scanner_health("EOD", status="OK", last_success=datetime.now(IST).isoformat(), today_alerts=0, total_count=0)
                    from push_service import send_push_to_all
                    send_push_to_all("🚀 EOD Scanner OK", "Found 0 new breakout alerts.", bypass_throttle=True)
                except Exception:
                    pass
            return 0

        # We do NOT purge here yet.
        # Purge only after upstream validation and fetch sufficiency checks succeed.
        # [VERSION: EOD_PATCH_v1.0] [BUG FIX 5] Compute today_str once here and reuse it throughout to avoid duplication
        today_str = ist_now.strftime("%Y-%m-%d")

        # [VERSION: SCANNER_DIAG_LOG_v1.0] Watchlist fingerprint for cross-run comparison
        import hashlib
        import uuid
        _wl_stocks = sorted(watchlist["Stock"].tolist())
        _wl_hash = hashlib.md5("|".join(_wl_stocks).encode()).hexdigest()[:12]
        scan_id = str(uuid.uuid4())
        logger.info(f"📋 [EOD] Watchlist fingerprint: {len(watchlist)} stocks | hash={_wl_hash} | scan_id={scan_id}")

        delivery_map: dict[str, float] = {}
        all_ticker_data = {}
        
        with StageTimelineTracker("EOD", "2. Pre-Scan Data (Pledge, Delivery, Sectors)"):
            # Fetch pledge map to pass to scoring engine
            try:
                from database import get_pledge_map
                symbols = [str(s) for s in watchlist["Stock"].tolist() if s]
                pledge_map = get_pledge_map(symbols)
                logger.info(f"🛡️ Fetched pledge data for {len(pledge_map)} symbols")
            except Exception as e:
                logger.exception("Failed to fetch pledge map")
                pledge_map = {}

            # [VERSION: EOD_DELIVERY_FALLBACK_v1.0] Try today first, fallback to previous days if not available
            delivery_map = {}
            for days_back in range(0, 5):
                candidate = ist_now.date() - timedelta(days=days_back)
                while candidate.weekday() >= 5:
                    candidate -= timedelta(days=1)
                
                try:
                    delivery_map = fetch_delivery_data(candidate, skip_db_save=(days_back > 0))
                    if delivery_map:
                        if days_back > 0:
                            logger.info(f"✅ EOD Scanner using FALLBACK Bhavcopy from: {candidate}")
                            try:
                                from push_service import send_push_to_all
                                msg = f"EOD Scanner is using stale Bhavcopy (fallback from {candidate}) because today's data is not yet published."
                                insert_notification("warning", "⚠️ Stale Bhavcopy Used", msg)
                                send_push_to_all("⚠️ Stale Bhavcopy Used", msg, bypass_throttle=True)
                            except Exception as ne:
                                logger.error(f"Failed to send stale Bhavcopy notification: {ne}")
                        else:
                            logger.info(f"✅ EOD Scanner using TODAY'S Bhavcopy from: {candidate}")
                        break
                except Exception as e:
                    logger.error(f"❌ Delivery fetch failed for {candidate}: {e}")

            try:
                rotation_result = get_sector_scores()
            except Exception:
                rotation_result = SectorRotationResult({}, set(), set(), "", datetime.now(IST).date(), 0.0)

        total_alerts       = 0
        alerts_by_category = {}

        provider_stats_counts = {
            "SUCCESS": 0,
            "NOT_FOUND": 0,
            "RATE_LIMIT": 0,
            "NETWORK_ERROR": 0,
            "TIMEOUT": 0,
            "EMPTY_DATA": 0
        }
        scan_failures = []

        rejection_counts = {k: 0 for k in [
            "no_data", "missing_col", "indicator_nan", "insufficient_bars", "indicator_fail", "weak_signals",
            "weak_body", "bearish_candle", "weak_close_pos", "upper_wick", "low_volume",
            "low_avg_volume", "penny_stock", "rsi_range", "below_ema20",
            "below_sma50", "weak_adx", "far_from_52w_high",
            "gap_day", "extended_breakout", "gap_extended", "low_score", "duplicate", "stale_data",
            "prior_red_candles", "obv_divergence", 
            "no_structural_breakout", "no_atr_expansion", "base_too_wide",
            "missing_atr", "zero_avg_volume", "zero_candle_range", "low_rr"
        ]}
        
        market_regime = get_macro_regime(nifty_ret_20d)
        logger.info(f"📊 Market Regime Classifier: {market_regime}")

        # [EOD_REGIME_CTX_FIX_v1.0] BUG-1 FIX: regime_ctx was never initialized in eod_scanner.
        # Only market_regime (a string) was built via get_macro_regime().
        # reversal_scanner and multi_tf_scanner both correctly build the full dict via
        # MarketRegimeEngine.get_regime_context(). Now aligned.
        try:
            regime_ctx = MarketRegimeEngine.get_regime_context(nifty_ret_20d)
            policy = StrategyPolicyEngine.get_policy(regime_ctx, "EOD")
            regime_ctx["policy"] = policy
        except Exception:
            logger.warning("⚠️ Could not build regime_ctx from MarketRegimeEngine — using neutral fallback")
            regime_ctx = {"trend": market_regime, "biases": {}}
            
        try:
            from database import get_latest_weights
            regime_str = regime_ctx.get("trend", "NEUTRAL")
            latest_db_weights = get_latest_weights(regime_str)
            if latest_db_weights:
                bayesian_weights = latest_db_weights.get("weights")
                bayesian_version = latest_db_weights.get("version", "v1")
            else:
                bayesian_weights = None
                bayesian_version = "v1"
        except Exception:
            bayesian_weights = None
            bayesian_version = "v1"

        # [BUG-1 FIX v1.5] Compute threshold BEFORE regime check to avoid NameError
        BASE_SCORE_THRESHOLD = SCORE_THRESHOLDS.get("1d", 82)
        global_min_score = BASE_SCORE_THRESHOLD

        # Wire the threshold increase to read dynamically from the config.py regime modifiers
        try:
            from config import REGIME_POLICIES
            modifier = REGIME_POLICIES.get(market_regime, {}).get("score_modifier", 0)
            if modifier > 0:
                logger.info(f"🛑 {market_regime} regime detected — raising score threshold by +{modifier}.")
                global_min_score += modifier
        except Exception as e:
            logger.warning(f"Failed to fetch REGIME_POLICIES: {e}")
        
        logger.info(f"📊 Score threshold for {market_regime} regime: {global_min_score}")

        import gc, time
        BATCH_SIZE = int(os.environ.get("EOD_FETCH_BATCH_SIZE", "50"))
        
        total_fetched_count = 0
        logger.info(f"📥 Processing EOD phase in chunks of {BATCH_SIZE}...")

        from memory_profiler import chunk_iterable, BatchMemoryTracker
        total_batches = (len(watchlist) + BATCH_SIZE - 1) // BATCH_SIZE

        with MemoryProfiler("Process Symbols"):
            for batch_num, chunk_df in enumerate(chunk_iterable(watchlist, BATCH_SIZE), start=1):
                with BatchMemoryTracker("EOD", batch_num, total_batches, len(chunk_df), collect_gc=True) as tracker:
                    import pandas as pd
                    all_ticker_data = fetch_watchlist_data(chunk_df, "1y", "1d")
                    if not all_ticker_data:
                        continue
                    
                    valid_fetches = sum(1 for v in all_ticker_data.values() if isinstance(v, pd.DataFrame) and not v.empty)
                    total_fetched_count += valid_fetches
                    from core_enums import ProviderResult
                    rows_fetched = sum(len(df) for df in all_ticker_data.values() if isinstance(df, pd.DataFrame))
                    tracker.mark_fetch_complete(row_count=rows_fetched)
                
                for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):
                    symbol = "UNKNOWN"
                    try:
                        symbol   = row["Stock"]
                        category = row["Category"]
                        sector   = row.get("Sector", None)

                        if symbol in get_live_blacklist():
                            continue

                        if symbol not in all_ticker_data or all_ticker_data[symbol] is None:
                            rejection_counts["no_data"] += 1
                            provider_stats_counts["EMPTY_DATA"] += 1
                            scan_failures.append(ScanFailure(symbol=symbol, scanner_name="EOD", provider="unknown", failure_reason="missing data", scan_id=scan_id))
                            continue

                        if isinstance(all_ticker_data[symbol], ProviderResult):
                            res = all_ticker_data[symbol]
                            provider_stats_counts[res.name] += 1
                            if res != ProviderResult.SUCCESS:
                                rejection_counts["no_data"] += 1
                                scan_failures.append(ScanFailure(symbol=symbol, scanner_name="EOD", provider="unknown", failure_reason=f"Provider error: {res.name}", scan_id=scan_id))
                                continue
                        else:
                            provider_stats_counts["SUCCESS"] += 1

                        ticker = all_ticker_data[symbol].copy()

                        if ticker.empty:
                            rejection_counts["no_data"] += 1
                            continue

                        # If provider returned stale data (used as fallback during rate limits), skip EOD buy generation
                        if getattr(ticker, 'attrs', {}).get('is_stale'):
                            rejection_counts["stale_data"] += 1
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

                        # [VERSION: EOD_BAR_LIMIT_FIX] Lowered bar minimum from 200 to 50 to allow IPOs/new listings to be evaluated
                        if len(ticker) < 50:
                            rejection_counts["insufficient_bars"] += 1
                            continue

                        ticker = apply_indicators(ticker, timeframe="1d")

                        if ticker is None or ticker.empty:
                            rejection_counts["indicator_fail"] += 1
                            continue

                        signals = detect_breakouts(ticker, timeframe="1d")

                        if len(signals) < MIN_SIGNALS:
                            rejection_counts["weak_signals"] += 1
                            continue

                        latest = ticker.iloc[-1]

                        if "RSI" not in ticker.columns or pd.isna(latest["RSI"]):
                            logger.debug(f"[EOD] {symbol} rejected: latest RSI is missing or NaN")
                            rejection_counts["indicator_nan"] += 1
                            continue

                        # [VERSION: EOD_PATCH_v1.1] [BUG FIX 8 REGRESSION FIX] Proper fallback to DatetimeIndex when Date/Datetime column is missing
                        # [FIX P0] Compare against the last bar's own date rather than ist_now.date().
                        # On weekends/holidays, ist_now.date() is a non-trading day and every symbol
                        # would be rejected as stale. Instead, we confirm the last bar is reasonably
                        # recent (within 4 calendar days to cover long weekends).
                        _expected_max_age_days = 4  # Covers Fri→Mon and long weekends
                        _stale_col = next((c for c in ["Date", "Datetime"] if c in ticker.columns), None)
                        if _stale_col:
                            try:
                                _last_ts = pd.to_datetime(latest[_stale_col])
                                # [VERSION: EOD_TZ_STALE_FIX_v1.0] Localize timezone naive timestamp to IST to prevent midnight rollover miscalculation
                                if _last_ts.tzinfo is None:
                                    _last_ts = _last_ts.tz_localize("Asia/Kolkata")
                                else:
                                    _last_ts = _last_ts.tz_convert("Asia/Kolkata")
                                _bar_age_days = (ist_now.date() - _last_ts.date()).days
                                if _bar_age_days < 0 or _bar_age_days > _expected_max_age_days:
                                    rejection_counts["stale_data"] += 1
                                    continue
                            except Exception as e:
                                logger.debug(f"⏭️ {symbol} stale-data check failed: {e}")
                                rejection_counts["stale_data"] += 1
                                continue
                        elif isinstance(ticker.index, pd.DatetimeIndex):
                            try:
                                _last_ts = pd.Timestamp(ticker.index[-1])
                                if _last_ts.tzinfo is not None:
                                    _last_ts = _last_ts.tz_convert("Asia/Kolkata")
                                else:
                                    # [FIX P0] yfinance .NS returns naive timestamps in IST, not UTC.
                                    # Localizing as UTC shifts the date by 5.5h and causes valid bars
                                    # to be rejected near midnight IST.
                                    _last_ts = _last_ts.tz_localize("Asia/Kolkata")
                                _bar_age_days = (ist_now.date() - _last_ts.date()).days
                                if _bar_age_days < 0 or _bar_age_days > _expected_max_age_days:
                                    rejection_counts["stale_data"] += 1
                                    continue
                            except Exception as e:
                                logger.debug(f"⏭️ {symbol} stale-data check failed (index): {e}")
                                rejection_counts["stale_data"] += 1
                                continue
                        else:
                            logger.debug(f"⏭️ {symbol} no timestamp available (neither column nor DatetimeIndex)")
                            rejection_counts["stale_data"] += 1
                            continue

                        # [VERSION: EOD_VOL_RATIO_FIX] Protect against newly listed stocks with <22 bars
                        if len(ticker) >= 22:
                            avg_volume = float(ticker["Volume"].iloc[-21:-1].mean())
                        else:
                            avg_volume = float(ticker["Volume"].iloc[:-1].mean())

                        if avg_volume <= 0:
                            # [VERSION: EOD_PATCH_v1.0] [BUG FIX 3] Rejection counters updated for zero volume and candle range
                            rejection_counts["zero_avg_volume"] += 1
                            continue

                        volume_ratio = _safe_float(latest.get("Volume")) / avg_volume

                        candle_high  = _safe_float(latest.get("High"))
                        candle_low   = _safe_float(latest.get("Low"))
                        candle_open  = _safe_float(latest.get("Open"))
                        candle_close = _safe_float(latest.get("Close"))
                        candle_range = candle_high - candle_low
                        candle_body  = abs(candle_close - candle_open)
                        upper_wick   = candle_high - candle_close

                        if candle_range <= 0:
                            rejection_counts["zero_candle_range"] += 1
                            continue

                        body_ratio     = candle_body / candle_range
                        close_position = (candle_close - candle_low) / candle_range
                        wick_ratio     = upper_wick / candle_range
                        rsi_val        = _safe_float(latest.get("RSI"))

                        if body_ratio < MIN_BODY_RATIO:
                            rejection_counts["weak_body"] += 1
                            continue
                        if candle_close <= candle_open:
                            rejection_counts["bearish_candle"] += 1
                            continue
                        if close_position < MIN_CLOSE_POSITION:
                            rejection_counts["weak_close_pos"] += 1
                            continue
                        if wick_ratio > MAX_UPPER_WICK_RATIO:
                            rejection_counts["upper_wick"] += 1
                            continue
                        if volume_ratio < MIN_VOLUME_RATIO:
                            rejection_counts["low_volume"] += 1
                            continue
                        if avg_volume < MIN_AVG_VOLUME_SHARES:
                            rejection_counts["low_avg_volume"] += 1
                            continue
                        if candle_close < MIN_STOCK_PRICE:
                            rejection_counts["penny_stock"] += 1
                            continue
                        if not (MIN_RSI <= rsi_val <= MAX_RSI):
                            rejection_counts["rsi_range"] += 1
                            continue

                        # ── v6: STRUCTURAL BREAKOUT FILTERS ─────────────────────────────
                        # [VERSION: EOD_PATCH_v1.0] [BUG FIX 2] Added explicit outer else rejection to avoid silent bypass of structural filters
                        if "PRIOR_20D_HIGH" not in ticker.columns or pd.isna(latest.get("PRIOR_20D_HIGH")):
                            rejection_counts["missing_atr"] += 1
                            continue

                        prior_high = _safe_float(latest.get("PRIOR_20D_HIGH"))
                        if prior_high <= 0:
                            rejection_counts["no_structural_breakout"] += 1
                            continue

                        if candle_close <= prior_high:
                            rejection_counts["no_structural_breakout"] += 1
                            continue

                        # Not Extended
                        if "ATR20" not in ticker.columns or pd.isna(latest.get("ATR20")):
                            rejection_counts["missing_atr"] += 1
                            continue

                        atr20 = _safe_float(latest.get("ATR20"))
                        if atr20 <= 0:
                            rejection_counts["missing_atr"] += 1
                            continue

                        # [VERSION: BUSINESS_LOGIC_FIX_v1.0] Gap-and-go penalty (Soft Gate)
                        technical_penalties = {}
                        atr_extension = (candle_close - prior_high) / atr20
                        max_ext = EOD_ADVANCED_CONFIG.get("MAX_EXTENDED_BREAKOUT_ATR_MULT", 1.5)
                        if atr_extension > max_ext:
                            pen_mult = EOD_ADVANCED_CONFIG.get("GAP_AND_GO_PENALTY_MULT", 10)
                            max_pen = EOD_ADVANCED_CONFIG.get("GAP_AND_GO_MAX_PENALTY", 20)
                            technical_penalties["extended_breakout"] = min(max_pen, (atr_extension - max_ext) * pen_mult)
                
                        # ATR Expansion
                        if candle_range / atr20 < EOD_ADVANCED_CONFIG.get("MIN_ATR_EXPANSION_RATIO", 1.2):
                            rejection_counts["no_atr_expansion"] += 1
                            continue

                        if "BB_WIDTH_PCTILE" in ticker.columns and not pd.isna(latest.get("BB_WIDTH_PCTILE")):
                            bb_width_pctile = _safe_float(latest.get("BB_WIDTH_PCTILE"))
                            if bb_width_pctile > EOD_ADVANCED_CONFIG.get("MAX_BB_WIDTH_PCTILE", 0.80):
                                rejection_counts["base_too_wide"] += 1
                                continue

                        if "EMA20" in ticker.columns and not pd.isna(latest.get("EMA20")):
                            if candle_close < _safe_float(latest.get("EMA20")):
                                rejection_counts["below_ema20"] += 1
                                continue

                        if "SMA50" in ticker.columns and not pd.isna(latest.get("SMA50")):
                            if candle_close < _safe_float(latest.get("SMA50")):
                                rejection_counts["below_sma50"] += 1
                                continue

                        # Golden Cross is no longer mandatory, shifted to scoring engine

                        if "ADX" in ticker.columns and not pd.isna(latest.get("ADX")):
                            if _safe_float(latest.get("ADX")) < ADX_MIN_THRESHOLD:
                                rejection_counts["weak_adx"] += 1
                                continue

                        # MACD is no longer mandatory, shifted to scoring engine

                        if "HIGH_52W" in ticker.columns and not pd.isna(latest.get("HIGH_52W")):
                            high_52w = _safe_float(latest.get("HIGH_52W"))
                            if high_52w > 0:
                                pct_from_high = (high_52w - candle_close) / high_52w * 100
                                if pct_from_high > MAX_DISTANCE_FROM_52W_HIGH_PCT:
                                    rejection_counts["far_from_52w_high"] += 1
                                    continue

                        if len(ticker) >= 2:
                            prev_close = _safe_float(ticker["Close"].iloc[-2])
                            if prev_close > 0:
                                single_move_pct = abs(candle_close - prev_close) / prev_close * 100
                                max_single_day_move_pct = EOD_ADVANCED_CONFIG.get("MAX_SINGLE_DAY_MOVE_PCT", 15.0)
                                if single_move_pct > max_single_day_move_pct:
                                    rejection_counts["gap_day"] += 1
                                    continue

                        gap_lookback_bars = EOD_ADVANCED_CONFIG.get("GAP_LOOKBACK_BARS", 10)
                        max_gap_pct = EOD_ADVANCED_CONFIG.get("MAX_GAP_FROM_PRIOR_HIGH_PCT", 3.0)
                        if len(ticker) >= gap_lookback_bars + 1:
                            gap_reference_high = float(ticker["High"].iloc[-(gap_lookback_bars + 1):-1].max())
                            if gap_reference_high > 0:
                                gap_pct = (candle_open - gap_reference_high) / gap_reference_high * 100
                                if gap_pct > max_gap_pct:
                                    rejection_counts["gap_extended"] += 1
                                    continue

                        delivery_pct = delivery_map.get(symbol, None)

                        # ── v5: PREVIOUS CANDLE CONTEXT FILTER ─────────────────────────────
                        lookback = EOD_ADVANCED_CONFIG.get("PRE_BREAKOUT_LOOKBACK_BARS", 5)
                        max_red = EOD_ADVANCED_CONFIG.get("MAX_PRE_BREAKOUT_RED_CANDLES", 2)
                        tight_base_threshold = EOD_ADVANCED_CONFIG.get("TIGHT_BASE_BB_WIDTH_PCTILE", 0.35)
                
                        if len(ticker) >= (lookback + 1):
                            red_count = 0
                            for _ri in range(-(lookback + 1), -1):
                                if _safe_float(ticker["Close"].iloc[_ri]) < _safe_float(ticker["Open"].iloc[_ri]):
                                    red_count += 1
                    
                            if red_count > max_red:
                                # Too many red candles. Reject unless it's a very tight base (volatility compression)
                                is_tight_base = False
                                if "BB_WIDTH_PCTILE" in ticker.columns and len(ticker) >= 2:
                                    if _safe_float(ticker["BB_WIDTH_PCTILE"].iloc[-2]) <= tight_base_threshold:
                                        is_tight_base = True
                                
                                if not is_tight_base:
                                    logger.debug(f"  ⊘ {symbol} pre-breakout trend too red ({red_count}/{lookback}) — skipping")
                                    rejection_counts["pre_breakout_weak"] += 1
                                    continue

                        # ── v5: BASE TIGHTNESS FILTER ──────────────────────────────────────────
                        if "BB_WIDTH_PCTILE" in ticker.columns and len(ticker) >= 2:
                            bb_width_pctile = _safe_float(ticker["BB_WIDTH_PCTILE"].iloc[-2])
                            if bb_width_pctile > EOD_ADVANCED_CONFIG.get("MAX_BB_WIDTH_PCTILE", 0.80):
                                logger.debug(f"  ⊘ {symbol} base too wide (BB Pctile {bb_width_pctile:.2f}) — skipping")
                                rejection_counts["base_too_wide"] += 1
                                continue

                        # ── v6: OBV STRUCTURE — SCORING PENALTY (not hard reject) ──────────
                        # [FINDING-8 FIX] OBV_SLOPE is a 3-bar diff which is noisy on breakout
                        # days. Converted from hard reject to a -5 score penalty applied after
                        # scoring. The scoring engine already penalizes via BASE_WIDTH and
                        # unsustained volume checks.
                        obv_penalty = 0
                        if "OBV_SLOPE" in ticker.columns and not pd.isna(latest.get("OBV_SLOPE")):
                            if _safe_float(latest.get("OBV_SLOPE")) <= EOD_ADVANCED_CONFIG.get("MIN_OBV_SLOPE", 0.0):
                                obv_penalty = -5
                                logger.debug(f"⚠️ {symbol} OBV divergence detected (slope <= 0), applying -5 penalty")

                        atr_val_eod = (
                            _safe_float(latest.get("ATR"))
                            if "ATR" in ticker.columns and not pd.isna(latest.get("ATR"))
                            else None
                        )


                        score, model_version, bayesian_weights = calculate_score(
                            category=category,
                            breakout_count=len(signals),
                            rsi=rsi_val,
                            volume_ratio=volume_ratio,
                            breakout_signals=signals,
                            ticker=ticker,
                            latest=latest,
                            symbol=symbol,
                            timeframe="1d",
                            atr_val=atr_val_eod,
                            delivery_pct=delivery_pct,
                            promoter_pledge_pct=pledge_map.get(symbol),
                            nifty_ret=nifty_ret_20d,
                            regime_ctx=regime_ctx,
                            bayesian_weights=bayesian_weights,
                            bayesian_version=bayesian_version
                        )

                        if score > 0:
                            for pen_name, pen_val in technical_penalties.items():
                                score -= pen_val
                        
                            # [FINDING-8] Apply OBV divergence penalty (soft, not hard reject)
                            score = max(0, score + obv_penalty)
                            try:
                                safe_sector  = "Unknown" if (sector is None or (isinstance(sector, float) and pd.isna(sector))) else str(sector).strip()
                                sector_bonus = rotation_result.score_bonus_for(safe_sector)
                                score = max(0, min(score + sector_bonus, 100))
                            except Exception:
                                pass

                        # ── FORENSIC RISK TIER POLICY CHECK ──────────────────────────────────────
                        forensic_tier = row.get("Forensic_Risk_Tier", "UNKNOWN")
                        if forensic_tier == "REJECT":
                            rejection_counts["forensic_reject"] = rejection_counts.get("forensic_reject", 0) + 1
                            logger.debug(f"  ⊘ {symbol} rejected by Forensic Risk Engine (Tier: REJECT)")
                            continue

                        # ── REGIME-AWARE THRESHOLDS ──────────────────────────────────────
                        if score < global_min_score:
                            rejection_counts["low_score"] += 1
                            try:
                                from near_miss_tracker import log_near_miss
                                log_near_miss(symbol, "EOD", primary_signal, "score_threshold", score, global_min_score, score=score)
                            except Exception:
                                pass
                            continue


                        signal_str = ", ".join(signals.keys() if isinstance(signals, dict) else signals)
                        dedup_key  = f"{category}|{signal_str}|{today_str}|EOD"

                        # [VERSION: EOD_DEDUP_FIX] Fixed dedup check to correctly match DB tuple schema (symbol, breakout_type)
                        if (symbol, "EOD") in cooldown_alerts:
                            rejection_counts["duplicate"] += 1
                            continue

                        # ── Dynamic S/R and Indicator-based SL + Target (EOD mode) ───────
                        sl_result = compute_sl_and_target(
                            entry_price=candle_close,
                            atr=atr_val_eod,
                            candle_range=candle_range,
                            mode="EOD",
                            adx=latest.get("ADX"),
                            rsi=rsi_val,
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
                            ticker=ticker,
                        )
                
                        if sl_result.get("is_rejected"):
                            rejection_counts["low_rr"] += 1  # Reusing this counter for engine rejects
                            from database import save_rejected_alert
                            if not is_test_mode:
                                save_rejected_alert(
                                    symbol=symbol,
                                    scanner="EOD",
                                    rejection_reason=sl_result.get("rejection_reason", "V7 Engine Reject"),
                                    engine_version=sl_result.get("engine_version", "SL_ENGINE_V7.0"),
                                    context={"category": category, "score": score, "sl_result": sl_result}
                                )
                            continue

                        suggested_stop = sl_result["stop_loss"]
                        target_price = sl_result["target_1"]
 
                        above_ema20  = bool(candle_close >= _safe_float(latest.get("EMA20"))) if "EMA20" in ticker.columns and not pd.isna(latest.get("EMA20")) else None
                        above_sma50  = bool(candle_close >= _safe_float(latest.get("SMA50"))) if "SMA50" in ticker.columns and not pd.isna(latest.get("SMA50")) else None
                        # [VERSION: EOD_PATCH_v1.0] [BUG FIX 6] Renamed golden_cross to above_golden_cross to accurately reflect it's a state check
                        above_golden_cross = bool(_safe_float(latest.get("SMA50")) >= _safe_float(latest.get("SMA200"))) if ("SMA50" in ticker.columns and "SMA200" in ticker.columns and not pd.isna(latest.get("SMA50")) and not pd.isna(latest.get("SMA200"))) else None

                        context = {
                            "technicals": {
                                "above_ema20":      above_ema20,
                                "above_sma50":      above_sma50,
                                "above_golden_cross":     above_golden_cross,
                                "body_ratio":       round(body_ratio * 100, 2),
                                "delivery_pct":     round(delivery_pct, 1) if delivery_pct is not None else None,
                                "rsi":              round(rsi_val, 1),
                                "volume_ratio":     round(volume_ratio, 2),
                                "breakout_level":   round(_safe_float(latest.get("PRIOR_20D_HIGH")), 2) if "PRIOR_20D_HIGH" in latest else None,
                                "atr20":            round(_safe_float(latest.get("ATR20")), 2) if "ATR20" in latest else None,
                                "regime":           market_regime,
                                "score":            score
                            },
                            "session": {
                                "open":             round(candle_open, 2),
                                "day_high":         round(candle_high, 2),
                                "day_low":          round(candle_low, 2)
                            },
                            "fundamentals": {
                                "peg":              row.get("PEG Ratio"),
                                "yoy_rev":          row.get("YOY Revenue %"),
                                "yoy_profit":       row.get("YOY Profit %"),
                                "roe":              row.get("ROE %")
                            },
                            "execution": {
                                "sl_method":        sl_result.get("sl_method"),
                                "t_method":         sl_result.get("target_method")
                            },
                            "sl_result": sl_result
                        }
                
                        # Append configuration metadata for forward-testing and analytics
                        context["algo_version"] = ACTIVE_ALGO_VERSION
                        context["algo_params"] = {
                            **EOD_CONFIG,
                            **EOD_ADVANCED_CONFIG,
                            "MIN_BREAKOUT_MARGIN_1D": MIN_BREAKOUT_MARGIN.get("1d"),
                            "MIN_BREAKOUT_VOLUME_RATIO": MIN_BREAKOUT_VOLUME_RATIO,
                            "BASE_TIGHTNESS_THRESHOLD": BASE_TIGHTNESS_THRESHOLD
                        }

                        if not is_test_mode:
                            # [EOD_SAVE_ALERT_FIX_v1.0] BUG-5 FIX: dedup_key was passed as breakout_type (2nd positional).
                            # save_alert_if_new(symbol, breakout_type, alert_time, ...) — 2nd arg must be the scanner type string.
                            # Passing the full dedup_key string was corrupting the breakout_type column in the DB.
                            # BUG-7 FIX: regime_ctx is not a named param in save_alert_if_new — it was silently swallowed by **kwargs.
                            # Derive bayesian_regime (string) from the dict and pass it via the correct named param.
                            _bayesian_regime = regime_ctx.get("trend", "BULL") if isinstance(regime_ctx, dict) else "BULL"
                            _regime_score = float(regime_ctx.get("market_score", 80.0)) if isinstance(regime_ctx, dict) else 80.0

                            # ── Feature F-03 & F-07: Momentum Bonus Injection ──
                            from macro_utils import compute_nifty_rs_rating, compute_sector_regime_rankings
                            from config import RS_BONUS, SECTOR_BONUS, MAX_MOMENTUM_BONUS

                            rs_dict = compute_nifty_rs_rating([symbol])
                            rs_pct_val = float(rs_dict.get(symbol, 50.0))
                            rs_bonus_val = RS_BONUS if rs_pct_val >= 80.0 else 0

                            sector_rankings_dict = compute_sector_regime_rankings()
                            sector_info = sector_rankings_dict.get(sector, {})
                            sector_status = sector_info.get("effective_status", "NEUTRAL")
                            sector_name_val = sector_info.get("sector_name", sector or "")
                            sector_bonus_val = SECTOR_BONUS if sector_status == "TAILWIND" else 0

                            total_momentum_bonus = min(MAX_MOMENTUM_BONUS, rs_bonus_val + sector_bonus_val)
                            base_score_val = int(score)
                            final_score_val = min(100, base_score_val + total_momentum_bonus)

                            saved, reason, cap_alloc, shares = save_alert_if_new(
                                symbol,
                                "EOD",
                                ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                                scanner="EOD",
                                category=category,
                                entry_price=round(candle_close, 2),
                                signals=signal_str,
                                score=final_score_val,
                                rsi=round(rsi_val, 1),
                                volume_ratio=round(volume_ratio, 2),
                                stop_loss=suggested_stop,
                                target_1=sl_result.get("target_1"),
                                target_2=sl_result.get("target_2"),
                                target_3=sl_result.get("target_3"),
                                target_price=target_price,
                                context=context,
                                model_version=model_version,
                                bayesian_regime=_bayesian_regime,
                                bayesian_weights=bayesian_weights,
                                structural_failure_stop=sl_result.get("structural_failure_stop"),
                                target_quality_score=sl_result.get("target_quality"),
                                base_score=base_score_val,
                                rs_bonus=rs_bonus_val,
                                sector_bonus=sector_bonus_val,
                                rs_percentile=rs_pct_val,
                                sector_name=sector_name_val,
                                regime_score=_regime_score
                            )

                        else:
                            saved, reason, cap_alloc, shares = True, "", 0.0, 0

                        if not saved:
                            rejection_counts["duplicate"] += 1
                            continue

                        alerts_by_category.setdefault(category, []).append({
                            "symbol":           symbol,
                            "category":         category,
                            "breakout_signals": list(signals.keys()) if isinstance(signals, dict) else signals,
                            "price":            round(candle_close, 2),
                            "open":             round(candle_open, 2),
                            "day_high":         round(candle_high, 2),
                            "day_low":          round(candle_low, 2),
                            "rsi":              round(rsi_val, 1),
                            "volume_ratio":     round(volume_ratio, 2),
                            "body_ratio":       round(body_ratio * 100),
                            "close_position":   round(close_position * 100),
                            "score":            score,
                            "above_ema20":      above_ema20,
                            "above_sma50":      above_sma50,
                            "above_golden_cross":     above_golden_cross,
                            "atr_stop":         suggested_stop,
                            "target_price":     target_price,
                            "target_2":         sl_result.get("target_2"),
                            "target_3":         sl_result.get("target_3"),
                            "sl_method":        sl_result.get("sl_method"),
                            "t_method":         sl_result.get("target_method"),
                            "rr_ratio":         sl_result.get("natural_rr"),
                            "delivery_pct":     round(delivery_pct, 1) if delivery_pct is not None else None,
                            "peg":              row.get("PEG Ratio"),
                            "yoy_rev":          row.get("YOY Revenue %"),
                            "yoy_profit":       row.get("YOY Profit %"),
                            "roe":              row.get("ROE %"),
                            "capital_allocated": cap_alloc,
                            "shares_bought":     shares
                        })
                        total_alerts += 1

                        # [VERSION: SCANNER_DIAG_LOG_v1.0] Log full diagnostic for every stock that passes ALL filters
                        _last_bar_date = "unknown"
                        try:
                            if isinstance(ticker.index, pd.DatetimeIndex):
                                _last_bar_date = str(ticker.index[-1])[:10]
                            elif "Date" in ticker.columns:
                                _last_bar_date = str(ticker["Date"].iloc[-1])[:10]
                        except Exception:
                            pass
                        logger.info(
                            f"✅ [EOD] PASSED ALL FILTERS: {symbol} | "
                            f"score={score} | vol_ratio={volume_ratio:.2f} | rsi={rsi_val:.1f} | "
                            f"entry=₹{candle_close:.2f} | sl=₹{suggested_stop} | t1=₹{target_price} | "
                            f"last_bar={_last_bar_date} | category={category}"
                        )

                    # [VERSION: EOD_PATCH_v1.0] [BUG FIX 4] Catch general Exceptions rather than specific errors to prevent ZeroDivisionError/AttributeError from crashing the entire scan loop
                    except Exception as e:
                        error_type = type(e).__name__
                        logger.warning(f"⚠️ Exception ({error_type}) processing {symbol}: {str(e)[:100]}")
                        rejection_counts["indicator_fail"] = rejection_counts.get("indicator_fail", 0) + 1
                        if not is_test_mode:
                            try:
                                upsert_fetch_error('yfinance', 'EOD', symbol, '1d', f'processing_error_{error_type}', str(e)[:500])
                            except Exception:
                                logger.exception(f'Failed to upsert fetch error for {symbol}')
                        continue

                # ── VERIFICATION & STATUS ────────────────────────────────────────────────────
                # Removed Telegram notifications (2026-06-17)

                fired = {k: v for k, v in rejection_counts.items() if v > 0}
                if fired:
                    logger.info("   Rejections: " + " | ".join(f"{k}={v}" for k, v in fired.items()))

                # ✅ CRITICAL: Verify alerts were actually saved to database (2026-06-17)
                if total_alerts > 0 and not is_test_mode:
                    if not verify_alerts_saved_today("EOD", total_alerts):
                        logger.critical(f"🚨 CRITICAL ERROR: EOD generated {total_alerts} alerts but save failed!")
                        upsert_scanner_health(
                            scanner_name="EOD",
                            status="DOWN",
                            error_msg=f"CRITICAL: {total_alerts} alerts failed to save to database"
                        )
                        raise RuntimeError("Alert save verification failed - database connectivity issue")

                status = "OK"
                error_msg = None
        
                # [VERSION: EOD_STALE_DEGRADE_FIX] Mark degraded if >30% stale
                stale_count = rejection_counts.get("stale_data", 0)
                total_symbols = len(watchlist)
                if total_symbols > 0 and (stale_count / total_symbols) > 0.30:
                    status = "DEGRADED"
                    error_msg = f"High stale data: {stale_count}/{total_symbols} symbols rejected (likely due to fallback watchlist)"
        
                # [VERSION: EOD_PATCH_v1.3] Log active thread count to monitor potential ThreadPoolExecutor leaks
                active_threads = threading.active_count()
                logger.info(f"🧵 Final Active Thread Count: {active_threads}")
        
                if total_fetched_count < len(watchlist) * 0.95:
                    status = "DEGRADED"
                    error_msg = f"Partial Fetch: {total_fetched_count}/{len(watchlist)} symbols"

                duration_sec = (datetime.now(IST) - start_time).total_seconds()
        
                del all_ticker_data
                locals().pop('ticker', None)
            
            # Check if we fetched enough data overall
            if total_fetched_count < len(watchlist) * 0.70:
                logger.warning(f"⚠️ EOD data fetch returned {total_fetched_count}/{len(watchlist)} symbols (70% minimum required). EOD results may be incomplete.")
            else:
                logger.info(f"✅ Successfully fetched {total_fetched_count} symbols for EOD phase")
            # Insert scan failures via batch
            if scan_failures and not is_test_mode:
                try:
                    from database import get_connection
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            from psycopg2.extras import execute_values
                            execute_values(
                                cur,
                                """
                                INSERT INTO scan_failures (symbol, scanner_name, provider, failure_reason, failed_at, scan_id)
                                VALUES %s
                                """,
                                [(f.symbol, f.scanner_name, f.provider, f.failure_reason, f.failed_at, f.scan_id) for f in scan_failures]
                            )
                        conn.commit()
                except Exception as e:
                    logger.error(f"Failed to record {len(scan_failures)} scan failures: {e}")

            # Map overall outcome & status guard — Missing/unfetched data is a CRITICAL BLOCKER
            outcome = "SUCCESS"
            no_data_count = rejection_counts.get("no_data", 0)
            
            if no_data_count >= len(watchlist) * 0.25:
                status = "DOWN"
                outcome = "FAILED"
                error_msg = f"🚫 CRITICAL BLOCKER: {no_data_count}/{len(watchlist)} symbols unfetched (missing data)"
                logger.error(f"🚨 {error_msg}")
                try:
                    from telegram_engine import send_telegram_message
                    send_telegram_message(f"🚨 <b>CRITICAL BLOCKER: EOD SCANNER FAILED</b>\n{no_data_count}/{len(watchlist)} symbols were unfetched / missing data.")
                except Exception:
                    pass

            elif total_fetched_count < len(watchlist) * 0.70:
                outcome = "PARTIAL"
                status = "DEGRADED"
            elif total_fetched_count == 0:
                outcome = "FAILED"
                status = "DOWN"

            if not is_test_mode:
                try:
                    upsert_scanner_health(
                        scanner_name="EOD",
                        status=status,
                        last_success=datetime.now(IST).isoformat(),
                        today_alerts=total_alerts,
                        processed_count=total_alerts,
                        total_count=len(watchlist),
                        error_msg=error_msg,
                        outcome=outcome,
                        provider_stats=provider_stats_counts,
                        duration_seconds=duration_sec
                    )
                except Exception:
                    logger.exception("❌ Failed to update scanner health for EOD")
                if status == "OK" or status == "DEGRADED":
                    try:
                        insert_notification("admin", f"🚀 EOD Scanner ran successfully. Found {total_alerts} new breakout alerts.", f"Generated {total_alerts} alerts from {len(watchlist)} scanned stocks. Outcome: {outcome}")
                        from push_service import send_push_to_all
                        send_push_to_all("🚀 EOD Scanner OK", f"Found {total_alerts} new breakout alerts.", bypass_throttle=True)
                    except Exception:
                        pass
                elif status == "DEGRADED":
                    try:
                        insert_notification("admin", f"⚠️ EOD Scanner finished with DEGRADED status", error_msg or f"Generated {total_alerts} alerts but data was degraded.")
                        from push_service import send_push_to_all
                        send_push_to_all("⚠️ EOD Scanner DEGRADED", error_msg or "Stale data exceeded limit.")
                    except Exception:
                        pass

            try:
                from funnel_telemetry import log_funnel_metrics
                log_funnel_metrics("EOD", market_regime, len(watchlist), rejection_counts, total_alerts)
            except Exception as e:
                logger.warning(f"Failed to log funnel telemetry: {e}")

            elapsed_time = (datetime.now(IST) - start_time).total_seconds()
            logger.info("\n" + "=" * 80)
            logger.info(f"🛑🛑🛑 [COMPLETE] EOD SCANNER DONE | {elapsed_time:.2f}s | Alerts={total_alerts} | Status={status} 🛑🛑🛑")
            logger.info("=" * 80 + "\n")

            try:
                from memory_profiler import run_purge_with_telemetry
                run_purge_with_telemetry("EOD Scanner Complete")
            except Exception as me:
                logger.debug(f"EOD memory purge failed: {me}")

        return total_alerts


    except Exception as e:
        logger.exception("❌ CRITICAL EOD SCAN ERROR")
        if not is_test_mode:
            try:
                upsert_scanner_health(scanner_name="EOD", status="DOWN", error_msg=str(e))
                insert_notification("admin", f"❌ EOD Scanner CRASHED (DOWN)", f"Error: {str(e)[:200]}")
                from push_service import send_push_to_all
                send_push_to_all("❌ EOD Scanner DOWN", f"Crash: {str(e)[:100]}")
            except Exception:
                pass
        raise  # re-raise so caller can send Telegram crash alert
