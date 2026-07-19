import pandas as pd
from typing import List
from ..base import BaseValidator
from ..result import ValidationResult
from ..context import ValidationContext
from ..codes import ValidationFailure, FailureCode, Severity

class CorporateActionsValidator(BaseValidator):
    """
    [Time-Series Validator]
    Validates normalized corporate actions for a single symbol over time.
    Expects pre-parsed events (e.g. RATIO = numerator/denominator), not raw text.
    """
    
    @property
    def name(self) -> str:
        return "CorporateActionsValidator"
        
    @property
    def version(self) -> str:
        return "1.0"

    def required_columns(self) -> List[str]:
        return ["EX_DATE", "PURPOSE", "NUMERATOR", "DENOMINATOR"]

    def optional_columns(self) -> List[str]:
        return ["SYMBOL", "RECORD_DATE"]

    def validate(self, df: pd.DataFrame, context: ValidationContext) -> ValidationResult:
        result = ValidationResult()
        
        self._validate_schema(df, result)
        if result.schema_failures:
            return result
            
        self._validate_business(df, result)
        if result.business_failures:
            return result
            
        self._validate_historical(df, context, result)
        return result

    def _validate_schema(self, df: pd.DataFrame, result: ValidationResult) -> None:
        if len(df.columns) != len(set(df.columns)):
            result.schema_failures.append(ValidationFailure(
                code=FailureCode.SCH001,
                severity=Severity.CRITICAL,
                message="Duplicate column names found in the dataset"
            ))
            return
            
        missing_cols = [c for c in self.required_columns() if c not in df.columns]
        if missing_cols:
            result.schema_failures.append(ValidationFailure(
                code=FailureCode.SCH001,
                severity=Severity.CRITICAL,
                message=f"Missing required columns: {missing_cols}"
            ))
            return

        numeric_cols = ["NUMERATOR", "DENOMINATOR"]
        try:
            for col in numeric_cols:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    coerced = pd.to_numeric(df[col], errors='coerce')
                    if coerced.isna().sum() > df[col].isna().sum():
                        result.schema_failures.append(ValidationFailure(
                            code=FailureCode.SCH002,
                            severity=Severity.CRITICAL,
                            message=f"Column '{col}' is not numeric and cannot be coerced"
                        ))
                        return
        except Exception as e:
            result.schema_failures.append(ValidationFailure(
                code=FailureCode.SCH002,
                severity=Severity.CRITICAL,
                message=f"Type validation crashed: {str(e)}"
            ))
            return

    def _validate_business(self, df: pd.DataFrame, result: ValidationResult) -> None:
        try:
            numerator = pd.to_numeric(df["NUMERATOR"], errors='coerce')
            denominator = pd.to_numeric(df["DENOMINATOR"], errors='coerce')
            
            # Mathematical validity
            invalid_math = ((numerator <= 0) | (denominator <= 0) | denominator.isna() | numerator.isna())
            
            # Valid action type
            valid_purposes = {"SPLIT", "BONUS", "DIVIDEND", "RIGHTS"}
            invalid_purpose = ~df["PURPOSE"].str.upper().isin(valid_purposes)
            
            invalid_mask = invalid_math | invalid_purpose
            invalid_count = invalid_mask.sum()
            result.metrics.invalid_prices = int(invalid_count) # general penalty tracker
            
            total_cells = len(df) * len(self.required_columns())
            missing_cells = df[self.required_columns()].isna().sum().sum()
            result.metrics.missing_pct = (missing_cells / total_cells) * 100 if total_cells > 0 else 100.0
            
            # Optional columns missing penalty (we track it manually by checking optional columns)
            # We can just add them to the missing_pct calculation or calculate overall missing_pct
            all_cols = [c for c in df.columns if c in self.required_columns() + self.optional_columns()]
            all_cells = len(df) * len(all_cols)
            all_missing = df[all_cols].isna().sum().sum()
            if all_cells > 0:
                result.metrics.missing_pct = max(result.metrics.missing_pct, (all_missing / all_cells) * 100)
            
            if invalid_count > 0:
                result.business_failures.append(ValidationFailure(
                    code=FailureCode.BUS002,
                    severity=Severity.CRITICAL,
                    message=f"{invalid_count} actions have invalid math (denominator <= 0) or unknown PURPOSE"
                ))
                return
                
        except Exception as e:
            result.business_failures.append(ValidationFailure(
                code=FailureCode.BUS001,
                severity=Severity.CRITICAL,
                message=f"Business logic validation crashed: {str(e)}"
            ))
            return

    def _validate_historical(self, df: pd.DataFrame, context: ValidationContext, result: ValidationResult) -> None:
        try:
            result.metrics.row_count = len(df)
            
            time_series_dt = pd.to_datetime(df["EX_DATE"], errors='coerce')
            
            # Uniqueness: Duplicate EX_DATE + PURPOSE
            # Usually corporate actions don't have multiple splits on the exact same ex-date
            # But they can have Dividend + Split on the same ex-date. So PURPOSE is necessary.
            duplicates = df.duplicated(subset=["EX_DATE", "PURPOSE"]).sum()
            if duplicates > 0:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS003,
                    severity=Severity.CRITICAL,
                    message=f"Duplicate EX_DATE + PURPOSE detected: {duplicates} rows"
                ))
                return
            
            # We enforce monotonic increasing EX_DATES to ensure downstream parsers can apply them strictly in order
            if not time_series_dt.is_monotonic_increasing:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS002,
                    severity=Severity.CRITICAL,
                    message="EX_DATE series is not monotonic increasing"
                ))
                return
                
            # Note: We intentionally DO NOT fail on future dates.
            # Corporate actions are announced ahead of time.
            
        except Exception as e:
            result.historical_failures.append(ValidationFailure(
                code=FailureCode.HIS001,
                severity=Severity.CRITICAL,
                message=f"Historical validation crashed: {str(e)}"
            ))
            return
