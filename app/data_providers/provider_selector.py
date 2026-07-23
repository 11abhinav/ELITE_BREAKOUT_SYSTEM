from typing import List
import logging
from data_registry import registry

logger = logging.getLogger(__name__)

class ProviderSelector:
    """
    Decouples the provider fallback policy from the fetcher execution.
    Determines the sequence of providers to try for a given data request.
    """
    
    def __init__(self):
        self.registry = registry

    def get_providers(self, dataset_id: str, fetch_type: str = "historical") -> List[str]:
        """
        Returns an ordered list of providers to try (primary, secondary, etc.)
        based on the Dataset Registry configuration or default rules.
        """
        entry = self.registry.get_entry(dataset_id)
        
        # If registry explicitly defines a preferred provider, prioritize it
        if entry and entry.preferred_provider:
            # We treat preferred_provider as the primary if we are fetching fresh.
            primary = entry.preferred_provider
            if primary == "fyers":
                return ["fyers", "yahoo", "bse"]
            elif primary == "yahoo":
                return ["yahoo", "fyers", "bse"]
            elif primary == "nse":
                return ["nse"]
                
        # Default fallback policies based on the type of data requested
        if fetch_type == "live_quotes":
            return ["fyers", "yahoo", "bse"]
        elif fetch_type == "historical":
            return ["fyers", "yahoo", "bse"]
            
        return ["fyers", "yahoo", "bse"]

# Global instance
selector = ProviderSelector()
