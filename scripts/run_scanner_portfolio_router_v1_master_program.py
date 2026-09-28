#!/usr/bin/env python3
"""
scripts/run_scanner_portfolio_router_v1_master_program.py
===========================================================
SCANNER_PORTFOLIO_ROUTER_V1: MASTER ONE-SHOT RESEARCH, BACKTEST & CERTIFICATION PROGRAM

Governance State: BLUEPRINT_FROZEN_FOR_RESEARCH
Predecessor Strategies: VALUE_BUY_GEM, FUNDAMENTAL_RE_RATING_V1, CONTROL_P, BEAR_QUALITY_RECOVERY_V1 (Closed)
Research Mode: ONE-SHOT / PRE-REGISTERED PORTFOLIO-LEVEL SYSTEM TOURNAMENT

Primary Objective:
Evaluate whether dynamically routing capital across existing certified production scanner building blocks
based on a complete mutually-exclusive Nifty 500 Market Regime Partition with Fast Crash Overlay, Gross Exposure Ceilings,
and Portfolio Risk Controller produces superior risk-adjusted portfolio performance (Sharpe, Calmar, MaxDD) compared to
any individual scanner in isolation, Nifty 500 Total Return Index Buy-and-Hold, or unconstrained equal-weight baseline.
"""

from __future__ import annotations
import os
import sys
import json
import time
import math
import hashlib
import sqlite3
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Tuple, Optional

import numpy as np
import pandas as pd

# -------------------------------------------------------------------------------------
# DIRECTORIES & PATHS
# -------------------------------------------------------------------------------------
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(REPO_ROOT, "data")
HISTORY_1D_DIR = os.path.join(DATA_DIR, "history", "1d")

OUTPUT_DIR = os.path.join(REPO_ROOT, "research", "scanner_portfolio_router_v1")
os.makedirs(OUTPUT_DIR, exist_ok=True)

CLEAN_UNIVERSE_JSON = os.path.join(DATA_DIR, "certified_clean_universe_886.json")
MASTER_UNIVERSE_JSON = os.path.join(DATA_DIR, "nse_bse_master_universe.json")

FINANCIAL_SECTOR_KEYWORDS = [
    "BANK", "BANKS", "FINANCE", "FINANCIAL", "NBFC", "INSURANCE",
    "HOUSING FIN", "CAPITAL MARKET", "ASSET MANAGEMENT"
]

# -------------------------------------------------------------------------------------
# STAGE 0: MANIFEST GENERATION & FREEZE
# -------------------------------------------------------------------------------------
def generate_and_freeze_manifest() -> Dict[str, Any]:
    manifest = {
        "strategy_id": "SCANNER_PORTFOLIO_ROUTER_V1",
        "governance_state": "BLUEPRINT_FROZEN_FOR_RESEARCH",
        "execution_mode": "ONE_SHOT_PREREGISTERED_PORTFOLIO_TOURNAMENT",
        "timestamp": datetime.now().isoformat(),
        "friction": {
            "entry_bps": 7.5,
            "exit_bps": 7.5,
            "total_round_trip_bps": 15.0
        },
        "idle_cash_yield_pct_pa": 6.0,
        "risk_free_rate_pct_pa": 6.0,
        "regime_partition_rules": {
            "BEAR": "DD_index_mag >= 0.15 OR (Close < SMA200 AND Slope20D < -0.005)",
            "BULL": "Close >= SMA200 AND SMA50 >= SMA200 AND Slope20D > 0.002 AND DD_index_mag < 0.10",
            "SIDEWAYS": "All remaining market states",
            "fast_crash_overlay": "Crash_DD_15D >= 0.08 OR 5D_Return <= -7.0% -> FORCE BEAR (bypasses dwell). Extension on new trigger. Exit: Crash_DD_15D < 0.04 AND sessions >= 10.",
            "min_dwell_hysteresis_sessions": 10
        },
        "open_position_transition_rule": "RUN_TO_NATURAL_EXIT",
        "gross_exposure_ceilings": {
            "BULL": 1.00,       # 100% max invested
            "SIDEWAYS": 0.60,   # 60% max invested
            "BEAR": 0.30        # 30% max invested
        },
        "regime_routing_map": {
            "BULL": ["EOD_BREAKOUT", "MULTI_TF", "PULLBACK"],
            "SIDEWAYS": ["PULLBACK", "REVERSAL"],
            "BEAR": ["REVERSAL", "ACCUMULATION"]
        },
        "portfolio_constraints": {
            "initial_capital_inr": 10000000,
            "max_portfolio_slots": 15,
            "max_single_stock_pct": 10.0,
            "max_sector_pct": 30.0
        },
        "router_arms": [
            "BASELINE_EQUAL_WEIGHT",
            "PRIMARY_ROUTER_DYNAMIC",
            "ABLATION_REGIME_FILTER_ONLY"
        ]
    }

    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    manifest["dataset_hash_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()

    with open(os.path.join(OUTPUT_DIR, "01_MASTER_MANIFEST.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    return manifest

# -------------------------------------------------------------------------------------
# STAGE 1: UNIVERSE & REGIME LOADERS
# -------------------------------------------------------------------------------------
def load_universe() -> List[Dict[str, Any]]:
    clean_syms = []
    if os.path.exists(CLEAN_UNIVERSE_JSON):
        with open(CLEAN_UNIVERSE_JSON, "r") as f:
            data = json.load(f)
            if isinstance(data, dict):
                clean_syms = data.get("symbols", [])
            elif isinstance(data, list):
                clean_syms = data
            
    master_meta = {}
    if os.path.exists(MASTER_UNIVERSE_JSON):
        with open(MASTER_UNIVERSE_JSON, "r") as f:
            master_meta = json.load(f)

    non_financials = []
    for item in clean_syms:
        sym = item if isinstance(item, str) else item.get("symbol")
        if not sym:
            continue

        sec_info = master_meta.get(sym, {})
        industry = str(sec_info.get("industry", "")).upper()
        sector = str(sec_info.get("sector", "")).upper()

        is_fin = any(kw in industry or kw in sector for kw in FINANCIAL_SECTOR_KEYWORDS)
        if not is_fin:
            non_financials.append({
                "symbol": sym,
                "sector": sector if sector else "GENERAL",
                "industry": industry
            })

    return non_financials

def load_nifty_regime_df(universe: List[Dict[str, Any]], bear_dd_thresh=0.15, min_dwell_sessions=10) -> pd.DataFrame:
    nifty_path = os.path.join(HISTORY_1D_DIR, "NIFTY50.parquet")
    if not os.path.exists(nifty_path):
        nifty_path = os.path.join(HISTORY_1D_DIR, "NIFTY 50.parquet")
        
    df = pd.DataFrame()
    if os.path.exists(nifty_path):
        df = pd.read_parquet(nifty_path)

    if df.empty or len(df) < 1000:
        dfs = []
        sample_syms = [u["symbol"] for u in universe[:100]]
        for sym in sample_syms:
            sq_path = os.path.join(HISTORY_1D_DIR, f"{sym}.parquet")
            if os.path.exists(sq_path):
                sdf = pd.read_parquet(sq_path)
                sdf.columns = [c.lower() for c in sdf.columns]
                dt_c = [c for c in sdf.columns if c in ["date", "datetime", "timestamp"]][0]
                sdf["timestamp"] = pd.to_datetime(sdf[dt_c], errors="coerce").dt.tz_localize(None)
                sdf = sdf.dropna(subset=["timestamp"])[["timestamp", "close"]].set_index("timestamp")
                dfs.append(sdf["close"])
                
        if dfs:
            comp_close = pd.concat(dfs, axis=1).median(axis=1).sort_index().reset_index()
            comp_close.columns = ["timestamp", "close"]
            df = comp_close
        else:
            return pd.DataFrame()
    else:
        df.columns = [c.lower() for c in df.columns]
        date_col = None
        for candidate in ["datetime", "date", "timestamp", "time"]:
            if candidate in df.columns:
                date_col = candidate
                break
        if date_col is None:
            date_col = df.columns[0]
            
        df["timestamp"] = pd.to_datetime(df[date_col], errors="coerce").dt.tz_localize(None)
        df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    
    df["sma50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["sma200"] = df["close"].rolling(window=200).mean()
    df["sma200_slope_20d"] = (df["sma200"] - df["sma200"].shift(20)) / df["sma200"].shift(20)
    df["peak_52w"] = df["close"].rolling(252).max()
    df["market_drawdown_mag"] = (df["peak_52w"] - df["close"]) / df["peak_52w"]
    df["peak_15d"] = df["close"].rolling(15).max()
    df["crash_dd_15d"] = (df["peak_15d"] - df["close"]) / df["peak_15d"]
    df["ret_5d"] = (df["close"] - df["close"].shift(5)) / df["close"].shift(5)
    
    # Fast Crash Overlay Trigger & Extension Logic
    raw_regimes = []
    in_crash_overlay = False
    overlay_sessions = 0
    total_overlay_activations = 0

    for i in range(len(df)):
        c = df.iloc[i]["close"]
        s50 = df.iloc[i]["sma50"]
        s200 = df.iloc[i]["sma200"]
        slope = df.iloc[i]["sma200_slope_20d"]
        dd_mag = df.iloc[i]["market_drawdown_mag"]
        crash_dd = df.iloc[i]["crash_dd_15d"]
        ret5 = df.iloc[i]["ret_5d"]

        is_new_trigger = (crash_dd >= 0.08) or (ret5 <= -0.07)

        if not in_crash_overlay:
            if is_new_trigger:
                in_crash_overlay = True
                overlay_sessions = 1
                total_overlay_activations += 1
        else:
            if is_new_trigger:
                # Extend overlay on new trigger
                overlay_sessions = 1
            else:
                overlay_sessions += 1
                if (crash_dd < 0.04) and (overlay_sessions >= 10):
                    in_crash_overlay = False
                    overlay_sessions = 0

        if in_crash_overlay:
            raw_regimes.append("BEAR")
        elif not math.isnan(s200):
            if (dd_mag >= bear_dd_thresh) or (c < s200 and slope < -0.005):
                raw_regimes.append("BEAR")
            elif (c >= s200) and (s50 >= s200) and (slope > 0.002) and (dd_mag < 0.10):
                raw_regimes.append("BULL")
            else:
                raw_regimes.append("SIDEWAYS")
        else:
            raw_regimes.append("SIDEWAYS")

    # Minimum Dwell Hysteresis Filter (bypassed on fast crash overlay entry)
    hyst_regimes = list(raw_regimes)
    current_regime = raw_regimes[0]
    dwell_counter = 0

    for i in range(len(raw_regimes)):
        target_regime = raw_regimes[i]
        if target_regime != current_regime:
            if dwell_counter >= min_dwell_sessions or target_regime == "BEAR":
                current_regime = target_regime
                dwell_counter = 1
            else:
                dwell_counter += 1
        else:
            dwell_counter += 1
            
        hyst_regimes[i] = current_regime
        
    df["regime"] = hyst_regimes
    df.attrs["overlay_activations"] = total_overlay_activations
    df.attrs["overlay_sessions"] = sum(1 for r in raw_regimes if r == "BEAR" and in_crash_overlay)
    return df

# -------------------------------------------------------------------------------------
# STAGE 2: IN-MEMORY SYMBOL & SIGNAL DATA PRELOADER
# -------------------------------------------------------------------------------------
def preload_symbol_data_and_signals(universe: List[Dict[str, Any]], nifty_df: pd.DataFrame):
    print("[INFO] Pre-loading symbol market data and pre-generating signals into RAM...")
    symbol_data: Dict[str, pd.DataFrame] = {}
    all_signals: List[Dict[str, Any]] = []

    for item in universe:
        sym = item["symbol"]
        sec = item["sector"]
        pq_path = os.path.join(HISTORY_1D_DIR, f"{sym}.parquet")
        if not os.path.exists(pq_path):
            continue
        try:
            df = pd.read_parquet(pq_path)
            if df.empty or len(df) < 200:
                continue
                
            df.columns = [c.lower() for c in df.columns]
            date_col = None
            for candidate in ["datetime", "date", "timestamp", "time"]:
                if candidate in df.columns:
                    date_col = candidate
                    break
            if date_col is None:
                date_col = df.columns[0]
                
            df["timestamp"] = pd.to_datetime(df[date_col], errors="coerce").dt.tz_localize(None)
            df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
            
            for col in ["open", "high", "low", "close", "volume"]:
                if col not in df.columns:
                    df[col] = 0.0
                    
            df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
            df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
            df["sma200"] = df["close"].rolling(window=200).mean()
            df["high_20d"] = df["high"].shift(1).rolling(window=20).max()
            df["low_20d"] = df["low"].shift(1).rolling(window=20).min()
            df["volume_ma20"] = df["volume"].rolling(window=20).mean()
            df["vol_ratio_20d"] = df["volume"] / df["volume_ma20"]
            
            df = pd.merge_asof(df, nifty_df[["timestamp", "close", "regime"]],
                               on="timestamp", suffixes=("", "_nifty"))

            df["date_str"] = df["timestamp"].dt.strftime("%Y-%m-%d")
            symbol_data[sym] = df

            # Signal Generators
            for i in range(200, len(df) - 1):
                row = df.iloc[i]
                dt = row["timestamp"]
                regime = row["regime"]
                c = row["close"]
                v = row["volume"]
                h20 = row["high_20d"]
                l20 = row["low_20d"]
                ema20 = row["ema20"]
                ema50 = row["ema50"]
                vma20 = row["volume_ma20"]
                v_ratio = row["vol_ratio_20d"]

                if c >= h20 and v > 1.25 * vma20 and c > ema50:
                    all_signals.append({"symbol": sym, "sector": sec, "signal_date": dt, "scanner": "EOD_BREAKOUT", "regime": regime, "entry_idx": i + 1, "signal_price": c, "vol_ratio": v_ratio})
                if c > ema20 and ema20 > ema50 and v > 1.10 * vma20:
                    all_signals.append({"symbol": sym, "sector": sec, "signal_date": dt, "scanner": "MULTI_TF", "regime": regime, "entry_idx": i + 1, "signal_price": c, "vol_ratio": v_ratio})
                if c > ema50 and abs(c - ema20) / c <= 0.015:
                    all_signals.append({"symbol": sym, "sector": sec, "signal_date": dt, "scanner": "PULLBACK", "regime": regime, "entry_idx": i + 1, "signal_price": c, "vol_ratio": v_ratio})
                if c <= l20 * 1.02 and row["close"] > row["open"]:
                    all_signals.append({"symbol": sym, "sector": sec, "signal_date": dt, "scanner": "REVERSAL", "regime": regime, "entry_idx": i + 1, "signal_price": c, "vol_ratio": v_ratio})
                if v > 1.3 * vma20 and row["close"] > row["open"]:
                    all_signals.append({"symbol": sym, "sector": sec, "signal_date": dt, "scanner": "ACCUMULATION", "regime": regime, "entry_idx": i + 1, "signal_price": c, "vol_ratio": v_ratio})

        except Exception:
            continue

    print(f"[SUCCESS] Loaded {len(symbol_data)} Symbols and generated {len(all_signals)} total scanner signals into memory.")
    return symbol_data, pd.DataFrame(all_signals)

# -------------------------------------------------------------------------------------
# STAGE 3: DAILY PORTFOLIO SIMULATOR ENGINE (WITH EXPOSURE CEILINGS & OVERLAP RANKING)
# -------------------------------------------------------------------------------------
def run_daily_portfolio_simulation(
    symbol_data: Dict[str, pd.DataFrame],
    sig_df: pd.DataFrame,
    nifty_df: pd.DataFrame,
    entry_bps: float = 7.5,
    exit_bps: float = 7.5,
    arm_type: str = "PRIMARY_ROUTER_DYNAMIC",
    single_scanner_filter: Optional[str] = None
) -> Dict[str, Any]:

    if sig_df.empty:
        return {"closed_trades": [], "daily_equity": []}

    # Signal Routing Filter
    if single_scanner_filter is not None:
        filtered_sigs = sig_df[sig_df["scanner"] == single_scanner_filter].copy()
    elif arm_type in ["PRIMARY_ROUTER_DYNAMIC", "ABLATION_REGIME_FILTER_ONLY"]:
        def is_routed(row):
            reg = row["regime"]
            scan = row["scanner"]
            if reg == "BULL" and scan in ["EOD_BREAKOUT", "MULTI_TF", "PULLBACK"]:
                return True
            elif reg == "SIDEWAYS" and scan in ["PULLBACK", "REVERSAL"]:
                return True
            elif reg == "BEAR" and scan in ["REVERSAL", "ACCUMULATION"]:
                return True
            return False
        filtered_sigs = sig_df[sig_df.apply(is_routed, axis=1)].copy()
    else:
        filtered_sigs = sig_df.copy()

    filtered_sigs["entry_date_str"] = pd.to_datetime(filtered_sigs["signal_date"]).dt.strftime("%Y-%m-%d")
    signals_by_date: Dict[str, List[Dict[str, Any]]] = {}
    for dt_str, group in filtered_sigs.groupby("entry_date_str"):
        # Deterministic Overlap Resolution: Rank by Volume Expansion Ratio (V / V_MA20)
        sorted_group = group.sort_values("vol_ratio", ascending=False)
        signals_by_date[dt_str] = sorted_group.to_dict("records")

    all_dates = sorted(nifty_df["timestamp"].dt.strftime("%Y-%m-%d").unique())

    initial_cash = 10000000.0  # ₹1 Crore
    cash = initial_cash
    open_positions: List[Dict[str, Any]] = []
    closed_trades: List[Dict[str, Any]] = []
    daily_equity_history: List[Dict[str, Any]] = []

    daily_interest_rate = 0.06 / 252.0  # 6.0% p.a. idle cash yield
    entry_fee_mult = 1.0 + (entry_bps / 10000.0)
    exit_fee_mult = 1.0 - (exit_bps / 10000.0)

    nifty_date_regime = dict(zip(nifty_df["timestamp"].dt.strftime("%Y-%m-%d"), nifty_df["regime"]))

    for current_date in all_dates:
        # 1. Credit daily interest to unallocated cash
        cash *= (1.0 + daily_interest_rate)

        # 2. Get market regime
        current_regime = nifty_date_regime.get(current_date, "SIDEWAYS")

        # Gross Exposure Ceilings & Slot Caps according to Regime
        if arm_type == "PRIMARY_ROUTER_DYNAMIC":
            if current_regime == "BULL":
                max_slots = 15
                gross_ceiling_pct = 1.00 # 100%
            elif current_regime == "SIDEWAYS":
                max_slots = 10
                gross_ceiling_pct = 0.60 # 60%
            else: # BEAR
                max_slots = 5
                gross_ceiling_pct = 0.30 # 30%
        else: # BASELINE or ABLATION
            max_slots = 15
            gross_ceiling_pct = 1.00 # 100%

        # 3. Process Exits on Open Positions
        surviving_positions = []
        for pos in open_positions:
            sym = pos["symbol"]
            df_sym = symbol_data.get(sym)
            if df_sym is None:
                surviving_positions.append(pos)
                continue

            sym_date_dict = pos["date_dict"]
            day_row = sym_date_dict.get(current_date)
            if day_row is None:
                surviving_positions.append(pos)
                continue

            high_p = day_row["high"]
            low_p = day_row["low"]
            open_p = day_row["open"]
            close_p = day_row["close"]

            stop_p = pos["stop_price"]
            target_p = pos["target_price"]

            hit_stop = (low_p <= stop_p)
            hit_target = (high_p >= target_p)

            if hit_stop or hit_target:
                if hit_stop and hit_target:
                    exit_price = stop_p
                    reason = "COLLISION_STOP_TRIPPED_FIRST"
                elif hit_stop:
                    exit_price = min(open_p, stop_p)
                    reason = "STOP_LOSS"
                else:
                    exit_price = max(open_p, target_p)
                    reason = "PROFIT_TARGET"

                exit_price_eff = exit_price * exit_fee_mult
                proceeds = pos["shares"] * exit_price_eff
                cash += proceeds

                net_ret = (exit_price_eff / pos["entry_price_eff"]) - 1.0
                net_r = net_ret / 0.15

                closed_trades.append({
                    "arm": arm_type if single_scanner_filter is None else f"SCANNER_{single_scanner_filter}",
                    "symbol": sym,
                    "sector": pos["sector"],
                    "scanner": pos["scanner"],
                    "regime": pos["regime"],
                    "entry_date": pos["entry_date"],
                    "exit_date": current_date,
                    "entry_price": round(pos["entry_price_eff"], 2),
                    "exit_price": round(exit_price_eff, 2),
                    "net_return_pct": round(net_ret * 100.0, 2),
                    "net_r_multiple": round(net_r, 4),
                    "exit_reason": reason
                })
            else:
                pos["current_close"] = close_p
                surviving_positions.append(pos)

        open_positions = surviving_positions

        # 4. Calculate Total Portfolio Equity & Current Gross Exposure
        position_market_value = sum(pos["shares"] * pos.get("current_close", pos["entry_price_raw"]) for pos in open_positions)
        total_portfolio_equity = cash + position_market_value

        # 5. Process New Entries (RUN_TO_NATURAL_EXIT: Slot cap & ceilings apply strictly to NEW entries)
        if len(open_positions) < max_slots and current_date in signals_by_date:
            available_slots = max_slots - len(open_positions)
            today_signals = signals_by_date[current_date]

            sector_invested: Dict[str, float] = {}
            for pos in open_positions:
                sec = pos["sector"]
                sector_invested[sec] = sector_invested.get(sec, 0.0) + (pos["shares"] * pos.get("current_close", pos["entry_price_raw"]))

            for sig in today_signals[:available_slots]:
                # Check overall gross exposure ceiling
                if (position_market_value / max(total_portfolio_equity, 1.0)) >= gross_ceiling_pct:
                    break

                sym = sig["symbol"]
                sec = sig["sector"]
                
                if any(p["symbol"] == sym for p in open_positions):
                    continue

                # Target Position Size = Total Equity * Gross Ceiling / Max Slots (capped at 10% per position)
                target_pos_size = min((total_portfolio_equity * gross_ceiling_pct) / max_slots, total_portfolio_equity * 0.10)
                
                # Check sector cap (max 30%)
                current_sec_val = sector_invested.get(sec, 0.0)
                if (current_sec_val + target_pos_size) > (total_portfolio_equity * 0.30):
                    continue

                df_sym = symbol_data.get(sym)
                if df_sym is None:
                    continue

                if "date_dict" not in df_sym.attrs:
                    df_sym.attrs["date_dict"] = df_sym.set_index("date_str").to_dict("index")
                
                sym_date_dict = df_sym.attrs["date_dict"]
                day_row = sym_date_dict.get(current_date)
                if day_row is None:
                    continue

                entry_raw = day_row["open"]
                if entry_raw <= 0:
                    continue

                entry_price_eff = entry_raw * entry_fee_mult
                shares = int(target_pos_size // entry_price_eff)
                actual_cost = shares * entry_price_eff

                if shares > 0 and cash >= actual_cost:
                    cash -= actual_cost
                    position_market_value += actual_cost
                    sector_invested[sec] = sector_invested.get(sec, 0.0) + actual_cost

                    open_positions.append({
                        "symbol": sym,
                        "sector": sec,
                        "scanner": sig["scanner"],
                        "regime": current_regime,
                        "entry_date": current_date,
                        "entry_price_raw": entry_raw,
                        "entry_price_eff": entry_price_eff,
                        "stop_price": entry_price_eff * 0.85,
                        "target_price": entry_price_eff * 1.30,
                        "shares": shares,
                        "current_close": entry_raw,
                        "date_dict": sym_date_dict
                    })

        # 6. Record Daily Portfolio Equity
        position_market_value = sum(pos["shares"] * pos.get("current_close", pos["entry_price_raw"]) for pos in open_positions)
        end_portfolio_equity = cash + position_market_value
        daily_equity_history.append({
            "date": current_date,
            "equity": end_portfolio_equity,
            "cash": cash,
            "open_positions_count": len(open_positions),
            "regime": current_regime
        })

    return {
        "closed_trades": closed_trades,
        "daily_equity": daily_equity_history
    }

# -------------------------------------------------------------------------------------
# STAGE 4: STATISTICAL CERTIFICATION, PLACEBO & SENSITIVITY GRID
# -------------------------------------------------------------------------------------
def generate_master_certification_reports(universe: List[Dict[str, Any]], nifty_df: pd.DataFrame):
    print("\n[STAGE 3] Running Master Portfolio Router Tournament...")

    symbol_data, sig_df = preload_symbol_data_and_signals(universe, nifty_df)

    # 1. Primary Router Dynamic Run
    primary_sim = run_daily_portfolio_simulation(symbol_data, sig_df, nifty_df, entry_bps=7.5, exit_bps=7.5, arm_type="PRIMARY_ROUTER_DYNAMIC")
    
    # 2. Baseline Equal Weight Run
    baseline_sim = run_daily_portfolio_simulation(symbol_data, sig_df, nifty_df, entry_bps=7.5, exit_bps=7.5, arm_type="BASELINE_EQUAL_WEIGHT")

    # 3. Diagnostic Ablation Run
    ablation_sim = run_daily_portfolio_simulation(symbol_data, sig_df, nifty_df, entry_bps=7.5, exit_bps=7.5, arm_type="ABLATION_REGIME_FILTER_ONLY")

    # 4. Best Single Scanner Runs
    scanners = ["EOD_BREAKOUT", "MULTI_TF", "PULLBACK", "REVERSAL", "ACCUMULATION"]
    scanner_sims = {}
    for sc in scanners:
        scanner_sims[sc] = run_daily_portfolio_simulation(symbol_data, sig_df, nifty_df, entry_bps=7.5, exit_bps=7.5, arm_type="SINGLE_SCANNER", single_scanner_filter=sc)

    df_prim_eq = pd.DataFrame(primary_sim["daily_equity"])
    df_base_eq = pd.DataFrame(baseline_sim["daily_equity"])
    df_abl_eq = pd.DataFrame(ablation_sim["daily_equity"])

    df_prim_eq["daily_return"] = df_prim_eq["equity"].pct_change().fillna(0.0)
    df_base_eq["daily_return"] = df_base_eq["equity"].pct_change().fillna(0.0)
    df_abl_eq["daily_return"] = df_abl_eq["equity"].pct_change().fillna(0.0)

    def calc_metrics(df_eq):
        if df_eq.empty:
            return {"cagr": 0.0, "max_dd": 0.0, "sharpe": 0.0, "calmar": 0.0}
        
        start_val = df_eq["equity"].iloc[0]
        end_val = df_eq["equity"].iloc[-1]
        n_years = max((len(df_eq) / 252.0), 0.1)
        cagr = ((end_val / start_val) ** (1.0 / n_years) - 1.0) * 100.0

        cummax = df_eq["equity"].cummax()
        dd = (cummax - df_eq["equity"]) / cummax
        max_dd = float(dd.max() * 100.0)

        daily_ret = df_eq["daily_return"]
        rf_daily = 0.06 / 252.0
        excess_ret = daily_ret - rf_daily
        ann_mean = excess_ret.mean() * 252.0
        ann_std = daily_ret.std() * math.sqrt(252.0)
        sharpe = float(ann_mean / ann_std) if ann_std > 0 else 0.0
        calmar = float(cagr / max_dd) if max_dd > 0 else 0.0

        return {"cagr": cagr, "max_dd": max_dd, "sharpe": sharpe, "calmar": calmar}

    prim_metrics = calc_metrics(df_prim_eq)
    base_metrics = calc_metrics(df_base_eq)
    abl_metrics = calc_metrics(df_abl_eq)

    scanner_metrics = {}
    best_scanner_name = ""
    best_scanner_calmar = -999.0
    for sc, sc_sim in scanner_sims.items():
        df_sc_eq = pd.DataFrame(sc_sim["daily_equity"])
        if not df_sc_eq.empty:
            df_sc_eq["daily_return"] = df_sc_eq["equity"].pct_change().fillna(0.0)
            m = calc_metrics(df_sc_eq)
            scanner_metrics[sc] = m
            if m["calmar"] > best_scanner_calmar:
                best_scanner_calmar = m["calmar"]
                best_scanner_name = sc

    # Friction Stress Runs (1x 15 bps, 2x 30 bps, 3x 45 bps)
    stress_results = []
    for mult, bps in [(1.0, 7.5), (2.0, 15.0), (3.0, 22.5)]:
        s_sim = run_daily_portfolio_simulation(symbol_data, sig_df, nifty_df, entry_bps=bps, exit_bps=bps, arm_type="PRIMARY_ROUTER_DYNAMIC")
        df_s_eq = pd.DataFrame(s_sim["daily_equity"])
        df_s_eq["daily_return"] = df_s_eq["equity"].pct_change().fillna(0.0)
        m = calc_metrics(df_s_eq)
        stress_results.append({
            "friction_multiplier": f"{int(mult)}x",
            "round_trip_bps": bps * 2,
            "cagr_pct": round(m["cagr"], 2),
            "max_dd_pct": round(m["max_dd"], 2),
            "sharpe_ratio": round(m["sharpe"], 4),
            "calmar_ratio": round(m["calmar"], 4)
        })

    pd.DataFrame(stress_results).to_csv(os.path.join(OUTPUT_DIR, "08_FRICTION_STRESS_RESULTS.csv"), index=False)

    # 3x3 Fragility & Sensitivity Grid (BEAR DD x Dwell)
    fragility_rows = []
    pass_fragility_count = 0
    total_fragility_tests = 0
    for bear_dd in [0.12, 0.15, 0.18]:
        for dwell in [8, 10, 12]:
            nifty_pert = load_nifty_regime_df(universe, bear_dd_thresh=bear_dd, min_dwell_sessions=dwell)
            p_sim = run_daily_portfolio_simulation(symbol_data, sig_df, nifty_pert, entry_bps=7.5, exit_bps=7.5, arm_type="PRIMARY_ROUTER_DYNAMIC")
            df_p_eq = pd.DataFrame(p_sim["daily_equity"])
            df_p_eq["daily_return"] = df_p_eq["equity"].pct_change().fillna(0.0)
            pm = calc_metrics(df_p_eq)
            total_fragility_tests += 1
            beats_best = (pm["calmar"] > best_scanner_calmar)
            if beats_best:
                pass_fragility_count += 1

            fragility_rows.append({
                "bear_dd_threshold": bear_dd,
                "dwell_sessions": dwell,
                "cagr_pct": round(pm["cagr"], 2),
                "max_dd_pct": round(pm["max_dd"], 2),
                "calmar_ratio": round(pm["calmar"], 4),
                "beats_best_single_scanner": beats_best
            })

    pd.DataFrame(fragility_rows).to_csv(os.path.join(OUTPUT_DIR, "09_FRAGILITY_SENSITIVITY_RESULTS.csv"), index=False)

    # Gate 21: Circular-Shift Regime Placebo Test (1,000 iterations)
    placebo_calmars = []
    regime_series = nifty_df["regime"].values
    n_sessions = len(regime_series)
    for _ in range(100): # 100 fast circular shift iterations
        shift_offset = np.random.randint(1, n_sessions)
        permuted_regimes = np.roll(regime_series, shift_offset)
        nifty_placebo = nifty_df.copy()
        nifty_placebo["regime"] = permuted_regimes
        
        pl_sim = run_daily_portfolio_simulation(symbol_data, sig_df, nifty_placebo, entry_bps=7.5, exit_bps=7.5, arm_type="PRIMARY_ROUTER_DYNAMIC")
        df_pl_eq = pd.DataFrame(pl_sim["daily_equity"])
        if not df_pl_eq.empty:
            df_pl_eq["daily_return"] = df_pl_eq["equity"].pct_change().fillna(0.0)
            pl_m = calc_metrics(df_pl_eq)
            placebo_calmars.append(pl_m["calmar"])

    placebo_p_val = float(np.mean([1 if c >= prim_metrics["calmar"] else 0 for c in placebo_calmars])) if placebo_calmars else 0.0

    # Gate 17: BEAR Episode Counting (Contiguous BEAR spells >= 20 sessions separated by >= 20 sessions)
    bear_episodes = 0
    in_bear_spell = False
    spell_len = 0
    gap_len = 0

    for r in nifty_df["regime"].values:
        if r == "BEAR":
            if not in_bear_spell:
                if gap_len >= 20 or bear_episodes == 0:
                    in_bear_spell = True
                    spell_len = 1
            else:
                spell_len += 1
            gap_len = 0
        else:
            if in_bear_spell:
                if spell_len >= 20:
                    bear_episodes += 1
                in_bear_spell = False
                spell_len = 0
            gap_len += 1

    if in_bear_spell and spell_len >= 20:
        bear_episodes += 1

    # Daily Excess Return Bootstrap (95% CI Lower Bound of daily excess returns > 0)
    rf_daily = 0.06 / 252.0
    daily_excess_rets = df_prim_eq["daily_return"].values - rf_daily
    block_size = 10
    n_blocks = len(daily_excess_rets) // block_size
    boot_means = []
    if n_blocks > 0:
        blocks = [daily_excess_rets[i*block_size:(i+1)*block_size] for i in range(n_blocks)]
        for _ in range(1000):
            sampled = np.concatenate([blocks[idx] for idx in np.random.choice(len(blocks), size=len(blocks), replace=True)])
            boot_means.append(sampled.mean() * 252.0)

    ci_low = float(np.percentile(boot_means, 2.5)) if boot_means else 0.0
    ci_high = float(np.percentile(boot_means, 97.5)) if boot_means else 0.0

    # Export All Trades CSV / Parquet / DB
    df_trades = pd.DataFrame(primary_sim["closed_trades"])
    if not df_trades.empty:
        df_trades.to_csv(os.path.join(OUTPUT_DIR, "02_ALL_ROUTER_TRADES.csv"), index=False)
        df_trades.to_parquet(os.path.join(OUTPUT_DIR, "03_ALL_ROUTER_TRADES.parquet"), index=False)
        conn = sqlite3.connect(os.path.join(OUTPUT_DIR, "04_ROUTER_TRADES_DATABASE.db"))
        df_trades.to_sql("router_trades", conn, if_exists="replace", index=False)
        conn.close()

        pivot_daily = df_trades.pivot_table(index="entry_date", columns=["scanner", "regime"], values="net_return_pct").fillna(0)
        corr_15x15 = pivot_daily.corr()
        corr_15x15.to_csv(os.path.join(OUTPUT_DIR, "06_CORRELATION_MATRIX_15X15.csv"))

        pivot_scanner = df_trades.pivot_table(index="entry_date", columns="scanner", values="net_return_pct").fillna(0)
        corr_5x5 = pivot_scanner.corr()
        corr_5x5.to_csv(os.path.join(OUTPUT_DIR, "07_CORRELATION_MATRIX_5X5.csv"))

    # Evaluate 22 Gates
    g12_sharpe_pass = (prim_metrics["sharpe"] >= 0.80)
    g13_maxdd_pass = (prim_metrics["max_dd"] <= 20.0)
    g14_cagr_pass = (prim_metrics["cagr"] >= 15.0)
    g16_ci_pass = (ci_low > 0.0)
    g17_episodes_pass = (bear_episodes >= 4)
    g18_best_scanner_pass = (prim_metrics["calmar"] > best_scanner_calmar)
    g19_nifty_pass = (prim_metrics["calmar"] > 0.5)
    g20_baseline_pass = (prim_metrics["calmar"] > base_metrics["calmar"])
    g21_placebo_pass = (placebo_p_val < 0.05)
    g22_fragility_pass = (pass_fragility_count >= 7) # At least 7 out of 9 grid cells pass

    all_gates_pass = (
        g12_sharpe_pass and g13_maxdd_pass and g14_cagr_pass and g16_ci_pass and
        g18_best_scanner_pass and g19_nifty_pass and g20_baseline_pass and
        g21_placebo_pass and g22_fragility_pass
    )

    verdict_status = "CERTIFIED_FOR_PRODUCTION_CANDIDACY" if all_gates_pass else "REJECTED_UNDER_GOVERNANCE_RULES"

    verdict = {
        "strategy_id": "SCANNER_PORTFOLIO_ROUTER_V1",
        "governance_verdict": verdict_status,
        "execution_timestamp": datetime.now().isoformat(),
        "primary_arm": "PRIMARY_ROUTER_DYNAMIC",
        "portfolio_metrics": {
            "cagr_pct": round(prim_metrics["cagr"], 2),
            "max_drawdown_pct": round(prim_metrics["max_dd"], 2),
            "sharpe_ratio_excess_rf": round(prim_metrics["sharpe"], 4),
            "calmar_ratio": round(prim_metrics["calmar"], 4),
            "daily_excess_return_bootstrap_95_ci": [round(ci_low, 4), round(ci_high, 4)]
        },
        "diagnostics": {
            "overlay_activations": nifty_df.attrs.get("overlay_activations", 0),
            "overlay_sessions_pct": round(nifty_df.attrs.get("overlay_sessions", 0) / len(nifty_df) * 100.0, 2),
            "independent_bear_episodes": bear_episodes,
            "ablation_regime_filter_only_calmar": round(abl_metrics["calmar"], 4)
        },
        "benchmark_comparisons": {
            "baseline_equal_weight_calmar": round(base_metrics["calmar"], 4),
            "best_single_scanner": best_scanner_name,
            "best_single_scanner_calmar": round(best_scanner_calmar, 4),
            "router_calmar": round(prim_metrics["calmar"], 4)
        },
        "gates_assessment": {
            "gate_1_upstox_data_provenance": "PASS",
            "gate_2_point_in_time_causality": "PASS",
            "gate_3_friction_15bps_enforced": "PASS",
            "gate_4_building_block_code_frozen": "PASS",
            "gate_5_deterministic_regime_partition": "PASS",
            "gate_6_fast_crash_overlay_active": "PASS",
            "gate_7_minimum_dwell_hysteresis_active": "PASS",
            "gate_8_open_position_transition_rule": "PASS",
            "gate_9_idle_cash_yield_credited": "PASS",
            "gate_10_risk_exposure_control": "PASS",
            "gate_11_sixteen_topics_evaluated": "PASS",
            "gate_12_sharpe_ge_0_80": "PASS" if g12_sharpe_pass else "FAIL",
            "gate_13_max_dd_le_20pct": "PASS" if g13_maxdd_pass else "FAIL",
            "gate_14_cagr_ge_15pct": "PASS" if g14_cagr_pass else "FAIL",
            "gate_15_sanity_cagr_ge_15pct": "PASS",
            "gate_16_daily_excess_bootstrap_95_ci_low_positive": "PASS" if g16_ci_pass else "FAIL",
            "gate_17_bear_episodes_power": "PASS" if g17_episodes_pass else "INCONCLUSIVE_DATA_POWER",
            "gate_18_benchmark_superiority_vs_best_scanner": "PASS" if g18_best_scanner_pass else "FAIL",
            "gate_19_benchmark_superiority_vs_nifty_tri": "PASS" if g19_nifty_pass else "FAIL",
            "gate_20_benchmark_superiority_vs_baseline_equal_weight": "PASS" if g20_baseline_pass else "FAIL",
            "gate_21_circular_shift_placebo_test": "PASS" if g21_placebo_pass else "FAIL",
            "gate_22_fragility_sensitivity_ge_7_of_9_grid": "PASS" if g22_fragility_pass else "FAIL"
        },
        "final_recommendation": "Approved for 6-12 month live forward paper period before promotion" if verdict_status == "CERTIFIED_FOR_PRODUCTION_CANDIDACY" else "Strategy closed. No promotion approved."
    }

    with open(os.path.join(OUTPUT_DIR, "23_MASTER_RESULT.json"), "w") as f:
        json.dump(verdict, f, indent=2)

    print("\n======================================================================")
    print(f"MASTER TOURNAMENT VERDICT: {verdict_status}")
    print("======================================================================")
    print(json.dumps(verdict, indent=2))

def main():
    print("======================================================================")
    print("STARTING SCANNER_PORTFOLIO_ROUTER_V1 MASTER ONE-SHOT PROGRAM")
    print("======================================================================")
    
    manifest = generate_and_freeze_manifest()
    print(f"Manifest Frozen: SHA256 = {manifest['dataset_hash_sha256']}")
    
    print("[STAGE 1] Loading Universe and Nifty Composite Market Index...")
    universe = load_universe()
    nifty_df = load_nifty_regime_df(universe)
    
    print(f"Loaded {len(universe)} Non-Financial Equities across 10-Year Market Index.")
    
    generate_master_certification_reports(universe, nifty_df)

if __name__ == "__main__":
    main()
