# [VERSION: V5_ACQUISITION_ROUTING_V1.0] Configuration-driven ProviderSelector with Capability Matching
from typing import List, Optional
import logging
from data_registry import registry
import config

logger = logging.getLogger(__name__)

class ProviderSelector:
    """
    Decouples the provider fallback policy from the fetcher execution.
    Determines the sequence of providers to try for a given data request
    based on config.PROVIDER_ROUTING_POLICY and config.PROVIDER_CAPABILITIES.
    """
    
    def __init__(self):
        self.registry = registry

    def resolve_dataset_id(self, dataset_or_interval: str, fetch_type: str = "historical") -> str:
        """Helper to map interval string (e.g. '1d') to canonical dataset ID (e.g. 'price_1d')."""
        if not dataset_or_interval:
            return "default"
            
        dataset_str = str(dataset_or_interval).strip().lower()
        if dataset_str.startswith("price_"):
            return dataset_str
            
        if fetch_type == "live_quotes" or dataset_str in ("quote", "live"):
            return "live_quotes"
            
        if dataset_str in ("1d", "1wk", "1mo", "1h", "30m", "15m", "5m", "1m"):
            return f"price_{dataset_str}"
            
        return dataset_str

    def get_providers(self, dataset_id: str, fetch_type: str = "historical", required_capability: Optional[str] = None) -> List[str]:
        """
        Returns an ordered list of providers to try (primary, secondary, etc.)
        governed by config.PROVIDER_ROUTING_POLICY and capability checks.
        """
        canonical_key = self.resolve_dataset_id(dataset_id, fetch_type=fetch_type)
        
        # 1. Check registry override first if defined
        entry = self.registry.get_entry(canonical_key)
        if entry and entry.preferred_provider:
            primary = entry.preferred_provider.lower()
            if primary == "fyers":
                base_route = ["fyers", "upstox", "yahoo", "bse"]
            elif primary == "upstox":
                base_route = ["upstox", "fyers", "yahoo", "bse"]
            elif primary == "yahoo":
                base_route = ["yahoo", "upstox", "fyers", "bse"]
            elif primary == "nse":
                base_route = ["nse"]
            else:
                base_route = [primary, "upstox", "fyers", "yahoo", "bse"]
        else:
            # 2. Configuration-driven policy routing from config.py
            routing_policy = getattr(config, "PROVIDER_ROUTING_POLICY", {})
            base_route = list(routing_policy.get(canonical_key, routing_policy.get("default", ["upstox", "fyers", "yahoo", "bse"])))

        # Automatically prioritize Upstox as primary provider if UPSTOX_ACCESS_TOKEN is configured
        if getattr(config, "UPSTOX_ACCESS_TOKEN", None):
            if "upstox" not in base_route:
                base_route = ["upstox"] + base_route
            elif base_route[0] != "upstox":
                base_route = ["upstox"] + [p for p in base_route if p != "upstox"]

        # 3. Optional Capability Filter
        if required_capability:
            capabilities = getattr(config, "PROVIDER_CAPABILITIES", {})
            filtered_route = []
            for p in base_route:
                p_caps = capabilities.get(p, {})
                if p_caps.get(required_capability, True):
                    filtered_route.append(p)
                else:
                    logger.debug(f"Provider '{p}' filtered out for key '{canonical_key}': lacks capability '{required_capability}'")
            if filtered_route:
                return filtered_route

        return list(base_route)

# Global instance
selector = ProviderSelector()

