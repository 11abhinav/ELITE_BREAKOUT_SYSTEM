import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import pytz
import pandas as pd

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

class DateStatus:
    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"
    UNKNOWN = "UNKNOWN"

class EarningsSeverity:
    HIGH_TODAY = "HIGH_TODAY"      # Results today 🔴
    HIGH_SOON = "HIGH_SOON"        # 1-2 days 🟠
    MEDIUM_WEEK = "MEDIUM_WEEK"    # 3-5 days 🟡
    NONE = "NONE"                  # > 5 days 🟢

class EarningsProvider(ABC):
    @abstractmethod
    def fetch_earnings_date(self, symbol: str) -> Tuple[Optional[date], str]:
        """Returns (earnings_date, date_status)."""
        pass

class YahooEarningsProvider(EarningsProvider):
    def fetch_earnings_date(self, symbol: str) -> Tuple[Optional[date], str]:
        """Fetches upcoming earnings date via yfinance."""
        import yfinance as yf
        clean_upper = symbol.strip().upper()
        ticker_str = clean_upper if clean_upper.endswith(".NS") or clean_upper.endswith(".BO") else f"{clean_upper}.NS"
        
        try:
            t = yf.Ticker(ticker_str)
            
            # Method A: t.calendar dict/df
            cal = t.calendar
            if cal is not None and len(cal) > 0:
                if isinstance(cal, dict) and "Earnings Date" in cal:
                    ed_list = cal["Earnings Date"]
                    if ed_list and len(ed_list) > 0:
                        first_ed = ed_list[0]
                        if isinstance(first_ed, (datetime, date)):
                            val_date = first_ed.date() if isinstance(first_ed, datetime) else first_ed
                            return val_date, DateStatus.ESTIMATED
                elif isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                    vals = cal.loc["Earnings Date"].values
                    if len(vals) > 0 and pd.notnull(vals[0]):
                        dt_val = pd.to_datetime(vals[0])
                        return dt_val.date(), DateStatus.ESTIMATED

            # Method B: t.earnings_dates DataFrame
            ed_df = t.earnings_dates
            if ed_df is not None and not ed_df.empty:
                now_date = datetime.now(IST).date()
                future_dates = [d.date() for d in ed_df.index if d.date() >= now_date]
                if future_dates:
                    return min(future_dates), DateStatus.ESTIMATED
                    
        except Exception as e:
            logger.debug(f"Yahoo earnings fetch failed for {symbol}: {e}")
            
        return None, DateStatus.UNKNOWN


class EarningsCalendarService:
    """
    Service managing upcoming earnings dates cache in PostgreSQL with provider abstraction.
    Scanners strictly read from DB cache at runtime for non-blocking execution.
    """
    def __init__(self, provider: Optional[EarningsProvider] = None):
        self.provider = provider or YahooEarningsProvider()

    def refresh_earnings_calendar(self, symbols: List[str]) -> int:
        """
        Refreshes earnings calendar for a list of symbols and caches results in PostgreSQL.
        Intended to run daily at 08:00 AM IST before scanning.
        """
        if not symbols:
            return 0

        updated_count = 0
        logger.info(f"📅 [EARNINGS CALENDAR] Starting daily refresh for {len(symbols)} symbols...")
        
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        
        def _fetch_one(sym):
            ed, status = self.provider.fetch_earnings_date(sym)
            return sym, ed, status

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_fetch_one, s) for s in symbols]
            for fut in as_completed(futures):
                try:
                    sym, ed, status = fut.result()
                    if ed:
                        results[sym] = (ed, status)
                except Exception as e:
                    logger.debug(f"Error fetching earnings date: {e}")

        # Batch insert/upsert into PostgreSQL
        if results:
            try:
                from database import get_db_connection
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        for sym, (ed, status) in results.items():
                            cur.execute("""
                                INSERT INTO earnings_calendar (symbol, earnings_date, date_status, updated_at)
                                VALUES (%s, %s, %s, NOW())
                                ON CONFLICT (symbol) DO UPDATE SET
                                    earnings_date = EXCLUDED.earnings_date,
                                    date_status = EXCLUDED.date_status,
                                    updated_at = NOW()
                            """, (sym, ed, status))
                        conn.commit()
                        updated_count = len(results)
                        logger.info(f"✅ [EARNINGS CALENDAR] Cached earnings dates for {updated_count} symbols in PostgreSQL.")
            except Exception as e:
                logger.error(f"❌ Failed to persist earnings_calendar to DB: {e}")

        return updated_count

    def get_earnings_info(self, symbol: str, target_date: Optional[date] = None) -> Dict:
        """
        Reads earnings info for a symbol from PostgreSQL cache and computes graded risk classification.
        Non-blocking, fast DB lookup.
        """
        clean_upper = symbol.strip().upper()
        if clean_upper.endswith(".NS"): clean_upper = clean_upper[:-3]
        if clean_upper.endswith(".BO"): clean_upper = clean_upper[:-3]

        if target_date is None:
            target_date = datetime.now(IST).date()
        elif isinstance(target_date, datetime):
            target_date = target_date.date()

        default_response = {
            "earnings_flag": False,
            "days_to_earnings": 999,
            "earnings_date": None,
            "earnings_severity": EarningsSeverity.NONE,
            "date_status": DateStatus.UNKNOWN,
            "warning_msg": ""
        }

        try:
            from database import get_db_connection
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT earnings_date, date_status FROM earnings_calendar WHERE symbol = %s",
                        (clean_upper,)
                    )
                    row = cur.fetchone()
                    if not row or not row[0]:
                        return default_response
                        
                    ed_date = row[0]
                    date_status = row[1] or DateStatus.ESTIMATED
                    
                    diff_days = (ed_date - target_date).days
                    
                    # Determine severity & warning
                    if diff_days == 0:
                        severity = EarningsSeverity.HIGH_TODAY
                        warning_msg = "🔴 HIGH EARNINGS RISK: Results expected today. Technical stop-loss may not protect against overnight gaps."
                        earnings_flag = True
                    elif 1 <= diff_days <= 2:
                        severity = EarningsSeverity.HIGH_SOON
                        warning_msg = f"🟠 HIGH EARNINGS RISK: Results expected in {diff_days} day(s) ({ed_date}). Elevated overnight gap risk. Review position size and risk before entry."
                        earnings_flag = True
                    elif 3 <= diff_days <= 5:
                        severity = EarningsSeverity.MEDIUM_WEEK
                        warning_msg = f"🟡 MEDIUM EARNINGS RISK: Results expected in {diff_days} days ({ed_date}). Elevated gap risk. Review risk before entry."
                        earnings_flag = True
                    elif -1 <= diff_days < 0:
                        severity = EarningsSeverity.MEDIUM_WEEK
                        warning_msg = f"🟡 RECENT EARNINGS: Results declared {abs(diff_days)} day ago ({ed_date}). Watch for post-earnings volatility."
                        earnings_flag = True
                    else:
                        severity = EarningsSeverity.NONE
                        warning_msg = ""
                        earnings_flag = False

                    return {
                        "earnings_flag": earnings_flag,
                        "days_to_earnings": diff_days,
                        "earnings_date": ed_date.strftime("%Y-%m-%d"),
                        "earnings_severity": severity,
                        "date_status": date_status,
                        "warning_msg": warning_msg
                    }
        except Exception as e:
            logger.debug(f"DB lookup failed for earnings_calendar: {e}")
            return default_response

# Global Singleton
earnings_calendar_service = EarningsCalendarService()
