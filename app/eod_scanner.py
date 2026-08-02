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
from macro_utils import (
    MarketRegimeEngine, get_nifty_20d_return, get_macro_regime,
    compute_nifty_rs_rating, compute_sector_regime_rankings
)
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

# [VERSION: PERF_PROFILER_v1.0] Stage timing + filter rejection observability
# profile_timing logs duration + RSS delta for each EOD scanner run.
# FilterStats exports per-filter rejection CSV to artifacts/profiling/.
from perf_utils import profile_timing, FilterStats

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
    RS_BONUS,
    SECTOR_BONUS,
    MAX_MOMENTUM_BONUS,
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
_global_lock = ProcessLock("global_scanner_lock")

def start(force: bool = False):
    from database import is_scanner_stopped
    if is_scanner_stopped("EOD"):
        logger.info("🛑 EOD Scanner is STOPPED by Admin. Skipping execution.")
        return 0
    logger.info("⏳ [EOD] Waiting for global scanner lock...")
    if not _global_lock.acquire(blocking=True):
        raise RuntimeError("Failed to acquire global scanner lock.")
    if not _scan_lock.acquire(blocking=False):
        _global_lock.release()
        raise RuntimeError("Scanner is already actively running!")
    try:
        return _start_wrapper(force)
    finally:
        _scan_lock.release()
        _global_lock.release()


def _safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)
    except Exception:
        return default


def _check_eod_conditions(
    ticker: pd.DataFrame,
    latest: pd.Series,
    symbol: str,
    mode: str = "production",
    prior_high_source: str = "indicator",
    delivery_pct: float = None,
    nifty_ret: float = None,
    regime_ctx: dict = None,
) -> dict:
    """
    Shared EOD breakout condition checks for both UI and production paths.

    Args:
        ticker: full DataFrame with indicators applied
        latest: the last row of ticker
        symbol: stock symbol for logging
        mode: "ui" returns reasons list; "production" returns rejection key + logs
        prior_high_source: "indicator" uses PRIOR_20D_HIGH column, "raw" uses 20-bar max
        delivery_pct, nifty_ret, regime_ctx: passed through for scoring

    Returns dict:
        passed: bool
        reason: str or None
        candle_penalty: int
        body_ratio, close_pos, wick_ratio, rsi_val, volume_ratio, avg_volume, atr20
        candle_close, candle_open, candle_high, candle_low, candle_range, candle_body
        prior_high, atr_extension, gap_pct (may be None if not computed)
    """
    candle_high  = _safe_float(latest.get("High"))
    candle_low   = _safe_float(latest.get("Low"))
    candle_open  = _safe_float(latest.get("Open"))
    candle_close = _safe_float(latest.get("Close"))
    candle_range = candle_high - candle_low
    candle_body  = abs(candle_close - candle_open)
    upper_wick   = candle_high - max(candle_close, candle_open)

    if candle_range <= 0:
        return {"passed": False, "reason": "Zero candle range"}

    body_ratio  = candle_body / candle_range
    close_pos   = (candle_close - candle_low) / candle_range
    wick_ratio  = upper_wick / candle_range

    if len(ticker) >= 22:
        avg_volume = float(ticker["Volume"].iloc[-21:-1].mean())
    else:
        avg_volume = float(ticker["Volume"].iloc[:-1].mean())

    if avg_volume <= 0:
        return {"passed": False, "reason": "Zero avg volume"}

    volume_ratio = _safe_float(latest.get("Volume")) / avg_volume
    rsi_val      = _safe_float(latest.get("RSI"), 50.0)
    atr20        = _safe_float(latest.get("ATR20"), _safe_float(latest.get("ATR"), candle_close * 0.025))

    # ── Shared hard gates ──────────────────────────────────────────────────
    if volume_ratio < MIN_VOLUME_RATIO:
        return {"passed": False, "reason": f"Volume ratio {volume_ratio:.2f}x < {MIN_VOLUME_RATIO:.1f}x"}
    if avg_volume < MIN_AVG_VOLUME_SHARES:
        return {"passed": False, "reason": f"Avg volume {avg_volume:.0f} < {MIN_AVG_VOLUME_SHARES:.0f}"}
    if candle_close < MIN_STOCK_PRICE:
        return {"passed": False, "reason": f"Close ₹{candle_close:.2f} < ₹{MIN_STOCK_PRICE:.0f} floor"}
    if not (MIN_RSI <= rsi_val <= MAX_RSI):
        return {"passed": False, "reason": f"RSI {rsi_val:.1f} outside {MIN_RSI}-{MAX_RSI}"}

    # ── Prior high & breakout check ────────────────────────────────────────
    if prior_high_source == "indicator":
        if "PRIOR_20D_HIGH" not in ticker.columns or pd.isna(latest.get("PRIOR_20D_HIGH")):
            return {"passed": False, "reason": "Missing PRIOR_20D_HIGH"}
        prior_high = _safe_float(latest.get("PRIOR_20D_HIGH"))
        if prior_high <= 0:
            return {"passed": False, "reason": "Invalid PRIOR_20D_HIGH"}
        if candle_close <= prior_high:
            return {"passed": False, "reason": f"Close ₹{candle_close:.2f} <= Prior High ₹{prior_high:.2f}"}
    else:
        lookback = 20 if len(ticker) >= 21 else len(ticker) - 1
        prior_high = float(ticker['High'].iloc[-lookback-1:-1].max()) if lookback > 0 else float(ticker['High'].max())
        if candle_close <= prior_high:
            return {"passed": False, "reason": f"Close ₹{candle_close:.2f} <= 20D High ₹{prior_high:.2f}"}

    # ── ATR checks ─────────────────────────────────────────────────────────
    if "ATR20" in ticker.columns and not pd.isna(latest.get("ATR20")):
        atr20 = _safe_float(latest.get("ATR20"))
    if atr20 <= 0:
        return {"passed": False, "reason": "ATR20 <= 0"}

    atr_extension = (candle_close - prior_high) / atr20
    max_ext = EOD_ADVANCED_CONFIG.get("MAX_EXTENDED_BREAKOUT_ATR_MULT", 1.5)
    if atr_extension > max_ext:
        pass  # Soft penalty, not hard reject

    # ── ATR expansion ──────────────────────────────────────────────────────
    if candle_range / atr20 < EOD_ADVANCED_CONFIG.get("MIN_ATR_EXPANSION_RATIO", 0.9):
        return {"passed": False, "reason": f"ATR expansion {candle_range/atr20:.2f} < {EOD_ADVANCED_CONFIG.get('MIN_ATR_EXPANSION_RATIO', 0.9)}"}

    # ── Trend alignment ────────────────────────────────────────────────────
    if "EMA20" in ticker.columns and not pd.isna(latest.get("EMA20")):
        if candle_close < _safe_float(latest.get("EMA20")):
            return {"passed": False, "reason": f"Below EMA20"}
    if "SMA50" in ticker.columns and not pd.isna(latest.get("SMA50")):
        if candle_close < _safe_float(latest.get("SMA50")):
            return {"passed": False, "reason": "Below SMA50"}
    if "ADX" in ticker.columns and not pd.isna(latest.get("ADX")):
        if _safe_float(latest.get("ADX")) < ADX_MIN_THRESHOLD:
            return {"passed": False, "reason": f"ADX {_safe_float(latest.get('ADX')):.1f} < {ADX_MIN_THRESHOLD}"}

    # ── 52W high distance ──────────────────────────────────────────────────
    if "HIGH_52W" in ticker.columns and not pd.isna(latest.get("HIGH_52W")):
        high_52w = _safe_float(latest.get("HIGH_52W"))
        if high_52w > 0:
            pct_from_high = (high_52w - candle_close) / high_52w * 100
            if pct_from_high > MAX_DISTANCE_FROM_52W_HIGH_PCT:
                return {"passed": False, "reason": f"Too far from 52W high ({pct_from_high:.1f}%)"}

    # ── Single-day move cap ────────────────────────────────────────────────
    if len(ticker) >= 2:
        prev_close = _safe_float(ticker["Close"].iloc[-2])
        if prev_close > 0:
            single_move_pct = abs(candle_close - prev_close) / prev_close * 100
            if single_move_pct > EOD_ADVANCED_CONFIG.get("MAX_SINGLE_DAY_MOVE_PCT", 15.0):
                return {"passed": False, "reason": f"Single-day move {single_move_pct:.1f}% > cap"}

    # ── Pre-breakout candle context ────────────────────────────────────────
    lookback_ctx = EOD_ADVANCED_CONFIG.get("PRE_BREAKOUT_LOOKBACK_BARS", 5)
    max_red = EOD_ADVANCED_CONFIG.get("MAX_PRE_BREAKOUT_RED_CANDLES", 2)
    tight_base_threshold = EOD_ADVANCED_CONFIG.get("TIGHT_BASE_BB_WIDTH_PCTILE", 0.35)
    if len(ticker) >= (lookback_ctx + 1):
        red_count = sum(
            1 for _ri in range(-(lookback_ctx + 1), -1)
            if _safe_float(ticker["Close"].iloc[_ri]) < _safe_float(ticker["Open"].iloc[_ri])
        )
        if red_count > max_red:
            is_tight_base = False
            if "BB_WIDTH_PCTILE" in ticker.columns and len(ticker) >= 2:
                if _safe_float(ticker["BB_WIDTH_PCTILE"].iloc[-2]) <= tight_base_threshold:
                    is_tight_base = True
            if not is_tight_base:
                return {"passed": False, "reason": f"Pre-breakout weak ({red_count}/{lookback_ctx} red candles)"}

    # ── Base width ─────────────────────────────────────────────────────────
    if "BB_WIDTH_PCTILE" in ticker.columns and len(ticker) >= 2:
        bb_width_pctile = _safe_float(ticker["BB_WIDTH_PCTILE"].iloc[-2])
        if bb_width_pctile > EOD_ADVANCED_CONFIG.get("MAX_BB_WIDTH_PCTILE", 0.80):
            return {"passed": False, "reason": f"Base too wide (BB Pctile {bb_width_pctile:.2f})"}

    # ── Candle quality penalties (soft, not hard) ──────────────────────────
    candle_penalty = 0
    if body_ratio < MIN_BODY_RATIO:
        shortfall = (MIN_BODY_RATIO - body_ratio) / MIN_BODY_RATIO
        candle_penalty += min(15, int(shortfall * 30))
    if candle_close <= candle_open:
        candle_penalty += 5
    if close_pos < MIN_CLOSE_POSITION:
        shortfall = (MIN_CLOSE_POSITION - close_pos) / MIN_CLOSE_POSITION
        candle_penalty += min(10, int(shortfall * 20))
    if wick_ratio > MAX_UPPER_WICK_RATIO:
        excess = (wick_ratio - MAX_UPPER_WICK_RATIO) / MAX_UPPER_WICK_RATIO
        candle_penalty += min(10, int(excess * 20))

    # ── Gap penalty (soft) ─────────────────────────────────────────────────
    gap_lookback_bars = EOD_ADVANCED_CONFIG.get("GAP_LOOKBACK_BARS", 10)
    max_gap_pct = EOD_ADVANCED_CONFIG.get("MAX_GAP_FROM_PRIOR_HIGH_PCT", 3.0)
    gap_pct = None
    if len(ticker) >= gap_lookback_bars + 1:
        gap_ref_high = float(ticker["High"].iloc[-(gap_lookback_bars + 1):-1].max())
        if gap_ref_high > 0:
            gap_pct = (candle_open - gap_ref_high) / gap_ref_high * 100

    return {
        "passed": True,
        "candle_penalty": candle_penalty,
        "body_ratio": body_ratio,
        "close_pos": close_pos,
        "wick_ratio": wick_ratio,
        "rsi_val": rsi_val,
        "volume_ratio": volume_ratio,
        "avg_volume": avg_volume,
        "atr20": atr20,
        "candle_close": candle_close,
        "candle_open": candle_open,
        "candle_high": candle_high,
        "candle_low": candle_low,
        "candle_range": candle_range,
        "candle_body": candle_body,
        "upper_wick": upper_wick,
        "prior_high": prior_high,
        "atr_extension": atr_extension,
        "gap_pct": gap_pct,
    }

def evaluate_eod_symbol(symbol: str, df: pd.DataFrame, fund_data: dict = None, regime_ctx: dict = None) -> dict:
    """
    Evaluates a single symbol against the production EOD breakout scanner rules.
    Runs full breakout detection, candle body/wick gates, ATR expansion, trend alignment, scoring engine, and target calculations without side effects.
    """
    if df is None or df.empty or len(df) < 50:
        return {
            "status": "NO",
            "reasons": [f"Insufficient historical price data ({len(df) if df is not None else 0} bars < 50 minimum)"],
            "score": 0.0,
            "qualified": False
        }

    ticker = df.copy()
    if isinstance(ticker.columns, pd.MultiIndex):
        ticker.columns = ticker.columns.get_level_values(0)
    ticker = ticker.loc[:, ~ticker.columns.duplicated()]

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    for col in required_cols:
        if col not in ticker.columns:
            return {"status": "NO", "reasons": [f"Missing required price column '{col}'"], "score": 0.0, "qualified": False}
        ticker[col] = pd.Series(ticker[col]).astype(float)

    ticker = ticker.dropna(subset=required_cols)
    if ticker.empty or len(ticker) < 50:
        return {"status": "NO", "reasons": [f"Insufficient valid bars ({len(ticker)} < 50)"], "score": 0.0, "qualified": False}

    ticker = apply_indicators(ticker, timeframe="1d")
    latest = ticker.iloc[-1]

    # [FIX P6-13] Use shared condition check for consistency with production path
    cond = _check_eod_conditions(
        ticker=ticker, latest=latest, symbol=symbol, mode="ui",
        prior_high_source="raw",
    )
    if not cond.get("passed"):
        return {
            "status": "NO",
            "reasons": [cond.get("reason", "Condition check failed")],
            "score": 0.0,
            "qualified": False,
            "entry_price": _safe_float(latest.get("Close")),
            "atr_20": cond.get("atr20", 0)
        }

    candle_close = cond["candle_close"]
    candle_low   = cond["candle_low"]
    candle_high  = cond["candle_high"]
    candle_range = cond["candle_range"]
    prior_high   = cond["prior_high"]
    rsi_val      = cond["rsi_val"]
    vol_ratio    = cond["volume_ratio"]
    atr20        = cond["atr20"]

    signals = detect_breakouts(ticker, timeframe="1d")
    score, _, _ = calculate_score(
        category=fund_data.get("Category", "EQUITY") if fund_data else "EQUITY",
        breakout_count=len(signals),
        rsi=rsi_val,
        volume_ratio=vol_ratio,
        breakout_signals=signals,
        ticker=ticker,
        latest=latest,
        symbol=symbol,
        timeframe="1d",
        atr_val=atr20,
        regime_ctx=regime_ctx
    )

    if score > 0:
        candle_pen = cond.get("candle_penalty", 0)
        score = max(0, score - candle_pen)

        # Gap-and-go extension penalty
        atr_ext = (candle_close - prior_high) / atr20 if atr20 > 0 else 0
        max_ext = EOD_ADVANCED_CONFIG.get("MAX_EXTENDED_BREAKOUT_ATR_MULT", 1.5)
        if atr_ext > max_ext:
            pen_mult = EOD_ADVANCED_CONFIG.get("GAP_AND_GO_PENALTY_MULT", 10)
            max_pen = EOD_ADVANCED_CONFIG.get("GAP_AND_GO_MAX_PENALTY", 20)
            score = max(0, score - min(max_pen, (atr_ext - max_ext) * pen_mult))

        # Gap penalty (gapping > 3% above prior high)
        gap_pct = cond.get("gap_pct")
        if gap_pct is not None:
            max_gap_pct = EOD_ADVANCED_CONFIG.get("MAX_GAP_FROM_PRIOR_HIGH_PCT", 3.0)
            if gap_pct > max_gap_pct:
                excess = gap_pct - max_gap_pct
                score = max(0, score - min(20, int(excess * 3)))

        # OBV divergence penalty
        if "OBV_SLOPE" in ticker.columns and not pd.isna(latest.get("OBV_SLOPE")):
            if _safe_float(latest.get("OBV_SLOPE")) <= EOD_ADVANCED_CONFIG.get("MIN_OBV_SLOPE", 0.0):
                score = max(0, score - 5)

    score_threshold = SCORE_THRESHOLDS.get("1d", 82)
    is_qualified = (score >= score_threshold)

    sl_result = compute_sl_and_target(
        entry_price=candle_close,
        atr=atr20,
        candle_range=candle_range,
        mode="EOD",
        rsi=rsi_val,
        candle_low=candle_low,
        ticker=ticker
    )

    status_str = "CORE MET" if is_qualified else "NO"
    reasons = [f"Clean Breakout Close (₹{candle_close:.2f} > ₹{prior_high:.2f}) | Volume Surge {vol_ratio:.2f}x ≥ 1.8x | EOD Score {score:.1f}/100"] if is_qualified else [f"Score {score:.1f} < {score_threshold} minimum threshold"]

    return {
        "status": status_str,
        "reasons": reasons,
        "score": score,
        "qualified": is_qualified,
        "entry_price": candle_close,
        "stop_loss": sl_result.get("stop_loss"),
        "target_1": sl_result.get("target_1"),
        "target_2": sl_result.get("target_2"),
        "target_3": sl_result.get("target_3"),
        "target_4": sl_result.get("target_4"),
        "atr_20": atr20
    }



# [VERSION: PERF_PROFILER_v1.0] Wrap the scan body so every EOD run reports
# wall-clock time, memory delta (RSS), and any top-level exception — all without
# changing any business logic or scanner decision paths.
@profile_timing("eod_scanner._start_wrapper", log_to_file=True)
def _start_wrapper(force: bool = False):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    start_time = datetime.now(IST)
    total_alerts = 0
    total_fetched_count = 0
    duration_sec = 0.0

    is_test_mode = True  # Safe default
    init_db()
    # Initialize the fundamentals cache into the DatasetRegistry (DURABLE)
    from fundamentals_cache import init_fundamentals_registry
    init_fundamentals_registry()
    
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
            delivery_days_back = 0
            delivery_found = False
            seen_delivery_dates = set()

            for days_back in range(0, 5):
                candidate = ist_now.date() - timedelta(days=days_back)
                while candidate.weekday() >= 5:
                    candidate -= timedelta(days=1)
                
                if candidate in seen_delivery_dates:
                    continue
                seen_delivery_dates.add(candidate)

                try:
                    delivery_map = fetch_delivery_data(candidate, skip_db_save=(days_back > 0))
                    if delivery_map:
                        delivery_days_back = (ist_now.date() - candidate).days
                        delivery_found = True
                        if delivery_days_back > 0:
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
                    logger.debug(f"Delivery fetch failed for {candidate}: {e}")

            try:
                rotation_result = get_sector_scores()
            except Exception:
                rotation_result = SectorRotationResult({}, set(), set(), "", datetime.now(IST).date(), 0.0)

            # Pre-compute macro momentum rankings for entire watchlist once before scan loop
            try:
                rs_dict = compute_nifty_rs_rating(symbols)
            except Exception as _rse:
                logger.warning(f"Failed to pre-compute RS ratings: {_rse}")
                rs_dict = {}

            try:
                sector_rankings_dict = compute_sector_regime_rankings()
            except Exception as _sre:
                logger.warning(f"Failed to pre-compute sector regime rankings: {_sre}")
                sector_rankings_dict = {}

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

        # [FIX P1-4] Cap the regime-adjusted threshold at 87 to prevent over-rejection
        # in sideways/bear regimes. Regime modifiers above +5 were pushing the threshold
        # to 92-95, killing valid setups that the scoring engine already penalizes for weakness.
        global_min_score = min(global_min_score, 87)
        
        logger.info(f"📊 Score threshold for {market_regime} regime: {global_min_score}")

        import gc, time
        BATCH_SIZE = int(os.environ.get("EOD_FETCH_BATCH_SIZE", "50"))
        
        from config import ALERT_COOLDOWN_MINUTES
        cooldown_alerts = get_recent_alerts_for_scanner("EOD", ALERT_COOLDOWN_MINUTES.get("EOD", 1440))
        
        total_fetched_count = 0
        logger.info(f"📥 Processing EOD phase in chunks of {BATCH_SIZE}...")

        from memory_profiler import chunk_iterable, BatchMemoryTracker
        total_batches = (len(watchlist) + BATCH_SIZE - 1) // BATCH_SIZE

        approved_candidates = []
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
                
                    for idx, row_tuple in enumerate(chunk_df.itertuples(index=False), start=1):
                        symbol = "UNKNOWN"
                        try:
                            row = row_tuple._asdict() if hasattr(row_tuple, '_asdict') else (row_tuple if isinstance(row_tuple, dict) else {})
                            symbol   = row.get("Stock", "UNKNOWN")
                            category = row.get("Category", "MIDCAP")
                            sector   = row.get("Sector", None)

                            if symbol in get_live_blacklist():
                                continue

                            # Robust symbol resolution across .NS / .BO suffixes
                            ticker_data = all_ticker_data.get(symbol)
                            if ticker_data is None:
                                ticker_data = all_ticker_data.get(f"{symbol}.NS") or all_ticker_data.get(f"{symbol}.BO") or all_ticker_data.get(symbol.split('.')[0])

                            if ticker_data is None:
                                rejection_counts["no_data"] += 1
                                provider_stats_counts["EMPTY_DATA"] += 1
                                scan_failures.append(ScanFailure(symbol=symbol, scanner_name="EOD", provider="unknown", failure_reason="missing data", scan_id=scan_id))
                                continue

                            if isinstance(ticker_data, ProviderResult):
                                res = ticker_data
                                provider_stats_counts[res.name] += 1
                                if res != ProviderResult.SUCCESS:
                                    rejection_counts["no_data"] += 1
                                    scan_failures.append(ScanFailure(symbol=symbol, scanner_name="EOD", provider="unknown", failure_reason=f"Provider error: {res.name}", scan_id=scan_id))
                                    continue
                            else:
                                provider_stats_counts["SUCCESS"] += 1

                            ticker = ticker_data.copy()

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

                            # [PERFORMANCE_FIX] apply_indicators() is now pre-calculated by price_cache.py 
                            # immediately after downloading the dataset. Doing it once there instead of 
                            # 5000 times here eliminates 4-5 minutes of latency per batch!
                            # ticker = apply_indicators(ticker, timeframe="1d")

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
                                logger.info(f"REJECTION: {symbol} (Phase: LIQUIDITY_FILTER, Reason: 20D average volume is zero)")
                                rejection_counts["zero_avg_volume"] += 1
                                continue

                            volume_ratio = _safe_float(latest.get("Volume")) / avg_volume

                            candle_high  = _safe_float(latest.get("High"))
                            candle_low   = _safe_float(latest.get("Low"))
                            candle_open  = _safe_float(latest.get("Open"))
                            candle_close = _safe_float(latest.get("Close"))
                            candle_range = candle_high - candle_low
                            candle_body  = abs(candle_close - candle_open)
                            upper_wick   = candle_high - max(candle_close, candle_open)

                            if candle_range <= 0:
                                rejection_counts["zero_candle_range"] += 1
                                continue

                            body_ratio     = candle_body / candle_range
                            close_position = (candle_close - candle_low) / candle_range
                            wick_ratio     = upper_wick / candle_range
                            rsi_val        = _safe_float(latest.get("RSI"))

                            # [FIX P1-3] Converted hard candle gates to scoring penalties.
                            # Previously these 4 conditions hard-rejected ~40% of valid breakouts.
                            # Now each applies a proportional penalty to the final score.
                            candle_penalty = 0
                            if body_ratio < MIN_BODY_RATIO:
                                shortfall = (MIN_BODY_RATIO - body_ratio) / MIN_BODY_RATIO
                                pen = min(15, int(shortfall * 30))
                                candle_penalty += pen
                                logger.debug(f"⚠️ {symbol} body_ratio penalty: -{pen} (ratio={body_ratio:.2f} < {MIN_BODY_RATIO})")
                            if candle_close <= candle_open:
                                candle_penalty += 5
                                logger.debug(f"⚠️ {symbol} bearish_candle penalty: -5")
                            if close_position < MIN_CLOSE_POSITION:
                                shortfall = (MIN_CLOSE_POSITION - close_position) / MIN_CLOSE_POSITION
                                pen = min(10, int(shortfall * 20))
                                candle_penalty += pen
                                logger.debug(f"⚠️ {symbol} close_position penalty: -{pen} (pos={close_position:.2f} < {MIN_CLOSE_POSITION})")
                            if wick_ratio > MAX_UPPER_WICK_RATIO:
                                excess = (wick_ratio - MAX_UPPER_WICK_RATIO) / MAX_UPPER_WICK_RATIO
                                pen = min(10, int(excess * 20))
                                candle_penalty += pen
                                logger.debug(f"⚠️ {symbol} upper_wick penalty: -{pen} (wick={wick_ratio:.2f} > {MAX_UPPER_WICK_RATIO})")
                            if volume_ratio < MIN_VOLUME_RATIO:
                                logger.info(f"REJECTION: {symbol} (Phase: VOLUME_RATIO, Reason: Volume ratio {volume_ratio:.2f}x < {MIN_VOLUME_RATIO:.2f}x)")
                                rejection_counts["low_volume"] += 1
                                continue
                            if avg_volume < MIN_AVG_VOLUME_SHARES:
                                logger.info(f"REJECTION: {symbol} (Phase: LIQUIDITY_FILTER, Reason: Avg volume {avg_volume:.0f} < {MIN_AVG_VOLUME_SHARES:.0f} shares)")
                                rejection_counts["low_avg_volume"] += 1
                                continue
                            if candle_close < MIN_STOCK_PRICE:
                                logger.info(f"REJECTION: {symbol} (Phase: PRICE_FLOOR, Reason: Close ₹{candle_close:.2f} < ₹{MIN_STOCK_PRICE:.2f})")
                                rejection_counts["penny_stock"] += 1
                                continue
                            if not (MIN_RSI <= rsi_val <= MAX_RSI):
                                logger.info(f"REJECTION: {symbol} (Phase: RSI_GATE, Reason: RSI {rsi_val:.1f} outside {MIN_RSI}-{MAX_RSI} range)")
                                rejection_counts["rsi_range"] += 1
                                continue

                            # ── v6: STRUCTURAL BREAKOUT FILTERS ─────────────────────────────
                            # [VERSION: EOD_PATCH_v1.0] [BUG FIX 2] Added explicit outer else rejection to avoid silent bypass of structural filters
                            if "PRIOR_20D_HIGH" not in ticker.columns or pd.isna(latest.get("PRIOR_20D_HIGH")):
                                logger.info(f"REJECTION: {symbol} (Phase: STRUCTURAL_BREAKOUT, Reason: Missing PRIOR_20D_HIGH indicator)")
                                rejection_counts["missing_atr"] += 1
                                continue

                            prior_high = _safe_float(latest.get("PRIOR_20D_HIGH"))
                            if prior_high <= 0:
                                logger.info(f"REJECTION: {symbol} (Phase: STRUCTURAL_BREAKOUT, Reason: Invalid prior 20D high ₹{prior_high:.2f})")
                                rejection_counts["no_structural_breakout"] += 1
                                continue

                            if candle_close <= prior_high:
                                logger.info(f"REJECTION: {symbol} (Phase: STRUCTURAL_BREAKOUT, Reason: Close ₹{candle_close:.2f} <= Prior 20D High ₹{prior_high:.2f})")
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
                            min_atr_expansion = EOD_ADVANCED_CONFIG.get("MIN_ATR_EXPANSION_RATIO", 0.9)
                            if candle_range / atr20 < min_atr_expansion:
                                logger.info(f"REJECTION: {symbol} (Phase: ATR_EXPANSION, Reason: Candle range / ATR20 ({candle_range / atr20:.2f}) < {min_atr_expansion:.1f})")
                                rejection_counts["no_atr_expansion"] += 1
                                continue

                            # [FIX P1-2] Removed redundant BB_WIDTH_PCTILE check on the current bar.
                            # The base_too_wide filter at line 776 already checks the PREVIOUS bar's
                            # BB_WIDTH_PCTILE, which is the correct pre-breakout snapshot. Checking the
                            # current (breakout) bar's BB width is self-defeating because BB expands on
                            # breakout candles.

                            if "EMA20" in ticker.columns and not pd.isna(latest.get("EMA20")):
                                if candle_close < _safe_float(latest.get("EMA20")):
                                    logger.info(f"REJECTION: {symbol} (Phase: EMA20_TREND, Reason: Close ₹{candle_close:.2f} < EMA20 ₹{_safe_float(latest.get('EMA20')):.2f})")
                                    rejection_counts["below_ema20"] += 1
                                    continue

                            if "SMA50" in ticker.columns and not pd.isna(latest.get("SMA50")):
                                if candle_close < _safe_float(latest.get("SMA50")):
                                    logger.info(f"REJECTION: {symbol} (Phase: SMA50_TREND, Reason: Close ₹{candle_close:.2f} < SMA50 ₹{_safe_float(latest.get('SMA50')):.2f})")
                                    rejection_counts["below_sma50"] += 1
                                    continue

                            if "ADX" in ticker.columns and not pd.isna(latest.get("ADX")):
                                if _safe_float(latest.get("ADX")) < ADX_MIN_THRESHOLD:
                                    logger.info(f"REJECTION: {symbol} (Phase: ADX_GATE, Reason: ADX {_safe_float(latest.get('ADX')):.1f} < {ADX_MIN_THRESHOLD})")
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

                            # [FIX P1-1] Removed hard 3% gap filter — gap penalty is now
                            # applied as a scoring penalty via technical_penalties below.
                            # Previously this hard-rejected valid breakout candidates that
                            # gapped up on strong institutional demand.

                            # [FIX P1-1] Gap penalty: proportional scoring penalty instead of hard reject.
                            # Stocks gapping up >3% on breakout day are penalized but not killed.
                            gap_lookback_bars = EOD_ADVANCED_CONFIG.get("GAP_LOOKBACK_BARS", 10)
                            max_gap_pct = EOD_ADVANCED_CONFIG.get("MAX_GAP_FROM_PRIOR_HIGH_PCT", 3.0)
                            if len(ticker) >= gap_lookback_bars + 1:
                                gap_reference_high = float(ticker["High"].iloc[-(gap_lookback_bars + 1):-1].max())
                                if gap_reference_high > 0:
                                    gap_pct = (candle_open - gap_reference_high) / gap_reference_high * 100
                                    if gap_pct > max_gap_pct:
                                        excess = gap_pct - max_gap_pct
                                        pen = min(20, int(excess * 3))
                                        technical_penalties["gap_extended"] = pen
                                        logger.debug(f"⚠️ {symbol} gap penalty: -{pen} (gap={gap_pct:.1f}%)")

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
                                        rejection_counts["prior_red_candles"] = rejection_counts.get("prior_red_candles", 0) + 1
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

                            # Default momentum values in case score <= 0 or gating fails
                            rs_pct_val = float(rs_dict.get(symbol, 50.0))
                            rs_bonus_val = 0
                            sector_bonus_val = 0
                            total_momentum_bonus = 0
                            base_score_val = int(score)

                            if score > 0:
                                for pen_name, pen_val in technical_penalties.items():
                                    score -= pen_val
                        
                                # [FINDING-8] Apply OBV divergence penalty (soft, not hard reject)
                                score = max(0, score + obv_penalty)
                                # [FIX P1-3] Apply candle quality penalty (body/wick/close position)
                                score = max(0, score - candle_penalty)

                                base_score_val = int(score)

                                # ── Feature F-03 & F-07: Momentum Bonus Injection (Prior to Score Gate) ──
                                rs_bonus_val = RS_BONUS if rs_pct_val >= 80.0 else 0

                                safe_sec_str = "Unknown" if (sector is None or (isinstance(sector, float) and pd.isna(sector))) else str(sector).strip()
                                sector_info = sector_rankings_dict.get(safe_sec_str, {})
                                sector_status = sector_info.get("effective_status", "NEUTRAL")
                                sector_bonus_val = SECTOR_BONUS if sector_status == "TAILWIND" else 0

                                total_momentum_bonus = min(MAX_MOMENTUM_BONUS, rs_bonus_val + sector_bonus_val)
                                score = max(0, min(100, score + total_momentum_bonus))

                            # ── FORENSIC RISK TIER POLICY CHECK ──────────────────────────────────────
                            forensic_tier = row.get("Forensic_Risk_Tier", "UNKNOWN")
                            if forensic_tier == "REJECT":
                                rejection_counts["forensic_reject"] = rejection_counts.get("forensic_reject", 0) + 1
                                logger.debug(f"  ⊘ {symbol} rejected by Forensic Risk Engine (Tier: REJECT)")
                                continue

                            signal_str = ", ".join(signals.keys() if isinstance(signals, dict) else signals)

                            # ── REGIME-AWARE THRESHOLDS ──────────────────────────────────────
                            if score < global_min_score:
                                rejection_counts["low_score"] += 1
                                logger.info(f"REJECTION: {symbol} (Phase: SCORE_GATE, Reason: Score {score:.1f} < threshold {global_min_score})")
                                try:
                                    from near_miss_tracker import log_near_miss
                                    log_near_miss(symbol, "EOD", signal_str, "score_threshold", score, global_min_score, score=score)
                                except Exception:
                                    pass
                                continue

                            logger.info(f"📍 PICKED [EOD: IN BETWEEN]: {symbol} @ ₹{candle_close:.2f} (Score: {score:.1f}, Prior High: ₹{prior_high:.2f})")

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
                            if delivery_found and delivery_days_back > 0:
                                context["delivery_data_status"] = "missing_used_fallback"
                            elif not delivery_found:
                                context["delivery_data_status"] = "unavailable"
                            
                            context["algo_params"] = {
                                **EOD_CONFIG,
                                **EOD_ADVANCED_CONFIG,
                                "MIN_BREAKOUT_MARGIN_1D": MIN_BREAKOUT_MARGIN.get("1d"),
                                "MIN_BREAKOUT_VOLUME_RATIO": MIN_BREAKOUT_VOLUME_RATIO,
                                "BASE_TIGHTNESS_THRESHOLD": BASE_TIGHTNESS_THRESHOLD
                            }

                            if not is_test_mode:
                                _bayesian_regime = regime_ctx.get("trend", "BULL") if isinstance(regime_ctx, dict) else "BULL"
                                _regime_score = float(regime_ctx.get("market_score", 80.0)) if isinstance(regime_ctx, dict) else 80.0

                                safe_sec_str = "Unknown" if (sector is None or (isinstance(sector, float) and pd.isna(sector))) else str(sector).strip()
                                sector_info = sector_rankings_dict.get(safe_sec_str, {})
                                sector_name_val = sector_info.get("sector_name", sector or "")

                                cand = {
                                    "symbol": symbol,
                                    "breakout_type": "EOD",
                                    "alert_time": ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                                    "scanner": "EOD",
                                    "category": category,
                                    "entry_price": round(candle_close, 2),
                                    "signals": signal_str,
                                    "score": int(score),
                                    "rsi": round(rsi_val, 1),
                                    "volume_ratio": round(volume_ratio, 2),
                                    "stop_loss": suggested_stop,
                                    "target_1": sl_result.get("target_1"),
                                    "target_2": sl_result.get("target_2"),
                                    "target_3": sl_result.get("target_3"),
                                    "target_price": target_price,
                                    "context": context,
                                    "model_version": model_version,
                                    "bayesian_regime": _bayesian_regime,
                                    "bayesian_weights": bayesian_weights,
                                    "structural_failure_stop": sl_result.get("structural_failure_stop"),
                                    "target_quality_score": sl_result.get("target_quality"),
                                    "base_score": base_score_val,
                                    "rs_bonus": rs_bonus_val,
                                    "sector_bonus": sector_bonus_val,
                                    "rs_percentile": rs_pct_val,
                                    "sector_name": sector_name_val,
                                    "regime_score": _regime_score,
                                    # Extra data for logging and tracking
                                    "_candle_open": candle_open,
                                    "_candle_high": candle_high,
                                    "_candle_low": candle_low,
                                    "_body_ratio": body_ratio,
                                    "_close_position": close_position,
                                    "_above_ema20": above_ema20,
                                    "_above_sma50": above_sma50,
                                    "_above_golden_cross": above_golden_cross,
                                    "_sl_method": sl_result.get("sl_method"),
                                    "_target_method": sl_result.get("target_method"),
                                    "_natural_rr": sl_result.get("natural_rr"),
                                    "_delivery_pct": delivery_pct,
                                    "_peg": row.get("PEG Ratio"),
                                    "_yoy_rev": row.get("YOY Revenue %"),
                                    "_yoy_profit": row.get("YOY Profit %"),
                                    "_roe": row.get("ROE %"),
                                    "_ticker": ticker
                                }
                                approved_candidates.append(cand)
                            else:
                                pass

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

        # ── MAX ALERTS ENFORCEMENT & PERSISTENCE ──────────────────────────────────────────
        if approved_candidates:
            logger.info(f"📊 EOD Candidates Discovered: {len(approved_candidates)}")
            for cand in approved_candidates:
                logger.info(f"  • 🟢 {cand['symbol']} @ ₹{cand['entry_price']:.2f} (Score: {cand['score']}, RSI: {cand['rsi']:.1f}, Vol Ratio: {cand['volume_ratio']:.2f}x)")
            approved_candidates.sort(key=lambda x: x["score"], reverse=True)
        else:
            logger.info("📊 EOD Candidates Discovered: 0")

        if approved_candidates:
            from config import SCANNER_MAX_ALERTS
            max_alerts = SCANNER_MAX_ALERTS.get("EOD", 10)
            
            if len(approved_candidates) > max_alerts:
                logger.info(f"Limiting EOD alerts from {len(approved_candidates)} to {max_alerts}")
                rejected_cands = approved_candidates[max_alerts:]
                approved_candidates = approved_candidates[:max_alerts]
                from database import save_rejected_alert
                for cand in rejected_cands:
                    rejection_counts["max_alerts_exceeded"] = rejection_counts.get("max_alerts_exceeded", 0) + 1
                    logger.info(f"🚫 {cand['symbol']} alert SUPPRESSED: Exceeded MAX_ALERTS_PER_SCAN limit (Score: {cand['score']})")
                    
            for cand in approved_candidates:
                c = dict(cand)
                # Remove extra keys before saving
                _candle_open = c.pop("_candle_open")
                _candle_high = c.pop("_candle_high")
                _candle_low = c.pop("_candle_low")
                _body_ratio = c.pop("_body_ratio")
                _close_position = c.pop("_close_position")
                _above_ema20 = c.pop("_above_ema20")
                _above_sma50 = c.pop("_above_sma50")
                _above_golden_cross = c.pop("_above_golden_cross")
                _sl_method = c.pop("_sl_method")
                _target_method = c.pop("_target_method")
                _natural_rr = c.pop("_natural_rr")
                _delivery_pct = c.pop("_delivery_pct")
                _peg = c.pop("_peg")
                _yoy_rev = c.pop("_yoy_rev")
                _yoy_profit = c.pop("_yoy_profit")
                _roe = c.pop("_roe")
                _ticker = c.pop("_ticker")
                
                if not is_test_mode:
                    saved, reason, cap_alloc, shares = save_alert_if_new(**c)
                else:
                    saved, reason, cap_alloc, shares = True, "", 0.0, 0
                    
                if not saved:
                    rejection_counts["duplicate"] += 1
                    continue
                    
                alerts_by_category.setdefault(c["category"], []).append({
                    "symbol":           c["symbol"],
                    "category":         c["category"],
                    "breakout_signals": [c["signals"]],
                    "price":            c["entry_price"],
                    "open":             round(_candle_open, 2),
                    "day_high":         round(_candle_high, 2),
                    "day_low":          round(_candle_low, 2),
                    "rsi":              c["rsi"],
                    "volume_ratio":     c["volume_ratio"],
                    "body_ratio":       round(_body_ratio * 100),
                    "close_position":   round(_close_position * 100),
                    "score":            c["score"],
                    "above_ema20":      _above_ema20,
                    "above_sma50":      _above_sma50,
                    "above_golden_cross":     _above_golden_cross,
                    "atr_stop":         c["stop_loss"],
                    "target_price":     c["target_price"],
                    "target_2":         c["target_2"],
                    "target_3":         c["target_3"],
                    "sl_method":        _sl_method,
                    "t_method":         _target_method,
                    "rr_ratio":         _natural_rr,
                    "delivery_pct":     round(_delivery_pct, 1) if _delivery_pct is not None else None,
                    "peg":              _peg,
                    "yoy_rev":          _yoy_rev,
                    "yoy_profit":       _yoy_profit,
                    "roe":              _roe,
                    "capital_allocated": cap_alloc,
                    "shares_bought":     shares
                })
                total_alerts += 1
                
                _last_bar_date = "unknown"
                try:
                    if isinstance(_ticker.index, pd.DatetimeIndex):
                        _last_bar_date = str(_ticker.index[-1])[:10]
                    elif "Date" in _ticker.columns:
                        _last_bar_date = str(_ticker["Date"].iloc[-1])[:10]
                except Exception:
                    pass
                logger.info(
                    f"✅ [EOD] PASSED ALL FILTERS AND LIMITS: {c['symbol']} | "
                    f"score={c['score']} | vol_ratio={c['volume_ratio']:.2f} | rsi={c['rsi']:.1f} | "
                    f"entry=₹{c['entry_price']:.2f} | sl=₹{c['stop_loss']} | t1=₹{c['target_price']} | "
                    f"last_bar={_last_bar_date} | category={c['category']}"
                )

        # ── VERIFICATION & STATUS ────────────────────────────────────────────────────
        # [VERSION: EOD_INDENT_FIX_v1.0] Fixed un-indentation of verification, status, telemetry, and return block out of candidate loop
        fired = {k: v for k, v in rejection_counts.items() if v > 0}
        duration_sec = round((datetime.now(IST) - start_time).total_seconds(), 1)
        total_symbols = len(watchlist)
        stale_count = rejection_counts.get("stale_data", 0)
        no_data_count = rejection_counts.get("no_data", 0)
        fresh_count = max(0, total_fetched_count - stale_count)
        data_status = "DEGRADED (Stale Data > 30%)" if (stale_count / max(total_symbols, 1)) > 0.30 else "OK"

        summary_lines = [
            "======================================================================",
            "=== [EOD SCANNER PIPELINE SUMMARY] ===",
            "======================================================================",
            "📊 DATA QUALITY SNAPSHOT:",
            f"  • Total Watchlist Requested : {total_symbols}",
            f"  • Fresh Data OK             : {fresh_count}",
            f"  • Stale Data                : {stale_count}",
            f"  • Missing / No Data         : {no_data_count}",
            f"  • Data Health Status        : {data_status}",
            "",
            "🎯 CRITERIA & FILTER BREAKDOWN:"
        ]
        for k, v in fired.items():
            summary_lines.append(f"  • {k:<27}: {v}")

        summary_lines.extend([
            "",
            "🏆 FINAL OUTCOME:",
            f"  • Alerts Generated          : {total_alerts}",
            f"  • Total Execution Time      : {duration_sec}s",
            "======================================================================"
        ])
        logger.info("\n".join(summary_lines))

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
        # [AUDIT-E1 FIX] stale_count and total_symbols already set at summary block above — removed duplicate assignments
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

        elif total_fetched_count == 0:
            outcome = "FAILED"
            status = "DOWN"
            error_msg = f"🚫 CRITICAL BLOCKER: 0/{len(watchlist)} symbols fetched (missing data)"
        elif total_fetched_count < len(watchlist) * 0.70:
            outcome = "PARTIAL"
            status = "DEGRADED"

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
            if status == "OK":
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
        logger.info(f"📊 Provider Stats: {dict(provider_stats_counts)}")
        logger.info(f"📊 Final Rejections: {dict(rejection_counts)}")
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
