from typing import Dict, List
from core.models import MetricResult, PillarResult, CoreScoreResult

class ScoreEngine:
    def __init__(self):
        # Maps pillars to the registered metric names that belong to them
        self.pillar_map = {
            "quality": ["roic", "roce", "cash_conversion", "fcf_margin", "asset_turnover"],
            "growth": ["revenue_cagr", "eps_cagr", "fcf_cagr"],
            "value": ["pe", "pb", "ev_ebitda", "peg", "fcf_yield"],
            "risk": ["debt_trend", "interest_coverage", "altman_z"],
            "capital_allocation": ["buyback_yield", "div_yield", "reinvestment_rate", "promoter_holding"],
            "momentum": ["rs", "relative_volume", "dist_from_ath"]
        }
        
    def _calculate_pillar(self, name: str, expected_metrics: List[str], metrics_dict: Dict[str, MetricResult]) -> PillarResult:
        pillar_metrics = []
        total_score = 0.0
        total_confidence = 0.0
        
        for m_name in expected_metrics:
            res = metrics_dict.get(m_name)
            if res and res.value is not None:
                pillar_metrics.append(res)
                total_score += res.score_contribution
                total_confidence += res.confidence
                
        if not expected_metrics:
            return PillarResult(name=name, score=0.0, confidence=0.0, metrics=[])
            
        avg_score = total_score / len(expected_metrics)
        avg_confidence = total_confidence / len(expected_metrics) if pillar_metrics else 0.0
        
        # Scale to 100
        scaled_score = max(0.0, min(100.0, avg_score * 100))
        
        return PillarResult(
            name=name,
            score=scaled_score,
            confidence=avg_confidence,
            metrics=pillar_metrics
        )

    def compute_core_score(self, metrics_dict: Dict[str, MetricResult]) -> CoreScoreResult:
        quality = self._calculate_pillar("Quality", self.pillar_map["quality"], metrics_dict)
        growth = self._calculate_pillar("Growth", self.pillar_map["growth"], metrics_dict)
        value = self._calculate_pillar("Value", self.pillar_map["value"], metrics_dict)
        risk = self._calculate_pillar("Risk", self.pillar_map["risk"], metrics_dict)
        cap_alloc = self._calculate_pillar("Capital Allocation", self.pillar_map["capital_allocation"], metrics_dict)
        momentum = self._calculate_pillar("Momentum", self.pillar_map["momentum"], metrics_dict)
        
        return CoreScoreResult(
            quality=quality,
            growth=growth,
            value=value,
            risk=risk,
            capital_allocation=cap_alloc,
            momentum=momentum
        )

# Example usage interface
def run_score_engine(metrics_dict: Dict[str, MetricResult]) -> CoreScoreResult:
    engine = ScoreEngine()
    return engine.compute_core_score(metrics_dict)
