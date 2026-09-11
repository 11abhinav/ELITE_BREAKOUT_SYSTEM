#!/usr/bin/env python3
# =============================================================================
# engine/production/v520_gem_router_engine.py
# V5.20 PRODUCTION GEM-AWARE ROUTING & DYNAMIC RISK ALLOCATION ENGINE
# =============================================================================
# Implements the frozen production package:
#   - Daily Builder Gem Engine (GEM_CORE & GEM_ULTRA)
#   - 60-minute state lifetime horizon & persistence decay management
#   - Multi-tier scanner priority queueing & dynamic risk allocation
#   - Two-stage quality ranking (Top 20% quality filter)
#   - Portfolio-level capacity & concurrent risk limits
#   - Zero weekend candle enforcement
# =============================================================================

import os
import sys
import json
import zoneinfo
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
IST = zoneinfo.ZoneInfo("Asia/Kolkata")

class DailyBuilderGemDetector:
    """
    Evaluates market candles for Daily Builder Gem State ignition.
    Frozen under V5.20 governance:
      - GEM_CORE: ORB20 + Top 10% Quality Score + Mandatory Vetoes
      - GEM_ULTRA: ORB30 + Top 20% Quality Score + Mandatory Vetoes
    """
    @staticmethod
    def evaluate_gem(
        symbol: str,
        timestamp: datetime,
        orb_mode: str, # "ORB20" or "ORB30"
        orb_high: float,
        orb_low: float,
        close_price: float,
        rs_rating: float,
        clv: float,
        rvol: float,
        nifty_above_vwap: bool,
        overhead_runway_r: float,
        sector_breadth_pct: float,
        open_gap_pct: float,
        candle1_5m_rvol: float,
        scanner_quality_score: float # 0 - 100
    ) -> Dict[str, Any]:
        if timestamp.weekday() in (5, 6):
            raise ValueError(f"CRITICAL INVARIANT VIOLATION: Weekend timestamp {timestamp} prohibited.")

        # Mandatory Vetoes Audit
        if not nifty_above_vwap:
            return {"gem_active": False, "status": "VETO_NIFTY_BELOW_VWAP"}
        if overhead_runway_r < 1.5:
            return {"gem_active": False, "status": "VETO_INSUFFICIENT_OVERHEAD_RUNWAY"}
        if sector_breadth_pct < 50.0:
            return {"gem_active": False, "status": "VETO_SECTOR_DIVERGENCE"}
        if open_gap_pct > 3.5:
            return {"gem_active": False, "status": "VETO_OPENING_GAP_TOO_LARGE"}
        if candle1_5m_rvol > 6.0:
            return {"gem_active": False, "status": "VETO_VOLUME_CLIMAX_EXHAUSTION"}

        # Baseline Momentum Gating
        if rs_rating < 70.0 or clv < 0.75 or rvol < 1.4:
            return {"gem_active": False, "status": "GATING_FAILED_MOMENTUM"}

        # Mode-specific Quality Cutoff
        if orb_mode == "ORB20":
            # Top 10% quality threshold (Score >= 88.5)
            if scanner_quality_score >= 88.5 and close_price > orb_high:
                return {
                    "gem_active": True,
                    "gem_mode": "GEM_CORE",
                    "state_ttl_minutes": 60,
                    "force_exit_time": "15:15 IST",
                    "status": "ACTIVE_GEM_CORE_TRIGGERED"
                }
        elif orb_mode == "ORB30":
            # Top 20% quality threshold (Score >= 80.0)
            if scanner_quality_score >= 80.0 and close_price > orb_high:
                return {
                    "gem_active": True,
                    "gem_mode": "GEM_ULTRA",
                    "state_ttl_minutes": 60,
                    "force_exit_time": "15:15 IST",
                    "status": "ACTIVE_GEM_ULTRA_TRIGGERED"
                }

        return {"gem_active": False, "status": "NO_TRIGGER"}


class GemStateRouter:
    """
    Manages active Gem state lifetime and routes scanner priority and risk sizing.
    """
    def __init__(self, state_ttl_minutes: int = 60):
        self.state_ttl = timedelta(minutes=state_ttl_minutes)
        self.active_gem_until: Optional[datetime] = None
        self.active_gem_symbol: Optional[str] = None
        self.active_gem_mode: Optional[str] = None

    def update_gem_state(self, gem_payload: Dict[str, Any], timestamp: datetime):
        if gem_payload.get("gem_active"):
            self.active_gem_until = timestamp + self.state_ttl
            self.active_gem_symbol = gem_payload.get("symbol")
            self.active_gem_mode = gem_payload.get("gem_mode")

    def is_gem_active(self, current_time: datetime) -> bool:
        if self.active_gem_until is None:
            return False
        return current_time <= self.active_gem_until

    def route_scanner(
        self,
        scanner_name: str,
        current_time: datetime,
        scanner_quality_score: float, # 0 - 100
        quality_top20_threshold: float = 75.0
    ) -> Dict[str, Any]:
        """
        Calculates priority, risk multiplier, and two-stage ranking eligibility.
        """
        gem_active = self.is_gem_active(current_time)
        passes_quality_filter = scanner_quality_score >= quality_top20_threshold

        tier1_beneficiaries = ["REVERSAL", "PULLBACK_V2", "MULTITF_1H", "MULTIBAGGER"]
        tier2_neutrals = ["EOD_BREAKOUT", "ACCUMULATION_VCP", "MULTITF_5M", "WEALTH", "TECHNICAL_AHAT"]
        tier3_inverses = ["SHORT_COVERING"]

        if gem_active:
            if scanner_name in tier1_beneficiaries:
                return {
                    "ecosystem_tier": "Tier 1: High Synergy",
                    "priority": 1,
                    "risk_allocation_r": 1.50,
                    "gem_active": True,
                    "quality_top20_passed": passes_quality_filter,
                    "executable": passes_quality_filter,
                    "action": "ROUTE_TOP_PRIORITY_SCALED_RISK"
                }
            elif scanner_name in tier2_neutrals:
                return {
                    "ecosystem_tier": "Tier 2: Neutral / Robust",
                    "priority": 2,
                    "risk_allocation_r": 1.00,
                    "gem_active": True,
                    "quality_top20_passed": passes_quality_filter,
                    "executable": passes_quality_filter,
                    "action": "ROUTE_STANDARD_PRIORITY_BASE_RISK"
                }
            elif scanner_name in tier3_inverses:
                return {
                    "ecosystem_tier": "Tier 3: Inverse Decoupled",
                    "priority": 3,
                    "risk_allocation_r": 0.50,
                    "gem_active": True,
                    "quality_top20_passed": passes_quality_filter,
                    "executable": passes_quality_filter,
                    "action": "ROUTE_DEPRIORITIZED_HALVED_RISK"
                }
        else:
            # Standalone execution when No Gem is active
            return {
                "ecosystem_tier": "Baseline Standalone",
                "priority": 2,
                "risk_allocation_r": 1.00,
                "gem_active": False,
                "quality_top20_passed": True, # Normal standalone execution
                "executable": True,
                "action": "ROUTE_NORMAL_STANDALONE_EXECUTION"
            }


class PortfolioRiskController:
    """
    Enforces master portfolio governance invariants:
      - Max concurrent positions: 8
      - Max sector concentration: 25%
      - Max single symbol risk: 1.5R
      - Max aggregate open portfolio risk: 8.0R
      - Daily loss stop circuit breaker: 3.0R
    """
    def __init__(
        self,
        max_positions: int = 8,
        max_sector_pct: float = 25.0,
        max_symbol_risk_r: float = 1.50,
        max_portfolio_open_risk_r: float = 8.0,
        daily_loss_limit_r: float = 3.0
    ):
        self.max_positions = max_positions
        self.max_sector_pct = max_sector_pct
        self.max_symbol_risk_r = max_symbol_risk_r
        self.max_portfolio_open_risk_r = max_portfolio_open_risk_r
        self.daily_loss_limit_r = daily_loss_limit_r

    def validate_new_order(
        self,
        symbol: str,
        sector: str,
        requested_risk_r: float,
        current_open_positions: List[Dict[str, Any]],
        realized_daily_loss_r: float
    ) -> Tuple[bool, str]:
        # Daily loss circuit breaker
        if realized_daily_loss_r >= self.daily_loss_limit_r:
            return False, "CIRCUIT_BREAKER_DAILY_LOSS_LIMIT_REACHED"

        # Position count ceiling
        if len(current_open_positions) >= self.max_positions:
            return False, "PORTFOLIO_CAPACITY_MAX_POSITIONS_REACHED"

        # Symbol allocation cap
        if requested_risk_r > self.max_symbol_risk_r:
            return False, "RISK_CAP_EXCEEDS_MAX_SYMBOL_ALLOCATION"

        # Aggregate open risk ceiling
        current_open_risk = sum(p.get("risk_r", 1.0) for p in current_open_positions)
        if (current_open_risk + requested_risk_r) > self.max_portfolio_open_risk_r:
            return False, "PORTFOLIO_CAPACITY_MAX_OPEN_RISK_REACHED"

        # Sector concentration cap
        sector_positions = [p for p in current_open_positions if p.get("sector") == sector]
        sector_allocation_pct = ((len(sector_positions) + 1) / (len(current_open_positions) + 1)) * 100.0
        if sector_allocation_pct > self.max_sector_pct and len(current_open_positions) >= 4:
            return False, "SECTOR_CONCENTRATION_LIMIT_EXCEEDED"

        return True, "ORDER_APPROVED_FOR_EXECUTION"
