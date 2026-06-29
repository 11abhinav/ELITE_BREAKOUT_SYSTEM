from typing import Callable, Dict, Any, List, Optional
from core.models import MetricResult, MetricSource
import pandas as pd
import numpy as np

class MetricRegistry:
    def __init__(self):
        # Format: { "metric_name": {"func": callable, "pillar": str, "is_improvement": bool, ...} }
        self._metrics: Dict[str, Dict[str, Any]] = {}
        
    def register(self, name: str, func: Callable, pillar: str = "custom", is_improvement: bool = False, weight: float = 1.0):
        self._metrics[name] = {
            "func": func,
            "pillar": pillar,
            "is_improvement": is_improvement,
            "weight": weight
        }
        
    def execute_all(self, raw_data: Dict[str, Any]) -> Dict[str, MetricResult]:
        results = {}
        for name, meta in self._metrics.items():
            try:
                # The metric function should take raw_data and return a MetricResult
                res: MetricResult = meta["func"](raw_data)
                res.name = name # Ensure name matches registry
                results[name] = res
            except Exception as e:
                # Fallback on failure
                results[name] = MetricResult(
                    name=name,
                    value=None,
                    confidence=0.0,
                    coverage=0.0,
                    source=MetricSource.DERIVED,
                    freshness_days=999,
                    explanation=f"Error computing {name}: {e}"
                )
        return results

# Example adaptive metric functions

def adaptive_cagr(series: pd.Series, max_years: int = 5) -> MetricResult:
    """Computes adaptive CAGR preferring longer history but falling back to shorter."""
    if series.empty or len(series) < 2:
        return MetricResult(name="", value=None, confidence=0.0, coverage=0.0, 
                            source=MetricSource.YAHOO_FINANCE, freshness_days=0, 
                            history_length_used=0, explanation="Insufficient history for CAGR")
        
    series = series.dropna()
    if len(series) < 2:
        return MetricResult(name="", value=None, confidence=0.0, coverage=0.0, 
                            source=MetricSource.YAHOO_FINANCE, freshness_days=0, 
                            history_length_used=0, explanation="Insufficient valid data points")

    latest = float(series.iloc[0])
    
    # Try 5Y, then 3Y, 2Y, 1Y
    for y in [5, 3, 2, 1]:
        if y <= max_years and len(series) > y:
            oldest = float(series.iloc[y])
            if oldest > 0 and latest > 0:
                cagr = ((latest / oldest) ** (1.0 / y)) - 1.0
                confidence = 1.0 if y == 5 else (0.8 if y == 3 else (0.6 if y == 2 else 0.4))
                return MetricResult(
                    name="", 
                    value=cagr,
                    confidence=confidence,
                    coverage=1.0,
                    source=MetricSource.YAHOO_FINANCE,
                    freshness_days=0,
                    history_length_used=y,
                    explanation=f"Calculated {y}Y CAGR adaptively"
                )
                
    return MetricResult(name="", value=None, confidence=0.0, coverage=0.0, 
                        source=MetricSource.YAHOO_FINANCE, freshness_days=0, 
                        history_length_used=None, explanation="Data format uncomputable for CAGR")

# Default registry
registry = MetricRegistry()

def compute_roic(raw_data: Dict[str, Any]) -> MetricResult:
    # Expects raw_data to have 'financials', 'balance_sheet' etc.
    ebit = raw_data.get('ebit_ttm')
    invested_capital = raw_data.get('invested_capital_ttm')
    
    if ebit is not None and invested_capital and invested_capital > 0:
        roic = ebit / invested_capital
        return MetricResult(
            name="roic",
            value=roic,
            confidence=1.0, # High confidence for TTM raw data
            coverage=1.0,
            source=MetricSource.YAHOO_FINANCE,
            freshness_days=0,
            explanation=f"TTM ROIC = {roic*100:.1f}%"
        )
    return MetricResult(
        name="roic", value=None, confidence=0.0, coverage=0.0,
        source=MetricSource.DERIVED, freshness_days=999, explanation="Missing EBIT or IC"
    )

registry.register("roic", compute_roic, pillar="quality")

# We will add more metrics here as we build out the engine.
