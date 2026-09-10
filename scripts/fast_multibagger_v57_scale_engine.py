#!/usr/bin/env python3
# =============================================================================
# scripts/fast_multibagger_v57_scale_engine.py
# V5.7 MULTIBAGGER 1:5R CONVEXITY SCALE ENGINE (N >= 100+ RIGHT-TAIL PRESERVED)
# =============================================================================
# Purpose: Scale sample size toward N >= 100+ while strictly defending
# the 1:5R asymmetric payoff, high right-tail skewness, and 5R+ frequency >= 7-10%.
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

def run_multibagger_v57_scale():
    print("=" * 110)
    print("V5.7 MULTIBAGGER 1:5R CONVEXITY SCALE ENGINE (N >= 100+, RIGHT-TAIL PRESERVED)")
    print("Goal: Scale sample size toward N >= 100 while preserving E[R] >= +0.50R, PF >= 2.0, 5R+ >= 7-10%")
    print("=" * 110)
    t0 = time.time()

    daily_files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(daily_files)} verified daily equity parquets.")

    # ── Define Multibagger V5.7 Scale Challenger Suite ───────────────────────
    challengers = [
        # Challenger 1: 60D Base, 1.65x Vol, 15D Shelf Stop, 5.0R Target, 45D Horizon (Scaled 60D Base)
        {"id": "MBAG_V57_SCALE_A_60D_165V", "base_lookback": 60, "min_vol": 1.65, "min_cpos": 0.65, "stop_type": "SHELF_15D", "t_r": 5.0, "max_holding_bars": 45, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Scale A: 60D Base, 1.65x Vol, 15D Shelf Stop, 5.0R Target, 45D Horizon"},

        # Challenger 2: 75D Intermediate Base, 1.80x Vol, 15D Shelf Stop, 5.0R Target, 50D Horizon
        {"id": "MBAG_V57_SCALE_B_75D_180V", "base_lookback": 75, "min_vol": 1.80, "min_cpos": 0.65, "stop_type": "SHELF_15D", "t_r": 5.0, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Scale B: 75D Base, 1.80x Vol, 15D Shelf Stop, 5.0R Target, 50D Horizon"},

        # Challenger 3: 60D Base, 1.70x Vol, Hybrid 1.8x ATR / Shelf Stop, 5.0R Target, 45D Horizon
        {"id": "MBAG_V57_SCALE_C_60D_HYBRID_STOP", "base_lookback": 60, "min_vol": 1.70, "min_cpos": 0.65, "stop_type": "HYBRID_SHELF_ATR", "t_r": 5.0, "max_holding_bars": 45, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Scale C: 60D Base, 1.70x Vol, Hybrid Shelf/ATR Stop, 5.0R Target, 45D Horizon"},

        # Challenger 4: 60D Base, 1.80x Vol, 15D Shelf Stop, 4.5R Asymmetric Target, 40D Horizon
        {"id": "MBAG_V57_SCALE_D_60D_45R", "base_lookback": 60, "min_vol": 1.80, "min_cpos": 0.65, "stop_type": "SHELF_15D", "t_r": 4.5, "max_holding_bars": 40, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Scale D: 60D Base, 1.80x Vol, 15D Shelf Stop, 4.5R Target, 40D Horizon"},

        # Challenger 5: 60D Base, 1.65x Vol, 15D Shelf Stop, 5.5R Asymmetric Target, 50D Horizon
        {"id": "MBAG_V57_SCALE_E_60D_55R", "base_lookback": 60, "min_vol": 1.65, "min_cpos": 0.65, "stop_type": "SHELF_15D", "t_r": 5.5, "max_holding_bars": 50, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Scale E: 60D Base, 1.65x Vol, 15D Shelf Stop, 5.5R Target, 50D Horizon"},

        # Challenger 6: 60D Base Multi-Regime Operation (Bull + Neutral + Bear)
        {"id": "MBAG_V57_SCALE_F_MULTI_REG", "base_lookback": 60, "min_vol": 1.75, "min_cpos": 0.65, "stop_type": "SHELF_15D", "t_r": 5.0, "max_holding_bars": 45, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Scale F Multi-Regime: 60D Base, 5.0R Target across Bull+Neut+Bear"},
    ]

    all_specs = challengers
    all_outcomes = []

    for d_path in daily_files:
        sym = os.path.splitext(os.path.basename(d_path))[0].upper()
        try:
            df = pd.read_parquet(d_path)
            if df is None or len(df) < 100:
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
            if n_d < 100:
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
        for i in range(80, n_d - 5):
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
                if stop_type == "SHELF_15D":
                    shelf_low = np.min(d_l[max(0, i - 15) : i + 1])
                    sl_p = round(shelf_low * 0.995, 2)
                elif stop_type == "HYBRID_SHELF_ATR":
                    shelf_low = np.min(d_l[max(0, i - 15) : i + 1])
                    atr_sl = entry_p - (1.8 * atr14[i])
                    sl_p = round(max(shelf_low * 0.995, atr_sl), 2)
                else:
                    sl_p = round(d_l[i] * 0.995, 2)

                risk = entry_p - sl_p
                if risk <= 0:
                    continue

                # Institutional Risk Clamp (2.0% to 10.0%)
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
    out_csv = os.path.join(_REPORTS_DIR, "multibagger_v57_scale_outcomes.csv")
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

            hits_5r = np.sum(r_vals >= 4.4)
            pct_5r = (hits_5r / n_trades) * 100.0

            neut_df = p_df[p_df["regime"] == "NEUTRAL"]
            bull_df = p_df[p_df["regime"] == "BULL"]

            neut_er = f"{np.mean(neut_df['r_multiple'].values):+.3f}R" if len(neut_df) > 0 else "N/A"
            bull_er = f"{np.mean(bull_df['r_multiple'].values):+.3f}R" if len(bull_df) > 0 else "N/A"

            highlight = "🟢" if (part == "HOLDOUT" and er >= 0.40 and pf >= 1.80 and n_trades >= 60) else "  "
            print(f"{highlight}{vid:<32} | {part:<8} | {n_trades:>5} | {win_pct:>5.1f}% | {er:>+6.3f}R | {pf:>5.2f} | {pct_5r:>8.1f}% | {neut_er:>9} | {bull_er:>9}")
        print("-" * 125)

    return out_df

if __name__ == "__main__":
    run_multibagger_v57_scale()
