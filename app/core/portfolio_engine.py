from typing import List, Dict, Any
from core.models import FinalScannerResult

class PortfolioManager:
    def __init__(self, max_sector_exposure: float = 0.20, max_single_position: float = 0.10):
        self.max_sector_exposure = max_sector_exposure
        self.max_single_position = max_single_position
        
    def optimize(self, opportunities: List[FinalScannerResult], current_portfolio: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """
        Layer 8: Portfolio Optimizer
        Separates the discovery of opportunities from the decision of what to own.
        """
        if not current_portfolio:
            current_portfolio = {"holdings": [], "cash": 1.0}
            
        recommendations = []
        
        owned_symbols = [h['symbol'] for h in current_portfolio.get('holdings', [])]
        
        for opp in opportunities:
            if opp.symbol in owned_symbols:
                recommendations.append({
                    "symbol": opp.symbol,
                    "action": "HOLD/ADD",
                    "reason": "Already owned, setup is still valid."
                })
            else:
                # We could add sector correlation logic here
                recommendations.append({
                    "symbol": opp.symbol,
                    "action": "BUY",
                    "reason": f"New opportunity. Tier: {opp.classification.value}"
                })
                
        # Limit to top 12
        recommendations = recommendations[:12]
        
        return recommendations
