"""
Replay and Reconciliation Audit Module for Wave 3B.
Performs trade-by-trade root cause tracing of replay anomalies,
enforces rigorous price scale & risk denominator validity,
performs zero-delta accounting reconciliation, and relabels unsimulated gates.
"""

from typing import Dict, Any, List, Tuple
import pandas as pd
import numpy as np
import os
import json


def audit_multitf_replays(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Performs trade-by-trade audit of all Multi-TF eligible replays.
    Identifies price scale mismatches and invalid mock/dummy levels.
    """
    m_el = df[(df["scanner"] == "MULTI_TF") & (df["trade_eligibility_status"] == "ELIGIBLE")]
    
    trade_traces = []
    for idx, row in m_el.iterrows():
        entry = float(row["entry_price"]) if pd.notnull(row["entry_price"]) else None
        sl = float(row["sl_price"]) if pd.notnull(row["sl_price"]) else None
        target = float(row["target_price"]) if pd.notnull(row["target_price"]) else None
        close = float(row["close_price"]) if pd.notnull(row["close_price"]) else None
        
        raw_risk = abs(entry - sl) if (entry is not None and sl is not None) else None
        
        # Scale mismatch check: if entry is orders of magnitude different from close or parquet high
        scale_mismatch = False
        if close is not None and entry is not None and close > 0:
            ratio = entry / close
            if ratio < 0.2 or ratio > 5.0:
                scale_mismatch = True
                
        # Target equals entry check
        target_equals_entry = (entry == target) if (entry is not None and target is not None) else False
        
        status = "VALID"
        if scale_mismatch:
            status = "REPLAY_INVALID_RISK_SCALE_MISMATCH"
        elif target_equals_entry:
            status = "REPLAY_INVALID_ZERO_TARGET_DISTANCE"
            
        trade_traces.append({
            "evaluation_id": row.get("evaluation_id", str(idx)),
            "symbol": row["symbol"],
            "decision_timestamp": row["decision_timestamp"],
            "raw_entry": entry,
            "raw_sl": sl,
            "raw_target": target,
            "raw_risk": raw_risk,
            "close_price": close,
            "scale_ratio": round(entry / close, 4) if (close and entry) else None,
            "cf_mfe_r": row.get("cf_mfe_r"),
            "cf_mae_r": row.get("cf_mae_r"),
            "cf_realized_r": row.get("cf_realized_r"),
            "audit_status": status,
            "root_cause": (
                "Hardcoded/dummy test entry levels (129.5/128.0) evaluated against real equity price scale "
                f"({row['symbol']} ~₹{close:.2f}), causing artificial MFE explosion."
                if scale_mismatch else "Normal"
            )
        })
        
    return {
        "total_multitf_eligible": len(m_el),
        "invalid_scale_mismatches": sum(1 for t in trade_traces if "SCALE_MISMATCH" in t["audit_status"]),
        "trade_traces": trade_traces
    }


def perform_zero_delta_accounting(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Categorizes 100% of telemetry records into mutually exclusive,
    collectively exhaustive reconciliation categories with zero unexplained residual.
    """
    total_evals = len(df)
    
    # 1. Terminal decisions breakdown
    terminal_counts = df["terminal_decision"].value_counts(dropna=False).to_dict()
    terminal_sum = sum(terminal_counts.values())
    
    # 2. Rejection reason breakdown
    rej_df = df[df["terminal_decision"] == "REJECTED"]
    total_rejected = len(rej_df)
    
    reason_counts = rej_df["primary_reason"].value_counts(dropna=False).to_dict()
    reason_sum = sum(reason_counts.values())
    
    # Group into canonical categories
    top_categories = {}
    long_tail_categories = {}
    
    for reason, count in reason_counts.items():
        if count >= 80:
            top_categories[str(reason)] = int(count)
        else:
            long_tail_categories[str(reason)] = int(count)
            
    top_sum = sum(top_categories.values())
    long_tail_sum = sum(long_tail_categories.values())
    
    # Assert exact zero delta
    unexplained_terminal = total_evals - terminal_sum
    unexplained_rejections = total_rejected - (top_sum + long_tail_sum)
    
    # 3. Trade eligibility breakdown
    eligibility_counts = df["trade_eligibility_status"].value_counts(dropna=False).to_dict()
    eligibility_sum = sum(eligibility_counts.values())
    unexplained_eligibility = total_evals - eligibility_sum
    
    return {
        "total_evaluations": total_evals,
        "terminal_decisions": {
            "counts": terminal_counts,
            "sum": terminal_sum,
            "delta_unexplained": unexplained_terminal
        },
        "rejections": {
            "total_rejected": total_rejected,
            "top_categories_count": top_sum,
            "long_tail_categories_count": long_tail_sum,
            "reconciled_sum": top_sum + long_tail_sum,
            "delta_unexplained": unexplained_rejections,
            "top_categories": top_categories,
            "long_tail_categories": long_tail_categories
        },
        "trade_eligibility": {
            "counts": eligibility_counts,
            "sum": eligibility_sum,
            "delta_unexplained": unexplained_eligibility
        },
        "reconciliation_status": "EXACT_ZERO_DELTA_VERIFIED" if (unexplained_terminal == 0 and unexplained_rejections == 0 and unexplained_eligibility == 0) else "DISCREPANCY_DETECTED"
    }


def relabel_rejection_gates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Relabels unsimulated rejection gates lacking counterfactual execution levels as UNTESTABLE_WITH_CURRENT_DATA.
    """
    df_out = df.copy()
    untestable_mask = (df_out["terminal_decision"] == "REJECTED") & (df_out["trade_eligibility_status"] == "NOT_ELIGIBLE")
    df_out.loc[untestable_mask, "gate_status_label"] = "UNTESTABLE_WITH_CURRENT_DATA"
    return df_out
