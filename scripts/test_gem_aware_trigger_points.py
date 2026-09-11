#!/usr/bin/env python3
# =============================================================================
# scripts/test_gem_aware_trigger_points.py
# Verification of Gem-Aware Scanner Trigger Points & Risk Routing
# =============================================================================

import os
import sys
from datetime import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP_DIR = os.path.join(_REPO_ROOT, "app")
for p in [_REPO_ROOT, _APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from app.trade_ranking_engine import TradeRankingEngine
except ImportError:
    from trade_ranking_engine import TradeRankingEngine

from engine.production.v520_gem_router_engine import (
    DailyBuilderGemDetector,
    GemStateRouter,
    PortfolioRiskController
)

def test_trigger_point_architecture():
    print("=" * 80)
    print("TESTING GEM-AWARE SCANNER TRIGGER POINT & ROUTING ARCHITECTURE")
    print("=" * 80)

    # 1. Test Daily Builder Gem Ignition Trigger Point
    dt_morning = datetime(2026, 9, 11, 9, 36) # Friday (Valid weekday)
    gem_result = DailyBuilderGemDetector.evaluate_gem(
        symbol="HDFCBANK",
        timestamp=dt_morning,
        orb_mode="ORB20",
        orb_high=1650.0,
        orb_low=1630.0,
        close_price=1652.5,
        rs_rating=82.0,
        clv=0.88,
        rvol=2.4,
        nifty_above_vwap=True,
        overhead_runway_r=2.5,
        sector_breadth_pct=72.0,
        open_gap_pct=1.8,
        candle1_5m_rvol=2.8,
        scanner_quality_score=92.0
    )
    print(f"\n1. Daily Builder Gem Trigger Evaluation: {gem_result}")
    assert gem_result["gem_active"] is True
    assert gem_result["gem_mode"] == "GEM_CORE"
    assert gem_result["state_ttl_minutes"] == 60

    # 2. Test Gem State Router Life Cycle
    router = GemStateRouter(state_ttl_minutes=60)
    router.update_gem_state(gem_result, timestamp=dt_morning)

    # During Gem (9:50 AM)
    dt_active = datetime(2026, 9, 11, 9, 50)
    assert router.is_gem_active(dt_active) is True
    print("\n2. Gem State Router Status at 09:50 IST: ACTIVE (TTL 60 min)")

    # After Gem Expired (10:45 AM)
    dt_expired = datetime(2026, 9, 11, 10, 45)
    assert router.is_gem_active(dt_expired) is False
    print("   Gem State Router Status at 10:45 IST: EXPIRED -> NORMAL ROUTE")

    # 3. Test Scanner Trigger Points During Active Gem
    cands = [
        {"symbol": "TATASTEEL", "scanner": "Reversal", "technical_score": 88.0, "volume_ratio": 2.2, "rr_ratio": 2.8},
        {"symbol": "SBIN", "scanner": "Pullback V2", "technical_score": 82.0, "volume_ratio": 1.9, "rr_ratio": 2.4},
        {"symbol": "RELIANCE", "scanner": "EOD Breakout", "technical_score": 90.0, "volume_ratio": 1.6, "rr_ratio": 2.1},
        {"symbol": "INFY", "scanner": "Short Covering", "technical_score": 78.0, "volume_ratio": 1.7, "rr_ratio": 2.0}
    ]

    ranked_gem = TradeRankingEngine.rank_candidates_gem_aware(cands.copy(), gem_active=True)
    print("\n3. Scanner Trigger Points & Risk Routing During Active Gem:")
    for c in ranked_gem:
        routing = c["gem_routing"]
        print(f"   • {c['symbol']:<10} [{c['scanner']:<14}] -> Priority {routing['priority']} | Risk {routing['risk_allocation_r']:.2f}R | {routing['ecosystem_tier']}")

    # Tier 1 Priority 1 verification
    assert ranked_gem[0]["gem_routing"]["priority"] == 1
    assert ranked_gem[0]["gem_routing"]["risk_allocation_r"] == 1.50

    # Short covering Tier 3 verification
    sc_cand = [c for c in ranked_gem if c["scanner"] == "Short Covering"][0]
    assert sc_cand["gem_routing"]["priority"] == 3
    assert sc_cand["gem_routing"]["risk_allocation_r"] == 0.50

    # 4. Test Portfolio Risk Controller
    controller = PortfolioRiskController(max_positions=8, max_sector_pct=25.0, daily_loss_limit_r=3.0)
    current_positions = [
        {"symbol": "TATASTEEL", "sector": "METALS", "risk_r": 1.50},
        {"symbol": "JSWSTEEL", "sector": "METALS", "risk_r": 1.50}
    ]
    approved, reason = controller.validate_new_order(
        symbol="SBIN",
        sector="BANKING",
        requested_risk_r=1.50,
        current_open_positions=current_positions,
        realized_daily_loss_r=0.5
    )
    print(f"\n4. Portfolio Risk Governance Check: {approved} ({reason})")
    assert approved is True

    print("\n" + "=" * 80)
    print("ALL GEM-AWARE TRIGGER POINTS & ROUTING LOGIC VERIFIED SUCCESSFULLY")
    print("=" * 80)

if __name__ == "__main__":
    test_trigger_point_architecture()
