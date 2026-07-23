import logging
import pandas as pd
from typing import Optional, Dict
from .fyers_fetcher import FyersFetcher
from .provider_selector import selector
from data_registry import registry

import threading

logger = logging.getLogger(__name__)

# Resource-specific lock for all external provider fetches
network_fetch_lock = threading.Lock()

class UnifiedFetcher:
    """
    Data requested through this unified fetcher is logged in DatasetRegistry.
    Provider fallback policy is governed by ProviderSelector.
    """
    def __init__(self):
        self.registry = registry
        self.selector = selector
        self.fyers = FyersFetcher()

    def fetch_historical(self, symbol: str, interval: str, period: str, consumer: str) -> Optional[pd.DataFrame]:
        with network_fetch_lock:
            logger.info(f"[{consumer}] Fetching {symbol} ({interval} / {period}) via UnifiedFetcher")
            
            dataset_id = f"price_{interval}"
            if self.registry.get_entry(dataset_id):
                self.registry.register_consumer(dataset_id, consumer)
            
        providers = self.selector.get_providers(dataset_id, fetch_type="historical")
        
        for provider in providers:
            if provider == "fyers":
                try:
                    md = self.fyers.get_ohlcv(symbol, interval=interval, period=period)
                    if md is not None and md.df is not None and not md.df.empty:
                        logger.info(f"✅ [Fyers] Successfully fetched historical {symbol}")
                        entry = self.registry.get_entry(dataset_id)
                        if entry:
                            entry.provider_used = "fyers"
                            is_fallback = entry.preferred_provider and provider != entry.preferred_provider
                            from datetime import datetime
                            md.df.attrs = {
                                "dataset": dataset_id,
                                "provider": provider,
                                "preferred_provider": entry.preferred_provider,
                                "fallback_used": bool(is_fallback),
                                "fetch_timestamp": datetime.now().isoformat()
                            }
                        return md.df
                except Exception as e:
                    logger.warning(f"⚠️ [Fyers] Failed to fetch historical {symbol}: {e}")
            
            elif provider == "yahoo":
                try:
                    import yfinance as yf
                    yf_symbol = symbol + ".NS"
                    logger.info(f"🔄 [Yahoo] Falling back to {yf_symbol}")
                    df = yf.download(yf_symbol, interval=interval, period=period, progress=False)
                    if df is not None and not df.empty:
                        df = df.reset_index()
                        if "Date" in df.columns:
                            df.rename(columns={"Date": "Datetime"}, inplace=True)
                        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
                        logger.info(f"✅ [Yahoo] Successfully fetched historical {symbol}")
                        entry = self.registry.get_entry(dataset_id)
                        if entry:
                            entry.provider_used = "yahoo"
                            is_fallback = entry.preferred_provider and provider != entry.preferred_provider
                            from datetime import datetime
                            df.attrs = {
                                "dataset": dataset_id,
                                "provider": provider,
                                "preferred_provider": entry.preferred_provider,
                                "fallback_used": bool(is_fallback),
                                "fetch_timestamp": datetime.now().isoformat()
                            }
                        return df
                except Exception as e:
                    logger.warning(f"⚠️ [Yahoo] Failed to fetch historical {symbol}: {e}")
                    
            elif provider == "bse":
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
                        logger.info(f"✅ [BSE] Successfully fetched historical {symbol}")
                        entry = self.registry.get_entry(dataset_id)
                        if entry:
                            entry.provider_used = "bse"
                            is_fallback = entry.preferred_provider and provider != entry.preferred_provider
                            from datetime import datetime
                            df.attrs = {
                                "dataset": dataset_id,
                                "provider": provider,
                                "preferred_provider": entry.preferred_provider,
                                "fallback_used": bool(is_fallback),
                                "fetch_timestamp": datetime.now().isoformat()
                            }
                        return df
                except Exception as e:
                    logger.error(f"❌ [BSE] Failed to fetch historical {symbol}: {e}")
                    
        logger.error(f"❌ Exhausted all providers for historical {symbol}")
        return pd.DataFrame()

    def fetch_live_quotes(self, symbols: list[str], consumer: str) -> dict[str, dict]:
        """
        Fetches live snapshot data (quotes) for a list of symbols.
        Uses ProviderSelector for routing.
        Returns a dict of symbol -> quote data mapping.
        """
        if not symbols:
            return {}
            
        with network_fetch_lock:
            logger.info(f"[{consumer}] Fetching live quotes for {len(symbols)} symbols via UnifiedFetcher")
            dataset_id = "live_quotes"
            if self.registry.get_entry(dataset_id):
                self.registry.register_consumer(dataset_id, consumer)
                
            providers = self.selector.get_providers(dataset_id, fetch_type="live_quotes")
            results: Dict[str, dict] = {}
            pending = set(symbols)
            
            for provider in providers:
            if not pending:
                break
                
            if provider == "fyers":
                logger.info(f"🔄 [Fyers] Fetching live quotes for {len(pending)} symbols...")
                pending_list = list(pending)
                chunk_size = 50
                for i in range(0, len(pending_list), chunk_size):
                    chunk = pending_list[i:i+chunk_size]
                    fyers_symbols = [self.fyers._normalize_symbol(s) for s in chunk if self.fyers._normalize_symbol(s)]
                    if not fyers_symbols: continue
                    
                    try:
                        from fyers_auth import get_fyers_client
                        fyers_client = get_fyers_client()
                        if fyers_client:
                            resp = fyers_client.quotes({"symbols": ",".join(fyers_symbols)})
                            if resp and isinstance(resp, dict) and resp.get("s") == "ok":
                                for item in resp.get("d", []):
                                    if item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                                        sym = item["n"].split("-")[-1] # Simple extract
                                        # Match back
                                        for orig in chunk:
                                            if self.fyers._normalize_symbol(orig) == item["n"]:
                                                results[orig] = {"v": {"cmd": {"c": item["v"]["lp"]}}}
                                                pending.remove(orig)
                                                break
                    except Exception as e:
                        logger.warning(f"⚠️ [Fyers] Batch quote fetch failed: {e}")
                        
            elif provider == "yahoo":
                logger.info(f"🔄 [Yahoo] Fetching live quotes for {len(pending)} symbols...")
                import yfinance as yf
                pending_list = list(pending)
                chunk_size = 100
                for i in range(0, len(pending_list), chunk_size):
                    chunk = pending_list[i:i+chunk_size]
                    yf_symbols = [s + ".NS" for s in chunk]
                    try:
                        df = yf.download(" ".join(yf_symbols), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True)
                        if len(chunk) == 1:
                            if not df.empty and "Close" in df.columns:
                                val = float(df["Close"].iloc[-1])
                                if val > 0:
                                    results[chunk[0]] = {"v": {"cmd": {"c": val}}}
                                    pending.remove(chunk[0])
                        elif hasattr(df.columns, 'levels'):
                            for y_sym, orig in zip(yf_symbols, chunk):
                                if y_sym in df.columns.levels[0]:
                                    if not df[y_sym].empty:
                                        val = float(df[y_sym]["Close"].iloc[-1])
                                        if val > 0:
                                            results[orig] = {"v": {"cmd": {"c": val}}}
                                            pending.remove(orig)
                    except Exception as e:
                        logger.warning(f"⚠️ [Yahoo] Batch quote fetch failed: {e}")
                        
            elif provider == "bse":
                logger.info(f"🔄 [BSE] Fetching live quotes for {len(pending)} symbols...")
                import yfinance as yf
                pending_list = list(pending)
                chunk_size = 100
                for i in range(0, len(pending_list), chunk_size):
                    chunk = pending_list[i:i+chunk_size]
                    yf_symbols = [s + ".BO" for s in chunk]
                    try:
                        df = yf.download(" ".join(yf_symbols), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True)
                        if len(chunk) == 1:
                            if not df.empty and "Close" in df.columns:
                                val = float(df["Close"].iloc[-1])
                                if val > 0:
                                    results[chunk[0]] = {"v": {"cmd": {"c": val}}}
                                    pending.remove(chunk[0])
                        elif hasattr(df.columns, 'levels'):
                            for y_sym, orig in zip(yf_symbols, chunk):
                                if y_sym in df.columns.levels[0]:
                                    if not df[y_sym].empty:
                                        val = float(df[y_sym]["Close"].iloc[-1])
                                        if val > 0:
                                            results[orig] = {"v": {"cmd": {"c": val}}}
                                            pending.remove(orig)
                    except Exception as e:
                        logger.warning(f"⚠️ [BSE] Batch quote fetch failed: {e}")

        if pending:
            logger.error(f"❌ Exhausted all providers for live quotes. Failed symbols: {len(pending)}")
            
        if self.registry.get_entry(dataset_id):
            self.registry.get_entry(dataset_id).provider_used = "live_batch"
            
        return results

# Global instance
fetcher = UnifiedFetcher()
