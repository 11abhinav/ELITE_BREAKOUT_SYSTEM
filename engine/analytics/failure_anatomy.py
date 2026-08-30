"""
Failure Anatomy Engine
Final Failure-Anatomy Engine for Elite Breakout System.
Ranks failure modes by Economic Impact = Failure Frequency * Economic Loss.
Computes 2x2 Matrix: (High Quality Win, High Quality Loss, Low Quality Win, Low Quality Loss).
"""

from dataclasses import dataclass
from typing import List, Dict, Any
import pandas as pd
import numpy as np

@dataclass
class FailureModeSummary:
    scanner: str
    total_alerts: int
    failures_count: int
    failure_frequency_pct: float
    mean_failure_loss_r: float
    total_economic_impact_r: float
    mean_failure_mae_r: float
    mean_failure_mfe_r: float

def compute_failure_anatomy(df_outcomes: pd.DataFrame, scanner_name: str) -> FailureModeSummary:
    """
    Analyzes failure anatomy and computes economic impact.
    Expects 'net_r', 'mae_r', 'mfe_r' in df_outcomes.
    """
    total = len(df_outcomes)
    if total == 0:
        return FailureModeSummary(scanner_name, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0)
    
    failures = df_outcomes[df_outcomes['net_r'] < 0]
    f_count = len(failures)
    f_freq = (f_count / total) * 100.0
    mean_loss = float(failures['net_r'].mean()) if f_count > 0 else 0.0
    econ_impact = f_count * abs(mean_loss)
    mean_mae = float(failures['mae_r'].mean()) if ('mae_r' in failures and f_count > 0) else 0.0
    mean_mfe = float(failures['mfe_r'].mean()) if ('mfe_r' in failures and f_count > 0) else 0.0
    
    return FailureModeSummary(
        scanner=scanner_name,
        total_alerts=total,
        failures_count=f_count,
        failure_frequency_pct=round(f_freq, 2),
        mean_failure_loss_r=round(mean_loss, 3),
        total_economic_impact_r=round(econ_impact, 2),
        mean_failure_mae_r=round(mean_mae, 3),
        mean_failure_mfe_r=round(mean_mfe, 3)
    )

def compute_2x2_matrix(df_outcomes: pd.DataFrame, score_col: str = 'aqs_score', score_cutoff: float = 50.0) -> Dict[str, Any]:
    """
    Computes mandatory 2x2 Quality-Outcome Matrix:
    - High Quality + Win
    - High Quality + Loss
    - Low Quality + Win
    - Low Quality + Loss
    """
    if df_outcomes.empty or score_col not in df_outcomes.columns:
        return {}
    
    hq = df_outcomes[df_outcomes[score_col] >= score_cutoff]
    lq = df_outcomes[df_outcomes[score_col] < score_cutoff]
    
    hq_win = hq[hq['net_r'] > 0]
    hq_loss = hq[hq['net_r'] <= 0]
    lq_win = lq[lq['net_r'] > 0]
    lq_loss = lq[lq['net_r'] <= 0]
    
    total_winners = len(df_outcomes[df_outcomes['net_r'] > 0])
    total_losers = len(df_outcomes[df_outcomes['net_r'] <= 0])
    
    winner_retention_pct = (len(hq_win) / max(total_winners, 1)) * 100.0
    loser_recall_pct = (len(lq_loss) / max(total_losers, 1)) * 100.0
    
    return {
        "hq_win_count": len(hq_win),
        "hq_loss_count": len(hq_loss),
        "lq_win_count": len(lq_win),
        "lq_loss_count": len(lq_loss),
        "winner_retention_pct": round(winner_retention_pct, 2),
        "loser_recall_pct": round(loser_recall_pct, 2),
        "hq_net_er": round(float(hq['net_r'].mean()), 3) if len(hq) > 0 else 0.0,
        "lq_net_er": round(float(lq['net_r'].mean()), 3) if len(lq) > 0 else 0.0
    }
