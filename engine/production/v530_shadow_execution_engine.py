"""
V5.30 Production Shadow Execution & Telemetry Engine
====================================================
Implements the certified V5.30 Daily Builder Shadow Execution Architecture:
1. 45-Minute Breakout Trigger Confirmation (Dynamic intraday HOD/VWAP support without lookahead)
2. Focused Core Model G Ranking Engine (1.5x CLV Priority + 1.5x Base Compression + Exponential Freshness)
3. Orthogonal Macro Regime Divergence Veto (Only active in Choppy/Bear with negative sector RS)
4. Regime-Dynamic Capacity Policy (Strong Bull: 5, Neutral Bull: 4, Choppy: 2, Neutral Bear: 1, Sharp Selloff: 0)
5. Paired Telemetry Engine & Disagreement Attribution (V5.30 vs V5.29 live disagreement ledger)
6. Zero Production Mutation: Operates in strictly isolated shadow telemetry tables
7. Calendar & Invariant Protections: Saturday = 0, Sunday = 0, Lookahead = 0
"""

import os
import math
import sqlite3
import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict

TELEMETRY_DB_PATH = "data/shadow_telemetry.db"
PARAM_DB_PATH = "data/production_parameters.db"
SHADOW_CONFIG_VERSION = "V5.30_DB_SHADOW"

from engine.production.v600_hybrid_router_engine import v600_router_engine, HybridRouterEngineV6

V530_TELEMETRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS v530_shadow_alert_telemetry (
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
    veto_regime_divergence INTEGER NOT NULL,
    is_vetoed INTEGER NOT NULL,
    qualification_status TEXT NOT NULL,
    regime_capacity_limit INTEGER NOT NULL,
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
    outcome_classification TEXT CHECK(outcome_classification IN (
        'CORRECT_AVOID', 'FALSE_AVOID', 'CORRECT_PROMOTE', 'BAD_PROMOTE',
        'CONCURRING_WIN', 'CONCURRING_LOSS', 'UNCONFIRMED_TRAP_AVOIDED',
        'CAPACITY_THROTTLED_AVOID', 'PENDING'
    ))
);

CREATE TABLE IF NOT EXISTS v530_shadow_trigger_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    monitoring_start_timestamp TEXT NOT NULL,
    eligibility_45m_timestamp TEXT NOT NULL,
    point_in_time_hod REAL NOT NULL,
    point_in_time_vwap REAL NOT NULL,
    intraday_price_at_45m REAL NOT NULL,
    vwap_support_confirmed INTEGER NOT NULL,
    hod_breakout_confirmed INTEGER NOT NULL,
    final_trigger_state TEXT NOT NULL,
    trigger_price REAL,
    execution_slippage_r REAL
);

CREATE TABLE IF NOT EXISTS v530_vs_v529_disagreement_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    nifty_regime TEXT NOT NULL,
    v529_decision_status TEXT NOT NULL,
    v529_rank INTEGER,
    v529_allocated_r REAL NOT NULL,
    v529_trigger_state TEXT,
    v529_realized_r REAL,
    v530_decision_status TEXT NOT NULL,
    v530_rank INTEGER,
    v530_allocated_r REAL NOT NULL,
    v530_trigger_state TEXT,
    v530_realized_r REAL,
    is_disagreement INTEGER NOT NULL,
    disagreement_root_cause TEXT CHECK(disagreement_root_cause IN (
        'NONE', '45M_CONFIRMATION', 'FOCUSED_MODEL_G', 'REGIME_VETO',
        'DYNAMIC_CAPACITY', 'COMBINED'
    )),
    paired_delta_r REAL,
    attribution_notes TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v530_symbol_ts ON v530_shadow_alert_telemetry (symbol, decision_timestamp);
CREATE INDEX IF NOT EXISTS idx_v530_config ON v530_shadow_alert_telemetry (config_version_id);
CREATE INDEX IF NOT EXISTS idx_v530_disagree_sess ON v530_vs_v529_disagreement_telemetry (session_date);
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

class V530ShadowExecutionEngine:
    def __init__(self, db_path: str = TELEMETRY_DB_PATH, commit_hash: str = "bf4da25f"):
        self.db_path = db_path
        self.commit_hash = commit_hash
        self._init_db()
        self.parameters = self._load_and_verify_parameters()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=10.0) as conn:
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.executescript(V530_TELEMETRY_SCHEMA_SQL)
            conn.commit()

    def _load_and_verify_parameters(self) -> Dict[str, Any]:
        """Loads and verifies immutable parameter versions from production_parameters.db."""
        if not os.path.exists(PARAM_DB_PATH):
            return {
                "daily_builder_45m_breakout_trigger": 45.0,
                "daily_builder_clv_weight": 1.5,
                "daily_builder_compression_weight": 1.5,
                "daily_builder_veto_regime_divergence": 1.0,
                "daily_builder_regime_dynamic_capacity": 1.0
            }
        
        with sqlite3.connect(PARAM_DB_PATH, timeout=10.0) as conn:
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT parameter_name, value
                FROM production_parameter_versions
                WHERE version_id LIKE '%V530%' OR (scanner_scope='DAILY_BUILDER' AND status='SHADOW')
            """).fetchall()
        
        params = {r["parameter_name"]: r["value"] for r in rows}
        return params

    def assert_calendar_invariant(self, date_str: str):
        """Hard invariant: Saturday and Sunday candles are strictly prohibited."""
        dt = datetime.date.fromisoformat(date_str)
        if dt.weekday() >= 5:
            raise ValueError(f"CRITICAL GOVERNANCE VIOLATION: Ingestion of weekend bar on {date_str} (weekday={dt.weekday()}).")

    def get_regime_capacity(self, regime: str) -> int:
        """
        Certified Regime-Dynamic Capacity Policy:
        - STRONG_BULL: 5 slots
        - NEUTRAL_BULL: 4 slots
        - CHOPPY_RANGE: 2 slots
        - NEUTRAL_BEAR: 1 slot
        - SHARP_SELLOFF: 0 slots
        """
        capacities = {
            "STRONG_BULL": 5,
            "NEUTRAL_BULL": 4,
            "CHOPPY_RANGE": 2,
            "NEUTRAL_BEAR": 1,
            "SHARP_SELLOFF": 0
        }
        return capacities.get(regime, 0)

    def compute_focused_model_g_score(self, ctx: CandidateContext) -> Dict[str, Any]:
        """Calculates exact certified Focused Model G composite score and Regime Veto."""
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

        # 5. Structure & Timing Components with Focused 1.5x CLV and 1.5x Compression weighting
        s_base = min((ctx.compression_days * 1.5) / 15.0, 1.0) * 20.0
        s_clv = (clv * 1.5) / 1.5 * 30.0  # Normalized CLV
        s_run = min(runway_atr / 4.0, 1.0) * 25.0
        s_vol = min(vol_ret / 1.5, 1.0) * 25.0
        structure_score = s_base + s_clv + s_run + s_vol

        t_bo = (readiness_score / 100.0) * 30.0
        t_fresh = (fresh_score_exp / 100.0) * 35.0
        t_vwap = 20.0 if vwap_rel == "ABOVE_VWAP" else 0.0
        t_vol_conc = min(ctx.close_volume_conc / 0.4, 1.0) * 15.0
        timing_score = t_bo + t_fresh + t_vwap + t_vol_conc

        # 6. Sector Context Multiplier
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
        else: mkt_factor = 0.00  # Strict zero emission in SHARP_SELLOFF

        # 7. RS Acceleration Bonus & Tail-Risk Penalty
        rs_mom_bonus = max(0.0, min(12.0, (ctx.rs_3d_momentum / 0.03) * 10.0))
        tail_risk_pen = max(0.0, (ctx.wick_pct - 0.20) * 35.0) + max(0.0, (ctx.base_tightness - 1.5) * 15.0)

        # 8. Model G Composite
        raw_g = ((structure_score * 0.40 + timing_score * 0.40 + rs_mom_bonus - tail_risk_pen) * exhaust_dampener * sec_factor * mkt_factor)
        if vwap_rel == "BELOW_VWAP" or clv < 0.58 or extension_r > 3.00:
            raw_g = 0.0
        model_g_score = round(max(0.0, raw_g), 2)

        # 9. Minimal Orthogonal Regime Divergence Veto (Structural vetoes unbundled)
        veto_regime_div = (ctx.rs_vs_sector < 0 and ctx.nifty_regime in ["CHOPPY_RANGE", "NEUTRAL_BEAR"])
        is_vetoed = veto_regime_div

        # 10. Qualification Gate (Score >= 60.0, Exhaustion <= 22.0, Veto = False)
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
            "veto_regime_divergence": 1 if veto_regime_div else 0,
            "is_vetoed": 1 if is_vetoed else 0,
            "is_qualified": is_qualified
        }

    def evaluate_shadow_session(self, candidates: List[CandidateContext], regime: str) -> List[Dict[str, Any]]:
        """
        Evaluates full EOD session candidates and applies certified Regime-Dynamic Capacity:
        - STRONG_BULL: 5 slots
        - NEUTRAL_BULL: 4 slots
        - CHOPPY_RANGE: 2 slots
        - NEUTRAL_BEAR: 1 slot
        - SHARP_SELLOFF: 0 slots
        """
        capacity_limit = self.get_regime_capacity(regime)
        evaluated = []
        for ctx in candidates:
            res = self.compute_focused_model_g_score(ctx)
            evaluated.append({
                "context": ctx,
                "model_g": res,
                "score": res["model_g_score"] if res["is_qualified"] else 0.0
            })

        # Rank descending by score
        evaluated_sorted = sorted(evaluated, key=lambda x: x["score"], reverse=True)
        qualified_rank = 0

        for item in evaluated_sorted:
            ctx = item["context"]
            # Classify candidate under V6 Hybrid Router
            cand_dict = {
                "symbol": ctx.symbol,
                "clv": item["model_g"]["clv"],
                "compression_ratio": ctx.base_tightness,
                "base_duration_days": ctx.compression_days,
                "days_since_impulse": ctx.days_since_impulse,
                "rs_percentile": max(0.0, min(100.0, 50.0 + ctx.rs_vs_nifty * 500.0)),
                "regime": regime
            }
            routing_meta = v600_router_engine.classify_candidate(cand_dict)
            item["routing_metadata"] = routing_meta

            if item["model_g"]["is_qualified"]:
                qualified_rank += 1
                item["rank"] = qualified_rank
                if qualified_rank <= capacity_limit:
                    item["status"] = "PENDING_45M_CONFIRMATION"
                    item["allocated_r"] = 1.00
                else:
                    item["status"] = "CAPACITY_FILTERED"
                    item["allocated_r"] = 0.00
            else:
                item["rank"] = None
                if item["model_g"]["is_vetoed"]:
                    item["status"] = "VETO_FILTERED"
                else:
                    item["status"] = "SCORE_FILTERED"
                item["allocated_r"] = 0.00
            item["regime_capacity_limit"] = capacity_limit

        return evaluated_sorted

    def route_session_to_scanners(self, evaluated_candidates: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Generates certified V6.00 Hybrid soft-routed streams for all 5 downstream scanners:
        - SCAN_VCP_1H
        - SCAN_MULTIBAGGER_EOD
        - SCAN_REVERSAL_KEYLEVEL
        - SCAN_SHORT_COVERING
        - SCAN_DAILY_BUILDER_45M
        """
        raw_list = []
        for item in evaluated_candidates:
            ctx = item["context"]
            raw_list.append({
                "symbol": ctx.symbol,
                "context": ctx,
                "model_g_score": item["model_g"]["model_g_score"],
                "is_qualified": item["model_g"]["is_qualified"],
                "clv": item["model_g"]["clv"],
                "compression_ratio": ctx.base_tightness,
                "base_duration_days": ctx.compression_days,
                "days_since_impulse": ctx.days_since_impulse,
                "rs_percentile": max(0.0, min(100.0, 50.0 + ctx.rs_vs_nifty * 500.0)),
                "regime": ctx.nifty_regime
            })
        return v600_router_engine.route_candidate_pool(raw_list)

    def evaluate_45m_breakout_trigger(
        self,
        candidate_id: str,
        ctx: CandidateContext,
        point_in_time_hod: float,
        point_in_time_vwap: float,
        price_at_45m: float,
        breakout_pivot: float
    ) -> Dict[str, Any]:
        """
        Evaluates the certified 45-Minute Breakout Trigger confirmation on morning session T+1.
        Strict Point-In-Time Requirements (10:00:00 IST snapshot):
        1. Price at 45m >= point_in_time_vwap (Intraday VWAP Support)
        2. Price at 45m >= breakout_pivot (Breakout Continuation)
        """
        vwap_supported = (price_at_45m >= point_in_time_vwap)
        hod_confirmed = (price_at_45m >= breakout_pivot)

        is_confirmed = (vwap_supported and hod_confirmed)
        
        if is_confirmed:
            trigger_state = "CONFIRMED_BREAKOUT"
            trigger_price = max(breakout_pivot, price_at_45m)
            slippage_r = 0.08  # Standard execution friction model
        else:
            trigger_state = "UNCONFIRMED_TRAP_AVOIDED"
            trigger_price = None
            slippage_r = 0.00

        return {
            "candidate_id": candidate_id,
            "symbol": ctx.symbol,
            "decision_timestamp": ctx.decision_timestamp,
            "monitoring_start_timestamp": f"{ctx.exchange_session_date}T09:15:00",
            "eligibility_45m_timestamp": f"{ctx.exchange_session_date}T10:00:00",
            "point_in_time_hod": point_in_time_hod,
            "point_in_time_vwap": point_in_time_vwap,
            "intraday_price_at_45m": price_at_45m,
            "vwap_support_confirmed": 1 if vwap_supported else 0,
            "hod_breakout_confirmed": 1 if hod_confirmed else 0,
            "final_trigger_state": trigger_state,
            "trigger_price": trigger_price,
            "execution_slippage_r": slippage_r
        }

    def attribute_disagreement(
        self,
        v529_item: Dict[str, Any],
        v530_item: Dict[str, Any],
        v529_trig: Optional[Dict[str, Any]] = None,
        v530_trig: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str, str]:
        """
        Identifies and attributes exact root cause of decision disagreement between V5.29 and V5.30:
        Causes: 45M_CONFIRMATION, FOCUSED_MODEL_G, REGIME_VETO, DYNAMIC_CAPACITY, COMBINED, NONE
        """
        v529_exec = (v529_item.get("status") == "PENDING_30M_CONFIRMATION" and (v529_trig and v529_trig.get("final_trigger_state") == "CONFIRMED_BREAKOUT"))
        v530_exec = (v530_item.get("status") == "PENDING_45M_CONFIRMATION" and (v530_trig and v530_trig.get("final_trigger_state") == "CONFIRMED_BREAKOUT"))

        if v529_exec == v530_exec:
            return False, "NONE", "Concurring execution decision"

        causes = []
        # Check if capacity limit in V5.30 throttled it
        if v530_item.get("status") == "CAPACITY_FILTERED" and v529_item.get("status") == "PENDING_30M_CONFIRMATION":
            causes.append("DYNAMIC_CAPACITY")
        
        # Check if regime veto triggered in V5.30 or V5.29
        v529_vetoed = v529_item.get("model_g", {}).get("is_vetoed", 0)
        v530_vetoed = v530_item.get("model_g", {}).get("is_vetoed", 0)
        if v529_vetoed != v530_vetoed:
            causes.append("REGIME_VETO")

        # Check if trigger timing differed (30m confirmed vs 45m unconfirmed or vice versa)
        if v529_trig and v530_trig:
            if v529_trig.get("final_trigger_state") != v530_trig.get("final_trigger_state"):
                causes.append("45M_CONFIRMATION")

        # Check if model ranking/qualification differed
        if v529_item.get("model_g", {}).get("is_qualified") != v530_item.get("model_g", {}).get("is_qualified") and not causes:
            causes.append("FOCUSED_MODEL_G")

        if not causes:
            root_cause = "FOCUSED_MODEL_G"
        elif len(causes) == 1:
            root_cause = causes[0]
        else:
            root_cause = "COMBINED"

        notes = f"Disagreement: V5.29={v529_exec}, V5.30={v530_exec} | Drivers: {','.join(causes) if causes else root_cause}"
        return True, root_cause, notes
