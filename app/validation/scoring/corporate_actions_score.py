from .base import BaseScoreCalculator
from ..result import ValidationResult

class CorporateActionsScoreCalculator(BaseScoreCalculator):
    """
    Calculates the fidelity score of a Corporate Actions dataset.
    Penalizes for minor degradation like missing optional fields.
    """
    def calculate(self, result: ValidationResult) -> float:
        score = 100.0

        if not result.is_valid:
            return 0.0

        if result.metrics.missing_pct > 0:
            penalty = result.metrics.missing_pct * 2.0
            score -= penalty

        return max(0.0, min(100.0, score))
