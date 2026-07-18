from dataclasses import dataclass, field
from typing import List, Dict, Optional

@dataclass
class TargetCandidate:
    price:         float
    source:        str
    strength:      str

@dataclass
class TargetAnalysis:
    structural_score: int
    rr_bonus: int
    distance_penalty: int
    confidence_pct: int
    execution_priority: int
    explanation: dict

@dataclass
class ClusteredTarget:
    cluster_id: int
    consensus_price: float
    score: int
    candidates: List[TargetCandidate]
    is_round_number: bool = False
    analysis: Optional[TargetAnalysis] = None

class ClusterAnalyzer:
    @staticmethod
    def analyze(cluster: ClusteredTarget, entry: float, eff_atr: float, macro_regime: str) -> dict:
        unique_sources = set(c.source for c in cluster.candidates)
        # Mocking data quality penalties (e.g. single touches could be represented by 'NORMAL' vs 'STRONG' strength)
        data_quality_penalty = sum(5 for c in cluster.candidates if c.strength == "NORMAL")
        atr_distance = abs(cluster.consensus_price - entry) / eff_atr if eff_atr > 0 else 0
        
        # Confluence bonus: More unique independent sources = better zone
        confluence_bonus = (len(unique_sources) - 1) * 10
        
        return {
            "unique_sources": list(unique_sources),
            "data_quality_penalty": data_quality_penalty,
            "atr_distance": atr_distance,
            "confluence_bonus": confluence_bonus
        }

class TargetScorer:
    @staticmethod
    def score_cluster(cluster: ClusteredTarget, entry: float, risk: float, eff_atr: float, macro_regime: str) -> TargetAnalysis:
        from config import TARGET_SOURCE_WEIGHTS, TARGET_CONFIDENCE_BASELINE
        
        features = ClusterAnalyzer.analyze(cluster, entry, eff_atr, macro_regime)
        
        # 1. Base Structural Score
        base_score = 0
        for cand in cluster.candidates:
            if cand.source == "FIB_200":
                # FIB_200_WEIGHTS logic mocked here
                base_score += 5
            else:
                base_score += TARGET_SOURCE_WEIGHTS.get(cand.source, 0)
                
        structural_score = base_score + features["confluence_bonus"] - features["data_quality_penalty"]
        
        # 2. RR Bonus (Diminishing Returns)
        rr = (cluster.consensus_price - entry) / risk if risk > 0 else 0
        rr = round(rr, 2)
        rr_bonus = 0
        if rr >= 6.0: rr_bonus = 20
        elif rr >= 4.0: rr_bonus = 19
        elif rr >= 3.0: rr_bonus = 17
        elif rr >= 2.5: rr_bonus = 14
        elif rr >= 2.0: rr_bonus = 10
        elif rr >= 1.5: rr_bonus = 5
        
        # 3. Distance Penalty (ATR multiples, capped)
        distance_penalty = min(int(features["atr_distance"] * 2), 20)
        
        # 4. Confidence Pct
        # Normalizing against the TARGET_CONFIDENCE_BASELINE (95th percentile empirically strong score)
        raw_confidence = (structural_score / TARGET_CONFIDENCE_BASELINE) * 100 if TARGET_CONFIDENCE_BASELINE > 0 else 0
        confidence_pct = max(0, min(100, int(raw_confidence)))
        
        # 5. Execution Priority
        execution_priority = structural_score + rr_bonus - distance_penalty
        
        # 6. Explanation
        explanation = {
            "selected_target": round(cluster.consensus_price, 2),
            "sources": features["unique_sources"],
            "rr": rr,
            "confidence_pct": confidence_pct,
            "execution_priority": execution_priority,
            "cluster_strength": "HIGH" if confidence_pct >= 85 else ("MEDIUM" if confidence_pct >= 60 else "LOW")
        }
        
        return TargetAnalysis(
            structural_score=structural_score,
            rr_bonus=rr_bonus,
            distance_penalty=distance_penalty,
            confidence_pct=confidence_pct,
            execution_priority=execution_priority,
            explanation=explanation
        )

print("Classes compiled successfully")
