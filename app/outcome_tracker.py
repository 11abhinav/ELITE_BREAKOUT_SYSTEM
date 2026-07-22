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


# =====================================================================================
# FEATURE F-13: ADVANCED OUTCOME ANALYTICS & FEATURE ATTRIBUTION ENGINE
# =====================================================================================

def _metric_confidence(n_trades: int) -> str:
    if n_trades >= 50:
        return "HIGH"
    elif n_trades >= 20:
        return "MEDIUM"
    return "LOW"


def _compute_metrics_block(rows: list) -> dict:
    n = len(rows)
    if n == 0:
        return {
            "trades": 0,
            "win_rate_pct": 0.0,
            "avg_realized_r": 0.0,
            "expectancy_r": 0.0,
            "avg_mfe_r": 0.0,
            "avg_mae_r": 0.0,
            "capture_efficiency_pct": 0.0,
            "confidence": "LOW"
        }

    wins = sum(1 for r in rows if (r.get("realized_rr") or 0) > 0 and r.get("exit_reason") != "AMBIGUOUS_SL_HIT")
    win_rate = round((wins / n) * 100, 1)

    realized_list = [float(r.get("realized_rr") or 0.0) for r in rows]
    mfe_list = [float(r.get("max_favorable_excursion_r") or 0.0) for r in rows]
    mae_list = [float(r.get("max_adverse_excursion_r") or 0.0) for r in rows]

    avg_realized = round(sum(realized_list) / n, 2)
    avg_mfe = round(sum(mfe_list) / n, 2)
    avg_mae = round(sum(mae_list) / n, 2)

    win_r_list = [r for r in realized_list if r > 0]
    loss_r_list = [r for r in realized_list if r <= 0]

    avg_win_r = (sum(win_r_list) / len(win_r_list)) if win_r_list else 0.0
    avg_loss_r = (sum(loss_r_list) / len(loss_r_list)) if loss_r_list else 0.0

    wr_frac = win_rate / 100.0
    expectancy = round((wr_frac * avg_win_r) + ((1.0 - wr_frac) * avg_loss_r), 2)
    capture_eff = round((avg_realized / avg_mfe * 100.0), 1) if avg_mfe > 0 else 0.0

    return {
        "trades": n,
        "win_rate_pct": win_rate,
        "avg_realized_r": avg_realized,
        "expectancy_r": expectancy,
        "avg_mfe_r": avg_mfe,
        "avg_mae_r": avg_mae,
        "capture_efficiency_pct": capture_eff,
        "confidence": _metric_confidence(n)
    }


def compute_advanced_outcome_analytics() -> Dict[str, Any]:
    """
    Feature F-13: Advanced Outcome Analytics & Feature Attribution (Preview Mode Edition).
    
    Reads alert_outcomes and calculates:
      1. Telemetry & Snapshot Coverage (Total completed trades, trades with snapshots, % coverage).
      2. Dual Confidence Architecture:
         - overall_confidence: LOW (<100 trades), MEDIUM (100-300 trades), HIGH (>300 trades)
         - per-metric confidence: LOW (<20 trades), MEDIUM (20-50 trades), HIGH (>50 trades)
      3. Capture Efficiency Diagnostic: Avg MFE R, Avg MAE R, Avg Realized R, Capture Efficiency %
      4. Feature Attribution: RS Leadership (RS >= 80 vs < 80), Sector Tailwind (sector_bonus > 0 vs 0), Regime Confluence
      5. Score Band Expectancy Table: Configurable via SCORE_BANDS in config.py
      6. Rolling Window Performance: 30d, 90d, 180d metrics including active trades in window.
    """
    from config import SCORE_BANDS

    records = []
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT o.alert_id, o.symbol, o.scanner, o.regime, o.regime_score, o.base_score,
                           o.rs_bonus, o.sector_bonus, o.rs_percentile, o.sector_name, o.rr_at_alert,
                           o.exit_reason, o.realized_rr, o.max_favorable_excursion_r, o.max_adverse_excursion_r,
                           o.exit_timestamp, o.alert_timestamp, a.score,
                           o.earnings_flag, o.days_to_earnings, o.earnings_severity
                    FROM alert_outcomes o
                    LEFT JOIN alerts a ON o.alert_id = a.id
                    WHERE o.exit_timestamp IS NOT NULL
                """)
                rows = cur.fetchall()
                for r in rows:
                    records.append({
                        "alert_id": r[0],
                        "symbol": r[1],
                        "scanner": r[2],
                        "regime": r[3],
                        "regime_score": float(r[4] or 0.0),
                        "base_score": r[5] or 0,
                        "rs_bonus": r[6] or 0,
                        "sector_bonus": r[7] or 0,
                        "rs_percentile": float(r[8] or 0.0),
                        "sector_name": r[9] or "",
                        "rr_at_alert": float(r[10] or 0.0),
                        "exit_reason": r[11] or "",
                        "realized_rr": float(r[12] or 0.0) if r[12] is not None else None,
                        "max_favorable_excursion_r": float(r[13] or 0.0),
                        "max_adverse_excursion_r": float(r[14] or 0.0),
                        "exit_timestamp": r[15],
                        "alert_timestamp": r[16],
                        "final_score": r[17] if r[17] is not None else ((r[5] or 0) + (r[6] or 0) + (r[7] or 0)),
                        "earnings_flag": bool(r[18]) if len(r) > 18 and r[18] is not None else False,
                        "days_to_earnings": r[19] if len(r) > 19 and r[19] is not None else 999,
                        "earnings_severity": r[20] if len(r) > 20 and r[20] is not None else "NONE"
                    })

    except Exception as e:
        logger.exception(f"Error fetching alert_outcomes for analytics: {e}")

    total_completed = len(records)

    # 1. Telemetry Coverage
    records_with_snapshot = [r for r in records if (r["rs_percentile"] > 0 or r["base_score"] > 0 or r["rs_bonus"] > 0 or r["sector_bonus"] > 0)]
    snapshot_count = len(records_with_snapshot)
    coverage_pct = round((snapshot_count / total_completed * 100.0), 1) if total_completed > 0 else 0.0

    # 2. Overall Confidence & Preview Mode Guard
    if total_completed >= 300:
        overall_confidence = "HIGH"
    elif total_completed >= 100:
        overall_confidence = "MEDIUM"
    else:
        overall_confidence = "LOW"

    is_preview = total_completed < 100

    # 3. Overall Execution Capture Efficiency
    overall_metrics = _compute_metrics_block(records)

    # 4. Feature Attribution Breakdowns
    rs_ge_80 = [r for r in records if r["rs_percentile"] >= 80.0 or r["rs_bonus"] >= 10]
    rs_lt_80 = [r for r in records if r["rs_percentile"] < 80.0 and r["rs_bonus"] < 10]

    sector_yes = [r for r in records if r["sector_bonus"] > 0]
    sector_no = [r for r in records if r["sector_bonus"] == 0]

    regime_bull = [r for r in records if r["regime"] in ("BULL", "STRONG_BULL")]
    regime_other = [r for r in records if r["regime"] not in ("BULL", "STRONG_BULL")]

    # 4b. Granular Earnings Risk Attribution Buckets
    ed_today = [r for r in records if r["days_to_earnings"] == 0]
    ed_1d_before = [r for r in records if r["days_to_earnings"] == 1]
    ed_2d_before = [r for r in records if r["days_to_earnings"] == 2]
    ed_3_5d_before = [r for r in records if 3 <= r["days_to_earnings"] <= 5]
    ed_1d_after = [r for r in records if r["days_to_earnings"] == -1]
    ed_normal = [r for r in records if r["days_to_earnings"] > 5 or r["days_to_earnings"] < -1 or not r["earnings_flag"]]

    feature_attribution = {
        "relative_strength": {
            "rs_ge_80": _compute_metrics_block(rs_ge_80),
            "rs_lt_80": _compute_metrics_block(rs_lt_80),
        },
        "sector_tailwind": {
            "tailwind_active": _compute_metrics_block(sector_yes),
            "tailwind_inactive": _compute_metrics_block(sector_no),
        },
        "macro_regime": {
            "bull_regime": _compute_metrics_block(regime_bull),
            "non_bull_regime": _compute_metrics_block(regime_other),
        },
        "earnings_window": {
            "results_today": _compute_metrics_block(ed_today),
            "one_day_before": _compute_metrics_block(ed_1d_before),
            "two_days_before": _compute_metrics_block(ed_2d_before),
            "three_to_five_before": _compute_metrics_block(ed_3_5d_before),
            "one_day_after": _compute_metrics_block(ed_1d_after),
            "normal_trades": _compute_metrics_block(ed_normal),
        }
    }


    # 5. Score Band Expectancy
    score_bands_result = []
    for low, high in SCORE_BANDS:
        band_records = [r for r in records if low <= r["final_score"] < high]
        block = _compute_metrics_block(band_records)
        score_bands_result.append({
            "band_label": f"{low}-{high-1}" if high < 100 else f"{low}+",
            "low_score": low,
            "high_score": high,
            "metrics": block
        })

    # 6. Rolling Window Validation (30d, 90d, 180d)
    now_dt = datetime.now(IST)
    def _filter_rolling(days: int):
        cutoff = now_dt - timedelta(days=days)
        return [
            r for r in records
            if r["exit_timestamp"] and (
                (isinstance(r["exit_timestamp"], datetime) and (r["exit_timestamp"].astimezone(IST) if r["exit_timestamp"].tzinfo else r["exit_timestamp"].replace(tzinfo=IST)) >= cutoff) or
                (isinstance(r["exit_timestamp"], str) and pd.to_datetime(r["exit_timestamp"]).tz_localize(IST) >= cutoff)
            )
        ]

    rolling_validation = {
        "30d": _compute_metrics_block(_filter_rolling(30)),
        "90d": _compute_metrics_block(_filter_rolling(90)),
        "180d": _compute_metrics_block(_filter_rolling(180)),
    }

    return {
        "timestamp": now_dt.isoformat(),
        "total_completed_trades": total_completed,
        "trades_with_snapshot": snapshot_count,
        "snapshot_coverage_pct": coverage_pct,
        "overall_confidence": overall_confidence,
        "is_preview_mode": is_preview,
        "overall_metrics": overall_metrics,
        "feature_attribution": feature_attribution,
        "score_bands": score_bands_result,
        "rolling_validation": rolling_validation
    }

