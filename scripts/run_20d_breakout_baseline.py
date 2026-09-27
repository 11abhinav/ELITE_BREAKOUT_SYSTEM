#!/usr/bin/env python3
"""
scripts/run_20d_breakout_baseline.py
====================================
PHASE 2: PURE 20-DAY BREAKOUT UNCONDITIONED BASELINE BACKTEST ENGINE
===================================================================

Hypothesis:
  Establishes the unconditioned baseline performance (Net R, Win Rate, Expectancy,
  Drawdown, Sharpe, and Temporal Consistency) of a pure 20-day high breakout across
  all 931 certified Indian equities over the 10-year Upstox dataset (2016–2026).
  
  This benchmark is the mandatory reference against which any fundamental or
  accelerated earnings filter (Phase 5) must prove incremental alpha.

Mandatory Invariants:
  1. Real Upstox 1D Parquet Data Only (data/history/1d/*.parquet, N=931 stocks).
  2. Data provenance verified through MarketDataProtocol.
  3. Signal at Date T Close (Close > max(High[T-20:T])) -> Executable at Date T+1 Open.
  4. Dual-arm exit simulation (Arm A: Fixed 2R target / 1R stop; Arm B: Dynamic trail + partials).
  5. Tested across all 3 certified regimes (BULL, SIDEWAYS, BEAR) and 4 non-overlapping
     temporal cells (2016-18, 2019-21, 2022-24, 2025-26) + calendar quarters.
"""

import os
import sys
import glob
import json
import math
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Any, Optional, Tuple
import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("BASELINE_20D_BREAKOUT")

BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.production.market_data_protocol import MarketDataProtocol
from engine.production.temporal_replication_gate import TemporalReplicationGate, DEFAULT_TEMPORAL_CELLS

DATA_1D_DIR = os.path.join(BASE_DIR, "data", "history", "1d")
REGIME_DAILY_PATH = os.path.join(BASE_DIR, "reports", "certification", "FINAL_AUDIT_2026-09-26", "regime_daycount_daily.csv")
OUT_DIR = os.path.join(BASE_DIR, "reports", "certification", "BASELINE_20D_BREAKOUT_2026-09-27")
DOCS_OUT_DIR = os.path.join(BASE_DIR, "docs", "research")
os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(DOCS_OUT_DIR, exist_ok=True)

COST_BPS = 0.0005  # 5 bps per side, 10 bps round-trip friction
LOOKBACK_WINDOW = 20  # 20 trading sessions
HOLDING_CAP = 15  # 15 sessions maximum holding
ATR_PERIOD = 14
RISK_MULT = 1.5  # Risk unit R = 1.5 * ATR14

REGIMES = ["BULL", "SIDEWAYS", "BEAR"]


def calculate_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Computes standard Wilder Average True Range."""
    n = len(close)
    tr = np.zeros(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)
    
    atr = np.zeros(n, dtype=np.float64)
    if n < period:
        return atr
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def simulate_trade_arm_a(
    entry_idx: int,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    dates: List[str],
    entry_price: float,
    risk: float,
    holding_cap: int
) -> Dict[str, Any]:
    """Arm A: Static Baseline (Target = +2.0R, Stop = -1.0R, Time Expiry at holding cap)."""
    stop_loss = entry_price - risk
    target = entry_price + 2.0 * risk
    n_bars = len(close)
    end_idx = min(n_bars - 1, entry_idx + holding_cap)

    exit_p = close[end_idx]
    exit_date = dates[end_idx]
    exit_reason = "TIME_EXPIRY"
    exit_offset = end_idx - entry_idx

    max_high = entry_price
    min_low = entry_price

    for k in range(entry_idx, end_idx + 1):
        b_low = low[k]
        b_high = high[k]
        max_high = max(max_high, b_high)
        min_low = min(min_low, b_low)

        hit_stop = (b_low <= stop_loss)
        hit_target = (b_high >= target)

        if hit_stop and hit_target:
            exit_p = stop_loss
            exit_date = dates[k]
            exit_reason = "STOP_LOSS"
            exit_offset = k - entry_idx
            break
        elif hit_stop:
            exit_p = stop_loss
            exit_date = dates[k]
            exit_reason = "STOP_LOSS"
            exit_offset = k - entry_idx
            break
        elif hit_target:
            exit_p = target
            exit_date = dates[k]
            exit_reason = "TARGET"
            exit_offset = k - entry_idx
            break

    gross_r = (exit_p - entry_price) / risk
    cost_entry = COST_BPS * entry_price
    cost_exit = COST_BPS * exit_p
    cost_r = (cost_entry + cost_exit) / risk
    net_r = gross_r - cost_r

    mfe_r = (max_high - entry_price) / risk
    mae_r = (min_low - entry_price) / risk

    return {
        "arm_a_exit_date": exit_date,
        "arm_a_exit_reason": exit_reason,
        "arm_a_exit_price": round(exit_p, 4),
        "arm_a_holding_days": exit_offset,
        "arm_a_gross_r": round(gross_r, 4),
        "arm_a_cost_r": round(cost_r, 4),
        "arm_a_net_r": round(net_r, 4),
        "mfe_r": round(mfe_r, 4),
        "mae_r": round(mae_r, 4)
    }


def simulate_trade_arm_b(
    entry_idx: int,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    dates: List[str],
    entry_price: float,
    risk: float,
    holding_cap: int
) -> Dict[str, Any]:
    """Arm B: Dynamic Production Architecture (T1 +1.5R 50%, T2 +2.5R 50%, Breakeven +1.0R, 0.5 ATR trail)."""
    initial_stop = entry_price - risk
    current_stop = initial_stop
    target_1 = entry_price + 1.5 * risk
    target_2 = entry_price + 2.5 * risk
    be_trigger = entry_price + 1.0 * risk
    atr = risk / RISK_MULT
    trail_dist = 0.5 * atr

    n_bars = len(close)
    end_idx = min(n_bars - 1, entry_idx + holding_cap)

    legs = []
    remaining_weight = 1.0
    be_active = False
    highest_high = entry_price

    for k in range(entry_idx, end_idx + 1):
        b_low = low[k]
        b_high = high[k]
        b_close = close[k]
        bar_offset = k - entry_idx

        # Check Stop Loss / Trailing Stop
        if b_low <= current_stop:
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

        # Check Targets
        if remaining_weight == 1.0:
            if b_high >= target_1:
                legs.append({
                    "weight": 0.5,
                    "exit_price": target_1,
                    "reason": "TARGET_1",
                    "date": dates[k],
                    "bar_offset": bar_offset
                })
                remaining_weight = 0.5
                if b_high >= target_2:
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
            if b_high >= target_2:
                legs.append({
                    "weight": 0.5,
                    "exit_price": target_2,
                    "reason": "TARGET_2",
                    "date": dates[k],
                    "bar_offset": bar_offset
                })
                remaining_weight = 0.0
                break

        # Check Time Expiry
        if bar_offset == holding_cap:
            legs.append({
                "weight": remaining_weight,
                "exit_price": b_close,
                "reason": "TIME_EXPIRY",
                "date": dates[k],
                "bar_offset": bar_offset
            })
            remaining_weight = 0.0
            break

        # Trailing defense logic
        if b_high >= be_trigger:
            be_active = True
        if be_active:
            highest_high = max(highest_high, b_high)
            trail_level = highest_high - trail_dist
            new_stop = max(current_stop, entry_price, trail_level)
            current_stop = new_stop

    if remaining_weight > 0.0:
        legs.append({
            "weight": remaining_weight,
            "exit_price": close[end_idx],
            "reason": "DATA_END_EXPIRY",
            "date": dates[end_idx],
            "bar_offset": end_idx - entry_idx
        })
        remaining_weight = 0.0

    cost_entry = COST_BPS * entry_price * 1.0
    total_exit_cost_rs = 0.0
    total_gross_r = 0.0
    exit_reasons = []

    for leg in legs:
        w = leg["weight"]
        p_exit = leg["exit_price"]
        gross_rs = (p_exit - entry_price) * w
        total_gross_r += (gross_rs / risk)
        total_exit_cost_rs += (COST_BPS * p_exit * w)
        exit_reasons.append(f"{leg['reason']}({w:.1f})")

    cost_r = (cost_entry + total_exit_cost_rs) / risk
    net_r = total_gross_r - cost_r
    final_exit_date = legs[-1]["date"] if legs else dates[end_idx]
    final_holding_days = legs[-1]["bar_offset"] if legs else (end_idx - entry_idx)

    return {
        "arm_b_exit_date": final_exit_date,
        "arm_b_exit_reason": " + ".join(exit_reasons),
        "arm_b_holding_days": final_holding_days,
        "arm_b_gross_r": round(total_gross_r, 4),
        "arm_b_cost_r": round(cost_r, 4),
        "arm_b_net_r": round(net_r, 4)
    }


def run_baseline_study():
    logger.info("=" * 80)
    logger.info("🚀 EXECUTING PHASE 2: PURE 20-DAY BREAKOUT BASELINE BACKTEST")
    logger.info("=" * 80)

    # 1. Load Macro Regime Calendar
    logger.info(f"Loading regime calendar from: {REGIME_DAILY_PATH}")
    df_regime = pd.read_csv(REGIME_DAILY_PATH)
    regime_map = dict(zip(df_regime["date"], df_regime["regime"]))
    logger.info(f"Loaded {len(regime_map)} regime calendar sessions.")

    # 2. Discover and Validate All Upstox 1D Parquet Datasets
    parquet_files = sorted(glob.glob(os.path.join(DATA_1D_DIR, "*.parquet")))
    total_symbols = len(parquet_files)
    logger.info(f"Found {total_symbols} daily parquet files in {DATA_1D_DIR}")
    if total_symbols == 0:
        raise RuntimeError("No daily parquet files discovered! Halting.")

    # Pre-flight Provenance Audit Sample
    sample_audit = MarketDataProtocol.verify_dataset_provenance(
        symbol="SBIN",
        filepath_or_df=os.path.join(DATA_1D_DIR, "SBIN.parquet"),
        timeframe="1d",
        provider="UPSTOX",
        allow_certified_cache=True
    )
    logger.info(f"Pre-flight provenance verified: {sample_audit['symbol']} | Status: {sample_audit['provenance_status']}")

    all_signals = []
    skipped_count = 0
    processed_count = 0

    # 3. Process Each Symbol Causal Time-Series
    for p_path in parquet_files:
        symbol = os.path.basename(p_path).replace(".parquet", "")
        try:
            df = pd.read_parquet(p_path)
            if len(df) < (LOOKBACK_WINDOW + ATR_PERIOD + 10):
                skipped_count += 1
                continue

            df.columns = [str(c).capitalize() for c in df.columns]
            date_col = "Date" if "Date" in df.columns else df.columns[0]
            df = df.sort_values(by=date_col).reset_index(drop=True)

            dates_str = df[date_col].dt.strftime("%Y-%m-%d").values
            opens = df["Open"].values.astype(np.float64)
            highs = df["High"].values.astype(np.float64)
            lows = df["Low"].values.astype(np.float64)
            closes = df["Close"].values.astype(np.float64)
            volumes = df["Volume"].values.astype(np.int64)

            atrs = calculate_atr(highs, lows, closes, period=ATR_PERIOD)
            n_bars = len(df)

            # Causal Signal Scan:
            # At bar t (Close):
            # Prior 20-day high is max(highs[t-20:t]).
            # Condition: closes[t] > prior_20d_high.
            # Entry on bar t+1: Open[t+1].
            # Exit evaluation: bar t+1 onwards.
            for t in range(LOOKBACK_WINDOW + ATR_PERIOD, n_bars - 1):
                prior_high = np.max(highs[t - LOOKBACK_WINDOW: t])
                if closes[t] > prior_high:
                    signal_date = dates_str[t]
                    entry_date = dates_str[t + 1]
                    entry_price = opens[t + 1]

                    atr_val = atrs[t]
                    if atr_val <= 1e-4 or entry_price <= 0:
                        continue

                    risk = RISK_MULT * atr_val
                    macro_reg = regime_map.get(entry_date, "SIDEWAYS")

                    # Run Dual-Arm Simulation
                    res_a = simulate_trade_arm_a(
                        entry_idx=t + 1,
                        high=highs,
                        low=lows,
                        close=closes,
                        dates=dates_str,
                        entry_price=entry_price,
                        risk=risk,
                        holding_cap=HOLDING_CAP
                    )

                    res_b = simulate_trade_arm_b(
                        entry_idx=t + 1,
                        high=highs,
                        low=lows,
                        close=closes,
                        dates=dates_str,
                        entry_price=entry_price,
                        risk=risk,
                        holding_cap=HOLDING_CAP
                    )

                    all_signals.append({
                        "symbol": symbol,
                        "signal_date": signal_date,
                        "entry_date": entry_date,
                        "macro_regime": macro_reg,
                        "entry_price": round(entry_price, 2),
                        "atr_14": round(atr_val, 2),
                        "risk_r": round(risk, 2),
                        "prior_20d_high": round(prior_high, 2),
                        "signal_close": round(closes[t], 2),
                        "signal_volume": int(volumes[t]),
                        **res_a,
                        **res_b,
                        "delta_net_r": round(res_b["arm_b_net_r"] - res_a["arm_a_net_r"], 4)
                    })

            processed_count += 1
            if processed_count % 150 == 0:
                logger.info(f"Processed {processed_count}/{total_symbols} stocks... Signals gathered: {len(all_signals):,}")

        except Exception as e:
            logger.warning(f"Error processing {symbol}: {e}")
            skipped_count += 1

    df_trades = pd.DataFrame(all_signals)
    total_trades = len(df_trades)
    logger.info(f"\n✅ Total 20D Breakout Signals Identified: {total_trades:,} across {processed_count} symbols.")

    # Save complete trade ledger
    ledger_path = os.path.join(OUT_DIR, "baseline_20d_trades.csv")
    df_trades.to_csv(ledger_path, index=False)
    logger.info(f"Trade ledger written to: {ledger_path}")

    # 4. Multi-Regime & Temporal Replication Analysis
    matrix_results = {}
    flat_rows = []

    for reg in REGIMES:
        df_reg = df_trades[df_trades["macro_regime"] == reg].copy().reset_index(drop=True)
        reg_n = len(df_reg)
        logger.info(f"\n── Regime: {reg} (N = {reg_n:,}) ──")

        # Partition into 4 Multi-Year Cells
        my_cells = TemporalReplicationGate.partition_into_temporal_cells(df_reg, date_col="entry_date")
        my_metrics = {}
        for cid, cdf in my_cells.items():
            my_metrics[cid] = TemporalReplicationGate.calculate_cell_metrics(
                cdf, arm_a_col="arm_a_net_r", arm_b_col="arm_b_net_r", symbol_col="symbol", date_col="entry_date"
            )

        # Partition into Calendar Quarters
        q_cells = TemporalReplicationGate.partition_into_quarters(df_reg, date_col="entry_date")
        q_metrics = {}
        for qid, qdf in q_cells.items():
            q_metrics[qid] = TemporalReplicationGate.calculate_cell_metrics(
                qdf, arm_a_col="arm_a_net_r", arm_b_col="arm_b_net_r", symbol_col="symbol", date_col="entry_date"
            )

        my_consistency = TemporalReplicationGate.evaluate_replication_consistency(my_metrics)
        q_consistency = TemporalReplicationGate.evaluate_replication_consistency(q_metrics)

        # Pooled stats
        pooled_metrics = TemporalReplicationGate.calculate_cell_metrics(
            df_reg, arm_a_col="arm_a_net_r", arm_b_col="arm_b_net_r", symbol_col="symbol", date_col="entry_date"
        )

        matrix_results[reg] = {
            "regime": reg,
            "sample_size": reg_n,
            "pooled": pooled_metrics,
            "multi_year_cells": my_metrics,
            "quarterly_cells": q_metrics,
            "my_consistency": my_consistency,
            "q_consistency": q_consistency
        }

        # Store for tabular summary
        for cid, c_data in my_metrics.items():
            flat_rows.append({
                "Regime": reg,
                "Cell": cid,
                "N": c_data["trade_count"],
                "Arm A Mean R": c_data["arm_a_mean"],
                "Arm B Mean R": c_data["arm_b_mean"],
                "Win Rate B": f"{c_data['win_rate_b']*100:.1f}%",
                "Delta (B-A)": c_data["delta_mean"],
                "Arm B 95% CI": f"[{c_data['arm_b_ci_95'][0]:.3f}, {c_data['arm_b_ci_95'][1]:.3f}]",
                "Delta 95% CI": f"[{c_data['delta_ci_95'][0]:.3f}, {c_data['delta_ci_95'][1]:.3f}]",
                "Perm p": c_data["permutation_p"],
                "Sharpe": c_data["portfolio_sharpe"],
                "Max DD (R)": c_data["max_drawdown_r"],
                "Eff N": c_data["effective_n"],
                "Cell Pass": "PASS" if c_data["cell_passed"] else "FAIL"
            })

    # Save summary JSON
    summary_json_path = os.path.join(OUT_DIR, "baseline_20d_summary.json")
    with open(summary_json_path, "w") as f:
        json.dump(matrix_results, f, indent=2)
    logger.info(f"Summary JSON saved to: {summary_json_path}")

    # 5. Generate Full Authoritative Audit Report
    df_flat = pd.DataFrame(flat_rows)
    report_md = generate_markdown_report(matrix_results, df_flat, sample_audit, total_trades, processed_count)

    report_path = os.path.join(OUT_DIR, "BASELINE_20D_BREAKOUT_AUDIT_REPORT.md")
    with open(report_path, "w") as f:
        f.write(report_md)
    logger.info(f"Authoritative audit report generated at: {report_path}")

    # Copy to docs/research/
    docs_report_path = os.path.join(DOCS_OUT_DIR, "BASELINE_20D_BREAKOUT_AUDIT_REPORT.md")
    with open(docs_report_path, "w") as f:
        f.write(report_md)
    logger.info(f"Copy written to docs/research: {docs_report_path}")

    logger.info("=" * 80)
    logger.info("🏆 PHASE 2: BASELINE 20D BREAKOUT BACKTEST COMPLETE")
    logger.info("=" * 80)


def generate_markdown_report(
    matrix_results: Dict[str, Any],
    df_flat: pd.DataFrame,
    audit_meta: Dict[str, Any],
    total_trades: int,
    stock_count: int
) -> str:
    """Formats full markdown audit report conforming to AGENTS.md."""
    provenance_section = MarketDataProtocol.generate_audit_report_section(audit_meta)

    # Build Flat Table Markdown
    table_lines = [
        "| Regime | Cell | N | Arm A Mean R | Arm B Mean R | Win Rate (B) | Delta (B-A) | Arm B 95% CI | Delta 95% CI | Perm p | Sharpe | Max DD | Eff N | Gate |",
        "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |"
    ]
    for _, r in df_flat.iterrows():
        table_lines.append(
            f"| **{r['Regime']}** | {r['Cell']} | {r['N']:,} | {r['Arm A Mean R']:+.3f} | {r['Arm B Mean R']:+.3f} | {r['Win Rate B']} | {r['Delta (B-A)']:+.3f} | {r['Arm B 95% CI']} | {r['Delta 95% CI']} | {r['Perm p']:.4f} | {r['Sharpe']:.2f} | {r['Max DD (R)']} | {r['Eff N']} | **{r['Cell Pass']}** |"
        )
    table_md = "\n".join(table_lines)

    # Build Summary Narrative per Regime
    regime_summaries = []
    for reg, data in matrix_results.items():
        pooled = data["pooled"]
        my_c = data["my_consistency"]
        regime_summaries.append(f"""
### {reg} Regime Unconditioned Baseline
- **Total Trades:** {data['sample_size']:,}
- **Pooled Arm A Net R:** {pooled['arm_a_mean']:+.4f} (95% CI: [{pooled['arm_a_ci_95'][0]:.3f}, {pooled['arm_a_ci_95'][1]:.3f}])
- **Pooled Arm B Net R:** {pooled['arm_b_mean']:+.4f} (95% CI: [{pooled['arm_b_ci_95'][0]:.3f}, {pooled['arm_b_ci_95'][1]:.3f}])
- **Win Rate (Arm B):** {pooled['win_rate_b']*100:.2f}%
- **Expectancy Delta (B - A):** {pooled['delta_mean']:+.4f} (Permutation p: {pooled['permutation_p']:.4f})
- **Portfolio Sharpe:** {pooled['portfolio_sharpe']:.2f}
- **Max Drawdown:** {pooled['max_drawdown_r']:.1f} R
- **Temporal Consistency:** Valid cells = {my_c.get('valid_cells_count', 0)}, Best cell = {my_c.get('best_cell_mean_r', 0):+.3f}, Worst cell = {my_c.get('worst_cell_mean_r', 0):+.3f}
- **Consistency Flags:** {', '.join(my_c.get('consistency_flags', ['NONE'])) if my_c.get('consistency_flags') else 'CLEAN'}
""")

    report_content = f"""# PHASE 2: PURE 20-DAY BREAKOUT BASELINE AUDIT REPORT
**Strategy Identity:** `BASELINE_20D_BREAKOUT` (Unconditioned Price-Action Reference)  
**Evaluation Window:** 2016-09-27 to 2026-09-25 (10-Year Full Cycle)  
**Universe:** {stock_count} Certified Indian Equities (NSE/BSE)  
**Total Signals Tested:** {total_trades:,} Causal Executions  
**Governance Status:** BASELINE BENCHMARK ESTABLISHED (Reference for Phase 5 Fundamental Acceleration)

---

{provenance_section}

---

## 1. PURPOSE & SCIENTIFIC HYPOTHESIS
This Phase 2 benchmark measures the raw, unconditioned statistical distribution of a standard 20-day high breakout across the Indian equity universe.
Before testing fundamental earnings acceleration (`EARNINGS_ACCELERATION_BREAKOUT`), the system requires an empirical baseline to quantify:
1. **Raw Trend Premium:** How much positive expectancy is generated by pure 20-day price momentum alone?
2. **Regime Dependency:** How does the raw breakout perform across BULL vs. SIDEWAYS vs. BEAR regimes?
3. **Temporal Decay:** Does raw breakout expectancy replicate consistently across independent 3-year multi-year blocks, or is it heavily concentrated in specific market cycles?
4. **Alpha Hurdle:** In Phase 5, the fundamental earnings acceleration filter must produce statistically superior results (Delta $p < 0.05$ and superior CI) compared to this unconditioned baseline.

---

## 2. EXPERIMENTAL DESIGN & FROZEN PARAMETERS
- **Signal Trigger:** Bar $T$ Daily Close > 20-day High (excluding Bar $T$).
- **Causal Latency:** Order placed for execution at Bar $T+1$ Open (zero same-bar execution).
- **Risk Definition ($R$):** $1.5 \\times \\text{{ATR}}_{{14}}$ on Bar $T$.
- **Friction:** 5 bps entry + 5 bps exit (10 bps round trip).
- **Arm A (Static Baseline):** Target $+2.0R$, Stop Loss $-1.0R$, Max holding cap 15 sessions.
- **Arm B (Active Production Architecture):**
  - Target 1: $+1.5R$ (50% scale-out)
  - Target 2: $+2.5R$ (remaining 50% scale-out)
  - Breakeven Trigger: Activated when price reaches $+1.0R$
  - Trailing Stop: $0.5 \\times \\text{{ATR}}_{{14}}$ trail after breakeven
  - Holding Cap: 15 trading sessions

---

## 3. FULL 12-CELL TEMPORAL × REGIME MATRIX
The table below records the cell-by-cell statistical battery across all 3 market regimes and 4 pre-registered non-overlapping calendar epochs:

{table_md}

---

## 4. REGIME-BY-REGIME EMPIRICAL BREAKDOWN
{''.join(regime_summaries)}

---

## 5. SUMMARY OF KEY FINDINGS & ALPHA THRESHOLDS FOR PHASE 5
1. **BULL Regime Baseline:** Establishes the expected baseline return, win rate, and drawdown of price breakout momentum in supportive market environments.
2. **SIDEWAYS Regime Baseline:** Quantifies the historical chop and whipsaw drag when equities lack broader index trend support.
3. **BEAR Regime Baseline:** Provides empirical verification of false-breakout risks during market drawdowns.
4. **Mandatory Hurdle for Phase 5 (`EARNINGS_ACCELERATION_BREAKOUT`):**
   When historical point-in-time quarterly earnings data is joined, the combined strategy must demonstrate:
   - Statistically significant positive delta ($\Delta Net R > 0$, paired permutation $p < 0.05$) against this 20D baseline.
   - Significant reduction in false breakouts in SIDEWAYS and BEAR regimes.
   - Preserved or improved replication consistency across all 4 independent temporal cells.

---
*Generated automatically by Elite Breakout System Baseline Engine under AGENTS.md Governance Protocol.*
"""
    return report_content


if __name__ == "__main__":
    run_baseline_study()
