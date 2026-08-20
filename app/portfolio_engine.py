"""
PortfolioEngine — Capital allocation via Risk Units.

Answers: "Which of the ranked trades can we actually afford?"

Separation of Concerns:
  TradeRankingEngine → "Which trades are best?"
  PortfolioEngine    → "Which of these can we actually afford?"

Returns a rich allocation dict on each funding decision.
"""
import logging

logger = logging.getLogger(__name__)


class PortfolioEngine:

    @staticmethod
    def _get_current_open_risk_pct() -> float:
        """
        Returns current open risk as a % of account across all live positions.
        V1: stub — returns 0.0.
        V2: query broker/DB for open positions and sum their risk %.
        """
        return 0.0

    @staticmethod
    def execute_ranked_candidates(ranked_candidates: list, policy: dict) -> list[dict]:
        """
        Allocates capital to the top-ranked candidates until risk budget is consumed.

        Modifies candidates in-place:
          - FUNDED           → funded, allocation dict attached
          - (else left as QUALIFIED for OpportunityManager to mark REJECTED_CAPITAL)

        Returns list of allocation dicts for the funded candidates.
        """
        if not ranked_candidates:
            return []

        risk_cfg      = policy.get("risk", {})
        max_open_risk = risk_cfg.get("max_open_risk", 6.0)     # % of account
        risk_mult     = risk_cfg.get("multiplier",    1.0)
        max_new_pos   = risk_cfg.get("max_new_positions", 5)

        current_risk    = PortfolioEngine._get_current_open_risk_pct()
        remaining_risk  = max_open_risk - current_risk
        base_risk_trade = 1.0 * risk_mult                      # % per trade

        logger.info(
            f"💼 PortfolioEngine | "
            f"max_open={max_open_risk:.1f}% | current={current_risk:.1f}% | "
            f"remaining={remaining_risk:.2f}% | risk_per_trade={base_risk_trade:.2f}% | "
            f"max_new={max_new_pos}"
        )

        allocations = []
        funded_count = 0

        for c in ranked_candidates:
            symbol = c.get("symbol", "?")
            rank   = c.get("ranking_breakdown", {}).get("global_rank", "?")

            if funded_count >= max_new_pos:
                logger.info(f"⏭️  {symbol} (Rank {rank}) — max new positions reached ({max_new_pos})")
                continue

            if remaining_risk < base_risk_trade:
                logger.info(
                    f"⏭️  {symbol} (Rank {rank}) — "
                    f"insufficient risk capacity ({remaining_risk:.2f}% < {base_risk_trade:.2f}%)"
                )
                continue

            # ── Fund this trade ─────────────────────────────────────────────
            remaining_risk -= base_risk_trade
            funded_count   += 1

            allocation = {
                "status":         "FUNDED",
                "risk_used":      round(base_risk_trade, 4),
                "remaining_risk": round(remaining_risk, 4),
                # allocation in currency requires account size — placeholder for V2
                "allocation_pct": round(base_risk_trade, 4),
            }

            c["status"]     = "FUNDED"
            c["allocation"] = allocation
            allocations.append(allocation)

            logger.info(
                f"✅ {symbol} FUNDED (Rank {rank}) | "
                f"risk_used={base_risk_trade:.2f}% | remaining={remaining_risk:.2f}%"
            )

        logger.info(
            f"💼 PortfolioEngine complete: {funded_count} funded, "
            f"{len(ranked_candidates) - funded_count} awaiting capacity decision."
        )
        return allocations

# =====================================================================================
# LEGACY V1 METHODS (Required by database.py for UI/Admin and recalculation endpoints)
# =====================================================================================
from typing import Tuple
from math import floor
from database import get_connection, get_capital_info

BASE_CAPITAL = 500000.0
RISK_PERCENT = 0.01  # 1% of total equity risked per trade
MAX_POSITION_PCT = 0.03  # hard cap on capital allocated to a single trade (3% of equity)

def get_portfolio_state() -> dict:
    """
    Returns the exact current state of the Live Portfolio:
    - total_equity (Realized)
    - available_margin
    - deployed_margin
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            # 1. Total realized PnL
            cur.execute("SELECT COALESCE(SUM(pnl_rs), 0) FROM alerts WHERE status IN ('WIN', 'LOSS') AND is_rejected = FALSE")
            r1 = cur.fetchone()
            realized_pnl = float((r1[0] if r1 else 0.0) or 0.0)
            
            # 2. Total allocated capital in open trades
            cur.execute("SELECT COALESCE(SUM(capital_allocated), 0) FROM alerts WHERE status = 'OPEN' AND is_rejected = FALSE")
            r2 = cur.fetchone()
            deployed_margin = float((r2[0] if r2 else 0.0) or 0.0)
            
    cap_info = get_capital_info()
    base_capital = cap_info.get("total_capital", BASE_CAPITAL)
    total_equity = base_capital + realized_pnl
    available_margin = total_equity - deployed_margin
    
    return {
        "total_equity": total_equity,
        "available_margin": available_margin,
        "deployed_margin": deployed_margin,
    }

def calculate_trade_allocation(entry_price: float, stop_loss: float, score: int = 80) -> Tuple[float, int]:
    """
    Legacy Risk-based sizing (institutional style) for fallback / UI usage.
    Returns (capital_allocated, shares_bought)
    """
    try:
        entry_price = float(entry_price)
        stop_loss = float(stop_loss)
    except Exception:
        stop_loss = 0.0

    if entry_price <= 0:
        return 0.0, 0
        
    if stop_loss <= 0:
        stop_loss = entry_price * 0.90

    state = get_portfolio_state()
    total_equity = state["total_equity"]
    available_margin = state["available_margin"]

    base_risk_percent = RISK_PERCENT
    risk_percent = min(0.05, base_risk_percent * 2) if score >= 90 else base_risk_percent
    per_trade_risk = total_equity * risk_percent

    per_share_risk = abs(entry_price - stop_loss)
    if per_share_risk <= 0:
        return 0.0, 0

    shares_by_risk = floor(per_trade_risk / per_share_risk)
    if shares_by_risk <= 0:
        return 0.0, 0

    capital_required = shares_by_risk * entry_price

    max_allocation = total_equity * MAX_POSITION_PCT
    if capital_required > max_allocation:
        shares_by_risk = floor(max_allocation / entry_price)
        capital_required = shares_by_risk * entry_price

    if capital_required > available_margin:
        shares_by_cash = floor(available_margin / entry_price)
        shares_to_buy = max(0, min(shares_by_risk, shares_by_cash))
    else:
        shares_to_buy = int(shares_by_risk)

    final_capital = float(shares_to_buy * entry_price)
    return final_capital, shares_to_buy
