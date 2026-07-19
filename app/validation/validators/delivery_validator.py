import pandas as pd
from typing import List
from ..base import BaseValidator
from ..result import ValidationResult
from ..context import ValidationContext
from ..codes import ValidationFailure, FailureCode, Severity

class DeliveryValidator(BaseValidator):
    """
    [Time-Series Validator]
    Validates delivery datasets for a single symbol over time.
    Ensures that delivery metrics (Qty, %) are mathematically consistent with total traded quantities.
    """
    
    @property
    def name(self) -> str:
        return "DeliveryValidator"
        
    @property
    def version(self) -> str:
        return "1.0"

    def required_columns(self) -> List[str]:
        # Based on NSE historical delivery data
        return ["TRADED_QTY", "DELIV_QTY", "DELIV_PCT"]

    def optional_columns(self) -> List[str]:
        return ["SYMBOL", "SERIES", "DATE"]

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

        numeric_cols = ["TRADED_QTY", "DELIV_QTY", "DELIV_PCT"]
        try:
            for col in numeric_cols:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    result.schema_failures.append(ValidationFailure(
                        code=FailureCode.SCH002,
                        severity=Severity.CRITICAL,
                        message=f"Column '{col}' is not numeric"
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
            # Recompute delivery percentage to check for consistency with tolerance
            # We ignore division by zero here by using numpy/pandas safe division, 
            # and then handle the TRADED_QTY == 0 case separately.
            implied_pct = (df["DELIV_QTY"] / df["TRADED_QTY"].replace(0, pd.NA)) * 100
            
            # Tolerance for percentage mismatches (0.5% absolute difference)
            pct_mismatch = (df["DELIV_PCT"].notna()) & (implied_pct.notna()) & (abs(df["DELIV_PCT"] - implied_pct) > 0.5)
            
            invalid_mask = ((df["DELIV_QTY"] < 0) | 
                            (df["TRADED_QTY"] < 0) |
                            (df["DELIV_QTY"] > df["TRADED_QTY"]) | 
                            (df["DELIV_PCT"] < 0) |
                            (df["DELIV_PCT"] > 100) |
                            pct_mismatch |
                            ((df["TRADED_QTY"] == 0) & (df["DELIV_QTY"] != 0)))
            
            invalid_count = invalid_mask.sum()
            result.metrics.invalid_prices = int(invalid_count) # Reuse the invalid_prices metric for generic invalid rows
            
            total_cells = len(df) * len(self.required_columns())
            missing_cells = df[self.required_columns()].isna().sum().sum()
            result.metrics.missing_pct = (missing_cells / total_cells) * 100 if total_cells > 0 else 100.0
            
            if invalid_count > 0:
                # We fail on ANY business logic violation since Delivery Data must be pristine
                result.business_failures.append(ValidationFailure(
                    code=FailureCode.BUS002,
                    severity=Severity.CRITICAL,
                    message=f"Delivery corruption: {invalid_count} rows contain impossible quantities or percentage mismatch"
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
        time_col = 'DATE' if 'DATE' in df.columns else ('TIMESTAMP' if 'TIMESTAMP' in df.columns else None)
        time_series = df[time_col] if time_col else df.index
        
        try:
            duplicate_rows = time_series.duplicated().sum()
            result.metrics.duplicate_rows = int(duplicate_rows)
            result.metrics.monotonic = bool(time_series.is_monotonic_increasing)
            result.metrics.row_count = len(df)
            
            if duplicate_rows > 0:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS003,
                    severity=Severity.CRITICAL,
                    message=f"Duplicate timestamps detected: {duplicate_rows} rows"
                ))
                return
                
            if not result.metrics.monotonic:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS002,
                    severity=Severity.CRITICAL,
                    message="Time series is not monotonic increasing"
                ))
                return

            from datetime import datetime
            from zoneinfo import ZoneInfo
            IST = ZoneInfo("Asia/Kolkata")
            now_dt = datetime.now(IST)
            
            # Future timestamp check
            if not time_series.empty:
                time_series_dt = pd.to_datetime(time_series, errors='coerce')
                # Localize timezone appropriately
                time_series_dt = time_series_dt.apply(lambda x: x.tz_localize(IST) if x.tzinfo is None else x.tz_convert(IST))
                future_timestamps = (time_series_dt > now_dt).sum()
                if future_timestamps > 0:
                    result.historical_failures.append(ValidationFailure(
                        code=FailureCode.HIS001,
                        severity=Severity.CRITICAL,
                        message=f"Future timestamps detected: {future_timestamps} rows"
                    ))
                    return
            
            freshness_days = 999
            if not time_series.empty:
                last_dt = pd.to_datetime(time_series.iloc[-1])
                if not pd.isna(last_dt):
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.tz_localize(IST)
                    else:
                        last_dt = last_dt.tz_convert(IST)
                    freshness_days = max(0, (now_dt.date() - last_dt.date()).days)
            result.metrics.stale_days = freshness_days
            
        except Exception as e:
            result.historical_failures.append(ValidationFailure(
                code=FailureCode.HIS001,
                severity=Severity.CRITICAL,
                message=f"Historical validation crashed: {str(e)}"
            ))
            return
