import pandas as pd
import os
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

FUNNEL_CSV_PATH = "data/funnel_telemetry.csv"

def _bucketize(rejection_counts: dict) -> dict:
    """Classify raw rejection keys into semantic buckets and return per-bucket totals."""
    liquidity_keys = {"stale_data", "no_data", "insufficient_bars", "penny_stock", "stale_30m", "stale_15m", "stale_5m", "missing_col", "zero_candle_range", "no_data"}
    trend_keys = {"below_ema20", "below_sma50", "bearish_candle", "no_structural_breakout", "weak_close_pos", "upper_wick", "pre_breakout_weak", "price_not_above_bb_mid", "below_sma50_or_ema20", "below_ema200", "pb_fail_engulf", "demoted", "ema_filter", "not_above_sma50", "no_uptrend"}
    momentum_keys = {"weak_adx", "no_atr_expansion", "base_too_wide", "gap_extended", "no_macd_cross", "rsi_range", "obv_weak", "failed_pattern", "rsi_curl", "rsi_lookback"}
    volume_keys = {"zero_avg_volume", "low_volume", "low_avg_volume", "delivery_weak", "pb_fail_vol", "low_liquidity"}
    rr_keys = {"low_score", "low_rr", "rr_ratio", "reward_potential", "rr_rejections"}

    buckets = {"liquidity": 0, "trend": 0, "momentum": 0, "volume": 0, "rr": 0, "logic": 0}
    for k, count in rejection_counts.items():
        if k in liquidity_keys: buckets["liquidity"] += count
        elif k in trend_keys: buckets["trend"] += count
        elif k in momentum_keys: buckets["momentum"] += count
        elif k in volume_keys: buckets["volume"] += count
        elif k in rr_keys: buckets["rr"] += count
        else: buckets["logic"] += count
    return buckets


def log_funnel_metrics(scanner_name: str, regime: str, total_candidates: int, rejection_counts: dict, total_alerts: int):
    buckets = _bucketize(rejection_counts)

    date_str = datetime.now(IST).strftime("%Y-%m-%d")

    data = {
        "Date": date_str,
        "Scanner": scanner_name,
        "Regime": regime,
        "Universe": total_candidates,
        "Liquidity_Drop": buckets["liquidity"],
        "Trend_Drop": buckets["trend"],
        "Momentum_Drop": buckets["momentum"],
        "Volume_Drop": buckets["volume"],
        "RR_Drop": buckets["rr"],
        "Other_Drop": buckets["logic"],
        "Alerts": total_alerts
    }

    try:
        os.makedirs(os.path.dirname(FUNNEL_CSV_PATH), exist_ok=True)
        file_exists = os.path.exists(FUNNEL_CSV_PATH)
        df = pd.DataFrame([data])
        df.to_csv(FUNNEL_CSV_PATH, mode='a', header=not file_exists, index=False)
    except Exception as e:
        logger.error(f"Failed to write funnel telemetry: {e}")

    # ── Per-condition breakdown (emitted every run) ──
    total_rejected = total_candidates - total_alerts
    if total_candidates > 0:
        lines = [f"📊 [{scanner_name}] Per-Condition Funnel ({total_candidates} universe → {total_alerts} alerts, {total_rejected} killed):"]
        for k, count in sorted(rejection_counts.items(), key=lambda x: -x[1]):
            if count > 0:
                pct = (count / total_candidates) * 100
                lines.append(f"   {k:<30} → {count:>5} ({pct:5.1f}%)")
        logger.info("\n".join(lines))


def log_condition_rejection(scanner_name: str, condition_name: str, count: int, total: int):
    """Log a single condition's rejection impact. Called from per-scanner telemetry hooks."""
    if count > 0 and total > 0:
        pct = (count / total) * 100
        logger.info(f"📊 [{scanner_name}] Condition '{condition_name}' killed {count}/{total} ({pct:.1f}%)")
