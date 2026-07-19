# =====================================================================================
# app/delivery_data.py
#
# WHAT THIS FILE DOES:
#   Fetches NSE end-of-day delivery volume data from the NSE bhavcopy archive.
#   Uses ScraperAPI (via pledge_scraper infrastructure) to permanently bypass Akamai WAF.
# =====================================================================================

import logging
import requests
import pandas as pd
import time
import io
import threading
from datetime import date, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from data_fetch_status import mark_success, mark_failure

logger = logging.getLogger(__name__)

BHAVCOPY_URL = (
    "https://archives.nseindia.com/products/content/"
    "sec_bhavdata_full_{date_str}.csv"
)

# ScraperAPI adds significant latency since it proxies through residential IPs.
FETCH_TIMEOUT = 60
MAX_RETRIES = 3

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

_delivery_cache = None
_delivery_cache_date = None
_delivery_cache_lock = threading.Lock()

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
                
                # sec_bhavdata_full format: standard CSV with headers
                df = pd.read_csv(io.StringIO(raw_data))
                df.columns = [c.strip().upper() for c in df.columns]
                
                # Validate columns
                required_cols = {"SYMBOL", "SERIES", "DATE1", "DELIV_PER"}
                if not required_cols.issubset(set(df.columns)):
                    logger.warning(f"⚠️ Bhavcopy structure invalid. Columns found: {df.columns.tolist()}")
                    return {}
                
                # 0. Internal Date Integrity Check
                expected_date_str = trading_date.strftime("%d-%b-%Y") # e.g., 17-Jul-2026
                actual_date = str(df["DATE1"].iloc[0]).strip()
                if actual_date != expected_date_str:
                    logger.warning(f"⚠️ Bhavcopy rejected: File contents are for {actual_date}, but we expected {expected_date_str} (NSE publishing error)")
                    return {}
                
                # Filter strictly to Equities
                df = df[df['SERIES'] == 'EQ'].copy()
                
                df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
                df["DELIV_PER"] = pd.to_numeric(df["DELIV_PER"], errors="coerce")
                
                num_symbols = len(df)
                
                # 1. Hard Limits
                if num_symbols < 2000 or num_symbols > 4000:
                    logger.warning(f"⚠️ Bhavcopy rejected: abnormal symbol count ({num_symbols})")
                    return {}
                
                # 2. Statistical Anomaly Check (Max 10% drift from previous valid cache)
                with _delivery_cache_lock:
                    if _delivery_cache is not None:
                        prev_count = len(_delivery_cache)
                        drift = abs(num_symbols - prev_count) / prev_count
                        if drift > 0.10:
                            logger.warning(f"⚠️ Bhavcopy rejected: Symbol count drifted {drift:.1%} from previous day (Prev: {prev_count}, New: {num_symbols})")
                            return {}

                # 3. Duplicate Check
                if df["SYMBOL"].duplicated().any():
                    logger.warning("⚠️ Bhavcopy rejected: duplicate symbols found in EQ series")
                    return {}
                    
                # 4. Missing Data Check
                missing_deliv_pct = df["DELIV_PER"].isna().mean()
                if missing_deliv_pct > 0.05:
                    logger.warning(f"⚠️ Bhavcopy rejected: too much missing delivery data ({missing_deliv_pct:.1%})")
                    return {}
                    
                logger.info(f"✅ Bhavcopy Validated | Date: {date_str} | Symbols: {num_symbols} | Missing: {missing_deliv_pct:.2%}")
                
                try:
                    mark_success('nse_bhavcopy')
                except Exception:
                    pass
                    
                return dict(zip(df["SYMBOL"], df["DELIV_PER"].astype(float)))
                
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
