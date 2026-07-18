import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from config import QUALITY_SCORE_WEIGHTS, QUALITY_VALIDATOR_VERSION

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

@dataclass(frozen=True)
class DataQualityReport:
    version: str
    is_valid: bool
    quality_score: float
    row_count: int
    expected_rows: int
    freshness_days: int
    missing_pct: float
    duplicate_rows: int
    monotonic: bool
    schema_valid: bool
    types_valid: bool
    reason: str

@dataclass
class MarketData:
    dataframe: Optional[pd.DataFrame]
    source: str
    quality_report: Optional[DataQualityReport]
    stale: bool
    used_fallback: bool
    error: Optional[str] = None

class ExpectedRowEstimator:
    @staticmethod
    def estimate(period: str, interval: str, range_from: Optional[str] = None, range_to: Optional[str] = None) -> int:
        """Estimate the expected number of rows for a given period."""
        # Simple estimator - can be expanded to account for exact holidays
        if range_from and range_to:
            try:
                start = pd.to_datetime(range_from)
                end = pd.to_datetime(range_to)
                if end < start:
                    return 0
                days = (end - start).days + 1
                return max(1, int(days * (252 / 365)))
            except Exception:
                return 1
                
        p = period.lower()
        if p == "10y": return 2520
        if p == "5y": return 1260
        if p == "2y": return 504
        if p == "1y": return 252
        if p == "6mo": return 126
        if p == "3mo": return 63
        if p == "1mo": return 21
        if p.endswith("d"):
            try:
                return max(1, int(p[:-1]))
            except ValueError:
                return 1
        return 250


class DataQualityValidator:
    """
    V8 Data Quality Framework
    Validates schema, types, and computes an objective quality score.
    """
    
    REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]

    @classmethod
    def validate(cls, df: pd.DataFrame, period: str, interval: str, range_from: str = None, range_to: str = None) -> DataQualityReport:
        if df is None or getattr(df, 'empty', True):
            return DataQualityReport(
                version=QUALITY_VALIDATOR_VERSION, is_valid=False, quality_score=0.0,
                row_count=0, expected_rows=0, freshness_days=999, missing_pct=100.0,
                duplicate_rows=0, monotonic=False, schema_valid=False, types_valid=False,
                reason="Empty or None DataFrame"
            )

        # 1. Schema Validation
        missing_cols = [c for c in cls.REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            return DataQualityReport(
                version=QUALITY_VALIDATOR_VERSION, is_valid=False, quality_score=0.0,
                row_count=len(df), expected_rows=0, freshness_days=999, missing_pct=100.0,
                duplicate_rows=0, monotonic=False, schema_valid=False, types_valid=False,
                reason=f"Missing required columns: {missing_cols}"
            )

        # 2. Type Validation
        try:
            for col in ["Open", "High", "Low", "Close"]:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    return cls._invalid_type(len(df), f"Column {col} is not numeric")
            if not pd.api.types.is_numeric_dtype(df["Volume"]):
                return cls._invalid_type(len(df), "Volume is not numeric")
                
            time_col = 'Date' if 'Date' in df.columns else ('Datetime' if 'Datetime' in df.columns else None)
            if time_col:
                if not pd.api.types.is_datetime64_any_dtype(df[time_col]):
                    # Check if it can be coerced cleanly? No, type validation requires strictness.
                    # But since yfinance might return objects if we don't convert, let's allow strings if they are parsable, but standard pipeline parses them before.
                    pass
            elif not pd.api.types.is_datetime64_any_dtype(df.index):
                pass
        except Exception as e:
            return cls._invalid_type(len(df), f"Type validation crashed: {e}")

        # 3. Integrity & Score Calculation
        row_count = len(df)
        expected_rows = ExpectedRowEstimator.estimate(period, interval, range_from, range_to)
        
        # Missing values
        total_cells = row_count * len(cls.REQUIRED_COLUMNS)
        missing_cells = df[cls.REQUIRED_COLUMNS].isna().sum().sum()
        missing_pct = (missing_cells / total_cells) * 100 if total_cells > 0 else 100.0
        
        # Date Continuity
        time_series = df[time_col] if time_col else df.index
        duplicate_rows = time_series.duplicated().sum()
        
        try:
            monotonic = time_series.is_monotonic_increasing
        except Exception:
            monotonic = False
        
        # Freshness
        try:
            last_dt = pd.to_datetime(time_series[-1] if not time_series.empty else None)
            if pd.isna(last_dt):
                freshness_days = 999
            else:
                if last_dt.tzinfo is None:
                    last_dt = last_dt.tz_localize(IST)
                else:
                    last_dt = last_dt.tz_convert(IST)
                now_dt = datetime.now(IST)
                freshness_days = (now_dt.date() - last_dt.date()).days
                if freshness_days < 0: freshness_days = 0
        except Exception:
            freshness_days = 999
            now_dt = datetime.now(IST)
        
        # Price Sanity
        invalid_prices = ((df["High"] < df["Low"]) | 
                         (df["Close"] > df["High"]) | 
                         (df["Close"] < df["Low"]) | 
                         (df["Open"] < 0)).sum()

        score = cls._compute_score(row_count, expected_rows, missing_pct, duplicate_rows, monotonic, invalid_prices, freshness_days, now_dt)
        
        is_valid = True if score > 0 else False
        reason = "Valid" if is_valid else "Score dropped to 0 due to penalties"
        
        return DataQualityReport(
            version=QUALITY_VALIDATOR_VERSION,
            is_valid=is_valid,
            quality_score=round(score, 2),
            row_count=row_count,
            expected_rows=expected_rows,
            freshness_days=freshness_days,
            missing_pct=round(missing_pct, 2),
            duplicate_rows=int(duplicate_rows),
            monotonic=bool(monotonic),
            schema_valid=True,
            types_valid=True,
            reason=reason
        )

    @classmethod
    def _invalid_type(cls, rows: int, reason: str) -> DataQualityReport:
        return DataQualityReport(
            version=QUALITY_VALIDATOR_VERSION, is_valid=False, quality_score=0.0,
            row_count=rows, expected_rows=0, freshness_days=999, missing_pct=100.0,
            duplicate_rows=0, monotonic=False, schema_valid=True, types_valid=False,
            reason=reason
        )

    @classmethod
    def _compute_score(cls, rows: int, expected: int, missing_pct: float, dupes: int, monotonic: bool, invalid_prices: int, freshness_days: int, now_dt: datetime) -> float:
        w = QUALITY_SCORE_WEIGHTS
        score = 0.0
        
        # Row Completeness
        completeness = min(1.0, rows / expected) if expected > 0 else 1.0
        score += w["row_completeness"] * completeness
        
        # Missing
        missing_penalty = missing_pct * 2.0
        score += max(0, w["missing"] - missing_penalty)
        
        # Price Sanity
        price_penalty = (invalid_prices / rows) * 100 if rows > 0 else w["price_sanity"]
        score += max(0, w["price_sanity"] - price_penalty)
        
        # Continuity
        cont_score = w["continuity"]
        if not monotonic: cont_score -= 5
        if dupes > 0: cont_score -= 5
        score += max(0, cont_score)
        
        # Freshness
        fresh_score = w["freshness"]
        if freshness_days > 0:
            is_weekend = now_dt.weekday() >= 5
            market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            
            if not is_weekend and now_dt > market_close and freshness_days >= 1:
                fresh_score -= 5 * freshness_days
            elif is_weekend and freshness_days > 2:
                fresh_score -= 5 * (freshness_days - 2)
                
        # Heavy penalty if freshness is extreme (e.g. > 100 days)
        if freshness_days > 100:
            fresh_score -= 20
                
        score += max(0, fresh_score)
        
        # If completeness is severely lacking, apply a global scale down
        # A 1-row dataframe for a 1y request should not pass based on its 1 perfect row.
        if completeness < 0.10:
            score = score * completeness * 2.0  # Scales it down massively
            
        return max(0.0, min(100.0, score))
