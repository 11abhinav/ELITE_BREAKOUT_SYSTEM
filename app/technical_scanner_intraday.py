# =====================================================================================
# PRODUCTION-GRADE 15M INTRADAY TECHNICAL SCANNER — Market Hours Engine
# 
# Operates on 15-minute candles during market hours (09:15 - 15:30 IST).
# Reuses all institutional-grade pattern sub-detectors and scoring engines,
# with 1-hour per-symbol per-pattern alert cooldown deduplication.
# =====================================================================================

import logging
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple, Set
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from application_context import current_app_context
from candidate_analytics_engine import record_candidate_snapshot
from candidate_tracker import candidate_pool
from database import DB_PATH, save_alert_if_new
from data_fetch_status import record_data_fetch_start, record_data_fetch_end
from lock_utils import ProcessLock, print_scanner_start_banner, print_scanner_end_banner
from price_cache import fetch_watchlist_data
from technical_indicators import apply_indicators
from telemetry_manager import telemetry
from watchlist_cache import get_watchlist
from trading_calendar import is_market_open_now

from scanner_telemetry import (
    start_scanner_execution_run,
    finish_scanner_execution_run,
    upsert_scanner_health,
)

try:
    from regime_pattern_policy import APPROVED_TECHNICAL_PATTERNS, evaluate_pattern_for_regime
except ImportError:
    APPROVED_TECHNICAL_PATTERNS = {
        "WYCKOFF_SPRING_TYPE_2", "BULL_FLAG", "MULTI_MONTH_BASE_BREAKOUT",
        "UNDERCUT_AND_RALLY", "SHAKEOUT_RECLAIM", "DOUBLE_BOTTOM", "V_REVERSAL",
        "CUP_HANDLE", "CUP_AND_HANDLE", "ASCENDING_TRIANGLE", "BULL_PENNANT",
        "HIGHER_LOW_REVERSAL", "FLAT_BASE_BREAKOUT", "FALLING_WEDGE_REVERSAL",
        "INVERSE_HEAD_AND_SHOULDERS", "DOUBLE_BOTTOM_SHAKEOUT", "PENNANT_CONVERGENCE",
        "PULLBACK_EMA_BOUNCE", "HIGH_TIGHT_FLAG", "VCP_CONTRACTION",
    }
    def evaluate_pattern_for_regime(pat, reg=None):
        return {"allowed": True, "bonus_points": 5.0}

from technical_scanner import (
    _safe_float,
    _extract_ohlcv,
    _coalesce_indicator_val,
    _detect_wyckoff_spring_type_2,
    _detect_bull_flag,
    _detect_multi_month_base_breakout,
    _detect_undercut_and_rally,
    _detect_double_bottom,
    _detect_v_reversal,
    _detect_cup_and_handle,
    _detect_ascending_triangle,
    _detect_bull_pennant,
    _detect_higher_low_reversal,
    _detect_flat_base_breakout,
    MIN_RVOL_HARD_GATE,
    MIN_CLV_HARD_GATE,
    MAX_UPPER_WICK_PCT,
    MIN_AVG_TURNOVER_INR,
    MIN_AVG_VOLUME,
    MAX_SL_PCT,
    MIN_SL_PCT,
    MIN_ROOM_TO_RESISTANCE_R,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_scan_lock = ProcessLock("technical_intraday_scanner_lock")
_global_lock = ProcessLock("global_scanner_lock")

# Alert Cooldown: 1 alert per symbol per pattern per hour (3600 seconds)
ALERT_COOLDOWN_SECONDS = 3600

def _is_alert_in_cooldown(symbol: str, category: str, cooldown_secs: int = ALERT_COOLDOWN_SECONDS) -> bool:
    """Checks if an alert for this symbol and pattern category was dispatched within cooldown window."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        query = """
            SELECT alert_time FROM alerts 
            WHERE symbol = ? AND scanner = 'TECHNICAL_INTRADAY' AND category = ?
            ORDER BY id DESC LIMIT 1
        """
        cursor.execute(query, (symbol, category))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return False

        last_time_str = row[0]
        # Parse timestamp
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                last_dt = datetime.strptime(last_time_str, fmt).replace(tzinfo=IST)
                now_dt = datetime.now(IST)
                diff = (now_dt - last_dt).total_seconds()
                return diff < cooldown_secs
            except ValueError:
                continue
        return False
    except Exception as e:
        logger.debug(f"Error checking cooldown for {symbol} {category}: {e}")
        return False


def run_technical_intraday_pipeline(
    watchlist: Optional[List[str]] = None,
    is_test_mode: bool = False,
    force: bool = False,
    run_ctx: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Executes the Intraday 15M Technical Scanner over market hours.
    
    Operates on latest 15-minute OHLCV candles with 1-hour alert cooldown per symbol/pattern.
    """
    now = datetime.now(IST)
    if not force and not is_test_mode:
        if not is_market_open_now():
            logger.info("⏸️ [TECHNICAL INTRADAY] Market is currently closed. Skipping run.")
            return {"total_count": 0, "processed_count": 0, "today_alerts": 0, "status": "MARKET_CLOSED"}

    start_time_mono = time.monotonic()
    run_id, real_run_ctx = start_scanner_execution_run("TECHNICAL_INTRADAY", passed_ctx=run_ctx)

    with _scan_lock.acquire(), _global_lock.acquire():
        print_scanner_start_banner("TECHNICAL INTRADAY 15M SCANNER")

        if watchlist is None or len(watchlist) == 0:
            watchlist = get_watchlist()

        if not watchlist:
            logger.warning("⚠️ Watchlist is empty. Aborting TECHNICAL INTRADAY scan.")
            upsert_scanner_health("TECHNICAL_INTRADAY", "WARNING", f"Empty watchlist evaluated at {now.strftime('%H:%M:%S')}")
            finish_scanner_execution_run("TECHNICAL_INTRADAY", real_run_ctx, total_scanned=0, alerts_found=0, status="SUCCESS")
            return {"total_count": 0, "processed_count": 0, "today_alerts": 0}

        logger.info(f"🚀 Initializing Intraday 15M Technical Scan over {len(watchlist)} watchlist symbols...")

        # Ingest 15M intraday candles
        all_15m = fetch_watchlist_data(
            watchlist,
            period="30d",
            interval="15m",
            requester="TECHNICAL_INTRADAY",
            run_ctx=real_run_ctx,
        )

        qualified_candidates: List[Dict[str, Any]] = []
        rejection_counts: Dict[str, int] = {}
        funnel_stats = {
            "universe": len(watchlist),
            "data_fetched": 0,
            "common_gates_pass": 0,
            "pattern_candidates": 0,
            "risk_pass": 0,
            "score_pass": 0,
            "final_alerts": 0,
        }

        for symbol in watchlist:
            df = all_15m.get(symbol)
            if df is None or df.empty or len(df) < 15:
                if real_run_ctx:
                    real_run_ctx.mark_incomplete(1)
                rejection_counts["NO_15M_DATA"] = rejection_counts.get("NO_15M_DATA", 0) + 1
                continue

            funnel_stats["data_fetched"] += 1
            df_calc = apply_indicators(df.copy())
            
            opens, highs, lows, closes, volumes = _extract_ohlcv(df_calc)
            n = len(df_calc)
            if n < 15:
                rejection_counts["INSUFFICIENT_BARS"] = rejection_counts.get("INSUFFICIENT_BARS", 0) + 1
                continue

            c_today = _safe_float(closes[-1])
            h_today = _safe_float(highs[-1])
            l_today = _safe_float(lows[-1])
            o_today = _safe_float(opens[-1])
            v_today = _safe_float(volumes[-1])

            # 20-period 15m volume SMA
            vol_sma20 = _safe_float(np.mean(volumes[-20:])) if n >= 20 else _safe_float(np.mean(volumes))
            rvol = v_today / max(vol_sma20, 1.0)

            atr14 = _coalesce_indicator_val(df_calc, ["ATR", "ATR_14", "atr"], default=c_today * 0.015)

            # Common Gate: Minimum 15m RVOL >= 1.20
            if rvol < 1.20:
                rejection_counts["RVOL_BELOW_1.20"] = rejection_counts.get("RVOL_BELOW_1.20", 0) + 1
                continue

            # Candidate Pattern Evaluation
            detected_patterns: List[Dict[str, Any]] = []

            # 1. Wyckoff Spring
            ws = _detect_wyckoff_spring_type_2(df_calc, atr14)
            if ws: detected_patterns.append(ws)

            # 2. Bull Flag & Pole
            bf = _detect_bull_flag(df_calc, atr14)
            if bf: detected_patterns.append(bf)

            # 3. Multi-Month Base / Base Breakout
            mm = _detect_multi_month_base_breakout(df_calc, atr14)
            if mm: detected_patterns.append(mm)

            # 4. Undercut & Rally
            ur = _detect_undercut_and_rally(df_calc, atr14)
            if ur: detected_patterns.append(ur)

            # 5. Double Bottom
            db = _detect_double_bottom(df_calc, atr14)
            if db: detected_patterns.append(db)

            # 6. V-Reversal
            vr = _detect_v_reversal(df_calc, atr14)
            if vr: detected_patterns.append(vr)

            # 7. Cup & Handle
            ch = _detect_cup_and_handle(df_calc, atr14)
            if ch: detected_patterns.append(ch)

            # 8. Ascending Triangle
            at = _detect_ascending_triangle(df_calc, atr14)
            if at: detected_patterns.append(at)

            # 9. Bull Pennant
            bp = _detect_bull_pennant(df_calc, atr14)
            if bp: detected_patterns.append(bp)

            # 10. Higher Low Reversal
            hl = _detect_higher_low_reversal(df_calc, atr14)
            if hl: detected_patterns.append(hl)

            # 11. Flat Base
            fb = _detect_flat_base_breakout(df_calc, atr14)
            if fb: detected_patterns.append(fb)

            if not detected_patterns:
                rejection_counts["NO_PATTERN_MATCH"] = rejection_counts.get("NO_PATTERN_MATCH", 0) + 1
                continue

            funnel_stats["pattern_candidates"] += 1
            best_pat = max(detected_patterns, key=lambda x: x.get("pattern_quality_score", 20))
            pat_name = best_pat["pattern"]

            # Calculate Stop Loss and Targets on 15m structural pivot
            sl = best_pat.get("invalidation_level", round(l_today * 0.99, 2))
            risk_per_share = max(c_today - sl, c_today * 0.01)
            t1 = round(c_today + (risk_per_share * 1.5), 2)
            t2 = round(c_today + (risk_per_share * 2.5), 2)
            t3 = round(c_today + (risk_per_share * 4.0), 2)

            score = min(100, int(70 + (rvol * 5.0) + best_pat.get("pattern_quality_score", 20)))

            # Cooldown check: 1 alert per symbol per pattern per hour
            cat_str = pat_name.replace("_", " ")
            if _is_alert_in_cooldown(symbol, cat_str, ALERT_COOLDOWN_SECONDS):
                logger.debug(f"⏳ [COOLDOWN ACTIVE] Skipping {symbol} {pat_name} (alert sent < 60m ago)")
                rejection_counts["COOLDOWN_ACTIVE"] = rejection_counts.get("COOLDOWN_ACTIVE", 0) + 1
                continue

            res = {
                "symbol": symbol,
                "cmp": c_today,
                "rvol": round(rvol, 2),
                "stop_loss": sl,
                "target_1": t1,
                "target_2": t2,
                "target_3": t3,
                "score": score,
                "primary_pattern": pat_name,
                "classification": "🔥 TIER 1 15M INTRADAY BREAKOUT",
                "description": best_pat.get("description", f"15M Intraday {pat_name}"),
            }
            qualified_candidates.append(res)

        # Sort and dispatch alerts
        qualified_candidates.sort(key=lambda x: x["score"], reverse=True)
        alerts_saved = 0

        for cand in qualified_candidates:
            sym = cand["symbol"]
            cmp_price = cand["cmp"]
            score = cand["score"]
            sl = cand["stop_loss"]
            t1 = cand["target_1"]
            t2 = cand["target_2"]
            t3 = cand["target_3"]
            pat = cand["primary_pattern"]
            classification = cand["classification"]
            rvol = cand["rvol"]
            desc = cand["description"]

            logger.info(
                f"{classification} [TECHNICAL INTRADAY 15M TRIGGERED] {sym} | Pattern: {pat} | CMP: ₹{cmp_price:.2f} | "
                f"RVOL: {rvol:.2f}x | SL: ₹{sl:.2f} | T1: ₹{t1:.2f} | Score: {score}/100 | {desc}"
            )

            if not is_test_mode:
                inserted, reason, _, _ = save_alert_if_new(
                    symbol=sym,
                    breakout_type="TECHNICAL_INTRADAY",
                    alert_time=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                    scanner="TECHNICAL_INTRADAY",
                    category=pat.replace("_", " "),
                    entry_price=cmp_price,
                    stop_loss=sl,
                    target_1=t1,
                    target_2=t2,
                    target_3=t3,
                    signals=f"15M {pat.replace('_', ' ')}",
                    score=int(score),
                    context={
                        "timeframe": "15m",
                        "rvol": rvol,
                        "description": desc,
                        "pattern": pat,
                        "scanner_type": "TECHNICAL_INTRADAY",
                    }
                )
                if inserted:
                    alerts_saved += 1

        funnel_stats["final_alerts"] = alerts_saved
        elapsed_sec = round(time.monotonic() - start_time_mono, 2)

        status_msg = f"Completed 15M scan over {len(watchlist)} symbols in {elapsed_sec}s. Alerts: {alerts_saved}"
        logger.info(f"✅ [TECHNICAL INTRADAY 15M] {status_msg}")
        
        upsert_scanner_health("TECHNICAL_INTRADAY", "HEALTHY", status_msg)
        finish_scanner_execution_run("TECHNICAL_INTRADAY", real_run_ctx, total_scanned=len(watchlist), alerts_found=alerts_saved, status="SUCCESS")

        print_scanner_end_banner("TECHNICAL INTRADAY 15M SCANNER")

        return {
            "total_count": len(watchlist),
            "processed_count": funnel_stats["data_fetched"],
            "today_alerts": alerts_saved,
            "qualified_count": len(qualified_candidates),
            "funnel": funnel_stats,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    res = run_technical_intraday_pipeline(force=True, is_test_mode=True)
    print("Test Execution Result:", res)
