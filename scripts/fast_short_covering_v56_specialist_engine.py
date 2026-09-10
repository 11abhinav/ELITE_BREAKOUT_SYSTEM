#!/usr/bin/env python3
# =============================================================================
# scripts/fast_short_covering_v56_specialist_engine.py
# V5.6 SHORT COVERING SPECIALIST ENGINE (BEAR/NEUTRAL REGIME SPECIFIC)
# =============================================================================
# Implements a dedicated Bear/Neutral Short Covering Specialist architecture:
# Failed Breakdown -> Exhaustion -> Reclaim -> Volume/Squeeze Confirmation
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

def run_short_covering_specialist():
    print("=" * 110)
    print("V5.6 SHORT COVERING SPECIALIST (BEAR/NEUTRAL SQUEEZE) & COMPONENT ABLATION SUITE")
    print("Architecture: Failed Breakdown -> Exhaustion Undercut -> Reclaim -> Squeeze Confirmation")
    print("=" * 110)
    t0 = time.time()

    daily_files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(daily_files)} verified daily equity parquets.")

    # ── Define Short Covering Specialist Challenger Suite Across Frontiers ───
    challengers = [
        # 1. Precision Squeeze Frontier (Focus: 55-65% WR, selective Bear/Neutral squeeze)
        {"id": "SC_V56_PRECISION_A_25X_VOL", "breakdown_lookback": 15, "max_rsi": 38.0, "min_vol": 2.20, "min_cpos": 0.70, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Precision A: 15D Breakdown Reclaim, RSI <= 38, 2.2x Vol, CPOS >= 0.70, 2.5R Target"},
        {"id": "SC_V56_PRECISION_B_20X_VOL", "breakdown_lookback": 15, "max_rsi": 40.0, "min_vol": 2.00, "min_cpos": 0.70, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Precision B: 15D Breakdown Reclaim, RSI <= 40, 2.0x Vol, CPOS >= 0.70, 2.5R Target"},
        {"id": "SC_V56_PRECISION_C_TIGHT_SL", "breakdown_lookback": 10, "max_rsi": 40.0, "min_vol": 2.00, "min_cpos": 0.75, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Precision C: 10D Breakdown Reclaim, RSI <= 40, 2.0x Vol, CPOS >= 0.75, 2.5R Target"},

        # 2. Balanced Squeeze Frontier (Focus: 50-60% WR, Higher Trade Sample)
        {"id": "SC_V56_BALANCED_A_15D_16X", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Balanced A (Core): 15D Breakdown Reclaim, RSI <= 42, 1.60x Vol, CPOS >= 0.65, 2.5R Target"},
        {"id": "SC_V56_BALANCED_B_10D_FAST", "breakdown_lookback": 10, "max_rsi": 45.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Balanced B: 10D Fast Reclaim, RSI <= 45, 1.60x Vol, CPOS >= 0.65, 2.5R Target"},
        {"id": "SC_V56_BALANCED_C_BEAR_ONLY", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR"], "desc": "Balanced C: Pure Bear Market Correction Squeeze Specialist"},
        {"id": "SC_V56_BALANCED_D_MULTI_REG", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL", "BULL"], "desc": "Balanced D: Multi-Regime Benchmark (Unrestricted Regime)"},

        # 3. Component Ablation Suite (Isolated on SC_V56_BALANCED_A_15D_16X)
        {"id": "SC_ABL_1_NO_RECLAIM_FILTER", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "no_reclaim": True, "desc": "Ablation 1: Remove Reclaim Requirement (Buy breakdown blindly)"},
        {"id": "SC_ABL_2_NO_RSI_FILTER", "breakdown_lookback": 15, "max_rsi": 100.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Ablation 2: Remove RSI Exhaustion Filter (RSI <= 100 vs <= 42)"},
        {"id": "SC_ABL_3_NO_VOL_SURGE", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.00, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Ablation 3: Remove Volume Surge Requirement (1.00x vs 1.60x)"},
        {"id": "SC_ABL_4_NO_CPOS_FILTER", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.60, "min_cpos": 0.00, "stop_type": "UNDERCUT_LOW", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Ablation 4: Remove Candle Close Position Filter (0.00 vs 0.65)"},
        {"id": "SC_ABL_5_FIXED_ATR_STOP", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "FIXED_ATR", "t_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Ablation 5: Replace Undercut Shelf Stop with Fixed 1.5x ATR Stop"},
        {"id": "SC_ABL_6_TARGET_30R", "breakdown_lookback": 15, "max_rsi": 42.0, "min_vol": 1.60, "min_cpos": 0.65, "stop_type": "UNDERCUT_LOW", "t_r": 3.0, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Ablation 6: Extend Target to 3.0R (vs 2.5R)"},
    ]

    all_specs = challengers
    all_outcomes = []

    for d_path in daily_files:
        sym = os.path.splitext(os.path.basename(d_path))[0].upper()
        try:
            df = pd.read_parquet(d_path)
            if df is None or len(df) < 60:
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
            if n_d < 60:
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
            # 4. 14D RSI
            delta = pd.Series(d_c).diff()
            gain = delta.where(delta > 0, 0.0).rolling(14, min_periods=5).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=5).mean()
            rs = gain / (loss + 1e-6)
            rsi14 = (100.0 - (100.0 / (1.0 + rs))).values

        except Exception:
            continue

        # Evaluate every trading day
        for i in range(40, n_d - 5):
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
                allowed_regs = spec.get("allowed_regimes", ["BEAR", "NEUTRAL"])
                if regime not in allowed_regs:
                    continue

                bk_len = spec["breakdown_lookback"]
                max_rsi = spec["max_rsi"]
                min_vol = spec["min_vol"]
                min_cpos = spec["min_cpos"]
                stop_type = spec["stop_type"]
                t_r = spec["t_r"]
                no_reclaim = spec.get("no_reclaim", False)

                if i < bk_len + 5:
                    continue

                # 1. Check prior swing low (Breakdown Shelf) over [i - bk_len - 5 : i - 2]
                prior_shelf_low = np.min(d_l[i - bk_len - 5 : i - 2])
                
                # 2. Check breakdown occurred in the last 1 to 4 days
                recent_undercut_low = np.min(d_l[i - 4 : i])
                did_breakdown = recent_undercut_low < prior_shelf_low

                if not did_breakdown and not no_reclaim:
                    continue

                # 3. Exhaustion: RSI must be oversold
                rsi_val = rsi14[i]
                if rsi_val > max_rsi:
                    continue

                # 4. Reclaim: Today's close must reclaim back ABOVE the prior shelf low
                if not no_reclaim:
                    if d_c[i] <= prior_shelf_low:
                        continue

                # 5. Volume Surge Confirmation
                v_sma = vol_sma20[i]
                vol_ratio = (d_v[i] / v_sma) if v_sma > 0 else 1.0
                if vol_ratio < min_vol:
                    continue

                # 6. Candle Close Position
                day_range = d_h[i] - d_l[i]
                cpos = (d_c[i] - d_l[i]) / day_range if day_range > 0 else 0.5
                if cpos < min_cpos:
                    continue

                # 7. Stop Loss Calculation
                entry_p = d_c[i]
                if stop_type == "UNDERCUT_LOW":
                    # Place SL 0.5% below the exhaustion undercut low
                    sl_p = round(min(recent_undercut_low, d_l[i]) * 0.995, 2)
                elif stop_type == "FIXED_ATR":
                    sl_p = round(entry_p - (1.5 * atr14[i]), 2)
                else:
                    sl_p = round(d_l[i] * 0.995, 2)

                risk = entry_p - sl_p
                if risk <= 0:
                    continue

                # Institutional Risk Clamp (1.5% to 8.5%)
                risk_pct = risk / entry_p
                if risk_pct < 0.015 or risk_pct > 0.085:
                    continue

                target_p = round(entry_p + (t_r * risk), 2)

                # Forward Outcome Evaluation over forward daily bars (up to 20 days)
                fwd_h = d_h[i + 1 : min(i + 21, n_d)]
                fwd_l = d_l[i + 1 : min(i + 21, n_d)]
                fwd_c = d_c[i + 1 : min(i + 21, n_d)]

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
    out_csv = os.path.join(_REPORTS_DIR, "short_covering_v56_specialist_outcomes.csv")
    out_df.to_csv(out_csv, index=False)
    print(f"\nSaved {len(out_df):,} total simulation evaluations to {out_csv}")
    print(f"Elapsed time: {time.time() - t0:.2f} seconds\n")

    # ── Summary Across Partitions ───────────────────────────────────────────
    print("=" * 115)
    print(f"{'VARIANT ID':<34} | {'PARTITION':<8} | {'N':>5} | {'WIN%':>6} | {'E[R]':>7} | {'PF':>5} | {'BEAR E[R]':>9} | {'NEUT E[R]':>9} | {'BULL E[R]':>9}")
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

            highlight = "🟢" if (part == "HOLDOUT" and er >= 0.20 and pf >= 1.50) else "  "
            print(f"{highlight}{vid:<32} | {part:<8} | {n_trades:>5} | {win_pct:>5.1f}% | {er:>+6.3f}R | {pf:>5.2f} | {bear_er:>9} | {neut_er:>9} | {bull_er:>9}")
        print("-" * 115)

    return out_df

if __name__ == "__main__":
    run_short_covering_specialist()
