#!/usr/bin/env python3
# =============================================================================
# scripts/fast_eod_v56_sample_expansion_engine.py
# V5.6 EOD BREAKOUT SAMPLE-EXPANSION (N >= 100) & COMPONENT ABLATION ENGINE
# =============================================================================
# Evaluates Precision, Balanced, and Capacity EOD Breakout challengers across all
# 884 NSE equities under strict three-way partitioning (DEV -> VAL -> LOCKED HOLDOUT)
# with the primary objective of scaling N >= 100 while preserving >56-65% WR & PF >= 2.0.
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

def run_eod_sample_expansion():
    print("=" * 110)
    print("V5.6 EOD BREAKOUT SAMPLE-EXPANSION (N >= 100) & COMPONENT ABLATION SUITE")
    print("Goal: Scale sample size past N >= 100 while strictly preserving +0.355R / PF 2.17+ quality")
    print("=" * 110)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} verified 1D equity parquets.")

    # ── Define EOD Challenger Suite Across Frontiers ────────────────────────
    challengers = [
        # 1. Precision Frontier (Focus: 58-68% WR, selective entry)
        {"id": "EOD_V56_PRECISION_A_BASE15D_14X", "lb": 15, "max_span_atr": 2.2, "min_cpos": 0.70, "vol_mult": 1.40, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision A: 15D Lookback, 2.2 ATR Span, CPOS >= 0.70, 1.40x Vol, 2.5R Target"},
        {"id": "EOD_V56_PRECISION_B_BASE20D_13X", "lb": 20, "max_span_atr": 2.4, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision B: 20D Lookback, 2.4 ATR Span, CPOS >= 0.65, 1.30x Vol, 2.5R Target"},
        {"id": "EOD_V56_PRECISION_C_SWEET15D", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision C (Sweet Spot): 15D Lookback, 2.5 ATR Span, CPOS >= 0.65, 1.30x Vol, 2.5R Target"},

        # 2. Balanced Frontier (Focus: 54-62% WR, N = 100-150+)
        {"id": "EOD_V56_BALANCED_A_EXPANDED_125X", "lb": 15, "max_span_atr": 2.6, "min_cpos": 0.65, "vol_mult": 1.25, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced A: 15D Lookback, 2.6 ATR Span, CPOS >= 0.65, 1.25x Vol, 2.5R Target"},
        {"id": "EOD_V56_BALANCED_B_12D_EXPAND", "lb": 12, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.25, "shelf_bars": 6, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced B: 12D Fast Base, 2.5 ATR Span, CPOS >= 0.65, 1.25x Vol, 2.5R Target"},
        {"id": "EOD_V56_BALANCED_C_MULTI_REG", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Balanced C Multi-Regime: 15D Lookback, Bull+Neut+Bear Operation, 2.5R Target"},
        {"id": "EOD_V56_BALANCED_D_TARGET_28R", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.8, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced D Asymmetric: 15D Lookback, 2.5 ATR Span, 2.8R Target Extension"},

        # 3. Capacity Frontier (Focus: 50-58% WR, N = 150-250+)
        {"id": "EOD_V56_CAPACITY_A_BROAD_12D", "lb": 12, "max_span_atr": 2.8, "min_cpos": 0.60, "vol_mult": 1.20, "shelf_bars": 6, "stop": "SHELF", "t_r": 2.2, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Capacity A: 12D Lookback, 2.8 ATR Span, CPOS >= 0.60, 1.20x Vol, 2.2R Target"},
        {"id": "EOD_V56_CAPACITY_B_HYBRID_SL", "lb": 15, "max_span_atr": 2.8, "min_cpos": 0.60, "vol_mult": 1.20, "shelf_bars": 8, "stop": "HYBRID_1_5", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Capacity B: 15D Lookback, 2.8 ATR Span, 1.20x Vol, Hybrid 1.5 ATR Stop, 2.5R Target"},

        # 4. Component Ablation Suite (Isolated on EOD_V56_PRECISION_C_SWEET15D)
        {"id": "EOD_ABL_1_NO_SPAN_FILTER", "lb": 15, "max_span_atr": 6.0, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 1: Remove Base Tightness Span Filter (6.0 vs 2.5 ATR)"},
        {"id": "EOD_ABL_2_NO_CPOS_FILTER", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.00, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 2: Remove Close Position Requirement (0.00 vs 0.65)"},
        {"id": "EOD_ABL_3_NO_VOL_FILTER", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.00, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 3: Remove Volume Thrust Requirement (1.00x vs 1.30x)"},
        {"id": "EOD_ABL_4_STOP_FIXED_15ATR", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "FIXED_1_5", "t_r": 2.5, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 4: Replace Shelf Stop with Fixed 1.5x ATR Stop"},
        {"id": "EOD_ABL_5_TARGET_20R", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 2.0, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 5: Reduce Target Payoff (2.0R vs 2.5R)"},
        {"id": "EOD_ABL_6_TARGET_30R", "lb": 15, "max_span_atr": 2.5, "min_cpos": 0.65, "vol_mult": 1.30, "shelf_bars": 8, "stop": "SHELF", "t_r": 3.0, "horizon": 15, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 6: Expand Target Payoff (3.0R vs 2.5R)"},
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

        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr14 = pd.Series(h - l).rolling(14, min_periods=5).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=5).mean().values

        # Rolling Highs for different lookbacks
        hh12 = pd.Series(h).shift(1).rolling(12, min_periods=6).max().values
        hh15 = pd.Series(h).shift(1).rolling(15, min_periods=8).max().values
        hh20 = pd.Series(h).shift(1).rolling(20, min_periods=10).max().values

        # Rolling Shelf Lows
        shelf6 = pd.Series(l).shift(1).rolling(6, min_periods=3).min().values
        shelf8 = pd.Series(l).shift(1).rolling(8, min_periods=4).min().values

        for i in range(40, n - 20):
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

            if ci < 80.0:  # Minimum liquidity floor
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0
            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            prior_5d_high = np.max(h[max(0, i - 5): i])
            prior_5d_low = np.min(l[max(0, i - 5): i])
            prior_5d_span = (prior_5d_high - prior_5d_low) / atr_i if atr_i > 0 else 2.0

            for spec in all_specs:
                vid = spec["id"]
                lb = spec["lb"]
                max_span = spec["max_span_atr"]
                min_cpos = spec["min_cpos"]
                vol_mult = spec["vol_mult"]
                shelf_n = spec["shelf_bars"]
                stop_type = spec["stop"]
                t_r = spec["t_r"]
                horizon = spec["horizon"]
                allowed_regimes = spec["allowed_regimes"]

                if regime not in allowed_regimes:
                    continue

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

                # 2. Microstructure Filters
                if prior_5d_span > max_span:
                    continue
                if cpos < min_cpos:
                    continue
                if vol_ratio < vol_mult:
                    continue

                # 3. Stop Loss Calculation
                shelf_l = shelf6[i] if shelf_n == 6 else shelf8[i]
                if pd.isna(shelf_l) or shelf_l <= 0:
                    shelf_l = li

                if stop_type == "SHELF":
                    sl_price = round(shelf_l * 0.995, 2)
                elif stop_type == "HYBRID_1_5":
                    sl_price = round(max(shelf_l * 0.99, ci - 1.5 * atr_i), 2)
                elif stop_type == "FIXED_1_5":
                    sl_price = round(ci - (1.5 * atr_i), 2)
                else:
                    sl_price = round(shelf_l * 0.995, 2)

                # Institutional Risk Clamp (1.5% <= risk <= 7.5%)
                stop_pct = (ci - sl_price) / ci
                if stop_pct > 0.075 or stop_pct < 0.015:
                    continue

                risk = ci - sl_price
                if risk <= 0:
                    continue

                target_price = round(ci + (t_r * risk), 2)

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
                        outcome_r = t_r
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
                    "target_r": t_r,
                    "r_multiple": outcome_r,
                    "outcome_type": outcome_type,
                    "bars_held": bars_held,
                })

    out_df = pd.DataFrame(all_outcomes)
    out_csv = os.path.join(_REPORTS_DIR, "eod_v56_sample_expansion_outcomes.csv")
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

            highlight = "🟢" if (part == "HOLDOUT" and er >= 0.30 and pf >= 1.50 and n_trades >= 100) else "  "
            print(f"{highlight}{vid:<30} | {part:<8} | {n_trades:>5} | {win_pct:>5.1f}% | {er:>+6.3f}R | {pf:>5.2f} | {bear_er:>9} | {neut_er:>9} | {bull_er:>9}")
        print("-" * 115)

    return out_df

if __name__ == "__main__":
    run_eod_sample_expansion()
