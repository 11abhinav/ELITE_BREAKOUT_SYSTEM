"""
OpportunityManager — V1.5 Final Architecture.

Owns all business logic for the candidate lifecycle:
  - Freshness validation (time decay + price drift)
  - Duplicate suppression
  - Ranking trigger
  - Funding trigger
  - Persistence of ALL outcomes (FUNDED, REJECTED_CAPITAL, REJECTED_RANK, EXPIRED)

Uses CandidatePool as its storage backend.
V2 upgrade: swap InMemoryCandidatePool → RedisCandidatePool.
No scanner changes required.

Candidate Lifecycle:
  FOUND → QUALIFIED → RANKED → FUNDED       → EXECUTED
                             → REJECTED_CAPITAL (persist)
                             → REJECTED_RANK    (persist)
                    → EXPIRED                  (persist)
"""
import logging
from datetime import datetime, timezone

from candidate_pool import CandidatePool, InMemoryCandidatePool

logger = logging.getLogger(__name__)


# Maximum acceptable price drift from the original entry price, by scanner type.
# If the current price has moved beyond this %, the opportunity is stale regardless of age.
MAX_ENTRY_DRIFT = {
    "MULTI_TF":  0.75,   # 0.75% — intraday entries must be precise
    "EOD":       1.50,   # 1.50% — EOD allows slightly wider entries
    "REVERSAL":  2.00,   # 2.00% — reversal entries are more flexible
}

# Time-decay table: (age_seconds_min, age_seconds_max, freshness_pct)
FRESHNESS_TABLE = [
    (0,    120,  100),   # 0–2 min: perfect
    (120,  300,   95),   # 2–5 min: excellent
    (300,  600,   90),   # 5–10 min: good
    (600,  1800,  75),   # 10–30 min: fair, decaying
    (1800, float("inf"), 0),  # >30 min: reject
]


class OpportunityManager:
    """
    Owns the full candidate lifecycle. Uses a CandidatePool for storage.

    Usage (in scanner):
        pool = OpportunityManager(policy=regime_ctx.get("policy", {}))
        pool.add(candidate_dict)   # inside the stock loop
        pool.process()             # end of sweep
    """

    def __init__(self, policy: dict, pool: CandidatePool = None):
        self.policy    = policy
        self._pool     = pool if pool is not None else InMemoryCandidatePool()
        self._outcomes: list[dict] = []   # All final outcomes for bulk persistence

    # ── Freshness checks ────────────────────────────────────────────────────

    @staticmethod
    def _time_freshness(found_at_ts: float) -> int:
        """Returns 0–100 freshness based on age in seconds. 0 = reject."""
        age_secs = datetime.now(timezone.utc).timestamp() - found_at_ts
        for lo, hi, score in FRESHNESS_TABLE:
            if lo <= age_secs < hi:
                return score
        return 0

    @staticmethod
    def _price_drift_ok(candidate: dict) -> bool:
        """
        Returns True if price has NOT drifted too far from the intended entry.
        If current_price is not available, defaults to True (no rejection).
        """
        entry   = candidate.get("entry_price")
        current = candidate.get("current_price")
        scanner = candidate.get("scanner", "MULTI_TF").upper()
        max_drift = MAX_ENTRY_DRIFT.get(scanner, 1.0)

        if not entry or not current or entry <= 0:
            return True   # Cannot check — don't penalise

        drift_pct = abs((current - entry) / entry) * 100
        return drift_pct <= max_drift

    # ── Public interface ─────────────────────────────────────────────────────

    def add(self, candidate: dict) -> None:
        """
        Accepts a QUALIFIED candidate, timestamps it, and adds it to the pool.
        Suppresses exact duplicates (same symbol + breakout_type).
        """
        symbol = candidate.get("symbol")
        btype  = candidate.get("breakout_type", "MULTI_TF")

        # Duplicate suppression
        existing = self._pool.get_candidates()
        if any(c.get("symbol") == symbol and c.get("breakout_type") == btype for c in existing):
            logger.info(f"⛔ {symbol} already in pool — duplicate suppressed.")
            return

        candidate["status"]   = "QUALIFIED"
        candidate["found_at"] = datetime.now(timezone.utc).timestamp()
        self._pool.add(candidate)
        logger.info(f"📥 {symbol} queued ({len(self._pool)} total in pool)")

    def process(self) -> None:
        """
        Main orchestration: expire → rank → allocate → persist.
        Called once at the end of each scanner sweep.
        """
        candidates = self._pool.get_candidates()
        if not candidates:
            logger.info("OpportunityManager: No candidates to process.")
            return

        logger.info(f"🔄 OpportunityManager: processing {len(candidates)} candidates...")

        self._expire(candidates)
        live = [c for c in candidates if c.get("status") == "QUALIFIED"]

        if not live:
            logger.info("OpportunityManager: All candidates expired. Persisting and exiting.")
            self._persist()
            return

        self._rank(live)
        self._allocate(live)
        self._persist()
        self._pool.clear()

    # ── Internal steps ───────────────────────────────────────────────────────

    def _expire(self, candidates: list[dict]) -> None:
        """
        Checks time decay AND price drift. Marks stale candidates as EXPIRED.
        """
        for c in candidates:
            symbol = c.get("symbol")

            # Check 1: time decay
            fs = self._time_freshness(c.get("found_at", 0))
            if fs == 0:
                c["status"]          = "EXPIRED"
                c["expired_reason"]  = "TIME_DECAY"
                self._outcomes.append(c)
                logger.info(f"⏳ {symbol} EXPIRED — time decay (>30 min)")
                continue

            c["freshness_score"] = fs

            # Check 2: price drift
            if not self._price_drift_ok(c):
                c["status"]         = "EXPIRED"
                c["expired_reason"] = "PRICE_DRIFT"
                self._outcomes.append(c)
                entry   = c.get("entry_price", 0)
                current = c.get("current_price", 0)
                logger.info(f"🏃 {symbol} EXPIRED — price drifted too far (entry={entry}, current={current})")

    def _rank(self, live_candidates: list[dict]) -> None:
        """Delegates to TradeRankingEngine. Updates candidates in-place."""
        from trade_ranking_engine import TradeRankingEngine
        ranked = TradeRankingEngine.rank_candidates(live_candidates)
        logger.info(
            f"🏆 Ranked {len(ranked)} candidates. "
            f"Top: {ranked[0]['symbol']} — {ranked[0].get('ranking_breakdown', {}).get('reasons', [])[:1]}"
        )

    def _allocate(self, ranked_candidates: list[dict]) -> None:
        """
        Delegates to PortfolioEngine.
        Candidates that don't get funded are marked REJECTED_CAPITAL.
        """
        from portfolio_engine import PortfolioEngine
        PortfolioEngine.execute_ranked_candidates(ranked_candidates, self.policy)

        for c in ranked_candidates:
            if c.get("status") == "QUALIFIED":
                # PortfolioEngine left it QUALIFIED = not funded due to capacity
                c["status"] = "REJECTED_CAPITAL"
            self._outcomes.append(c)

    def _persist(self) -> None:
        """
        Writes ALL outcomes to the database/validation log.
        FUNDED    → live alert + full persistence
        Others    → rejection log for validation
        """
        from database import save_alert_if_new, save_candidate
        from datetime import datetime as dt
        from zoneinfo import ZoneInfo
        from config import ACTIVE_ALGO_VERSION

        IST = ZoneInfo("Asia/Kolkata")
        now_ist = dt.now(IST).strftime('%Y-%m-%d %H:%M:%S+05:30')

        counts = {"FUNDED": 0, "REJECTED_CAPITAL": 0, "REJECTED_RANK": 0, "EXPIRED": 0}

        for c in self._outcomes:
            status = c.get("status", "UNKNOWN")
            symbol = c.get("symbol", "?")
            counts[status] = counts.get(status, 0) + 1

            if status == "FUNDED":
                try:
                    save_alert_if_new(
                        symbol=symbol,
                        breakout_type=c.get("breakout_type", "MULTI_TF"),
                        alert_time=now_ist,
                        scanner=c.get("scanner", "MULTI_TF"),
                        category=c.get("category"),
                        entry_price=c.get("entry_price"),
                        stop_loss=c.get("stop_loss"),
                        target_1=c.get("target_1"),
                        target_2=c.get("target_2"),
                        target_3=c.get("target_3"),
                        signals=c.get("signals"),
                        score=c.get("technical_score", 0),
                        rsi=c.get("rsi", 0.0),
                        volume_ratio=c.get("volume_ratio", 0.0),
                        model_version=ACTIVE_ALGO_VERSION,
                        context={
                            **(c.get("context") or {}),
                            "portfolio_funded": True,
                            "ranking": c.get("ranking_breakdown"),
                            "freshness_score": c.get("freshness_score"),
                            "allocation": c.get("allocation"),
                        },
                    )
                    logger.info(f"✅ {symbol} persisted as FUNDED alert.")
                except Exception as e:
                    logger.error(f"Failed to persist FUNDED {symbol}: {e}")

            else:
                # Persist rejection/expiry as validation data
                try:
                    save_candidate(
                        symbol=symbol,
                        breakout_type=c.get("breakout_type", "MULTI_TF"),
                        scanner=c.get("scanner", ""),
                        technical_score=c.get("technical_score", 0),
                        volume_ratio=c.get("volume_ratio", 0.0),
                        delivery_pct=c.get("delivery_pct", 0.0),
                        rr_ratio=c.get("rr_ratio", 0.0),
                        market_context=c.get("market_context") or {},
                        status=status,
                        rejection_reason=c.get("expired_reason", ""),
                        ranking_breakdown=c.get("ranking_breakdown") or {},
                    )
                    logger.info(f"📦 {symbol} persisted as {status} (validation data).")
                except Exception as e:
                    logger.debug(f"Could not persist rejection for {symbol}: {e}")

        logger.info(
            f"💾 Persist complete: "
            f"FUNDED={counts.get('FUNDED',0)} | "
            f"REJECTED_CAPITAL={counts.get('REJECTED_CAPITAL',0)} | "
            f"REJECTED_RANK={counts.get('REJECTED_RANK',0)} | "
            f"EXPIRED={counts.get('EXPIRED',0)}"
        )
