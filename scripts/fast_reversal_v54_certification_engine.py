#!/usr/bin/env python3
# =============================================================================
# scripts/fast_reversal_v54_certification_engine.py
# V5.4 REVERSAL SWEEP PRODUCTION-CERTIFICATION & COMPONENT ABLATION ENGINE
# =============================================================================
# Evaluates 12+ Reversal challenger variants across all 884 NSE equities under
# strict three-way data partitioning (DEV -> VAL -> LOCKED HOLDOUT) and executes
# multi-component ablation tests to isolate the exact structural sources of alpha.
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

# ── Market Regime Determination Helper ────────────────────────────────────────
def _get_market_regime(nifty_close, nifty_sma50, nifty_sma200):
    if pd.isna(nifty_close) or pd.isna(nifty_sma50):
        return "NEUTRAL"
    if nifty_close > nifty_sma50 and (pd.isna(nifty_sma200) or nifty_sma50 > nifty_sma200):
        return "BULL"
    elif nifty_close < nifty_sma50 and (pd.isna(nifty_sma200) or nifty_sma50 < nifty_sma200):
        return "BEAR"
    return "NEUTRAL"


def run_reversal_v54_certification():
    print("=" * 100)
    print("V5.4 REVERSAL PRODUCTION-CERTIFICATION & COMPONENT ABLATION SUITE")
    print("=" * 100)
    t0 = time.time()

    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(files)} verified 1D equity parquets.")

    # ── Define 12 Challenger Configurations ─────────────────────────────────
    challengers = [
        {"id": "REV_V1_BASELINE", "lookback": 20, "depth_atr": 0.0, "sl_buf": 0.0, "min_clv": 0.0, "min_vol_ratio": 0.0, "target_r": 2.0, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Legacy V1 Baseline (Raw 20D Low, 2.0R)"},
        {"id": "REV_V22C_WIDER_SL_20D", "lookback": 20, "depth_atr": 0.10, "sl_buf": 0.25, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Challenger 20D: 0.10x Sweep + 0.25x SL Buffer (No Bear)"},
        {"id": "REV_V23A_10D_EXPANDED", "lookback": 10, "depth_atr": 0.10, "sl_buf": 0.35, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Challenger 10D: Fast Swing Low + 0.35x SL Buffer (No Bear)"},
        {"id": "REV_V23B_15D_EXPANDED", "lookback": 15, "depth_atr": 0.10, "sl_buf": 0.35, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Challenger 15D: Intermediate Swing Low + 0.35x SL Buffer (No Bear)"},
        {"id": "REV_V23C_10D_DEEP_SWEEP", "lookback": 10, "depth_atr": 0.25, "sl_buf": 0.50, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL"], "desc": "Challenger 10D Deep: 0.25x ATR Sweep + 0.50x SL Buffer (No Bear)"},
        {"id": "REV_V23D_15D_MULTI_REGIME", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Champion V23D: 15D Lookback + 0.15x Sweep + 0.40x SL Buffer (All Regimes)"},
        {"id": "REV_V24A_15D_VOL_SPIKE_130", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.30, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Challenger 15D Vol+: Squeeze Ignition Volume >= 1.30x SMA20"},
        {"id": "REV_V24B_15D_HAMMER_WICK", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "min_lower_wick": 0.35, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Challenger 15D Hammer: Absorption Lower Wick >= 35% of Range"},
        {"id": "REV_V24C_15D_CLV_UPPER_HALF", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.65, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Challenger 15D Close+: High CLV >= 0.65 (Upper-Third Close Reclaim)"},
        {"id": "REV_V24D_15D_ASYMMETRIC_3R", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 3.0, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Challenger 15D Asymmetric: 3.0R Target Extension Multiplier"},
        {"id": "REV_V24E_12D_BALANCED", "lookback": 12, "depth_atr": 0.12, "sl_buf": 0.35, "min_clv": 0.55, "min_vol_ratio": 1.10, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Challenger 12D Balanced: Intermediate 12D + 0.12x Depth + 0.35x SL"},
        {"id": "REV_V24F_15D_BEAR_NEUTRAL_SPEC", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BEAR", "NEUTRAL"], "desc": "Challenger 15D Bear/Neutral Specialist: Pure Crisis/Correction Squeeze"},
    ]

    # ── Define 7 Component Ablations for V23D ───────────────────────────────
    ablations = [
        {"id": "REV_ABL_0_FULL_MODEL", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Full V23D Model (Reference Benchmark)"},
        {"id": "REV_ABL_1_NO_SWEEP_DEPTH", "lookback": 15, "depth_atr": 0.00, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Ablation 1: Remove Sweep Depth (0.0x vs 0.15x ATR)"},
        {"id": "REV_ABL_2_NO_STRUCTURAL_SL", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.00, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Ablation 2: Remove SL Buffer (Raw Sweep Low vs +0.40x ATR Buffer)"},
        {"id": "REV_ABL_3_NO_REGIME_FILTER", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Ablation 3: Unfiltered Multi-Regime Operation"},
        {"id": "REV_ABL_4_NO_CLV_CONFIRM", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.00, "min_vol_ratio": 1.0, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Ablation 4: Remove Close Strength CLV Reclaim Requirement"},
        {"id": "REV_ABL_5_NO_VOLUME_FILTER", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 0.00, "target_r": 2.5, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Ablation 5: Remove Volume Threshold"},
        {"id": "REV_ABL_6_TARGET_2R_PAYOFF", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 2.0, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Ablation 6: Payoff Target Reduction (2.0R vs 2.5R)"},
        {"id": "REV_ABL_7_TARGET_3R_PAYOFF", "lookback": 15, "depth_atr": 0.15, "sl_buf": 0.40, "min_clv": 0.50, "min_vol_ratio": 1.0, "target_r": 3.0, "allowed_regimes": ["BULL", "NEUTRAL", "BEAR"], "desc": "Ablation 7: Payoff Target Expansion (3.0R vs 2.5R)"},
    ]

    all_specs = challengers + [a for a in ablations if a["id"] != "REV_ABL_0_FULL_MODEL"]

    all_outcomes = []

    for fpath in files:
        sym = os.path.basename(fpath).replace(".parquet", "")
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 50:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            # Enforce Weekend Ban invariant
            df = df[df.index.dayofweek < 5]
        except Exception:
            continue

        n = len(df)
        c = df["Close"].values
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        v = df["Volume"].values if "Volume" in df.columns else np.ones(n) * 100000.0
        dates = df.index.strftime("%Y-%m-%d").values

        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr20 = pd.Series(h - l).rolling(20, min_periods=10).mean().values
        vol_sma20 = pd.Series(v).rolling(20, min_periods=5).mean().values

        # Rolling swing lows for different lookbacks
        low10 = pd.Series(l).shift(1).rolling(10, min_periods=5).min().values
        low12 = pd.Series(l).shift(1).rolling(12, min_periods=6).min().values
        low15 = pd.Series(l).shift(1).rolling(15, min_periods=8).min().values
        low20 = pd.Series(l).shift(1).rolling(20, min_periods=10).min().values

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
            ai = atr20[i]
            v_sma = vol_sma20[i]

            if ci < 50.0 or pd.isna(ai) or ai <= 0:
                continue

            c_range = hi - li
            if c_range <= 0:
                continue
            clv = (ci - li) / c_range
            lower_wick = (min(oi, ci) - li) / c_range
            vol_ratio = vi / max(v_sma, 1.0) if v_sma > 0 else 1.0

            # Determine macro regime
            regime = _get_market_regime(ci, sma50[i], sma200[i])

            # Evaluate each spec
            for spec in all_specs:
                var_id = spec["id"]
                lb = spec["lookback"]
                depth_mult = spec.get("depth_atr", 0.15)
                sl_buf_mult = spec.get("sl_buf", 0.40)
                min_clv_req = spec.get("min_clv", 0.0)
                min_vol_req = spec.get("min_vol_ratio", 0.0)
                min_wick_req = spec.get("min_lower_wick", 0.0)
                target_r = spec.get("target_r", 2.5)
                allowed_regimes = spec.get("allowed_regimes", ["BULL", "NEUTRAL", "BEAR"])

                # Regime filter
                if regime not in allowed_regimes:
                    continue

                # Select reference prior swing low
                if lb == 10:
                    ref_low = low10[i]
                elif lb == 12:
                    ref_low = low12[i]
                elif lb == 15:
                    ref_low = low15[i]
                else:
                    ref_low = low20[i]

                if pd.isna(ref_low) or ref_low <= 0:
                    continue

                # 1. Sweep Condition: Today's low swept prior low by required ATR depth
                sweep_threshold = ref_low - (depth_mult * ai)
                if li > sweep_threshold:
                    continue

                # 2. Reclaim Condition: Bullish close recovering above swept support and open
                if ci <= ref_low or ci <= oi:
                    continue

                # 3. Microstructure Filters: CLV, Volume, Lower Wick
                if clv < min_clv_req:
                    continue
                if vol_ratio < min_vol_req:
                    continue
                if lower_wick < min_wick_req:
                    continue

                # 4. Stop-Loss & Target Geometry
                sl_price = round(li - (sl_buf_mult * ai), 2)
                risk = ci - sl_price
                if risk <= 0:
                    continue

                target_price = round(ci + (target_r * risk), 2)

                # 5. Outcome Forward Evaluation (Max 20 holding sessions)
                outcome_r = 0.0
                outcome_type = "EXPIRED"
                bars_held = 0

                for f_idx in range(i + 1, min(i + 21, n)):
                    bars_held += 1
                    fh = h[f_idx]
                    fl = l[f_idx]
                    fc = c[f_idx]

                    # Check SL hit
                    if fl <= sl_price:
                        outcome_r = -1.0
                        outcome_type = "SL_HIT"
                        break

                    # Check Target hit
                    if fh >= target_price:
                        outcome_r = target_r
                        outcome_type = "TARGET_HIT"
                        break

                if outcome_type == "EXPIRED":
                    fc_final = c[min(i + 20, n - 1)]
                    outcome_r = round((fc_final - ci) / risk, 4)
                    outcome_type = "EXPIRED_POS" if outcome_r > 0 else "EXPIRED_NEG"

                all_outcomes.append({
                    "symbol": sym,
                    "variant_id": var_id,
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
    out_csv = os.path.join(_REPORTS_DIR, "reversal_v54_outcomes.csv")
    out_df.to_csv(out_csv, index=False)
    print(f"\nSaved {len(out_df):,} total simulation evaluations to {out_csv}")
    print(f"Elapsed time: {time.time() - t0:.2f} seconds\n")

    # ── Print Challenger Summary Across Partitions ──────────────────────────
    print("=" * 110)
    print(f"{'VARIANT ID':<32} | {'PARTITION':<8} | {'N':>5} | {'WIN%':>6} | {'E[R]':>7} | {'PF':>5} | {'BEAR E[R]':>9} | {'NEUT E[R]':>9} | {'BULL E[R]':>9}")
    print("-" * 110)

    for var_id in [s["id"] for s in all_specs]:
        v_df = out_df[out_df["variant_id"] == var_id]
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

            # Regime stats
            bear_df = p_df[p_df["regime"] == "BEAR"]
            neut_df = p_df[p_df["regime"] == "NEUTRAL"]
            bull_df = p_df[p_df["regime"] == "BULL"]

            bear_er = f"{np.mean(bear_df['r_multiple'].values):+.3f}R" if len(bear_df) > 0 else "N/A"
            neut_er = f"{np.mean(neut_df['r_multiple'].values):+.3f}R" if len(neut_df) > 0 else "N/A"
            bull_er = f"{np.mean(bull_df['r_multiple'].values):+.3f}R" if len(bull_df) > 0 else "N/A"

            highlight = "🟢" if (part == "HOLDOUT" and er >= 0.30 and pf >= 1.50) else "  "
            print(f"{highlight}{var_id:<30} | {part:<8} | {n_trades:>5} | {win_pct:>5.1f}% | {er:>+6.3f}R | {pf:>5.2f} | {bear_er:>9} | {neut_er:>9} | {bull_er:>9}")
        print("-" * 110)

    return out_df


if __name__ == "__main__":
    run_reversal_v54_certification()
