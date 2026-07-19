from abc import ABC, abstractmethod
from ..result import ValidationResult
from ..context import ValidationContext

class BaseScoreCalculator(ABC):
    """
    Abstract base class for assigning a quality score to a VALID dataset.
    Only called if the dataset passes critical validation (Schema, Business, Historical).
    """

    @abstractmethod
    def calculate(self, result: ValidationResult, context: ValidationContext) -> int:
        """
        Calculates a quality score (0-100) based on validated metrics.
        Returns the final computed score.
        """
        pass
