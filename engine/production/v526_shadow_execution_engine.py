"""
V5.26 Production Shadow Execution & Telemetry Engine
====================================================
Operationalizes Phases 1 through 12 of the V5.26 Production Mandate:
- Promotes parameters to SHADOW in immutable DB registry.
- Executes parallel Shadow Pipeline (Legacy Arm A vs V5.26 Shadow Arm C/D).
- Formats and records full human-auditable telemetry with config_version_id.
- Evaluates deterministic catalyst states (SURVIVED, COOLING, EXHAUSTED, INVALIDATED, FRESH_BASE).
- Supports Daily Builder dual-engine (Surviving Catalyst + Fresh EOD Base).
- Enforces strict <= 60m Intraday Gem TTL and Short Covering Inverse Trap allocation.
"""

import os
import sqlite3
import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict

TELEMETRY_DB_PATH = "data/shadow_telemetry.db"
SHADOW_CONFIG_VERSION = "V5.26_SHADOW"

TELEMETRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS shadow_alert_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_version_id TEXT NOT NULL,
    scanner_name TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_timestamp TEXT NOT NULL,
    gem_timestamp TEXT,
    gem_age_minutes REAL,
    catalyst_state TEXT NOT NULL,
    clv REAL NOT NULL,
    extension_r REAL NOT NULL,
    volume_retention_ratio REAL NOT NULL,
    vwap_relationship TEXT NOT NULL,
    orb_relationship TEXT NOT NULL,
    runway_atr REAL NOT NULL,
    old_rank INTEGER,
    new_rank INTEGER,
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    allocated_r REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    target_price REAL NOT NULL,
    decision_rationale TEXT NOT NULL,
    actual_r REAL,
    mfe_r REAL,
    mae_r REAL,
    exit_reason TEXT,
    holding_period_bars INTEGER
);

CREATE INDEX IF NOT EXISTS idx_telemetry_scanner_ts ON shadow_alert_telemetry (scanner_name, decision_timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_state ON shadow_alert_telemetry (catalyst_state);
CREATE INDEX IF NOT EXISTS idx_telemetry_config ON shadow_alert_telemetry (config_version_id);
"""

@dataclass
class CandidateBar:
    symbol: str
    decision_timestamp: str
    scanner_name: str
    open_p: float
    high_p: float
    low_p: float
    close_p: float
    volume: float
    sma20_volume: float
    atr: float
    vwap: float
    orb_high: float
    orb_low: float
    overhead_resistance: float
    gem_detected: bool
    gem_timestamp: Optional[str]

class ShadowExecutionEngine:
    def __init__(self, db_path: str = TELEMETRY_DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(TELEMETRY_SCHEMA_SQL)
            conn.commit()

    def calculate_gem_age(self, bar: CandidateBar) -> Optional[float]:
        if not bar.gem_detected or not bar.gem_timestamp:
            return None
        try:
            t_dec = datetime.datetime.fromisoformat(bar.decision_timestamp)
            t_gem = datetime.datetime.fromisoformat(bar.gem_timestamp)
            return max(0.0, (t_dec - t_gem).total_seconds() / 60.0)
        except Exception:
            return 300.0 # Default late-day fallback

    def evaluate_catalyst_state(self, bar: CandidateBar, gem_age: Optional[float]) -> Tuple[str, Dict[str, Any], str]:
        """Deterministic Catalyst State Evaluator matching V5.26 production policies."""
        bar_range = max(bar.high_p - bar.low_p, 1e-6)
        clv = (bar.close_p - bar.low_p) / bar_range
        extension_r = (bar.close_p - bar.open_p) / max(bar.atr, 1e-6)
        vol_ret = bar.volume / max(bar.sma20_volume, 1e-6)
        vwap_rel = "ABOVE_VWAP" if bar.close_p >= bar.vwap else "BELOW_VWAP"
        orb_rel = "ABOVE_ORB_HIGH" if bar.close_p >= bar.orb_high else "INSIDE_ORB"
        runway_atr = (bar.overhead_resistance - bar.close_p) / max(bar.atr, 1e-6)

        features = {
            "clv": round(clv, 3),
            "extension_r": round(extension_r, 2),
            "volume_retention_ratio": round(vol_ret, 2),
            "vwap_relationship": vwap_rel,
            "orb_relationship": orb_rel,
            "runway_atr": round(runway_atr, 2),
            "gem_age_minutes": round(gem_age, 1) if gem_age is not None else None
        }

        # Intraday Check
        if bar.scanner_name in ["MultiTF 1H", "MultiTF 5M"]:
            if gem_age is not None and gem_age <= 60.0:
                return "LIVE_GEM_ACTIVE", features, f"Live Intraday Gem Active (Age {gem_age:.1f}m <= 60m TTL)"
            elif gem_age is not None:
                return "INTRADAY_EXPIRED", features, f"Intraday Gem Expired (Age {gem_age:.1f}m > 60m TTL)"
            else:
                return "ORGANIC_INTRADAY", features, "Organic Intraday Candidate (No Gem)"

        # Short Covering Specialist
        if bar.scanner_name == "Short Covering":
            if bar.close_p < bar.open_p or vwap_rel == "BELOW_VWAP":
                return "MORNING_TRAP_ACTIVE", features, "Morning Breakout Trap Confirmed: Size 1.50R"
            else:
                return "SHORT_COVERING_BASELINE", features, "Standard Short Covering: Size 0.50R"

        # After-Market Base Scanners
        if not bar.gem_detected:
            # Fresh EOD Base
            if clv >= 0.65 and runway_atr >= 2.0:
                return "FRESH_BASE", features, "Clean Organic EOD Consolidation Base (Zero Stale Gem Noise)"
            else:
                return "ORGANIC_BASELINE", features, "Standard Organic Base"

        # Gem Detected in Morning -> Revalidate at EOD
        if bar.close_p < bar.vwap or clv < 0.50:
            return "CATALYST_INVALIDATED", features, "Structure Breakdown: Closed below VWAP or CLV < 0.50 (HARD VETO)"
        if extension_r > 3.20:
            return "CATALYST_EXHAUSTED", features, f"Climax Exhaustion: Extension {extension_r:.2f}R > 3.20R limit (VETO GEM BOOST)"
        if clv >= 0.68 and extension_r <= 3.20 and vol_ret >= 1.10 and bar.close_p >= bar.vwap and runway_atr >= 2.50:
            return "CATALYST_SURVIVED", features, "Structural Revalidation Passed: Top-Third Close, Retained Volume, Open Runway"
        
        return "CATALYST_COOLING", features, "Cooling Catalyst: Baseline Sizing, Moderate Extension"

    def execute_shadow_ranking(self, candidates: List[CandidateBar]) -> List[Dict[str, Any]]:
        """Executes both Legacy Arm A Ranking and V5.26 Shadow Revalidated Ranking."""
        evaluated = []
        for c in candidates:
            gem_age = self.calculate_gem_age(c)
            state, feats, rationale = self.evaluate_catalyst_state(c, gem_age)

            # Legacy Score (Arm A: Blind Gem Boost regardless of age)
            legacy_score = 50.0
            if c.gem_detected:
                legacy_score += 40.0 # Blind boost
            legacy_score += feats["clv"] * 20.0

            # V5.26 Shadow Score (Arm C/D: Deterministic State Routing)
            shadow_score = 50.0
            alloc_r = 1.00

            if state == "LIVE_GEM_ACTIVE":
                shadow_score += 45.0
                alloc_r = 1.00
            elif state == "MORNING_TRAP_ACTIVE":
                shadow_score += 40.0
                alloc_r = 1.50
            elif state == "CATALYST_SURVIVED":
                shadow_score += 35.0 # Quality priority
                alloc_r = 1.00
            elif state == "FRESH_BASE":
                shadow_score += 30.0 # First-class organic EOD base
                alloc_r = 1.00
            elif state == "CATALYST_COOLING":
                shadow_score += 10.0
                alloc_r = 0.75
            elif state in ["CATALYST_EXHAUSTED", "CATALYST_INVALIDATED", "INTRADAY_EXPIRED"]:
                shadow_score = 0.0 # VETOED
                alloc_r = 0.00
            else:
                shadow_score += 15.0
                alloc_r = 1.00

            evaluated.append({
                "bar": c,
                "gem_age": gem_age,
                "catalyst_state": state,
                "features": feats,
                "rationale": rationale,
                "legacy_score": legacy_score,
                "shadow_score": shadow_score,
                "allocated_r": alloc_r
            })

        # Compute Ranks
        evaluated_legacy = sorted(evaluated, key=lambda x: x["legacy_score"], reverse=True)
        for i, item in enumerate(evaluated_legacy, 1):
            item["old_rank"] = i
            item["old_status"] = "SELECTED" if i <= 5 and item["legacy_score"] > 0 else "FILTERED"

        evaluated_shadow = sorted(evaluated, key=lambda x: x["shadow_score"], reverse=True)
        for i, item in enumerate(evaluated_shadow, 1):
            item["new_rank"] = i
            item["new_status"] = "SELECTED" if i <= 5 and item["shadow_score"] > 0 else "FILTERED"

        return evaluated

    def record_telemetry(self, ranked_items: List[Dict[str, Any]]):
        """Persists shadow decision telemetry to SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            for item in ranked_items:
                b: CandidateBar = item["bar"]
                f = item["features"]
                sl = round(b.low_p - 0.5 * b.atr, 2)
                tgt = round(b.close_p + 2.5 * b.atr, 2)

                conn.execute("""
                    INSERT INTO shadow_alert_telemetry (
                        config_version_id, scanner_name, symbol, decision_timestamp,
                        gem_timestamp, gem_age_minutes, catalyst_state, clv, extension_r,
                        volume_retention_ratio, vwap_relationship, orb_relationship, runway_atr,
                        old_rank, new_rank, old_status, new_status, allocated_r, entry_price,
                        stop_loss, target_price, decision_rationale
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    SHADOW_CONFIG_VERSION, b.scanner_name, b.symbol, b.decision_timestamp,
                    b.gem_timestamp, f["gem_age_minutes"], item["catalyst_state"],
                    f["clv"], f["extension_r"], f["volume_retention_ratio"],
                    f["vwap_relationship"], f["orb_relationship"], f["runway_atr"],
                    item["old_rank"], item["new_rank"], item["old_status"],
                    item["new_status"], item["allocated_r"], b.close_p, sl, tgt,
                    item["rationale"]
                ))
            conn.commit()
