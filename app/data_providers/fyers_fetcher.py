import os
import sys
import time
import logging
import concurrent.futures
from datetime import datetime, timedelta
import pandas as pd
import pytz
from threading import Lock

# Ensure parent directory is in sys.path to access configurations and auth utilities
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from data_provider import DataFetcher
import fyers_auth
import config

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

class RateLimiter:
    """Thread-safe rate limiter to space requests and prevent HTTP 429 rate limit errors."""
    def __init__(self, max_per_second: float):
        self.interval = 1.0 / max_per_second
        self.last_call = 0.0
        self.lock = Lock()
        
    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_call = time.time()

# Shared rate limiter across Fyers fetcher instances. 
# Fyers limit is 10/sec. We use 4.0 to ensure bursts from threads don't overwhelm it.
_fyers_rate_limiter = RateLimiter(max_per_second=4.0)


class FyersFetcher(DataFetcher):
    def __init__(self):
        self.rate_limiter = _fyers_rate_limiter
        
        # Map standard intervals to Fyers resolution parameters
        self.INTERVAL_MAP = {
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            "1d": "1D"
        }

    def _normalize_symbol(self, symbol: str) -> str:
        """Translates standard symbols (e.g. RELIANCE, FIVESTAR.NS, ^NSEI) to Fyers specific formats.
        Also trims whitespace and normalizes casing to avoid "Invalid input" caused by trailing spaces or stray newlines.
        """
        if not symbol:
            return ""
        # Trim invisible characters first
        symbol = symbol.strip()
        sym = symbol.upper()
        
        # If already formatted with exchange prefix, return as is (prevents double-normalization)
        if sym.startswith("NSE:") or sym.startswith("BSE:") or sym.startswith("MCX:"):
            return sym
            
        if sym.endswith(".NS"):
            sym = sym[:-3]
        sym = sym.replace("_", "-")
        
        # Map index symbols
        if sym in ("^NSEI", "NIFTY", "NIFTY-50", "NSEI"):
            return "NSE:NIFTY50-INDEX"
        if sym in ("^NSEBANK", "BANKNIFTY", "NSEBANK"):
            return "NSE:NIFTYBANK-INDEX"
            
        if sym.startswith("^"):
            # Generic index format
            return f"NSE:{sym[1:]}-INDEX"
            
        # Standard stock format
        return f"NSE:{sym}-EQ"


    def _get_date_range(self, period: str) -> tuple[str, str]:
        """Calculates historical range_from and range_to date strings based on period string.
        For 'y' (year) requests, cap at 365 days to avoid Fyers 'Invalid input' on daily resolution.
        Uses zero-padded YYYY-MM-DD strings.
        """
        today = datetime.now(IST).date()
        days_back = 30
        p = (period or "").lower()

        if p.endswith("d"):
            try:
                days_back = int(p[:-1])
            except ValueError:
                days_back = 5
            buffer_days = max(3, int(days_back * 0.2))
        elif p.endswith("mo") or (p.endswith("m") and len(p) > 1):
            unit = p[:-2] if p.endswith("mo") else p[:-1]
            try:
                days_back = int(unit) * 30
            except ValueError:
                days_back = 30
            buffer_days = max(5, int(days_back * 0.25))
        elif p.endswith("y"):
            try:
                requested_years = int(p[:-1])
            except ValueError:
                requested_years = 1
            # Cap any yearly request to at most 365 days per single call
            days_back = min(requested_years * 365, 365)
            buffer_days = 0
        elif p == "max":
            days_back = 365 * 5  # keep as-is for non-daily resolutions
            buffer_days = int(days_back * 0.2)
        else:
            # default last 30 days
            days_back = 30
            buffer_days = max(3, int(days_back * 0.2))

        start_date = today - timedelta(days=days_back + buffer_days)
        # Ensure we never produce a span > 365 days for daily resolution callers
        return start_date.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3) -> pd.DataFrame:
        """Fetch OHLCV data for a single symbol from Fyers."""
        ns_symbol = self._normalize_symbol(symbol)
        logger.info(f"📥 Fetching OHLCV for {symbol} ({interval}, {period}) via Fyers API...")
        
        # Normalize interval key and map to Fyers resolution
        res = self.INTERVAL_MAP.get(interval.lower()) if isinstance(interval, str) else None
        if not res:
            logger.error(f"Unsupported interval for FyersFetcher: {interval}")
            return None
        # Ensure resolution is uppercase (Fyers can be strict about case)
        res = str(res).upper()

        # Compute date range and then enforce strict 365-day cap for daily resolution
        range_from, range_to = self._get_date_range(period)
        try:
            start_date = datetime.strptime(range_from, "%Y-%m-%d").date()
            end_date = datetime.strptime(range_to, "%Y-%m-%d").date()
        except Exception:
            # Fall back to safe defaults
            end_date = datetime.now(IST).date()
            start_date = end_date - timedelta(days=30)
            range_from = start_date.strftime("%Y-%m-%d")
            range_to = end_date.strftime("%Y-%m-%d")

        if res in ("1D", "D"):
            span_days = (end_date - start_date).days
            if span_days > 365:
                # Cap span to 365 days to avoid Fyers 'Invalid input'
                start_date = end_date - timedelta(days=365)
                range_from = start_date.strftime("%Y-%m-%d")

        client = fyers_auth.get_fyers_client()
        if not client:
            logger.error("Fyers API client is uninitialized. Generate a token via /fyers/login.")
            return None

        data = {
            "symbol": ns_symbol,
            "resolution": res,
            "date_format": "1",  # YYYY-MM-DD string format
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": "1"
        }
        
        for attempt in range(retries):
            try:
                self.rate_limiter.wait()
                response = client.history(data=data)
                
                if not response:
                    raise ValueError("Received empty response from Fyers history API")
                    
                if response.get("s") != "ok":
                    error_msg = response.get("message", "Unknown error")
                    raise ValueError(f"Fyers history API error: {error_msg}")
                    
                candles = response.get("candles", [])
                if not candles:
                    # Return empty DataFrame with expected layout
                    if interval == "1d":
                        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                    else:
                        return pd.DataFrame(columns=["Datetime", "Open", "High", "Low", "Close", "Volume"])
                
                df = pd.DataFrame(candles, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
                
                # Convert Fyers Unix epoch timestamps (seconds) to IST Datetimes
                timestamps = pd.to_datetime(df["Timestamp"], unit="s", utc=True).dt.tz_convert(IST)
                
                # Cast columns to appropriate float types
                df["Open"] = df["Open"].astype(float)
                df["High"] = df["High"].astype(float)
                df["Low"] = df["Low"].astype(float)
                df["Close"] = df["Close"].astype(float)
                df["Volume"] = df["Volume"].astype(float)
                
                if interval == "1d":
                    df["Date"] = timestamps.dt.date
                    df = df.drop(columns=["Timestamp"], errors="ignore")
                else:
                    df["Datetime"] = timestamps
                    df = df.drop(columns=["Timestamp"], errors="ignore")
                    
                return df
                
            except Exception as e:
                error_str = str(e)
                # Do not retry for non-retryable errors like bad symbols
                if "Invalid symbol provided" in error_str:
                    if ns_symbol.endswith("-EQ"):
                        fallback_sym = ns_symbol.replace("-EQ", "-BE")
                        logger.info(f"🔄 Fyers: {ns_symbol} is invalid, attempting fallback to {fallback_sym}")
                        ns_symbol = fallback_sym
                        data["symbol"] = fallback_sym
                        continue  # Immediate retry with -BE without sleeping
                    
                    logger.warning(f"⚠️ Skipping {ns_symbol} — non-retryable Fyers error: {e}")
                    return None
                    
                if "Invalid input" in error_str:
                    logger.warning(f"⚠️ Skipping {ns_symbol} — non-retryable Fyers error: {e}")
                    return None
                    
                # Log the failed payload to help debug "Invalid input" cases (captures trailing spaces, bad dates, floats)
                try:
                    logger.error(f"Failed Payload for {ns_symbol}: {data}")
                except Exception:
                    pass
                logger.warning(f"⚠️ Attempt {attempt+1}/{retries} failed for {ns_symbol}: {e}")
                # Add larger exponential backoff to handle rate limits gracefully
                import random
                time.sleep((2 ** attempt) * 1.5 + random.uniform(0.5, 1.5))
                
        logger.error(f"❌ Failed to download historical data for {symbol} after {retries} attempts.")
        return None

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3) -> dict[str, pd.DataFrame]:
        """Fetch OHLCV data for multiple symbols concurrently using ThreadPoolExecutor."""
        logger.info(f"📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via Fyers API...")
        normalized_map = {}
        for s in symbols:
            orig = s.strip() if isinstance(s, str) else s
            ns_sym = self._normalize_symbol(orig)
            if ns_sym not in normalized_map:
                normalized_map[ns_sym] = []
            normalized_map[ns_sym].append(orig)
            
        ns_symbols = list(normalized_map.keys())
        results = {}
        
        # Restrict max workers to 3 to prevent burst spikes on Fyers API
        max_workers = min(3, len(ns_symbols) if ns_symbols else 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ns = {
                executor.submit(self.get_ohlcv, ns_sym, interval, period, retries): ns_sym
                for ns_sym in ns_symbols
            }
            
            for future in concurrent.futures.as_completed(future_to_ns):
                ns_sym = future_to_ns[future]
                try:
                    df = future.result()
                    # Map dataframe to all requested symbols mapping to this normalized symbol
                    for orig_sym in normalized_map[ns_sym]:
                        results[orig_sym] = df
                except Exception as e:
                    logger.error(f"Error fetching batch OHLCV for {ns_sym}: {e}")
                    for orig_sym in normalized_map[ns_sym]:
                        results[orig_sym] = None
                        
        return results

    def get_quote(self, symbol: str) -> dict:
        """Fetch current market quote for a single symbol from Fyers."""
        ns_symbol = self._normalize_symbol(symbol)
        logger.info(f"📥 Fetching quote for {symbol} via Fyers API...")
        client = fyers_auth.get_fyers_client()
        if not client:
            logger.error("Fyers API client not initialized.")
            return {}
            
        data = {
            "symbols": ns_symbol
        }
        
        try:
            self.rate_limiter.wait()
            response = client.quotes(data=data)
            
            if response and response.get("s") == "ok" and response.get("d"):
                quote_data = response["d"][0]
                v = quote_data.get("v", {})
                
                # Mimic standard YFinance ticker info dictionary structure
                close_price = v.get("close", 0.0)
                net_change = v.get("ch", 0.0)
                prev_close = close_price - net_change
                
                return {
                    "regularMarketPrice": v.get("lp", close_price),
                    "currentPrice": v.get("lp", close_price),
                    "open": v.get("open"),
                    "dayHigh": v.get("high"),
                    "dayLow": v.get("low"),
                    "previousClose": prev_close,
                    "volume": v.get("volume"),
                    "symbol": symbol
                }
            else:
                error_msg = response.get("message", "Unknown error") if response else "Empty response"
                logger.error(f"Fyers quotes API returned error for {ns_symbol}: {error_msg}")
                return {}
        except Exception as e:
            logger.error(f"Failed to fetch quote for symbol {symbol}: {e}")
            return {}
