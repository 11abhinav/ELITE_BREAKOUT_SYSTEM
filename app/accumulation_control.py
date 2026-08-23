"""
app/accumulation_control.py

Admin Control Plane for ACCUMULATION_SCANNER_V1.
Provides cooperative cancellation checks, pause loops, manual run triggers, and state queries.
"""

import time
import logging
from typing import Dict, Any, Optional
from database import get_connection

logger = logging.getLogger(__name__)


class AccumulationControl:
    @staticmethod
    def get_global_control() -> Dict[str, Any]:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT global_enabled, global_paused, global_stop_requested, reason FROM system_control WHERE id = 1")
                    row = cur.fetchone()
                    if row:
                        return {
                            "global_enabled": bool(row[0]),
                            "global_paused": bool(row[1]),
                            "global_stop_requested": bool(row[2]),
                            "reason": row[3]
                        }
        except Exception as e:
            logger.warning(f"Could not query system_control: {e}")
        return {"global_enabled": True, "global_paused": False, "global_stop_requested": False, "reason": None}

    @staticmethod
    def get_scanner_control(scanner_name: str = "ACCUMULATION") -> Dict[str, Any]:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT enabled, paused, stop_requested, manual_run_requested, reason FROM scanner_control WHERE scanner_name = %s",
                        (scanner_name,)
                    )
                    row = cur.fetchone()
                    if row:
                        return {
                            "enabled": bool(row[0]),
                            "paused": bool(row[1]),
                            "stop_requested": bool(row[2]),
                            "manual_run_requested": bool(row[3]),
                            "reason": row[4]
                        }
        except Exception as e:
            logger.warning(f"Could not query scanner_control for {scanner_name}: {e}")
        return {"enabled": True, "paused": False, "stop_requested": False, "manual_run_requested": False, "reason": None}

    @classmethod
    def should_stop(cls, scanner_name: str = "ACCUMULATION") -> bool:
        """
        Cooperative Cancellation Check.
        Returns True if global_stop_requested OR scanner-level stop_requested is True.
        """
        global_ctrl = cls.get_global_control()
        if global_ctrl.get("global_stop_requested"):
            logger.info(f"🛑 [CONTROL] Stop requested via system_control (Global). Aborting {scanner_name}.")
            return True

        scanner_ctrl = cls.get_scanner_control(scanner_name)
        if scanner_ctrl.get("stop_requested"):
            logger.info(f"🛑 [CONTROL] Stop requested via scanner_control ({scanner_name}). Aborting.")
            return True

        return False

    @classmethod
    def is_paused(cls, scanner_name: str = "ACCUMULATION") -> bool:
        global_ctrl = cls.get_global_control()
        if global_ctrl.get("global_paused"):
            return True
        scanner_ctrl = cls.get_scanner_control(scanner_name)
        return scanner_ctrl.get("paused", False)

    @classmethod
    def wait_if_paused(cls, scanner_name: str = "ACCUMULATION", poll_interval: float = 2.0) -> bool:
        """
        Pause Loop handling.
        Returns True if resumed, or False if stop was requested during pause.
        """
        if not cls.is_paused(scanner_name):
            return True

        logger.info(f"⏸️ [CONTROL] {scanner_name} is PAUSED by Admin. Entering pause wait loop...")
        while cls.is_paused(scanner_name):
            if cls.should_stop(scanner_name):
                return False
            time.sleep(poll_interval)
        
        logger.info(f"▶️ [CONTROL] {scanner_name} RESUMED execution by Admin.")
        return True

    @classmethod
    def consume_manual_trigger(cls, scanner_name: str = "ACCUMULATION") -> bool:
        """
        Atomically checks and consumes a manual run request for scanner_name.
        """
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT manual_run_requested FROM scanner_control WHERE scanner_name = %s FOR UPDATE",
                        (scanner_name,)
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        cur.execute(
                            "UPDATE scanner_control SET manual_run_requested = FALSE, updated_at = NOW() WHERE scanner_name = %s",
                            (scanner_name,)
                        )
                        conn.commit()
                        return True
        except Exception as e:
            logger.warning(f"Could not consume manual trigger for {scanner_name}: {e}")
        return False

    @classmethod
    def update_control_state(
        cls,
        scanner_name: str,
        enabled: Optional[bool] = None,
        paused: Optional[bool] = None,
        stop_requested: Optional[bool] = None,
        manual_run_requested: Optional[bool] = None,
        reason: Optional[str] = None
    ) -> bool:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO scanner_control (scanner_name, enabled, paused, stop_requested, manual_run_requested, reason, updated_at)
                        VALUES (%s, COALESCE(%s, TRUE), COALESCE(%s, FALSE), COALESCE(%s, FALSE), COALESCE(%s, FALSE), %s, NOW())
                        ON CONFLICT (scanner_name) DO UPDATE SET
                            enabled = COALESCE(%s, scanner_control.enabled),
                            paused = COALESCE(%s, scanner_control.paused),
                            stop_requested = COALESCE(%s, scanner_control.stop_requested),
                            manual_run_requested = COALESCE(%s, scanner_control.manual_run_requested),
                            reason = COALESCE(%s, scanner_control.reason),
                            updated_at = NOW()
                        """,
                        (
                            scanner_name, enabled, paused, stop_requested, manual_run_requested, reason,
                            enabled, paused, stop_requested, manual_run_requested, reason
                        )
                    )
                    conn.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to update control state for {scanner_name}: {e}")
            return False
