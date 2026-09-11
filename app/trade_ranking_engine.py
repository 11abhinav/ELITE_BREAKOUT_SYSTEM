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

    @staticmethod
    def rank_candidates_gem_aware(
        candidates: list,
        gem_active: bool = False,
        quality_top20_threshold: float = 75.0
    ) -> list:
        """
        V5.20 Gem-Aware Hierarchical Ranking & Trigger Point Router.
        
        Sort Hierarchy during active Gem:
          1. Ecosystem Priority Tier (Priority 1: Reversal, Pullback, 1H, Multibagger)
          2. Top 20% Technical Quality Score (>= 75.0)
          3. Technical Confluence
          4. Institutional Footprint
          5. Structural Reward/Risk
        """
        class_a_tier1 = {"REVERSAL", "PULLBACK", "PULLBACK_V2", "MULTITF_1H", "MULTIBAGGER"}
        class_a_tier3 = {"SHORT_COVERING"}
        class_b_and_c = {"EOD", "EOD_BREAKOUT", "ACCUMULATION", "ACCUMULATION_VCP", "WEALTH", "WEALTH_ENGINE", "TECHNICAL", "TECHNICAL_AHAT"}

        ranked = TradeRankingEngine.rank_candidates(candidates)

        for c in ranked:
            raw_name = str(c.get("scanner", "")).upper()
            scanner_name = raw_name.replace(" ", "_").replace("-", "_")
            tech = c.get("ranking_breakdown", {}).get("technical", 50)
            passed_top20 = tech >= quality_top20_threshold

            is_class_b_or_c = any(bc in scanner_name for bc in class_b_and_c)

            if is_class_b_or_c:
                # Class B EOD & Class C After-Hours: Strictly decoupled to prevent stale-signal exhaustion
                priority = 2
                risk_r = 1.00
                tier = "Standalone Clean Baseline (Class B/C Decoupled)"
                effective_gem_active = False
            elif gem_active:
                effective_gem_active = True
                if any(t1 in scanner_name for t1 in class_a_tier1):
                    priority = 1
                    risk_r = 1.50
                    tier = "Tier 1: High Synergy (Class A Intraday)"
                elif any(t3 in scanner_name for t3 in class_a_tier3):
                    priority = 3
                    risk_r = 0.50
                    tier = "Tier 3: Inverse Decoupled (Class A Intraday)"
                else:
                    priority = 2
                    risk_r = 1.00
                    tier = "Tier 2: Intraday Scalp (Class A Intraday)"
            else:
                effective_gem_active = False
                priority = 2
                risk_r = 1.00
                tier = "Normal Baseline"

            c["gem_routing"] = {
                "gem_active": effective_gem_active,
                "ecosystem_tier": tier,
                "priority": priority,
                "risk_allocation_r": risk_r,
                "two_stage_quality_pass": passed_top20 if effective_gem_active else True,
                "execution_permitted": (passed_top20 if (effective_gem_active and priority == 1) else True)
            }

        # Sort by priority first (1 is highest), then by original rank
        if gem_active:
            ranked.sort(key=lambda x: (x["gem_routing"]["priority"], x["ranking_breakdown"]["global_rank"]))
            for idx, c in enumerate(ranked):
                c["ranking_breakdown"]["gem_routed_rank"] = idx + 1

        return ranked

    @staticmethod
    def rank_candidates_regime_aware(
        candidates: list,
        regime_score: float = 0.50,
        quality_top20_threshold: float = 75.0
    ) -> list:
        """
        V5.23 Market-Catalyst-Regime Hierarchical Ranking & Dynamic Risk Allocation.
        
        Applies aggregate market catalyst regime to all 11 scanners (Intraday, EOD, After-Hours).
        Replaces individual stale Gem inheritance with macro catalyst regime scoring and
        fresh consolidation base gating.
        """
        try:
            from engine.production.v523_market_catalyst_regime_engine import (
                ScannerRegimePolicyEngine,
                MarketCatalystScoreAggregator,
                FreshnessExhaustionGuard
            )
        except ImportError:
            # Fallback if engine package structure varies
            return TradeRankingEngine.rank_candidates(candidates)

        ranked = TradeRankingEngine.rank_candidates(candidates)
        regime = MarketCatalystScoreAggregator.resolve_regime(regime_score)

        for c in ranked:
            policy_decision = ScannerRegimePolicyEngine.evaluate_candidate(c, regime_score=regime_score)
            tech = c.get("ranking_breakdown", {}).get("technical", 50)
            passed_top20 = tech >= quality_top20_threshold

            exec_permitted = policy_decision.execution_permitted
            if policy_decision.priority == 1 and not passed_top20:
                # Priority 1 allocations require top 20% technical quality
                exec_permitted = False

            c["regime_routing"] = {
                "regime_state": policy_decision.regime.value,
                "regime_score": policy_decision.score,
                "scanner_name": policy_decision.scanner_name,
                "priority": policy_decision.priority,
                "risk_allocation_r": policy_decision.risk_allocation_r,
                "execution_permitted": exec_permitted,
                "veto_reason": policy_decision.veto_reason,
                "policy_description": policy_decision.policy_description,
                "climax_exhaustion_checked": policy_decision.climax_exhaustion_checked,
                "is_fresh_base": FreshnessExhaustionGuard.is_fresh_base(c)
            }

        # Sort by regime priority (1 is highest), execution permitted flag, then global technical rank
        ranked.sort(key=lambda x: (
            0 if x["regime_routing"]["execution_permitted"] else 1,
            x["regime_routing"]["priority"],
            x["ranking_breakdown"]["global_rank"]
        ))

        for idx, c in enumerate(ranked):
            c["ranking_breakdown"]["regime_routed_rank"] = idx + 1

        return ranked

    @staticmethod
    def rank_candidates_v524_revalidated(
        candidates: list,
        evaluation_time_hours: float = 16.0,
        is_intraday_scheduler: bool = False,
        quality_top20_threshold: float = 75.0
    ) -> list:
        """
        V5.24 Deterministic Catalyst State & Revalidation Hierarchical Ranking Engine.
        
        Separates live intraday Gem routing (<= 60m TTL) from after-market structural revalidation.
        Consumes DeterministicCatalystStateEngine to classify every candidate into:
          - FRESH (Intraday active, 1.50R, Priority 1)
          - SURVIVED (EOD structural survivor, 1.50R, Priority 1 Context Boost)
          - COOLING (Standard 1.00R Baseline, Priority 2)
          - EXHAUSTED (Climax runner, 0.00R VETO for continuation breakouts)
          - INVALIDATED (Breakdown, 0.00R VETO)
          - NO_CATALYST (Organic base, 1.00R Baseline)
        """
        try:
            from engine.production.v524_catalyst_state_engine import DeterministicCatalystStateEngine, CatalystState
        except ImportError:
            return TradeRankingEngine.rank_candidates(candidates)

        ranked = TradeRankingEngine.rank_candidates(candidates)

        for c in ranked:
            eval_res = DeterministicCatalystStateEngine.evaluate_candidate(
                candidate=c,
                evaluation_time_hours=evaluation_time_hours,
                is_intraday_scheduler=is_intraday_scheduler
            )
            tech = c.get("ranking_breakdown", {}).get("technical", 50)
            passed_top20 = tech >= quality_top20_threshold

            exec_permitted = eval_res.risk_allocation_r > 0.0
            if eval_res.priority_tier == 1 and not passed_top20:
                exec_permitted = False

            c["catalyst_revalidation"] = {
                "state": eval_res.state.value,
                "gem_age_minutes": eval_res.gem_age_minutes,
                "priority_tier": eval_res.priority_tier,
                "risk_allocation_r": eval_res.risk_allocation_r,
                "execution_permitted": exec_permitted,
                "veto_reason": eval_res.veto_reason,
                "policy_description": eval_res.policy_description,
                "is_live_intraday_eligible": eval_res.is_live_intraday_eligible,
                "is_aftermarket_context_eligible": eval_res.is_aftermarket_context_eligible
            }

        # Sort: permitted first, priority tier (1 is highest), then technical rank
        ranked.sort(key=lambda x: (
            0 if x["catalyst_revalidation"]["execution_permitted"] else 1,
            x["catalyst_revalidation"]["priority_tier"],
            x["ranking_breakdown"]["global_rank"]
        ))

        for idx, c in enumerate(ranked):
            c["ranking_breakdown"]["v524_revalidated_rank"] = idx + 1

        return ranked



