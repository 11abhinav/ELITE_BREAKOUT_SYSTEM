from typing import Dict, Any, List
from core.models import MetricResult, PillarResult, EmergingScoreResult, ImprovementResult

class EmergingEngine:
    def __init__(self):
        # We define what metrics map to the emerging categories
        self.category_map = {
            "financial_improvement": ["roic_trend", "margin_expansion", "cash_conversion_trend", "debt_reduction"],
            "growth_improvement": ["revenue_acceleration", "eps_acceleration", "fcf_growth_trend", "operating_leverage"],
            "market_recognition": ["relative_strength_improvement", "volume_accumulation", "institutional_trend"]
        }
        
    def _score_category(self, name: str, expected: List[str], metrics_dict: Dict[str, MetricResult]) -> PillarResult:
        cat_metrics = []
        total_score = 0.0
        total_conf = 0.0
        
        for m_name in expected:
            res = metrics_dict.get(m_name)
            if res and res.value is not None:
                cat_metrics.append(res)
                total_score += res.score_contribution
                total_conf += res.confidence
                
        if not expected:
            return PillarResult(name=name, score=0.0, confidence=0.0, metrics=[])
             
        avg_score = total_score / len(expected)
        avg_conf = total_conf / len(expected) if cat_metrics else 0.0
        scaled_score = max(0.0, min(100.0, avg_score * 100))
        
        return PillarResult(
            name=name,
            score=scaled_score,
            confidence=avg_conf,
            metrics=cat_metrics
        )

    def compute_emerging_score(self, improvement: ImprovementResult, metrics_dict: Dict[str, MetricResult]) -> EmergingScoreResult:
        # If no base improvement is detected at all, the score is capped or zeroed.
        # But normally we score the magnitude of improvement here.
        
        fin_imp = self._score_category("Financial Improvement", self.category_map["financial_improvement"], metrics_dict)
        growth_imp = self._score_category("Growth Improvement", self.category_map["growth_improvement"], metrics_dict)
        market_rec = self._score_category("Market Recognition", self.category_map["market_recognition"], metrics_dict)
        
        # If Layer 4.5 said completely NO improvements, we could penalize here
        if not improvement.has_improvement:
            # Force score lower if no fundamental improvements exist
            fin_imp.score *= 0.5
            growth_imp.score *= 0.5
            
        return EmergingScoreResult(
            financial_improvement=fin_imp,
            growth_improvement=growth_imp,
            market_recognition=market_rec
        )
