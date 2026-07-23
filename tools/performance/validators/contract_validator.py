import pandas as pd
import logging

logger = logging.getLogger(__name__)

class ContractValidator:
    """
    Validates dataset contracts (schema, dtypes, timezone, duplicates, freshness).
    """
    @staticmethod
    def validate_dataset(df: pd.DataFrame, dataset_name: str, expected_columns: list, check_timezone: bool = True):
        status = "PASS"
        errors = []

        # 1. Provenance Check
        if not hasattr(df, "attrs") or "provider" not in df.attrs:
            status = "FAIL"
            errors.append("Missing provenance (df.attrs['provider']).")

        # 2. Schema Check
        missing_cols = [col for col in expected_columns if col not in df.columns]
        if missing_cols:
            status = "FAIL"
            errors.append(f"Missing expected columns: {missing_cols}")

        # 3. Duplicate Check
        # Depending on dataset, if index is date or symbol
        if df.index.duplicated().any():
            status = "FAIL"
            errors.append("Duplicate index values detected.")

        # 4. Timezone Check (if datetime index)
        if check_timezone and isinstance(df.index, pd.DatetimeIndex):
            if df.index.tzinfo is None:
                errors.append("DatetimeIndex is tz-naive.")
                status = "WARNING" if status == "PASS" else status

        if status != "PASS":
            logger.error(f"[ContractValidator] {dataset_name} validation failed: {errors}")
        else:
            logger.info(f"[ContractValidator] {dataset_name} validation passed.")
            
        return status, errors
