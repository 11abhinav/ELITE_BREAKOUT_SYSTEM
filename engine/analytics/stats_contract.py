"""
Statistical Contract Module for Wave 3 Governance.
Provides rigorous Wilson CIs, dependence-aware Cluster Bootstrap with BCa and Percentile fallback,
sample size validation, confusion matrix analytics, and three-tier R-multiple accounting.
"""

import math
from typing import Dict, Any, Callable, List, Optional, Tuple, Union
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd


def _norm_cdf(x: float) -> float:
    """Standard Normal Cumulative Distribution Function using math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """
    Standard Normal Percent Point Function (Quantile / Probit)
    using Acklam's high-precision rational approximation (max error < 1.15e-9).
    """
    if p <= 0.0:
        return -8.0
    if p >= 1.0:
        return 8.0

    # Coefficients for Acklam's algorithm
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]

    p_low = 0.02425
    p_high = 1.0 - p_low

    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
               ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)
    elif p <= p_high:
        q = p - 0.5
        r = q * q
        return (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5])*q / \
               (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
                ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1.0)


@dataclass
class ConfidenceIntervalResult:
    point_estimate: float
    ci_lower: float
    ci_upper: float
    confidence_level: float
    ci_method: str
    ci_degraded: bool
    n_observations: int
    n_clusters: Optional[int] = None
    independence_unit: Optional[str] = None
    bootstrap_seed: Optional[int] = None
    bootstrap_iterations: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def wilson_score_ci(
    successes: int,
    trials: int,
    confidence: float = 0.95
) -> ConfidenceIntervalResult:
    """
    Calculates the two-sided Wilson Score interval for binomial proportions.
    Properly handles boundary cases (0/N, N/N, and small samples).
    """
    if trials <= 0:
        return ConfidenceIntervalResult(
            point_estimate=0.0,
            ci_lower=0.0,
            ci_upper=0.0,
            confidence_level=confidence,
            ci_method="WILSON_SCORE",
            ci_degraded=False,
            n_observations=0
        )

    p = float(successes) / float(trials)
    alpha = 1.0 - confidence
    z = _norm_ppf(1.0 - alpha / 2.0)
    z_sq = z * z

    denominator = 1.0 + (z_sq / trials)
    center = (p + (z_sq / (2.0 * trials))) / denominator
    spread = (z / denominator) * math.sqrt((p * (1.0 - p) / trials) + (z_sq / (4.0 * trials * trials)))

    ci_lower = max(0.0, float(center - spread))
    ci_upper = min(1.0, float(center + spread))

    return ConfidenceIntervalResult(
        point_estimate=round(p, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        confidence_level=confidence,
        ci_method="WILSON_SCORE",
        ci_degraded=False,
        n_observations=trials
    )


def cluster_bootstrap_ci(
    df: pd.DataFrame,
    cluster_col: str,
    metric_func: Callable[[pd.DataFrame], float],
    confidence: float = 0.95,
    n_resamples: int = 10000,
    random_seed: int = 42,
    independence_unit: str = "symbol_x_trading_date"
) -> ConfidenceIntervalResult:
    """
    Performs dependence-aware Cluster Bootstrap resampling on clusters rather than individual rows.
    Attempts BCa (Bias-Corrected and Accelerated) bootstrap with fallback to Percentile bootstrap.
    """
    if df.empty:
        return ConfidenceIntervalResult(
            point_estimate=0.0,
            ci_lower=0.0,
            ci_upper=0.0,
            confidence_level=confidence,
            ci_method="INSUFFICIENT_DATA",
            ci_degraded=True,
            n_observations=0,
            n_clusters=0,
            independence_unit=independence_unit
        )

    if cluster_col not in df.columns:
        raise ValueError(f"Cluster column '{cluster_col}' not found in dataframe.")

    df = df.reset_index(drop=True)
    clusters = df[cluster_col].unique()
    n_clusters = len(clusters)
    n_obs = len(df)

    # Point estimate on complete dataset
    try:
        point_estimate = float(metric_func(df))
    except Exception as e:
        raise RuntimeError(f"Error computing metric function on base data: {e}")

    if n_clusters < 3:
        # Too few clusters to bootstrap meaningfully
        return ConfidenceIntervalResult(
            point_estimate=round(point_estimate, 4),
            ci_lower=round(point_estimate, 4),
            ci_upper=round(point_estimate, 4),
            confidence_level=confidence,
            ci_method="DEGENERATE_SAMPLE",
            ci_degraded=True,
            n_observations=n_obs,
            n_clusters=n_clusters,
            independence_unit=independence_unit,
            bootstrap_seed=random_seed,
            bootstrap_iterations=0
        )

    # Group indices by cluster
    cluster_groups = df.groupby(cluster_col).groups
    cluster_indices = [np.array(cluster_groups[c], dtype=int) for c in clusters]
    cluster_to_idx = {c: i for i, c in enumerate(clusters)}

    rng = np.random.RandomState(random_seed)
    boot_estimates = np.zeros(n_resamples, dtype=float)

    # Pre-extract data for blazing fast vectorized resampling
    for i in range(n_resamples):
        sampled_c_idxs = rng.choice(n_clusters, size=n_clusters, replace=True)
        sampled_idx = np.concatenate([cluster_indices[k] for k in sampled_c_idxs])
        resampled_df = df.iloc[sampled_idx]
        try:
            boot_estimates[i] = metric_func(resampled_df)
        except Exception:
            boot_estimates[i] = point_estimate

    # Check for bootstrap variance
    boot_var = np.var(boot_estimates)
    if boot_var < 1e-12:
        return ConfidenceIntervalResult(
            point_estimate=round(point_estimate, 4),
            ci_lower=round(point_estimate, 4),
            ci_upper=round(point_estimate, 4),
            confidence_level=confidence,
            ci_method="ZERO_VARIANCE",
            ci_degraded=True,
            n_observations=n_obs,
            n_clusters=n_clusters,
            independence_unit=independence_unit,
            bootstrap_seed=random_seed,
            bootstrap_iterations=n_resamples
        )

    # Attempt BCa calculation
    alpha = 1.0 - confidence
    z_alpha_low = _norm_ppf(alpha / 2.0)
    z_alpha_high = _norm_ppf(1.0 - alpha / 2.0)

    # 1. Bias correction parameter z0
    prop_less = np.mean(boot_estimates < point_estimate)
    prop_less = np.clip(prop_less, 1e-6, 1.0 - 1e-6)
    z0 = _norm_ppf(prop_less)

    # 2. Acceleration parameter a using Cluster Jackknife (leave-one-cluster-out)
    jackknife_estimates = np.zeros(n_clusters, dtype=float)
    all_cluster_indices = np.arange(n_clusters)
    for j in range(n_clusters):
        keep_clusters = all_cluster_indices[all_cluster_indices != j]
        jack_idx = np.concatenate([cluster_indices[k] for k in keep_clusters])
        jack_df = df.iloc[jack_idx]
        try:
            jackknife_estimates[j] = metric_func(jack_df)
        except Exception:
            jackknife_estimates[j] = point_estimate

    jack_mean = np.mean(jackknife_estimates)
    diff = jack_mean - jackknife_estimates
    sum_diff_sq = np.sum(diff ** 2)
    sum_diff_cubed = np.sum(diff ** 3)

    ci_method = "BCa"
    ci_degraded = False

    if sum_diff_sq < 1e-12 or np.isnan(sum_diff_sq) or np.isinf(sum_diff_sq):
        # Degenerate acceleration, fall back to percentile
        ci_method = "PERCENTILE_FALLBACK"
        ci_degraded = True
        a = 0.0
    else:
        a = sum_diff_cubed / (6.0 * (sum_diff_sq ** 1.5))
        if np.isnan(a) or np.isinf(a) or abs(a) > 0.5:
            ci_method = "PERCENTILE_FALLBACK"
            ci_degraded = True
            a = 0.0

    if not ci_degraded:
        # BCa adjusted quantiles
        try:
            denom_low = 1.0 - a * (z0 + z_alpha_low)
            denom_high = 1.0 - a * (z0 + z_alpha_high)
            if denom_low <= 0 or denom_high <= 0:
                raise ValueError("Degenerate BCa denominator")
            a1 = _norm_cdf(z0 + (z0 + z_alpha_low) / denom_low)
            a2 = _norm_cdf(z0 + (z0 + z_alpha_high) / denom_high)
            a1 = np.clip(a1, 0.0001, 0.9999)
            a2 = np.clip(a2, 0.0001, 0.9999)
            ci_lower = float(np.percentile(boot_estimates, a1 * 100.0))
            ci_upper = float(np.percentile(boot_estimates, a2 * 100.0))
        except Exception:
            ci_method = "PERCENTILE_FALLBACK"
            ci_degraded = True

    if ci_degraded:
        # Fallback to standard Percentile Bootstrap
        ci_lower = float(np.percentile(boot_estimates, (alpha / 2.0) * 100.0))
        ci_upper = float(np.percentile(boot_estimates, (1.0 - alpha / 2.0) * 100.0))

    return ConfidenceIntervalResult(
        point_estimate=round(point_estimate, 4),
        ci_lower=round(ci_lower, 4),
        ci_upper=round(ci_upper, 4),
        confidence_level=confidence,
        ci_method=ci_method,
        ci_degraded=ci_degraded,
        n_observations=n_obs,
        n_clusters=n_clusters,
        independence_unit=independence_unit,
        bootstrap_seed=random_seed,
        bootstrap_iterations=n_resamples
    )


@dataclass
class ConfusionMatrixResult:
    true_positives: int    # Retained Wins
    false_positives: int   # Retained Losses
    false_negatives: int   # Filtered Wins
    true_negatives: int    # Filtered Losses
    total_samples: int
    winner_retention_pct: float  # TP / (TP + FN)
    loser_recall_pct: float      # TN / (TN + FP) [Losers eliminated]
    specificity_pct: float       # TN / (TN + FP)
    precision_pct: float         # TP / (TP + FP)
    balanced_accuracy_pct: float # (Sensitivity + Specificity) / 2
    trade_retention_pct: float   # (TP + FP) / Total
    expected_r_before: float
    expected_r_after: float
    delta_expected_r: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def calculate_confusion_matrix(
    is_winner: pd.Series,
    pass_filter: pd.Series,
    realized_r: pd.Series
) -> ConfusionMatrixResult:
    """
    Computes a complete 2x2 confusion matrix and diagnostic classification metrics for candidate filters.
    """
    total = len(is_winner)
    if total == 0:
        return ConfusionMatrixResult(
            true_positives=0, false_positives=0, false_negatives=0, true_negatives=0,
            total_samples=0, winner_retention_pct=0.0, loser_recall_pct=0.0,
            specificity_pct=0.0, precision_pct=0.0, balanced_accuracy_pct=0.0,
            trade_retention_pct=0.0, expected_r_before=0.0, expected_r_after=0.0, delta_expected_r=0.0
        )

    tp = int(((is_winner == True) & (pass_filter == True)).sum())
    fp = int(((is_winner == False) & (pass_filter == True)).sum())
    fn = int(((is_winner == True) & (pass_filter == False)).sum())
    tn = int(((is_winner == False) & (pass_filter == False)).sum())

    total_winners = tp + fn
    total_losers = fp + tn
    retained_trades = tp + fp

    win_retention = (tp / total_winners * 100.0) if total_winners > 0 else 0.0
    loser_recall = (tn / total_losers * 100.0) if total_losers > 0 else 0.0
    specificity = loser_recall
    precision = (tp / retained_trades * 100.0) if retained_trades > 0 else 0.0
    balanced_acc = (win_retention + loser_recall) / 2.0
    trade_retention = (retained_trades / total * 100.0)

    er_before = float(realized_r.mean()) if total > 0 else 0.0
    er_after = float(realized_r[pass_filter].mean()) if retained_trades > 0 else 0.0
    delta_er = er_after - er_before

    return ConfusionMatrixResult(
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        true_negatives=tn,
        total_samples=total,
        winner_retention_pct=round(win_retention, 2),
        loser_recall_pct=round(loser_recall, 2),
        specificity_pct=round(specificity, 2),
        precision_pct=round(precision, 2),
        balanced_accuracy_pct=round(balanced_acc, 2),
        trade_retention_pct=round(trade_retention, 2),
        expected_r_before=round(er_before, 4),
        expected_r_after=round(er_after, 4),
        delta_expected_r=round(delta_er, 4)
    )


def evaluate_sample_eligibility(n: int, threshold: int = 50) -> Dict[str, Any]:
    """
    Explicitly evaluates sample size thresholds to distinguish INSUFFICIENT_SAMPLE from FAIL.
    """
    if n >= threshold:
        return {
            "status": "ELIGIBLE",
            "is_inferable": True,
            "sample_size": n,
            "threshold": threshold,
            "note": "Sufficient sample for statistical hypothesis testing and promotion evaluation."
        }
    else:
        return {
            "status": "INSUFFICIENT_SAMPLE",
            "is_inferable": False,
            "sample_size": n,
            "threshold": threshold,
            "note": "Descriptive only. Subgroup sample is below minimum statistical requirement; does NOT imply hypothesis failure."
        }


def compute_three_tier_r(
    entry_price: float,
    exit_price: float,
    sl_price: float,
    friction_r: float = 0.05,
    position_size_multiplier: float = 1.0
) -> Dict[str, float]:
    """
    Computes three-tier R-multiple accounting:
    1. gross_trade_R: Pure setup return in risk units
    2. net_trade_R: After deducting execution friction (slippage/commissions in R units)
    3. portfolio_weighted_R: Economic return weighted by position sizing multiplier
    """
    risk_unit = abs(entry_price - sl_price)
    if risk_unit <= 1e-6:
        raise ValueError(f"Risk unit too small: {risk_unit} (Entry: {entry_price}, SL: {sl_price})")

    gross_r = (exit_price - entry_price) / risk_unit
    net_r = gross_r - friction_r
    portfolio_r = net_r * position_size_multiplier

    return {
        "gross_trade_R": round(gross_r, 4),
        "net_trade_R": round(net_r, 4),
        "portfolio_weighted_R": round(portfolio_r, 4)
    }
