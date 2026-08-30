"""
Scanner Quality Orchestrator
Final Automated State Machine for Elite Breakout Scanner Ecosystem.
Advances scanners through:
DATA_REPAIR -> BASELINE_READY -> DISCOVERY -> VALIDATION -> HOLDOUT -> FORWARD -> PROMOTION_ELIGIBLE -> PRODUCTION_IMPROVED
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any

class ScannerLifecycleState(str, Enum):
    DATA_REPAIR = "DATA_REPAIR"
    BASELINE_READY = "BASELINE_READY"
    DISCOVERY_ACTIVE = "DISCOVERY_ACTIVE"
    VALIDATION_ACTIVE = "VALIDATION_ACTIVE"
    HOLDOUT_TESTING = "HOLDOUT_TESTING"
    FORWARD_VALIDATING = "FORWARD_VALIDATING"
    PROMOTION_ELIGIBLE = "PROMOTION_ELIGIBLE"
    PRODUCTION_IMPROVED = "PRODUCTION_IMPROVED"

@dataclass
class ScannerGateRequirements:
    min_forward_n: int = 50
    min_unique_symbols: int = 15
    min_trading_days: int = 5
    max_symbol_concentration_pct: float = 20.0
    require_bca_lower_pos: bool = True
    require_delta_maxdd_nonpos: bool = True

def evaluate_forward_gate(
    forward_n: int,
    unique_symbols: int,
    trading_days: int,
    max_concentration_pct: float,
    delta_net_er: float,
    bca_lower_ci: float,
    delta_maxdd: float,
    req: ScannerGateRequirements = ScannerGateRequirements()
) -> str:
    """
    Evaluates whether a trade scanner has satisfied the complete forward promotion gate.
    Returns: 'PASS', 'FAIL', or 'ACCUMULATING'
    """
    if forward_n < req.min_forward_n:
        return "ACCUMULATING"
    
    # Check sample diversity invariants
    diversity_pass = (
        unique_symbols >= req.min_unique_symbols and
        trading_days >= req.min_trading_days and
        max_concentration_pct <= req.max_symbol_concentration_pct
    )
    if not diversity_pass:
        return "FAIL"
    
    # Check economic & uncertainty requirements
    econ_pass = (
        delta_net_er > 0 and
        bca_lower_ci > 0 and
        delta_maxdd <= 0
    )
    return "PASS" if econ_pass else "FAIL"
