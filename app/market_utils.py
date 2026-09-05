from datetime import datetime, date, timedelta, time as dt_time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

def is_market_open(now_dt: datetime = None) -> bool:
    """
    Returns True ONLY if the current time is between 09:15 and 15:30 on an official trading day (Mon-Fri, non-holiday).
    """
    if now_dt is None:
        now_dt = datetime.now(IST)
    
    if now_dt.weekday() >= 5:  # 5 is Saturday, 6 is Sunday
        return False
        
    try:
        from trading_calendar import default_trading_calendar
        if not default_trading_calendar.is_trading_day(now_dt):
            return False
    except Exception:
        pass

    current_time = now_dt.time()
    return dt_time(9, 15) <= current_time <= dt_time(15, 30)

def is_within_custom_hours(start_time: dt_time, end_time: dt_time, now_dt: datetime = None) -> bool:
    """
    Returns True if the current time is between start_time and end_time on an official trading day.
    """
    if now_dt is None:
        now_dt = datetime.now(IST)
        
    if now_dt.weekday() >= 5:
        return False
        
    try:
        from trading_calendar import default_trading_calendar
        if not default_trading_calendar.is_trading_day(now_dt):
            return False
    except Exception:
        pass

    current_time = now_dt.time()
    return start_time <= current_time <= end_time


def get_expected_latest_trading_date(now_dt: datetime = None) -> date:
    """
    Returns the expected date of the latest completed daily bar.
    - If now_dt is during or after market hours (Mon-Fri after 09:15 AM IST on trading days): returns today's date.
    - If now_dt is PRE-MARKET or weekend/holiday: returns the date of the last completed trading day.
    """
    if now_dt is None:
        now_dt = datetime.now(IST)
    
    from trading_calendar import default_trading_calendar
    current_time = now_dt.time()
    # If official trading day and time >= 09:15 AM IST, expected bar is TODAY
    if default_trading_calendar.is_trading_day(now_dt) and current_time >= dt_time(9, 15):
        return now_dt.date()
    
    # Otherwise (Pre-market morning before 9:15 AM IST or Weekend/Holiday), expected bar is from PREVIOUS trading day
    candidate = now_dt.date() - timedelta(days=1)
    while not default_trading_calendar.is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def get_expected_latest_closed_daily_bar(now_dt: datetime = None) -> date:
    """
    Institutional trading-calendar helper returning the date of the latest COMPLETED daily session bar.
    - During active market hours (09:15–15:30 IST) or pre-market (<09:15 IST): today's daily session is NOT closed yet.
      The latest completed closed daily bar is from the PREVIOUS completed trading session (e.g. Friday for Monday).
    - After market close (>=15:30 IST) on an official trading day: today's daily bar IS closed, so returns today's date.
    - On weekends/holidays: returns the date of the last completed trading day (e.g. Friday).
    """
    if now_dt is None:
        now_dt = datetime.now(IST)
    
    from trading_calendar import default_trading_calendar
    current_time = now_dt.time()
    
    # If after 15:30 IST on an official trading day, today's session is CLOSED
    if default_trading_calendar.is_trading_day(now_dt) and current_time >= dt_time(15, 30):
        return now_dt.date()
    
    # Otherwise, the latest completed closed daily bar is from the PREVIOUS trading session
    candidate = now_dt.date() - timedelta(days=1)
    while not default_trading_calendar.is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def evaluate_data_staleness(latest_bar_dt, now_dt: datetime = None) -> dict:
    """
    Evaluates whether latest_bar_dt is stale based on current time context.
    - During market hours or pre-market: expected closed daily bar is from the previous completed trading day.
    - Post-market (after 15:30 IST): expected closed daily bar is today's completed session.
    """
    if now_dt is None:
        now_dt = datetime.now(IST)
    
    if latest_bar_dt is None:
        return {
            "is_stale": True,
            "latest_available": "NONE",
            "expected_date": str(get_expected_latest_closed_daily_bar(now_dt)),
            "stale_age_days": 999,
            "message": "Data timestamp is missing or invalid"
        }
    
    latest_date = latest_bar_dt.date() if hasattr(latest_bar_dt, 'date') else latest_bar_dt
    expected_date = get_expected_latest_closed_daily_bar(now_dt)
    
    if latest_date >= expected_date:
        is_stale = False
        message = f"Data fresh (Available: {latest_bar_dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(latest_bar_dt, 'strftime') else latest_date}, Expected: {expected_date})"
    else:
        is_stale = True
        message = f"Data stale: Available till {latest_bar_dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(latest_bar_dt, 'strftime') else latest_date} (Expected at least {expected_date})"
    
    stale_age_days = (expected_date - latest_date).days if expected_date > latest_date else 0
    
    return {
        "is_stale": is_stale,
        "latest_available": latest_bar_dt.strftime('%Y-%m-%d %H:%M:%S') if hasattr(latest_bar_dt, 'strftime') else str(latest_date),
        "expected_date": str(expected_date),
        "stale_age_days": stale_age_days,
        "message": message
    }

