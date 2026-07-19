# =====================================================================================
# app/delivery_data.py
#
# WHAT THIS FILE DOES:
#   Fetches NSE end-of-day delivery volume data from the NSE MTO archive.
#   Bypasses Akamai WAF by using the official raw archive endpoint.
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

MTO_URL = (
    "https://nsearchives.nseindia.com/archives/equities/mto/"
    "MTO_{date_str}.DAT"
)

FETCH_TIMEOUT = 30
MAX_RETRIES = 3

def _get_robust_session():
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
    url      = MTO_URL.format(date_str=date_str)
    session  = _get_robust_session()

    for attempt in range(1, MAX_RETRIES + 1):
        headers = {
            "User-Agent": "Mozilla/5.0",
        }
        try:
            response = session.get(url, headers=headers, timeout=FETCH_TIMEOUT)
            if response.status_code == 404:
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
                
                # MTO format: The file is malformed. 
                # Header (row 4) has 6 columns, but data has 7 columns (including SERIES).
                col_names = ['Record Type', 'Sr No', 'SYMBOL', 'SERIES', 'Quantity Traded', 'Deliverable Quantity', 'DELIV_PER']
                df = pd.read_csv(io.StringIO(raw_data), skiprows=4, names=col_names)
                
                # Filter strictly to Equities
                df = df[df['SERIES'] == 'EQ'].copy()
                
                df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
                df["DELIV_PER"] = pd.to_numeric(df["DELIV_PER"], errors="coerce")
                
                # --- V8 Data Quality Guards ---
                
                # 1. Date Integrity Check
                if date_str not in raw_data[:300]:
                    logger.warning(f"⚠️ MTO rejected: File header does not contain expected date {date_str}")
                    return {}
                
                num_symbols = len(df)
                
                # 2. Hard Limits
                if num_symbols < 2000 or num_symbols > 4000:
                    logger.warning(f"⚠️ MTO rejected: abnormal symbol count ({num_symbols})")
                    return {}
                
                # 3. Statistical Anomaly Check (Max 10% drift from previous valid cache)
                with _delivery_cache_lock:
                    if _delivery_cache is not None:
                        prev_count = len(_delivery_cache)
                        drift = abs(num_symbols - prev_count) / prev_count
                        if drift > 0.10:
                            logger.warning(f"⚠️ MTO rejected: Symbol count drifted {drift:.1%} from previous day (Prev: {prev_count}, New: {num_symbols})")
                            return {}

                # 4. Duplicate Check
                if df["SYMBOL"].duplicated().any():
                    logger.warning("⚠️ MTO rejected: duplicate symbols found in EQ series")
                    return {}
                    
                # 5. Missing Data Check
                missing_deliv_pct = df["DELIV_PER"].isna().mean()
                if missing_deliv_pct > 0.05:
                    logger.warning(f"⚠️ MTO rejected: too much missing delivery data ({missing_deliv_pct:.1%})")
                    return {}
                    
                logger.info(f"✅ MTO Validated | Date: {date_str} | Symbols: {num_symbols} | Missing: {missing_deliv_pct:.2%}")
                
                try:
                    mark_success('nse_bhavcopy')
                except Exception:
                    pass
                    
                return dict(zip(df["SYMBOL"], df["DELIV_PER"].astype(float)))
                
        except Exception as e:
            logger.warning(f"⚠️ MTO attempt {attempt} failed for {date_str}: {e}")
            try:
                mark_failure('nse_bhavcopy', e)
            except Exception:
                pass
                
        if attempt < MAX_RETRIES:
            time.sleep(1.5)
        else:
            try:
                from push_service import send_push_to_all
                send_push_to_all("⚠️ NSE API ERROR", f"Delivery (MTO) fetch failed for {date_str}")
            except Exception: pass
            
    return {}
