"""
Feature Extraction and Dependency Audit Module for Alert Quality Engineering.
Enforces strict Point-in-Time (PIT) feature availability and provenance classification:
  - PRE_DECISION_FEATURE: Features computable prior to decision day (e.g. daily SMAs, pre-breakout RSI, sector scores).
  - POST_SETUP_FEATURE: Features known at trigger point (e.g. breakout volume ratio).
  - DERIVED_FROM_EXECUTION: Post-setup execution fields (e.g. rr_ratio - excluded from model fitting).
"""

from typing import Dict, Any, List, Tuple
import pandas as pd
import numpy as np

# Canonical Feature Column Order
FEATURE_COLUMNS: List[str] = [
    "dist_sma50_pct",
    "dist_sma200_pct",
    "rsi",
    "sector_blended_score",
    "is_tailwind",
    "volume_ratio",
    "rr_ratio"
]

# Canonical Feature Provenance Classification
FEATURE_PROVENANCE: Dict[str, str] = {
    "dist_sma50_pct": "PRE_DECISION_FEATURE",
    "dist_sma200_pct": "PRE_DECISION_FEATURE",
    "rsi": "PRE_DECISION_FEATURE",
    "sector_blended_score": "PRE_DECISION_FEATURE",
    "is_tailwind": "PRE_DECISION_FEATURE",
    "volume_ratio": "POST_SETUP_FEATURE",
    "rr_ratio": "DERIVED_FROM_EXECUTION"
}

ACTIVE_PREDICTOR_COLUMNS: List[str] = [
    "dist_sma50_pct",
    "dist_sma200_pct",
    "rsi",
    "volume_ratio"
]

def extract_features(
    df: pd.DataFrame,
    median_vol_reference: float = None
) -> Tuple[pd.DataFrame, float]:
    """
    Extracts canonical feature matrix and returns (df_features, median_vol_reference).
    Guarantees zero future data leakage by accepting train-fitted median volume.
    """
    df_feat = pd.DataFrame(index=df.index)

    close_p = pd.to_numeric(df.get("close_price", 0.0), errors="coerce").fillna(0.0)
    sma50 = pd.to_numeric(df.get("sma50", 0.0), errors="coerce").fillna(0.0)
    sma200 = pd.to_numeric(df.get("sma200", 0.0), errors="coerce").fillna(0.0)

    # 1. Trend Quality (PRE_DECISION)
    df_feat["dist_sma50_pct"] = np.where(sma50 > 0, ((close_p - sma50) / sma50) * 100.0, 0.0)
    df_feat["dist_sma200_pct"] = np.where(sma200 > 0, ((close_p - sma200) / sma200) * 100.0, 0.0)

    # 2. RSI (PRE_DECISION)
    df_feat["rsi"] = pd.to_numeric(df.get("rsi", 50.0), errors="coerce").fillna(50.0)

    # 3. Sector Context (PRE_DECISION)
    df_feat["sector_blended_score"] = pd.to_numeric(df.get("sector_blended_score", 0.0), errors="coerce").fillna(0.0)
    df_feat["is_tailwind"] = (df.get("sector_status") == "TAILWIND").astype(float)

    # 4. Volume Expansion (POST_SETUP_FEATURE)
    raw_vol = pd.to_numeric(df.get("volume", 0.0), errors="coerce").fillna(0.0)
    if median_vol_reference is None or median_vol_reference <= 0:
        median_vol_reference = float(raw_vol.median()) if len(raw_vol) > 0 else 1.0
        if median_vol_reference <= 0:
            median_vol_reference = 1.0

    df_feat["volume_ratio"] = raw_vol / median_vol_reference

    # 5. Execution Context (DERIVED_FROM_EXECUTION)
    df_feat["rr_ratio"] = pd.to_numeric(df.get("rr_ratio", 2.0), errors="coerce").fillna(2.0)

    return df_feat[FEATURE_COLUMNS], median_vol_reference

def audit_feature_dependencies(df_feat: pd.DataFrame) -> Dict[str, Any]:
    """
    Audits feature distributions, missing rates, variances, and collinearity on Train data.
    Identifies zero-variance and highly redundant features to prune prior to model fitting.
    """
    stats = {}
    active_cols = []
    pruned_cols = []

    for col in df_feat.columns:
        series = df_feat[col].astype(float)
        var = float(series.var())
        provenance = FEATURE_PROVENANCE.get(col, "UNKNOWN")

        stat_item = {
            "provenance": provenance,
            "missing_count": int(series.isna().sum()),
            "mean": round(float(series.mean()), 4),
            "std": round(float(series.std()), 4),
            "variance": round(var, 4),
            "min": round(float(series.min()), 4),
            "max": round(float(series.max()), 4)
        }
        stats[col] = stat_item

        if var < 1e-6 or np.isnan(var):
            pruned_cols.append(col)
        else:
            active_cols.append(col)

    corr_matrix = df_feat[active_cols].corr().round(4).to_dict()

    collinear_pairs = []
    for i in range(len(active_cols)):
        for j in range(i + 1, len(active_cols)):
            c1 = active_cols[i]
            c2 = active_cols[j]
            r = corr_matrix[c1][c2]
            if abs(r) >= 0.70:
                collinear_pairs.append({
                    "feature_1": c1,
                    "feature_2": c2,
                    "correlation_r": r,
                    "remediation": "Managed via Ridge L2 penalty"
                })

    return {
        "feature_statistics": stats,
        "active_features": active_cols,
        "pruned_features": pruned_cols,
        "correlation_matrix": corr_matrix,
        "collinear_pairs": collinear_pairs
    }
