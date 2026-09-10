#!/usr/bin/env python3
# =============================================================================
# scripts/fast_reversal_expansion_engine.py
# ULTRA-FAST REVERSAL SWEEP SAMPLE EXPANSION & LIQUIDITY DEPTH ENGINE
# =============================================================================

import glob
import os
import sys
import time
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")

DEV_START = "2025-07-24"
VAL_START = "2026-01-01"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"

def run_fast_reversal_expansion():
    print("=" * 100)
    print("ULTRA-FAST REVERSAL SWEEP SAMPLE EXPANSION & LIQUIDITY DEPTH ENGINE")
    print("=" * 100)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} 1D parquet files.")

    # Reversal sweep variants testing:
    # 1. Sweep lookback: 10D vs 15D vs 20D prior swing low
    # 2. Sweep depth: 0.10 ATR vs 0.25 ATR vs 0.50 ATR below prior low
    # 3. Stop buffer: 0.25 ATR vs 0.50 ATR vs 10D low buffer
    # 4. Reclaim requirement: Close > prior low + Close > Open
    # 5. Regime gate: No Bear vs All Regimes
    variants = [
        {"id": "REV_V1_BASELINE", "lookback": 20, "depth_atr": 0.0, "sl_buf": 0.0, "no_bear": False, "target_r": 2.0},
        {"id": "REV_V22C_WIDER_SL_20D", "lookback": 20, "depth_atr": 0.10, "sl_buf": 0.25, "no_bear": True, "target_r": 2.5},
        {"id": "REV_V23A_10D_SWEEP_EXPANDED", "lookback": 10, "depth_atr": 0.10, "sl_buf": 0.35, "no_bear": True, "target_r": 2.5},
        {"id": "REV_V23B_15D_SWEEP_EXPANDED", "lookback": 15, "depth_atr": 0.10, "sl_buf": 0.35, "no_bear": True, "target_r": 2.5},
        {"id": "REV_V23C_10D_DEEP_SWEEP", "lookback": 10, "depth_atr": 0.25, "sl_buf": 0.50, "no_bear": True, "target_r": 2.5},
        {"id": "REV_V23D_15D_MULTI_REGIME", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "no_bear": False, "target_r": 2.5},
        {"id": "REV_V23E_10D_CONFIRMED_HAMMER", "lookback": 10, "depth_atr": 0.10, "sl_buf": 0.35, "no_bear": True, "hammer_only": True, "target_r": 2.5},
    ]

    all_outcomes = []

    for fpath in files:
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 50:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
        except Exception:
            continue

        n = len(df)
        c = df["Close"].values
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        v = df["Volume"].values if "Volume" in df.columns else np.ones(n) * 100000.0
        dates = df.index.strftime("%Y-%m-%d").values

        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr20 = pd.Series(h - l).rolling(20, min_periods=10).mean().values

        # Rolling swing lows of different lookbacks
        low10 = pd.Series(l).shift(1).rolling(10, min_periods=5).min().values
        low15 = pd.Series(l).shift(1).rolling(15, min_periods=8).min().values
        low20 = pd.Series(l).shift(1).rolling(20, min_periods=10).min().values

        for i in range(30, n - 20):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            if d_str < VAL_START:
                part = "DEV"
            elif d_str < HLD_START:
                part = "VAL"
            else:
                part = "HOLDOUT"

            ci = c[i]
            oi = o[i]
            hi = h[i]
            li = l[i]

            if ci < 100.0:
                continue

            atr_i = atr20[i] if not np.isnan(atr20[i]) and atr20[i] > 0 else (ci * 0.028)
            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            # Forward window
            fwd_c = c[i + 1: i + 21]
            fwd_h = h[i + 1: i + 21]
            fwd_l = l[i + 1: i + 21]
            if len(fwd_c) < 2:
                continue

            for v_cfg in variants:
                vid = v_cfg["id"]

                # Bear exclusion check
                if v_cfg.get("no_bear", False) and regime == "BEAR":
                    continue

                # Lookback swing low
                lb = v_cfg["lookback"]
                sw_low = low10[i] if lb == 10 else (low15[i] if lb == 15 else low20[i])
                if np.isnan(sw_low) or sw_low <= 0:
                    continue

                # Sweep condition: Intraday Low swept below prior swing low
                min_depth = v_cfg["depth_atr"] * atr_i
                if not (li <= (sw_low - min_depth)):
                    continue

                # Reclaim condition: Close > prior swing low (bullish absorption) AND Close >= Open
                if not (ci > sw_low and ci >= oi):
                    continue

                # Hammer wick check if specified
                if v_cfg.get("hammer_only", False):
                    lower_wick = min(oi, ci) - li
                    candle_range = max(0.01, hi - li)
                    if lower_wick / candle_range < 0.40:
                        continue

                # Compute Stop Loss: below the sweep candle low + buffer
                buf = v_cfg["sl_buf"] * atr_i
                sl = round(li - buf, 2)
                # clamp stop width between 2.5% and 8.0%
                stop_pct = max(min((ci - sl) / ci, 0.080), 0.025)
                sl = round(ci * (1.0 - stop_pct), 2)

                risk = max(0.01, ci - sl)
                t1 = round(ci + (v_cfg["target_r"] * risk), 2)

                # Outcome evaluation
                hit_sl = False
                hit_t1 = False
                r_mult = 0.0

                for b in range(len(fwd_c)):
                    if fwd_l[b] <= sl:
                        hit_sl = True
                        r_mult = -1.0
                        break
                    if fwd_h[b] >= t1:
                        hit_t1 = True
                        r_mult = v_cfg["target_r"]
                        break

                if not hit_sl and not hit_t1:
                    r_mult = round((fwd_c[-1] - ci) / risk, 2)

                all_outcomes.append({
                    "symbol": sym,
                    "variant_id": vid,
                    "scan_date": d_str,
                    "partition": part,
                    "regime": regime,
                    "entry_price": ci,
                    "stop_loss": sl,
                    "target_1": t1,
                    "realized_rr": r_mult,
                    "hit_sl": int(hit_sl),
                    "hit_t1": int(hit_t1),
                })

    df_res = pd.DataFrame(all_outcomes)
    print(f"Generated {len(df_res)} total Reversal outcomes across {len(variants)} variants in {time.time() - t0:.2f}s.")
    df_res.to_csv(os.path.join(_REPO_ROOT, "reports", "reversal_expansion_outcomes.csv"), index=False)

    print("\n" + "=" * 115)
    print(f"{'Variant ID':<40} | {'Part':<7} | {'N':<6} | {'E[R]':<8} | {'PF':<6} | {'Win%':<6} | {'T1 Hit%':<8} | {'SL Hit%':<8}")
    print("-" * 115)

    for v_cfg in variants:
        vid = v_cfg["id"]
        v_df = df_res[df_res["variant_id"] == vid]
        if v_df.empty:
            continue
        for part in ["DEV", "VAL", "HOLDOUT", "ALL"]:
            p_df = v_df if part == "ALL" else v_df[v_df["partition"] == part]
            n_trades = len(p_df)
            if n_trades == 0:
                continue
            er = p_df["realized_rr"].mean()
            wins = p_df[p_df["realized_rr"] > 0]["realized_rr"].sum()
            losses = abs(p_df[p_df["realized_rr"] < 0]["realized_rr"].sum())
            pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 1.0)
            wr = (p_df["realized_rr"] > 0).mean() * 100
            t1_pct = p_df["hit_t1"].mean() * 100
            sl_pct = p_df["hit_sl"].mean() * 100
            print(f"{vid:<40} | {part:<7} | {n_trades:<6} | {er:>+7.3f}R | {pf:>5.2f} | {wr:>5.1f}% | {t1_pct:>7.1f}% | {sl_pct:>7.1f}%")
        print("-" * 115)

if __name__ == "__main__":
    run_fast_reversal_expansion()
