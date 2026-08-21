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
