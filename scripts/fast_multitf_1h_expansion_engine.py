#!/usr/bin/env python3
# =============================================================================
# scripts/fast_multitf_1h_expansion_engine.py
# ULTRA-FAST MULTI-TF 1H BASE CONTRACTION EXPANSION REPLAY ENGINE
# =============================================================================

import glob
import os
import sys
import time
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1H_DIR = os.path.join(_REPO_ROOT, "data", "history", "1h")

DEV_START = "2025-07-24"
VAL_START = "2026-01-01"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"

def run_fast_multitf_1h_expansion():
    print("=" * 100)
    print("ULTRA-FAST MULTI-TF 1H BASE CONTRACTION SAMPLE EXPANSION & CALIBRATION ENGINE")
    print("=" * 100)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet")))
    print(f"Loaded {len(files)} 1H parquet files.")

    # Variants testing:
    # 1. Base length: 12 bars (2 days) vs 18 bars (3 days) vs 24 bars (4 days)
    # 2. Contraction width: <= 1.5 ATR vs <= 1.8 ATR vs <= 2.2 ATR
    # 3. Volume thrust: >= 1.3x vs >= 1.5x vs >= 1.8x 20-bar avg
    # 4. Stop: Base Low Shelf vs 1.8 ATR below breakout
    # 5. Regime: Macro Bull Only vs Bull+Neutral
    variants = [
        {"id": "MTF_1H_V3D_BULL_ONLY_20BAR", "base_bars": 20, "max_base_atr": 1.8, "vol_mult": 1.5, "bull_only": True, "stop": "SHELF"},
        {"id": "MTF_1H_V4A_12BAR_TIGHT_IGNITION", "base_bars": 12, "max_base_atr": 1.5, "vol_mult": 1.5, "bull_only": True, "stop": "SHELF"},
        {"id": "MTF_1H_V4B_18BAR_MODERATE_BASE", "base_bars": 18, "max_base_atr": 1.8, "vol_mult": 1.3, "bull_only": True, "stop": "SHELF"},
        {"id": "MTF_1H_V4C_18BAR_BULL_NEUTRAL", "base_bars": 18, "max_base_atr": 1.8, "vol_mult": 1.4, "bull_only": False, "stop": "SHELF"},
        {"id": "MTF_1H_V4D_12BAR_HYBRID_STOP", "base_bars": 12, "max_base_atr": 1.6, "vol_mult": 1.4, "bull_only": False, "stop": "HYBRID_1_8ATR"},
        {"id": "MTF_1H_V4E_24BAR_DEEP_CONSOLIDATION", "base_bars": 24, "max_base_atr": 2.0, "vol_mult": 1.5, "bull_only": False, "stop": "SHELF"},
    ]

    all_outcomes = []

    for fpath in files:
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 50:
                continue
            if "Datetime" in df.columns:
                df = df.set_index("Datetime")
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
        v = df["Volume"].values if "Volume" in df.columns else np.ones(n) * 10000.0
        dates = df.index.strftime("%Y-%m-%d").values

        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr20 = pd.Series(h - l).rolling(20, min_periods=10).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=10).mean().values

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
            vi = v[i]

            if ci < 100.0:
                continue

            atr_i = atr20[i] if not np.isnan(atr20[i]) and atr20[i] > 0 else (ci * 0.015)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            fwd_c = c[i + 1: i + 21]
            fwd_h = h[i + 1: i + 21]
            fwd_l = l[i + 1: i + 21]
            if len(fwd_c) < 2:
                continue

            for v_cfg in variants:
                vid = v_cfg["id"]

                # Regime check
                if v_cfg["bull_only"] and regime != "BULL":
                    continue
                if not v_cfg["bull_only"] and regime == "BEAR":
                    continue

                # Base lookback
                bb = v_cfg["base_bars"]
                if i < bb:
                    continue

                base_h = np.max(h[i - bb: i])
                base_l = np.min(l[i - bb: i])
                base_span = base_h - base_l

                # Base contraction check
                if base_span > v_cfg["max_base_atr"] * atr_i:
                    continue

                # Breakout condition: 1H candle closes above base ceiling
                if not (ci > base_h and ci >= oi):
                    continue

                # Volume thrust condition
                if vi < avg_v * v_cfg["vol_mult"]:
                    continue

                # Stop loss
                if v_cfg["stop"] == "SHELF":
                    sl = round(base_l * 0.995, 2)
                    stop_pct = max(min((ci - sl) / ci, 0.060), 0.015)
                    sl = round(ci * (1.0 - stop_pct), 2)
                else:
                    sl = round(ci - 1.8 * atr_i, 2)
                    stop_pct = max(min((ci - sl) / ci, 0.055), 0.015)
                    sl = round(ci * (1.0 - stop_pct), 2)

                risk = max(0.01, ci - sl)
                t1 = round(ci + 2.5 * risk, 2)

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
                        r_mult = 2.5
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
    print(f"Generated {len(df_res)} total Multi-TF 1H outcomes across {len(variants)} variants in {time.time() - t0:.2f}s.")
    df_res.to_csv(os.path.join(_REPO_ROOT, "reports", "multitf_1h_expansion_outcomes.csv"), index=False)

    print("\n" + "=" * 115)
    print(f"{'Variant ID':<44} | {'Part':<7} | {'N':<6} | {'E[R]':<8} | {'PF':<6} | {'Win%':<6} | {'T1 Hit%':<8} | {'SL Hit%':<8}")
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
            print(f"{vid:<44} | {part:<7} | {n_trades:<6} | {er:>+7.3f}R | {pf:>5.2f} | {wr:>5.1f}% | {t1_pct:>7.1f}% | {sl_pct:>7.1f}%")
        print("-" * 115)

if __name__ == "__main__":
    run_fast_multitf_1h_expansion()
