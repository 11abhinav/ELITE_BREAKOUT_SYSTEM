"""
Benchmark Analytics, Risk Metrics & Bootstrap Engine for RSMR-1.
"""

from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
import numpy as np

def calculate_portfolio_metrics(
    daily_returns: pd.Series,
    rf_annual: float = 0.06,  # 6.0% risk-free rate for INR
    periods_per_year: int = 252
) -> Dict[str, Any]:
    daily_returns = daily_returns.dropna()
    n_days = len(daily_returns)
    if n_days < 10:
        return {}
        
    cum_ret = (1.0 + daily_returns).prod() - 1.0
    years = n_days / float(periods_per_year)
    cagr = ((1.0 + cum_ret) ** (1.0 / years)) - 1.0 if (years > 0 and (1.0 + cum_ret) > 0) else 0.0
    
    ann_vol = daily_returns.std() * np.sqrt(periods_per_year)
    
    # Sharpe ratio
    rf_daily = (1.0 + rf_annual) ** (1.0 / periods_per_year) - 1.0
    excess_daily = daily_returns - rf_daily
    sharpe = (excess_daily.mean() / daily_returns.std() * np.sqrt(periods_per_year)) if daily_returns.std() > 0 else 0.0
    
    # Sortino ratio
    downside_returns = daily_returns[daily_returns < 0]
    downside_std = downside_returns.std() * np.sqrt(periods_per_year)
    sortino = (excess_daily.mean() * periods_per_year / downside_std) if (len(downside_returns) > 0 and downside_std > 0) else 0.0
    
    # Drawdowns
    wealth_index = (1.0 + daily_returns).cumprod()
    peak = wealth_index.cummax()
    drawdown_series = (wealth_index - peak) / peak
    max_dd = float(drawdown_series.min())
    
    # Calmar ratio
    calmar = (cagr / abs(max_dd)) if abs(max_dd) > 0 else 0.0
    
    return {
        "days": n_days,
        "years": round(years, 2),
        "cum_return": cum_ret,
        "cagr": cagr,
        "ann_volatility": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "drawdown_series": drawdown_series
    }

def align_benchmark(
    strat_daily_df: pd.DataFrame,
    benchmark_parquet_path: str = "research/strategies/rsmr_1/data/NIFTY_500.parquet"
) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """
    Aligns strategy daily NAV returns with benchmark returns.
    """
    bench_df = pd.read_parquet(benchmark_parquet_path)
    if "Date" in bench_df.columns:
        bench_df["dt"] = pd.to_datetime(bench_df["Date"]).dt.tz_localize(None).dt.normalize()
    elif "Datetime" in bench_df.columns:
        bench_df["dt"] = pd.to_datetime(bench_df["Datetime"]).dt.tz_localize(None).dt.normalize()
    else:
        dt_idx = pd.to_datetime(bench_df.index)
        if dt_idx.tz is not None:
            dt_idx = dt_idx.tz_localize(None)
        bench_df["dt"] = dt_idx.normalize()
        
    bench_df = bench_df.drop_duplicates("dt").set_index("dt").sort_index()
    bench_df["bench_return"] = bench_df["Close"].pct_change().fillna(0.0)
    
    strat_df = strat_daily_df.copy()
    strat_df["dt"] = pd.to_datetime(strat_df["date"]).dt.tz_localize(None).dt.normalize()
    strat_df = strat_df.drop_duplicates("dt").set_index("dt").sort_index()
    
    aligned = pd.DataFrame({
        "strat_return": strat_df["daily_return"],
        "bench_return": bench_df["bench_return"]
    }).dropna()
    
    aligned["active_return"] = aligned["strat_return"] - aligned["bench_return"]
    
    strat_metrics = calculate_portfolio_metrics(aligned["strat_return"])
    bench_metrics = calculate_portfolio_metrics(aligned["bench_return"])
    
    # Active metrics
    cov = aligned["strat_return"].cov(aligned["bench_return"])
    bench_var = aligned["bench_return"].var()
    beta = (cov / bench_var) if bench_var > 0 else 1.0
    corr = aligned["strat_return"].corr(aligned["bench_return"])
    
    strat_metrics["beta"] = beta
    strat_metrics["correlation"] = corr
    strat_metrics["excess_cagr"] = strat_metrics.get("cagr", 0) - bench_metrics.get("cagr", 0)
    
    return aligned, strat_metrics, bench_metrics

def block_bootstrap_active_return(
    aligned_returns: pd.DataFrame,
    block_size_days: int = 21, # ~1 calendar month block
    num_simulations: int = 2000,
    random_seed: int = 42
) -> Dict[str, Any]:
    """
    Moving block bootstrap to preserve short-term serial correlation.
    Computes 95% confidence interval on annualized active excess CAGR.
    """
    np.random.seed(random_seed)
    n = len(aligned_returns)
    if n < block_size_days * 2:
        return {}
        
    strat_rets = aligned_returns["strat_return"].values
    bench_rets = aligned_returns["bench_return"].values
    
    num_blocks = int(np.ceil(n / block_size_days))
    max_start_idx = n - block_size_days
    
    bootstrapped_active_cagrs = []
    
    for _ in range(num_simulations):
        # Sample random block starting indices
        start_indices = np.random.randint(0, max_start_idx + 1, size=num_blocks)
        sample_indices = np.concatenate([np.arange(idx, idx + block_size_days) for idx in start_indices])[:n]
        
        sim_strat = strat_rets[sample_indices]
        sim_bench = bench_rets[sample_indices]
        
        years = n / 252.0
        cum_s = np.prod(1.0 + sim_strat)
        cum_b = np.prod(1.0 + sim_bench)
        
        cagr_s = (cum_s ** (1.0 / years)) - 1.0 if cum_s > 0 else -1.0
        cagr_b = (cum_b ** (1.0 / years)) - 1.0 if cum_b > 0 else -1.0
        
        bootstrapped_active_cagrs.append(cagr_s - cagr_b)
        
    bootstrapped_active_cagrs = np.array(bootstrapped_active_cagrs)
    ci_low = np.percentile(bootstrapped_active_cagrs, 2.5)
    ci_median = np.median(bootstrapped_active_cagrs)
    ci_high = np.percentile(bootstrapped_active_cagrs, 97.5)
    p_value = (bootstrapped_active_cagrs <= 0).mean()
    
    return {
        "num_simulations": num_simulations,
        "block_size_days": block_size_days,
        "mean_active_cagr": float(np.mean(bootstrapped_active_cagrs)),
        "median_active_cagr": float(ci_median),
        "ci_95_low": float(ci_low),
        "ci_95_high": float(ci_high),
        "p_value_one_tailed": float(p_value)
    }
