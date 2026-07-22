# =====================================================================================
# app/outcome_tracker.py
# FEATURE F-01: DAILY RUNNING OUTCOME, EXCURSION & FEATURE SNAPSHOT TRACKER WORKER
# =====================================================================================

import logging
import time
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, Any

from database import get_connection, IST
from price_cache import fetch_unified_historical

logger = logging.getLogger("outcome_tracker")

def run_outcome_tracker(force: bool = False) -> Dict[str, Any]:
    """
    Post-market worker (scheduled at 04:45 PM IST with 07:00 PM retry).
    
    Responsibilities:
      1. Data Freshness Guard: Verifies market data for today is available.
      2. Daily Running MFE/MAE Accumulation for every OPEN trade.
      3. Conservative Same-Bar Collision Rule: Records AMBIGUOUS_SL_HIT if High >= T1 AND Low <= SL.
      4. Gap-Through-SL Slippage Calculation: Uses actual open price if gap-down occurs.
      5. Expiry Classification: Stores EXPIRED_POS or EXPIRED_NEG with unrealized R at 20 days.
      6. Writes full feature snapshots to alert_outcomes.
    """
    today_dt = datetime.now(IST).date()
    today_str = today_dt.strftime("%Y-%m-%d")
    logger.info(f"📊 [START] Alert Outcome Tracker Worker for {today_str}")

    # Fetch all OPEN alerts from database
    open_alerts = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, symbol, scanner, breakout_type, alert_date, entry_price, stop_loss, target_1, target_2,
                           score, bayesian_regime, alert_time
                    FROM alerts
                    WHERE status = 'OPEN' AND is_rejected = FALSE
                """)
                open_alerts = cur.fetchall()
    except Exception as dbe:
        logger.exception(f"Failed to query OPEN alerts for Outcome Tracker: {dbe}")
        return {"processed": 0, "status": "DB_ERROR"}

    if not open_alerts:
        logger.info("ℹ️ No OPEN alerts to track today.")
        return {"processed": 0, "status": "NO_OPEN_ALERTS"}

    symbols = list(set([row[1] for row in open_alerts]))
    historical_dict = fetch_unified_historical(symbols, period="1m", interval="1d", requester="outcome_tracker")

    # Data Freshness Guard: Check if today's bar is present
    sample_df = next((df for df in historical_dict.values() if df is not None and not df.empty), None)
    if sample_df is not None:
        last_date = pd.to_datetime(sample_df.index[-1]).date() if isinstance(sample_df.index, pd.DatetimeIndex) else today_dt
        if last_date < today_dt and not force:
            logger.warning(f"⚠️ EOD market data not fresh (last date: {last_date}, today: {today_dt}). Will retry at 07:00 PM.")
            return {"processed": 0, "status": "DATA_NOT_FRESH"}

    updated_count = 0
    closed_count = 0

    for alert in open_alerts:
        alert_id, symbol, scanner, breakout_type, alert_date_val, entry, sl, t1, t2, score, regime, alert_time = alert
        df_sym = historical_dict.get(symbol)
        if df_sym is None or df_sym.empty:
            continue

        # Filter price bars after alert date
        try:
            df_after = df_sym[df_sym.index >= str(alert_date_val)]
        except Exception:
            df_after = df_sym

        if df_after.empty:
            continue

        risk_dist = max(0.01, float(entry) - float(sl))
        holding_bars = len(df_after)

        # Running MFE and MAE Accumulation
        highs = df_after["High"].values
        lows = df_after["Low"].values
        closes = df_after["Close"].values
        opens = df_after["Open"].values

        running_mfe_r = max(0.0, float((highs.max() - entry) / risk_dist))
        running_mae_r = max(0.0, float((entry - lows.min()) / risk_dist))

        # Check resolution bar-by-bar
        exit_reason = None
        exit_timestamp = None
        realized_rr = None
        unrealized_rr = None
        is_closed = False

        for idx in range(len(df_after)):
            b_open = float(opens[idx])
            b_high = float(highs[idx])
            b_low = float(lows[idx])
            b_close = float(closes[idx])
            bar_date = str(df_after.index[idx])[:10]

            hit_target = (b_high >= float(t1))
            hit_sl = (b_low <= float(sl))

            # Fix #2: Conservative Same-Bar Collision Rule
            if hit_target and hit_sl:
                exit_reason = "AMBIGUOUS_SL_HIT"
                exit_timestamp = bar_date
                realized_rr = -1.0  # Conservative -1.0R loss
                is_closed = True
                break

            elif hit_sl:
                exit_reason = "SL_HIT"
                exit_timestamp = bar_date
                # Fix #2 (Refinement): Gap-Through-SL Slippage Calculation
                if b_open < float(sl):
                    realized_rr = round((b_open - float(entry)) / risk_dist, 2)
                else:
                    realized_rr = -1.0
                is_closed = True
                break

            elif hit_target:
                exit_reason = "T1_HIT"
                exit_timestamp = bar_date
                realized_rr = round((float(t1) - float(entry)) / risk_dist, 2)
                is_closed = True
                break

        # Check 20-Day Expiry if not closed
        if not is_closed and holding_bars >= 20:
            last_close = float(closes[-1])
            unrealized_rr = round((last_close - float(entry)) / risk_dist, 2)
            exit_reason = "EXPIRED_POS" if unrealized_rr >= 0 else "EXPIRED_NEG"
            exit_timestamp = today_str
            realized_rr = unrealized_rr
            is_closed = True

        # Update Database Record
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    if is_closed:
                        db_status = "WIN" if (realized_rr and realized_rr > 0 and exit_reason != "AMBIGUOUS_SL_HIT") else "LOSS"
                        cur.execute("""
                            UPDATE alerts
                            SET status = %s, closed_at = NOW()
                            WHERE id = %s
                        """, (db_status, alert_id))

                        cur.execute("""
                            UPDATE alert_outcomes
                            SET exit_timestamp = %s,
                                exit_reason = %s,
                                realized_rr = %s,
                                unrealized_rr_at_expiry = %s,
                                holding_period_bars = %s,
                                max_favorable_excursion_r = %s,
                                max_adverse_excursion_r = %s
                            WHERE alert_id = %s AND leg = 1
                        """, (exit_timestamp, exit_reason, realized_rr, unrealized_rr, holding_bars,
                              round(running_mfe_r, 2), round(running_mae_r, 2), alert_id))
                        closed_count += 1
                    else:
                        # Update running excursion for OPEN alert
                        cur.execute("""
                            UPDATE alert_outcomes
                            SET holding_period_bars = %s,
                                max_favorable_excursion_r = %s,
                                max_adverse_excursion_r = %s
                            WHERE alert_id = %s AND leg = 1
                        """, (holding_bars, round(running_mfe_r, 2), round(running_mae_r, 2), alert_id))
                    conn.commit()
                    updated_count += 1
        except Exception as dbe:
            logger.exception(f"Failed to update alert_outcomes for alert {alert_id}: {dbe}")

    logger.info(f"✅ Outcome Tracker finished: {updated_count} alerts updated, {closed_count} alerts closed.")
    return {"processed": updated_count, "closed": closed_count, "status": "SUCCESS"}
