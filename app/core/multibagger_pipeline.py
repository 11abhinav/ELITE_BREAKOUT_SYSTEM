from typing import Dict, Any, Optional
from core.models import FinalScannerResult, ClassificationTier, ExitState
from core.metric_engine import registry as metric_registry
from core.gate_engine import run_gates
from core.score_engine import run_score_engine
from core.improvement_engine import detect_improvements
from core.emerging_engine import EmergingEngine
from core.technical_engine import evaluate_technicals

emerging_engine = EmergingEngine()

def determine_classification(static_score, emerging_score) -> ClassificationTier:
    """Layer 6: Opportunity Classification"""
    q = static_score.quality.score
    g = static_score.growth.score
    e = emerging_score.overall_score
    
    if q >= 75.0 and g >= 80.0 and e >= 85.0:
        return ClassificationTier.TIER_A
    elif q >= 85.0 and g >= 60.0:
        return ClassificationTier.TIER_B
    elif q < 60.0 and e >= 75.0:
        return ClassificationTier.TIER_C
    elif static_score.value.score >= 80.0 and e >= 70.0:
        return ClassificationTier.TIER_D
    else:
        return ClassificationTier.TIER_E

def run_pipeline_for_symbol(symbol: str, raw_fundamentals: Dict[str, Any], technicals: Dict[str, float]) -> FinalScannerResult:
    """
    Executes Layers 2 through 7 for a single symbol.
    Returns FinalScannerResult (tier is INVALIDATED if rejected by gates).
    """
    
    # Layer 2: Metric Engine
    # Combines raw fundamentals with some technicals if needed
    raw_data = {**raw_fundamentals, **technicals}
    metrics_dict = metric_registry.execute_all(raw_data)
    
    # Layer 3: Gate Engine
    passed, reject_reason = run_gates(raw_data)
        
    # Layer 4: Six Pillar Score
    static_score = run_score_engine(metrics_dict)
    
    # Layer 4.5: Improvement Detection
    improvement = detect_improvements(raw_data)
    
    # Layer 5: Emerging Engine
    emerging_score = emerging_engine.compute_emerging_score(improvement, metrics_dict)
    
    # Layer 6: Classification
    if not passed:
        tier = ClassificationTier.INVALIDATED
    else:
        tier = determine_classification(static_score, emerging_score)
    
    # Layer 7: Technical Entry
    in_buy_zone, buy_zone_low, buy_zone_high, tech_reason = evaluate_technicals(technicals)
    
    # We assign an initial HOLD exit state since it's just scanned, not owned yet.
    # The true Exit logic (Layer 9) runs separately on open positions.
    
    # Generate Audit Trail (End-User Facing Reason)
    audit = reject_reason if not passed else tech_reason
    
    # Build final result
    return FinalScannerResult(
        symbol=symbol,
        static_score=static_score,
        improvement=improvement,
        emerging_score=emerging_score,
        classification=tier,
        exit_state=ExitState.HOLD,
        confidence=1.0, # Could be derived from metric coverages
        freshness=0,
        in_buy_zone=in_buy_zone,
        buy_zone_low=buy_zone_low,
        buy_zone_high=buy_zone_high,
        audit_trail=audit
    )
