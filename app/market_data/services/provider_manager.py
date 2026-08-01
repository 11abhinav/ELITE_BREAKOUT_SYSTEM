import logging
from typing import List, Optional
from ..core.interfaces import ProviderInterface
from ..core.models import ProviderStatus

logger = logging.getLogger(__name__)

class ProviderManager:
    """
    Manages the pool of providers (Upstox, Fyers, etc.).
    Evaluates health scores and executes auto-failover routing.
    """
    def __init__(self):
        self.providers: List[ProviderInterface] = []
        
    def register_provider(self, provider: ProviderInterface) -> None:
        self.providers.append(provider)
        logger.info(f"Registered provider: {provider.provider_name}")
        
    def get_best_provider(self, timeframe: str) -> Optional[ProviderInterface]:
        """
        Selects the healthiest provider that supports the requested timeframe.
        Providers are sorted by Health Score (highest first).
        """
        valid_providers = []
        
        for p in self.providers:
            if p.get_status() == ProviderStatus.CIRCUIT_OPEN:
                continue
                
            # Check capability matrix
            caps = p.capabilities
            if timeframe == "1m" and not caps.supports_1m: continue
            if timeframe == "5m" and not caps.supports_5m: continue
            if timeframe == "15m" and not caps.supports_15m: continue
            if timeframe == "1h" and not caps.supports_1h: continue
            if timeframe == "1d" and not caps.supports_1d: continue
            
            valid_providers.append(p)
            
        if not valid_providers:
            logger.error("No valid providers available for timeframe: " + timeframe)
            return None
            
        # Sort by health score descending
        valid_providers.sort(key=lambda x: x.get_health_score(), reverse=True)
        best = valid_providers[0]
        
        logger.debug(f"Selected {best.provider_name} (Health: {best.get_health_score()})")
        return best
