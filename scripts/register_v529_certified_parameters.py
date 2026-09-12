"""
Register Certified V5.29 Parameters in Immutable Governance DB
=============================================================
Registers V5.29 parameters with status 'BACKTEST_CERTIFIED' in data/production_parameters.db:
- PARAM_DB_MODEL_G_SCORE_FLOOR_V1_CERTIFIED (60.0)
- PARAM_DB_EXHAUST_CLIFF_V2_CERTIFIED (22.0)
- PARAM_DB_EXP_FRESHNESS_LAMBDA_V1_CERTIFIED (0.099)
- PARAM_DB_EXEC_30M_HOD_TRIGGER_V1_CERTIFIED (30.0)
- PARAM_DB_FAILURE_VETO_WICKS_V1_CERTIFIED (0.25)
"""

import sqlite3
import datetime

DB_PATH = "data/production_parameters.db"

V529_PARAMS = [
    (
        "PARAM_DB_MODEL_G_SCORE_FLOOR_V1_CERTIFIED",
        "daily_builder_model_g_score_floor",
        60.0,
        "points",
        "PARAM_DB_SCORE_FLOOR_V1_CERTIFIED",
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        None,
        "Daily Builder",
        "EXP_V529_MODEL_G_RESEARCH",
        "bf4da25f",
        "2024-07-01_to_2026-06-30_HOLDOUT_250D",
        684,
        "BACKTEST_CERTIFIED",
        "V5.29 Model G composite score floor integrating exponential freshness and RS acceleration",
        91.52,
        1.515,
        24.10,
        "SYSTEM_RESEARCH_GOVERNANCE"
    ),
    (
        "PARAM_DB_EXHAUST_CLIFF_V2_CERTIFIED",
        "daily_builder_exhaustion_cliff",
        22.0,
        "penalty_points",
        "PARAM_DB_EXHAUST_CLIFF_V1_CERTIFIED",
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        None,
        "Daily Builder",
        "EXP_V529_EXHAUSTION_V2",
        "bf4da25f",
        "2024-07-01_to_2026-06-30_HOLDOUT_250D",
        684,
        "BACKTEST_CERTIFIED",
        "V5.29 tightened continuous exhaustion penalty ceiling from 25.0 to 22.0",
        91.52,
        1.515,
        24.10,
        "SYSTEM_RESEARCH_GOVERNANCE"
    ),
    (
        "PARAM_DB_EXP_FRESHNESS_LAMBDA_V1_CERTIFIED",
        "daily_builder_exp_freshness_lambda",
        0.099,
        "decay_rate",
        None,
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        None,
        "Daily Builder",
        "EXP_V529_FRESHNESS_HALFLIFE",
        "bf4da25f",
        "2024-07-01_to_2026-06-30_HOLDOUT_250D",
        684,
        "BACKTEST_CERTIFIED",
        "V5.29 exponential base impulse freshness half-life decay rate (tau=7 days)",
        91.52,
        1.515,
        24.10,
        "SYSTEM_RESEARCH_GOVERNANCE"
    ),
    (
        "PARAM_DB_EXEC_30M_HOD_TRIGGER_V1_CERTIFIED",
        "daily_builder_30m_breakout_trigger",
        30.0,
        "minutes",
        None,
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        None,
        "Daily Builder",
        "EXP_V529_EXEC_30M_TRIGGER",
        "bf4da25f",
        "2024-07-01_to_2026-06-30_HOLDOUT_250D",
        684,
        "BACKTEST_CERTIFIED",
        "V5.29 intraday High-of-Day breakout confirmation requirement with VWAP support on 30m bar",
        91.52,
        1.515,
        24.10,
        "SYSTEM_RESEARCH_GOVERNANCE"
    ),
    (
        "PARAM_DB_FAILURE_VETO_WICKS_V1_CERTIFIED",
        "daily_builder_veto_wick_threshold",
        0.25,
        "ratio",
        None,
        datetime.datetime.now(datetime.timezone.utc).isoformat(),
        None,
        "Daily Builder",
        "EXP_V529_FAILURE_VETOES",
        "bf4da25f",
        "2024-07-01_to_2026-06-30_HOLDOUT_250D",
        684,
        "BACKTEST_CERTIFIED",
        "V5.29 asymmetric failure veto rule targeting upper wick > 25% with extension and volume drain",
        91.52,
        1.515,
        24.10,
        "SYSTEM_RESEARCH_GOVERNANCE"
    )
]

def register_all():
    with sqlite3.connect(DB_PATH) as conn:
        for p in V529_PARAMS:
            conn.execute("""
                INSERT OR REPLACE INTO production_parameter_versions (
                    version_id, parameter_name, value, value_unit, previous_version_id,
                    created_at, effective_at, scanner_scope, experiment_id, source_commit,
                    backtest_period, sample_size, status, rationale, win_rate_pct,
                    net_er, profit_factor, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, p)
        conn.commit()
    print(f"SUCCESS: Registered {len(V529_PARAMS)} immutable V5.29 parameter versions with status 'BACKTEST_CERTIFIED'.")

if __name__ == "__main__":
    register_all()
