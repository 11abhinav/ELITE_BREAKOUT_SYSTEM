import ast
import os
import pytest

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")

def get_ast(filepath):
    with open(filepath, "r") as f:
        return ast.parse(f.read(), filename=filepath)

class GoldenRuleVisitor(ast.NodeVisitor):
    def __init__(self):
        self.errors = []
        
    def visit_Try(self, node):
        # Enforce Rule 1: Lock Safety
        # If any except block in a Try statement calls upsert_scanner_health with status="DOWN",
        # the Try statement MUST contain a check for "actively running" in at least one of its
        # except handlers to prevent lock collisions from falsely marking the scanner as DOWN.
        
        has_upsert_down = False
        has_actively_running_check = False
        
        for handler in node.handlers:
            for child in ast.walk(handler):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id == "upsert_scanner_health":
                        for kw in child.keywords:
                            if kw.arg == "status" and isinstance(kw.value, ast.Constant) and kw.value.value == "DOWN":
                                has_upsert_down = True
                
                if isinstance(child, ast.Constant) and isinstance(child.value, str):
                    if "actively running" in child.value.lower():
                        has_actively_running_check = True
                        
        if has_upsert_down and not has_actively_running_check:
            self.errors.append(f"Line {node.lineno}: found upsert_scanner_health(..., status='DOWN') in a try-except block WITHOUT checking for 'actively running' lock collision.")
            
        self.generic_visit(node)
        
    def visit_If(self, node):
        # Enforce Rule 2: Scheduler Bypass Rule
        # If we see already_ran, we must see time checks to prevent manual triggers
        # from bypassing schedulers.
        
        has_already_ran_assign = False
        for child in ast.walk(node):
            if isinstance(child, ast.Assign):
                for target in child.targets:
                    if isinstance(target, ast.Name) and target.id == "already_ran":
                        if isinstance(child.value, ast.Constant) and child.value.value is True:
                            has_already_ran_assign = True
                            break
                            
        if has_already_ran_assign:
            # We must see some time-based check (e.g. .time() >= start_time) 
            has_time_check = False
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "time":
                    has_time_check = True
                if isinstance(child, ast.Compare):
                    has_time_check = True
                    
            if not has_time_check:
                self.errors.append(f"Line {node.lineno}: assigns already_ran=True but lacks time-based validation (only checking date allows manual triggers to bypass scheduler).")
                
        self.generic_visit(node)


def test_golden_rules_enforced_in_main():
    main_py_path = os.path.join(APP_DIR, "main.py")
    if not os.path.exists(main_py_path):
        pytest.skip("main.py not found")
        
    tree = get_ast(main_py_path)
    visitor = GoldenRuleVisitor()
    visitor.visit(tree)
    
    if visitor.errors:
        error_msg = "\n".join(visitor.errors)
        pytest.fail(f"Golden Rule Violations in main.py:\n{error_msg}")

def test_golden_gate_earnings_calendar_integration():
    """Golden Gate 14: Verifies Earnings Calendar F-06 contracts."""
    import app.earnings_calendar as ec
    assert hasattr(ec, "EarningsProvider"), "Golden Gate Failure: EarningsProvider missing"
    assert hasattr(ec, "EarningsCalendarService"), "Golden Gate Failure: EarningsCalendarService missing"
    assert hasattr(ec, "earnings_calendar_service"), "Golden Gate Failure: earnings_calendar_service missing"
    info = ec.earnings_calendar_service.get_earnings_info("RELIANCE.NS")
    assert "earnings_flag" in info
    assert "days_to_earnings" in info
    assert "earnings_severity" in info

def test_golden_gate_quality_trajectory_engine():
    """Golden Gate 15: Verifies Quality Trajectory F-14 contracts."""
    import app.quality_trajectory as qt
    assert hasattr(qt, "compute_trajectory_score"), "Golden Gate Failure: compute_trajectory_score missing"
    assert hasattr(qt, "safe_float"), "Golden Gate Failure: safe_float missing"
    res = qt.compute_trajectory_score({"roce_history": [10.0, 15.0, 20.0], "roe_history": [12.0, 16.0, 20.0], "cfo_pat": 1.10})
    assert "trajectory_score" in res
    assert "trajectory_grade" in res

def test_golden_gate_forensic_risk_engine():
    """Golden Gate 16: Verifies Forensic Risk Engine F-15 contracts."""
    import app.forensic_engine as fe
    assert hasattr(fe, "ForensicEngine"), "Golden Gate Failure: ForensicEngine class missing"
    assert hasattr(fe, "ForensicRiskTier"), "Golden Gate Failure: ForensicRiskTier enum missing"
    
    # Assert Hard Gate contract
    reject_res = fe.ForensicEngine.evaluate_symbol({"cfo_pat_3y": 0.45})
    assert reject_res["forensic_risk_tier"] == fe.ForensicRiskTier.REJECT
    assert reject_res["forensic_score"] == -30

    # Assert Growth Mode contract
    growth_res = fe.ForensicEngine.evaluate_symbol({
        "cfo_pat_3y": 1.10,
        "fcf_history": [-10.0, -20.0, -30.0],
        "capex_sales_ratio": 0.22,
        "revenue_cagr_3y": 0.25,
        "roce": 0.24
    })
    assert growth_res["growth_investment_mode"] is True
    assert growth_res["forensic_score"] == -3

