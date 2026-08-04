from datetime import datetime
from zoneinfo import ZoneInfo
from .base import BaseScoreCalculator
from ..result import ValidationResult
from ..context import ValidationContext
from config import QUALITY_SCORE_WEIGHTS

IST = ZoneInfo("Asia/Kolkata")

class PriceScoreCalculator(BaseScoreCalculator):
    """
    Computes quality scores (0-100) for OHLCV data that has passed critical validation.
    Penalizes for missing cells, staleness, duplicates, and invalid prices.
    Calculates purely based on validated metrics, entirely decoupled from the dataframe.
    """
    def _estimate_expected_rows(self, period: str, interval: str, range_from: str = None, range_to: str = None) -> int:
        from data_quality import ExpectedRowEstimator
        return ExpectedRowEstimator.estimate(period, interval, range_from, range_to)

    def calculate(self, result: ValidationResult, context: ValidationContext) -> int:
        w = QUALITY_SCORE_WEIGHTS
        score = 0.0
        
        rows = result.metrics.row_count
        expected = self._estimate_expected_rows(context.period, context.interval, context.range_from, context.range_to)
        
        missing_pct = result.metrics.missing_pct
        dupes = result.metrics.duplicate_rows
        monotonic = result.metrics.monotonic
        invalid_prices = result.metrics.invalid_prices
        freshness_days = result.metrics.stale_days

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
            now_dt = datetime.now(IST)
            is_weekend = now_dt.weekday() >= 5
            market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
            
            if not is_weekend and now_dt > market_close and freshness_days >= 1:
                fresh_score -= 5 * freshness_days
            elif is_weekend and freshness_days > 2:
                fresh_score -= 5 * (freshness_days - 2)
                
        if freshness_days > 100:
            fresh_score -= 20
                
        score += max(0, fresh_score)
        
        # Global Scale Down for severe incompleteness (Only for FULL period fetches, not DELTA fetches)
        is_delta = (context.fetch_mode == "DELTA") or bool(context.range_from)
        if not is_delta and completeness < 0.10:
            score = score * completeness * 2.0
            
        return int(max(0.0, min(100.0, score)))

