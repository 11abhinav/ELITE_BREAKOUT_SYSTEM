"""
Forward Outcome Resolver & Scanner Execution Policies
Evaluates bar-by-bar future price paths according to scanner-specific execution policies.
Calculates Gross R, Net R (with exact 4-component 10 bps round-trip friction), MFE (R), MAE (R), and exit reason.
Enforces realistic gap-through-entry, gap-through-stop, conservative collision priority, and observation censoring.
"""

import os
import json
from enum import Enum
from dataclasses import dataclass
from datetime import datetime, time
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from engine.analytics.quality_contract import (
    QualityAlertContract, ScannerType, IntegrityStatus
)

FORWARD_TELEMETRY_LOG_DIR = "artifacts/telemetry/forward"
FORWARD_LEDGER_FILE = "artifacts/telemetry/forward_ledger.json"

# Standardized Canonical Transaction Friction: Exactly 10.0 bps Total Round-Trip
CANONICAL_ROUNDTRIP_FRICTION_BPS = 10.0  # 2.5 entry slip + 2.5 entry comm + 2.5 exit slip + 2.5 exit comm

class ObservationState(str, Enum):
    """
    Explicit 5-state forward observation lifecycle.
    RULE 67 RATIONALE: Separates incomplete future data (PARTIALLY_OBSERVED_PENDING) from permanent
    truncation (CENSORED) and true terminal trade completions (RESOLVED, RESOLVED_TIME_HORIZON).
    Only RESOLVED and RESOLVED_TIME_HORIZON count as valid forward evidence.
    """
    PENDING = "PENDING"
    PARTIALLY_OBSERVED_PENDING = "PARTIALLY_OBSERVED_PENDING"
    CENSORED = "CENSORED"
    RESOLVED = "RESOLVED"
    RESOLVED_TIME_HORIZON = "RESOLVED_TIME_HORIZON"

@dataclass
class ScannerExecutionPolicy:
    scanner: ScannerType
    timeframe: str
    max_holding_bars: int
    intraday_session_bounded: bool
    intrabar_collision_rule: str  # "CONSERVATIVE" (Evaluate Stop Loss first)
    gap_policy: str               # "REALISTIC_GAP_EXECUTION"

SCANNER_EXECUTION_POLICIES: Dict[ScannerType, ScannerExecutionPolicy] = {
    ScannerType.EOD: ScannerExecutionPolicy(
        scanner=ScannerType.EOD,
        timeframe="1D",
        max_holding_bars=20,
        intraday_session_bounded=False,
        intrabar_collision_rule="CONSERVATIVE",
        gap_policy="REALISTIC_GAP_EXECUTION"
    ),
    ScannerType.MULTIBAGGER: ScannerExecutionPolicy(
        scanner=ScannerType.MULTIBAGGER,
        timeframe="1D",
        max_holding_bars=60,
        intraday_session_bounded=False,
        intrabar_collision_rule="CONSERVATIVE",
        gap_policy="REALISTIC_GAP_EXECUTION"
    ),
    ScannerType.PULLBACK: ScannerExecutionPolicy(
        scanner=ScannerType.PULLBACK,
        timeframe="1D",
        max_holding_bars=15,
        intraday_session_bounded=False,
        intrabar_collision_rule="CONSERVATIVE",
        gap_policy="REALISTIC_GAP_EXECUTION"
    ),
    ScannerType.DAILY_BUILDER: ScannerExecutionPolicy(
        scanner=ScannerType.DAILY_BUILDER,
        timeframe="15m",
        max_holding_bars=25, # Single trading session (approx 25 15-minute bars)
        intraday_session_bounded=True,
        intrabar_collision_rule="CONSERVATIVE",
        gap_policy="REALISTIC_GAP_EXECUTION"
    ),
    ScannerType.MULTI_TF: ScannerExecutionPolicy(
        scanner=ScannerType.MULTI_TF,
        timeframe="5m",
        max_holding_bars=75, # 1-2 trading days (75 5-minute bars)
        intraday_session_bounded=False,
        intrabar_collision_rule="CONSERVATIVE",
        gap_policy="REALISTIC_GAP_EXECUTION"
    ),
    ScannerType.REVERSAL: ScannerExecutionPolicy(
        scanner=ScannerType.REVERSAL,
        timeframe="1D",
        max_holding_bars=10,
        intraday_session_bounded=False,
        intrabar_collision_rule="CONSERVATIVE",
        gap_policy="REALISTIC_GAP_EXECUTION"
    ),
    ScannerType.WEALTH_ENGINE: ScannerExecutionPolicy(
        scanner=ScannerType.WEALTH_ENGINE,
        timeframe="1D",
        max_holding_bars=90, # Quarterly holding horizon
        intraday_session_bounded=False,
        intrabar_collision_rule="CONSERVATIVE",
        gap_policy="REALISTIC_GAP_EXECUTION"
    )
}

def resolve_trade_path(
    alert: Dict[str, Any],
    df_future_bars: pd.DataFrame,
    scanner_type: Optional[ScannerType] = None,
    decision_timestamp: Optional[datetime] = None,
    observation_complete: bool = False
) -> Dict[str, Any]:
    """
    Evaluates the bar-by-bar path of a trade from entry to exit using scanner-specific policies.
    Guarantees:
      1. Strictly filters future bars to bar_timestamp > decision_timestamp (if index is DatetimeIndex).
      2. Handles gaps through entry and stops/targets for both LONG and SHORT.
      3. Conservative intrabar collision rule: Stop loss evaluated strictly before profit target.
      4. Exact 4-component transaction friction:
         Friction = 0.00025*E + 0.00025*E + 0.00025*X + 0.00025*X = 0.0005*(E + X)
      5. Strict observation state machine & censoring distinction.
    """
    sc = scanner_type or ScannerType(alert.get("scanner", "EOD"))
    policy = SCANNER_EXECUTION_POLICIES.get(sc, SCANNER_EXECUTION_POLICIES[ScannerType.EOD])
    
    entry_p = float(alert.get("entry_price", 0.0))
    stop_p = float(alert.get("stop_price", 0.0))
    target_p = float(alert.get("target_price", 0.0))
    risk_dist = abs(entry_p - stop_p)
    
    if risk_dist <= 0 or df_future_bars is None or df_future_bars.empty:
        return {
            "outcome_status": IntegrityStatus.CORRUPTED_RECORD.value,
            "observation_state": ObservationState.CENSORED.value,
            "realized_R": 0.0,
            "gross_realized_R": 0.0,
            "net_realized_R": 0.0,
            "mfe_R": 0.0,
            "mae_R": 0.0,
            "is_valid_evidence": False,
            "exit_reason": "INVALID_GEOMETRY_OR_EMPTY_DATA"
        }
        
    side = alert.get("side", "LONG").upper()

    # Slicing strictly future bars after decision_timestamp if provided
    if decision_timestamp and isinstance(df_future_bars.index, pd.DatetimeIndex):
        valid_future = df_future_bars[df_future_bars.index > decision_timestamp]
    else:
        valid_future = df_future_bars

    if valid_future.empty:
        return {
            "outcome_status": IntegrityStatus.PENDING.value,
            "observation_state": ObservationState.PENDING.value,
            "realized_R": 0.0,
            "gross_realized_R": 0.0,
            "net_realized_R": 0.0,
            "mfe_R": 0.0,
            "mae_R": 0.0,
            "is_valid_evidence": False,
            "exit_reason": "AWAITING_FUTURE_BARS"
        }

    bars = valid_future.iloc[:policy.max_holding_bars]
    
    max_favorable = 0.0
    max_adverse = 0.0
    exit_p = None
    exit_reason = None
    exit_bar_idx = None
    
    # Handle entry bar gap
    first_open = float(bars["Open"].iloc[0]) if "Open" in bars.columns else entry_p
    actual_entry_p = entry_p
    if side == "LONG" and first_open > entry_p:
        actual_entry_p = first_open # Executed at open if gapped up
    elif side == "SHORT" and first_open < entry_p:
        actual_entry_p = first_open # Executed at open if gapped down
        
    for idx, (ts, row) in enumerate(bars.iterrows()):
        h = float(row["High"])
        l = float(row["Low"])
        c = float(row["Close"])
        o = float(row["Open"]) if "Open" in row else c
        
        if side == "LONG":
            fav = h - actual_entry_p
            adv = actual_entry_p - l
            max_favorable = max(max_favorable, fav)
            max_adverse = max(max_adverse, adv)
            
            # Conservative collision rule: Stop loss evaluated strictly first
            if l <= stop_p:
                exit_p = min(o, stop_p) if o <= stop_p else stop_p # Gap down through stop
                exit_reason = "STOP_LOSS_HIT"
                exit_bar_idx = idx
                break
            elif h >= target_p:
                exit_p = max(o, target_p) if o >= target_p else target_p # Gap up through target
                exit_reason = "TARGET_HIT"
                exit_bar_idx = idx
                break
        else: # SHORT
            fav = actual_entry_p - l
            adv = h - actual_entry_p
            max_favorable = max(max_favorable, fav)
            max_adverse = max(max_adverse, adv)
            
            # Conservative collision rule: Stop loss evaluated strictly first
            if h >= stop_p:
                exit_p = max(o, stop_p) if o >= stop_p else stop_p # Gap up through stop
                exit_reason = "STOP_LOSS_HIT"
                exit_bar_idx = idx
                break
            elif l <= target_p:
                exit_p = min(o, target_p) if o <= target_p else target_p # Gap down through target
                exit_reason = "TARGET_HIT"
                exit_bar_idx = idx
                break

    # Observation State & Censoring Evaluation
    if exit_p is not None:
        # Terminal price level reached
        obs_state = ObservationState.RESOLVED
        outcome_status = IntegrityStatus.VALID.value
        is_valid = True
    elif len(bars) >= policy.max_holding_bars:
        # Completed full holding horizon
        exit_p = float(bars["Close"].iloc[-1])
        exit_reason = "SESSION_EXPIRATION" if policy.intraday_session_bounded else "TIME_HORIZON_EXIT"
        exit_bar_idx = len(bars) - 1
        obs_state = ObservationState.RESOLVED_TIME_HORIZON
        outcome_status = IntegrityStatus.VALID.value
        is_valid = True
    else:
        # Incomplete horizon (< max_holding_bars) and no terminal level hit
        if observation_complete:
            # Dataset permanently ended without completing horizon
            exit_p = float(bars["Close"].iloc[-1])
            exit_reason = "DATASET_ENDED_CENSORED"
            exit_bar_idx = len(bars) - 1
            obs_state = ObservationState.CENSORED
            outcome_status = "CENSORED"
            is_valid = False
        else:
            # Awaiting further market bars
            obs_state = ObservationState.PARTIALLY_OBSERVED_PENDING
            outcome_status = IntegrityStatus.PENDING.value
            exit_reason = "PENDING_INCOMPLETE_HORIZON"
            exit_bar_idx = len(bars) - 1
            is_valid = False
            return {
                "outcome_status": outcome_status,
                "observation_state": obs_state.value,
                "entry_price_executed": round(actual_entry_p, 4),
                "bars_observed": len(bars),
                "max_holding_bars": policy.max_holding_bars,
                "exit_reason": exit_reason,
                "gross_realized_R": 0.0,
                "net_realized_R": 0.0,
                "mfe_R": round(max_favorable / risk_dist, 4),
                "mae_R": round(max_adverse / risk_dist, 4),
                "is_valid_evidence": False
            }

    # Gross PnL (Directional)
    gross_pnl = (exit_p - actual_entry_p) if side == "LONG" else (actual_entry_p - exit_p)
    gross_R = gross_pnl / risk_dist
    
    # RULE 67 RATIONALE: Exact 4-Component 10 bps Transaction Friction Model
    # Entry Slippage: 2.5 bps * Entry Price
    # Entry Commission: 2.5 bps * Entry Price
    # Exit Slippage: 2.5 bps * Exit Price
    # Exit Commission: 2.5 bps * Exit Price
    # Total Friction = 0.0005 * (actual_entry_p + exit_p)
    entry_slippage = actual_entry_p * (2.5 / 10000.0)
    entry_commission = actual_entry_p * (2.5 / 10000.0)
    exit_slippage = exit_p * (2.5 / 10000.0)
    exit_commission = exit_p * (2.5 / 10000.0)
    total_friction = entry_slippage + entry_commission + exit_slippage + exit_commission

    net_pnl = gross_pnl - total_friction
    net_R = net_pnl / risk_dist
    
    mfe_R = max_favorable / risk_dist
    mae_R = max_adverse / risk_dist

    return {
        "outcome_status": outcome_status,
        "observation_state": obs_state.value,
        "is_valid_evidence": is_valid,
        "entry_price_executed": round(actual_entry_p, 4),
        "exit_price": round(exit_p, 4),
        "exit_reason": exit_reason,
        "exit_bars_held": exit_bar_idx + 1,
        "gross_pnl": round(gross_pnl, 4),
        "net_pnl": round(net_pnl, 4),
        "gross_realized_R": round(gross_R, 4),
        "net_realized_R": round(net_R, 4),
        "mfe_R": round(mfe_R, 4),
        "mae_R": round(mae_R, 4),
        "entry_slippage": round(entry_slippage, 4),
        "entry_commission": round(entry_commission, 4),
        "exit_slippage": round(exit_slippage, 4),
        "exit_commission": round(exit_commission, 4),
        "total_friction": round(total_friction, 4),
        "policy_timeframe": policy.timeframe,
        "roundtrip_friction_bps": CANONICAL_ROUNDTRIP_FRICTION_BPS
    }

