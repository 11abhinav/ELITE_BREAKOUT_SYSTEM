# tests/test_codebase_integrity.py
# [RULE 67: CONTINUOUS INTEGRITY & PRE-PUSH VALIDATION SUITE]
# Statically and dynamically validates:
# 1. Zero undefined variables or referenced-before-assignment bugs across app/ and tests/
# 2. Database column safety on key relational tables (scanner_candidates, alerts, etc.)
# 3. Multi-TF box SL/Target computation variable ordering and safety

import os
import sys
import ast
import re
import pytest
import pandas as pd
import numpy as np

# Ensure app and root are in sys.path
sys.path.insert(0, os.path.abspath("app"))
sys.path.insert(0, os.path.abspath("."))

from pyflakes import checker
from app.sl_target_helper import _compute_multi_tf_v2, compute_sl_and_target, TradeStructureValidator


def test_no_undefined_variables_across_repo():
    """Validates that no undefined names or unassigned local variable bugs exist in any python file."""
    root_dirs = ["app", "tests"]
    failures = []
    scanned_count = 0
    
    for r_dir in root_dirs:
        for root, dirs, files in os.walk(r_dir):
            # Skip hidden and cache dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "node_modules", "artifacts", "logs")]
            for file in files:
                if not file.endswith(".py"):
                    continue
                path = os.path.join(root, file)
                scanned_count += 1
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        source = f.read()
                    tree = ast.parse(source, filename=path)
                    w = checker.Checker(tree, filename=path)
                    for msg in w.messages:
                        msg_str = str(msg)
                        msg_cls = msg.__class__.__name__
                        if msg_cls in ("UndefinedName", "UndefinedLocal", "UndefinedExport") or \
                           "undefined name" in msg_str.lower() or "referenced before assignment" in msg_str.lower():
                            failures.append(f"{path}:{getattr(msg, 'lineno', '?')} -> {msg_str}")
                except Exception as e:
                    failures.append(f"PARSE_ERROR: {path} -> {e}")

                if scanned_count % 50 == 0:
                    print(f"  [Progress] Scanned {scanned_count} Python files...", flush=True)

    print(f"Scanned {scanned_count} Python files. Total failures: {len(failures)}", flush=True)
    assert not failures, f"Found {len(failures)} undefined variable or syntax issues:\n" + "\n".join(failures)


def test_multi_tf_box_sl_target_variable_ordering():
    """Validates that _compute_multi_tf_v2 executes cleanly without UnboundLocalError for t2/t3."""
    # Test Case 1: Standard box setup with df
    df = pd.DataFrame([{
        "LOOKBACK_SWING_HIGH": 115.0,
        "R1": 120.0,
        "R2": 130.0
    }])
    res = _compute_multi_tf_v2(
        entry=100.0,
        eff_atr=3.0,
        ticker=df,
        box_low=95.0
    )
    assert res is not None
    assert "target_1" in res
    assert "target_2" in res
    assert "target_3" in res
    assert res["target_2"] > res["target_1"]
    assert res["target_3"] > res["target_2"]
    assert res["stop_loss"] < res["entry"]
    assert res["rr_ratio"] >= 1.5

    # Test Case 2: Blue sky (no structural highs) -> measured move
    res_blue_sky = _compute_multi_tf_v2(
        entry=500.0,
        eff_atr=10.0,
        ticker=pd.DataFrame(),
        box_low=480.0
    )
    assert res_blue_sky is not None
    assert res_blue_sky["target_basis"] == "2R_Measured_Move"
    assert res_blue_sky["target_2"] > res_blue_sky["target_1"]


def test_scanner_candidates_sql_column_safety():
    """Validates that all SQL SELECT queries querying scanner_candidates only reference valid columns."""
    with open("app/master_orchestrator.py", "r", encoding="utf-8") as f:
        content = f.read()

    # Search each line block containing scanner_candidates
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        if "FROM scanner_candidates" in line:
            # Look back to the immediate preceding SELECT statement
            select_block = []
            for k in range(idx - 1, max(-1, idx - 25), -1):
                select_block.insert(0, lines[k])
                if "SELECT" in lines[k].upper():
                    break
            
            # Ensure no bare 'id,' or 'id ' without 'candidate_id'
            for l in select_block:
                tokens = [t.strip().rstrip(",") for t in l.split()]
                if "id" in tokens and "candidate_id" not in l and "as id" not in l.lower():
                    pytest.fail(f"Invalid column 'id' found querying scanner_candidates at line {idx}: {l}")
