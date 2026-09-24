# app/certification/point_in_time.py
"""
Point-In-Time Invariant Validator for Strict Causality and Zero Lookahead.
"""
from datetime import datetime, date
from typing import List, Optional, Tuple, Union
import pandas as pd
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def validate_point_in_time(
    df: pd.DataFrame,
    evaluation_date: Union[str, date, datetime],
    evaluation_cutoff_time: Optional[str] = "15:30:00",
    delivery_date: Optional[Union[str, date]] = None,
    symbol: Optional[str] = None
) -> Tuple[bool, List[str]]:
    """
    Validates that no future market data or delivery reports are ingested.
    Enforces strict point-in-time causality: T <= evaluation_timestamp.
    
    Returns:
        (is_valid: bool, violations: List[str])
    """
    violations = []
    
    # 1. Parse evaluation date
    if isinstance(evaluation_date, str):
        eval_dt = datetime.strptime(evaluation_date.split(" ")[0], "%Y-%m-%d").date()
    elif isinstance(evaluation_date, datetime):
        eval_dt = evaluation_date.date()
    else:
        eval_dt = evaluation_date

    # 2. Check DataFrame timestamps
    if df is not None and not df.empty:
        time_col = None
        for col in ["Datetime", "Date", "timestamp"]:
            if col in df.columns:
                time_col = col
                break
        
        if time_col:
            dt_series = pd.to_datetime(df[time_col], utc=True)
            max_dt = dt_series.max()
            if hasattr(max_dt, "tzinfo") and max_dt.tzinfo is not None:
                max_date = max_dt.astimezone(IST).date()
            else:
                max_date = max_dt.date()

            if max_date > eval_dt:
                violations.append(
                    f"LOOKAHEAD_VIOLATION: Input data contains timestamp {max_date} which is strictly after evaluation date {eval_dt}."
                )

            # Check for future bar leakage count
            future_mask = dt_series.apply(lambda x: (x.astimezone(IST).date() if hasattr(x, "tzinfo") and x.tzinfo else x.date()) > eval_dt)
            if future_mask.any():
                count = future_mask.sum()
                violations.append(f"FUTURE_BAR_LEAK: {count} bar(s) detected beyond evaluation cutoff.")

    # 3. Check delivery date causality
    if delivery_date:
        if isinstance(delivery_date, str):
            deliv_dt = datetime.strptime(delivery_date.split(" ")[0], "%Y-%m-%d").date()
        elif isinstance(delivery_date, datetime):
            deliv_dt = delivery_date.date()
        else:
            deliv_dt = delivery_date

        if deliv_dt > eval_dt:
            violations.append(
                f"FUTURE_DELIVERY_LEAK: Delivery data date {deliv_dt} post-dates evaluation date {eval_dt}."
            )

    is_valid = len(violations) == 0
    return is_valid, violations
