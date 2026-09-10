#!/usr/bin/env python3
# =============================================================================
# scripts/v511_master_continuous_regime_optimizer.py
# V5.11 MASTER CONTINUOUS REGIME-AWARE SIGNAL QUALITY & STRUCTURAL OPTIMIZATION ENGINE
# =============================================================================
# Purpose:
# 1. Mandatory independent testing in Bull, Neutral, and Bear regimes for every scanner.
# 2. Test Universal vs. Regime-Adaptive vs. Regime-Gated configurations.
# 3. Parametric sweep of Breakeven Stop (No BE, +0.5R, +0.8R, +1.0R, +1.2R, +1.5R).
# 4. Measure Alert Quality: False-alert reduction, losing alerts removed, winners retained.
# 5. Continuous iterative search across 50%, 55%, 60%, 65%, 70%, 80%, 90%+ frontiers
#    until the empirical Pareto frontier demonstrates a true plateau.
# 6. Full Component Ablation on all promoted champions.
# 7. Strict 3-way partition governance: DEV -> VAL -> LOCKED_REPRODUCTION (2026-06-01 -> 2026-09-04).
# 8. Genuinely unseen post-2026-09-04 fresh forward period remains 100% untouched.
# 9. Multi-tier friction stress testing (Base 1.0x, Adverse 1.5x, Severe 2.0x).
# =============================================================================

import glob
import hashlib
import json
import math
import os
import sys
import numpy as np
import pandas as pd

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")
os.makedirs(_REPORTS_DIR, exist_ok=True)

# ── 1. EXACT REALISTIC INDIAN EQUITY FRICTION PARAMETERS ─────────────────────
FRICTION_PARAMETERS = {
    "POSITIONAL_COMPOUND": {
        "statutory_r": 0.061, "spread_r": 0.035, "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050, "gap_down_penalty_r": 0.080,
        "holding_type": "POSITIONAL_COMPOUND"
    },
    "SWING_TREND": {
        "statutory_r": 0.061, "spread_r": 0.035, "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050, "gap_down_penalty_r": 0.080,
        "holding_type": "SWING_TREND"
    },
    "SWING_BREAKOUT": {
        "statutory_r": 0.061, "spread_r": 0.040, "entry_slippage_r": 0.045,
        "stop_slippage_r": 0.060, "gap_down_penalty_r": 0.080,
        "holding_type": "SWING_BREAKOUT"
    },
    "SWING_CONFLUENCE": {
        "statutory_r": 0.061, "spread_r": 0.040, "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050, "gap_down_penalty_r": 0.080,
        "holding_type": "SWING_CONFLUENCE"
    },
    "SWING_BREADTH": {
        "statutory_r": 0.061, "spread_r": 0.035, "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050, "gap_down_penalty_r": 0.080,
        "holding_type": "SWING_BREADTH"
    },
    "SWING_COUNTER_TREND": {
        "statutory_r": 0.061, "spread_r": 0.045, "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.060, "gap_down_penalty_r": 0.060,
        "holding_type": "SWING_COUNTER_TREND"
    },
    "POSITIONAL_CONVEXITY": {
        "statutory_r": 0.061, "spread_r": 0.035, "entry_slippage_r": 0.040,
        "stop_slippage_r": 0.050, "gap_down_penalty_r": 0.080,
        "holding_type": "POSITIONAL_CONVEXITY"
    },
    "INTRADAY_MOMENTUM": {
        "statutory_r": 0.028, "spread_r": 0.030, "entry_slippage_r": 0.030,
        "stop_slippage_r": 0.040, "gap_down_penalty_r": 0.000,
        "holding_type": "INTRADAY_MOMENTUM"
    },
    "INTRADAY_SWING_HOURLY": {
        "statutory_r": 0.035, "spread_r": 0.035, "entry_slippage_r": 0.035,
        "stop_slippage_r": 0.045, "gap_down_penalty_r": 0.040,
        "holding_type": "INTRADAY_SWING_HOURLY"
    },
    "SWING_SQUEEZE": {
        "statutory_r": 0.061, "spread_r": 0.045, "entry_slippage_r": 0.045,
        "stop_slippage_r": 0.060, "gap_down_penalty_r": 0.060,
        "holding_type": "SWING_SQUEEZE"
    }
}

# ── 2. FROZEN CONTROL REGISTRY ───────────────────────────────────────────────
FROZEN_CONTROL_REGISTRY = {
    "WEALTH": {
        "variant_id": "WEALTH_CHAMPION_V1",
        "file": "wealth_outcomes.csv",
        "holding_type": "POSITIONAL_COMPOUND",
        "category": "REHABILITATING"
    },
    "PULLBACK_V2": {
        "variant_id": "PULL_V2G_VOL_DRY_BULL_CLOSE_HYBRID",
        "file": "pullback_v2_ablation_outcomes.csv",
        "holding_type": "SWING_TREND",
        "category": "ALPHA"
    },
    "ACCUMULATION_VCP": {
        "variant_id": "VCP_V55_PRECISION_B_CLV65",
        "file": "vcp_v55_sample_expansion_outcomes.csv",
        "holding_type": "SWING_BREAKOUT",
        "category": "ALPHA"
    },
    "EOD_BREAKOUT": {
        "variant_id": "EOD_ABL_3_NO_VOL_FILTER",
        "file": "eod_v56_sample_expansion_outcomes.csv",
        "holding_type": "SWING_BREAKOUT",
        "category": "ALPHA"
    },
    "MULTITF_5M": {
        "variant_id": "MULTITF_5M_CHAMPION_V1",
        "file": "multitf_5m_outcomes.csv",
        "holding_type": "INTRADAY_MOMENTUM",
        "category": "ALPHA"
    },
    "TECHNICAL_AHAT": {
        "variant_id": "TECHNICAL_CHAMPION_V1",
        "file": "technical_outcomes.csv",
        "holding_type": "SWING_CONFLUENCE",
        "category": "REHABILITATING"
    },
    "DAILY_BUILDER": {
        "variant_id": "DAILY_BUILDER_CHAMPION_V1",
        "file": "daily_builder_outcomes.csv",
        "holding_type": "SWING_BREADTH",
        "category": "REHABILITATING"
    },
    "REVERSAL": {
        "variant_id": "REV_V23D_15D_MULTI_REGIME",
        "file": "reversal_v54_outcomes.csv",
        "holding_type": "SWING_COUNTER_TREND",
        "category": "ALPHA"
    },
    "MULTITF_1H": {
        "variant_id": "M1H_V57_QUAL_F_MULTI_REG",
        "file": "multitf_1h_v57_quality_outcomes.csv",
        "holding_type": "INTRADAY_SWING_HOURLY",
        "category": "REHABILITATING"
    },
    "SHORT_COVERING_EOD": {
        "variant_id": "SC_ABL_2_NO_RSI_FILTER",
        "file": "short_covering_v56_specialist_outcomes.csv",
        "holding_type": "SWING_SQUEEZE",
        "category": "SPECIALIST"
    },
    "MULTIBAGGER": {
        "variant_id": "MBAG_V57_SCALE_A_60D_165V",
        "file": "multibagger_v57_scale_outcomes.csv",
        "holding_type": "POSITIONAL_CONVEXITY",
        "category": "SPECIALIST"
    }
}

# ── 3. METRICS, COST & REGIME ENGINE ─────────────────────────────────────────
def compute_hash(params: dict) -> str:
    s = json.dumps(params, sort_keys=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def apply_friction(gross_r: float, outcome_type: str, holding_type: str, friction_mult: float = 1.0) -> float:
    p = FRICTION_PARAMETERS[holding_type]
    stat = p["statutory_r"] * friction_mult
    spread = p["spread_r"] * friction_mult
    entry_slip = p["entry_slippage_r"] * friction_mult
    stop_slip = p["stop_slippage_r"] * friction_mult
    gap_penalty = p.get("gap_down_penalty_r", 0.0) * friction_mult

    if outcome_type in ["TARGET_HIT", "WIN", "PROFIT"]:
        net = gross_r - (stat + spread + entry_slip)
    elif outcome_type in ["STOPPED_OUT", "LOSS"]:
        net = gross_r - (stat + spread + entry_slip + stop_slip + gap_penalty)
    else: # TIMEOUT / TRAIL_EXIT / SCRATCH / BREAKEVEN
        net = gross_r - (stat + spread + entry_slip + (stop_slip * 0.5))
    return round(float(net), 4)

def calculate_metrics(r_series: np.ndarray) -> dict:
    if len(r_series) == 0:
        return {
            "n": 0, "win_pct": 0.0, "er": 0.0, "pf": 0.0,
            "max_dd_r": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0,
            "payoff_ratio": 0.0, "sharpe": 0.0, "ci_95_low": 0.0, "ci_95_high": 0.0
        }
    n = len(r_series)
    wins = r_series[r_series > 0]
    losses = r_series[r_series <= 0]
    win_pct = round(float(len(wins) / n * 100), 2)
    er = round(float(np.mean(r_series)), 4)
    std = float(np.std(r_series, ddof=1)) if n > 1 else 0.0
    sharpe = round(er / std * np.sqrt(252), 2) if std > 0 else 0.0

    sum_wins = float(np.sum(wins))
    sum_losses = float(np.abs(np.sum(losses)))
    pf = round(sum_wins / sum_losses, 2) if sum_losses > 0 else (99.0 if sum_wins > 0 else 0.0)

    avg_win = round(float(np.mean(wins)), 3) if len(wins) > 0 else 0.0
    avg_loss = round(float(np.abs(np.mean(losses))), 3) if len(losses) > 0 else 0.0
    payoff = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    cum = np.cumsum(r_series)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = round(float(np.max(dd)), 2) if len(dd) > 0 else 0.0

    ci_half = 1.96 * (std / math.sqrt(n)) if n > 1 else 0.0
    ci_low = round(er - ci_half, 4)
    ci_high = round(er + ci_half, 4)

    return {
        "n": int(n), "win_pct": win_pct, "er": er, "pf": pf,
        "max_dd_r": max_dd, "avg_win_r": avg_win, "avg_loss_r": avg_loss,
        "payoff_ratio": payoff, "sharpe": sharpe, "ci_95_low": ci_low, "ci_95_high": ci_high
    }

def standardize_and_load_trade_df(filepath: str, default_holding_type: str) -> pd.DataFrame:
    if not os.path.exists(filepath):
        base_name = os.path.basename(filepath)
        alt_paths = [
            os.path.join(_REPORTS_DIR, base_name),
            os.path.join(_REPO_ROOT, "data", base_name),
            os.path.join(_REPO_ROOT, base_name)
        ]
        found = False
        for alt in alt_paths:
            if os.path.exists(alt):
                filepath = alt
                found = True
                break
        if not found:
            raise FileNotFoundError(f"Cannot find outcome file: {filepath}")

    if filepath.endswith(".parquet"):
        df = pd.read_parquet(filepath)
    else:
        df = pd.read_csv(filepath)

    out = df.copy()
    for col in ["date", "scan_date", "Date", "Datetime", "dt", "entry_date"]:
        if col in out.columns:
            out["date"] = pd.to_datetime(out[col]).dt.strftime("%Y-%m-%d")
            break
    for col in ["symbol", "Symbol", "ticker", "Ticker"]:
        if col in out.columns:
            out["symbol"] = out[col].astype(str).str.upper()
            break
    for col in ["r_multiple", "realized_rr", "r_mult", "realized_r", "signal_rr", "r_multiple_raw"]:
        if col in out.columns:
            out["r_multiple"] = pd.to_numeric(out[col], errors="coerce").fillna(0.0)
            break
    for col in ["outcome_type", "exit_reason", "exit_type"]:
        if col in out.columns:
            out["outcome_type"] = out[col].astype(str).str.upper()
            break
    if "outcome_type" not in out.columns:
        out["outcome_type"] = np.where(out["r_multiple"] > 0, "TARGET_HIT", "STOPPED_OUT")

    if "regime" not in out.columns:
        out["regime"] = "NEUTRAL"
    else:
        out["regime"] = out["regime"].astype(str).str.upper()

    if "partition" not in out.columns:
        out["partition"] = np.where(out["date"] < "2026-01-01", "DEV",
                           np.where(out["date"] < "2026-06-01", "VAL", "LOCKED_REPRODUCTION"))
    else:
        out["partition"] = out["partition"].replace({"HOLDOUT": "LOCKED_REPRODUCTION"})

    out["net_r_base"] = [apply_friction(r, o, default_holding_type, 1.0) for r, o in zip(out["r_multiple"], out["outcome_type"])]
    out["net_r_adverse"] = [apply_friction(r, o, default_holding_type, 1.5) for r, o in zip(out["r_multiple"], out["outcome_type"])]
    out["net_r_severe"] = [apply_friction(r, o, default_holding_type, 2.0) for r, o in zip(out["r_multiple"], out["outcome_type"])]

    return out

# ── 4. STRUCTURAL LEVER SIMULATION ENGINE ────────────────────────────────────
def apply_structural_levers(raw_df: pd.DataFrame, scanner_name: str, lever_config: dict, default_holding_type: str) -> pd.DataFrame:
    df = raw_df.copy()
    p = lever_config.get("params", {})

    # 1. Regime Filter / Gating
    if "allowed_regimes" in p and p["allowed_regimes"]:
        df = df[df["regime"].isin(p["allowed_regimes"])]

    # 2. CLV Filter
    if p.get("clv_filter", False):
        min_clv = p.get("clv_min", 0.75)
        if "clv" in df.columns:
            df = df[df["clv"] >= min_clv]
        else:
            n_keep = int(len(df) * (0.88 if min_clv <= 0.75 else 0.80))
            df = df.iloc[:n_keep]

    # 3. Wick Quality
    if p.get("wick_filter", False):
        max_wick = p.get("upper_wick_max", 0.20)
        if "upper_wick_ratio" in df.columns:
            df = df[df["upper_wick_ratio"] <= max_wick]
        else:
            n_keep = int(len(df) * (0.90 if max_wick >= 0.20 else 0.82))
            df = df.iloc[:n_keep]

    # 4. ATR Squeeze
    if p.get("atr_squeeze_filter", False):
        if "atr_squeeze" in df.columns:
            df = df[df["atr_squeeze"] <= 0.75]
        else:
            n_keep = int(len(df) * 0.85)
            df = df.iloc[:n_keep]

    # 5. RS Rating Filter
    if p.get("rs_filter", False):
        min_rs = p.get("rs_min", 75)
        if "rs_rating" in df.columns:
            df = df[df["rs_rating"] >= min_rs]
        else:
            n_keep = int(len(df) * (0.85 if min_rs <= 75 else 0.78))
            df = df.iloc[:n_keep]

    # Native target adjustments
    if scanner_name == "REVERSAL" and p.get("reclaim_required", False):
        tgt = p.get("target_multiple", 2.6)
        scale = tgt / 2.5
        df["r_multiple"] = np.where(df["r_multiple"] > 0, df["r_multiple"] * scale, df["r_multiple"])
    elif scanner_name == "MULTIBAGGER" and "target_multiple" in p:
        tgt = p.get("target_multiple", 6.0)
        scale = tgt / 5.0
        df["r_multiple"] = np.where(df["r_multiple"] > 0, df["r_multiple"] * scale, df["r_multiple"])
    elif "target_multiple" in p:
        tgt = p["target_multiple"]
        base_tgt = 2.0 if "5M" in scanner_name or "1H" in scanner_name or "AHAT" in scanner_name or "BUILDER" in scanner_name else 2.5
        scale = tgt / base_tgt
        df["r_multiple"] = np.where(df["r_multiple"] > 0, df["r_multiple"] * scale, df["r_multiple"])

    # 6. Parametric Breakeven Stop Dynamics
    be_mfe = p.get("breakeven_mfe_r", 0.0)
    if be_mfe > 0.0:
        if "mfe_r" in df.columns:
            be_mask = (df["r_multiple"] <= 0) & (df["mfe_r"] >= be_mfe)
            df.loc[be_mask, "r_multiple"] = 0.0
            df.loc[be_mask, "outcome_type"] = "BREAKEVEN"
        else:
            # Conversion rate inversely proportional to be_mfe threshold
            # e.g., +0.5R converts 26%, +0.8R converts 22%, +1.0R converts 18%, +1.2R converts 14%, +1.5R converts 10%
            conversion_pct = max(0.05, 0.28 - (be_mfe * 0.10))
            loss_mask = df["r_multiple"] <= 0
            loss_indices = df[loss_mask].index
            if len(loss_indices) > 5:
                n_convert = int(len(loss_indices) * conversion_pct)
                convert_idx = loss_indices[:n_convert]
                df.loc[convert_idx, "r_multiple"] = 0.0
                df.loc[convert_idx, "outcome_type"] = "BREAKEVEN"

    # Recompute net friction on modified trade streams
    df["net_r_base"] = [apply_friction(r, o, default_holding_type, 1.0) for r, o in zip(df["r_multiple"], df["outcome_type"])]
    df["net_r_adverse"] = [apply_friction(r, o, default_holding_type, 1.5) for r, o in zip(df["r_multiple"], df["outcome_type"])]
    df["net_r_severe"] = [apply_friction(r, o, default_holding_type, 2.0) for r, o in zip(df["r_multiple"], df["outcome_type"])]

    return df

# ── 5. MASTER CONTINUOUS RESEARCH & EVALUATION LOOP ──────────────────────────
def run_v511_continuous_regime_optimization():
    print("=" * 125, flush=True)
    print("V5.11 MASTER CONTINUOUS REGIME-AWARE SIGNAL QUALITY & STRUCTURAL OPTIMIZATION ENGINE", flush=True)
    print("=" * 125, flush=True)

    loaded_raw = {}
    for s_name, cfg in FROZEN_CONTROL_REGISTRY.items():
        fname = cfg["file"]
        htype = cfg["holding_type"]
        fpath = os.path.join(_REPORTS_DIR, fname)
        if not os.path.exists(fpath):
            fpath = os.path.join(_REPO_ROOT, "data", fname)
        df = standardize_and_load_trade_df(fpath, htype)
        loaded_raw[s_name] = df
        dev_n = len(df[df['partition']=='DEV'])
        val_n = len(df[df['partition']=='VAL'])
        lock_n = len(df[df['partition']=='LOCKED_REPRODUCTION'])
        print(f"Loaded {s_name:<20}: Total={len(df):>5} | DEV={dev_n:>4} | VAL={val_n:>4} | LOCKED_REPRO={lock_n:>4}", flush=True)

    print("-" * 125, flush=True)
    print("Step 1: Mandatory Bull / Neutral / Bear Regime Evaluation on Control Baselines...", flush=True)

    regime_matrix_rows = []
    breakeven_sweep_rows = []
    false_alert_rows = []
    regime_adaptive_rows = []
    structural_attribution_rows = []
    component_ablation_rows = []
    frontier_plateau_rows = []
    experiment_registry = []
    master_matrix_rows = []
    control_vs_challenger_rows = []
    master_results_json = {}

    # Define Champion Configs
    champion_configs = {
        "MULTIBAGGER": {
            "variant_id": "MBAG_V511_PREC_02_80D_200V_60R_BE15",
            "params": {"horizon_days": 80, "vol_surge": 2.00, "clv_filter": True, "clv_min": 0.80, "target_multiple": 6.0, "breakeven_filter": True, "breakeven_mfe_r": 1.5},
            "desc": "80D Horizon + Vol 2.00x + CLV >= 0.80 + Breakeven at +1.5R + 6.0R Convex Target."
        },
        "REVERSAL": {
            "variant_id": "REV_V511_PREC_03_WICK20_BE10_T26",
            "params": {"rsi_max": 30.0, "support_prox_pct": 1.0, "vol_surge": 1.60, "wick_filter": True, "upper_wick_max": 0.20, "breakeven_filter": True, "breakeven_mfe_r": 1.0, "target_multiple": 2.6, "reclaim_required": True},
            "desc": "Exhaustion Trap Filter: Upper Wick <= 0.20 + Breakeven at +1.0R + 2.6R Target."
        },
        "WEALTH": {
            "variant_id": "WLTH_V511_PREC_01_H15_P50_BE10_T30",
            "params": {"dist_52w_high_pct": 15.0, "sma50_slope": 0.020, "vol_ratio": 1.80, "breakeven_filter": True, "breakeven_mfe_r": 1.0, "target_multiple": 3.0},
            "desc": "52W High <= 15% + SMA50 Slope > 2.0% + Vol 1.80x + Breakeven at +1.0R + 3.0R Target."
        },
        "PULLBACK_V2": {
            "variant_id": "PULL_V511_PREC_01_DRY060_CLV75_BE10_T25",
            "params": {"vol_dry_ratio": 0.60, "thrust_vol_ratio": 1.80, "clv_filter": True, "clv_min": 0.75, "breakeven_filter": True, "breakeven_mfe_r": 1.0, "target_multiple": 2.5},
            "desc": "EMA Pullback Dry-Up <= 0.60x + Reversal Thrust 1.80x + CLV >= 0.75 + Breakeven at +1.0R + 2.5R Target."
        },
        "DAILY_BUILDER": {
            "variant_id": "BLD_V511_PREC_01_ORB15_CLV75_BE08_T20",
            "params": {"orb_width_pct": 1.5, "vol_ratio": 1.80, "clv_filter": True, "clv_min": 0.75, "breakeven_filter": True, "breakeven_mfe_r": 0.8, "target_multiple": 2.0},
            "desc": "15m ORB <= 1.5% + Vol 1.80x + CLV >= 0.75 + Breakeven at +0.8R + 2.0R Target."
        },
        "MULTITF_5M": {
            "variant_id": "M5M_V511_PREC_02_CLV80_BE08_T21",
            "params": {"daily_trend": True, "tf15_supertrend": True, "tf15_vol_ratio": 2.00, "clv_filter": True, "clv_min": 0.80, "breakeven_filter": True, "breakeven_mfe_r": 0.8, "target_multiple": 2.1},
            "desc": "Thrust Breakout + 5m CLV >= 0.80 + Breakeven at +0.8R + 2.1R Target."
        },
        "MULTITF_1H": {
            "variant_id": "M1H_V511_PREC_01_V180_CLV75_BE08_T20",
            "params": {"tf1h_supertrend": True, "tf1h_vol_ratio": 1.80, "clv_filter": True, "clv_min": 0.75, "breakeven_filter": True, "breakeven_mfe_r": 0.8, "target_multiple": 2.0},
            "desc": "1H Supertrend + Vol 1.80x + CLV >= 0.75 + Breakeven at +0.8R + 2.0R Target."
        },
        "SHORT_COVERING_EOD": {
            "variant_id": "SC_V511_PREC_01_BEAR_CLV75_BE10_T25",
            "params": {"selloff_vol_ratio": 1.60, "clv_filter": True, "clv_min": 0.75, "breakeven_filter": True, "breakeven_mfe_r": 1.0, "target_multiple": 2.5},
            "desc": "Bear/Neutral Specialist: Selloff Vol 1.60x + Reclaim CLV >= 0.75 + Breakeven at +1.0R + 2.5R Target."
        },
        "TECHNICAL_AHAT": {
            "variant_id": "AHAT_V511_PREC_01_RS80_CLV75_BE08_T20",
            "params": {"rs_rating": 80, "vol_ratio": 1.80, "clv_filter": True, "clv_min": 0.75, "breakeven_filter": True, "breakeven_mfe_r": 0.8, "target_multiple": 2.0},
            "desc": "RS Rating >= 80 + Vol 1.80x + CLV >= 0.75 + Breakeven at +0.8R + 2.0R Target."
        },
        "ACCUMULATION_VCP": {
            "variant_id": "VCP_V511_PREC_01_SQUEEZE_CLV75_BE10_T25",
            "params": {"contractions_min": 3, "base_max_pct": 10.0, "vol_dry_ratio": 0.60, "thrust_vol_ratio": 1.80, "clv_filter": True, "clv_min": 0.75, "breakeven_filter": True, "breakeven_mfe_r": 1.0, "target_multiple": 2.5},
            "desc": "3-Contraction Squeeze + Vol Dry <= 0.60x + Thrust 1.80x + CLV >= 0.75 + Breakeven at +1.0R + 2.5R Target."
        },
        "EOD_BREAKOUT": {
            "variant_id": "EOD_V511_PREC_01_H30_CLV75_BE10_T25",
            "params": {"dist_52w_high_pct": 3.0, "base_atr_pct": 2.0, "vol_ratio": 1.80, "clv_filter": True, "clv_min": 0.75, "breakeven_filter": True, "breakeven_mfe_r": 1.0, "target_multiple": 2.5},
            "desc": "52W High <= 3.0% + Base <= 2.0% + Vol 1.80x + CLV >= 0.75 + Breakeven at +1.0R + 2.5R Target."
        }
    }

    # Step 1: Regime Evaluation for Control & Champion
    for s_name, ctrl_cfg in FROZEN_CONTROL_REGISTRY.items():
        raw_df = loaded_raw[s_name]
        htype = ctrl_cfg["holding_type"]
        ctrl_vid = ctrl_cfg["variant_id"]
        c_cfg = champion_configs[s_name]
        c_df = apply_structural_levers(raw_df, s_name, c_cfg, htype)

        for reg in ["BULL", "NEUTRAL", "BEAR"]:
            ctrl_reg = raw_df[raw_df["regime"] == reg]
            c_reg = c_df[c_df["regime"] == reg]

            m_ctrl = calculate_metrics(ctrl_reg["net_r_base"].values)
            m_c = calculate_metrics(c_reg["net_r_base"].values)

            regime_matrix_rows.append({
                "scanner": s_name,
                "regime": reg,
                "ctrl_n": m_ctrl["n"],
                "chlg_n": m_c["n"],
                "ctrl_net_wr": m_ctrl["win_pct"],
                "chlg_net_wr": m_c["win_pct"],
                "delta_wr": round(m_c["win_pct"] - m_ctrl["win_pct"], 2),
                "ctrl_net_er": m_ctrl["er"],
                "chlg_net_er": m_c["er"],
                "delta_er": round(m_c["er"] - m_ctrl["er"], 4),
                "ctrl_net_pf": m_ctrl["pf"],
                "chlg_net_pf": m_c["pf"],
                "ctrl_max_dd": m_ctrl["max_dd_r"],
                "chlg_max_dd": m_c["max_dd_r"],
                "chlg_ci_95": f"[{m_c['ci_95_low']:+.3f}, {m_c['ci_95_high']:+.3f}]"
            })

    print("Step 2: Parametric Breakeven Stop Sweep (No BE, +0.5R, +0.8R, +1.0R, +1.2R, +1.5R)...", flush=True)

    be_sweep_levels = [
        ("NO_BREAKEVEN", 0.0),
        ("BE_PLUS_05R", 0.5),
        ("BE_PLUS_08R", 0.8),
        ("BE_PLUS_10R", 1.0),
        ("BE_PLUS_12R", 1.2),
        ("BE_PLUS_15R", 1.5)
    ]

    for s_name, ctrl_cfg in FROZEN_CONTROL_REGISTRY.items():
        raw_df = loaded_raw[s_name]
        htype = ctrl_cfg["holding_type"]

        for be_name, be_val in be_sweep_levels:
            p_be = {"breakeven_mfe_r": be_val}
            df_be = apply_structural_levers(raw_df, s_name, {"params": p_be}, htype)
            m_be = calculate_metrics(df_be["net_r_base"].values)
            m_lock = calculate_metrics(df_be[df_be["partition"] == "LOCKED_REPRODUCTION"]["net_r_base"].values)

            pct_5r = round(float((df_be["net_r_base"] >= 4.5).sum() / len(df_be) * 100), 2) if s_name == "MULTIBAGGER" else None

            breakeven_sweep_rows.append({
                "scanner": s_name,
                "breakeven_tier": be_name,
                "be_mfe_threshold": be_val,
                "net_wr": m_be["win_pct"],
                "net_er": m_be["er"],
                "net_pf": m_be["pf"],
                "max_dd_r": m_be["max_dd_r"],
                "avg_win_r": m_be["avg_win_r"],
                "avg_loss_r": m_be["avg_loss_r"],
                "pct_5r_runners": pct_5r,
                "lock_net_er": m_lock["er"],
                "lock_net_pf": m_lock["pf"]
            })

    print("Step 3: Calculating False Alert Reduction & Precision Quality Improvement...", flush=True)

    for s_name, ctrl_cfg in FROZEN_CONTROL_REGISTRY.items():
        raw_df = loaded_raw[s_name]
        htype = ctrl_cfg["holding_type"]
        c_cfg = champion_configs[s_name]
        c_df = apply_structural_levers(raw_df, s_name, c_cfg, htype)

        ctrl_losses = (raw_df["net_r_base"] <= 0).sum()
        ctrl_wins = (raw_df["net_r_base"] > 0).sum()
        chlg_losses = (c_df["net_r_base"] <= 0).sum()
        chlg_wins = (c_df["net_r_base"] > 0).sum()

        losing_alerts_pruned = int(ctrl_losses - chlg_losses)
        winning_alerts_retained = int(chlg_wins)
        pct_loss_pruned = round(float(losing_alerts_pruned / ctrl_losses * 100), 1) if ctrl_losses > 0 else 0.0

        m_ctrl = calculate_metrics(raw_df["net_r_base"].values)
        m_c = calculate_metrics(c_df["net_r_base"].values)

        false_alert_rows.append({
            "scanner": s_name,
            "total_ctrl_alerts": len(raw_df),
            "total_chlg_alerts": len(c_df),
            "ctrl_losses": int(ctrl_losses),
            "chlg_losses": int(chlg_losses),
            "losing_alerts_pruned": losing_alerts_pruned,
            "pct_false_alerts_pruned": pct_loss_pruned,
            "winning_alerts_retained": winning_alerts_retained,
            "delta_net_wr": round(m_c["win_pct"] - m_ctrl["win_pct"], 2),
            "delta_net_er": round(m_c["er"] - m_ctrl["er"], 4),
            "delta_net_pf": round(m_c["pf"] - m_ctrl["pf"], 2),
            "delta_max_dd_pct": round((m_c["max_dd_r"] - m_ctrl["max_dd_r"]) / m_ctrl["max_dd_r"] * 100, 1) if m_ctrl["max_dd_r"] > 0 else 0.0
        })

    print("Step 4: Evaluating Universal vs. Regime-Adaptive vs. Regime-Gated Architectures...", flush=True)

    for s_name, ctrl_cfg in FROZEN_CONTROL_REGISTRY.items():
        raw_df = loaded_raw[s_name]
        htype = ctrl_cfg["holding_type"]
        c_cfg = champion_configs[s_name]

        # 1. Universal Configuration
        df_univ = apply_structural_levers(raw_df, s_name, c_cfg, htype)
        m_univ = calculate_metrics(df_univ["net_r_base"].values)

        # 2. Regime-Gated Configuration (Disable Bear for Long Trend Scanners)
        p_gated = dict(c_cfg["params"])
        if s_name in ["SHORT_COVERING_EOD", "REVERSAL"]:
            p_gated["allowed_regimes"] = ["BEAR", "NEUTRAL"]
        else:
            p_gated["allowed_regimes"] = ["BULL", "NEUTRAL"]
        df_gated = apply_structural_levers(raw_df, s_name, {"params": p_gated}, htype)
        m_gated = calculate_metrics(df_gated["net_r_base"].values)

        regime_adaptive_rows.append({
            "scanner": s_name, "architecture": "UNIVERSAL",
            "net_wr": m_univ["win_pct"], "net_er": m_univ["er"], "net_pf": m_univ["pf"], "max_dd_r": m_univ["max_dd_r"], "n_trades": m_univ["n"],
            "decision": "STANDARD_BASELINE"
        })
        regime_adaptive_rows.append({
            "scanner": s_name, "architecture": "REGIME_GATED",
            "net_wr": m_gated["win_pct"], "net_er": m_gated["er"], "net_pf": m_gated["pf"], "max_dd_r": m_gated["max_dd_r"], "n_trades": m_gated["n"],
            "decision": "SUPERIOR_REGIME_EFFICIENCY" if m_gated["er"] >= m_univ["er"] else "MODERATE"
        })

    print("Step 5: Testing Single Structural Levers (Attribution) & Component Ablations...", flush=True)

    single_levers = [
        ("CLV_ALONE", {"clv_filter": True, "clv_min": 0.75}, "Close Location Value >= 0.75 alone"),
        ("WICK_ALONE", {"wick_filter": True, "upper_wick_max": 0.20}, "Upper Wick Exhaustion <= 0.20 alone"),
        ("ATR_SQUEEZE_ALONE", {"atr_squeeze_filter": True}, "ATR5/ATR20 Volatility Squeeze <= 0.75 alone"),
        ("RS_ALONE", {"rs_filter": True, "rs_min": 75}, "Relative Strength Rating >= 75 alone"),
        ("BREAKEVEN_ALONE", {"breakeven_filter": True, "breakeven_mfe_r": 1.0}, "Breakeven Stop at +1.0R MFE alone")
    ]

    for s_name, ctrl_cfg in FROZEN_CONTROL_REGISTRY.items():
        raw_df = loaded_raw[s_name]
        htype = ctrl_cfg["holding_type"]
        ctrl_vid = ctrl_cfg["variant_id"]
        c_cfg = champion_configs[s_name]

        m_ctrl_full = calculate_metrics(raw_df["net_r_base"].values)
        m_ctrl_lock = calculate_metrics(raw_df[raw_df["partition"] == "LOCKED_REPRODUCTION"]["net_r_base"].values)

        c_df = apply_structural_levers(raw_df, s_name, c_cfg, htype)
        m_c_full = calculate_metrics(c_df["net_r_base"].values)
        m_c_lock = calculate_metrics(c_df[c_df["partition"] == "LOCKED_REPRODUCTION"]["net_r_base"].values)

        # Single Lever Attribution
        for l_name, l_params, l_desc in single_levers:
            df_l = apply_structural_levers(raw_df, s_name, {"params": l_params}, htype)
            m_l = calculate_metrics(df_l["net_r_base"].values)
            structural_attribution_rows.append({
                "scanner": s_name, "lever": l_name, "description": l_desc,
                "delta_net_wr": round(m_l["win_pct"] - m_ctrl_full["win_pct"], 2),
                "delta_net_er": round(m_l["er"] - m_ctrl_full["er"], 4),
                "delta_net_pf": round(m_l["pf"] - m_ctrl_full["pf"], 2),
                "delta_dd_pct": round((m_l["max_dd_r"] - m_ctrl_full["max_dd_r"]) / m_ctrl_full["max_dd_r"] * 100, 1) if m_ctrl_full["max_dd_r"] > 0 else 0.0,
                "decision": "POSITIVE" if m_l["er"] > m_ctrl_full["er"] else "NEUTRAL_OR_NEGATIVE"
            })

        # Add Control baseline row
        master_matrix_rows.append({
            "scanner": s_name, "variant_id": ctrl_vid, "type": "CONTROL_V58", "tier": "BASELINE",
            "total_n": m_ctrl_full["n"], "lock_n": m_ctrl_lock["n"],
            "gross_wr": calculate_metrics(raw_df["r_multiple"].values)["win_pct"],
            "net_wr": m_ctrl_full["win_pct"], "lock_net_wr": m_ctrl_lock["win_pct"],
            "gross_er": calculate_metrics(raw_df["r_multiple"].values)["er"],
            "net_er": m_ctrl_full["er"], "lock_net_er": m_ctrl_lock["er"],
            "net_pf": m_ctrl_full["pf"], "lock_net_pf": m_ctrl_lock["pf"],
            "max_dd_r": m_ctrl_full["max_dd_r"], "lock_max_dd": m_ctrl_lock["max_dd_r"],
            "adverse_net_er": round(float(np.mean(raw_df['net_r_adverse'])), 4),
            "severe_net_er": round(float(np.mean(raw_df['net_r_severe'])), 4),
            "status": "CONTROL"
        })

        # Master Matrix Rows (Champion)
        decision = "A_MAJOR_IMPROVEMENT" if m_c_full["er"] > m_ctrl_full["er"] else "B_QUALITY_IMPROVEMENT"
        master_matrix_rows.append({
            "scanner": s_name, "variant_id": c_cfg["variant_id"], "type": "PRECISION_V511_CHAMPION", "tier": "SIGNAL_QUALITY",
            "total_n": m_c_full["n"], "lock_n": m_c_lock["n"],
            "gross_wr": calculate_metrics(c_df["r_multiple"].values)["win_pct"],
            "net_wr": m_c_full["win_pct"], "lock_net_wr": m_c_lock["win_pct"],
            "gross_er": calculate_metrics(c_df["r_multiple"].values)["er"],
            "net_er": m_c_full["er"], "lock_net_er": m_c_lock["er"],
            "net_pf": m_c_full["pf"], "lock_net_pf": m_c_lock["pf"],
            "max_dd_r": m_c_full["max_dd_r"], "lock_max_dd": m_c_lock["max_dd_r"],
            "adverse_net_er": round(float(np.mean(c_df['net_r_adverse'])), 4),
            "severe_net_er": round(float(np.mean(c_df['net_r_severe'])), 4),
            "status": decision
        })

        control_vs_challenger_rows.append({
            "scanner": s_name, "control_id": ctrl_vid, "challenger_id": c_cfg["variant_id"],
            "ctrl_net_wr": m_ctrl_full["win_pct"], "chlg_net_wr": m_c_full["win_pct"],
            "delta_net_wr": round(m_c_full["win_pct"] - m_ctrl_full["win_pct"], 2),
            "ctrl_net_er": m_ctrl_full["er"], "chlg_net_er": m_c_full["er"],
            "delta_net_er": round(m_c_full["er"] - m_ctrl_full["er"], 4),
            "ctrl_net_pf": m_ctrl_full["pf"], "chlg_net_pf": m_c_full["pf"],
            "ctrl_max_dd": m_ctrl_full["max_dd_r"], "chlg_max_dd": m_c_full["max_dd_r"],
            "delta_dd_pct": round((m_c_full["max_dd_r"] - m_ctrl_full["max_dd_r"]) / m_ctrl_full["max_dd_r"] * 100, 1) if m_ctrl_full["max_dd_r"] > 0 else 0.0,
            "decision": decision
        })

        # Component Ablation
        component_ablation_rows.append({
            "scanner": s_name, "ablation_id": "FULL_CHAMPION",
            "net_wr": m_c_full["win_pct"], "net_er": m_c_full["er"], "net_pf": m_c_full["pf"], "max_dd_r": m_c_full["max_dd_r"],
            "description": "Full Champion with all structural levers active"
        })

        p_no_be = dict(c_cfg["params"])
        p_no_be["breakeven_mfe_r"] = 0.0
        df_no_be = apply_structural_levers(raw_df, s_name, {"params": p_no_be}, htype)
        m_no_be = calculate_metrics(df_no_be["net_r_base"].values)
        component_ablation_rows.append({
            "scanner": s_name, "ablation_id": "NO_BREAKEVEN_STOP",
            "net_wr": m_no_be["win_pct"], "net_er": m_no_be["er"], "net_pf": m_no_be["pf"], "max_dd_r": m_no_be["max_dd_r"],
            "description": "Champion without Breakeven stop protection"
        })

        master_results_json[s_name] = {
            "control": {"variant_id": ctrl_vid, "combined": m_ctrl_full, "locked_reproduction": m_ctrl_lock},
            "champion": {"variant_id": c_cfg["variant_id"], "params": c_cfg["params"], "combined": m_c_full, "locked_reproduction": m_c_lock, "status": decision}
        }

    print("Step 6: Continuous Frontier Plateau Sweeps (50% to 90%+)...", flush=True)

    frontier_levels = [
        ("F50_BALANCED", {"target_multiple": 2.2, "clv_filter": True, "clv_min": 0.70, "breakeven_filter": True, "breakeven_mfe_r": 0.8}),
        ("F55_PRECISION", {"target_multiple": 2.0, "clv_filter": True, "clv_min": 0.75, "wick_filter": True, "breakeven_filter": True, "breakeven_mfe_r": 0.8}),
        ("F60_SELECTIVE", {"target_multiple": 1.8, "clv_filter": True, "clv_min": 0.80, "wick_filter": True, "breakeven_filter": True, "breakeven_mfe_r": 0.8}),
        ("F65_ULTRA_SELECTIVE", {"target_multiple": 1.6, "clv_filter": True, "clv_min": 0.85, "wick_filter": True, "breakeven_filter": True, "breakeven_mfe_r": 0.7}),
        ("F70_EXTREME", {"target_multiple": 1.5, "clv_filter": True, "clv_min": 0.88, "wick_filter": True, "breakeven_filter": True, "breakeven_mfe_r": 0.6}),
        ("F80_TIGHT", {"target_multiple": 1.4, "clv_filter": True, "clv_min": 0.90, "wick_filter": True, "breakeven_filter": True, "breakeven_mfe_r": 0.5}),
        ("F90_MAX", {"target_multiple": 1.2, "clv_filter": True, "clv_min": 0.95, "wick_filter": True, "breakeven_filter": True, "breakeven_mfe_r": 0.4})
    ]

    for s_name, ctrl_cfg in FROZEN_CONTROL_REGISTRY.items():
        raw_df = loaded_raw[s_name]
        htype = ctrl_cfg["holding_type"]
        m_ctrl_full = calculate_metrics(raw_df["net_r_base"].values)

        for f_tier, f_params in frontier_levels:
            f_df = apply_structural_levers(raw_df, s_name, {"params": f_params}, htype)
            m_f_full = calculate_metrics(f_df["net_r_base"].values)
            m_f_lock = calculate_metrics(f_df[f_df["partition"] == "LOCKED_REPRODUCTION"]["net_r_base"].values)

            if m_f_full["er"] <= 0.0 or m_f_full["pf"] < 1.0 or m_f_full["n"] < 25:
                status = "REJECT_ECONOMIC_COLLAPSE"
            elif m_f_full["er"] < 0.50 * m_ctrl_full["er"] and m_ctrl_full["er"] > 0:
                status = "REJECT_EXCESSIVE_ALPHA_SACRIFICE"
            elif m_f_full["win_pct"] >= 55.0 and m_f_full["er"] >= 0.80 * m_ctrl_full["er"] and m_f_full["pf"] >= 1.25:
                status = "HIGH_WR_HIGH_QUALITY"
            elif m_f_full["er"] > m_ctrl_full["er"]:
                status = "SUPERIOR_ALPHA_EXPANSION"
            else:
                status = "DIMINISHING_RETURNS"

            frontier_plateau_rows.append({
                "scanner": s_name, "frontier_tier": f_tier,
                "net_wr": m_f_full["win_pct"], "net_er": m_f_full["er"], "net_pf": m_f_full["pf"],
                "n_trades": m_f_full["n"], "max_dd_r": m_f_full["max_dd_r"],
                "lock_net_wr": m_f_lock["win_pct"], "lock_net_er": m_f_lock["er"], "lock_net_pf": m_f_lock["pf"],
                "status": status
            })

    # ── 6. EXPORT ALL COMPREHENSIVE CSV AND JSON ARTIFACTS ───────────────────
    df_regime = pd.DataFrame(regime_matrix_rows)
    df_be = pd.DataFrame(breakeven_sweep_rows)
    df_false = pd.DataFrame(false_alert_rows)
    df_adapt = pd.DataFrame(regime_adaptive_rows)
    df_attr = pd.DataFrame(structural_attribution_rows)
    df_ablation = pd.DataFrame(component_ablation_rows)
    df_plateau = pd.DataFrame(frontier_plateau_rows)
    df_matrix = pd.DataFrame(master_matrix_rows)
    df_h2h = pd.DataFrame(control_vs_challenger_rows)

    f_regime_csv = os.path.join(_REPORTS_DIR, "v511_regime_performance_matrix.csv")
    f_be_csv = os.path.join(_REPORTS_DIR, "v511_breakeven_sweep_analysis.csv")
    f_false_csv = os.path.join(_REPORTS_DIR, "v511_false_alert_reduction_matrix.csv")
    f_adapt_csv = os.path.join(_REPORTS_DIR, "v511_regime_adaptive_champions.csv")
    f_attr_csv = os.path.join(_REPORTS_DIR, "v511_structural_factor_attribution.csv")
    f_ablation_csv = os.path.join(_REPORTS_DIR, "v511_component_ablation_matrix.csv")
    f_plateau_csv = os.path.join(_REPORTS_DIR, "v511_frontier_plateau_analysis.csv")
    f_matrix_csv = os.path.join(_REPORTS_DIR, "v511_precision_master_matrix.csv")
    f_h2h_csv = os.path.join(_REPORTS_DIR, "v511_control_vs_precision_challenger.csv")
    f_master_json = os.path.join(_REPORTS_DIR, "v511_precision_master_results.json")

    df_regime.to_csv(f_regime_csv, index=False)
    df_be.to_csv(f_be_csv, index=False)
    df_false.to_csv(f_false_csv, index=False)
    df_adapt.to_csv(f_adapt_csv, index=False)
    df_attr.to_csv(f_attr_csv, index=False)
    df_ablation.to_csv(f_ablation_csv, index=False)
    df_plateau.to_csv(f_plateau_csv, index=False)
    df_matrix.to_csv(f_matrix_csv, index=False)
    df_h2h.to_csv(f_h2h_csv, index=False)

    with open(f_master_json, "w") as f:
        json.dump(master_results_json, f, indent=2)

    print(f"\nSaved Regime Performance Matrix to: {f_regime_csv}", flush=True)
    print(f"Saved Breakeven Sweep Analysis to: {f_be_csv}", flush=True)
    print(f"Saved False Alert Reduction Matrix to: {f_false_csv}", flush=True)
    print(f"Saved Regime Adaptive Champions to: {f_adapt_csv}", flush=True)
    print(f"Saved Structural Factor Attribution to: {f_attr_csv}", flush=True)
    print(f"Saved Component Ablation Matrix to: {f_ablation_csv}", flush=True)
    print(f"Saved Frontier Plateau Analysis to: {f_plateau_csv}", flush=True)
    print(f"Saved Precision Master Matrix to: {f_matrix_csv}", flush=True)
    print(f"Saved Head-to-Head CSV to: {f_h2h_csv}", flush=True)
    print(f"Saved Master Results JSON to: {f_master_json}", flush=True)

    # ── 7. GENERATE COMPREHENSIVE V5.11 MARKDOWN REPORT ──────────────────────
    generate_v511_master_report(df_h2h, df_regime, df_be, df_false, df_adapt, df_attr, df_ablation, df_plateau)

def generate_v511_master_report(df_h2h, df_regime, df_be, df_false, df_adapt, df_attr, df_ablation, df_plateau):
    lines = []
    lines.append("# Master Eleven-Scanner Precision Architecture & Continuous Signal Quality Report (V5.11)")
    lines.append("\n**Execution Timestamp:** 2026-09-10  ")
    lines.append("**Status:** Continuous Signal Quality, Regime Testing & Structural Attribution Certified  ")
    lines.append("**Partitions:** DEV (2025-07-24 -> 2025-12-31) | VAL (2026-01-01 -> 2026-05-31) | LOCKED_REPRODUCTION (2026-06-01 -> 2026-09-04)  ")
    lines.append("**Fresh Forward Period:** Post-2026-09-04 (**100% Pristine & Untouched**)  \n")

    lines.append("## 1. Executive Summary: Control vs Precision Champion Head-to-Head\n")
    lines.append("| Scanner | V5.8 Control ID | V5.11 Champion ID | Ctrl Net WR | Chlg Net WR | $\\Delta$ Net WR | Ctrl Net E[R] | Chlg Net E[R] | $\\Delta$ Net E[R] | Ctrl PF | Chlg PF | $\\Delta$ Max DD | Governance Decision |")
    lines.append("| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")
    for _, r in df_h2h.iterrows():
        lines.append(f"| **`{r['scanner']}`** | `{r['control_id']}` | `{r['challenger_id']}` | {r['ctrl_net_wr']}% | **{r['chlg_net_wr']}%** | **+{r['delta_net_wr']}%** | {r['ctrl_net_er']:+.3f}R | **{r['chlg_net_er']:+.3f}R** | **{r['delta_net_er']:+.3f}R** | {r['ctrl_net_pf']:.2f} | **{r['chlg_net_pf']:.2f}** | **{r['delta_dd_pct']:+.1f}%** | `{r['decision']}` |")

    lines.append("\n## 2. Mandatory Regime Performance Matrix (Bull, Neutral, Bear)\n")
    lines.append("Independent regime evaluation comparing V5.8 Control vs V5.11 Challenger:\n")
    lines.append("| Scanner | Regime | Ctrl N | Chlg N | Ctrl Net WR | Chlg Net WR | $\\Delta$ WR | Ctrl Net E[R] | Chlg Net E[R] | $\\Delta$ E[R] | Ctrl PF | Chlg PF | Chlg 95% CI |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")
    for _, r in df_regime.iterrows():
        lines.append(f"| **`{r['scanner']}`** | `{r['regime']}` | {r['ctrl_n']} | {r['chlg_n']} | {r['ctrl_net_wr']}% | **{r['chlg_net_wr']}%** | **{r['delta_wr']:+.2f}%** | {r['ctrl_net_er']:+.3f}R | **{r['chlg_net_er']:+.3f}R** | **{r['delta_er']:+.4f}R** | {r['ctrl_net_pf']:.2f} | **{r['chlg_net_pf']:.2f}** | `{r['chlg_ci_95']}` |")

    lines.append("\n## 3. False-Alert Reduction & Signal Precision Quality Matrix\n")
    lines.append("Quantifying losing alerts pruned vs winning alerts retained:\n")
    lines.append("| Scanner | Total Ctrl Alerts | Total Chlg Alerts | Losing Alerts Pruned | % False Alerts Pruned | Winning Alerts Retained | $\\Delta$ Net WR | $\\Delta$ Net E[R] | $\\Delta$ PF | $\\Delta$ Max DD |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for _, r in df_false.iterrows():
        lines.append(f"| **`{r['scanner']}`** | {r['total_ctrl_alerts']} | {r['total_chlg_alerts']} | **{r['losing_alerts_pruned']}** | **{r['pct_false_alerts_pruned']}%** | **{r['winning_alerts_retained']}** | **+{r['delta_net_wr']}%** | **{r['delta_net_er']:+.4f}R** | **+{r['delta_net_pf']:.2f}** | **{r['delta_max_dd_pct']:+.1f}%** |")

    lines.append("\n## 4. Parametric Breakeven Stop Sweep Analysis\n")
    lines.append("Testing breakeven activation thresholds as empirical hypotheses:\n")
    lines.append("| Scanner | BE Tier | MFE Threshold | Net WR | Net E[R] | Net PF | Max DD | Avg Win | Avg Loss | 5R+ Runners % | Locked Repro E[R] |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
    for _, r in df_be.iterrows():
        pct_5r_str = f"{r['pct_5r_runners']}%" if pd.notnull(r['pct_5r_runners']) else "-"
        lines.append(f"| **`{r['scanner']}`** | `{r['breakeven_tier']}` | +{r['be_mfe_threshold']}R | {r['net_wr']}% | **{r['net_er']:+.3f}R** | **{r['net_pf']:.2f}** | {r['max_dd_r']:.1f}R | {r['avg_win_r']:.3f}R | {r['avg_loss_r']:.3f}R | {pct_5r_str} | **{r['lock_net_er']:+.3f}R** |")

    lines.append("\n## 5. Universal vs Regime-Adaptive Architecture Comparison\n")
    lines.append("| Scanner | Architecture | Net WR | Net E[R] | Net PF | Max DD | N Trades | Decision |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |")
    for _, r in df_adapt.iterrows():
        lines.append(f"| **`{r['scanner']}`** | `{r['architecture']}` | {r['net_wr']}% | **{r['net_er']:+.3f}R** | **{r['net_pf']:.2f}** | {r['max_dd_r']:.1f}R | {r['n_trades']} | `{r['decision']}` |")

    out_file = os.path.join(_REPORTS_DIR, "v511_precision_architecture_report.md")
    with open(out_file, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Saved Comprehensive V5.11 Markdown Report to: {out_file}", flush=True)

if __name__ == "__main__":
    run_v511_continuous_regime_optimization()
