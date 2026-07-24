# =====================================================================================
# app/pullback_pipeline.py
# V8-PB PULLBACK CONTINUATION SCANNER PIPELINE (PURE ORCHESTRATOR PATTERN)
# =====================================================================================

import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
import pandas as pd

from core_enums import CandidateState, RejectionReason
from core_models import PullbackCandidate, DataQualityError
from config import PULLBACK_CONFIG, PULLBACK_CONFIG as config, REGIME_POLICIES
import swing_utils
import scoring_engine
from sl_target_helper import compute_sl_and_target
from database import (
    init_db, save_alert_if_new, upsert_scanner_health,
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

def start(force: bool = False):
    """
    Main entry point for Pullback Scanner. Acquires process lock and delegates to pipeline.
    """
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("Pullback Scanner is already actively running!")
    try:
        return run_pullback_pipeline(force=force)
    finally:
        _scan_lock.release()

def run_pullback_pipeline(run_date: str = None, force: bool = False) -> int:
    init_db()
    ist_now = datetime.now(IST)
    if not run_date:
        run_date = ist_now.strftime("%Y-%m-%d")
        
    logger.info("=" * 80)
    logger.info(f"🚀🚀🚀 [START] PULLBACK SCANNER PIPELINE INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')} 🚀🚀🚀")
    logger.info("=" * 80)
    
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

    policy = REGIME_POLICIES.get(market_regime, REGIME_POLICIES["NEUTRAL"])
    base_threshold = 75 # Default pullback base threshold
    score_modifier = policy.get("score_modifier", 0)
    required_threshold = base_threshold + score_modifier

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
    
    dataset_date = None
    if sample_data:
        for s_df in sample_data.values():
            if s_df is not None and not s_df.empty:
                try:
                    last_dt = s_df.iloc[-1].name if isinstance(s_df.index, pd.DatetimeIndex) else s_df.iloc[-1].get("Date", s_df.iloc[-1].get("Datetime"))
                    if last_dt:
                        dataset_date = pd.to_datetime(last_dt).strftime("%Y-%m-%d")
                        break
                except Exception:
                    pass

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
        if sample_data:
            for s_df in sample_data.values():
                if s_df is not None and not s_df.empty:
                    try:
                        last_dt = s_df.iloc[-1].name if isinstance(s_df.index, pd.DatetimeIndex) else s_df.iloc[-1].get("Date", s_df.iloc[-1].get("Datetime"))
                        if last_dt:
                            dataset_date = pd.to_datetime(last_dt).strftime("%Y-%m-%d")
                            break
                    except Exception:
                        pass

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
    prior_eod_symbols = {s for (s, _) in get_recent_alerts_for_scanner("EOD", prior_window_mins)}
    prior_multi_symbols = {s for (s, _) in get_recent_alerts_for_scanner("MULTIBAGGER", prior_window_mins)}.union(
        {s for (s, _) in get_recent_alerts_for_scanner("MULTI_TF", prior_window_mins)}
    )

    BATCH_SIZE = int(os.environ.get("PULLBACK_FETCH_BATCH_SIZE", "50"))
    total_batches = (len(watchlist) + BATCH_SIZE - 1) // BATCH_SIZE
    
    candidates: list[PullbackCandidate] = []

    # Rejection and provider counters for end-of-scan telemetry
    rejected = {k: 0 for k in [
        "no_data", "provider_error", "insufficient_bars", "data_quality",
        "no_uptrend", "no_pivots", "no_impulse", "pullback_invalid",
        "no_trigger", "processing_error"
    ]}
    provider_stats_counts = {
        "SUCCESS": 0, "NOT_FOUND": 0, "RATE_LIMIT": 0,
        "NETWORK_ERROR": 0, "TIMEOUT": 0, "EMPTY_DATA": 0
    }
    total_fetched_count = 0

    # ---------------- ORCHESTRATION LOOP ----------------
    with MemoryProfiler("Pullback Scanner Process"):
        for batch_num, chunk_df in enumerate(chunk_iterable(watchlist, BATCH_SIZE), start=1):
            with BatchMemoryTracker("PULLBACK", batch_num, total_batches, len(chunk_df), collect_gc=True) as tracker:
                all_ticker_data = fetch_watchlist_data(chunk_df, "1y", "1d")
                if not all_ticker_data:
                    continue

                valid_fetches = sum(1 for v in all_ticker_data.values() if isinstance(v, pd.DataFrame) and not v.empty)
                total_fetched_count += valid_fetches

                for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):
                    symbol = row.get("Stock", "UNKNOWN")
                    try:
                        category = row.get("Category", "MIDCAP")
                        sector = row.get("Sector", None)

                        from core_enums import ProviderResult

                        # Robust symbol resolution across .NS / .BO suffixes
                        ticker_data = all_ticker_data.get(symbol)
                        if ticker_data is None:
                            ticker_data = all_ticker_data.get(f"{symbol}.NS") or all_ticker_data.get(f"{symbol}.BO") or all_ticker_data.get(symbol.split('.')[0])

                        if ticker_data is None:
                            logger.debug(f"[PULLBACK] {symbol} rejected: missing historical data")
                            rejected["no_data"] += 1
                            provider_stats_counts["EMPTY_DATA"] += 1
                            continue

                        if isinstance(ticker_data, ProviderResult):
                            logger.debug(f"[PULLBACK] {symbol} rejected: Provider error ({ticker_data.name})")
                            provider_stats_counts[ticker_data.name] = provider_stats_counts.get(ticker_data.name, 0) + 1
                            rejected["provider_error"] += 1
                            continue
                        else:
                            provider_stats_counts["SUCCESS"] += 1

                        df = ticker_data.copy()
                        if df.empty or len(df) < effective_config.get("MIN_HISTORY", 260):
                            logger.debug(f"[PULLBACK] {symbol} rejected: insufficient historical bars ({len(df) if isinstance(df, pd.DataFrame) else 0} < {effective_config.get('MIN_HISTORY', 260)})")
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
                            logger.debug(f"⏭️ {symbol} failed data quality check: {dqe}")
                            rejected["data_quality"] += 1
                            continue

                        from indicator_manager import manager
                        bundle = manager.compute_base_indicators(historical_view, symbol)
                        last_bar = historical_view.iloc[-1]
                        
                        sma50_val = bundle.sma_50.iloc[-1] if bundle.sma_50 is not None and not bundle.sma_50.empty else None
                        sma200_val = bundle.sma_200.iloc[-1] if bundle.sma_200 is not None and not bundle.sma_200.empty else None

                        if not (sma50_val and sma200_val and last_bar['Close'] > sma50_val > sma200_val):
                            rejected["no_uptrend"] += 1
                            continue

                        # PHASE B: IMPULSE & ORDERLY PULLBACK STRUCTURE
                        pivots = swing_utils.detect_confirmed_pivots(historical_view, effective_config["LOOKBACK"], effective_config["CONFIRM"])
                        if not pivots:
                            rejected["no_pivots"] += 1
                            continue

                        impulse = swing_utils.select_pullback_origin(pivots, historical_view, effective_config)
                        if not impulse:
                            rejected["no_impulse"] += 1
                            continue

                        ps = swing_utils.measure_pullback(historical_view, impulse, effective_config, debug=effective_config.get("DEBUG_SWINGS", False))
                        save_funnel_telemetry("PULLBACK", run_date, symbol, ps.stage_results)
                    
                        if not ps.valid:
                            rejected["pullback_invalid"] += 1
                            continue

                        # PHASE C: RESUMPTION TRIGGER
                        trig = swing_utils.detect_resumption_trigger(historical_view, ps, effective_config)
                        if not trig.valid:
                            rejected["no_trigger"] += 1
                            continue

                        cand = PullbackCandidate(
                            symbol=symbol,
                            as_of_date=ist_now.date(),
                            structure=ps,
                            trigger=trig,
                            entry_price=trig.entry_price,
                            warnings=[],
                            config_version=effective_config.get("VERSION", "pb-1.0.0"),
                            status=CandidateState.NEW
                        )
                        candidates.append(cand)
                    except Exception as sym_err:
                        logger.error(f"❌ Error processing symbol {symbol} in Pullback Scanner: {sym_err}")
                        rejected["processing_error"] += 1
                        continue
            del all_ticker_data
            import gc; gc.collect()

    logger.info(f"📊 Pullback Candidates Discovered: {len(candidates)}")

    # ---------------- SCORING & MODIFIERS ----------------
    for c in candidates:
        # Base scoring calculation
        base_score = 70.0 # Default starting baseline for candidates passing structure + trigger
        
        # Trend maturity penalty
        maturity_penalties = {0: 0, 1: 0, 2: -3, 3: -6}
        penalty = maturity_penalties.get(c.structure.pullback_count_in_trend, -10)
        
        # Combined evidence bonus (capped at MAX_BONUS=5)
        # +3 if prior EOD alert in last 30 days, +2 if prior MULTIBAGGER or MULTI_TF alert in last 30 days
        eod_bonus = 3 if c.symbol in prior_eod_symbols else 0
        multi_bonus = 2 if c.symbol in prior_multi_symbols else 0
        eligible_bonus = min(eod_bonus + multi_bonus, effective_config.get("MAX_BONUS", 5))
        
        c.base_score = base_score + penalty
        c.final_score = c.base_score + eligible_bonus

    # Filter out scores below threshold
    survivors = [c for c in candidates if c.final_score >= required_threshold]

    # ---------------- SAME-NIGHT EOD SUPPRESSION ----------------
    tonight_eod_alerts = get_recent_alerts_for_scanner("EOD", 300)
    for c in survivors:
        if (c.symbol, "EOD") in tonight_eod_alerts:
            c.status = CandidateState.SUPPRESSED
            c.suppressed_by = "EOD"
            logger.info(f"🛡️ Suppressing Pullback candidate {c.symbol} (EOD alert primary)")

    alertable = [c for c in survivors if c.status != CandidateState.SUPPRESSED]
    alertable.sort(key=lambda x: x.final_score, reverse=True)
    max_alerts = policy.get("max_new_positions_per_day", 3)
    alertable = alertable[:max_alerts]

    # ---------------- RISK ENGINE & SIGNAL DISPATCH ----------------
    alert_count = 0
    for c in alertable:
        entry_val = float(c.entry_price)
        sl_result = compute_sl_and_target(
            entry_price=entry_val,
            atr=float(c.structure.impulse.end.price - c.structure.pullback_low.price) * 0.2, # approximate ATR
            mode="PULLBACK",
            swing_low=c.structure.pullback_low.price,
            swing_high=c.structure.impulse.end.price,
        )

        if sl_result.get("is_rejected"):
            logger.info(f"⏭️ {c.symbol} rejected by SL/Target Engine: {sl_result.get('rejection_reason')}")
            continue

        c.status = CandidateState.ALERTED
        if is_historical_fallback:
            logger.info(f"🧪 [HISTORICAL FALLBACK] Candidate {c.symbol} @ ₹{entry_val:.2f} — Read-only mode, skipped database persistence & notifications.")
        else:
            # ── Feature F-03 & F-07: Momentum Bonus Injection ──
            from macro_utils import compute_nifty_rs_rating, compute_sector_regime_rankings
            from config import RS_BONUS, SECTOR_BONUS, MAX_MOMENTUM_BONUS

            rs_dict = compute_nifty_rs_rating([c.symbol])
            rs_pct_val = float(rs_dict.get(c.symbol, 50.0))
            rs_bonus_val = RS_BONUS if rs_pct_val >= 80.0 else 0

            sector_rankings_dict = compute_sector_regime_rankings()
            sector_info = sector_rankings_dict.get(sector, {}) if 'sector' in locals() else {}
            sector_status = sector_info.get("effective_status", "NEUTRAL")
            sector_name_val = sector_info.get("sector_name", "")
            sector_bonus_val = SECTOR_BONUS if sector_status == "TAILWIND" else 0

            total_momentum_bonus = min(MAX_MOMENTUM_BONUS, rs_bonus_val + sector_bonus_val)
            base_score_val = int(c.final_score)
            final_score_val = min(100, base_score_val + total_momentum_bonus)

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
                logger.info(f"✅ ALERTED [PULLBACK] {c.symbol} @ ₹{entry_val:.2f} (Score: {c.final_score:.1f})")
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
                        f"📉 <b>Pullback Depth:</b> {c.structure.depth_pct:.1f}% ({c.structure.duration_bars} bars)\n"
                        f"🔊 <b>Volume Ratio:</b> {c.structure.volume_ratio:.2f}x\n"
                        f"⚡ <b>Mode:</b> LIVE PRODUCTION"
                    )
                    send_telegram_message(msg, scan_type="EOD")
                except Exception as tg_err:
                    logger.warning(f"⚠️ Could not dispatch Telegram message for {c.symbol}: {tg_err}")

    # ── CRITICAL BLOCKER GUARD ──
    no_data_count = rejected.get("no_data", 0)
    total_symbols = len(watchlist)

    if not is_historical_fallback:
        status_val = "OK"
        err_val = None

        if total_symbols > 0 and no_data_count >= total_symbols * 0.25:
            status_val = "DOWN"
            err_val = f"🚫 CRITICAL BLOCKER: {no_data_count}/{total_symbols} symbols unfetched (missing data)"
            logger.error(f"🚨 {err_val}")
            try:
                from telegram_engine import send_telegram_message
                send_telegram_message(f"🚨 <b>CRITICAL BLOCKER: PULLBACK SCANNER FAILED</b>\n{no_data_count}/{total_symbols} symbols were unfetched / missing data.")
            except Exception:
                pass
        elif total_symbols > 0 and total_fetched_count < total_symbols * 0.70:
            status_val = "DEGRADED"
            err_val = f"Partial Fetch: {total_fetched_count}/{total_symbols} symbols"

        upsert_scanner_health("PULLBACK", status=status_val, last_success=ist_now.isoformat(), today_alerts=alert_count, error_msg=err_val)
    
    fired_pb = {k: v for k, v in rejected.items() if v > 0}
    elapsed_time = round((datetime.now(IST) - ist_now).total_seconds(), 1)
    total_symbols = len(watchlist)
    stale_count = rejected.get("stale_data", 0)
    no_data_count = rejected.get("no_data", 0)
    fresh_count = max(0, total_fetched_count - stale_count)
    data_status = "DEGRADED (Stale Data > 20%)" if (stale_count / max(total_symbols, 1)) > 0.20 else "OK"

    summary_lines = [
        "======================================================================",
        "=== [PULLBACK SCANNER PIPELINE SUMMARY] ===",
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

    if not is_historical_fallback:
        logger.info(f"✅ [COMPLETE] PULLBACK SCANNER DONE | {elapsed_time:.2f}s | Alerts={alert_count} | Status={status_val}")
    else:
        logger.info(f"✅ [COMPLETE] PULLBACK SCANNER DONE (historical fallback) | {elapsed_time:.2f}s | Candidates={len(candidates)} | Dataset={dataset_date}")

    return alert_count

