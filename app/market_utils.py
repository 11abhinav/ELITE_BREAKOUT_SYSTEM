from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

def is_market_open(now_dt: datetime = None) -> bool:
    """
    Returns True ONLY if the current time is between 09:15 and 15:30 on a weekday (Mon-Fri).
    """
    if now_dt is None:
        now_dt = datetime.now(IST)
    
    if now_dt.weekday() >= 5:  # 5 is Saturday, 6 is Sunday
        return False
        
    current_time = now_dt.time()
    return dt_time(9, 15) <= current_time <= dt_time(15, 30)

def is_within_custom_hours(start_time: dt_time, end_time: dt_time, now_dt: datetime = None) -> bool:
    """
    Returns True if the current time is between start_time and end_time on a weekday.
    Useful for scanners like intraday or live that have custom start/end boundaries.
    """
    if now_dt is None:
        now_dt = datetime.now(IST)
        
    if now_dt.weekday() >= 5:
        return False
        
    current_time = now_dt.time()
    return start_time <= current_time <= end_time
