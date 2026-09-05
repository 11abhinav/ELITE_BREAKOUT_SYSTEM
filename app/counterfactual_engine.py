# =====================================================================================
# app/counterfactual_engine.py
#
# Physically Isolated Counterfactual Simulation Engine
# Replays historical ticks for rejected candidates to simulate hypothetical trade outcomes.
# GUARANTEE: NO imports or connections to active live/broker trading modules.
# =====================================================================================

import logging
import json
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import yfinance as yf
import pandas as pd

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# Hardcoded DB connection settings to remain isolated from database.py config imports
DB_DSN = "postgresql://postgres:postgres@localhost:5432/trade_system"

def get_isolated_connection():
    # Attempt to read DSN from environment variable first
    import os
    dsn = os.getenv("DATABASE_URL", DB_DSN)
    return psycopg2.connect(dsn, cursor_factory=RealDictCursor)

def run_counterfactual_simulation() -> int:
    """
    Query PENDING counterfactuals, simulate hypothetical trades, and update outcomes.
    Returns count of simulated records.
    """
    logger.info("⏳ Starting isolated counterfactual simulation run...")
    completed_count = 0
    
    try:
        conn = get_isolated_connection()
    except Exception as e:
        logger.error(f"❌ Counterfactual Engine failed to connect to database: {e}")
        return 0
        
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, scanner, decision_timestamp,
                       counterfactual_entry_price, counterfactual_stop_loss,
                       counterfactual_target_1, counterfactual_target_2, counterfactual_target_3,
                       counterfactual_entry_mode
                FROM scanner_evaluation_log
                WHERE counterfactual_status = 'PENDING'
                  AND counterfactual_exclusion_reason IS NULL
                LIMIT 50;
            """)
            pending = cur.fetchall()
            
        if not pending:
            logger.info("✅ No pending counterfactual records found.")
            return 0
            
        for row in pending:
            rec_id = row["id"]
            symbol = row["symbol"]
            entry_p = row["counterfactual_entry_price"]
            sl_p = row["counterfactual_stop_loss"]
            t1 = row["counterfactual_target_1"]
            t2 = row["counterfactual_target_2"]
            t3 = row["counterfactual_target_3"]
            mode = row["counterfactual_entry_mode"] or "BREAKOUT_TRIGGER"
            dec_time = row["decision_timestamp"]
            
            if not entry_p or not sl_p or not t1:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE scanner_evaluation_log
                        SET counterfactual_status = 'NOT_ELIGIBLE',
                            counterfactual_exclusion_reason = 'NO_VALID_SETUP_PRICE',
                            counterfactual_generated_at = NOW()
                        WHERE id = %s
                    """, (rec_id,))
                conn.commit()
                continue
                
            # Fetch subsequent price history from decision_timestamp to today
            try:
                hist = None
                try:
                    from price_cache import get_cached_df
                    cached_hist = get_cached_df(symbol, interval="1d", period="1y")
                    if cached_hist is not None and not cached_hist.empty:
                        start_date_str = dec_time.strftime("%Y-%m-%d")
                        if "Date" in cached_hist.columns:
                            hist = cached_hist[cached_hist["Date"] >= start_date_str].copy()
                        elif isinstance(cached_hist.index, pd.DatetimeIndex):
                            hist = cached_hist[cached_hist.index >= dec_time].copy()
                except Exception:
                    pass

                if hist is None or hist.empty or len(hist) < 2:
                    ticker = yf.Ticker(f"{symbol}.NS") # Assume NSE
                    start_date = dec_time.strftime("%Y-%m-%d")
                    end_date = (datetime.now(IST) + timedelta(days=1)).strftime("%Y-%m-%d")
                    
                    hist = ticker.history(start=start_date, end=end_date, interval="1d")
                    if hist.empty or len(hist) < 2:
                        ticker = yf.Ticker(symbol) # Fallback to global symbol
                        hist = ticker.history(start=start_date, end=end_date, interval="1d")
                    
                if hist.empty:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE scanner_evaluation_log
                            SET counterfactual_status = 'INSUFFICIENT_DATA',
                                counterfactual_generated_at = NOW()
                            WHERE id = %s
                        """, (rec_id,))
                    conn.commit()
                    continue
                    
                # Replay loop
                triggered = False
                triggered_idx = None
                exit_idx = None
                
                max_fav = 0.0
                max_adv = 0.0
                risk_unit = abs(entry_p - sl_p)
                if risk_unit == 0:
                    risk_unit = entry_p * 0.05
                    
                t1_hit = False
                t2_hit = False
                t3_hit = False
                sl_hit = False
                
                bars_to_t1 = None
                bars_to_sl = None
                
                ticks = []
                for ts, r in hist.iterrows():
                    ticks.append((ts, float(r["Open"]), float(r["Low"]), float(r["High"]), float(r["Close"])))
                    
                for idx, (ts, op, lo, hi, cl) in enumerate(ticks):
                    if not triggered:
                        if mode == "BREAKOUT_TRIGGER" and hi >= entry_p:
                            triggered = True
                            triggered_idx = idx
                        elif mode == "LIMIT_PULLBACK" and lo <= entry_p:
                            triggered = True
                            triggered_idx = idx
                        continue
                        
                    # Evaluate excursions & exits once triggered
                    if triggered:
                        # Assuming LONG only
                        fav = hi - entry_p
                        adv = entry_p - lo
                        
                        max_fav = max(max_fav, fav)
                        max_adv = max(max_adv, adv)
                        
                        # Evaluate exits
                        # SL priority (worst case scenario)
                        if lo <= sl_p:
                            sl_hit = True
                            exit_idx = idx
                            bars_to_sl = idx - triggered_idx
                            break
                        # Targets
                        if hi >= t1:
                            t1_hit = True
                            bars_to_t1 = idx - triggered_idx
                        if t2 and hi >= t2:
                            t2_hit = True
                        if t3 and hi >= t3:
                            t3_hit = True
                            exit_idx = idx
                            break
                            
                # Compile outcomes
                cf_mfe_r = round(max_fav / risk_unit, 2)
                cf_mae_r = round(max_adv / risk_unit, 2)
                
                # Determine realized R
                if sl_hit:
                    cf_realized = round(-1.0, 2)
                elif t3_hit:
                    cf_realized = round((t3 - entry_p) / risk_unit, 2)
                elif t1_hit:
                    cf_realized = round((t1 - entry_p) / risk_unit, 2)
                else:
                    # MTM open
                    last_close = ticks[-1][4]
                    cf_realized = round((last_close - entry_p) / risk_unit, 2)
                    
                labels = {
                    "A": bool(t1_hit),
                    "B": bool(t2_hit),
                    "C": bool(cf_mfe_r >= 2.0 and cf_mae_r < 1.0),
                    "D": bool(t1_hit and bars_to_t1 is not None and bars_to_t1 <= 5),
                    "E": bool(sl_hit and not t1_hit)
                }
                
                status_val = 'COMPLETED' if (sl_hit or t3_hit or len(ticks) > 10) else 'PENDING'
                
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE scanner_evaluation_log
                        SET counterfactual_mfe_r = %s,
                            counterfactual_mae_r = %s,
                            counterfactual_realized_r = %s,
                            counterfactual_outcome_labels = %s,
                            counterfactual_status = %s,
                            counterfactual_generated_at = NOW()
                        WHERE id = %s
                    """, (cf_mfe_r, cf_mae_r, cf_realized, json.dumps(labels), status_val, rec_id))
                conn.commit()
                completed_count += 1
                
            except Exception as row_err:
                logger.error(f"Error processing counterfactual for {symbol}: {row_err}")
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE scanner_evaluation_log
                        SET counterfactual_status = 'ERROR',
                            counterfactual_generated_at = NOW()
                        WHERE id = %s
                    """, (rec_id,))
                conn.commit()
                
    finally:
        conn.close()
        
    logger.info(f"✅ Completed {completed_count} counterfactual simulations.")
    return completed_count
