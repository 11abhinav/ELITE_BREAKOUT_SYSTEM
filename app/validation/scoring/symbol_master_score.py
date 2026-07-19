from .base import BaseScoreCalculator
from ..result import ValidationResult

class SymbolMasterScoreCalculator(BaseScoreCalculator):
    """
    Calculates the fidelity score of a Symbol Master dataset.
    Penalizes for duplicate ISINs (warning flag) or missing optional cells.
    """
    def calculate(self, result: ValidationResult) -> float:
        score = 100.0

        if not result.is_valid:
            return 0.0

        if result.metrics.missing_pct > 0:
            penalty = result.metrics.missing_pct * 2.0
            score -= penalty

        # We used invalid_prices in SymbolMaster to track Duplicate ISINs + Invalid Series
        if result.metrics.row_count > 0 and result.metrics.invalid_prices > 0:
            invalid_pct = (result.metrics.invalid_prices / result.metrics.row_count) * 100
            score -= (invalid_pct * 5.0)

        return max(0.0, min(100.0, score))
