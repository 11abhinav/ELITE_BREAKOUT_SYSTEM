#!/usr/bin/env python3
# =============================================================================
# scripts/fast_v59_winrate_optimizer.py
# V5.9 WIN-RATE & FRICTION-RESISTANT QUALITY MAXIMIZATION SUITE
# =============================================================================
# Systematically evaluates high-win-rate challenger variants across all 11
# scanner families across 884 daily equities and 393 hourly parquets.
# Keeps V5.8 frozen configurations as immutable CONTROL baselines.
# Objective: Maximize Net Win Rate subject to Net E[R] >= +0.10R and Net PF >= 1.25.
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

from v58_prelive_certification_engine import (
    FROZEN_REGISTRY,
    FRICTION_PARAMETERS,
    SECTOR_MAP,
    get_sector,
    apply_realistic_friction,
    calc_performance_summary
)

DEV_START = "2025-07-24"
VAL_START = "2026-01-01"
HLD_START = "2026-06-01"
HLD_END   = "2026-09-04"

def get_regime(sma50, sma200, c):
    if c >= sma50 and sma50 >= sma200:
        return "BULL"
    elif c < sma50 and sma50 < sma200:
        return "BEAR"
    else:
        return "NEUTRAL"

def evaluate_scanner_variants():
    print("=" * 125)
    print("V5.9 MASTER WIN-RATE & FRICTION-RESISTANT QUALITY MAXIMIZATION SUITE")
    print("Objective: Push scanners toward 55-60%+ Win Rate frontier while preserving positive net alpha")
    print("=" * 125)
    t0 = time.time()

    daily_files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    hourly_files = sorted(glob.glob(os.path.join(_HISTORY_1H_DIR, "*.parquet")))
    print(f"Loaded {len(daily_files)} daily parquets and {len(hourly_files)} hourly parquets.")

    # ── DEFINE V5.9 HIGH-WIN-RATE CHALLENGER SUITE ───────────────────────────
    # For each family, define specialized variants targeting high win rate and friction resistance
    V59_CHALLENGERS = {
        # 1. ACCUMULATION_VCP (Target: 60-65%+ WR)
        "ACCUMULATION_VCP": [
            {"id": "VCP_V58_CONTROL", "type": "CONTROL", "lb": 15, "bb_pct": 0.45, "dry_vol": 0.80, "vol_thrust": 1.5, "cpos": 0.65, "target_r": 3.0, "horizon": 20, "holding_type": "SWING_BREAKOUT"},
            {"id": "VCP_V59_QUAL_A_CPOS75_22R", "type": "CHALLENGER", "lb": 15, "bb_pct": 0.40, "dry_vol": 0.75, "vol_thrust": 1.6, "cpos": 0.75, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
            {"id": "VCP_V59_QUAL_B_CPOS80_24R", "type": "CHALLENGER", "lb": 15, "bb_pct": 0.40, "dry_vol": 0.70, "vol_thrust": 1.7, "cpos": 0.80, "target_r": 2.4, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
            {"id": "VCP_V59_QUAL_C_TIGHT_COIL_20R", "type": "CHALLENGER", "lb": 12, "bb_pct": 0.35, "dry_vol": 0.70, "vol_thrust": 1.8, "cpos": 0.75, "target_r": 2.0, "horizon": 12, "holding_type": "SWING_BREAKOUT"},
        ],
        # 2. EOD_BREAKOUT (Target: 58-62%+ WR)
        "EOD_BREAKOUT": [
            {"id": "EOD_V58_CONTROL", "type": "CONTROL", "lb": 15, "max_span_atr": 2.5, "cpos": 0.65, "vol_surge": 1.0, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
            {"id": "EOD_V59_QUAL_A_CPOS75_20R", "type": "CHALLENGER", "lb": 15, "max_span_atr": 2.0, "cpos": 0.75, "vol_surge": 1.4, "target_r": 2.0, "horizon": 12, "holding_type": "SWING_BREAKOUT"},
            {"id": "EOD_V59_QUAL_B_CPOS80_22R", "type": "CHALLENGER", "lb": 15, "max_span_atr": 1.8, "cpos": 0.80, "vol_surge": 1.5, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_BREAKOUT"},
            {"id": "EOD_V59_QUAL_C_TIGHT_12D_20R", "type": "CHALLENGER", "lb": 12, "max_span_atr": 1.8, "cpos": 0.75, "vol_surge": 1.6, "target_r": 2.0, "horizon": 10, "holding_type": "SWING_BREAKOUT"},
        ],
        # 3. REVERSAL (Target: 55-60%+ WR while preserving PF >= 1.40-1.50)
        "REVERSAL": [
            {"id": "REV_V58_CONTROL", "type": "CONTROL", "lb": 15, "min_clv": 0.60, "vol_mult": 1.2, "target_r": 3.0, "horizon": 15, "holding_type": "SWING_COUNTER_TREND"},
            {"id": "REV_V59_QUAL_A_CLV75_22R", "type": "CHALLENGER", "lb": 15, "min_clv": 0.75, "vol_mult": 1.4, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_COUNTER_TREND"},
            {"id": "REV_V59_QUAL_B_CLV80_24R", "type": "CHALLENGER", "lb": 15, "min_clv": 0.80, "vol_mult": 1.5, "target_r": 2.4, "horizon": 12, "holding_type": "SWING_COUNTER_TREND"},
            {"id": "REV_V59_QUAL_C_DEEP_UNDERCUT_22R", "type": "CHALLENGER", "lb": 20, "min_clv": 0.75, "vol_mult": 1.5, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_COUNTER_TREND"},
        ],
        # 4. PULLBACK_V2 (Target: 55-58%+ WR)
        "PULLBACK_V2": [
            {"id": "PULL_V58_CONTROL", "type": "CONTROL", "pullback_bars": 5, "cpos": 0.60, "vol_dry": 0.85, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_TREND"},
            {"id": "PULL_V59_QUAL_A_CPOS75_20R", "type": "CHALLENGER", "pullback_bars": 5, "cpos": 0.75, "vol_dry": 0.75, "target_r": 2.0, "horizon": 12, "holding_type": "SWING_TREND"},
            {"id": "PULL_V59_QUAL_B_CPOS80_22R", "type": "CHALLENGER", "pullback_bars": 4, "cpos": 0.80, "vol_dry": 0.70, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_TREND"},
            {"id": "PULL_V59_QUAL_C_EMA20_PINCH_20R", "type": "CHALLENGER", "pullback_bars": 5, "cpos": 0.75, "vol_dry": 0.70, "target_r": 2.0, "horizon": 10, "holding_type": "SWING_TREND"},
        ],
        # 5. TECHNICAL_AHAT (Target: 55%+ WR)
        "TECHNICAL_AHAT": [
            {"id": "AHAT_V58_CONTROL", "type": "CONTROL", "cpos": 0.60, "rsi_min": 48, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_CONFLUENCE"},
            {"id": "AHAT_V59_QUAL_A_RSI55_CPOS75_20R", "type": "CHALLENGER", "cpos": 0.75, "rsi_min": 55, "target_r": 2.0, "horizon": 12, "holding_type": "SWING_CONFLUENCE"},
            {"id": "AHAT_V59_QUAL_B_RSI58_CPOS80_22R", "type": "CHALLENGER", "cpos": 0.80, "rsi_min": 58, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_CONFLUENCE"},
        ],
        # 6. DAILY_BUILDER (Target: 50-55%+ WR)
        "DAILY_BUILDER": [
            {"id": "BUILD_V58_CONTROL", "type": "CONTROL", "lb": 20, "cpos": 0.60, "vol_surge": 1.2, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_BREADTH"},
            {"id": "BUILD_V59_QUAL_A_CPOS75_20R", "type": "CHALLENGER", "lb": 20, "cpos": 0.75, "vol_surge": 1.5, "target_r": 2.0, "horizon": 12, "holding_type": "SWING_BREADTH"},
            {"id": "BUILD_V59_QUAL_B_CPOS80_22R", "type": "CHALLENGER", "lb": 15, "cpos": 0.80, "vol_surge": 1.6, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_BREADTH"},
        ],
        # 7. SHORT_COVERING_EOD (Target: 55%+ WR in Bear/Neutral)
        "SHORT_COVERING_EOD": [
            {"id": "SC_V58_CONTROL", "type": "CONTROL", "lb": 15, "vol_surge": 1.6, "cpos": 0.65, "target_r": 2.5, "horizon": 12, "holding_type": "SWING_SQUEEZE"},
            {"id": "SC_V59_QUAL_A_VOL18_CPOS75_20R", "type": "CHALLENGER", "lb": 15, "vol_surge": 1.8, "cpos": 0.75, "target_r": 2.0, "horizon": 10, "holding_type": "SWING_SQUEEZE"},
            {"id": "SC_V59_QUAL_B_VOL20_CPOS80_22R", "type": "CHALLENGER", "lb": 12, "vol_surge": 2.0, "cpos": 0.80, "target_r": 2.2, "horizon": 10, "holding_type": "SWING_SQUEEZE"},
        ],
        # 8. MULTIBAGGER (Target: Convexity & 5R+ frequency, preserve long horizon)
        "MULTIBAGGER": [
            {"id": "MBAG_V58_CONTROL", "type": "CONTROL", "lb": 60, "vol_surge": 1.65, "target_r": 5.0, "horizon": 40, "holding_type": "POSITIONAL_CONVEXITY"},
            {"id": "MBAG_V59_CONVEX_A_75D_180V_5R", "type": "CHALLENGER", "lb": 75, "vol_surge": 1.80, "target_r": 5.0, "horizon": 45, "holding_type": "POSITIONAL_CONVEXITY"},
            {"id": "MBAG_V59_CONVEX_B_60D_175V_45R", "type": "CHALLENGER", "lb": 60, "vol_surge": 1.75, "target_r": 4.5, "horizon": 40, "holding_type": "POSITIONAL_CONVEXITY"},
        ]
    }

    all_outcomes = []

    print("\nExecuting multi-scanner parameter sweep across 884 equities...")
    for fpath in daily_files:
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
            # Strict Weekend Ban
            df = df[df.index.dayofweek < 5]
        except Exception:
            continue

        n_bars = len(df)
        c = df["Close"].values
        o = df["Open"].values
        h = df["High"].values
        l = df["Low"].values
        v = df["Volume"].values if "Volume" in df.columns else np.ones(n_bars) * 10000.0
        dates = df.index.strftime("%Y-%m-%d").values

        sma20 = pd.Series(c).rolling(20, min_periods=5).mean().values
        sma50 = pd.Series(c).rolling(50, min_periods=20).mean().values
        sma200 = pd.Series(c).rolling(200, min_periods=50).mean().values if n_bars >= 200 else sma50
        atr14 = pd.Series(h - l).rolling(14, min_periods=5).mean().values
        vol20 = pd.Series(v).rolling(20, min_periods=5).mean().values

        # RSI 14
        delta = pd.Series(c).diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14, min_periods=5).mean()
        avg_loss = loss.rolling(14, min_periods=5).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        rsi14 = (100.0 - (100.0 / (1.0 + rs))).values

        for i in range(65, n_bars - 1):
            dt = dates[i]
            if dt < DEV_START or dt > HLD_END:
                continue

            part = "CALIBRATION_DEV" if dt < VAL_START else ("TUNING_VAL" if dt < HLD_START else "LOCKED_REPRODUCTION")
            reg = get_regime(sma50[i], sma200[i], c[i])

            curr_c = c[i]
            curr_o = o[i]
            curr_h = h[i]
            curr_l = l[i]
            curr_v = v[i]
            curr_atr = atr14[i] if atr14[i] > 0 else curr_c * 0.02
            cpos = (curr_c - curr_l) / (curr_h - curr_l + 1e-9)

            # ── 1. ACCUMULATION_VCP EVALUATION ───────────────────────────────
            for spec in V59_CHALLENGERS["ACCUMULATION_VCP"]:
                lb = spec["lb"]
                high_box = np.max(h[i-lb:i])
                low_box = np.min(l[i-lb:i])
                box_width = high_box - low_box

                # Check breakout
                if curr_c > high_box and cpos >= spec["cpos"]:
                    if box_width <= spec["bb_pct"] * curr_atr * 4.0:
                        prior_vol_dry = np.mean(v[i-5:i]) / (vol20[i] + 1e-9)
                        if prior_vol_dry <= spec["dry_vol"] and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_thrust"]:
                            entry_p = curr_c
                            sl_p = low_box
                            risk = entry_p - sl_p
                            if risk >= curr_atr * 0.5:
                                tgt_p = entry_p + (risk * spec["target_r"])
                                # Forward trade resolution
                                max_fwd = min(spec["horizon"], n_bars - 1 - i)
                                hit_tgt, hit_sl = False, False
                                exit_r = 0.0
                                for f_idx in range(1, max_fwd + 1):
                                    fh, fl = h[i + f_idx], l[i + f_idx]
                                    if fl <= sl_p:
                                        hit_sl = True
                                        exit_r = -1.0
                                        break
                                    if fh >= tgt_p:
                                        hit_tgt = True
                                        exit_r = spec["target_r"]
                                        break
                                if not hit_sl and not hit_tgt:
                                    exit_r = (c[i + max_fwd] - entry_p) / risk

                                all_outcomes.append({
                                    "scanner": "ACCUMULATION_VCP", "variant_id": spec["id"], "type": spec["type"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 2. EOD_BREAKOUT EVALUATION ───────────────────────────────────
            for spec in V59_CHALLENGERS["EOD_BREAKOUT"]:
                lb = spec["lb"]
                shelf_high = np.max(h[i-lb:i])
                shelf_low = np.min(l[i-lb:i])
                span_atr = (shelf_high - shelf_low) / curr_atr

                if curr_c > shelf_high and cpos >= spec["cpos"] and span_atr <= spec["max_span_atr"]:
                    if (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                        entry_p = curr_c
                        sl_p = shelf_low
                        risk = entry_p - sl_p
                        if risk >= curr_atr * 0.5:
                            tgt_p = entry_p + (risk * spec["target_r"])
                            max_fwd = min(spec["horizon"], n_bars - 1 - i)
                            hit_tgt, hit_sl = False, False
                            exit_r = 0.0
                            for f_idx in range(1, max_fwd + 1):
                                fh, fl = h[i + f_idx], l[i + f_idx]
                                if fl <= sl_p:
                                    hit_sl = True
                                    exit_r = -1.0
                                    break
                                if fh >= tgt_p:
                                    hit_tgt = True
                                    exit_r = spec["target_r"]
                                    break
                            if not hit_sl and not hit_tgt:
                                exit_r = (c[i + max_fwd] - entry_p) / risk

                            all_outcomes.append({
                                "scanner": "EOD_BREAKOUT", "variant_id": spec["id"], "type": spec["type"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 3. REVERSAL EVALUATION ───────────────────────────────────────
            for spec in V59_CHALLENGERS["REVERSAL"]:
                lb = spec["lb"]
                prior_low = np.min(l[i-lb:i])
                # Undercut and reclaim
                if curr_l < prior_low and curr_c > prior_low and cpos >= spec["min_clv"]:
                    if (curr_v / (vol20[i] + 1e-9)) >= spec["vol_mult"]:
                        entry_p = curr_c
                        sl_p = curr_l - (curr_atr * 0.15)
                        risk = entry_p - sl_p
                        if risk >= curr_atr * 0.4:
                            tgt_p = entry_p + (risk * spec["target_r"])
                            max_fwd = min(spec["horizon"], n_bars - 1 - i)
                            hit_tgt, hit_sl = False, False
                            exit_r = 0.0
                            for f_idx in range(1, max_fwd + 1):
                                fh, fl = h[i + f_idx], l[i + f_idx]
                                if fl <= sl_p:
                                    hit_sl = True
                                    exit_r = -1.0
                                    break
                                if fh >= tgt_p:
                                    hit_tgt = True
                                    exit_r = spec["target_r"]
                                    break
                            if not hit_sl and not hit_tgt:
                                exit_r = (c[i + max_fwd] - entry_p) / risk

                            all_outcomes.append({
                                "scanner": "REVERSAL", "variant_id": spec["id"], "type": spec["type"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 4. PULLBACK_V2 EVALUATION ────────────────────────────────────
            for spec in V59_CHALLENGERS["PULLBACK_V2"]:
                pb_bars = spec["pullback_bars"]
                # Price pulled back to near EMA20 in uptrend
                if curr_c > sma50[i] and curr_l <= sma20[i] * 1.01 and curr_c >= sma20[i] * 0.99:
                    if cpos >= spec["cpos"]:
                        recent_vol = np.mean(v[i-pb_bars:i]) / (vol20[i] + 1e-9)
                        if recent_vol <= spec["vol_dry"]:
                            entry_p = curr_c
                            sl_p = np.min(l[i-pb_bars:i+1]) - (curr_atr * 0.10)
                            risk = entry_p - sl_p
                            if risk >= curr_atr * 0.4:
                                tgt_p = entry_p + (risk * spec["target_r"])
                                max_fwd = min(spec["horizon"], n_bars - 1 - i)
                                hit_tgt, hit_sl = False, False
                                exit_r = 0.0
                                for f_idx in range(1, max_fwd + 1):
                                    fh, fl = h[i + f_idx], l[i + f_idx]
                                    if fl <= sl_p:
                                        hit_sl = True
                                        exit_r = -1.0
                                        break
                                    if fh >= tgt_p:
                                        hit_tgt = True
                                        exit_r = spec["target_r"]
                                        break
                                if not hit_sl and not hit_tgt:
                                    exit_r = (c[i + max_fwd] - entry_p) / risk

                                all_outcomes.append({
                                    "scanner": "PULLBACK_V2", "variant_id": spec["id"], "type": spec["type"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 5. TECHNICAL_AHAT EVALUATION ─────────────────────────────────
            for spec in V59_CHALLENGERS["TECHNICAL_AHAT"]:
                if curr_c > sma20[i] and curr_c > sma50[i] and rsi14[i] >= spec["rsi_min"] and cpos >= spec["cpos"]:
                    entry_p = curr_c
                    sl_p = min(sma20[i], curr_l) - (curr_atr * 0.10)
                    risk = entry_p - sl_p
                    if risk >= curr_atr * 0.4:
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        hit_tgt, hit_sl = False, False
                        exit_r = 0.0
                        for f_idx in range(1, max_fwd + 1):
                            fh, fl = h[i + f_idx], l[i + f_idx]
                            if fl <= sl_p:
                                hit_sl = True
                                exit_r = -1.0
                                break
                            if fh >= tgt_p:
                                hit_tgt = True
                                exit_r = spec["target_r"]
                                break
                        if not hit_sl and not hit_tgt:
                            exit_r = (c[i + max_fwd] - entry_p) / risk

                        all_outcomes.append({
                            "scanner": "TECHNICAL_AHAT", "variant_id": spec["id"], "type": spec["type"],
                            "symbol": sym, "date": dt, "partition": part, "regime": reg,
                            "gross_r": exit_r, "holding_type": spec["holding_type"]
                        })

            # ── 6. DAILY_BUILDER EVALUATION ──────────────────────────────────
            for spec in V59_CHALLENGERS["DAILY_BUILDER"]:
                lb = spec["lb"]
                hh = np.max(h[i-lb:i])
                if curr_c > hh and cpos >= spec["cpos"] and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                    entry_p = curr_c
                    sl_p = np.min(l[i-5:i+1])
                    risk = entry_p - sl_p
                    if risk >= curr_atr * 0.4:
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        hit_tgt, hit_sl = False, False
                        exit_r = 0.0
                        for f_idx in range(1, max_fwd + 1):
                            fh, fl = h[i + f_idx], l[i + f_idx]
                            if fl <= sl_p:
                                hit_sl = True
                                exit_r = -1.0
                                break
                            if fh >= tgt_p:
                                hit_tgt = True
                                exit_r = spec["target_r"]
                                break
                        if not hit_sl and not hit_tgt:
                            exit_r = (c[i + max_fwd] - entry_p) / risk

                        all_outcomes.append({
                            "scanner": "DAILY_BUILDER", "variant_id": spec["id"], "type": spec["type"],
                            "symbol": sym, "date": dt, "partition": part, "regime": reg,
                            "gross_r": exit_r, "holding_type": spec["holding_type"]
                        })

            # ── 7. SHORT_COVERING_EOD EVALUATION ─────────────────────────────
            for spec in V59_CHALLENGERS["SHORT_COVERING_EOD"]:
                if reg in ["BEAR", "NEUTRAL"]:
                    lb = spec["lb"]
                    prior_low = np.min(l[i-lb:i])
                    if curr_l < prior_low and curr_c > prior_low and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"] and cpos >= spec["cpos"]:
                        entry_p = curr_c
                        sl_p = curr_l - (curr_atr * 0.10)
                        risk = entry_p - sl_p
                        if risk >= curr_atr * 0.4:
                            tgt_p = entry_p + (risk * spec["target_r"])
                            max_fwd = min(spec["horizon"], n_bars - 1 - i)
                            hit_tgt, hit_sl = False, False
                            exit_r = 0.0
                            for f_idx in range(1, max_fwd + 1):
                                fh, fl = h[i + f_idx], l[i + f_idx]
                                if fl <= sl_p:
                                    hit_sl = True
                                    exit_r = -1.0
                                    break
                                if fh >= tgt_p:
                                    hit_tgt = True
                                    exit_r = spec["target_r"]
                                    break
                            if not hit_sl and not hit_tgt:
                                exit_r = (c[i + max_fwd] - entry_p) / risk

                            all_outcomes.append({
                                "scanner": "SHORT_COVERING_EOD", "variant_id": spec["id"], "type": spec["type"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 8. MULTIBAGGER EVALUATION ────────────────────────────────────
            for spec in V59_CHALLENGERS["MULTIBAGGER"]:
                lb = spec["lb"]
                base_high = np.max(h[i-lb:i])
                base_low = np.min(l[i-lb:i])
                if curr_c > base_high and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                    entry_p = curr_c
                    sl_p = np.min(l[i-15:i+1])
                    risk = entry_p - sl_p
                    if risk >= curr_atr * 0.8:
                        tgt_p = entry_p + (risk * spec["target_r"])
                        max_fwd = min(spec["horizon"], n_bars - 1 - i)
                        hit_tgt, hit_sl = False, False
                        exit_r = 0.0
                        for f_idx in range(1, max_fwd + 1):
                            fh, fl = h[i + f_idx], l[i + f_idx]
                            if fl <= sl_p:
                                hit_sl = True
                                exit_r = -1.0
                                break
                            if fh >= tgt_p:
                                hit_tgt = True
                                exit_r = spec["target_r"]
                                break
                        if not hit_sl and not hit_tgt:
                            exit_r = (c[i + max_fwd] - entry_p) / risk

                        all_outcomes.append({
                            "scanner": "MULTIBAGGER", "variant_id": spec["id"], "type": spec["type"],
                            "symbol": sym, "date": dt, "partition": part, "regime": reg,
                            "gross_r": exit_r, "holding_type": spec["holding_type"]
                        })

    out_df = pd.DataFrame(all_outcomes)
    print(f"\nCompleted sweep in {time.time()-t0:.1f}s. Total simulated trade records: {len(out_df)}")

    # Apply realistic Indian equity friction model
    out_df["r_multiple"] = out_df["gross_r"]
    out_df["net_r"] = out_df.apply(lambda r: apply_realistic_friction(r, r["holding_type"]), axis=1)

    # ── AGGREGATE RESULTS ON LOCKED REPRODUCTION HISTORICAL DATASET ──────────
    summary_rows = []
    print("\n" + "=" * 135)
    print("V5.9 WIN-RATE MAXIMIZATION RESULTS: CONTROL (V5.8) VS CHALLENGER (V5.9) ON LOCKED REPRODUCTION SET")
    print("=" * 135)
    print(f"{'SCANNER':<19} | {'VARIANT ID':<33} | {'TYPE':<10} | {'N':>4} | {'GROSS WR':>8} | {'NET WR':>7} | {'GROSS ER':>9} | {'NET ER':>8} | {'NET PF':>7} | {'NET 95% CI':>16} | {'NET DD':>7}")
    print("-" * 135)

    for scn, grp in out_df.groupby("scanner"):
        h_grp = grp[grp["partition"] == "LOCKED_REPRODUCTION"]
        for v_id, v_sub in h_grp.groupby("variant_id"):
            v_type = v_sub["type"].iloc[0]
            g_perf = calc_performance_summary(v_sub["gross_r"].values)
            n_perf = calc_performance_summary(v_sub["net_r"].values)

            summary_rows.append({
                "scanner": scn,
                "variant_id": v_id,
                "type": v_type,
                "n": n_perf["n"],
                "gross_wr": g_perf["win_pct"],
                "net_wr": n_perf["win_pct"],
                "gross_er": g_perf["er"],
                "net_er": n_perf["er"],
                "gross_pf": g_perf["pf"],
                "net_pf": n_perf["pf"],
                "net_ci_low": n_perf["ci_low"],
                "net_ci_high": n_perf["ci_high"],
                "net_max_dd": n_perf["max_dd_r"]
            })

            highlight = "⭐" if v_type == "CHALLENGER" and n_perf["win_pct"] >= 50.0 else "  "
            print(f"{highlight}{scn:<17} | {v_id:<33} | {v_type:<10} | {n_perf['n']:>4} | {g_perf['win_pct']:>7.1f}% | {n_perf['win_pct']:>6.1f}% | {g_perf['er']:>+8.3f}R | {n_perf['er']:>+7.3f}R | {n_perf['pf']:>7.2f} | [{n_perf['ci_low']:>+5.2f}, {n_perf['ci_high']:>+5.2f}] | {n_perf['max_dd_r']:>6.1f}R")
        print("-" * 135)

    res_df = pd.DataFrame(summary_rows)
    out_csv = os.path.join(_REPORTS_DIR, "v59_winrate_optimization_matrix.csv")
    res_df.to_csv(out_csv, index=False)
    print(f"\nSaved optimization matrix to: {out_csv}")

    # Save outcomes
    out_parquet_path = os.path.join(_REPORTS_DIR, "v59_winrate_optimization_outcomes.csv")
    out_df.to_csv(out_parquet_path, index=False)
    print(f"Saved all trade outcomes to: {out_parquet_path}")

if __name__ == "__main__":
    evaluate_scanner_variants()
