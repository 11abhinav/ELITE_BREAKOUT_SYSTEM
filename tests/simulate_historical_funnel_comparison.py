#!/usr/bin/env python3
"""
Historical EOD Funnel Simulation & Differential Analysis (BEFORE vs AFTER).
Simulates the entire EOD scanner cascade on real historical data.
"""

import os
import sys
import glob
from collections import defaultdict
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from config import (
    EOD_CONFIG,
    EOD_ADVANCED_CONFIG,
    SCORE_THRESHOLDS,
    MIN_NATURAL_RR,
    MIN_STOCK_PRICE,
    REGIME_POLICIES
)
from technical_indicators import apply_indicators
from breakout_engine import detect_breakouts
from scoring_engine import calculate_score
from sl_target_helper import compute_sl_and_target


def _safe_float(v, default=0.0):
    try:
        if v is None or pd.isna(v): return default
        return float(v)
    except:
        return default


_DF_RAW_CACHE = {}

def compute_local_rs(symbols, base_dir, target_date):
    """Computes 63-day relative strength percentiles purely from local 1D parquet data."""
    returns = {}
    for sym in symbols:
        p_path = os.path.join(base_dir, "history", "1d", f"{sym}.parquet")
        if not os.path.exists(p_path): continue
        try:
            if sym not in _DF_RAW_CACHE:
                _DF_RAW_CACHE[sym] = pd.read_parquet(p_path)
            df = _DF_RAW_CACHE[sym]
            if df.empty or len(df) < 20: continue
            if isinstance(df.index, pd.DatetimeIndex):
                target_ts = pd.to_datetime(target_date).tz_localize("Asia/Kolkata") if df.index.tz is not None else pd.to_datetime(target_date)
                df_cut = df[df.index <= target_ts]
            elif "Date" in df.columns:
                df_cut = df[pd.to_datetime(df["Date"]) <= pd.to_datetime(target_date)]
            else:
                df_cut = df
            if len(df_cut) < 20: continue
            s_start = float(df_cut["Close"].iloc[max(0, len(df_cut) - 63)])
            s_end = float(df_cut["Close"].iloc[-1])
            returns[sym] = ((s_end - s_start) / s_start) * 100.0 if s_start > 0 else 0.0
        except Exception:
            pass
    if returns:
        ser = pd.Series(returns)
        return (ser.rank(pct=True) * 100.0).round(2).to_dict()
    return {}


def run_comparison(target_date="2026-08-25", market_regime="BEAR"):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
    wl_path = os.path.join(base_dir, "elite_fundamental_watchlist.parquet")
    
    symbols_info = {}
    if os.path.exists(wl_path):
        wl = pd.read_parquet(wl_path)
        for _, row in wl.iterrows():
            sym = str(row["Stock"]).strip()
            symbols_info[sym] = row.to_dict()
    
    files = glob.glob(os.path.join(base_dir, "history", "1d", "*.parquet"))
    symbols = [os.path.basename(f).replace(".parquet", "") for f in files]
    
    print(f"📊 Computing local Relative Strength ratings for {len(symbols)} symbols...")
    rs_dict = compute_local_rs(symbols, base_dir, target_date)
    print(f"✅ RS ratings computed for {len(rs_dict)} symbols.")

    base_score_threshold = SCORE_THRESHOLDS.get("1d", 75)
    regime_mod = REGIME_POLICIES.get(market_regime, {}).get("score_modifier", 0)
    effective_score_threshold = min(base_score_threshold + regime_mod, 82)
    min_rr_threshold = MIN_NATURAL_RR.get("EOD", 2.5)

    print("=" * 80)
    print(f"🔬 HISTORICAL EOD FUNNEL REPLAY: BEFORE vs AFTER")
    print(f"  • Target Date            : {target_date}")
    print(f"  • Market Regime          : {market_regime} (Min score threshold: {effective_score_threshold})")
    print(f"  • Total Symbols Evaluated: {len(symbols)}")
    print("=" * 80)

    funnel_before = defaultdict(int)
    funnel_after = defaultdict(int)
    rejection_before = {}
    rejection_after = {}
    alerts_before = {}
    alerts_after = {}
    terminal_counts_before = defaultdict(int)
    terminal_counts_after = defaultdict(int)

    for sym in symbols:
        p_path = os.path.join(base_dir, "history", "1d", f"{sym}.parquet")
        if not os.path.exists(p_path):
            continue
        try:
            if sym not in _DF_RAW_CACHE:
                _DF_RAW_CACHE[sym] = pd.read_parquet(p_path)
            df = _DF_RAW_CACHE[sym]
        except:
            continue

        if df.empty or len(df) < 50:
            continue

        if isinstance(df.index, pd.DatetimeIndex):
            target_ts = pd.to_datetime(target_date).tz_localize("Asia/Kolkata") if df.index.tz is not None else pd.to_datetime(target_date)
            df_cut = df[df.index <= target_ts]
        elif "Date" in df.columns:
            df_cut = df[pd.to_datetime(df["Date"]) <= pd.to_datetime(target_date)]
        else:
            df_cut = df

        if len(df_cut) < 50:
            continue

        try:
            ticker = apply_indicators(df_cut.copy(), timeframe="1d")
        except Exception:
            continue

        if ticker is None or ticker.empty:
            continue

        latest = ticker.iloc[-1]
        candle_close = _safe_float(latest.get("Close"))
        candle_open = _safe_float(latest.get("Open"))
        candle_high = _safe_float(latest.get("High"))
        candle_low = _safe_float(latest.get("Low"))
        candle_range = candle_high - candle_low
        candle_body = abs(candle_close - candle_open)
        upper_wick = candle_high - max(candle_close, candle_open)

        if len(ticker) >= 22:
            avg_volume = float(ticker["Volume"].iloc[-21:-1].mean())
        else:
            avg_volume = float(ticker["Volume"].iloc[:-1].mean())

        if avg_volume <= 0:
            continue

        vol_ratio = _safe_float(latest.get("Volume")) / avg_volume
        rsi_val = _safe_float(latest.get("RSI"), 50.0)
        atr20 = _safe_float(latest.get("ATR20"), _safe_float(latest.get("ATR"), candle_close * 0.025))
        prior_20d_high = _safe_float(latest.get("PRIOR_20D_HIGH"))
        high_52w = _safe_float(latest.get("HIGH_52W"))
        pct_from_52w = ((high_52w - candle_close) / high_52w * 100) if high_52w > 0 else 999.0
        bb_pctile = _safe_float(ticker["BB_WIDTH_PCTILE"].iloc[-2]) if ("BB_WIDTH_PCTILE" in ticker.columns and len(ticker) >= 2) else 1.0
        stock_rs = float(rs_dict.get(sym, 50.0))
        fund_info = symbols_info.get(sym, {})
        forensic_tier = fund_info.get("Forensic_Risk_Tier", "UNKNOWN")

        # ATR10 calculation
        h10 = ticker["High"].iloc[-11:-1]
        l10 = ticker["Low"].iloc[-11:-1]
        c10 = ticker["Close"].iloc[-12:-2]
        tr10 = np.maximum(h10 - l10, np.maximum(np.abs(h10 - c10), np.abs(l10 - c10)))
        atr10 = float(tr10.mean())
        prev_close_bar = _safe_float(ticker["Close"].iloc[-2]) if len(ticker) >= 2 else candle_close
        if prev_close_bar <= 0: prev_close_bar = candle_close
        atr10_pct_before = (atr10 / candle_close * 100) if candle_close > 0 else 99.0
        atr10_pct_after = (atr10 / prev_close_bar * 100) if prev_close_bar > 0 else 99.0

        # Pre-breakout red candles
        lookback_ctx = 5
        red_count = sum(1 for _ri in range(-(lookback_ctx + 1), -1) if _safe_float(ticker["Close"].iloc[_ri]) < _safe_float(ticker["Open"].iloc[_ri]))

        # Breakout signals
        signals = detect_breakouts(ticker, timeframe="1d")

        # Candle quality penalties
        candle_penalty = 0
        body_ratio = (candle_body / candle_range) if candle_range > 0 else 1.0
        close_pos = ((candle_close - candle_low) / candle_range) if candle_range > 0 else 1.0
        wick_ratio = (upper_wick / candle_range) if candle_range > 0 else 0.0

        if body_ratio < 0.50:
            candle_penalty += min(15, int(((0.50 - body_ratio) / 0.50) * 30))
        if candle_close <= candle_open:
            candle_penalty += 5
        if close_pos < 0.60:
            candle_penalty += min(10, int(((0.60 - close_pos) / 0.60) * 20))
        if wick_ratio > 0.40:
            candle_penalty += min(10, int(((wick_ratio - 0.40) / 0.40) * 20))

        obv_penalty = 0
        if "OBV_SLOPE" in ticker.columns and not pd.isna(latest.get("OBV_SLOPE")):
            if _safe_float(latest.get("OBV_SLOPE")) <= 0.0:
                obv_penalty = -5

        technical_penalties = {}
        atr_ext = (candle_close - prior_20d_high) / atr20 if (atr20 > 0 and prior_20d_high > 0) else 0
        if atr_ext > 1.5:
            technical_penalties["extended_breakout"] = min(20, (atr_ext - 1.5) * 10)

        # ----------------------------------------------------
        # 1. EVALUATE BEFORE MODEL (Pre-Refactor)
        # ----------------------------------------------------
        def eval_before():
            funnel_before["01_UNIVERSE"] += 1
            if candle_close < MIN_STOCK_PRICE: return "PENNY_STOCK"
            if avg_volume < 50000: return "LOW_AVG_VOLUME"
            if vol_ratio < 1.5: return "LOW_VOLUME_RATIO"
            funnel_before["02_VOLUME_SURGE_PASS"] += 1

            if not (50 <= rsi_val <= 88): return "RSI_RANGE_88"
            funnel_before["03_RSI_PASS"] += 1

            if prior_20d_high <= 0 or candle_close <= prior_20d_high: return "NO_20D_BREAKOUT"
            funnel_before["04_20D_BREAKOUT_PASS"] += 1

            if atr20 > 0 and (candle_range / atr20) < 0.8: return "WEAK_ATR_EXPANSION"
            ema20 = _safe_float(latest.get("EMA20"))
            sma50 = _safe_float(latest.get("SMA50"))
            if ema20 > 0 and candle_close < ema20: return "BELOW_EMA20"
            if sma50 > 0 and candle_close < sma50: return "BELOW_SMA50"
            if _safe_float(latest.get("ADX")) < 15: return "WEAK_ADX"
            funnel_before["05_TREND_ALIGNMENT_PASS"] += 1

            if pct_from_52w > 5.0: return "FAR_FROM_52W_HIGH"
            funnel_before["06_52W_PROXIMITY_PASS"] += 1

            if atr10 > (candle_close * 0.025): return "BASE_ATR10_TOO_WIDE"
            if red_count > 2:
                if bb_pctile > 0.35: return "PRE_BREAKOUT_RED_CANDLES"
            if bb_pctile > 0.80: return "BASE_TOO_WIDE"
            funnel_before["07_BASE_QUALITY_PASS"] += 1

            if len(signals) < 1: return "NO_BREAKOUT_SIGNALS"
            funnel_before["08_BREAKOUT_SIGNALS_PASS"] += 1

            cat = fund_info.get("Category", "EQUITY")
            regime_ctx = {"trend": market_regime, "market_score": 45.0 if market_regime == "BEAR" else 80.0}
            raw_score, _, _ = calculate_score(
                category=cat, breakout_count=len(signals), rsi=rsi_val, volume_ratio=vol_ratio,
                breakout_signals=signals, ticker=ticker, latest=latest, symbol=sym, timeframe="1d",
                atr_val=atr20, regime_ctx=regime_ctx
            )
            total_ded = min(15, sum(technical_penalties.values()) + abs(obv_penalty) + candle_penalty)
            score_b = max(0, raw_score - total_ded)
            rs_b = 5 if stock_rs >= 80.0 else 0
            score_b = max(0, min(100, score_b + rs_b))

            if score_b < effective_score_threshold: return "LOW_SCORE"
            funnel_before["09_SCORE_PASS"] += 1

            if forensic_tier == "REJECT": return "FORENSIC_REJECT"
            funnel_before["10_FORENSIC_PASS"] += 1

            sl_res = compute_sl_and_target(entry_price=candle_close, atr=atr20, candle_range=candle_range, mode="EOD", rsi=rsi_val, candle_low=candle_low, ticker=ticker)
            if sl_res.get("is_rejected"): return "SL_REJECTED"
            natural_rr = sl_res.get("natural_rr", 0.0)
            if natural_rr < min_rr_threshold: return "LOW_RR"
            funnel_before["11_RR_PASS"] += 1

            alerts_before[sym] = {
                "symbol": sym, "score": score_b, "mode": "A", "rr": natural_rr,
                "sl": sl_res.get("stop_loss"), "t1": sl_res.get("target_1")
            }
            return "QUALIFIED"

        res_before = eval_before()
        rejection_before[sym] = res_before
        terminal_counts_before[res_before] += 1

        # ----------------------------------------------------
        # 2. EVALUATE AFTER MODEL (Post-Refactor)
        # ----------------------------------------------------
        def eval_after():
            funnel_after["01_UNIVERSE"] += 1
            if candle_close < MIN_STOCK_PRICE: return "PENNY_STOCK"
            if avg_volume < 50000: return "LOW_AVG_VOLUME"
            if vol_ratio < 1.5: return "LOW_VOLUME_RATIO"
            funnel_after["02_VOLUME_SURGE_PASS"] += 1

            # RSI: Hard ceiling 92 (graduated penalty applied in scoring)
            if not (50 <= rsi_val <= 92): return "RSI_RANGE_92"
            funnel_after["03_RSI_PASS"] += 1

            if prior_20d_high <= 0 or candle_close <= prior_20d_high: return "NO_20D_BREAKOUT"
            funnel_after["04_20D_BREAKOUT_PASS"] += 1

            if atr20 > 0 and (candle_range / atr20) < 0.8: return "WEAK_ATR_EXPANSION"
            ema20 = _safe_float(latest.get("EMA20"))
            sma50 = _safe_float(latest.get("SMA50"))
            if ema20 > 0 and candle_close < ema20: return "BELOW_EMA20"
            if sma50 > 0 and candle_close < sma50: return "BELOW_SMA50"
            if _safe_float(latest.get("ADX")) < 15: return "WEAK_ADX"
            funnel_after["05_TREND_ALIGNMENT_PASS"] += 1

            # 52W Two-Mode Gate
            breakout_mode = "A"
            recovery_adj = 0
            if pct_from_52w > 5.0:
                mode_b_dist = pct_from_52w <= 15.0
                mode_b_vol = vol_ratio >= 2.5
                mode_b_bb = bb_pctile <= 0.50
                mode_b_rs = stock_rs >= 60.0
                if mode_b_dist and mode_b_vol and mode_b_bb and mode_b_rs:
                    breakout_mode = "B"
                    recovery_adj = -5
                else:
                    mode_b_fails = []
                    if not mode_b_dist: mode_b_fails.append("DISTANCE")
                    if not mode_b_vol: mode_b_fails.append("RVOL")
                    if not mode_b_bb: mode_b_fails.append("BB")
                    if not mode_b_rs: mode_b_fails.append("RS")
                    fail_str = "_".join(mode_b_fails) if mode_b_fails else "EXCLUDED"
                    return f"FAR_FROM_52W_HIGH_MODE_B_{fail_str}"

            funnel_after["06_52W_PROXIMITY_PASS"] += 1

            # ATR10 uses pre-breakout close denominator
            if atr10 > (prev_close_bar * 0.025): return "BASE_ATR10_TOO_WIDE"
            # Red candles default = 3, BB default = 0.50
            if red_count > 3:
                if bb_pctile > 0.50: return "PRE_BREAKOUT_RED_CANDLES"
            if bb_pctile > 0.80: return "BASE_TOO_WIDE"
            funnel_after["07_BASE_QUALITY_PASS"] += 1

            if len(signals) < 1: return "NO_BREAKOUT_SIGNALS"
            funnel_after["08_BREAKOUT_SIGNALS_PASS"] += 1

            cat = fund_info.get("Category", "EQUITY")
            regime_ctx = {"trend": market_regime, "market_score": 45.0 if market_regime == "BEAR" else 80.0}
            raw_score, _, _ = calculate_score(
                category=cat, breakout_count=len(signals), rsi=rsi_val, volume_ratio=vol_ratio,
                breakout_signals=signals, ticker=ticker, latest=latest, symbol=sym, timeframe="1d",
                atr_val=atr20, regime_ctx=regime_ctx
            )

            # Three-Bucket Penalty Architecture
            b_candle = min(15, candle_penalty)
            b_gap = min(15, technical_penalties.get("extended_breakout", 0))
            b_obv = min(5, abs(obv_penalty))
            rsi_pen = min(10, int(max(0.0, rsi_val - 88.0) * 2.5))
            red_pen = 4 if red_count > 3 and bb_pctile <= 0.50 else 0
            b_misc = min(10, red_pen + rsi_pen)

            # Triple-fault veto
            if b_candle >= 10 and b_gap >= 10 and b_obv > 0:
                return "TRIPLE_FAULT_REJECT"

            total_ded = b_candle + b_gap + b_obv + b_misc
            score_base = max(0, raw_score - total_ded)

            rs_b = 5 if stock_rs >= 80.0 else 0
            score_after = max(0, min(100, score_base + rs_b))

            # Auditable Mode B recovery adjustment applied post-score
            if recovery_adj != 0:
                score_after = max(0, score_after + recovery_adj)

            if score_after < effective_score_threshold: return "LOW_SCORE"
            funnel_after["09_SCORE_PASS"] += 1

            if forensic_tier == "REJECT": return "FORENSIC_REJECT"
            funnel_after["10_FORENSIC_PASS"] += 1

            sl_res = compute_sl_and_target(entry_price=candle_close, atr=atr20, candle_range=candle_range, mode="EOD", rsi=rsi_val, candle_low=candle_low, ticker=ticker)
            if sl_res.get("is_rejected"): return "SL_REJECTED"
            natural_rr = sl_res.get("natural_rr", 0.0)
            if natural_rr < min_rr_threshold: return "LOW_RR"
            funnel_after["11_RR_PASS"] += 1

            alerts_after[sym] = {
                "symbol": sym,
                "mode": breakout_mode,
                "rsi": round(rsi_val, 1),
                "breakout_pct": round(((candle_close - prior_20d_high) / prior_20d_high * 100), 2) if prior_20d_high > 0 else 0.0,
                "volume_ratio": round(vol_ratio, 2),
                "dist_52w": round(pct_from_52w, 1),
                "bb_width": round(bb_pctile, 2),
                "rs_pct": round(stock_rs, 0),
                "atr10_pct": round(atr10_pct_after, 2),
                "score_before_penalties": int(raw_score),
                "b_candle": b_candle, "b_gap": b_gap, "b_obv": b_obv, "b_misc": b_misc,
                "recovery_adj": recovery_adj,
                "final_score": int(score_after),
                "sl": sl_res.get("stop_loss"),
                "t0": candle_close,
                "t1": sl_res.get("target_1"),
                "natural_rr": round(natural_rr, 2),
                "forensic_tier": forensic_tier
            }
            return "QUALIFIED"

        res_after = eval_after()
        rejection_after[sym] = res_after
        terminal_counts_after[res_after] += 1

    # ----------------------------------------------------
    # PRINT RESULTS & DIFFERENTIAL REPORT
    # ----------------------------------------------------
    print("\n" + "=" * 80)
    print(f"📊 HISTORICAL ATTRITION FUNNEL COMPARISON (Date: {target_date})")
    print("=" * 80)
    print(f"{'Funnel Stage':<30} | {'BEFORE':>10} | {'AFTER':>10} | {'Delta':>8}")
    print("-" * 80)
    stages = [
        "01_UNIVERSE", "02_VOLUME_SURGE_PASS", "03_RSI_PASS", "04_20D_BREAKOUT_PASS",
        "05_TREND_ALIGNMENT_PASS", "06_52W_PROXIMITY_PASS", "07_BASE_QUALITY_PASS",
        "08_BREAKOUT_SIGNALS_PASS", "09_SCORE_PASS", "10_FORENSIC_PASS", "11_RR_PASS"
    ]
    for stg in stages:
        cnt_b = funnel_before[stg]
        cnt_a = funnel_after[stg]
        delta = cnt_a - cnt_b
        print(f"{stg:<30} | {cnt_b:>10} | {cnt_a:>10} | {delta:>+8}")
    print("=" * 80)

    print("\n" + "=" * 80)
    print("🚫 FIRST TERMINAL REJECTION REASON BREAKDOWN:")
    print("=" * 80)
    all_reasons = sorted(list(set(terminal_counts_before.keys()) | set(terminal_counts_after.keys())))
    print(f"{'Terminal Reason':<30} | {'BEFORE':>10} | {'AFTER':>10} | {'Delta':>8}")
    print("-" * 80)
    for r in all_reasons:
        cb = terminal_counts_before[r]
        ca = terminal_counts_after[r]
        d = ca - cb
        print(f"{r:<30} | {cb:>10} | {ca:>10} | {d:>+8}")
    print("=" * 80)

    print(f"\n🎯 FINAL ALERTS GENERATED:")
    print(f"  • BEFORE Refactor : {len(alerts_before)} alerts")
    print(f"  • AFTER Refactor  : {len(alerts_after)} alerts")

    # Differential categorisation
    newly_eligible = []
    newly_rejected = []
    categorised_recoveries = {
        "RSI_RECOVERED": [],
        "RECOVERY_MODE_B": [],
        "ATR_DENOMINATOR_RECOVERED": [],
        "RED_CANDLE_CONFIG_RECOVERED": [],
        "PENALTY_MODEL_CHANGED": []
    }

    for sym in symbols:
        b_res = rejection_before.get(sym)
        a_res = rejection_after.get(sym)
        if b_res != a_res:
            if b_res != "QUALIFIED" and a_res == "QUALIFIED":
                newly_eligible.append(sym)
                if "RSI" in str(b_res):
                    categorised_recoveries["RSI_RECOVERED"].append(sym)
                elif "52W" in str(b_res):
                    categorised_recoveries["RECOVERY_MODE_B"].append(sym)
                elif "ATR10" in str(b_res):
                    categorised_recoveries["ATR_DENOMINATOR_RECOVERED"].append(sym)
                elif "RED_CANDLES" in str(b_res):
                    categorised_recoveries["RED_CANDLE_CONFIG_RECOVERED"].append(sym)
                else:
                    categorised_recoveries["PENALTY_MODEL_CHANGED"].append(sym)
            elif b_res == "QUALIFIED" and a_res != "QUALIFIED":
                newly_rejected.append(sym)

    print("\n" + "=" * 80)
    print("📈 DIFFERENTIAL RECOVERY BREAKDOWN:")
    print("=" * 80)
    for cat, sym_list in categorised_recoveries.items():
        print(f"  • {cat:<30}: {len(sym_list)} recovered -> {', '.join(sym_list) if sym_list else 'None'}")
    print(f"  • Newly Rejected (e.g. triple fault veto) : {len(newly_rejected)} -> {', '.join(newly_rejected) if newly_rejected else 'None'}")
    print("=" * 80)

    # Detailed Inspection of Alert Candidates
    print("\n" + "=" * 115)
    print("🔍 DEEP-DIVE INSPECTION OF QUALIFIED ALERTS (AFTER MODEL):")
    print("=" * 115)
    if alerts_after:
        print(f"{'Symbol':<12} {'Mode':<5} {'RSI':>5} {'20D%':>6} {'VolR':>5} {'52W%':>5} {'BB':>5} {'RS':>4} {'ATR%':>5} {'RawSc':>6} {'Penalties(C/G/O/M)':<18} {'RecAdj':>6} {'FinalSc':>7} {'SL':>8} {'T1':>8} {'R:R':>6} {'Forensic':<8}")
        print("-" * 115)
        for sym, d in alerts_after.items():
            pen_str = f"{d['b_candle']}/{d['b_gap']}/{d['b_obv']}/{d['b_misc']}"
            print(f"{d['symbol']:<12} {d['mode']:<5} {d['rsi']:>5.1f} {d['breakout_pct']:>5.1f}% {d['volume_ratio']:>5.2f} {d['dist_52w']:>4.1f}% {d['bb_width']:>5.2f} {d['rs_pct']:>4.0f} {d['atr10_pct']:>4.2f}% {d['score_before_penalties']:>6} {pen_str:<18} {d['recovery_adj']:>6} {d['final_score']:>7} {d['sl']:>8.2f} {d['t1']:>8.2f} {d['natural_rr']:>5.2f}R {d['forensic_tier']:<8}")
        print("=" * 115)
    else:
        print("No alerts generated on this date.")

    return alerts_before, alerts_after, categorised_recoveries


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-08-25")
    parser.add_argument("--regime", default="BEAR")
    args = parser.parse_args()
    run_comparison(args.date, args.regime)
