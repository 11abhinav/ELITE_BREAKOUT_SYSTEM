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
    
    from pledge_scraper import get_crawlora_api_key, mark_crawlora_key_exhausted_today, get_scraper_api_key, mark_key_exhausted_today
    crawlora_key = get_crawlora_api_key()
    scraper_key = get_scraper_api_key()
    
    if not crawlora_key and not scraper_key:
        return fast_url

    # 1. Attempt fast HEAD/GET request via Crawlora first, then ScraperAPI
    res = None
    if crawlora_key:
        try:
            c_payload = {'api_key': crawlora_key, 'url': fast_url}
            res = requests.get('https://api.crawlora.net/v1/scrape', params=c_payload, timeout=15)
            if res is not None and res.status_code in (401, 403, 429):
                mark_crawlora_key_exhausted_today(crawlora_key)
                res = None
            elif res is not None and res.status_code == 200:
                return fast_url
        except Exception:
            res = None

    if res is None and scraper_key:
        payload = {'api_key': scraper_key, 'url': fast_url, 'render': 'false'}
        try:
            res = requests.get('https://api.scraperapi.com/', params=payload, timeout=10)
            if res is not None and res.status_code == 200:
                return fast_url
        except Exception:
            pass

    # 2. If direct URL 404s/fails, search Google via Crawlora first, then ScraperAPI
    logger.info(f"🔍 Direct URL failed for {clean_symbol}. Searching Google...")
    search_url = f"https://www.google.com/search?q=site:trendlyne.com/equity/+{clean_symbol}"
    
    search_res = None
    if crawlora_key:
        try:
            c_payload = {'api_key': crawlora_key, 'url': search_url}
            search_res = requests.get('https://api.crawlora.net/v1/scrape', params=c_payload, timeout=30)
            if search_res is not None and search_res.status_code in (401, 403, 429):
                mark_crawlora_key_exhausted_today(crawlora_key)
                search_res = None
        except Exception:
            search_res = None

    if search_res is None and scraper_key:
        payload = {'api_key': scraper_key, 'url': search_url, 'render': 'false'}
        try:
            search_res = requests.get('https://api.scraperapi.com/', params=payload, timeout=30)
        except Exception as e:
            logger.warning(f"Google search fallback failed for {clean_symbol}: {e}")

    if search_res is not None and search_res.status_code == 200:
        soup = BeautifulSoup(search_res.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if "trendlyne.com/equity/" in href and clean_symbol.upper() in href.upper():
                actual_url = href.split("q=")[-1].split("&")[0] if "/url?q=" in href else href
                if actual_url.startswith("https://trendlyne.com"):
                    import re
                    m = re.search(r'(https://trendlyne\.com/equity/)(?:[a-z\-]+/)?(\d+/[^/]+/[^/]+/?)', actual_url)
                    if m:
                        actual_url = m.group(1) + m.group(2)
                        
                    logger.info(f"✅ Discovered Google URL for {clean_symbol}: {actual_url}")
                    return actual_url

    # Ultimate fallback: Return direct stock URL so discover_trendlyne_url NEVER returns None
    return fast_url

def save_pledge_cache(symbol: str, pledge_val: float, is_not_found: bool = False):
    """Save or update promoter pledge cache with single connection checkout."""
    try:
        updated_expr = "NOW()" if not is_not_found else "NOW() - INTERVAL '21 days'"
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    INSERT INTO promoter_pledge_cache (symbol, pledge_pct, updated_at, last_attempted_at)
                    VALUES (%s, %s, {updated_expr}, NOW())
                    ON CONFLICT (symbol) DO UPDATE 
                    SET pledge_pct = EXCLUDED.pledge_pct, updated_at = {updated_expr}, last_attempted_at = NOW()
                """, (symbol, pledge_val))
                conn.commit()
    except Exception as e:
        logger.error(f"Failed to save pledge cache for {symbol}: {e}")

def is_pledge_active_window(now: datetime = None) -> bool:
    """Check if current time is within active worker window: Whole Saturday & Sunday only."""
    if now is None:
        now = datetime.now(IST_ZONE)
    return now.weekday() >= 5  # 5=Saturday, 6=Sunday

def get_pledge_window_desc(now: datetime = None) -> str:
    return "00:00 - 23:59 IST (Sat-Sun Only)"

def worker_loop():
    import time
    logger.info("🚀 Starting Pledge Worker Daemon")
    init_db()
    iteration = 0
    
    if not os.getenv("SCRAPERAPI_KEY"):
        logger.error("❌ SCRAPERAPI_KEY env var not set. Scraper daemon will pause.")
        while True:
            try:
                upsert_scanner_health("Pledge Worker", "DOWN", error_msg="SCRAPERAPI_KEY env var is not set")
            except Exception:
                pass
            time.sleep(3600)

    while True:
        iteration += 1
        loop_start = time.time()
        mode = get_worker_mode()
        now = datetime.now(IST_ZONE)
        logger.debug(f"\n{'='*70}")
        logger.debug(f"🔄 [PLEDGE WORKER] Iteration #{iteration} | Mode={mode} | Time={now.strftime('%H:%M:%S IST')}")
        logger.debug(f"{'='*70}")
        
        # [VERSION: PLEDGE_WORKER_PROGRESS_v1.6] Load universe and check DB on every loop iteration
        # to ensure dashboard stats show correct cumulative counts (old + todays) instantly on boot.
        symbols_set = set()
        watchlist_count = 0
        if os.path.exists(WATCHLIST_PATH):
            try:
                df = pd.read_parquet(WATCHLIST_PATH)
                if "Stock" in df.columns:
                    watch_symbols = df["Stock"].unique().tolist()
                    symbols_set.update(watch_symbols)
                    watchlist_count = len(watch_symbols)
            except Exception as e:
                logger.warning(f"Could not read watchlist parquet: {e}")
                
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
                        break
                except Exception as e:
                    logger.warning(f"Could not read excluded csv {excluded_path}: {e}")
                    
        from constituent_service import fetch_constituents
        idx_symbols = fetch_constituents()
        constituents_count = len(idx_symbols) if idx_symbols else 0
        if idx_symbols:
            symbols_set.update(idx_symbols)
            
        if not symbols_set:
            logger.warning("No symbols found in watchlist, excluded list, or constituents. Sleeping 60s...")
            time.sleep(60)
            continue
            
        symbols = sorted(list(symbols_set))
        total_watch = len(symbols)
        
        # Check DB for stale pledges and calculate processed_base
        processed_base = 0
        stale_symbols = []
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT symbol 
                        FROM promoter_pledge_cache 
                        WHERE symbol = ANY(%s)
                          AND (updated_at >= NOW() - INTERVAL '28 days' OR COALESCE(last_attempted_at, updated_at) >= CURRENT_DATE)
                    """, (symbols,))
                    rows = cur.fetchall()
                    fresh_symbols = {row[0] for row in rows}
            
            for sym in symbols:
                if sym in fresh_symbols:
                    processed_base += 1
                else:
                    stale_symbols.append(sym)
        except Exception as e:
            logger.exception("Failed to check database for stale symbols")
            
        from database import is_scanner_stopped
        if mode == 'manual_stop' or is_scanner_stopped("Pledge Worker"):
            upsert_scanner_health("Pledge Worker", "STOPPED", last_success=now.isoformat(), today_alerts=processed_base, processed_count=processed_base, total_count=total_watch, error_msg="Stopped by Admin")
            sleep_with_mode_check(60)
            continue
            
        if mode == 'auto':
            if not is_pledge_active_window(now):
                win_desc = get_pledge_window_desc(now)
                upsert_scanner_health("Pledge Worker", "IDLE", last_success=now.isoformat(), today_alerts=processed_base, processed_count=processed_base, total_count=total_watch, error_msg=f"Outside active window ({win_desc})")
                sleep_with_mode_check(300)
                continue

        try:
            if not get_scraper_api_key():
                logger.warning("🚨 All Scraper API keys are exhausted. Pausing scraping daemon for 1 hour.")
                time.sleep(3600)
                continue
                
            logger.debug(f"📋 [PLEDGE WORKER] Universe loaded in {time.time()-loop_start:.1f}s | Watchlist={watchlist_count} | Excluded={excluded_count} | Constituents={constituents_count} | Total={len(symbols_set)} unique symbols")
            logger.debug(f"💾 [PLEDGE WORKER] Checking DB for stale pledge data (threshold: 28 days)...")
            logger.debug(f"🔍 [PLEDGE WORKER] DB Check complete: {len(stale_symbols)} stale symbols need refresh, {total_watch - len(stale_symbols)} already fresh in DB")
            upsert_scanner_health("Pledge Worker", "OK", today_alerts=processed_base, processed_count=processed_base, total_count=total_watch, error_msg=f"Last: Starting... | Total stale: {len(stale_symbols)}")

            if not stale_symbols:
                if get_worker_mode() == 'manual_start':
                    logger.info("Manual start completed. Reverting to auto mode.")
                    set_worker_mode('auto')
                    
                sleep_secs = 3600 # Check every hour
                # [VERSION: PLEDGE_WORKER_PROGRESS_v1.4] Update start loops to show processed_base / total_watch
                logger.debug(f"✅ [PLEDGE WORKER] All promoter pledges are processed for today. Sleeping {sleep_secs}s...")
                upsert_scanner_health("Pledge Worker", "IDLE", last_success=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(), today_alerts=total_watch, processed_count=total_watch, total_count=total_watch, error_msg=f"All processed | Total: {total_watch}")
                sleep_with_mode_check(sleep_secs)
                logger.debug("⏰ Woke up from daily sleep! Starting fresh scan...")
                continue
                
            logger.info(f"📊 [PLEDGE WORKER] Pending symbols to fetch today: {len(stale_symbols)} (out of {total_watch} total universe)")
            from database import start_scanner_execution_run, complete_scanner_execution_run
            worker_run_ctx = start_scanner_execution_run(scanner_name="Pledge Worker", trigger_type="SCHEDULED", scheduler_name="WORKER", total_stocks=len(stale_symbols))
            upsert_scanner_health("Pledge Worker", "OK", today_alerts=processed_base, processed_count=processed_base, total_count=total_watch, error_msg=f"Last: Starting... | Total stale: {len(stale_symbols)}")
            
            def process_symbol(sym, i_total, is_retry=False):
                """Returns 'FOUND', 'MISSING', '404', or 'ERROR'."""
                target_url = discover_trendlyne_url(sym) or f"https://trendlyne.com/stock/{sym.replace('.NS', '')}/"
                prefix = "[RETRY]" if is_retry else f"[{i_total}/{len(stale_symbols)}]"
                logger.info(f"{prefix} Scraping pledge for {sym} at {target_url}")
                
                from pledge_scraper import get_crawlora_api_key, mark_crawlora_key_exhausted_today, get_scraper_api_key, mark_key_exhausted_today
                crawlora_key = get_crawlora_api_key()
                scraper_key = get_scraper_api_key()
                
                if not crawlora_key and not scraper_key:
                    logger.error(f"❌ All Crawlora & ScraperAPI keys exhausted or missing during processing {sym}")
                    return "QUOTA_EXHAUSTED"
                    
                res = None
                
                # 1. Try Crawlora First
                if crawlora_key:
                    try:
                        c_payload = {'api_key': crawlora_key, 'url': target_url}
                        masked_ckey = f"{crawlora_key[:4]}...{crawlora_key[-4:]}" if len(crawlora_key) > 8 else "CRAWLORA"
                        logger.info(f"🌐 [CRAWLORA] Scraping pledge for {sym} (Key: [{masked_ckey}]): {target_url}")
                        res = requests.get('https://api.crawlora.net/v1/scrape', params=c_payload, timeout=45)
                        if res is not None and res.status_code in (401, 403, 429):
                            logger.warning(f"⚠️ [CRAWLORA EXHAUSTED] HTTP {res.status_code} for key [{masked_ckey}] URL={target_url}. Reason: {res.text[:150]}. Marking key exhausted.")
                            mark_crawlora_key_exhausted_today(crawlora_key)
                            res = None
                        elif res is not None and res.status_code == 200:
                            logger.info(f"✅ [CRAWLORA SUCCESS] HTTP 200 for {sym} ({len(res.content)} bytes)")
                        else:
                            status_str = res.status_code if res else "No Response"
                            logger.warning(f"⚠️ [CRAWLORA FAIL] HTTP {status_str} for {sym}: {res.text[:150] if res else ''}")
                            res = None
                    except Exception as crawlora_err:
                        logger.warning(f"❌ [CRAWLORA ERROR] Exception for {sym}: {crawlora_err}")
                        res = None

                # 2. Fall back to ScraperAPI if Crawlora is missing or failed
                if res is None and scraper_key:
                    payload = {'api_key': scraper_key, 'url': target_url, 'render': 'false'}
                    masked_skey = f"{scraper_key[:4]}...{scraper_key[-4:]}" if len(scraper_key) > 8 else "SCRAPERAPI"
                    try:
                        logger.info(f"🌐 [SCRAPERAPI] Scraping pledge for {sym} (Key: [{masked_skey}]): {target_url}")
                        res = requests.get('https://api.scraperapi.com/', params=payload, timeout=45)
                        if res is not None and res.status_code in (401, 403, 429):
                            reason = res.text.strip()[:200]
                            try:
                                err_dict = res.json()
                                if isinstance(err_dict, dict) and "error" in err_dict:
                                    reason = err_dict["error"]
                            except Exception:
                                pass
                            logger.warning(f"❌ [SCRAPERAPI EXHAUSTED] HTTP {res.status_code} for key [{masked_skey}] URL={target_url}. Reason: {reason}")
                            mark_failure('scraperapi', f"HTTP {res.status_code} ({reason}): URL={target_url}")
                            mark_key_exhausted_today(scraper_key)
                            return "ERROR"
                        elif res is not None and res.status_code == 200:
                            logger.info(f"✅ [SCRAPERAPI SUCCESS] HTTP 200 for {sym} ({len(res.content)} bytes)")
                        else:
                            status_str = res.status_code if res else "No Response"
                            logger.warning(f"⚠️ [SCRAPERAPI FAIL] HTTP {status_str} for {sym}: {res.text[:150] if res else ''}")
                    except Exception as e:
                        logger.warning(f"❌ [SCRAPERAPI ERROR] Exception for {sym}: {e}")
                            
                if res is None:
                    logger.error(f"❌ No valid response received for {sym} from Crawlora or ScraperAPI")
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
                        # [VERSION: PLEDGE_WORKER_STAT_v1.0] Update cache inserts to populate last_attempted_at column
                        # Streamlined single connection checkout per symbol save
                        save_pledge_cache(sym, pledge_val if pledge_val is not None else -1.0, is_not_found=(pledge_val is None))
                        if pledge_val is not None:
                            logger.info(f"✅ Saved pledge for {sym}: {pledge_val}%")
                        else:
                            logger.warning(f"⚠️ Could not find pledge text on page for {sym}. Saving -1.0 (Not Found) - retrying in 7 days")
                        mark_success('scraperapi')
                        return "FOUND" if pledge_val is not None else "MISSING"
                    elif res.status_code == 404:
                        logger.warning(f"❌ 404 Not Found for {sym} at {target_url}")
                        mark_failure('scraperapi', f"404 Not Found: {target_url}")
                        save_pledge_cache(sym, -1.0, is_not_found=True)
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
            
            scrape_start = time.time()
            import concurrent.futures
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(process_symbol, sym, i+1): sym for i, sym in enumerate(stale_symbols)}
                for future in concurrent.futures.as_completed(futures):
                    sym = futures[future]
                    sym_start = time.time()
                    try:
                        status_res = future.result()
                    except Exception as e:
                        logger.error(f"Error processing {sym}: {e}")
                        status_res = "ERROR"
                        
                    if status_res == "QUOTA_EXHAUSTED":
                        if not quota_exhausted:
                            logger.warning("🚨 All API keys are exhausted. Stopping scrape loop for now.")
                            quota_exhausted = True
                        continue
                        
                    if status_res == "FOUND": found_count += 1
                    elif status_res == "MISSING": missing_count += 1
                    elif status_res == "404": fail_404_count += 1
                    else: error_count += 1
                    
                    if status_res != "ERROR":
                        successful_in_first_pass += 1
                    else:
                        failed_queue.append(sym)
                        
                    sym_elapsed = round(time.time() - sym_start, 2)
                    processed = found_count + missing_count + fail_404_count + error_count
                    pending = total_stale - processed
                    logger.info(f"🛡️ [PLEDGE WORKER] [{processed}/{total_stale}] Scraping pledge for {sym} [{status_res}] | Elapsed={sym_elapsed}s | Pending={pending} | Found={found_count} | Errors={error_count}")
                        
                    # [VERSION: PLEDGE_WORKER_PROGRESS_v1.5] Update upserts to write processed_base + successful_in_first_pass
                    now_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                    upsert_scanner_health("Pledge Worker", "OK", last_success=now_str, today_alerts=processed_base + successful_in_first_pass, processed_count=processed_base + successful_in_first_pass, total_count=total_watch, error_msg=f"Last: {sym} | Total stale: {total_stale}")

            final_error_count = 0
            
            if failed_queue:
                logger.info(f"Retrying {len(failed_queue)} failed symbols...")
                time.sleep(10) # Brief pause before retries
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    retry_futures = {executor.submit(process_symbol, sym, 0, is_retry=True): sym for sym in failed_queue}
                    for future in concurrent.futures.as_completed(retry_futures):
                        sym = retry_futures[future]
                        try:
                            status_res = future.result()
                        except Exception as e:
                            logger.error(f"Error processing {sym} on retry: {e}")
                            status_res = "ERROR"
                            
                        if status_res == "QUOTA_EXHAUSTED":
                            if not quota_exhausted:
                                logger.warning("🚨 All API keys are exhausted during retry. Stopping scrape loop for now.")
                                quota_exhausted = True
                            continue
                        
                        if status_res != "ERROR":
                            successful_in_first_pass += 1
                        else:
                            final_error_count += 1
                            # Save negative cache so it's not retried again today, but tomorrow
                            try:
                                with get_connection() as conn:
                                    with conn.cursor() as cur:
                                        # [VERSION: PLEDGE_WORKER_STAT_v1.1] Update retry failure cache insert to populate last_attempted_at
                                        cur.execute("""
                                            INSERT INTO promoter_pledge_cache (symbol, pledge_pct, updated_at, last_attempted_at)
                                            VALUES (%s, 0.0, NOW() - INTERVAL '27 days', NOW())
                                            ON CONFLICT (symbol) DO UPDATE 
                                            SET updated_at = NOW() - INTERVAL '27 days', last_attempted_at = NOW()
                                        """, (sym,))
                                        conn.commit()
                                logger.info(f"⚠️ Saved temporary failure negative cache for {sym}")
                            except Exception as cache_err:
                                logger.error(f"Failed to save failure cache for {sym}: {cache_err}")
                        time.sleep(1) # Reduced to 1s since threads spread out load
                        
                        now_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                        upsert_scanner_health("Pledge Worker", "OK", last_success=now_str, today_alerts=processed_base + successful_in_first_pass, processed_count=processed_base + successful_in_first_pass, total_count=total_watch, error_msg=f"Last: {sym} (Retry) | Total stale: {total_stale}")

            # Loop done
            status = "IDLE" if final_error_count == 0 else "DOWN"
            last_sym = stale_symbols[-1] if stale_symbols else "None"
            
            now_str = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
            
            if final_error_count > 0:
                err_msg = f"Last: {last_sym} | Total stale: {total_stale} | Failed: {final_error_count}"
                logger.warning(f"⚠️ Pledge Worker completed with {final_error_count} failures")
            else:
                err_msg = f"Last: {last_sym} | Total stale: {total_stale}"
                logger.info(f"✅ Pledge Worker completed successfully for all {total_stale} stale symbols")
            
            upsert_scanner_health("Pledge Worker", status, last_success=now_str, today_alerts=processed_base + successful_in_first_pass, processed_count=processed_base + successful_in_first_pass, total_count=total_watch, error_msg=err_msg)
            
            loop_elapsed = round(time.time() - loop_start, 1)
            logger.info(f"✅ [PLEDGE WORKER] Iteration #{iteration} complete in {loop_elapsed}s | Found={found_count} | Missing={missing_count} | 404={fail_404_count} | Errors={final_error_count} | Total Processed={processed_base + successful_in_first_pass}/{total_watch}")
            
            if 'worker_run_ctx' in locals() and worker_run_ctx:
                worker_run_ctx.set_total_stocks(total_stale)
                worker_run_ctx.fresh_count = found_count
                worker_run_ctx.stale_count = missing_count + fail_404_count
                worker_run_ctx.incomplete_count = final_error_count
                complete_scanner_execution_run(worker_run_ctx)
            
            if quota_exhausted:
                logger.info("⏳ Quota exhausted or proxy blocked. Sleeping for 1 hour before retrying...")
                upsert_scanner_health("Pledge Worker", "ERROR", last_success=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(), today_alerts=processed_base + successful_in_first_pass, processed_count=processed_base + successful_in_first_pass, total_count=total_watch, error_msg="Quota Exhausted - Waiting 1h")
                sleep_with_mode_check(3600)
                logger.info("⏰ Woke up from 1-hour sleep! Retrying scraper loop now...")
                continue
                
            # Sleep for 5 minutes before rechecking (allows watchlist updates)
            sleep_with_mode_check(300)
            
        except Exception as e:
            logger.exception("Pledge worker loop crashed")
            upsert_scanner_health("Pledge Worker", "DOWN", error_msg=str(e), today_alerts=processed_base, processed_count=processed_base, total_count=total_watch)
            try:
                from database import insert_notification
                from push_service import send_push_to_all
                insert_notification("admin", f"❌ PLEDGE WORKER CRASHED (DOWN)", f"Error: {str(e)[:200]}")
                send_push_to_all("❌ PLEDGE WORKER DOWN", f"Crash: {str(e)[:100]}")
            except Exception:
                pass
            sleep_with_mode_check(300)

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    worker_loop()
