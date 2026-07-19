from abc import ABC, abstractmethod
import pandas as pd
from typing import List, Optional
from .result import ValidationResult
from .context import ValidationContext

class BaseValidator(ABC):
    """
    Abstract base class for dataset-specific validators.
    Responsible for checking structural and logical correctness (PASS/FAIL).
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the validator (e.g. 'PriceValidator')"""
        pass
        
    @property
    @abstractmethod
    def version(self) -> str:
        """Version of the validator ruleset (e.g. '2.1')"""
        pass

    @abstractmethod
    def required_columns(self) -> List[str]:
        """List of columns strictly required for the dataset to be valid."""
        pass

    @abstractmethod
    def optional_columns(self) -> List[str]:
        """List of columns that are recognized but not strictly required."""
        pass

    @abstractmethod
    def validate(self, df: pd.DataFrame, context: ValidationContext) -> ValidationResult:
        """
        Orchestrates internal validation sequences (Schema -> Business -> Historical)
        and returns a complete ValidationResult.
        """
        pass
