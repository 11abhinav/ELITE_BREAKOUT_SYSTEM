class ProviderError(Exception):
    """Base class for errors originating from data providers (Yahoo, Fyers, TradingView)."""
    pass

class RateLimitError(ProviderError):
    """Raised when a provider explicitly enforces a rate limit."""
    pass

class NetworkError(ProviderError):
    """Raised when a connection to a provider fails or times out."""
    pass

class InvalidSymbolError(ProviderError):
    """Raised when a provider confirms a symbol does not exist or is delisted."""
    pass

class CorruptDataError(ProviderError):
    """Raised when the provider returns data that fails schema or sanity checks."""
    pass

class MarketClosedError(ProviderError):
    """Raised when data is unavailable because the market is explicitly closed."""
    pass

class DatabaseWriteError(Exception):
    """Raised when a transactional boundary fails to commit data."""
    pass

class IndicatorCalculationError(Exception):
    """Raised when TA-Lib or Pandas mathematical operations fail (e.g. division by zero, missing periods)."""
    pass
