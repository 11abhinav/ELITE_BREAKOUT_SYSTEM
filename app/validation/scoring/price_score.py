import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from .base import BaseScoreCalculator
from ..result import ValidationResult
from config import QUALITY_SCORE_WEIGHTS

IST = ZoneInfo("Asia/Kolkata")

class PriceScoreCalculator(BaseScoreCalculator):
    """
    Computes quality scores (0-100) for OHLCV data that has passed critical validation.
    Penalizes for missing cells, staleness, duplicates, and invalid prices.
    """
    def __init__(self, period: str, interval: str, range_from: str = None, range_to: str = None):
        self.period = period
        self.interval = interval
        self.range_from = range_from
        self.range_to = range_to

    def _estimate_expected_rows(self) -> int:
        from data_quality import ExpectedRowEstimator # Reuse the existing estimator logic for now
        return ExpectedRowEstimator.estimate(self.period, self.interval, self.range_from, self.range_to)

    def calculate(self, df: pd.DataFrame, result: ValidationResult) -> int:
        w = QUALITY_SCORE_WEIGHTS
        score = 0.0
        
        rows = result.metrics.get("row_count", len(df))
        expected = self._estimate_expected_rows()
        missing_pct = result.metrics.get("missing_pct", 0.0)
        dupes = result.metrics.get("duplicate_rows", 0)
        monotonic = result.metrics.get("monotonic", True)
        invalid_prices = result.metrics.get("invalid_prices", 0)
        
        # Calculate Freshness
        time_col = 'Date' if 'Date' in df.columns else ('Datetime' if 'Datetime' in df.columns else None)
        time_series = df[time_col] if time_col else df.index
        
        freshness_days = 999
        now_dt = datetime.now(IST)
        try:
            if not time_series.empty:
                last_dt = pd.to_datetime(time_series[-1])
                if not pd.isna(last_dt):
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.tz_localize(IST)
                    else:
                        last_dt = last_dt.tz_convert(IST)
                    freshness_days = max(0, (now_dt.date() - last_dt.date()).days)
        except Exception:
            pass
            
        result.metrics["stale_days"] = freshness_days

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
        
        # Freshness Penalty Logic
        fresh_score = w["freshness"]
        if freshness_days > 0:
            is_weekend = now_dt.weekday() >= 5
            market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            
            if not is_weekend and now_dt > market_close and freshness_days >= 1:
                fresh_score -= 5 * freshness_days
            elif is_weekend and freshness_days > 2:
                fresh_score -= 5 * (freshness_days - 2)
                
        if freshness_days > 100:
            fresh_score -= 20
                
        score += max(0, fresh_score)
        
        # Global Scale Down for severe incompleteness
        if completeness < 0.10:
            score = score * completeness * 2.0
            
        return int(max(0.0, min(100.0, score)))
