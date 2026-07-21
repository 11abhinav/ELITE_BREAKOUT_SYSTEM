import pandas as pd
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

FUNNEL_CSV_PATH = "data/funnel_telemetry.csv"

def log_funnel_metrics(scanner_name: str, regime: str, total_candidates: int, rejection_counts: dict, total_alerts: int):
    liquidity_keys = {"stale_data", "no_data", "insufficient_bars", "penny_stock", "stale_30m", "stale_15m", "stale_5m"}
    trend_keys = {"below_ema20", "below_sma50", "bearish_candle", "no_structural_breakout", "weak_close_pos", "upper_wick", "pre_breakout_weak", "price_not_above_bb_mid", "below_sma50_or_ema20", "below_ema200", "pb_fail_engulf", "demoted"}
    momentum_keys = {"weak_adx", "no_atr_expansion", "base_too_wide", "gap_extended", "no_macd_cross", "rsi_range", "obv_weak"}
    volume_keys = {"zero_avg_volume", "low_volume", "low_avg_volume", "delivery_weak", "pb_fail_vol"}
    rr_keys = {"low_score", "low_rr", "rr_ratio", "reward_potential", "rr_rejections"}
    
    # Catch any generic or unknown ones in "logic" bucket
    logic_drop = 0
    liquidity_drop = 0
    trend_drop = 0
    momentum_drop = 0
    volume_drop = 0
    rr_drop = 0

    for k, count in rejection_counts.items():
        if k in liquidity_keys: liquidity_drop += count
        elif k in trend_keys: trend_drop += count
        elif k in momentum_keys: momentum_drop += count
        elif k in volume_keys: volume_drop += count
        elif k in rr_keys: rr_drop += count
        else: logic_drop += count
        
    liquidity_survivors = max(0, total_candidates - liquidity_drop)
    trend_survivors = max(0, liquidity_survivors - trend_drop)
    momentum_survivors = max(0, trend_survivors - momentum_drop)
    volume_survivors = max(0, momentum_survivors - volume_drop)
    rr_survivors = max(0, volume_survivors - rr_drop)
    
    date_str = datetime.now(IST).strftime("%Y-%m-%d")
    
    data = {
        "Date": date_str,
        "Scanner": scanner_name,
        "Regime": regime,
        "Universe": total_candidates,
        "Liquidity_Drop": liquidity_drop,
        "Trend_Drop": trend_drop,
        "Momentum_Drop": momentum_drop,
        "Volume_Drop": volume_drop,
        "RR_Drop": rr_drop,
        "Other_Drop": logic_drop,
        "Alerts": total_alerts
    }
    
    try:
        os.makedirs(os.path.dirname(FUNNEL_CSV_PATH), exist_ok=True)
        file_exists = os.path.exists(FUNNEL_CSV_PATH)
        df = pd.DataFrame([data])
        df.to_csv(FUNNEL_CSV_PATH, mode='a', header=not file_exists, index=False)
        logger.info(f"📊 Funnel Telemetry Logged: Universe={total_candidates} -> L_Drop={liquidity_drop} -> T_Drop={trend_drop} -> M_Drop={momentum_drop} -> V_Drop={volume_drop} -> RR_Drop={rr_drop} -> Alerts={total_alerts}")
    except Exception as e:
        logger.error(f"Failed to write funnel telemetry: {e}")
