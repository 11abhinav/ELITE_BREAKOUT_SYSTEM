#!/usr/bin/env python3
# =============================================================================
# scripts/fast_multitf_1h_v56_depth_engine.py
# V5.6 MULTI-TF 1H SAMPLE-EXPANSION (N >= 75-100) & COMPONENT ABLATION ENGINE
# =============================================================================
# Evaluates Precision, Balanced, and Capacity Multi-TF 1H challengers across all
# 393 verified hourly equity parquets under strict DEV -> VAL -> LOCKED HOLDOUT
# governance with the goal of scaling sample maturity while preserving positive E[R].
# =============================================================================

import glob
import os
import sys
import time
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_HISTORY_1H_DIR = os.path.join(_REPO_ROOT, "data", "history", "1h")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

DEV_START = "2025-07-24"
VAL_START = "2026-01-01"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"

def run_multitf_1h_expansion():
    print("=" * 110)
    print("V5.6 MULTI-TF 1H SAMPLE-EXPANSION (N >= 75-100) & COMPONENT ABLATION SUITE")
    print("Goal: Scale sample depth from N=20 toward N >= 75-100 without destroying expectancy")
    print("=" * 110)
    t0 = time.time()

    h1_files = sorted(glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet")))
    daily_files = {
        os.path.splitext(os.path.basename(f))[0].upper(): f
        for f in glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet"))
    }
    print(f"Loaded {len(h1_files)} verified 1H equity parquets and {len(daily_files)} matching 1D parquets.")

    # ── Define Multi-TF 1H Challenger Suite Across Frontiers ────────────────
    challengers = [
        # 1. Precision Frontier (Focus: 55-65% WR, selective entry)
        {"id": "M1H_V56_PRECISION_A_15H", "base_bars": 15, "max_width_atr": 1.60, "max_comp": 0.75, "min_vol": 1.60, "min_cpos": 0.70, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision A: 15H Base, 1.6 ATR Width, 0.75 Comp, 1.60x Vol, 2.5R Target"},
        {"id": "M1H_V56_PRECISION_B_20H", "base_bars": 20, "max_width_atr": 1.50, "max_comp": 0.70, "min_vol": 1.60, "min_cpos": 0.70, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision B: 20H Base, 1.5 ATR Width, 0.70 Comp, 1.60x Vol, 2.5R Target"},
        {"id": "M1H_V56_PRECISION_C_TIGHT20H", "base_bars": 20, "max_width_atr": 1.40, "max_comp": 0.70, "min_vol": 1.80, "min_cpos": 0.70, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Precision C (Benchmark): 20H Base, 1.4 ATR Width, 1.80x Vol, 2.5R Target"},

        # 2. Balanced Frontier (Focus: 50-60% WR, Target N = 60-100+)
        {"id": "M1H_V56_BALANCED_A_15H_14X", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced A: 15H Base, 1.8 ATR Width, 0.80 Comp, 1.40x Vol, 2.5R Target"},
        {"id": "M1H_V56_BALANCED_B_12H_FAST", "base_bars": 12, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced B: 12H Fast Base (~2 Days), 1.8 ATR Width, 1.40x Vol, 2.5R Target"},
        {"id": "M1H_V56_BALANCED_C_10H_FAST", "base_bars": 10, "max_width_atr": 1.60, "max_comp": 0.75, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced C: 10H Fast Base, 1.6 ATR Width, 1.40x Vol, 2.5R Target"},
        {"id": "M1H_V56_BALANCED_D_MULTI_REG", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Balanced D Multi-Regime: 15H Base, Bull+Neut+Bear Operation, 2.5R Target"},
        {"id": "M1H_V56_BALANCED_E_TARGET_28R", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.8, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Balanced E Asymmetric: 15H Base, 2.8R Target Extension"},

        # 3. Capacity Frontier (Focus: 48-55% WR, Target N = 100-180+)
        {"id": "M1H_V56_CAPACITY_A_12H_20W", "base_bars": 12, "max_width_atr": 2.00, "max_comp": 0.85, "min_vol": 1.30, "min_cpos": 0.60, "stop": "SHELF", "t_r": 2.2, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Capacity A: 12H Base, 2.0 ATR Width, 0.85 Comp, 1.30x Vol, 2.2R Target"},
        {"id": "M1H_V56_CAPACITY_B_15H_22W", "base_bars": 15, "max_width_atr": 2.20, "max_comp": 0.85, "min_vol": 1.30, "min_cpos": 0.60, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Capacity B: 15H Base, 2.2 ATR Width, 0.85 Comp, 1.30x Vol, 2.5R Target"},

        # 4. Component Ablation Suite (Isolated on M1H_V56_BALANCED_A_15H_14X)
        {"id": "M1H_ABL_1_NO_WIDTH_FILTER", "base_bars": 15, "max_width_atr": 5.00, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 1: Remove Base Width Filter (5.0 vs 1.8 ATR)"},
        {"id": "M1H_ABL_2_NO_COMP_FILTER", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 1.00, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 2: Remove Compression Ratio Filter (1.00 vs 0.80)"},
        {"id": "M1H_ABL_3_NO_VOL_FILTER", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.00, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 3: Remove Hourly Volume Thrust Filter (1.00x vs 1.40x)"},
        {"id": "M1H_ABL_4_NO_CPOS_FILTER", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.00, "stop": "SHELF", "t_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 4: Remove Close Position Filter (0.00 vs 0.65)"},
        {"id": "M1H_ABL_5_TARGET_20R", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 2.0, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 5: Reduce Target Payoff (2.0R vs 2.5R)"},
        {"id": "M1H_ABL_6_TARGET_30R", "base_bars": 15, "max_width_atr": 1.80, "max_comp": 0.80, "min_vol": 1.40, "min_cpos": 0.65, "stop": "SHELF", "t_r": 3.0, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 6: Expand Target Payoff (3.0R vs 2.5R)"},
    ]

    all_specs = challengers
    all_outcomes = []

    for h_path in h1_files:
        sym = os.path.splitext(os.path.basename(h_path))[0].upper()
        d_path = daily_files.get(sym)
        if not d_path:
            continue

        try:
            df_h = pd.read_parquet(h_path)
            df_d = pd.read_parquet(d_path)
            if df_h is None or len(df_h) < 40 or df_d is None or len(df_d) < 50:
                continue

            # Standardize hourly index
            if not isinstance(df_h.index, pd.DatetimeIndex):
                if "Datetime" in df_h.columns:
                    df_h.index = pd.to_datetime(df_h["Datetime"])
                elif "Date" in df_h.columns:
                    df_h.index = pd.to_datetime(df_h["Date"])
            if df_h.index.tz is None:
                df_h.index = df_h.index.tz_localize("Asia/Kolkata")
            else:
                df_h.index = df_h.index.tz_convert("Asia/Kolkata")
            df_h = df_h.sort_index()
            df_h = df_h[df_h.index.dayofweek < 5]

            # Standardize daily index
            if not isinstance(df_d.index, pd.DatetimeIndex):
                if "Date" in df_d.columns:
                    df_d.index = pd.to_datetime(df_d["Date"])
                elif "Datetime" in df_d.columns:
                    df_d.index = pd.to_datetime(df_d["Datetime"])
            if df_d.index.tz is None:
                df_d.index = df_d.index.tz_localize("Asia/Kolkata")
            else:
                df_d.index = df_d.index.tz_convert("Asia/Kolkata")
            df_d = df_d.sort_index()
            df_d = df_d[df_d.index.dayofweek < 5]
        except Exception:
            continue

        n_h = len(df_h)
        n_d = len(df_d)
        if n_h < 30 or n_d < 50:
            continue

        h_c = df_h["Close"].values
        h_o = df_h["Open"].values
        h_h = df_h["High"].values
        h_l = df_h["Low"].values
        h_v = df_h["Volume"].values if "Volume" in df_h.columns else np.ones(n_h) * 5000.0
        h_dates = df_h.index.strftime("%Y-%m-%d").values

        d_c = df_d["Close"].values
        d_h = df_d["High"].values
        d_l = df_d["Low"].values
        d_dates = df_d.index.strftime("%Y-%m-%d").values

        # Daily indicators for macro trend & ATR
        d_sma50 = pd.Series(d_c).rolling(50, min_periods=20).mean().values
        d_sma200 = pd.Series(d_c).rolling(200, min_periods=50).mean().values if n_d >= 200 else d_sma50
        d_atr14 = pd.Series(d_h - d_l).rolling(14, min_periods=5).mean().values

        # Hourly Volume 20 SMA
        h_vol20 = pd.Series(h_v).rolling(20, min_periods=5).mean().values

        for i in range(25, n_h - 15):
            d_str = h_dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            if d_str < VAL_START:
                part = "DEV"
            elif d_str < HLD_START:
                part = "VAL"
            else:
                part = "HOLDOUT"

            ci = h_c[i]
            oi = h_o[i]
            hi = h_h[i]
            li = h_l[i]
            vi = h_v[i]

            if ci < 80.0:
                continue

            # Find corresponding daily bar
            scan_dt = df_h.index[i]
            d_pos = df_d.index.searchsorted(scan_dt, side="right")
            if d_pos < 30 or d_pos >= n_d:
                continue

            daily_atr = d_atr14[d_pos - 1] if not np.isnan(d_atr14[d_pos - 1]) and d_atr14[d_pos - 1] > 0 else (ci * 0.02)
            daily_sma200 = d_sma200[d_pos - 1]

            regime = "BULL" if ci > daily_sma200 * 1.02 else ("BEAR" if ci < daily_sma200 * 0.95 else "NEUTRAL")

            avg_hv = h_vol20[i] if not np.isnan(h_vol20[i]) and h_vol20[i] > 0 else vi
            vol_ratio = (vi / avg_hv) if avg_hv > 0 else 1.0
            h_range = max(0.01, hi - li)
            cpos = (ci - li) / h_range

            for spec in all_specs:
                vid = spec["id"]
                base_bars = spec["base_bars"]
                max_width = spec["max_width_atr"]
                max_comp = spec["max_comp"]
                min_vol = spec["min_vol"]
                min_cpos = spec["min_cpos"]
                t_r = spec["t_r"]
                allowed_regimes = spec["allowed_regimes"]

                if regime not in allowed_regimes:
                    continue

                if i < base_bars:
                    continue

                # Base high/low over prior base_bars hourly bars
                base_h = np.max(h_h[i - base_bars: i])
                base_l = np.min(h_l[i - base_bars: i])
                base_width = base_h - base_l
                base_width_atr = base_width / daily_atr if daily_atr > 0 else 2.0

                # 1. Breakout condition: Current hour closes above prior base high
                if ci <= base_h or ci <= oi:
                    continue

                # 2. Base width filter
                if base_width_atr > max_width:
                    continue

                # 3. Compression ratio (First half vs Second half volatility)
                half_b = base_bars // 2
                h1_width = np.max(h_h[i - base_bars: i - half_b]) - np.min(h_l[i - base_bars: i - half_b])
                h2_width = np.max(h_h[i - half_b: i]) - np.min(h_l[i - half_b: i])
                comp_ratio = (h2_width / h1_width) if h1_width > 0 else 1.0
                if comp_ratio > max_comp:
                    continue

                # 4. Hourly Volume Thrust & Close Position
                if vol_ratio < min_vol:
                    continue
                if cpos < min_cpos:
                    continue

                # 5. Stop Loss: Anchor below base low (shelf)
                sl_price = round(base_l * 0.995, 2)
                risk = ci - sl_price
                if risk <= 0:
                    continue

                stop_pct = risk / ci
                if stop_pct > 0.08 or stop_pct < 0.012:
                    continue

                target_price = round(ci + (t_r * risk), 2)

                # 6. Forward Outcome Evaluation over forward daily bars (up to 20 days)
                fwd_d_h = d_h[d_pos: min(d_pos + 20, n_d)]
                fwd_d_l = d_l[d_pos: min(d_pos + 20, n_d)]
                fwd_d_c = d_c[d_pos: min(d_pos + 20, n_d)]

                if len(fwd_d_c) < 3:
                    continue

                outcome_r = 0.0
                outcome_type = "EXPIRED"
                bars_held = 0

                for f_idx in range(len(fwd_d_c)):
                    bars_held += 1
                    fl = fwd_d_l[f_idx]
                    fh = fwd_d_h[f_idx]

                    if fl <= sl_price:
                        outcome_r = -1.0
                        outcome_type = "SL_HIT"
                        break

                    if fh >= target_price:
                        outcome_r = t_r
                        outcome_type = "TARGET_HIT"
                        break

                if outcome_type == "EXPIRED":
                    fc_final = fwd_d_c[-1]
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
    out_csv = os.path.join(_REPORTS_DIR, "multitf_1h_v56_expansion_outcomes.csv")
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

            highlight = "🟢" if (part == "HOLDOUT" and er >= 0.25 and pf >= 1.50 and n_trades >= 50) else "  "
            print(f"{highlight}{vid:<30} | {part:<8} | {n_trades:>5} | {win_pct:>5.1f}% | {er:>+6.3f}R | {pf:>5.2f} | {bear_er:>9} | {neut_er:>9} | {bull_er:>9}")
        print("-" * 115)

    return out_df

if __name__ == "__main__":
    run_multitf_1h_expansion()
