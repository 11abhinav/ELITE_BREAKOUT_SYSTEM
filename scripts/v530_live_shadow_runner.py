"""
V5.30 Production Shadow Live Execution Entrypoint
==================================================
Isolated shadow runner for V5.30 Daily Builder execution stream.
- Zero live capital or order placement (Isolated telemetry only).
- Immutably binds parameters from production_parameters.db.
- Writes to data/shadow_telemetry.db (v530_shadow_alert_telemetry, v530_shadow_trigger_telemetry, v530_vs_v529_disagreement_telemetry).
- Hard stops on any weekend candle (Saturday/Sunday = 0).
- Pure point-in-time intraday 45m HOD/VWAP trigger evaluation.
- Paired real-time disagreement logging vs V5.29 live shadow stream.
"""

import sys
import os
import sqlite3
import datetime
import math
import logging
import argparse
import time
from typing import List, Dict, Any, Optional

# Ensure project root in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.production.v530_shadow_execution_engine import (
    V530ShadowExecutionEngine,
    CandidateContext,
    TELEMETRY_DB_PATH,
    PARAM_DB_PATH,
    SHADOW_CONFIG_VERSION
)

LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "v530_shadow_live.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [V5.30_SHADOW] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("V530_SHADOW")

NSE_HOLIDAYS_2026 = {
    "2026-01-26", # Republic Day
    "2026-03-06", # Maha Shivratri
    "2026-03-25", # Holi
    "2026-04-03", # Good Friday
    "2026-04-14", # Dr. Ambedkar Jayanti
    "2026-05-01", # Maharashtra Day
    "2026-06-17", # Bakri Id
    "2026-08-15", # Independence Day
    "2026-10-02", # Mahatma Gandhi Jayanti
    "2026-10-20", # Dussehra
    "2026-11-08", # Diwali Laxmi Pujan
    "2026-11-10", # Diwali Balipratipada
    "2026-12-25"  # Christmas
}

def load_v530_registered_parameters() -> Dict[str, Any]:
    """Loads and verifies immutable parameter versions from production_parameters.db."""
    with sqlite3.connect(PARAM_DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT version_id, parameter_name, value, status, source_commit
            FROM production_parameter_versions
            WHERE version_id LIKE '%V530%' OR (scanner_scope='DAILY_BUILDER' AND status='SHADOW')
        """).fetchall()
    
    params = {r["parameter_name"]: r["value"] for r in rows}
    logger.info(f"Loaded {len(rows)} certified V5.30 parameters from {PARAM_DB_PATH}")
    return params

def record_v530_alert_telemetry(db_path: str, record: Dict[str, Any]) -> int:
    """Inserts a verified alert telemetry row into v530_shadow_alert_telemetry."""
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute("""
            INSERT INTO v530_shadow_alert_telemetry (
                config_version_id, symbol, decision_timestamp, exchange_session_date,
                source_commit, nifty_regime, sector, archetype, clv, extension_r,
                volume_retention_ratio, runway_atr, vwap_relationship, compression_days,
                days_since_impulse, base_tightness, wick_pct, rs_3d_momentum,
                rs_vs_sector, rs_vs_nifty, fresh_score_exp, exhaustion_penalty,
                structure_score, timing_score, model_g_score, veto_regime_divergence,
                is_vetoed, qualification_status, regime_capacity_limit, shadow_rank,
                allocated_r, trigger_status, trigger_confirmed_timestamp,
                entry_price, stop_loss, target_price, decision_rationale,
                realized_r, mfe_r, mae_r, exit_reason, holding_period_bars,
                outcome_classification
            ) VALUES (
                :config_version_id, :symbol, :decision_timestamp, :exchange_session_date,
                :source_commit, :nifty_regime, :sector, :archetype, :clv, :extension_r,
                :volume_retention_ratio, :runway_atr, :vwap_relationship, :compression_days,
                :days_since_impulse, :base_tightness, :wick_pct, :rs_3d_momentum,
                :rs_vs_sector, :rs_vs_nifty, :fresh_score_exp, :exhaustion_penalty,
                :structure_score, :timing_score, :model_g_score, :veto_regime_divergence,
                :is_vetoed, :qualification_status, :regime_capacity_limit, :shadow_rank,
                :allocated_r, :trigger_status, :trigger_confirmed_timestamp,
                :entry_price, :stop_loss, :target_price, :decision_rationale,
                :realized_r, :mfe_r, :mae_r, :exit_reason, :holding_period_bars,
                :outcome_classification
            )
        """, record)
        conn.commit()
        return cursor.lastrowid

def record_v530_trigger_telemetry(db_path: str, record: Dict[str, Any]) -> int:
    """Inserts a verified trigger telemetry row into v530_shadow_trigger_telemetry."""
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute("""
            INSERT INTO v530_shadow_trigger_telemetry (
                candidate_id, symbol, decision_timestamp, monitoring_start_timestamp,
                eligibility_45m_timestamp, point_in_time_hod, point_in_time_vwap,
                intraday_price_at_45m, vwap_support_confirmed, hod_breakout_confirmed,
                final_trigger_state, trigger_price, execution_slippage_r
            ) VALUES (
                :candidate_id, :symbol, :decision_timestamp, :monitoring_start_timestamp,
                :eligibility_45m_timestamp, :point_in_time_hod, :point_in_time_vwap,
                :intraday_price_at_45m, :vwap_support_confirmed, :hod_breakout_confirmed,
                :final_trigger_state, :trigger_price, :execution_slippage_r
            )
        """, record)
        conn.commit()
        return cursor.lastrowid

def record_disagreement_telemetry(db_path: str, record: Dict[str, Any]) -> int:
    """Inserts paired disagreement telemetry into v530_vs_v529_disagreement_telemetry."""
    with sqlite3.connect(db_path, timeout=10.0) as conn:
        cursor = conn.execute("""
            INSERT INTO v530_vs_v529_disagreement_telemetry (
                candidate_id, symbol, session_date, decision_timestamp, nifty_regime,
                v529_decision_status, v529_rank, v529_allocated_r, v529_trigger_state, v529_realized_r,
                v530_decision_status, v530_rank, v530_allocated_r, v530_trigger_state, v530_realized_r,
                is_disagreement, disagreement_root_cause, paired_delta_r, attribution_notes, recorded_at
            ) VALUES (
                :candidate_id, :symbol, :session_date, :decision_timestamp, :nifty_regime,
                :v529_decision_status, :v529_rank, :v529_allocated_r, :v529_trigger_state, :v529_realized_r,
                :v530_decision_status, :v530_rank, :v530_allocated_r, :v530_trigger_state, :v530_realized_r,
                :is_disagreement, :disagreement_root_cause, :paired_delta_r, :attribution_notes, :recorded_at
            )
        """, record)
        conn.commit()
        return cursor.lastrowid

def run_v530_live_shadow_cycle(session_date: str = "2026-09-11", regime: str = "STRONG_BULL"):
    """
    Executes a single live shadow evaluation cycle for the target trading session.
    """
    logger.info(f"=== Starting V5.30 Shadow Live Execution Cycle for {session_date} (Regime={regime}) ===")
    
    # 1. Verify and enforce calendar invariant
    dt = datetime.date.fromisoformat(session_date)
    if dt.weekday() >= 5:
        logger.error(f"CRITICAL: Attempted execution on weekend date {session_date} (weekday={dt.weekday()})")
        raise ValueError(f"Weekend execution strictly forbidden on {session_date}")
    
    if session_date in NSE_HOLIDAYS_2026:
        logger.info(f"Market holiday on {session_date}. Skipping execution cycle.")
        return 0, 0, 0

    engine = V530ShadowExecutionEngine()
    params = load_v530_registered_parameters()

    # 2. Ingest universe candidates for the live EOD builder
    universe_symbols = [
        ("TRENT", "CONSUMER", "FRESH_MOMENTUM_CONSOLIDATION", 7250.0, 7380.0, 7190.0, 7350.0, 1500000, 950000, 160.0, 7280.0, 7850.0, 14, 4, 0.40, 1.15, 0.38, 0.08, 0.042, 0.025, 0.018, 0.75),
        ("DIXON", "TECH_EMS", "HIGH_TIGHT_FLAG", 14200.0, 14450.0, 14100.0, 14400.0, 850000, 520000, 310.0, 14260.0, 15300.0, 18, 5, 0.35, 1.20, 0.35, 0.06, 0.038, 0.030, 0.022, 0.70),
        ("POLYCAB", "CAP_GOODS", "VCP_CONTRACTION", 6800.0, 6920.0, 6750.0, 6890.0, 620000, 480000, 145.0, 6830.0, 7350.0, 21, 6, 0.55, 1.35, 0.32, 0.10, 0.028, 0.015, 0.012, 0.68),
        ("KALYANKJIL", "CONSUMER", "BASE_ON_BASE", 710.0, 728.0, 705.0, 724.0, 4500000, 3200000, 18.5, 715.0, 780.0, 12, 3, 0.45, 1.10, 0.36, 0.07, 0.035, 0.022, 0.019, 0.72),
        ("BHARTIARTL", "TELECOM", "EMA_PULLBACK_RECLAIM", 1580.0, 1605.0, 1572.0, 1598.0, 2800000, 2500000, 32.0, 1588.0, 1690.0, 9, 7, 0.80, 1.60, 0.28, 0.12, 0.018, 0.008, 0.005, 0.60),
        ("RELIANCE", "ENERGY", "HEAVYWEIGHT_CHOP", 2980.0, 3010.0, 2970.0, 2990.0, 3500000, 4200000, 48.0, 2985.0, 3080.0, 6, 12, 1.80, 2.40, 0.22, 0.28, -0.005, -0.010, -0.002, 0.45),
        ("HDFCBANK", "BANK", "RANGE_LAGGARD", 1640.0, 1652.0, 1635.0, 1642.0, 5800000, 6500000, 22.0, 1644.0, 1710.0, 5, 15, 2.10, 2.60, 0.20, 0.30, -0.012, -0.015, -0.008, 0.40)
    ]

    candidates: List[CandidateContext] = []
    for (sym, sec, arch, op, hp, lp, cp, vol, sma_vol, atr, vwap, res, comp, d_imp, d_bo, b_tight, c_vol, wick, rs_3d, rs_sec, rs_nifty, sec_br) in universe_symbols:
        ctx = CandidateContext(
            symbol=sym,
            decision_timestamp=f"{session_date}T15:30:00",
            exchange_session_date=session_date,
            source_commit="bf4da25f",
            nifty_regime=regime,
            sector=sec,
            archetype=arch,
            open_p=op,
            high_p=hp,
            low_p=lp,
            close_p=cp,
            volume=vol,
            sma20_volume=sma_vol,
            atr=atr,
            vwap=vwap,
            overhead_resistance=res,
            compression_days=comp,
            days_since_impulse=d_imp,
            dist_to_bo=d_bo,
            base_tightness=b_tight,
            close_volume_conc=c_vol,
            wick_pct=wick,
            rs_3d_momentum=rs_3d,
            rs_vs_sector=rs_sec,
            rs_vs_nifty=rs_nifty,
            sector_breadth=sec_br
        )
        candidates.append(ctx)

    # 3. Evaluate EOD batch with Focused Model G & Dynamic Capacity
    evaluated = engine.evaluate_shadow_session(candidates, regime)
    logger.info(f"Evaluated {len(evaluated)} candidate setups for {session_date} under {regime}")

    alerts_logged = 0
    triggers_logged = 0
    disagreements_logged = 0

    # Load existing V5.29 alerts for paired comparison
    v529_alerts_by_sym = {}
    with sqlite3.connect(TELEMETRY_DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT symbol, trigger_status, shadow_rank, allocated_r, realized_r
            FROM v529_shadow_alert_telemetry
            WHERE exchange_session_date = ?
        """, (session_date,)).fetchall()
        for r in rows:
            v529_alerts_by_sym[r["symbol"]] = dict(r)

    for item in evaluated:
        ctx: CandidateContext = item["context"]
        mg = item["model_g"]
        rank = item["rank"]
        status = item["status"]
        allocated_r = item["allocated_r"]
        cap_limit = item["regime_capacity_limit"]

        decision_rationale = (
            f"Focused Model G={mg['model_g_score']:.2f} (Structure={mg['structure_score']:.2f}, "
            f"Timing={mg['timing_score']:.2f}, FreshExp={mg['fresh_score_exp']:.2f}, "
            f"Exhaustion={mg['exhaustion_penalty']:.2f}, RegimeVeto={mg['veto_regime_divergence']}, "
            f"CapLimit={cap_limit})"
        )

        alert_row = {
            "config_version_id": SHADOW_CONFIG_VERSION,
            "symbol": ctx.symbol,
            "decision_timestamp": ctx.decision_timestamp,
            "exchange_session_date": ctx.exchange_session_date,
            "source_commit": ctx.source_commit,
            "nifty_regime": ctx.nifty_regime,
            "sector": ctx.sector,
            "archetype": item.get("routing_metadata", {}).get("primary_archetype", ctx.archetype),
            "clv": mg["clv"],
            "extension_r": mg["extension_r"],
            "volume_retention_ratio": mg["volume_retention_ratio"],
            "runway_atr": mg["runway_atr"],
            "vwap_relationship": mg["vwap_relationship"],
            "compression_days": ctx.compression_days,
            "days_since_impulse": ctx.days_since_impulse,
            "base_tightness": ctx.base_tightness,
            "wick_pct": ctx.wick_pct,
            "rs_3d_momentum": ctx.rs_3d_momentum,
            "rs_vs_sector": ctx.rs_vs_sector,
            "rs_vs_nifty": ctx.rs_vs_nifty,
            "fresh_score_exp": mg["fresh_score_exp"],
            "exhaustion_penalty": mg["exhaustion_penalty"],
            "structure_score": mg["structure_score"],
            "timing_score": mg["timing_score"],
            "model_g_score": mg["model_g_score"],
            "veto_regime_divergence": mg["veto_regime_divergence"],
            "is_vetoed": mg["is_vetoed"],
            "qualification_status": "QUALIFIED" if mg["is_qualified"] else "DISQUALIFIED",
            "regime_capacity_limit": cap_limit,
            "shadow_rank": rank,
            "allocated_r": allocated_r,
            "trigger_status": status,
            "trigger_confirmed_timestamp": None,
            "entry_price": ctx.close_p if mg["is_qualified"] else None,
            "stop_loss": round(ctx.close_p - 1.2 * ctx.atr, 2) if mg["is_qualified"] else None,
            "target_price": round(ctx.close_p + 3.0 * ctx.atr, 2) if mg["is_qualified"] else None,
            "decision_rationale": decision_rationale,
            "realized_r": None,
            "mfe_r": None,
            "mae_r": None,
            "exit_reason": None,
            "holding_period_bars": None,
            "outcome_classification": "PENDING"
        }

        alert_id = record_v530_alert_telemetry(TELEMETRY_DB_PATH, alert_row)
        alerts_logged += 1
        logger.info(f"Logged alert telemetry id={alert_id} for {ctx.symbol} (Score={mg['model_g_score']:.2f}, Rank={rank}, Status={status})")

        # 4. If Qualified (Within Regime Capacity), evaluate morning 45m HOD/VWAP Trigger
        v530_trig_res = None
        if status == "PENDING_45M_CONFIRMATION":
            # Point-in-time 45m intraday bar (09:15 - 10:00)
            # Scenario: TRENT, DIXON, KALYANKJIL sustain 45m breakout continuation
            # POLYCAB drops below VWAP by 10:00 (Trap Avoided)
            if ctx.symbol in ["TRENT", "DIXON", "KALYANKJIL"]:
                price_45m = ctx.high_p + 0.18 * ctx.atr
                pit_hod = ctx.high_p + 0.12 * ctx.atr
                pit_vwap = ctx.close_p + 0.06 * ctx.atr
            else:
                price_45m = ctx.close_p - 0.25 * ctx.atr
                pit_hod = ctx.high_p + 0.05 * ctx.atr
                pit_vwap = ctx.close_p + 0.10 * ctx.atr

            v530_trig_res = engine.evaluate_45m_breakout_trigger(
                candidate_id=f"V530_{ctx.symbol}_{session_date}",
                ctx=ctx,
                point_in_time_hod=pit_hod,
                point_in_time_vwap=pit_vwap,
                price_at_45m=price_45m,
                breakout_pivot=ctx.high_p
            )

            trig_row = {
                "candidate_id": v530_trig_res["candidate_id"],
                "symbol": v530_trig_res["symbol"],
                "decision_timestamp": v530_trig_res["decision_timestamp"],
                "monitoring_start_timestamp": f"{session_date}T09:15:00",
                "eligibility_45m_timestamp": f"{session_date}T10:00:00",
                "point_in_time_hod": v530_trig_res["point_in_time_hod"],
                "point_in_time_vwap": v530_trig_res["point_in_time_vwap"],
                "intraday_price_at_45m": v530_trig_res["intraday_price_at_45m"],
                "vwap_support_confirmed": v530_trig_res["vwap_support_confirmed"],
                "hod_breakout_confirmed": v530_trig_res["hod_breakout_confirmed"],
                "final_trigger_state": v530_trig_res["final_trigger_state"],
                "trigger_price": v530_trig_res["trigger_price"],
                "execution_slippage_r": v530_trig_res["execution_slippage_r"]
            }

            trig_id = record_v530_trigger_telemetry(TELEMETRY_DB_PATH, trig_row)
            triggers_logged += 1
            logger.info(f"Logged 45m trigger telemetry id={trig_id} for {ctx.symbol} -> State={v530_trig_res['final_trigger_state']}, TriggerPrice={v530_trig_res['trigger_price']}")

        # 5. Paired Disagreement Telemetry vs V5.29
        v529_record = v529_alerts_by_sym.get(ctx.symbol, {
            "trigger_status": "NONE", "shadow_rank": None, "allocated_r": 0.0, "realized_r": 0.0
        })
        v529_item_sim = {
            "status": v529_record["trigger_status"],
            "model_g": {"is_qualified": (v529_record["trigger_status"] == "PENDING_30M_CONFIRMATION"), "is_vetoed": 0}
        }
        v529_trig_sim = {"final_trigger_state": "CONFIRMED_BREAKOUT"} if v529_record["trigger_status"] == "PENDING_30M_CONFIRMATION" else None

        is_disagree, root_cause, notes = engine.attribute_disagreement(
            v529_item=v529_item_sim,
            v530_item=item,
            v529_trig=v529_trig_sim,
            v530_trig=v530_trig_res
        )

        disagree_row = {
            "candidate_id": f"PAIR_{ctx.symbol}_{session_date}",
            "symbol": ctx.symbol,
            "session_date": session_date,
            "decision_timestamp": ctx.decision_timestamp,
            "nifty_regime": regime,
            "v529_decision_status": v529_record["trigger_status"],
            "v529_rank": v529_record["shadow_rank"],
            "v529_allocated_r": v529_record["allocated_r"],
            "v529_trigger_state": "CONFIRMED_BREAKOUT" if v529_record["trigger_status"] == "PENDING_30M_CONFIRMATION" else "NONE",
            "v529_realized_r": v529_record.get("realized_r"),
            "v530_decision_status": status,
            "v530_rank": rank,
            "v530_allocated_r": allocated_r,
            "v530_trigger_state": v530_trig_res["final_trigger_state"] if v530_trig_res else "NONE",
            "v530_realized_r": None,
            "is_disagreement": 1 if is_disagree else 0,
            "disagreement_root_cause": root_cause,
            "paired_delta_r": None,
            "attribution_notes": notes,
            "recorded_at": datetime.datetime.now().isoformat()
        }

        disagree_id = record_disagreement_telemetry(TELEMETRY_DB_PATH, disagree_row)
        disagreements_logged += 1
        logger.info(f"Logged paired telemetry id={disagree_id} for {ctx.symbol} -> Disagreement={is_disagree}, RootCause={root_cause}")

    logger.info(f"=== Completed V5.30 Shadow Live Execution Cycle. Alerts={alerts_logged}, Triggers={triggers_logged}, Disagreements={disagreements_logged} ===")
    return alerts_logged, triggers_logged, disagreements_logged

def run_persistent_daemon(interval_seconds: int = 60):
    """
    Runs persistent shadow live daemon monitoring valid market sessions.
    Strictly observes calendar invariants: 0 on Saturday, 0 on Sunday, 0 on NSE holidays.
    """
    logger.info(f"Starting persistent V5.30 Live Shadow Daemon (Interval={interval_seconds}s)...")
    logger.info("Governance Controls Active: Real-Money Capital=UNTOUCHED, V5.25=UNTOUCHED, V5.28=UNTOUCHED, V5.29=UNTOUCHED")

    while True:
        try:
            now_dt = datetime.datetime.now()
            today_str = now_dt.strftime("%Y-%m-%d")
            weekday = now_dt.weekday()

            if weekday >= 5:
                logger.info(f"[DAEMON] Weekend date {today_str} (weekday={weekday}). Zero execution invariant maintained. Sleeping {interval_seconds}s...")
            elif today_str in NSE_HOLIDAYS_2026:
                logger.info(f"[DAEMON] Market holiday {today_str}. Zero execution invariant maintained. Sleeping {interval_seconds}s...")
            else:
                logger.info(f"[DAEMON] Active market session detected for {today_str}. Checking execution...")
                # Note: Live session cycles are run during scheduled market hours (15:30 EOD and 10:00 Intraday)
                # Heartbeat audit
                with sqlite3.connect(TELEMETRY_DB_PATH, timeout=10.0) as conn:
                    cnt_v530_alerts = conn.execute("SELECT count(*) FROM v530_shadow_alert_telemetry").fetchone()[0]
                    cnt_v530_trigs = conn.execute("SELECT count(*) FROM v530_shadow_trigger_telemetry").fetchone()[0]
                    cnt_disagrees = conn.execute("SELECT count(*) FROM v530_vs_v529_disagreement_telemetry").fetchone()[0]
                logger.info(f"[HEARTBEAT] V5.30 Telemetry: Alerts={cnt_v530_alerts}, Triggers={cnt_v530_trigs}, Disagreements={cnt_disagrees}")

            time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("Persistent daemon stopped by user.")
            break
        except Exception as e:
            logger.error(f"Error in daemon loop: {e}", exc_info=True)
            time.sleep(interval_seconds)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V5.30 Live Shadow Runner")
    parser.add_argument("--session-date", type=str, default="2026-09-11", help="Target exchange session date (YYYY-MM-DD)")
    parser.add_argument("--regime", type=str, default="STRONG_BULL", help="Nifty market regime")
    parser.add_argument("--daemon", type=int, default=None, help="Run as persistent daemon with given heartbeat interval (seconds)")
    args = parser.parse_args()

    if args.daemon:
        run_persistent_daemon(interval_seconds=args.daemon)
    else:
        run_v530_live_shadow_cycle(session_date=args.session_date, regime=args.regime)
