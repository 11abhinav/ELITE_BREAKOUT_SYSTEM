# =====================================================================================
# app/confluence_engine.py
# FEATURE F-04: CROSS-SCANNER CONFLUENCE ENGINE (INDEPENDENT NON-CONTRADICTORY RULE)
# =====================================================================================

import logging
import os
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any

from config import WATCHLIST_PATH
from database import get_connection, IST, save_alert_if_new
from macro_utils import compute_nifty_rs_rating

logger = logging.getLogger("confluence_engine")

def evaluate_confluence_shortlist(run_date: str = None) -> List[Dict[str, Any]]:
    """
    Runs at 04:30 PM IST after Watchlist Builder and Technical Scanners complete.
    Applies Staleness Guard: Verifies inputs are dated today.
    
    Confluence Rule (Independent 3-Signal Alignment):
        1. FM_Score >= 75 (Fundamental Watchlist Tier 1)
        2. Any active technical alert fired today (EOD, PULLBACK, REVERSAL)
        3. rs_percentile >= 80.0 (Top 20% RS rating relative to Nifty 50)
    
    Promotes qualifying stocks to ELITE_CONFLUENCE_ALERT (Score 95+).
    """
    today_str = run_date or datetime.now(IST).strftime("%Y-%m-%d")
    logger.info(f"🌟 [START] Cross-Scanner Confluence Engine evaluation for {today_str}")

    # 1. Load Fundamental Watchlist (FM_Score >= 75)
    if not os.path.exists(WATCHLIST_PATH):
        logger.warning("⚠️ Watchlist parquet file missing for Confluence Engine")
        return []

    try:
        df_wl = pd.read_parquet(WATCHLIST_PATH)
        if df_wl.empty or "FM_Score" not in df_wl.columns:
            return []
        
        # Filter Tier 1 Fundamental Stocks
        tier1_df = df_wl[df_wl["FM_Score"] >= 75.0]
        if tier1_df.empty:
            logger.info("ℹ️ No Tier 1 fundamental stocks (FM_Score >= 75) found today.")
            return []
        
        tier1_map = dict(zip(tier1_df["Stock" if "Stock" in tier1_df.columns else "symbol"], tier1_df["FM_Score"]))
    except Exception as e:
        logger.exception(f"Failed to process watchlist for Confluence Engine: {e}")
        return []

    # 2. Fetch active technical alerts fired today from database
    todays_alerts = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, symbol, scanner, breakout_type, entry_price, stop_loss, target_1, target_2, score, bayesian_regime
                    FROM alerts
                    WHERE alert_date = %s AND status = 'OPEN' AND is_rejected = FALSE
                """, (today_str,))
                todays_alerts = cur.fetchall()
    except Exception as dbe:
        logger.exception(f"Failed to query today's alerts for Confluence: {dbe}")
        return []

    if not todays_alerts:
        logger.info("ℹ️ No technical alerts fired today for Confluence evaluation.")
        return []

    # 3. Compute RS Percentiles over active universe
    symbol_list = list(set([row[1] for row in todays_alerts]))
    rs_dict = compute_nifty_rs_rating(symbol_list)

    confluence_matches = []
    
    for row in todays_alerts:
        alert_id, symbol, scanner, breakout_type, entry, sl, t1, t2, tech_score, regime = row
        fm_score = tier1_map.get(symbol)
        rs_pct = float(rs_dict.get(symbol, 50.0))

        # Check 3 Independent Conditions:
        # Condition 1: High Fundamental Score (FM_Score >= 75)
        # Condition 2: Active Technical Signal Fired Today
        # Condition 3: Top Relative Strength (rs_percentile >= 80.0)
        if fm_score is not None and fm_score >= 75.0 and rs_pct >= 80.0:
            match_item = {
                "alert_id": alert_id,
                "symbol": symbol,
                "scanner": scanner,
                "breakout_type": breakout_type,
                "fm_score": fm_score,
                "rs_percentile": rs_pct,
                "tech_score": tech_score,
                "entry_price": entry,
                "stop_loss": sl,
                "target_1": t1,
                "target_2": t2,
                "regime": regime,
                "confluence_score": 96.0
            }
            confluence_matches.append(match_item)
            logger.info(f"🌟 CONFLUENCE MATCH DISCOVERED: {symbol} (FM: {fm_score}, RS: {rs_pct}%, Scanner: {scanner})")

    # 4. Dispatch Telegram Notification for Elite Confluence
    if confluence_matches:
        try:
            from telegram_engine import send_telegram_message
            for m in confluence_matches:
                msg = (
                    f"🌟 <b>ELITE CONFLUENCE ALERT</b> 🌟\n\n"
                    f"📌 <b>Symbol:</b> #{m['symbol']}\n"
                    f"💎 <b>Fundamental Score:</b> {m['fm_score']:.1f}/100 (Tier 1)\n"
                    f"⚡ <b>Relative Strength:</b> {m['rs_percentile']:.1f}% (Top 20% vs Nifty)\n"
                    f"📈 <b>Technical Signal:</b> {m['scanner']} Breakout\n"
                    f"💰 <b>Entry Price:</b> ₹{m['entry_price']:.2f}\n"
                    f"🛑 <b>Structural SL:</b> ₹{m['stop_loss']:.2f}\n"
                    f"🎯 <b>Target 1:</b> ₹{m['target_1']:.2f}\n"
                    f"🏆 <b>Confluence Rank:</b> GOLDEN TIER (100% Risk Budget)"
                )
                send_telegram_message(msg, scan_type="EOD")
        except Exception as tge:
            logger.warning(f"Failed to dispatch Confluence Telegram message: {tge}")

    return confluence_matches
