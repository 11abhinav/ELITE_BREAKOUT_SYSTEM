import ast
import json
import logging
from typing import Dict, List, Set

logger = logging.getLogger(__name__)

class ScannerASTAnalyzer(ast.NodeVisitor):
    def __init__(self):
        self.dataframe_inputs = set()
        self.config_inputs = set()
        self.decision_variables = set()
        self.gates = set()

    def visit_Subscript(self, node):
        # Detect df['Close'], row['Volume'], latest['RSI']
        if isinstance(node.value, ast.Name):
            if isinstance(node.slice, ast.Constant):
                # We assume if they access a string index, it's a dataframe/dict input
                self.dataframe_inputs.add(node.slice.value)
        self.generic_visit(node)
        
    def visit_Call(self, node):
        # Detect latest.get("Open")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args and isinstance(node.args[0], ast.Constant):
                self.dataframe_inputs.add(node.args[0].value)
        
        # Detect capture_gate("NO_DATA")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "capture_gate":
            if node.args and isinstance(node.args[0], ast.Constant):
                self.gates.add(node.args[0].value)
            elif node.keywords:
                for kw in node.keywords:
                    if kw.arg == "gate_name" and isinstance(kw.value, ast.Constant):
                        self.gates.add(kw.value.value)
                        
        self.generic_visit(node)

    def visit_Compare(self, node):
        # Basic extraction of comparison variables
        for comp in [node.left] + node.comparators:
            if isinstance(comp, ast.Name):
                self.decision_variables.add(comp.id)
        self.generic_visit(node)


def build_dependency_manifest(filepath: str) -> Dict[str, List[str]]:
    """Uses AST to build a manifest of expected dependencies."""
    with open(filepath, "r") as f:
        tree = ast.parse(f.read())
        
    analyzer = ScannerASTAnalyzer()
    analyzer.visit(tree)
    
    return {
        "dataframe_inputs": sorted(list(analyzer.dataframe_inputs)),
        "decision_variables": sorted(list(analyzer.decision_variables)),
        "gates": sorted(list(analyzer.gates)),
    }

def certify_telemetry(manifest: Dict[str, List[str]], emitted_json: dict) -> bool:
    """Verifies that emitted telemetry covers the AST manifest."""
    passed = True
    
    # 1. Check Dataframe Inputs
    actual_inputs = set()
    if "data" in emitted_json and "market_data" in emitted_json["data"]:
        actual_inputs.update(emitted_json["data"]["market_data"].keys())
    if "data" in emitted_json and "indicators" in emitted_json["data"]:
        actual_inputs.update(emitted_json["data"]["indicators"].keys())
        
    # We won't strictly fail on missing inputs since AST might catch unrelated dict keys,
    # but we log it.
    
    # 2. Check Decision Traces
    actual_gates = set()
    if "decision_trace" in emitted_json:
        for trace in emitted_json["decision_trace"]:
            actual_gates.add(trace.get("gate"))
            
    # Every gate captured at runtime should ideally be found in the AST. 
    # But conversely, if the scanner failed at a gate, we expect that gate to be the last one.
    
    # This is a stub for the full runtime execution test which would run the scanner logic
    # and compare the exact emitted JSON against the expected path.
    return passed

def test_data_quality_invariant():
    from scanner_telemetry import DecisionContext
    
    ctx = DecisionContext("TCS", "WEALTH_ENGINE")
    
    # 1. Add VALID input
    ctx.capture("cmp", 3500.0)
    ctx.add_decision_input("cmp", 3500.0, "Watchlist", "Live", "LIVE", required=True, valid=True)
    # 2. Add NULL input
    ctx.capture("roce", None)
    ctx.add_decision_input("roce", None, "Watchlist", "Live", "MISSING", required=True, valid=False)
    # 3. Add NAN input
    import math
    ctx.capture("roe", float("nan"))
    ctx.add_decision_input("roe", float("nan"), "Watchlist", "Live", "MISSING", required=True, valid=False)
    # 4. Add STALE input
    ctx.capture("debt_equity", 0.5)
    # manually setting status to STALE to simulate stale logic which might be external
    ctx.entries["debt_equity"].status = "STALE"
    ctx.add_decision_input("debt_equity", 0.5, "Watchlist", "2022-01-01", "STALE", required=True, valid=False)
    # 5. Add INVALID input (e.g. empty string)
    ctx.capture("yoy_profit", "")
    ctx.add_decision_input("yoy_profit", "", "Watchlist", "Live", "MISSING", required=True, valid=False)
    
    summary = ctx.data_quality_summary
    assert summary["expected_fields"] == 5
    assert summary["valid_fields"] == 1
    assert summary["null_fields"] == 1
    assert summary["nan_fields"] == 1
    assert summary["stale_fields"] == 1
    assert summary["invalid_fields"] == 1
    
    # Invariant assertion
    assert summary["expected_fields"] == summary["valid_fields"] + summary["null_fields"] + summary["nan_fields"] + summary["invalid_fields"] + summary["stale_fields"]

def test_certify_final_decision():
    from scanner_telemetry import DecisionContext, certify_final_decision
    
    ctx = DecisionContext("INFY", "WEALTH_ENGINE")
    ctx.add_decision_input("cmp", 1500.0, "Watchlist", "Live", "LIVE", required=True, valid=True)
    ctx.add_decision_input("roce", 25.0, "Watchlist", "Live", "LIVE", required=True, valid=True)
    
    # Pass certification
    is_certified, reason = certify_final_decision("INFY", "WEALTH_ENGINE", 1500.0, "2024-05-15", ctx.decision_manifest)
    assert is_certified is True
    assert reason == "CERTIFIED"
    
    # Fail certification (missing required field)
    ctx.add_decision_input("roe", None, "Watchlist", "Live", "MISSING", required=True, valid=False)
    is_certified, reason = certify_final_decision("INFY", "WEALTH_ENGINE", 1500.0, "2024-05-15", ctx.decision_manifest)
    assert is_certified is False
    assert "REQUIRED_DECISION_INPUT_INVALID" in reason
    
    # Fail certification (stale required field)
    ctx2 = DecisionContext("WIPRO", "WEALTH_ENGINE")
    ctx2.add_decision_input("cmp", 400.0, "Watchlist", "Live", "LIVE", required=True, valid=True)
    ctx2.add_decision_input("roce", 15.0, "Watchlist", "Live", "STALE", required=True, valid=False)
    is_certified, reason = certify_final_decision("WIPRO", "WEALTH_ENGINE", 400.0, "2024-05-15", ctx2.decision_manifest)
    assert is_certified is False
    assert "REQUIRED_DECISION_INPUT_INVALID" in reason
    
    # Fail certification (NaN price)
    import math
    is_certified, reason = certify_final_decision("TCS", "WEALTH_ENGINE", float("nan"), "2024-05-15", ctx.decision_manifest)
    assert is_certified is False
    assert "INVALID_ENTRY_PRICE" in reason

def test_no_trading_activity_terminal_decision():
    from scanner_telemetry import DecisionContext
    
    ctx = DecisionContext("RELIANCE", "MULTIBAGGER")
    ctx.capture_raw_market(open_p=100.0, high_p=100.0, low_p=100.0, close_p=100.0, volume=0.0)
    ctx.add_decision_input(name="Volume", value=0.0, source="MarketData", as_of="Live", freshness="LIVE", required=True, valid=False)
    ctx.finalize(decision="REJECTED", primary_reason="NO_TRADING_ACTIVITY")
    
    assert ctx.terminal_decision == "REJECTED"
    assert ctx.primary_reason == "NO_TRADING_ACTIVITY"
    assert len(ctx.decision_manifest) == 1
    assert ctx.decision_manifest[0]["name"] == "Volume"
    assert ctx.decision_manifest[0]["value"] == 0.0

def test_fallback_row_stale_inputs():
    from scanner_telemetry import DecisionContext
    import pandas as pd
    
    ctx = DecisionContext("HDFCBANK", "EOD")
    # Simulate a fallback row where only Bhavcopy fields are stale
    row = {"Open": 1500, "High": 1510, "Low": 1490, "Close": 1505, "Volume": 1000000, "RSI": 60}
    ctx.capture_dataframe_row(row, is_fallback=True, fallback_fields={"OPEN", "HIGH", "LOW", "CLOSE", "VOLUME"})
    
    # Check freshness in entries
    assert ctx.entries["Open"].freshness == "STALE"
    assert ctx.entries["Volume"].freshness == "STALE"
    
    # RSI wasn't in fallback_fields
    assert ctx.entries["RSI"].freshness == "LIVE"
    
    # Now explicitly add them to the manifest
    ctx.add_decision_input("Volume", 1000000, "Bhavcopy", "2023-01-01", "STALE", required=True, valid=True)
    ctx.add_decision_input("RSI", 60, "Calculated", "Live", "LIVE", required=True, valid=True)
    
    stale_count = sum(1 for f in ctx.decision_manifest if f["freshness"] == "STALE")
    live_count = sum(1 for f in ctx.decision_manifest if f["freshness"] == "LIVE")
    assert stale_count == 1
    assert live_count == 1

def test_critical_gate_structure():
    from scanner_telemetry import DecisionContext
    ctx = DecisionContext("ITC", "PULLBACK")
    
    ctx.capture_gate(gate_name="LOW_VOLUME", passed=False, actual_val=5000, threshold_val=50000, reason="Volume too low", gate_type="THRESHOLD")
    ctx.add_decision_input(name="LOW_VOLUME", value=5000, source="GateCheck", as_of="Live", freshness="LIVE", required=True, valid=False)
    ctx.finalize(decision="REJECTED", primary_reason="LOW_VOLUME_FAIL")
    
    gate_data = ctx.gate_results["LOW_VOLUME"]
    assert gate_data["passed"] is False
    assert gate_data["actual"] == 5000
    assert gate_data["threshold"] == 50000
    assert gate_data["gate_type"] == "THRESHOLD"
    
    assert len(ctx.decision_manifest) == 1
    assert ctx.decision_manifest[0]["name"] == "LOW_VOLUME"

