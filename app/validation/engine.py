import pandas as pd
import logging
from typing import Optional
from .base import BaseValidator
from .scoring.base import BaseScoreCalculator
from .result import ValidationResult
from .report import DataQualityReport
from .context import ValidationContext
from .codes import ValidationFailure, FailureCode, Severity

logger = logging.getLogger(__name__)

class ValidationEngine:
    """
    Orchestrates the validation lifecycle for any dataset.
    Delegates validation to the Validator, and scoring to the ScoreCalculator.
    """
    def __init__(self, validator: BaseValidator, score_calculator: Optional[BaseScoreCalculator] = None):
        self.validator = validator
        self.score_calculator = score_calculator

    def validate(self, df: pd.DataFrame, context: Optional[ValidationContext] = None) -> DataQualityReport:
        """
        Runs the full validation pipeline on the provided dataframe.
        """
        if context is None:
            context = ValidationContext()
            
        if df is None or df.empty:
            result = ValidationResult()
            result.schema_failures.append(ValidationFailure(
                code=FailureCode.QLT001,
                severity=Severity.CRITICAL,
                message="Dataframe is None or empty."
            ))
            return self._build_report(result, 0)

        # 1. Critical Validation Phase
        try:
            result = self.validator.validate(df, context)
            if not result.is_valid:
                logger.warning(f"[{self.validator.name}] Critical validation failed: {result.critical_failures}")
                return self._build_report(result, 0)
        except Exception as e:
            logger.exception(f"[{self.validator.name}] Unhandled exception during validation")
            result = ValidationResult()
            result.schema_failures.append(ValidationFailure(
                code=FailureCode.BUS001,
                severity=Severity.CRITICAL,
                message=f"Unhandled exception: {str(e)}"
            ))
            return self._build_report(result, 0)

        # 2. Scoring Phase (Only if Critical Validation Passed)
        score = 0
        if self.score_calculator:
            try:
                score = self.score_calculator.calculate(result, context)
            except Exception as e:
                logger.exception(f"[{self.validator.name}] Unhandled exception during scoring")
                result.warnings.append(f"Scoring failed: {str(e)}")
        else:
            score = 100 # Default perfect score if no calculator is provided

        return self._build_report(result, score)

    def _build_report(self, result: ValidationResult, score: int) -> DataQualityReport:
        """Constructs the immutable DataQualityReport."""
        return DataQualityReport(
            is_valid=result.is_valid,
            quality_score=score if result.is_valid else 0,
            critical_failures=result.critical_failures,
            row_count=result.metrics.row_count,
            missing_pct=result.metrics.missing_pct,
            stale_days=result.metrics.stale_days,
            validator_name=self.validator.name,
            validator_version=self.validator.version
        )
