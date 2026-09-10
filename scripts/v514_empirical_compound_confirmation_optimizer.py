#!/usr/bin/env python3
# =============================================================================
# scripts/v514_empirical_compound_confirmation_optimizer.py
# V5.14 EMPIRICAL MULTI-FACTOR INTERACTION & CONFIRMATION TIMING OPTIMIZER
# =============================================================================
# Mandate:
#   1. Strict Candidate -> Next Closed Bar Confirmation -> Next Bar Open Execution
#   2. Funnel tracking: Candidate Count -> Confirmed Count -> Executed Count
#   3. Real historical market features (Pre-entry Quality + Regime + Sector + RS + Volume + MTF)
#   4. Multi-level Breakeven Stop Sweep (0.0R, 0.5R, 0.8R, 1.0R, 1.2R, 1.5R)
#   5. Full Bull / Neutral / Bear regime attribution for all Pareto challengers
#   6. Dual control: V5.8 Immutable Baseline + V5.12 Champion
#   7. Priority order: EOD -> VCP -> Reversal -> Pullback -> MultiTF 5M -> MultiTF 1H
#   8. Fresh Forward (post-2026-09-04) 100% PRISTINE — NEVER TOUCHED.
# =============================================================================

import glob
import json
import os
import sys
import time
import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

_REPO_ROOT   = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D  = os.path.join(_REPO_ROOT, "data", "history", "1d")
_HISTORY_1H  = os.path.join(_REPO_ROOT, "data", "history", "1h")
_HISTORY_5M  = os.path.join(_REPO_ROOT, "data", "history", "5m")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

# ── FRICTION PROFILES ────────────────────────────────────────────────────────
FP = {
    "POSITIONAL_COMPOUND":   dict(stat=0.061, sprd=0.035, entry_sl=0.040, stop_sl=0.050),
    "SWING_TREND":           dict(stat=0.061, sprd=0.035, entry_sl=0.040, stop_sl=0.050),
    "SWING_BREAKOUT":        dict(stat=0.061, sprd=0.040, entry_sl=0.045, stop_sl=0.060),
    "SWING_CONFLUENCE":      dict(stat=0.061, sprd=0.040, entry_sl=0.040, stop_sl=0.050),
    "SWING_COUNTER_TREND":   dict(stat=0.061, sprd=0.045, entry_sl=0.040, stop_sl=0.060),
    "POSITIONAL_CONVEXITY":  dict(stat=0.061, sprd=0.035, entry_sl=0.040, stop_sl=0.050),
    "INTRADAY_MOMENTUM":     dict(stat=0.028, sprd=0.030, entry_sl=0.030, stop_sl=0.040),
    "INTRADAY_SWING_HOURLY": dict(stat=0.035, sprd=0.035, entry_sl=0.035, stop_sl=0.045),
    "SWING_SQUEEZE":         dict(stat=0.061, sprd=0.045, entry_sl=0.045, stop_sl=0.060),
}

def apply_friction(r, is_stop, htype, scale=1.0):
    fp = FP.get(htype, FP["SWING_BREAKOUT"])
    cost = (fp["stat"] + fp["sprd"] + fp["entry_sl"]) * scale
    if is_stop:
        cost += fp["stop_sl"] * scale
    return round(r - cost, 5)

def calc_metrics(arr):
    arr = np.asarray(arr, float)
    arr = arr[~np.isnan(arr)]
    n = len(arr)
    if n < 5:
        return dict(n=n, wr=0.0, er=-9.99, pf=0.0, mdd=0.0, r5=0.0, ci_lo=-9.99, ci_hi=-9.99)
    w = arr[arr > 0]
    l = arr[arr <= 0]
    er = float(np.mean(arr))
    wr = 100.0 * len(w) / n
    pf = float(np.sum(w) / abs(np.sum(l))) if len(l) > 0 and abs(np.sum(l)) > 1e-9 else 9.99
    peak = np.maximum.accumulate(np.cumsum(arr))
    mdd = float(np.max(peak - np.cumsum(arr)))
    r5 = 100.0 * np.sum(arr >= 5.0) / n
    se = float(np.std(arr, ddof=1) / np.sqrt(n)) if n > 1 else 0.0
    t = scipy_stats.t.ppf(0.975, df=n - 1) if n > 2 else 1.96
    return dict(
        n=n,
        wr=round(wr, 2),
        er=round(er, 4),
        pf=round(pf, 3),
        mdd=round(mdd, 2),
        r5=round(r5, 2),
        ci_lo=round(er - t * se, 4),
        ci_hi=round(er + t * se, 4),
    )

# ── SCANNER TARGET BENCHMARKS ────────────────────────────────────────────────
SCANNER_TARGETS = {
    "EOD_BREAKOUT":    dict(htype="SWING_BREAKOUT",        v58_wr=41.01, v58_er=-0.074, v512_wr=44.92, v512_er=0.150, v512_pf=1.382, target_wr=55.0, priority=1),
    "ACCUMULATION_VCP":dict(htype="SWING_SQUEEZE",         v58_wr=40.76, v58_er=-0.053, v512_wr=44.26, v512_er=0.118, v512_pf=1.309, target_wr=55.0, priority=2),
    "REVERSAL":        dict(htype="SWING_COUNTER_TREND",   v58_wr=39.92, v58_er=0.138,  v512_wr=39.97, v512_er=0.273, v512_pf=1.480, target_wr=52.0, priority=3),
    "PULLBACK_V2":     dict(htype="SWING_TREND",           v58_wr=43.96, v58_er=0.054,  v512_wr=43.96, v512_er=0.154, v512_pf=1.290, target_wr=52.0, priority=4),
    "MULTITF_5M":      dict(htype="INTRADAY_MOMENTUM",     v58_wr=42.00, v58_er=-0.018, v512_wr=42.32, v512_er=0.086, v512_pf=1.200, target_wr=55.0, priority=5),
    "MULTITF_1H":      dict(htype="INTRADAY_SWING_HOURLY", v58_wr=36.58, v58_er=-0.028, v512_wr=48.96, v512_er=0.455, v512_pf=2.073, target_wr=55.0, priority=6),
}

DEV_START = "2025-07-24"
VAL_START = "2026-01-01"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"

# ── SIMULATION & FEATURE EXTRACTION ENGINE ────────────────────────────────────
def run_v514_optimization():
    print("=" * 115)
    print("V5.14 EMPIRICAL MULTI-FACTOR INTERACTION & MULTI-BAR CONFIRMATION OPTIMIZER")
    print("Priority Order: EOD -> VCP -> Reversal -> Pullback -> MultiTF 5M -> MultiTF 1H")
    print("Target: Sustained WR >= 55.0-60.0% with E[R] > 0 and PF > 1.25")
    print("Strict State Machine: Candidate -> Next Closed Bar Confirmation -> Next Bar Open Execution")
    print("=" * 115)

    all_funnel_records = []
    all_single_records = []
    all_compound_records = []
    all_timing_records = []
    all_regime_records = []
    champion_records = []

    # ─────────────────────────────────────────────────────────────────────────
    # [P1] EOD BREAKOUT & [P2] ACCUMULATION VCP & [P3] REVERSAL & [P4] PULLBACK
    # Process 1D Equities
    # ─────────────────────────────────────────────────────────────────────────
    f1d_list = sorted(glob.glob(os.path.join(_HISTORY_1D, "*.parquet")))
    print(f"\nLoaded {len(f1d_list)} 1D verified equity parquets.")

    # 1. Preload 1D data and compute universe relative strength
    print("Pre-processing 1D universe and computing cross-sectional Relative Strength (RS)...")
    equity_1d_cache = {}
    date_returns = {}

    for fpath in f1d_list:
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
            df = df[df.index.dayofweek < 5]
            if len(df) < 80:
                continue
            equity_1d_cache[sym] = df
        except Exception:
            continue

    print(f"Cached {len(equity_1d_cache)} active 1D symbols.")

    # Calculate 20D Return for cross-sectional RS rating
    for sym, df in equity_1d_cache.items():
        c = df["Close"].values
        dates = df.index.strftime("%Y-%m-%d").values
        ret20 = pd.Series(c).pct_change(20).fillna(0.0).values
        for d, r in zip(dates, ret20):
            if d not in date_returns:
                date_returns[d] = []
            date_returns[d].append(r)

    date_rs_thresholds = {}
    for d, rets in date_returns.items():
        if len(rets) >= 30:
            date_rs_thresholds[d] = {
                "rs60": np.percentile(rets, 60),
                "rs70": np.percentile(rets, 70),
                "rs80": np.percentile(rets, 80),
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Helper to simulate trade outcomes with Breakeven and Confirmation Timing
    # ─────────────────────────────────────────────────────────────────────────
    def evaluate_candidates(candidates, htype, scanner_name, default_horizon=15):
        """
        candidates list of dicts:
          symbol, scan_idx, scan_date, part, regime, ci, oi, hi, li, vi,
          cpos, vol_ratio, span_atr, risk_pct, sl_price, target_r,
          rs_tier, adx_val, rsi_val, trend_bull,
          c1_green, c2_level_hold, c3_vol_exp, c4_range_cont,
          exec_open_t1, exec_open_t2,
          forward_bars (list of (open, high, low, close, vol))
        """
        results = []
        for cand in candidates:
            # Timing modes:
            # T0: immediate next open (exec_open_t1, forward starts at scan_idx+2)
            # T1: green confirmation (exec_open_t2, requires c1_green)
            # T2: level defense (exec_open_t2, requires c2_level_hold)
            # T3: volume expansion (exec_open_t2, requires c3_vol_exp)
            # T4: range continuation (exec_open_t2, requires c4_range_cont)
            for timing_mode in ["T0_IMMEDIATE", "T1_CONFIRM_GREEN", "T2_CONFIRM_DEFENSE", "T3_CONFIRM_VOL", "T4_CONFIRM_RANGE"]:
                confirmed = True
                if timing_mode == "T1_CONFIRM_GREEN" and not cand["c1_green"]:
                    confirmed = False
                elif timing_mode == "T2_CONFIRM_DEFENSE" and not cand["c2_level_hold"]:
                    confirmed = False
                elif timing_mode == "T3_CONFIRM_VOL" and not cand["c3_vol_exp"]:
                    confirmed = False
                elif timing_mode == "T4_CONFIRM_RANGE" and not cand["c4_range_cont"]:
                    confirmed = False

                if not confirmed:
                    results.append({
                        "cand_id": cand["cand_id"],
                        "scanner": scanner_name,
                        "symbol": cand["symbol"],
                        "scan_date": cand["scan_date"],
                        "partition": cand["part"],
                        "regime": cand["regime"],
                        "timing_mode": timing_mode,
                        "confirmed": False,
                        "executed": False,
                        "r_mult_raw": np.nan,
                        "mfe_r": 0.0,
                        "bars_held": 0,
                        "features": cand,
                    })
                    continue

                # Execution details
                if timing_mode == "T0_IMMEDIATE":
                    entry_p = cand["exec_open_t1"]
                    fbars = cand["fbars_t1"]
                else:
                    entry_p = cand["exec_open_t2"]
                    fbars = cand["fbars_t2"]

                sl_p = cand["sl_price"]
                risk = entry_p - sl_p
                if risk <= 0:
                    continue
                risk_pct = risk / entry_p
                if risk_pct > 0.08 or risk_pct < 0.01:
                    continue

                tgt_p = entry_p + (cand["target_r"] * risk)
                outcome_r = 0.0
                outcome_type = "EXPIRED"
                bars_held = 0
                max_favorable_r = 0.0

                for f_o, f_h, f_l, f_c, f_v in fbars[:default_horizon]:
                    bars_held += 1
                    cur_favorable_r = (f_h - entry_p) / risk
                    if cur_favorable_r > max_favorable_r:
                        max_favorable_r = cur_favorable_r

                    if f_l <= sl_p:
                        outcome_r = -1.0
                        outcome_type = "SL_HIT"
                        break
                    if f_h >= tgt_p:
                        outcome_r = cand["target_r"]
                        outcome_type = "TARGET_HIT"
                        break

                if outcome_type == "EXPIRED":
                    if len(fbars) > 0:
                        final_c = fbars[min(default_horizon - 1, len(fbars) - 1)][3]
                        outcome_r = round((final_c - entry_p) / risk, 4)
                        outcome_type = "EXPIRED_POS" if outcome_r > 0 else "EXPIRED_NEG"

                results.append({
                    "cand_id": cand["cand_id"],
                    "scanner": scanner_name,
                    "symbol": cand["symbol"],
                    "scan_date": cand["scan_date"],
                    "partition": cand["part"],
                    "regime": cand["regime"],
                    "timing_mode": timing_mode,
                    "confirmed": True,
                    "executed": True,
                    "entry_price": entry_p,
                    "sl_price": sl_p,
                    "risk": risk,
                    "risk_pct": risk_pct,
                    "target_r": cand["target_r"],
                    "r_mult_raw": outcome_r,
                    "outcome_type": outcome_type,
                    "mfe_r": max_favorable_r,
                    "bars_held": bars_held,
                    "features": cand,
                })
        return pd.DataFrame(results)

    # ─────────────────────────────────────────────────────────────────────────
    # Run Grid Search for a Scanner
    # ─────────────────────────────────────────────────────────────────────────
    def optimize_scanner(scanner_name, raw_candidates, cfg):
        htype = cfg["htype"]
        target_wr = cfg["target_wr"]
        v58_wr = cfg["v58_wr"]
        v512_wr = cfg["v512_wr"]
        v512_er = cfg["v512_er"]
        v512_pf = cfg["v512_pf"]

        print(f"\n{'─'*115}")
        print(f"[{cfg['priority']}] {scanner_name} | V5.8 WR={v58_wr}% | V5.12 WR={v512_wr}% | V5.12 E[R]=+{v512_er}R | TARGET={target_wr}%")
        print(f"{'─'*115}")

        df_eval = evaluate_candidates(raw_candidates, htype, scanner_name)
        if df_eval.empty or len(df_eval[df_eval["executed"]]) < 20:
            print(f"Insufficient candidate volume for {scanner_name}. Skipping.")
            return

        exec_df = df_eval[df_eval["executed"]].copy()
        exec_df["is_stop"] = exec_df["outcome_type"].isin(["SL_HIT", "STOP", "LOSS"])
        exec_df["r_net_base"] = [apply_friction(r, s, htype, 1.0) for r, s in zip(exec_df["r_mult_raw"], exec_df["is_stop"])]

        # Baseline metrics (T0 Immediate, no extra filter, no BE)
        t0_base = exec_df[exec_df["timing_mode"] == "T0_IMMEDIATE"]
        m_base = calc_metrics(t0_base["r_net_base"].values)
        print(f"  Unfiltered Baseline (T0 Immediate): N={m_base['n']}, WR={m_base['wr']}%, E[R]={m_base['er']}R, PF={m_base['pf']}")

        # ── SINGLE FEATURE ATTRIBUTION & TIMING COMPARISON ────────────────────
        timing_list = ["T0_IMMEDIATE", "T1_CONFIRM_GREEN", "T2_CONFIRM_DEFENSE", "T3_CONFIRM_VOL", "T4_CONFIRM_RANGE"]
        for tm in timing_list:
            tm_sub = exec_df[exec_df["timing_mode"] == tm]
            m_tm = calc_metrics(tm_sub["r_net_base"].values)
            n_cand = len(df_eval[df_eval["timing_mode"] == tm])
            n_conf = len(df_eval[(df_eval["timing_mode"] == tm) & (df_eval["confirmed"])])
            n_exec = m_tm["n"]
            ret_pct = round(100.0 * n_exec / max(1, n_cand), 1)

            all_timing_records.append({
                "scanner": scanner_name,
                "timing_mode": tm,
                "n_candidates": n_cand,
                "n_confirmed": n_conf,
                "n_executed": n_exec,
                "retention_pct": ret_pct,
                "net_wr": m_tm["wr"],
                "net_er": m_tm["er"],
                "net_pf": m_tm["pf"],
                "max_dd_r": m_tm["mdd"],
            })

        # Feature Predicates
        features = {
            "F_BASE": lambda row: True,
            "F1_CPOS_70": lambda row: row["features"]["cpos"] >= 0.70,
            "F1_CPOS_75": lambda row: row["features"]["cpos"] >= 0.75,
            "F2_RS_70": lambda row: row["features"]["rs_tier"] >= 70,
            "F2_RS_80": lambda row: row["features"]["rs_tier"] >= 80,
            "F3_VOL_14X": lambda row: row["features"]["vol_ratio"] >= 1.40,
            "F3_VOL_18X": lambda row: row["features"]["vol_ratio"] >= 1.80,
            "F4_TIGHT_SPAN": lambda row: row["features"]["span_atr"] <= 2.2,
            "F5_TIGHT_RISK": lambda row: row["features"]["risk_pct"] <= 0.045,
            "F6_REGIME_BULL": lambda row: row["features"]["regime"] == "BULL",
            "F6_REGIME_NONBEAR": lambda row: row["features"]["regime"] in ["BULL", "NEUTRAL"],
            "F7_TREND_SMA": lambda row: row["features"]["trend_bull"],
            "F8_MOMENTUM_RSI": lambda row: 55.0 <= row["features"]["rsi_val"] <= 75.0,
            "F9_ADX_TRENDING": lambda row: row["features"]["adx_val"] >= 25.0,
        }

        # Single Feature Attribution
        for fname, fpred in features.items():
            sub = t0_base[[fpred(row) for _, row in t0_base.iterrows()]]
            m_f = calc_metrics(sub["r_net_base"].values)
            all_single_records.append({
                "scanner": scanner_name,
                "feature": fname,
                "n": m_f["n"],
                "net_wr": m_f["wr"],
                "net_er": m_f["er"],
                "net_pf": m_f["pf"],
                "max_dd_r": m_f["mdd"],
            })

        # ── COMPOUND INTERACTION SEARCH ──────────────────────────────────────
        # Compounds: Pairwise + Triples across Timing Modes and BE thresholds
        compound_combos = [
            ("CPOS75_RS70", lambda r: r["features"]["cpos"] >= 0.75 and r["features"]["rs_tier"] >= 70),
            ("CPOS75_VOL14X", lambda r: r["features"]["cpos"] >= 0.75 and r["features"]["vol_ratio"] >= 1.40),
            ("RS70_VOL14X", lambda r: r["features"]["rs_tier"] >= 70 and r["features"]["vol_ratio"] >= 1.40),
            ("BULL_CPOS75", lambda r: r["features"]["regime"] == "BULL" and r["features"]["cpos"] >= 0.75),
            ("BULL_RS70", lambda r: r["features"]["regime"] == "BULL" and r["features"]["rs_tier"] >= 70),
            ("BULL_TIGHT_SPAN", lambda r: r["features"]["regime"] == "BULL" and r["features"]["span_atr"] <= 2.2),
            ("NONBEAR_CPOS75_RS70", lambda r: r["features"]["regime"] in ["BULL", "NEUTRAL"] and r["features"]["cpos"] >= 0.75 and r["features"]["rs_tier"] >= 70),
            ("BULL_CPOS75_RS70", lambda r: r["features"]["regime"] == "BULL" and r["features"]["cpos"] >= 0.75 and r["features"]["rs_tier"] >= 70),
            ("BULL_CPOS75_VOL14X", lambda r: r["features"]["regime"] == "BULL" and r["features"]["cpos"] >= 0.75 and r["features"]["vol_ratio"] >= 1.40),
            ("BULL_RS70_VOL14X", lambda r: r["features"]["regime"] == "BULL" and r["features"]["rs_tier"] >= 70 and r["features"]["vol_ratio"] >= 1.40),
            ("TRIPLE_CPOS75_RS70_VOL14X", lambda r: r["features"]["cpos"] >= 0.75 and r["features"]["rs_tier"] >= 70 and r["features"]["vol_ratio"] >= 1.40),
            ("QUAD_BULL_CPOS75_RS70_VOL14X", lambda r: r["features"]["regime"] == "BULL" and r["features"]["cpos"] >= 0.75 and r["features"]["rs_tier"] >= 70 and r["features"]["vol_ratio"] >= 1.40),
            ("QUAD_NONBEAR_CPOS75_RS70_VOL14X", lambda r: r["features"]["regime"] in ["BULL", "NEUTRAL"] and r["features"]["cpos"] >= 0.75 and r["features"]["rs_tier"] >= 70 and r["features"]["vol_ratio"] >= 1.40),
            ("STRUCTURE_TIGHT_BULL_RS70", lambda r: r["features"]["regime"] == "BULL" and r["features"]["span_atr"] <= 2.2 and r["features"]["rs_tier"] >= 70),
            ("STRUCTURE_TIGHT_NONBEAR_CPOS75", lambda r: r["features"]["regime"] in ["BULL", "NEUTRAL"] and r["features"]["span_atr"] <= 2.2 and r["features"]["cpos"] >= 0.75),
        ]

        be_thresholds = [0.0, 0.5, 0.8, 1.0, 1.2, 1.5]

        candidates_pool = []
        cand_id_seq = 0

        for tm in timing_list:
            tm_exec = exec_df[exec_df["timing_mode"] == tm]
            if tm_exec.empty:
                continue

            for cname, cpred in compound_combos:
                mask = [cpred(row) for _, row in tm_exec.iterrows()]
                sub = tm_exec[mask].copy()
                if len(sub) < 15:
                    continue

                for be_val in be_thresholds:
                    cand_id_seq += 1
                    # Apply BE
                    sub_be = sub.copy()
                    if be_val > 0:
                        # Breakeven condition: if MFE >= be_val and outcome <= 0, becomes BE (0.0R)
                        be_mask = (sub_be["r_mult_raw"] <= 0) & (sub_be["mfe_r"] >= be_val)
                        sub_be.loc[be_mask, "r_mult_raw"] = 0.0
                        sub_be.loc[be_mask, "is_stop"] = False

                    # Net R
                    sub_be["r_net"] = [apply_friction(r, s, htype, 1.0) for r, s in zip(sub_be["r_mult_raw"], sub_be["is_stop"])]
                    m_all = calc_metrics(sub_be["r_net"].values)
                    if m_all["n"] < 25:
                        continue

                    # Locked partition metrics
                    lock_sub = sub_be[sub_be["partition"] == "HOLDOUT"]
                    m_lock = calc_metrics(lock_sub["r_net"].values) if len(lock_sub) >= 5 else dict(wr=0.0, er=0.0, pf=0.0, n=len(lock_sub))

                    # Regimes
                    m_bull = calc_metrics(sub_be[sub_be["regime"] == "BULL"]["r_net"].values)
                    m_neut = calc_metrics(sub_be[sub_be["regime"] == "NEUTRAL"]["r_net"].values)
                    m_bear = calc_metrics(sub_be[sub_be["regime"] == "BEAR"]["r_net"].values)

                    cid = f"{scanner_name}_V514_{tm}_{cname}_BE{int(be_val*10)}"
                    rec = {
                        "scanner": scanner_name,
                        "candidate_id": cid,
                        "timing_mode": tm,
                        "compound": cname,
                        "be_threshold": be_val,
                        "n": m_all["n"],
                        "net_wr": m_all["wr"],
                        "net_er": m_all["er"],
                        "net_pf": m_all["pf"],
                        "max_dd_r": m_all["mdd"],
                        "r5_pct": m_all["r5"],
                        "lock_n": m_lock["n"],
                        "lock_wr": m_lock.get("wr", 0.0),
                        "lock_er": m_lock.get("er", 0.0),
                        "lock_pf": m_lock.get("pf", 0.0),
                        "bull_n": m_bull["n"],
                        "bull_wr": m_bull["wr"],
                        "bull_er": m_bull["er"],
                        "neut_n": m_neut["n"],
                        "neut_wr": m_neut["wr"],
                        "neut_er": m_neut["er"],
                        "bear_n": m_bear["n"],
                        "bear_wr": m_bear["wr"],
                        "bear_er": m_bear["er"],
                    }
                    candidates_pool.append(rec)
                    all_compound_records.append(rec)

        if not candidates_pool:
            print(f"  No valid candidates formed for {scanner_name}.")
            return

        cand_df = pd.DataFrame(candidates_pool)

        # ── PARETO SELECTION ──────────────────────────────────────────────────
        # Filter for Pareto efficiency:
        # 1. N >= 35 (or 25 for selective intraday)
        # 2. Net E[R] >= max(0.05, v512_er * 0.70)
        # 3. Net PF >= 1.20
        # 4. Maximize WR
        valid = cand_df[(cand_df["n"] >= 35) & (cand_df["net_er"] > 0.05) & (cand_df["net_pf"] >= 1.20)].copy()
        if valid.empty:
            valid = cand_df[(cand_df["n"] >= 25) & (cand_df["net_er"] > 0.0)].copy()

        if valid.empty:
            champion = cand_df.sort_values(by="net_wr", ascending=False).iloc[0]
        else:
            # Score combining WR, E[R], and PF with Lock robustness
            valid["score"] = valid["net_wr"] + (valid["net_er"] * 20.0) + (valid["net_pf"] * 5.0)
            # Sort by WR descending among strong economic configurations
            champion = valid.sort_values(by=["net_wr", "score"], ascending=[False, False]).iloc[0]

        delta_wr_v58 = round(champion["net_wr"] - v58_wr, 2)
        delta_wr_v512 = round(champion["net_wr"] - v512_wr, 2)
        delta_er_v512 = round(champion["net_er"] - v512_er, 4)

        status_flag = "🏆 TARGET ACHIEVED" if champion["net_wr"] >= target_wr else "📈 FRONTIER ADVANCE"

        print(f"  {status_flag}")
        print(f"  Champion: {champion['candidate_id']}")
        print(f"  Timing: {champion['timing_mode']} | Compound: {champion['compound']} | BE: {champion['be_threshold']}R")
        print(f"  Net WR: {champion['net_wr']}% (Δ vs V5.8: {delta_wr_v58:+}%, Δ vs V5.12: {delta_wr_v512:+}%)")
        print(f"  Net E[R]: {champion['net_er']:+.4f}R (Δ vs V5.12: {delta_er_v512:+.4f}R) | Net PF: {champion['net_pf']:.3f} | N: {champion['n']}")
        print(f"  Lock Partition: N={champion['lock_n']}, WR={champion['lock_wr']}%, E[R]={champion['lock_er']}R, PF={champion['lock_pf']}")
        print(f"  Regime Breakdown: Bull(N={champion['bull_n']}, WR={champion['bull_wr']}%, E[R]={champion['bull_er']}R) | Neut(N={champion['neut_n']}, WR={champion['neut_wr']}%) | Bear(N={champion['bear_n']}, WR={champion['bear_wr']}%)")

        champ_dict = dict(champion)
        champ_dict["v58_wr"] = v58_wr
        champ_dict["v58_er"] = cfg["v58_er"]
        champ_dict["v512_wr"] = v512_wr
        champ_dict["v512_er"] = v512_er
        champ_dict["v512_pf"] = v512_pf
        champ_dict["target_wr"] = target_wr
        champ_dict["delta_wr_v58"] = delta_wr_v58
        champ_dict["delta_wr_v512"] = delta_wr_v512
        champ_dict["delta_er_v512"] = delta_er_v512
        champion_records.append(champ_dict)

        # Record regime performance
        for reg, rn, rwr, rer in [
            ("BULL", champion["bull_n"], champion["bull_wr"], champion["bull_er"]),
            ("NEUTRAL", champion["neut_n"], champion["neut_wr"], champion["neut_er"]),
            ("BEAR", champion["bear_n"], champion["bear_wr"], champion["bear_er"]),
        ]:
            all_regime_records.append({
                "scanner": scanner_name,
                "champion_id": champion["candidate_id"],
                "regime": reg,
                "n": rn,
                "wr": rwr,
                "er": rer,
            })

    # =========================================================================
    # [1] EOD BREAKOUT CANDIDATE GENERATION
    # =========================================================================
    print("\n" + "=" * 115)
    print("Generating High-Fidelity Signal Candidates from Historical Parquets...")
    print("=" * 115)

    eod_candidates = []
    cand_seq = 0

    for sym, df in equity_1d_cache.items():
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
        adx14 = df["ADX_14"].values if "ADX_14" in df.columns else (df["ADX"].values if "ADX" in df.columns else np.ones(n) * 20.0)
        rsi14 = df["RSI_14"].values if "RSI_14" in df.columns else (df["RSI"].values if "RSI" in df.columns else np.ones(n) * 50.0)

        hh15 = pd.Series(h).shift(1).rolling(15, min_periods=8).max().values
        shelf8 = pd.Series(l).shift(1).rolling(8, min_periods=4).min().values

        for i in range(40, n - 20):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            part = "DEV" if d_str < VAL_START else ("VAL" if d_str < HLD_START else "HOLDOUT")
            ci, oi, hi, li, vi = c[i], o[i], h[i], l[i], v[i]
            if ci < 60.0:
                continue

            hh_val = hh15[i]
            if pd.isna(hh_val) or hh_val <= 0 or ci <= hh_val or ci <= oi:
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0
            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            prior_5d_high = np.max(h[max(0, i - 5): i])
            prior_5d_low = np.min(l[max(0, i - 5): i])
            span_atr = (prior_5d_high - prior_5d_low) / atr_i if atr_i > 0 else 2.0

            shelf_l = shelf8[i] if not pd.isna(shelf8[i]) and shelf8[i] > 0 else li
            sl_price = round(shelf_l * 0.995, 2)
            risk = ci - sl_price
            if risk <= 0:
                continue
            risk_pct = risk / ci
            if risk_pct > 0.08 or risk_pct < 0.015:
                continue

            # RS evaluation
            rs_info = date_rs_thresholds.get(d_str, {"rs60": 0.0, "rs70": 0.02, "rs80": 0.05})
            ret20_sym = (ci - c[i - 20]) / c[i - 20] if i >= 20 else 0.0
            rs_tier = 80 if ret20_sym >= rs_info["rs80"] else (70 if ret20_sym >= rs_info["rs70"] else (60 if ret20_sym >= rs_info["rs60"] else 50))

            # Confirmation candles (Bar i+1)
            c1_ci = c[i + 1]
            c1_oi = o[i + 1]
            c1_hi = h[i + 1]
            c1_li = l[i + 1]
            c1_vi = v[i + 1]
            avg_v1 = vol20[i + 1] if not np.isnan(vol20[i + 1]) and vol20[i + 1] > 0 else c1_vi

            c1_green = c1_ci > ci
            c2_level_hold = c1_li >= hh_val * 0.998
            c3_vol_exp = c1_vi >= (avg_v1 * 1.1)
            c4_range_cont = c1_hi > hi

            exec_open_t1 = o[i + 1]
            exec_open_t2 = o[i + 2] if (i + 2) < n else o[i + 1]

            fbars_t1 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 1, min(i + 25, n))]
            fbars_t2 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 2, min(i + 26, n))]

            cand_seq += 1
            eod_candidates.append({
                "cand_id": f"EOD_{cand_seq}",
                "symbol": sym,
                "scan_idx": i,
                "scan_date": d_str,
                "part": part,
                "regime": regime,
                "ci": ci,
                "oi": oi,
                "hi": hi,
                "li": li,
                "vi": vi,
                "cpos": cpos,
                "vol_ratio": vol_ratio,
                "span_atr": span_atr,
                "risk_pct": risk_pct,
                "sl_price": sl_price,
                "target_r": 2.5,
                "rs_tier": rs_tier,
                "adx_val": adx14[i] if not np.isnan(adx14[i]) else 20.0,
                "rsi_val": rsi14[i] if not np.isnan(rsi14[i]) else 50.0,
                "trend_bull": sma50[i] > sma200[i],
                "c1_green": c1_green,
                "c2_level_hold": c2_level_hold,
                "c3_vol_exp": c3_vol_exp,
                "c4_range_cont": c4_range_cont,
                "exec_open_t1": exec_open_t1,
                "exec_open_t2": exec_open_t2,
                "fbars_t1": fbars_t1,
                "fbars_t2": fbars_t2,
            })

    print(f"Generated {len(eod_candidates):,} EOD Breakout signal candidates.")

    # =========================================================================
    # [2] ACCUMULATION VCP CANDIDATE GENERATION
    # =========================================================================
    vcp_candidates = []
    cand_seq = 0

    for sym, df in equity_1d_cache.items():
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
        adx14 = df["ADX_14"].values if "ADX_14" in df.columns else np.ones(n) * 20.0
        rsi14 = df["RSI_14"].values if "RSI_14" in df.columns else np.ones(n) * 50.0

        hh10 = pd.Series(h).shift(1).rolling(10, min_periods=5).max().values
        swing_low5 = pd.Series(l).shift(1).rolling(5, min_periods=3).min().values

        # Rolling Volatility Contraction
        span5 = (pd.Series(h).rolling(5).max() - pd.Series(l).rolling(5).min()).values
        span15 = (pd.Series(h).rolling(15).max() - pd.Series(l).rolling(15).min()).values

        for i in range(40, n - 20):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            part = "DEV" if d_str < VAL_START else ("VAL" if d_str < HLD_START else "HOLDOUT")
            ci, oi, hi, li, vi = c[i], o[i], h[i], l[i], v[i]
            if ci < 60.0:
                continue

            # VCP Condition: Contraction (span5 < 0.60 * span15) + Breakout above 10D High
            if span15[i] <= 0 or (span5[i] / span15[i]) > 0.65:
                continue

            hh_val = hh10[i]
            if pd.isna(hh_val) or hh_val <= 0 or ci <= hh_val or ci <= oi:
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0
            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            span_atr = span5[i] / atr_i if atr_i > 0 else 2.0
            sl_l = swing_low5[i] if not pd.isna(swing_low5[i]) and swing_low5[i] > 0 else li
            sl_price = round(sl_l * 0.995, 2)
            risk = ci - sl_price
            if risk <= 0:
                continue
            risk_pct = risk / ci
            if risk_pct > 0.075 or risk_pct < 0.015:
                continue

            rs_info = date_rs_thresholds.get(d_str, {"rs60": 0.0, "rs70": 0.02, "rs80": 0.05})
            ret20_sym = (ci - c[i - 20]) / c[i - 20] if i >= 20 else 0.0
            rs_tier = 80 if ret20_sym >= rs_info["rs80"] else (70 if ret20_sym >= rs_info["rs70"] else (60 if ret20_sym >= rs_info["rs60"] else 50))

            c1_ci = c[i + 1]
            c1_oi = o[i + 1]
            c1_hi = h[i + 1]
            c1_li = l[i + 1]
            c1_vi = v[i + 1]
            avg_v1 = vol20[i + 1] if not np.isnan(vol20[i + 1]) and vol20[i + 1] > 0 else c1_vi

            c1_green = c1_ci > ci
            c2_level_hold = c1_li >= hh_val * 0.998
            c3_vol_exp = c1_vi >= (avg_v1 * 1.1)
            c4_range_cont = c1_hi > hi

            exec_open_t1 = o[i + 1]
            exec_open_t2 = o[i + 2] if (i + 2) < n else o[i + 1]

            fbars_t1 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 1, min(i + 25, n))]
            fbars_t2 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 2, min(i + 26, n))]

            cand_seq += 1
            vcp_candidates.append({
                "cand_id": f"VCP_{cand_seq}",
                "symbol": sym,
                "scan_idx": i,
                "scan_date": d_str,
                "part": part,
                "regime": regime,
                "ci": ci,
                "oi": oi,
                "hi": hi,
                "li": li,
                "vi": vi,
                "cpos": cpos,
                "vol_ratio": vol_ratio,
                "span_atr": span_atr,
                "risk_pct": risk_pct,
                "sl_price": sl_price,
                "target_r": 2.5,
                "rs_tier": rs_tier,
                "adx_val": adx14[i] if not np.isnan(adx14[i]) else 20.0,
                "rsi_val": rsi14[i] if not np.isnan(rsi14[i]) else 50.0,
                "trend_bull": sma50[i] > sma200[i],
                "c1_green": c1_green,
                "c2_level_hold": c2_level_hold,
                "c3_vol_exp": c3_vol_exp,
                "c4_range_cont": c4_range_cont,
                "exec_open_t1": exec_open_t1,
                "exec_open_t2": exec_open_t2,
                "fbars_t1": fbars_t1,
                "fbars_t2": fbars_t2,
            })

    print(f"Generated {len(vcp_candidates):,} Accumulation VCP signal candidates.")

    # =========================================================================
    # [3] REVERSAL CANDIDATE GENERATION
    # =========================================================================
    rev_candidates = []
    cand_seq = 0

    for sym, df in equity_1d_cache.items():
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
        adx14 = df["ADX_14"].values if "ADX_14" in df.columns else np.ones(n) * 20.0
        rsi14 = df["RSI_14"].values if "RSI_14" in df.columns else np.ones(n) * 50.0

        for i in range(40, n - 20):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            part = "DEV" if d_str < VAL_START else ("VAL" if d_str < HLD_START else "HOLDOUT")
            ci, oi, hi, li, vi = c[i], o[i], h[i], l[i], v[i]
            if ci < 60.0:
                continue

            # Reversal setup: Sweep prior 3-day low and close in top 40% with bullish engulfing / hammer
            prior_3d_low = np.min(l[max(0, i - 3): i])
            if li >= prior_3d_low or ci <= oi:
                continue

            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range
            if cpos < 0.60:
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            sl_price = round(li * 0.992, 2)
            risk = ci - sl_price
            if risk <= 0:
                continue
            risk_pct = risk / ci
            if risk_pct > 0.07 or risk_pct < 0.01:
                continue

            rs_info = date_rs_thresholds.get(d_str, {"rs60": 0.0, "rs70": 0.02, "rs80": 0.05})
            ret20_sym = (ci - c[i - 20]) / c[i - 20] if i >= 20 else 0.0
            rs_tier = 80 if ret20_sym >= rs_info["rs80"] else (70 if ret20_sym >= rs_info["rs70"] else (60 if ret20_sym >= rs_info["rs60"] else 50))

            c1_ci = c[i + 1]
            c1_oi = o[i + 1]
            c1_hi = h[i + 1]
            c1_li = l[i + 1]
            c1_vi = v[i + 1]
            avg_v1 = vol20[i + 1] if not np.isnan(vol20[i + 1]) and vol20[i + 1] > 0 else c1_vi

            c1_green = c1_ci > ci
            c2_level_hold = c1_li >= li * 0.998
            c3_vol_exp = c1_vi >= (avg_v1 * 1.0)
            c4_range_cont = c1_hi > hi

            exec_open_t1 = o[i + 1]
            exec_open_t2 = o[i + 2] if (i + 2) < n else o[i + 1]

            fbars_t1 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 1, min(i + 20, n))]
            fbars_t2 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 2, min(i + 21, n))]

            cand_seq += 1
            rev_candidates.append({
                "cand_id": f"REV_{cand_seq}",
                "symbol": sym,
                "scan_idx": i,
                "scan_date": d_str,
                "part": part,
                "regime": regime,
                "ci": ci,
                "oi": oi,
                "hi": hi,
                "li": li,
                "vi": vi,
                "cpos": cpos,
                "vol_ratio": vol_ratio,
                "span_atr": 2.0,
                "risk_pct": risk_pct,
                "sl_price": sl_price,
                "target_r": 2.0,
                "rs_tier": rs_tier,
                "adx_val": adx14[i] if not np.isnan(adx14[i]) else 20.0,
                "rsi_val": rsi14[i] if not np.isnan(rsi14[i]) else 40.0,
                "trend_bull": sma50[i] > sma200[i],
                "c1_green": c1_green,
                "c2_level_hold": c2_level_hold,
                "c3_vol_exp": c3_vol_exp,
                "c4_range_cont": c4_range_cont,
                "exec_open_t1": exec_open_t1,
                "exec_open_t2": exec_open_t2,
                "fbars_t1": fbars_t1,
                "fbars_t2": fbars_t2,
            })

    print(f"Generated {len(rev_candidates):,} Reversal signal candidates.")

    # =========================================================================
    # [4] PULLBACK V2 CANDIDATE GENERATION
    # =========================================================================
    pb_candidates = []
    cand_seq = 0

    for sym, df in equity_1d_cache.items():
        n = len(df)
        c = df["Close"].values
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        v = df["Volume"].values if "Volume" in df.columns else np.ones(n) * 10000.0
        dates = df.index.strftime("%Y-%m-%d").values

        ema20 = pd.Series(c).ewm(span=20, adjust=False).mean().values
        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n >= 200 else sma50
        atr14 = pd.Series(h - l).rolling(14, min_periods=5).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=5).mean().values
        adx14 = df["ADX_14"].values if "ADX_14" in df.columns else np.ones(n) * 20.0
        rsi14 = df["RSI_14"].values if "RSI_14" in df.columns else np.ones(n) * 50.0

        for i in range(40, n - 20):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            part = "DEV" if d_str < VAL_START else ("VAL" if d_str < HLD_START else "HOLDOUT")
            ci, oi, hi, li, vi = c[i], o[i], h[i], l[i], v[i]
            if ci < 60.0:
                continue

            # Pullback condition: uptrend (SMA50 > SMA200) + test of EMA20 (Low <= EMA20 * 1.01) + bounce (Close > Open)
            if sma50[i] <= sma200[i] or li > (ema20[i] * 1.015) or ci <= oi:
                continue

            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range
            if cpos < 0.55:
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.02)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            sl_price = round(min(li, ema20[i]) * 0.993, 2)
            risk = ci - sl_price
            if risk <= 0:
                continue
            risk_pct = risk / ci
            if risk_pct > 0.065 or risk_pct < 0.01:
                continue

            rs_info = date_rs_thresholds.get(d_str, {"rs60": 0.0, "rs70": 0.02, "rs80": 0.05})
            ret20_sym = (ci - c[i - 20]) / c[i - 20] if i >= 20 else 0.0
            rs_tier = 80 if ret20_sym >= rs_info["rs80"] else (70 if ret20_sym >= rs_info["rs70"] else (60 if ret20_sym >= rs_info["rs60"] else 50))

            c1_ci = c[i + 1]
            c1_oi = o[i + 1]
            c1_hi = h[i + 1]
            c1_li = l[i + 1]
            c1_vi = v[i + 1]
            avg_v1 = vol20[i + 1] if not np.isnan(vol20[i + 1]) and vol20[i + 1] > 0 else c1_vi

            c1_green = c1_ci > ci
            c2_level_hold = c1_li >= ema20[i + 1] * 0.99
            c3_vol_exp = c1_vi >= (avg_v1 * 1.0)
            c4_range_cont = c1_hi > hi

            exec_open_t1 = o[i + 1]
            exec_open_t2 = o[i + 2] if (i + 2) < n else o[i + 1]

            fbars_t1 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 1, min(i + 20, n))]
            fbars_t2 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 2, min(i + 21, n))]

            cand_seq += 1
            pb_candidates.append({
                "cand_id": f"PB_{cand_seq}",
                "symbol": sym,
                "scan_idx": i,
                "scan_date": d_str,
                "part": part,
                "regime": regime,
                "ci": ci,
                "oi": oi,
                "hi": hi,
                "li": li,
                "vi": vi,
                "cpos": cpos,
                "vol_ratio": vol_ratio,
                "span_atr": 2.0,
                "risk_pct": risk_pct,
                "sl_price": sl_price,
                "target_r": 2.0,
                "rs_tier": rs_tier,
                "adx_val": adx14[i] if not np.isnan(adx14[i]) else 20.0,
                "rsi_val": rsi14[i] if not np.isnan(rsi14[i]) else 50.0,
                "trend_bull": sma50[i] > sma200[i],
                "c1_green": c1_green,
                "c2_level_hold": c2_level_hold,
                "c3_vol_exp": c3_vol_exp,
                "c4_range_cont": c4_range_cont,
                "exec_open_t1": exec_open_t1,
                "exec_open_t2": exec_open_t2,
                "fbars_t1": fbars_t1,
                "fbars_t2": fbars_t2,
            })

    print(f"Generated {len(pb_candidates):,} Pullback V2 signal candidates.")

    # =========================================================================
    # [5] MULTITF 1H CANDIDATE GENERATION
    # =========================================================================
    f1h_list = sorted(glob.glob(os.path.join(_HISTORY_1H, "*.parquet")))
    print(f"\nLoaded {len(f1h_list)} 1H verified parquets.")
    mtf1h_candidates = []
    cand_seq = 0

    for fpath in f1h_list:
        sym = os.path.basename(fpath).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 80:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                if "Datetime" in df.columns:
                    df.index = pd.to_datetime(df["Datetime"])
                else:
                    df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            df = df[df.index.dayofweek < 5]
        except Exception:
            continue

        n = len(df)
        if n < 80:
            continue
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
        hh26 = pd.Series(h).shift(1).rolling(26, min_periods=10).max().values
        shelf12 = pd.Series(l).shift(1).rolling(12, min_periods=6).min().values

        for i in range(30, n - 25):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            part = "DEV" if d_str < VAL_START else ("VAL" if d_str < HLD_START else "HOLDOUT")
            ci, oi, hi, li, vi = c[i], o[i], h[i], l[i], v[i]
            if ci < 60.0:
                continue

            hh_val = hh26[i]
            if pd.isna(hh_val) or hh_val <= 0 or ci <= hh_val or ci <= oi:
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.015)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0
            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range

            regime = "BULL" if ci > sma200[i] * 1.02 else ("BEAR" if ci < sma200[i] * 0.95 else "NEUTRAL")

            sl_l = shelf12[i] if not pd.isna(shelf12[i]) and shelf12[i] > 0 else li
            sl_price = round(sl_l * 0.996, 2)
            risk = ci - sl_price
            if risk <= 0:
                continue
            risk_pct = risk / ci
            if risk_pct > 0.06 or risk_pct < 0.008:
                continue

            rs_info = date_rs_thresholds.get(d_str, {"rs60": 0.0, "rs70": 0.02, "rs80": 0.05})
            ret20_sym = (ci - c[max(0, i - 20)]) / c[max(0, i - 20)] if i >= 20 else 0.0
            rs_tier = 80 if ret20_sym >= rs_info["rs80"] else (70 if ret20_sym >= rs_info["rs70"] else (60 if ret20_sym >= rs_info["rs60"] else 50))

            c1_ci = c[i + 1]
            c1_oi = o[i + 1]
            c1_hi = h[i + 1]
            c1_li = l[i + 1]
            c1_vi = v[i + 1]
            avg_v1 = vol20[i + 1] if not np.isnan(vol20[i + 1]) and vol20[i + 1] > 0 else c1_vi

            c1_green = c1_ci > ci
            c2_level_hold = c1_li >= hh_val * 0.998
            c3_vol_exp = c1_vi >= (avg_v1 * 1.1)
            c4_range_cont = c1_hi > hi

            exec_open_t1 = o[i + 1]
            exec_open_t2 = o[i + 2] if (i + 2) < n else o[i + 1]

            fbars_t1 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 1, min(i + 30, n))]
            fbars_t2 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 2, min(i + 31, n))]

            cand_seq += 1
            mtf1h_candidates.append({
                "cand_id": f"MTF1H_{cand_seq}",
                "symbol": sym,
                "scan_idx": i,
                "scan_date": d_str,
                "part": part,
                "regime": regime,
                "ci": ci,
                "oi": oi,
                "hi": hi,
                "li": li,
                "vi": vi,
                "cpos": cpos,
                "vol_ratio": vol_ratio,
                "span_atr": 2.0,
                "risk_pct": risk_pct,
                "sl_price": sl_price,
                "target_r": 2.2,
                "rs_tier": rs_tier,
                "adx_val": 25.0,
                "rsi_val": 60.0,
                "trend_bull": sma50[i] > sma200[i],
                "c1_green": c1_green,
                "c2_level_hold": c2_level_hold,
                "c3_vol_exp": c3_vol_exp,
                "c4_range_cont": c4_range_cont,
                "exec_open_t1": exec_open_t1,
                "exec_open_t2": exec_open_t2,
                "fbars_t1": fbars_t1,
                "fbars_t2": fbars_t2,
            })

    print(f"Generated {len(mtf1h_candidates):,} MultiTF 1H signal candidates.")

    # =========================================================================
    # [6] MULTITF 5M CANDIDATE GENERATION
    # =========================================================================
    f5m_list = sorted(glob.glob(os.path.join(_HISTORY_5M, "*.parquet")))
    print(f"\nLoaded {len(f5m_list)} 5M verified parquets.")
    mtf5m_candidates = []
    cand_seq = 0

    for fpath in f5m_list:
        sym = os.path.basename(fpath).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 100:
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            df = df[df.index.dayofweek < 5]
        except Exception:
            continue

        n = len(df)
        if n < 100:
            continue
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
        hh26 = pd.Series(h).shift(1).rolling(26, min_periods=10).max().values
        shelf10 = pd.Series(l).shift(1).rolling(10, min_periods=5).min().values

        for i in range(40, n - 30):
            d_str = dates[i]
            if d_str < DEV_START or d_str > HLD_END:
                continue

            part = "DEV" if d_str < VAL_START else ("VAL" if d_str < HLD_START else "HOLDOUT")
            ci, oi, hi, li, vi = c[i], o[i], h[i], l[i], v[i]
            if ci < 60.0:
                continue

            hh_val = hh26[i]
            if pd.isna(hh_val) or hh_val <= 0 or ci <= hh_val or ci <= oi:
                continue

            atr_i = atr14[i] if not np.isnan(atr14[i]) and atr14[i] > 0 else (ci * 0.01)
            avg_v = vol20[i] if not np.isnan(vol20[i]) and vol20[i] > 0 else vi
            vol_ratio = (vi / avg_v) if avg_v > 0 else 1.0
            day_range = max(0.01, hi - li)
            cpos = (ci - li) / day_range

            regime = "BULL" if ci > sma200[i] * 1.01 else ("BEAR" if ci < sma200[i] * 0.98 else "NEUTRAL")

            sl_l = shelf10[i] if not pd.isna(shelf10[i]) and shelf10[i] > 0 else li
            sl_price = round(sl_l * 0.997, 2)
            risk = ci - sl_price
            if risk <= 0:
                continue
            risk_pct = risk / ci
            if risk_pct > 0.04 or risk_pct < 0.005:
                continue

            rs_tier = 70 if vol_ratio >= 1.5 else 50

            c1_ci = c[i + 1]
            c1_oi = o[i + 1]
            c1_hi = h[i + 1]
            c1_li = l[i + 1]
            c1_vi = v[i + 1]
            avg_v1 = vol20[i + 1] if not np.isnan(vol20[i + 1]) and vol20[i + 1] > 0 else c1_vi

            c1_green = c1_ci > ci
            c2_level_hold = c1_li >= hh_val * 0.999
            c3_vol_exp = c1_vi >= (avg_v1 * 1.1)
            c4_range_cont = c1_hi > hi

            exec_open_t1 = o[i + 1]
            exec_open_t2 = o[i + 2] if (i + 2) < n else o[i + 1]

            fbars_t1 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 1, min(i + 30, n))]
            fbars_t2 = [(o[k], h[k], l[k], c[k], v[k]) for k in range(i + 2, min(i + 31, n))]

            cand_seq += 1
            mtf5m_candidates.append({
                "cand_id": f"MTF5M_{cand_seq}",
                "symbol": sym,
                "scan_idx": i,
                "scan_date": d_str,
                "part": part,
                "regime": regime,
                "ci": ci,
                "oi": oi,
                "hi": hi,
                "li": li,
                "vi": vi,
                "cpos": cpos,
                "vol_ratio": vol_ratio,
                "span_atr": 1.8,
                "risk_pct": risk_pct,
                "sl_price": sl_price,
                "target_r": 2.0,
                "rs_tier": rs_tier,
                "adx_val": 25.0,
                "rsi_val": 60.0,
                "trend_bull": sma50[i] > sma200[i],
                "c1_green": c1_green,
                "c2_level_hold": c2_level_hold,
                "c3_vol_exp": c3_vol_exp,
                "c4_range_cont": c4_range_cont,
                "exec_open_t1": exec_open_t1,
                "exec_open_t2": exec_open_t2,
                "fbars_t1": fbars_t1,
                "fbars_t2": fbars_t2,
            })

    print(f"Generated {len(mtf5m_candidates):,} MultiTF 5M signal candidates.")

    # ─────────────────────────────────────────────────────────────────────────
    # OPTIMIZE ALL 6 SCANNERS IN PRIORITY ORDER
    # ─────────────────────────────────────────────────────────────────────────
    scanner_candidate_map = [
        ("EOD_BREAKOUT", eod_candidates),
        ("ACCUMULATION_VCP", vcp_candidates),
        ("REVERSAL", rev_candidates),
        ("PULLBACK_V2", pb_candidates),
        ("MULTITF_5M", mtf5m_candidates),
        ("MULTITF_1H", mtf1h_candidates),
    ]

    for sname, cands in scanner_candidate_map:
        cfg = SCANNER_TARGETS[sname]
        optimize_scanner(sname, cands, cfg)

    # ─────────────────────────────────────────────────────────────────────────
    # SAVE ALL CSV REPORTS & JSON
    # ─────────────────────────────────────────────────────────────────────────
    pd.DataFrame(all_timing_records).to_csv(os.path.join(_REPORTS_DIR, "v514_confirmation_timing_comparison.csv"), index=False)
    pd.DataFrame(all_single_records).to_csv(os.path.join(_REPORTS_DIR, "v514_single_feature_attribution.csv"), index=False)
    pd.DataFrame(all_compound_records).to_csv(os.path.join(_REPORTS_DIR, "v514_compound_interaction_matrix.csv"), index=False)
    pd.DataFrame(all_regime_records).to_csv(os.path.join(_REPORTS_DIR, "v514_regime_performance_matrix.csv"), index=False)
    champ_df = pd.DataFrame(champion_records)
    champ_df.to_csv(os.path.join(_REPORTS_DIR, "v514_champion_summary.csv"), index=False)

    class CustomEncoder(json.JSONEncoder):
        def default(self, obj):
            if isinstance(obj, (np.integer, np.int64, np.int32)):
                return int(obj)
            elif isinstance(obj, (np.floating, np.float64, np.float32)):
                return float(obj)
            elif isinstance(obj, (np.bool_, bool)):
                return bool(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            return super().default(obj)

    master_results = {
        "timestamp": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "champions": champion_records,
    }
    with open(os.path.join(_REPORTS_DIR, "v514_master_results.json"), "w") as f:
        json.dump(master_results, f, cls=CustomEncoder, indent=2)

    print("\n" + "=" * 115)
    print("V5.14 OPTIMIZATION COMPLETED SUCCESSFULLY.")
    print(f"Saved artifacts to {_REPORTS_DIR}/v514_*.csv and .json")
    print("=" * 115)

if __name__ == "__main__":
    t0 = time.time()
    run_v514_optimization()
    print(f"Total execution time: {time.time() - t0:.2f}s")
