# =====================================================================================
# app/durable_upload_queue.py
# DURABLE DB UPLOAD QUEUE
#
# RULE 67 MANDATORY CHANGE-RATIONALE:
# - Replaces bare submit_background_upload() thread-and-forget pattern for Multibagger
#   cache persistence.
# - Problem: bare daemon threads disappear silently on Railway container restart,
#   meaning a scan result is locally safe but its Postgres backup never completes.
# - Solution: A lightweight JSON-backed state machine with PENDING → UPLOADING →
#   SUCCESS / FAILED → RETRY states. A persistent background worker resumes
#   all PENDING/FAILED jobs from previous process runs on startup.
# - Local JSON file is the authoritative source of truth. DB parquet is durable
#   eventual persistence, not primary storage.
# - Gate 6 of the Multibagger Two-Pass implementation plan.
# =====================================================================================

import os
import json
import time
import uuid
import threading
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

logger = logging.getLogger("durable_upload_queue")
IST = ZoneInfo("Asia/Kolkata")

# ── Queue state machine constants ─────────────────────────────────────────────
_STATE_PENDING    = "PENDING"
_STATE_UPLOADING  = "UPLOADING"
_STATE_SUCCESS    = "SUCCESS"
_STATE_FAILED     = "FAILED"
_STATE_ABANDONED  = "ABANDONED"   # max retries exhausted — logged, not retried

_MAX_ATTEMPTS     = 3
_RETRY_DELAYS_S   = [30, 300, 1800]   # 30s → 5m → 30m exponential backoff
_QUEUE_PATH       = "data/durable_upload_queue.json"
_POLL_INTERVAL_S  = 15             # how often the daemon thread polls the queue

# ── Internal state ─────────────────────────────────────────────────────────────
_queue_lock  = threading.Lock()
_daemon_started = False


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def enqueue_durable_upload(name: str, file_path: str) -> str:
    """
    Atomically enqueue a DB parquet upload job. Returns the job_id.

    The job is written to QUEUE_PATH immediately before returning, so it
    survives a process restart. The background daemon picks it up and uploads
    it with retries.

    [RULE 67] This is the only function callers should use. Do NOT call
    upload_parquet_to_db() directly from scanner hot paths.
    """
    job_id = str(uuid.uuid4())[:8]
    job = {
        "job_id":          job_id,
        "name":            name,
        "file_path":       file_path,
        "status":          _STATE_PENDING,
        "attempts":        0,
        "max_attempts":    _MAX_ATTEMPTS,
        "created_at":      datetime.now(IST).isoformat(),
        "last_attempt_at": None,
        "error":           None,
    }
    _write_job(job)
    logger.info(f"📬 [DURABLE_QUEUE] Enqueued upload job {job_id}: name='{name}' file='{file_path}'")
    _ensure_daemon_running()
    return job_id


def resume_durable_uploads():
    """
    Called at system startup. Re-queues any PENDING or FAILED jobs that were
    left behind by a previous process run (e.g. Railway container restart).

    [RULE 67] Call this once from app startup, before any scanner runs.
    """
    queue = _load_queue()
    recoverable = [j for j in queue if j["status"] in (_STATE_PENDING, _STATE_FAILED, _STATE_UPLOADING)]
    if recoverable:
        logger.info(f"🔄 [DURABLE_QUEUE] Recovering {len(recoverable)} upload job(s) from previous run.")
        for job in recoverable:
            # Reset UPLOADING → PENDING (interrupted mid-upload)
            if job["status"] == _STATE_UPLOADING:
                job["status"] = _STATE_PENDING
                job["error"] = "Recovered from interrupted UPLOADING state (process restart)"
                _write_job(job)
    _ensure_daemon_running()


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_queue() -> list:
    """Load all jobs from the queue file. Returns [] if missing. 
    Quarantines corrupt files with admin warnings to preserve cache integrity."""
    if not os.path.exists(_QUEUE_PATH):
        return []
    try:
        with open(_QUEUE_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        else:
            raise ValueError(f"Queue data is not a list (got {type(data).__name__})")
    except Exception as e:
        timestamp = datetime.now(IST).strftime("%Y%m%d_%H%M%S")
        corrupt_path = f"{_QUEUE_PATH}.corrupt.{timestamp}"
        logger.error(f"🚨 [DURABLE_QUEUE ADMIN WARNING] Corrupt queue file detected ({e}). Quarantining to '{corrupt_path}'.")
        try:
            os.replace(_QUEUE_PATH, corrupt_path)
        except Exception as q_err:
            logger.error(f"Failed to quarantine corrupt file: {q_err}")
    return []


def _save_queue(queue: list):
    """Atomically write the full queue list to disk."""
    os.makedirs(os.path.dirname(_QUEUE_PATH) if os.path.dirname(_QUEUE_PATH) else ".", exist_ok=True)
    tmp_path = _QUEUE_PATH + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            json.dump(queue, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, _QUEUE_PATH)
    except Exception as e:
        logger.warning(f"⚠️ [DURABLE_QUEUE] Failed to save queue file: {e}")


def _write_job(job: dict):
    """Upsert a single job into the persistent queue file (thread-safe)."""
    with _queue_lock:
        queue = _load_queue()
        # Replace existing job with same job_id, or append if new
        updated = False
        for i, existing in enumerate(queue):
            if existing.get("job_id") == job["job_id"]:
                queue[i] = job
                updated = True
                break
        if not updated:
            queue.append(job)
        _save_queue(queue)


def _process_job(job: dict) -> dict:
    """
    Attempt a single upload for one job. Updates job state in-place and returns it.

    [RULE 67] Uses database.upload_parquet_to_db() which is the existing
    authoritative Postgres parquet upload function. No new DB logic introduced here.
    """
    job["status"] = _STATE_UPLOADING
    job["last_attempt_at"] = datetime.now(IST).isoformat()
    job["attempts"] = job.get("attempts", 0) + 1
    _write_job(job)

    try:
        if not os.path.exists(job["file_path"]):
            job["status"] = _STATE_ABANDONED
            job["error"] = f"Local payload file missing: {job['file_path']}"
            logger.error(f"❌ [DURABLE_QUEUE] Job {job['job_id']} ABANDONED — local payload file '{job['file_path']}' missing.")
            _write_job(job)
            return job

        from database import upload_parquet_to_db
        success = upload_parquet_to_db(job["name"], job["file_path"])
        if success:
            job["status"] = _STATE_SUCCESS
            job["error"] = None
            logger.info(f"☁️ [DURABLE_QUEUE] Job {job['job_id']} SUCCESS: uploaded '{job['name']}' to Postgres.")
        else:
            raise RuntimeError(f"upload_parquet_to_db returned False for '{job['name']}'")
    except Exception as e:
        job["error"] = str(e)
        if job["attempts"] >= job.get("max_attempts", _MAX_ATTEMPTS):
            job["status"] = _STATE_ABANDONED
            logger.error(
                f"❌ [DURABLE_QUEUE] Job {job['job_id']} ABANDONED after {job['attempts']} attempts: {e}. "
                f"Local file '{job['file_path']}' remains authoritative."
            )
        else:
            job["status"] = _STATE_FAILED
            delay = _RETRY_DELAYS_S[min(job["attempts"] - 1, len(_RETRY_DELAYS_S) - 1)]
            logger.warning(
                f"⚠️ [DURABLE_QUEUE] Job {job['job_id']} FAILED (attempt {job['attempts']}/{job['max_attempts']}): "
                f"{e}. Will retry in {delay}s."
            )

    _write_job(job)
    return job


def _is_job_ready_for_retry(job: dict) -> bool:
    """
    Returns True if a FAILED job's retry delay has elapsed.
    PENDING jobs are always ready.
    """
    if job["status"] == _STATE_PENDING:
        return True
    if job["status"] != _STATE_FAILED:
        return False

    last_attempt_str = job.get("last_attempt_at")
    if not last_attempt_str:
        return True

    try:
        last_attempt = datetime.fromisoformat(last_attempt_str)
        if last_attempt.tzinfo is None:
            last_attempt = last_attempt.replace(tzinfo=IST)
        elapsed = (datetime.now(IST) - last_attempt).total_seconds()
        attempt_num = job.get("attempts", 1)
        required_delay = _RETRY_DELAYS_S[min(attempt_num - 1, len(_RETRY_DELAYS_S) - 1)]
        return elapsed >= required_delay
    except Exception:
        return True


def _daemon_loop():
    """
    Background daemon thread. Polls the queue every _POLL_INTERVAL_S seconds
    and processes any PENDING or ready-to-retry FAILED jobs.

    [RULE 67] Runs as a daemon thread — does NOT block scanner execution.
    Exits cleanly when the process terminates.
    """
    logger.info("🟢 [DURABLE_QUEUE] Background upload daemon started.")
    while True:
        try:
            with _queue_lock:
                queue = _load_queue()

            actionable = [
                j for j in queue
                if j["status"] in (_STATE_PENDING, _STATE_FAILED)
                and _is_job_ready_for_retry(j)
            ]

            for job in actionable:
                logger.info(
                    f"🔄 [DURABLE_QUEUE] Processing job {job['job_id']} "
                    f"(attempt {job.get('attempts', 0) + 1}/{job.get('max_attempts', _MAX_ATTEMPTS)}): "
                    f"name='{job['name']}' file='{job['file_path']}'"
                )
                _process_job(job)

            # Prune old SUCCESS/ABANDONED jobs older than 48 hours to keep file small
            with _queue_lock:
                queue = _load_queue()
                now = datetime.now(IST)
                pruned = []
                for j in queue:
                    if j["status"] in (_STATE_SUCCESS, _STATE_ABANDONED):
                        try:
                            created = datetime.fromisoformat(j["created_at"])
                            if created.tzinfo is None:
                                created = created.replace(tzinfo=IST)
                            if (now - created).total_seconds() < 48 * 3600:
                                pruned.append(j)
                        except Exception:
                            pass  # drop corrupt entries
                    else:
                        pruned.append(j)
                _save_queue(pruned)

        except Exception as e:
            logger.exception(f"❌ [DURABLE_QUEUE] Daemon loop error: {e}")

        time.sleep(_POLL_INTERVAL_S)


def _ensure_daemon_running():
    """Start the background daemon thread if it is not already running."""
    global _daemon_started
    if _daemon_started:
        return
    with _queue_lock:
        if _daemon_started:
            return
        t = threading.Thread(target=_daemon_loop, name="durable_upload_daemon", daemon=True)
        t.start()
        _daemon_started = True
        logger.info("🟢 [DURABLE_QUEUE] Daemon thread launched.")
