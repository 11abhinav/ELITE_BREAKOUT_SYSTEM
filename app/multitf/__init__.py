# =====================================================================================
# app/multitf/__init__.py
# MULTI_TF V2 — 15m Consolidation + 5m Expansion Production Scanner Package
#
# Dependency order (strict — no reverse imports allowed):
#   data → context → consolidation → pressure → confluence → state → candidate → scanner
#
# External consumers only import from scanner:
#   from multitf.scanner import run_multitf_v2
# =====================================================================================
from multitf.scanner import run_multitf_v2

__all__ = ["run_multitf_v2"]
