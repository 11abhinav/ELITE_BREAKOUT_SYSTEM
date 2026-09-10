#!/usr/bin/env python3
# =============================================================================
# scripts/fast_vcp_v55_sample_expansion_engine.py
# V5.5 VCP SAMPLE-EXPANSION RESEARCH & COMPONENT ABLATION ENGINE
# =============================================================================
# Evaluates Precision, Balanced, and Capacity VCP challengers across all 884 NSE
# equities under strict three-way data partitioning (DEV -> VAL -> LOCKED HOLDOUT)
# with the goal of scaling N towards 75-100+ while preserving 60-70% Win Rate.
# =============================================================================

import glob
import os
import sys
import time
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

DEV_START = "2025-07-24"
VAL_START = "2026-01-01"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"

def run_vcp_sample_expansion():
    print("=" * 110)
    print("V5.5 ACCUMULATION / VCP SAMPLE-EXPANSION RESEARCH & COMPONENT ABLATION SUITE")
    print("Goal: Scale sample size toward N >= 75-100 while preserving 60-70% Win Rate frontier")
    print("=" * 110)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} verified 1D equity parquets.")

    # ── Define VCP Challenger Suite Across Frontiers ────────────────────────
    challengers = [
        # 1. Precision Frontier (Focus: 65-75% WR, selective entry)
        {"id": "VCP_V55_PRECISION_A_BENCH", "lb": 20, "bb_pct": 0.40, "dry_vol": 0.75, "thrust_vol": 1.6, "min_clv": 0.60, "stop": "SHELF", "target_r": 3.0, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision A (Benchmark): 20D Lookback, 0.40 BB Pct, 0.75x Dryup, 1.6x Vol, 3.0R Target"},
        {"id": "VCP_V55_PRECISION_B_CLV65", "lb": 15, "bb_pct": 0.45, "dry_vol": 0.80, "thrust_vol": 1.5, "min_clv": 0.65, "stop": "SHELF", "target_r": 3.0, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision B: 15D Intermediate Lookback, 0.45 BB Pct, 0.80x Dryup, 1.5x Vol, CLV >= 0.65, 3.0R Target"},
        {"id": "VCP_V55_PRECISION_C_TIGHT_28R", "lb": 15, "bb_pct": 0.45, "dry_vol": 0.80, "thrust_vol": 1.4, "min_clv": 0.65, "stop": "SHELF", "target_r": 2.8, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision C: 15D Lookback, 0.45 BB Pct, 1.4x Vol Thrust, 2.8R Target"},

        # 2. Balanced Frontier (Focus: 55-65% WR, N = 50-80)
        {"id": "VCP_V55_BALANCED_A_15D_25R", "lb": 15, "bb_pct": 0.50, "dry_vol": 0.85, "thrust_vol": 1.4, "min_clv": 0.60, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced A: 15D Lookback, 0.50 BB Pct, 0.85x Dryup, 1.4x Vol, 2.5R Target"},
        {"id": "VCP_V55_BALANCED_B_20D_28R", "lb": 20, "bb_pct": 0.55, "dry_vol": 0.90, "thrust_vol": 1.4, "min_clv": 0.60, "stop": "SHELF", "target_r": 2.8, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced B: 20D Lookback, 0.55 BB Pct, 0.90x Dryup, 1.4x Vol, 2.8R Target"},
        {"id": "VCP_V55_BALANCED_C_12D_FAST", "lb": 12, "bb_pct": 0.50, "dry_vol": 0.85, "thrust_vol": 1.4, "min_clv": 0.65, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced C: 12D Fast Base, 0.50 BB Pct, 0.85x Dryup, 1.4x Vol, CLV >= 0.65, 2.5R Target"},
        {"id": "VCP_V55_BALANCED_D_MULTI_REG", "lb": 15, "bb_pct": 0.45, "dry_vol": 0.85, "thrust_vol": 1.5, "min_clv": 0.60, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Balanced D Multi-Regime: 15D Lookback, Bull+Neut+Bear Operation, 2.5R Target"},

        # 3. Capacity Frontier (Focus: 50-60% WR, N = 90-130+)
        {"id": "VCP_V55_CAPACITY_A_BROAD_COIL", "lb": 15, "bb_pct": 0.60, "dry_vol": 0.95, "thrust_vol": 1.30, "min_clv": 0.55, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Capacity A: 15D Lookback, 0.60 BB Pct, 0.95x Dryup, 1.30x Vol, 2.5R Target"},
        {"id": "VCP_V55_CAPACITY_B_HYBRID_SL", "lb": 15, "bb_pct": 0.60, "dry_vol": 1.00, "thrust_vol": 1.35, "min_clv": 0.55, "stop": "HYBRID_1_5", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Capacity B: 15D Lookback, 0.60 BB Pct, 1.35x Vol, 1.5x ATR Hybrid Stop, 2.5R Target"},
        {"id": "VCP_V55_CAPACITY_C_12D_22R", "lb": 12, "bb_pct": 0.60, "dry_vol": 0.95, "thrust_vol": 1.30, "min_clv": 0.60, "stop": "SHELF", "target_r": 2.2, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Capacity C: 12D Fast Base, 0.60 BB Pct, 1.30x Vol, 2.2R Target"},

        # 4. Component Ablation Suite (Isolated on VCP_V55_BALANCED_A)
        {"id": "VCP_ABL_1_NO_BB_PINCH", "lb": 15, "bb_pct": 1.00, "dry_vol": 0.85, "thrust_vol": 1.4, "min_clv": 0.60, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 1: Remove BB Contraction Pinch (1.00 vs 0.50 BB Pct)"},
        {"id": "VCP_ABL_2_NO_VOL_DRYUP", "lb": 15, "bb_pct": 0.50, "dry_vol": 2.00, "thrust_vol": 1.4, "min_clv": 0.60, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 2: Remove Prior Vol Dryup Requirement (2.00x vs 0.85x)"},
        {"id": "VCP_ABL_3_NO_VOL_THRUST", "lb": 15, "bb_pct": 0.50, "dry_vol": 0.85, "thrust_vol": 1.0, "min_clv": 0.60, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 3: Remove Breakout Vol Thrust (1.0x vs 1.4x)"},
        {"id": "VCP_ABL_4_NO_CLV_REQ", "lb": 15, "bb_pct": 0.50, "dry_vol": 0.85, "thrust_vol": 1.4, "min_clv": 0.00, "stop": "SHELF", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 4: Remove CLV Close Strength Requirement (0.00 vs 0.60)"},
        {"id": "VCP_ABL_5_STOP_FIXED_15ATR", "lb": 15, "bb_pct": 0.50, "dry_vol": 0.85, "thrust_vol": 1.4, "min_clv": 0.60, "stop": "FIXED_1_5", "target_r": 2.5, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 5: Fixed 1.5x ATR Stop (vs Structural Shelf Stop)"},
        {"id": "VCP_ABL_6_TARGET_30R", "lb": 15, "bb_pct": 0.50, "dry_vol": 0.85, "thrust_vol": 1.4, "min_clv": 0.60, "stop": "SHELF", "target_r": 3.0, "horizon": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 6: Target Expansion to 3.0R (vs 2.5R)"},
    ]

    all_specs = challengers
    all_outcomes = []

    for fpath in files:
        sym = os.path.basename(fpath).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 80:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            # Enforce Weekend Ban
            df = df[df.index.dayofweek < 5]
        except Exception:
            continue

        n = len(df)
        c = df["Close"].values
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        v = df["Volume"].values if "Volume" in df.columns else np.ones(n) * 10000.0
        dates = df.index.strftime("%Y-%m-%d").values

        sma20 = pd.Series(c).rolling(20, min_periods=5).mean().values
        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr14 = pd.Series(h - l).rolling(14, min_periods=5).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=5).mean().values

        # Bollinger Band Width & Rolling Percentile
        std20 = pd.Series(c).rolling(20, min_periods=5).std().values
        bb_width = (4.0 * std20) / np.maximum(sma20, 1.0)
        bb_width_pctile = pd.Series(bb_width).rolling(60, min_periods=20).rank(pct=True).values

        # Rolling Highs for different lookbacks
        hh12 = pd.Series(h).shift(1).rolling(12, min_periods=6).max().values
        hh15 = pd.Series(h).shift(1).rolling(15, min_periods=8).max().values
        hh20 = pd.Series(h).shift(1).rolling(20, min_periods=10).max().values

        # Rolling Shelf Lows
        shelf8 = pd.Series(l).shift(1).rolling(8, min_periods=4).min().values

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

            if ci < 75.0:  # Minimum liquidity floor
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0
            bb_pct = bb_width_pctile[i] if not np.isnan(bb_width_pctile[i]) else 0.50
            day_range = max(0.01, hi - li)
            clv = (ci - li) / day_range

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            # Prior 3-day volume dryup check
            prior_3d_vol = np.mean(v[max(0, i - 3): i]) / avg_v if avg_v > 0 else 1.0

            shelf_l = shelf8[i] if not np.isnan(shelf8[i]) else li

            for spec in all_specs:
                vid = spec["id"]
                lb = spec["lb"]
                bb_req = spec["bb_pct"]
                dry_req = spec["dry_vol"]
                thrust_req = spec["thrust_vol"]
                min_clv_req = spec["min_clv"]
                stop_type = spec["stop"]
                target_r = spec["target_r"]
                horizon = spec["horizon"]
                allowed_regimes = spec["allowed_regimes"]

                if regime not in allowed_regimes:
                    continue

                # Select swing high lookback
                if lb == 12:
                    hh_val = hh12[i]
                elif lb == 15:
                    hh_val = hh15[i]
                else:
                    hh_val = hh20[i]

                if pd.isna(hh_val) or hh_val <= 0:
                    continue

                # 1. Breakout Condition
                if ci <= hh_val or ci <= oi:
                    continue

                # 2. Contraction & Volume Filters
                if bb_pct > bb_req:
                    continue
                if prior_3d_vol > dry_req:
                    continue
                if vol_ratio < thrust_req:
                    continue
                if clv < min_clv_req:
                    continue

                # 3. Stop Loss Calculation
                if stop_type == "SHELF":
                    sl_price = round(min(li, shelf_l) - (0.10 * atr_i), 2)
                elif stop_type == "HYBRID_1_5":
                    sl_price = round(max(min(li, shelf_l) - 0.10 * atr_i, ci - 1.5 * atr_i), 2)
                elif stop_type == "FIXED_1_5":
                    sl_price = round(ci - (1.5 * atr_i), 2)
                else:
                    sl_price = round(min(li, shelf_l) - (0.10 * atr_i), 2)

                risk = ci - sl_price
                if risk <= 0:
                    continue

                target_price = round(ci + (target_r * risk), 2)

                # 4. Forward Outcome Evaluation
                outcome_r = 0.0
                outcome_type = "EXPIRED"
                bars_held = 0

                for f_idx in range(i + 1, min(i + horizon + 1, n)):
                    bars_held += 1
                    fh = h[f_idx]
                    fl = l[f_idx]

                    if fl <= sl_price:
                        outcome_r = -1.0
                        outcome_type = "SL_HIT"
                        break

                    if fh >= target_price:
                        outcome_r = target_r
                        outcome_type = "TARGET_HIT"
                        break

                if outcome_type == "EXPIRED":
                    fc_final = c[min(i + horizon, n - 1)]
                    outcome_r = round((fc_final - ci) / risk, 4)
                    outcome_type = "EXPIRED_POS" if outcome_r > 0 else "EXPIRED_NEG"

                all_outcomes.append({
                    "symbol": sym,
                    "variant_id": vid,
                    "date": d_str,
                    "partition": part,
                    "regime": regime,
                    "entry_price": ci,
                    "stop_loss": sl_price,
                    "target_price": target_price,
                    "risk": round(risk, 2),
                    "target_r": target_r,
                    "r_multiple": outcome_r,
                    "outcome_type": outcome_type,
                    "bars_held": bars_held,
                })

    out_df = pd.DataFrame(all_outcomes)
    out_csv = os.path.join(_REPORTS_DIR, "vcp_v55_sample_expansion_outcomes.csv")
    out_df.to_csv(out_csv, index=False)
    print(f"\nSaved {len(out_df):,} total simulation evaluations to {out_csv}")
    print(f"Elapsed time: {time.time() - t0:.2f} seconds\n")

    # ── Summary Across Partitions ───────────────────────────────────────────
    print("=" * 115)
    print(f"{'VARIANT ID':<32} | {'PARTITION':<8} | {'N':>5} | {'WIN%':>6} | {'E[R]':>7} | {'PF':>5} | {'BEAR E[R]':>9} | {'NEUT E[R]':>9} | {'BULL E[R]':>9}")
    print("-" * 115)

    for spec in all_specs:
        vid = spec["id"]
        v_df = out_df[out_df["variant_id"] == vid]
        if v_df.empty:
            continue

        for part in ["DEV", "VAL", "HOLDOUT"]:
            p_df = v_df[v_df["partition"] == part]
            n_trades = len(p_df)
            if n_trades == 0:
                continue

            r_vals = p_df["r_multiple"].values
            wins = r_vals[r_vals > 0]
            losses = r_vals[r_vals < 0]
            win_pct = (len(wins) / n_trades) * 100.0
            er = np.mean(r_vals)
            tot_win = np.sum(wins)
            tot_loss = abs(np.sum(losses))
            pf = tot_win / tot_loss if tot_loss > 0 else (9.99 if tot_win > 0 else 0.0)

            bear_df = p_df[p_df["regime"] == "BEAR"]
            neut_df = p_df[p_df["regime"] == "NEUTRAL"]
            bull_df = p_df[p_df["regime"] == "BULL"]

            bear_er = f"{np.mean(bear_df['r_multiple'].values):+.3f}R" if len(bear_df) > 0 else "N/A"
            neut_er = f"{np.mean(neut_df['r_multiple'].values):+.3f}R" if len(neut_df) > 0 else "N/A"
            bull_er = f"{np.mean(bull_df['r_multiple'].values):+.3f}R" if len(bull_df) > 0 else "N/A"

            highlight = "🟢" if (part == "HOLDOUT" and er >= 0.30 and pf >= 1.50) else "  "
            print(f"{highlight}{vid:<30} | {part:<8} | {n_trades:>5} | {win_pct:>5.1f}% | {er:>+6.3f}R | {pf:>5.2f} | {bear_er:>9} | {neut_er:>9} | {bull_er:>9}")
        print("-" * 115)

    return out_df

if __name__ == "__main__":
    run_vcp_sample_expansion()
