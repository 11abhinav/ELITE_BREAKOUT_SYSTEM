#!/usr/bin/env python3
# =============================================================================
# scripts/fast_multibagger_expansion_engine.py
# ULTRA-FAST MULTIBAGGER CONVEXITY TUNING & SAMPLE EXPANSION ENGINE
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

def run_fast_multibagger_expansion():
    print("=" * 100)
    print("ULTRA-FAST MULTIBAGGER CONVEXITY TUNING & SAMPLE EXPANSION ENGINE")
    print("=" * 100)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} 1D parquet files.")

    # Variants testing:
    # 1. Base lookback: 40D vs 60D vs 90D vs 120D
    # 2. Volume expansion: >= 1.8x vs >= 2.2x vs >= 2.5x
    # 3. Stop loss: 1.5 ATR vs 1.8 ATR vs 2.0 ATR vs 20D swing low
    # 4. Target R: 3.0R vs 3.5R vs 4.0R vs 5.0R
    # 5. Holding horizon: 20D vs 30D vs 40D
    variants = [
        {"id": "MBAG_V1_CHAMP_BASE60_T3R", "lb": 60, "vol_mult": 1.5, "stop_type": "ATR_2_5", "target_r": 3.0, "horizon": 20},
        {"id": "MBAG_V2A_CONVEX_CAT_90D_T4R", "lb": 90, "vol_mult": 2.0, "stop_type": "ATR_2_0", "target_r": 4.0, "horizon": 30},
        {"id": "MBAG_V2B_EXPANSION_120D_T5R", "lb": 120, "vol_mult": 2.5, "stop_type": "ATR_1_8", "target_r": 5.0, "horizon": 40},
        {"id": "MBAG_V3A_40D_MICRO_SURGE_T3_5R", "lb": 40, "vol_mult": 2.2, "stop_type": "HYBRID_1_8", "target_r": 3.5, "horizon": 25},
        {"id": "MBAG_V3B_60D_VOLUME_ANOMALY_T4R", "lb": 60, "vol_mult": 2.5, "stop_type": "HYBRID_1_8", "target_r": 4.0, "horizon": 30},
        {"id": "MBAG_V3C_90D_MULTI_REGIME_T4R", "lb": 90, "vol_mult": 1.8, "stop_type": "HYBRID_2_0", "target_r": 4.0, "horizon": 30},
        {"id": "MBAG_V3D_60D_CONVEX_RUNNER_T5R", "lb": 60, "vol_mult": 2.0, "stop_type": "HYBRID_1_6", "target_r": 5.0, "horizon": 35},
    ]

    all_outcomes = []

    for fpath in files:
        sym = os.path.basename(fpath).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 100:
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

        # Precompute highest highs for lookbacks
        hh_map = {}
        for lb in [40, 60, 90, 120]:
            hh_map[lb] = pd.Series(h).shift(1).rolling(lb, min_periods=max(10, lb // 3)).max().values

        for i in range(60, n - 40):
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

            if ci < 50.0:  # Minimum liquidity floor
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            for v_cfg in variants:
                vid = v_cfg["id"]
                lb = v_cfg["lb"]
                req_vol = v_cfg["vol_mult"]
                stop_type = v_cfg["stop_type"]
                t_r = v_cfg["target_r"]
                horizon = v_cfg["horizon"]

                if i < lb:
                    continue

                hh_lb = hh_map[lb][i]
                if np.isnan(hh_lb) or hh_lb <= 0:
                    continue

                # Multibagger breakout condition: Close breaks out above N-day high with volume thrust
                if not (ci > hh_lb and ci >= oi):
                    continue

                if vol_ratio < req_vol:
                    continue

                # Stop loss calculation
                if stop_type == "ATR_2_5":
                    sl = round(ci - 2.5 * atr_i, 2)
                elif stop_type == "ATR_2_0":
                    sl = round(ci - 2.0 * atr_i, 2)
                elif stop_type == "ATR_1_8":
                    sl = round(ci - 1.8 * atr_i, 2)
                elif stop_type == "HYBRID_1_8":
                    shelf_l = np.min(l[max(0, i - 10): i])
                    sl = round(max(ci - 1.8 * atr_i, shelf_l * 0.99), 2)
                elif stop_type == "HYBRID_2_0":
                    shelf_l = np.min(l[max(0, i - 15): i])
                    sl = round(max(ci - 2.0 * atr_i, shelf_l * 0.99), 2)
                elif stop_type == "HYBRID_1_6":
                    shelf_l = np.min(l[max(0, i - 10): i])
                    sl = round(max(ci - 1.6 * atr_i, shelf_l * 0.99), 2)
                else:
                    sl = round(ci - 2.0 * atr_i, 2)

                stop_pct = (ci - sl) / ci
                if stop_pct > 0.12 or stop_pct < 0.02:  # Controlled risk bracket
                    continue

                risk = max(0.01, ci - sl)
                t1 = round(ci + t_r * risk, 2)

                fwd_c = c[i + 1: i + 1 + horizon]
                fwd_h = h[i + 1: i + 1 + horizon]
                fwd_l = l[i + 1: i + 1 + horizon]
                if len(fwd_c) < 5:
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
    print(f"Generated {len(df_res)} total Multibagger outcomes across {len(variants)} variants in {time.time() - t0:.2f}s.")
    df_res.to_csv(os.path.join(_REPO_ROOT, "reports", "multibagger_expansion_outcomes.csv"), index=False)

    print("\n" + "=" * 115)
    print(f"{'Variant ID':<36} | {'Part':<7} | {'N':<6} | {'E[R]':<8} | {'PF':<6} | {'Win%':<6} | {'T1 Hit%':<8} | {'SL Hit%':<8}")
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
            print(f"{vid:<36} | {part:<7} | {n_trades:<6} | {er:>+7.3f}R | {pf:>5.2f} | {wr:>5.1f}% | {t1_pct:>7.1f}% | {sl_pct:>7.1f}%")
        print("-" * 115)

if __name__ == "__main__":
    run_fast_multibagger_expansion()
