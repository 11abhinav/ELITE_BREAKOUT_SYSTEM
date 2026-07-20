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
from validation import ValidationEngine, ValidationContext, registry as val_registry, DatasetType
from validation.result import ValidationStatus
from validation.history import history_recorder

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
_delivery_cache_lock = threading.RLock()
_processed_sha256 = set()

def _compute_sha256(raw_data: str) -> str:
    return hashlib.sha256(raw_data.encode('utf-8')).hexdigest()

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
            
            if response.status_code in [401, 403, 429]:
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
                    
                if "<html" in raw_data.lower() or "<body" in raw_data.lower() or "<!doctype" in raw_data.lower():
                    logger.warning("⚠️ ScraperAPI returned an HTML block page instead of CSV. Retrying...")
                    time.sleep(2)
                    continue
                
                file_hash = _compute_sha256(raw_data)
                if file_hash in _processed_sha256:
                    logger.warning(f"⚠️ Duplicate file hash {file_hash[:8]} detected. Skipping.")
                    return {}
                    
                try:
                    df = pd.read_csv(io.StringIO(raw_data))
                    df.columns = [c.strip().upper() for c in df.columns]
                    
                    # NSE introduced a new Bhavcopy schema format. Map it back to standard legacy headers.
                    rename_map = {
                        "DATE1": "TIMESTAMP",
                        "PREV_CLOSE": "PREVCLOSE",
                        "OPEN_PRICE": "OPEN",
                        "HIGH_PRICE": "HIGH",
                        "LOW_PRICE": "LOW",
                        "LAST_PRICE": "LAST",
                        "CLOSE_PRICE": "CLOSE",
                        "TTL_TRD_QNTY": "TOTTRDQTY",
                        "TURNOVER_LACS": "TOTTRDVAL",
                        "NO_OF_TRADES": "TOTALTRADES"
                    }
                    df.rename(columns=rename_map, inplace=True)
                except Exception as e:
                    logger.warning(f"⚠️ Parse Error: {e}")
                    return {}
                
                pipeline = val_registry.get_pipeline(DatasetType.BHAVCOPY)
                engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
                ctx = ValidationContext(provider="NSE_BHAVCOPY")
                
                validated_dataset = engine.process(df, context=ctx)
                
                # Record to history
                history_recorder.record_single(DatasetType.BHAVCOPY, validated_dataset)
                
                if validated_dataset.status == ValidationStatus.INVALID:
                    logger.error(f"❌ Bhavcopy Validation Failed: {validated_dataset.result.critical_failures}")
                    return {}
                    
                if validated_dataset.status == ValidationStatus.DEGRADED:
                    logger.warning(f"⚠️ Bhavcopy Validation Degraded. Score: {validated_dataset.score}. Warnings: {validated_dataset.result.warnings}")
                elif validated_dataset.result.has_warnings:
                    logger.warning(f"⚠️ Bhavcopy Validation Warnings: {validated_dataset.result.warnings}")
                    
                _processed_sha256.add(file_hash)
                logger.info(f"✅ ValidationEngine: Bhavcopy Validated | Date: {date_str} | Symbols: {len(df)}")
                
                # Consumer-specific extraction from the validated cross-sectional snapshot
                if "DELIV_PER" not in df.columns:
                    logger.error("❌ Bhavcopy is valid, but missing DELIV_PER column required for delivery extraction.")
                    return {}
                    
                df["DELIV_PER"] = pd.to_numeric(df["DELIV_PER"], errors="coerce")
                df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
                
                # Filter for valid series
                df["SERIES"] = df["SERIES"].astype(str).str.strip()
                df = df[df['SERIES'].isin(['EQ', 'BE', 'SM', 'BZ'])].copy()
                
                valid_dict = dict(zip(df["SYMBOL"], df["DELIV_PER"].astype(float)))
                
                if valid_dict:
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
