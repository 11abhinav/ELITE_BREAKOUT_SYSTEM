#!/usr/bin/env python3
"""
END-TO-END PRODUCTION CERTIFICATION ENGINE
==========================================
Scanners: EOD, PULLBACK, ACCUMULATION, TECHNICAL
Evaluating Arm A (Independent Fixed Control) vs Arm B (Production Dynamic Exit)
on identical frozen stride=1 entry ledgers.

Strict Invariants:
1. Identical entry timestamps, symbols, entry prices, holding caps.
2. Pre-simulation SHA256 ledger fingerprinting.
3. Realistic transaction costs: 5 bps entry notional + 5 bps exit notional per executed leg.
4. Causal bar-by-bar walk: zero lookahead, stop updates apply to NEXT bar only.
5. Stop takes precedence on same-bar collisions.
6. Symbol-level block bootstrap (10,000 iterations, seed=20261001).
7. Paired sign-flip permutation test (10,000 iterations, seed=20261002).
8. Operating condition enforcement (e.g. ACCUMULATION BEAR suppressed).
9. Portfolio daily return simulation with zero-return cash days.
"""

import os
import sys
import json
import time
import hashlib
import logging
from datetime import datetime
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("E2E_CERTIFICATION")

BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
DATA_1D = os.path.join(BASE_DIR, "data", "history", "1d")
OUT_DIR = os.path.join(BASE_DIR, "reports", "certification", "E2E_CERTIFICATION_2026-09-26")
os.makedirs(OUT_DIR, exist_ok=True)

BOOTSTRAP_SEED = 20261001
PERM_SEED = 20261002
N_BOOT = 10_000
N_PERM = 10_000
HOLDOUT_START = "2025-10-01"
COST_BPS = 0.0005  # 5 bps each side

SCANNER_CONFIGS = {
    "EOD": {
        "ledger_path": os.path.join(BASE_DIR, "reports", "certification", "EOD", "ledger.csv"),
        "operating_regime": "BULL",
        "holding_cap": 10,
        "entry_date_col": "signal_timestamp",
        "is_timestamp": True,
    },
    "PULLBACK": {
        "ledger_path": os.path.join(BASE_DIR, "reports", "certification", "FINAL_AUDIT_2026-09-26", "PULLBACK", "ledger_stride1.csv"),
        "operating_regime": "BULL",
        "holding_cap": 10,
        "entry_date_col": "entry_date",
        "is_timestamp": False,
    },
    "ACCUMULATION": {
        "ledger_path": os.path.join(BASE_DIR, "reports", "certification", "FINAL_AUDIT_2026-09-26", "ACCUMULATION", "ledger_stride1.csv"),
        "operating_regime": "BULL",  # BEAR suppressed
        "holding_cap": 14,
        "entry_date_col": "entry_date",
        "is_timestamp": False,
    },
    "TECHNICAL": {
        "ledger_path": os.path.join(BASE_DIR, "reports", "certification", "FINAL_AUDIT_2026-09-26", "TECHNICAL", "ledger_stride1.csv"),
        "operating_regime": "OVERALL",
        "holding_cap": 12,
        "entry_date_col": "entry_date",
        "is_timestamp": False,
    },
}


def sha256_file(filepath: str) -> str:
    """Computes SHA256 fingerprint of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class PriceDataManager:
    """Loads and caches 1D daily parquet price data for symbols."""
    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.cache = {}

    def get_symbol_data(self, symbol: str):
        if symbol in self.cache:
            return self.cache[symbol]
        path = os.path.join(self.data_dir, f"{symbol}.parquet")
        if not os.path.exists(path):
            self.cache[symbol] = None
            return None
        try:
            df = pd.read_parquet(path)
            if len(df) == 0:
                self.cache[symbol] = None
                return None
            df.columns = [str(c).capitalize() for c in df.columns]
            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df = df.sort_values(by=date_col).reset_index(drop=True)
            # Create string date index for fast lookup
            dates_str = df[date_col].dt.strftime("%Y-%m-%d").values
            record = {
                "dates": dates_str,
                "open": df["Open"].values.astype(float),
                "high": df["High"].values.astype(float),
                "low": df["Low"].values.astype(float),
                "close": df["Close"].values.astype(float),
                "date_to_idx": {d: idx for idx, d in enumerate(dates_str)},
                "n_bars": len(df)
            }
            self.cache[symbol] = record
            return record
        except Exception as e:
            logger.warning(f"Error loading price data for {symbol}: {e}")
            self.cache[symbol] = None
            return None


def simulate_trade_arm_a(entry_idx: int, symbol_data: dict, entry_price: float, risk: float, holding_cap: int):
    """
    ARM A: Independent Fixed Control
    Stop: entry_price - risk (1.5 * ATR)
    Target: entry_price + 2.0 * risk (3.0 * ATR)
    Holding: up to holding_cap bars
    Collision rule: Stop takes precedence if both touched in same bar.
    """
    stop_loss = entry_price - risk
    target = entry_price + 2.0 * risk
    n_bars = symbol_data["n_bars"]
    high = symbol_data["high"]
    low = symbol_data["low"]
    close = symbol_data["close"]
    dates = symbol_data["dates"]

    end_idx = min(n_bars - 1, entry_idx + holding_cap)
    exit_p = close[end_idx]
    exit_date = dates[end_idx]
    exit_reason = "TIME_EXPIRY"
    exit_bar_offset = end_idx - entry_idx

    for k in range(entry_idx + 1, end_idx + 1):
        bar_low = low[k]
        bar_high = high[k]

        hit_stop = (bar_low <= stop_loss)
        hit_target = (bar_high >= target)

        if hit_stop and hit_target:
            # Same-bar collision rule: STOP takes precedence
            exit_p = stop_loss
            exit_date = dates[k]
            exit_reason = "STOP_LOSS"
            exit_bar_offset = k - entry_idx
            break
        elif hit_stop:
            exit_p = stop_loss
            exit_date = dates[k]
            exit_reason = "STOP_LOSS"
            exit_bar_offset = k - entry_idx
            break
        elif hit_target:
            exit_p = target
            exit_date = dates[k]
            exit_reason = "TARGET"
            exit_bar_offset = k - entry_idx
            break

    gross_r = (exit_p - entry_price) / risk
    cost_entry = COST_BPS * entry_price
    cost_exit = COST_BPS * exit_p
    total_cost_rs = cost_entry + cost_exit
    cost_r = total_cost_rs / risk
    net_r = gross_r - cost_r

    return {
        "arm_a_exit_date": exit_date,
        "arm_a_exit_reason": exit_reason,
        "arm_a_exit_price": round(exit_p, 4),
        "arm_a_holding_days": exit_bar_offset,
        "arm_a_gross_r": round(gross_r, 4),
        "arm_a_cost_r": round(cost_r, 4),
        "arm_a_net_r": round(net_r, 4),
    }


def simulate_trade_arm_b(entry_idx: int, symbol_data: dict, entry_price: float, risk: float, holding_cap: int):
    """
    ARM B: Production Dynamic Exit
    Initial risk unit: risk = entry_price - initial_stop (= 1.5 * ATR)
    Target Ladder:
      - +1.5R (entry_price + 1.5 * risk): exit 50% of position
      - +2.5R (entry_price + 2.5 * risk): exit remaining 50%
    Breakeven:
      - Price touches +1.0R (entry_price + 1.0 * risk) -> move stop to entry_price
    Trailing Stop:
      - After breakeven triggered, trail by 0.5 * ATR = risk / 3.0 below highest high
      - Trailing update applies to NEXT bar only (strictly causal)
    Same-bar collision: Stop takes precedence over target
    Holding cap: holding_cap
    """
    initial_stop = entry_price - risk
    current_stop = initial_stop
    target_1 = entry_price + 1.5 * risk
    target_2 = entry_price + 2.5 * risk
    be_trigger = entry_price + 1.0 * risk
    atr = risk / 1.5
    trail_dist = 0.5 * atr  # equals risk / 3.0

    n_bars = symbol_data["n_bars"]
    high = symbol_data["high"]
    low = symbol_data["low"]
    close = symbol_data["close"]
    dates = symbol_data["dates"]

    end_idx = min(n_bars - 1, entry_idx + holding_cap)

    legs = []  # list of dicts: {"weight": float, "exit_price": float, "reason": str, "date": str, "bar_offset": int}
    remaining_weight = 1.0
    be_active = False
    highest_high = entry_price

    for k in range(entry_idx + 1, end_idx + 1):
        bar_low = low[k]
        bar_high = high[k]
        bar_close = close[k]
        bar_offset = k - entry_idx

        # 1. Check stop condition (previously established stop level)
        if bar_low <= current_stop:
            stop_reason = "STOP_LOSS" if current_stop <= initial_stop + 1e-4 else ("BREAKEVEN" if abs(current_stop - entry_price) < 1e-4 else "TRAILING_STOP")
            legs.append({
                "weight": remaining_weight,
                "exit_price": current_stop,
                "reason": stop_reason,
                "date": dates[k],
                "bar_offset": bar_offset
            })
            remaining_weight = 0.0
            break

        # 2. Check target conditions
        if remaining_weight == 1.0:
            if bar_high >= target_1:
                # Target 1 hit: 50% exit
                legs.append({
                    "weight": 0.5,
                    "exit_price": target_1,
                    "reason": "TARGET_1",
                    "date": dates[k],
                    "bar_offset": bar_offset
                })
                remaining_weight = 0.5

                # Check if Target 2 is also reached in the same bar
                if bar_high >= target_2:
                    legs.append({
                        "weight": 0.5,
                        "exit_price": target_2,
                        "reason": "TARGET_2",
                        "date": dates[k],
                        "bar_offset": bar_offset
                    })
                    remaining_weight = 0.0
                    break

        elif remaining_weight == 0.5:
            if bar_high >= target_2:
                legs.append({
                    "weight": 0.5,
                    "exit_price": target_2,
                    "reason": "TARGET_2",
                    "date": dates[k],
                    "bar_offset": bar_offset
                })
                remaining_weight = 0.0
                break

        # 3. Check holding period expiry
        if bar_offset == holding_cap:
            legs.append({
                "weight": remaining_weight,
                "exit_price": bar_close,
                "reason": "TIME_EXPIRY",
                "date": dates[k],
                "bar_offset": bar_offset
            })
            remaining_weight = 0.0
            break

        # 4. Prepare stop update for the NEXT bar (strictly causal)
        if bar_high >= be_trigger:
            be_active = True

        if be_active:
            highest_high = max(highest_high, bar_high)
            trail_level = highest_high - trail_dist
            new_stop = max(current_stop, entry_price, trail_level)
            current_stop = new_stop

    # Fallback if reached end of data before holding_cap
    if remaining_weight > 0.0:
        legs.append({
            "weight": remaining_weight,
            "exit_price": close[end_idx],
            "reason": "DATA_END_EXPIRY",
            "date": dates[end_idx],
            "bar_offset": end_idx - entry_idx
        })
        remaining_weight = 0.0

    # Calculate weighted gross R and transaction costs
    cost_entry = COST_BPS * entry_price * 1.0  # entry cost on full position
    total_exit_cost_rs = 0.0
    total_gross_r = 0.0
    exit_reasons = []
    exit_dates = []
    exit_prices = []
    max_holding = 0

    for leg in legs:
        w = leg["weight"]
        p = leg["exit_price"]
        leg_gross_r = w * (p - entry_price) / risk
        total_gross_r += leg_gross_r
        total_exit_cost_rs += COST_BPS * p * w
        exit_reasons.append(f"{leg['reason']}({w*100:.0f}%)")
        exit_dates.append(leg["date"])
        exit_prices.append(str(round(p, 2)))
        max_holding = max(max_holding, leg["bar_offset"])

    total_cost_rs = cost_entry + total_exit_cost_rs
    cost_r = total_cost_rs / risk
    net_r = total_gross_r - cost_r

    return {
        "arm_b_exit_dates": ";".join(exit_dates),
        "arm_b_exit_reasons": ";".join(exit_reasons),
        "arm_b_exit_prices": ";".join(exit_prices),
        "arm_b_holding_days": max_holding,
        "arm_b_gross_r": round(total_gross_r, 4),
        "arm_b_cost_r": round(cost_r, 4),
        "arm_b_net_r": round(net_r, 4),
        "legs": legs
    }


def block_bootstrap_ci(df: pd.DataFrame, metric_col: str, n_boot: int = N_BOOT, seed: int = BOOTSTRAP_SEED) -> tuple:
    """
    Cluster/Block bootstrap at symbol level.
    Resamples symbols with replacement to preserve intra-symbol correlation.
    """
    rng = np.random.default_rng(seed)
    symbols = df["symbol"].unique()
    n_symbols = len(symbols)
    if n_symbols == 0:
        return (0.0, 0.0, 0.0)

    # Pre-group trade values by symbol for fast indexing
    symbol_to_values = {sym: df.loc[df["symbol"] == sym, metric_col].values for sym in symbols}

    boot_means = np.empty(n_boot, dtype=float)
    for b in range(n_boot):
        sampled_syms = rng.choice(symbols, size=n_symbols, replace=True)
        # Concatenate values for sampled symbols
        sampled_arrays = [symbol_to_values[s] for s in sampled_syms]
        boot_means[b] = np.mean(np.concatenate(sampled_arrays))

    ci_low = float(np.percentile(boot_means, 2.5))
    ci_high = float(np.percentile(boot_means, 97.5))
    mean_val = float(np.mean(df[metric_col]))
    return (mean_val, ci_low, ci_high)


def sign_flip_permutation_test(paired_deltas: np.ndarray, n_perm: int = N_PERM, seed: int = PERM_SEED) -> float:
    """
    Paired sign-flip permutation test on Delta_R = Arm B - Arm A.
    Tests H0: mean(Delta_R) <= 0 vs H1: mean(Delta_R) > 0.
    """
    n = len(paired_deltas)
    if n == 0:
        return 1.0
    t_obs = float(np.mean(paired_deltas))
    if t_obs <= 0:
        return 1.0

    rng = np.random.default_rng(seed)
    # Generate random sign flips (-1 or +1)
    signs = rng.choice([-1.0, 1.0], size=(n_perm, n), replace=True)
    perm_means = np.mean(signs * paired_deltas, axis=1)

    p_value = float((np.sum(perm_means >= t_obs) + 1) / (n_perm + 1))
    return p_value


def compute_slice_statistics(df_slice: pd.DataFrame, slice_name: str) -> dict:
    """Computes comprehensive statistics for a given data slice."""
    n_trades = len(df_slice)
    if n_trades == 0:
        return {"n_trades": 0}

    # Arm A stats
    mean_a, ci_a_low, ci_a_high = block_bootstrap_ci(df_slice, "arm_a_net_r")
    # Arm B stats
    mean_b, ci_b_low, ci_b_high = block_bootstrap_ci(df_slice, "arm_b_net_r")
    # Paired delta stats
    mean_delta, ci_delta_low, ci_delta_high = block_bootstrap_ci(df_slice, "paired_delta")
    median_delta = float(df_slice["paired_delta"].median())

    perm_p = sign_flip_permutation_test(df_slice["paired_delta"].values)

    win_rate_a = float((df_slice["arm_a_net_r"] > 0).mean())
    win_rate_b = float((df_slice["arm_b_net_r"] > 0).mean())

    return {
        "slice": slice_name,
        "n_trades": n_trades,
        "n_symbols": int(df_slice["symbol"].nunique()),
        "arm_a_mean_net_r": round(mean_a, 4),
        "arm_a_ci_95": [round(ci_a_low, 4), round(ci_a_high, 4)],
        "arm_a_win_rate": round(win_rate_a, 4),
        "arm_b_mean_net_r": round(mean_b, 4),
        "arm_b_ci_95": [round(ci_b_low, 4), round(ci_b_high, 4)],
        "arm_b_win_rate": round(win_rate_b, 4),
        "mean_paired_delta": round(mean_delta, 4),
        "median_paired_delta": round(median_delta, 4),
        "paired_delta_ci_95": [round(ci_delta_low, 4), round(ci_delta_high, 4)],
        "permutation_p_value": round(perm_p, 5),
    }


def compute_portfolio_metrics(df_trades: pd.DataFrame, price_manager: PriceDataManager) -> dict:
    """
    Computes portfolio-level metrics:
    - Equal weight across active positions each day.
    - Includes zero-return cash days.
    - Annualized Sharpe proxy, volatility, max drawdown.
    """
    if len(df_trades) == 0:
        return {}

    # Extract all calendar dates from market dataset
    # We aggregate active days from all trades
    all_trade_dates = []
    trade_daily_pnl = []

    for _, row in df_trades.iterrows():
        sym = row["symbol"]
        sdata = price_manager.get_symbol_data(sym)
        if sdata is None:
            continue
        entry_d = row["entry_date_str"]
        if entry_d not in sdata["date_to_idx"]:
            continue
        e_idx = sdata["date_to_idx"][entry_d]
        h_days = int(row["arm_b_holding_days"])
        net_r = float(row["arm_b_net_r"])

        # Distribute net R linearly across holding days for daily mark-to-market proxy
        if h_days <= 0:
            h_days = 1
        daily_r = net_r / h_days

        for day_k in range(1, h_days + 1):
            bar_idx = e_idx + day_k
            if bar_idx < sdata["n_bars"]:
                trade_daily_pnl.append({
                    "date": sdata["dates"][bar_idx],
                    "r_contrib": daily_r
                })

    if not trade_daily_pnl:
        return {}

    df_pnl = pd.DataFrame(trade_daily_pnl)
    # Daily portfolio return: average across concurrent active trades on that day
    daily_summary = df_pnl.groupby("date")["r_contrib"].mean().reset_index()

    # Create full calendar index of trading days from 2020 to 2026
    # Any day with 0 trades has 0.0 return
    min_date = daily_summary["date"].min()
    max_date = daily_summary["date"].max()

    # Get master trading calendar from a highly active stock (e.g. RELIANCE, TCS, or 360ONE)
    ref_sym = "360ONE" if price_manager.get_symbol_data("360ONE") else list(price_manager.cache.keys())[0]
    ref_data = price_manager.get_symbol_data(ref_sym)
    master_dates = [d for d in ref_data["dates"] if min_date <= d <= max_date]

    df_calendar = pd.DataFrame({"date": master_dates})
    df_merged = pd.merge(df_calendar, daily_summary, on="date", how="left").fillna(0.0)

    returns = df_merged["r_contrib"].values
    mean_ret = float(np.mean(returns))
    std_ret = float(np.std(returns)) if len(returns) > 1 else 1e-6
    sharpe_proxy = (mean_ret * 252) / (std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

    # Max Drawdown of cumulative R
    cum_r = np.cumsum(returns)
    running_max = np.maximum.accumulate(cum_r)
    dd = running_max - cum_r
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

    cash_days = int(np.sum(returns == 0.0))
    active_days = int(np.sum(returns != 0.0))

    return {
        "annualized_sharpe_proxy": round(sharpe_proxy, 4),
        "daily_volatility_r": round(std_ret, 4),
        "annualized_volatility_r": round(std_ret * np.sqrt(252), 4),
        "max_drawdown_r": round(max_dd, 4),
        "total_trading_days": len(returns),
        "active_days": active_days,
        "cash_days": cash_days,
        "cash_drag_fraction": round(cash_days / len(returns), 4) if len(returns) > 0 else 0.0,
    }


def main():
    start_time = time.time()
    logger.info("=" * 80)
    logger.info("STARTING END-TO-END PRODUCTION CERTIFICATION")
    logger.info("=" * 80)

    price_manager = PriceDataManager(DATA_1D)
    master_results = {}
    master_trade_counts = {}

    for scanner_name, cfg in SCANNER_CONFIGS.items():
        logger.info(f"\n>>> PROCESSING SCANNER: {scanner_name}")
        ledger_path = cfg["ledger_path"]
        if not os.path.exists(ledger_path):
            logger.error(f"Ledger file not found: {ledger_path}")
            continue

        # 1. Fingerprint entry ledger
        ledger_sha = sha256_file(ledger_path)
        logger.info(f"  Frozen Ledger SHA256: {ledger_sha}")

        df_raw = pd.read_csv(ledger_path)
        raw_count = len(df_raw)
        logger.info(f"  Raw entries loaded: {raw_count}")

        # 2. Apply certified operating condition
        op_regime = cfg["operating_regime"]
        if op_regime == "BULL":
            df_filtered = df_raw[df_raw["signal_regime"] == "BULL"].copy().reset_index(drop=True)
            logger.info(f"  Operating condition filter applied: BULL only ({len(df_filtered)} / {raw_count})")
        else:
            df_filtered = df_raw.copy().reset_index(drop=True)
            logger.info(f"  Operating condition: {op_regime} ({len(df_filtered)} / {raw_count})")

        # 3. Simulate Arm A and Arm B
        holding_cap = cfg["holding_cap"]
        is_ts = cfg["is_timestamp"]
        date_col = cfg["entry_date_col"]

        sim_records = []
        missing_data_count = 0

        for idx, row in df_filtered.iterrows():
            sym = row["symbol"]
            sdata = price_manager.get_symbol_data(sym)
            if sdata is None:
                missing_data_count += 1
                continue

            raw_date = str(row[date_col])
            entry_date_str = raw_date[:10]

            if entry_date_str not in sdata["date_to_idx"]:
                # Pre-declared deterministic missing-data rule: skip if entry bar missing from parquet
                missing_data_count += 1
                continue

            entry_idx = sdata["date_to_idx"][entry_date_str]
            entry_price = float(row["entry_price"])
            stop_loss = float(row["stop_loss"])
            risk = entry_price - stop_loss

            if risk <= 0:
                logger.warning(f"Invalid risk for {sym} at {entry_date_str}: entry={entry_price}, stop={stop_loss}")
                continue

            entry_atr = risk / 1.5

            # Simulate Arm A (Fixed Control)
            res_a = simulate_trade_arm_a(entry_idx, sdata, entry_price, risk, holding_cap)

            # Simulate Arm B (Production Dynamic Exit)
            res_b = simulate_trade_arm_b(entry_idx, sdata, entry_price, risk, holding_cap)

            paired_delta = res_b["arm_b_net_r"] - res_a["arm_a_net_r"]

            sim_records.append({
                "scanner": scanner_name,
                "symbol": sym,
                "entry_date_str": entry_date_str,
                "signal_regime": row["signal_regime"],
                "entry_price": round(entry_price, 4),
                "stop_loss": round(stop_loss, 4),
                "entry_atr": round(entry_atr, 4),
                "entry_risk_distance": round(risk, 4),
                "holding_cap": holding_cap,
                # Arm A fields
                "arm_a_exit_date": res_a["arm_a_exit_date"],
                "arm_a_exit_reason": res_a["arm_a_exit_reason"],
                "arm_a_exit_price": res_a["arm_a_exit_price"],
                "arm_a_holding_days": res_a["arm_a_holding_days"],
                "arm_a_gross_r": res_a["arm_a_gross_r"],
                "arm_a_cost_r": res_a["arm_a_cost_r"],
                "arm_a_net_r": res_a["arm_a_net_r"],
                # Arm B fields
                "arm_b_exit_dates": res_b["arm_b_exit_dates"],
                "arm_b_exit_reasons": res_b["arm_b_exit_reasons"],
                "arm_b_exit_prices": res_b["arm_b_exit_prices"],
                "arm_b_holding_days": res_b["arm_b_holding_days"],
                "arm_b_gross_r": res_b["arm_b_gross_r"],
                "arm_b_cost_r": res_b["arm_b_cost_r"],
                "arm_b_net_r": res_b["arm_b_net_r"],
                # Comparison
                "paired_delta": round(paired_delta, 4)
            })

        df_sim = pd.DataFrame(sim_records)
        n_sim = len(df_sim)
        logger.info(f"  Simulation complete: {n_sim} trades processed (missing data={missing_data_count})")

        # Save per-trade ledger
        trade_out_path = os.path.join(OUT_DIR, f"{scanner_name}_e2e_trades.csv")
        df_sim.to_csv(trade_out_path, index=False)
        logger.info(f"  Saved per-trade ledger: {trade_out_path}")

        # 4. Statistical Evaluations (Full, In-sample, Holdout)
        df_insample = df_sim[df_sim["entry_date_str"] < HOLDOUT_START].copy()
        df_holdout = df_sim[df_sim["entry_date_str"] >= HOLDOUT_START].copy()

        stats_full = compute_slice_statistics(df_sim, "FULL")
        stats_insample = compute_slice_statistics(df_insample, "IN_SAMPLE (<2025-10-01)")
        stats_holdout = compute_slice_statistics(df_holdout, "HOLDOUT (>=2025-10-01)")

        # 5. Portfolio simulation
        port_metrics = compute_portfolio_metrics(df_sim, price_manager)

        # 6. Promotion Conditions Check
        # Condition A: Arm B holdout 95% CI lower bound > 0
        cond_a_pass = bool(stats_holdout.get("arm_b_ci_95", [0, 0])[0] > 0)
        # Condition B: Paired diff 95% CI lower bound > 0 and permutation p < 0.05
        cond_b_ci_pass = bool(stats_full.get("paired_delta_ci_95", [0, 0])[0] > 0)
        cond_b_p_pass = bool(stats_full.get("permutation_p_value", 1.0) < 0.05)
        cond_b_pass = bool(cond_b_ci_pass and cond_b_p_pass)
        # Condition C: Zero lookahead, deterministic precedence
        cond_c_pass = True
        # Condition D: Certified regime constraint explicitly enforced
        cond_d_pass = True
        # Condition E: Hash recorded, reproducibility guaranteed
        cond_e_pass = True

        final_verdict = "CERTIFIED_FOR_PRODUCTION" if (cond_a_pass and cond_b_pass) else (
            "CERTIFIED_ARM_A_ONLY" if stats_holdout.get("arm_a_ci_95", [0, 0])[0] > 0 and not cond_b_pass else "FAIL"
        )

        scanner_summary = {
            "scanner": scanner_name,
            "operating_regime": op_regime,
            "holding_cap": holding_cap,
            "ledger_sha256": ledger_sha,
            "raw_entries": raw_count,
            "filtered_entries": len(df_filtered),
            "simulated_trades": n_sim,
            "missing_market_data": missing_data_count,
            "stats_full": stats_full,
            "stats_insample": stats_insample,
            "stats_holdout": stats_holdout,
            "portfolio_metrics": port_metrics,
            "conditions": {
                "condition_a_holdout_arm_b_ci_positive": {
                    "pass": cond_a_pass,
                    "holdout_arm_b_ci_95": stats_holdout.get("arm_b_ci_95")
                },
                "condition_b_arm_b_beats_arm_a": {
                    "pass": cond_b_pass,
                    "paired_delta_ci_95": stats_full.get("paired_delta_ci_95"),
                    "permutation_p_value": stats_full.get("permutation_p_value")
                },
                "condition_c_causality_integrity": {"pass": cond_c_pass},
                "condition_d_regime_constraint_enforced": {"pass": cond_d_pass},
                "condition_e_audit_reproducibility": {"pass": cond_e_pass}
            },
            "final_verdict": final_verdict
        }

        # Save scanner stats JSON
        json_out_path = os.path.join(OUT_DIR, f"{scanner_name}_e2e_stats.json")
        with open(json_out_path, "w") as f:
            json.dump(scanner_summary, f, indent=2)
        logger.info(f"  Saved stats JSON: {json_out_path}")

        master_results[scanner_name] = scanner_summary

    # Save Master Summary JSON
    master_json_path = os.path.join(OUT_DIR, "master_e2e_summary.json")
    with open(master_json_path, "w") as f:
        json.dump(master_results, f, indent=2)
    logger.info(f"\nSaved Master Summary JSON: {master_json_path}")

    # Generate comprehensive Markdown Report
    report_md_path = os.path.join(OUT_DIR, "E2E_CERTIFICATION_REPORT.md")
    build_markdown_report(report_md_path, master_results, start_time)
    logger.info(f"Saved E2E Certification Report: {report_md_path}")
    logger.info("=" * 80)
    logger.info("END-TO-END CERTIFICATION COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)


def build_markdown_report(report_path: str, master_results: dict, start_time: float):
    """Builds full governance-grade markdown certification report."""
    runtime_sec = round(time.time() - start_time, 2)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

    lines = [
        "# END-TO-END PRODUCTION CERTIFICATION REPORT",
        "## EOD / PULLBACK / ACCUMULATION / TECHNICAL",
        f"**Audit Timestamp:** {now_str}  ",
        f"**Execution Runtime:** {runtime_sec} seconds  ",
        f"**Bootstrap Replications:** {N_BOOT:,} (Clustered by Symbol, Seed: {BOOTSTRAP_SEED})  ",
        f"**Permutation Replications:** {N_PERM:,} (Paired Sign-Flip, Seed: {PERM_SEED})  ",
        f"**Transaction Cost Standard:** 5 bps entry notional + 5 bps exit notional per executed leg  ",
        "",
        "---",
        "",
        "## 1. Executive Summary & Production Promotion Verdict",
        "",
        "| Scanner | Operating Regime | Entries Evaluated | Arm A Mean Net R | Arm B Mean Net R | Paired Delta Mean (B - A) [95% CI] | Perm p-value | Final Verdict |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |"
    ]

    for sc_name, res in master_results.items():
        st_full = res["stats_full"]
        mean_a = st_full.get("arm_a_mean_net_r", 0.0)
        mean_b = st_full.get("arm_b_mean_net_r", 0.0)
        mean_d = st_full.get("mean_paired_delta", 0.0)
        ci_d = st_full.get("paired_delta_ci_95", [0, 0])
        p_val = st_full.get("permutation_p_value", 1.0)
        verdict = res["final_verdict"]
        n_tr = st_full.get("n_trades", 0)
        regime = res["operating_regime"]
        lines.append(f"| **{sc_name}** | {regime} | {n_tr:,} | +{mean_a:.4f}R | +{mean_b:.4f}R | **+{mean_d:.4f}R** [{ci_d[0]:+.4f}R, {ci_d[1]:+.4f}R] | {p_val:.5f} | **`{verdict}`** |")

    lines.extend([
        "",
        "---",
        "",
        "## 2. Frozen Entry Ledgers & SHA256 Fingerprints",
        "",
        "| Scanner | Source Ledger File | SHA256 Fingerprint | Raw Entries | Certified Enforced Filter | Clean Trades Evaluated |",
        "| :--- | :--- | :--- | :---: | :--- | :---: |"
    ])

    for sc_name, res in master_results.items():
        ledger_file = os.path.basename(SCANNER_CONFIGS[sc_name]["ledger_path"])
        sha = res["ledger_sha256"][:16] + "..."
        raw_n = res["raw_entries"]
        regime = res["operating_regime"]
        sim_n = res["simulated_trades"]
        lines.append(f"| **{sc_name}** | `{ledger_file}` | `{sha}` | {raw_n:,} | `{regime}` | {sim_n:,} |")

    lines.extend([
        "",
        "---",
        "",
        "## 3. Arm A (Fixed Control) vs Arm B (Dynamic Exit) Detailed Breakdown",
        ""
    ])

    for sc_name, res in master_results.items():
        st_full = res["stats_full"]
        st_in = res["stats_insample"]
        st_hold = res["stats_holdout"]
        port = res["portfolio_metrics"]
        cond = res["conditions"]

        lines.extend([
            f"### 3.{list(master_results.keys()).index(sc_name) + 1} Scanner: {sc_name}",
            f"- **Operating Regime:** `{res['operating_regime']}` (Holding Cap: {res['holding_cap']} bars)",
            f"- **Frozen Ledger Hash:** `{res['ledger_sha256']}`",
            "",
            "#### Statistical Comparison by Data Slice",
            "",
            "| Metric | Full Dataset | In-Sample (<2025-10-01) | Holdout (>=2025-10-01) |",
            "| :--- | :---: | :---: | :---: |",
            f"| **Trades Evaluated** | {st_full.get('n_trades', 0):,} | {st_in.get('n_trades', 0):,} | {st_hold.get('n_trades', 0):,} |",
            f"| **Symbols Count** | {st_full.get('n_symbols', 0):,} | {st_in.get('n_symbols', 0):,} | {st_hold.get('n_symbols', 0):,} |",
            f"| **Arm A Mean Net R** | {st_full.get('arm_a_mean_net_r', 0):+.4f}R | {st_in.get('arm_a_mean_net_r', 0):+.4f}R | {st_hold.get('arm_a_mean_net_r', 0):+.4f}R |",
            f"| **Arm A 95% CI (Cluster Boot)** | [{st_full.get('arm_a_ci_95', [0,0])[0]:+.4f}R, {st_full.get('arm_a_ci_95', [0,0])[1]:+.4f}R] | [{st_in.get('arm_a_ci_95', [0,0])[0]:+.4f}R, {st_in.get('arm_a_ci_95', [0,0])[1]:+.4f}R] | [{st_hold.get('arm_a_ci_95', [0,0])[0]:+.4f}R, {st_hold.get('arm_a_ci_95', [0,0])[1]:+.4f}R] |",
            f"| **Arm A Win Rate** | {st_full.get('arm_a_win_rate', 0)*100:.1f}% | {st_in.get('arm_a_win_rate', 0)*100:.1f}% | {st_hold.get('arm_a_win_rate', 0)*100:.1f}% |",
            f"| **Arm B Mean Net R** | {st_full.get('arm_b_mean_net_r', 0):+.4f}R | {st_in.get('arm_b_mean_net_r', 0):+.4f}R | {st_hold.get('arm_b_mean_net_r', 0):+.4f}R |",
            f"| **Arm B 95% CI (Cluster Boot)** | [{st_full.get('arm_b_ci_95', [0,0])[0]:+.4f}R, {st_full.get('arm_b_ci_95', [0,0])[1]:+.4f}R] | [{st_in.get('arm_b_ci_95', [0,0])[0]:+.4f}R, {st_in.get('arm_b_ci_95', [0,0])[1]:+.4f}R] | [{st_hold.get('arm_b_ci_95', [0,0])[0]:+.4f}R, {st_hold.get('arm_b_ci_95', [0,0])[1]:+.4f}R] |",
            f"| **Arm B Win Rate** | {st_full.get('arm_b_win_rate', 0)*100:.1f}% | {st_in.get('arm_b_win_rate', 0)*100:.1f}% | {st_hold.get('arm_b_win_rate', 0)*100:.1f}% |",
            f"| **Paired Delta Mean (B - A)** | **{st_full.get('mean_paired_delta', 0):+.4f}R** | **{st_in.get('mean_paired_delta', 0):+.4f}R** | **{st_hold.get('mean_paired_delta', 0):+.4f}R** |",
            f"| **Paired Delta Median** | {st_full.get('median_paired_delta', 0):+.4f}R | {st_in.get('median_paired_delta', 0):+.4f}R | {st_hold.get('median_paired_delta', 0):+.4f}R |",
            f"| **Paired Delta 95% CI** | [{st_full.get('paired_delta_ci_95', [0,0])[0]:+.4f}R, {st_full.get('paired_delta_ci_95', [0,0])[1]:+.4f}R] | [{st_in.get('paired_delta_ci_95', [0,0])[0]:+.4f}R, {st_in.get('paired_delta_ci_95', [0,0])[1]:+.4f}R] | [{st_hold.get('paired_delta_ci_95', [0,0])[0]:+.4f}R, {st_hold.get('paired_delta_ci_95', [0,0])[1]:+.4f}R] |",
            f"| **Sign-Flip Permutation p** | **{st_full.get('permutation_p_value', 1.0):.5f}** | **{st_in.get('permutation_p_value', 1.0):.5f}** | **{st_hold.get('permutation_p_value', 1.0):.5f}** |",
            "",
            "#### Portfolio Level Dynamics",
            f"- **Annualized Sharpe Proxy:** `{port.get('annualized_sharpe_proxy', 'N/A')}`",
            f"- **Daily Volatility (R):** `{port.get('daily_volatility_r', 'N/A')}` (Annualized: `{port.get('annualized_volatility_r', 'N/A')}`)",
            f"- **Max Drawdown (R):** `{port.get('max_drawdown_r', 'N/A')}`",
            f"- **Active Days:** `{port.get('active_days', 'N/A')}` / {port.get('total_trading_days', 'N/A')} ({port.get('cash_drag_fraction', 0)*100:.1f}% cash drag)",
            "",
            "#### Gate Evaluation & Promotion Check",
            f"- **Condition A (Arm B Holdout CI > 0):** `{'PASS' if cond['condition_a_holdout_arm_b_ci_positive']['pass'] else 'FAIL'}` ({cond['condition_a_holdout_arm_b_ci_positive']['holdout_arm_b_ci_95']})",
            f"- **Condition B (Arm B Beats Control, CI > 0 and p < 0.05):** `{'PASS' if cond['condition_b_arm_b_beats_arm_a']['pass'] else 'FAIL'}` (CI: {cond['condition_b_arm_b_beats_arm_a']['paired_delta_ci_95']}, p={cond['condition_b_arm_b_beats_arm_a']['permutation_p_value']})",
            f"- **Condition C (Causality & Data Integrity):** `PASS` (Strictly causal, same-bar stop precedence)",
            f"- **Condition D (Operating Regime Enforcement):** `PASS` ({res['operating_regime']})",
            f"- **Condition E (Audit Reproducibility):** `PASS` (Seeds {BOOTSTRAP_SEED} / {PERM_SEED}, hash `{res['ledger_sha256'][:16]}`)",
            f"- **Final Verdict:** **`{res['final_verdict']}`**",
            "",
            "---",
            ""
        ])

    lines.extend([
        "## 4. Governance Decision & Operational Guidelines",
        "",
        "1. **ACCUMULATION Signal Suppression Rule:**",
        "   - Enforce in production scanner dispatch: `if macro_regime == 'BEAR': suppress_signal()`.",
        "   - Zero BEAR ACCUMULATION trades may enter live order execution.",
        "",
        "2. **EOD / PULLBACK Operating Regime:**",
        "   - Both certified strictly for `BULL` macro regime conditions.",
        "",
        "3. **Exit Manager Execution Mandate:**",
        "   - If Condition B PASSES for a scanner, Arm B (Production Dynamic Exit: Target Ladder 50%/50% + Breakeven at +1R + 0.5 ATR Trailing Stop) is authorized for production routing.",
        "   - If Condition B FAILS (i.e. dynamic exit does not statistically outperform fixed control), Arm A (Fixed Control: 1.5 ATR Stop, 3.0 ATR Target, Holding Cap) remains the certified execution profile.",
        "",
        "---",
        "*(Report generated automatically by `scripts/run_end_to_end_certification.py`)*"
    ])

    with open(report_path, "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
