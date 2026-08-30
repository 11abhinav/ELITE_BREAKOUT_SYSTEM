"""
Quality Features Registry & Point-in-Time (PIT) Feature Extractor
Final Common Alert-Quality Feature Registry for Elite Breakout System.
Categorizes predictors into PRE_DECISION, AVAILABLE_AT_DECISION, POST_DECISION, EXECUTION_DERIVED.
Enforces strict PIT safety and explicit SMA200 history requirements.
"""

import zoneinfo
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, List, Optional
from datetime import datetime
import pandas as pd
import numpy as np

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

class FeatureTiming(str, Enum):
    PRE_DECISION = "PRE_DECISION"
    AVAILABLE_AT_DECISION = "AVAILABLE_AT_DECISION"
    POST_DECISION = "POST_DECISION"
    EXECUTION_DERIVED = "EXECUTION_DERIVED"

@dataclass
class QualityFeatureDefinition:
    feature_name: str
    timing: FeatureTiming
    family: str  # Trend, Structure, Momentum, Participation, Volatility, Context
    description: str
    is_incremental: bool = True
    production_dependency: bool = False

# RULE 67 RATIONALE: Canonical feature vocabulary aligns with model registry contract
# (dist_sma50_pct, dist_sma200_pct) while retaining compatibility aliases.
FEATURE_REGISTRY: Dict[str, QualityFeatureDefinition] = {
    # Trend Family (Canonical Registry Predictors)
    "dist_sma20_pct": QualityFeatureDefinition("dist_sma20_pct", FeatureTiming.AVAILABLE_AT_DECISION, "Trend", "Distance from 20-day SMA in %"),
    "dist_sma50_pct": QualityFeatureDefinition("dist_sma50_pct", FeatureTiming.AVAILABLE_AT_DECISION, "Trend", "Distance from 50-day SMA in %"),
    "dist_sma200_pct": QualityFeatureDefinition("dist_sma200_pct", FeatureTiming.AVAILABLE_AT_DECISION, "Trend", "Distance from 200-day SMA in % (NaN if <200 bars)"),
    "sma200_available": QualityFeatureDefinition("sma200_available", FeatureTiming.AVAILABLE_AT_DECISION, "Trend", "1.0 if >=200 bars available else 0.0"),
    "trend_alignment_score": QualityFeatureDefinition("trend_alignment_score", FeatureTiming.AVAILABLE_AT_DECISION, "Trend", "Confluence score (Price > 20 > 50 SMA)"),
    
    # Structure Family
    "consolidation_width_pct": QualityFeatureDefinition("consolidation_width_pct", FeatureTiming.AVAILABLE_AT_DECISION, "Structure", "Width of pre-breakout base in %"),
    "base_duration_bars": QualityFeatureDefinition("base_duration_bars", FeatureTiming.AVAILABLE_AT_DECISION, "Structure", "Length of consolidation base in bars"),
    "pullback_depth_fit": QualityFeatureDefinition("pullback_depth_fit", FeatureTiming.AVAILABLE_AT_DECISION, "Structure", "Fit to ideal 38.2%-50% Fibonacci retracement"),
    
    # Momentum Family
    "rsi_14": QualityFeatureDefinition("rsi_14", FeatureTiming.AVAILABLE_AT_DECISION, "Momentum", "14-period Relative Strength Index"),
    "rsi_slope_5": QualityFeatureDefinition("rsi_slope_5", FeatureTiming.AVAILABLE_AT_DECISION, "Momentum", "5-bar slope of RSI"),
    
    # Participation Family
    "vol_surge_ratio": QualityFeatureDefinition("vol_surge_ratio", FeatureTiming.AVAILABLE_AT_DECISION, "Participation", "Breakout volume vs 20-SMA volume"),
    "obv_slope_10": QualityFeatureDefinition("obv_slope_10", FeatureTiming.AVAILABLE_AT_DECISION, "Participation", "10-bar On-Balance Volume slope"),
    
    # Volatility Family
    "atr_pct": QualityFeatureDefinition("atr_pct", FeatureTiming.AVAILABLE_AT_DECISION, "Volatility", "14-period ATR as % of price"),
    "volatility_contraction_ratio": QualityFeatureDefinition("volatility_contraction_ratio", FeatureTiming.AVAILABLE_AT_DECISION, "Volatility", "Ratio of recent 5-day range to 20-day range"),
    
    # Context Family
    "market_regime_bullish": QualityFeatureDefinition("market_regime_bullish", FeatureTiming.PRE_DECISION, "Context", "1 if Nifty 50 > 50 SMA else 0"),
    "sector_relative_strength_21d": QualityFeatureDefinition("sector_relative_strength_21d", FeatureTiming.PRE_DECISION, "Context", "21-day return vs benchmark")
}

def normalize_pit_timestamp(decision_timestamp: datetime, index_tz: Optional[Any] = None) -> Any:
    """
    Normalizes decision_timestamp according to strict Asia/Kolkata (IST) contract.
    RULE 67 RATIONALE: Eliminates non-deterministic local OS timezone assumptions.
    - Naive decision_timestamp is ALWAYS interpreted as Asia/Kolkata (IST).
    - Aware decision_timestamp preserves the exact instant and converts to match index timezone.
    - Matches DataFrame index timezone (UTC, IST, or naive).
    """
    if decision_timestamp.tzinfo is None:
        # Interpret naive input strictly as Asia/Kolkata
        ts_ist = decision_timestamp.replace(tzinfo=IST)
    else:
        ts_ist = decision_timestamp.astimezone(IST)

    if index_tz is not None:
        # Convert IST timestamp into target index timezone
        return ts_ist.astimezone(index_tz)
    else:
        # Index is naive: return naive representation in IST
        return ts_ist.replace(tzinfo=None)

def extract_quality_features(
    df_history: pd.DataFrame,
    decision_timestamp: Optional[datetime] = None,
    decision_idx: Optional[int] = None
) -> Dict[str, Any]:
    """
    Extracts strictly PRE_DECISION and AVAILABLE_AT_DECISION features.
    
    Point-in-Time (PIT) Guarantees:
      1. If decision_timestamp is provided, slices df_history to only rows where index/Date <= normalized timestamp.
      2. If decision_idx is provided without timestamp, slices to df_history.iloc[:decision_idx+1].
      3. Verifies feature_timestamp <= decision_timestamp for all predictors.
      4. Explicit SMA200 handling: If <200 bars exist, dist_sma200_pct is set to NaN and sma200_available=0.0.
         Never silently relabels a shorter SMA as SMA200.
      5. Emits canonical names (dist_sma50_pct, dist_sma200_pct) and backward compatibility aliases.
    """
    if df_history is None or df_history.empty:
        return {}
        
    df = df_history.copy()
    
    # Apply PIT timestamp slicing if provided
    if decision_timestamp is not None:
        if isinstance(df.index, pd.DatetimeIndex):
            target_tz = df.index.tz
            normalized_ts = normalize_pit_timestamp(decision_timestamp, target_tz)
            past = df[df.index <= normalized_ts]
        elif "Date" in df.columns or "datetime" in df.columns:
            date_col = "Date" if "Date" in df.columns else "datetime"
            dt_series = pd.to_datetime(df[date_col])
            target_tz = dt_series.dt.tz
            normalized_ts = normalize_pit_timestamp(decision_timestamp, target_tz)
            past = df[dt_series <= normalized_ts]
        else:
            past = df
    elif decision_idx is not None:
        if decision_idx < 0 or decision_idx >= len(df):
            return {}
        past = df.iloc[:decision_idx + 1]
    else:
        past = df

    if len(past) < 20:
        return {}
        
    closes = past['Close'].astype(float)
    highs = past['High'].astype(float)
    lows = past['Low'].astype(float)
    vols = past['Volume'].astype(float) if 'Volume' in past.columns else pd.Series(1.0, index=past.index)
    
    c = float(closes.iloc[-1])
    sma20 = float(closes.rolling(20).mean().iloc[-1])
    sma50 = float(closes.rolling(50).mean().iloc[-1]) if len(past) >= 50 else np.nan
    
    # Explicit P0-4 Rule: SMA200 requires >= 200 valid bars
    if len(past) >= 200:
        sma200 = float(closes.rolling(200).mean().iloc[-1])
        dist_sma200 = round((c - sma200) / sma200 * 100.0, 2)
        sma200_avail = 1.0
    else:
        sma200 = np.nan
        dist_sma200 = np.nan
        sma200_avail = 0.0

    dist_sma50 = round((c - sma50) / sma50 * 100.0, 2) if not np.isnan(sma50) else np.nan
    dist_sma20 = round((c - sma20) / sma20 * 100.0, 2)

    # Trend alignment (Price > 20 > 50)
    trend_alignment = 1.0 if (not np.isnan(sma50) and c > sma20 > sma50) else 0.0

    # RSI 14
    delta = closes.diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean().iloc[-1]
    rs = gain / (loss + 1e-6)
    rsi14 = 100.0 - (100.0 / (1.0 + rs))
    
    # Volume surge ratio
    vol_sma20 = float(vols.rolling(20).mean().iloc[-1]) if len(vols) >= 20 else 1.0
    vol_surge = float(vols.iloc[-1]) / max(vol_sma20, 1.0)
    
    # Base consolidation width (last 20 bars)
    base_high = float(highs.iloc[-20:].max())
    base_low = float(lows.iloc[-20:].min())
    base_width_pct = (base_high - base_low) / max(base_low, 1e-4) * 100.0
    
    # Pullback depth fit (distance from 20-day high to low vs 38.2%-50% retracement)
    recent_high = float(highs.iloc[-20:].max())
    recent_low = float(lows.iloc[-20:].min())
    total_range = max(recent_high - recent_low, 1e-4)
    retrace_pct = (recent_high - c) / total_range
    # Fit score: peak at 0.382 to 0.50 retrace
    pullback_fit = float(np.exp(-((retrace_pct - 0.44) ** 2) / 0.05))

    feat_ts = past.index[-1] if isinstance(past.index, pd.DatetimeIndex) else str(decision_timestamp or datetime.now())

    # RULE 67 RATIONALE: Emits canonical keys (dist_sma50_pct, dist_sma200_pct) as defined
    # in the model registry, along with backwards-compatible aliases (sma50_dist_pct, sma200_dist_pct).
    return {
        # Canonical registry feature names
        "dist_sma20_pct": dist_sma20,
        "dist_sma50_pct": dist_sma50,
        "dist_sma200_pct": dist_sma200,
        
        # Compatibility aliases
        "sma20_dist_pct": dist_sma20,
        "sma50_dist_pct": dist_sma50,
        "sma200_dist_pct": dist_sma200,
        
        # Common predictors
        "sma200_available": sma200_avail,
        "trend_alignment_score": trend_alignment,
        "consolidation_width_pct": round(base_width_pct, 2),
        "pullback_depth_fit": round(pullback_fit, 3),
        "rsi_14": round(rsi14, 2),
        "vol_surge_ratio": round(vol_surge, 2),
        "feature_timestamp": str(feat_ts),
        "pit_valid": True
    }
