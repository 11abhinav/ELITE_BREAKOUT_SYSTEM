import logging
import requests
import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime

from ..core.interfaces import ProviderInterface
from ..core.models import NormalizedMarketData, CapabilityMatrix, ProviderStatus, DataProvenance

logger = logging.getLogger(__name__)

class UpstoxProvider(ProviderInterface):
    """
    Official Upstox API v2 Integration.
    Acts as the Primary Historical Data Provider to bypass WAF bans.
    """
    def __init__(self, auth_service):
        self.auth_service = auth_service
        self._capabilities = CapabilityMatrix(
            supports_1m=True,
            supports_5m=True,
            supports_15m=True,
            supports_1h=True,
            supports_1d=True,
            supports_corporate_actions=False,
            supports_oi=True
        )
        self._health_score = 100.0
        self._status = ProviderStatus.HEALTHY
        
    @property
    def provider_name(self) -> str:
        return "Upstox"
        
    @property
    def capabilities(self) -> CapabilityMatrix:
        return self._capabilities
        
    def get_health_score(self) -> float:
        return self._health_score
        
    def get_status(self) -> ProviderStatus:
        return self._status
        
    def _map_timeframe(self, timeframe: str) -> str:
        mapping = {
            "1m": "1minute",
            "5m": "5minute",
            "15m": "15minute",
            "30m": "30minute",
            "1h": "60minute",
            "1d": "day"
        }
        return mapping.get(timeframe.lower(), "day")
        
    def _get_instrument_key(self, symbol: str) -> str:
        # Converts "RELIANCE" to "NSE_EQ|INE002A01018" (Simplification for now)
        return f"NSE_EQ|{symbol}"

    def fetch_ohlcv(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> NormalizedMarketData:
        # 1. Use Long-Lived Analytics Token
        from ... import config
        token = config.UPSTOX_ACCESS_TOKEN
        
        if not token:
            self._status = ProviderStatus.AUTH_FAILED
            self._health_score -= 10
            raise PermissionError("UPSTOX_ACCESS_TOKEN is completely missing from config.")
            
        # 2. Build Request
        instrument_key = self._get_instrument_key(symbol)
        interval = self._map_timeframe(timeframe)
        url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{range_to.strftime('%Y-%m-%d')}/{range_from.strftime('%Y-%m-%d')}"
        
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        start_time = datetime.now()
        
        try:
            # TODO: Add specific Upstox RateLimiter (100 req / 10s) here
            response = requests.get(url, headers=headers, timeout=10)
            latency = (datetime.now() - start_time).total_seconds() * 1000
            
            if response.status_code == 429:
                self._health_score = max(0, self._health_score - 5)
                logger.warning("Upstox Rate Limit hit (429)")
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="429 Rate Limit")
                
            if response.status_code == 401:
                self._status = ProviderStatus.AUTH_FAILED
                self._health_score -= 20
                logger.error("Upstox Analytics Token is INVALID or EXPIRED (401).")
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="401 Auth Expired")
                
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "success":
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="API Failure")
                
            candles = data.get("data", {}).get("candles", [])
            if not candles:
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 100), error=None)
                
            # Upstox returns: [timestamp, open, high, low, close, volume, oi]
            df = pd.DataFrame(candles, columns=["Datetime", "Open", "High", "Low", "Close", "Volume", "OI"])
            df["Datetime"] = pd.to_datetime(df["Datetime"])
            df = df.set_index("Datetime").sort_index()
            
            prov = DataProvenance(self.provider_name, start_time, latency, 100.0)
            return NormalizedMarketData(symbol, timeframe, df, prov, is_complete_candle=True)
            
        except Exception as e:
            self._health_score = max(0, self._health_score - 2)
            logger.error(f"Upstox fetch error for {symbol}: {e}")
            latency = (datetime.now() - start_time).total_seconds() * 1000
            prov = DataProvenance(self.provider_name, start_time, latency, 0.0)
            return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), prov, error=str(e))
            
    def fetch_batch_ohlcv(self, symbols: List[str], timeframe: str, range_from: datetime, range_to: datetime) -> Dict[str, NormalizedMarketData]:
        # Upstox does not support bulk downloading natively via a single endpoint.
        # Coordinator manages parallelization. For interface compliance:
        results = {}
        for sym in symbols:
            results[sym] = self.fetch_ohlcv(sym, timeframe, range_from, range_to)
        return results
