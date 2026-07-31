import logging
import pandas as pd
from typing import Optional, Dict
from .fyers_fetcher import FyersFetcher
from .provider_selector import selector
from data_registry import registry
from yf_rate_limiter import acquire as yf_acquire, release as yf_release

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
                    # ── FORMAT GATE: validate Yahoo format before download ──────────────────
                    try:
                        from symbol_format_validator import validate_yahoo_symbol
                        yf_symbol = validate_yahoo_symbol(symbol + ".NS")
                    except ValueError as fmt_err:
                        logger.error(f"🚫 [YahooFormat] Skipping fetch_historical — invalid Yahoo symbol: {fmt_err}")
                        continue
                    logger.info(f"🔄 [Yahoo] Falling back to {yf_symbol}")
                    # [VERSION: UNIFIED_FETCHER_KEYERROR_FIX_v1.0] Rate-limited yf.download with strict timeouts & single-threading
                    yf_acquire(context=f"UnifiedFetcher.fetch_historical | {yf_symbol}")
                    try:
                        df = yf.download(yf_symbol, interval=interval, period=period, progress=False, threads=False, auto_adjust=True, timeout=60)
                    finally:
                        yf_release()
                        
                    if df is not None and not df.empty:
                        df = df.reset_index()
                        if "Date" in df.columns:
                            df.rename(columns={"Date": "Datetime"}, inplace=True)
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        else:
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
                    yf_acquire(context=f"UnifiedFetcher.fetch_historical | {yf_symbol}")
                    try:
                        df = yf.download(yf_symbol, interval=interval, period=period, progress=False, threads=False, auto_adjust=True, timeout=60)
                    finally:
                        yf_release()

                    if df is not None and not df.empty:
                        df = df.reset_index()
                        if "Date" in df.columns:
                            df.rename(columns={"Date": "Datetime"}, inplace=True)
                        if isinstance(df.columns, pd.MultiIndex):
                            df.columns = df.columns.get_level_values(0)
                        else:
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
                    # [VERSION: CONCURRENT_FYERS_BATCH_v1.0] Run Fyers live quote batches concurrently using ThreadPoolExecutor
                    import concurrent.futures

                    def fetch_fyers_chunk(chunk):
                        fyers_map = {}
                        for orig in chunk:
                            norm = self.fyers._normalize_symbol(orig)
                            if norm and (norm.startswith("NSE:") or norm.startswith("BSE:") or norm.startswith("MCX:")):
                                fyers_map[norm] = orig

                        if not fyers_map:
                            return

                        try:
                            from fyers_auth import get_fyers_client
                            import time
                            fyers_client = get_fyers_client()
                            if fyers_client:
                                fyers_symbols_str = ",".join(fyers_map.keys())
                                resp = fyers_client.quotes({"symbols": fyers_symbols_str})

                                if resp and isinstance(resp, dict) and resp.get("s") == "ok":
                                    success_count = 0
                                    for item in resp.get("d", []):
                                        if item.get("s") == "ok" and "v" in item and "lp" in item["v"]:
                                            sym_name = item.get("n")
                                            orig = fyers_map.get(sym_name)
                                            if orig:
                                                results[orig] = {"v": {"cmd": {"c": item["v"]["lp"]}}}
                                                logger.info(f"✅ [Fyers] Successfully fetched live quote for {orig} ({sym_name}): ₹{item['v']['lp']:.2f}")
                                                pending.discard(orig)
                                                success_count += 1
                                    if success_count > 0:
                                        logger.info(f"✅ [Fyers] Fetched {success_count}/{len(fyers_map)} quotes successfully.")
                                else:
                                    logger.warning(f"⚠️ [Fyers] Quote batch response not ok for {len(fyers_map)} symbols: {resp}")
                        except Exception as e:
                            logger.warning(f"⚠️ [Fyers] Batch quote fetch failed: {e}")

                    pending_list = list(pending)
                    chunk_size = 50
                    chunks = [pending_list[i:i+chunk_size] for i in range(0, len(pending_list), chunk_size)]
                    
                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(chunks) or 1)) as executor:
                        executor.map(fetch_fyers_chunk, chunks)
                        
                elif provider == "yahoo":
                    logger.info(f"🔄 [Yahoo] Fetching live quotes for {len(pending)} symbols...")
                    import yfinance as yf
                    pending_list = list(pending)
                    chunk_size = 100
                    
                    INDEX_YF_MAP = {
                        "NIFTY 50": "^NSEI", "NIFTY": "^NSEI", "^NSEI": "^NSEI",
                        "BANKNIFTY": "^NSEBANK", "^NSEBANK": "^NSEBANK",
                        "SENSEX": "^BSESN", "^BSESN": "^BSESN"
                    }
                    
                    for i in range(0, len(pending_list), chunk_size):
                        chunk = pending_list[i:i+chunk_size]
                        # ── FORMAT GATE: ensure every symbol is SYMBOL.NS or ^INDEX for Yahoo ──────────
                        raw_yf_symbols = [INDEX_YF_MAP.get(s, s + ".NS") for s in chunk]
                        try:
                            from symbol_format_validator import sanitize_yahoo_ticker_list
                            yf_symbols = sanitize_yahoo_ticker_list(raw_yf_symbols)
                        except Exception:
                            yf_symbols = raw_yf_symbols
                        try:
                            yf_acquire(context="UnifiedFetcher.fetch_live_quotes | Yahoo")
                            try:
                                df = yf.download(" ".join(yf_symbols), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True, timeout=60)
                            finally:
                                yf_release()
                                
                            if not df.empty:
                                for y_sym, orig in zip(yf_symbols, chunk):
                                    try:
                                        sub_df = None
                                        if isinstance(df.columns, pd.MultiIndex):
                                            if y_sym in df.columns.get_level_values(0):
                                                sub_df = df[y_sym]
                                            elif y_sym in df.columns.get_level_values(1):
                                                sub_df = df.xs(y_sym, axis=1, level=1)
                                        elif "Close" in df.columns:
                                            sub_df = df
                                            
                                        if sub_df is not None and not sub_df.empty and "Close" in sub_df.columns:
                                            val = float(sub_df["Close"].dropna().iloc[-1])
                                            if val > 0:
                                                results[orig] = {"v": {"cmd": {"c": val}}}
                                                logger.info(f"✅ [Yahoo] Successfully fetched live quote for {orig} ({y_sym}): ₹{val:.2f}")
                                                pending.discard(orig)
                                    except Exception:
                                        pass
                        except Exception as e:
                            logger.warning(f"⚠️ [Yahoo] Batch quote fetch failed: {e}")
                        
                elif provider == "bse":
                    # Skip BSE provider if there are no pending symbols or if circuit is still open.
                    # BSE provider for indices uses the same yfinance endpoint as Yahoo (^NSEI, ^NSEBANK, ^BSESN
                    # map to the same Yahoo symbols regardless of .BO suffix), so retrying when Yahoo is rate-limited
                    # just burns another full cooldown period on the same endpoint.
                    from yf_rate_limiter import is_circuit_open
                    if is_circuit_open():
                        logger.warning("⚠️ [BSE] Skipping BSE fallback — Yahoo circuit still open. No point retrying same endpoint.")
                    elif pending:
                        logger.info(f"🔄 [BSE] Fetching live quotes for {len(pending)} symbols...")
                        import yfinance as yf
                        pending_list = list(pending)
                        chunk_size = 100

                        INDEX_BSE_MAP = {
                            "NIFTY 50": "^NSEI", "NIFTY": "^NSEI", "^NSEI": "^NSEI",
                            "BANKNIFTY": "^NSEBANK", "^NSEBANK": "^NSEBANK",
                            "SENSEX": "^BSESN", "^BSESN": "^BSESN"
                        }

                        for i in range(0, len(pending_list), chunk_size):
                            chunk = pending_list[i:i+chunk_size]
                            yf_symbols = [INDEX_BSE_MAP.get(s, s + ".BO") for s in chunk]
                            try:
                                yf_acquire(context="UnifiedFetcher.fetch_live_quotes | BSE")
                                try:
                                    df = yf.download(" ".join(yf_symbols), period="1d", group_by="ticker", progress=False, threads=False, auto_adjust=True, timeout=60)
                                finally:
                                    yf_release()

                                if not df.empty:
                                    for y_sym, orig in zip(yf_symbols, chunk):
                                        try:
                                            sub_df = None
                                            if isinstance(df.columns, pd.MultiIndex):
                                                if y_sym in df.columns.get_level_values(0):
                                                    sub_df = df[y_sym]
                                                elif y_sym in df.columns.get_level_values(1):
                                                    sub_df = df.xs(y_sym, axis=1, level=1)
                                            elif "Close" in df.columns:
                                                sub_df = df

                                            if sub_df is not None and not sub_df.empty and "Close" in sub_df.columns:
                                                val = float(sub_df["Close"].dropna().iloc[-1])
                                                if val > 0:
                                                    results[orig] = {"v": {"cmd": {"c": val}}}
                                                    logger.info(f"✅ [BSE] Successfully resolved fallback live quote for {orig} ({y_sym}): ₹{val:.2f}")
                                                    pending.discard(orig)
                                        except Exception:
                                            pass
                            except Exception as e:
                                logger.warning(f"⚠️ [BSE] Batch quote fetch failed: {e}")

        if pending:
            logger.error(f"❌ Exhausted all providers for live quotes. Failed symbols: {len(pending)}")
            
        if self.registry.get_entry(dataset_id):
            self.registry.get_entry(dataset_id).provider_used = "live_batch"
            
        return results

# Global instance
fetcher = UnifiedFetcher()
