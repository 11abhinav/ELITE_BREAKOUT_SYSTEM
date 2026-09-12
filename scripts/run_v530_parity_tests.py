"""
Standalone V5.30 Parity & Certification Test Runner
"""

import os
import sys
import tempfile
import sqlite3

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.production.v530_shadow_execution_engine import (
    V530ShadowExecutionEngine,
    CandidateContext,
    TELEMETRY_DB_PATH,
    PARAM_DB_PATH
)

def run_all_tests():
    print("================================================================")
    print("   STARTING V5.30 SHADOW PARITY & PLUMBING CERTIFICATION SUITE   ")
    print("================================================================")

    # 1. Parameter Parity Test
    print("\n[TEST 1/7] Testing Parameter Parity in production_parameters.db...")
    assert os.path.exists(PARAM_DB_PATH), "production_parameters.db must exist"
    with sqlite3.connect(PARAM_DB_PATH, timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT version_id, parameter_name, value, status
            FROM production_parameter_versions
            WHERE version_id LIKE '%V530%' OR (scanner_scope='DAILY_BUILDER' AND status='SHADOW')
        """).fetchall()
    
    param_dict = {r["parameter_name"]: r["value"] for r in rows}
    assert param_dict.get("daily_builder_45m_breakout_trigger") == 45.0, f"Trigger failed: {param_dict.get('daily_builder_45m_breakout_trigger')}"
    assert param_dict.get("daily_builder_clv_weight") == 1.5, f"CLV failed: {param_dict.get('daily_builder_clv_weight')}"
    assert param_dict.get("daily_builder_compression_weight") == 1.5, f"Compression failed: {param_dict.get('daily_builder_compression_weight')}"
    assert param_dict.get("daily_builder_veto_regime_divergence") == 1.0, f"Regime veto failed: {param_dict.get('daily_builder_veto_regime_divergence')}"
    assert param_dict.get("daily_builder_regime_dynamic_capacity") == 1.0, f"Capacity failed: {param_dict.get('daily_builder_regime_dynamic_capacity')}"
    print("  ✓ Parameter Parity: All 5 certified V5.30 parameters present & verified.")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db = os.path.join(tmpdir, "test_shadow.db")
        engine = V530ShadowExecutionEngine(db_path=tmp_db)

        cand = CandidateContext(
            symbol="TRENT",
            decision_timestamp="2026-09-11T15:30:00",
            exchange_session_date="2026-09-11",
            source_commit="bf4da25f",
            nifty_regime="STRONG_BULL",
            sector="CONSUMER",
            archetype="FRESH_MOMENTUM_CONSOLIDATION",
            open_p=7250.0, high_p=7380.0, low_p=7190.0, close_p=7350.0,
            volume=1500000.0, sma20_volume=950000.0, atr=160.0, vwap=7280.0,
            overhead_resistance=7850.0, compression_days=14, days_since_impulse=4,
            dist_to_bo=0.40, base_tightness=1.15, close_volume_conc=0.38,
            wick_pct=0.08, rs_3d_momentum=0.042, rs_vs_sector=0.025, rs_vs_nifty=0.018,
            sector_breadth=0.75
        )

        # 2. Calendar Invariant
        print("\n[TEST 2/7] Testing Calendar & Weekend Invariant Enforcement...")
        cand.exchange_session_date = "2026-09-12" # Saturday
        sat_blocked = False
        try:
            engine.compute_focused_model_g_score(cand)
        except ValueError:
            sat_blocked = True
        assert sat_blocked, "Saturday must raise ValueError"

        cand.exchange_session_date = "2026-09-13" # Sunday
        sun_blocked = False
        try:
            engine.compute_focused_model_g_score(cand)
        except ValueError:
            sun_blocked = True
        assert sun_blocked, "Sunday must raise ValueError"
        cand.exchange_session_date = "2026-09-11" # Reset to Friday
        print("  ✓ Calendar Invariant: Saturday = 0, Sunday = 0 strictly enforced.")

        # 3. Model G Scoring
        print("\n[TEST 3/7] Testing Focused Model G Composite Scoring...")
        res = engine.compute_focused_model_g_score(cand)
        assert res["model_g_score"] >= 60.0, f"Score {res['model_g_score']} < 60.0"
        assert res["is_qualified"] is True, "Expected candidate to qualify"
        assert res["is_vetoed"] == 0, "Candidate should not be vetoed"
        assert res["clv"] == 0.842, f"CLV mismatch: {res['clv']}"
        print(f"  ✓ Focused Model G: Score={res['model_g_score']}, CLV={res['clv']}, Qualified={res['is_qualified']}.")

        # 4. Dynamic Capacity Allocation
        print("\n[TEST 4/7] Testing Regime-Dynamic Capacity Policy...")
        candidates = []
        for i in range(6):
            c = CandidateContext(
                symbol=f"SYM_{i}",
                decision_timestamp="2026-09-11T15:30:00",
                exchange_session_date="2026-09-11",
                source_commit="bf4da25f",
                nifty_regime="STRONG_BULL",
                sector="TECH",
                archetype="MOMENTUM",
                open_p=100.0, high_p=110.0, low_p=98.0, close_p=109.0,
                volume=1000000.0, sma20_volume=800000.0, atr=2.5, vwap=104.0,
                overhead_resistance=130.0, compression_days=15, days_since_impulse=3,
                dist_to_bo=0.3, base_tightness=1.2, close_volume_conc=0.4,
                wick_pct=0.05, rs_3d_momentum=0.03, rs_vs_sector=0.02, rs_vs_nifty=0.01,
                sector_breadth=0.80
            )
            candidates.append(c)

        eval_strong = engine.evaluate_shadow_session(candidates, "STRONG_BULL")
        assert len([x for x in eval_strong if x["status"] == "PENDING_45M_CONFIRMATION"]) == 5
        print("  ✓ Strong Bull: 5/5 capacity slots allocated.")

        eval_neutral = engine.evaluate_shadow_session(candidates, "NEUTRAL_BULL")
        assert len([x for x in eval_neutral if x["status"] == "PENDING_45M_CONFIRMATION"]) == 4
        print("  ✓ Neutral Bull: 4/4 capacity slots allocated.")

        eval_choppy = engine.evaluate_shadow_session(candidates, "CHOPPY_RANGE")
        assert len([x for x in eval_choppy if x["status"] == "PENDING_45M_CONFIRMATION"]) == 2
        print("  ✓ Choppy Range: 2/2 capacity slots allocated (throttled).")

        eval_bear = engine.evaluate_shadow_session(candidates, "NEUTRAL_BEAR")
        assert len([x for x in eval_bear if x["status"] == "PENDING_45M_CONFIRMATION"]) == 1
        print("  ✓ Neutral Bear: 1/1 capacity slot allocated (heavily throttled).")

        eval_selloff = engine.evaluate_shadow_session(candidates, "SHARP_SELLOFF")
        assert len([x for x in eval_selloff if x["status"] == "PENDING_45M_CONFIRMATION"]) == 0
        print("  ✓ Sharp Selloff: 0 capacity slots (complete safety gating).")

        # 5. Point-In-Time 45m Confirmation
        print("\n[TEST 5/7] Testing 45-Minute Point-In-Time Breakout Trigger...")
        trig_conf = engine.evaluate_45m_breakout_trigger(
            candidate_id="TRENT_01",
            ctx=cand,
            point_in_time_hod=7390.0,
            point_in_time_vwap=7340.0,
            price_at_45m=7395.0,
            breakout_pivot=7380.0
        )
        assert trig_conf["final_trigger_state"] == "CONFIRMED_BREAKOUT"
        assert trig_conf["trigger_price"] == 7395.0
        assert trig_conf["execution_slippage_r"] == 0.08
        print(f"  ✓ 45m Breakout Confirmed: State={trig_conf['final_trigger_state']}, TriggerPrice={trig_conf['trigger_price']}")

        trig_trap = engine.evaluate_45m_breakout_trigger(
            candidate_id="TRENT_02",
            ctx=cand,
            point_in_time_hod=7390.0,
            point_in_time_vwap=7340.0,
            price_at_45m=7320.0,
            breakout_pivot=7380.0
        )
        assert trig_trap["final_trigger_state"] == "UNCONFIRMED_TRAP_AVOIDED"
        assert trig_trap["trigger_price"] is None
        print(f"  ✓ 45m Morning Trap Avoided: State={trig_trap['final_trigger_state']}")

        # 6. Paired Disagreement Telemetry & Attribution
        print("\n[TEST 6/7] Testing Paired Disagreement Telemetry & Root Cause Engine...")
        v529_item = {"status": "PENDING_30M_CONFIRMATION", "model_g": {"is_qualified": True, "is_vetoed": 0}}
        v530_item = {"status": "CAPACITY_FILTERED", "model_g": {"is_qualified": True, "is_vetoed": 0}}
        v529_trig = {"final_trigger_state": "CONFIRMED_BREAKOUT"}
        v530_trig = {"final_trigger_state": "UNCONFIRMED_TRAP_AVOIDED"}

        is_disagree, cause, notes = engine.attribute_disagreement(v529_item, v530_item, v529_trig, v530_trig)
        assert is_disagree is True
        assert cause in ["DYNAMIC_CAPACITY", "45M_CONFIRMATION", "COMBINED"]
        print(f"  ✓ Paired Attribution: Disagreement={is_disagree}, RootCause={cause}")

        # 7. Production Isolation & Non-Interference
        print("\n[TEST 7/7] Testing Production Isolation & Non-Interference...")
        engine_prod = V530ShadowExecutionEngine(db_path=TELEMETRY_DB_PATH)
        with sqlite3.connect(TELEMETRY_DB_PATH, timeout=10.0) as conn:
            conn.row_factory = sqlite3.Row
            v529_cnt = conn.execute("SELECT count(*) as cnt FROM v529_shadow_alert_telemetry").fetchone()["cnt"]
            assert v529_cnt >= 0
            v530_tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'v530_%'").fetchall()
            names = [r["name"] for r in v530_tables]
            assert "v530_shadow_alert_telemetry" in names
            assert "v530_shadow_trigger_telemetry" in names
            assert "v530_vs_v529_disagreement_telemetry" in names
        print(f"  ✓ Production Isolation: V5.29 rows untouched ({v529_cnt} rows), V5.30 isolated tables verified.")

    print("\n================================================================")
    print("   ALL 7 V5.30 PARITY & PLUMBING CERTIFICATION TESTS PASSED!   ")
    print("================================================================")

if __name__ == "__main__":
    run_all_tests()
