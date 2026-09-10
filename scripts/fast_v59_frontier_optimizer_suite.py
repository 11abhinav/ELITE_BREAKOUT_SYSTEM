#!/usr/bin/env python3
# =============================================================================
# scripts/fast_v59_frontier_optimizer_suite.py
# V5.9 MULTI-FRONTIER WIN-RATE & NET-ALPHA OPTIMIZATION ENGINE (UP TO 90% WR)
# =============================================================================
# Evaluates multi-tiered Win Rate frontiers (60%, 70%, 80%, 90%!) across all 11
# scanner families under strict institutional methodology:
# 1. V5.8 Control retained as immutable benchmark.
# 2. Scanner-specific objective functions:
#    - Strong Scanners: Maximize Net WR s.t. Net ER >= 80% Control & Net PF >= 1.35
#    - Weak Scanners: Maximize Net WR s.t. Net ER > +0.10R & Net PF >= 1.25
#    - Short Covering: Maximize Bear Net WR & Bear Net ER
#    - Multibagger: Optimize 5R+ frequency & right-tail convexity (NO WR constraint)
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

# ── DEFINITION OF MULTI-FRONTIER CHALLENGERS (SEARCHING TOWARD 90% WR) ───────
FRONTIER_SPECS = {
    "ACCUMULATION_VCP": [
        {"id": "VCP_V58_CONTROL", "tier": "CONTROL", "lb": 15, "bb_pct": 0.45, "dry_vol": 0.80, "vol_thrust": 1.5, "cpos": 0.65, "target_r": 3.0, "horizon": 20, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F90_ULTRA_PINCH", "tier": "FRONTIER_90", "lb": 15, "bb_pct": 0.25, "dry_vol": 0.60, "vol_thrust": 2.0, "cpos": 0.85, "target_r": 1.4, "horizon": 10, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F80_PRECISION", "tier": "FRONTIER_80", "lb": 15, "bb_pct": 0.30, "dry_vol": 0.65, "vol_thrust": 1.8, "cpos": 0.80, "target_r": 1.8, "horizon": 12, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F70_BALANCED", "tier": "FRONTIER_70", "lb": 15, "bb_pct": 0.35, "dry_vol": 0.70, "vol_thrust": 1.6, "cpos": 0.75, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
        {"id": "VCP_V59_F60_CAPACITY", "tier": "FRONTIER_60", "lb": 15, "bb_pct": 0.40, "dry_vol": 0.75, "vol_thrust": 1.5, "cpos": 0.70, "target_r": 2.5, "horizon": 20, "holding_type": "SWING_BREAKOUT"},
    ],
    "EOD_BREAKOUT": [
        {"id": "EOD_V58_CONTROL", "tier": "CONTROL", "lb": 15, "max_span_atr": 2.5, "cpos": 0.65, "vol_surge": 1.0, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F90_ULTRA_TIGHT", "tier": "FRONTIER_90", "lb": 12, "max_span_atr": 1.3, "cpos": 0.88, "vol_surge": 2.0, "target_r": 1.4, "horizon": 8, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F80_PRECISION", "tier": "FRONTIER_80", "lb": 15, "max_span_atr": 1.6, "cpos": 0.82, "vol_surge": 1.8, "target_r": 1.8, "horizon": 10, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F70_BALANCED", "tier": "FRONTIER_70", "lb": 15, "max_span_atr": 1.8, "cpos": 0.75, "vol_surge": 1.5, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_BREAKOUT"},
        {"id": "EOD_V59_F60_CAPACITY", "tier": "FRONTIER_60", "lb": 15, "max_span_atr": 2.0, "cpos": 0.70, "vol_surge": 1.3, "target_r": 2.4, "horizon": 15, "holding_type": "SWING_BREAKOUT"},
    ],
    "REVERSAL": [
        {"id": "REV_V58_CONTROL", "tier": "CONTROL", "lb": 15, "min_clv": 0.60, "vol_mult": 1.2, "target_r": 3.0, "horizon": 15, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F90_ULTRA_EXHAUST", "tier": "FRONTIER_90", "lb": 15, "min_clv": 0.90, "vol_mult": 2.0, "target_r": 1.5, "horizon": 8, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F80_PRECISION", "tier": "FRONTIER_80", "lb": 15, "min_clv": 0.85, "vol_mult": 1.8, "target_r": 1.8, "horizon": 10, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F70_BALANCED", "tier": "FRONTIER_70", "lb": 15, "min_clv": 0.78, "vol_mult": 1.5, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_COUNTER_TREND"},
        {"id": "REV_V59_F60_ALPHA", "tier": "FRONTIER_60", "lb": 15, "min_clv": 0.72, "vol_mult": 1.4, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_COUNTER_TREND"},
    ],
    "PULLBACK_V2": [
        {"id": "PULL_V58_CONTROL", "tier": "CONTROL", "pullback_bars": 5, "cpos": 0.60, "vol_dry": 0.85, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F90_ENGULF_BOUNCE", "tier": "FRONTIER_90", "pullback_bars": 4, "cpos": 0.88, "vol_dry": 0.60, "target_r": 1.4, "horizon": 8, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F80_PRECISION", "tier": "FRONTIER_80", "pullback_bars": 4, "cpos": 0.82, "vol_dry": 0.65, "target_r": 1.8, "horizon": 10, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F70_BALANCED", "tier": "FRONTIER_70", "pullback_bars": 5, "cpos": 0.75, "vol_dry": 0.70, "target_r": 2.0, "horizon": 12, "holding_type": "SWING_TREND"},
        {"id": "PULL_V59_F60_CAPACITY", "tier": "FRONTIER_60", "pullback_bars": 5, "cpos": 0.70, "vol_dry": 0.75, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_TREND"},
    ],
    "TECHNICAL_AHAT": [
        {"id": "AHAT_V58_CONTROL", "tier": "CONTROL", "cpos": 0.60, "rsi_min": 48, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_CONFLUENCE"},
        {"id": "AHAT_V59_F80_TRIPLE_CONFL", "tier": "FRONTIER_80", "cpos": 0.85, "rsi_min": 60, "target_r": 1.6, "horizon": 10, "holding_type": "SWING_CONFLUENCE"},
        {"id": "AHAT_V59_F70_PRECISION", "tier": "FRONTIER_70", "cpos": 0.80, "rsi_min": 56, "target_r": 1.9, "horizon": 12, "holding_type": "SWING_CONFLUENCE"},
        {"id": "AHAT_V59_F60_BALANCED", "tier": "FRONTIER_60", "cpos": 0.75, "rsi_min": 52, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_CONFLUENCE"},
    ],
    "DAILY_BUILDER": [
        {"id": "BUILD_V58_CONTROL", "tier": "CONTROL", "lb": 20, "cpos": 0.60, "vol_surge": 1.2, "target_r": 2.5, "horizon": 15, "holding_type": "SWING_BREADTH"},
        {"id": "BUILD_V59_F80_SURGE_PINCH", "tier": "FRONTIER_80", "lb": 12, "cpos": 0.85, "vol_surge": 2.0, "target_r": 1.6, "horizon": 10, "holding_type": "SWING_BREADTH"},
        {"id": "BUILD_V59_F70_PRECISION", "tier": "FRONTIER_70", "lb": 15, "cpos": 0.80, "vol_surge": 1.7, "target_r": 1.9, "horizon": 12, "holding_type": "SWING_BREADTH"},
        {"id": "BUILD_V59_F60_BALANCED", "tier": "FRONTIER_60", "lb": 20, "cpos": 0.75, "vol_surge": 1.5, "target_r": 2.2, "horizon": 15, "holding_type": "SWING_BREADTH"},
    ],
    "SHORT_COVERING_EOD": [
        {"id": "SC_V58_CONTROL", "tier": "CONTROL", "lb": 15, "vol_surge": 1.6, "cpos": 0.65, "target_r": 2.5, "horizon": 12, "holding_type": "SWING_SQUEEZE"},
        {"id": "SC_V59_F80_SQUEEZE_SURGE", "tier": "FRONTIER_80", "lb": 12, "vol_surge": 2.2, "cpos": 0.85, "target_r": 1.6, "horizon": 8, "holding_type": "SWING_SQUEEZE"},
        {"id": "SC_V59_F70_PRECISION", "tier": "FRONTIER_70", "lb": 15, "vol_surge": 1.8, "cpos": 0.80, "target_r": 1.9, "horizon": 10, "holding_type": "SWING_SQUEEZE"},
        {"id": "SC_V59_F60_BALANCED", "tier": "FRONTIER_60", "lb": 15, "vol_surge": 1.5, "cpos": 0.72, "target_r": 2.2, "horizon": 12, "holding_type": "SWING_SQUEEZE"},
    ],
    "MULTIBAGGER": [
        {"id": "MBAG_V58_CONTROL", "tier": "CONTROL", "lb": 60, "vol_surge": 1.65, "target_r": 5.0, "horizon": 40, "holding_type": "POSITIONAL_CONVEXITY"},
        {"id": "MBAG_V59_CONVEX_A_75D_180V_5R", "tier": "CONVEXITY_75D", "lb": 75, "vol_surge": 1.80, "target_r": 5.0, "horizon": 45, "holding_type": "POSITIONAL_CONVEXITY"},
        {"id": "MBAG_V59_CONVEX_B_90D_200V_55R", "tier": "CONVEXITY_90D", "lb": 90, "vol_surge": 2.00, "target_r": 5.5, "horizon": 50, "holding_type": "POSITIONAL_CONVEXITY"},
    ]
}

def run_multi_frontier_optimizer():
    print("=" * 125)
    print("V5.9 MULTI-FRONTIER WIN-RATE OPTIMIZER (TESTING UP TO 90% WIN-RATE FRONTIERS)")
    print("=" * 125)
    t0 = time.time()

    daily_files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Loaded {len(daily_files)} daily equity parquets.")

    all_outcomes = []

    for fpath in daily_files:
        sym = os.path.basename(fpath).replace(".parquet", "").upper()
        try:
            df = pd.read_parquet(fpath)
            if df is None or len(df) < 110:
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

        n_bars = len(df)
        if n_bars < 110:
            continue

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

        delta = pd.Series(c).diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14, min_periods=5).mean()
        avg_loss = loss.rolling(14, min_periods=5).mean()
        rs = avg_gain / (avg_loss + 1e-9)
        rsi14 = (100.0 - (100.0 / (1.0 + rs))).values

        for i in range(100, n_bars - 1):
            dt = dates[i]
            if dt < DEV_START or dt > HLD_END:
                continue

            part = "CALIBRATION_DEV" if dt < VAL_START else ("TUNING_VAL" if dt < HLD_START else "LOCKED_REPRODUCTION")
            reg = get_regime(sma50[i], sma200[i], c[i])

            curr_c = c[i]
            curr_h = h[i]
            curr_l = l[i]
            curr_v = v[i]
            curr_atr = atr14[i] if atr14[i] > 0 else curr_c * 0.02
            cpos = (curr_c - curr_l) / (curr_h - curr_l + 1e-9)

            # ── 1. ACCUMULATION_VCP ──────────────────────────────────────────
            for spec in FRONTIER_SPECS["ACCUMULATION_VCP"]:
                lb = spec["lb"]
                if i - lb < 0 or i - 5 < 0:
                    continue
                high_box = np.max(h[i-lb:i])
                low_box = np.min(l[i-lb:i])
                box_width = high_box - low_box

                if curr_c > high_box and cpos >= spec["cpos"]:
                    if box_width <= spec["bb_pct"] * curr_atr * 4.0:
                        prior_vol_dry = np.mean(v[i-5:i]) / (vol20[i] + 1e-9)
                        if prior_vol_dry <= spec["dry_vol"] and (curr_v / (vol20[i] + 1e-9)) >= spec["vol_thrust"]:
                            entry_p = curr_c
                            sl_p = low_box
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
                                    "scanner": "ACCUMULATION_VCP", "variant_id": spec["id"], "tier": spec["tier"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 2. EOD_BREAKOUT ──────────────────────────────────────────────
            for spec in FRONTIER_SPECS["EOD_BREAKOUT"]:
                lb = spec["lb"]
                if i - lb < 0:
                    continue
                shelf_high = np.max(h[i-lb:i])
                shelf_low = np.min(l[i-lb:i])
                span_atr = (shelf_high - shelf_low) / curr_atr

                if curr_c > shelf_high and cpos >= spec["cpos"] and span_atr <= spec["max_span_atr"]:
                    if (curr_v / (vol20[i] + 1e-9)) >= spec["vol_surge"]:
                        entry_p = curr_c
                        sl_p = shelf_low
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
                                "scanner": "EOD_BREAKOUT", "variant_id": spec["id"], "tier": spec["tier"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 3. REVERSAL ──────────────────────────────────────────────────
            for spec in FRONTIER_SPECS["REVERSAL"]:
                lb = spec["lb"]
                if i - lb < 0:
                    continue
                prior_low = np.min(l[i-lb:i])
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
                                "scanner": "REVERSAL", "variant_id": spec["id"], "tier": spec["tier"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 4. PULLBACK_V2 ───────────────────────────────────────────────
            for spec in FRONTIER_SPECS["PULLBACK_V2"]:
                pb_bars = spec["pullback_bars"]
                if i - pb_bars < 0:
                    continue
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
                                    "scanner": "PULLBACK_V2", "variant_id": spec["id"], "tier": spec["tier"],
                                    "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                    "gross_r": exit_r, "holding_type": spec["holding_type"]
                                })

            # ── 5. TECHNICAL_AHAT ────────────────────────────────────────────
            for spec in FRONTIER_SPECS["TECHNICAL_AHAT"]:
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
                            "scanner": "TECHNICAL_AHAT", "variant_id": spec["id"], "tier": spec["tier"],
                            "symbol": sym, "date": dt, "partition": part, "regime": reg,
                            "gross_r": exit_r, "holding_type": spec["holding_type"]
                        })

            # ── 6. DAILY_BUILDER ─────────────────────────────────────────────
            for spec in FRONTIER_SPECS["DAILY_BUILDER"]:
                lb = spec["lb"]
                if i - lb < 0 or i - 5 < 0:
                    continue
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
                            "scanner": "DAILY_BUILDER", "variant_id": spec["id"], "tier": spec["tier"],
                            "symbol": sym, "date": dt, "partition": part, "regime": reg,
                            "gross_r": exit_r, "holding_type": spec["holding_type"]
                        })

            # ── 7. SHORT_COVERING_EOD ────────────────────────────────────────
            for spec in FRONTIER_SPECS["SHORT_COVERING_EOD"]:
                if reg in ["BEAR", "NEUTRAL"]:
                    lb = spec["lb"]
                    if i - lb < 0:
                        continue
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
                                "scanner": "SHORT_COVERING_EOD", "variant_id": spec["id"], "tier": spec["tier"],
                                "symbol": sym, "date": dt, "partition": part, "regime": reg,
                                "gross_r": exit_r, "holding_type": spec["holding_type"]
                            })

            # ── 8. MULTIBAGGER ───────────────────────────────────────────────
            for spec in FRONTIER_SPECS["MULTIBAGGER"]:
                lb = spec["lb"]
                if i - lb < 0 or i - 15 < 0:
                    continue
                base_high = np.max(h[i-lb:i])
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
                            "scanner": "MULTIBAGGER", "variant_id": spec["id"], "tier": spec["tier"],
                            "symbol": sym, "date": dt, "partition": part, "regime": reg,
                            "gross_r": exit_r, "holding_type": spec["holding_type"]
                        })

    out_df = pd.DataFrame(all_outcomes)
    print(f"\nCompleted multi-frontier sweep in {time.time()-t0:.1f}s. Total simulated trade outcomes: {len(out_df)}")

    # Apply realistic execution friction
    out_df["r_multiple"] = out_df["gross_r"]
    out_df["net_r"] = out_df.apply(lambda r: apply_realistic_friction(r, r["holding_type"]), axis=1)

    # ── AGGREGATE RESULTS ACROSS MULTI-TIERED WIN-RATE FRONTIERS ─────────────
    summary_rows = []
    print("\n" + "=" * 140)
    print("V5.9 MULTI-FRONTIER WIN-RATE CERTIFICATION MATRIX (CONTROL VS 60%, 70%, 80%, 90% FRONTIERS)")
    print("=" * 140)
    print(f"{'SCANNER':<19} | {'VARIANT ID':<32} | {'FRONTIER':<14} | {'N':>4} | {'GROSS WR':>8} | {'NET WR':>7} | {'GROSS ER':>9} | {'NET ER':>8} | {'NET PF':>7} | {'NET 95% CI':>16} | {'NET DD':>7}")
    print("-" * 140)

    for scn, grp in out_df.groupby("scanner"):
        h_grp = grp[grp["partition"] == "LOCKED_REPRODUCTION"]
        for v_id, v_sub in h_grp.groupby("variant_id"):
            v_tier = v_sub["tier"].iloc[0]
            g_perf = calc_performance_summary(v_sub["gross_r"].values)
            n_perf = calc_performance_summary(v_sub["net_r"].values)

            summary_rows.append({
                "scanner": scn,
                "variant_id": v_id,
                "tier": v_tier,
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

            frontier_badge = "👑 " if "F90" in v_tier or "F80" in v_tier else ("⭐ " if "F70" in v_tier else "   ")
            print(f"{frontier_badge}{scn:<16} | {v_id:<32} | {v_tier:<14} | {n_perf['n']:>4} | {g_perf['win_pct']:>7.1f}% | {n_perf['win_pct']:>6.1f}% | {g_perf['er']:>+8.3f}R | {n_perf['er']:>+7.3f}R | {n_perf['pf']:>7.2f} | [{n_perf['ci_low']:>+5.2f}, {n_perf['ci_high']:>+5.2f}] | {n_perf['max_dd_r']:>6.1f}R")
        print("-" * 140)

    res_df = pd.DataFrame(summary_rows)
    out_csv = os.path.join(_REPORTS_DIR, "v59_frontier_optimization_matrix.csv")
    res_df.to_csv(out_csv, index=False)
    print(f"\nSaved multi-frontier optimization matrix to: {out_csv}")

    out_json = os.path.join(_REPORTS_DIR, "v59_frontier_optimization_results.json")
    with open(out_json, "w") as f:
        json.dump(summary_rows, f, indent=2)
    print(f"Saved JSON results to: {out_json}")

if __name__ == "__main__":
    run_multi_frontier_optimizer()
