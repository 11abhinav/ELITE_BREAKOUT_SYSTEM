from abc import ABC, abstractmethod
import pandas as pd
from typing import List, Optional
from .result import ValidationResult

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
        """Version of the validator ruleset (e.g. '1.0')"""
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
    def validate_schema(self, df: pd.DataFrame, result: ValidationResult) -> None:
        """
        Validates column presence and data types.
        Populates result.schema_pass and result.critical_failures.
        """
        pass

    @abstractmethod
    def validate_business(self, df: pd.DataFrame, result: ValidationResult) -> None:
        """
        Validates business logic invariants (e.g. High >= Low, Close >= 0).
        Populates result.business_pass and result.critical_failures.
        """
        pass

    @abstractmethod
    def validate_historical(self, df: pd.DataFrame, cache_df: Optional[pd.DataFrame], result: ValidationResult) -> None:
        """
        Validates continuity against an existing historical cache.
        Populates result.historical_pass and result.critical_failures.
        """
        pass
