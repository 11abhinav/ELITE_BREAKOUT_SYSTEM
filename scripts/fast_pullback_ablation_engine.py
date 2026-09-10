#!/usr/bin/env python3
# =============================================================================
# scripts/fast_pullback_ablation_engine.py
# ULTRA-FAST VECTORIZED PULLBACK V2 COMPONENT ABLATION & REDESIGN ENGINE
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

def run_fast_pullback_ablations():
    print("=" * 100)
    print("ULTRA-FAST PULLBACK V2 COMPONENT ABLATION & REDESIGN REPLAY ENGINE")
    print("=" * 100)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} 1D parquet files.")

    # Candidate variants to test
    variants = [
        {"id": "PULL_V1_BASELINE_6D_5PCT", "entry": "6D_HIGH", "vol_dry": False, "stop": "FIXED_5PCT", "regime": "ALL", "stage2": False},
        {"id": "PULL_V2A_BULL_CLOSE_2ATR", "entry": "BULL_CLOSE", "vol_dry": False, "stop": "2_0_ATR", "regime": "BULL_NEUT", "stage2": False},
        {"id": "PULL_V2B_BULL_CLOSE_SHELF", "entry": "BULL_CLOSE", "vol_dry": False, "stop": "SHELF_10D", "regime": "BULL_NEUT", "stage2": False},
        {"id": "PULL_V2C_PRIOR_HIGH_2ATR", "entry": "PRIOR_HIGH", "vol_dry": False, "stop": "2_0_ATR", "regime": "BULL_NEUT", "stage2": False},
        {"id": "PULL_V2D_PRIOR_HIGH_SHELF", "entry": "PRIOR_HIGH", "vol_dry": False, "stop": "SHELF_10D", "regime": "BULL_NEUT", "stage2": False},
        {"id": "PULL_V2E_EMA20_RECLAIM_2ATR", "entry": "EMA20_RECLAIM", "vol_dry": False, "stop": "2_0_ATR", "regime": "BULL_NEUT", "stage2": False},
        {"id": "PULL_V2F_VOL_DRY_PRIOR_HIGH_SHELF", "entry": "PRIOR_HIGH", "vol_dry": True, "stop": "SHELF_10D", "regime": "BULL_NEUT", "stage2": False},
        {"id": "PULL_V2G_VOL_DRY_BULL_CLOSE_HYBRID", "entry": "BULL_CLOSE", "vol_dry": True, "stop": "HYBRID", "regime": "BULL_ONLY", "stage2": False},
        {"id": "PULL_V2H_VOL_DRY_PRIOR_HIGH_HYBRID_BULL", "entry": "PRIOR_HIGH", "vol_dry": True, "stop": "HYBRID", "regime": "BULL_ONLY", "stage2": False},
        {"id": "PULL_V2I_STAGE2_VOL_DRY_PRIOR_HIGH_HYBRID", "entry": "PRIOR_HIGH", "vol_dry": True, "stop": "HYBRID", "regime": "BULL_NEUT", "stage2": True},
        {"id": "PULL_V2J_STAGE2_VOL_DRY_BULL_CLOSE_HYBRID", "entry": "BULL_CLOSE", "vol_dry": True, "stop": "HYBRID", "regime": "BULL_NEUT", "stage2": True},
    ]

    all_outcomes = []

    for fpath in files:
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 60:
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

        # Rolling indicators
        sma20 = pd.Series(c).rolling(20, min_periods=10).mean().values
        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        ema20 = pd.Series(c).ewm(span=20, adjust=False).mean().values
        atr20 = pd.Series(h - l).rolling(20, min_periods=10).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=10).mean().values

        # Rolling swing lows and highs for pullbacks
        shelf10 = pd.Series(l).shift(1).rolling(10, min_periods=5).min().values
        impulse_high30 = pd.Series(h).shift(1).rolling(30, min_periods=15).max().values
        swing_low60 = pd.Series(l).shift(15).rolling(45, min_periods=20).min().values
        high6 = pd.Series(h).shift(1).rolling(5, min_periods=3).max().values

        for i in range(50, n - 20):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            # Partition
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

            # Primary trend
            if not (ci > sma50[i] and sma50[i] >= sma200[i] * 0.99):
                continue

            # Impulse check: >= 8% rally
            sw_low = swing_low60[i]
            imp_hi = impulse_high30[i]
            if np.isnan(sw_low) or np.isnan(imp_hi) or sw_low <= 0:
                continue
            if (imp_hi - sw_low) / sw_low < 0.08:
                continue

            # Retracement depth 5% - 50%
            retrace_pct = (imp_hi - ci) / (imp_hi - sw_low) * 100.0
            if not (5.0 <= retrace_pct <= 50.0):
                continue

            # Support hold: near EMA20 or 10D shelf
            sh10 = shelf10[i] if not np.isnan(shelf10[i]) else li
            near_ema20 = abs(ci - ema20[i]) / ci <= 0.025
            near_shelf = abs(ci - sh10) / ci <= 0.035
            if not (near_ema20 or near_shelf):
                continue

            # Quality metrics
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            is_vol_dry = (vi / avg_v <= 0.85)

            # Entry triggers
            c_prev = c[i - 1]
            h_prev = h[i - 1]
            ema_prev = ema20[i - 1]

            is_bull_close = (ci >= oi) and (ci >= c_prev)
            is_prior_high_break = (ci > h_prev) and (ci >= oi)
            is_ema20_reclaim = (ci > ema20[i]) and (c_prev <= ema_prev)
            h6 = high6[i] if not np.isnan(high6[i]) else hi
            is_resumption_6d = (ci > h6) and (vi / avg_v >= 1.5)

            # Macro regime
            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            atr_i = atr20[i] if not np.isnan(atr20[i]) and atr20[i] > 0 else (ci * 0.028)

            # Forward window
            fwd_c = c[i + 1: i + 21]
            fwd_h = h[i + 1: i + 21]
            fwd_l = l[i + 1: i + 21]
            if len(fwd_c) < 2:
                continue

            for v_cfg in variants:
                vid = v_cfg["id"]

                # Strict stage 2
                if v_cfg["stage2"]:
                    if not (ci > sma20[i] > sma50[i] > sma200[i]):
                        continue

                # Regime gate
                r_gate = v_cfg["regime"]
                if r_gate == "BULL_ONLY" and regime != "BULL":
                    continue
                elif r_gate == "BULL_NEUT" and regime == "BEAR":
                    continue

                # Vol dry
                if v_cfg["vol_dry"] and not is_vol_dry:
                    continue

                # Entry mode
                emode = v_cfg["entry"]
                trig = False
                if emode == "6D_HIGH" and is_resumption_6d:
                    trig = True
                elif emode == "BULL_CLOSE" and is_bull_close:
                    trig = True
                elif emode == "PRIOR_HIGH" and is_prior_high_break:
                    trig = True
                elif emode == "EMA20_RECLAIM" and is_ema20_reclaim:
                    trig = True

                if not trig:
                    continue

                # Stop loss
                smod = v_cfg["stop"]
                if smod == "FIXED_5PCT":
                    sl = round(ci * 0.95, 2)
                elif smod == "2_0_ATR":
                    sl = round(ci - 2.0 * atr_i, 2)
                elif smod == "SHELF_10D":
                    sl = round(min(sh10 * 0.99, ci - 1.5 * atr_i), 2)
                elif smod == "HYBRID":
                    sl = round(min(sh10 * 0.99, ci - 2.0 * atr_i), 2)
                    stop_pct = max(min((ci - sl) / ci, 0.080), 0.035)
                    sl = round(ci * (1.0 - stop_pct), 2)
                else:
                    sl = round(ci * 0.95, 2)

                risk = max(0.01, ci - sl)
                t1 = round(ci + 2.5 * risk, 2)

                # Vectorized trade outcome evaluation
                hit_sl = False
                hit_t1 = False
                r_mult = 0.0

                for b in range(len(fwd_c)):
                    # Check low vs SL
                    if fwd_l[b] <= sl:
                        hit_sl = True
                        r_mult = -1.0
                        break
                    # Check high vs T1
                    if fwd_h[b] >= t1:
                        hit_t1 = True
                        r_mult = 2.5
                        break

                if not hit_sl and not hit_t1:
                    # Exited at horizon close
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
    print(f"Generated {len(df_res)} total outcomes across {len(variants)} variants in {time.time() - t0:.2f}s.")
    df_res.to_csv(os.path.join(_REPO_ROOT, "reports", "pullback_v2_ablation_outcomes.csv"), index=False)

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
    run_fast_pullback_ablations()
