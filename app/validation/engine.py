import pandas as pd
import logging
from typing import Optional
from .base import BaseValidator
from .scoring.base import BaseScoreCalculator
from .result import ValidationResult
from .report import DataQualityReport

logger = logging.getLogger(__name__)

class ValidationEngine:
    """
    Orchestrates the validation lifecycle for any dataset.
    Executes Critical Validation (Schema -> Business -> Historical) and then delegates to ScoreCalculator.
    """
    def __init__(self, validator: BaseValidator, score_calculator: Optional[BaseScoreCalculator] = None):
        self.validator = validator
        self.score_calculator = score_calculator

    def validate(self, df: pd.DataFrame, cache_df: Optional[pd.DataFrame] = None) -> DataQualityReport:
        """
        Runs the full validation pipeline on the provided dataframe.
        """
        result = ValidationResult()

        if df is None or df.empty:
            result.critical_failures.append("Dataframe is None or empty.")
            return self._build_report(result, 0)

        # 1. Critical Validation Phase
        try:
            self.validator.validate_schema(df, result)
            if not result.schema_pass:
                logger.warning(f"[{self.validator.name}] Schema validation failed: {result.critical_failures}")
                return self._build_report(result, 0)

            self.validator.validate_business(df, result)
            if not result.business_pass:
                logger.warning(f"[{self.validator.name}] Business validation failed: {result.critical_failures}")
                return self._build_report(result, 0)

            self.validator.validate_historical(df, cache_df, result)
            if not result.historical_pass:
                logger.warning(f"[{self.validator.name}] Historical validation failed: {result.critical_failures}")
                return self._build_report(result, 0)

        except Exception as e:
            logger.exception(f"[{self.validator.name}] Unhandled exception during validation")
            result.critical_failures.append(f"Unhandled exception: {str(e)}")
            result.schema_pass = False
            return self._build_report(result, 0)

        # 2. Scoring Phase (Only if Critical Validation Passed)
        score = 0
        if self.score_calculator:
            try:
                score = self.score_calculator.calculate(df, result)
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
            critical_failures=tuple(result.critical_failures),
            row_count=result.metrics.get("row_count", 0),
            missing_pct=result.metrics.get("missing_pct", 0.0),
            stale_days=result.metrics.get("stale_days", 0),
            validator_name=self.validator.name,
            validator_version=self.validator.version
        )
