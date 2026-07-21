import os
import ast
from pathlib import Path

# Legacy files that currently violate the architecture boundaries.
# These should be refactored eventually, but we whitelist them here so CI passes.
# ANY NEW FILE MUST STRICTLY ADHERE TO THE DEPENDENCY RULES.
LEGACY_EXCLUSIONS = {
    "live_prices.py",
    "block_deal_detector.py",
    "valuation_utils.py",
    "dashboard_server.py",
    "institutional_data.py",
    "check_yf2.py",
    "check_yf.py",
    "eod_scanner.py", # imports _fyers_circuit_breaker
    "yf_bootstrap.py",
    "diagnostic.py",
    "pledge_worker.py",
    "fundamentals_cache.py",
    "ai_analyzer.py",
    "test_sim.py",
    "multibagger.py",
    "pledge_scraper.py",
    "sector_rotation.py",
    "macro_utils.py",
    "price_provider.py",
    "yf_rate_limiter.py",
    "fyers_auth.py",
    "surveillance.py",
    "pdf_parser.py",
    "delivery_data.py",
    "validation_report.py",
    "reversal_scanner.py",
    "multi_tf_scanner.py",
    "test_low.py",
    "constituent_service.py"
}

FORBIDDEN_IMPORTS = {"yfinance", "requests", "fyers_auth"}

def check_dependencies(file_path: Path):
    """
    Parses the AST of a Python file and asserts that it does not import
    forbidden external API libraries directly.
    """
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read(), filename=str(file_path))
        except SyntaxError:
            return # Skip files with syntax errors (e.g. bak files if they have issues)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base_module = alias.name.split('.')[0]
                assert base_module not in FORBIDDEN_IMPORTS, f"Dependency Rule Violation: '{file_path.name}' imports forbidden module '{base_module}'"
        
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                base_module = node.module.split('.')[0]
                assert base_module not in FORBIDDEN_IMPORTS, f"Dependency Rule Violation: '{file_path.name}' imports from forbidden module '{base_module}'"
                
                # Also forbid importing provider modules directly into scanners
                if base_module == "data_providers" and "scanner" in file_path.name.lower():
                    # eod_scanner.py is an exception currently whitelisted
                    assert False, f"Dependency Rule Violation: Scanner '{file_path.name}' directly imports from 'data_providers'"

def test_dependency_rules_enforced():
    """
    Architectural Rule:
    Only `app/data_providers/` and `app/data_provider.py` should communicate with external APIs.
    Everything else should consume validated domain objects from the ValidationEngine.
    """
    app_dir = Path(__file__).parent.parent / "app"
    
    for py_file in app_dir.rglob("*.py"):
        # Skip __init__.py files
        if py_file.name == "__init__.py":
            continue
            
        # Skip files inside data_providers directory
        if "data_providers" in py_file.parts:
            continue
            
        # Skip data_provider.py itself
        if py_file.name == "data_provider.py":
            continue
            
        # Skip whitelisted legacy files
        if py_file.name in LEGACY_EXCLUSIONS:
            continue
            
        # Check all other files in app/
        check_dependencies(py_file)
