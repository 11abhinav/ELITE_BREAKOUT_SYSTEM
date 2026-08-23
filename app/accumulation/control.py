"""
app/accumulation/control.py — Dedicated Control Plane for ACCUMULATION_SCANNER_V1.
Manages isolated accumulation_control table state without touching shared system_control.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class AccumulationControlPlane:
    """Isolated Control Plane for ACCUMULATION_SCANNER_V1."""

    @staticmethod
    def get_control_state(conn=None) -> Dict[str, Any]:
        close_conn = False
        if conn is None:
            try:
                from database import get_db_connection
                conn = get_db_connection()
                close_conn = True
            except Exception as e:
                logger.warning(f"Failed DB connection in get_control_state: {e}")
                return {"enabled": True, "paused": False, "stop_requested": False, "manual_run_requested": False}

        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT accumulation_enabled, accumulation_paused, accumulation_stop_requested, accumulation_manual_run_requested, reason
                FROM accumulation_control
                WHERE scanner_name = 'ACCUMULATION_SCANNER_V1'
                LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                return {
                    "enabled": bool(row[0]),
                    "paused": bool(row[1]),
                    "stop_requested": bool(row[2]),
                    "manual_run_requested": bool(row[3]),
                    "reason": row[4],
                }
            else:
                # Seed default row
                cur.execute("""
                    INSERT INTO accumulation_control (scanner_name, accumulation_enabled)
                    VALUES ('ACCUMULATION_SCANNER_V1', TRUE)
                    ON CONFLICT (scanner_name) DO NOTHING;
                """)
                conn.commit()
                return {"enabled": True, "paused": False, "stop_requested": False, "manual_run_requested": False}
        except Exception as e:
            logger.error(f"Error reading accumulation_control state: {e}")
            return {"enabled": True, "paused": False, "stop_requested": False, "manual_run_requested": False}
        finally:
            if close_conn and conn:
                try: conn.close()
                except: pass

    @staticmethod
    def update_control_state(paused: Optional[bool] = None, stop_requested: Optional[bool] = None, manual_run: Optional[bool] = None, reason: Optional[str] = None, conn=None) -> bool:
        close_conn = False
        if conn is None:
            try:
                from database import get_db_connection
                conn = get_db_connection()
                close_conn = True
            except Exception as e:
                logger.error(f"Failed DB connection in update_control_state: {e}")
                return False

        try:
            cur = conn.cursor()
            is_sqlite = type(conn).__module__.startswith("sqlite3")
            ph = "?" if is_sqlite else "%s"
            now_func = "CURRENT_TIMESTAMP" if is_sqlite else "NOW()"

            updates = []
            params = []
            if paused is not None:
                updates.append(f"accumulation_paused = {ph}")
                params.append(paused)
            if stop_requested is not None:
                updates.append(f"accumulation_stop_requested = {ph}")
                params.append(stop_requested)
            if manual_run is not None:
                updates.append(f"accumulation_manual_run_requested = {ph}")
                params.append(manual_run)
            if reason is not None:
                updates.append(f"reason = {ph}")
                params.append(reason)
            updates.append(f"updated_at = {now_func}")

            if not updates:
                return True

            query = f"""
                UPDATE accumulation_control
                SET {", ".join(updates)}
                WHERE scanner_name = 'ACCUMULATION_SCANNER_V1';
            """
            cur.execute(query, tuple(params))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error updating accumulation_control state: {e}")
            if hasattr(conn, "rollback"): conn.rollback()
            return False
        finally:
            if close_conn and conn:
                try: conn.close()
                except: pass
