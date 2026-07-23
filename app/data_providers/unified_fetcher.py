import logging
import pandas as pd
from typing import Optional
from app.data_providers.fyers_fetcher import get_historical_data_fyers
from data_registry import registry

logger = logging.getLogger(__name__)

class UnifiedFetcher:
    """
    Enforces the frozen provider hierarchy:
    1. Fyers (Primary/Intraday)
    2. Yahoo (Secondary/Historical)
    3. BSE (Tertiary fallback)
    
    Data requested through this unified fetcher is logged in DatasetRegistry.
    """
    def __init__(self):
        self.registry = registry

    def fetch_historical(self, symbol: str, interval: str, period: str, consumer: str) -> Optional[pd.DataFrame]:
        logger.info(f"[{consumer}] Fetching {symbol} ({interval} / {period}) via UnifiedFetcher")
        
        # Track consumer in registry for dataset ID
        dataset_id = f"price_{interval}"
        if self.registry.get_entry(dataset_id):
            self.registry.register_consumer(dataset_id, consumer)
            
        # 1. Primary: Fyers
        try:
            df = get_historical_data_fyers(symbol, interval=interval, period=period)
            if df is not None and not df.empty:
                logger.info(f"✅ [Fyers] Successfully fetched {symbol}")
                if self.registry.get_entry(dataset_id):
                    self.registry.get_entry(dataset_id).provider_used = "fyers"
                return df
        except Exception as e:
            logger.warning(f"⚠️ [Fyers] Failed to fetch {symbol}: {e}")

        # 2. Secondary: Yahoo
        try:
            import yfinance as yf
            yf_symbol = symbol + ".NS"
            logger.info(f"🔄 [Yahoo] Falling back to {yf_symbol}")
            df = yf.download(yf_symbol, interval=interval, period=period, progress=False)
            if df is not None and not df.empty:
                # Standardize columns to match Fyers
                df = df.reset_index()
                if "Date" in df.columns:
                    df.rename(columns={"Date": "Datetime"}, inplace=True)
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                
                logger.info(f"✅ [Yahoo] Successfully fetched {symbol}")
                if self.registry.get_entry(dataset_id):
                    self.registry.get_entry(dataset_id).provider_used = "yahoo"
                return df
        except Exception as e:
            logger.warning(f"⚠️ [Yahoo] Failed to fetch {symbol}: {e}")

        # 3. Tertiary: BSE Fallback (usually for Yahoo, but handled via .BO suffix if needed)
        try:
            import yfinance as yf
            yf_symbol = symbol + ".BO"
            logger.info(f"🔄 [BSE] Falling back to {yf_symbol}")
            df = yf.download(yf_symbol, interval=interval, period=period, progress=False)
            if df is not None and not df.empty:
                df = df.reset_index()
                if "Date" in df.columns:
                    df.rename(columns={"Date": "Datetime"}, inplace=True)
                df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                
                logger.info(f"✅ [BSE] Successfully fetched {symbol}")
                if self.registry.get_entry(dataset_id):
                    self.registry.get_entry(dataset_id).provider_used = "bse"
                return df
        except Exception as e:
            logger.error(f"❌ [BSE] Failed to fetch {symbol}: {e}")
            
        logger.error(f"❌ Exhausted all providers for {symbol}")
        return pd.DataFrame()

# Global instance
fetcher = UnifiedFetcher()
