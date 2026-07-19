from abc import ABC, abstractmethod
import pandas as pd
from ..result import ValidationResult

class BaseScoreCalculator(ABC):
    """
    Abstract base class for assigning a quality score to a VALID dataset.
    Only called if the dataset passes critical validation (Schema, Business, Historical).
    """

    @abstractmethod
    def calculate(self, df: pd.DataFrame, result: ValidationResult) -> int:
        """
        Calculates a quality score (0-100) based on warnings, missing cells, staleness, etc.
        Returns the final computed score.
        """
        pass
