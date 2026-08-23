"""
app/accumulation_health.py

Dedicated Scanner Health Engine for ACCUMULATION_SCANNER_V1.
Tracks lifecycle state transitions, processing performance, and error states.
"""

import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from database import get_connection

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


class AccumulationHealthTracker:
    def __init__(self, run_id: str, scanner: str = "ACCUMULATION"):
        self.run_id = run_id
        self.scanner = scanner
        self.status = "IDLE"
        self.lifecycle_state = "IDLE"
        
        self.requested_symbols = 0
        self.processed_symbols = 0
        self.valid_symbols = 0
        self.rejected_symbols = 0
        self.candidates = 0
        self.alerts = 0
        
        self.raw_data_errors = 0
        self.stale_symbols = 0
        self.invalid_symbols = 0
        
        self.cache_hits = 0
        self.cache_misses = 0
        self.bytes_fetched = 0
        
        self.api_latency_ms = 0.0
        self.calculation_time_ms = 0.0
        self.persistence_time_ms = 0.0
        
        self.started_at = datetime.now(IST)
        self.completed_at: Optional[datetime] = None
        self.duration_seconds = 0.0
        
        self.pause_requested = False
        self.stop_requested = False
        self.last_error: Optional[str] = None
        self.error_count = 0

    def transition(self, new_state: str, status: str = "RUNNING"):
        self.lifecycle_state = new_state
        self.status = status
        logger.info(f"🔄 [{self.scanner} HEALTH] Transition -> {new_state} (status={status})")
        self.persist()

    def record_metrics(
        self,
        processed_inc: int = 0,
        valid_inc: int = 0,
        rejected_inc: int = 0,
        candidates_inc: int = 0,
        alerts_inc: int = 0,
        stale_inc: int = 0,
        errors_inc: int = 0
    ):
        self.processed_symbols += processed_inc
        self.valid_symbols += valid_inc
        self.rejected_symbols += rejected_inc
        self.candidates += candidates_inc
        self.alerts += alerts_inc
        self.stale_symbols += stale_inc
        self.error_count += errors_inc
        self.persist()

    def complete(self, status: str = "OK"):
        self.completed_at = datetime.now(IST)
        self.duration_seconds = round((self.completed_at - self.started_at).total_seconds(), 2)
        self.lifecycle_state = "COMPLETED"
        self.status = status
        logger.info(f"✅ [{self.scanner} HEALTH] Completed run {self.run_id} in {self.duration_seconds}s | Candidates={self.candidates}, Alerts={self.alerts}")
        self.persist()

    def stop(self, reason: str = "ADMIN_STOP"):
        self.completed_at = datetime.now(IST)
        self.duration_seconds = round((self.completed_at - self.started_at).total_seconds(), 2)
        self.lifecycle_state = "STOPPED"
        self.status = "STOPPED"
        self.stop_requested = True
        self.last_error = f"Stopped: {reason}"
        logger.warning(f"🛑 [{self.scanner} HEALTH] Stopped run {self.run_id} | Reason: {reason}")
        self.persist()

    def fail(self, exc: Exception):
        self.completed_at = datetime.now(IST)
        self.duration_seconds = round((self.completed_at - self.started_at).total_seconds(), 2)
        self.lifecycle_state = "FAILED"
        self.status = "DOWN"
        self.error_count += 1
        self.last_error = str(exc)[:500]
        logger.error(f"❌ [{self.scanner} HEALTH] Failed run {self.run_id}: {exc}")
        self.persist()

    def persist(self):
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO accumulation_health (
                            run_id, scanner, status, lifecycle_state,
                            requested_symbols, processed_symbols, valid_symbols, rejected_symbols,
                            candidates, alerts, raw_data_errors, stale_symbols, invalid_symbols,
                            cache_hits, cache_misses, bytes_fetched, api_latency_ms, calculation_time_ms, persistence_time_ms,
                            started_at, completed_at, duration_seconds, pause_requested, stop_requested, last_error, error_count
                        ) VALUES (
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            self.run_id, self.scanner, self.status, self.lifecycle_state,
                            self.requested_symbols, self.processed_symbols, self.valid_symbols, self.rejected_symbols,
                            self.candidates, self.alerts, self.raw_data_errors, self.stale_symbols, self.invalid_symbols,
                            self.cache_hits, self.cache_misses, self.bytes_fetched, self.api_latency_ms, self.calculation_time_ms, self.persistence_time_ms,
                            self.started_at, self.completed_at, self.duration_seconds, self.pause_requested, self.stop_requested, self.last_error, self.error_count
                        )
                    )
                    conn.commit()
        except Exception as e:
            logger.warning(f"Could not persist accumulation_health: {e}")
