#!/usr/bin/env python3
# =============================================================================
# scripts/fast_vcp_eod_winrate_optimizer.py
# ULTRA-FAST VCP & EOD BREAKOUT WIN-RATE MAXIMIZATION ENGINE (>60% TARGET)
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

def run_vcp_eod_winrate_optimization():
    print("=" * 110)
    print("ULTRA-FAST VCP & EOD BREAKOUT QUALITY & WIN-RATE OPTIMIZATION ENGINE")
    print("Objective: Maximize achievable Win% (>60% target) while protecting Expectancy & Profit Factor")
    print("=" * 110)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} 1D parquet files.")

    # VCP Variants testing:
    # 1. BB Width Percentile: <= 0.50 vs <= 0.40 vs <= 0.30 vs <= 0.25
    # 2. Volume Dryup on Contraction: <= 0.75x vs <= 0.65x vs <= 0.55x
    # 3. Breakout Volume Thrust: >= 1.5x vs >= 1.8x vs >= 2.0x
    # 4. Stop Loss: 1.5 ATR vs 1.8 ATR vs Prior Swing Low (Shelf)
    # 5. Target R: 2.5R vs 3.0R vs 3.5R
    vcp_variants = [
        {"id": "VCP_CHAMP_G_RUNNER", "bb_pct": 0.50, "dry_vol": 0.85, "thrust_vol": 1.5, "stop": "SHELF", "target_r": 3.0, "horizon": 20},
        {"id": "VCP_CHALL_H_TIGHT_COIL_40", "bb_pct": 0.40, "dry_vol": 0.75, "thrust_vol": 1.6, "stop": "SHELF", "target_r": 3.0, "horizon": 20},
        {"id": "VCP_CHALL_I_ULTRA_PINCH_30", "bb_pct": 0.30, "dry_vol": 0.70, "thrust_vol": 1.8, "stop": "SHELF", "target_r": 3.0, "horizon": 20},
        {"id": "VCP_CHALL_J_EXTREME_DRYUP", "bb_pct": 0.40, "dry_vol": 0.60, "thrust_vol": 2.0, "stop": "HYBRID_1_8", "target_r": 3.5, "horizon": 25},
        {"id": "VCP_CHALL_K_PRECISION_IGNITION", "bb_pct": 0.35, "dry_vol": 0.65, "thrust_vol": 1.8, "stop": "HYBRID_1_6", "target_r": 2.8, "horizon": 20},
    ]

    # EOD Breakout Variants testing:
    # 1. Base Contraction Width: <= 1.8 ATR vs <= 1.5 ATR vs <= 1.2 ATR
    # 2. Volume Thrust: >= 1.5x vs >= 1.8x vs >= 2.2x
    # 3. Close Position in Day Range: >= 0.70 vs >= 0.80 vs >= 0.85
    # 4. Stop Loss: 1.5 ATR vs 1.8 ATR vs Shelf Low
    eod_variants = [
        {"id": "EOD_CHAMP_G_SWEET_SPOT", "max_base_atr": 1.8, "min_cpos": 0.70, "thrust_vol": 1.5, "stop": "SHELF", "target_r": 2.5, "horizon": 15},
        {"id": "EOD_CHALL_H_TIGHT_BASE_1_4", "max_base_atr": 1.4, "min_cpos": 0.75, "thrust_vol": 1.6, "stop": "SHELF", "target_r": 2.5, "horizon": 15},
        {"id": "EOD_CHALL_I_POWER_CLOSE_80", "max_base_atr": 1.5, "min_cpos": 0.80, "thrust_vol": 1.8, "stop": "HYBRID_1_6", "target_r": 2.5, "horizon": 15},
        {"id": "EOD_CHALL_J_INSTITUTIONAL_SURGE", "max_base_atr": 1.3, "min_cpos": 0.85, "thrust_vol": 2.0, "stop": "HYBRID_1_5", "target_r": 3.0, "horizon": 20},
    ]

    all_vcp_outcomes = []
    all_eod_outcomes = []

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

        sma20 = pd.Series(c).rolling(20, min_periods=5).mean().values
        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr14 = pd.Series(h - l).rolling(14, min_periods=5).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=5).mean().values

        # Bollinger Band Width & Percentile for VCP
        std20 = pd.Series(c).rolling(20, min_periods=5).std().values
        bb_width = (4.0 * std20) / np.maximum(sma20, 1.0)
        bb_width_pctile = pd.Series(bb_width).rolling(60, min_periods=20).rank(pct=True).values

        # Highest high 20D
        hh20 = pd.Series(h).shift(1).rolling(20, min_periods=10).max().values
        ll20 = pd.Series(l).shift(1).rolling(20, min_periods=10).min().values

        for i in range(40, n - 30):
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

            if ci < 80.0:  # Minimum liquidity
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0
            bb_pct = bb_width_pctile[i] if not np.isnan(bb_width_pctile[i]) else 0.50
            hh_i = hh20[i]
            ll_i = ll20[i]

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")
            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range

            # --- 1. EVALUATE VCP VARIANTS ---
            if regime in ("BULL", "NEUTRAL") and ci > hh_i and ci >= oi:
                # Prior 3-day volume dryup check
                prior_3d_vol = np.mean(v[max(0, i - 4): i]) / avg_v if avg_v > 0 else 1.0
                shelf_l = np.min(l[max(0, i - 8): i])

                for v_cfg in vcp_variants:
                    vid = v_cfg["id"]
                    if bb_pct > v_cfg["bb_pct"]:
                        continue
                    if prior_3d_vol > v_cfg["dry_vol"]:
                        continue
                    if vol_ratio < v_cfg["thrust_vol"]:
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
                    t_r = v_cfg["target_r"]
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

                    all_vcp_outcomes.append({
                        "symbol": sym, "variant_id": vid, "scan_date": d_str, "partition": part,
                        "regime": regime, "entry_price": ci, "stop_loss": sl, "target_1": t1,
                        "realized_rr": r_mult, "hit_sl": int(hit_sl), "hit_t1": int(hit_t1),
                    })

            # --- 2. EVALUATE EOD BREAKOUT VARIANTS ---
            if regime in ("BULL", "NEUTRAL") and ci > hh_i and ci >= oi:
                prior_5d_high = np.max(h[max(0, i - 5): i])
                prior_5d_low = np.min(l[max(0, i - 5): i])
                base_span = (prior_5d_high - prior_5d_low) / atr_i if atr_i > 0 else 2.0
                shelf_l = np.min(l[max(0, i - 10): i])

                for e_cfg in eod_variants:
                    eid = e_cfg["id"]
                    if base_span > e_cfg["max_base_atr"]:
                        continue
                    if cpos < e_cfg["min_cpos"]:
                        continue
                    if vol_ratio < e_cfg["thrust_vol"]:
                        continue

                    if e_cfg["stop"] == "SHELF":
                        sl = round(shelf_l * 0.995, 2)
                    elif e_cfg["stop"] == "HYBRID_1_6":
                        sl = round(max(ci - 1.6 * atr_i, shelf_l * 0.99), 2)
                    elif e_cfg["stop"] == "HYBRID_1_5":
                        sl = round(max(ci - 1.5 * atr_i, shelf_l * 0.99), 2)
                    else:
                        sl = round(ci - 1.8 * atr_i, 2)

                    stop_pct = (ci - sl) / ci
                    if stop_pct > 0.07 or stop_pct < 0.015:
                        continue

                    risk = max(0.01, ci - sl)
                    t_r = e_cfg["target_r"]
                    t1 = round(ci + t_r * risk, 2)
                    horizon = e_cfg["horizon"]

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

                    all_eod_outcomes.append({
                        "symbol": sym, "variant_id": eid, "scan_date": d_str, "partition": part,
                        "regime": regime, "entry_price": ci, "stop_loss": sl, "target_1": t1,
                        "realized_rr": r_mult, "hit_sl": int(hit_sl), "hit_t1": int(hit_t1),
                    })

    df_vcp = pd.DataFrame(all_vcp_outcomes)
    df_eod = pd.DataFrame(all_eod_outcomes)
    print(f"Generated {len(df_vcp)} VCP outcomes and {len(df_eod)} EOD outcomes in {time.time() - t0:.2f}s.")

    df_vcp.to_csv(os.path.join(_REPO_ROOT, "reports", "vcp_winrate_outcomes.csv"), index=False)
    df_eod.to_csv(os.path.join(_REPO_ROOT, "reports", "eod_winrate_outcomes.csv"), index=False)

    print("\n" + "=" * 115)
    print("VCP OPTIMIZATION SUMMARY (DEV / VAL / LOCKED HOLDOUT)")
    print("=" * 115)
    print(f"{'Variant ID':<36} | {'Part':<7} | {'N':<6} | {'E[R]':<8} | {'PF':<6} | {'Win%':<6} | {'T1 Hit%':<8} | {'SL Hit%':<8}")
    print("-" * 115)

    for v_cfg in vcp_variants:
        vid = v_cfg["id"]
        v_df = df_vcp[df_vcp["variant_id"] == vid]
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

    print("\n" + "=" * 115)
    print("EOD BREAKOUT OPTIMIZATION SUMMARY (DEV / VAL / LOCKED HOLDOUT)")
    print("=" * 115)
    print(f"{'Variant ID':<36} | {'Part':<7} | {'N':<6} | {'E[R]':<8} | {'PF':<6} | {'Win%':<6} | {'T1 Hit%':<8} | {'SL Hit%':<8}")
    print("-" * 115)

    for e_cfg in eod_variants:
        eid = e_cfg["id"]
        e_df = df_eod[df_eod["variant_id"] == eid]
        if e_df.empty: continue
        for part in ["DEV", "VAL", "HOLDOUT", "ALL"]:
            p_df = e_df if part == "ALL" else e_df[e_df["partition"] == part]
            n_trades = len(p_df)
            if n_trades == 0: continue
            er = p_df["realized_rr"].mean()
            wins = p_df[p_df["realized_rr"] > 0]["realized_rr"].sum()
            losses = abs(p_df[p_df["realized_rr"] < 0]["realized_rr"].sum())
            pf = wins / losses if losses > 0 else (99.0 if wins > 0 else 1.0)
            wr = (p_df["realized_rr"] > 0).mean() * 100
            t1_pct = p_df["hit_t1"].mean() * 100
            sl_pct = p_df["hit_sl"].mean() * 100
            print(f"{eid:<36} | {part:<7} | {n_trades:<6} | {er:>+7.3f}R | {pf:>5.2f} | {wr:>5.1f}% | {t1_pct:>7.1f}% | {sl_pct:>7.1f}%")
        print("-" * 115)

if __name__ == "__main__":
    run_vcp_eod_winrate_optimization()
