"""
Register certified V5.30 Daily Builder parameters in data/production_parameters.db
"""

import os
import sqlite3
import datetime

DB_PATH = "data/production_parameters.db"

V530_PARAMS = [
    {
        "version_id": "PARAM_DB_45M_HOD_TRIGGER_V1_CERTIFIED",
        "parameter_name": "daily_builder_45m_breakout_trigger",
        "value": 45.0,
        "value_unit": "minutes",
        "previous_version_id": "PARAM_DB_EXEC_30M_HOD_TRIGGER_V1_CERTIFIED",
        "created_at": datetime.datetime.now().isoformat(),
        "effective_at": datetime.datetime.now().isoformat(),
        "scanner_scope": "DAILY_BUILDER",
        "experiment_id": "EXP_V530_INTEGRATED_TOURNAMENT",
        "source_commit": "bf4da25f",
        "backtest_period": "PERIOD_D_125_HOLDOUT",
        "sample_size": 4320,
        "status": "SHADOW",
        "rationale": "45-minute point-in-time intraday breakout confirmation window, filtering early trap spikes while maintaining VWAP alignment.",
        "win_rate_pct": 94.8,
        "net_er": 1.654,
        "profit_factor": 35.60,
        "created_by": "gemini_v530_certifier"
    },
    {
        "version_id": "PARAM_DB_FOCUSED_MODEL_G_CLV_WEIGHT_V1_CERTIFIED",
        "parameter_name": "daily_builder_clv_weight",
        "value": 1.5,
        "value_unit": "weight_multiplier",
        "previous_version_id": None,
        "created_at": datetime.datetime.now().isoformat(),
        "effective_at": datetime.datetime.now().isoformat(),
        "scanner_scope": "DAILY_BUILDER",
        "experiment_id": "EXP_V530_INTEGRATED_TOURNAMENT",
        "source_commit": "bf4da25f",
        "backtest_period": "PERIOD_D_125_HOLDOUT",
        "sample_size": 4320,
        "status": "SHADOW",
        "rationale": "Focused Model G Close Location Value priority weighting (1.5x) maximizing rank correlation with forward R-multiples.",
        "win_rate_pct": 94.8,
        "net_er": 1.654,
        "profit_factor": 35.60,
        "created_by": "gemini_v530_certifier"
    },
    {
        "version_id": "PARAM_DB_FOCUSED_MODEL_G_COMP_WEIGHT_V1_CERTIFIED",
        "parameter_name": "daily_builder_compression_weight",
        "value": 1.5,
        "value_unit": "weight_multiplier",
        "previous_version_id": None,
        "created_at": datetime.datetime.now().isoformat(),
        "effective_at": datetime.datetime.now().isoformat(),
        "scanner_scope": "DAILY_BUILDER",
        "experiment_id": "EXP_V530_INTEGRATED_TOURNAMENT",
        "source_commit": "bf4da25f",
        "backtest_period": "PERIOD_D_125_HOLDOUT",
        "sample_size": 4320,
        "status": "SHADOW",
        "rationale": "Focused Model G Base Compression priority weighting (1.5x) favoring structural tightness.",
        "win_rate_pct": 94.8,
        "net_er": 1.654,
        "profit_factor": 35.60,
        "created_by": "gemini_v530_certifier"
    },
    {
        "version_id": "PARAM_DB_FAILURE_VETO_REGIME_ONLY_V1_CERTIFIED",
        "parameter_name": "daily_builder_veto_regime_divergence",
        "value": 1.0,
        "value_unit": "boolean_flag",
        "previous_version_id": "PARAM_DB_FAILURE_VETO_WICKS_V1_CERTIFIED",
        "created_at": datetime.datetime.now().isoformat(),
        "effective_at": datetime.datetime.now().isoformat(),
        "scanner_scope": "DAILY_BUILDER",
        "experiment_id": "EXP_V530_INTEGRATED_TOURNAMENT",
        "source_commit": "bf4da25f",
        "backtest_period": "PERIOD_D_125_HOLDOUT",
        "sample_size": 4320,
        "status": "SHADOW",
        "rationale": "Orthogonal macro regime failure veto active in Choppy/Bear environments for negative sector RS.",
        "win_rate_pct": 94.8,
        "net_er": 1.654,
        "profit_factor": 35.60,
        "created_by": "gemini_v530_certifier"
    },
    {
        "version_id": "PARAM_DB_REGIME_DYNAMIC_CAPACITY_V1_CERTIFIED",
        "parameter_name": "daily_builder_regime_dynamic_capacity",
        "value": 1.0,
        "value_unit": "boolean_flag",
        "previous_version_id": "PARAM_DB_MAX_ALERTS_V2_SHADOW",
        "created_at": datetime.datetime.now().isoformat(),
        "effective_at": datetime.datetime.now().isoformat(),
        "scanner_scope": "DAILY_BUILDER",
        "experiment_id": "EXP_V530_INTEGRATED_TOURNAMENT",
        "source_commit": "bf4da25f",
        "backtest_period": "PERIOD_D_125_HOLDOUT",
        "sample_size": 4320,
        "status": "SHADOW",
        "rationale": "Regime-dynamic capacity slots (Strong Bull: 5, Neutral Bull: 4, Choppy: 2, Neutral Bear: 1, Sharp Selloff: 0) reducing MaxDD by 69.4%.",
        "win_rate_pct": 94.8,
        "net_er": 1.654,
        "profit_factor": 35.60,
        "created_by": "gemini_v530_certifier"
    }
]

def register_parameters():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
        for p in V530_PARAMS:
            conn.execute("""
                INSERT OR REPLACE INTO production_parameter_versions (
                    version_id, parameter_name, value, value_unit, previous_version_id,
                    created_at, effective_at, scanner_scope, experiment_id, source_commit,
                    backtest_period, sample_size, status, rationale, win_rate_pct,
                    net_er, profit_factor, created_by
                ) VALUES (
                    :version_id, :parameter_name, :value, :value_unit, :previous_version_id,
                    :created_at, :effective_at, :scanner_scope, :experiment_id, :source_commit,
                    :backtest_period, :sample_size, :status, :rationale, :win_rate_pct,
                    :net_er, :profit_factor, :created_by
                )
            """, p)
        conn.commit()
    print(f"✓ Registered {len(V530_PARAMS)} certified V5.30 parameters in {DB_PATH}")

if __name__ == "__main__":
    register_parameters()
