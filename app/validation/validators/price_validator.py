import pandas as pd
from typing import List
from ..base import BaseValidator
from ..result import ValidationResult
from ..context import ValidationContext

class PriceValidator(BaseValidator):
    """
    Validates OHLCV dataset schema, business logic invariants, and historical continuity.
    """
    
    @property
    def name(self) -> str:
        return "PriceValidator"
        
    @property
    def version(self) -> str:
        return "2.1"

    def required_columns(self) -> List[str]:
        return ["Open", "High", "Low", "Close", "Volume"]

    def optional_columns(self) -> List[str]:
        return ["Adj Close", "Dividends", "Stock Splits"]

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
        missing_cols = [c for c in self.required_columns() if c not in df.columns]
        if missing_cols:
            result.schema_failures.append(f"Missing required columns: {missing_cols}")
            return

        # Type Validation
        try:
            for col in self.required_columns():
                if not pd.api.types.is_numeric_dtype(df[col]):
                    result.schema_failures.append(f"Column '{col}' is not numeric")
                    return
        except Exception as e:
            result.schema_failures.append(f"Type validation crashed: {str(e)}")
            return

    def _validate_business(self, df: pd.DataFrame, result: ValidationResult) -> None:
        # Price Sanity (Business Invariants)
        try:
            invalid_mask = ((df["High"] < df["Low"]) | 
                            (df["Close"] > df["High"]) | 
                            (df["Close"] < df["Low"]) | 
                            (df["Open"] <= 0) | 
                            (df["High"] <= 0) | 
                            (df["Low"] <= 0) | 
                            (df["Close"] <= 0) |
                            (df.get("Volume", 0) < 0))
            
            invalid_count = invalid_mask.sum()
            result.metrics.invalid_prices = int(invalid_count)
            
            # Record total missing cells to be used by score calculator
            total_cells = len(df) * len(self.required_columns())
            missing_cells = df[self.required_columns()].isna().sum().sum()
            result.metrics.missing_pct = (missing_cells / total_cells) * 100 if total_cells > 0 else 100.0
            
            # Optional: fail business if more than 50% prices are completely corrupted
            if invalid_count > len(df) * 0.5 and len(df) > 0:
                result.business_failures.append(f"Severe price corruption: {invalid_count}/{len(df)} rows contain impossible OHLCV data")
                return

        except Exception as e:
            result.business_failures.append(f"Business logic validation crashed: {str(e)}")
            return

    def _validate_historical(self, df: pd.DataFrame, context: ValidationContext, result: ValidationResult) -> None:
        time_col = 'Date' if 'Date' in df.columns else ('Datetime' if 'Datetime' in df.columns else None)
        time_series = df[time_col] if time_col else df.index
        
        try:
            duplicate_rows = time_series.duplicated().sum()
            result.metrics.duplicate_rows = int(duplicate_rows)
            result.metrics.monotonic = bool(time_series.is_monotonic_increasing)
            result.metrics.row_count = len(df)
            
            # Compute Staleness for Score Calculator
            from datetime import datetime
            from zoneinfo import ZoneInfo
            IST = ZoneInfo("Asia/Kolkata")
            
            freshness_days = 999
            now_dt = datetime.now(IST)
            if not time_series.empty:
                last_dt = pd.to_datetime(time_series[-1])
                if not pd.isna(last_dt):
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.tz_localize(IST)
                    else:
                        last_dt = last_dt.tz_convert(IST)
                    freshness_days = max(0, (now_dt.date() - last_dt.date()).days)
            result.metrics.stale_days = freshness_days
            
            # Historical Shrink detection is handled by cache engine currently
            if duplicate_rows > len(df) * 0.5 and len(df) > 0:
                result.historical_failures.append("Massive duplicate explosion detected")
                return
                
        except Exception as e:
            result.historical_failures.append(f"Historical validation crashed: {str(e)}")
            return
