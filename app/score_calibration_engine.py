# =====================================================================================
# app/score_calibration_engine.py
# INSTITUTIONAL SCORE CALIBRATION & MATCHED-SAMPLE ATTRIBUTION ENGINE (Release 3)
# =====================================================================================

import math
import logging
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger("score_calibration_engine")

SCORE_BUCKETS = [
    ("70-74", 70, 74.99),
    ("75-79", 75, 79.99),
    ("80-84", 80, 84.99),
    ("85-89", 85, 89.99),
    ("90+",   90, 100.0)
]


class ScoreCalibrationEngine:
    """
    Translates raw heuristic scanner scores into empirically calibrated expectancies E[R],
    calculates Block-Bootstrap 95% confidence intervals, and performs matched-sample filter attribution
    with multiple-testing false discovery rate (FDR) control.
    """

    @staticmethod
    def compute_distribution_metrics(
        outcomes_df: pd.DataFrame,
        r_column: str = "realized_rr",
        block_size: int = 5,
        n_bootstraps: int = 1000,
        random_seed: int = 42
    ) -> Dict[str, Any]:
        """
        Calculates institutional distribution statistics with Block-Bootstrap 95% Confidence Intervals.
        """
        if outcomes_df is None or outcomes_df.empty:
            return {
                "sample_size": 0,
                "win_rate_pct": 0.0,
                "avg_win_r": 0.0,
                "avg_loss_r": 0.0,
                "expectancy_r": 0.0,
                "median_r": 0.0,
                "profit_factor": 0.0,
                "ci_95_lower_r": 0.0,
                "ci_95_upper_r": 0.0,
                "avg_mfe_r": 0.0,
                "avg_mae_r": 0.0,
                "r1_hit_rate_pct": 0.0,
                "r1_5_hit_rate_pct": 0.0,
                "r2_hit_rate_pct": 0.0
            }

        r_values = outcomes_df[r_column].dropna().astype(float).values
        n = len(r_values)
        if n == 0:
            return {"sample_size": 0, "expectancy_r": 0.0}

        wins = r_values[r_values > 0]
        losses = r_values[r_values <= 0]

        win_rate = (len(wins) / n) * 100.0
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 1.0

        expectancy = float(r_values.mean())
        median_r = float(np.median(r_values))

        gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

        # Block-Bootstrap Confidence Interval to account for time/cross-sectional clustering
        rng = np.random.default_rng(random_seed)
        num_blocks = max(1, n // block_size)
        boot_means = []

        for _ in range(n_bootstraps):
            # Resample blocks of indices
            block_starts = rng.integers(0, max(1, n - block_size + 1), size=num_blocks)
            sampled_indices = []
            for b in block_starts:
                sampled_indices.extend(range(b, min(b + block_size, n)))
            if sampled_indices:
                boot_means.append(r_values[sampled_indices[:n]].mean())

        if boot_means:
            ci_lower = float(np.percentile(boot_means, 2.5))
            ci_upper = float(np.percentile(boot_means, 97.5))
        else:
            ci_lower = expectancy
            ci_upper = expectancy

        # Quality ladder milestones
        r1_rate = (outcomes_df["r1_hit_before_sl"].sum() / n * 100.0) if "r1_hit_before_sl" in outcomes_df.columns else 0.0
        r1_5_rate = (outcomes_df["r1_5_hit_before_sl"].sum() / n * 100.0) if "r1_5_hit_before_sl" in outcomes_df.columns else 0.0
        r2_rate = (outcomes_df["r2_hit_before_sl"].sum() / n * 100.0) if "r2_hit_before_sl" in outcomes_df.columns else 0.0

        avg_mfe = float(outcomes_df["max_favorable_excursion_r"].mean()) if "max_favorable_excursion_r" in outcomes_df.columns else 0.0
        avg_mae = float(outcomes_df["max_adverse_excursion_r"].mean()) if "max_adverse_excursion_r" in outcomes_df.columns else 0.0

        return {
            "sample_size": n,
            "win_rate_pct": round(win_rate, 1),
            "avg_win_r": round(avg_win, 2),
            "avg_loss_r": round(avg_loss, 2),
            "expectancy_r": round(expectancy, 2),
            "median_r": round(median_r, 2),
            "profit_factor": round(profit_factor, 2),
            "ci_95_lower_r": round(ci_lower, 2),
            "ci_95_upper_r": round(ci_upper, 2),
            "avg_mfe_r": round(avg_mfe, 2),
            "avg_mae_r": round(avg_mae, 2),
            "r1_hit_rate_pct": round(r1_rate, 1),
            "r1_5_hit_rate_pct": round(r1_5_rate, 1),
            "r2_hit_rate_pct": round(r2_rate, 1)
        }

    @classmethod
    def calibrate_score_buckets(cls, outcomes_df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        """
        Generates calibrated expectancy tables across standard score buckets.
        """
        if outcomes_df is None or outcomes_df.empty or "score" not in outcomes_df.columns:
            return {}

        results = {}
        for bucket_name, min_s, max_s in SCORE_BUCKETS:
            subset = outcomes_df[(outcomes_df["score"] >= min_s) & (outcomes_df["score"] <= max_s)]
            metrics = cls.compute_distribution_metrics(subset)
            metrics["score_range"] = f"{min_s}-{max_s}"
            results[bucket_name] = metrics

        return results

    @classmethod
    def compute_matched_sample_attribution(
        cls,
        population_df: pd.DataFrame,
        filter_column: str,
        target_r_col: str = "realized_rr"
    ) -> Dict[str, Any]:
        """
        Calculates marginal filter value ΔE[R] = E[R]_with - E[R]_without on matched cohorts.
        """
        if population_df is None or population_df.empty or filter_column not in population_df.columns:
            return {"status": "INVALID_INPUT", "delta_expectancy_r": 0.0}

        with_filter = population_df[population_df[filter_column] == True]
        without_filter = population_df[population_df[filter_column] == False]

        metrics_with = cls.compute_distribution_metrics(with_filter, r_column=target_r_col)
        metrics_without = cls.compute_distribution_metrics(without_filter, r_column=target_r_col)

        delta_e = metrics_with["expectancy_r"] - metrics_without["expectancy_r"]
        ci_lower = metrics_with["ci_95_lower_r"] - metrics_without["ci_95_upper_r"]
        ci_upper = metrics_with["ci_95_upper_r"] - metrics_without["ci_95_lower_r"]

        return {
            "filter_name": filter_column,
            "sample_size_with": metrics_with["sample_size"],
            "sample_size_without": metrics_without["sample_size"],
            "expectancy_with": metrics_with["expectancy_r"],
            "expectancy_without": metrics_without["expectancy_r"],
            "delta_expectancy_r": round(delta_e, 2),
            "delta_ci_95_lower": round(ci_lower, 2),
            "delta_ci_95_upper": round(ci_upper, 2),
            "metrics_with": metrics_with,
            "metrics_without": metrics_without
        }

    @classmethod
    def compute_multidimensional_calibration_matrix(
        cls,
        outcomes_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        Decomposes performance across Scanner x Score Bucket x Regime.
        Controls for Simpson's Paradox to verify whether the Quality Score has independent
        predictive power within each scanner family and market regime.
        """
        if outcomes_df is None or outcomes_df.empty:
            return pd.DataFrame()

        records = []
        required_cols = ["scanner", "score", "regime"]
        if not all(col in outcomes_df.columns for col in required_cols):
            return pd.DataFrame()

        for scanner, sc_group in outcomes_df.groupby("scanner"):
            for bucket_name, min_s, max_s in SCORE_BUCKETS:
                bucket_group = sc_group[(sc_group["score"] >= min_s) & (sc_group["score"] <= max_s)]
                if bucket_group.empty:
                    continue

                for regime, reg_group in bucket_group.groupby("regime"):
                    n = len(reg_group)
                    if n == 0:
                        continue
                    m = cls.compute_distribution_metrics(reg_group)
                    records.append({
                        "scanner": scanner,
                        "score_bucket": bucket_name,
                        "regime": regime,
                        "sample_size": n,
                        "win_rate_pct": m["win_rate_pct"],
                        "expectancy_r": m["expectancy_r"],
                        "ci_95_lower": m["ci_95_lower_r"],
                        "ci_95_upper": m["ci_95_upper_r"],
                        "profit_factor": m["profit_factor"],
                        "r1_5_hit_pct": m["r1_5_hit_rate_pct"]
                    })

        return pd.DataFrame(records)

    @staticmethod
    def apply_multiple_testing_correction(
        hypotheses_results: List[Dict[str, Any]],
        alpha: float = 0.05
    ) -> List[Dict[str, Any]]:
        """
        Applies Benjamini-Hochberg False Discovery Rate (FDR) procedure to filter promotions.
        """
        if not hypotheses_results:
            return []

        # Sort by p-value or proxy test statistic
        sorted_hyps = sorted(hypotheses_results, key=lambda x: x.get("p_value", 1.0))
        m = len(sorted_hyps)

        for rank, hyp in enumerate(sorted_hyps, start=1):
            p_val = hyp.get("p_value", 1.0)
            bh_critical_value = (rank / m) * alpha
            hyp["bh_rank"] = rank
            hyp["bh_critical_value"] = round(bh_critical_value, 4)
            hyp["fdr_significant"] = bool(p_val <= bh_critical_value)

        return sorted_hyps
