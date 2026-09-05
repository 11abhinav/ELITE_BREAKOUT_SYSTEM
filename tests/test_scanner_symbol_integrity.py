# =====================================================================================
# tests/test_scanner_symbol_integrity.py
#
# RULE 67 CHANGE-RATIONALE:
# - Automated regression test ensuring all scanner files and critical helpers have:
#   1. Valid Python AST syntax
#   2. Zero undefined variables / unbound local names (pyflakes F821 / F823)
#   3. Correctly resolved closure and default argument bindings
# =====================================================================================

import ast
import os
import pyflakes.api
import pyflakes.reporter
import io
import pytest

SCANNER_MODULES = [
    "app/eod_scanner.py",
    "app/multi_tf_scanner.py",
    "app/reversal_scanner.py",
    "app/wealth_engine.py",
    "app/pullback_pipeline.py",
    "app/multibagger.py",
    "app/daily_builder.py",
    "app/accumulation_scanner.py",
    "app/ai_analyzer.py",
    "app/pledge_worker.py",
    "app/multitf/scanner.py",
    "app/technical_indicators.py",
    "app/sl_target_helper.py",
    "app/fundamentals_cache.py",
    "app/counterfactual_engine.py",
]

def test_ast_parse_all_scanners():
    """Verify that all scanner modules parse cleanly into valid Python AST."""
    for mod_path in SCANNER_MODULES:
        assert os.path.exists(mod_path), f"Scanner file missing: {mod_path}"
        with open(mod_path, "r", encoding="utf-8") as f:
            code = f.read()
        try:
            tree = ast.parse(code, filename=mod_path)
            assert tree is not None
        except Exception as e:
            pytest.fail(f"Syntax/AST failure in {mod_path}: {e}")

def test_pyflakes_zero_undefined_names():
    """Verify pyflakes reports ZERO undefined names (F821) or unbound names (F823)."""
    for mod_path in SCANNER_MODULES:
        out = io.StringIO()
        err = io.StringIO()
        reporter = pyflakes.reporter.Reporter(out, err)
        with open(mod_path, "r", encoding="utf-8") as f:
            code = f.read()
        pyflakes.api.check(code, mod_path, reporter=reporter)
        output_lines = out.getvalue().splitlines() + err.getvalue().splitlines()
        
        undefined_errors = [
            line for line in output_lines
            if "undefined name" in line or "referenced before assignment" in line
        ]
        assert len(undefined_errors) == 0, f"Found undefined names in {mod_path}: {undefined_errors}"
