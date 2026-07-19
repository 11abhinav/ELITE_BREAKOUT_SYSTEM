# =====================================================================================
# app/delivery_data.py
#
# WHAT THIS FILE DOES:
#   Fetches NSE end-of-day delivery volume data from the NSE bhavcopy archive.
#   Uses ScraperAPI (via pledge_scraper infrastructure) to permanently bypass Akamai WAF.
#   Implements the 7-Stage V8 Data Quality Framework for source-agnostic validation.
# =====================================================================================

import logging
import requests
import pandas as pd
import time
import io
import hashlib
import threading
from datetime import date, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from data_fetch_status import mark_success, mark_failure

logger = logging.getLogger(__name__)

BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/products/content/"
    "sec_bhavdata_full_{date_str}.csv"
)

# ScraperAPI adds significant latency since it proxies through residential IPs.
FETCH_TIMEOUT = 60
MAX_RETRIES = 3

_delivery_cache = None
_delivery_cache_date = None
_delivery_cache_lock = threading.Lock()
_processed_sha256 = set()

class V8DeliveryValidator:
    """
    7-Stage Data Quality Framework for V8.
    Validates any incoming Bhavcopy data regardless of the source.
    """
    def __init__(self, expected_date: date):
        self.expected_date = expected_date
        self.expected_date_str = expected_date.strftime("%d-%b-%Y")
        
    def _log_reject(self, reason: str, **kwargs):
        msg = f"DATA_VALIDATOR | Reason: {reason}"
        for k, v in kwargs.items():
            msg += f" | {k}: {v}"
        logger.warning(msg)
        
    def _compute_sha256(self, raw_data: str) -> str:
        return hashlib.sha256(raw_data.encode('utf-8')).hexdigest()
        
    def run_pipeline(self, raw_data: str) -> dict[str, float]:
        # Stage 1: SHA256 (Prevent processing the exact same corrupted/stale file twice)
        file_hash = self._compute_sha256(raw_data)
        if file_hash in _processed_sha256:
            self._log_reject("DUPLICATE_FILE_HASH", Hash=file_hash[:8])
            return None
            
        try:
            df = pd.read_csv(io.StringIO(raw_data))
            df.columns = [c.strip().upper() for c in df.columns]
        except Exception as e:
            self._log_reject("PARSE_ERROR", Error=str(e))
            return None
            
        # Stage 2: Schema Validation
        required_cols = {"SYMBOL", "SERIES", "DATE1", "DELIV_PER"}
        missing = required_cols - set(df.columns)
        if missing:
            self._log_reject("INVALID_SCHEMA", Missing=missing)
            return None
            
        # Stage 3: Type Validation
        try:
            df["DELIV_PER"] = pd.to_numeric(df["DELIV_PER"], errors="coerce")
        except Exception as e:
            self._log_reject("TYPE_VALIDATION_FAILED", Column="DELIV_PER")
            return None
            
        # Stage 4: Content Validation
        actual_date = str(df["DATE1"].iloc[0]).strip()
        if actual_date != self.expected_date_str:
            self._log_reject("DATE_MISMATCH", Expected=self.expected_date_str, Actual=actual_date)
            return None
            
        df["SERIES"] = df["SERIES"].astype(str).str.strip()
        df = df[df['SERIES'].isin(['EQ', 'BE', 'SM', 'BZ'])].copy()
        
        if (df["DELIV_PER"] < 0).any() or (df["DELIV_PER"] > 100).any():
            invalid_count = len(df[(df["DELIV_PER"] < 0) | (df["DELIV_PER"] > 100)])
            self._log_reject("INVALID_DELIVERY_PERCENT", Count=invalid_count)
            return None
            
        empty_symbols = df["SYMBOL"].isna() | (df["SYMBOL"].astype(str).str.strip() == "")
        if empty_symbols.any():
            self._log_reject("EMPTY_SYMBOLS", Count=empty_symbols.sum())
            return None
            
        df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
        
        if df.duplicated(subset=["SYMBOL"]).any():
            dup_count = df.duplicated(subset=["SYMBOL"]).sum()
            self._log_reject("DUPLICATE_SYMBOLS", Count=dup_count)
            return None
            
        # Stage 5: Market Sanity Validation
        num_symbols = len(df)
        if num_symbols < 1800 or num_symbols > 4000:
            self._log_reject("SYMBOL_COUNT_OUT_OF_BOUNDS", Expected="1800-4000", Actual=num_symbols)
            return None
            
        missing_deliv_pct = df["DELIV_PER"].isna().mean()
        if missing_deliv_pct > 0.05:
            self._log_reject("TOO_MUCH_MISSING_DATA", Missing_Pct=f"{missing_deliv_pct:.1%}")
            return None
            
        # Stage 6: Historical Comparison
        with _delivery_cache_lock:
            if _delivery_cache is not None:
                prev_count = len(_delivery_cache)
                drift = abs(num_symbols - prev_count) / prev_count
                if drift > 0.10:
                    self._log_reject("HISTORICAL_DRIFT_TOO_HIGH", Prev=prev_count, New=num_symbols, Drift=f"{drift:.1%}")
                    return None

        # Stage 7: Quality Score (implicit ACCEPT)
        _processed_sha256.add(file_hash)
        logger.info(f"✅ V8 Pipeline: Bhavcopy Validated | Date: {self.expected_date_str} | Symbols: {num_symbols}")
        
        return dict(zip(df["SYMBOL"], df["DELIV_PER"].astype(float)))

def _get_robust_session():
    # curl_cffi and impersonation are no longer needed since ScraperAPI handles TLS spoofing natively.
    session = requests.Session()
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def fetch_previous_day_delivery() -> dict[str, float]:
    global _delivery_cache, _delivery_cache_date
    from datetime import datetime as _dt
    today = _dt.now().date()
    
    with _delivery_cache_lock:
        if _delivery_cache is not None and _delivery_cache_date == today:
            logger.info(f"⚡ [CACHE HIT] Returning {len(_delivery_cache)} symbols from memory (already fetched today).")
            return _delivery_cache

        for days_back in range(1, 5):
            candidate = today - timedelta(days=days_back)
            while candidate.weekday() >= 5:
                candidate -= timedelta(days=1)
                
            logger.info(f"🔄 Attempting to fetch Bhavcopy delivery data via ScraperAPI for date: {candidate}")
            result = fetch_delivery_data(candidate)
            if result:
                logger.info(f"📦 Previous-day delivery loaded | Date={candidate}")
                _delivery_cache = result
                _delivery_cache_date = today
                return result
        
        # FINAL FALLBACK: If all fetches fail, return the stale cache if we have one.
        if _delivery_cache is not None:
            logger.warning("⚠️ NSE Delivery fetch failed for all recent days. Using stale cache as final fallback.")
            return _delivery_cache
            
    return {}

def fetch_delivery_data(trading_date: date) -> dict[str, float]:
    from pledge_scraper import get_scraper_api_key, mark_key_exhausted_today
    
    date_str = trading_date.strftime("%d%m%Y")
    target_url = BHAVCOPY_URL.format(date_str=date_str)
    session  = _get_robust_session()

    for attempt in range(1, MAX_RETRIES + 1):
        api_key = get_scraper_api_key()
        if not api_key:
            logger.error("❌ No valid SCRAPERAPI_KEY found. Aborting Bhavcopy fetch.")
            return {}

        payload = {
            'api_key': api_key,
            'url': target_url,
            'render': 'false', # CSVs don't need JS rendering
            'country_code': 'in' # Prioritize Indian IPs for NSE (Optional, but recommended)
        }

        try:
            logger.info(f"🔄 [Attempt {attempt}] Requesting Bhavcopy CSV via ScraperAPI: {target_url}")
            
            # Request through ScraperAPI rather than hitting NSE directly
            response = session.get('https://api.scraperapi.com/', params=payload, timeout=FETCH_TIMEOUT)
            
            logger.info(f"   -> 📥 CSV Response status: {response.status_code}")
            
            if response.status_code in [403, 429]:
                logger.warning(f"⚠️ ScraperAPI key {api_key[:5]}... exhausted or rate limited (HTTP {response.status_code}).")
                mark_key_exhausted_today(api_key)
                time.sleep(2)
                continue
            
            # ScraperAPI returns the target's status code. So if NSE returns 404, ScraperAPI returns 404.
            if response.status_code == 404:
                logger.info(f"   -> ❌ Got 404 Not Found. Assuming file does not exist yet for {date_str}.")
                try:
                    mark_failure('nse_bhavcopy', '404')
                except Exception: pass
                return {}
                
            if response.status_code == 200:
                raw_data = response.text
                if len(raw_data) < 1000:
                    logger.warning("⚠️ Received suspiciously small response. Retrying...")
                    time.sleep(1)
                    continue
                
                validator = V8DeliveryValidator(expected_date=trading_date)
                valid_dict = validator.run_pipeline(raw_data)
                
                if valid_dict is not None:
                    try:
                        mark_success('nse_bhavcopy')
                    except Exception: pass
                    return valid_dict
                
        except Exception as e:
            logger.warning(f"⚠️ Bhavcopy attempt {attempt} failed via ScraperAPI for {date_str}: {e}")
            try:
                mark_failure('nse_bhavcopy', str(e))
            except Exception:
                pass
                
        if attempt < MAX_RETRIES:
            time.sleep(2)
        else:
            try:
                from push_service import send_push_to_all
                send_push_to_all("⚠️ NSE API ERROR", f"Delivery (Bhavcopy) fetch failed for {date_str} via ScraperAPI")
            except Exception: pass
            
    return {}
