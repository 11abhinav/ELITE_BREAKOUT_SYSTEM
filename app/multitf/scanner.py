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
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")

from config import MULTI_TF_V2_CONFIG
from database import get_elite_watchlist, get_multitf_universe, save_alert_if_new
from opportunity_manager import OpportunityManager
from lock_utils import ProcessLock
from database import upsert_scanner_health
from price_cache import fetch_watchlist_data

from multitf.data import load_multitf_data, strip_closed_candles
from multitf.context import evaluate_1h_context, evaluate_30m_context, evaluate_market_context
from multitf.consolidation import detect_15m_consolidation, prepare_15m_context, Prepared15mContext
from multitf.pressure import evaluate_5m_pressure
from multitf.confluence import evaluate_breakout_confluence
from multitf.breakout_strength import compute_breakout_strength, classify_alert_severity, SEVERITY_EMOJI, SEVERITY_LABEL
from multitf.alert_builder import build_multitf_alert_message
from multitf.state import (
    load_state,
    find_active_box_for_symbol,
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
_global_lock = ProcessLock("global_scanner_lock")


def _get_rss_mb() -> float:
    """Returns current process RSS memory in MB cross-platform (Linux & macOS)."""
    try:
        import psutil
        return float(psutil.Process().memory_info().rss) / (1024.0 * 1024.0)
    except Exception:
        pass
    try:
        import resource, sys
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return float(usage) / (1024.0 * 1024.0) if sys.platform == "darwin" else float(usage) / 1024.0
    except Exception:
        return 0.0


def _get_atr(df, default: float = 0.0) -> float:
    """Extracts ATR from DataFrame checking 'ATR_14', 'ATR', or 'ATR20', with rolling TrueRange fallback."""
    if df is None or not hasattr(df, "empty") or df.empty:
        return default
    for col in ("ATR_14", "ATR", "ATR20"):
        if col in df.columns and len(df[col]) > 0:
            val = float(df[col].iloc[-1])
            if val > 0:
                return val
    # Fallback: compute last 14-bar True Range average if OHLC columns exist
    try:
        if all(c in df.columns for c in ("High", "Low", "Close")) and len(df) >= 2:
            prev_c = df["Close"].shift(1)
            tr = pd.concat([
                df["High"] - df["Low"],
                (df["High"] - prev_c).abs(),
                (df["Low"] - prev_c).abs()
            ], axis=1).max(axis=1)
            atr_calc = float(tr.tail(14).mean())
            if atr_calc > 0:
                return atr_calc
    except Exception:
        pass
    return default


def run_multitf_v2(regime_ctx: Dict[str, Any], ist_now: datetime, run_ctx: str = "SCHEDULED"):
    """
    Main Primary Intelligence Layer for MULTI_TF V2 (15-Minute Cadence).
    1. Pre-fetches 1d and 15m closed bars across universe.
    2. Detects 15m consolidation setups.
    3. Lazy-fetches 1h, 30m, 5m ONLY for shortlisted/armed candidate stocks.
    4. Evaluates setups, targets, and dispatches breakout alerts.
    """
    if _scan_lock.locked():
        logger.warning("🛑 [DUPLICATE GUARD] MULTI_TF Scanner is ALREADY actively running in thread lock. Skipping duplicate trigger.")
        return

    acquired_global = False
    acquired_scan = False
    start_time = time.monotonic()
    real_run_ctx = None

    try:
        if not _scan_lock.acquire(blocking=False):
            logger.warning("[MULTI_TF] Scanner is already running. Skipping cycle.")
            try:
                from database import record_skipped_execution_run
                record_skipped_execution_run(scanner_name="MULTI_TF", trigger_type="SCHEDULED", scheduler_name="CRON", stop_reason="Scanner lock held (previous run active)")
            except Exception:
                pass
            return
        acquired_scan = True

        # Acquire universal global scanner lock
        if not _global_lock.acquire(blocking=False, owner_scanner="MULTI_TF", operation="FULL_SCAN"):
            logger.info("⏳ [MULTI_TF] Global scanner lock busy — waiting in queue until active scanner finishes...")
            upsert_scanner_health("MULTI_TF", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")

            try:
                acquired_global = _global_lock.acquire(blocking=True, owner_scanner="MULTI_TF", operation="FULL_SCAN")
            except Exception as lock_err:
                logger.error(f"❌ [MULTI_TF] Error acquiring global lock: {lock_err}")
                acquired_global = False

            if not acquired_global:
                logger.error("❌ [MULTI_TF] Failed to acquire global scanner lock after queue wait.")
                upsert_scanner_health("MULTI_TF", "IDLE", error_msg="Lock acquisition timed out")
                return
        else:
            acquired_global = True

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
            if "actively running" in str(exc).lower():
                logger.info("🛑 [MULTI_TF] Scanner is ALREADY actively running. Skipping duplicate execution.")
                return 0
            logger.warning(f"⚠️ [MULTI_TF] Could not create run_ctx: {exc}")
            real_run_ctx = None

        # [RULE 67 CHANGE-RATIONALE]: Explicitly record scheduled_for in scanner_health so
        # the dashboard always displays accurate, true schedule timings without relying on background seeders.
        _MTF_SCHEDULE = "Every 15m Scan / 5m Monitor (09:30 - 15:30 IST)"
        stage_tracker.start_stage(1, "Load Watchlist", "Fetching elite watchlist symbols from DB")
        upsert_scanner_health(
            scanner_name="MULTI_TF",
            status="RUNNING",
            error_msg="Scan execution in progress...",
            scheduled_for=_MTF_SCHEDULE
        )

        watchlist = get_multitf_universe()
        if not watchlist:
            logger.warning("[MULTI_TF] Watchlist empty.")
            upsert_scanner_health(
                scanner_name="MULTI_TF",
                status="OK",
                outcome="SUCCESS",
                processed_count=0,
                duration_seconds=round(time.monotonic() - start_time, 2),
                scheduled_for=_MTF_SCHEDULE
            )
            stage_tracker.end_stage("Watchlist empty")
            telemetry.log_scheduler_event("MULTI_TF", "CYCLE_COMPLETE")
            if real_run_ctx:
                complete_scanner_execution_run(real_run_ctx)
            return

        stage_tracker.end_stage(f"Loaded {len(watchlist)} symbols")

        # Stage 2: Intelligence Layer: Universe Pre-fetch (1d and 15m)
        stage_tracker.start_stage(2, "Fetch Setup Data (1d, 15m)", "Pre-fetching 1d and 15m closed bars across universe")
        logger.info("[MULTI_TF] Pre-fetching setup timeframes (1d, 15m) for %d universe symbols...", len(watchlist))
        t_fetch_start = time.monotonic()

        all_1d  = fetch_watchlist_data(watchlist, period="1y", interval="1d", requester="MULTI_TF", run_ctx=real_run_ctx)
        all_15m = fetch_watchlist_data(watchlist, period="15d", interval="15m", requester="MULTI_TF", run_ctx=real_run_ctx)

        # Stage 2.5: Fast 15m Consolidation Screening across universe (Adaptive V3)
        # [RULE 67 CHANGE-RATIONALE: OPTIMIZED_STAGE_2_5_V1.0]
        # 1. Zero DataFrame slicing inside candidate window loops; uses Prepared15mContext.
        # 2. Only mathematically provable necessary conditions gate deep evaluation (len < 6, atr <= 0, flatline).
        # 3. Preserves all 9 adaptive candidate windows [6, 8, 10, 12, 16, 20, 24, 30, 35] without 35-bar veto.
        # 4. Strict conservation-of-universe accounting: universe = fast_rejected + deep_screened (0 lost symbols).
        # 5. Periodic progress logging every 50 symbols and per-stage timing / latency / memory RSS profiling.
        shortlisted_symbols = []
        consolidation_map = {}
        total_symbols = len(watchlist)
        min_bars_config = MULTI_TF_V2_CONFIG.get("MIN_CONSOLIDATION_BARS", 6)

        rss_before = _get_rss_mb()
        rss_peak = rss_before
        t_stage25_start = time.monotonic()

        t_ctx_prep_total = 0.0
        t_fast_filter_total = 0.0
        t_deep_screen_total = 0.0
        symbol_latencies_ms: List[float] = []

        fast_rejected_breakdown = {
            "NO_DATA": 0,
            "INSUFFICIENT_BARS": 0,
            "ATR_ZERO_OR_NEG": 0,
            "FLATLINE_ZERO_RANGE": 0,
        }

        deep_screened_breakdown = {
            "QUALIFIED": 0,
            "PRESSURE": 0,
            "PRE_BREAKOUT": 0,
            "STRONG": 0,
            "FORMING": 0,
            "WIDTH_EXCEEDED": 0,
            "SCORE_TOO_LOW": 0,
            "TESTS_TOO_LOW": 0,
            "DORMANT": 0,
            "GAP_BROKEN": 0,
            "OTHER_REJECT": 0,
        }

        for idx, symbol in enumerate(watchlist):
            t_sym_start = time.perf_counter()
            if real_run_ctx and idx % 20 == 0:
                try:
                    real_run_ctx.heartbeat()
                except Exception:
                    pass

            if idx % 50 == 0:
                cur_rss = _get_rss_mb()
                if cur_rss > rss_peak:
                    rss_peak = cur_rss

            t_ff_start = time.perf_counter()
            df_15m_raw = all_15m.get(symbol)
            if df_15m_raw is None or (hasattr(df_15m_raw, "empty") and df_15m_raw.empty):
                fast_rejected_breakdown["NO_DATA"] += 1
                t_fast_filter_total += (time.perf_counter() - t_ff_start)
                symbol_latencies_ms.append((time.perf_counter() - t_sym_start) * 1000.0)
                continue

            df_15m_closed = strip_closed_candles(df_15m_raw, 15, ist_now)
            if df_15m_closed is None or df_15m_closed.empty or len(df_15m_closed) < min_bars_config:
                fast_rejected_breakdown["INSUFFICIENT_BARS"] += 1
                t_fast_filter_total += (time.perf_counter() - t_ff_start)
                symbol_latencies_ms.append((time.perf_counter() - t_sym_start) * 1000.0)
                continue

            atr_15m = _get_atr(df_15m_closed)
            if atr_15m <= 0:
                fast_rejected_breakdown["ATR_ZERO_OR_NEG"] += 1
                t_fast_filter_total += (time.perf_counter() - t_ff_start)
                symbol_latencies_ms.append((time.perf_counter() - t_sym_start) * 1000.0)
                continue

            t_fast_filter_total += (time.perf_counter() - t_ff_start)

            # Context Preparation (Single-pass contiguous numpy arrays + session dates)
            t_cp_start = time.perf_counter()
            ctx = prepare_15m_context(df_15m_closed, atr_15m, MULTI_TF_V2_CONFIG, symbol=symbol)
            t_ctx_prep_total += (time.perf_counter() - t_cp_start)

            if ctx is None:
                fast_rejected_breakdown["INSUFFICIENT_BARS"] += 1
                symbol_latencies_ms.append((time.perf_counter() - t_sym_start) * 1000.0)
                continue

            if ctx.recent_high <= ctx.recent_low:
                fast_rejected_breakdown["FLATLINE_ZERO_RANGE"] += 1
                symbol_latencies_ms.append((time.perf_counter() - t_sym_start) * 1000.0)
                continue

            # Deep Evaluation across candidate windows
            t_ds_start = time.perf_counter()
            cons = detect_15m_consolidation(
                df_15m_closed, atr_15m, ist_now, MULTI_TF_V2_CONFIG, symbol=symbol, precomputed_context=ctx
            )
            t_deep_screen_total += (time.perf_counter() - t_ds_start)

            if cons.is_valid:
                shortlisted_symbols.append(symbol)
                consolidation_map[symbol] = cons
                stage = getattr(cons, "lifecycle_stage", "FORMING")
                if stage in deep_screened_breakdown:
                    deep_screened_breakdown[stage] += 1
                else:
                    deep_screened_breakdown["QUALIFIED"] += 1
            else:
                reason = cons.rejection_reason or ""
                if "GAP" in reason:
                    deep_screened_breakdown["GAP_BROKEN"] += 1
                elif "WIDTH" in reason or "OCCUPANCY" in reason:
                    deep_screened_breakdown["WIDTH_EXCEEDED"] += 1
                elif "SCORE" in reason:
                    deep_screened_breakdown["SCORE_TOO_LOW"] += 1
                elif "TEST" in reason:
                    deep_screened_breakdown["TESTS_TOO_LOW"] += 1
                elif cons.is_dormant:
                    deep_screened_breakdown["DORMANT"] += 1
                else:
                    deep_screened_breakdown["OTHER_REJECT"] += 1

            symbol_latencies_ms.append((time.perf_counter() - t_sym_start) * 1000.0)

            # Event-based progress logging every 50 symbols
            if (idx + 1) % 50 == 0 or (idx + 1) == total_symbols:
                fast_rej_so_far = sum(fast_rejected_breakdown.values())
                logger.info(
                    "[MULTI_TF][2.5] Screening progress: %d/%d symbols processed (%d qualified, %d fast-rejected)",
                    idx + 1, total_symbols, len(shortlisted_symbols), fast_rej_so_far
                )

        t_stage25_total = time.monotonic() - t_stage25_start
        rss_after = _get_rss_mb()
        if rss_after > rss_peak:
            rss_peak = rss_after

        fast_rejected_count = sum(fast_rejected_breakdown.values())
        deep_screened_count = total_symbols - fast_rejected_count
        qualified_count = len(shortlisted_symbols)
        invalid_screened_count = deep_screened_count - qualified_count

        import numpy as _np
        if symbol_latencies_ms:
            lat_arr = _np.array(symbol_latencies_ms)
            p50_ms = float(_np.percentile(lat_arr, 50))
            p95_ms = float(_np.percentile(lat_arr, 95))
            max_ms = float(_np.max(lat_arr))
        else:
            p50_ms, p95_ms, max_ms = 0.0, 0.0, 0.0

        logger.info(
            f"\n[MULTI_TF][2.5] COMPLETE\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Universe                  : {total_symbols}\n"
            f"Fast rejected             : {fast_rejected_count}\n"
            f"  ├── No Data             : {fast_rejected_breakdown['NO_DATA']}\n"
            f"  ├── Insufficient Bars   : {fast_rejected_breakdown['INSUFFICIENT_BARS']}\n"
            f"  ├── ATR <= 0            : {fast_rejected_breakdown['ATR_ZERO_OR_NEG']}\n"
            f"  └── Flatline Zero Range : {fast_rejected_breakdown['FLATLINE_ZERO_RANGE']}\n"
            f"Deep screened             : {deep_screened_count} (valid={qualified_count}, invalid={invalid_screened_count})\n"
            f"  ├── Qualified Setups    : {qualified_count}\n"
            f"  │     ├── PRESSURE      : {deep_screened_breakdown['PRESSURE']}\n"
            f"  │     ├── PRE-BREAKOUT  : {deep_screened_breakdown['PRE_BREAKOUT']}\n"
            f"  │     ├── STRONG        : {deep_screened_breakdown['STRONG']}\n"
            f"  │     └── FORMING       : {deep_screened_breakdown['FORMING']}\n"
            f"  └── Rejections (Deep)   :\n"
            f"        ├── Width Exceeded: {deep_screened_breakdown['WIDTH_EXCEEDED']}\n"
            f"        ├── Score Too Low : {deep_screened_breakdown['SCORE_TOO_LOW']}\n"
            f"        ├── Tests Too Low : {deep_screened_breakdown['TESTS_TOO_LOW']}\n"
            f"        ├── Dormant Vol   : {deep_screened_breakdown['DORMANT']}\n"
            f"        ├── Gap Broken    : {deep_screened_breakdown['GAP_BROKEN']}\n"
            f"        └── Other Reject  : {deep_screened_breakdown['OTHER_REJECT']}\n"
            f"\n"
            f"Conservation Accounting   : {fast_rejected_count} + {deep_screened_count} = {total_symbols} (delta={total_symbols - (fast_rejected_count + deep_screened_count)})\n"
            f"\n"
            f"Timing\n"
            f"  Context preparation     : {t_ctx_prep_total:.2f}s\n"
            f"  Fast funnel             : {t_fast_filter_total:.2f}s\n"
            f"  Deep geometry & scoring : {t_deep_screen_total:.2f}s\n"
            f"  Total Stage 2.5         : {t_stage25_total:.2f}s\n"
            f"\n"
            f"Per-symbol Latency\n"
            f"  p50                     : {p50_ms:.1f}ms\n"
            f"  p95                     : {p95_ms:.1f}ms\n"
            f"  max                     : {max_ms:.1f}ms\n"
            f"\n"
            f"Memory\n"
            f"  RSS before              : {rss_before:.1f}MB\n"
            f"  RSS peak                : {rss_peak:.1f}MB\n"
            f"  RSS after               : {rss_after:.1f}MB\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

        if real_run_ctx:
            try:
                real_run_ctx.heartbeat(force=True)
            except Exception:
                pass

        # Also include any previously ARMED candidates from DB to ensure active setups continue tracking
        active_armed = get_active_armed_candidates()
        db_armed_symbols = set()
        for cand in active_armed:
            sym = cand.get("symbol")
            if sym:
                db_armed_symbols.add(sym)
                if sym not in shortlisted_symbols:
                    shortlisted_symbols.append(sym)

        # [RULE 67 CHANGE-RATIONALE: ACTIONABLE_LAZY_FETCH_TIERING_V1.0]
        # In a universe of 420 stocks, ~300 qualify as having some 15m base (mostly FORMING bases far from ceiling).
        # Downloading 45d 1h, 20d 30m, and 5d 5m for all 300+ stocks generates ~900 broker historical requests,
        # taking 35+ minutes over the network.
        # Instead, tier the lazy fetch:
        # Tier 1: Actionable / Near-Term setups (PRESSURE, PRE_BREAKOUT, STRONG, setup_score >= 60, or active DB-armed).
        # These are immediate trade candidates requiring multi-timeframe 5m/30m/1h analysis (~40-60 symbols).
        # Tier 2: Developing bases (FORMING with setup_score < 60). Their 15m base structure is already computed
        # and safely saved to DB/watchlist without wasting 30+ minutes downloading intraday bars.
        actionable_symbols = []
        for sym in shortlisted_symbols:
            if sym in db_armed_symbols:
                actionable_symbols.append(sym)
                continue
            cons = consolidation_map.get(sym)
            if cons is not None:
                stage = getattr(cons, "lifecycle_stage", "FORMING")
                score = getattr(cons, "setup_score", 0)
                if stage in ("PRESSURE", "PRE_BREAKOUT", "STRONG") or score >= 60:
                    actionable_symbols.append(sym)

        # Ensure deduplicated list preserving order
        actionable_symbols = list(dict.fromkeys(actionable_symbols))

        all_1h = {}
        all_30m = {}
        all_5m = {}
        if actionable_symbols:
            if real_run_ctx:
                try:
                    real_run_ctx.heartbeat(force=True)
                except Exception:
                    pass
            logger.info(
                f"⚡ [MULTI_TF] Lazy-fetching (1h, 30m, 5m) for {len(actionable_symbols)} actionable candidates "
                f"(out of {len(shortlisted_symbols)} qualified bases; deferred {len(shortlisted_symbols) - len(actionable_symbols)} forming bases)..."
            )
            all_1h  = fetch_watchlist_data(actionable_symbols, period="45d", interval="1h", requester="MULTI_TF", run_ctx=real_run_ctx)
            if real_run_ctx:
                try:
                    real_run_ctx.heartbeat(force=True)
                except Exception:
                    pass
            all_30m = fetch_watchlist_data(actionable_symbols, period="20d", interval="30m", requester="MULTI_TF", run_ctx=real_run_ctx)
            if real_run_ctx:
                try:
                    real_run_ctx.heartbeat(force=True)
                except Exception:
                    pass
            all_5m  = fetch_watchlist_data(actionable_symbols, period="5d",  interval="5m",  requester="MULTI_TF", run_ctx=real_run_ctx)
            if real_run_ctx:
                try:
                    real_run_ctx.heartbeat(force=True)
                except Exception:
                    pass

        t_fetch_dur = round(time.monotonic() - t_fetch_start, 2)
        logger.info("⚡ [MULTI_TF] Completed market data pre-fetch in %ss", t_fetch_dur)
        stage_tracker.end_stage(f"Fetched data in {t_fetch_dur}s")

        # Stage 3: Process Symbols
        stage_tracker.start_stage(3, "Process Symbols", "Evaluating compression and breakout models per symbol")
        logger.info("[MULTI_TF] Analyzing breakout signals for actionable symbols...")
        t_process_start = time.monotonic()
        opp_manager = OpportunityManager(policy=regime_ctx.get("policy", {}) if regime_ctx else {})

        target_evaluation_symbols = actionable_symbols if actionable_symbols else []
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
                    config=MULTI_TF_V2_CONFIG,
                    # [FIX: CONSOLIDATION_MAP_REUSE_v1.0]
                    # Pre-screen already computed consolidation for shortlisted symbols.
                    # Pass it in so _process_symbol doesn't re-run detect_15m_consolidation
                    # from scratch — avoids double CPU and prevents armed-only DB-pulled
                    # symbols from failing consolidation detection if box_id shifted.
                    precomputed_consolidation=consolidation_map.get(symbol)
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
            duration_seconds=duration,
            scheduled_for=_MTF_SCHEDULE
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

        # Telemetry distinguishing SCAN_SUCCESS and CACHE_PERSISTED vs CACHE_PERSIST_PENDING
        try:
            from database import get_interval_generation
            g_15m, up_15m = get_interval_generation("15m")
            g_1d, up_1d = get_interval_generation("1d")
            status_15m = f"15m:gen={g_15m}/up={up_15m}(" + ("PENDING" if up_15m < g_15m else "PERSISTED") + ")"
            status_1d = f"1d:gen={g_1d}/up={up_1d}(" + ("PENDING" if up_1d < g_1d else "PERSISTED") + ")"
            logger.info(f"💾 [MULTI_TF PERSISTENCE TELEMETRY] SCAN_SUCCESS | {status_15m} | {status_1d}")
        except Exception:
            pass

        # Background sync updated history bundles to PostgreSQL parquet_cache so restarts never re-fetch
        try:
            from database import upload_history_bundle_to_db, submit_background_upload
            submit_background_upload(lambda: upload_history_bundle_to_db("15m", min_interval_sec=300.0))
            submit_background_upload(lambda: upload_history_bundle_to_db("1d", min_interval_sec=300.0))
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
            duration_seconds=duration,
            scheduled_for=_MTF_SCHEDULE
        )
        if real_run_ctx:
            try:
                complete_scanner_execution_run(real_run_ctx, exception=exc)
            except Exception:
                pass
        telemetry.log_scheduler_event("MULTI_TF", "CYCLE_FAILED", error=str(exc))
    finally:
        if acquired_global:
            try:
                _global_lock.release()
            except Exception as _ge:
                logger.debug(f"Error releasing global lock: {_ge}")
        if acquired_scan:
            try:
                _scan_lock.release()
            except Exception as _se:
                logger.debug(f"Error releasing scan lock: {_se}")


def run_multitf_5m_monitor(regime_ctx: Optional[Dict[str, Any]] = None, ist_now: Optional[datetime] = None, run_ctx: Any = None):
    """
    Secondary Confirmation Layer: Runs every 5 minutes on closed 5m candles.
    Only checks currently ARMED candidates from mtf_v2_watchlist.
    Takes < 3 seconds to confirm 5m pressure/expansion and trigger alerts.
    """
    if ist_now is None:
        ist_now = datetime.now(ZoneInfo("Asia/Kolkata") if "ZoneInfo" in globals() else None)
    if regime_ctx is None:
        regime_ctx = {"status": "NORMAL"}

    trigger_type = run_ctx if isinstance(run_ctx, str) else "SCHEDULED"
    from database import start_scanner_execution_run, complete_scanner_execution_run, upsert_scanner_health
    _MTF_5M_SCHEDULE = "Every 5min Monitor (09:35 - 15:25 IST)"

    try:
        real_run_ctx = start_scanner_execution_run(scanner_name="MULTI_TF_5M", trigger_type=trigger_type, scheduler_name="CRON")
    except Exception as exc:
        if "actively running" in str(exc).lower():
            logger.info("🛑 [MULTI_TF_5M] Scanner is ALREADY actively running. Skipping duplicate execution.")
            return 0
        real_run_ctx = None

    active_candidates = get_active_armed_candidates()
    if not active_candidates:
        logger.debug("[MULTI_TF_5M] No active armed candidates to monitor.")
        upsert_scanner_health(
            scanner_name="MULTI_TF_5M",
            status="OK",
            outcome="SUCCESS",
            processed_count=0,
            total_count=0,
            duration_seconds=0.05,
            scheduled_for=_MTF_5M_SCHEDULE
        )
        if real_run_ctx:
            try:
                real_run_ctx.set_total_stocks(0)
                complete_scanner_execution_run(real_run_ctx, status_override="COMPLETED", stop_reason="No armed candidates to monitor")
            except Exception:
                pass
        return 0

    if not _scan_lock.acquire(blocking=False):
        logger.debug("[MULTI_TF_5M] Scanner lock busy. Skipping 5m monitor cycle.")
        upsert_scanner_health(
            scanner_name="MULTI_TF_5M",
            status="OK",
            outcome="SKIPPED",
            processed_count=0,
            total_count=len(active_candidates),
            duration_seconds=0.05,
            scheduled_for=_MTF_5M_SCHEDULE,
            error_msg="Scanner lock busy (15m cycle running)"
        )
        if real_run_ctx:
            try:
                real_run_ctx.set_total_stocks(len(active_candidates))
                complete_scanner_execution_run(real_run_ctx, status_override="SKIPPED_DUPLICATE", stop_reason="Scanner lock busy (15m cycle running)")
            except Exception:
                pass
        return 0

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
            duration_seconds=duration,
            scheduled_for=_MTF_5M_SCHEDULE
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
            duration_seconds=duration,
            scheduled_for=_MTF_5M_SCHEDULE
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
    config: Dict[str, Any],
    precomputed_consolidation=None
):
    # 1. Load Data
    bundle = load_multitf_data(symbol, ist_now, all_1h, all_30m, all_15m, all_5m, all_1d)
    if not bundle.data_sufficient:
        return

    # Extract indicators
    atr_15m = _get_atr(bundle.df_15m_closed)
    atr_5m = _get_atr(bundle.df_5m_closed)
    if atr_15m <= 0 or atr_5m <= 0:
        return

    current_price = float(bundle.df_5m_closed["Close"].iloc[-1])

    # 2. Setup Detection (15m strictly closed)
    # [FIX: CONSOLIDATION_MAP_REUSE_v1.0] Use pre-computed result from pre-screen if available.
    # Armed-only symbols (pulled from DB) may not have a pre-computed consolidation.
    # For those, re-detect normally. If still invalid, exit — the DB record stays as-is.
    if precomputed_consolidation is not None and precomputed_consolidation.is_valid:
        consolidation = precomputed_consolidation
    else:
        consolidation = detect_15m_consolidation(bundle.df_15m_closed, atr_15m, ist_now, config)
    if not consolidation.is_valid:
        return

    # 3. State Management & Stable Box Lineage
    active_record = find_active_box_for_symbol(symbol, consolidation.box_high, atr_15m)
    if active_record:
        # Re-use the existing box_id so the structure evolves without duplicate records
        consolidation.box_id = active_record.box_id
        state_record = active_record
        is_new = False
    else:
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

    # [FIX: EARLY_EXIT_STAMP_v1.0]
    # Helper: builds the live-data dict so every early exit also refreshes box/score columns.
    def _live_data_updates():
        prov_1h  = bundle.prov_1h.to_dict()  if bundle.prov_1h  else {}
        prov_30m = bundle.prov_30m.to_dict() if bundle.prov_30m else {}
        prov_15m = bundle.prov_15m.to_dict() if bundle.prov_15m else {}
        prov_5m  = bundle.prov_5m.to_dict()  if bundle.prov_5m  else {}
        return {
            "box_high": consolidation.box_high, "box_low": consolidation.box_low,
            "box_mid": consolidation.box_mid, "box_value_center": consolidation.box_value_center,
            "hard_high": consolidation.hard_high, "hard_low": consolidation.hard_low,
            "box_width_pct": consolidation.box_width_pct, "box_width_atr": consolidation.box_width_atr,
            "box_occupancy": consolidation.box_occupancy,
            "consolidation_bars": consolidation.bars_count,
            "consolidation_sessions": consolidation.sessions_count,
            "consolidation_end_ts": consolidation.end_ts,
            "resistance_test_count": consolidation.resistance_test_count,
            "higher_low_score": consolidation.score_hl,
            "compression_score": consolidation.score_compression,
            "setup_score": consolidation.setup_score,
            "last_confirmed_pivot_level": consolidation.last_confirmed_pivot_level,
            "last_confirmed_pivot_ts": consolidation.last_confirmed_pivot_ts,
            "live_position_5m": round(current_price, 4) if current_price else None,
            "distance_to_box_high": round(consolidation.box_high - current_price, 4) if current_price else None,
            "data_source_1h": prov_1h.get("source", ""), "data_source_30m": prov_30m.get("source", ""),
            "data_source_15m": prov_15m.get("source", ""), "data_source_5m": prov_5m.get("source", ""),
            "candle_ts_1h": prov_1h.get("last_candle_ts"), "candle_ts_30m": prov_30m.get("last_candle_ts"),
            "candle_ts_15m": prov_15m.get("last_candle_ts"), "candle_ts_5m": prov_5m.get("last_candle_ts"),
        }

    # If already fully handled or invalid — still stamp last_evaluated_at so UI shows current time
    if state_record.mtf_substate in (MtfSubstate.INVALIDATED, MtfSubstate.BREAKOUT_CONFIRMED):
        update_state_in_db(state_record, _live_data_updates())
        return

    # Check invalidation logic
    if handle_box_invalidation(state_record, current_price, consolidation.box_low, atr_15m, ist_now):
        if not update_state_in_db(state_record, _live_data_updates()):
            return
        return

    # TTL checks
    current_5m_bars_count = len(bundle.df_5m_closed)
    if apply_ttl_and_cooldown(state_record, ist_now, current_5m_bars_count):
        if not update_state_in_db(state_record, _live_data_updates()):
            return

    if state_record.mtf_substate == MtfSubstate.FAILED_ATTEMPT:
        # Still stamp last_evaluated_at even while cooling down
        update_state_in_db(state_record, _live_data_updates())
        return

    # 5. Pressure / Expansion (5m Live + Closed)
    daily_atr_val = _get_atr(bundle.df_1d)
    pressure = evaluate_5m_pressure(
        live_candle=bundle.live_5m,
        df_5m_closed=bundle.df_5m_closed,
        box_high=consolidation.box_high,
        atr_5m=atr_5m,
        ist_now=ist_now,
        config=config,
        daily_atr=daily_atr_val
    )

    updates = {}

    # Check 14:15 IST trigger generation cutoff
    cutoff_str = config.get("ENTRY_CUTOFF_TIME", "14:15")
    try:
        cutoff_time = datetime.strptime(cutoff_str, "%H:%M").time()
        is_past_cutoff = (ist_now.time() >= cutoff_time)
    except Exception:
        is_past_cutoff = False

    if pressure.is_confirmed:
        if state_record.mtf_substate == MtfSubstate.BREAKOUT_CONFIRMED:
            # [RULE: MODEL B IS TRADE EVOLUTION, NOT A DUPLICATE TRADE]
            if pressure.trigger_model == "MODEL_B_RETEST":
                logger.info(f"🛡️ [{symbol}] Breakout Retest Defended @ ₹{current_price:.2f} — Trade Evolution recorded.")
                updates["last_retest_ts"] = ist_now
        elif not is_past_cutoff:
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
                # ── HARD BREAKOUT VALIDITY GATE (Mandatory before strength & trade execution) ──
                # Gate checks:
                # 1. 15m base is armed and valid (setup_score >= 70, box_width_atr <= 1.50)
                # 2. 5m candle is strictly closed
                # 3. 5m Close >= box_high + buffer
                # 4. Volume confirmation: RVOL >= 1.25x (or Model B retest)
                # 5. Anti-overextension dual cap passed
                c_5m = float(bundle.df_5m_closed["Close"].iloc[-1])
                buffer_atr = config.get("BREAKOUT_BUFFER_ATR_MULT", 0.10) * (atr_5m if atr_5m > 0 else 1.0)
                res_line = consolidation.box_high

                is_hard_breakout = (
                    consolidation.is_valid
                    and consolidation.setup_score >= config.get("MIN_SETUP_SCORE", 70)
                    and c_5m >= (res_line + buffer_atr)
                    and not pressure.is_overextended
                    and (pressure.volume_ratio >= config.get("MIN_VOLUME_EXPANSION_CONFIRM", 1.25) or pressure.trigger_model == "MODEL_B_RETEST")
                )

                if not is_hard_breakout:
                    logger.debug("[%s] Candidate rejected by Hard Breakout Gate (close=%.2f, res=%.2f, rvol=%.2f, ext=%s)",
                                 symbol, c_5m, res_line, pressure.volume_ratio, pressure.is_overextended)
                    return

                # 7. R:R Target Generation & Pre-Validation Gate
                sl_target = compute_sl_and_target(
                    entry_price=c_5m,
                    atr=atr_5m,
                    ticker=bundle.df_1h,  # Pass 1H for structural targets
                    mode="MULTI_TF_V2",
                    box_low=consolidation.box_low
                )

                # Strict Tradeability Pre-Check: Do NOT save to alerts if rejected by R:R!
                if sl_target.get("is_rejected"):
                    logger.info("[%s] Breakout rejected by R:R gate (%.2f < %.2f). NOT SAVING TO ALERTS.",
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
                    return

                # 8. [V3] Breakout Strength Engine (evaluated only for verified breakouts passing Hard Gate)
                nifty_5m = bundle.__dict__.get("df_nifty_5m", None)
                brkout_strength = compute_breakout_strength(
                    pressure_result=pressure,
                    consolidation_result=consolidation,
                    df_5m_closed=bundle.df_5m_closed,
                    nifty_5m=nifty_5m,
                    ist_now=ist_now,
                    config=config
                )

                # 9. Alert Severity Classification (Base Quality × Breakout Strength Matrix)
                market_status = str(market_ctx.get("status", "NORMAL"))
                severity = classify_alert_severity(
                    consolidation_result=consolidation,
                    breakout_result=brkout_strength,
                    config=config,
                    market_status=market_status
                )

                if severity == "WEAK":
                    logger.info("[%s] Breakout confirmed but classified WEAK (base=%d, brk=%d). Logging to near miss only.",
                                symbol, consolidation.setup_score, brkout_strength.breakout_score)
                    return

                # 10. Canonical Alert Registration for High-Conviction Tradeable Signals
                idempotency_signals = f"BOX_ID={consolidation.box_id}"

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
                    score=int(consolidation.setup_score),
                    volume_ratio=pressure.volume_ratio,
                    context={
                        "box_id": consolidation.box_id,
                        "signal_status": "CONFIRMED",
                        "trigger_model": pressure.trigger_model,
                        "tradeability_status": "TRADEABLE",
                        "tradeability_reason": "",
                        "rr_ratio": sl_target.get("rr_ratio"),
                        # [V3] Base Quality
                        "base_score": consolidation.setup_score,
                        "base_rating": consolidation.base_rating_label,
                        "has_higher_lows": consolidation.has_higher_lows,
                        "compression_ratio": consolidation.compression_ratio,
                        "resistance_tests": consolidation.resistance_test_count,
                        "supply_absorption": consolidation.supply_absorption_label,
                        "base_score_breakdown": {
                            "maturity": consolidation.score_maturity,
                            "tightness": consolidation.score_tightness,
                            "resistance_quality": consolidation.score_resistance_quality,
                            "repeated_tests": consolidation.score_repeated_tests,
                            "compression": consolidation.score_compression,
                            "higher_lows": consolidation.score_higher_lows,
                            "support_integrity": consolidation.score_support_integrity,
                        },
                        # [V3] Breakout Strength
                        "breakout_score": brkout_strength.breakout_score,
                        "breakout_rating": brkout_strength.breakout_rating_label,
                        "breakout_energy": brkout_strength.breakout_energy,
                        "breakout_energy_label": brkout_strength.breakout_energy_label,
                        "severity": severity,
                        "severity_label": SEVERITY_LABEL.get(severity, severity),
                        "rvol": round(pressure.volume_ratio, 2),
                        "rvol_label": brkout_strength.rvol_label,
                        "volume_acceleration": brkout_strength.volume_acceleration,
                        "base_relative_volume": brkout_strength.base_relative_volume,
                        "velocity_label": brkout_strength.velocity_label,
                        "penetration_atr": brkout_strength.penetration_atr,
                        "close_position": brkout_strength.close_position,
                        "breakout_score_breakdown": brkout_strength.to_dict().get("score_breakdown"),
                    }
                )

                # 11. Dispatch to OpportunityManager
                if inserted:
                    rich_message = build_multitf_alert_message(
                        symbol=symbol,
                        exchange="NSE",
                        consolidation=consolidation,
                        pressure=pressure,
                        breakout_strength=brkout_strength,
                        severity=severity,
                        sl_levels={
                            "entry": float(sl_target.get("entry_price") or 0),
                            "stop":  float(sl_target.get("stop_loss") or 0),
                            "t1":    float(sl_target.get("target_1") or 0),
                            "t2":    float(sl_target.get("target_2") or 0),
                            "t3":    float(sl_target.get("target_3") or 0),
                            "rr_ratio": float(sl_target.get("rr_ratio") or 0),
                            "extension_daily_atr": float(sl_target.get("extension_daily_atr") or 0),
                        },
                        ist_now=ist_now
                    )
                    logger.info("[%s] %s Alert (base=%d, brk=%d):\n%s",
                                symbol, SEVERITY_EMOJI.get(severity, "🟢"),
                                consolidation.setup_score, brkout_strength.breakout_score, rich_message)

                    payload = build_confirmed_payload(
                        bundle=bundle,
                        consolidation=consolidation,
                        pressure=pressure,
                        confluence=None,
                        sl_target=sl_target,
                        ist_now=ist_now,
                        alert_message=rich_message,
                        severity=severity,
                        breakout_strength=brkout_strength
                    )
                    opp_manager.add(payload)
                else:
                    logger.debug("[%s] Alert already processed for box %s, skipping OpportunityManager.", symbol, consolidation.box_id)

                state_record.mtf_substate = MtfSubstate.BREAKOUT_CONFIRMED
                state_record.state = "CONFIRMED"
                updates["last_confirmation_ts"] = ist_now
        else:
            logger.debug(f"⏳ [{symbol}] Entry cutoff ({cutoff_str} IST) reached — skipping new trade initiation.")

    elif pressure.is_attempt and state_record.mtf_substate == MtfSubstate.WATCHING:
        # Switch to ATTEMPT state (Informational BREAKOUT_APPROACHING on watchlist, not a trade order)
        state_record.mtf_substate = MtfSubstate.ATTEMPT
        state_record.state = "CANDIDATE"
        state_record.attempt_count += 1

        updates["attempt_started_ts"] = ist_now
        updates["last_attempt_ts"] = ist_now
        updates["attempt_bar_boundary"] = pressure.attempt_bar_boundary

    # 10. Sync all live evaluation data to DB on every cycle
    # [FIX: LIVE_DATA_ALWAYS_REFRESH_v1.0]
    # Previously only state-transition fields were written. Box geometry, scores,
    # pressure metrics, context scores, candle timestamps were frozen at first insert.
    # Now every 15m cycle refreshes ALL columns so the UI always shows current data.
    prov_1h  = bundle.prov_1h.to_dict()  if bundle.prov_1h  else {}
    prov_30m = bundle.prov_30m.to_dict() if bundle.prov_30m else {}
    prov_15m = bundle.prov_15m.to_dict() if bundle.prov_15m else {}
    prov_5m  = bundle.prov_5m.to_dict()  if bundle.prov_5m  else {}

    updates.update({
        # Box geometry (refreshed each 15m — box can evolve as new bars close)
        "box_high":               consolidation.box_high,
        "box_low":                consolidation.box_low,
        "box_mid":                consolidation.box_mid,
        "box_value_center":       consolidation.box_value_center,
        "hard_high":              consolidation.hard_high,
        "hard_low":               consolidation.hard_low,
        "box_width_pct":          consolidation.box_width_pct,
        "box_width_atr":          consolidation.box_width_atr,
        "box_occupancy":          consolidation.box_occupancy,
        "consolidation_bars":     consolidation.bars_count,
        "consolidation_sessions": consolidation.sessions_count,
        "consolidation_end_ts":   consolidation.end_ts,
        # Base quality scores (recomputed each scan)
        "resistance_test_count":      consolidation.resistance_test_count,
        "higher_low_score":           consolidation.score_hl,
        "compression_score":          consolidation.score_compression,
        "setup_score":                consolidation.setup_score,
        "last_confirmed_pivot_level": consolidation.last_confirmed_pivot_level,
        "last_confirmed_pivot_ts":    consolidation.last_confirmed_pivot_ts,
        # Pressure metrics (live 5m state)
        "pressure_state":       pressure.label if hasattr(pressure, "label") else None,
        "volume_ratio_5m":      round(pressure.volume_ratio, 4) if pressure.volume_ratio else None,
        "range_ratio_5m":       round(pressure.range_ratio, 4) if hasattr(pressure, "range_ratio") and pressure.range_ratio else None,
        "distance_to_box_high": round(consolidation.box_high - current_price, 4) if current_price else None,
        "live_position_5m":     round(current_price, 4) if current_price else None,
        # Multi-TF context scores
        "context_1h_score":  ctx_1h.get("score",  0),
        "context_30m_score": ctx_30m.get("score", 0),
        "market_regime":     market_ctx.get("regime", "UNKNOWN"),
        # Data freshness provenance
        "data_source_1h":  prov_1h.get("source",  ""),
        "data_source_30m": prov_30m.get("source", ""),
        "data_source_15m": prov_15m.get("source", ""),
        "data_source_5m":  prov_5m.get("source",  ""),
        "candle_ts_1h":    prov_1h.get("last_candle_ts"),
        "candle_ts_30m":   prov_30m.get("last_candle_ts"),
        "candle_ts_15m":   prov_15m.get("last_candle_ts"),
        "candle_ts_5m":    prov_5m.get("last_candle_ts"),
    })

    update_state_in_db(state_record, updates)

