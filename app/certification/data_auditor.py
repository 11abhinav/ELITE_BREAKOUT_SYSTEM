# app/certification/data_auditor.py
"""
Global Historical Data Quality Auditor and Sanitizer across all timeframes.
Audits 1D, 5M, 15M, 30M, 1H parquets, Bhavcopy, Delivery data, and F&O OI stores.
Generates comprehensive Master Data Health Audit Matrix.
"""
import glob
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd
import numpy as np


class ParquetAuditor:
    """Audits and sanitizes historical parquet files across arbitrary timeframes."""
    
    @staticmethod
    def audit_file(file_path: str, timeframe: str = "1d") -> Dict[str, Any]:
        result = {
            "file": file_path,
            "symbol": os.path.basename(file_path).replace(".parquet", ""),
            "timeframe": timeframe,
            "total_rows": 0,
            "is_clean": True,
            "corrupted_rows": 0,
            "issues": [],
            "non_midnight_timestamps": 0,
            "duplicate_timestamps": 0,
            "impossible_ohlc": 0,
            "extreme_single_day_spikes": 0,
            "future_timestamps": 0,
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

        # Detect time column or DatetimeIndex
        time_series = None
        if isinstance(df.index, pd.DatetimeIndex):
            time_series = df.index
        else:
            for col in ["Datetime", "Date", "timestamp", "Time"]:
                if col in df.columns:
                    time_series = df[col]
                    break

        if time_series is not None:
            ts_str = time_series.astype(str)
            # In 1D, timestamps MUST be midnight (00:00:00)
            if timeframe.lower() in ("1d", "daily"):
                bad_ts_mask = ts_str.str.contains(r"\d{2}:\d{2}:\d{2}\.\d+") | ts_str.str.contains(r" (?!00:00:00)\d{2}:\d{2}:\d{2}")
                bad_ts_count = int(bad_ts_mask.sum())
                if bad_ts_count > 0:
                    result["non_midnight_timestamps"] = bad_ts_count
                    result["corrupted_rows"] += bad_ts_count
                    result["is_clean"] = False
                    result["issues"].append(f"NON_MIDNIGHT_TIMESTAMPS: {bad_ts_count} rows have intraday/microsecond times in daily file")

            # Duplicate timestamps
            try:
                dt_vals = pd.to_datetime(time_series, utc=True)
                dup_count = int(dt_vals.duplicated().sum())
                if dup_count > 0:
                    result["duplicate_timestamps"] = dup_count
                    result["is_clean"] = False
                    result["issues"].append(f"DUPLICATE_TIMESTAMPS: {dup_count} duplicate timestamps")

                # Future timestamps beyond current time
                now_utc = pd.Timestamp.now(tz="UTC")
                future_count = int((dt_vals > (now_utc + pd.Timedelta(days=1))).sum())
                if future_count > 0:
                    result["future_timestamps"] = future_count
                    result["is_clean"] = False
                    result["issues"].append(f"FUTURE_TIMESTAMPS: {future_count} bars in future")
            except Exception:
                pass

        # Check impossible OHLC (High < Low, Close > High, Low > Open, etc.)
        req_cols = ["Open", "High", "Low", "Close"]
        if all(c in df.columns for c in req_cols):
            h = pd.to_numeric(df["High"], errors="coerce")
            l = pd.to_numeric(df["Low"], errors="coerce")
            c = pd.to_numeric(df["Close"], errors="coerce")
            o = pd.to_numeric(df["Open"], errors="coerce")

            impossible = (h < l) | (c > h * 1.002) | (c < l * 0.998) | (o > h * 1.002) | (o < l * 0.998) | (l <= 0)
            imp_count = int(impossible.fillna(False).sum())
            if imp_count > 0:
                result["impossible_ohlc"] = imp_count
                result["corrupted_rows"] += imp_count
                result["is_clean"] = False
                result["issues"].append(f"IMPOSSIBLE_OHLC: {imp_count} rows violate High >= max(Open, Close) or Low <= min(Open, Close)")

        # Single-day 50%+ spike immediately reverting (synthetic injection signature)
        if "Close" in df.columns and len(df) > 5 and timeframe.lower() in ("1d", "daily"):
            close = pd.to_numeric(df["Close"], errors="coerce")
            pct_change = close.pct_change(fill_method=None)
            next_pct = close.pct_change(periods=-1, fill_method=None)
            spikes = (pct_change > 0.45) & (next_pct < -0.30)
            spike_count = int(spikes.fillna(False).sum())
            if spike_count > 0:
                result["extreme_single_day_spikes"] = spike_count
                result["is_clean"] = False
                result["issues"].append(f"SYNTHETIC_PRICE_SPIKE: {spike_count} extreme flash spike-and-revert detected")

        return result

    @classmethod
    def audit_timeframe_directory(cls, dir_path: str, timeframe: str) -> Dict[str, Any]:
        """Audits all parquet files in a directory for a given timeframe."""
        files = glob.glob(os.path.join(dir_path, "*.parquet"))
        total = len(files)
        total_rows = 0
        clean = 0
        corrupted_files = []

        for f in files:
            res = cls.audit_file(f, timeframe=timeframe)
            total_rows += res["total_rows"]
            if res["is_clean"]:
                clean += 1
            else:
                corrupted_files.append(res)

        return {
            "timeframe": timeframe,
            "dir_path": dir_path,
            "total_files": total,
            "total_rows": total_rows,
            "clean_files": clean,
            "corrupted_count": len(corrupted_files),
            "corrupted_files": corrupted_files
        }

    @classmethod
    def audit_all_historical_stores(cls, base_dir: str = "data/history") -> List[Dict[str, Any]]:
        """Audits 1D, 5M, 15M, 30M, 1H directories and returns structured audit matrix."""
        results = []
        for tf in ["1d", "5m", "15m", "30m", "1h"]:
            tf_dir = os.path.join(base_dir, tf)
            if os.path.exists(tf_dir):
                r = cls.audit_timeframe_directory(tf_dir, tf)
                results.append(r)
        return results

    @classmethod
    def sanitize_file(cls, file_path: str, timeframe: str = "1d", backup: bool = True) -> Tuple[bool, int, str]:
        """
        Sanitizes a parquet file by stripping corrupted/synthetic rows and resaving clean data.
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

        clean_df = df.copy()

        # 1. 1D non-midnight filter
        if timeframe.lower() in ("1d", "daily"):
            time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in clean_df.columns), None)
            if time_col:
                ts_str = clean_df[time_col].astype(str)
                bad_ts_mask = ts_str.str.contains(r"\d{2}:\d{2}:\d{2}\.\d+") | ts_str.str.contains(r" (?!00:00:00)\d{2}:\d{2}:\d{2}")
                clean_df = clean_df[~bad_ts_mask]

        # 2. Impossible OHLC filter
        if all(c in clean_df.columns for c in ["Open", "High", "Low", "Close"]):
            h = pd.to_numeric(clean_df["High"], errors="coerce")
            l = pd.to_numeric(clean_df["Low"], errors="coerce")
            c = pd.to_numeric(clean_df["Close"], errors="coerce")
            valid_ohlc = (h >= l) & (c <= h * 1.002) & (c >= l * 0.998) & (l > 0)
            clean_df = clean_df[valid_ohlc.fillna(True)]

        # 3. Deduplicate
        time_col = next((c for c in ["Datetime", "Date", "timestamp"] if c in clean_df.columns), None)
        if time_col:
            clean_df = clean_df.drop_duplicates(subset=[time_col], keep="last")
            clean_df = clean_df.sort_values(time_col).reset_index(drop=True)
        elif isinstance(clean_df.index, pd.DatetimeIndex):
            clean_df = clean_df[~clean_df.index.duplicated(keep="last")]
            clean_df = clean_df.sort_index()

        rows_removed = orig_len - len(clean_df)
        if rows_removed > 0:
            if backup:
                backup_path = file_path + ".corrupt_bak"
                if not os.path.exists(backup_path):
                    shutil.copy2(file_path, backup_path)
            clean_df.to_parquet(file_path, index=isinstance(clean_df.index, pd.DatetimeIndex))
            return True, rows_removed, f"Sanitized successfully. Removed {rows_removed} rogue rows."
        return True, 0, "Already clean."
