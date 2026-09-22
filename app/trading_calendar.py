# =====================================================================================
# app/trading_calendar.py
# STANDALONE TRADING CALENDAR SERVICE (TRADING DAYS & MARKET HOLIDAYS)
# =====================================================================================

import logging
from datetime import datetime, date, timedelta
from typing import Union, Set, Optional, Any
import pytz
import pandas as pd

logger = logging.getLogger("trading_calendar")
IST = pytz.timezone("Asia/Kolkata")

# Standard official NSE Market Holidays (YYYY-MM-DD)
NSE_HOLIDAYS_2026: Set[date] = {
    date(2026, 1, 26),  # Republic Day
    date(2026, 3, 10),  # Holi
    date(2026, 3, 30),  # Id-Ul-Fitr (Ramzan Id)
    date(2026, 4, 3),   # Good Friday
    date(2026, 4, 14),  # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),   # Maharashtra Day
    date(2026, 5, 27),  # Bakri Id
    date(2026, 6, 26),  # Muharram
    date(2026, 8, 15),  # Independence Day
    date(2026, 9, 14),  # Ganesh Chaturthi
    date(2026, 10, 2),  # Mahatma Gandhi Jayanti
    date(2026, 10, 20), # Dussehra
    date(2026, 11, 9),  # Diwali Laxmi Pujan
    date(2026, 11, 10), # Diwali Balipratipada
    date(2026, 11, 24), # Guru Nanak Jayanti
    date(2026, 12, 25), # Christmas
}


class TradingCalendar:
    """
    Cross-cutting Trading Calendar Service.
    Computes trading session differences skipping weekends and exchange holidays.
    Reusable across F&O expiry, SL/target calculations, backtesting, and corporate events.
    """

    def __init__(self, holidays: Optional[Set[date]] = None):
        self.holidays = holidays if holidays is not None else NSE_HOLIDAYS_2026

    def is_trading_day(self, dt: Union[datetime, date, str, Any]) -> bool:
        """Returns True if the given date is a valid trading session (not Saturday, Sunday, or Holiday)."""
        d = self._parse_date(dt)
        if d is None:
            return False
        if d.weekday() >= 5:  # Saturday or Sunday
            return False
        if d in self.holidays:
            return False
        return True

    def days_between(self, start: Union[datetime, date, str], end: Union[datetime, date, str]) -> int:
        """
        Computes signed trading session days between start and end.
        Returns:
            Positive int: end is in the future (+N trading days).
            Negative int: end is in the past (-N trading days).
            0: start and end are on the same trading day.
        """
        d_start = self._parse_date(start)
        d_end = self._parse_date(end)

        if not d_start or not d_end:
            return 0

        if d_start == d_end:
            return 0

        reverse = False
        if d_start > d_end:
            d_start, d_end = d_end, d_start
            reverse = True

        trading_days = 0
        curr = d_start + timedelta(days=1)
        while curr <= d_end:
            if self.is_trading_day(curr):
                trading_days += 1
            curr += timedelta(days=1)

        return -trading_days if reverse else trading_days

    @staticmethod
    def _parse_date(val: Union[datetime, date, str, Any]) -> Optional[date]:
        if val is None:
            return None
        if isinstance(val, date) and not isinstance(val, datetime):
            return val
        if isinstance(val, datetime):
            return val.date()
        if hasattr(val, "date") and callable(getattr(val, "date")):
            try:
                return val.date()
            except Exception:
                pass
        if isinstance(val, str):
            clean_str = val.strip().split("T")[0].split(" ")[0]
            try:
                return datetime.strptime(clean_str, "%Y-%m-%d").date()
            except Exception:
                return None
        return None


# Global Singleton Instance
default_trading_calendar = TradingCalendar()


def is_trading_day(val: Union[datetime, date, str]) -> bool:
    """
    Returns True if the given date is an official NSE trading day (not weekend or market holiday).
    """
    d = TradingCalendar._parse_date(val)
    if d is None:
        return False
    return default_trading_calendar.is_trading_day(d)


def get_latest_trading_date(val: Optional[Union[datetime, date, str]] = None) -> date:
    """
    Resolves to the most recent valid trading session date on or before the given date.
    E.g. Sunday -> Friday, Monday holiday -> Friday.
    """
    if val is None:
        curr = datetime.now(IST).date()
    else:
        curr = TradingCalendar._parse_date(val) or datetime.now(IST).date()

    while not default_trading_calendar.is_trading_day(curr):
        curr -= timedelta(days=1)
    return curr


def get_previous_trading_date(val: Optional[Union[datetime, date, str]] = None) -> date:
    """
    Resolves to the valid trading session date strictly prior to the given date.
    E.g. Monday -> Friday, Tuesday after Monday holiday -> Friday.
    """
    if val is None:
        curr = datetime.now(IST).date()
    else:
        curr = TradingCalendar._parse_date(val) or datetime.now(IST).date()

    curr -= timedelta(days=1)
    while not default_trading_calendar.is_trading_day(curr):
        curr -= timedelta(days=1)
    return curr


def get_next_trading_date(val: Optional[Union[datetime, date, str]] = None) -> date:
    """
    Resolves to the valid trading session date strictly after the given date.
    E.g. Friday -> Monday, day before holiday -> next business day.
    """
    if val is None:
        curr = datetime.now(IST).date()
    else:
        curr = TradingCalendar._parse_date(val) or datetime.now(IST).date()

    curr += timedelta(days=1)
    while not default_trading_calendar.is_trading_day(curr):
        curr += timedelta(days=1)
    return curr


def is_weekend_date(val: Union[datetime, date, str]) -> bool:
    """
    Returns True if the given date/timestamp lands on a Saturday (5) or Sunday (6).
    """
    d = TradingCalendar._parse_date(val)
    if d is None:
        return False
    return d.weekday() >= 5


def is_market_candle_eligible(val: Union[datetime, date, str]) -> bool:
    """
    CRITICAL INVARIANT: A candle is eligible ONLY if its timestamp belongs to an actual
    official NSE/BSE trading session.
    Saturday and Sunday are CATEGORICALLY INVALID.
    Exchange holidays are non-trading days.
    """
    d = TradingCalendar._parse_date(val)
    if d is None:
        return False
    if d.weekday() >= 5:
        return False
    return default_trading_calendar.is_trading_day(d)


from datetime import time as time_cls

def is_valid_market_session_timestamp(val: Union[datetime, str, pd.Timestamp]) -> bool:
    """
    CRITICAL INVARIANT: Returns True ONLY if the given timestamp belongs to:
    1. A valid weekday (Monday to Friday, Saturday=0, Sunday=0)
    2. An official NSE/BSE trading day (not an exchange holiday)
    3. Official NSE/BSE active trading session hours: 09:15 to 15:30 IST.
    """
    if val is None or pd.isna(val):
        return False
    if isinstance(val, str):
        val_str = val.strip()
        if not val_str:
            return False
        try:
            val = pd.to_datetime(val_str)
            if pd.isna(val):
                return False
        except Exception:
            return False

    try:
        if hasattr(val, "tzinfo"):
            if val.tzinfo is None:
                val = IST.localize(val)
            else:
                val = val.astimezone(IST)
        elif isinstance(val, pd.Timestamp):
            if val.tz is None:
                val = val.tz_localize(IST)
            else:
                val = val.tz_convert(IST)

        d = val.date()
        if not default_trading_calendar.is_trading_day(d):
            return False

        t = val.time()
        return time_cls(9, 15) <= t <= time_cls(15, 30)
    except Exception:
        return False


def sanitize_market_session_timestamp(val: Union[datetime, str, pd.Timestamp, None], fallback_date: Optional[date] = None) -> str:
    """
    CRITICAL HARD GLOBAL INVARIANT: STRICT MARKET SESSION TIMESTAMP SANITIZATION.
    Guarantees that ANY timestamp produced or persisted for trade closures (closed_at, exit_timestamp):
    1. Is strictly in Asia/Kolkata (IST) timezone.
    2. Belongs strictly to an official NSE/BSE trading day (Monday to Friday, non-holiday).
    3. Belongs strictly to active market session hours (09:15:00 to 15:30:00 IST).
    4. If date-only (e.g. "2026-09-18"), snaps to session close: "2026-09-18 15:30:00".
    5. If off-market or midnight (e.g. "2026-09-22 00:34:00" or weekend), snaps to 15:30:00 of the
       latest valid trading date on or strictly prior to that moment.
    6. Returns a standardized string in "YYYY-MM-DD HH:MM:SS" format.
    """
    if val is None or pd.isna(val):
        target_d = fallback_date or datetime.now(IST).date()
        latest_d = get_latest_trading_date(target_d)
        return f"{latest_d} 15:30:00"

    val_str = str(val).strip()
    if not val_str:
        target_d = fallback_date or datetime.now(IST).date()
        latest_d = get_latest_trading_date(target_d)
        return f"{latest_d} 15:30:00"

    # Date-only check: YYYY-MM-DD
    if len(val_str) == 10 and val_str.count("-") == 2 and not ("T" in val_str or " " in val_str or ":" in val_str):
        try:
            d = date.fromisoformat(val_str)
            latest_d = get_latest_trading_date(d)
            return f"{latest_d} 15:30:00"
        except Exception:
            pass

    try:
        if isinstance(val, (datetime, pd.Timestamp)):
            dt = val
        else:
            clean_str = val_str.replace("Z", "+00:00").replace(" IST", "")
            dt = pd.to_datetime(clean_str)

        if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
            dt_ist = dt.astimezone(IST)
        elif hasattr(dt, "tz") and dt.tz is not None:
            dt_ist = dt.tz_convert(IST)
        else:
            dt_ist = IST.localize(pd.to_datetime(dt).to_pydatetime())

        d = dt_ist.date()
        t = dt_ist.time()

        if default_trading_calendar.is_trading_day(d):
            if time_cls(9, 15) <= t <= time_cls(15, 30):
                return dt_ist.strftime("%Y-%m-%d %H:%M:%S")
            elif t < time_cls(9, 15):
                # Pre-market / midnight: previous session close
                prev_d = get_previous_trading_date(d)
                return f"{prev_d} 15:30:00"
            else:
                # Post-market: today's session close
                return f"{d} 15:30:00"
        else:
            # Weekend / exchange holiday: latest valid trading session close
            latest_d = get_latest_trading_date(d)
            return f"{latest_d} 15:30:00"
    except Exception as e:
        logger.warning(f"Failed to sanitize market session timestamp '{val}': {e}. Falling back to latest session close.")
        target_d = fallback_date or datetime.now(IST).date()
        latest_d = get_latest_trading_date(target_d)
        return f"{latest_d} 15:30:00"



def enforce_trading_day_candles(df, symbol: str = "") -> "pd.DataFrame":
    """
    CRITICAL HARD GLOBAL INVARIANT: TRADING SESSION CANDLE ENFORCEMENT — SYSTEM-WIDE.
    - Weekend timestamps (Saturday = 0, Sunday = 0) are strictly forbidden.
    - Official NSE/BSE exchange holidays are strictly forbidden.
    - Off-market / midnight ghost bars outside 09:15–15:30 IST are strictly purged for intraday data.
    - Returns cleaned DataFrame containing ONLY official trading session data in IST.
    """
    if df is None or not hasattr(df, "empty") or df.empty:
        return df

    import pandas as pd
    time_col = None
    for candidate in ("Date", "Datetime", "timestamp", "time"):
        if candidate in df.columns:
            time_col = candidate
            break

    try:
        if time_col is not None:
            ts_series = pd.to_datetime(df[time_col], errors="coerce")
            if hasattr(ts_series.dt, "tz") and ts_series.dt.tz is not None:
                ts_series = ts_series.dt.tz_convert("Asia/Kolkata")
            else:
                ts_series = ts_series.dt.tz_localize("Asia/Kolkata")
        elif isinstance(df.index, pd.DatetimeIndex):
            dt_idx = df.index.tz_convert("Asia/Kolkata") if df.index.tz is not None else df.index.tz_localize("Asia/Kolkata")
            ts_series = pd.Series(dt_idx, index=df.index)
        else:
            ts_series = pd.to_datetime(df.index, errors="coerce")
            if hasattr(ts_series.dt, "tz") and ts_series.dt.tz is not None:
                ts_series = ts_series.dt.tz_convert("Asia/Kolkata")
            else:
                ts_series = ts_series.dt.tz_localize("Asia/Kolkata")

        if ts_series is None or len(ts_series) == 0:
            return df

        # 1. Saturday = 5, Sunday = 6
        is_weekend = (ts_series.dt.weekday >= 5)

        # 2. Official NSE market holidays
        is_holiday = ts_series.dt.date.isin(default_trading_calendar.holidays)

        # 3. Off-market hours (midnight ghost bars, pre/post market outside 09:15-15:30 IST)
        # Off-market filtering strictly applies to INTRADAY datasets (5m, 15m, 30m, 1h).
        # Daily EOD datasets (at most 1 bar per day, or 2 if today has a live bar appended)
        # have timestamps at 00:00:00, 05:30:00, or 15:30:00 by exchange/provider convention.
        # Daily candles must NEVER be purged as "off-market" hours.
        valid_dates = ts_series.dt.date.dropna()
        date_counts = valid_dates.value_counts()
        is_intraday_dataset = bool((date_counts > 2).any() or (len(valid_dates) > 5 and date_counts.mean() > 1.5))
        if is_intraday_dataset:
            is_off_hours = (ts_series.dt.time < time_cls(9, 15)) | (ts_series.dt.time > time_cls(15, 30))
        else:
            is_off_hours = pd.Series(False, index=df.index)

        # 4. Corrupt / NaT timestamps
        is_corrupt = ts_series.isna()

        is_invalid = is_weekend | is_holiday | is_off_hours | is_corrupt
        if is_invalid.any():
            dropped_count = int(is_invalid.sum())
            sym_tag = f" for {symbol}" if symbol else ""
            df_clean = df[~is_invalid].copy()
            latest_valid_str = "None"
            if not df_clean.empty:
                if time_col and time_col in df_clean.columns:
                    latest_valid_str = str(df_clean[time_col].iloc[-1])[:19]
                elif isinstance(df_clean.index, pd.DatetimeIndex):
                    latest_valid_str = str(df_clean.index[-1])[:19]
                else:
                    latest_valid_str = str(df_clean.index[-1])[:19]

            reasons = []
            if is_weekend.any(): reasons.append(f"{int(is_weekend.sum())} weekend")
            if is_holiday.any(): reasons.append(f"{int(is_holiday.sum())} holiday")
            if is_off_hours.any(): reasons.append(f"{int(is_off_hours.sum())} off-market/midnight")
            if is_corrupt.any(): reasons.append(f"{int(is_corrupt.sum())} corrupt/NaT")
            reason_str = ", ".join(reasons)

            logger.warning(
                f"🚫 [TRADING SESSION ENFORCEMENT] Purged {dropped_count} invalid candle(s){sym_tag} ({reason_str}). "
                f"Latest valid trading candle: {latest_valid_str}. Continuing using valid trading session data."
            )
            if not isinstance(df_clean.index, pd.DatetimeIndex):
                df_clean = df_clean.reset_index(drop=True)
            if hasattr(df, "attrs"):
                df_clean.attrs = dict(df.attrs)
            return df_clean
    except Exception as err:
        logger.error(f"❌ [TRADING SESSION ENFORCEMENT] Error enforcing session rules on candles: {err}")

    return df

