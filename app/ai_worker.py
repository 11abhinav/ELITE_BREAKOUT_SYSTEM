import os
import time
import logging
import threading
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST_ZONE = ZoneInfo("Asia/Kolkata")
from pledge_worker import get_constituents_cached

def is_in_window() -> bool:
    """Check if current time is between 7 PM IST and 7 AM IST."""
    now = datetime.now(IST_ZONE)
    return now.hour >= 19 or now.hour < 7

def wait_until_next_window() -> float:
    """Calculate seconds until the next 7 PM IST."""
    now = datetime.now(IST_ZONE)
    target = now.replace(hour=19, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()

# [VERSION: AI_WORKER_MANUAL_v1.0] Extract run_ai_worker_scan_once and protect with _scan_lock
_scan_lock = threading.Lock()

def run_ai_worker_scan_once() -> dict:
    """Run a single scan of watchlist and excluded stocks to analyze concalls.
    Protected by _scan_lock to prevent concurrent executions.
    """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("🤖 AI Worker Scan already running. Skipping execution.")
        raise RuntimeError("AI Worker is already actively running!")
        
    try:
        from config import WATCHLIST_PATH
        from database import get_recent_concall_analysis, upsert_scanner_health, get_total_cached_concalls, upsert_fetch_error, save_concall_analysis
        from dashboard_server import fetch_and_analyze_concall
        
        logger.info("🤖 AI Worker: Starting manual concall analysis scan...")
        
        if not os.path.exists(WATCHLIST_PATH):
            logger.warning("Watchlist parquet file does not exist yet.")
            return {"total_count": 0, "processed_count": 0}
            
        try:
            df = pd.read_parquet(WATCHLIST_PATH)
        except Exception as e:
            logger.exception(f"Failed to read parquet watchlist")
            return {"total_count": 0, "processed_count": 0}
            
        pending_stocks = df["Stock"].tolist()
        
        # Read excluded stocks so they are pre-cached if they break out later
        excluded_csv_paths = [
            os.path.join(os.path.dirname(WATCHLIST_PATH), 'elite_fundamental_watchlist_excluded.csv'),
            os.path.join(os.path.dirname(WATCHLIST_PATH), 'elite_fundamental_watchlist-excluded.csv'),
            WATCHLIST_PATH.replace('.parquet', '_excluded.csv'),
        ]
        for excluded_csv_path in excluded_csv_paths:
            if os.path.exists(excluded_csv_path):
                try:
                    df_ex = pd.read_csv(excluded_csv_path)
                    if 'Stock' in df_ex.columns:
                        ex_stocks = df_ex['Stock'].dropna().tolist()
                        pending_stocks.extend(ex_stocks)
                        break
                except Exception as e:
                    logger.warning(f"Failed to load exclusion list {excluded_csv_path}: {e}")
                    
        pending_stocks = sorted(list(set(pending_stocks)))
        total_stocks = len(pending_stocks)
        
        # Pre-filter to only those that actually need processing today
        actual_pending = []
        for sym in pending_stocks:
            cached = get_recent_concall_analysis(sym, max_age_days=60)
            # If we have a valid cache (dict without 'error', or string without 'error'), skip it.
            if cached:
                if isinstance(cached, dict) and "error" not in cached:
                    continue
                elif isinstance(cached, str) and "error" not in cached.lower():
                    continue
                    
            cached_today = get_recent_concall_analysis(sym, max_age_days=1)
            if cached_today:
                if isinstance(cached_today, dict) and "error" in cached_today:
                    continue
                elif isinstance(cached_today, str) and "error" in cached_today.lower():
                    continue
                    
            actual_pending.append(sym)
            
        db_processed_count = get_total_cached_concalls()
        if not actual_pending:
            logger.info(f"🤖 [AI WORKER] All {total_stocks} stocks are already cached today.")
            return {"total_count": total_stocks, "processed_count": db_processed_count}
            
        logger.info(f"🤖 [AI WORKER] Found {len(actual_pending)}/{total_stocks} stocks requiring analysis.")
        
        max_retries = 3
        global_penalty_idx = 0
        final_failed_count = 0
        db_processed_count = total_stocks - len(actual_pending)
        
        for attempt in range(max_retries):
            failed_stocks = []
            for i, sym in enumerate(actual_pending):
                try:
                    logger.info(f"🤖 [AI WORKER] Missing cache for {sym} ({i+1}/{len(actual_pending)} in batch). Fetching live...")
                    result = fetch_and_analyze_concall(sym)
                    
                    if result and "error" not in result:
                        global_penalty_idx = 0
                        conf = result.get("management_confidence", "N/A")
                        key_used = result.get("key_used", "Key 1")
                        logger.info(f"✅ [AI WORKER] Successfully cached analysis for {sym} | Confidence: {conf} | {key_used}")
                        db_processed_count += 1
                        upsert_scanner_health("AI Worker", "OK", last_success=datetime.now(IST_ZONE).isoformat(), today_alerts=db_processed_count, processed_count=db_processed_count, total_count=total_stocks, error_msg=f"Last: {sym} | Total: {total_stocks}")
                    else:
                        error_msg = result.get('error', 'Unknown Error')
                        logger.warning(f"⚠️ [AI WORKER] Failed to cache {sym}: {error_msg}")
                        try:
                            upsert_fetch_error('ai', 'AI Worker', sym, None, 'ai_concall', error_msg)
                        except Exception:
                            logger.exception("Failed to upsert fetch_error for AI Worker")
                        upsert_scanner_health("AI Worker", "OK", last_success=datetime.now(IST_ZONE).isoformat(), today_alerts=db_processed_count, processed_count=db_processed_count, total_count=total_stocks, error_msg=f"Last: {sym} | Total: {total_stocks}")
                        
                        if "429" in error_msg or "All AI models" in error_msg:
                            failed_stocks.append(sym)
                            penalty = [300, 900, 1800][min(global_penalty_idx, 2)]
                            logger.warning(f"⚠️ [AI WORKER] Global API rate limit/failure. Backing off for {penalty//60} minutes...")
                            time.sleep(penalty)
                            global_penalty_idx += 1
                        else:
                            logger.warning(f"⚠️ [AI WORKER] Saving negative cache for {sym} - {error_msg}, will not retry today")
                            save_concall_analysis(sym, f"NONE_{sym}", {"error": error_msg})
                    time.sleep(5)
                except Exception as e:
                    logger.exception(f"❌ [AI WORKER] Error processing {sym}")
                    try:
                        upsert_fetch_error('ai', 'AI Worker', sym, None, 'ai_concall_failure', str(e))
                    except Exception:
                        logger.exception(f"Failed to upsert fetch_error for {sym}")
                    failed_stocks.append(sym)
                    time.sleep(10)
                    
            if not failed_stocks:
                break
            actual_pending = failed_stocks
            if attempt < max_retries - 1:
                logger.info(f"🤖 [AI WORKER] {len(failed_stocks)} stocks failed. Retrying in 60s (Attempt {attempt+2}/{max_retries})...")
                time.sleep(60)
            else:
                logger.error(f"❌ [AI WORKER] Giving up on {len(failed_stocks)} stocks after {max_retries} attempts.")
                final_failed_count = len(failed_stocks)
                for fsym in failed_stocks:
                    try:
                        upsert_fetch_error('ai', 'AI Worker', fsym, None, 'ai_concall', 'Giving up after retries')
                        save_concall_analysis(fsym, f"NONE_{fsym}", {"error": "Giving up after retries"})
                    except Exception:
                        logger.exception(f"Failed to upsert final fetch_error for {fsym}")
                        
        return {"total_count": total_stocks, "processed_count": db_processed_count}
        
    finally:
        _scan_lock.release()

def run_worker_loop():
    """Infinite loop that scans the watchlist CSV and fetches AI concall reports."""
    from database import upsert_scanner_health, get_ai_concall_stats
    from config import WATCHLIST_PATH
    
    logger.info("🤖 AI Worker Thread Started. Monitoring watchlist for missing caches...")
    
    # [VERSION: AI_WORKER_PROGRESS_v1.0] Calculate initial dynamic counts on boot
    try:
        symbols_set = set()
        if os.path.exists(WATCHLIST_PATH):
            df = pd.read_parquet(WATCHLIST_PATH)
            if "Stock" in df.columns:
                symbols_set.update(df["Stock"].dropna().unique().tolist())
                
        excluded_paths = [
            os.path.join(os.path.dirname(WATCHLIST_PATH), 'elite_fundamental_watchlist_excluded.csv'),
            os.path.join(os.path.dirname(WATCHLIST_PATH), 'elite_fundamental_watchlist-excluded.csv'),
            WATCHLIST_PATH.replace(".parquet", "_excluded.csv"),
        ]
        for f in excluded_paths:
            if os.path.exists(f):
                try:
                    dfw = pd.read_csv(f)
                    if 'Stock' in dfw.columns:
                        symbols_set.update(dfw['Stock'].dropna().tolist())
                        break
                except Exception:
                    pass
                    
        idx_symbols = get_constituents_cached()
        if idx_symbols:
            symbols_set.update(idx_symbols)
            
        symbols = list(symbols_set)
        total_watch = len(symbols)
        stats = get_ai_concall_stats(symbols)
        processed_count = stats.get("total_cached", 0)
    except Exception as e:
        logger.warning(f"Failed to calculate boot progress stats: {e}")
        total_watch = 0
        processed_count = 0
        
    upsert_scanner_health("AI Worker", "IDLE", last_success=None, today_alerts=processed_count, processed_count=processed_count, total_count=total_watch, error_msg="Status: Booting up")
    
    while True:
        # Re-calculate on each loop iteration
        try:
            symbols_set = set()
            if os.path.exists(WATCHLIST_PATH):
                df = pd.read_parquet(WATCHLIST_PATH)
                if "Stock" in df.columns:
                    symbols_set.update(df["Stock"].dropna().unique().tolist())
                    
            for f in excluded_paths:
                if os.path.exists(f):
                    try:
                        dfw = pd.read_csv(f)
                        if 'Stock' in dfw.columns:
                            symbols_set.update(dfw['Stock'].dropna().tolist())
                            break
                    except Exception:
                        pass
                        
            idx_symbols = get_constituents_cached()
            if idx_symbols:
                symbols_set.update(idx_symbols)
                
            symbols = list(symbols_set)
            total_watch = len(symbols)
            stats = get_ai_concall_stats(symbols)
            processed_count = stats.get("total_cached", 0)
        except Exception as e:
            logger.warning(f"Failed to calculate loop progress stats: {e}")
            total_watch = total_watch or 0
            processed_count = processed_count or 0
            
        if not is_in_window():
            sleep_secs = wait_until_next_window()
            logger.info(f"🤖 [AI WORKER] Outside active window (7 PM - 7 AM IST). Sleeping {sleep_secs:.1f}s until 7 PM IST...")
            upsert_scanner_health("AI Worker", "IDLE", today_alerts=processed_count, processed_count=processed_count, total_count=total_watch, error_msg="Outside active window (7 PM - 7 AM IST)")
            time.sleep(sleep_secs)
            continue

        try:
            stats_scan = run_ai_worker_scan_once()
            status = "IDLE"
            error_msg = f"Last: Finished | Total: {stats_scan.get('total_count', 'N/A')}"
            
            # Recalculate after running scan
            try:
                stats = get_ai_concall_stats(symbols)
                processed_count = stats.get("total_cached", 0)
            except Exception:
                pass
                
            upsert_scanner_health("AI Worker", status, last_success=datetime.now(IST_ZONE).isoformat(), today_alerts=processed_count, processed_count=processed_count, total_count=total_watch, error_msg=error_msg)
        except RuntimeError:
            # Already running manually
            pass
        except Exception as e:
            logger.exception(f"❌ [AI WORKER] Main loop crashed")
            upsert_scanner_health("AI Worker", "DOWN", error_msg=str(e))
            try:
                from database import insert_notification
                from push_service import send_push_to_all
                insert_notification("admin", f"❌ AI WORKER CRASHED (DOWN)", f"Error: {str(e)[:200]}")
                send_push_to_all("❌ AI WORKER DOWN", f"Crash: {str(e)[:100]}")
            except Exception:
                pass
            
        time.sleep(300)

def start_worker():
    """Starts the AI worker in a daemon thread."""
    thread = threading.Thread(target=run_worker_loop, daemon=True)
    thread.start()
    return thread

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker_loop()
