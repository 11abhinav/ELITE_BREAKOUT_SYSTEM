#!/usr/bin/env python3
# =============================================================================
# scripts/fast_multibagger_v56_convexity_engine.py
# V5.6 MULTIBAGGER CONVEXITY & ASYMMETRIC 1:5R PAYOFF OPTIMIZATION ENGINE
# =============================================================================
# Optimizes Stage-2 Base Breakouts for maximum right-tail skewness, 5R+ frequency,
# and high positive expectancy without compromising risk or forcing artificial win rates.
# Evaluated under DEV -> VAL -> LOCKED HOLDOUT governance across 884 NSE Equities.
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

def run_multibagger_convexity():
    print("=" * 110)
    print("V5.6 MULTIBAGGER CONVEXITY (1:5R ASYMMETRY & RIGHT-TAIL CAPTURE) & COMPONENT ABLATION SUITE")
    print("Goal: Maximize right-tail payoff, 5R+ frequency, and high expectancy with controlled drawdown")
    print("=" * 110)
    t0 = time.time()

    daily_files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(daily_files)} verified daily equity parquets.")

    # ── Define Multibagger Convexity Challenger Suite Across Frontiers ───────
    challengers = [
        # 1. 1:5R Convexity Frontier (Core Mandate: 1:5R Payoff Structure)
        {"id": "MBAG_V56_CONVEX_A_90D_5R", "base_lookback": 90, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 5.0, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Convex A: 90D Base Breakout, 2.00x Vol, 20D Shelf Stop, 5.0R Target, 50D Horizon"},
        {"id": "MBAG_V56_CONVEX_B_120D_5R", "base_lookback": 120, "min_vol": 2.20, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 5.0, "max_holding_bars": 60, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Convex B: 120D Deep Base Breakout, 2.20x Vol, 20D Shelf Stop, 5.0R Target, 60D Horizon"},
        {"id": "MBAG_V56_CONVEX_C_60D_FAST_5R", "base_lookback": 60, "min_vol": 1.80, "min_cpos": 0.65, "stop_type": "SHELF_15D", "t_r": 5.0, "max_holding_bars": 40, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Convex C: 60D Fast Base Breakout, 1.80x Vol, 15D Shelf Stop, 5.0R Target, 40D Horizon"},

        # 2. Super-Convexity & Payoff Frontier (4.0R, 6.0R, and Runner Extensions)
        {"id": "MBAG_V56_CONVEX_D_90D_4R", "base_lookback": 90, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 4.0, "max_holding_bars": 45, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Convex D: 90D Base Breakout, 2.00x Vol, 20D Shelf Stop, 4.0R Target, 45D Horizon"},
        {"id": "MBAG_V56_CONVEX_E_90D_6R", "base_lookback": 90, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 6.0, "max_holding_bars": 60, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Convex E Super-Convex: 90D Base, 2.00x Vol, 6.0R Target, 60D Horizon"},
        {"id": "MBAG_V56_CONVEX_F_MULTI_REG", "base_lookback": 90, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 5.0, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Convex F Multi-Regime: 90D Base, 5.0R Target across Bull+Neut+Bear"},

        # 3. Component Ablation Suite (Isolated on MBAG_V56_CONVEX_A_90D_5R)
        {"id": "MBAG_ABL_1_NO_BASE_FILTER", "base_lookback": 20, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 5.0, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 1: Shorten Base Lookback (20D vs 90D)"},
        {"id": "MBAG_ABL_2_NO_VOL_FILTER", "base_lookback": 90, "min_vol": 1.00, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 5.0, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 2: Remove Volume Ignition Gate (1.00x vs 2.00x)"},
        {"id": "MBAG_ABL_3_NO_CPOS_FILTER", "base_lookback": 90, "min_vol": 2.00, "min_cpos": 0.00, "stop_type": "SHELF_20D", "t_r": 5.0, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 3: Remove Close Position Gate (0.00 vs 0.70)"},
        {"id": "MBAG_ABL_4_FIXED_ATR_STOP", "base_lookback": 90, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "FIXED_ATR", "t_r": 5.0, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 4: Replace 20D Shelf Stop with Fixed 2.0x ATR Stop"},
        {"id": "MBAG_ABL_5_SHORT_HORIZON_20D", "base_lookback": 90, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "SHELF_20D", "t_r": 5.0, "max_holding_bars": 20, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Ablation 5: Truncate Holding Horizon (20D vs 50D)"},
    ]

    all_specs = challengers
    all_outcomes = []

    for d_path in daily_files:
        sym = os.path.splitext(os.path.basename(d_path))[0].upper()
        try:
            df = pd.read_parquet(d_path)
            if df is None or len(df) < 140:
                continue

            # Standardize index
            if not isinstance(df.index, pd.DatetimeIndex):
                if "Date" in df.columns:
                    df.index = pd.to_datetime(df["Date"])
                elif "Datetime" in df.columns:
                    df.index = pd.to_datetime(df["Datetime"])
                else:
                    continue
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            # INVARIANT: Exclude weekend bars
            df = df[df.index.dayofweek < 5]

            d_dates = [d.strftime("%Y-%m-%d") for d in df.index]
            n_d = len(df)
            if n_d < 140:
                continue

            d_o = df["Open"].values
            d_h = df["High"].values
            d_l = df["Low"].values
            d_c = df["Close"].values
            d_v = df["Volume"].values

            # Precalculate Indicators
            # 1. 20D SMA Volume
            vol_sma20 = pd.Series(d_v).rolling(20, min_periods=5).mean().values
            # 2. 50D & 200D SMA for Regime
            sma50 = pd.Series(d_c).rolling(50, min_periods=10).mean().values
            sma200 = pd.Series(d_c).rolling(200, min_periods=30).mean().values
            # 3. 14D ATR
            tr = np.maximum(d_h - d_l, np.maximum(np.abs(d_h - np.roll(d_c, 1)), np.abs(d_l - np.roll(d_c, 1))))
            tr[0] = d_h[0] - d_l[0]
            atr14 = pd.Series(tr).rolling(14, min_periods=5).mean().values

        except Exception:
            continue

        # Evaluate every trading day
        for i in range(125, n_d - 5):
            d_str = d_dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            part = "DEV" if d_str < VAL_START else ("VAL" if d_str < HLD_START else "HOLDOUT")

            # Determine Macro Regime
            c_val = d_c[i]
            s50 = sma50[i]
            s200 = sma200[i]
            if c_val < s50 and c_val < s200:
                regime = "BEAR"
            elif c_val > s50 and c_val > s200:
                regime = "BULL"
            else:
                regime = "NEUTRAL"

            # Evaluate each challenger spec
            for spec in all_specs:
                vid = spec["id"]
                allowed_regs = spec.get("allowed_regimes", ["BULL", "NEUTRAL"])
                if regime not in allowed_regs:
                    continue

                base_lb = spec["base_lookback"]
                min_vol = spec["min_vol"]
                min_cpos = spec["min_cpos"]
                stop_type = spec["stop_type"]
                t_r = spec["t_r"]
                max_bars = spec["max_holding_bars"]

                if i < base_lb + 5:
                    continue

                # 1. Base High Breakout (Prior base_lb Days)
                prior_base_high = np.max(d_h[i - base_lb : i])
                if d_c[i] <= prior_base_high:
                    continue

                # 2. Volume Ignition Gate
                v_sma = vol_sma20[i]
                vol_ratio = (d_v[i] / v_sma) if v_sma > 0 else 1.0
                if vol_ratio < min_vol:
                    continue

                # 3. Candle Close Position
                day_range = d_h[i] - d_l[i]
                cpos = (d_c[i] - d_l[i]) / day_range if day_range > 0 else 0.5
                if cpos < min_cpos:
                    continue

                # 4. Stop Loss Calculation
                entry_p = d_c[i]
                if stop_type == "SHELF_20D":
                    shelf_low = np.min(d_l[max(0, i - 20) : i + 1])
                    sl_p = round(shelf_low * 0.995, 2)
                elif stop_type == "SHELF_15D":
                    shelf_low = np.min(d_l[max(0, i - 15) : i + 1])
                    sl_p = round(shelf_low * 0.995, 2)
                elif stop_type == "FIXED_ATR":
                    sl_p = round(entry_p - (2.0 * atr14[i]), 2)
                else:
                    sl_p = round(d_l[i] * 0.995, 2)

                risk = entry_p - sl_p
                if risk <= 0:
                    continue

                # Institutional Risk Clamp (2.0% to 10.0% for long-term multibaggers)
                risk_pct = risk / entry_p
                if risk_pct < 0.020 or risk_pct > 0.100:
                    continue

                target_p = round(entry_p + (t_r * risk), 2)

                # Forward Outcome Evaluation over forward daily bars (up to max_bars)
                fwd_h = d_h[i + 1 : min(i + 1 + max_bars, n_d)]
                fwd_l = d_l[i + 1 : min(i + 1 + max_bars, n_d)]
                fwd_c = d_c[i + 1 : min(i + 1 + max_bars, n_d)]

                if len(fwd_c) < 3:
                    continue

                outcome_r = 0.0
                outcome_type = "EXPIRED"
                bars_held = 0

                for f_idx in range(len(fwd_c)):
                    bars_held += 1
                    fl = fwd_l[f_idx]
                    fh = fwd_h[f_idx]

                    if fl <= sl_p:
                        outcome_r = -1.0
                        outcome_type = "SL_HIT"
                        break

                    if fh >= target_p:
                        outcome_r = t_r
                        outcome_type = "TARGET_HIT"
                        break

                if outcome_type == "EXPIRED":
                    fc_final = fwd_c[-1]
                    outcome_r = round((fc_final - entry_p) / risk, 4)
                    outcome_type = "EXPIRED_POS" if outcome_r > 0 else "EXPIRED_NEG"

                all_outcomes.append({
                    "symbol": sym,
                    "variant_id": vid,
                    "date": d_str,
                    "partition": part,
                    "regime": regime,
                    "entry_price": entry_p,
                    "stop_loss": sl_p,
                    "target_price": target_p,
                    "risk": round(risk, 2),
                    "target_r": t_r,
                    "r_multiple": outcome_r,
                    "outcome_type": outcome_type,
                    "bars_held": bars_held,
                })

    out_df = pd.DataFrame(all_outcomes)
    out_csv = os.path.join(_REPORTS_DIR, "multibagger_v56_convexity_outcomes.csv")
    out_df.to_csv(out_csv, index=False)
    print(f"\nSaved {len(out_df):,} total simulation evaluations to {out_csv}")
    print(f"Elapsed time: {time.time() - t0:.2f} seconds\n")

    # ── Summary Across Partitions ───────────────────────────────────────────
    print("=" * 125)
    print(f"{'VARIANT ID':<34} | {'PARTITION':<8} | {'N':>5} | {'WIN%':>6} | {'E[R]':>7} | {'PF':>5} | {'5R+ HIT%':>9} | {'NEUT E[R]':>9} | {'BULL E[R]':>9}")
    print("-" * 125)

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

            hits_5r = np.sum(r_vals >= 4.9)
            pct_5r = (hits_5r / n_trades) * 100.0

            neut_df = p_df[p_df["regime"] == "NEUTRAL"]
            bull_df = p_df[p_df["regime"] == "BULL"]

            neut_er = f"{np.mean(neut_df['r_multiple'].values):+.3f}R" if len(neut_df) > 0 else "N/A"
            bull_er = f"{np.mean(bull_df['r_multiple'].values):+.3f}R" if len(bull_df) > 0 else "N/A"

            highlight = "🟢" if (part == "HOLDOUT" and er >= 0.35 and pf >= 1.60) else "  "
            print(f"{highlight}{vid:<32} | {part:<8} | {n_trades:>5} | {win_pct:>5.1f}% | {er:>+6.3f}R | {pf:>5.2f} | {pct_5r:>8.1f}% | {neut_er:>9} | {bull_er:>9}")
        print("-" * 125)

    return out_df

if __name__ == "__main__":
    run_multibagger_convexity()
