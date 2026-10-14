"""
RSMR-1 Comprehensive Multi-Timeframe, Multi-Regime & Multi-Episode Master Runner
Executes exhaustive testing across:
- 3 Macro Regimes: BULL, SIDEWAYS, BEAR
- 10 Distinct Historical Episodes (Crashes, Rallies, Consolidations)
- 4 Rebalance Timeframes: Monthly (21d), Bi-Monthly (42d), Quarterly (63d), Semi-Annual (126d)
- 3 Momentum Horizon Lookbacks: 3-Month (63d), 6-Month (126d), 12-Month (252d)
- 4 Independent Temporal Cells: 2016-2018, 2019-2021, 2022-2024, 2025-2026
- Complete trade-level win rates, profit factors, drawdowns, and Sharpe ratios.
"""

import os
import sys
import json
import logging
import pandas as pd
import numpy as np

SRC_DIR = os.path.join(os.path.dirname(__file__), "src")
sys.path.insert(0, SRC_DIR)

from universe import UniverseManager
from multi_regime_backtest import MultiRegimeBacktester
from benchmark import calculate_portfolio_metrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("RSMR_1_Comprehensive")

BASE_DIR = os.path.dirname(__file__)
DATA_OUT = os.path.join(BASE_DIR, "data")
REPORTS_OUT = os.path.join(BASE_DIR, "reports")
BENCH_PATH = os.path.join(DATA_OUT, "NIFTY_500.parquet")

def analyze_trade_metrics(order_ledger: list) -> dict:
    sells = [o for o in order_ledger if o.get("side") == "SELL" and "pnl" in o]
    if not sells:
        return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0}
    
    wins = [s for s in sells if s["pnl"] > 0]
    losses = [s for s in sells if s["pnl"] <= 0]
    
    tot_win_amt = sum(s["pnl"] for s in wins)
    tot_loss_amt = abs(sum(s["pnl"] for s in losses))
    
    win_rate = (len(wins) / len(sells)) * 100.0
    profit_factor = (tot_win_amt / tot_loss_amt) if tot_loss_amt > 0 else 999.0
    
    avg_win = float(np.mean([s.get("pnl_pct", 0) for s in wins])) * 100.0 if wins else 0.0
    avg_loss = float(np.mean([s.get("pnl_pct", 0) for s in losses])) * 100.0 if losses else 0.0
    
    return {
        "total_trades": len(sells),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win, 1),
        "avg_loss_pct": round(avg_loss, 1)
    }

def main():
    logger.info("Initializing Universe for Comprehensive Multi-Timeframe & Multi-Regime Study...")
    univ = UniverseManager(data_dir="data/history/1d")
    tester = MultiRegimeBacktester(univ, BENCH_PATH)

    results_matrix = {}

    # -------------------------------------------------------------
    # STUDY 1: MULTIPLE REBALANCE TIMEFRAMES (Holding Period Cadences)
    # (Tested with baseline 6-Month Momentum Lookback)
    # -------------------------------------------------------------
    logger.info("=============================================================")
    logger.info("STUDY 1: MULTIPLE REBALANCE TIMEFRAMES (Holding Cadences)...")
    logger.info("=============================================================")
    cadences = [
        {"name": "Monthly (21 Days)", "rebal_days": 21, "lookback": 126},
        {"name": "Bi-Monthly (42 Days)", "rebal_days": 42, "lookback": 126},
        {"name": "Quarterly (63 Days - Baseline)", "rebal_days": 63, "lookback": 126},
        {"name": "Semi-Annual (126 Days)", "rebal_days": 126, "lookback": 126},
    ]

    timeframe_results = []
    cadence_nav_dfs = {}

    for c in cadences:
        logger.info(f"Running Cadence: {c['name']}...")
        nav_df, orders = tester.run_strategy_simulation(
            rebalance_days=c["rebal_days"],
            momentum_lookback_days=c["lookback"]
        )
        cadence_nav_dfs[c["name"]] = nav_df
        
        # Align with benchmark
        nav_df["dt"] = pd.to_datetime(nav_df["date"]).dt.normalize()
        m_df = pd.merge(nav_df, tester.bench_df[["Close", "bench_return", "regime"]], left_on="dt", right_index=True)
        
        p_metrics = calculate_portfolio_metrics(m_df["daily_return"])
        b_metrics = calculate_portfolio_metrics(m_df["bench_return"])
        t_metrics = analyze_trade_metrics(orders)
        
        timeframe_results.append({
            "timeframe": c["name"],
            "rebal_days": c["rebal_days"],
            "cagr": round(p_metrics.get("cagr", 0)*100, 2),
            "bench_cagr": round(b_metrics.get("cagr", 0)*100, 2),
            "excess_cagr": round((p_metrics.get("cagr", 0) - b_metrics.get("cagr", 0))*100, 2),
            "sharpe": round(p_metrics.get("sharpe", 0), 2),
            "sortino": round(p_metrics.get("sortino", 0), 2),
            "max_dd": round(p_metrics.get("max_drawdown", 0)*100, 2),
            "bench_max_dd": round(b_metrics.get("max_drawdown", 0)*100, 2),
            "win_rate": t_metrics["win_rate"],
            "profit_factor": t_metrics["profit_factor"],
            "total_trades": t_metrics["total_trades"]
        })

    results_matrix["timeframe_cadences"] = timeframe_results
    pd.DataFrame(timeframe_results).to_csv(os.path.join(DATA_OUT, "timeframe_cadence_study.csv"), index=False)

    # -------------------------------------------------------------
    # STUDY 2: MULTIPLE MOMENTUM LOOKBACK TIMEFRAMES
    # (Tested with Quarterly Rebalance Cadence)
    # -------------------------------------------------------------
    logger.info("=============================================================")
    logger.info("STUDY 2: MULTIPLE MOMENTUM LOOKBACK HORIZONS...")
    logger.info("=============================================================")
    lookbacks = [
        {"name": "3-Month Lookback (63 Sessions)", "rebal_days": 63, "lookback": 63},
        {"name": "6-Month Lookback (126 Sessions - Baseline)", "rebal_days": 63, "lookback": 126},
        {"name": "12-Month Lookback (252 Sessions)", "rebal_days": 63, "lookback": 252},
    ]

    lookback_results = []
    for lb in lookbacks:
        logger.info(f"Running Lookback: {lb['name']}...")
        nav_df, orders = tester.run_strategy_simulation(
            rebalance_days=lb["rebal_days"],
            momentum_lookback_days=lb["lookback"]
        )
        nav_df["dt"] = pd.to_datetime(nav_df["date"]).dt.normalize()
        m_df = pd.merge(nav_df, tester.bench_df[["Close", "bench_return", "regime"]], left_on="dt", right_index=True)
        
        p_metrics = calculate_portfolio_metrics(m_df["daily_return"])
        b_metrics = calculate_portfolio_metrics(m_df["bench_return"])
        t_metrics = analyze_trade_metrics(orders)
        
        lookback_results.append({
            "lookback": lb["name"],
            "lookback_bars": lb["lookback"],
            "cagr": round(p_metrics.get("cagr", 0)*100, 2),
            "bench_cagr": round(b_metrics.get("cagr", 0)*100, 2),
            "excess_cagr": round((p_metrics.get("cagr", 0) - b_metrics.get("cagr", 0))*100, 2),
            "sharpe": round(p_metrics.get("sharpe", 0), 2),
            "max_dd": round(p_metrics.get("max_drawdown", 0)*100, 2),
            "win_rate": t_metrics["win_rate"],
            "profit_factor": t_metrics["profit_factor"],
            "total_trades": t_metrics["total_trades"]
        })

    results_matrix["momentum_lookbacks"] = lookback_results
    pd.DataFrame(lookback_results).to_csv(os.path.join(DATA_OUT, "momentum_lookback_study.csv"), index=False)

    # -------------------------------------------------------------
    # STUDY 3: REGIME ATTRIBUTION (BULL vs SIDEWAYS vs BEAR)
    # Using Baseline (Quarterly Rebalance + 6M Momentum)
    # -------------------------------------------------------------
    logger.info("=============================================================")
    logger.info("STUDY 3: MACRO REGIME PERFORMANCE ATTRIBUTION...")
    logger.info("=============================================================")
    base_nav = cadence_nav_dfs["Quarterly (63 Days - Baseline)"]
    base_nav["dt"] = pd.to_datetime(base_nav["date"]).dt.normalize()
    base_merged = pd.merge(base_nav, tester.bench_df[["Close", "bench_return", "regime"]], left_on="dt", right_index=True)
    base_merged = base_merged.set_index("dt").sort_index()

    regime_results = []
    for reg in ["BULL", "SIDEWAYS", "BEAR"]:
        r_slice = base_merged[base_merged["regime"] == reg]
        if len(r_slice) > 0:
            strat_ret = r_slice["daily_return"]
            bench_ret = r_slice["bench_return"]
            
            s_cum = (1.0 + strat_ret).prod() - 1.0
            b_cum = (1.0 + bench_ret).prod() - 1.0
            
            # Annualized return during regime sessions
            yrs = len(r_slice) / 252.0
            s_ann = ((1.0 + s_cum) ** (1.0 / yrs)) - 1.0 if (yrs > 0 and (1.0 + s_cum) > 0) else 0.0
            b_ann = ((1.0 + b_cum) ** (1.0 / yrs)) - 1.0 if (yrs > 0 and (1.0 + b_cum) > 0) else 0.0
            
            s_vol = strat_ret.std() * np.sqrt(252)
            b_vol = bench_ret.std() * np.sqrt(252)
            
            sharpe = (strat_ret.mean() / strat_ret.std() * np.sqrt(252)) if strat_ret.std() > 0 else 0.0
            win_pct = (strat_ret > 0).mean() * 100.0
            
            regime_results.append({
                "regime": reg,
                "sessions_count": len(r_slice),
                "sessions_pct": round(len(r_slice) / len(base_merged) * 100, 1),
                "strat_ann_return": round(s_ann * 100, 2),
                "bench_ann_return": round(b_ann * 100, 2),
                "excess_ann_return": round((s_ann - b_ann) * 100, 2),
                "strat_vol": round(s_vol * 100, 2),
                "bench_vol": round(b_vol * 100, 2),
                "sharpe": round(sharpe, 2),
                "daily_win_rate": round(win_pct, 1)
            })

    results_matrix["regime_attribution"] = regime_results
    pd.DataFrame(regime_results).to_csv(os.path.join(DATA_OUT, "regime_attribution_study.csv"), index=False)

    # -------------------------------------------------------------
    # STUDY 4: 10 DISTINCT HISTORICAL MARKET EPISODES
    # -------------------------------------------------------------
    logger.info("=============================================================")
    logger.info("STUDY 4: 10 DISTINCT HISTORICAL EPISODES...")
    logger.info("=============================================================")
    episodes = [
        # BEAR EPISODES
        {"name": "2018 NBFC Liquidity Crisis", "type": "BEAR", "start": "2018-01-01", "end": "2018-10-31"},
        {"name": "2020 COVID-19 Crash", "type": "BEAR", "start": "2020-01-01", "end": "2020-04-30"},
        {"name": "2021-2022 Global Rate Hike / War", "type": "BEAR", "start": "2021-10-01", "end": "2022-06-30"},
        {"name": "Late 2024-2025 Market Correction", "type": "BEAR", "start": "2024-10-01", "end": "2025-03-31"},
        # BULL EPISODES
        {"name": "2017 Mega Mid-Cap Bull Run", "type": "BULL", "start": "2017-01-01", "end": "2017-12-31"},
        {"name": "2020-2021 Post-COVID Liquidity Surge", "type": "BULL", "start": "2020-05-01", "end": "2021-09-30"},
        {"name": "2023-2024 Capex & Manufacturing Rally", "type": "BULL", "start": "2023-04-01", "end": "2024-09-30"},
        # SIDEWAYS EPISODES
        {"name": "Late 2016 Demonetization Aftermath", "type": "SIDEWAYS", "start": "2016-10-01", "end": "2016-12-31"},
        {"name": "2019 Pre-Election Choppiness", "type": "SIDEWAYS", "start": "2019-01-01", "end": "2019-12-31"},
        {"name": "2022-2023 Consolidation Range", "type": "SIDEWAYS", "start": "2022-07-01", "end": "2023-03-31"}
    ]

    episode_results = []
    for ep in episodes:
        sub = base_merged.loc[ep["start"]:ep["end"]]
        if len(sub) > 0:
            s_ret = sub["daily_return"]
            b_ret = sub["bench_return"]
            
            s_cum = (1.0 + s_ret).prod() - 1.0
            b_cum = (1.0 + b_ret).prod() - 1.0
            
            # Max DD inside episode
            s_w = (1.0 + s_ret).cumprod()
            s_dd = ((s_w - s_w.cummax()) / s_w.cummax()).min()
            
            b_w = (1.0 + b_ret).cumprod()
            b_dd = ((b_w - b_w.cummax()) / b_w.cummax()).min()
            
            episode_results.append({
                "episode": ep["name"],
                "regime_type": ep["type"],
                "calendar_span": f"{ep['start']} to {ep['end']}",
                "sessions": len(sub),
                "strategy_return_pct": round(s_cum * 100, 2),
                "benchmark_return_pct": round(b_cum * 100, 2),
                "alpha_pct": round((s_cum - b_cum) * 100, 2),
                "strategy_max_dd_pct": round(s_dd * 100, 2),
                "benchmark_max_dd_pct": round(b_dd * 100, 2)
            })

    results_matrix["historical_episodes"] = episode_results
    pd.DataFrame(episode_results).to_csv(os.path.join(DATA_OUT, "historical_episodes_study.csv"), index=False)

    # -------------------------------------------------------------
    # STUDY 5: 4 INDEPENDENT TEMPORAL CELLS (Per AGENTS.md Protocol)
    # -------------------------------------------------------------
    logger.info("=============================================================")
    logger.info("STUDY 5: 4 INDEPENDENT TEMPORAL CELLS...")
    logger.info("=============================================================")
    cells = [
        {"cell": "Cell 1 (2016-2018)", "start": "2016-09-27", "end": "2018-12-31"},
        {"cell": "Cell 2 (2019-2021)", "start": "2019-01-01", "end": "2021-12-31"},
        {"cell": "Cell 3 (2022-2024)", "start": "2022-01-01", "end": "2024-12-31"},
        {"cell": "Cell 4 (2025-2026)", "start": "2025-01-01", "end": "2026-10-12"},
    ]

    cell_results = []
    for cl in cells:
        sub = base_merged.loc[cl["start"]:cl["end"]]
        if len(sub) > 0:
            p_m = calculate_portfolio_metrics(sub["daily_return"])
            b_m = calculate_portfolio_metrics(sub["bench_return"])
            
            cell_results.append({
                "cell": cl["cell"],
                "sessions": len(sub),
                "strat_cagr": round(p_m.get("cagr", 0)*100, 2),
                "bench_cagr": round(b_m.get("cagr", 0)*100, 2),
                "excess_cagr": round((p_m.get("cagr", 0) - b_m.get("cagr", 0))*100, 2),
                "sharpe": round(p_m.get("sharpe", 0), 2),
                "bench_sharpe": round(b_m.get("sharpe", 0), 2),
                "strat_max_dd": round(p_m.get("max_drawdown", 0)*100, 2),
                "bench_max_dd": round(b_m.get("max_drawdown", 0)*100, 2)
            })

    results_matrix["temporal_cells"] = cell_results
    pd.DataFrame(cell_results).to_csv(os.path.join(DATA_OUT, "temporal_cells_study.csv"), index=False)

    # Save complete JSON artifact
    with open(os.path.join(DATA_OUT, "comprehensive_multi_regime_study.json"), "w") as f:
        json.dump(results_matrix, f, indent=2)

    logger.info("=============================================================")
    logger.info("COMPREHENSIVE MULTI-TIMEFRAME & MULTI-REGIME STUDY COMPLETED!")
    logger.info(f"Artifacts saved in {DATA_OUT}")
    logger.info("=============================================================")

if __name__ == "__main__":
    main()
