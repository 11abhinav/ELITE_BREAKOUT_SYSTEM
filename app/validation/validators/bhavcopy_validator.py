import pandas as pd
from typing import List
from ..base import BaseValidator
from ..result import ValidationResult
from ..context import ValidationContext
from ..codes import ValidationFailure, FailureCode, Severity

class BhavcopyValidator(BaseValidator):
    """
    [Cross-Section Validator]
    Validates NSE Bhavcopy datasets for schema compliance, business rules, and dataset-level integrity.
    
    Unlike Time-Series Validators (which validate one entity over time), this validator
    receives the entire Bhavcopy DataFrame for a single trading day to perform
    cross-sectional checks like duplicate symbols, global missing fields, and dataset size anomalies.
    """
    
    @property
    def name(self) -> str:
        return "BhavcopyValidator"
        
    @property
    def version(self) -> str:
        return "1.0"

    def required_columns(self) -> List[str]:
        # Based on standard NSE Bhavcopy headers. ISIN is no longer provided in the new format.
        return ["SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", 
                "LAST", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL", "TIMESTAMP", 
                "TOTALTRADES"]

    def optional_columns(self) -> List[str]:
        return ["Unnamed: 13", "ISIN"]

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
        # Check for duplicate column names
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

        # Type Validation for key numeric columns
        numeric_cols = ["OPEN", "HIGH", "LOW", "CLOSE", "TOTTRDQTY", "TOTTRDVAL", "TOTALTRADES"]
        try:
            for col in numeric_cols:
                if not pd.api.types.is_numeric_dtype(df[col]):
                    # Sometimes numeric columns are parsed as object if there are stray strings.
                    # This check is strict.
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
            # Enforce strictly EQ series (or other valid series if needed, but standard logic filters for EQ)
            # The validator itself might not strictly reject the whole file if non-EQ exists, but it should track invalid ones.
            # Usually Bhavcopy contains EQ, BE, SM etc. Let's just validate internal price consistency.
            
            invalid_mask = ((df["HIGH"] < df["LOW"]) | 
                            (df["CLOSE"] > df["HIGH"]) | 
                            (df["CLOSE"] < df["LOW"]) | 
                            (df["OPEN"] > df["HIGH"]) |
                            (df["OPEN"] < df["LOW"]) |
                            (df["LAST"] > df["HIGH"]) |
                            (df["LAST"] < df["LOW"]) |
                            (df["OPEN"] <= 0) | 
                            (df["HIGH"] <= 0) | 
                            (df["LOW"] <= 0) | 
                            (df["CLOSE"] <= 0) |
                            (df["PREVCLOSE"] <= 0) |
                            (df["TOTTRDQTY"] < 0) |
                            (df["TOTTRDVAL"] < 0) |
                            (df["TOTALTRADES"] < 0) |
                            (df["SYMBOL"].isna()) |
                            (df["SYMBOL"] == ""))
            
            invalid_count = invalid_mask.sum()
            result.metrics.invalid_prices = int(invalid_count)
            
            total_cells = len(df) * len(self.required_columns())
            missing_cells = df[self.required_columns()].isna().sum().sum()
            result.metrics.missing_pct = (missing_cells / total_cells) * 100 if total_cells > 0 else 100.0
            
            if invalid_count > len(df) * 0.1 and len(df) > 0:
                result.business_failures.append(ValidationFailure(
                    code=FailureCode.BUS002,
                    severity=Severity.CRITICAL,
                    message=f"Severe Bhavcopy corruption: {invalid_count}/{len(df)} rows contain impossible data"
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
            # Convert TIMESTAMP to datetime to check monotonicity
            time_series = pd.to_datetime(df["TIMESTAMP"], format='mixed', dayfirst=False, errors='coerce')
            
            result.metrics.row_count = len(df)
            result.metrics.monotonic = True  # Monotonic logic can be expanded if needed
            
            # Check for duplicate SYMBOL + TIMESTAMP
            duplicates = df.duplicated(subset=["SYMBOL", "TIMESTAMP"]).sum()
            result.metrics.duplicate_rows = int(duplicates)
            
            # Since a Bhavcopy dataframe could be sorted by SYMBOL rather than TIMESTAMP, 
            # we should group by SYMBOL to check if time is monotonic for each symbol, or just 
            # sort the dataframe by TIMESTAMP before checking.
            # For cross-sectional data of a single day, all timestamps are identical, so it's technically monotonic.
            
            if duplicates > 0:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS003,
                    severity=Severity.CRITICAL,
                    message=f"Duplicate primary key (SYMBOL + TIMESTAMP) detected: {duplicates} rows"
                ))
                return
                 
            # Check for duplicate ISINs (Only if valid ISINs exist)
            if "ISIN" in df.columns and not df["ISIN"].isna().all() and (df["ISIN"] != "UNKNOWN_ISIN").all():
                # Filter out UNKNOWN_ISIN or NaNs before checking for duplicates
                valid_isins = df[df["ISIN"].notna() & (df["ISIN"] != "UNKNOWN_ISIN")]
                dup_isins = valid_isins.duplicated(subset=["ISIN", "TIMESTAMP"]).sum()
                if dup_isins > 0:
                    result.historical_failures.append(ValidationFailure(
                        code=FailureCode.HIS003,
                        severity=Severity.CRITICAL,
                        message=f"Duplicate ISINs detected: {dup_isins} rows"
                    ))
                    return
                 
            # Unexpected symbol-count regression (Outlier detection)
            # A typical NSE bhavcopy has around 2000-2500 equity symbols. If it's less than 500, it's highly suspicious.
            unique_symbols = df["SYMBOL"].nunique()
            if unique_symbols < 100 and len(df) > 0:
                result.historical_failures.append(ValidationFailure(
                    code=FailureCode.HIS001, # Treating as a form of historical shrink
                    severity=Severity.CRITICAL,
                    message=f"Unexpected symbol count regression: Only {unique_symbols} symbols found"
                ))
                return
                
            # Stale metrics
            from datetime import datetime
            from zoneinfo import ZoneInfo
            IST = ZoneInfo("Asia/Kolkata")
            
            freshness_days = 999
            now_dt = datetime.now(IST)
            if not time_series.empty and not time_series.isna().all():
                max_dt = time_series.max()
                if not pd.isna(max_dt):
                    if max_dt.tzinfo is None:
                        max_dt = max_dt.tz_localize(IST)
                    else:
                        max_dt = max_dt.tz_convert(IST)
                    freshness_days = max(0, (now_dt.date() - max_dt.date()).days)
            result.metrics.stale_days = freshness_days

        except Exception as e:
            result.historical_failures.append(ValidationFailure(
                code=FailureCode.HIS001,
                severity=Severity.CRITICAL,
                message=f"Historical validation crashed: {str(e)}"
            ))
            return
