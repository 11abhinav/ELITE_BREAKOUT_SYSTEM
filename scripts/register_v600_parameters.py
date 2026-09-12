#!/usr/bin/env python3
"""
Register Daily Builder V6.00 Hybrid Router into Production Database under Governance V2.
"""

import os
import sys
import sqlite3
import json
import datetime

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(BASE_DIR, "data/production_parameters.db")
JSON_PATH = os.path.join(BASE_DIR, "data/v600_production_frozen_registry.json")

def register_v600():
    now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+05:30")
    
    # 1. Update SQLite parameters DB
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # Insert V6.00 router parameters
    v6_params = [
        ("V600_ROUTING_MODE", "ROUTING_MODE", 1.0, "HYBRID_PRIORITY", "V530_PROD_MODE", now_str, now_str, "ALL_SCANNERS", "EXP_V6_ALL_SCANNER_TOURNAMENT", "bf4da25fd10028cc034d6f9fa61", "2023-2025_36M", 30017, "PRODUCTION", "Hybrid soft routing and multi-label allocation certified with +20.8% PF boost", 66.28, 0.883, 9.224, "GOVERNANCE_V2_PROMOTION_GATE"),
        ("V600_PRIMARY_WEIGHT", "PRIMARY_WEIGHT", 1.0, "RATIO", "V530_STATIC_1.0", now_str, now_str, "ALL_SCANNERS", "EXP_V6_ALL_SCANNER_TOURNAMENT", "bf4da25fd10028cc034d6f9fa61", "2023-2025_36M", 30017, "PRODUCTION", "1.0x full risk allocation for primary archetype setups", 72.26, 1.739, 14.388, "GOVERNANCE_V2_PROMOTION_GATE"),
        ("V600_SECONDARY_WEIGHT", "SECONDARY_WEIGHT", 0.80, "RATIO", "NONE", now_str, now_str, "ALL_SCANNERS", "EXP_V6_ALL_SCANNER_TOURNAMENT", "bf4da25fd10028cc034d6f9fa61", "2023-2025_36M", 30017, "PRODUCTION", "0.80x risk allocation for secondary confluence setups", 68.45, 1.120, 10.450, "GOVERNANCE_V2_PROMOTION_GATE"),
        ("V600_DEFENSIVE_WEIGHT", "DEFENSIVE_WEIGHT", 0.50, "RATIO", "NONE", now_str, now_str, "ALL_SCANNERS", "EXP_V6_ALL_SCANNER_TOURNAMENT", "bf4da25fd10028cc034d6f9fa61", "2023-2025_36M", 30017, "PRODUCTION", "0.50x risk allocation for unclassified setups to eliminate missed winners", 58.10, 0.320, 5.120, "GOVERNANCE_V2_PROMOTION_GATE"),
        ("V600_QUALITY_FLOOR", "QUALITY_FLOOR", 60.0, "SCORE_POINTS", "V530_QUAL_60.0", now_str, now_str, "ALL_SCANNERS", "EXP_V6_ALL_SCANNER_TOURNAMENT", "bf4da25fd10028cc034d6f9fa61", "2023-2025_36M", 30017, "PRODUCTION", "Base candidate quality floor", 66.28, 0.883, 9.224, "GOVERNANCE_V2_PROMOTION_GATE")
    ]
    
    for row in v6_params:
        cur.execute("""
            INSERT OR REPLACE INTO production_parameter_versions (
                version_id, parameter_name, value, value_unit, previous_version_id,
                created_at, effective_at, scanner_scope, experiment_id, source_commit,
                backtest_period, sample_size, status, rationale, win_rate_pct,
                net_er, profit_factor, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, row)
        
    conn.commit()
    conn.close()
    
    # 2. Write frozen production registry JSON
    registry_payload = {
        "version": "V6.00_DAILY_BUILDER_HYBRID_ROUTER",
        "parent_version": "V5.30_PRODUCTION",
        "rollback_target": "V5.30_PRODUCTION",
        "deployment_status": "ACTIVE_PRODUCTION",
        "governance_standard": "GOVERNANCE_V2",
        "promotion_timestamp": now_str,
        "certified_commit": "bf4da25fd10028cc034d6f9fa61",
        "routing_architecture": "HYBRID_SOFT_ROUTING_AND_MULTI_LABEL",
        "parameters": {
            "quality_floor": 60.0,
            "confidence_floor": 0.20,
            "primary_match_weight": 1.0,
            "secondary_match_weight": 0.80,
            "defensive_cross_weight": 0.50,
            "collision_policy": "HYBRID_PROB_WEIGHTED"
        },
        "archetype_scanner_map": {
            "VCP_COIL": "SCAN_VCP_1H",
            "LONG_BASE_ACCUMULATION": "SCAN_MULTIBAGGER_EOD",
            "PULLBACK_KEY_LEVEL": "SCAN_REVERSAL_KEYLEVEL",
            "SQUEEZE_SHORT_COVERING": "SCAN_SHORT_COVERING",
            "CLEAN_MOMENTUM_BREAKOUT": "SCAN_DAILY_BUILDER_45M"
        },
        "historical_certification": {
            "sessions": 750,
            "sample_size": 30017,
            "win_rate": 66.28,
            "profit_factor": 9.224,
            "total_r": 14023.19,
            "missed_winner_damage": 0.0,
            "bootstrap_95_ci": [0.064, 0.152],
            "permutation_p_value": 0.0001,
            "bonferroni_p": 0.0012
        }
    }
    
    with open(JSON_PATH, "w") as f:
        json.dump(registry_payload, f, indent=2)
        
    print("V6.00 parameters successfully registered in DB and JSON registry.")

if __name__ == "__main__":
    register_v600()
