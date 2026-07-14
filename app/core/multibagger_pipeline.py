from typing import Dict, Any
from datetime import datetime
import pytz
import yaml
import os

from core.models import InvestmentDecision
from core.audit_engine import audit_engine
from core.gate_engine import run_gates
from core.quality_engine import run_quality_engine
from core.growth_engine import run_growth_engine
from core.financial_strength_engine import run_financial_strength_engine
from core.valuation_engine import run_valuation_engine
from core.market_structure_engine import run_market_structure_engine
from core.technical_engine import run_buy_zone_engine

# Load Config Once
config_path = os.path.join(os.path.dirname(__file__), 'config', 'v5_weights.yaml')
with open(config_path, 'r') as f:
    V5_CONFIG = yaml.safe_load(f)

# Validate configuration on startup
for s, w in V5_CONFIG.get('sector_weights', {}).items():
    if abs(sum(w.values()) - 100) > 0.001:
        raise ValueError(f"Startup Config Error: Weights for sector '{s}' must total 100, got {sum(w.values())}")
def run_pipeline_for_symbol(symbol: str, raw_data: Dict[str, Any], technicals: Dict[str, Any] = None) -> InvestmentDecision:
    """
    V5 Orchestrator Pipeline
    Runs all 7 layers and outputs the final InvestmentDecision.
    """
    # Merge technicals without mutating caller's dict
    merged_data = {**raw_data, **(technicals or {})}
    
    # Get sector overrides if applicable
    sector = merged_data.get('sector', 'default').lower()
    if sector not in V5_CONFIG['sector_weights']:
        sector = 'default'
    weights = V5_CONFIG['sector_weights'][sector]


    # Layer 1: Gates
    passed_gates, invalidation_reason = run_gates(symbol, merged_data)

    # We run the rest of the pipeline even if gates fail, 
    # to populate the data structure for debugging, 
    # but we will mark it as Invalidated at the end.

    # Layer 2: Business Quality
    quality = run_quality_engine(symbol, merged_data, V5_CONFIG.get('quality_weights', {}))
    
    # Layer 3: Growth & Capital Allocation
    growth = run_growth_engine(symbol, merged_data)
    
    # Layer 4: Financial Strength
    financial = run_financial_strength_engine(symbol, merged_data)
    
    # Layer 5: Valuation
    valuation = run_valuation_engine(symbol, merged_data, V5_CONFIG.get('valuation_weights', {}))
    
    # Layer 6: Market Structure
    market = run_market_structure_engine(symbol, merged_data)
    
    # Layer 7: Buy Zone (Technical Entry)
    buy_zone = run_buy_zone_engine(symbol, merged_data)

    # Composite Score Calculation
    comp_score = (
        (quality.score * (weights['quality'] / 100)) +
        (growth.score * (weights['growth'] / 100)) +
        (financial.score * (weights['financial_strength'] / 100)) +
        (valuation.score * (weights['valuation'] / 100)) +
        (market.score * (weights['market_structure'] / 100))
    )

    # Composite Confidence Calculation
    comp_confidence = (
        (quality.confidence * (weights['quality'] / 100)) +
        (growth.confidence * (weights['growth'] / 100)) +
        (financial.confidence * (weights['financial_strength'] / 100)) +
        (valuation.confidence * (weights['valuation'] / 100)) +
        (market.confidence * (weights['market_structure'] / 100))
    )

    # Allow extensible confidence penalties based on data freshness
    if merged_data.get('data_freshness') == 'STALE':
        comp_confidence -= 20.0
    
    # Final clamping for safety
    comp_score = max(0.0, min(100.0, comp_score))
    comp_confidence = max(0.0, min(100.0, comp_confidence))
    
    raw_composite_score = comp_score

    # Classification driven by v5_weights.yaml
    c_config = V5_CONFIG.get('classification', {})
    prime = c_config.get('prime', {'score': 80, 'confidence': 80, 'require_buy_zone': True})
    high_q = c_config.get('high_quality', {'score': 65, 'confidence': 60, 'require_buy_zone': False})
    good_b = c_config.get('good_business', {'score': 50, 'confidence': 50, 'require_buy_zone': False})

    classification = "🟡 Watchlist"
    if passed_gates:
        if comp_score >= prime['score'] and comp_confidence >= prime['confidence'] and (buy_zone.in_buy_zone if prime['require_buy_zone'] else True):
            classification = "🚀 Prime Multibagger"
        elif comp_score >= high_q['score'] and comp_confidence >= high_q['confidence'] and (buy_zone.in_buy_zone if high_q['require_buy_zone'] else True):
            classification = "💎 High Quality"
        elif comp_score >= good_b['score'] and comp_confidence >= good_b['confidence'] and (buy_zone.in_buy_zone if good_b['require_buy_zone'] else True):
            classification = "🏆 Good Business"
    else:
        classification = "Invalidated"
        comp_score = 0.0 # Zero out the final score for invalid companies so they drop in rank

    decision = InvestmentDecision(
        symbol=symbol,
        quality=quality,
        growth=growth,
        financial_strength=financial,
        valuation=valuation,
        market_structure=market,
        buy_zone=buy_zone,
        composite_score=round(comp_score, 2),
        raw_composite_score=round(raw_composite_score, 2),
        confidence=round(comp_confidence, 2),
        classification=classification,
        current_price=merged_data.get('price', 0.0),
        is_invalidated=not passed_gates,
        invalidation_reason=invalidation_reason,
        audit_trail=audit_engine.export_trail(symbol) if hasattr(audit_engine, 'export_trail') else [],
        engine_version="V5.0.0",
        weights_profile=sector,
        weights_version=V5_CONFIG.get('version', "1.0"),
        valuation_version="1.0",
        timestamp=datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    )
    
    # Clear audit engine memory for this symbol after pipeline is complete
    if hasattr(audit_engine, 'clear'):
        audit_engine.clear(symbol)

    return decision
