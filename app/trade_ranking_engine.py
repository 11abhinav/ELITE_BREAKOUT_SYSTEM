"""
TradeRankingEngine — Hierarchical candidate ranking.

Sort Hierarchy (primary to tie-breaker):
  1. Technical Quality   — Is the setup technically sound?
  2. Institutional Score — Is smart money participating?
  3. Reward Quality      — What is the structural RR (capped at 5x)?
  4. Market Context      — Is the market regime supporting this strategy?
  5. Freshness           — How fresh is the opportunity?
"""


import math

class TradeRankingEngine:

    @staticmethod
    def _safe_float(val, default: float) -> float:
        if val is None:
            return default
        try:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return default
            return f
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _technical_score(candidate: dict) -> int:
        """0-100 technical quality from the scoring engine."""
        raw = TradeRankingEngine._safe_float(candidate.get("technical_score"), 0.0)
        return max(0, min(100, int(raw)))

    @staticmethod
    def _institutional_score(candidate: dict) -> int:
        """
        0-100 institutional footprint.
        Volume Ratio + Delivery %.
        Future: Candle quality, gap quality.
        """
        vol_ratio = TradeRankingEngine._safe_float(candidate.get("volume_ratio"), 1.0)
        delivery_pct = TradeRankingEngine._safe_float(candidate.get("delivery_pct"), 0.0)

        vol_capped = min(5.0, max(1.0, vol_ratio))
        vol_score = ((vol_capped - 1.0) / 4.0) * 100

        if delivery_pct > 0:
            del_capped = min(75.0, max(20.0, delivery_pct))
            del_score = ((del_capped - 20.0) / 55.0) * 100
            return int((vol_score * 0.5) + (del_score * 0.5))
        return int(vol_score)

    @staticmethod
    def _rr_score(candidate: dict) -> int:
        """
        0-100 structural reward quality. Capped at 5x RR so RR never dominates.
        """
        rr = TradeRankingEngine._safe_float(candidate.get("rr_ratio"), 1.5)
        rr_capped = min(5.0, max(1.5, rr))
        return int(((rr_capped - 1.5) / 3.5) * 100)

    @staticmethod
    def _market_score(candidate: dict) -> int:
        """
        0-100 market context alignment from the MarketRegimeEngine.
        """
        ctx = candidate.get("market_context")
        if isinstance(ctx, dict):
            return ctx.get("market_score", 50)
        return 50

    @staticmethod
    def _freshness_score(candidate: dict) -> int:
        """Passes through the freshness score set by OpportunityManager."""
        return candidate.get("freshness_score", 100)

    @staticmethod
    def _build_reasons(tech: int, inst: int, rr: int, market: int, freshness: int) -> list[str]:
        reasons = []
        if tech >= 85:
            reasons.append("Excellent technical confluence")
        elif tech >= 70:
            reasons.append("Good technical setup")
        else:
            reasons.append("Acceptable technical quality")

        if inst >= 70:
            reasons.append("High institutional participation")
        elif inst >= 40:
            reasons.append("Moderate institutional footprint")

        if rr >= 70:
            reasons.append("Excellent structural reward/risk")
        elif rr >= 40:
            reasons.append("Acceptable reward/risk structure")

        if market >= 70:
            reasons.append("Strong market regime alignment")
        elif market >= 50:
            reasons.append("Neutral market regime")
        else:
            reasons.append("Weak market regime — reduced conviction")

        if freshness < 60:
            reasons.append("⚠️ Aging opportunity — freshness decaying")

        return reasons

    @staticmethod
    def rank_candidates(candidates: list) -> list:
        """
        Takes a list of candidate dicts and returns them sorted hierarchically.

        Sort tuple (all descending):
          (technical_score, institutional_score, rr_score, market_score, freshness_score)

        Attaches `ranking_breakdown` dict and removes internal keys.
        """
        ranked = []
        for c in candidates:
            tech   = TradeRankingEngine._technical_score(c)
            inst   = TradeRankingEngine._institutional_score(c)
            rr     = TradeRankingEngine._rr_score(c)
            market = TradeRankingEngine._market_score(c)
            fresh  = TradeRankingEngine._freshness_score(c)

            # Freshness acts as a soft multiplier on all scores
            # A 50% fresh candidate has its effective sort keys halved
            fresh_mult = fresh / 100.0

            sort_key = (
                int(tech   * fresh_mult),
                int(inst   * fresh_mult),
                int(rr     * fresh_mult),
                int(market * fresh_mult),
                fresh,
            )

            c["ranking_breakdown"] = {
                "technical":     tech,
                "institutional": inst,
                "reward":        rr,
                "market":        market,
                "freshness":     fresh,
                "reasons":       TradeRankingEngine._build_reasons(tech, inst, rr, market, fresh),
            }
            c["_sort_key"] = sort_key
            ranked.append(c)

        ranked.sort(key=lambda x: x["_sort_key"], reverse=True)

        for idx, c in enumerate(ranked):
            c["ranking_breakdown"]["global_rank"] = idx + 1
            del c["_sort_key"]

        return ranked
