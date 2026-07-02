import os
import time
import logging
import requests
import re
from bs4 import BeautifulSoup
from functools import lru_cache
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json
from database import get_connection, upsert_scanner_health, init_db
from data_fetch_status import mark_success, mark_failure
from config import WATCHLIST_PATH, DATA_DIR
from pledge_scraper import get_scraper_api_key, mark_key_exhausted_today

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

IST_ZONE = ZoneInfo("Asia/Kolkata")

CONFIG_PATH = os.path.join(DATA_DIR, "pledge_config.json")

def get_worker_mode() -> str:
    """Returns 'auto', 'manual_start', or 'manual_stop'."""
    if not os.path.exists(CONFIG_PATH):
        return 'auto'
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
            return data.get("mode", "auto")
    except Exception:
        return 'auto'

def set_worker_mode(mode: str):
    """Sets the worker mode."""
    if mode not in ['auto', 'manual_start', 'manual_stop']:
        return
    
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump({"mode": mode}, f)
    except Exception as e:
        logger.error(f"Failed to set worker mode: {e}")

def sleep_with_mode_check(seconds: int):
    """Sleep for X seconds, but wake up immediately if mode changes to manual_start."""
    end_time = time.time() + seconds
    while time.time() < end_time:
        if get_worker_mode() == 'manual_start':
            return
        time.sleep(5)

def discover_trendlyne_url(symbol: str) -> str:
    """Try to find the correct Trendlyne URL dynamically."""
    clean_symbol = symbol.replace('.NS', '')
    
    # Hardcoded fallback list
    fallbacks = {
        'HINDCOPPER': 'https://trendlyne.com/equity/551/HINDCOPPER/hindustan-copper-ltd/',
    }
    if clean_symbol in fallbacks:
        return fallbacks[clean_symbol]
        
    fast_url = f"https://trendlyne.com/stock/{clean_symbol}/"
    
    api_key = get_scraper_api_key()
    if not api_key:
        return fast_url

    # 1. Attempt fast HEAD request
    payload = {'api_key': api_key, 'url': fast_url, 'render': 'false'}
    try:
        # Note: ScraperAPI sometimes ignores HEAD, so we do a quick GET with a short timeout
        res = requests.get('https://api.scraperapi.com/', params=payload, timeout=10)
        if res.status_code == 200:
            return fast_url
    except Exception:
        pass

    # 2. If it 404s, use ScraperAPI to search Google for the proper trendlyne URL
    logger.info(f"🔍 Direct URL failed for {clean_symbol}. Searching Google...")
    search_url = f"https://www.google.com/search?q=site:trendlyne.com/equity/+{clean_symbol}"
    payload = {'api_key': api_key, 'url': search_url, 'render': 'false'}
    try:
        res = requests.get('https://api.scraperapi.com/', params=payload, timeout=30)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            for a in soup.find_all('a', href=True):
                href = a['href']
                # Check if it looks like a trendlyne equity link and contains the symbol
                if "trendlyne.com/equity/" in href and clean_symbol.upper() in href.upper():
                    # Extract from google redirect format
                    actual_url = href.split("q=")[-1].split("&")[0] if "/url?q=" in href else href
                    if actual_url.startswith("https://trendlyne.com"):
                        logger.info(f"✅ Discovered Google URL for {clean_symbol}: {actual_url}")
                        return actual_url
    except Exception as e:
        logger.warning(f"Google search fallback failed for {clean_symbol}: {e}")

    # 3. Fallback to the fast_url so it fails naturally downstream
    return fast_url

def worker_loop():
    logger.info("🚀 Starting Pledge Worker Daemon")
    init_db()
    
    if not os.getenv("SCRAPERAPI_KEY"):
        logger.error("❌ SCRAPERAPI_KEY env var not set. Exiting.")
        return

    while True:
        mode = get_worker_mode()
        now = datetime.now(IST_ZONE)
        
        if mode == 'manual_stop':
            upsert_scanner_health("Pledge Worker", "STOPPED", last_success=now.isoformat(), today_alerts=0, error_msg="Stopped by Admin")
            sleep_with_mode_check(60)
            continue
            
        if mode == 'auto':
            if not (6 <= now.hour < 8):
                upsert_scanner_health("Pledge Worker", "WAITING", last_success=now.isoformat(), today_alerts=0, error_msg="Waiting for 06:00 - 08:00 IST Window")
                sleep_with_mode_check(300)
                continue

        try:
            if not get_scraper_api_key() and not os.getenv("BRIGHTDATA_URL"):
                logger.warning("🚨 All Scraper API keys are exhausted and no BrightData URL provided. Pausing scraping daemon for 1 hour.")
                time.sleep(3600)
                continue
                
            symbols_set = set()
            watchlist_count = 0
            if os.path.exists(WATCHLIST_PATH):
                df = pd.read_parquet(WATCHLIST_PATH)
                if "Stock" in df.columns:
                    watch_symbols = df["Stock"].unique().tolist()
                    symbols_set.update(watch_symbols)
                    watchlist_count = len(watch_symbols)
            
            excluded_count = 0
            excluded_paths = [
                os.path.join(os.path.dirname(WATCHLIST_PATH), 'elite_fundamental_watchlist_excluded.csv'),
                os.path.join(os.path.dirname(WATCHLIST_PATH), 'elite_fundamental_watchlist-excluded.csv'),
                WATCHLIST_PATH.replace(".parquet", "_excluded.csv"),
            ]
            for excluded_path in excluded_paths:
                if os.path.exists(excluded_path):
                    try:
                        ex_df = pd.read_csv(excluded_path)
                        if "Stock" in ex_df.columns:
                            ex_symbols = ex_df["Stock"].dropna().unique().tolist()
                            symbols_set.update(ex_symbols)
                            excluded_count = len(ex_symbols)
                            logger.info(f"📋 Loaded {excluded_count} stocks from excluded list: {excluded_path}")
                            break  # Stop after first successful load
                    except Exception as e:
                        logger.warning(f"Could not read excluded csv {excluded_path}: {e}")
            
            if excluded_count == 0:
                logger.warning("⚠️ No excluded stocks loaded — will only process watchlist")

            
            constituents_count = 0
            try:
                from multibagger import fetch_constituents
                idx_symbols = fetch_constituents()
                if idx_symbols:
                    symbols_set.update(idx_symbols)
                    constituents_count = len(idx_symbols)
                    logger.info(f"📋 Loaded {constituents_count} stocks from NSE constituents.")
            except Exception as e:
                logger.warning(f"Could not fetch NSE constituents: {e}")

            if not symbols_set:
                logger.warning(f"No symbols found in watchlist, excluded list, or constituents. Sleeping 60s...")
                time.sleep(60)
                continue
                
            symbols = sorted(list(symbols_set))
            total_watch = len(symbols)
            logger.info(f"📋 Loaded {watchlist_count} (watchlist) + {excluded_count} (excluded) + {constituents_count} (constituents) = {total_watch} unique symbols")
            
            # 2. Check DB for stale pledges (refresh every 28 days = ~1 month)
            # This ensures data freshness while not overloading the API and syncing with Live Scanner
            stale_symbols = []
            with get_connection() as conn:
                with conn.cursor() as cur:
                    for sym in symbols:
                        cur.execute("""
                            SELECT updated_at 
                            FROM promoter_pledge_cache 
                            WHERE symbol = %s 
                              AND updated_at >= NOW() - INTERVAL '28 days'
                        """, (sym,))
                        if not cur.fetchone():
                            stale_symbols.append(sym)
                            
            processed_base = total_watch - len(stale_symbols)

            if not stale_symbols:
                if get_worker_mode() == 'manual_start':
                    logger.info("Manual start completed. Reverting to auto mode.")
                    set_worker_mode('auto')
                    
                sleep_secs = 3600 # Check every hour
                logger.info(f"✅ [PLEDGE WORKER] All promoter pledges are processed for today. Sleeping {sleep_secs}s...")
                upsert_scanner_health("Pledge Worker", "IDLE", last_success=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(), today_alerts=total_watch, error_msg=f"All processed | Total: {total_watch}")
                sleep_with_mode_check(sleep_secs)
                logger.info("⏰ Woke up from daily sleep! Starting fresh scan...")
                continue
                
            logger.info(f"Found {len(stale_symbols)} symbols needing pledge updates (out of {total_watch} total).")
            upsert_scanner_health("Pledge Worker", "OK", today_alerts=processed_base, error_msg=f"Last: Starting... | Total: {total_watch}")
            
            def process_symbol(sym, i_total, is_retry=False):
                """Returns 'FOUND', 'MISSING', '404', or 'ERROR'."""
                target_url = discover_trendlyne_url(sym)
                prefix = "[RETRY]" if is_retry else f"[{i_total}/{len(stale_symbols)}]"
                logger.info(f"{prefix} Scraping pledge for {sym} at {target_url}")
                
                brightdata_url = os.getenv("BRIGHTDATA_URL", "")
                api_key = get_scraper_api_key()
                
                if not brightdata_url and not api_key:
                    logger.error(f"❌ Both BrightData and SCRAPERAPI_KEY exhausted or missing during processing {sym}")
                    return "QUOTA_EXHAUSTED"
                    
                res = None
                brightdata_failed = False
                
                # 1. Try Bright Data first (if available)
                if brightdata_url:
                    proxies = {"http": brightdata_url, "https": brightdata_url}
                    try:
                        # Disable warnings for unverified HTTPS requests when using BrightData proxy
                        import urllib3
                        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                        res = requests.get(target_url, proxies=proxies, verify=False, timeout=45)
                        if res.status_code in (401, 403, 407, 429):
                            logger.warning(f"BrightData proxy returned {res.status_code} for {sym}. May be blocked or quota exceeded.")
                            brightdata_failed = True
                    except Exception as e:
                        logger.error(f"BrightData proxy critically failed for {sym}: {e}")
                        res = None
                        brightdata_failed = True
                
                # 2. Fallback to Scraper API
                if res is None or brightdata_failed or res.status_code not in (200, 404):
                    if api_key:
                        payload = {'api_key': api_key, 'url': target_url, 'render': 'false'}
                        try:
                            res = requests.get('https://api.scraperapi.com/', params=payload, timeout=45)
                            if res is not None and res.status_code in (401, 403, 429):
                                logger.warning(f"❌ HTTP {res.status_code} quota exceeded for ScraperAPI key")
                                mark_failure('scraperapi', f"HTTP {res.status_code} URL={target_url}")
                                mark_key_exhausted_today(api_key)
                                return "ERROR" # Returning ERROR here allows the loop to retry the next key on the next iteration
                        except Exception as e:
                            logger.warning(f"ScraperAPI failed for {sym}: {e}")
                    else:
                        # If Scraper API key is missing/exhausted AND BrightData failed, we must stop the loop!
                        if brightdata_failed or not brightdata_url:
                            logger.error(f"🚨 Both BrightData (failed) and SCRAPERAPI_KEY (exhausted) are unavailable. Stopping.")
                            return "QUOTA_EXHAUSTED"
                            
                if res is None:
                    logger.error(f"❌ No valid response received for {sym}")
                    return "ERROR"
                
                try:
                    if res.status_code == 200:
                        pledge_val = None
                        import html
                        decoded_html = html.unescape(res.text)
                        
                        # Strategy 1: Extract from structured JSON blob `data-companyinsights`
                        json_match = re.search(r"\'parameter\'\:\s*\'Promoter Pledges?\'[^\}]+?\'value\'\:\s*Decimal\(\'(\d+\.?\d*)\'\)", decoded_html)
                        if json_match:
                            pledge_val = float(json_match.group(1))
                        
                        # Strategy 2: Fallback to loose regex on raw HTML
                        if pledge_val is None:
                            match = re.search(r'pledge[^\d]{1,30}?(\d+\.?\d*)\s*%', res.text, re.IGNORECASE)
                            if match:
                                pledge_val = float(match.group(1))
                            else:
                                soup = BeautifulSoup(res.text, 'html.parser')
                                for div in soup.find_all(['div', 'span', 'td', 'p']):
                                    if 'pledge' in div.text.lower() and '%' in div.text:
                                        m = re.search(r'(\d+\.?\d*)\s*%', div.text)
                                        if m:
                                            pledge_val = float(m.group(1))
                                            break
                        if pledge_val is not None:
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("""
                                        INSERT INTO promoter_pledge_cache (symbol, pledge_pct, updated_at)
                                        VALUES (%s, %s, NOW())
                                        ON CONFLICT (symbol) DO UPDATE 
                                        SET pledge_pct = EXCLUDED.pledge_pct, updated_at = NOW()
                                    """, (sym, pledge_val))
                                    conn.commit()
                            logger.info(f"✅ Saved pledge for {sym}: {pledge_val}%")
                        else:
                            logger.warning(f"⚠️ Could not find pledge text on page for {sym}. Saving -1.0 (Not Found)")
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("""
                                        INSERT INTO promoter_pledge_cache (symbol, pledge_pct, updated_at)
                                        VALUES (%s, -1.0, NOW())
                                        ON CONFLICT (symbol) DO UPDATE 
                                        SET pledge_pct = EXCLUDED.pledge_pct, updated_at = NOW()
                                    """, (sym,))
                                    conn.commit()
                        mark_success('scraperapi')
                        return "FOUND" if pledge_val is not None else "MISSING"
                    elif res.status_code == 404:
                        logger.warning(f"❌ 404 Not Found for {sym} at {target_url}")
                        mark_failure('scraperapi', f"404 Not Found: {target_url}")
                        # Cache the 404 temporarily so we don't spam it, retry tomorrow
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO promoter_pledge_cache (symbol, pledge_pct, updated_at)
                                    VALUES (%s, %s, NOW() - INTERVAL '27 days')
                                    ON CONFLICT (symbol) DO UPDATE 
                                    SET pledge_pct = EXCLUDED.pledge_pct, updated_at = NOW() - INTERVAL '27 days'
                                """, (sym, -1.0))
                                conn.commit()
                        return "404"
                    else:
                        logger.warning(f"❌ HTTP {res.status_code} for {sym}")
                        mark_failure('scraperapi', f"HTTP {res.status_code} URL={target_url}")
                        return "ERROR"
                except Exception as e:
                    logger.exception(f"Exception scraping {sym}: {e}")
                    mark_failure('scraperapi', str(e))
                    return "ERROR"

            failed_queue = []
            successful_in_first_pass = 0
            
            found_count = 0
            missing_count = 0
            fail_404_count = 0
            error_count = 0
            total_stale = len(stale_symbols)
            quota_exhausted = False
            
            try:
                public_ip = requests.get("https://api.ipify.org", timeout=10).text
                logger.info(f"🌐 Railway Server Public IP Address: {public_ip} (Whitelist this in Bright Data!)")
            except Exception as e:
                pass
            
            for i, sym in enumerate(stale_symbols):
                status_res = process_symbol(sym, i+1)
                
                if status_res == "QUOTA_EXHAUSTED":
                    logger.warning("🚨 All API keys are exhausted. Stopping scrape loop for now.")
                    quota_exhausted = True
                    break
                    
                if status_res == "FOUND": found_count += 1
                elif status_res == "MISSING": missing_count += 1
                elif status_res == "404": fail_404_count += 1
                else: error_count += 1
                
                if status_res != "ERROR":
                    successful_in_first_pass += 1
                else:
                    failed_queue.append(sym)
                    
                processed = i + 1
                pending = total_stale - processed
                logger.info(f"📊 PROGRESS: {processed}/{total_stale} Processed | {pending} Pending to fetch | {found_count} Found | {missing_count} Missing | {fail_404_count} Not Found (404) | {error_count} Errors")
                    
                # Update health with processed count
                now_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                current_processed = processed_base + successful_in_first_pass
                upsert_scanner_health("Pledge Worker", "OK", last_success=now_str, today_alerts=current_processed, error_msg=f"Last: {sym} | Total: {total_watch}")

            final_error_count = 0
            
            if failed_queue:
                logger.info(f"Retrying {len(failed_queue)} failed symbols...")
                time.sleep(10) # Brief pause before retries
                for sym in failed_queue:
                    status_res = process_symbol(sym, 0, is_retry=True)
                    if status_res == "QUOTA_EXHAUSTED":
                        logger.warning("🚨 All API keys are exhausted during retry. Stopping scrape loop for now.")
                        quota_exhausted = True
                        break
                    
                    if status_res != "ERROR":
                        successful_in_first_pass += 1
                    else:
                        final_error_count += 1
                        # Save negative cache so it's not retried again today, but tomorrow
                        try:
                            with get_connection() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("""
                                        INSERT INTO promoter_pledge_cache (symbol, pledge_pct, updated_at)
                                        VALUES (%s, 0.0, NOW() - INTERVAL '27 days')
                                        ON CONFLICT (symbol) DO UPDATE 
                                        SET updated_at = NOW() - INTERVAL '27 days'
                                    """, (sym,))
                                    conn.commit()
                            logger.info(f"⚠️ Saved temporary failure negative cache for {sym}")
                        except Exception as cache_err:
                            logger.error(f"Failed to save failure cache for {sym}: {cache_err}")
                    time.sleep(3)
                    
                    now_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                    current_processed = processed_base + successful_in_first_pass
                    upsert_scanner_health("Pledge Worker", "OK", last_success=now_str, today_alerts=current_processed, error_msg=f"Last: {sym} (Retry) | Total: {total_watch}")

            # Loop done
            status = "IDLE" if final_error_count == 0 else "DOWN"
            last_sym = stale_symbols[-1] if stale_symbols else "None"
            current_processed = processed_base + successful_in_first_pass
            
            now_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            
            if final_error_count > 0:
                err_msg = f"Last: {last_sym} | Total: {total_watch} | Failed: {final_error_count}"
                logger.warning(f"⚠️ Pledge Worker completed with {final_error_count} failures")
            else:
                err_msg = f"Last: {last_sym} | Total: {total_watch}"
                logger.info(f"✅ Pledge Worker completed successfully for all {total_watch} symbols")
            
            upsert_scanner_health("Pledge Worker", status, last_success=now_str, today_alerts=current_processed, error_msg=err_msg)
            
            if quota_exhausted:
                logger.info("⏳ Quota exhausted or proxy blocked. Sleeping for 1 hour before retrying...")
                upsert_scanner_health("Pledge Worker", "ERROR", last_success=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(), error_msg="Quota Exhausted - Waiting 1h")
                time.sleep(3600)
                logger.info("⏰ Woke up from 1-hour sleep! Retrying scraper loop now...")
                continue
                
            # Sleep for 5 minutes before rechecking (allows watchlist updates)
            time.sleep(300)
            
        except Exception as e:
            logger.exception("Pledge worker loop crashed")
            upsert_scanner_health("Pledge Worker", "DOWN", error_msg=str(e), today_alerts=1)
            time.sleep(300)

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    worker_loop()
