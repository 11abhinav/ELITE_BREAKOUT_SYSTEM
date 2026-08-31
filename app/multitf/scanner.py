# =====================================================================================
# app/multitf/scanner.py
# MULTI_TF V2 — Main Execution Orchestrator
#
# Responsibility: Main entry point for the V2 scanner.
# Loops through the watchlist, delegates to engines, and pushes CONFIRMED signals
# directly to the global OpportunityManager.
# =====================================================================================

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional, List

IST = ZoneInfo("Asia/Kolkata")

from config import MULTI_TF_V2_CONFIG
from database import get_elite_watchlist, save_alert_if_new
from opportunity_manager import OpportunityManager
from lock_utils import ProcessLock
from database import upsert_scanner_health
from price_cache import fetch_watchlist_data

from multitf.data import load_multitf_data, strip_closed_candles
from multitf.context import evaluate_1h_context, evaluate_30m_context, evaluate_market_context
from multitf.consolidation import detect_15m_consolidation
from multitf.pressure import evaluate_5m_pressure
from multitf.confluence import evaluate_breakout_confluence
from multitf.state import (
    load_state,
    apply_ttl_and_cooldown,
    handle_box_invalidation,
    persist_new_watchlist_candidate,
    update_state_in_db,
    get_active_armed_candidates,
    MtfSubstate
)
from multitf.candidate import build_watchlist_candidate, build_confirmed_payload

from sl_target_helper import compute_sl_and_target

logger = logging.getLogger("multitf.scanner")
_scan_lock = ProcessLock("multi_tf_scanner")


def run_multitf_v2(regime_ctx: Dict[str, Any], ist_now: datetime, run_ctx: str = "SCHEDULED"):
    """
    Main Primary Intelligence Layer for MULTI_TF V2 (15-Minute Cadence).
    1. Pre-fetches 1d and 15m closed bars across universe.
    2. Detects 15m consolidation setups.
    3. Lazy-fetches 1h, 30m, 5m ONLY for shortlisted/armed candidate stocks.
    4. Evaluates setups, targets, and dispatches breakout alerts.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("[MULTI_TF] Scanner is already running. Skipping cycle.")
        return

    logger.info("=" * 70)
    logger.info("📊 MULTI_TF V2 ENGINE | Starting 15m execution cycle (Lazy Fetch)...")
    logger.info("=" * 70)

    from telemetry_manager import telemetry
    from perf_utils import ScannerStageTracker

    telemetry.log_scheduler_event("MULTI_TF", "CYCLE_START")
    stage_tracker = ScannerStageTracker("MULTI_TF_V2")

    # Create proper DB execution run context
    trigger_type = run_ctx if isinstance(run_ctx, str) else "SCHEDULED"
    from database import start_scanner_execution_run, complete_scanner_execution_run
    try:
        real_run_ctx = start_scanner_execution_run(scanner_name="MULTI_TF", trigger_type=trigger_type, scheduler_name="CRON")
    except Exception as exc:
        logger.warning(f"⚠️ [MULTI_TF] Could not create run_ctx: {exc}")
        real_run_ctx = None

    start_time = time.monotonic()

    try:
        stage_tracker.start_stage(1, "Load Watchlist", "Fetching elite watchlist symbols from DB")
        upsert_scanner_health(
            scanner_name="MULTI_TF",
            status="RUNNING",
            error_msg="Scan execution in progress..."
        )

        watchlist = get_elite_watchlist()
        if not watchlist:
            logger.warning("[MULTI_TF] Watchlist empty.")
            upsert_scanner_health(
                scanner_name="MULTI_TF",
                status="OK",
                outcome="SUCCESS",
                processed_count=0,
                duration_seconds=round(time.monotonic() - start_time, 2)
            )
            stage_tracker.end_stage("Watchlist empty")
            telemetry.log_scheduler_event("MULTI_TF", "CYCLE_COMPLETE")
            return

        stage_tracker.end_stage(f"Loaded {len(watchlist)} symbols")

        # Stage 2: Intelligence Layer: Universe Pre-fetch (1d and 15m)
        stage_tracker.start_stage(2, "Fetch Setup Data (1d, 15m)", "Pre-fetching 1d and 15m closed bars across universe")
        logger.info("[MULTI_TF] Pre-fetching setup timeframes (1d, 15m) for %d universe symbols...", len(watchlist))
        t_fetch_start = time.monotonic()

        all_1d  = fetch_watchlist_data(watchlist, period="1y", interval="1d", requester="MULTI_TF", run_ctx=real_run_ctx)
        all_15m = fetch_watchlist_data(watchlist, period="15d", interval="15m", requester="MULTI_TF", run_ctx=real_run_ctx)

        # Stage 2.5: Fast 15m Consolidation Screening across universe
        shortlisted_symbols = []
        consolidation_map = {}
        for symbol in watchlist:
            df_15m_raw = all_15m.get(symbol)
            if df_15m_raw is None or (hasattr(df_15m_raw, "empty") and df_15m_raw.empty):
                continue
            df_15m_closed = strip_closed_candles(df_15m_raw, 15, ist_now)
            if df_15m_closed is None or df_15m_closed.empty or len(df_15m_closed) < 14:
                continue
            atr_15m = float(df_15m_closed["ATR_14"].iloc[-1]) if "ATR_14" in df_15m_closed else 0.0
            if atr_15m <= 0:
                continue
            cons = detect_15m_consolidation(df_15m_closed, atr_15m, ist_now, MULTI_TF_V2_CONFIG)
            if cons.is_valid:
                shortlisted_symbols.append(symbol)
                consolidation_map[symbol] = cons

        # Also include any previously ARMED candidates from DB to ensure active setups continue tracking
        active_armed = get_active_armed_candidates()
        for cand in active_armed:
            sym = cand.get("symbol")
            if sym and sym not in shortlisted_symbols:
                shortlisted_symbols.append(sym)

        logger.info(f"🎯 [MULTI_TF] Screened {len(watchlist)} symbols -> Found {len(shortlisted_symbols)} qualified/armed candidates for deep evaluation: {shortlisted_symbols}")

        # Lazy fetch 1h, 30m, 5m ONLY for shortlisted candidates!
        all_1h = {}
        all_30m = {}
        all_5m = {}
        if shortlisted_symbols:
            logger.info(f"⚡ [MULTI_TF] Lazy-fetching (1h, 30m, 5m) for {len(shortlisted_symbols)} shortlisted candidates...")
            all_1h  = fetch_watchlist_data(shortlisted_symbols, period="45d", interval="1h", requester="MULTI_TF", run_ctx=real_run_ctx)
            all_30m = fetch_watchlist_data(shortlisted_symbols, period="20d", interval="30m", requester="MULTI_TF", run_ctx=real_run_ctx)
            all_5m  = fetch_watchlist_data(shortlisted_symbols, period="5d",  interval="5m",  requester="MULTI_TF", run_ctx=real_run_ctx)

        t_fetch_dur = round(time.monotonic() - t_fetch_start, 2)
        logger.info("⚡ [MULTI_TF] Completed market data pre-fetch in %ss", t_fetch_dur)
        stage_tracker.end_stage(f"Fetched data in {t_fetch_dur}s")

        # Stage 3: Process Symbols
        stage_tracker.start_stage(3, "Process Symbols", "Evaluating compression and breakout models per symbol")
        logger.info("[MULTI_TF] Analyzing breakout signals for shortlisted symbols...")
        t_process_start = time.monotonic()
        opp_manager = OpportunityManager(policy=regime_ctx.get("policy", {}) if regime_ctx else {})

        target_evaluation_symbols = shortlisted_symbols if shortlisted_symbols else []
        for symbol in target_evaluation_symbols:
            if real_run_ctx:
                try:
                    if hasattr(real_run_ctx, "heartbeat"):
                        real_run_ctx.heartbeat()
                except Exception:
                    pass
            try:
                _process_symbol(
                    symbol=symbol,
                    ist_now=ist_now,
                    regime_ctx=regime_ctx,
                    opp_manager=opp_manager,
                    all_1d=all_1d,
                    all_1h=all_1h,
                    all_30m=all_30m,
                    all_15m=all_15m,
                    all_5m=all_5m,
                    config=MULTI_TF_V2_CONFIG
                )
            except Exception as loop_exc:
                logger.error("[MULTI_TF] Failed processing %s: %s", symbol, loop_exc)

        t_process_dur = round(time.monotonic() - t_process_start, 2)
        logger.info("⚡ [MULTI_TF] Completed symbol evaluations in %ss", t_process_dur)
        stage_tracker.end_stage(f"Processed symbols in {t_process_dur}s")

        # Stage 4: Dispatch Opportunities
        stage_tracker.start_stage(4, "Dispatch Opportunities", "Filtering and executing OpportunityManager alerts")
        t_opp_start = time.monotonic()
        try:
            opp_manager.process()
        except Exception as e:
            logger.error("[MULTI_TF] OpportunityManager failed to process: %s", e)
        t_opp_dur = round(time.monotonic() - t_opp_start, 2)
        stage_tracker.end_stage(f"Dispatched in {t_opp_dur}s")

        duration = round(time.monotonic() - start_time, 2)
        upsert_scanner_health(
            scanner_name="MULTI_TF",
            status="OK",
            outcome="SUCCESS",
            processed_count=len(watchlist),
            total_count=len(watchlist),
            duration_seconds=duration
        )

        if real_run_ctx:
            try:
                real_run_ctx.set_total_stocks(len(watchlist))
                real_run_ctx.record_fresh_data(len(watchlist))
                complete_scanner_execution_run(real_run_ctx, status_override="COMPLETED")
            except Exception as _c_err:
                logger.warning(f"⚠️ [MULTI_TF] Failed to complete execution run: {_c_err}")

        telemetry.log_scheduler_event("MULTI_TF", "CYCLE_COMPLETE")
        logger.info("✅ MULTI_TF V2 ENGINE | Execution cycle complete in %ss.", duration)

        # Background sync updated history bundles to PostgreSQL parquet_cache so restarts never re-fetch
        try:
            from database import upload_history_bundle_to_db, submit_background_upload
            submit_background_upload(lambda: upload_history_bundle_to_db("15m", force=True))
            submit_background_upload(lambda: upload_history_bundle_to_db("1d"))
        except Exception as _sync_err:
            logger.debug(f"[MULTI_TF] History bundle background upload dispatch error: {_sync_err}")

    except Exception as exc:
        duration = round(time.monotonic() - start_time, 2)
        logger.error("[MULTI_TF] Fatal error during cycle: %s", exc)
        upsert_scanner_health(
            scanner_name="MULTI_TF",
            status="DOWN",
            outcome="FAILED",
            error_msg=str(exc),
            duration_seconds=duration
        )
        if real_run_ctx:
            try:
                complete_scanner_execution_run(real_run_ctx, exception=exc)
            except Exception:
                pass
        telemetry.log_scheduler_event("MULTI_TF", "CYCLE_FAILED", error=str(exc))
    finally:
        _scan_lock.release()


def run_multitf_5m_monitor(regime_ctx: Optional[Dict[str, Any]] = None, ist_now: Optional[datetime] = None, run_ctx: Any = None):
    """
    Secondary Confirmation Layer: Runs every 5 minutes on closed 5m candles.
    Only checks currently ARMED candidates from mtf_v2_watchlist.
    Takes < 3 seconds to confirm 5m pressure/expansion and trigger alerts.
    """
    active_candidates = get_active_armed_candidates()
    if not active_candidates:
        logger.debug("[MULTI_TF_5M] No active armed candidates to monitor.")
        return

    if not _scan_lock.acquire(blocking=False):
        logger.debug("[MULTI_TF_5M] Scanner lock busy. Skipping 5m monitor cycle.")
        return

    if ist_now is None:
        ist_now = datetime.now(ZoneInfo("Asia/Kolkata") if "ZoneInfo" in globals() else None)
    if regime_ctx is None:
        regime_ctx = {"status": "NORMAL"}

    trigger_type = run_ctx if isinstance(run_ctx, str) else "SCHEDULED"
    from database import start_scanner_execution_run, complete_scanner_execution_run
    try:
        real_run_ctx = start_scanner_execution_run(scanner_name="MULTI_TF_5M", trigger_type=trigger_type, scheduler_name="CRON")
    except Exception:
        real_run_ctx = None

    start_time = time.monotonic()
    try:
        symbols = list({c["symbol"] for c in active_candidates if c.get("symbol")})
        logger.info(f"⚡ [MULTI_TF_5M] Monitoring {len(symbols)} ARMED candidates for 5m breakout: {symbols}")

        all_1d  = fetch_watchlist_data(symbols, period="1y", interval="1d", requester="MULTI_TF_5M", run_ctx=real_run_ctx)
        all_1h  = fetch_watchlist_data(symbols, period="45d", interval="1h", requester="MULTI_TF_5M", run_ctx=real_run_ctx)
        all_30m = fetch_watchlist_data(symbols, period="20d", interval="30m", requester="MULTI_TF_5M", run_ctx=real_run_ctx)
        all_15m = fetch_watchlist_data(symbols, period="15d", interval="15m", requester="MULTI_TF_5M", run_ctx=real_run_ctx)
        all_5m  = fetch_watchlist_data(symbols, period="5d",  interval="5m",  requester="MULTI_TF_5M", run_ctx=real_run_ctx)

        opp_manager = OpportunityManager(policy=regime_ctx.get("policy", {}) if regime_ctx else {})
        for symbol in symbols:
            try:
                _process_symbol(
                    symbol=symbol,
                    ist_now=ist_now,
                    regime_ctx=regime_ctx,
                    opp_manager=opp_manager,
                    all_1d=all_1d,
                    all_1h=all_1h,
                    all_30m=all_30m,
                    all_15m=all_15m,
                    all_5m=all_5m,
                    config=MULTI_TF_V2_CONFIG
                )
            except Exception as e:
                logger.error(f"[MULTI_TF_5M] Error evaluating {symbol}: {e}")

        opp_manager.process()
        duration = round(time.monotonic() - start_time, 2)
        upsert_scanner_health(
            scanner_name="MULTI_TF_5M",
            status="OK",
            outcome="SUCCESS",
            processed_count=len(symbols),
            total_count=len(symbols),
            duration_seconds=duration
        )
        if real_run_ctx:
            try:
                real_run_ctx.set_total_stocks(len(symbols))
                real_run_ctx.record_fresh_data(len(symbols))
                complete_scanner_execution_run(real_run_ctx, status_override="COMPLETED")
            except Exception:
                pass
        logger.info(f"✅ [MULTI_TF_5M] 5m monitor cycle complete in {duration}s for {len(symbols)} candidates.")
    except Exception as exc:
        duration = round(time.monotonic() - start_time, 2)
        logger.error(f"[MULTI_TF_5M] Error during 5m monitor: {exc}")
        upsert_scanner_health(
            scanner_name="MULTI_TF_5M",
            status="DOWN",
            outcome="FAILED",
            error_msg=str(exc),
            duration_seconds=duration
        )
        if real_run_ctx:
            try:
                complete_scanner_execution_run(real_run_ctx, exception=exc)
            except Exception:
                pass
    finally:
        _scan_lock.release()


def _process_symbol(
    symbol: str,
    ist_now: datetime,
    regime_ctx: Dict[str, Any],
    opp_manager: OpportunityManager,
    all_1d: Dict,
    all_1h: Dict,
    all_30m: Dict,
    all_15m: Dict,
    all_5m: Dict,
    config: Dict[str, Any]
):
    # 1. Load Data
    bundle = load_multitf_data(symbol, ist_now, all_1h, all_30m, all_15m, all_5m, all_1d)
    if not bundle.data_sufficient:
        return

    # Extract indicators
    atr_15m = float(bundle.df_15m_closed["ATR_14"].iloc[-1]) if "ATR_14" in bundle.df_15m_closed else 0.0
    atr_5m = float(bundle.df_5m_closed["ATR_14"].iloc[-1]) if "ATR_14" in bundle.df_5m_closed else 0.0
    if atr_15m <= 0 or atr_5m <= 0:
        return

    current_price = float(bundle.df_5m_closed["Close"].iloc[-1])

    # 2. Setup Detection (15m strictly closed)
    consolidation = detect_15m_consolidation(bundle.df_15m_closed, atr_15m, ist_now, config)
    if not consolidation.is_valid:
        return

    # 3. State Management
    state_record = load_state(symbol, consolidation.box_id)
    is_new = (state_record is None)

    # 4. Context Evaluation (lazy, only needed if valid setup exists)
    ctx_1h = evaluate_1h_context(bundle.df_1h, config)
    ctx_30m = evaluate_30m_context(bundle.df_30m, consolidation.box_high, config)
    market_ctx = evaluate_market_context(regime_ctx, symbol, bundle.df_5m_closed)

    if is_new:
        # First time seeing this box
        cand_dict = build_watchlist_candidate(bundle, consolidation, ctx_1h, ctx_30m, market_ctx, ist_now)
        persist_new_watchlist_candidate(cand_dict)
        state_record = load_state(symbol, consolidation.box_id) # Reload to get initialized record
        if not state_record: return

    # If already fully handled or invalid, exit early
    if state_record.mtf_substate in (MtfSubstate.INVALIDATED, MtfSubstate.BREAKOUT_CONFIRMED):
        return

    # Check invalidation logic
    if handle_box_invalidation(state_record, current_price, consolidation.box_low, atr_15m, ist_now):
        if not update_state_in_db(state_record, {}):
            return
        return

    # TTL checks
    current_5m_bars_count = len(bundle.df_5m_closed)
    if apply_ttl_and_cooldown(state_record, ist_now, current_5m_bars_count):
        if not update_state_in_db(state_record, {}):
            return

    if state_record.mtf_substate == MtfSubstate.FAILED_ATTEMPT:
        return # Cooling down

    # 5. Pressure / Expansion (5m Live + Closed)
    pressure = evaluate_5m_pressure(
        live_candle=bundle.live_5m,
        df_5m_closed=bundle.df_5m_closed,
        box_high=consolidation.box_high,
        atr_5m=atr_5m,
        ist_now=ist_now,
        config=config
    )

    updates = {}

    if pressure.is_confirmed and state_record.mtf_substate != MtfSubstate.BREAKOUT_CONFIRMED:
        # 6. Confluence Evaluation
        confluence = evaluate_breakout_confluence(
            consolidation=consolidation,
            pressure=pressure,
            ctx_1h=ctx_1h,
            ctx_30m=ctx_30m,
            market_ctx=market_ctx,
            config=config
        )

        if confluence.is_approved:
            # 7. R:R Target Generation
            sl_target = compute_sl_and_target(
                entry_price=float(bundle.df_5m_closed["Close"].iloc[-1]),
                atr=atr_5m,
                ticker=bundle.df_1h,  # Pass 1H for structural targets
                mode="MULTI_TF_V2",
                box_low=consolidation.box_low
            )

            # 8. Canonical Alert Registration (Record the setup regardless of economic tradeability)
            idempotency_signals = f"BOX_ID={consolidation.box_id}"

            tradeability_status = "NOT_TRADEABLE" if sl_target.get("is_rejected") else "TRADEABLE"
            tradeability_reason = "RR_REJECTED" if sl_target.get("is_rejected") else ""

            inserted, _, _, _ = save_alert_if_new(
                symbol=symbol,
                breakout_type="MULTI_TF",
                alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S"),
                scanner="MULTI_TF",
                category="INTRADAY",
                entry_price=sl_target.get("entry_price"),
                stop_loss=sl_target.get("stop_loss"),
                target_1=sl_target.get("target_1"),
                target_2=sl_target.get("target_2"),
                target_3=sl_target.get("target_3"),
                signals=idempotency_signals,
                score=int(confluence.total_score),
                volume_ratio=pressure.volume_ratio,
                context={
                    "box_id": consolidation.box_id,
                    "rr_ratio": sl_target.get("rr_ratio"),
                    "signal_status": "CONFIRMED",
                    "tradeability_status": tradeability_status,
                    "tradeability_reason": tradeability_reason
                }
            )

            if sl_target.get("is_rejected"):
                # 9a. Tradeability Rejection (Structurally valid, but poor RR)
                logger.info("[%s] CONFIRMED breakout rejected by R:R gate (%.2f < %.2f). Marked NOT_TRADEABLE.",
                            symbol, sl_target.get("rr_ratio", 0), config.get("MIN_RR_RATIO", 1.5))
                state_record.mtf_substate = MtfSubstate.INVALIDATED
                state_record.state = "REJECTED"
                updates["invalidated_at"] = ist_now
                updates["invalidation_reason"] = "NOT_TRADEABLE"
                try:
                    from near_miss_tracker import log_near_miss
                    log_near_miss(
                        symbol=symbol,
                        scanner="MULTI_TF",
                        breakout_type="MULTI_TF",
                        gate_name="rr_ratio_gate",
                        observed_value=float(sl_target.get("rr_ratio", 0.0)),
                        threshold_value=float(config.get("MIN_RR_RATIO", 1.5)),
                        score=int(confluence.total_score),
                        entry_price=float(sl_target.get("entry_price") or 0.0),
                        stop_loss=float(sl_target.get("stop_loss") or 0.0),
                        target_1=float(sl_target.get("target_1") or 0.0),
                    )
                except Exception:
                    pass
            else:
                # 9b. Tradeable -> Dispatch to OpportunityManager
                if inserted:
                    payload = build_confirmed_payload(
                        bundle=bundle,
                        consolidation=consolidation,
                        pressure=pressure,
                        confluence=confluence,
                        sl_target=sl_target,
                        ist_now=ist_now
                    )
                    opp_manager.add(payload)
                else:
                    logger.debug("[%s] Alert already processed for box %s, skipping OpportunityManager.", symbol, consolidation.box_id)

                state_record.mtf_substate = MtfSubstate.BREAKOUT_CONFIRMED
                state_record.state = "CONFIRMED"
                updates["last_confirmation_ts"] = ist_now

    elif pressure.is_attempt and state_record.mtf_substate == MtfSubstate.WATCHING:
        # Switch to ATTEMPT state
        state_record.mtf_substate = MtfSubstate.ATTEMPT
        state_record.state = "CANDIDATE"
        state_record.attempt_count += 1

        updates["attempt_started_ts"] = ist_now
        updates["last_attempt_ts"] = ist_now
        updates["attempt_bar_boundary"] = pressure.attempt_bar_boundary

    # 10. Sync state changes to DB
    if updates or state_record.mtf_substate != MtfSubstate.WATCHING:
        update_state_in_db(state_record, updates)
