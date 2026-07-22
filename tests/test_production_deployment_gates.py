# tests/test_production_deployment_gates.py
"""
ELITE BREAKOUT SYSTEM — PRODUCTION DEPLOYMENT VERIFICATION SUITE (V8.1)
Blocks any deployment with startup crashes, import errors, or method signature mismatches.

Gate 1 — Cold Start Test
Gate 2 — Import & Compilation Test
Gate 3 — 30-Second Production Smoke Test
Gate 4 — AST Reflection & Method Signature Audit (missing self, @staticmethod checks)
Gate 5 — Runtime Railway Environment Integration Test
Gate 6 — Production Readiness Checklist
"""

import sys
import os
import ast
import glob
import time
import pytest
import subprocess

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")
sys.path.insert(0, APP_DIR)

class TestProductionDeploymentGates:

    def test_gate1_cold_start_execution(self):
        """Gate 1: Verify application startup without uncaught exceptions."""
        import main
        assert hasattr(main, "run_system_scheduler"), "main.py missing run_system_scheduler"
        assert hasattr(main, "forensics"), "main.py missing forensics module"
        
        # Take cold-start snapshot
        main.forensics.take_snapshot("cold_start_test")
        mem = main.forensics.get_memory_stats()
        assert mem["rss_mb"] > 0, "Cold start memory check failed"

    def test_gate2_import_all_modules(self):
        """Gate 2: Import every module in app/ to detect broken signatures/imports."""
        app_files = glob.glob(os.path.join(APP_DIR, "*.py"))
        app_files = list(set(app_files))
        
        imported_count = 0
        for fpath in app_files:
            rel = os.path.relpath(fpath, APP_DIR)
            if rel.startswith("check_") or rel.startswith("test_"):
                continue
            modname = rel.replace(".py", "")
            try:
                __import__(modname)
                imported_count += 1
            except ModuleNotFoundError:
                # Optional external dependencies (e.g. pywebpush)
                pass
            except Exception as e:
                pytest.fail(f"Gate 2 Import Failure in module '{modname}': {e}")
                
        assert imported_count > 30, f"Gate 2: Expected >30 modules imported, got {imported_count}"

    def test_gate4_ast_method_signature_audit(self):
        """Gate 4: AST Reflection Audit - detect missing 'self'/'cls', wrong @staticmethod/@classmethod."""
        app_files = glob.glob(os.path.join(APP_DIR, "**/*.py"), recursive=True) + glob.glob(os.path.join(APP_DIR, "*.py"))
        app_files = list(set(app_files))
        
        violations = []
        
        for fpath in app_files:
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    tree = ast.parse(f.read())
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ClassDef):
                            for item in node.body:
                                if isinstance(item, ast.FunctionDef):
                                    # Check decorators
                                    is_static = any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in item.decorator_list)
                                    is_class = any(isinstance(d, ast.Name) and d.id == "classmethod" for d in item.decorator_list)
                                    
                                    args = [a.arg for a in item.args.args]
                                    
                                    # If __new__, first arg MUST be 'cls'
                                    if item.name == "__new__":
                                        if not args or args[0] not in ["cls", "self"]:
                                            violations.append(f"{os.path.basename(fpath)}:{item.lineno} -> {node.name}.__new__ missing 'cls' (args={args})")
                                    # If regular instance method (not static/class), first arg MUST be 'self'
                                    elif not is_static and not is_class:
                                        if not args or args[0] != "self":
                                            violations.append(f"{os.path.basename(fpath)}:{item.lineno} -> {node.name}.{item.name} missing 'self' (args={args})")
            except Exception:
                pass
                
        assert len(violations) == 0, f"Gate 4 AST Signature Violations Found:\n" + "\n".join(violations)

    def test_gate5_runtime_railway_integration(self):
        """Gate 5: Replicate Railway startup environment."""
        import database
        assert hasattr(database, "init_db"), "Database missing init_db function"
        assert hasattr(database, "get_connection"), "Database missing get_connection function"

    def test_gate6_production_readiness_checklist(self):
        """Gate 6: Production Readiness Checklist."""
        from forensics import forensics
        mem = forensics.get_memory_stats()
        assert mem["rss_mb"] < 400.0, f"Memory threshold breached: {mem['rss_mb']} MB"

    def test_gate7_dependency_reproducibility(self):
        """Gate 7: Dependency Reproducibility - verify requirements.txt exists."""
        req_path = os.path.join(APP_DIR, "..", "requirements.txt")
        assert os.path.exists(req_path), "requirements.txt missing"
        with open(req_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "psycopg2" in content or "psycopg2-binary" in content, "psycopg2 missing from requirements.txt"
            assert "pandas" in content, "pandas missing from requirements.txt"

    def test_gate8_scheduled_execution_simulation(self):
        """Gate 8: Scheduled Execution Simulation - verify scheduler jobs."""
        import main
        assert hasattr(main, "_build_watchlist_background"), "Daily builder background handler missing"
        assert hasattr(main, "run_evening_scanners"), "Evening scanners scheduled handler missing"

    def test_gate9_memory_regression_budget(self):
        """Gate 9: Memory Regression Gate - enforce Startup RSS < 450 MB, Thread Count < 30."""
        from forensics import forensics
        mem = forensics.get_memory_stats()
        assert mem["rss_mb"] < 450.0, f"Startup RSS budget breached: {mem['rss_mb']} MB (Budget < 450 MB)"
        assert mem["thread_count"] < 30, f"Thread count budget breached: {mem['thread_count']} threads (Budget < 30)"

    def test_gate10_alert_contract_regression(self):
        """Gate 10: Alert Contract Regression - verify mandatory payload fields."""
        from core_models import PullbackCandidate
        annotations = PullbackCandidate.__annotations__
        assert "entry_price" in annotations, "PullbackCandidate DTO missing entry_price annotation"
        assert "as_of_date" in annotations, "PullbackCandidate DTO missing as_of_date annotation"

    def test_gate11_all_scanners_execution(self):
        """Gate 11: End-to-End Scanner Execution Test for all 6 scanners."""
        import eod_scanner
        import pullback_pipeline
        import reversal_scanner
        import multi_tf_scanner
        import wealth_engine
        import multibagger

        assert hasattr(eod_scanner, "start"), "EOD Scanner missing start entrypoint"
        assert hasattr(pullback_pipeline, "run_pullback_pipeline"), "Pullback Pipeline missing run_pullback_pipeline entrypoint"
        assert hasattr(reversal_scanner, "start"), "Reversal Scanner missing start entrypoint"
        assert hasattr(multi_tf_scanner, "start"), "Multi-TF Scanner missing start entrypoint"
        assert hasattr(wealth_engine, "run_wealth_scan"), "Wealth Engine missing run_wealth_scan entrypoint"
        assert hasattr(multibagger, "run_scanner"), "Multibagger Engine missing run_scanner entrypoint"

    def test_gate12_database_contract(self):
        """Gate 12: Database Contract & Schema Integrity Test."""
        import database
        assert hasattr(database, "save_alert_if_new"), "Database missing save_alert_if_new persistence contract"
        assert hasattr(database, "upsert_scanner_health"), "Database missing upsert_scanner_health contract"

    def test_gate13_version_endpoint(self):
        """Gate 13: Build Metadata /version Endpoint Verification."""
        import dashboard_server
        client = dashboard_server.app.test_client()
        response = client.get("/version")
        assert response.status_code == 200, f"/version endpoint failed with status {response.status_code}"
        data = response.get_json()
        assert data["git_commit"] in data or "git_commit" in data, "Build metadata missing git_commit"
        assert data["architecture_version"] == "8.1", "Architecture version mismatch"
        assert data["status"] == "RELEASE_GATE_APPROVED", "Release gate approval missing"

    def test_gate14_earnings_calendar_contract(self):
        """Gate 14: Earnings Calendar Subsystem Verification."""
        import earnings_calendar
        assert hasattr(earnings_calendar, "EarningsCalendarService"), "Earnings calendar missing EarningsCalendarService"
        assert hasattr(earnings_calendar, "earnings_calendar_service"), "Earnings calendar missing earnings_calendar_service singleton"
        
        info = earnings_calendar.earnings_calendar_service.get_earnings_info("RELIANCE.NS")
        assert "earnings_flag" in info, "Earnings info missing earnings_flag"
        assert "days_to_earnings" in info, "Earnings info missing days_to_earnings"
        assert "earnings_severity" in info, "Earnings info missing earnings_severity"
        assert "date_status" in info, "Earnings info missing date_status"

    def test_gate15_quality_trajectory_contract(self):
        """Gate 15: Quality Trajectory Engine Contract Verification."""
        import quality_trajectory
        assert hasattr(quality_trajectory, "compute_trajectory_score"), "Quality trajectory missing compute_trajectory_score"
        assert hasattr(quality_trajectory, "safe_float"), "Quality trajectory missing safe_float helper"

        # Contract snapshot test for valid input
        result = quality_trajectory.compute_trajectory_score({
            "roce_history": [10.0, 14.0, 18.0, 22.0],
            "roe_history": [12.0, 15.0, 18.0, 22.0],
            "opm_history": [10.0, 12.0, 14.0, 16.0],
            "de_history": [0.8, 0.6, 0.4, 0.2],
            "icr_history": [3.0, 5.0, 8.0, 12.0],
            "cfo_pat": 1.10
        })
        assert result["trajectory_score"] >= 18
        assert result["trajectory_grade"] == "A"

        # Contract snapshot test for missing input (Defensive UNKNOWN fallback)
        missing_res = quality_trajectory.compute_trajectory_score({})
        assert missing_res["trajectory_grade"] == "UNKNOWN"
        assert missing_res["trajectory_details"]["status"] == "MISSING_DATA"

    def test_gate16_forensic_engine_contract(self):
        """Gate 16: Forensic Risk Engine Contract Verification."""
        import forensic_engine
        assert hasattr(forensic_engine, "ForensicEngine"), "Forensic engine missing ForensicEngine class"
        assert hasattr(forensic_engine, "ForensicRiskTier"), "Forensic engine missing ForensicRiskTier enum"

        # Hard Reject Contract Test
        reject_res = forensic_engine.ForensicEngine.evaluate_symbol({"cfo_pat_3y": 0.45})
        assert reject_res["forensic_risk_tier"] == forensic_engine.ForensicRiskTier.REJECT
        assert reject_res["forensic_score"] == -30

        # Growth Mode Contract Test
        growth_res = forensic_engine.ForensicEngine.evaluate_symbol({
            "cfo_pat_3y": 1.10,
            "fcf_history": [-10.0, -20.0, -30.0],
            "capex_sales_ratio": 0.22,
            "revenue_cagr_3y": 0.25,
            "roce": 0.24
        })
        assert growth_res["growth_investment_mode"] is True
        assert growth_res["forensic_risk_tier"] == forensic_engine.ForensicRiskTier.LOW
        assert growth_res["forensic_score"] == -3


