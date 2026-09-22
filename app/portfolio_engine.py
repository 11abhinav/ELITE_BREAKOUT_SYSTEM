"""
PortfolioEngine — Score-based fixed capital bucket allocation.

[VERSION: CAPITAL_BUCKET_v2.0]
New model replaces the old risk-unit / available-cash sizing.
Each entered trade gets a fixed capital bucket based purely on its confidence score:
  score >= 85  → ₹1,00,000  (High Conviction)
  score 70-84  → ₹50,000    (Medium Conviction)
  score < 70   → ₹25,000    (Standard)

No total-capital limit. No available-cash check. No capital_history dependency.
shares_bought = floor(bucket / entry_price)
"""
import logging
from math import floor
from typing import Tuple

logger = logging.getLogger(__name__)

# ── Score bucket thresholds ────────────────────────────────────────────────────
BUCKET_HIGH   = 100_000.0   # ₹1,00,000 — score >= 85
BUCKET_MEDIUM =  50_000.0   # ₹50,000   — score 70-84
BUCKET_LOW    =  25_000.0   # ₹25,000   — score < 70

SCORE_HIGH_THRESHOLD   = 85
SCORE_MEDIUM_THRESHOLD = 70


def get_score_bucket(score) -> float:
    """Return the fixed capital bucket for a given score."""
    s = float(score) if score else 0.0
    if s >= SCORE_HIGH_THRESHOLD:
        return BUCKET_HIGH
    elif s >= SCORE_MEDIUM_THRESHOLD:
        return BUCKET_MEDIUM
    else:
        return BUCKET_LOW


def calculate_score_bucket_allocation(entry_price: float, score) -> Tuple[float, int]:
    """
    [VERSION: CAPITAL_BUCKET_v2.0] Score-based fixed bucket allocation.
    Returns (capital_allocated, shares_bought).

    Rules:
      - score >= 85 → ₹1,00,000
      - score 70-84 → ₹50,000
      - score < 70  → ₹25,000
      - shares_bought = floor(bucket / entry_price)
      - No available-cash check; no capital_history dependency.
    """
    try:
        entry_price = float(entry_price)
        score_val = float(score) if score else 0.0
    except (TypeError, ValueError):
        return 0.0, 0

    if entry_price <= 0:
        return 0.0, 0

    bucket = get_score_bucket(score_val)
    shares = floor(bucket / entry_price)

    if shares <= 0:
        return 0.0, 0

    capital = float(shares * entry_price)
    logger.info(
        f"💼 ScoreBucket | score={score_val} → bucket=₹{bucket:,.0f} | "
        f"entry=₹{entry_price:.2f} → {shares} shares | capital=₹{capital:,.2f}"
    )
    return capital, shares


# ── Legacy alias: calculate_trade_allocation (import-compat with database.py) ──
def calculate_trade_allocation(entry_price: float, stop_loss: float = 0.0, score=80) -> Tuple[float, int]:
    """
    [VERSION: CAPITAL_BUCKET_v2.0] Legacy name → delegates to score bucket.
    stop_loss parameter retained for call-site compatibility but ignored.
    """
    return calculate_score_bucket_allocation(entry_price, score)


# ── Stub: get_portfolio_state (import-compat) ──────────────────────────────────
def get_portfolio_state() -> dict:
    """
    [VERSION: CAPITAL_BUCKET_v2.0] Stub — capital tracking removed.
    Returns zeroed-out structure so any remaining callers don't crash.
    """
    return {
        "total_equity":     0.0,
        "available_margin": 0.0,
        "deployed_margin":  0.0,
    }


# ── Legacy constants (import-compat) ──────────────────────────────────────────
BASE_CAPITAL     = 0.0
RISK_PERCENT     = 0.0
MAX_POSITION_PCT = 0.0


# =====================================================================================
# PortfolioEngine class (retained for import compatibility)
# =====================================================================================
class PortfolioEngine:

    @staticmethod
    def _get_current_open_risk_pct() -> float:
        return 0.0

    @staticmethod
    def execute_ranked_candidates(ranked_candidates: list, policy: dict = None) -> list:
        """
        [VERSION: CAPITAL_BUCKET_v2.0] With unlimited capital and fixed score buckets,
        all ranked candidates are funded based on their individual confidence/score.
        """
        if not ranked_candidates:
            return []

        allocations = []
        for c in ranked_candidates:
            symbol = c.get("symbol", "?")
            ep = float(c.get("entry_price") or 0.0)
            score = int(c.get("technical_score") or c.get("score") or 80)
            cap, shares = calculate_score_bucket_allocation(ep, score)

            allocation = {
                "status": "FUNDED",
                "capital": cap,
                "shares": shares,
                "risk_used": 0.0,
                "remaining_risk": 999.0,
                "allocation_pct": 0.0,
            }
            c["status"] = "FUNDED"
            c["allocation"] = allocation
            c["capital_allocated"] = cap
            c["shares_bought"] = shares
            allocations.append(allocation)
            logger.info(f"✅ PortfolioEngine: {symbol} FUNDED via score bucket (score={score}, ₹{cap:,.0f}, {shares} shares)")

        return allocations


