import re

with open('app/main.py', 'r') as f:
    content = f.read()

# We need to replace the section from "def run_eod_scanner():" up to "def run_bayesian_loop():"
pattern = re.compile(r'def run_eod_scanner\(\):.*?(?=def run_bayesian_loop\(\):)', re.DOTALL)

replacement = """def _run_eod_with_retries(today_str):
    retry_count = 0
    while True:
        # Check database if we already succeeded today
        try:
            from database import get_all_scanner_health
            health_records = get_all_scanner_health()
            already_ran = False
            for rec in health_records:
                if rec.get("scanner_name") == "EOD" and rec.get("status") == "OK" and rec.get("last_success"):
                    last_success_str = str(rec["last_success"])
                    if last_success_str.startswith(today_str):
                        try:
                            from dateutil.parser import isoparse
                            ls_dt = isoparse(last_success_str)
                            start_time, _ = WINDOWS["eod"]
                            if ls_dt.time() >= start_time:
                                already_ran = True
                                break
                            else:
                                logger.info("📊 EOD SCAN | Previous run today was BEFORE 21:00 (manual trigger). Will execute scheduled run.")
                        except Exception as e:
                            logger.warning(f"Could not parse last_success: {e}")
                            already_ran = True
                            break
            
            if already_ran:
                logger.info("📊 EOD SCAN | Already successfully executed today.")
                return
        except Exception as e:
            logger.warning(f"Could not verify EOD previous run status: {e}")
        
        try:
            logger.info(f"📊 EOD SCAN | Starting scan for {today_str}...")
            from database import upsert_scanner_health
            upsert_scanner_health("EOD", status="QUEUED", error_msg="Waiting for global execution lock...")
            import eod_scanner
            with scanner_execution_lock:
                total = eod_scanner.start()   # returns int
                time.sleep(15)
            if total == 0:
                logger.info("📊 EOD | Zero alerts — no Telegram notification")
            else:
                logger.info(f"📊 EOD | Completed — {total} alert(s) sent")
            
            upsert_scanner_health(
                "EOD",
                status="OK",
                last_success=datetime.now(IST).isoformat(),
                today_alerts=total,
                scheduled_for="21:00 IST"
            )
            try:
                from performance_tracker import trigger_performance_rebuild
                trigger_performance_rebuild()
            except Exception as pe:
                logger.error(f"Failed to trigger performance rebuild post-EOD: {pe}")
            logger.info("✅ EOD SCANNER | Completed successfully for today.")
            return
            
        except Exception as exc:
            if "actively running" in str(exc).lower():
                logger.info("⏳ EOD scanner is already running in another process. Waiting...")
                time.sleep(60)
                continue
                
            retry_count += 1
            now = datetime.now(IST)
            
            if 0 <= now.hour < 6:
                logger.critical(f"⏰ MIDNIGHT PASSED — EOD scanner force-stopping after {retry_count} retries")
                upsert_scanner_health("EOD", status="DOWN", error_msg=f"Stopped at midnight after {retry_count} failed attempts", scheduled_for="21:00 IST")
                return
            
            logger.critical(f"💀 EOD scanner crashed (attempt {retry_count}): {exc}. Retrying in 1 minute...")
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health("EOD", status="DOWN", error_msg=str(exc)[:500], retry_count=retry_count, scheduled_for="21:00 IST")
            
            if retry_count == 1:
                try:
                    insert_notification(notif_type="scanner_down", title="🚨 EOD Scanner CRASHED", message=f"Error: {str(exc)[:400]}. Auto-retrying.")
                except Exception:
                    pass
            
            wait_time = min(300, (2 ** retry_count) * random.uniform(0.5, 1.5))
            time.sleep(wait_time)


def _run_reversal_with_retries(today_str):
    retry_count = 0
    while True:
        try:
            from database import get_all_scanner_health
            health_records = get_all_scanner_health()
            already_ran = False
            for rec in health_records:
                if rec.get("scanner_name") == "REVERSAL" and rec.get("status") == "OK" and rec.get("last_success"):
                    last_success_str = str(rec["last_success"])
                    if last_success_str.startswith(today_str):
                        try:
                            from dateutil.parser import isoparse
                            ls_dt = isoparse(last_success_str)
                            start_time, _ = WINDOWS["reversal"]
                            if ls_dt.time() >= start_time:
                                already_ran = True
                                break
                            else:
                                logger.info("🔄 REVERSAL SCAN | Previous run today was BEFORE 21:00 (manual trigger). Will execute scheduled run.")
                        except Exception as e:
                            logger.warning(f"Could not parse last_success: {e}")
                            already_ran = True
                            break
            
            if already_ran:
                logger.info("🔄 REVERSAL SCAN | Already successfully executed today.")
                return
        except Exception as e:
            logger.warning(f"Could not verify REVERSAL previous run status: {e}")
        
        try:
            logger.info(f"🔄 REVERSAL SCAN | Starting scan for {today_str}...")
            from database import upsert_scanner_health
            upsert_scanner_health("REVERSAL", status="QUEUED", error_msg="Waiting for global execution lock...")
            import reversal_scanner
            with scanner_execution_lock:
                total = reversal_scanner.start()   # returns int
                time.sleep(15)
            if total == 0:
                logger.info("🔄 REVERSAL | Zero alerts — no Telegram notification")
            else:
                logger.info(f"🔄 REVERSAL | Completed — {total} alert(s) sent")
            
            upsert_scanner_health(
                "REVERSAL",
                status="OK",
                last_success=datetime.now(IST).isoformat(),
                today_alerts=total,
                scheduled_for="21:00 IST"
            )
            try:
                from performance_tracker import trigger_performance_rebuild
                trigger_performance_rebuild()
            except Exception as pe:
                logger.error(f"Failed to trigger performance rebuild post-REVERSAL: {pe}")
            logger.info("✅ REVERSAL SCANNER | Completed successfully for today.")
            return
            
        except Exception as exc:
            if "actively running" in str(exc).lower():
                logger.info("⏳ REVERSAL scanner is already running in another process. Waiting...")
                time.sleep(60)
                continue
                
            retry_count += 1
            now = datetime.now(IST)
            
            if 0 <= now.hour < 6:
                logger.critical(f"⏰ MIDNIGHT PASSED — REVERSAL scanner force-stopping after {retry_count} retries")
                upsert_scanner_health("REVERSAL", status="DOWN", error_msg=f"Stopped at midnight after {retry_count} failed attempts", scheduled_for="21:00 IST")
                return
            
            logger.critical(f"💀 REVERSAL scanner crashed (attempt {retry_count}): {exc}. Retrying in 1 minute...")
            from database import upsert_scanner_health, insert_notification
            upsert_scanner_health("REVERSAL", status="DOWN", error_msg=str(exc)[:500], retry_count=retry_count, scheduled_for="21:00 IST")
            
            if retry_count == 1:
                try:
                    insert_notification(notif_type="scanner_down", title="🚨 REVERSAL Scanner CRASHED", message=f"Error: {str(exc)[:400]}. Auto-retrying.")
                except Exception:
                    pass
            
            wait_time = min(300, (2 ** retry_count) * random.uniform(0.5, 1.5))
            time.sleep(wait_time)


def run_evening_scanners():
    while True:
        block_until_watchlist_ready()
        wait_for_window("eod")
        wait_for_bhavcopy_or_fallback("EVENING_SCANNERS")
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")
        
        logger.info("🚀 Bhavcopy is ready! Spawning EOD and Reversal threads in parallel.")
        eod_thread = threading.Thread(target=_run_eod_with_retries, args=(today_str,), name="EODWorker")
        rev_thread = threading.Thread(target=_run_reversal_with_retries, args=(today_str,), name="ReversalWorker")
        
        eod_thread.start()
        rev_thread.start()
        
        eod_thread.join()
        rev_thread.join()
        
        logger.info("✅ Both Evening Scanners (EOD & Reversal) have finished execution for today.")
        # Sleep for a few hours to avoid retriggering until the window closes
        time.sleep(3600 * 6)


"""

new_content = pattern.sub(replacement, content)

# Also update the THREAD_TO_SCANNER in main.py
new_content = new_content.replace('"EODScanner":         run_eod_scanner,', '"EveningScanners":    run_evening_scanners,')
new_content = new_content.replace('    "ReversalScanner":    run_reversal_scanner,\n', '')

with open('app/main.py', 'w') as f:
    f.write(new_content)
