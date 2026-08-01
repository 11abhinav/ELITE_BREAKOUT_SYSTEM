from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from datetime import datetime
from .models import NormalizedMarketData, CapabilityMatrix, ProviderStatus

class ProviderInterface(ABC):
    """
    The absolute standard contract that every Provider (Upstox, Fyers, etc.) MUST implement.
    No provider-specific logic should leak outside of implementations of this interface.
    """
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass
        
    @property
    @abstractmethod
    def capabilities(self) -> CapabilityMatrix:
        pass
        
    @abstractmethod
    def get_health_score(self) -> float:
        """Returns 0-100 score indicating current provider health based on latency/429s."""
        pass
        
    @abstractmethod
    def get_status(self) -> ProviderStatus:
        pass
        
    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> NormalizedMarketData:
        """Fetch historical delta data."""
        pass
        
    @abstractmethod
    def fetch_batch_ohlcv(self, symbols: List[str], timeframe: str, range_from: datetime, range_to: datetime) -> Dict[str, NormalizedMarketData]:
        """Fetch historical data for a batch of symbols."""
        pass

class AuthenticationService(ABC):
    """
    Abstracts authentication logic away from the providers. 
    Manages OAuth tokens, refresh cycles, and headless browser fallbacks.
    """
    
    @abstractmethod
    def get_valid_token(self, provider_name: str) -> str:
        """Returns a valid access token. Blocks to refresh if necessary."""
        pass
        
    @abstractmethod
    def force_refresh(self, provider_name: str) -> bool:
        """Forces an OAuth refresh or browser login."""
        pass
