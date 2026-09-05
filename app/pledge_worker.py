"""
app/pledge_worker.py
====================
[RULE 67 CHANGE-RATIONALE: NSE_OFFICIAL_PLEDGE_WORKER_v2.0]
Official Exchange Ingestion Worker for Promoter Pledge & Encumbrance Data.
Replaces legacy 750-stock ScraperAPI/Trendlyne scraping with official bulk NSE ingestion.

Execution Schedule:
  - Day: Saturday Only
  - Window: 02:00 AM to 10:00 AM IST
  - Behavior: Downloads official bulk CSV snapshot from NSE via nse_pledge_fetcher,
    UPSERTs all 1,500+ records into PostgreSQL promoter_pledge_cache in under 5 seconds,
    records execution in pledge_snapshots, and finishes early.
  - Failure/Retry: If NSE returns an error (e.g. weekend maintenance), retries every 15 minutes
    until 10:00 AM IST hard stop.
"""

import os
import time
import logging
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any

from config import DATA_DIR
from database import (
    init_db,
    get_connection,
    upsert_scanner_health,
    has_today_pledge_snapshot,
    upsert_bulk_pledge_records,
    start_scanner_execution_run,
    complete_scanner_execution_run,
    is_scanner_stopped
)
from nse_pledge_fetcher import fetch_and_parse_nse_pledged_data

logger = logging.getLogger(__name__)
IST_ZONE = ZoneInfo("Asia/Kolkata")

CONFIG_PATH = os.path.join(DATA_DIR, "pledge_config.json")


def get_worker_mode() -> str:
    """Returns 'auto', 'manual_start', or 'manual_stop'."""
    if not os.path.exists(CONFIG_PATH):
        return 'auto'
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
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
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
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


def is_pledge_active_window(now: Optional[datetime] = None) -> bool:
    """
    Check if current time is within active worker window:
    Saturday 02:00 AM to 10:00 AM IST.
    """
    if now is None:
        now = datetime.now(IST_ZONE)
    # weekday() 5 = Saturday; 2 <= hour < 10 covers 02:00 to 10:00 AM IST
    return now.weekday() == 5 and (2 <= now.hour < 10)


def get_pledge_window_desc(now: Optional[datetime] = None) -> str:
    return "02:00 - 10:00 IST (Saturday Only)"


def run_pledge_worker_sync(force: bool = False) -> Dict[str, Any]:
    """
    Executes a single bulk ingestion pass of official NSE promoter pledge data:
    1. Checks if today's snapshot is already present in DB (unless force=True).
    2. Downloads and parses the official NSE CSV.
    3. Bulk UPSERTs all records into PostgreSQL promoter_pledge_cache.
    4. Records execution status in scanner_health and scanner_execution_history.
    """
    init_db()
    now_ist = datetime.now(IST_ZONE)
    start_time = time.time()

    # Step 1: Check if already completed today
    if not force and has_today_pledge_snapshot():
        logger.info("✅ [PLEDGE WORKER] Official NSE pledge snapshot already completed for today. Skipping duplicate pass.")
        return {
            "status": "ALREADY_COMPLETED",
            "message": "Snapshot already ingested for today",
            "matched_count": 0
        }

    # Step 2: Register scanner execution run
    worker_run_ctx = None
    try:
        worker_run_ctx = start_scanner_execution_run(
            scanner_name="Pledge Worker",
            trigger_type="MANUAL" if force else "SCHEDULED",
            scheduler_name="WORKER",
            total_stocks=0
        )
    except Exception as run_err:
        if "actively running" in str(run_err).lower():
            logger.info("⏳ Pledge Worker execution run is already actively running.")
            return {"status": "ALREADY_RUNNING", "message": "Already running"}
        logger.warning(f"Failed to register execution run: {run_err}")

    upsert_scanner_health("Pledge Worker", "RUNNING", error_msg="Downloading official NSE pledge snapshot...")

    try:
        # Step 3: Fetch and parse official bulk NSE CSV
        result, err = fetch_and_parse_nse_pledged_data()
        if err or not result:
            err_msg = err or "Empty result from NSE fetcher"
            logger.error(f"❌ [PLEDGE WORKER] NSE bulk fetch failed: {err_msg}")
            upsert_scanner_health(
                "Pledge Worker", "DEGRADED",
                last_success=now_ist.isoformat(),
                error_msg=f"NSE_FETCH_FAILED: {err_msg[:100]}"
            )
            if worker_run_ctx:
                complete_scanner_execution_run(worker_run_ctx, status_override="FAILED", stop_reason=err_msg)
            return {"status": "FAILED", "error": err_msg}

        records = result.get("records", [])
        total_rows = result.get("total_rows", len(records))
        matched_count = result.get("matched_count", len(records))
        snapshot_id = result.get("snapshot_id", "unknown")

        # Step 4: Bulk UPSERT into database
        upserted_count = upsert_bulk_pledge_records(records, result)

        elapsed = round(time.time() - start_time, 2)
        logger.info(
            f"🏆 [PLEDGE WORKER COMPLETE] Ingested {upserted_count} official NSE pledge records into PostgreSQL "
            f"in {elapsed}s (Snapshot: {snapshot_id})"
        )

        upsert_scanner_health(
            "Pledge Worker", "OK",
            last_success=now_ist.isoformat(),
            today_alerts=upserted_count,
            processed_count=upserted_count,
            total_count=total_rows,
            error_msg=f"Official NSE Snapshot Complete ({upserted_count} fresh)"
        )

        if worker_run_ctx:
            if hasattr(worker_run_ctx, "record_progress"):
                try:
                    worker_run_ctx.record_progress(processed=upserted_count, total=total_rows, success=upserted_count)
                except Exception:
                    pass
            complete_scanner_execution_run(
                worker_run_ctx,
                status_override="COMPLETED"
            )

        return {
            "status": "SUCCESS",
            "snapshot_id": snapshot_id,
            "total_rows": total_rows,
            "matched_count": matched_count,
            "elapsed_seconds": elapsed
        }

    except Exception as exc:
        logger.exception(f"❌ [PLEDGE WORKER ERROR] Unexpected failure during ingestion: {exc}")
        upsert_scanner_health("Pledge Worker", "DEGRADED", error_msg=f"Exception: {str(exc)[:100]}")
        if worker_run_ctx:
            complete_scanner_execution_run(worker_run_ctx, exception=exc)
        return {"status": "ERROR", "error": str(exc)}


def worker_loop():
    """
    Main daemon loop running continuously in background:
    Executes on Saturdays between 02:00 AM and 10:00 AM IST.
    """
    logger.info("🚀 Starting Official NSE Bulk Pledge Worker Daemon")
    init_db()
    iteration = 0

    while True:
        iteration += 1
        mode = get_worker_mode()
        now = datetime.now(IST_ZONE)

        # 1. Admin manual stop check
        if mode == 'manual_stop' or is_scanner_stopped("Pledge Worker"):
            upsert_scanner_health(
                "Pledge Worker", "STOPPED",
                last_success=now.isoformat(),
                today_alerts=0,
                error_msg="Stopped by Admin"
            )
            sleep_with_mode_check(60)
            continue

        # 2. Check if outside window (when in auto mode)
        if mode == 'auto':
            if not is_pledge_active_window(now):
                win_desc = get_pledge_window_desc(now)
                upsert_scanner_health(
                    "Pledge Worker", "IDLE",
                    last_success=now.isoformat(),
                    error_msg=f"Outside active window ({win_desc})"
                )
                sleep_with_mode_check(300)  # Check every 5 minutes
                continue

            # Within window: check if already completed today
            if has_today_pledge_snapshot():
                logger.debug("✅ [PLEDGE WORKER] Today's snapshot already completed. Sleeping until next check...")
                upsert_scanner_health(
                    "Pledge Worker", "IDLE",
                    last_success=now.isoformat(),
                    error_msg="Saturday snapshot completed"
                )
                sleep_with_mode_check(3600)  # Check hourly
                continue

        # 3. Trigger bulk ingestion
        is_manual = (mode == 'manual_start')
        logger.info(f"🔄 [PLEDGE WORKER] Starting NSE ingestion pass (Mode: {mode}, Time: {now.strftime('%H:%M:%S IST')})")

        res = run_pledge_worker_sync(force=is_manual)

        if is_manual:
            logger.info("Manual start completed. Reverting to auto mode.")
            set_worker_mode('auto')
            sleep_with_mode_check(60)
            continue

        if res.get("status") == "SUCCESS":
            logger.info("✅ Finished Saturday NSE pledge snapshot successfully. Entering weekly standby...")
            sleep_with_mode_check(3600)
        else:
            # If failed within window, retry after 15 minutes if still before 10:00 AM
            if now.hour < 10:
                logger.warning("⚠️ NSE ingestion failed. Retrying in 15 minutes (within 02:00-10:00 AM window)...")
                sleep_with_mode_check(900)
            else:
                logger.error("🛑 10:00 AM window elapsed. Halting retries until next Saturday.")
                sleep_with_mode_check(3600)
