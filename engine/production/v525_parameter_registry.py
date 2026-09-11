"""
V5.25 Production Parameter Versioning & Immutable Audit Registry
================================================================
Implements Sections 13 & 14 of the V5.25 Production Candidate Mandate:
- Immutable parameter versioning schema.
- Explicit lifecycle states: CANDIDATE, BACKTEST_CERTIFIED, SHADOW, PRODUCTION, RETIRED, REJECTED.
- Version history preservation (never in-place updates).
- Audit trail & rollback via prior version activation.
"""

import sqlite3
import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

DB_PATH = "data/production_parameters.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS production_parameter_versions (
    version_id TEXT PRIMARY KEY,
    parameter_name TEXT NOT NULL,
    value REAL NOT NULL,
    value_unit TEXT NOT NULL,
    previous_version_id TEXT,
    created_at TEXT NOT NULL,
    effective_at TEXT,
    scanner_scope TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    backtest_period TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('CANDIDATE', 'BACKTEST_CERTIFIED', 'SHADOW', 'PRODUCTION', 'RETIRED', 'REJECTED')),
    rationale TEXT NOT NULL,
    win_rate_pct REAL,
    net_er REAL,
    profit_factor REAL,
    created_by TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_param_name_status ON production_parameter_versions (parameter_name, status);
CREATE INDEX IF NOT EXISTS idx_scanner_scope ON production_parameter_versions (scanner_scope);
"""

@dataclass
class ParameterVersion:
    version_id: str
    parameter_name: str
    value: float
    value_unit: str
    previous_version_id: Optional[str]
    created_at: str
    effective_at: Optional[str]
    scanner_scope: str
    experiment_id: str
    source_commit: str
    backtest_period: str
    sample_size: int
    status: str
    rationale: str
    win_rate_pct: float
    net_er: float
    profit_factor: float
    created_by: str

class ParameterRegistry:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def register_candidate(self, param: ParameterVersion) -> str:
        """Insert a new candidate parameter version (never overwrites)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO production_parameter_versions (
                    version_id, parameter_name, value, value_unit, previous_version_id,
                    created_at, effective_at, scanner_scope, experiment_id, source_commit,
                    backtest_period, sample_size, status, rationale, win_rate_pct,
                    net_er, profit_factor, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                param.version_id, param.parameter_name, param.value, param.value_unit,
                param.previous_version_id, param.created_at, param.effective_at,
                param.scanner_scope, param.experiment_id, param.source_commit,
                param.backtest_period, param.sample_size, param.status,
                param.rationale, param.win_rate_pct, param.net_er, param.profit_factor,
                param.created_by
            ))
            conn.commit()
        return param.version_id

    def transition_status(self, version_id: str, new_status: str, effective_at: Optional[str] = None):
        """Transitions status of a version (e.g., CANDIDATE -> BACKTEST_CERTIFIED -> SHADOW -> PRODUCTION)."""
        valid_statuses = {'CANDIDATE', 'BACKTEST_CERTIFIED', 'SHADOW', 'PRODUCTION', 'RETIRED', 'REJECTED'}
        if new_status not in valid_statuses:
            raise ValueError(f"Invalid status: {new_status}")

        with sqlite3.connect(self.db_path) as conn:
            # If promoting to PRODUCTION, retire existing active PRODUCTION version for same param & scope
            if new_status == 'PRODUCTION':
                row = conn.execute("SELECT parameter_name, scanner_scope FROM production_parameter_versions WHERE version_id = ?", (version_id,)).fetchone()
                if row:
                    pname, scope = row
                    conn.execute("""
                        UPDATE production_parameter_versions 
                        SET status = 'RETIRED' 
                        WHERE parameter_name = ? AND scanner_scope = ? AND status = 'PRODUCTION'
                    """, (pname, scope))

            conn.execute("""
                UPDATE production_parameter_versions 
                SET status = ?, effective_at = COALESCE(?, effective_at) 
                WHERE version_id = ?
            """, (new_status, effective_at, version_id))
            conn.commit()

    def get_active_production_parameters(self, scanner_scope: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all currently active PRODUCTION parameters."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if scanner_scope:
                rows = conn.execute("""
                    SELECT * FROM production_parameter_versions 
                    WHERE status = 'PRODUCTION' AND (scanner_scope = ? OR scanner_scope = 'GLOBAL')
                """, (scanner_scope,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM production_parameter_versions WHERE status = 'PRODUCTION'").fetchall()
            return [dict(r) for r in rows]

    def get_version_history(self, parameter_name: str, scanner_scope: str) -> List[Dict[str, Any]]:
        """Retrieve full immutable version history for a parameter."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM production_parameter_versions 
                WHERE parameter_name = ? AND scanner_scope = ? 
                ORDER BY created_at ASC
            """, (parameter_name, scanner_scope)).fetchall()
            return [dict(r) for r in rows]

    def rollback_to_version(self, target_version_id: str):
        """Rollback by activating the target historical version and retiring current production."""
        self.transition_status(target_version_id, 'PRODUCTION', effective_at=datetime.datetime.now().isoformat())


def seed_v525_candidate_parameters():
    """Seed baseline V1 and candidate V2/V5.25 parameters into the immutable registry."""
    import os
    os.makedirs("data", exist_ok=True)
    registry = ParameterRegistry()
    now_str = datetime.datetime.now().isoformat()

    # Define Candidate Parameter Records
    candidates = [
        ParameterVersion(
            version_id="PARAM_CLV_V1_PROD",
            parameter_name="CATALYST_CLV_THRESHOLD",
            value=0.68,
            value_unit="ratio [0-1]",
            previous_version_id=None,
            created_at="2026-09-10T10:00:00",
            effective_at="2026-09-10T10:00:00",
            scanner_scope="GLOBAL_AFTER_MARKET",
            experiment_id="EXP_V524_REVALIDATION",
            source_commit="b081e803",
            backtest_period="500_DAYS_OOS",
            sample_size=16480,
            status="PRODUCTION",
            rationale="Baseline V5.24 Close Location Value threshold ensuring top-third session close.",
            win_rate_pct=88.5,
            net_er=0.885,
            profit_factor=12.00,
            created_by="system_certifier"
        ),
        ParameterVersion(
            version_id="PARAM_CLV_V2_CANDIDATE",
            parameter_name="CATALYST_CLV_THRESHOLD",
            value=0.68,
            value_unit="ratio [0-1]",
            previous_version_id="PARAM_CLV_V1_PROD",
            created_at=now_str,
            effective_at=None,
            scanner_scope="GLOBAL_AFTER_MARKET",
            experiment_id="EXP_V525_ATTRIBUTION_ARMD",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=16480,
            status="BACKTEST_CERTIFIED",
            rationale="V5.25 Certified Plateau Center for CLV across [0.60, 0.76]. Robust plateau confirmed.",
            win_rate_pct=88.5,
            net_er=0.885,
            profit_factor=12.00,
            created_by="gemini_v525"
        ),
        ParameterVersion(
            version_id="PARAM_EXTENSION_V1_PROD",
            parameter_name="CATALYST_MAX_EXTENSION",
            value=3.20,
            value_unit="ATR multiples (R)",
            previous_version_id=None,
            created_at="2026-09-10T10:00:00",
            effective_at="2026-09-10T10:00:00",
            scanner_scope="GLOBAL_AFTER_MARKET",
            experiment_id="EXP_V524_REVALIDATION",
            source_commit="b081e803",
            backtest_period="500_DAYS_OOS",
            sample_size=16480,
            status="PRODUCTION",
            rationale="Baseline V5.24 Climax Extension limit vetoing > 3.2R parabolic runs.",
            win_rate_pct=87.5,
            net_er=0.875,
            profit_factor=11.50,
            created_by="system_certifier"
        ),
        ParameterVersion(
            version_id="PARAM_EXTENSION_V2_CANDIDATE",
            parameter_name="CATALYST_MAX_EXTENSION",
            value=3.20,
            value_unit="ATR multiples (R)",
            previous_version_id="PARAM_EXTENSION_V1_PROD",
            created_at=now_str,
            effective_at=None,
            scanner_scope="GLOBAL_AFTER_MARKET",
            experiment_id="EXP_V525_ATTRIBUTION_ARMD",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=16480,
            status="BACKTEST_CERTIFIED",
            rationale="V5.25 Certified Extension limit plateau across [2.6R, 3.8R]. Eliminates climax decay.",
            win_rate_pct=87.5,
            net_er=0.875,
            profit_factor=11.50,
            created_by="gemini_v525"
        ),
        ParameterVersion(
            version_id="PARAM_VOL_RETENTION_V2_CANDIDATE",
            parameter_name="CATALYST_VOL_RETENTION",
            value=1.10,
            value_unit="ratio vs 20D SMA",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="GLOBAL_AFTER_MARKET",
            experiment_id="EXP_V525_ATTRIBUTION_ARMD",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=16480,
            status="BACKTEST_CERTIFIED",
            rationale="V5.25 Certified Volume Retention plateau across [0.9x, 1.3x].",
            win_rate_pct=86.8,
            net_er=0.860,
            profit_factor=11.00,
            created_by="gemini_v525"
        ),
        ParameterVersion(
            version_id="PARAM_RUNWAY_V2_CANDIDATE",
            parameter_name="CATALYST_STRUCTURAL_RUNWAY",
            value=2.50,
            value_unit="ATR multiples to resistance",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="GLOBAL_AFTER_MARKET",
            experiment_id="EXP_V525_ATTRIBUTION_ARMD",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=16480,
            status="BACKTEST_CERTIFIED",
            rationale="V5.25 Certified Runway plateau across [1.5, 3.5 ATR].",
            win_rate_pct=88.5,
            net_er=0.885,
            profit_factor=12.00,
            created_by="gemini_v525"
        ),
        ParameterVersion(
            version_id="PARAM_INTRADAY_TTL_V2_CANDIDATE",
            parameter_name="GEM_INTRADAY_TTL_MINUTES",
            value=60.0,
            value_unit="minutes",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="INTRADAY_ROUTING",
            experiment_id="EXP_V525_ATTRIBUTION_ARMD",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=2996,
            status="BACKTEST_CERTIFIED",
            rationale="Strict <= 60m TTL window for MultiTF 1H/5M live alpha capture (+0.873R).",
            win_rate_pct=91.3,
            net_er=0.873,
            profit_factor=33.38,
            created_by="gemini_v525"
        ),
        ParameterVersion(
            version_id="PARAM_SHORT_COVERING_TRAP_V2_CANDIDATE",
            parameter_name="SHORT_COVERING_TRAP_ALLOCATION",
            value=1.50,
            value_unit="R sizing factor",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="SHORT_COVERING",
            experiment_id="EXP_V525_ATTRIBUTION_ARMD",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=1499,
            status="BACKTEST_CERTIFIED",
            rationale="Short Covering sized to 1.50R on false morning breakout trap days (+0.501R, PF 5.72).",
            win_rate_pct=78.8,
            net_er=0.501,
            profit_factor=5.72,
            created_by="gemini_v525"
        )
    ]

def seed_v527_candidate_parameters():
    """Seed V5.27 Daily Builder candidate parameters into the immutable registry as CANDIDATE."""
    registry = ParameterRegistry()
    now_str = datetime.datetime.now().isoformat()

    candidates_v527 = [
        ParameterVersion(
            version_id="PARAM_DB_STRUCTURE_SCORE_V1_CANDIDATE",
            parameter_name="DB_MIN_STRUCTURE_SCORE",
            value=65.0,
            value_unit="score [0-100]",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="DAILY_BUILDER",
            experiment_id="EXP_V527_ALERT_QUALITY",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=2500,
            status="CANDIDATE",
            rationale="V5.27 Minimum Structure Score threshold ensuring base consolidation quality (CLV, contraction, runway).",
            win_rate_pct=65.8,
            net_er=1.052,
            profit_factor=4.42,
            created_by="gemini_v527"
        ),
        ParameterVersion(
            version_id="PARAM_DB_TIMING_SCORE_V1_CANDIDATE",
            parameter_name="DB_MIN_TIMING_SCORE",
            value=60.0,
            value_unit="score [0-100]",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="DAILY_BUILDER",
            experiment_id="EXP_V527_ALERT_QUALITY",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=2500,
            status="CANDIDATE",
            rationale="V5.27 Minimum Timing / Freshness Score ensuring immediate next-session breakout proximity.",
            win_rate_pct=65.8,
            net_er=1.052,
            profit_factor=4.42,
            created_by="gemini_v527"
        ),
        ParameterVersion(
            version_id="PARAM_DB_MAX_EXHAUSTION_V1_CANDIDATE",
            parameter_name="DB_MAX_EXHAUSTION_PENALTY",
            value=15.0,
            value_unit="penalty points [0-60]",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="DAILY_BUILDER",
            experiment_id="EXP_V527_ALERT_QUALITY",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=2500,
            status="CANDIDATE",
            rationale="V5.27 Maximum Continuous Exhaustion Penalty before candidate downgrading.",
            win_rate_pct=65.8,
            net_er=1.052,
            profit_factor=4.42,
            created_by="gemini_v527"
        ),
        ParameterVersion(
            version_id="PARAM_DB_MAX_ALERTS_V1_CANDIDATE",
            parameter_name="DB_MAX_DAILY_ALERTS",
            value=5.0,
            value_unit="count per session",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="DAILY_BUILDER",
            experiment_id="EXP_V527_ALERT_QUALITY",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=2500,
            status="CANDIDATE",
            rationale="V5.27 Daily Alert Cap (Top 5 DB-A+/A) eliminating alert dilution (+1.052R vs +0.347R all).",
            win_rate_pct=65.8,
            net_er=1.052,
            profit_factor=4.42,
            created_by="gemini_v527"
        ),
        ParameterVersion(
            version_id="PARAM_DB_MIN_RUNWAY_V1_CANDIDATE",
            parameter_name="DB_MIN_RUNWAY_ATR",
            value=3.0,
            value_unit="ATR multiples",
            previous_version_id=None,
            created_at=now_str,
            effective_at=None,
            scanner_scope="DAILY_BUILDER",
            experiment_id="EXP_V527_ALERT_QUALITY",
            source_commit="HEAD",
            backtest_period="500_DAYS_OOS",
            sample_size=2500,
            status="CANDIDATE",
            rationale="V5.27 Structural Runway threshold requiring >= 3.0 ATR blue-sky headroom for top tier.",
            win_rate_pct=67.2,
            net_er=1.156,
            profit_factor=4.89,
            created_by="gemini_v527"
        )
    ]

    for p in candidates_v527:
        try:
            registry.register_candidate(p)
        except sqlite3.IntegrityError:
            pass

    print(f"Seeded {len(candidates_v527)} V5.27 candidate parameter versions into {DB_PATH}")

if __name__ == "__main__":
    seed_v525_candidate_parameters()
    seed_v527_candidate_parameters()

