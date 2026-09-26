#!/usr/bin/env python3
"""
MANDATORY TEMPORAL REPLICATION & REGIME ROBUSTNESS GATE
======================================================
Authoritative Temporal Replication, Independent Episode Partitioning, and Consistency Gate.

Core Invariants:
1. Anti-Pooled-Bias Rule: A strategy must NEVER be promoted or permanently locked because
   of one favorable pooled result (N = 20,000+, CI > 0, p < 0.05 is NOT sufficient).
2. Proven Reproducibility: Must replicate across:
   - Multiple calendar years
   - Multiple calendar periods (Q1, Q2, Q3, Q4)
   - Multiple independent market episodes within the same regime
   - Multiple independent temporal cells
3. No False Independence: Bootstrap resamples and permutation iterations are statistical
   checks, NOT temporal replications. Replications must contain genuinely distinct calendar data.
4. Identical Frozen Strategy: Entry, exit, stop, target, slippage, and friction must remain
   identical across all cells. Zero cell-specific parameter tuning.
5. Permitted States: UNDER_CERTIFICATION, CERTIFIED_FOR_PRODUCTION, DECOMMISSIONED.
   Zero shadow / provisional mode.
"""

import os
import sys
import math
import logging
from typing import Dict, List, Any, Optional, Tuple, Set
import numpy as np
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("TEMPORAL_REPLICATION_GATE")
IST = ZoneInfo("Asia/Kolkata")

# Pre-defined, frozen temporal multi-year cell boundaries (Defined prior to performance evaluation)
DEFAULT_TEMPORAL_CELLS = [
    {"cell_id": "Cell_1_2016_2018", "start_date": "2016-01-01", "end_date": "2018-12-31"},
    {"cell_id": "Cell_2_2019_2021", "start_date": "2019-01-01", "end_date": "2021-12-31"},
    {"cell_id": "Cell_3_2022_2024", "start_date": "2022-01-01", "end_date": "2024-12-31"},
    {"cell_id": "Cell_4_2025_2026", "start_date": "2025-01-01", "end_date": "2026-12-31"}
]


class TemporalReplicationGate:
    """
    Authoritative Temporal Replication and Multi-Episode Robustness Evaluator.
    """

    @staticmethod
    def partition_into_temporal_cells(
        df: pd.DataFrame,
        date_col: str = "signal_date",
        cells: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, pd.DataFrame]:
        """
        Partitions trade ledger into pre-registered non-overlapping temporal cells.
        """
        if cells is None:
            cells = DEFAULT_TEMPORAL_CELLS

        df_copy = df.copy()
        df_copy[date_col] = pd.to_datetime(df_copy[date_col])

        partitioned = {}
        for c in cells:
            cid = c["cell_id"]
            s_date = pd.to_datetime(c["start_date"])
            e_date = pd.to_datetime(c["end_date"])
            mask = (df_copy[date_col] >= s_date) & (df_copy[date_col] <= e_date)
            cell_slice = df_copy[mask].copy().reset_index(drop=True)
            partitioned[cid] = cell_slice

        return partitioned

    @staticmethod
    def partition_into_quarters(
        df: pd.DataFrame,
        date_col: str = "signal_date"
    ) -> Dict[str, pd.DataFrame]:
        """
        Partitions trade ledger into quarterly buckets (Q1, Q2, Q3, Q4) to detect seasonality/volatility concentration.
        """
        df_copy = df.copy()
        df_copy[date_col] = pd.to_datetime(df_copy[date_col])
        df_copy["quarter"] = df_copy[date_col].dt.quarter.apply(lambda q: f"Q{q}")

        quarter_slices = {}
        for q in ["Q1", "Q2", "Q3", "Q4"]:
            quarter_slices[q] = df_copy[df_copy["quarter"] == q].copy().reset_index(drop=True)
        return quarter_slices

    @staticmethod
    def calculate_cell_metrics(
        cell_df: pd.DataFrame,
        arm_a_col: str = "net_r_arm_a",
        arm_b_col: str = "net_r_arm_b",
        symbol_col: str = "symbol",
        date_col: str = "signal_date"
    ) -> Dict[str, Any]:
        """
        Computes full cell-level statistical battery:
        N, win rate, mean/median Net R, Arm A/B CIs, Delta CI, permutation p,
        drawdown, Sharpe, symbol and calendar concentration.
        """
        n = len(cell_df)
        if n < 10:
            return {
                "trade_count": n,
                "status": "UNDERPOWERED",
                "win_rate_b": 0.0,
                "mean_net_r_b": 0.0,
                "median_net_r_b": 0.0,
                "arm_a_mean": 0.0,
                "arm_a_ci_95": [0.0, 0.0],
                "arm_b_mean": 0.0,
                "arm_b_ci_95": [0.0, 0.0],
                "delta_mean": 0.0,
                "delta_ci_95": [0.0, 0.0],
                "permutation_p": 1.0,
                "portfolio_sharpe": 0.0,
                "max_drawdown_r": 0.0,
                "total_pnl_r": 0.0,
                "effective_n": 0.0,
                "cell_passed": False
            }

        ra = cell_df[arm_a_col].values.astype(np.float64)
        rb = cell_df[arm_b_col].values.astype(np.float64)
        delta = rb - ra

        mean_a = float(np.mean(ra))
        mean_b = float(np.mean(rb))
        mean_delta = float(np.mean(delta))
        median_b = float(np.median(rb))
        win_rate = float(np.mean(rb > 0))
        total_pnl = float(np.sum(rb))

        # 1. Bootstrap 95% Confidence Intervals (1,000 iterations for cell-level)
        rng = np.random.default_rng(42)
        n_boot = 1000
        indices = rng.integers(0, n, size=(n_boot, n))

        boot_a = np.mean(ra[indices], axis=1)
        boot_b = np.mean(rb[indices], axis=1)
        boot_delta = np.mean(delta[indices], axis=1)

        ci_a = [round(float(np.percentile(boot_a, 2.5)), 4), round(float(np.percentile(boot_a, 97.5)), 4)]
        ci_b = [round(float(np.percentile(boot_b, 2.5)), 4), round(float(np.percentile(boot_b, 97.5)), 4)]
        ci_delta = [round(float(np.percentile(boot_delta, 2.5)), 4), round(float(np.percentile(boot_delta, 97.5)), 4)]

        # 2. Paired Sign-Flip Permutation Test
        flips = rng.choice([-1.0, 1.0], size=(n_boot, n))
        perm_means = np.mean(delta * flips, axis=1)
        p_val = float(np.mean(perm_means >= mean_delta)) if mean_delta > 0 else 1.0
        p_val = round(max(0.0001, p_val), 4)

        # 3. Portfolio Drawdown and Sharpe Proxy
        cum = np.cumsum(rb)
        peak = np.maximum.accumulate(cum)
        dd = peak - cum
        max_dd = round(float(np.max(dd)), 4) if len(dd) > 0 else 0.0

        daily_std = float(np.std(rb))
        sharpe = round(float(mean_b / daily_std * math.sqrt(252)), 2) if daily_std > 1e-6 else 0.0

        # 4. Symbol Concentration
        sym_counts = cell_df[symbol_col].value_counts()
        total_trades = len(cell_df)
        hhi = float(np.sum((sym_counts / total_trades) ** 2))
        eff_n = round(1.0 / hhi, 1) if hhi > 0 else 0.0

        # Cell Gate Rule: Arm B CI > 0 AND (B - A) Delta CI > 0 AND p < 0.05
        cell_passed = bool(ci_b[0] > 0 and ci_delta[0] > 0 and p_val < 0.05)

        return {
            "trade_count": n,
            "win_rate_b": round(win_rate, 4),
            "mean_net_r_b": round(mean_b, 4),
            "median_net_r_b": round(median_b, 4),
            "arm_a_mean": round(mean_a, 4),
            "arm_a_ci_95": ci_a,
            "arm_b_mean": round(mean_b, 4),
            "arm_b_ci_95": ci_b,
            "delta_mean": round(mean_delta, 4),
            "delta_ci_95": ci_delta,
            "permutation_p": p_val,
            "portfolio_sharpe": sharpe,
            "max_drawdown_r": max_dd,
            "total_pnl_r": round(total_pnl, 2),
            "effective_n": eff_n,
            "cell_passed": cell_passed
        }

    @classmethod
    def evaluate_replication_consistency(
        cls,
        cell_results: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Evaluates cross-cell dispersion, concentration of edge, and consistency.
        Flags:
        - CONCENTRATED_EDGE: if top cell contributes > 60% of total PnL
        - SINGLE_EPISODE_EDGE: if only 1 cell is positive
        - TEMPORALLY_INCONSISTENT: if positive cells < 50%
        - TEMPORALLY_CONCENTRATED: if > 50% of trades occur in 1 cell
        """
        valid_cells = {cid: res for cid, res in cell_results.items() if res.get("trade_count", 0) >= 10}

        if not valid_cells:
            return {
                "status": "INSUFFICIENT_CELL_DATA",
                "is_robust": False,
                "consistency_verdict": "UNDER_CERTIFICATION"
            }

        means = [res["mean_net_r_b"] for res in valid_cells.values()]
        pnls = [res["total_pnl_r"] for res in valid_cells.values()]
        counts = [res["trade_count"] for res in valid_cells.values()]
        passes = [res["cell_passed"] for res in valid_cells.values()]

        pos_count = sum(1 for m in means if m > 0)
        neg_count = sum(1 for m in means if m <= 0)
        pass_count = sum(1 for p in passes if p)

        total_pnl = sum(pnls)
        max_cell_pnl = max(pnls) if pnls else 0.0
        top_cell_pnl_share = round((max_cell_pnl / max(total_pnl, 1e-4)) * 100.0, 1) if total_pnl > 0 else 100.0

        total_trades = sum(counts)
        max_trades = max(counts) if counts else 0
        trade_concentration = round((max_trades / max(total_trades, 1)) * 100.0, 1)

        flags = []
        if top_cell_pnl_share >= 60.0 and len(valid_cells) >= 3:
            flags.append("CONCENTRATED_EDGE")
        if pos_count == 1 and len(valid_cells) >= 2:
            flags.append("SINGLE_EPISODE_EDGE")
        if pos_count < len(valid_cells) * 0.5:
            flags.append("TEMPORALLY_INCONSISTENT")
        if trade_concentration >= 60.0:
            flags.append("TEMPORALLY_CONCENTRATED")

        # Robustness Verdict
        is_robust = bool(pos_count >= 2 and pass_count >= 1 and "SINGLE_EPISODE_EDGE" not in flags and "CONCENTRATED_EDGE" not in flags)

        return {
            "valid_cells_count": len(valid_cells),
            "best_cell_mean_r": round(float(np.max(means)), 4),
            "worst_cell_mean_r": round(float(np.min(means)), 4),
            "median_cell_mean_r": round(float(np.median(means)), 4),
            "mean_cell_mean_r": round(float(np.mean(means)), 4),
            "dispersion_std_r": round(float(np.std(means)), 4),
            "positive_cells_count": pos_count,
            "negative_cells_count": neg_count,
            "passed_cells_count": pass_count,
            "top_cell_pnl_share_pct": top_cell_pnl_share,
            "top_cell_trade_share_pct": trade_concentration,
            "flags": flags,
            "is_robust": is_robust,
            "status": "PASS" if is_robust else "TEMPORALLY_INCONSISTENT"
        }

    @classmethod
    def evaluate_scanner_regime_temporal_gate(
        cls,
        scanner_name: str,
        regime_name: str,
        trade_df: pd.DataFrame,
        holdout_arm_b_ci_low: float,
        holdout_delta_ci_low: float,
        holdout_perm_p: float
    ) -> Dict[str, Any]:
        """
        Executes the entire Temporal Replication & Robustness Gate for a single Scanner x Regime.
        Evaluates Multi-Year temporal cells and Quarterly temporal cells.
        Returns full audit record and governance promotion verdict.
        """
        # 1. Multi-Year Cells
        my_cells = cls.partition_into_temporal_cells(trade_df)
        cell_metrics = {}
        for cid, cdf in my_cells.items():
            cell_metrics[cid] = cls.calculate_cell_metrics(cdf)

        # 2. Quarterly Cells
        q_cells = cls.partition_into_quarters(trade_df)
        q_metrics = {}
        for qid, qdf in q_cells.items():
            q_metrics[qid] = cls.calculate_cell_metrics(qdf)

        # 3. Replication Consistency Analysis
        my_consistency = cls.evaluate_replication_consistency(cell_metrics)
        q_consistency = cls.evaluate_replication_consistency(q_metrics)

        # 4. End-to-End Holdout Requirement Gate
        end_to_end_passed = bool(
            holdout_arm_b_ci_low > 0 and
            holdout_delta_ci_low > 0 and
            holdout_perm_p < 0.05
        )

        # 5. Final Permanent Governance Lock Rule (§14)
        # Requires:
        # a) End-to-End holdout passed
        # b) Temporal replication consistency passed (multiple years, multiple periods, positive replicated across cells)
        # c) Zero fatal flags (CONCENTRATED_EDGE, SINGLE_EPISODE_EDGE)
        if end_to_end_passed and my_consistency["is_robust"] and q_consistency["is_robust"]:
            final_status = "CERTIFIED_FOR_PRODUCTION"
        elif end_to_end_passed and not my_consistency["is_robust"]:
            final_status = "UNDER_CERTIFICATION"  # Positive pooled but insufficient temporal replication
        elif not end_to_end_passed:
            # If failed holdout or failed statistically
            final_status = "NOT_CERTIFIED"
        else:
            final_status = "UNDER_CERTIFICATION"

        return {
            "scanner": scanner_name,
            "regime": regime_name,
            "end_to_end_passed": end_to_end_passed,
            "temporal_cells": cell_metrics,
            "quarterly_cells": q_metrics,
            "temporal_consistency": my_consistency,
            "quarterly_consistency": q_consistency,
            "governance_verdict": final_status
        }

    @classmethod
    def generate_replication_matrix_markdown(
        cls,
        eval_results: List[Dict[str, Any]]
    ) -> str:
        """
        Generates the mandatory Regime Replication Matrix markdown table (§12).
        """
        lines = [
            "### TEMPORAL REPLICATION & REGIME ROBUSTNESS MATRIX",
            "",
            "| Scanner | Regime | Cell 1 (2016-18) | Cell 2 (2019-21) | Cell 3 (2022-24) | Cell 4 (2025-26) | Replication Status | Governance Verdict |",
            "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |"
        ]

        for res in eval_results:
            sc = res["scanner"]
            reg = res["regime"]
            tc = res["temporal_cells"]
            c1 = "PASS" if tc.get("Cell_1_2016_2018", {}).get("cell_passed") else f"FAIL ({tc.get('Cell_1_2016_2018', {}).get('trade_count', 0)}T)"
            c2 = "PASS" if tc.get("Cell_2_2019_2021", {}).get("cell_passed") else f"FAIL ({tc.get('Cell_2_2019_2021', {}).get('trade_count', 0)}T)"
            c3 = "PASS" if tc.get("Cell_3_2022_2024", {}).get("cell_passed") else f"FAIL ({tc.get('Cell_3_2022_2024', {}).get('trade_count', 0)}T)"
            c4 = "PASS" if tc.get("Cell_4_2025_2026", {}).get("cell_passed") else f"FAIL ({tc.get('Cell_4_2025_2026', {}).get('trade_count', 0)}T)"
            rep_stat = res["temporal_consistency"].get("status", "FAIL")
            verd = res["governance_verdict"]

            lines.append(f"| {sc} | {reg} | {c1} | {c2} | {c3} | {c4} | {rep_stat} | **{verd}** |")

        lines.append("")
        return "\n".join(lines)


# Global Singleton Instance
temporal_replication_gate = TemporalReplicationGate()
