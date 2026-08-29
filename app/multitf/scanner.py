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
from typing import Dict, Any

from config import MULTI_TF_V2_CONFIG
from database import get_elite_watchlist, save_alert_if_new
from opportunity_manager import OpportunityManager
from lock_utils import ProcessLock
from database import upsert_scanner_health
from data_layer import fetch_watchlist_data

from multitf.data import load_multitf_data
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
    MtfSubstate
)
from multitf.candidate import build_watchlist_candidate, build_confirmed_payload

from sl_target_helper import compute_sl_and_target

logger = logging.getLogger("multitf.scanner")
_scan_lock = ProcessLock("MULTI_TF_V2")


def run_multitf_v2(regime_ctx: Dict[str, Any], ist_now: datetime, run_ctx: str = "SCHEDULED"):
    """
    Main entry point for MULTI_TF V2.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("[MULTI_TF_V2] Scanner is already running. Skipping cycle.")
        return

    start_time = time.monotonic()
    
    try:
        upsert_scanner_health(
            scanner_name="MULTI_TF_V2",
            status="RUNNING",
            error_msg="Scan execution in progress..."
        )
        
        watchlist = get_elite_watchlist()
        if not watchlist:
            logger.warning("[MULTI_TF_V2] Watchlist empty.")
            upsert_scanner_health(
                scanner_name="MULTI_TF_V2",
                status="OK",
                outcome="SUCCESS",
                processed_count=0,
                duration_seconds=round(time.monotonic() - start_time, 2)
            )
            return

        logger.info("[MULTI_TF_V2] Pre-fetching data for %d symbols...", len(watchlist))
        
        # Parallel fetch for all required timeframes
        # We reuse the robust fetch_watchlist_data wrapper from data_layer
        all_1d  = fetch_watchlist_data(watchlist, period="1y", interval="1d", requester="MULTI_TF_V2")
        all_1h  = fetch_watchlist_data(watchlist, period="45d", interval="1h", requester="MULTI_TF_V2")
        all_30m = fetch_watchlist_data(watchlist, period="20d", interval="30m", requester="MULTI_TF_V2")
        all_15m = fetch_watchlist_data(watchlist, period="15d", interval="15m", requester="MULTI_TF_V2")
        all_5m  = fetch_watchlist_data(watchlist, period="5d",  interval="5m",  requester="MULTI_TF_V2")

        opp_manager = OpportunityManager()

        for symbol in watchlist:
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
                logger.error("[MULTI_TF_V2] Failed processing %s: %s", symbol, loop_exc)

        # Process all accumulated opportunities
        try:
            opp_manager.process()
        except Exception as e:
            logger.error("[MULTI_TF_V2] OpportunityManager failed to process: %s", e)

        duration = round(time.monotonic() - start_time, 2)
        upsert_scanner_health(
            scanner_name="MULTI_TF_V2",
            status="OK",
            outcome="SUCCESS",
            processed_count=len(watchlist),
            total_count=len(watchlist),
            duration_seconds=duration
        )

    except Exception as exc:
        duration = round(time.monotonic() - start_time, 2)
        logger.error("[MULTI_TF_V2] Fatal error: %s", exc)
        upsert_scanner_health(
            scanner_name="MULTI_TF_V2",
            status="DOWN",
            outcome="FAILED",
            error_msg=str(exc),
            duration_seconds=duration
        )
    finally:
        _scan_lock.release()

    except Exception as exc:
        logger.error("[MULTI_TF] Fatal scanner error: %s", exc)
        _scan_lock.release_failure(str(exc))


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
                breakout_type="MULTI_TF_V2",
                alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S"),
                scanner="MULTI_TF_V2",
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
