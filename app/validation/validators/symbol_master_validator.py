import pandas as pd
from typing import List
from ..base import BaseValidator
from ..result import ValidationResult
from ..context import ValidationContext
from ..codes import ValidationFailure, FailureCode, Severity

class SymbolMasterValidator(BaseValidator):
    """
    [Cross-Section Validator]
    Validates a Symbol Master dataset containing metadata (e.g. lot sizes, listing dates, ISINs) 
    for the entire equity universe at a single point in time.
    """
    
    @property
    def name(self) -> str:
        return "SymbolMasterValidator"
        
    @property
    def version(self) -> str:
        return "1.0"

    def required_columns(self) -> List[str]:
        return ["SYMBOL", "ISIN", "SERIES", "LOT_SIZE", "FACE_VALUE", "LISTING_DATE"]

    def optional_columns(self) -> List[str]:
        return ["COMPANY_NAME"]

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
            
        # Check for mandatory values missing in key identifiers
        if df["SYMBOL"].isna().any() or df["ISIN"].isna().any() or df["SYMBOL"].eq("").any() or df["ISIN"].eq("").any():
             result.schema_failures.append(ValidationFailure(
                code=FailureCode.SCH001,
                severity=Severity.CRITICAL,
                message="Mandatory values missing (SYMBOL or ISIN cannot be null/empty)"
            ))
             return

        numeric_cols = ["LOT_SIZE", "FACE_VALUE"]
        try:
            for col in numeric_cols:
                # Attempt coercion if strings exist, otherwise rely on numeric type
                if not pd.api.types.is_numeric_dtype(df[col]):
                    # Check if it's coercible
                    coerced = pd.to_numeric(df[col], errors='coerce')
                    if coerced.isna().sum() > df[col].isna().sum():
                        result.schema_failures.append(ValidationFailure(
                            code=FailureCode.SCH002,
                            severity=Severity.CRITICAL,
                            message=f"Column '{col}' contains non-numeric and non-coercible values"
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
            # 1. Negative or zero Lot sizes / face values
            # Coerce before checking if needed, but assuming valid numeric array from schema check
            lot_size = pd.to_numeric(df["LOT_SIZE"], errors='coerce')
            face_value = pd.to_numeric(df["FACE_VALUE"], errors='coerce')
            
            invalid_mask = ((lot_size <= 0) | (face_value <= 0))
            
            # 2. SERIES valid configuration
            # In a real app this might fetch from config, but for now we define known valid sets
            valid_series = {"EQ", "BE", "SM", "ST", "IL", "BZ", "GB", "B1", "B2", "B3"}
            invalid_series_mask = ~df["SERIES"].isin(valid_series)
            
            invalid_count = invalid_mask.sum() + invalid_series_mask.sum()
            result.metrics.invalid_prices = int(invalid_count) # generic failure stat
            
            total_cells = len(df) * len(self.required_columns())
            missing_cells = df[self.required_columns()].isna().sum().sum()
            result.metrics.missing_pct = (missing_cells / total_cells) * 100 if total_cells > 0 else 100.0
            
            if invalid_count > len(df) * 0.1 and len(df) > 0:
                result.business_failures.append(ValidationFailure(
                    code=FailureCode.BUS002,
                    severity=Severity.CRITICAL,
                    message=f"Business validation failed: {invalid_count} rows have invalid lots/face-values/series"
                ))
                return
                
            # 3. LISTING_DATE parsing and sanity check
            listing_dates = pd.to_datetime(df["LISTING_DATE"], errors='coerce')
            if listing_dates.isna().sum() > df["LISTING_DATE"].isna().sum():
                # Some dates failed to parse
                unparsable = listing_dates.isna().sum() - df["LISTING_DATE"].isna().sum()
                result.business_failures.append(ValidationFailure(
                    code=FailureCode.BUS002,
                    severity=Severity.CRITICAL,
                    message=f"{unparsable} LISTING_DATE values could not be parsed"
                ))
                return
                
            # check for absurd future dates (e.g. > 10 years in future is absurd)
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo
            IST = ZoneInfo("Asia/Kolkata")
            absurd_future = datetime.now(IST).replace(tzinfo=None) + timedelta(days=3650)
            
            future_dates = (listing_dates > absurd_future).sum()
            if future_dates > 0:
                result.business_failures.append(ValidationFailure(
                    code=FailureCode.BUS002,
                    severity=Severity.CRITICAL,
                    message=f"{future_dates} LISTING_DATEs are absurdly far in the future"
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
            
            # Cross-sectional uniqueness: Duplicate SYMBOL is hard failure
            duplicate_symbols = df["SYMBOL"].duplicated().sum()
            result.metrics.duplicate_rows = int(duplicate_symbols)
            
            if duplicate_symbols > 0:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS003,
                    severity=Severity.CRITICAL,
                    message=f"Duplicate SYMBOLs detected: {duplicate_symbols} rows"
                ))
                return
                
            # Duplicate ISIN logic
            # Case 1: Same SYMBOL + Same ISIN (Caught by duplicate symbol check above)
            # Case 2: Different SYMBOL + Same ISIN -> Rename / Merger -> Warning (Quality penalty)
            # We will use result.warnings for this if the framework supports it, otherwise metric degradation
            duplicate_isins = df["ISIN"].duplicated(keep=False)
            dup_isin_count = duplicate_isins.sum()
            if dup_isin_count > 0:
                 # Note: We do NOT append to historical_failures. We track it as a warning metric.
                 # Let's create a custom metric or just piggyback on an existing warning structure
                 # We can use invalid_prices as a general bad_row_count to drop the score
                 result.metrics.invalid_prices += int(dup_isin_count)
            
            # Dataset Size Anomaly
            # To avoid hardcoding, we would theoretically compare against `context.cache_df` or a rolling metric.
            # If no context is provided, we just do a basic sanity check (e.g. > 100)
            if len(df) < 100:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS001,
                    severity=Severity.CRITICAL,
                    message=f"Symbol master abnormally small: {len(df)} rows"
                ))
                return
                
        except Exception as e:
            result.historical_failures.append(ValidationFailure(
                code=FailureCode.HIS001,
                severity=Severity.CRITICAL,
                message=f"Cross-section validation crashed: {str(e)}"
            ))
            return
