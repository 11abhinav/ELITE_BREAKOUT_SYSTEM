import os
import time
import logging
import threading
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST_ZONE = ZoneInfo("Asia/Kolkata")
from constituent_service import fetch_constituents

def is_in_window() -> bool:
    """Check if current time is between 4 AM IST and 5 AM IST."""
    now = datetime.now(IST_ZONE)
    return 4 <= now.hour < 5

def wait_until_next_window() -> float:
    """Calculate seconds until the next 4 AM IST."""
    now = datetime.now(IST_ZONE)
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()

# [VERSION: AI_WORKER_MANUAL_v1.0] Extract run_ai_worker_scan_once and protect with _scan_lock
_scan_lock = threading.Lock()

def run_ai_worker_scan_once() -> dict:
    """Run a single scan of watchlist and excluded stocks to analyze concalls.
    Protected by _scan_lock to prevent concurrent executions.
    """
    from database import is_scanner_stopped
    if is_scanner_stopped("AI Worker"):
        logger.info("⏭️ AI Worker is PAUSED by Admin. Skipping execution.")
        return {"total_count": 0, "processed_count": 0}

    if not _scan_lock.acquire(blocking=False):
        logger.warning("🤖 AI Worker Scan already running. Skipping execution.")
        raise RuntimeError("AI Worker is already actively running!")
        
    _fn_start = time.time()
    try:
        from config import WATCHLIST_PATH
        from database import get_recent_concall_analysis, upsert_scanner_health, get_total_cached_concalls, upsert_fetch_error, save_concall_analysis, has_valid_concall_cache, has_error_concall_cache_within_24h
        from dashboard_server import fetch_and_analyze_concall
        
        upsert_scanner_health("AI Worker", "RUNNING", error_msg="AI Worker Scan in progress...")
        now_ist = datetime.now(IST_ZONE)
        logger.info("=" * 70)
        logger.info(f"🤖 [AI WORKER] Starting concall analysis scan | {now_ist.strftime('%H:%M:%S IST')}")
        logger.info("=" * 70)
        
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
        
        # ── Pre-filter: only process stocks that genuinely need analysis ────────────────
        from database import get_bulk_concall_cache_status
        status_cache = get_bulk_concall_cache_status(pending_stocks)
        
        actual_pending = []
        for sym in pending_stocks:
            # PRIMARY CHECK: Does a valid (non-error) cache exist for this symbol?
            if sym in status_cache['valid']:
                continue  # Valid analysis exists → skip

            # SECONDARY CHECK: Was an error cached within the last 7 days?
            if sym in status_cache['recent_error']:
                continue  # Recent error → back off

            actual_pending.append(sym)
            
        db_processed_count = get_total_cached_concalls()
        if not actual_pending:
            elapsed = round(time.time() - _fn_start, 1)
            logger.info(f"🤖 [AI WORKER] All {total_stocks} stocks already cached today. Nothing to do. ({elapsed}s)")
            return {"total_count": total_stocks, "processed_count": db_processed_count}
            
        logger.info(f"🤖 [AI WORKER] {len(actual_pending)}/{total_stocks} stocks need analysis | {total_stocks - len(actual_pending)} already cached in DB")
        
        max_retries = 3
        global_penalty_idx = 0
        final_failed_count = 0
        db_processed_count = total_stocks - len(actual_pending)
        
        for attempt in range(max_retries):
            attempt_start = time.time()
            logger.info(f"🤖 [AI WORKER] Batch attempt {attempt+1}/{max_retries} | Symbols to process: {len(actual_pending)}")
            failed_stocks = []
            for i, sym in enumerate(actual_pending):
                sym_start = time.time()
                try:
                    logger.info(f"🤖 [AI WORKER] Missing cache for {sym} ({i+1}/{len(actual_pending)} in batch). Fetching live...")
                    result = fetch_and_analyze_concall(sym)
                    
                    if result and "error" not in result:
                        sym_elapsed = round(time.time() - sym_start, 2)
                        global_penalty_idx = 0
                        conf = result.get("management_confidence", "N/A")
                        key_used = result.get("key_used", "Key 1")
                        logger.info(f"✅ [AI WORKER] {sym} ({i+1}/{len(actual_pending)}) ✔ Cached | Conf={conf} | {key_used} | {sym_elapsed}s")
                        db_processed_count += 1
                        upsert_scanner_health("AI Worker", "OK", last_success=datetime.now(IST_ZONE).isoformat(), today_alerts=db_processed_count, processed_count=db_processed_count, total_count=total_stocks, error_msg=f"Last: {sym} | Total: {total_stocks}")
                    else:
                        error_msg = result.get('error', 'Unknown Error') if result else 'No result returned'
                        logger.warning(f"⚠️ [AI WORKER] Failed to cache {sym}: {error_msg}")
                        try:
                            upsert_fetch_error('ai', 'AI Worker', sym, None, 'ai_concall', error_msg)
                        except Exception as inner_e:
                            logger.exception(f"Failed to upsert fetch_error for AI Worker: {inner_e}")
                        upsert_scanner_health("AI Worker", "OK", last_success=datetime.now(IST_ZONE).isoformat(), today_alerts=db_processed_count, processed_count=db_processed_count, total_count=total_stocks, error_msg=f"Last: {sym} | Total: {total_stocks}")

                        # Classify error for retry vs. negative-cache strategy
                        is_rate_limit = "429" in error_msg or "All AI models" in error_msg
                        is_transient  = "503" in error_msg or "502" in error_msg or "timeout" in error_msg.lower() or "unavailable" in error_msg.lower()

                        if is_rate_limit or is_transient:
                            # Transient/rate-limit errors → add to retry queue, back off
                            failed_stocks.append(sym)
                            penalty = [300, 900, 1800][min(global_penalty_idx, 2)]
                            logger.warning(f"⚠️ [AI WORKER] Transient/rate-limit error for {sym}. Backing off {penalty//60}m before retry...")
                            time.sleep(penalty)
                            global_penalty_idx += 1
                        else:
                            # Persistent error (no PDF, NSE down, etc.) → save negative cache to avoid re-hammering today
                            logger.warning(f"⚠️ [AI WORKER] Persistent error for {sym}: {error_msg}. Saving 24h negative cache.")
                            save_concall_analysis(sym, f"NONE_{sym}", {"error": error_msg})
                    time.sleep(5)
                except Exception as e:
                    logger.exception(f"❌ [AI WORKER] Error processing {sym}")
                    try:
                        upsert_fetch_error('ai', 'AI Worker', sym, None, 'ai_concall_failure', str(e))
                    except Exception as inner_e:
                        logger.exception(f"Failed to upsert fetch_error for {sym}: {inner_e}")
                    failed_stocks.append(sym)
                    time.sleep(10)
                    
            attempt_elapsed = round(time.time() - attempt_start, 1)
            logger.info(f"🤖 [AI WORKER] Attempt {attempt+1} done in {attempt_elapsed}s | Processed={len(actual_pending)-len(failed_stocks)} | Failed={len(failed_stocks)}")
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
                    except Exception as inner_e:
                        logger.exception(f"Failed to upsert final fetch_error for {fsym}: {inner_e}")
                        
        total_elapsed = round(time.time() - _fn_start, 1)
        logger.info("=" * 70)
        logger.info(f"🤖 [AI WORKER] Scan complete in {total_elapsed}s | Total={total_stocks} | Processed={db_processed_count} | Failed={final_failed_count}")
        logger.info("=" * 70)
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
                    
        idx_symbols = fetch_constituents()
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
        
    upsert_scanner_health("AI Worker", "IDLE", today_alerts=processed_count, processed_count=processed_count, total_count=total_watch, error_msg="Status: Booting up")
    
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
                        
            idx_symbols = fetch_constituents()
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
            
        from database import is_scanner_stopped
        if is_scanner_stopped("AI Worker"):
            upsert_scanner_health("AI Worker", "STOPPED", today_alerts=processed_count, processed_count=processed_count, total_count=total_watch, error_msg="Stopped by Admin")
            time.sleep(60)
            continue

        if not is_in_window():
            sleep_secs = wait_until_next_window()
            logger.info(f"🤖 [AI WORKER] Outside active window (04:00 - 05:00 IST). Sleeping {sleep_secs:.1f}s until 4 AM IST...")
            upsert_scanner_health("AI Worker", "IDLE", today_alerts=processed_count, processed_count=processed_count, total_count=total_watch, error_msg="Outside active window (04:00 - 05:00 IST)")
            time.sleep(min(sleep_secs, 300))
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
            except Exception as outer_e:
                logger.exception(f"Failed to send crash notifications: {outer_e}")
            
        time.sleep(300)

def start_worker():
    """Starts the AI worker in a daemon thread."""
    thread = threading.Thread(target=run_worker_loop, daemon=True)
    thread.start()
    return thread

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_worker_loop()
