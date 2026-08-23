"""
app/accumulation/contracts.py — Decision Contracts and Manifest Validator for ACCUMULATION_SCANNER_V1.
Enforces strict type definitions, lifecycle invariants, and path-scoped decision structures.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime

@dataclass
class FundamentalFloorResult:
    passed: bool
    roe: float
    roce: float
    de_ratio: float
    reason: str = "PASS"

@dataclass
class SubScoreResult:
    accumulation_score: float
    compression_score: float
    rs_score: float
    resistance_score: float
    volume_delivery_score: float
    fundamental_score: float
    composite_score: float

@dataclass
class GateResult:
    passed: bool
    failed_gates: List[str] = field(default_factory=list)
    gate_details: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SLTargetResult:
    is_valid: bool
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    risk_pct: float
    rr_1: float
    rr_2: float
    rr_3: float
    support_anchor_price: float
    support_anchor_type: str
    rejection_reason: Optional[str] = None

@dataclass
class TradeSetupContract:
    symbol: str
    signal_state: str
    entry_type: str  # ZONE_MIDPOINT or BREAKOUT_CONFIRMATION
    entry_trigger_rule: str  # RANGE_TOUCH or LEVEL_CROSS
    entry_reference_type: str  # STRATEGY_REFERENCE or CONFIRMED_LEVEL
    entry_zone_low: float
    entry_zone_high: float
    entry_price: float
    preferred_entry: float
    entry_trigger_level: float
    entry_displacement_reference: float
    breakout_level: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    risk_pct: float
    rr_1: float
    rr_2: float
    rr_3: float
    suggested_capital: float
    suggested_position_size: int
    position_sizing_basis: str = "ACCOUNT_RISK_1PCT"
    status: str = "ACTIVE_SETUP"
    setup_outcome: str = "PENDING"
    entry_trigger_level_reached: Optional[bool] = None

class AccumulationContractValidator:
    """Centralized lifecycle validator for ACCUMULATION_SCANNER_V1 trade setups."""

    @staticmethod
    def validate_setup_contract(contract: Any) -> Dict[str, Any]:
        def get_val(key: str, default: Any = None):
            if isinstance(contract, dict):
                return contract.get(key, default)
            return getattr(contract, key, default)

        entry_type = get_val("entry_type")
        entry_price = float(get_val("entry_price", 0.0))
        preferred_entry = float(get_val("preferred_entry", 0.0))
        entry_trigger_level = float(get_val("entry_trigger_level", 0.0))
        entry_trigger_rule = get_val("entry_trigger_rule")
        entry_reference_type = get_val("entry_reference_type")
        target_1 = float(get_val("target_1", 0.0))
        target_2 = float(get_val("target_2", 0.0))
        target_3 = float(get_val("target_3", 0.0))
        breakout_level = float(get_val("breakout_level", 0.0))
        stop_loss = float(get_val("stop_loss", 0.0))
        status = get_val("status")
        entry_trigger_level_reached = get_val("entry_trigger_level_reached")

        # 1. Entry price equality check by method
        if entry_type == "ZONE_MIDPOINT":
            if entry_price != preferred_entry:
                return {"is_valid": False, "reason": "ZONE_MIDPOINT requires entry_price == preferred_entry"}
            if entry_trigger_rule != "RANGE_TOUCH":
                return {"is_valid": False, "reason": "ZONE_MIDPOINT requires entry_trigger_rule == RANGE_TOUCH"}
            if entry_reference_type != "STRATEGY_REFERENCE":
                return {"is_valid": False, "reason": "ZONE_MIDPOINT requires entry_reference_type == STRATEGY_REFERENCE"}
        elif entry_type == "BREAKOUT_CONFIRMATION":
            if entry_price != entry_trigger_level:
                return {"is_valid": False, "reason": "BREAKOUT_CONFIRMATION requires entry_price == entry_trigger_level"}
            if entry_trigger_rule != "LEVEL_CROSS":
                return {"is_valid": False, "reason": "BREAKOUT_CONFIRMATION requires entry_trigger_rule == LEVEL_CROSS"}
            if entry_reference_type != "CONFIRMED_LEVEL":
                return {"is_valid": False, "reason": "BREAKOUT_CONFIRMATION requires entry_reference_type == CONFIRMED_LEVEL"}
        else:
            return {"is_valid": False, "reason": f"Unknown entry_type: {entry_type}"}

        # 2. Universal Target Hierarchy Invariant: target_1 >= breakout_level AND target_1 > entry_price AND target_1 < target_2 < target_3
        if target_1 < breakout_level:
            return {"is_valid": False, "reason": f"Target 1 ₹{target_1} must be >= Breakout Level ₹{breakout_level}"}
        if target_1 <= entry_price:
            return {"is_valid": False, "reason": f"Target 1 ₹{target_1} must be > Entry Price ₹{entry_price}"}
        if not (target_1 < target_2 < target_3):
            return {"is_valid": False, "reason": f"Target order invariant violated: T1 ₹{target_1} < T2 ₹{target_2} < T3 ₹{target_3}"}

        # 3. Stop loss below entry
        if stop_loss >= entry_price:
            return {"is_valid": False, "reason": f"Stop loss ₹{stop_loss} must be < Entry Price ₹{entry_price}"}

        # 4. Lifecycle contract: ACTIVE_SETUP requires entry_trigger_level_reached IS NULL
        if status == "ACTIVE_SETUP" and entry_trigger_level_reached is not None:
            return {"is_valid": False, "reason": "ACTIVE_SETUP requires entry_trigger_level_reached == NULL"}

        return {"is_valid": True, "reason": "VALID"}
