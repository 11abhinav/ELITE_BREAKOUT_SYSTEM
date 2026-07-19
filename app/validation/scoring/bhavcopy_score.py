from .base import BaseScoreCalculator
from ..result import ValidationResult
from ..context import ValidationContext

class BhavcopyScoreCalculator(BaseScoreCalculator):
    """
    Calculates the fidelity score of a Bhavcopy dataset.
    Bhavcopy data from the exchange is typically very high fidelity.
    Penalties are applied for missing data or minor inconsistencies.
    """
    def calculate(self, result: ValidationResult, context: ValidationContext = None) -> float:
        score = 100.0

        if not result.is_valid:
            return 0.0

        # Penalize for missing cells (e.g. 5% missing drops score by 10 points)
        if result.metrics.missing_pct > 0:
            penalty = result.metrics.missing_pct * 2.0
            score -= penalty

        # Penalize for invalid prices that didn't trigger a critical failure
        if result.metrics.row_count > 0 and result.metrics.invalid_prices > 0:
            invalid_pct = (result.metrics.invalid_prices / result.metrics.row_count) * 100
            score -= (invalid_pct * 5.0)  # Heavy penalty for any bad prices

        # Hard boundary
        return max(0.0, min(100.0, score))
