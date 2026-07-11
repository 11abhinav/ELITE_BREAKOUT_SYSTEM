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
_last_auth_notif_time = 0

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
# Fyers limit is often ~200/minute, but some users have stricter tiers (100/min).
# We use 1.5 (90/min) to stay safely below the limit.
_fyers_rate_limiter = RateLimiter(max_per_second=1.5)

# Circuit breaker for Fyers API to auto-fallback on repeated failures
class FyersCircuitBreaker:
    def __init__(self, failure_threshold: int = 10, reset_after_seconds: int = 300):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.reset_after_seconds = reset_after_seconds
        self.last_failure_time = 0
        self.is_open = False
        self.lock = Lock()

    def record_failure(self):
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.is_open = True
                logger.warning(f"⚠️ Fyers API circuit breaker OPENED after {self.failure_count} failures. Falling back to YFinance.")

    def is_available(self) -> bool:
        with self.lock:
            if not self.is_open:
                return True
            # Check if enough time has passed to attempt recovery
            if time.time() - self.last_failure_time > self.reset_after_seconds:
                self.is_open = False
                self.failure_count = 0
                logger.info("✅ Fyers API circuit breaker CLOSED. Attempting recovery.")
                return True
            return False

    def reset(self):
        with self.lock:
            self.failure_count = 0
            self.is_open = False

_fyers_circuit_breaker = FyersCircuitBreaker(failure_threshold=15, reset_after_seconds=600)


class FyersFetcher(DataFetcher):
    def __init__(self):
        self.rate_limiter = _fyers_rate_limiter
        
        # Map standard intervals to Fyers resolution parameters
        # Note: Fyers uses numeric strings for intraday and "D" for daily
        self.INTERVAL_MAP = {
            "5m": "5",
            "15m": "15",
            "30m": "30",
            "1h": "60",
            # CRITICAL FIX (Do not change in future revisions): 
            # In Fyers API, "1" means 1-minute data. For daily data, the resolution MUST be "1D" or "D".
            # Requesting "1" for a 365-day period triggers the 'range_to cannot be 100 days greater than range_from' error.
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
            
        # [VERSION: FYERS_PATCH_v1.0] Intercept ampersand symbols before blind replace
        # This fixes Fyers API warnings for M-M-EQ by enforcing M&M regardless of DB state
        _ampersand_map = {
            "M_M": "M&M", "M-M": "M&M",
            "M_MFIN": "M&MFIN", "M-MFIN": "M&MFIN",
            "J_KBANK": "J&KBANK", "J-KBANK": "J&KBANK",
            "GVT_D": "GVT&D", "GVT-D": "GVT&D",
            "L_TFH": "L&TFH", "L-TFH": "L&TFH",
            "T_IPOWER": "T&IPOWER", "T-IPOWER": "T&IPOWER",
        }
        if sym in _ampersand_map:
            sym = _ampersand_map[sym]
        else:
            sym = sym.replace("_", "-")
        
        # Map index symbols
        if sym in ("^NSEI", "NIFTY", "NIFTY-50", "NSEI"):
            return "NSE:NIFTY50-INDEX"
        if sym in ("^NSEBANK", "BANKNIFTY", "NSEBANK"):
            return "NSE:NIFTYBANK-INDEX"
            
        if sym.startswith("^"):
            # Generic index format
            return f"NSE:{sym[1:]}-INDEX"
            
        # Check mapping cache to skip the 1st failure if we already know it's a -BE
        try:
            from data_providers.fyers_mapping_utils import load_fyers_mappings
            mappings = load_fyers_mappings()
            if sym in mappings:
                return mappings[sym]
        except Exception:
            pass
            
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

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> pd.DataFrame:
        """Fetch OHLCV data for a single symbol from Fyers."""
        
        # Check if Fyers circuit breaker is open (too many failures)
        if not _fyers_circuit_breaker.is_available():
            return None
        
        ns_symbol = self._normalize_symbol(symbol)
        
        orig_sym = symbol.strip().upper()
        if orig_sym.endswith(".NS"): orig_sym = orig_sym[:-3]
        
        _ampersand_map = {
            "M_M": "M&M", "M-M": "M&M",
            "M_MFIN": "M&MFIN", "M-MFIN": "M&MFIN",
            "J_KBANK": "J&KBANK", "J-KBANK": "J&KBANK",
            "GVT_D": "GVT&D", "GVT-D": "GVT&D",
            "L_TFH": "L&TFH", "L-TFH": "L&TFH",
            "T_IPOWER": "T&IPOWER", "T-IPOWER": "T&IPOWER",
        }
        if orig_sym in _ampersand_map:
            orig_sym = _ampersand_map[orig_sym]
        else:
            orig_sym = orig_sym.replace("_", "-")
        try:
            from data_providers.fyers_mapping_utils import is_fyers_invalid
            if is_fyers_invalid(orig_sym):
                logger.debug(f"⚠️ Skipping known invalid Fyers symbol: {orig_sym}")
                return None
        except Exception:
            pass
            
        tried_suffixes = set()

        
        # Determine if this is an incremental fetch
        if range_from and range_to:
            logger.debug(f"📥 Fetching incremental OHLCV for {symbol} ({interval}) from {range_from} to {range_to} via Fyers API...")
            calc_range_from, calc_range_to = range_from, range_to
        else:
            logger.debug(f"📥 Fetching OHLCV for {symbol} ({interval}, {period}) via Fyers API...")
            calc_range_from, calc_range_to = self._get_date_range(period)
        
        # Normalize interval key and map to Fyers resolution
        res = self.INTERVAL_MAP.get(interval.lower()) if isinstance(interval, str) else None
        if not res:
            logger.error(f"Unsupported interval for FyersFetcher: {interval}")
            return None
        # Fyers resolution is already correctly formatted from INTERVAL_MAP as string

        # Compute date range and then enforce strict 365-day cap for daily resolution
        range_from, range_to = calc_range_from, calc_range_to
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
            "date_format": "1",
            "range_from": range_from,
            "range_to": range_to,
            "cont_flag": 1
        }
        
        for attempt in range(retries):
            try:
                self.rate_limiter.wait()
                response = client.history(data=data)
                
                if not response:
                    raise ValueError("Received empty response from Fyers history API")
                    
                if response.get("s") != "ok":
                    error_msg = response.get("message", "Unknown error")
                    code = response.get("code", "NO_CODE")
                    logger.warning(f"Fyers API warning for {ns_symbol}: code={code}, message={error_msg}, full_response={response}")
                    
                    if str(code) in ["494", "-401", "401"]:
                        logger.error(f"Fyers token is expired or invalid (code {code}). Clearing token cache.")
                        fyers_auth.clear_token()
                        raise ValueError("Could not authenticate the user")
                        
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
                    df["Date"] = pd.to_datetime(timestamps.dt.date)
                    df = df.drop(columns=["Timestamp"], errors="ignore")
                else:
                    df["Datetime"] = timestamps
                    df = df.drop(columns=["Timestamp"], errors="ignore")
                    
                return df
                
            except Exception as e:
                error_str = str(e)
                
                # Record failure for circuit breaker, but ignore expected validation errors
                if "Bad request" in error_str or "error" in error_str.lower():
                    if "invalid symbol" not in error_str.lower() and "invalid input" not in error_str.lower():
                        _fyers_circuit_breaker.record_failure()

                if "Could not authenticate the user" in error_str:
                    global _last_auth_notif_time
                    now = time.time()
                    if now - _last_auth_notif_time > 3600:
                        _last_auth_notif_time = now
                        try:
                            import database
                            database.insert_notification(
                                notif_type='system',
                                title='⚠️ Fyers Authentication Required',
                                message='Your Fyers API session expired or is missing. <a href="/fyers/login" style="color:#00e5a0; text-decoration:underline; font-weight:bold;">Click here to Re-authenticate</a>',
                                symbol='SYSTEM'
                            )
                        except Exception as notif_err:
                            logger.error(f"Failed to insert auth notification: {notif_err}")
                    return None
                    
                # Do not retry for non-retryable errors like bad symbols
                if "Invalid symbol provided" in error_str:
                    tried_suffixes.add(ns_symbol)
                    if ns_symbol.endswith("-EQ"):
                        fallback_sym = ns_symbol.replace("-EQ", "-BE")
                        if fallback_sym in tried_suffixes:
                            logger.warning(f"⚠️ Both -EQ and -BE failed for {orig_sym}. Marking as permanently invalid.")
                            try:
                                from data_providers.fyers_mapping_utils import mark_fyers_invalid
                                mark_fyers_invalid(orig_sym)
                            except Exception:
                                pass
                            return None
                            
                        logger.info(f"🔄 Fyers: {ns_symbol} is invalid, attempting fallback to {fallback_sym}")
                        
                        try:
                            from data_providers.fyers_mapping_utils import save_fyers_mapping
                            save_fyers_mapping(orig_sym, fallback_sym)
                        except Exception as e:
                            logger.warning(f"Failed to save fallback mapping: {e}")
                            
                        ns_symbol = fallback_sym
                        data["symbol"] = fallback_sym
                        continue  # Immediate retry with -BE without sleeping
                        
                    elif ns_symbol.endswith("-BE"):
                        fallback_sym = ns_symbol.replace("-BE", "-EQ")
                        if fallback_sym in tried_suffixes:
                            logger.warning(f"⚠️ Both -BE and -EQ failed for {orig_sym}. Marking as permanently invalid.")
                            try:
                                from data_providers.fyers_mapping_utils import mark_fyers_invalid
                                mark_fyers_invalid(orig_sym)
                            except Exception:
                                pass
                            return None
                            
                        logger.info(f"🔄 Fyers: {ns_symbol} is invalid (maybe moved back to EQ), attempting fallback to {fallback_sym}")
                        
                        try:
                            from data_providers.fyers_mapping_utils import remove_fyers_mapping
                            remove_fyers_mapping(orig_sym)
                        except Exception as e:
                            logger.warning(f"Failed to remove fallback mapping: {e}")
                            
                        ns_symbol = fallback_sym
                        data["symbol"] = fallback_sym
                        continue  # Immediate retry with -EQ without sleeping
                    
                    logger.warning(f"⚠️ Skipping {ns_symbol} — non-retryable Fyers error: {e}")
                    return None
                    
                if "Invalid input" in error_str:
                    logger.warning(f"⚠️ Skipping {ns_symbol} — non-retryable Fyers error: {e}")
                    return None
                    
                # Add larger exponential backoff to handle rate limits gracefully
                import random
                if "request limit reached" in error_str:
                    # Fyers usually has minute-level buckets for rate limits.
                    # A small 2-3s backoff is useless; we need to wait 20-30s for the bucket to reset.
                    backoff_time = 20.0 + random.uniform(0.0, 10.0)
                    logger.info(f"⏳ Rate limited by Fyers for {ns_symbol}. Backing off for {backoff_time:.1f}s... (Attempt {attempt+1}/{retries})")
                    time.sleep(backoff_time)
                else:
                    # Log the failed payload and full error response to help debug "Bad request" cases
                    try:
                        logger.error(f"Failed Payload for {ns_symbol}: {data}")
                        logger.error(f"Fyers API response for {ns_symbol}: {str(e)}")
                    except Exception:
                        pass
                    logger.warning(f"⚠️ Attempt {attempt+1}/{retries} failed for {ns_symbol}: {e}")
                    time.sleep((2 ** attempt) * 1.5 + random.uniform(0.5, 1.5))
                
        logger.error(f"❌ Failed to download historical data for {symbol} after {retries} attempts.")
        try:
            from data_fetch_status import mark_failure
            mark_failure('fyers', f"Failed to download history for {symbol} after {retries} attempts.")
        except Exception:
            pass
        return None

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, pd.DataFrame]:
        """Fetch OHLCV data for multiple symbols concurrently using ThreadPoolExecutor."""

        # Check if Fyers circuit breaker is open (too many failures)
        if not _fyers_circuit_breaker.is_available():
            return {}

        prefix = f"[{caller}] " if caller else ""
        if range_from and range_to:
            logger.info(f"{prefix}📥 Fetching incremental batch OHLCV for {len(symbols)} symbols ({interval}, {range_from} to {range_to}) via Fyers API...")
        else:
            logger.info(f"{prefix}📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via Fyers API...")
            
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
                executor.submit(self.get_ohlcv, ns_sym, interval, period, retries, range_from, range_to): ns_sym
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
                    logger.exception(f"Error fetching batch OHLCV for {ns_sym}")
                    for orig_sym in normalized_map[ns_sym]:
                        results[orig_sym] = None
                        
        return results

    def get_quote(self, symbol: str) -> dict:
        """Fetch current market quote for a single symbol from Fyers."""

        # Check if Fyers circuit breaker is open (too many failures)
        if not _fyers_circuit_breaker.is_available():
            return {}

        ns_symbol = self._normalize_symbol(symbol)
        
        orig_sym = symbol.strip().upper()
        if orig_sym.endswith(".NS"): orig_sym = orig_sym[:-3]
        
        _ampersand_map = {
            "M_M": "M&M", "M-M": "M&M",
            "M_MFIN": "M&MFIN", "M-MFIN": "M&MFIN",
            "J_KBANK": "J&KBANK", "J-KBANK": "J&KBANK",
            "GVT_D": "GVT&D", "GVT-D": "GVT&D",
            "L_TFH": "L&TFH", "L-TFH": "L&TFH",
            "T_IPOWER": "T&IPOWER", "T-IPOWER": "T&IPOWER",
        }
        if orig_sym in _ampersand_map:
            orig_sym = _ampersand_map[orig_sym]
        else:
            orig_sym = orig_sym.replace("_", "-")
        try:
            from data_providers.fyers_mapping_utils import is_fyers_invalid
            if is_fyers_invalid(orig_sym):
                logger.debug(f"⚠️ Skipping known invalid Fyers symbol for quotes: {orig_sym}")
                return {}
        except Exception:
            pass
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
                code = response.get("code", "NO_CODE") if response else "NO_CODE"
                logger.warning(f"Fyers quotes API returned warning for {ns_symbol}: {error_msg}, code={code}")
                
                if str(code) in ["494", "-401", "401"]:
                    logger.error(f"Fyers token is expired or invalid (code {code}). Clearing token cache.")
                    import fyers_auth
                    fyers_auth.clear_token()
                    # Trigger the 'Could not authenticate' handling below
                    raise ValueError("Could not authenticate the user")
                    
                if "invalid symbol" not in error_msg.lower():
                    _fyers_circuit_breaker.record_failure()
                    
                try:
                    from data_fetch_status import mark_failure
                    mark_failure('fyers', f"Quote API error for {symbol}: {error_msg}")
                except Exception:
                    pass
                return {}
        except Exception as e:
            error_str = str(e)
            # Record failure for circuit breaker
            if "error" in error_str.lower() or "request" in error_str.lower():
                if "invalid symbol" not in error_str.lower() and "invalid input" not in error_str.lower():
                    _fyers_circuit_breaker.record_failure()

            if "Could not authenticate the user" in error_str:
                global _last_auth_notif_time
                now = time.time()
                if now - _last_auth_notif_time > 3600:
                    _last_auth_notif_time = now
                    try:
                        import database
                        database.insert_notification(
                            notif_type='system',
                            title='⚠️ Fyers Authentication Required',
                            message='Your Fyers API session expired or is missing. <a href="/fyers/login" style="color:#00e5a0; text-decoration:underline; font-weight:bold;">Click here to Re-authenticate</a>',
                            symbol='SYSTEM'
                        )
                    except Exception:
                        pass

            logger.exception(f"Failed to fetch quote for symbol {symbol}")
            try:
                from data_fetch_status import mark_failure
                mark_failure('fyers', f"Quote fetch exception for {symbol}: {str(e)}")
            except Exception:
                pass
            return {}
