import pandas as pd
import numpy as np
from typing import List, Optional
from ..base import BaseValidator
from ..result import ValidationResult

class PriceValidator(BaseValidator):
    """
    Validates OHLCV dataset schema, business logic invariants, and historical continuity.
    """
    
    @property
    def name(self) -> str:
        return "PriceValidator"
        
    @property
    def version(self) -> str:
        return "2.0"

    def required_columns(self) -> List[str]:
        return ["Open", "High", "Low", "Close", "Volume"]

    def optional_columns(self) -> List[str]:
        return ["Adj Close", "Dividends", "Stock Splits"]

    def validate_schema(self, df: pd.DataFrame, result: ValidationResult) -> None:
        if df is None or getattr(df, 'empty', True):
            result.critical_failures.append("DataFrame is empty or None")
            result.schema_pass = False
            return

        missing_cols = [c for c in self.required_columns() if c not in df.columns]
        if missing_cols:
            result.critical_failures.append(f"Missing required columns: {missing_cols}")
            result.schema_pass = False
            return

        # Type Validation
        try:
            for col in self.required_columns():
                if not pd.api.types.is_numeric_dtype(df[col]):
                    result.critical_failures.append(f"Column '{col}' is not numeric")
                    result.schema_pass = False
                    return
        except Exception as e:
            result.critical_failures.append(f"Type validation crashed: {str(e)}")
            result.schema_pass = False
            return

        result.schema_pass = True

    def validate_business(self, df: pd.DataFrame, result: ValidationResult) -> None:
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
            result.metrics["invalid_prices"] = invalid_count
            
            # Record total missing cells to be used by score calculator
            total_cells = len(df) * len(self.required_columns())
            missing_cells = df[self.required_columns()].isna().sum().sum()
            result.metrics["missing_pct"] = (missing_cells / total_cells) * 100 if total_cells > 0 else 100.0
            
            # Optional: fail business if more than 50% prices are completely corrupted
            if invalid_count > len(df) * 0.5 and len(df) > 0:
                result.critical_failures.append(f"Severe price corruption: {invalid_count}/{len(df)} rows contain impossible OHLCV data")
                result.business_pass = False
                return

        except Exception as e:
            result.critical_failures.append(f"Business logic validation crashed: {str(e)}")
            result.business_pass = False
            return

        result.business_pass = True

    def validate_historical(self, df: pd.DataFrame, cache_df: Optional[pd.DataFrame], result: ValidationResult) -> None:
        time_col = 'Date' if 'Date' in df.columns else ('Datetime' if 'Datetime' in df.columns else None)
        time_series = df[time_col] if time_col else df.index
        
        try:
            duplicate_rows = time_series.duplicated().sum()
            result.metrics["duplicate_rows"] = int(duplicate_rows)
            result.metrics["monotonic"] = bool(time_series.is_monotonic_increasing)
            result.metrics["row_count"] = len(df)
            
            # Historical Shrink detection is handled by cache engine currently, but we could add 
            # advanced discontinuity/split detection here in the future.
            
            if duplicate_rows > len(df) * 0.5 and len(df) > 0:
                result.critical_failures.append("Massive duplicate explosion detected")
                result.historical_pass = False
                return
                
        except Exception as e:
            result.critical_failures.append(f"Historical validation crashed: {str(e)}")
            result.historical_pass = False
            return

        result.historical_pass = True
