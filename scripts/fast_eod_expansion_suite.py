#!/usr/bin/env python3
# =============================================================================
# scripts/fast_eod_expansion_suite.py
# ULTRA-FAST EOD BREAKOUT SAMPLE EXPANSION & QUALITY PRESERVATION ENGINE
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

def run_fast_eod_expansion():
    print("=" * 110)
    print("ULTRA-FAST EOD BREAKOUT SAMPLE EXPANSION & WIN-RATE PRESERVATION ENGINE")
    print("Objective: Expand N (target N >= 50-100+) while strictly preserving >60% Win Rate & PF >= 2.0")
    print("=" * 110)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} 1D parquet files.")

    # Variants testing:
    # 1. Breakout Lookback: 15D vs 20D vs 30D
    # 2. Base Contraction Width (5D span): <= 1.8 ATR vs <= 2.2 ATR vs <= 2.5 ATR
    # 3. Volume Thrust: >= 1.3x vs >= 1.5x vs >= 1.7x
    # 4. Close Position in Candle: >= 0.65 vs >= 0.70 vs >= 0.75
    # 5. Stop Loss: 1.6 ATR vs 1.8 ATR vs Prior 8D Shelf
    variants = [
        {"id": "EOD_V2A_BASE20D_THRU1_5_CPOS70", "lb": 20, "max_base_atr": 2.2, "min_cpos": 0.70, "vol_mult": 1.5, "stop": "SHELF", "t_r": 2.5, "horizon": 15},
        {"id": "EOD_V2B_BASE15D_THRU1_3_CPOS65", "lb": 15, "max_base_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.3, "stop": "SHELF", "t_r": 2.5, "horizon": 15},
        {"id": "EOD_V2C_BASE20D_THRU1_4_HYBRID", "lb": 20, "max_base_atr": 2.0, "min_cpos": 0.70, "vol_mult": 1.4, "stop": "HYBRID_1_8", "t_r": 2.5, "horizon": 15},
        {"id": "EOD_V2D_BASE30D_THRU1_5_CPOS75", "lb": 30, "max_base_atr": 2.0, "min_cpos": 0.75, "vol_mult": 1.5, "stop": "SHELF", "t_r": 2.8, "horizon": 20},
        {"id": "EOD_V2E_BASE15D_MULTI_REGIME_T3R", "lb": 15, "max_base_atr": 2.2, "min_cpos": 0.70, "vol_mult": 1.4, "stop": "HYBRID_1_6", "t_r": 3.0, "horizon": 20},
    ]

    all_outcomes = []

    for fpath in files:
        sym = os.path.basename(fpath).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 80:
                continue
            if "Date" in df.columns:
                df["dt"] = pd.to_datetime(df["Date"])
            elif isinstance(df.index, pd.DatetimeIndex):
                df["dt"] = pd.to_datetime(df.index)
            else:
                continue
            df = df.sort_values("dt").reset_index(drop=True)
        except Exception:
            continue

        n = len(df)
        c = df["Close"].values
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        v = df["Volume"].values if "Volume" in df.columns else np.ones(n) * 10000.0
        dates = df["dt"].dt.strftime("%Y-%m-%d").values

        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr14 = pd.Series(h - l).rolling(14, min_periods=5).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=5).mean().values

        hh_map = {}
        for lb in [15, 20, 30]:
            hh_map[lb] = pd.Series(h).shift(1).rolling(lb, min_periods=max(5, lb // 2)).max().values

        for i in range(40, n - 25):
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
            vi = v[i]

            if ci < 80.0:
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")
            if regime == "BEAR":
                continue

            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range
            prior_5d_span = (np.max(h[max(0, i - 5): i]) - np.min(l[max(0, i - 5): i])) / atr_i if atr_i > 0 else 2.0
            shelf_l = np.min(l[max(0, i - 8): i])

            for v_cfg in variants:
                vid = v_cfg["id"]
                lb = v_cfg["lb"]
                hh_i = hh_map[lb][i]

                if np.isnan(hh_i) or hh_i <= 0:
                    continue

                # Breakout condition
                if not (ci > hh_i and ci >= oi):
                    continue

                if prior_5d_span > v_cfg["max_base_atr"]:
                    continue

                if cpos < v_cfg["min_cpos"]:
                    continue

                if vol_ratio < v_cfg["vol_mult"]:
                    continue

                if v_cfg["stop"] == "SHELF":
                    sl = round(shelf_l * 0.995, 2)
                elif v_cfg["stop"] == "HYBRID_1_8":
                    sl = round(max(ci - 1.8 * atr_i, shelf_l * 0.99), 2)
                elif v_cfg["stop"] == "HYBRID_1_6":
                    sl = round(max(ci - 1.6 * atr_i, shelf_l * 0.99), 2)
                else:
                    sl = round(ci - 1.8 * atr_i, 2)

                stop_pct = (ci - sl) / ci
                if stop_pct > 0.08 or stop_pct < 0.015:
                    continue

                risk = max(0.01, ci - sl)
                t_r = v_cfg["t_r"]
                t1 = round(ci + t_r * risk, 2)
                horizon = v_cfg["horizon"]

                fwd_c = c[i + 1: i + 1 + horizon]
                fwd_h = h[i + 1: i + 1 + horizon]
                fwd_l = l[i + 1: i + 1 + horizon]
                if len(fwd_c) < 3:
                    continue

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
                        r_mult = t_r
                        break

                if not hit_sl and not hit_t1:
                    r_mult = round((fwd_c[-1] - ci) / risk, 2)

                all_outcomes.append({
                    "symbol": sym, "variant_id": vid, "scan_date": d_str, "partition": part,
                    "regime": regime, "entry_price": ci, "stop_loss": sl, "target_1": t1,
                    "realized_rr": r_mult, "hit_sl": int(hit_sl), "hit_t1": int(hit_t1),
                })

    df_res = pd.DataFrame(all_outcomes)
    print(f"Generated {len(df_res)} total EOD outcomes across {len(variants)} variants in {time.time() - t0:.2f}s.")
    df_res.to_csv(os.path.join(_REPO_ROOT, "reports", "eod_expansion_outcomes.csv"), index=False)

    print("\n" + "=" * 115)
    print(f"{'Variant ID':<36} | {'Part':<7} | {'N':<6} | {'E[R]':<8} | {'PF':<6} | {'Win%':<6} | {'T1 Hit%':<8} | {'SL Hit%':<8}")
    print("-" * 115)

    for v_cfg in variants:
        vid = v_cfg["id"]
        v_df = df_res[df_res["variant_id"] == vid]
        if v_df.empty: continue
        for part in ["DEV", "VAL", "HOLDOUT", "ALL"]:
            p_df = v_df if part == "ALL" else v_df[v_df["partition"] == part]
            n_trades = len(p_df)
            if n_trades == 0: continue
            er = p_df["realized_rr"].mean()
            wins = p_df[p_df["realized_rr"] > 0]["realized_rr"].sum()
            losses = abs(p_df[p_df["realized_rr"] < 0]["realized_rr"].sum())
            pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 1.0)
            wr = (p_df["realized_rr"] > 0).mean() * 100
            t1_pct = p_df["hit_t1"].mean() * 100
            sl_pct = p_df["hit_sl"].mean() * 100
            print(f"{vid:<36} | {part:<7} | {n_trades:<6} | {er:>+7.3f}R | {pf:>5.2f} | {wr:>5.1f}% | {t1_pct:>7.1f}% | {sl_pct:>7.1f}%")
        print("-" * 115)

if __name__ == "__main__":
    run_fast_eod_expansion()
