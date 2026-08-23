"""
app/accumulation/health.py — Health Tracking Engine for ACCUMULATION_SCANNER_V1.
Records 16 lifecycle states, heartbeat timestamps, duration metrics, and certification status.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class AccumulationHealthTracker:
    """Isolated Health Tracker for ACCUMULATION_SCANNER_V1."""

    @staticmethod
    def record_heartbeat(run_id: str, lifecycle_state: str = "RUNNING", current_phase: str = "INGESTION", conn=None) -> bool:
        close_conn = False
        if conn is None:
            try:
                from database import get_db_connection
                conn = get_db_connection()
                close_conn = True
            except Exception as e:
                logger.warning(f"Could not connect DB for health heartbeat: {e}")
                return False

        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO accumulation_health (
                    run_id, scanner, status, lifecycle_state, current_phase, last_heartbeat, certification_status
                ) VALUES (%s, 'ACCUMULATION_SCANNER_V1', 'HEALTHY', %s, %s, NOW(), 'CERTIFIED')
                ON CONFLICT (id) DO UPDATE SET
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    current_phase = EXCLUDED.current_phase,
                    last_heartbeat = NOW();
            """, (run_id, lifecycle_state, current_phase))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error recording accumulation health heartbeat: {e}")
            if hasattr(conn, "rollback"): conn.rollback()
            return False
        finally:
            if close_conn and conn:
                try: conn.close()
                except: pass

    @staticmethod
    def record_success(run_id: str, duration_seconds: float, conn=None) -> bool:
        close_conn = False
        if conn is None:
            try:
                from database import get_db_connection
                conn = get_db_connection()
                close_conn = True
            except Exception as e:
                logger.warning(f"Could not connect DB for health success: {e}")
                return False

        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO accumulation_health (
                    run_id, scanner, status, lifecycle_state, current_phase, last_heartbeat, last_success_at, completed_at, duration_seconds, certification_status
                ) VALUES (%s, 'ACCUMULATION_SCANNER_V1', 'HEALTHY', 'COMPLETED', 'FINISHED', NOW(), NOW(), NOW(), %s, 'CERTIFIED');
            """, (run_id, duration_seconds))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error recording accumulation health success: {e}")
            if hasattr(conn, "rollback"): conn.rollback()
            return False
        finally:
            if close_conn and conn:
                try: conn.close()
                except: pass

    @staticmethod
    def record_failure(run_id: str, failure_reason: str, conn=None) -> bool:
        close_conn = False
        if conn is None:
            try:
                from database import get_db_connection
                conn = get_db_connection()
                close_conn = True
            except Exception as e:
                logger.warning(f"Could not connect DB for health failure: {e}")
                return False

        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO accumulation_health (
                    run_id, scanner, status, lifecycle_state, current_phase, last_heartbeat, last_failure_at, failure_reason, completed_at, certification_status
                ) VALUES (%s, 'ACCUMULATION_SCANNER_V1', 'DEGRADED', 'FAILED', 'ERROR', NOW(), NOW(), %s, NOW(), 'UNCERTIFIED');
            """, (run_id, failure_reason))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error recording accumulation health failure: {e}")
            if hasattr(conn, "rollback"): conn.rollback()
            return False
        finally:
            if close_conn and conn:
                try: conn.close()
                except: pass
