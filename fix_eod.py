import re

with open("app/eod_scanner.py", "r") as f:
    code = f.read()

new_logic = """    if _scan_lock.locked():
        logger.warning("🛑 [DUPLICATE GUARD] EOD Scanner is ALREADY actively running in thread lock. Skipping duplicate trigger.")
        return 0

    queued_at = None
    if not _global_lock.acquire(blocking=False):
        queued_at = time.monotonic()
        logger.info("⏳ [EOD] Global scanner lock busy — marking QUEUED and waiting in queue...")
        upsert_scanner_health("EOD", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")
        if not _global_lock.acquire(blocking=True):
            raise RuntimeError("Failed to acquire global scanner lock.")
        logger.info(f"✅ [EOD] Global lock acquired after {round(time.monotonic()-queued_at,1)}s wait. Starting scan...")

    own_ctx = False
    if run_ctx is None:
        try:
            from database import start_scanner_execution_run
            run_ctx = start_scanner_execution_run(scanner_name="EOD", trigger_type=trigger_type, scheduler_name=scheduler_name)
            own_ctx = True
        except Exception:
            pass

    upsert_scanner_health("EOD", "RUNNING", error_msg="EOD scan in progress...")

    if not _scan_lock.acquire(blocking=False):
        _global_lock.release()
        logger.warning("🛑 EOD Scanner is ALREADY actively running. Skipping duplicate execution.")
        if own_ctx and run_ctx:
            from database import complete_scanner_execution_run
            complete_scanner_execution_run(run_ctx, status_override="SKIPPED_DUPLICATE", stop_reason="Scanner already actively running")
        return 0"""

pattern = re.compile(r'    own_ctx = False\n    if run_ctx is None:.*?return 0', re.DOTALL)
code = pattern.sub(new_logic, code, count=1)

with open("app/eod_scanner.py", "w") as f:
    f.write(code)
