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

from data_registry import registry
_delivery_cache_lock = threading.RLock()

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
    from datetime import datetime as _dt
    today = _dt.now().date()
    
    with _delivery_cache_lock:
        cached = registry.get("bhavcopy_delivery")
        if cached is not None and cached.get("_date") == today:
            logger.info(f"⚡ [CACHE HIT] Returning {len(cached) - 1} symbols from registry (already fetched today).")
            # Remove _date key before returning
            return {k: v for k, v in cached.items() if k != "_date"}

        for days_back in range(1, 5):
            candidate = today - timedelta(days=days_back)
            while candidate.weekday() >= 5:
                candidate -= timedelta(days=1)
                
            logger.info(f"🔄 Attempting to fetch Bhavcopy delivery data (Crawlora primary) for date: {candidate}")
            result = fetch_delivery_data(candidate)
            if result:
                logger.info(f"📦 Previous-day delivery loaded | Date={candidate}")
                result["_date"] = today
                registry.put("bhavcopy_delivery", result)
                return {k: v for k, v in result.items() if k != "_date"}
        
        # FINAL FALLBACK: If all fetches fail, return the stale cache if we have one.
        if cached is not None:
            logger.warning("⚠️ NSE Delivery fetch failed for all recent days. Using stale registry cache as final fallback.")
            return {k: v for k, v in cached.items() if k != "_date"}
            
    return {}


def fetch_latest_available_delivery_data(today_ist_date: date) -> tuple[dict[str, float], date]:
    """
    Attempts to fetch delivery data starting from today_ist_date down through previous trading days.
    Returns (delivery_map, resolved_date).
    """
    for days_back in range(0, 5):
        candidate = today_ist_date - timedelta(days=days_back)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        res = fetch_delivery_data(candidate, skip_db_save=(days_back > 0))
        if res:
            return res, candidate
    return {}, today_ist_date


def fetch_delivery_data(trading_date: date, skip_db_save: bool = False) -> dict[str, float]:
    from pledge_scraper import get_scraper_api_key, mark_key_exhausted_today
    from database import get_bhavcopy_cache, save_bhavcopy_cache, get_latest_bhavcopy_cache
    from datetime import datetime as _dt, time as dt_time
    
    # 0. Check in-memory DatasetRegistry first (fastest)
    registry_key = f"bhavcopy_delivery_{trading_date.isoformat()}"
    
    # Dynamically register the specific date key if not exists
    if not registry.get_entry(registry_key):
        from data_registry import DatasetEntry, StorageTier
        registry.register_dataset(DatasetEntry(
            id=registry_key, owner="DeliveryDataManager", 
            tier=StorageTier.EPHEMERAL, cadence=86400, preferred_provider="nse"
        ))
        
    cached_mem = registry.get(registry_key)
    if cached_mem is not None:
        return cached_mem
    
    # 1. Check database cache next (Outside lock for speed)
    cached_data = get_bhavcopy_cache(trading_date)
    if cached_data:
        logger.info(f"⚡ [DB CACHE HIT] Returning {len(cached_data)} symbols from DB for date: {trading_date}")
        registry.put(registry_key, cached_data)
        return cached_data

    # [BHAVCOPY_SAVER_v1.0] If requested date is in future or today before 18:00 IST release window,
    # do NOT waste Crawlora/ScraperAPI keys on an unreleased file. Return latest available DB Bhavcopy!
    now_ist = _dt.now(__import__('zoneinfo').ZoneInfo("Asia/Kolkata"))
    today_ist = now_ist.date()
    if trading_date > today_ist or (trading_date == today_ist and now_ist.time() < dt_time(18, 0)):
        logger.info(f"ℹ️ [BHAVCOPY] Date {trading_date} is before 18:00 IST release. Falling back to latest available DB Bhavcopy without wasting API keys.")
        latest = get_latest_bhavcopy_cache()
        if latest:
            registry.put(registry_key, latest)
            return latest
        return {}

    with _delivery_cache_lock:
        # Double-check inside lock to prevent race conditions when multiple scanners start in parallel
        cached_data = get_bhavcopy_cache(trading_date)
        if cached_data:
            logger.info(f"⚡ [DB CACHE HIT] Returning {len(cached_data)} symbols from DB for date: {trading_date} (after waiting for lock)")
            registry.put(registry_key, cached_data)
            return cached_data

        date_str = trading_date.strftime("%d%m%Y")
        target_url = BHAVCOPY_URL.format(date_str=date_str)
    with _get_robust_session() as session:

        for attempt in range(1, MAX_RETRIES + 1):
            from pledge_scraper import get_crawlora_api_key, mark_crawlora_key_exhausted_today, get_scraper_api_key, mark_key_exhausted_today
            crawlora_key = get_crawlora_api_key()
            scraper_key = get_scraper_api_key()

            if not crawlora_key and not scraper_key:
                logger.warning("⚠️ No valid Crawlora or SCRAPERAPI_KEY found. Falling back to latest available DB Bhavcopy.")
                latest = get_latest_bhavcopy_cache()
                if latest:
                    registry.put(registry_key, latest)
                    return latest
                return {}

            response = None
            last_err_msg = None
            
            # 1. Try Crawlora First
            if crawlora_key:
                try:
                    c_payload = {'api_key': crawlora_key, 'url': target_url}
                    logger.info(f"🔄 [Attempt {attempt}] Requesting Bhavcopy CSV via Crawlora: {target_url}")
                    c_resp = session.get('https://api.crawlora.net/v1/scrape', params=c_payload, timeout=FETCH_TIMEOUT)
                    if c_resp is not None and c_resp.status_code in [401, 403, 429]:
                        logger.warning(f"⚠️ Crawlora key {crawlora_key[:5]}... exhausted or rate limited (HTTP {c_resp.status_code}).")
                        mark_crawlora_key_exhausted_today(crawlora_key)
                    elif c_resp is not None and c_resp.status_code == 404:
                        logger.info(f"ℹ️ Bhavcopy {date_str} returned 404. Falling back to latest available DB Bhavcopy.")
                        latest = get_latest_bhavcopy_cache()
                        if latest:
                            registry.put(registry_key, latest)
                            return latest
                        return {}
                    elif c_resp is not None and c_resp.status_code == 200:
                        response = c_resp
                except Exception as crawlora_err:
                    last_err_msg = str(crawlora_err)
                    logger.debug(f"Crawlora Bhavcopy fetch failed: {crawlora_err}")
            else:
                if attempt == 1:
                    logger.info("ℹ️ CRAWLORA_API_KEY is not set or empty in environment. Falling back to ScraperAPI.")

            # 2. Fall back to ScraperAPI if Crawlora is missing or failed
            if response is None and scraper_key:
                payload = {
                    'api_key': scraper_key,
                    'url': target_url,
                    'render': 'false',
                    'country_code': 'in'
                }
                try:
                    logger.info(f"🔄 [Attempt {attempt}] Requesting Bhavcopy CSV via ScraperAPI: {target_url}")
                    s_resp = session.get('https://api.scraperapi.com/', params=payload, timeout=FETCH_TIMEOUT)
                    if s_resp is not None and s_resp.status_code in [401, 403, 429]:
                        logger.warning(f"⚠️ ScraperAPI key {scraper_key[:5]}... exhausted or rate limited (HTTP {s_resp.status_code}).")
                        mark_key_exhausted_today(scraper_key)
                    elif s_resp is not None and s_resp.status_code == 404:
                        logger.info(f"ℹ️ Bhavcopy {date_str} returned 404. Falling back to latest available DB Bhavcopy.")
                        latest = get_latest_bhavcopy_cache()
                        if latest:
                            registry.put(registry_key, latest)
                            return latest
                        return {}
                    else:
                        response = s_resp
                except Exception as scraper_err:
                    last_err_msg = str(scraper_err)
                    logger.warning(f"ScraperAPI Bhavcopy fetch failed: {scraper_err}")

            if response is None:
                logger.warning(f"⚠️ Attempt {attempt} failed via both Crawlora and ScraperAPI. Retrying...")
                if attempt == MAX_RETRIES and last_err_msg:
                    try:
                        mark_failure('nse_bhavcopy', last_err_msg)
                    except Exception: pass
                time.sleep(2)
                continue

            logger.info(f"   -> 📥 CSV Response status: {response.status_code}")
            
            # 404 Not Found means Bhavcopy file does not exist yet for this date
            if response.status_code == 404:
                logger.info(f"   -> ❌ Got 404 Not Found. Assuming file does not exist yet for {date_str}. Falling back to latest available DB Bhavcopy.")
                try:
                    mark_failure('nse_bhavcopy', '404')
                except Exception: pass
                latest = get_latest_bhavcopy_cache()
                if latest:
                    registry.put(registry_key, latest)
                    return latest
                return {}
                
            if response.status_code == 200:
                logger.info(f"   -> ✅ Successfully downloaded Bhavcopy data from NSE.")
                raw_data = response.text
                if len(raw_data) < 1000:
                    logger.warning("⚠️ Received suspiciously small response. Retrying...")
                    time.sleep(1)
                    continue
                
                if "<html" in raw_data.lower() or "<body" in raw_data.lower() or "<!doctype" in raw_data.lower():
                    logger.warning("⚠️ Scraper returned an HTML block page instead of CSV. Retrying...")
                    time.sleep(2)
                    continue
                
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
            
                # Drop NaN values to prevent PostgreSQL invalid JSON token errors
                df = df.dropna(subset=["DELIV_PER"])
            
                # [VERSION: BHAVCOPY_SERIES_PRIORITY_v1.0] Prioritize EQ > BE > SM > BZ when a symbol appears across multiple series
                series_order = {'EQ': 0, 'BE': 1, 'SM': 2, 'BZ': 3}
                df['_series_rank'] = df['SERIES'].map(lambda s: series_order.get(s, 99))
                df = df.sort_values(by=['_series_rank']).drop_duplicates(subset=['SYMBOL'], keep='first')
                df.drop(columns=['_series_rank'], inplace=True, errors='ignore')

                final_dict = df.set_index("SYMBOL")["DELIV_PER"].to_dict()
            
                # 2. Save to database cache
                if not skip_db_save:
                    save_bhavcopy_cache(trading_date, final_dict)
                else:
                    logger.info(f"⏭️ Skipping DB save for {trading_date} (fallback fetching mode).")
            
                if final_dict:
                    try:
                        mark_success('nse_bhavcopy')
                    except Exception: pass
                    registry.put(registry_key, final_dict)
                    return final_dict
                
            if attempt < MAX_RETRIES:
                time.sleep(2)
            else:
                try:
                    from push_service import send_push_to_all
                    send_push_to_all("⚠️ NSE API ERROR", f"Delivery (Bhavcopy) fetch failed for {date_str} via Crawlora & ScraperAPI")
                except Exception: pass
            
        latest = get_latest_bhavcopy_cache()
        if latest:
            logger.info(f"📦 [BHAVCOPY] All attempts exhausted for {date_str}. Using latest available DB Bhavcopy cache.")
            registry.put(registry_key, latest)
            return latest
        return {}
