import os
import time
import json
import math
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Set
import pandas as pd

from core_enums import CandidateState, RejectionReason
from core_models import PullbackCandidate, DataQualityError
from config import PULLBACK_CONFIG, PULLBACK_CONFIG as config, REGIME_POLICIES
import swing_utils
from sl_target_helper import compute_sl_and_target
from database import (
    init_db, save_alert_if_new, upsert_scanner_health, insert_notification,
    get_recent_alerts_for_scanner, save_funnel_telemetry
)
from memory_profiler import MemoryProfiler, BatchMemoryTracker, chunk_iterable
from watchlist_cache import get_watchlist
from price_cache import fetch_watchlist_data
from macro_utils import MarketRegimeEngine, get_nifty_20d_return, get_macro_regime
from lock_utils import ProcessLock

logger = logging.getLogger("pullback_scanner")
IST = ZoneInfo("Asia/Kolkata")
_scan_lock = ProcessLock("pullback_scanner")
_global_lock = ProcessLock("global_scanner_lock")

def compute_pullback_score(
    pullback_count_in_trend: int,
    volume_ratio: float,
    trigger_close_position: float,
    trigger_volume_mult: float,
    rs_percentile: float,
    sector_status: str,
    has_prior_eod: bool,
    has_prior_multi: bool,
    is_full_high_takeover: bool = False,
    is_bullish_engulfing: bool = False,
    depth_pct: float = 30.0,
    impulse_pct: float = 10.0,
    max_bonus: float = 5.0
) -> dict:
    """
    Computes pullback base_score and final_score additively.
    Returns a dictionary of the score breakdown.
    """
    base_score = 70.0
    
    # 1. Relative Strength (RS) Bonus (Graduated, Up to +5)
    if rs_percentile >= 90.0:
        rs_bonus = 5.0
    elif rs_percentile >= 80.0:
        rs_bonus = 4.0
    elif rs_percentile >= 70.0:
        rs_bonus = 2.0
    else:
        rs_bonus = 0.0

    # 2. Sector Tailwind Bonus (Up to +3)
    if sector_status == "TAILWIND":
        sector_bonus = 3.0
    elif sector_status == "MILD_TAILWIND":
        sector_bonus = 1.0
    else:
        sector_bonus = 0.0

    # 3. Volume Contraction Bonus (Up to +4)
    if volume_ratio <= 0.50:
        vol_bonus = 4.0
    elif volume_ratio <= 0.70:
        vol_bonus = 2.0
    else:
        vol_bonus = 0.0

    # 4. Trigger Strength & Pattern Bonus (Graduated, Up to +6)
    trigger_bonus = 0.0
    if is_full_high_takeover:
        trigger_bonus += 2.0  # Exceptional reclaim over entire upper wick
    if is_bullish_engulfing:
        trigger_bonus += 2.0  # Bullish engulfing structure
    if trigger_close_position >= 0.85 and trigger_volume_mult >= 1.50:
        trigger_bonus += 3.0
    elif trigger_close_position >= 0.75 and trigger_volume_mult >= 1.30:
        trigger_bonus += 2.0
    elif trigger_close_position >= 0.80:
        trigger_bonus += 1.0

    # 5. Trend maturity penalty
    maturity_penalties = {0: 0, 1: 0, 2: -3, 3: -6}
    penalty = maturity_penalties.get(pullback_count_in_trend, -10)
    
    
    # 6. Flag Depth Classification Bonus (High Tight Flags get bonus)
    depth_bonus = 0.0
    if 10.0 <= depth_pct < 23.6:
        depth_bonus = 5.0  # High Tight Flag / Shallow Flag
    elif 23.6 <= depth_pct <= 38.2:
        depth_bonus = 2.0  # Classic Pullback
    else:
        depth_bonus = 0.0  # Deep Pullback (no bonus, relies on trigger strength)
        
    # 7. Impulse Strength Bonus
    impulse_bonus = 0.0
    if impulse_pct >= 20.0:
        impulse_bonus = 5.0
    elif impulse_pct >= 12.0:
        impulse_bonus = 3.0
    elif impulse_pct >= 8.0:
        impulse_bonus = 1.0

    eod_bonus = 3.0 if has_prior_eod else 0.0
    final_score = base_score + rs_bonus + sector_bonus + vol_bonus + trigger_bonus + maturity_penalties.get(pullback_count_in_trend, -10) + depth_bonus + impulse_bonus + eod_bonus
    final_score = min(100.0, max(0.0, final_score))
    
    return {
        "base_score": base_score,
        "rs_bonus": rs_bonus,
        "sector_bonus": sector_bonus,
        "vol_bonus": vol_bonus,
        "trigger_bonus": trigger_bonus,
        "depth_bonus": depth_bonus,
        "impulse_bonus": impulse_bonus,
        "maturity_penalty": maturity_penalties.get(pullback_count_in_trend, -10),
        "catalyst_bonus": eod_bonus,
        "final_score": final_score
    }

def evaluate_pullback_symbol(symbol: str, df: pd.DataFrame, fund_data: dict = None, regime_ctx: dict = None) -> dict:
    """
    Evaluates a single symbol against the production Pullback Continuation scanner rules.
    Runs trend alignment (Close > SMA50 > SMA200), swing pivot detection, impulse wave selection (gain >= 8%), retracement depth (23.6%-61.8%), resumption trigger candle, scoring, and target calculations without side effects.
    """
    if df is None or df.empty or len(df) < 200:
        return {
            "status": "NO",
            "reasons": [f"Insufficient historical price data ({len(df) if df is not None else 0} bars < 200 minimum)"],
            "score": 0.0,
            "qualified": False
        }

    historical_view = df.copy()
    if isinstance(historical_view.columns, pd.MultiIndex):
        historical_view.columns = historical_view.columns.get_level_values(0)
    historical_view = historical_view.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    if len(historical_view) < 200:
        return {"status": "NO", "reasons": [f"Insufficient valid bars ({len(historical_view)} < 200)"], "score": 0.0, "qualified": False}

    from indicator_manager import manager
    try:
        bundle = manager.compute_base_indicators(historical_view, symbol)
    except Exception as _ie:
        return {"status": "NO", "reasons": [f"Failed to compute technical indicators: {_ie}"], "score": 0.0, "qualified": False}

    last_bar = historical_view.iloc[-1]
    close_price = float(last_bar['Close'])
    sma50_val = float(bundle.sma_50.iloc[-1]) if hasattr(bundle, 'sma_50') and bundle.sma_50 is not None and not bundle.sma_50.empty and not pd.isna(bundle.sma_50.iloc[-1]) else None
    sma200_val = float(bundle.sma_200.iloc[-1]) if hasattr(bundle, 'sma_200') and bundle.sma_200 is not None and not bundle.sma_200.empty and not pd.isna(bundle.sma_200.iloc[-1]) else None

    if not (sma50_val and sma200_val and close_price > sma50_val > sma200_val):
        return {
            "status": "NO",
            "reasons": [f"Trend Failure: Close ₹{close_price:.2f} is not aligned above SMA50 ₹{sma50_val if sma50_val else 0:.2f} > SMA200 ₹{sma200_val if sma200_val else 0:.2f}"],
            "score": 0.0,
            "qualified": False,
            "entry_price": close_price
        }

    pivots = swing_utils.detect_confirmed_pivots(historical_view, PULLBACK_CONFIG["LOOKBACK"], PULLBACK_CONFIG["CONFIRM"])
    if not pivots:
        return {"status": "NO", "reasons": ["No confirmed swing high/low pivots found for pullback calculation"], "score": 0.0, "qualified": False, "entry_price": close_price}

    impulse = swing_utils.select_pullback_origin(pivots, historical_view, PULLBACK_CONFIG)
    if not impulse:
        return {"status": "NO", "reasons": [f"No valid impulse origin leg identified (requires impulse gain ≥{PULLBACK_CONFIG['MIN_IMPULSE_GAIN_PCT']:.1f}%)"], "score": 0.0, "qualified": False, "entry_price": close_price}

    ps = swing_utils.measure_pullback(historical_view, impulse, PULLBACK_CONFIG)
    if not ps.valid:
        return {"status": "NO", "reasons": [f"Invalid pullback structure (Retracement {ps.depth_pct:.1f}%, Vol Ratio {ps.volume_ratio:.2f}x outside {PULLBACK_CONFIG['MIN_DEPTH_PCT']}%–{PULLBACK_CONFIG['MAX_DEPTH_PCT']}% bounds)"], "score": 0.0, "qualified": False, "entry_price": close_price}

    trig = swing_utils.detect_resumption_trigger(historical_view, ps, PULLBACK_CONFIG)
    if not trig.valid:
        return {
            "status": "WATCHLIST",
            "reasons": [f"Valid Pullback Structure (Depth {ps.depth_pct:.1f}%) — Awaiting Bullish Resumption Trigger Candle"],
            "score": 65.0,
            "qualified": False,
            "entry_price": close_price
        }

    entry_val = float(trig.entry_price)
    
    rs_percentile = 50.0
    if fund_data and isinstance(fund_data, dict):
        rs_percentile = fund_data.get("rs_rating") or fund_data.get("rs_percentile") or 50.0
    try:
        rs_percentile = float(rs_percentile)
    except (ValueError, TypeError):
        rs_percentile = 50.0

    sector_status = "NEUTRAL"
    if fund_data and isinstance(fund_data, dict):
        sector_status = fund_data.get("sector_status", "NEUTRAL")

    vol_ratio = float(ps.volume_ratio) if hasattr(ps, 'volume_ratio') and ps.volume_ratio is not None else 1.0

    # Calculate trigger close position from trig
    close_position = getattr(trig, "close_position", 0.5)

    has_prior_eod = False
    has_prior_multi = False
    if fund_data and isinstance(fund_data, dict):
        has_prior_eod = bool(fund_data.get("prior_eod_alert") or fund_data.get("has_prior_eod"))
        has_prior_multi = bool(fund_data.get("prior_multi_alert") or fund_data.get("has_prior_multi"))

    score_breakdown = compute_pullback_score(
        pullback_count_in_trend=ps.pullback_count_in_trend,
        volume_ratio=vol_ratio,
        trigger_close_position=close_position,
        trigger_volume_mult=trig.volume_mult,
        rs_percentile=rs_percentile,
        sector_status=sector_status,
        has_prior_eod=has_prior_eod,
        has_prior_multi=has_prior_multi,
        is_full_high_takeover=getattr(trig, "is_full_high_takeover", False),
        is_bullish_engulfing=getattr(trig, "is_bullish_engulfing", False),
        depth_pct=ps.depth_pct,
        impulse_pct=ps.impulse.gain_pct
    )
    final_score = score_breakdown["final_score"]

    market_regime = "NEUTRAL"
    if regime_ctx and isinstance(regime_ctx, dict):
        market_regime = regime_ctx.get("regime_type") or regime_ctx.get("regime") or "NEUTRAL"

    regime_thresholds = {
        "STRONG_BULL": 74.0,
        "BULL": 74.0,
        "NEUTRAL": 76.0,
        "WEAK_BEAR": 80.0,
        "BEAR": 80.0,
    }
    required_threshold = regime_thresholds.get(market_regime, 76.0)

    # 1. Run risk validation first
    sl_result = compute_sl_and_target(
        entry_price=entry_val,
        atr=float(bundle.atr_14.iloc[-1]) if hasattr(bundle, 'atr_14') and bundle.atr_14 is not None and not bundle.atr_14.empty and not pd.isna(bundle.atr_14.iloc[-1]) else (entry_val * 0.025),
        mode="PULLBACK",
        swing_low=ps.pullback_low.price if hasattr(ps, 'pullback_low') and ps.pullback_low else None,
        swing_high=ps.impulse.end.price if hasattr(ps, 'impulse') and ps.impulse else None
    )

    # 2. Qualification requires both threshold score and valid risk engine output
    is_qualified = (final_score >= required_threshold and not sl_result.get("is_rejected", False))

    status_str = "CORE MET" if is_qualified else "NO"
    
    if is_qualified:
        reasons = [f"Resumption Trigger Confirmed @ ₹{entry_val:.2f} (Depth {ps.depth_pct:.1f}%, Vol {ps.volume_ratio:.2f}x) | Pullback Score {final_score:.1f}/100"]
    else:
        reasons = []
        if final_score < required_threshold:
            reasons.append(f"Pullback Score {final_score:.1f} < {required_threshold} threshold")
        if sl_result.get("is_rejected"):
            reasons.append(f"Risk Rejected: {sl_result.get('rejection_reason')}")

    return {
        "status": status_str,
        "reasons": reasons,
        "score": final_score,
        "qualified": is_qualified,
        "entry_price": entry_val,
        "stop_loss": sl_result.get("stop_loss"),
        "target_1": sl_result.get("target_1"),
        "target_2": sl_result.get("target_2"),
        "target_3": sl_result.get("target_3"),
        "target_4": sl_result.get("target_4"),
        "atr_14": float(bundle.atr_14.iloc[-1]) if hasattr(bundle, 'atr_14') and bundle.atr_14 is not None and not bundle.atr_14.empty and not pd.isna(bundle.atr_14.iloc[-1]) else float(entry_val * 0.025)
    }

def start(force: bool = False, session=None):
    """
    Main entry point for Pullback Scanner. Acquires process lock and delegates to pipeline.
    """
    from database import is_scanner_stopped, upsert_scanner_health
    from lock_utils import print_scanner_start_banner, print_scanner_end_banner
    if is_scanner_stopped("PULLBACK"):
        logger.info("🛑 Pullback Scanner is STOPPED by Admin. Skipping execution.")
        return 0

    queued_at = None
    if not _global_lock.acquire(blocking=False):
        queued_at = time.monotonic()
        logger.info("⏳ [PULLBACK] Global lock busy — marking status QUEUED and waiting...")
        upsert_scanner_health("PULLBACK", "QUEUED", error_msg="Waiting in queue for active scanner to complete...")
        if not _global_lock.acquire(blocking=True):
            raise RuntimeError("Failed to acquire global scanner lock.")
        logger.info(f"✅ [PULLBACK] Global lock acquired after {round(time.monotonic()-queued_at,1)}s wait. Starting scan...")

    if not _scan_lock.acquire(blocking=False):
        _global_lock.release()
        raise RuntimeError("Pullback Scanner is already actively running!")

    _scan_start = print_scanner_start_banner("pullback_scanner", queued_at=queued_at)
    try:
        return run_pullback_pipeline(force=force, session=session)
    finally:
        print_scanner_end_banner("pullback_scanner", _scan_start)
        _scan_lock.release()
        _global_lock.release()

def _determine_dataset_date(sample_data: dict) -> Optional[str]:
    if not sample_data:
        return None
    dates = []
    for s_df in sample_data.values():
        if s_df is not None and not s_df.empty:
            try:
                last_dt = s_df.iloc[-1].name if isinstance(s_df.index, pd.DatetimeIndex) else s_df.iloc[-1].get("Date", s_df.iloc[-1].get("Datetime"))
                if last_dt:
                    dt_str = pd.to_datetime(last_dt).strftime("%Y-%m-%d")
                    dates.append(dt_str)
            except Exception:
                pass
    if not dates:
        return None
    from collections import Counter
    counts = Counter(dates)
    most_common_date, count = counts.most_common(1)[0]
    # Require at least 80% consensus across valid dates
    if count >= len(dates) * 0.8:
        return most_common_date
    return None

def run_pullback_pipeline(run_date: str = None, force: bool = False, session=None) -> int:
    init_db()
    ist_now = datetime.now(IST)
    if not run_date:
        run_date = ist_now.strftime("%Y-%m-%d")
        
    logger.info("=" * 80)
    logger.info(f"🚀🚀🚀 [START] PULLBACK SCANNER PIPELINE INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')} 🚀🚀🚀")
    logger.info("=" * 80)

    from perf_utils import ScannerStageTracker
    stage_tracker = ScannerStageTracker("PULLBACK_SCANNER")
    stage_tracker.start_stage(1, "Regime Check & Config Init", "Computing Nifty regime and loading effective config")
    
    try:
        upsert_scanner_health("PULLBACK", "RUNNING", error_msg="Pullback Scan in progress...")
    except Exception:
        logger.warning("⚠️ Could not mark PULLBACK as RUNNING")

    # Capture effective config snapshot at start of run (immutability)
    effective_config = dict(config)

    # ---------------- PRECONDITIONS & REGIME CHECK ----------------
    nifty_ret_20d = get_nifty_20d_return()
    market_regime = get_macro_regime(nifty_ret_20d)
    logger.info(f"📊 Market Regime: {market_regime}")

    if market_regime == "STRONG_BEAR":
        logger.info("🛑 STRONG_BEAR regime detected — Pullback scanner disabled entirely.")
        upsert_scanner_health("PULLBACK", status="OK", today_alerts=0, error_msg="Disabled in STRONG_BEAR regime")
        return 0

    regime_thresholds = {
        "STRONG_BULL": 74.0,
        "BULL": 74.0,
        "NEUTRAL": 76.0,
        "WEAK_BEAR": 80.0,
        "BEAR": 80.0,
    }
    required_threshold = regime_thresholds.get(market_regime, 76.0)

    stage_tracker.end_stage(f"Regime={market_regime}")
    stage_tracker.start_stage(2, "Watchlist & Data Acquisition", "Loading fundamental watchlist and fetching historical price data")
    # ---------------- DATA READINESS & ACQUISITION ----------------
    try:
        watchlist = get_watchlist()
    except Exception as e:
        logger.exception("❌ Failed to load fundamental watchlist for Pullback Scanner")
        upsert_scanner_health("PULLBACK", status="DOWN", error_msg=f"Watchlist load failed: {str(e)[:200]}")
        return 0

    if watchlist.empty:
        logger.info("🛡️ Watchlist is empty. Exiting Pullback scan cleanly.")
        upsert_scanner_health("PULLBACK", status="OK", today_alerts=0)
        return 0

    # Step 1: Check if today's dataset is already processed/available
    sample_chunk = watchlist.head(10)
    sample_data = fetch_watchlist_data(sample_chunk, "1y", "1d", requester="PULLBACK")
    
    dataset_date = _determine_dataset_date(sample_data)

    is_historical_fallback = False

    if dataset_date == run_date:
        logger.info(f"[PULLBACK] Using processed dataset for {dataset_date}")
    elif not force:
        # Step 2: Today's dataset is not available yet. Wait for Bhavcopy acquisition logic (scheduled mode).
        logger.info("[PULLBACK] Today's dataset unavailable, waiting for Bhavcopy...")
        try:
            from main import wait_for_bhavcopy_or_fallback
            wait_for_bhavcopy_or_fallback("PULLBACK")
        except Exception as bh_err:
            logger.warning(f"Could not execute Bhavcopy wait: {bh_err}")

        # Re-fetch sample after Bhavcopy wait
        sample_data = fetch_watchlist_data(sample_chunk, "1y", "1d", requester="PULLBACK")
        dataset_date = _determine_dataset_date(sample_data)

        if dataset_date == run_date:
            logger.info("[PULLBACK] Today's Bhavcopy processed successfully.")
        else:
            # Step 3: Today's Bhavcopy unavailable. Fallback to latest historical processed dataset (Read-Only)
            is_historical_fallback = True
            fallback_date = dataset_date or "HISTORICAL"
            logger.info(f"[PULLBACK] Admin mode using historical dataset from {fallback_date} (read-only fallback)")
    else:
        # Forced/Manual trigger mode: bypass blocking wait and execute using available dataset
        is_historical_fallback = True
        fallback_date = dataset_date or "HISTORICAL"
        logger.info(f"[PULLBACK] Forced/Manual trigger mode: bypassing Bhavcopy wait, using dataset from {fallback_date} (read-only fallback)")

    cooldown_alerts = get_recent_alerts_for_scanner("PULLBACK", PULLBACK_CONFIG.get("COOLDOWN_MINUTES", 1440))
    
    # 30-day prior alerts for evidence bonus calculation (+3 for EOD, +2 for MULTIBAGGER/MULTI_TF)
    prior_window_mins = effective_config.get("PRIOR_WINDOW", 30) * 1440
    prior_eod_symbols = {s for (s, _) in get_recent_alerts_for_scanner("EOD", prior_window_mins, only_active=True)}
    prior_multi_symbols = {s for (s, _) in get_recent_alerts_for_scanner("MULTIBAGGER", prior_window_mins, only_active=True)}.union(
        {s for (s, _) in get_recent_alerts_for_scanner("MULTI_TF", prior_window_mins, only_active=True)}
    )

    BATCH_SIZE = int(os.environ.get("PULLBACK_FETCH_BATCH_SIZE", "50"))
    total_batches = (len(watchlist) + BATCH_SIZE - 1) // BATCH_SIZE
    
    candidates: list[PullbackCandidate] = []

    rejected = {k: 0 for k in [
        "no_data", "provider_error", "insufficient_bars", "data_quality",
        "no_uptrend", "no_pivots", "no_impulse", "pullback_invalid",
        "no_trigger", "processing_error", "cooldown", "stale_data",
        "score_below_threshold", "risk_rejected", "eod_suppressed", "ranked_out", "persistence_failed"
    ]}
    provider_stats_counts = {
        "SUCCESS": 0, "NOT_FOUND": 0, "RATE_LIMIT": 0,
        "NETWORK_ERROR": 0, "TIMEOUT": 0, "EMPTY_DATA": 0
    }
    provider_resolved_symbols = set()
    fresh_valid_symbols = set()

    stage_tracker.end_stage(f"Watchlist={len(watchlist)} stocks loaded")
    stage_tracker.start_stage(3, "Symbol Evaluation Loop", f"Running pullback structure analysis on {len(watchlist)} stocks")
    stage_tracker.total_symbols = len(watchlist)
    # ---------------- ORCHESTRATION LOOP ----------------
    symbols_processed = 0
    if session is not None:
        logger.info(f"📦 [PULLBACK] Using MarketDataSession | {session.metadata.valid_symbols} symbols pre-fetched")
    with MemoryProfiler("Pullback Scanner Process"):
        for batch_num, chunk_df in enumerate(chunk_iterable(watchlist, BATCH_SIZE), start=1):
            with BatchMemoryTracker("PULLBACK", batch_num, total_batches, len(chunk_df), collect_gc=True) as tracker:
                # [VERSION: MARKET_DATA_SESSION_v1.0] Serve from session when available.
                if session is not None:
                    all_ticker_data = {
                        row["Stock"]: (
                            session.get(row["Stock"]).ohlcv_df
                            if session.get(row["Stock"]) is not None else None
                        )
                        for _, row in chunk_df.iterrows()
                    }
                else:
                    all_ticker_data = fetch_watchlist_data(chunk_df, "2y", "1d")
                if not all_ticker_data:
                    for _, row in chunk_df.iterrows():
                        symbols_processed += 1
                        rejected["provider_error"] += 1
                    continue

                for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):
                    symbols_processed += 1
                    symbol = row.get("Stock", "UNKNOWN")
                    try:
                        category = row.get("Category", "MIDCAP")
                        sector = row.get("Sector", None)

                        from core_enums import ProviderResult

                        # Robust symbol resolution across .NS / .BO suffixes
                        ticker_data = all_ticker_data.get(symbol)
                        if ticker_data is None:
                            ticker_data = all_ticker_data.get(f"{symbol}.NS")
                        if ticker_data is None:
                            ticker_data = all_ticker_data.get(f"{symbol}.BO")
                        if ticker_data is None:
                            ticker_data = all_ticker_data.get(symbol.split('.')[0])

                        if ticker_data is None:
                            logger.debug(f"REJECTION: {symbol} (Phase: FETCH, Reason: Missing historical data)")
                            rejected["no_data"] += 1
                            provider_stats_counts["EMPTY_DATA"] += 1
                            continue

                        if isinstance(ticker_data, ProviderResult):
                            logger.debug(f"REJECTION: {symbol} (Phase: FETCH, Reason: Provider error ({ticker_data.name}))")
                            provider_stats_counts[ticker_data.name] = provider_stats_counts.get(ticker_data.name, 0) + 1
                            rejected["provider_error"] += 1
                            continue
                        else:
                            provider_stats_counts["SUCCESS"] += 1
                            provider_resolved_symbols.add(symbol)

                        if (symbol, "PULLBACK") in cooldown_alerts:
                            logger.debug(f"REJECTION: {symbol} (Phase: COOLDOWN_GATE, Reason: Cooldown active from recent Pullback alert)")
                            rejected["cooldown"] = rejected.get("cooldown", 0) + 1
                            continue

                        df = ticker_data.copy()
                        
                        # [PULLBACK_NAN_FIX] Sanitize raw incoming data to prevent REJ_PRICE_NAN hard-failures
                        df.dropna(subset=["Open", "High", "Low", "Close", "Volume"], inplace=True)

                        # [STALE_DATA_CHECK] Replicating EOD's strict timestamp freshness validation
                        if getattr(ticker_data, 'attrs', {}).get('is_stale'):
                            rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                            continue
                            
                        _stale_col = next((c for c in ["Date", "Datetime"] if c in df.columns), None)
                        if is_historical_fallback and dataset_date:
                            try:
                                _target_val = df.index[-1] if isinstance(df.index, pd.DatetimeIndex) else (df.iloc[-1][_stale_col] if _stale_col else None)
                                if _target_val is None:
                                    rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                                    continue
                                _last_ts = pd.to_datetime(_target_val)
                                if _last_ts.date() != pd.to_datetime(dataset_date).date():
                                    rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                                    continue
                            except Exception as e:
                                logger.debug(f"⏭️ {symbol} fallback date alignment check failed: {e}")
                                rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                                continue
                        else:
                            _expected_max_age_days = 4
                            _benchmark_date = ist_now.date()
                            if _stale_col:
                                try:
                                    _last_ts = pd.to_datetime(df.iloc[-1][_stale_col])
                                    if _last_ts.tzinfo is None:
                                        _last_ts = _last_ts.tz_localize("Asia/Kolkata")
                                    else:
                                        _last_ts = _last_ts.tz_convert("Asia/Kolkata")
                                    _bar_age_days = (_benchmark_date - _last_ts.date()).days
                                    if _bar_age_days < 0 or _bar_age_days > _expected_max_age_days:
                                        rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                                        continue
                                except Exception as e:
                                    logger.debug(f"⏭️ {symbol} stale-data check failed: {e}")
                                    rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                                    continue
                            elif isinstance(df.index, pd.DatetimeIndex) and len(df) > 0:
                                try:
                                    _last_ts = pd.Timestamp(df.index[-1])
                                    if _last_ts.tzinfo is not None:
                                        _last_ts = _last_ts.tz_convert("Asia/Kolkata")
                                    else:
                                        _last_ts = _last_ts.tz_localize("Asia/Kolkata")
                                    _bar_age_days = (_benchmark_date - _last_ts.date()).days
                                    if _bar_age_days < 0 or _bar_age_days > _expected_max_age_days:
                                        rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                                        continue
                                except Exception as e:
                                    logger.debug(f"⏭️ {symbol} stale-data check failed (index): {e}")
                                    rejected["stale_data"] = rejected.get("stale_data", 0) + 1
                                    continue

                        if df.empty or len(df) < effective_config.get("MIN_HISTORY", 200):
                            logger.debug(f"REJECTION: {symbol} (Phase: BAR_HISTORY, Reason: Insufficient bars ({len(df) if isinstance(df, pd.DataFrame) else 0} < {effective_config.get('MIN_HISTORY', 200)}))")
                            rejected["insufficient_bars"] += 1
                            continue

                        df.attrs['adjusted'] = True
                        df.attrs['symbol'] = symbol
                        as_of_index = len(df) - 1
                        historical_view = df.iloc[:as_of_index + 1]

                        # PHASE A: ELIGIBILITY & ESTABLISHED UPTREND
                        try:
                            swing_utils.check_data_quality(historical_view)
                        except DataQualityError as dqe:
                            logger.debug(f"REJECTION: {symbol} (Phase: DATA_QUALITY, Reason: {dqe})")
                            rejected["data_quality"] += 1
                            continue

                        fresh_valid_symbols.add(symbol)

                        from indicator_manager import manager
                        bundle = manager.compute_base_indicators(historical_view, symbol)
                        last_bar = historical_view.iloc[-1]
                        
                        sma50_val = bundle.sma_50.iloc[-1] if bundle.sma_50 is not None and not bundle.sma_50.empty else None
                        sma200_val = bundle.sma_200.iloc[-1] if bundle.sma_200 is not None and not bundle.sma_200.empty else None

                        if not (sma50_val and sma200_val and last_bar['Close'] > sma50_val > sma200_val):
                            logger.debug(f"REJECTION: {symbol} (Phase: UPTREND_GATE, Reason: Price not > SMA50 > SMA200)")
                            rejected["no_uptrend"] += 1
                            continue

                        # PHASE B: IMPULSE & ORDERLY PULLBACK STRUCTURE
                        pivots = swing_utils.detect_confirmed_pivots(historical_view, effective_config["LOOKBACK"], effective_config["CONFIRM"])
                        if not pivots:
                            logger.debug(f"REJECTION: {symbol} (Phase: PIVOT_DETECTION, Reason: No confirmed swing pivots)")
                            rejected["no_pivots"] += 1
                            continue

                        impulse = swing_utils.select_pullback_origin(pivots, historical_view, effective_config)
                        if not impulse:
                            logger.debug(f"REJECTION: {symbol} (Phase: IMPULSE_WAVE, Reason: No valid impulse origin)")
                            rejected["no_impulse"] += 1
                            continue

                        ps = swing_utils.measure_pullback(historical_view, impulse, effective_config, debug=effective_config.get("DEBUG_SWINGS", False))
                        save_funnel_telemetry("PULLBACK", run_date, symbol, ps.stage_results)
                    
                        if not ps.valid:
                            logger.debug(f"REJECTION: {symbol} (Phase: PULLBACK_STRUCTURE, Reason: Invalid pullback depth/volume structure)")
                            rejected["pullback_invalid"] += 1
                            continue

                        # PHASE C: RESUMPTION TRIGGER
                        trig = swing_utils.detect_resumption_trigger(historical_view, ps, effective_config)
                        if not trig.valid:
                            logger.debug(f"REJECTION: {symbol} (Phase: RESUMPTION_TRIGGER, Reason: No valid trigger bar)")
                            rejected["no_trigger"] += 1
                            continue

                        logger.info(f"📍 PICKED [PULLBACK: IN BETWEEN]: {symbol} @ ₹{trig.entry_price:.2f} (Retracement: {ps.depth_pct:.1f}%, Vol Ratio: {ps.volume_ratio:.2f}x)")
                        
                        close_position = getattr(trig, "close_position", 0.5)
                        
                        atr_val = float(bundle.atr_14.iloc[-1]) if hasattr(bundle, 'atr_14') and bundle.atr_14 is not None and not bundle.atr_14.empty and not pd.isna(bundle.atr_14.iloc[-1]) else float(trig.entry_price) * 0.025

                        cand = PullbackCandidate(
                            symbol=symbol,
                            as_of_date=ist_now.date(),
                            structure=ps,
                            trigger=trig,
                            entry_price=trig.entry_price,
                            warnings=[],
                            config_version=effective_config.get("VERSION", "pb-1.0.0"),
                            sector=sector,
                            status=CandidateState.NEW
                        )
                        cand.trigger_close_position = close_position
                        cand.atr_val = atr_val
                        candidates.append(cand)
                    except Exception as sym_err:
                        logger.error(f"❌ Error processing symbol {symbol} in Pullback Scanner: {sym_err}")
                        rejected["processing_error"] += 1
                        continue
            del all_ticker_data
            import gc; gc.collect()
            logger.info(f"⏳ [PULLBACK SCANNER] Evaluated Batch {batch_num}/{total_batches} ({min(batch_num * BATCH_SIZE, len(watchlist))}/{len(watchlist)} stocks) | Candidates found so far: {len(candidates)}")

    logger.info(f"📊 Pullback Candidates Discovered: {len(candidates)}")
    
    # ── CRITICAL BLOCKER GUARD ──
    no_data_count = rejected.get("no_data", 0)
    stale_count = rejected.get("stale_data", 0)
    dirty_count = rejected.get("data_quality", 0)
    provider_errors_count = rejected.get("provider_error", 0)
    # Exclude dirty_count (data quality) as it represents stock-specific issues, not infrastructure outages
    total_failures = no_data_count + stale_count + provider_errors_count
    total_symbols = len(watchlist)
    total_fetched_count = len(provider_resolved_symbols)
    elapsed_time = round((datetime.now(IST) - ist_now).total_seconds(), 1)

    status_val = "OK"
    err_val = None

    if not is_historical_fallback and total_symbols > 0 and total_failures >= total_symbols * 0.25:
        status_val = "DOWN"
        err_val = f"🚫 CRITICAL BLOCKER: {total_failures}/{total_symbols} symbols missing/stale/error (≥25%)"
        logger.error(f"🚨 {err_val}")
        try:
            from telegram_engine import send_telegram_message
            send_telegram_message(f"🚨 <b>CRITICAL BLOCKER: PULLBACK SCANNER FAILED</b>\n{err_val}")
        except Exception:
            pass
            
        try:
            from push_service import send_push_to_all
            send_push_to_all(
                title="🚨 CRITICAL DATA OUTAGE",
                body=f"PULLBACK SCANNER Halted. {total_failures}/{total_symbols} symbols failed data quality checks.",
                bypass_throttle=True
            )
        except Exception as e:
            logger.error(f"Could not dispatch web push: {e}")
            
        upsert_scanner_health(
            "PULLBACK",
            status=status_val,
            last_success=ist_now.isoformat(),
            today_alerts=0,
            total_count=total_symbols,
            processed_count=symbols_processed,
            duration_seconds=elapsed_time,
            error_msg=err_val
        )
        logger.info(f"🚫 [HALTED] PULLBACK SCANNER terminated early due to critical data outage ({total_failures}/{total_symbols} symbols failed).")
        return {
            "total_count": total_symbols,
            "processed_count": symbols_processed,
            "today_alerts": 0
        }

    # Partial fetch degraded status check (warn but do not block)
    if not is_historical_fallback and total_symbols > 0 and total_fetched_count < total_symbols * 0.70:
        status_val = "DEGRADED"
        err_val = f"Partial Fetch: {total_fetched_count}/{total_symbols} symbols"

    stage_tracker.end_stage(f"Candidates={len(candidates)} pullback structures found out of {symbols_processed} processed")
    stage_tracker.start_stage(4, "Scoring & RS/Sector Modifiers", f"Computing pullback scores for {len(candidates)} candidates")
    # ---------------- SCORING & MODIFIERS ----------------
    from macro_utils import compute_nifty_rs_rating, compute_sector_regime_rankings

    rs_rankings = {}
    missing_rs = False
    if candidates:
        try:
            rs_rankings = compute_nifty_rs_rating([c.symbol for c in candidates])
        except Exception as rs_err:
            logger.warning(f"Failed to compute RS ratings for pullbacks: {rs_err}")
            missing_rs = True

    sector_rankings_dict = {}
    missing_sector = False
    try:
        sector_rankings_dict = compute_sector_regime_rankings()
    except Exception as sec_err:
        logger.warning(f"Failed to compute sector rankings for pullbacks: {sec_err}")
        missing_sector = True

    service_warnings = []
    if missing_rs:
        service_warnings.append("missing_rs")
    if missing_sector:
        service_warnings.append("missing_sector")

    if service_warnings:
        warn_str = f"Service failure ({', '.join(service_warnings)})"
        err_val = f"{err_val} | {warn_str}" if err_val else warn_str
        # Fail-safe threshold markup
        required_threshold += 3.0
        logger.warning(f"⚠️ Service failure detected. Required threshold raised to {required_threshold}")

    for c in candidates:
        rs_pct_val = float(rs_rankings.get(c.symbol, 50.0))
        
        sector_info = sector_rankings_dict.get(c.sector, {}) if c.sector else {}
        sector_status = sector_info.get("effective_status", "NEUTRAL")
        
        vol_ratio = float(c.structure.volume_ratio) if hasattr(c.structure, 'volume_ratio') and c.structure.volume_ratio is not None else 1.0
        
        close_position = getattr(c, "trigger_close_position", 0.5)
        volume_mult = c.trigger.volume_mult

        has_prior_eod = c.symbol in prior_eod_symbols
        has_prior_multi = c.symbol in prior_multi_symbols

        score_breakdown = compute_pullback_score(
            pullback_count_in_trend=c.structure.pullback_count_in_trend,
            volume_ratio=vol_ratio,
            trigger_close_position=close_position,
            trigger_volume_mult=volume_mult,
            rs_percentile=rs_pct_val,
            sector_status=sector_status,
            has_prior_eod=has_prior_eod,
            has_prior_multi=has_prior_multi,
            is_full_high_takeover=getattr(c.trigger, "is_full_high_takeover", False),
            is_bullish_engulfing=getattr(c.trigger, "is_bullish_engulfing", False)
        )
        c.base_score = score_breakdown["base_score"]
        c.final_score = score_breakdown["final_score"]
        c.score_breakdown = score_breakdown

    # Filter out scores below threshold
    scored_candidates = []
    for c in candidates:
        if c.final_score < required_threshold:
            logger.debug(f"REJECTION: {c.symbol} (Phase: SCORE_GATE, Reason: Final score {c.final_score:.1f} < required {required_threshold})")
            rejected["score_below_threshold"] += 1
            try:
                from near_miss_tracker import log_near_miss
                log_near_miss(
                    symbol=c.symbol,
                    scanner="PULLBACK",
                    breakout_type="PULLBACK_SETUP",
                    gate_name="score_below_threshold",
                    observed_value=float(c.final_score),
                    threshold_value=float(required_threshold),
                    score=int(c.final_score),
                    entry_price=float(c.entry_price) if hasattr(c, 'entry_price') and c.entry_price else None,
                    stop_loss=float(c.sl_result.get("stop_loss", 0.0)) if hasattr(c, 'sl_result') and c.sl_result and c.sl_result.get("stop_loss") else None,
                    target_1=float(c.sl_result.get("target_1", 0.0)) if hasattr(c, 'sl_result') and c.sl_result and c.sl_result.get("target_1") else None,
                )
            except Exception as _nm_e:
                # [FIX-P4] Promote to WARNING so near-miss telemetry failures are visible in production logs.
                logger.warning(f"⚠️ [PULLBACK] Near-miss log failed for {c.symbol}: {_nm_e}")
        else:
            scored_candidates.append(c)

    # ---------------- SAME-NIGHT EOD SUPPRESSION ----------------
    tonight_eod_alerts = get_recent_alerts_for_scanner("EOD", 300)
    for c in scored_candidates:
        if (c.symbol, "EOD") in tonight_eod_alerts:
            c.status = CandidateState.SUPPRESSED
            c.suppressed_by = "EOD"
            rejected["eod_suppressed"] += 1
            logger.debug(f"REJECTION: {c.symbol} (Phase: EOD_SUPPRESSION, Reason: Primary EOD alert already generated tonight)")

    survivors = [c for c in scored_candidates if c.status != CandidateState.SUPPRESSED]

    stage_tracker.end_stage(f"Scored={len(scored_candidates)} cleared threshold, {rejected.get('score_below_threshold',0)} rejected")
    stage_tracker.start_stage(5, "Risk Engine & Alert Persistence", f"Validating SL/target for {len(scored_candidates)} candidates and saving alerts")
    # ---------------- RISK ENGINE VALIDATION ----------------
    valid_risk_candidates = []
    for c in survivors:
        entry_val = float(c.entry_price)
        sl_result = compute_sl_and_target(
            entry_price=entry_val,
            atr=getattr(c, "atr_val", entry_val * 0.025),
            mode="PULLBACK",
            swing_low=c.structure.pullback_low.price,
            swing_high=c.structure.impulse.end.price,
        )

        if sl_result.get("is_rejected"):
            logger.debug(f"REJECTION: {c.symbol} (Phase: SL_TARGET_ENGINE, Reason: {sl_result.get('rejection_reason')})")
            c.status = CandidateState.REJECTED
            rejected["risk_rejected"] += 1
        else:
            c.sl_result = sl_result
            valid_risk_candidates.append(c)

    # ---------------- ALERT LIMITING & SORTING ----------------
    valid_risk_candidates.sort(key=lambda x: x.final_score, reverse=True)
    from config import SCANNER_MAX_ALERTS
    max_alerts = SCANNER_MAX_ALERTS.get("PULLBACK", 10)
    if len(valid_risk_candidates) > max_alerts:
        logger.info(f"Limiting PULLBACK alerts from {len(valid_risk_candidates)} to {max_alerts}")
        ranked_out = valid_risk_candidates[max_alerts:]
        from database import save_rejected_alert
        for c in ranked_out:
            c.status = CandidateState.SUPPRESSED
            rejected["ranked_out"] += 1
            logger.info(f"🚫 {c.symbol} alert SUPPRESSED: Exceeded MAX_ALERTS_PER_SCAN limit (Score: {c.final_score:.1f})")
            try:
                save_rejected_alert(c.symbol, "PULLBACK", "RANKED_OUT", context={"score": c.final_score})
            except Exception:
                pass
    alertable = valid_risk_candidates[:max_alerts]

    # ---------------- SIGNAL DISPATCH & PERSISTENCE ----------------
    alert_count = 0
    for c in alertable:
        entry_val = float(c.entry_price)
        sl_result = c.sl_result

        if is_historical_fallback:
            c.status = CandidateState.SCORED
            logger.info(f"🧪 [HISTORICAL FALLBACK] Candidate {c.symbol} @ ₹{entry_val:.2f} — Read-only mode, skipped database persistence & notifications.")
        else:
            rs_pct_val = float(rs_rankings.get(c.symbol, 50.0))
            
            sector_info = sector_rankings_dict.get(c.sector, {}) if c.sector else {}
            sector_name_val = sector_info.get("sector_name", "")

            # Exact score breakdown reconstruction for DB persistence
            rs_bonus_val = round(c.score_breakdown.get("rs_bonus", 0.0), 1)
            sector_bonus_val = round(c.score_breakdown.get("sector_bonus", 0.0), 1)
            final_score_val = round(c.final_score, 1)
            base_score_val = round(c.base_score, 1)

            saved, reason, _, _ = save_alert_if_new(
                symbol=c.symbol,
                breakout_type="PULLBACK",
                alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                scanner="PULLBACK",
                category="PULLBACK",
                entry_price=entry_val,
                stop_loss=sl_result.get("stop_loss"),
                target_1=sl_result.get("target_1"),
                target_2=sl_result.get("target_2"),
                target_3=sl_result.get("target_3"),
                score=final_score_val,
                context={
                    "config_version": c.config_version,
                    "structure": {
                        "depth_pct": c.structure.depth_pct,
                        "duration_bars": c.structure.duration_bars,
                        "volume_ratio": c.structure.volume_ratio
                    },
                    "score_breakdown": {
                        "base_score": base_score_val,
                        "rs_bonus": rs_bonus_val,
                        "sector_bonus": sector_bonus_val,
                        "vol_bonus": round(c.score_breakdown.get("vol_bonus", 0.0), 1),
                        "trigger_bonus": round(c.score_breakdown.get("trigger_bonus", 0.0), 1),
                        "eligible_bonus": round(c.score_breakdown.get("eligible_bonus", 0.0), 1),
                        "penalty": round(c.score_breakdown.get("penalty", 0.0), 1)
                    }
                },
                base_score=base_score_val,
                rs_bonus=rs_bonus_val,
                sector_bonus=sector_bonus_val,
                rs_percentile=rs_pct_val,
                sector_name=sector_name_val,
                regime_score=float(MarketRegimeEngine.get_regime_context().get("market_score", 80.0))
            )

            if saved:
                alert_count += 1
                c.status = CandidateState.ALERTED
                logger.info(
                    f"✅ [PULLBACK] PASSED ALL FILTERS: {c.symbol} | "
                    f"score={final_score_val} | entry=₹{entry_val:.2f} | "
                    f"depth={c.structure.depth_pct:.1f}% | volume_ratio={c.structure.volume_ratio:.2f} | "
                    f"category=PULLBACK"
                )
                try:
                    from telegram_engine import send_telegram_message
                    msg = (
                        f"↪️ <b>PULLBACK CONTINUATION ALERT</b> ↪️\n\n"
                        f"📌 <b>Symbol:</b> #{c.symbol}\n"
                        f"💰 <b>Entry Price:</b> ₹{entry_val:.2f}\n"
                        f"🛑 <b>Stop Loss:</b> ₹{sl_result.get('stop_loss', 0):.2f}\n"
                        f"🎯 <b>Target 1:</b> ₹{sl_result.get('target_1', 0):.2f}\n"
                        f"🎯 <b>Target 2:</b> ₹{sl_result.get('target_2', 0):.2f}\n"
                        f"🎯 <b>Target 3:</b> ₹{sl_result.get('target_3', 0):.2f}\n"
                        f"📊 <b>Score:</b> {c.final_score:.1f}/100\n"
                        f"📉 <b>Pullback Retracement:</b> {c.structure.depth_pct:.1f}% of impulse wave ({c.structure.duration_bars} bars)\n"
                        f"🔊 <b>Volume Ratio:</b> {c.structure.volume_ratio:.2f}x\n"
                        f"⚡ <b>Mode:</b> LIVE PRODUCTION"
                    )
                    send_telegram_message(msg, scan_type="PULLBACK")
                except Exception as tg_err:
                    logger.warning(f"⚠️ Could not dispatch Telegram message for {c.symbol}: {tg_err}")
            else:
                c.status = CandidateState.SUPPRESSED
                rejected["persistence_failed"] += 1
                logger.info(f"REJECTION: {c.symbol} (Phase: PERSISTENCE, Reason: {reason})")

    if not is_historical_fallback:
        upsert_scanner_health(
            "PULLBACK",
            status=status_val,
            last_success=ist_now.isoformat(),
            today_alerts=alert_count,
            total_count=total_symbols,
            processed_count=symbols_processed,
            duration_seconds=elapsed_time,
            error_msg=err_val
        )
        if status_val == "OK":
            try:
                insert_notification("admin", f"🎯 Pullback Scanner ran successfully. Found {alert_count} pullback alerts.", f"Generated {alert_count} alerts from {total_symbols} scanned stocks. Outcome: SUCCESS")
                from push_service import send_push_to_all
                send_push_to_all("🎯 Pullback Scanner OK", f"Found {alert_count} pullback alerts.", bypass_throttle=True)
            except Exception:
                pass
        elif status_val == "DEGRADED":
            try:
                insert_notification("admin", f"⚠️ Pullback Scanner finished with DEGRADED status", err_val or f"Generated {alert_count} alerts but data was degraded.")
                from push_service import send_push_to_all
                send_push_to_all("⚠️ Pullback Scanner DEGRADED", err_val or "Stale data exceeded limit.")
            except Exception:
                pass
    fired_pb = {k: v for k, v in rejected.items() if v > 0}
    stale_count = rejected.get("stale_data", 0)
    no_data_count = rejected.get("no_data", 0)
    fresh_count = len(fresh_valid_symbols)
    data_status = "DEGRADED (Stale Data > 20%)" if (stale_count / max(total_symbols, 1)) > 0.20 else "OK"

    summary_lines = [
        "======================================================================",
        "=== [PULLBACK SCANNER PIPELINE SUMMARY] ===",
        "======================================================================",
        "📊 DATA QUALITY SNAPSHOT:",
        f"  • Total Watchlist Requested : {total_symbols}",
        f"  • Provider Resolved Symbols : {total_fetched_count}",
        f"  • Fresh Valid Data OK       : {fresh_count}",
        f"  • Stale Data                : {stale_count}",
        f"  • Missing / No Data         : {no_data_count}",
        f"  • Data Health Status        : {data_status}",
        "",
        "🎯 CRITERIA & FILTER BREAKDOWN:"
    ]
    for k, v in fired_pb.items():
        summary_lines.append(f"  • {k:<27}: {v}")

    summary_lines.extend([
        "",
        "🏆 FINAL OUTCOME:",
        f"  • Alerts Generated          : {alert_count}",
        f"  • Total Execution Time      : {elapsed_time}s",
        "======================================================================"
    ])
    logger.info("\n".join(summary_lines))
    try:
        stage_tracker.end_stage(f"Alerts={alert_count} persisted")
        stage_tracker.print_summary(alerts_found=alert_count)
    except Exception:
        pass

    if not is_historical_fallback:
        logger.info(f"✅ [COMPLETE] PULLBACK SCANNER DONE | {elapsed_time:.2f}s | Alerts={alert_count} | Status={status_val}")
    else:
        logger.info(f"✅ [COMPLETE] PULLBACK SCANNER DONE (historical fallback) | {elapsed_time:.2f}s | Candidates={len(candidates)} | Dataset={dataset_date}")

    return {
        "total_count": total_symbols,
        "processed_count": symbols_processed,
        "today_alerts": alert_count
    }
