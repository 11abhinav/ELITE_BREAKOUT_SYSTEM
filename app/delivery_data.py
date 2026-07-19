# =====================================================================================
# app/delivery_data.py
#
# WHAT THIS FILE DOES:
#   Fetches NSE end-of-day delivery volume data from the NSE bhavcopy archive.
#   Uses curl_cffi and a cookie warmup to bypass Akamai WAF.
# =====================================================================================

import logging
import requests
import pandas as pd
import time
import random
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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
]

FETCH_TIMEOUT = 30
MAX_RETRIES = 3

def _get_robust_session():
    try:
        from curl_cffi import requests as cffi_requests
        # [TESTING: Upgraded impersonate from chrome120 to chrome124 to test WAF block]
        session = cffi_requests.Session(impersonate="chrome124")
        logger.info("🔧 _get_robust_session: Using curl_cffi with chrome124 impersonation")
        return session
    except ImportError:
        logger.warning("⚠️ curl_cffi not installed. Falling back to standard requests.")
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
            return _delivery_cache

        for days_back in range(1, 5):
            candidate = today - timedelta(days=days_back)
            while candidate.weekday() >= 5:
                candidate -= timedelta(days=1)
                
            logger.info(f"🔄 Attempting to fetch Bhavcopy delivery data for date: {candidate}")
            result = fetch_delivery_data(candidate)
            if result:
                logger.info(f"📦 Previous-day delivery loaded | Date={candidate}")
                _delivery_cache = result
                _delivery_cache_date = today
                return result
        
        # FINAL FALLBACK: If all fetches fail (e.g., NSE is completely down), 
        # return the stale cache if we have one, rather than failing the whole build.
        if _delivery_cache is not None:
            logger.warning("⚠️ NSE Delivery fetch failed for all recent days. Using stale cache as final fallback.")
            return _delivery_cache
            
    return {}

def fetch_delivery_data(trading_date: date) -> dict[str, float]:
    date_str = trading_date.strftime("%d%m%Y")
    url      = BHAVCOPY_URL.format(date_str=date_str)
    session  = _get_robust_session()

    for attempt in range(1, MAX_RETRIES + 1):
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": "https://www.nseindia.com/",
        }
        try:
            logger.info(f"🔄 [Attempt {attempt}] Starting fetch for {date_str}...")
            
            # First, hit the base domain to establish the session cookie
            try:
                logger.info(f"   -> 🍪 Hitting https://www.nseindia.com/ for cookie warmup...")
                warmup_resp = session.get("https://www.nseindia.com/", headers=headers, timeout=FETCH_TIMEOUT)
                logger.info(f"   -> 🍪 Warmup response status: {warmup_resp.status_code}")
                time.sleep(2.5) # Delay to mimic human behavior and avoid WAF ban
            except Exception as e:
                logger.warning(f"   -> ⚠️ Initial NSE cookie fetch failed: {e}")
                time.sleep(2.5)
                
            logger.info(f"   -> 📥 Requesting Bhavcopy CSV: {url}")
            response = session.get(url, headers=headers, timeout=FETCH_TIMEOUT)
            logger.info(f"   -> 📥 CSV Response status: {response.status_code}")
            
            if response.status_code == 404:
                logger.info(f"   -> ❌ Got 404 Not Found. Assuming file does not exist yet (or is Akamai fake 404).")
                try:
                    mark_failure('nse_bhavcopy', '404')
                except Exception:
                    logger.exception('Failed to report nse_bhavcopy 404')
                return {} # 404 means the file doesn't exist yet (or holiday)
                
            if response.status_code == 200:
                raw_data = response.text
                if len(raw_data) < 1000:
                    time.sleep(1)
                    continue
                
                # sec_bhavdata_full format: standard CSV with headers
                df = pd.read_csv(io.StringIO(raw_data))
                df.columns = [c.strip().upper() for c in df.columns]
                
                # Validate columns
                required_cols = {"SYMBOL", "SERIES", "DELIV_PER"}
                if not required_cols.issubset(set(df.columns)):
                    logger.warning(f"⚠️ Bhavcopy structure invalid. Columns found: {df.columns.tolist()}")
                    return {}
                
                # Filter strictly to Equities
                df = df[df['SERIES'] == 'EQ'].copy()
                
                df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
                df["DELIV_PER"] = pd.to_numeric(df["DELIV_PER"], errors="coerce")
                
                # --- V8 Data Quality Guards ---
                
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
            logger.warning(f"⚠️ Bhavcopy attempt {attempt} failed for {date_str}: {e}")
            try:
                mark_failure('nse_bhavcopy', e)
            except Exception:
                pass
                
        if attempt < MAX_RETRIES:
            time.sleep(1.5)
        else:
            try:
                from push_service import send_push_to_all
                send_push_to_all("⚠️ NSE API ERROR", f"Delivery (Bhavcopy) fetch failed for {date_str}")
            except Exception: pass
            
    return {}
