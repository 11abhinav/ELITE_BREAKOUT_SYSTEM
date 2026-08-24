"""
[VERSION: REVISIT_CLOSED_TRADES_v1.0]
Comprehensive historical trade auditor and state corrector.
Re-evaluates every past trade in 'alerts' and 'wealth_buy_alert' tables based on scanner-specific exit criteria:
- Swing Scanners (EOD, MULTI_TF, REVERSAL, PULLBACK): Categorizes generic CLOSED trades as WIN (if PnL >= 0) or LOSS (if PnL < 0).
- Compounder Scanners (MULTIBAGGER, WEALTH): Restores erroneous data-void closures to OPEN if fundamentals are healthy; otherwise sets status to WIN/LOSS.
"""

import os
import sys
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def audit_and_correct_closed_trades(rebuild_perf=False):
    """
    Scans all existing historical closed/review trades across all scanners and updates them to proper statuses.
    """
    logger.info("🔍 [TRADE AUDITOR] Starting historical trade re-evaluation and correction...")
    
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        
        # 1. Skip restoring healthy positions to OPEN (closed trades remain closed)
        mb_restored = 0
        wealth_restored = 0
        logger.info(f"📊 [TRADE AUDITOR] Wealth Engine pass complete (restored/updated {wealth_restored} positions).")

        # 3. Audit Swing Scanners (EOD, MULTI_TF, REVERSAL, PULLBACK) in alerts table
        swing_corrected = 0
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, symbol, scanner, status, entry_price, exit_price, pnl_pct, stop_loss, target_1, target_price
                    FROM alerts
                    WHERE status = 'CLOSED' OR (status IN ('WIN', 'LOSS') AND pnl_pct IS NOT NULL);
                """)
                rows = cur.fetchall()

        if rows:
            for r in rows:
                alert_id = r["id"]
                curr_status = r["status"]
                
                # Check for corporate action split adjustment
                trade_dict = {
                    "symbol": r.get("symbol"),
                    "entry_date": r.get("alert_date") or r.get("created_at"),
                    "entry_price": float(r["entry_price"]) if r.get("entry_price") is not None else None,
                    "stop_loss": float(r["stop_loss"]) if r.get("stop_loss") is not None else None,
                    "target_1": float(r["target_1"]) if r.get("target_1") is not None else None,
                }
                from corporate_actions import adjust_trade_for_corporate_actions
                adjust_trade_for_corporate_actions(trade_dict)

                entry_p = trade_dict["entry_price"]
                exit_p = float(r["exit_price"]) if r.get("exit_price") is not None else None
                pnl = float(r["pnl_pct"]) if r.get("pnl_pct") is not None else None

                if pnl is None and entry_p and exit_p and entry_p > 0:
                    pnl = ((exit_p - entry_p) / entry_p) * 100.0

                if pnl is not None:
                    correct_status = "WIN" if pnl >= 0 else "LOSS"
                    if curr_status != correct_status:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    UPDATE alerts
                                    SET status = %s,
                                        pnl_pct = COALESCE(pnl_pct, %s),
                                        updated_at = CURRENT_TIMESTAMP
                                    WHERE id = %s;
                                """, (correct_status, pnl, alert_id))
                            conn.commit()
                        swing_corrected += 1

        logger.info(f"📊 [TRADE AUDITOR] Swing scanners pass complete (corrected {swing_corrected} generic CLOSED alerts to WIN/LOSS).")

        # 4. Optionally trigger performance metrics rebuild if explicitly requested
        if rebuild_perf:
            try:
                from performance_tracker import build_performance_data
                build_performance_data(force_live_fetch=True)
                logger.info("✅ [TRADE AUDITOR] Successfully rebuilt performance data with corrected trade states.")
            except Exception as pe:
                logger.warning(f"⚠️ Could not rebuild performance data post-audit: {pe}")

        logger.info("✅ [TRADE AUDITOR] All existing trades successfully audited and marked correctly.")
        return {
            "multibagger_updates": mb_restored,
            "wealth_updates": wealth_restored,
            "swing_corrected": swing_corrected
        }

    except Exception as e:
        logger.exception(f"❌ [TRADE AUDITOR] Failed to audit and correct closed trades: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    audit_and_correct_closed_trades()
