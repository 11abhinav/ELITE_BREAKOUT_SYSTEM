import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import pytz
import pandas as pd

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

# [VERSION: PHASE2_EARNINGS_UNVERIFIED_v1.0] Added UNVERIFIED status/severity for missing earnings dates to prevent false green signals.
class DateStatus:
    CONFIRMED = "CONFIRMED"
    ESTIMATED = "ESTIMATED"
    UNVERIFIED = "UNVERIFIED"
    UNKNOWN = "UNKNOWN"

class EarningsSeverity:
    HIGH_TODAY = "HIGH_TODAY"      # Results today 🔴
    HIGH_SOON = "HIGH_SOON"        # 1-2 days 🟠
    MEDIUM_WEEK = "MEDIUM_WEEK"    # 3-5 days 🟡
    NONE = "NONE"                  # > 5 days 🟢 (Confirmed no earnings soon)
    UNVERIFIED = "UNVERIFIED"      # Missing / Unverified date data ⚠️

class EarningsProvider(ABC):
    @abstractmethod
    def fetch_earnings_date(self, symbol: str) -> Tuple[Optional[date], str]:
        """Returns (earnings_date, date_status)."""
        pass

class YahooEarningsProvider(EarningsProvider):
    def fetch_earnings_date(self, symbol: str) -> Tuple[Optional[date], str]:
        """Fetches upcoming earnings date via yfinance."""
        import yfinance as yf
        from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit
        clean_upper = symbol.strip().upper()
        ticker_str = clean_upper if clean_upper.endswith(".NS") or clean_upper.endswith(".BO") else f"{clean_upper}.NS"
        
        try:
            yf_acquire(context=f"YahooEarningsProvider | {symbol}")
            try:
                t = yf.Ticker(ticker_str)
                cal = t.calendar
                ed_df = t.earnings_dates
            finally:
                yf_release()

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

            if ed_df is not None and not ed_df.empty:
                now_date = datetime.now(IST).date()
                future_dates = [d.date() for d in ed_df.index if d.date() >= now_date]
                if future_dates:
                    return min(future_dates), DateStatus.ESTIMATED
                    
        except Exception as e:
            msg = str(e).lower()
            if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                record_rate_limit(context=f"YahooEarningsProvider | {symbol}")
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
        Refreshes earnings calendar for symbols and caches results in PostgreSQL.
        Intended to run daily post-market close (15:30-18:00 IST).
        - Priority 1: Stocks with results expected TODAY (re-checked post-market close).
        - Priority 2: Symbols uncached / older than 45d (known dates) or 7d (missing dates).
        - Gentle rate-limiting: 2 workers, 0.3s delay.
        """
        if not symbols:
            return 0

        # ── 1. Priority-based Symbol Selection ──────────────────────────────
        uncached_symbols = []
        try:
            from database import get_connection
            today_date = datetime.now(IST).date()
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Priority 1: Stocks whose results were scheduled for TODAY
                    cur.execute("SELECT symbol FROM earnings_calendar WHERE earnings_date = %s", (today_date,))
                    today_expected = {r[0].strip().upper() for r in cur.fetchall()}

                    # Priority 2: Recently updated symbols to SKIP (45d for known dates != today, 7d for missing)
                    cur.execute("""
                        SELECT symbol FROM earnings_calendar 
                        WHERE (earnings_date IS NOT NULL AND earnings_date != %s AND updated_at >= NOW() - INTERVAL '45 days')
                           OR (earnings_date IS NULL AND updated_at >= NOW() - INTERVAL '7 days')
                    """, (today_date,))
                    recently_valid = {r[0].strip().upper() for r in cur.fetchall()}

                    priority_symbols = [s for s in symbols if s.strip().upper() in today_expected]
                    other_symbols = [s for s in symbols if s.strip().upper() not in recently_valid and s.strip().upper() not in today_expected]

                    uncached_symbols = priority_symbols + other_symbols
                    skipped_count = len(symbols) - len(uncached_symbols)

                    if priority_symbols:
                        logger.info(f"🎯 [EARNINGS CALENDAR] Priority 1: Re-checking {len(priority_symbols)} stocks scheduled for results TODAY post-market.")
                    if skipped_count > 0:
                        logger.info(f"📅 [EARNINGS CALENDAR] Skipping {skipped_count}/{len(symbols)} symbols (cached within 45d for known dates / 7d for missing).")
        except Exception as e:
            logger.warning(f"⚠️ DB pre-check failed for earnings_calendar: {e}. Processing all symbols.")
            uncached_symbols = symbols

        if not uncached_symbols:
            logger.info("✅ [EARNINGS CALENDAR] All symbols fresh in PostgreSQL cache (45d/7d TTL). Nothing to fetch!")
            return 0

        updated_count = 0
        logger.info(f"📅 [EARNINGS CALENDAR] Starting 21:00 IST refresh for {len(uncached_symbols)} symbols (2 workers, 0.3s throttle)...")

        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}

        def _fetch_one(sym):
            time.sleep(0.3)  # Gentle delay between requests
            ed, status = self.provider.fetch_earnings_date(sym)
            return sym, ed, status

        # Gentle execution with 2 workers to avoid IP throttling
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_fetch_one, s) for s in uncached_symbols]
            for fut in as_completed(futures):
                try:
                    sym, ed, status = fut.result()
                    if ed:
                        results[sym] = (ed, status)
                except Exception as e:
                    logger.debug(f"Error fetching earnings date for {sym}: {e}")

        # Batch insert/upsert into PostgreSQL
        if results:
            try:
                from database import get_connection
                with get_connection() as conn:
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

        # [VERSION: PHASE2_EARNINGS_UNVERIFIED_v1.0] Default response for missing/unverified dates returns UNVERIFIED severity to prevent false green safety signals.
        default_response = {
            "earnings_flag": False,
            "days_to_earnings": 999,
            "earnings_date": None,
            "earnings_severity": EarningsSeverity.UNVERIFIED,
            "date_status": DateStatus.UNVERIFIED,
            "warning_msg": "⚠️ UNVERIFIED EARNINGS: No reliable upcoming earnings date available in calendar."
        }

        try:
            from database import get_connection
            with get_connection() as conn:
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
            logger.warning(f"⚠️ DB lookup failed for earnings_calendar: {e}")
            return default_response


# Global Singleton
earnings_calendar_service = EarningsCalendarService()


# =====================================================================================
# STANDALONE RUNNER  (called by scheduler at 08:00 & 18:00 IST and by admin trigger)
# =====================================================================================
import threading
_scan_lock = threading.Lock()


def run_earnings_calendar_refresh() -> dict:
    """
    Refresh earnings_calendar table for all watchlist + excluded symbols.
    Lock-protected to prevent concurrent runs.
    Tracks progress in scanner_health table.
    Returns: { "total_count": N, "updated_count": M }
    """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("📅 Earnings Calendar refresh already running. Skipping.")
        raise RuntimeError("Earnings Calendar is already actively running!")

    try:
        from database import upsert_scanner_health
        from config import WATCHLIST_PATH
        import os

        upsert_scanner_health(
            "Earnings Calendar", "RUNNING",
            error_msg="Earnings Calendar refresh in progress..."
        )
        logger.info("📅 [EARNINGS CALENDAR] Starting scheduled refresh...")

        # ── Build symbol universe ────────────────────────────────────────────────
        symbols_set: set = set()

        if os.path.exists(WATCHLIST_PATH):
            try:
                df = pd.read_parquet(WATCHLIST_PATH)
                if "Stock" in df.columns:
                    symbols_set.update(df["Stock"].dropna().unique().tolist())
            except Exception as e:
                logger.warning(f"📅 Failed to read watchlist parquet: {e}")

        excluded_paths = [
            os.path.join(os.path.dirname(WATCHLIST_PATH), "elite_fundamental_watchlist_excluded.csv"),
            os.path.join(os.path.dirname(WATCHLIST_PATH), "elite_fundamental_watchlist-excluded.csv"),
            WATCHLIST_PATH.replace(".parquet", "_excluded.csv"),
        ]
        for exc_path in excluded_paths:
            if os.path.exists(exc_path):
                try:
                    df_ex = pd.read_csv(exc_path)
                    if "Stock" in df_ex.columns:
                        symbols_set.update(df_ex["Stock"].dropna().tolist())
                    break
                except Exception as e:
                    logger.warning(f"📅 Failed to read exclusion list {exc_path}: {e}")

        symbols = sorted(symbols_set)
        total_count = len(symbols)

        if not symbols:
            logger.warning("📅 [EARNINGS CALENDAR] No symbols found — watchlist may not be ready yet.")
            upsert_scanner_health(
                "Earnings Calendar", "OK",
                last_success=datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S"),
                total_count=0, processed_count=0,
                error_msg="No symbols in watchlist"
            )
            return {"total_count": 0, "updated_count": 0}

        logger.info(f"📅 [EARNINGS CALENDAR] Refreshing for {total_count} symbols via Yahoo Finance...")

        # ── Run the refresh ─────────────────────────────────────────────────────
        updated_count = earnings_calendar_service.refresh_earnings_calendar(symbols)

        # ── Update health ────────────────────────────────────────────────────────
        upsert_scanner_health(
            "Earnings Calendar", "OK",
            last_success=datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S"),
            total_count=total_count,
            processed_count=updated_count,
            error_msg=f"Updated {updated_count}/{total_count} symbols"
        )
        logger.info(f"✅ [EARNINGS CALENDAR] Refresh complete — {updated_count}/{total_count} symbols updated.")
        return {"total_count": total_count, "updated_count": updated_count}

    except RuntimeError:
        raise  # propagate lock-contention error cleanly
    except Exception as e:
        logger.exception("❌ [EARNINGS CALENDAR] Refresh failed")
        try:
            from database import upsert_scanner_health
            upsert_scanner_health(
                "Earnings Calendar", "DOWN",
                error_msg=str(e)[:500]
            )
        except Exception:
            pass
        raise
    finally:
        _scan_lock.release()
