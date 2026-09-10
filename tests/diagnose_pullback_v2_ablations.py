#!/usr/bin/env python3
# =============================================================================
# tests/diagnose_pullback_v2_ablations.py
# PULLBACK V2 CONTROLLED COMPONENT ABLATION & REDESIGN RESEARCH ENGINE
# =============================================================================

import glob
import os
import sys
import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_APP_DIR = os.path.join(_REPO_ROOT, "app")
for d in (_REPO_ROOT, _APP_DIR):
    if d not in sys.path:
        sys.path.insert(0, d)

from alert_quality_engine import AlertQualityEngine

_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")

DEV_START = "2025-07-24"
DEV_END   = "2025-12-31"
VAL_START = "2026-01-01"
VAL_END   = "2026-05-31"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"


def _partition_label(dt_str: str) -> str:
    if dt_str < VAL_START:
        return "DEV"
    elif dt_str <= VAL_END:
        return "VAL"
    else:
        return "HOLDOUT"


def load_all_parquets(base_dir: str) -> Dict[str, pd.DataFrame]:
    symbol_dfs: Dict[str, pd.DataFrame] = {}
    files = sorted(glob.glob(os.path.join(base_dir, "*.parquet")))
    print(f"Loading {len(files)} parquet files...", flush=True)
    for f in files:
        sym = os.path.basename(f).replace(".parquet", "")
        try:
            df = pd.read_parquet(f)
            if df is None or len(df) < 60:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            symbol_dfs[sym] = df
        except Exception:
            pass
    print(f"Loaded {len(symbol_dfs)} symbols.", flush=True)
    return symbol_dfs


def run_pullback_v2_ablations():
    symbol_dfs = load_all_parquets(_HISTORY_1D_DIR)

    # Define Candidate Variants to test
    # Matrix of (Entry Timing, Volume Filter, Stop Architecture, Regime Gate)
    variants = [
        {
            "id": "PULLBACK_V1_BASELINE",
            "entry_mode": "RESUMPTION_6D_HIGH",
            "vol_dry_filter": False,
            "stop_model": "FIXED_5PCT",
            "regime_gate": "ALL",
            "desc": "Baseline V1: Break of 6D High + 5% Fixed Stop"
        },
        {
            "id": "PULLBACK_V2_A_BULL_CLOSE_2ATR",
            "entry_mode": "BULLISH_CLOSE_AT_SUPPORT",
            "vol_dry_filter": False,
            "stop_model": "2_0_ATR",
            "regime_gate": "BULL_NEUTRAL",
            "desc": "V2A: Bullish Close at Support + 2.0 ATR Stop"
        },
        {
            "id": "PULLBACK_V2_B_BULL_CLOSE_SHELF",
            "entry_mode": "BULLISH_CLOSE_AT_SUPPORT",
            "vol_dry_filter": False,
            "stop_model": "10D_SHELF_BUFFER",
            "regime_gate": "BULL_NEUTRAL",
            "desc": "V2B: Bullish Close at Support + 10D Shelf Buffer Stop"
        },
        {
            "id": "PULLBACK_V2_C_PRIOR_HIGH_BREAK_2ATR",
            "entry_mode": "PRIOR_DAY_HIGH_BREAK",
            "vol_dry_filter": False,
            "stop_model": "2_0_ATR",
            "regime_gate": "BULL_NEUTRAL",
            "desc": "V2C: Prior Day High Break (C > H[-1]) + 2.0 ATR Stop"
        },
        {
            "id": "PULLBACK_V2_D_PRIOR_HIGH_BREAK_SHELF",
            "entry_mode": "PRIOR_DAY_HIGH_BREAK",
            "vol_dry_filter": False,
            "stop_model": "10D_SHELF_BUFFER",
            "regime_gate": "BULL_NEUTRAL",
            "desc": "V2D: Prior Day High Break + 10D Shelf Stop"
        },
        {
            "id": "PULLBACK_V2_E_EMA20_RECLAIM_2ATR",
            "entry_mode": "EMA20_RECLAIM",
            "vol_dry_filter": False,
            "stop_model": "2_0_ATR",
            "regime_gate": "BULL_NEUTRAL",
            "desc": "V2E: EMA20 Reclaim (C > EMA20 and C[-1] <= EMA20) + 2.0 ATR Stop"
        },
        {
            "id": "PULLBACK_V2_F_VOL_DRY_PRIOR_HIGH_SHELF",
            "entry_mode": "PRIOR_DAY_HIGH_BREAK",
            "vol_dry_filter": True,
            "stop_model": "10D_SHELF_BUFFER",
            "regime_gate": "BULL_NEUTRAL",
            "desc": "V2F: Volume Dry-Up (Vol <= 0.85x 20D) + Prior High Break + 10D Shelf"
        },
        {
            "id": "PULLBACK_V2_G_VOL_DRY_BULL_CLOSE_HYBRID",
            "entry_mode": "BULLISH_CLOSE_AT_SUPPORT",
            "vol_dry_filter": True,
            "stop_model": "HYBRID_2ATR_SHELF",
            "regime_gate": "BULL_ONLY",
            "desc": "V2G: Volume Dry-Up + Bullish Close + Hybrid 2ATR/Shelf + Bull Only"
        },
        {
            "id": "PULLBACK_V2_H_VOL_DRY_PRIOR_HIGH_HYBRID_BULL",
            "entry_mode": "PRIOR_DAY_HIGH_BREAK",
            "vol_dry_filter": True,
            "stop_model": "HYBRID_2ATR_SHELF",
            "regime_gate": "BULL_ONLY",
            "desc": "V2H: Vol Dry-Up + Prior High Break + Hybrid 2ATR/Shelf + Bull Only"
        },
        {
            "id": "PULLBACK_V2_I_STRICT_STAGE2_HYBRID",
            "entry_mode": "PRIOR_DAY_HIGH_BREAK",
            "vol_dry_filter": True,
            "stop_model": "HYBRID_2ATR_SHELF",
            "regime_gate": "BULL_NEUTRAL",
            "strict_stage2": True,
            "desc": "V2I: Stage-2 Filter (C > SMA20 > SMA50 > SMA200) + Vol Dry + Prior High + Hybrid Stop"
        }
    ]

    all_outcomes = []

    # Get sorted market calendar
    from collections import Counter
    date_cnt = Counter()
    for df in symbol_dfs.values():
        for d in df.index.normalize().unique():
            date_cnt[d] += 1
    market_days = sorted([d for d, c in date_cnt.items() if c >= 100])
    print(f"Evaluating across {len(market_days)} market days...", flush=True)

    # Replay simulation
    for scan_idx, scan_day in enumerate(market_days):
        if scan_idx < 60:
            continue
        dt_str = scan_day.strftime("%Y-%m-%d")
        partition = _partition_label(dt_str)

        for sym, df in symbol_dfs.items():
            df_cut = df[df.index <= scan_day]
            if len(df_cut) < 60:
                continue

            c = float(df_cut["Close"].iloc[-1])
            o = float(df_cut["Open"].iloc[-1])
            h = float(df_cut["High"].iloc[-1])
            l = float(df_cut["Low"].iloc[-1])
            v = float(df_cut["Volume"].iloc[-1])

            if c < 100.0:
                continue

            # Moving averages
            sma20 = float(df_cut["Close"].iloc[-20:].mean())
            sma50 = float(df_cut["Close"].iloc[-50:].mean())
            sma200 = float(df_cut["Close"].iloc[-200:].mean()) if len(df_cut) >= 200 else float(df_cut["Close"].mean())
            ema20 = float(df_cut["Close"].ewm(span=20, adjust=False).mean().iloc[-1])
            atr20 = float((df_cut["High"] - df_cut["Low"]).iloc[-20:].mean())

            # Primary trend: Stage-2 alignment
            is_bull_trend = (c > sma50) and (sma50 >= sma200 * 0.99)
            if not is_bull_trend:
                continue

            # Prior impulse check: stock rallied >= 8% in last 30 bars
            impulse_high = float(df_cut["High"].iloc[-30:-1].max()) if len(df_cut) >= 30 else float(df_cut["High"].max())
            swing_low_pivot = float(df_cut["Low"].iloc[-60:-15].min()) if len(df_cut) >= 60 else float(df_cut["Low"].min())
            if swing_low_pivot <= 0 or (impulse_high - swing_low_pivot) / swing_low_pivot < 0.08:
                continue

            # Pullback depth: Retracement 5% - 50%
            retracement_pct = (impulse_high - c) / (impulse_high - swing_low_pivot) * 100.0
            if not (5.0 <= retracement_pct <= 50.0):
                continue

            # Support hold identification: near EMA20 or 10D swing shelf
            shelf_10d = float(df_cut["Low"].iloc[-10:-1].min())
            near_ema20 = abs(c - ema20) / c <= 0.025
            near_shelf = abs(c - shelf_10d) / c <= 0.035
            if not (near_ema20 or near_shelf):
                continue

            # Quality metrics
            avg_vol_20d = float(df_cut["Volume"].iloc[-21:-1].mean()) if len(df_cut) >= 21 else v
            is_vol_dry = (v / avg_vol_20d <= 0.85) if avg_vol_20d > 0 else True

            # Entry condition evaluation
            c_prev = float(df_cut["Close"].iloc[-2]) if len(df_cut) >= 2 else c
            h_prev = float(df_cut["High"].iloc[-2]) if len(df_cut) >= 2 else h
            ema20_prev = float(df_cut["Close"].ewm(span=20, adjust=False).mean().iloc[-2]) if len(df_cut) >= 2 else ema20

            is_bull_close = (c >= o) and (c >= c_prev)
            is_prior_high_break = (c > h_prev) and (c >= o)
            is_ema20_reclaim = (c > ema20) and (c_prev <= ema20_prev)
            resumption_6d_high = float(df_cut["High"].iloc[-6:-1].max())
            is_resumption_6d = (c > resumption_6d_high) and (v / avg_vol_20d >= 1.5)

            # Macro regime
            regime = "BULL" if c > sma200 * 1.02 else ("BEAR" if c < sma200 * 0.95 else "NEUTRAL")

            # Forward dataframe for outcome
            fwd_df = df[df.index > scan_day].iloc[:20]
            if len(fwd_df) < 2:
                continue

            for v_cfg in variants:
                vid = v_cfg["id"]

                # Strict stage-2 check
                if v_cfg.get("strict_stage2", False):
                    if not (c > sma20 > sma50 > sma200):
                        continue

                # Regime gate check
                r_gate = v_cfg["regime_gate"]
                if r_gate == "BULL_ONLY" and regime != "BULL":
                    continue
                elif r_gate == "BULL_NEUTRAL" and regime == "BEAR":
                    continue

                # Volume dry-up check
                if v_cfg["vol_dry_filter"] and not is_vol_dry:
                    continue

                # Entry trigger check
                emode = v_cfg["entry_mode"]
                triggered = False
                if emode == "RESUMPTION_6D_HIGH" and is_resumption_6d:
                    triggered = True
                elif emode == "BULLISH_CLOSE_AT_SUPPORT" and is_bull_close:
                    triggered = True
                elif emode == "PRIOR_DAY_HIGH_BREAK" and is_prior_high_break:
                    triggered = True
                elif emode == "EMA20_RECLAIM" and is_ema20_reclaim:
                    triggered = True

                if not triggered:
                    continue

                # Compute Stop Loss
                entry_p = c
                smodel = v_cfg["stop_model"]
                if smodel == "FIXED_5PCT":
                    sl = round(entry_p * 0.95, 2)
                elif smodel == "2_0_ATR":
                    sl = round(entry_p - 2.0 * atr20, 2)
                elif smodel == "10D_SHELF_BUFFER":
                    sl = round(min(shelf_10d * 0.99, entry_p - 1.5 * atr20), 2)
                elif smodel == "HYBRID_2ATR_SHELF":
                    sl = round(min(shelf_10d * 0.99, entry_p - 2.0 * atr20), 2)
                    # clamp stop width between 3.5% and 8.0%
                    stop_pct = max(min((entry_p - sl) / entry_p, 0.080), 0.035)
                    sl = round(entry_p * (1.0 - stop_pct), 2)
                else:
                    sl = round(entry_p * 0.95, 2)

                risk = max(0.01, entry_p - sl)
                t1 = round(entry_p + 2.5 * risk, 2)

                # Outcome evaluation
                try:
                    outcome = AlertQualityEngine.evaluate_trade_outcome(
                        entry_price=entry_p,
                        stop_loss=sl,
                        target_1=t1,
                        target_2=None,
                        price_df=fwd_df,
                        scanner="PULLBACK"
                    )
                except Exception:
                    continue

                all_outcomes.append({
                    "symbol": sym,
                    "variant_id": vid,
                    "scan_date": dt_str,
                    "partition": partition,
                    "regime": regime,
                    "entry_price": entry_p,
                    "stop_loss": sl,
                    "target_1": t1,
                    "realized_rr": outcome.get("realized_rr", 0.0),
                    "exit_reason": outcome.get("exit_reason", ""),
                    "holding_period_bars": outcome.get("holding_period_bars", 0),
                    "r1_hit_before_sl": int(outcome.get("r1_hit_before_sl", False)),
                    "r2_hit_before_sl": int(outcome.get("r2_hit_before_sl", False)),
                    "post_sl_recovered_entry": int(outcome.get("post_sl_recovered_entry", False)),
                })

    # Convert to DataFrame and report
    df_out = pd.DataFrame(all_outcomes)
    print(f"\nTotal Simulated Outcomes: {len(df_out)}")
    df_out.to_csv(os.path.join(_REPO_ROOT, "reports", "pullback_v2_ablation_outcomes.csv"), index=False)

    print("\n" + "=" * 110)
    print(f"{'Variant ID':<40} | {'Part':<7} | {'N':<5} | {'E[R]':<8} | {'PF':<6} | {'Win%':<6} | {'+2R%':<5} | {'Post-SL%':<8}")
    print("-" * 110)

    for v_cfg in variants:
        vid = v_cfg["id"]
        v_df = df_out[df_out["variant_id"] == vid]
        if v_df.empty:
            continue
        for part in ["DEV", "VAL", "HOLDOUT", "ALL"]:
            p_df = v_df if part == "ALL" else v_df[v_df["partition"] == part]
            n = len(p_df)
            if n == 0:
                continue
            er = p_df["realized_rr"].mean()
            wins = p_df[p_df["realized_rr"] > 0]["realized_rr"].sum()
            losses = abs(p_df[p_df["realized_rr"] < 0]["realized_rr"].sum())
            pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 1.0)
            wr = (p_df["realized_rr"] > 0).mean() * 100
            r2 = p_df["r2_hit_before_sl"].mean() * 100
            psl = p_df["post_sl_recovered_entry"].mean() * 100
            print(f"{vid:<40} | {part:<7} | {n:<5} | {er:>+7.3f}R | {pf:>5.2f} | {wr:>5.1f}% | {r2:>4.1f}% | {psl:>6.1f}%")
        print("-" * 110)

if __name__ == "__main__":
    run_pullback_v2_ablations()
