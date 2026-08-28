"""
app/accumulation_scanner.py

Main Orchestration Engine for ACCUMULATION_SCANNER_V1.
Additive, isolated 7th scanner subsystem for early detection of institutional accumulation,
volatility compression, and pre-breakout structures.
"""

import os
import time
import math
import json
import logging
import threading
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from accumulation_config import (
    ACCUMULATION_SCANNER_NAME,
    ACCUMULATION_VERSION,
    ACCUMULATION_DEFAULT_BATCH_SIZE,
    ACCUMULATION_WEIGHTS,
    STATE_THRESHOLDS,
    FUNDAMENTAL_FLOOR_CONFIG,
    TECHNICAL_LOOKBACKS
)
from accumulation_contracts import validate_accumulation_manifest
from accumulation_control import AccumulationControl
from accumulation_health import AccumulationHealthTracker
from accumulation_telemetry import AccumulationTelemetryContext
from accumulation_sl_target import compute_accumulation_sl_target

from lock_utils import ProcessLock
from database import (
    get_connection,
    save_alert_if_new,
    is_scanner_stopped,
    upsert_scanner_health,
    start_scanner_execution_run,
    complete_scanner_execution_run
)
from watchlist_cache import get_watchlist
from price_cache import fetch_watchlist_data
from macro_utils import get_nifty_20d_return

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_accumulation_run_lock = threading.Lock()


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return default
        return float(val)
    except Exception:
        return default


class AccumulationScanner:
    def __init__(self, batch_size: int = ACCUMULATION_DEFAULT_BATCH_SIZE):
        self.batch_size = batch_size

    def calculate_technical_features(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Calculates technical indicators for accumulation, compression, and resistance."""
        if df is None or df.empty or len(df) < 50:
            return {}

        df = df.copy()
        close = df["Close"].astype(float)
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
        volume = df["Volume"].astype(float)

        cmp = float(close.iloc[-1])
        vol_latest = float(volume.iloc[-1])
        avg_vol_20 = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.mean())
        vol_ratio = vol_latest / avg_vol_20 if avg_vol_20 > 0 else 1.0

        # Moving averages
        sma20 = float(close.iloc[-20:].mean()) if len(close) >= 20 else cmp
        sma50 = float(close.iloc[-50:].mean()) if len(close) >= 50 else cmp
        sma200 = float(close.iloc[-200:].mean()) if len(close) >= 200 else cmp

        # ATR 14
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1)))) if 'np' in globals() else (high - low)
        atr14 = float(tr.iloc[-14:].mean()) if len(tr) >= 14 else float(cmp * 0.02)

        # RSI 14
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).iloc[-14:].mean()
        loss = (-delta.where(delta < 0, 0)).iloc[-14:].mean()
        rs = gain / loss if loss > 0 else 1.0
        rsi14 = 100.0 - (100.0 / (1.0 + rs))

        # OBV & OBV Slope
        obv = (np.sign(close.diff().fillna(0)) * volume).cumsum() if 'np' in globals() else volume.cumsum()
        obv_latest = float(obv.iloc[-1])
        obv_20_ago = float(obv.iloc[-20]) if len(obv) >= 20 else float(obv.iloc[0])
        obv_slope = (obv_latest - obv_20_ago) / max(1.0, abs(obv_20_ago))

        # Bollinger Band Width
        rolling_std = float(close.iloc[-20:].std()) if len(close) >= 20 else cmp * 0.02
        bb_width = (4 * rolling_std) / sma20 if sma20 > 0 else 0.05

        # Volatility Compression / Range Compression
        range_20 = float((high.iloc[-20:].max() - low.iloc[-20:].min()) / cmp) if len(close) >= 20 else 0.10

        # Resistance Structure
        high_52w = float(high.max())
        high_200d = float(high.iloc[-200:].max()) if len(high) >= 200 else high_52w
        high_20d = float(high.iloc[-20:].max()) if len(high) >= 20 else high_52w
        
        nearest_resistance = high_20d if (high_20d > cmp and (high_20d - cmp) / cmp < 0.10) else high_52w
        if nearest_resistance <= cmp:
            nearest_resistance = cmp * 1.05

        distance_to_resistance = ((nearest_resistance - cmp) / cmp) * 100.0
        recent_swing_low = float(low.iloc[-10:].min()) if len(low) >= 10 else float(low.iloc[-1])

        return {
            "cmp": cmp,
            "sma20": sma20,
            "sma50": sma50,
            "sma200": sma200,
            "atr14": atr14,
            "rsi14": rsi14,
            "vol_ratio": vol_ratio,
            "obv": obv_latest,
            "obv_slope": obv_slope,
            "bb_width": bb_width,
            "range_20": range_20,
            "high_52w": high_52w,
            "high_200d": high_200d,
            "high_20d": high_20d,
            "nearest_resistance": nearest_resistance,
            "distance_to_resistance": distance_to_resistance,
            "recent_swing_low": recent_swing_low,
            "base_height": float(high.iloc[-20:].max() - low.iloc[-20:].min()) if len(high) >= 20 else atr14 * 4
        }

    def evaluate_fundamental_floor(self, fund_data: Optional[Dict[str, Any]]) -> Tuple[float, bool, List[str]]:
        """Evaluates lightweight fundamental quality floor."""
        if not fund_data:
            return 5.0, True, ["No fundamental data provided — using default floor pass"]

        roe = _safe_float(fund_data.get("ROE"))
        roce = _safe_float(fund_data.get("ROCE"))
        debt_eq = _safe_float(fund_data.get("DebtEquity"))
        sales_gr = _safe_float(fund_data.get("SalesGrowth"))
        pat_gr = _safe_float(fund_data.get("PATGrowth"))

        reasons = []
        score = 10.0

        if roe < FUNDAMENTAL_FLOOR_CONFIG["MIN_ROE"]:
            score -= 2.0
            reasons.append(f"ROE {roe:.1f}% < {FUNDAMENTAL_FLOOR_CONFIG['MIN_ROE']}%")
        if roce < FUNDAMENTAL_FLOOR_CONFIG["MIN_ROCE"]:
            score -= 2.0
            reasons.append(f"ROCE {roce:.1f}% < {FUNDAMENTAL_FLOOR_CONFIG['MIN_ROCE']}%")
        if debt_eq > FUNDAMENTAL_FLOOR_CONFIG["MAX_DEBT_EQUITY"]:
            score -= 3.0
            reasons.append(f"Debt/Equity {debt_eq:.2f} > {FUNDAMENTAL_FLOOR_CONFIG['MAX_DEBT_EQUITY']}")
        if sales_gr < FUNDAMENTAL_FLOOR_CONFIG["MIN_SALES_GROWTH"]:
            score -= 1.5
            reasons.append(f"Sales Growth {sales_gr:.1f}% < {FUNDAMENTAL_FLOOR_CONFIG['MIN_SALES_GROWTH']}%")
        if pat_gr < FUNDAMENTAL_FLOOR_CONFIG["MIN_PAT_GROWTH"]:
            score -= 1.5
            reasons.append(f"PAT Growth {pat_gr:.1f}% < {FUNDAMENTAL_FLOOR_CONFIG['MIN_PAT_GROWTH']}%")

        score = max(0.0, score)
        passed = (score >= 4.0)
        return score, passed, reasons

    def evaluate_symbol(
        self,
        symbol: str,
        df: pd.DataFrame,
        fund_data: Optional[Dict[str, Any]] = None,
        nifty_20d_ret: float = 0.0,
        run_id: str = "default_run"
    ) -> Dict[str, Any]:
        """Evaluates single symbol against accumulation rules and SL/Target engine."""
        telemetry = AccumulationTelemetryContext(run_id=run_id, symbol=symbol)

        if df is None or df.empty or len(df) < 50:
            telemetry.finalize("REJECTED", "INSUFFICIENT_HISTORY")
            telemetry.persist()
            return {"status": "NO", "reason": "INSUFFICIENT_HISTORY", "symbol": symbol}

        last_bar = df.iloc[-1]
        telemetry.capture_raw_market(
            open_p=_safe_float(last_bar.get("Open")),
            high_p=_safe_float(last_bar.get("High")),
            low_p=_safe_float(last_bar.get("Low")),
            close_p=_safe_float(last_bar.get("Close")),
            volume=_safe_float(last_bar.get("Volume")),
            timestamp=str(last_bar.name)
        )

        # Technical Feature Extraction
        tech = self.calculate_technical_features(df)
        telemetry.capture_indicators(tech)

        # Fundamental Floor Evaluation
        fund_score, fund_pass, fund_reasons = self.evaluate_fundamental_floor(fund_data)
        telemetry.capture_fundamentals(fund_data or {})
        telemetry.capture_gate("FundamentalFloor", fund_pass, actual_val=fund_score, operator_str=">=", threshold_val=4.0, reason="; ".join(fund_reasons))

        if not fund_pass:
            telemetry.finalize("REJECTED", "FUNDAMENTAL_FLOOR_FAILED")
            telemetry.persist()
            return {"status": "NO", "reason": "FUNDAMENTAL_FLOOR_FAILED", "symbol": symbol}

        # Sub-score calculations (0–100 scaled to weights)
        cmp = tech["cmp"]

        # 1. Accumulation Score (30 pts)
        obv_score = min(15.0, max(0.0, tech["obv_slope"] * 30.0))
        vol_acc_score = min(15.0, max(0.0, (tech["vol_ratio"] - 1.0) * 15.0)) if tech["vol_ratio"] > 1.0 else 5.0
        acc_score = min(30.0, obv_score + vol_acc_score)

        # 2. Volatility Compression Score (20 pts)
        bb_comp_score = min(10.0, max(0.0, (0.10 - tech["bb_width"]) * 100.0)) if tech["bb_width"] < 0.10 else 2.0
        range_comp_score = min(10.0, max(0.0, (0.15 - tech["range_20"]) * 66.0)) if tech["range_20"] < 0.15 else 2.0
        comp_score = min(20.0, bb_comp_score + range_comp_score)

        # 3. Relative Strength Score (15 pts)
        stock_20d_ret = ((cmp - float(df["Close"].iloc[-20])) / float(df["Close"].iloc[-20])) * 100.0 if len(df) >= 20 else 0.0
        rs_diff = stock_20d_ret - nifty_20d_ret
        rs_score = min(15.0, max(0.0, 7.5 + (rs_diff * 0.75)))
        telemetry.capture_relative_strength({"stock_20d_ret": stock_20d_ret, "nifty_20d_ret": nifty_20d_ret, "rs_diff": rs_diff})

        # 4. Resistance Proximity Score (15 pts)
        dist_res = tech["distance_to_resistance"]
        if 2.0 <= dist_res <= 8.0:
            res_score = 15.0
        elif dist_res < 2.0:
            res_score = 12.0
        elif dist_res <= 12.0:
            res_score = max(0.0, 15.0 - (dist_res - 8.0) * 2.5)
        else:
            res_score = 0.0
        telemetry.capture_resistance({"nearest_resistance": tech["nearest_resistance"], "distance_pct": dist_res})

        # 5. Volume/Delivery Structure (10 pts)
        vol_struct_score = min(10.0, max(0.0, tech["vol_ratio"] * 5.0))

        # Total Composite Score
        total_score = round(acc_score + comp_score + rs_score + res_score + vol_struct_score + fund_score, 1)

        scores_map = {
            "ACCUMULATION": round(acc_score, 1),
            "COMPRESSION": round(comp_score, 1),
            "RELATIVE_STRENGTH": round(rs_score, 1),
            "RESISTANCE": round(res_score, 1),
            "VOLUME_STRUCTURE": round(vol_struct_score, 1),
            "FUNDAMENTAL": round(fund_score, 1),
            "TOTAL": total_score
        }
        telemetry.capture_scores(scores_map)

        # State Classification
        state = "NONE"
        if total_score >= STATE_THRESHOLDS["BREAKOUT_READY"]:
            state = "BREAKOUT_READY"
        elif total_score >= STATE_THRESHOLDS["PRE_BREAKOUT"]:
            state = "PRE_BREAKOUT"
        elif total_score >= STATE_THRESHOLDS["ACCUMULATION_WATCH"]:
            state = "ACCUMULATION_WATCH"

        if state == "NONE":
            telemetry.finalize("REJECTED", f"Score {total_score:.1f} < {STATE_THRESHOLDS['ACCUMULATION_WATCH']} threshold")
            telemetry.persist()
            return {"status": "NO", "reason": "SCORE_BELOW_THRESHOLD", "score": total_score, "symbol": symbol}

        # Structural SL/Target Execution
        sl_tgt = compute_accumulation_sl_target(
            cmp=cmp,
            resistance=tech["nearest_resistance"],
            recent_swing_low=tech["recent_swing_low"],
            range_low=float(df["Low"].iloc[-20:].min()) if len(df) >= 20 else cmp * 0.95,
            nearest_support=tech["sma50"],
            atr=tech["atr14"],
            high_52w=tech["high_52w"],
            base_height=tech["base_height"]
        )
        telemetry.capture_sl_target(sl_tgt)

        # Record consumed decision inputs for path contract validation
        for k in ["Close", "Open", "High", "Low", "Volume", "SMA20", "SMA50", "SMA200", "EMA20", "ATR", "RSI", "ADX", "OBV", "OBV_SLOPE", "BB_WIDTH", "ATR_PERCENTILE", "RS_NIFTY_20D", "RS_NIFTY_60D", "HIGH_52W", "HIGH_200D", "RESISTANCE", "DISTANCE_TO_RESISTANCE", "ROE", "ROCE", "DEBT_EQUITY", "SALES_GROWTH", "PAT_GROWTH"]:
            val = tech.get(k.lower(), fund_data.get(k) if fund_data else 0.0)
            telemetry.add_decision_input(name=k, value=val, source="AccumulationEngine", valid=True)

        telemetry.finalize("SELECTED", f"Qualified for {state} with score {total_score:.1f}")
        telemetry.persist()

        return {
            "status": "QUALIFIED",
            "symbol": symbol,
            "state": state,
            "score": total_score,
            "scores_breakdown": scores_map,
            "sl_target": sl_tgt,
            "audit_snapshot_id": telemetry.audit_snapshot_id,
            "cmp": cmp
        }

    def start(self, force: bool = False, trigger_type: str = "SCHEDULED") -> Dict[str, Any]:
        """Main scanner execution loop with full Health Card and Execution History integration."""
        if is_scanner_stopped("ACCUMULATION"):
            logger.info("⏭️ ACCUMULATION scanner is STOPPED by Admin. Skipping.")
            return {"status": "STOPPED", "reason": "STOPPED_BY_ADMIN"}

        if not _accumulation_run_lock.acquire(blocking=False):
            logger.info("⏳ ACCUMULATION scanner already running in another thread. Skipping.")
            return {"status": "SKIPPED", "reason": "ALREADY_RUNNING"}
        start_time = datetime.now(IST)
        run_id = f"acc_run_{start_time.strftime('%Y%m%d_%H%M%S')}"
        
        acquired_global = False
        acquired_scan = False

        try:
            run_ctx = start_scanner_execution_run(
                scanner_name="ACCUMULATION",
                trigger_type=trigger_type,
                scheduler_name="SCHEDULER"
            )

            from lock_utils import ProcessLock
            _global_lock = ProcessLock("global_scanner_lock")
            if not _global_lock.acquire(blocking=False, owner_scanner="ACCUMULATION", operation="FULL_SCAN"):
                logger.info("⏳ [ACCUMULATION] Global scanner lock busy — waiting in queue...")
                from database import update_scanner_run_lifecycle
                update_scanner_run_lifecycle(run_ctx.run_id, "QUEUED")
                upsert_scanner_health("ACCUMULATION", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")
                
                try:
                    acquired_global = _global_lock.acquire(blocking=True, owner_scanner="ACCUMULATION", operation="FULL_SCAN", run_ctx=run_ctx)
                except Exception as lock_err:
                    logger.error(f"❌ [ACCUMULATION] Error acquiring global lock: {lock_err}")
                    acquired_global = False

                if not acquired_global:
                    logger.error("❌ [ACCUMULATION] Failed to acquire global scanner lock after queue wait.")
                    complete_scanner_execution_run(run_ctx, status_override="FAILED", stop_reason="Global lock acquire timeout")
                    upsert_scanner_health("ACCUMULATION", "IDLE", error_msg="Lock acquisition timed out")
                    return {"status": "FAILED", "reason": "LOCK_TIMEOUT"}
            else:
                acquired_global = True

            from database import update_scanner_run_lifecycle
            update_scanner_run_lifecycle(run_ctx.run_id, "RUNNING")
            health = AccumulationHealthTracker(run_id=run_id, scanner=ACCUMULATION_SCANNER_NAME)
            from lock_utils import print_scanner_start_banner
            _scan_start = print_scanner_start_banner("ACCUMULATION", run_id=run_id)
            acquired_scan = True

            health.transition("STARTING", status="RUNNING")

            # Check stop/pause controls
            if AccumulationControl.should_stop():
                health.stop("CONTROL_STOP_REQUESTED")
                upsert_scanner_health("ACCUMULATION", status="STOPPED", error_msg="Stopped by Admin")
                complete_scanner_execution_run(run_ctx, status_override="STOPPED", stop_reason="Stopped by Admin")
                return {"status": "STOPPED", "reason": "CONTROL_STOP_REQUESTED"}

            if not AccumulationControl.wait_if_paused():
                health.stop("CONTROL_STOP_DURING_PAUSE")
                upsert_scanner_health("ACCUMULATION", status="PAUSED", error_msg="Paused by Admin")
                complete_scanner_execution_run(run_ctx, status_override="PAUSED", stop_reason="Paused by Admin")
                return {"status": "STOPPED", "reason": "CONTROL_STOP_DURING_PAUSE"}

            # Load Watchlist
            health.transition("DATA_LOADING")
            wl_df = get_watchlist()
            if wl_df is None or wl_df.empty:
                health.fail(RuntimeError("Watchlist is empty"))
                upsert_scanner_health("ACCUMULATION", status="DOWN", error_msg="Watchlist is empty")
                complete_scanner_execution_run(run_ctx, status_override="FAILED", stop_reason="Watchlist is empty")
                return {"status": "FAILED", "reason": "EMPTY_WATCHLIST"}

            symbols = wl_df["Stock"].dropna().tolist()
            health.requested_symbols = len(symbols)
            if run_ctx:
                run_ctx.set_total_stocks(len(symbols))
            logger.info(f"🚀 [ACCUMULATION SCANNER] Starting scan for {len(symbols)} symbols...")

            # Load Macro Data (Nifty 20D Return)
            nifty_20d_ret = _safe_float(get_nifty_20d_return(), 0.0)

            health.transition("ACCUMULATION_EVALUATION")
            candidates = []
            alerts_count = 0

            # Batch processing loop
            batches = [symbols[i:i + self.batch_size] for i in range(0, len(symbols), self.batch_size)]
            _last_hb = time.monotonic()
            for b_idx, batch in enumerate(batches, start=1):
                if run_ctx and (time.monotonic() - _last_hb) >= 10.0:
                    try:
                        run_ctx.heartbeat()
                        _last_hb = time.monotonic()
                    except Exception: pass
                # Cooperative Stop / Pause check at every batch boundary
                if AccumulationControl.should_stop():
                    health.stop("ADMIN_STOP_MID_BATCH")
                    break

                if not AccumulationControl.wait_if_paused():
                    health.stop("ADMIN_STOP_DURING_PAUSE")
                    break

                # Fetch daily OHLCV batch
                ohlcv_map = fetch_watchlist_data(pd.DataFrame({"Stock": batch}), period="1y", interval="1d")

                for sym in batch:
                    df = ohlcv_map.get(sym)
                    if isinstance(df, pd.DataFrame) and not df.empty:
                        if getattr(df, "attrs", {}).get("is_stale"):
                            if run_ctx:
                                run_ctx.mark_stale()
                        else:
                            if run_ctx:
                                run_ctx.mark_fresh()
                    else:
                        if run_ctx:
                            run_ctx.mark_incomplete()

                    res = self.evaluate_symbol(
                        symbol=sym,
                        df=df if isinstance(df, pd.DataFrame) else None,
                        fund_data=None,
                        nifty_20d_ret=nifty_20d_ret,
                        run_id=run_id
                    )

                    health.record_metrics(processed_inc=1)
                    if res.get("status") == "QUALIFIED":
                        health.record_metrics(valid_inc=1, candidates_inc=1)
                        candidates.append(res)
                        
                        # Persist Alert to accumulation_alerts table
                        state = res["state"]
                        sl_tgt = res["sl_target"]
                        snapshot_id = res["audit_snapshot_id"]
                        
                        try:
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute(
                                        """
                                        INSERT INTO accumulation_alerts (
                                            run_id, audit_snapshot_id, symbol, state, tradable,
                                            score, accumulation_score, compression_score, relative_strength_score,
                                            resistance_score, volume_structure_score, fundamental_score,
                                            close, entry_zone_low, entry_zone_high, breakout_level, stop_loss,
                                            target_1, target_2, target_3, risk_pct, rr_1, rr_2, rr_3,
                                            time_stop_days, invalidation_reason, created_at, effective_as_of
                                        ) VALUES (
                                            %s, %s, %s, %s, %s,
                                            %s, %s, %s, %s,
                                            %s, %s, %s,
                                            %s, %s, %s, %s, %s,
                                            %s, %s, %s, %s, %s, %s, %s,
                                            %s, %s, NOW(), NOW()
                                        ) ON CONFLICT (symbol, state, run_id) DO NOTHING
                                        """,
                                        (
                                            run_id, snapshot_id, sym, state, sl_tgt.get("tradable", True),
                                            res["score"], res["scores_breakdown"]["ACCUMULATION"], res["scores_breakdown"]["COMPRESSION"], res["scores_breakdown"]["RELATIVE_STRENGTH"],
                                            res["scores_breakdown"]["RESISTANCE"], res["scores_breakdown"]["VOLUME_STRUCTURE"], res["scores_breakdown"]["FUNDAMENTAL"],
                                            res["cmp"], sl_tgt["entry_zone_low"], sl_tgt["entry_zone_high"], sl_tgt["breakout_level"], sl_tgt["stop_loss"],
                                            sl_tgt["target_1"], sl_tgt["target_2"], sl_tgt["target_3"], sl_tgt["risk_pct"], sl_tgt["rr_1"], sl_tgt["rr_2"], sl_tgt["rr_3"],
                                            sl_tgt["time_stop_days"], sl_tgt["invalidation_condition"]
                                        )
                                    )
                                    conn.commit()
                                    alerts_count += 1
                                    if run_ctx:
                                        run_ctx.add_alert(1)
                                    health.record_metrics(alerts_inc=1)
                        except Exception as al_err:
                            logger.warning(f"Could not persist accumulation alert for {sym}: {al_err}")
                    else:
                        health.record_metrics(rejected_inc=1)

            health.transition("COMPLETED", status="OK" if health.status != "STOPPED" else "STOPPED")
            health.complete()

            dur_sec = round((datetime.now(IST) - start_time).total_seconds(), 2)
            now_str = datetime.now(IST).isoformat()
            upsert_scanner_health(
                "ACCUMULATION",
                status="OK",
                last_success=now_str,
                today_alerts=alerts_count,
                processed_count=len(symbols),
                total_count=len(symbols),
                duration_seconds=dur_sec
            )
            if run_ctx:
                complete_scanner_execution_run(run_ctx)

            return {
                "status": "OK",
                "run_id": run_id,
                "processed": health.processed_symbols,
                "candidates_count": len(candidates),
                "alerts_count": alerts_count,
                "duration_seconds": health.duration_seconds
            }

        except Exception as exc:
            health.fail(exc)
            dur_sec = round((datetime.now(IST) - start_time).total_seconds(), 2)
            upsert_scanner_health(
                "ACCUMULATION",
                status="DOWN",
                error_msg=str(exc),
                duration_seconds=dur_sec
            )
            if run_ctx:
                complete_scanner_execution_run(run_ctx, exception=exc)
            return {"status": "FAILED", "error": str(exc)}
        finally:
            if '_scan_start' in locals() and _scan_start:
                try:
                    from lock_utils import print_scanner_end_banner
                    print_scanner_end_banner("ACCUMULATION", _scan_start, run_id=run_id)
                except Exception:
                    pass
            try:
                _global_lock.release()
            except Exception:
                pass
            _accumulation_run_lock.release()
