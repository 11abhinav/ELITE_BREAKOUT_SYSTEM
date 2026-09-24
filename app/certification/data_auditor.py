# app/certification/data_auditor.py
"""
Global Historical Parquet Data Quality Auditor and Sanitizer.
Audits 1D parquets for microsecond timestamps, synthetic interleaved rows, impossible OHLC, and abnormal jumps.
"""
import glob
import os
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np


class ParquetAuditor:
    """Audits and sanitizes historical parquet files."""
    
    @staticmethod
    def audit_file(file_path: str) -> Dict[str, Any]:
        result = {
            "file": file_path,
            "symbol": os.path.basename(file_path).replace(".parquet", ""),
            "total_rows": 0,
            "is_clean": True,
            "corrupted_rows": 0,
            "issues": [],
            "non_midnight_timestamps": 0,
            "duplicate_dates": 0,
            "impossible_ohlc": 0,
            "extreme_single_day_spikes": 0,
        }
        
        if not os.path.exists(file_path):
            result["is_clean"] = False
            result["issues"].append("FILE_NOT_FOUND")
            return result

        try:
            df = pd.read_parquet(file_path)
        except Exception as e:
            result["is_clean"] = False
            result["issues"].append(f"UNREADABLE_PARQUET: {e}")
            return result

        result["total_rows"] = len(df)
        if df.empty:
            result["is_clean"] = False
            result["issues"].append("EMPTY_PARQUET")
            return result

        # 1. Non-midnight / microsecond timestamps
        time_col = None
        for col in ["Datetime", "Date", "timestamp"]:
            if col in df.columns:
                time_col = col
                break

        if time_col:
            ts_str = df[time_col].astype(str)
            # Match microsecond or non-midnight times like 11:21:18.476379
            bad_ts_mask = ts_str.str.contains(r"\d{2}:\d{2}:\d{2}\.\d+") | ts_str.str.contains(r" (?!00:00:00)\d{2}:\d{2}:\d{2}")
            bad_ts_count = int(bad_ts_mask.sum())
            if bad_ts_count > 0:
                result["non_midnight_timestamps"] = bad_ts_count
                result["corrupted_rows"] += bad_ts_count
                result["is_clean"] = False
                result["issues"].append(f"NON_MIDNIGHT_TIMESTAMPS: {bad_ts_count} rows have intraday/microsecond times in daily file")

            # 2. Duplicate dates
            try:
                date_series = pd.to_datetime(df[time_col]).dt.date
                dup_count = int(date_series.duplicated().sum())
                if dup_count > 0:
                    result["duplicate_dates"] = dup_count
                    result["is_clean"] = False
                    result["issues"].append(f"DUPLICATE_DATES: {dup_count} duplicate session dates")
            except Exception:
                pass

        # 3. Impossible OHLC (High < Low, Close > High, Low > Open, etc.)
        req_cols = ["Open", "High", "Low", "Close"]
        if all(c in df.columns for c in req_cols):
            h = pd.to_numeric(df["High"], errors="coerce")
            l = pd.to_numeric(df["Low"], errors="coerce")
            c = pd.to_numeric(df["Close"], errors="coerce")
            o = pd.to_numeric(df["Open"], errors="coerce")

            impossible = (h < l) | (c > h * 1.001) | (c < l * 0.999) | (o > h * 1.001) | (o < l * 0.999) | (l <= 0)
            imp_count = int(impossible.fillna(False).sum())
            if imp_count > 0:
                result["impossible_ohlc"] = imp_count
                result["corrupted_rows"] += imp_count
                result["is_clean"] = False
                result["issues"].append(f"IMPOSSIBLE_OHLC: {imp_count} rows violate High >= max(Open, Close) or Low <= min(Open, Close)")

        # 4. Single-day 50%+ spike immediately reverting (synthetic injection signature)
        if "Close" in df.columns and len(df) > 5:
            close = pd.to_numeric(df["Close"], errors="coerce")
            pct_change = close.pct_change(fill_method=None)
            next_pct = close.pct_change(periods=-1, fill_method=None)
            # e.g. Day t spikes +70% and Day t+1 drops -40% back to baseline
            spikes = (pct_change > 0.45) & (next_pct < -0.30)
            spike_count = int(spikes.fillna(False).sum())
            if spike_count > 0:
                result["extreme_single_day_spikes"] = spike_count
                result["is_clean"] = False
                result["issues"].append(f"SYNTHETIC_PRICE_SPIKE: {spike_count} extreme flash spike-and-revert detected")

        return result

    @classmethod
    def audit_directory(cls, dir_path: str = "data/history/1d") -> Dict[str, Any]:
        """Audits all parquet files in directory."""
        files = glob.glob(os.path.join(dir_path, "*.parquet"))
        total = len(files)
        clean = 0
        contaminated = []

        for f in files:
            res = cls.audit_file(f)
            if res["is_clean"]:
                clean += 1
            else:
                contaminated.append(res)

        return {
            "total_files": total,
            "clean_files": clean,
            "contaminated_count": len(contaminated),
            "contaminated_files": contaminated
        }

    @classmethod
    def sanitize_file(cls, file_path: str, backup: bool = True) -> Tuple[bool, int, str]:
        """
        Strips contaminated non-midnight rows, duplicate dates, and impossible OHLC.
        Resaves clean genuine data.
        Returns: (success: bool, rows_removed: int, message: str)
        """
        if not os.path.exists(file_path):
            return False, 0, "File not found"

        try:
            df = pd.read_parquet(file_path)
        except Exception as e:
            return False, 0, f"Cannot read parquet: {e}"

        orig_len = len(df)
        if orig_len == 0:
            return True, 0, "Empty dataframe"

        time_col = None
        for col in ["Datetime", "Date", "timestamp"]:
            if col in df.columns:
                time_col = col
                break

        if not time_col:
            return False, 0, "No Datetime/Date column"

        # 1. Filter out microsecond/intraday timestamps
        ts_str = df[time_col].astype(str)
        bad_ts_mask = ts_str.str.contains(r"\d{2}:\d{2}:\d{2}\.\d+") | ts_str.str.contains(r" (?!00:00:00)\d{2}:\d{2}:\d{2}")
        clean_df = df[~bad_ts_mask].copy()

        # 2. Filter impossible OHLC
        if all(c in clean_df.columns for c in ["Open", "High", "Low", "Close"]):
            h = pd.to_numeric(clean_df["High"], errors="coerce")
            l = pd.to_numeric(clean_df["Low"], errors="coerce")
            c = pd.to_numeric(clean_df["Close"], errors="coerce")
            o = pd.to_numeric(clean_df["Open"], errors="coerce")
            valid_ohlc = (h >= l) & (c <= h * 1.002) & (c >= l * 0.998) & (l > 0)
            clean_df = clean_df[valid_ohlc.fillna(True)]

        # 3. Deduplicate by date keeping genuine entry
        clean_df["_audit_date"] = pd.to_datetime(clean_df[time_col]).dt.date
        clean_df = clean_df.drop_duplicates(subset=["_audit_date"], keep="last").drop(columns=["_audit_date"])
        clean_df = clean_df.sort_values(time_col).reset_index(drop=True)

        rows_removed = orig_len - len(clean_df)

        if rows_removed > 0:
            if backup:
                backup_path = file_path + ".corrupt_bak"
                if not os.path.exists(backup_path):
                    import shutil
                    shutil.copy2(file_path, backup_path)
            clean_df.to_parquet(file_path, index=False)
            return True, rows_removed, f"Sanitized successfully. Removed {rows_removed} rogue rows."
        else:
            return True, 0, "Already clean."
