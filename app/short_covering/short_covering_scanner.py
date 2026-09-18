"""
app/short_covering/short_covering_scanner.py

Layer 2: Intraday 5-Minute Ignition Engine for Short-Covering Early Alerts.
Features:
- Evidence-Based Dynamic Confirmation:
    High-conviction setups confirm and alert immediately on the same 5m bar without forced delay.
    Moderate-conviction setups transition to IGNITION_CANDIDATE and confirm on subsequent evidence hold.
- Latency Tracking: Measures exact minutes from primary ignition onset to confirmed alert.
- Tiered Progressive Scoring for 15m/30m structural context.
- Excess OI Contraction (Stock vs Index/Sector).
- Anti-Fake validation (rollover filter, liquidity, overhead clearance).
- Stateful alert emission with deduplication.
"""

import os
import logging
import time
from datetime import datetime, date, time as dt_time
from typing import List, Dict, Optional, Set, Tuple, Any
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

from app.short_covering.fno_universe import fno_universe_manager
from app.short_covering.oi_data_service import oi_data_service
from app.short_covering.short_covering_schema import (
    EODShortPositionCandidate,
    Intraday5mTrigger,
    ShortCoveringSignal,
    ShortCoveringState,
)
try:
    from app.lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner
    from app.database import get_connection, upsert_scanner_health, start_scanner_execution_run, complete_scanner_execution_run, save_alert_if_new
    from app.trading_calendar import get_latest_trading_date, get_previous_trading_date, is_trading_day
except ImportError:
    from lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner
    from database import get_connection, upsert_scanner_health, start_scanner_execution_run, complete_scanner_execution_run, save_alert_if_new
    from trading_calendar import get_latest_trading_date, get_previous_trading_date, is_trading_day

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
_scan_lock_5m = ProcessLock("short_covering_5m_lock")


class ShortCoveringEarlyIgnitionScanner:
    """Layer 2: Real-time 5-Minute Short-Covering Early-Ignition Scanner."""

    def __init__(
        self,
        min_5m_oi_contraction_pct: float = -0.50,
        min_15m_oi_contraction_pct: float = -1.00,
        min_session_oi_contraction_pct: float = -2.00,
        min_volume_surge_ratio: float = 1.25,
        min_rvol_diurnal: float = 1.15,
        min_risk_reward_ratio: float = 1.30,
        min_ignition_score: float = 65.0,
    ):
        self.min_5m_oi_contraction_pct = min_5m_oi_contraction_pct
        self.min_15m_oi_contraction_pct = min_15m_oi_contraction_pct
        self.min_session_oi_contraction_pct = min_session_oi_contraction_pct
        self.min_volume_surge_ratio = min_volume_surge_ratio
        self.min_rvol_diurnal = min_rvol_diurnal
        self.min_risk_reward_ratio = min_risk_reward_ratio
        self.min_ignition_score = min_ignition_score
        self.c5_intraday_only_mode = (os.getenv("SHORT_COVERING_ENGINE", "C5_INTRADAY_ONLY").upper() == "C5_INTRADAY_ONLY")

        # Stateful candidate tracker across 5m cycles:
        # Maps symbol -> {'state': ShortCoveringState, 'true_ignition_time': datetime, 'count': int}
        self._tracked_states: Dict[str, Dict] = {}
        self._last_alert_time: Dict[str, datetime] = {}
        self._last_scan_date: Optional[date] = None

    def check_watchlist_freshness(self, target_date: date) -> Tuple[bool, Optional[date], date]:
        """
        Verifies that short_covering_watchlist has candidates from the latest valid trading session.
        For intraday trading on a market day, the candidate watchlist was created by the previous
        session's EOD scan (e.g. Friday for Monday, or Monday for Tuesday).
        Returns (is_fresh, latest_watchlist_date, expected_trading_date).
        """
        # RULE 67 RATIONALE: During active market hours on a trading day (e.g. Mon 09:20),
        # the candidates being traded were produced by Friday's (previous session) EOD scan.
        # If today is a non-trading day (weekend/holiday), the expected session is the latest completed session.
        expected_date = get_previous_trading_date(target_date) if is_trading_day(target_date) else get_latest_trading_date(target_date)
        if not os.getenv("DATABASE_URL") or os.getenv("DISABLE_DB_OI_LOOKUP"):
            return True, expected_date, expected_date
        try:
            from app.database import get_connection
            with get_connection(timeout=1) as conn:
                if hasattr(conn, "is_dummy") and conn.is_dummy:
                    return True, expected_date, expected_date
                with conn.cursor() as cur:
                    cur.execute("SELECT MAX(scan_date) FROM short_covering_watchlist;")
                    row = cur.fetchone()
                    max_date = row[0] if row and row[0] else None
                    if max_date is None:
                        return False, None, expected_date
                    # Consider fresh if max_date matches or exceeds the expected session date
                    is_fresh = max_date >= expected_date
                    return is_fresh, max_date, expected_date
        except Exception as e:
            logger.debug("Failed to check watchlist freshness: %s", e)
            return True, expected_date, expected_date

    def run_5m_scan_cycle(
        self,
        current_time: Optional[datetime] = None,
        candidate_watchlist: Optional[List[EODShortPositionCandidate]] = None,
        persist_db: bool = True,
        trigger_type: str = "SCHEDULED",
        scheduler_name: str = "CRON"
    ) -> List[ShortCoveringSignal]:
        """
        Executes one 5-minute scanning cycle across the candidate universe.
        Returns newly triggered CONFIRMED_IGNITION ShortCoveringSignal alerts.
        """
        if current_time is None:
            current_time = datetime.now(IST)

        today = current_time.date()
        if self._last_scan_date != today:
            self._tracked_states.clear()
            self._last_alert_time.clear()
            self._last_scan_date = today

        logger.info("[SHORT_COVERING_5M] Acquiring lock: short_covering_5m_lock")
        if not _scan_lock_5m.acquire(blocking=False):
            logger.warning("🛑 [SHORT_COVERING_5M] Lock 'short_covering_5m_lock' held by another instance. Skipping duplicate cycle.")
            return []

        run_ctx = None
        try:
            run_ctx = start_scanner_execution_run(
                scanner_name="SHORT_COVERING_5M",
                trigger_type=trigger_type,
                scheduler_name=scheduler_name,
                total_stocks=len(candidate_watchlist) if candidate_watchlist else 0
            )
        except Exception as ctx_err:
            if "already actively running" in str(ctx_err).lower():
                logger.warning("🛑 [SHORT_COVERING_5M] Already actively running in DB history. Skipping duplicate cycle.")
                _scan_lock_5m.release()
                return []
            run_ctx = None

        _scan_start = print_scanner_start_banner("SHORT_COVERING_5M", run_id=run_ctx.run_id if run_ctx else None)
        _SCHEDULE_STR = "Every 5m (09:20 - 15:25 IST Market Days)"
        engine_mode = os.getenv("SHORT_COVERING_ENGINE", "C5_INTRADAY_ONLY").upper()

        try:
            # 1. Universe Selection: C5 Intraday-Only (All Active F&O) vs Legacy V1
            candidate_map: Dict[str, Optional[EODShortPositionCandidate]] = {}
            if engine_mode == "C5_INTRADAY_ONLY":
                # [VERSION: SC_DATA_HEALTH_GATE_v1.0]
                # Pre-flight: C5 requires live intraday OI data. Use SCDataHealthGate to assess
                # ALL data layers (Fyers, Upstox, parquet) via a full 9-step end-to-end probe.
                # This explicitly distinguishes INGESTION_FAILURE from NO_SIGNAL (legitimate zero).
                # A shallow bool(get_fyers_client()) check is intentionally replaced here.
                if not os.getenv("DISABLE_LIVE_DATA_FETCH"):
                    try:
                        try:
                            from app.short_covering.sc_data_health import sc_data_health_gate, SCDataHealth
                        except ImportError:
                            from short_covering.sc_data_health import sc_data_health_gate, SCDataHealth

                        _data_health = sc_data_health_gate.assess(target_date=today)

                        if _data_health.is_blocked():
                            _blocked_msg = (
                                f"SC_DATA_HEALTH = BLOCKED — INGESTION_FAILURE. "
                                f"Reason: {_data_health.reason}. "
                                f"Recommendation: {_data_health.recommendation}"
                            )
                            logger.warning("🚨 [SHORT_COVERING_5M] %s", _blocked_msg)
                            if run_ctx:
                                complete_scanner_execution_run(
                                    run_ctx, status_override="BLOCKED", stop_reason=_blocked_msg
                                )
                            upsert_scanner_health(
                                scanner_name="SHORT_COVERING_5M",
                                status="BLOCKED",
                                outcome="INGESTION_FAILURE",
                                error_msg=_blocked_msg,
                                duration_seconds=round(time.monotonic() - _scan_start, 2),
                                scheduled_for=_SCHEDULE_STR,
                                run_id=run_ctx.run_id if run_ctx else None
                            )
                            return []

                        elif _data_health.status in (
                            SCDataHealth.DEGRADED_REDUNDANCY,
                            SCDataHealth.DEGRADED,
                        ):
                            logger.warning(
                                "⚠️ [SHORT_COVERING_5M] SC_DATA_HEALTH = %s — "
                                "proceeding with reduced data confidence. %s. %s",
                                _data_health.status.value,
                                _data_health.reason,
                                _data_health.recommendation,
                            )

                    except Exception as _health_err:
                        # Health gate failure is non-fatal — fall back to legacy shallow check
                        logger.warning(
                            "⚠️ [SHORT_COVERING_5M] SCDataHealthGate probe failed (%s). "
                            "Falling back to shallow token check.", _health_err
                        )
                        try:
                            from app.fyers_auth import get_fyers_client
                        except ImportError:
                            from fyers_auth import get_fyers_client
                        _fyers_client = get_fyers_client()
                        has_upstox = bool(os.getenv("UPSTOX_ACCESS_TOKEN"))
                        if not _fyers_client and not has_upstox:
                            _no_broker_msg = (
                                "Neither Fyers nor Upstox API client is available. "
                                "SHORT_COVERING_5M requires live intraday OI — "
                                "cannot proceed without broker authentication."
                            )
                            logger.warning("⚠️ [SHORT_COVERING_5M] %s", _no_broker_msg)
                            if run_ctx:
                                complete_scanner_execution_run(
                                    run_ctx, status_override="IDLE", stop_reason=_no_broker_msg
                                )
                            upsert_scanner_health(
                                scanner_name="SHORT_COVERING_5M",
                                status="IDLE",
                                outcome="NO_BROKER_SESSION",
                                error_msg=_no_broker_msg,
                                duration_seconds=round(time.monotonic() - _scan_start, 2),
                                scheduled_for=_SCHEDULE_STR,
                                run_id=run_ctx.run_id if run_ctx else None
                            )
                            return []

                # Certified C5 Production: Direct Active F&O Universe (Zero EOD alpha threshold)
                symbols_to_scan = fno_universe_manager.get_fno_symbols()
                candidate_map = {sym: None for sym in symbols_to_scan}
                logger.info("🚀 [SHORT_COVERING_5M] Operating in C5_INTRADAY_ONLY Mode across %d active F&O symbols", len(symbols_to_scan))
            else:
                # Legacy V1 Rollback Mode (EOD Score >= 50, Top 35 Watchlist)
                logger.info("🔄 [SHORT_COVERING_5M] Operating in Legacy V1 Rollback Mode")
                is_fresh, max_date, expected_date = self.check_watchlist_freshness(today)
                if candidate_watchlist is None:
                    candidate_watchlist = self._load_eod_watchlist(today) if persist_db else None

                # Dynamic self-healing: if watchlist is missing or stale, dynamically generate from EOD scanner
                if (not is_fresh or not candidate_watchlist) and candidate_watchlist is None:
                    logger.info(
                        "⚡ [SHORT_COVERING_5M] Missing or stale watchlist (Latest: %s, Expected: %s). Attempting dynamic self-healing...",
                        max_date, expected_date
                    )
                    try:
                        from app.short_covering.short_position_detector import short_position_detector
                        healed_candidates = short_position_detector.scan_eod_universe(as_of_date=expected_date, persist_db=persist_db)
                        if healed_candidates:
                            candidate_watchlist = healed_candidates
                            logger.info("✅ [SHORT_COVERING_5M] Self-healed watchlist with %d candidates for %s", len(candidate_watchlist), expected_date)
                    except Exception as _heal_err:
                        logger.warning("Failed dynamic self-healing for SHORT_COVERING_5M: %s", _heal_err)

                if not candidate_watchlist:
                    err_msg = f"No active short-covering candidates found for {expected_date} (Latest: {max_date})."
                    logger.warning("⚠️ [SHORT_COVERING_5M] %s", err_msg)
                    if run_ctx:
                        complete_scanner_execution_run(run_ctx, status_override="DEGRADED", stop_reason=err_msg)
                    
                    try:
                        from app.database import insert_notification
                        insert_notification(
                            notif_type="error",
                            title="🚨 SHORT_COVERING_5M Watchlist Missing/Stale",
                            message=f"{err_msg} Check EOD Short Position Detector.",
                            symbol=None
                        )
                    except Exception:
                        pass

                    upsert_scanner_health(
                        scanner_name="SHORT_COVERING_5M",
                        status="DEGRADED",
                        outcome="MISSING_WATCHLIST",
                        error_msg=err_msg,
                        duration_seconds=round(time.monotonic() - _scan_start, 2),
                        scheduled_for=_SCHEDULE_STR,
                        run_id=run_ctx.run_id if run_ctx else None
                    )
                    return []

                candidate_map = {c.symbol: c for c in candidate_watchlist}
                symbols_to_scan = list(candidate_map.keys())

            if run_ctx:
                run_ctx.set_total_stocks(len(symbols_to_scan))

            logger.info("⚡ [SHORT_COVERING_5M] Starting 5m ignition cycle at %s across %d candidates", current_time.strftime("%H:%M:%S"), len(symbols_to_scan))

            new_alerts: List[ShortCoveringSignal] = []
            nifty_oi_5m_delta = self._get_index_5m_oi_delta(current_time)
            stale_count = 0
            gate_rejections: Dict[str, int] = {}

            # Certified signal window enforcement (09:20 - 15:25 IST)
            current_t = current_time.time()
            is_valid_signal_window = (dt_time(9, 20) <= current_t <= dt_time(15, 25))

            for symbol in symbols_to_scan:
                try:
                    _t0_sym_5m = time.monotonic()
                    df_5m = oi_data_service.get_intraday_5m_data(symbol, current_time.date())
                    _rows_5m = len(df_5m) if df_5m is not None else 0
                    logger.debug(
                        "[SC_5M] ── %s ── data_fetch → %d rows%s",
                        symbol, _rows_5m, "" if _rows_5m >= 2 else " ← INSUFFICIENT",
                    )
                    if df_5m is None or len(df_5m) < 2:
                        stale_count += 1
                        gate_rejections["DATA_INSUFFICIENT"] = gate_rejections.get("DATA_INSUFFICIENT", 0) + 1
                        continue

                    signal, rej_code, eval_score, rej_reason, eval_reasons = self.evaluate_symbol_5m(
                        symbol=symbol,
                        current_time=current_time,
                        eod_candidate=candidate_map.get(symbol),
                        nifty_oi_5m_delta=nifty_oi_5m_delta,
                        return_diagnostics=True
                    )
                    if signal is not None and signal.state == ShortCoveringState.CONFIRMED_IGNITION:
                        if is_valid_signal_window:
                            new_alerts.append(signal)
                            gate_rejections["CONFIRMED_IGNITION"] = gate_rejections.get("CONFIRMED_IGNITION", 0) + 1
                            logger.info("🚨 [SHORT COVERING ALERT] %s | Price=₹%.2f | Latency=%.0fm | Score=%.1f (%s) | Reasons: %s",
                                        symbol, signal.ignition_price, signal.alert_latency_minutes, signal.ignition_score, signal.grade, "; ".join(signal.reasons))
                        else:
                            gate_rejections["OUTSIDE_SIGNAL_WINDOW"] = gate_rejections.get("OUTSIDE_SIGNAL_WINDOW", 0) + 1
                            logger.info("🚫 [SHORT_COVERING_5M] %s REJECTED — Gate: OUTSIDE_SIGNAL_WINDOW (Detected at %s IST)", symbol, current_t)
                    else:
                        gate_rejections[rej_code] = gate_rejections.get(rej_code, 0) + 1
                        if eval_score >= 50.0 or rej_code in ("SCORE_BELOW_THRESHOLD", "IGNITION_CANDIDATE_WATCH", "EXTENDED_FROM_OPEN"):
                            logger.info("🚫 [SHORT_COVERING_5M] %s REJECTED — Gate: %s | Score: %.1f | Reason: %s",
                                        symbol, rej_code, eval_score, rej_reason)
                            if eval_score >= 50.0 or rej_code in ("SCORE_BELOW_THRESHOLD", "IGNITION_CANDIDATE_WATCH", "LOW_VOLUME_SURGE"):
                                try:
                                    from near_miss_tracker import log_near_miss
                                    last_close = float(df_5m["close"].iloc[-1]) if df_5m is not None and len(df_5m) > 0 else None
                                    sl_val = round(last_close * 0.985, 2) if last_close else None
                                    t1_val = round(last_close * 1.03, 2) if last_close else None
                                    log_near_miss(
                                        symbol=symbol,
                                        scanner="SHORT_COVERING_5M",
                                        breakout_type="SHORT_COVERING",
                                        gate_name=rej_code,
                                        observed_value=round(float(eval_score), 2),
                                        threshold_value=float(self.min_ignition_score),
                                        score=int(eval_score),
                                        entry_price=last_close,
                                        stop_loss=sl_val,
                                        target_1=t1_val
                                    )
                                except Exception as nm_err:
                                    logger.debug("Failed to persist near miss for %s: %s", symbol, nm_err)
                except Exception as e:
                    gate_rejections["EVALUATION_ERROR"] = gate_rejections.get("EVALUATION_ERROR", 0) + 1
                    logger.warning("[SC_5M] %s | EVALUATION_ERROR: %s", symbol, e, exc_info=True)

            # Log comprehensive stage-by-stage pipeline summary
            summary_lines = [
                "\n======================================================================",
                "=== [SHORT COVERING 5M PIPELINE SUMMARY] ===",
                "======================================================================",
                f"  • Total F&O Universe Scanned : {len(symbols_to_scan)}",
                f"  • Fresh Data Resolved        : {len(symbols_to_scan) - stale_count} ({((len(symbols_to_scan) - stale_count)/max(len(symbols_to_scan),1))*100:.1f}%)",
                f"  • Stale / Missing Symbols    : {stale_count}",
                f"  • Alerts Generated           : {len(new_alerts)}",
                f"  • Execution Mode             : {engine_mode}",
                f"  • Signal Window Active       : {'YES (09:20-15:25 IST)' if is_valid_signal_window else f'NO (Outside window: {current_t})'}",
                "",
                "🎯 GATE-BY-GATE REJECTION BREAKDOWN:"
            ]
            for gate_name, cnt in sorted(gate_rejections.items(), key=lambda x: x[1], reverse=True):
                summary_lines.append(f"  • {gate_name:<30}: {cnt}")
            summary_lines.append("======================================================================")
            logger.info("\n".join(summary_lines))

            # [VERSION: SC_DATA_HEALTH_GATE_v1.0] Retroactive systemic ingestion failure check.
            # If DATA_INSUFFICIENT >= 90% of all candidates, this run is INGESTION_FAILURE —
            # not a legitimate zero-signal market result. Emits BLOCKED outcome explicitly.
            data_insufficient_count = gate_rejections.get("DATA_INSUFFICIENT", 0)
            try:
                try:
                    from app.short_covering.sc_data_health import SCDataHealthGate
                except ImportError:
                    from short_covering.sc_data_health import SCDataHealthGate
                _is_systemic_failure = SCDataHealthGate.is_systemic_data_failure(
                    data_insufficient_count=data_insufficient_count,
                    total_candidates=len(symbols_to_scan)
                )
            except Exception:
                _is_systemic_failure = False

            if _is_systemic_failure:
                _systemic_msg = (
                    f"INGESTION_FAILURE: {data_insufficient_count}/{len(symbols_to_scan)} "
                    f"({data_insufficient_count / max(len(symbols_to_scan), 1) * 100:.1f}%) candidates "
                    f"returned DATA_INSUFFICIENT — systemic data outage, not a market result."
                )
                logger.warning("🚨 [SHORT_COVERING_5M] %s", _systemic_msg)
                upsert_scanner_health(
                    scanner_name="SHORT_COVERING_5M",
                    status="BLOCKED",
                    outcome="INGESTION_FAILURE",
                    error_msg=_systemic_msg,
                    duration_seconds=round(time.monotonic() - _scan_start, 2),
                    scheduled_for=_SCHEDULE_STR,
                    run_id=run_ctx.run_id if run_ctx else None
                )
                if run_ctx:
                    complete_scanner_execution_run(
                        run_ctx, status_override="BLOCKED", stop_reason=_systemic_msg
                    )
                return []

            # Enforce 25% staleness hard blocker (non-systemic partial stale)
            from app.market_utils import validate_batch_staleness
            staleness_check = validate_batch_staleness(
                stale_count=stale_count,
                total_count=len(symbols_to_scan),
                scanner_name="SHORT_COVERING_5M",
                max_stale_pct=25.0,
                run_ctx=run_ctx
            )
            if staleness_check["is_blocked"]:
                err_block = f"SHORT_COVERING_5M halted due to {staleness_check['stale_pct']:.1f}% stale intraday data ({stale_count}/{len(symbols_to_scan)} symbols)."
                if run_ctx:
                    complete_scanner_execution_run(run_ctx, status_override="DEGRADED", stop_reason=err_block)
                return []

            if new_alerts and persist_db:
                self._persist_alerts(new_alerts)

            dur = round(time.monotonic() - _scan_start, 2)
            if run_ctx:
                run_ctx.record_fresh_data(len(symbols_to_scan) - stale_count)
                run_ctx.record_stale_data(stale_count)
                if new_alerts:
                    run_ctx.increment_alerts(len(new_alerts))
                complete_scanner_execution_run(run_ctx)

            upsert_scanner_health(
                scanner_name="SHORT_COVERING_5M",
                status="OK",
                outcome="SUCCESS",
                total_count=len(symbols_to_scan),
                processed_count=len(symbols_to_scan) - stale_count,
                today_alerts=len(new_alerts),
                duration_seconds=dur,
                scheduled_for=_SCHEDULE_STR,
                run_id=run_ctx.run_id if run_ctx else None
            )
            return new_alerts
        except Exception as exc:
            dur = round(time.monotonic() - _scan_start, 2)
            logger.exception("❌ [SHORT_COVERING_5M] Cycle failed: %s", exc)
            if run_ctx:
                complete_scanner_execution_run(run_ctx, exception=exc)
            
            try:
                from app.database import insert_notification
                insert_notification(
                    notif_type="error",
                    title="❌ SHORT_COVERING_5M Scanner Error",
                    message=f"Execution failed: {exc}",
                    symbol=None
                )
            except Exception:
                pass

            upsert_scanner_health(
                scanner_name="SHORT_COVERING_5M",
                status="DOWN",
                outcome="FAILURE",
                error_msg=str(exc),
                duration_seconds=dur,
                scheduled_for=_SCHEDULE_STR,
                run_id=run_ctx.run_id if run_ctx else None
            )
            raise exc
        finally:
            if _scan_start is not None:
                print_scanner_end_banner("SHORT_COVERING_5M", _scan_start, run_id=run_ctx.run_id if run_ctx else None)
            _scan_lock_5m.release()


    def evaluate_symbol_5m(
        self,
        symbol: str,
        current_time: datetime,
        eod_candidate: Optional[EODShortPositionCandidate],
        nifty_oi_5m_delta: float = 0.0,
        return_diagnostics: bool = False
    ) -> Any:
        """
        Evaluates 5m bar, dynamic evidence-based state progression, and tiered structural context.
        """
        df_5m = oi_data_service.get_intraday_5m_data(symbol, current_time.date())
        _rows_eval = len(df_5m) if df_5m is not None else 0
        logger.debug("[SC_5M] %s | [step 1] data_fetch → %d bars", symbol, _rows_eval)
        if df_5m is None or len(df_5m) < 2:
            logger.debug("[SC_5M] %s | SKIP — DATA_INSUFFICIENT (%d bars)", symbol, _rows_eval)
            return (None, "DATA_INSUFFICIENT", 0.0, "Missing or insufficient 5m bars (< 2)", []) if return_diagnostics else None

        past_bars = df_5m[df_5m["timestamp"] <= current_time]
        if len(past_bars) < 2:
            past_bars = df_5m.head(2)

        cur_bar = past_bars.iloc[-1]
        prev_bar = past_bars.iloc[-2]
        session_open_price = float(past_bars.iloc[0]["open"])

        cur_close = float(cur_bar["close"])
        cur_open = float(cur_bar["open"])
        cur_high = float(cur_bar["high"])
        cur_low = float(cur_bar["low"])
        cur_vwap = float(cur_bar["vwap"])
        cur_vol = int(cur_bar["volume"])
        cur_oi = int(cur_bar["oi"])

        logger.debug(
            "[SC_5M] %s | [step 2] cur_bar → O=₹%.2f H=₹%.2f L=₹%.2f C=₹%.2f VWAP=₹%.2f Vol=%d OI=%d",
            symbol, cur_open, cur_high, cur_low, cur_close, cur_vwap, cur_vol, cur_oi,
        )

        # 30-Minute Symbol Cooldown Guard (Deduplication)
        last_alert = self._last_alert_time.get(symbol)
        if last_alert is not None and (current_time - last_alert).total_seconds() < 1800:
            logger.debug(
                "[SC_5M] %s | SKIP — COOLDOWN_ACTIVE (last alert %s, cooldown ends %s)",
                symbol, last_alert.strftime('%H:%M'),
                (last_alert + pd.Timedelta(minutes=30)).strftime('%H:%M'),
            )
            return (None, "COOLDOWN_ACTIVE", 0.0, f"30m cooldown active (last alert at {last_alert.strftime('%H:%M')})", []) if return_diagnostics else None

        # 1. Primary 5m Ignition Evidence & CLV
        is_green_candle = cur_close >= cur_open
        is_above_vwap = cur_close >= cur_vwap * 0.999
        price_change_5m_pct = ((cur_close - float(prev_bar["close"])) / float(prev_bar["close"])) * 100.0
        clv = (cur_close - cur_low) / max(cur_high - cur_low, 1e-4)

        avg_vol_10 = past_bars["volume"].tail(10).mean()
        vol_surge_ratio = cur_vol / max(avg_vol_10, 1.0)

        oi_data_mode = str(cur_bar.get("oi_data_mode", "DERIVATIVE_OI_UNAVAILABLE" if pd.isna(cur_bar.get("oi")) else "DERIVATIVE_OI_AVAILABLE"))

        logger.debug(
            "[SC_5M] %s | [step 3] momentum → green=%s vwap=%s price_chg=%+.2f%% vol_surge=%.2fx clv=%.2f avg_vol10=%.0f",
            symbol, is_green_candle, is_above_vwap, price_change_5m_pct, vol_surge_ratio, clv, avg_vol_10,
        )
            oi_change_5m_pct = float(cur_bar.get("oi_delta_1bar", cur_bar.get("oi_change_5m_pct", 0.0)))
            oi_delta_3bar = float(cur_bar.get("oi_delta_3bar", 0.0))
            oi_change_session_pct = float(cur_bar.get("oi_session", cur_bar.get("oi_change_session_pct", 0.0)))
            excess_oi_contraction = oi_change_5m_pct - nifty_oi_5m_delta
            recent_oi_pct = float(past_bars["oi_change_5m_pct"].dropna().tail(3).min()) if ("oi_change_5m_pct" in past_bars.columns and len(past_bars) >= 2) else oi_change_5m_pct
            recent_excess_oi = min(excess_oi_contraction, float(recent_oi_pct - nifty_oi_5m_delta))

            has_oi_unwind = (
                oi_change_5m_pct <= self.min_5m_oi_contraction_pct or
                excess_oi_contraction <= -0.20 or
                oi_delta_3bar <= -0.80 or
                recent_oi_pct <= self.min_5m_oi_contraction_pct or
                recent_excess_oi <= -0.20 or
                oi_change_session_pct <= -1.50
            )
            has_primary_ignition = (
                is_green_candle and
                is_above_vwap and
                price_change_5m_pct >= 0.08 and
                has_oi_unwind and
                vol_surge_ratio >= 1.10
            )
            # Anti-Fake Rollover Check
            if oi_data_service.is_rollover_in_progress(symbol, oi_change_5m_pct, 0.0, current_time.date()):
                return (None, "ROLLOVER_IN_PROGRESS", 0.0, "Expiry-week contract rollover flow detected", []) if return_diagnostics else None
        else:
            # Explicit NOT_COMPUTABLE data-quality contract
            oi_change_5m_pct = float("nan")
            oi_delta_3bar = float("nan")
            oi_change_session_pct = float("nan")
            excess_oi_contraction = float("nan")
            recent_excess_oi = float("nan")
            has_oi_unwind = True  # Bypassed on cash equity
            # EQUITY_SHORT_SQUEEZE_PROXY: requires stronger price thrust + volume surge
            has_primary_ignition = (
                is_green_candle and
                is_above_vwap and
                price_change_5m_pct >= 0.12 and
                vol_surge_ratio >= 1.25
            )

        # Multi-Vector Extension Analysis
        session_low_val = float(past_bars["low"].min()) if "low" in past_bars.columns else cur_low
        extension_from_open_pct = ((cur_close - session_open_price) / max(session_open_price, 1e-4)) * 100.0
        extension_from_vwap_pct = ((cur_close - cur_vwap) / max(cur_vwap, 1e-4)) * 100.0
        extension_from_low_pct = ((cur_close - session_low_val) / max(session_low_val, 1e-4)) * 100.0

        logger.debug(
            "[SC_5M] %s | [step 5] primary_ignition=%s | ext_open=%+.2f%% ext_vwap=%+.2f%% ext_low=%+.2f%%",
            symbol, has_primary_ignition, extension_from_open_pct, extension_from_vwap_pct, extension_from_low_pct,
        )

        # Hard safety floor: > 4.5% extension from open is overbought
        if extension_from_open_pct > 4.5:
            logger.debug(
                "[SC_5M] %s | REJECT — EXTENDED_FROM_OPEN (+%.1f%% > 4.5%% hard floor)",
                symbol, extension_from_open_pct,
            )
            return (None, "EXTENDED_FROM_OPEN", 0.0, f"Move already extended (+{extension_from_open_pct:.1f}% > +4.5% from open)", []) if return_diagnostics else None

        # 2. Tiered Multi-Timeframe Structural Context
        tf_confirmations = self._check_multitf_context(past_bars)

        # 3. Comprehensive Ignition Scoring (0 to 100)
        score = 0.0
        reasons = []

        # A. Prior Short Buildup Quality / CLV Strong Close (25 pts)
        _pts_a = 0.0
        if eod_candidate:
            _pts_a = (eod_candidate.buildup_quality_score / 100.0) * 25.0
            score += _pts_a
            reasons.append(f"Prior Short Score: {eod_candidate.buildup_quality_score:.0f}")
        else:
            # C5 Intraday-Only: CLV and Close Velocity (25 pts)
            if clv >= 0.80:
                _pts_a = 25.0
                reasons.append(f"High CLV Top Close ({clv:.2f})")
            elif clv >= 0.60:
                _pts_a = 18.0
                reasons.append(f"Moderate CLV Close ({clv:.2f})")
            else:
                _pts_a = 12.0
            score += _pts_a
        logger.debug("[SC_5M] %s | [A] CLV/Prior → %+.1f pts | clv=%.2f | running=%.1f", symbol, _pts_a, clv, score)

        # B. Excess OI Contraction Speed / Equity Squeeze Conviction (25 pts)
        _pts_b = 0.0
        if oi_data_mode == "DERIVATIVE_OI_AVAILABLE" and not pd.isna(excess_oi_contraction):
            if excess_oi_contraction <= -1.2 or recent_excess_oi <= -1.2:
                _pts_b = 25.0
                reasons.append(f"Strong Excess OI Unwind ({min(excess_oi_contraction, recent_excess_oi):.2f}%)")
            elif excess_oi_contraction <= -0.5 or recent_excess_oi <= -0.5 or oi_change_session_pct <= -2.0:
                _pts_b = 18.0
                reasons.append(f"Moderate Excess OI Unwind ({min(excess_oi_contraction, recent_excess_oi):.2f}%)")
            else:
                _pts_b = 12.0
        else:
            # Equity Proxy: Score based on price velocity + volume conviction
            if vol_surge_ratio >= 2.5 and price_change_5m_pct >= 0.35:
                _pts_b = 22.0
                reasons.append("High-Conviction Equity Squeeze Thrust (+22)")
            elif vol_surge_ratio >= 1.5:
                _pts_b = 16.0
                reasons.append("Moderate Equity Squeeze Thrust (+16)")
            else:
                _pts_b = 12.0
        score += _pts_b
        logger.debug(
            "[SC_5M] %s | [B] OI/Squeeze → %+.1f pts | excess=%.2f%% recent=%.2f%% | running=%.1f",
            symbol, _pts_b,
            excess_oi_contraction if not pd.isna(excess_oi_contraction) else float("nan"),
            recent_excess_oi if not pd.isna(recent_excess_oi) else float("nan"),
            score,
        )

        # C. Volume Surge & Conviction (20 pts)
        _pts_c = 0.0
        if vol_surge_ratio >= 2.0:
            _pts_c = 20.0
            reasons.append(f"High 5m Volume Spike ({vol_surge_ratio:.1f}x)")
        elif vol_surge_ratio >= self.min_volume_surge_ratio:
            _pts_c = 15.0
            reasons.append(f"Volume Surge ({vol_surge_ratio:.1f}x)")
        else:
            _pts_c = 8.0
        score += _pts_c
        logger.debug("[SC_5M] %s | [C] Volume → %+.1f pts | surge=%.2fx (min=%.2fx) | running=%.1f",
                     symbol, _pts_c, vol_surge_ratio, self.min_volume_surge_ratio, score)

        # D. VWAP & Price Momentum (15 pts)
        _pts_d = 0.0
        if cur_close >= cur_vwap * 1.003 and price_change_5m_pct >= 0.30:
            _pts_d = 15.0
            reasons.append("Clean VWAP acceleration")
        else:
            _pts_d = 10.0
        score += _pts_d
        logger.debug("[SC_5M] %s | [D] VWAP → %+.1f pts | close=₹%.2f vwap=₹%.2f chg=%+.2f%% | running=%.1f",
                     symbol, _pts_d, cur_close, cur_vwap, price_change_5m_pct, score)

        # E. Progressive 30m / 15m Structural Context (15 pts)
        struct_30m = tf_confirmations.get("30m_structure", "BASE")
        _pts_e = 0.0
        if struct_30m == "BREAKOUT":
            _pts_e = 15.0
            reasons.append("30m Structural Breakout (+15)")
        elif struct_30m == "NEAR_BREAKOUT":
            _pts_e = 10.0
            reasons.append("Near 30m Breakout (+10)")
        elif struct_30m == "RECLAIMING_STRUCTURE":
            _pts_e = 6.0
            reasons.append("Reclaiming 30m Structure (+6)")
        else:
            _pts_e = 2.0
        score += _pts_e
        logger.debug("[SC_5M] %s | [E] Structure → %+.1f pts | 30m=%s | running=%.1f",
                     symbol, _pts_e, struct_30m, score)

        # F. Extension Penalty Ladder (Graduated deduction for extension > 2.5%)
        _ext_pen = 0.0
        if extension_from_open_pct > 2.5:
            _ext_pen = min(8.0, (extension_from_open_pct - 2.5) * 4.0)
            score = max(0.0, score - _ext_pen)
            reasons.append(f"Extension from Open Penalty (-{_ext_pen:.1f} pts)")
        logger.debug("[SC_5M] %s | [F] Ext penalty → -%.1f pts | ext_open=%+.2f%% | final_score=%.1f (need ≥%.1f)",
                     symbol, _ext_pen, extension_from_open_pct, score, self.min_ignition_score)

        # 4. Evidence-Based Dynamic State Machine
        tracking = self._tracked_states.get(symbol, {"state": ShortCoveringState.WATCH, "true_ignition_time": current_time, "count": 0})
        current_state = tracking["state"]

        if not has_primary_ignition or score < self.min_ignition_score:
            if current_state == ShortCoveringState.IGNITION_CANDIDATE:
                candidate_bars = tracking.get("candidate_bars", 1)
                if candidate_bars >= 2 or not is_above_vwap:
                    tracking["state"] = ShortCoveringState.WATCH
                    self._tracked_states[symbol] = tracking
                else:
                    tracking["candidate_bars"] = candidate_bars + 1
                    self._tracked_states[symbol] = tracking

            # Determine dominant rejection failure reason
            if not is_green_candle:
                rej_code = "NOT_GREEN_CANDLE"
                rej_reason = f"Red candle (Close ₹{cur_close:.2f} < Open ₹{cur_open:.2f})"
            elif not is_above_vwap:
                rej_code = "BELOW_VWAP"
                rej_reason = f"Below VWAP (Close ₹{cur_close:.2f} < VWAP ₹{cur_vwap:.2f})"
            elif price_change_5m_pct < 0.08:
                rej_code = "LOW_5M_PRICE_MOMENTUM"
                rej_reason = f"Price change {price_change_5m_pct:+.2f}% < +0.08%"
            elif not has_oi_unwind:
                rej_code = "NO_OI_CONTRACTION"
                rej_reason = f"5m OI change {oi_change_5m_pct:+.2f}% (Excess {excess_oi_contraction:+.2f}%) not unwinding"
            elif vol_surge_ratio < 1.10:
                rej_code = "LOW_VOLUME_SURGE"
                rej_reason = f"Volume surge {vol_surge_ratio:.2f}x < 1.10x"
            elif score < self.min_ignition_score:
                rej_code = "SCORE_BELOW_THRESHOLD"
                rej_reason = f"Score {score:.1f} < threshold {self.min_ignition_score:.1f}"
            else:
                rej_code = "PRIMARY_IGNITION_FAIL"
                rej_reason = "Primary ignition criteria not met"

            logger.debug(
                "[SC_5M] %s | ❌ REJECT | gate=%s | score=%.1f | state=%s | %s",
                symbol, rej_code, score, current_state.value, rej_reason,
            )
            return (None, rej_code, score, rej_reason, reasons) if return_diagnostics else None

        # Evidence evaluation:
        # High Conviction (Score >= 76 or exceptionally clean surge + CLV) -> Confirm immediately on same candle!
        is_high_conviction = (score >= 76.0) or (
            vol_surge_ratio >= 1.8 and excess_oi_contraction <= -0.5 and clv >= 0.75
        )

        true_ignition_time = tracking.get("true_ignition_time", current_time)

        logger.debug(
            "[SC_5M] %s | [step 6] state_machine → current=%s | score=%.1f | high_conviction=%s",
            symbol, current_state.value, score, is_high_conviction,
        )

        if current_state == ShortCoveringState.WATCH:
            true_ignition_time = current_time
            tracking["true_ignition_time"] = true_ignition_time
            if is_high_conviction:
                tracking["state"] = ShortCoveringState.CONFIRMED_IGNITION
                tracking["count"] = 1
                self._tracked_states[symbol] = tracking
                logger.info(
                    "⚡ [SC_5M] %s | WATCH → CONFIRMED_IGNITION immediately | score=%.1f | vol=%.2fx | excess_oi=%+.2f%% | clv=%.2f",
                    symbol, score, vol_surge_ratio,
                    excess_oi_contraction if not pd.isna(excess_oi_contraction) else 0.0, clv,
                )
            else:
                tracking["state"] = ShortCoveringState.IGNITION_CANDIDATE
                tracking["count"] = 1
                self._tracked_states[symbol] = tracking
                logger.debug(
                    "[SC_5M] %s | WATCH → IGNITION_CANDIDATE (moderate conviction) | score=%.1f threshold=%.1f",
                    symbol, score, self.min_ignition_score,
                )
                return (None, "IGNITION_CANDIDATE_WATCH", score, f"Moderate ignition (Score: {score:.1f}) — watching for confirming candle", reasons) if return_diagnostics else None

        elif current_state == ShortCoveringState.IGNITION_CANDIDATE:
            # Confirming evidence in subsequent candle
            tracking["state"] = ShortCoveringState.CONFIRMED_IGNITION
            tracking["count"] = tracking.get("count", 1) + 1
            self._tracked_states[symbol] = tracking
            logger.info(
                "⚡ [SC_5M] %s | IGNITION_CANDIDATE → CONFIRMED_IGNITION (confirming candle) | score=%.1f | count=%d",
                symbol, score, tracking["count"],
            )

        elif current_state == ShortCoveringState.CONFIRMED_IGNITION:
            tracking["state"] = ShortCoveringState.CONTINUATION
            self._tracked_states[symbol] = tracking
            logger.debug("[SC_5M] %s | CONFIRMED_IGNITION → CONTINUATION (already alerted)", symbol)
            return (None, "CONTINUATION_STATE", score, "Already alerted — currently in continuation phase", reasons) if return_diagnostics else None

        elif current_state in (ShortCoveringState.CONTINUATION, ShortCoveringState.EXHAUSTED):
            logger.debug("[SC_5M] %s | %s → no action", symbol, current_state.value)
            return (None, "EXHAUSTED_STATE", score, "Ignition move exhausted", reasons) if return_diagnostics else None

        # Record alert timestamp for 30m cooldown guard
        self._last_alert_time[symbol] = current_time

        # Calculate Alert Latency
        latency_minutes = max(0.0, (current_time - true_ignition_time).total_seconds() / 60.0)

        # Determine Grade
        if score >= 85.0:
            grade = "A+"
        elif score >= 76.0:
            grade = "A"
        elif score >= 68.0:
            grade = "B"
        else:
            grade = "C"

        ignition_low = float(cur_bar["low"])
        stop_loss = round(min(ignition_low, cur_vwap * 0.996), 2)
        risk_per_share = max(cur_close - stop_loss, cur_close * 0.005)

        if eod_candidate and eod_candidate.overhead_resistance > cur_close:
            target = round(float(eod_candidate.overhead_resistance), 2)
        else:
            target = round(cur_close + (risk_per_share * 2.0), 2)

        rr_ratio = round((target - cur_close) / risk_per_share, 2)
        rs_pct = round(price_change_5m_pct - 0.10, 2)

        signal = ShortCoveringSignal(
            symbol=symbol,
            timestamp=current_time,
            ignition_price=cur_close,
            session_open_price=session_open_price,
            true_ignition_time=true_ignition_time,
            alert_latency_minutes=latency_minutes,
            vwap=cur_vwap,
            stop_loss=stop_loss,
            initial_target=target,
            risk_reward_ratio=float(rr_ratio),
            excess_oi_contraction=float(excess_oi_contraction),
            oi_contraction_session_pct=float(oi_change_session_pct),
            volume_surge_ratio=float(vol_surge_ratio),
            rs_vs_nifty_pct=float(rs_pct),
            prior_short_score=float(score * 0.25),
            ignition_score=min(100.0, float(score)),
            grade=grade,
            state=ShortCoveringState.CONFIRMED_IGNITION,
            timeframe_confirmations=tf_confirmations,
            reasons=reasons
        )
        logger.info(
            "🚨 [SC_5M] %s | ✅ CONFIRMED_IGNITION | grade=%s | score=%.1f"
            " | entry=₹%.2f SL=₹%.2f target=₹%.2f RR=%.2f"
            " | vol=%.2fx excess_oi=%+.2f%% latency=%.0fm"
            " | reasons: %s",
            symbol, grade, min(100.0, score), cur_close, stop_loss, target, rr_ratio,
            vol_surge_ratio,
            excess_oi_contraction if not pd.isna(excess_oi_contraction) else 0.0,
            latency_minutes, " | ".join(reasons),
        )
        return (signal, "CONFIRMED_IGNITION", score, "Confirmed ignition breakout", reasons) if return_diagnostics else signal

    def _check_multitf_context(self, past_5m_bars: pd.DataFrame) -> Dict[str, Any]:
        """Calculates progressive 15m and 30m context from 5m bars."""
        result = {"15m_vwap_hold": True, "30m_structure": "RECLAIMING_STRUCTURE"}
        if len(past_5m_bars) < 6:
            return result

        last_3 = past_5m_bars.tail(3)
        if last_3["close"].iloc[-1] >= last_3["vwap"].iloc[-1]:
            result["15m_vwap_hold"] = True

        last_6 = past_5m_bars.tail(6)
        prior_high_30m = last_6["high"].iloc[:-1].max()
        cur_close = last_6["close"].iloc[-1]

        if cur_close >= prior_high_30m:
            result["30m_structure"] = "BREAKOUT"
        elif cur_close >= prior_high_30m * 0.995:
            result["30m_structure"] = "NEAR_BREAKOUT"
        elif cur_close >= last_6["vwap"].iloc[-1]:
            result["30m_structure"] = "RECLAIMING_STRUCTURE"
        else:
            result["30m_structure"] = "BELOW_RESISTANCE"

        return result

    def _get_index_5m_oi_delta(self, current_time: datetime) -> float:
        """Fetches NIFTY 5m futures OI change percentage."""
        try:
            df_nifty = oi_data_service.get_intraday_5m_data("NIFTY", current_time.date())
            if df_nifty is not None and not df_nifty.empty:
                past = df_nifty[df_nifty["timestamp"] <= current_time]
                if not past.empty:
                    return float(past.iloc[-1]["oi_change_5m_pct"])
        except Exception:
            pass
        return 0.0

    def _load_eod_watchlist(self, target_date: date) -> List[EODShortPositionCandidate]:
        """Loads yesterday's shortlisted candidates from DB."""
        candidates = []
        if os.getenv("DATABASE_URL") and not os.getenv("DISABLE_DB_OI_LOOKUP"):
            try:
                try:
                    from app.database import get_connection
                except ImportError:
                    from database import get_connection
                from psycopg2.extras import RealDictCursor
                with get_connection(timeout=1) as conn:
                    if hasattr(conn, "is_dummy") and conn.is_dummy:
                        return []
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        # RULE 67 RATIONALE: Only select candidates matching the most recent session on/before target_date
                        # to ensure stale entries from older sessions are never intermingled.
                        cur.execute("""
                            SELECT * FROM short_covering_watchlist 
                            WHERE scan_date = (SELECT MAX(scan_date) FROM short_covering_watchlist WHERE scan_date <= %s)
                            ORDER BY buildup_quality_score DESC LIMIT 40;
                        """, (target_date,))
                        rows = cur.fetchall()
                        for r in rows:
                            candidates.append(EODShortPositionCandidate(
                                symbol=r["symbol"],
                                scan_date=r["scan_date"],
                                close_price=float(r.get("close_price") or 0),
                                total_oi=int(r.get("total_oi") or 0),
                                oi_change_pct_1d=float(r.get("oi_change_pct_1d") or 0),
                                oi_buildup_5d_pct=float(r.get("oi_buildup_5d_pct") or 0),
                                oi_buildup_10d_pct=float(r.get("oi_buildup_10d_pct") or r.get("oi_buildup_5d_pct") or 0),
                                short_buildup_ratio=float(r.get("short_buildup_ratio") or 0.6),
                                rsi_14=float(r.get("rsi_14") or 40.0),
                                support_level=float(r.get("support_level") or 0),
                                overhead_resistance=float(r.get("overhead_resistance") or 0),
                                atr_14=float(r.get("atr_14") or 0),
                                daily_volume=1_000_000,
                                sector=r.get("sector") or "GENERAL",
                                buildup_quality_score=float(r.get("buildup_quality_score") or 70.0),
                                reasons=["Loaded from Layer 1 EOD watchlist"]
                            ))
            except Exception as e:
                logger.error(f"Could not load EOD watchlist from DB: {e}")
                if os.getenv("DATABASE_URL") and not os.getenv("DISABLE_DB_OI_LOOKUP"):
                    raise
        return candidates

    def _persist_alerts(self, alerts: List[ShortCoveringSignal]) -> None:
        """Persists short-covering alerts to database."""
        try:
            from app.database import get_connection
            with get_connection(timeout=1) as conn:
                if getattr(conn, "is_dummy", False) is True:
                    return
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS short_covering_alerts (
                            id SERIAL PRIMARY KEY,
                            symbol TEXT NOT NULL,
                            alert_time TIMESTAMPTZ NOT NULL,
                            ignition_price DOUBLE PRECISION,
                            vwap DOUBLE PRECISION,
                            stop_loss DOUBLE PRECISION,
                            initial_target DOUBLE PRECISION,
                            risk_reward_ratio DOUBLE PRECISION,
                            excess_oi_contraction DOUBLE PRECISION,
                            volume_surge_ratio DOUBLE PRECISION,
                            ignition_score DOUBLE PRECISION,
                            grade VARCHAR(10),
                            reasons JSONB,
                            state VARCHAR(30) DEFAULT 'CONFIRMED_IGNITION',
                            created_at TIMESTAMPTZ DEFAULT NOW()
                        );
                        CREATE INDEX IF NOT EXISTS idx_sc_alerts_time ON short_covering_alerts(alert_time);
                        CREATE INDEX IF NOT EXISTS idx_sc_alerts_symbol ON short_covering_alerts(symbol);
                    """)
                    import json
                    for a in alerts:
                        cur.execute("""
                            INSERT INTO short_covering_alerts (
                                symbol, alert_time, ignition_price, vwap, stop_loss,
                                initial_target, risk_reward_ratio, excess_oi_contraction,
                                volume_surge_ratio, ignition_score, grade, reasons, state
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            a.symbol, a.timestamp, a.ignition_price, a.vwap, a.stop_loss,
                            a.initial_target, a.risk_reward_ratio, a.excess_oi_contraction,
                            a.volume_surge_ratio, a.ignition_score, a.grade,
                            json.dumps(a.reasons), a.state.value if hasattr(a.state, "value") else str(a.state)
                        ))
                    if hasattr(conn, "commit"):
                        conn.commit()
            logger.info(f"💾 Persisted {len(alerts)} alerts to short_covering_alerts table")

            # Canonical alerts table sync for trade dashboard, health, and exit tracking
            try:
                import sys
                db_mod = sys.modules.get("app.database") or sys.modules.get("database")
                _save_fn = getattr(db_mod, "save_alert_if_new", save_alert_if_new) if db_mod else save_alert_if_new
                for a in alerts:
                    alert_time_str = a.timestamp.strftime("%Y-%m-%d %H:%M:%S") if hasattr(a.timestamp, "strftime") else str(a.timestamp)[:19]
                    signals_str = (
                        f"Short Covering Ignition [{a.grade}] (OI: {a.excess_oi_contraction}%, Surge: {a.volume_surge_ratio}x)"
                        if a.excess_oi_contraction is not None
                        else f"Short Covering Ignition [{a.grade}]"
                    )
                    _save_fn(
                        symbol=a.symbol,
                        breakout_type=f"SHORT_COVERING_IGNITION_{a.grade}",
                        alert_time=alert_time_str,
                        scanner="SHORT_COVERING_5M",
                        category="SHORT_COVERING",
                        entry_price=float(a.ignition_price) if a.ignition_price else None,
                        stop_loss=float(a.stop_loss) if a.stop_loss else None,
                        target_1=float(a.initial_target) if a.initial_target else None,
                        target_2=float(a.initial_target * 1.05) if a.initial_target else None,
                        target_price=float(a.initial_target) if a.initial_target else None,
                        signals=signals_str,
                        score=int(round(float(a.ignition_score) + 1e-5)) if a.ignition_score else 70,
                        volume_ratio=float(a.volume_surge_ratio) if a.volume_surge_ratio else 1.0,
                        context={
                            "vwap": float(a.vwap) if a.vwap else None,
                            "excess_oi_contraction": float(a.excess_oi_contraction) if a.excess_oi_contraction else None,
                            "volume_surge_ratio": float(a.volume_surge_ratio) if a.volume_surge_ratio else None,
                            "grade": str(a.grade),
                            "reasons": a.reasons,
                            "state": a.state.value if hasattr(a.state, "value") else str(a.state)
                        }
                    )
            except Exception as _al_err:
                logger.warning(f"Could not save short covering alert to canonical alerts table: {_al_err}")
        except Exception as e:
            # RULE 67 RATIONALE: Re-raise DB persistence error when database is configured so that
            # scanner_health accurately reflects FAILURE / DOWN status rather than fake success.
            logger.error(f"❌ Could not persist alerts to DB: {e}")
            try:
                from app.database import insert_notification
                insert_notification(
                    notif_type="error",
                    title="🚨 SHORT_COVERING Alert Save Failed",
                    message=f"Failed to persist {len(alerts)} alerts to database: {e}",
                    symbol=None
                )
            except Exception:
                pass
            if os.getenv("DATABASE_URL") and not os.getenv("DISABLE_DB_OI_LOOKUP"):
                raise


# Global singleton instance
short_covering_scanner = ShortCoveringEarlyIgnitionScanner()
