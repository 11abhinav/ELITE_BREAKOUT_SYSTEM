#!/usr/bin/env bash
# [RULE 67: AUTOMATED PRE-PUSH INTEGRITY GATE]
# Verifies zero undefined variables, syntax errors, and SQL column safety before pushing.

set -e

echo "========================================================"
echo "🛡️  RUNNING AUTOMATED PRE-PUSH INTEGRITY GATE AUDIT"
echo "========================================================"

export PYTHONPATH="app:."

# 1. Codebase Integrity Test Suite
echo "🔍 [1/3] Checking undefined variables, unassigned locals, and SQL safety..."
venv/bin/pytest tests/test_codebase_integrity.py -v

# 2. Multi-TF Risk Repair Regression Suite
echo "🔍 [2/3] Checking Multi-TF Risk and Structure Validation..."
venv/bin/pytest tests/test_multi_tf_risk_repair.py -v

# 3. V2 Dashboard & Orchestrator Regression Suite
echo "🔍 [3/3] Checking Master Orchestrator and Screen Contracts..."
venv/bin/pytest tests/test_v2_orchestrator.py -v

echo "========================================================"
echo "✅ PRE-PUSH INTEGRITY GATE PASSED 100% (0 VIOLATIONS)"
echo "========================================================"
