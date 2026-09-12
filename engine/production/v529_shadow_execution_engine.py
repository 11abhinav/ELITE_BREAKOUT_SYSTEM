"""
V5.29 Production Shadow Execution & Telemetry Engine
====================================================
Implements the certified V5.29 Daily Builder Shadow Execution Architecture:
1. Model G Composite Ranking Engine (Exponential Freshness + RS Acceleration + Tail-Risk Penalty)
2. Three Asymmetric Failure Veto Rules (Wick Drain, Loose Base, Regime Divergence)
3. 30-Minute Breakout Trigger Confirmation (HOD Breakout with Intraday VWAP Support)
4. Dynamic Natural 0-5 Selection (Regime Cap permanently unbundled/excluded)
5. Zero Production Mutation: Operates in isolated shadow telemetry tables
6. Calendar & Lookahead Invariant Protections (0 Weekend Candles, Point-in-Time HOD/VWAP)
"""

import os
import math
import sqlite3
import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict

TELEMETRY_DB_PATH = "data/shadow_telemetry.db"
SHADOW_CONFIG_VERSION = "V5.29_DB_SHADOW"

V529_TELEMETRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v529_shadow_alert_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_version_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    exchange_session_date TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    nifty_regime TEXT NOT NULL,
    sector TEXT NOT NULL,
    archetype TEXT NOT NULL,
    clv REAL NOT NULL,
    extension_r REAL NOT NULL,
    volume_retention_ratio REAL NOT NULL,
    runway_atr REAL NOT NULL,
    vwap_relationship TEXT NOT NULL,
    compression_days INTEGER NOT NULL,
    days_since_impulse INTEGER NOT NULL,
    base_tightness REAL NOT NULL,
    wick_pct REAL NOT NULL,
    rs_3d_momentum REAL NOT NULL,
    rs_vs_sector REAL NOT NULL,
    rs_vs_nifty REAL NOT NULL,
    fresh_score_exp REAL NOT NULL,
    exhaustion_penalty REAL NOT NULL,
    structure_score REAL NOT NULL,
    timing_score REAL NOT NULL,
    model_g_score REAL NOT NULL,
    veto_wick_drain INTEGER NOT NULL,
    veto_loose_base INTEGER NOT NULL,
    veto_regime_divergence INTEGER NOT NULL,
    is_vetoed INTEGER NOT NULL,
    qualification_status TEXT NOT NULL,
    shadow_rank INTEGER,
    allocated_r REAL NOT NULL,
    trigger_status TEXT NOT NULL,
    trigger_confirmed_timestamp TEXT,
    entry_price REAL,
    stop_loss REAL,
    target_price REAL,
    decision_rationale TEXT NOT NULL,
    realized_r REAL,
    mfe_r REAL,
    mae_r REAL,
    exit_reason TEXT,
    holding_period_bars INTEGER,
    outcome_classification TEXT CHECK(outcome_classification IN ('CORRECT_AVOID', 'FALSE_AVOID', 'CORRECT_PROMOTE', 'BAD_PROMOTE', 'CONCURRING_WIN', 'CONCURRING_LOSS', 'UNCONFIRMED_TRAP_AVOIDED', 'PENDING'))
);

CREATE TABLE IF NOT EXISTS v529_shadow_trigger_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    monitoring_start_timestamp TEXT NOT NULL,
    eligibility_30m_timestamp TEXT NOT NULL,
    point_in_time_hod REAL NOT NULL,
    point_in_time_vwap REAL NOT NULL,
    intraday_price_at_30m REAL NOT NULL,
    vwap_support_confirmed INTEGER NOT NULL,
    hod_breakout_confirmed INTEGER NOT NULL,
    final_trigger_state TEXT NOT NULL,
    trigger_price REAL,
    execution_slippage_r REAL
);

CREATE INDEX IF NOT EXISTS idx_v529_symbol_ts ON v529_shadow_alert_telemetry (symbol, decision_timestamp);
CREATE INDEX IF NOT EXISTS idx_v529_config ON v529_shadow_alert_telemetry (config_version_id);
CREATE INDEX IF NOT EXISTS idx_v529_outcome ON v529_shadow_alert_telemetry (outcome_classification);
"""

@dataclass
class CandidateContext:
    symbol: str
    decision_timestamp: str
    exchange_session_date: str
    source_commit: str
    nifty_regime: str
    sector: str
    archetype: str
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    volume: float
    sma20_volume: float
    atr: float
    vwap: float
    overhead_resistance: float
    compression_days: int
    days_since_impulse: int
    dist_to_bo: float
    base_tightness: float
    close_volume_conc: float
    wick_pct: float
    rs_3d_momentum: float
    rs_vs_sector: float
    rs_vs_nifty: float
    sector_breadth: float

class V529ShadowExecutionEngine:
    def __init__(self, db_path: str = TELEMETRY_DB_PATH, commit_hash: str = "bf4da25f"):
        self.db_path = db_path
        self.commit_hash = commit_hash
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(V529_TELEMETRY_SCHEMA_SQL)
            conn.commit()

    def assert_calendar_invariant(self, date_str: str):
        """Hard invariant: Saturday and Sunday candles are strictly prohibited."""
        dt = datetime.date.fromisoformat(date_str)
        if dt.weekday() >= 5:
            raise ValueError(f"CRITICAL GOVERNANCE VIOLATION: Ingestion of weekend bar on {date_str} (weekday={dt.weekday()}).")

    def compute_model_g_score(self, ctx: CandidateContext) -> Dict[str, Any]:
        """Calculates exact certified Model G composite score and veto rules."""
        self.assert_calendar_invariant(ctx.exchange_session_date)

        # 1. Base Ratios
        bar_range = max(ctx.high_p - ctx.low_p, 1e-6)
        clv = (ctx.close_p - ctx.low_p) / bar_range
        extension_r = (ctx.close_p - ctx.open_p) / max(ctx.atr, 1e-6)
        vol_ret = ctx.volume / max(ctx.sma20_volume, 1e-6)
        vwap_rel = "ABOVE_VWAP" if ctx.close_p >= ctx.vwap else "BELOW_VWAP"
        runway_atr = (ctx.overhead_resistance - ctx.close_p) / max(ctx.atr, 1e-6)

        # 2. Breakout Readiness
        if ctx.dist_to_bo <= 0.5 and ctx.base_tightness <= 1.5 and runway_atr >= 3.0:
            readiness_score = 90.0
        elif ctx.dist_to_bo <= 1.2 and ctx.base_tightness <= 2.2 and runway_atr >= 2.2:
            readiness_score = 70.0
        elif extension_r > 3.0 or ctx.dist_to_bo > 2.0:
            readiness_score = 25.0
        else:
            readiness_score = 40.0

        # 3. Exponential Freshness Decay (Lambda = 0.099, 7-day half life)
        lambda_decay = 0.099
        fresh_score_exp = min(100.0, max(0.0, (
            (1.0 - math.exp(-ctx.compression_days / 10.0)) * 40.0 +
            math.exp(-lambda_decay * ctx.days_since_impulse) * 35.0 +
            max(0.0, 1.0 - (ctx.base_tightness / 2.5)) * 25.0
        )))

        # 4. Continuous Sigmoid Exhaustion Penalty
        p_ext = max(0.0, (extension_r - 2.20) * 18.0)
        p_wick = max(0.0, (1.0 - clv) * 25.0)
        p_runway = max(0.0, (3.0 - runway_atr) * 12.0)
        exhaustion_penalty = min(80.0, p_ext + p_wick + p_runway)
        exhaust_dampener = 1.0 / (1.0 + math.exp((exhaustion_penalty - 20.0) / 6.0))

        # 5. Structure & Timing Components
        s_base = min(ctx.compression_days / 15.0, 1.0) * 20.0
        s_clv = clv * 30.0
        s_run = min(runway_atr / 4.0, 1.0) * 25.0
        s_vol = min(vol_ret / 1.5, 1.0) * 25.0
        structure_score = s_base + s_clv + s_run + s_vol

        t_bo = (readiness_score / 100.0) * 30.0
        t_fresh = (fresh_score_exp / 100.0) * 35.0
        t_vwap = 20.0 if vwap_rel == "ABOVE_VWAP" else 0.0
        t_vol_conc = min(ctx.close_volume_conc / 0.4, 1.0) * 15.0
        timing_score = t_bo + t_fresh + t_vwap + t_vol_conc

        # 6. Sector & Regime Context Multipliers
        sec_score = 50.0
        if ctx.rs_vs_nifty > 0: sec_score += 15.0
        if ctx.sector_breadth > 0.65: sec_score += 15.0
        if ctx.rs_vs_sector > 0: sec_score += 20.0
        sec_score = min(100.0, max(0.0, sec_score))
        sec_factor = (sec_score / 100.0) * 0.30 + 0.70

        if ctx.nifty_regime == "STRONG_BULL": mkt_factor = 1.15
        elif ctx.nifty_regime == "NEUTRAL_BULL": mkt_factor = 1.05
        elif ctx.nifty_regime == "CHOPPY_RANGE": mkt_factor = 0.90
        elif ctx.nifty_regime == "NEUTRAL_BEAR": mkt_factor = 0.70
        else: mkt_factor = 0.00 # Strict zero emission in SHARP_SELLOFF

        # 7. RS Acceleration Bonus & Tail-Risk Penalty
        rs_mom_bonus = max(0.0, min(12.0, (ctx.rs_3d_momentum / 0.03) * 10.0))
        tail_risk_pen = max(0.0, (ctx.wick_pct - 0.20) * 35.0) + max(0.0, (ctx.base_tightness - 1.5) * 15.0)

        # 8. Model G Composite
        raw_g = ((structure_score * 0.40 + timing_score * 0.40 + rs_mom_bonus - tail_risk_pen) * exhaust_dampener * sec_factor * mkt_factor)
        if vwap_rel == "BELOW_VWAP" or clv < 0.58 or extension_r > 3.00:
            raw_g = 0.0
        model_g_score = round(max(0.0, raw_g), 2)

        # 9. Asymmetric Failure Vetoes
        veto_wick = (ctx.wick_pct > 0.25 and extension_r > 2.50 and vol_ret < 1.20)
        veto_loose = (ctx.base_tightness > 2.0 and ctx.compression_days < 7)
        veto_chop_lag = (ctx.rs_vs_sector < 0 and ctx.nifty_regime in ["CHOPPY_RANGE", "NEUTRAL_BEAR"])
        is_vetoed = veto_wick or veto_loose or veto_chop_lag

        # 10. Qualification Gate (Score >= 60.0, Exhaustion <= 22.0, Vetoes = False)
        is_qualified = (model_g_score >= 60.0 and exhaustion_penalty <= 22.0 and not is_vetoed and ctx.nifty_regime != "SHARP_SELLOFF")

        return {
            "clv": round(clv, 3),
            "extension_r": round(extension_r, 2),
            "volume_retention_ratio": round(vol_ret, 2),
            "runway_atr": round(runway_atr, 2),
            "vwap_relationship": vwap_rel,
            "readiness_score": round(readiness_score, 1),
            "fresh_score_exp": round(fresh_score_exp, 1),
            "exhaustion_penalty": round(exhaustion_penalty, 2),
            "structure_score": round(structure_score, 2),
            "timing_score": round(timing_score, 2),
            "model_g_score": model_g_score,
            "veto_wick_drain": 1 if veto_wick else 0,
            "veto_loose_base": 1 if veto_loose else 0,
            "veto_regime_divergence": 1 if veto_chop_lag else 0,
            "is_vetoed": 1 if is_vetoed else 0,
            "is_qualified": is_qualified
        }

    def evaluate_shadow_session(self, candidates: List[CandidateContext]) -> List[Dict[str, Any]]:
        """Evaluates a full EOD session candidate batch and assigns natural 0-5 ranking."""
        evaluated = []
        for ctx in candidates:
            res = self.compute_model_g_score(ctx)
            evaluated.append({
                "context": ctx,
                "model_g": res,
                "score": res["model_g_score"] if res["is_qualified"] else 0.0
            })

        # Rank descending by Model G score
        evaluated_sorted = sorted(evaluated, key=lambda x: x["score"], reverse=True)
        for rank, item in enumerate(evaluated_sorted, 1):
            item["rank"] = rank
            if item["model_g"]["is_qualified"] and rank <= 5:
                item["status"] = "PENDING_30M_CONFIRMATION"
                item["allocated_r"] = 1.00
            else:
                item["status"] = "FILTERED"
                item["allocated_r"] = 0.00

        return evaluated_sorted

    def evaluate_30m_breakout_trigger(
        self,
        candidate_id: str,
        ctx: CandidateContext,
        point_in_time_hod: float,
        point_in_time_vwap: float,
        price_at_30m: float,
        breakout_pivot: float
    ) -> Dict[str, Any]:
        """
        Evaluates the 30-Minute Breakout Trigger confirmation on morning session T+1.
        Requires:
        1. Price at 30m >= point_in_time_vwap (VWAP Support)
        2. Price at 30m >= breakout_pivot / HOD (Breakout Continuation)
        """
        vwap_supported = (price_at_30m >= point_in_time_vwap)
        hod_confirmed = (price_at_30m >= breakout_pivot)

        is_confirmed = (vwap_supported and hod_confirmed)
        
        if is_confirmed:
            trigger_state = "CONFIRMED_BREAKOUT"
            trigger_price = max(breakout_pivot, price_at_30m)
            slippage_r = 0.08 # Standard execution friction
        else:
            trigger_state = "UNCONFIRMED_TRAP_AVOIDED"
            trigger_price = None
            slippage_r = 0.00

        return {
            "candidate_id": candidate_id,
            "symbol": ctx.symbol,
            "decision_timestamp": ctx.decision_timestamp,
            "point_in_time_hod": point_in_time_hod,
            "point_in_time_vwap": point_in_time_vwap,
            "intraday_price_at_30m": price_at_30m,
            "vwap_support_confirmed": 1 if vwap_supported else 0,
            "hod_breakout_confirmed": 1 if hod_confirmed else 0,
            "final_trigger_state": trigger_state,
            "trigger_price": trigger_price,
            "execution_slippage_r": slippage_r
        }
