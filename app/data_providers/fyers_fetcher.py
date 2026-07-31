import os
import sys
import time
import logging
import concurrent.futures
from datetime import datetime, timedelta
import pandas as pd
from zoneinfo import ZoneInfo
from threading import Lock

# Ensure parent directory is in sys.path to access configurations and auth utilities
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from data_provider import DataFetcher
from validation import ValidationEngine, MarketData, ValidationContext, registry as val_registry, DatasetType

import fyers_auth
import config

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
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

# [VERSION: DATA_FETCH_ACCELERATION_v1.0] Rate limit increased to 3.0 req/sec with 6 parallel threads
_fyers_rate_limiter = RateLimiter(max_per_second=3.0)

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
        # [VERSION: NULL_POINTER_FIX_v1.0] Guard against missing symbols from upstream
        if not symbol:
            return ""
        # Trim invisible characters first
        symbol = str(symbol).strip()
        sym = symbol.upper()
        
        # If already formatted with exchange prefix, return as is (prevents double-normalization)
        if sym.startswith("NSE:") or sym.startswith("BSE:") or sym.startswith("MCX:"):
            if sym.startswith("BSE:") and not any(sym.endswith(sfx) for sfx in ("-EQ", "-BE", "-SM", "-ST", "-A", "-B", "-T", "-M", "-X", "-XC", "-XD", "-XT", "-INDEX")):
                return f"{sym}-EQ"
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
        if sym in ("^NSEI", "NIFTY", "NIFTY-50", "NIFTY 50", "NSEI"):
            return "NSE:NIFTY50-INDEX"
        if sym in ("^NSEBANK", "BANKNIFTY", "NSEBANK"):
            return "NSE:NIFTYBANK-INDEX"
        if sym in ("^BSESN", "SENSEX", "BSE:SENSEX-INDEX"):
            return "BSE:SENSEX-INDEX"
            
        if sym.startswith("^"):
            # Generic index format
            return f"NSE:{sym[1:]}-INDEX"
        
        # [VERSION: FYERS_SCRIP_OVERRIDE_v1.1] Static overrides for stocks where Fyers uses custom codes.
        _bse_scrip_overrides = {"NSDL": "BSE:NSDL-EQ"}
        if sym in _bse_scrip_overrides:
            return _bse_scrip_overrides[sym]
            
        # Check mapping cache to skip the 1st failure if we already know mapped format
        try:
            from data_providers.fyers_mapping_utils import load_fyers_mappings
            mappings = load_fyers_mappings()
            if sym in mappings and mappings[sym]:
                return mappings[sym]
            if symbol.strip().upper() in mappings and mappings[symbol.strip().upper()]:
                return mappings[symbol.strip().upper()]
        except Exception:
            pass

        # Check BSE mapping cache to immediately use BSE for known BSE-only stocks
        try:
            from bse_mapping_utils import load_bse_mappings
            bse_mappings = load_bse_mappings()
            if sym in bse_mappings or sym.endswith(".BO"):
                clean_sym = sym[:-3] if sym.endswith(".BO") else sym
                return f"BSE:{clean_sym}-EQ"
            if sym.endswith(".NS") and sym[:-3] in bse_mappings:
                return f"BSE:{sym[:-3]}-EQ"
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

        range_from = (today - timedelta(days=days_back + buffer_days)).strftime("%Y-%m-%d")
        range_to = today.strftime("%Y-%m-%d")
        return range_from, range_to

    def _generate_fyers_candidate_symbols(self, symbol: str) -> list[str]:
        """
        Generates an exhaustive, multi-exchange & multi-series candidate list for Fyers API.
        Supports: Mainboard (-EQ), Trade-to-Trade/ASM/GSM (-BE), SME (-SM, -ST),
        BSE Mainboard (BSE:SYMBOL-EQ, BSE:SYMBOL), and BSE Scrip Codes (BSE:5XXXXX-EQ).
        Prioritizes BSE series for BSE-preference symbols (.BO, BSE:, or BSE-mapped).
        """
        if not symbol:
            return []

        raw = str(symbol).strip().upper()
        _ampersand_map = {
            "M_M": "M&M", "M-M": "M&M",
            "M_MFIN": "M&MFIN", "M-MFIN": "M&MFIN",
            "J_KBANK": "J&KBANK", "J-KBANK": "J&KBANK",
        }
        if raw in _ampersand_map:
            raw = _ampersand_map[raw]

        is_bse_pref = raw.startswith("BSE:") or raw.endswith(".BO")

        if raw.endswith(".NS") or raw.endswith(".BO"):
            raw = raw[:-3]
        if ":" in raw:
            raw = raw.split(":", 1)[1]

        # Strip standard series suffixes
        base = raw
        for suffix in ("-EQ", "-BE", "-SM", "-ST", "-A", "-B", "-T", "-M", "-X", "-XC", "-XD", "-XT"):
            if base.endswith(suffix):
                base = base[:-len(suffix)]
                break

        # Check if known in BSE mappings
        try:
            from bse_mapping_utils import load_bse_mappings
            if base in load_bse_mappings():
                is_bse_pref = True
        except Exception:
            pass

        candidates = []

        # If base is numeric (BSE Scrip Code), prioritize BSE
        if base.isdigit():
            candidates.append(f"BSE:{base}-EQ")
            candidates.append(f"BSE:{base}")
        elif is_bse_pref:
            # Prioritize BSE series for BSE-preference stocks
            candidates.append(f"BSE:{base}-EQ")
            candidates.append(f"BSE:{base}")
            candidates.append(f"NSE:{base}-EQ")
            candidates.append(f"NSE:{base}-BE")
        else:
            # Standard NSE first (-EQ, -BE series), then BSE series
            candidates.append(f"NSE:{base}-EQ")
            candidates.append(f"NSE:{base}-BE")
            candidates.append(f"BSE:{base}-EQ")
            candidates.append(f"BSE:{base}")

            # Known BSE scrip code map for stocks with custom Fyers BSE tickers
            _KNOWN_BSE_SCRIP_CODES = {
                "POONAWALLA": "524000",
                "PFC": "532648",
                "SENORES": "544256",
                "MRF": "500290",
                "TORNTPHARM": "500420",
                "HINDUNILVR": "500696",
                "HAL": "541154",
                "AADHARHFC": "544175",
            }
            if base in _KNOWN_BSE_SCRIP_CODES:
                bse_code = _KNOWN_BSE_SCRIP_CODES[base]
                candidates.append(f"BSE:{bse_code}-EQ")
                candidates.append(f"BSE:{bse_code}")

            try:
                from bse_mapping_utils import load_bse_mappings
                bse_map = load_bse_mappings()
                if base in bse_map:
                    clean_code = str(bse_map[base]).upper().replace(".BO", "").replace("BSE:", "").strip()
                    if clean_code.isdigit():
                        candidates.append(f"BSE:{clean_code}-EQ")
                        candidates.append(f"BSE:{clean_code}")
            except Exception:
                pass

        # Deduplicate preserving order
        seen = set()
        deduped = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                deduped.append(c)
        return deduped

    def get_ohlcv(self, symbol: str, interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None) -> MarketData:
        """Fetch OHLCV data for a single symbol from Fyers."""
        # [VERSION: NULL_POINTER_FIX_v1.0]
        if not symbol:
            return MarketData(None, "UNKNOWN", None, False, False, "No symbol")
            
        # Check if Fyers circuit breaker is open (too many failures)
        if not _fyers_circuit_breaker.is_available():
            return MarketData(None, "Fyers", None, False, False, "Circuit Breaker Open")
        
        ns_symbol = self._normalize_symbol(symbol)
        
        orig_sym = symbol.strip().upper()
        if orig_sym.endswith(".NS"): orig_sym = orig_sym[:-3]
        if orig_sym.startswith("NSE:"): orig_sym = orig_sym[4:]
        if orig_sym.startswith("BSE:"): orig_sym = orig_sym[4:]
        if orig_sym.endswith("-EQ"): orig_sym = orig_sym[:-3]
        if orig_sym.endswith("-BE"): orig_sym = orig_sym[:-3]
        
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
            # Skip the invalid check if this symbol has a known static scrip override
            _scrip_overrides_check = {"NSDL"}  # keep in sync with _normalize_symbol overrides
            if orig_sym not in _scrip_overrides_check and is_fyers_invalid(orig_sym):
                logger.debug(f"⚠️ Skipping known invalid Fyers symbol: {orig_sym}")
                return None
        except Exception:
            pass
            
        tried_suffixes = set()
        original_ns = ns_symbol

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
        else:
            # Fyers API enforces a strict 100-day cap for intraday resolutions (1m to 240m / 1h)
            span_days = (end_date - start_date).days
            if span_days > 99:
                start_date = end_date - timedelta(days=99)
                range_from = start_date.strftime("%Y-%m-%d")

        # Multi-series & multi-exchange candidate resolution loop
        candidates = self._generate_fyers_candidate_symbols(orig_sym)
        if ns_symbol not in candidates:
            candidates.insert(0, ns_symbol)

        for cand_symbol in candidates:
            if cand_symbol in tried_suffixes:
                continue

            data = {
                "symbol": cand_symbol,
                "resolution": res,
                "date_format": "1",
                "range_from": range_from,
                "range_to": range_to,
                "cont_flag": "1"
            }
            
            for attempt in range(retries):
                try:
                    client = fyers_auth.get_fyers_client()
                    if not client:
                        logger.error("Fyers API client is uninitialized. Generate a token via /fyers/login.")
                        from core_exceptions import ProviderError
                        raise ProviderError("Fyers Authentication Required")

                    self.rate_limiter.wait()
                    response = client.history(data=data)
                    
                    if not response:
                        raise ValueError("Received empty response from Fyers history API")
                        
                    if response.get("s") != "ok":
                        error_msg = str(response.get("message", "Unknown error"))
                        code = str(response.get("code", "NO_CODE"))
                        if "Invalid symbol provided" in error_msg or code in ("-403", "403") or "Additional permission required" in error_msg:
                            logger.info(f"Fyers API symbol miss/unsupported series for {cand_symbol} (code {code}) - trying next candidate")
                            tried_suffixes.add(cand_symbol)
                            break
                        else:
                            logger.warning(f"Fyers API warning for {cand_symbol}: code={code}, message={error_msg}, full_response={response}")
                        
                        if code in ["494", "-401", "401", "-16", "-15"] or "authenticate" in error_msg.lower():
                            logger.warning(f"Fyers auth response warning for {cand_symbol} (code {code}): {error_msg}")
                            raise ValueError("Could not authenticate the user")
                            
                        raise ValueError(f"Fyers history API error: {error_msg}")
                        
                    candles = response.get("candles", [])
                    if not candles:
                        return MarketData(None, "Fyers", None, False, False, "No data available in response")
                    
                    df = pd.DataFrame(candles, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
                    
                    # Convert Fyers Unix epoch timestamps (seconds) to IST Datetimes
                    timestamps = pd.to_datetime(df["Timestamp"], unit="s", utc=True).dt.tz_convert(IST)
                    
                    # Cast columns to appropriate float types
                    import numpy as np
                    df["Open"] = df["Open"].astype(np.float32)
                    df["High"] = df["High"].astype(np.float32)
                    df["Low"] = df["Low"].astype(np.float32)
                    df["Close"] = df["Close"].astype(np.float32)
                    df["Volume"] = df["Volume"].astype(np.float32)
                    
                    if interval == "1d":
                        df["Date"] = pd.to_datetime(timestamps.dt.date)
                        df = df.drop(columns=["Timestamp"], errors="ignore")
                    else:
                        df["Datetime"] = timestamps
                        df = df.drop(columns=["Timestamp"], errors="ignore")
                    
                    # ── Save confirmed mapping only after a successful fetch ──────────────
                    if cand_symbol != original_ns and not cand_symbol.endswith("-INDEX"):
                        try:
                            from data_providers.fyers_mapping_utils import save_fyers_mapping
                            save_fyers_mapping(orig_sym, cand_symbol)
                            save_fyers_mapping(symbol.strip().upper(), cand_symbol)
                        except Exception:
                            pass
                        
                    pipeline = val_registry.get_pipeline(DatasetType.PRICE)
                    engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
                    ctx = ValidationContext(provider="Fyers", period=period, interval=interval, range_from=range_from, range_to=range_to, fetch_mode="DELTA" if range_from else "FULL")
                    report = engine.validate(df, ctx)
                    if not report.is_valid:
                        return MarketData(None, "Fyers", report, False, False, "Quality Check Failed")
                    return MarketData(df, "Fyers", report, False, False, None)
                    
                except Exception as e:
                    error_str = str(e)
                    
                    # Record failure for circuit breaker, but ignore expected symbol mismatches or unsupported series errors
                    if "Bad request" in error_str or "error" in error_str.lower():
                        if not any(k in error_str.lower() for k in ("invalid symbol", "invalid input", "additional permission required", "403")):
                            _fyers_circuit_breaker.record_failure()

                    if "Could not authenticate the user" in error_str:
                        return None
                        
                    # Do not retry for bad symbols; move immediately to the next candidate
                    if "Invalid symbol provided" in error_str:
                        tried_suffixes.add(cand_symbol)
                        logger.info(f"🔄 Fyers candidate {cand_symbol} is invalid. Trying next candidate...")
                        break  # Break inner network retries, try next candidate in outer loop
                        
                    if "Invalid input" in error_str:
                        logger.warning(f"⚠️ Skipping {cand_symbol} — non-retryable Fyers error: {e}")
                        break
                        
                    # Exponential backoff for network rate limits or server errors
                    import random
                    if "request limit reached" in error_str:
                        backoff_time = 20.0 + random.uniform(0.0, 10.0)
                        logger.info(f"⏳ Rate limited by Fyers for {cand_symbol}. Backing off for {backoff_time:.1f}s... (Attempt {attempt+1}/{retries})")
                        time.sleep(backoff_time)
                    else:
                        logger.warning(f"⚠️ Attempt {attempt+1}/{retries} failed for {cand_symbol}: {e}")
                        time.sleep((2 ** attempt) * 1.5 + random.uniform(0.5, 1.5))

        logger.warning(f"⚠️ All Fyers series candidates failed for {orig_sym} ({candidates}). Temporarily caching in 24h negative cache to avoid redundant retries today.")
        try:
            from data_providers.fyers_mapping_utils import mark_fyers_invalid
            mark_fyers_invalid(orig_sym)
            from database import insert_notification
            insert_notification(
                "warning",
                f"⚠️ FYERS SYMBOL INVALID: {orig_sym}",
                f"Symbol '{orig_sym}' failed across all Fyers candidate series ({candidates}). Marked INVALID for 24h to skip on subsequent runs."
            )
        except Exception:
            pass
        return None

    def get_batch_ohlcv(self, symbols: list[str], interval: str, period: str, retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> dict[str, MarketData]:
        """Fetch OHLCV data for multiple symbols concurrently using ThreadPoolExecutor."""

        # Check if Fyers circuit breaker is open (too many failures)
        if not _fyers_circuit_breaker.is_available():
            logger.warning(f"🚫 Fyers Circuit Breaker is OPEN. Skipping Fyers batch fetch for {len(symbols)} symbols.")
            return {}

        prefix = f"[{caller}] " if caller else ""
        if range_from and range_to:
            logger.info(f"{prefix}📥 Fetching incremental batch OHLCV for {len(symbols)} symbols ({interval}, {range_from} to {range_to}) via Fyers API...")
        else:
            logger.info(f"{prefix}📥 Fetching batch OHLCV for {len(symbols)} symbols ({interval}, {period}) via Fyers API...")
            
        normalized_map = {}
        for s in symbols:
            # [VERSION: NULL_POINTER_FIX_v1.0] Prevent None leaks from batch dataframe extraction
            if not s:
                continue
            orig = s.strip() if isinstance(s, str) else str(s)
            ns_sym = self._normalize_symbol(orig)
            if not ns_sym:
                continue
            if ns_sym not in normalized_map:
                normalized_map[ns_sym] = []
            normalized_map[ns_sym].append(orig)
            
        ns_symbols = list(normalized_map.keys())
        results = {}
        
        # [VERSION: DATA_FETCH_ACCELERATION_v1.0] Scale max workers to 6 for faster batch fetching
        max_workers = min(6, len(ns_symbols) if ns_symbols else 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ns = {
                executor.submit(self.get_ohlcv, normalized_map[ns_sym][0], interval, period, retries, range_from, range_to): ns_sym
                for ns_sym in ns_symbols
            }
            
            completed = 0
            total = len(future_to_ns)
            try:
                # Dynamic timeout based on list size, max 1800s (30 mins)
                # 1.5 req/sec = ~666 ms per request. Add generous buffer for backoffs.
                calc_timeout = min(1800, max(300, len(ns_symbols) * 2))
                for future in concurrent.futures.as_completed(future_to_ns, timeout=calc_timeout):
                    ns_sym = future_to_ns[future]
                    completed += 1
                    if completed % 50 == 0 or completed == total:
                        logger.info(f"{prefix}⏳ Progress: Fetched {completed}/{total} symbols from Fyers...")
                        
                    try:
                        df = future.result()
                        # Map dataframe to all requested symbols mapping to this normalized symbol
                        for orig_sym in normalized_map[ns_sym]:
                            results[orig_sym] = df
                    except Exception as e:
                        logger.exception(f"Error fetching batch OHLCV for {ns_sym}")
                        for orig_sym in normalized_map[ns_sym]:
                            results[orig_sym] = MarketData(None, "Fyers", None, False, False, "Exception")
            except concurrent.futures.TimeoutError:
                logger.error(f"Fyers batch fetch timed out after {calc_timeout}s. Cancelling remaining fetches.")
                pass
                        
        for s in symbols:
            if s not in results:
                results[s] = MarketData(None, "Fyers", None, False, False, "Missing")
                        
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
                
                if str(code) in ["494", "-401", "401", "-16", "-15"]:
                    logger.warning(f"Fyers quotes auth warning for {ns_symbol} (code {code}): {error_msg}")
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
                logger.error("Fyers API authentication expired or invalid.")
                from core_exceptions import ProviderError
                raise ProviderError("Fyers Authentication Required")

            logger.exception(f"Failed to fetch quote for symbol {symbol}")
            try:
                from data_fetch_status import mark_failure
                mark_failure('fyers', f"Quote fetch exception for {symbol}: {str(e)}")
            except Exception:
                pass
            return {}
