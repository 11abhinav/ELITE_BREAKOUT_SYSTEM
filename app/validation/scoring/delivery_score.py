from .base import BaseScoreCalculator
from ..result import ValidationResult

class DeliveryScoreCalculator(BaseScoreCalculator):
    """
    Calculates the fidelity score of a historical Delivery dataset.
     Penalties applied for missing cells or minor inconsistencies.
    """
    def calculate(self, result: ValidationResult) -> float:
        score = 100.0

        if not result.is_valid:
            return 0.0

        if result.metrics.missing_pct > 0:
            penalty = result.metrics.missing_pct * 2.0
            score -= penalty

        if result.metrics.row_count > 0 and result.metrics.invalid_prices > 0:
            invalid_pct = (result.metrics.invalid_prices / result.metrics.row_count) * 100
            score -= (invalid_pct * 5.0)

        return max(0.0, min(100.0, score))
