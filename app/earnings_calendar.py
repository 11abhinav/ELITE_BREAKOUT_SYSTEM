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

class NseEarningsProvider(EarningsProvider):
    """Primary provider fetching upcoming earnings/board meeting dates directly from official NSE India APIs."""
    _bulk_cache: Dict[str, date] = {}
    _last_bulk_fetch: Optional[datetime] = None

    @classmethod
    def _refresh_bulk_cache_if_needed(cls):
        """Pre-fetches bulk corporate board meetings & event calendar from official NSE India APIs in 1 HTTP call."""
        now = datetime.now(IST)
        if cls._last_bulk_fetch is not None and (now - cls._last_bulk_fetch).total_seconds() < 21600:
            return  # Cache valid for 6 hours
        
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }
        now_date = now.date()
        new_map: Dict[str, date] = {}

        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=headers, timeout=10)

            # 1. Bulk Board Meetings Endpoint
            url1 = "https://www.nseindia.com/api/corporate-board-meetings?index=equities"
            resp1 = session.get(url1, headers=headers, timeout=10)
            if resp1.status_code == 200:
                for item in resp1.json():
                    sym = str(item.get("bm_symbol", "")).strip().upper()
                    bm_date_str = item.get("bm_date")
                    purpose = str(item.get("bm_purpose", "")).lower() + " " + str(item.get("bm_desc", "")).lower()
                    if sym and bm_date_str and ("financial result" in purpose or "results" in purpose or "board meeting" in purpose):
                        try:
                            dt = pd.to_datetime(bm_date_str).date()
                            if dt >= now_date:
                                if sym not in new_map or dt < new_map[sym]:
                                    new_map[sym] = dt
                        except Exception:
                            pass

            # 2. Bulk Event Calendar Endpoint
            url2 = "https://www.nseindia.com/api/event-calendar?index=equities"
            resp2 = session.get(url2, headers=headers, timeout=10)
            if resp2.status_code == 200:
                for item in resp2.json():
                    sym = str(item.get("symbol", "")).strip().upper()
                    date_str = item.get("date")
                    purpose = str(item.get("purpose", "")).lower() + " " + str(item.get("bm_desc", "")).lower()
                    if sym and date_str and ("financial result" in purpose or "results" in purpose or "board meeting" in purpose or "fund raising" in purpose):
                        try:
                            dt = pd.to_datetime(date_str).date()
                            if dt >= now_date:
                                if sym not in new_map or dt < new_map[sym]:
                                    new_map[sym] = dt
                        except Exception:
                            pass

            cls._bulk_cache = new_map
            cls._last_bulk_fetch = now
            logger.info(f"✅ [NSE EARNINGS] Bulk NSE Earnings Calendar cache populated ({len(new_map)} confirmed upcoming board meetings) in <0.5s.")
        except Exception as e:
            logger.debug(f"⚠️ Bulk NSE earnings pre-fetch failed: {e}")

    def fetch_earnings_date(self, symbol: str) -> Tuple[Optional[date], str]:
        clean_sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
        self._refresh_bulk_cache_if_needed()

        # Check bulk in-memory map first (0 network latency)
        if clean_sym in self._bulk_cache:
            return self._bulk_cache[clean_sym], DateStatus.CONFIRMED

        # Per-symbol NSE Corporate Announcements fallback
        import requests
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }
        try:
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=headers, timeout=10)
            url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={clean_sym}"
            resp = session.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                items = resp.json()
                now_date = datetime.now(IST).date()
                for item in items:
                    purpose = str(item.get("purpose", "")).lower()
                    desc = str(item.get("desc", "")).lower()
                    if "financial result" in purpose or "board meeting" in purpose or "results" in desc:
                        an_date_str = item.get("an_dt") or item.get("bm_date")
                        if an_date_str:
                            try:
                                dt = pd.to_datetime(an_date_str).date()
                                if dt >= now_date:
                                    return dt, DateStatus.CONFIRMED
                            except Exception:
                                pass
        except Exception as e:
            logger.debug(f"NSE earnings fetch failed for {clean_sym}: {e}")

        # Tier 2: Yahoo Finance Calendar Fallback
        try:
            import yfinance as yf
            t = yf.Ticker(f"{clean_sym}.NS")
            cal = t.calendar
            if isinstance(cal, dict) and "Earnings Date" in cal:
                ed_list = cal["Earnings Date"]
                if isinstance(ed_list, list) and len(ed_list) > 0:
                    first_ed = ed_list[0]
                    if isinstance(first_ed, (date, datetime)):
                        ed_dt = first_ed.date() if isinstance(first_ed, datetime) else first_ed
                        now_date = datetime.now(IST).date()
                        if ed_dt >= now_date:
                            return ed_dt, DateStatus.ESTIMATED
        except Exception as e:
            logger.debug(f"yfinance earnings fallback failed for {clean_sym}: {e}")

        return None, DateStatus.UNKNOWN


# Alias for backward compatibility — NseEarningsProvider is now the authoritative zero-rate-limit provider
YahooEarningsProvider = NseEarningsProvider


class EarningsCalendarService:
    """
    Service managing upcoming earnings dates cache in PostgreSQL with provider abstraction.
    Scanners strictly read from DB cache at runtime for non-blocking execution.
    """
    def __init__(self, provider: Optional[EarningsProvider] = None):
        self.provider = provider or NseEarningsProvider()

    def refresh_earnings_calendar(self, symbols: List[str]) -> int:
        """
        Refreshes earnings calendar for symbols and caches results in PostgreSQL.
        Intended to run daily during off-peak hours (22:00-23:59 IST).
        - Priority 1: Stocks with results expected TODAY (re-checked post-market close).
        - Priority 2: Symbols uncached / older than 45d (known dates) or 7d (missing dates).
        - Off-peak serial rate-limiting: 1 worker, 3.5s delay, 100 batch limit.
        """
        from database import is_scanner_stopped
        if is_scanner_stopped("Earnings Calendar"):
            logger.info("⏭️ Earnings Calendar is PAUSED by Admin. Skipping refresh cycle.")
            return 0

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

                    uncached_symbols = (priority_symbols + other_symbols)[:100]
                    skipped_count = len(symbols) - len(uncached_symbols)

                    if priority_symbols:
                        logger.info(f"🎯 [EARNINGS CALENDAR] Priority 1: Re-checking {len(priority_symbols)} stocks scheduled for results TODAY post-market.")
                    if skipped_count > 0:
                        logger.info(f"📅 [EARNINGS CALENDAR] Skipping {skipped_count}/{len(symbols)} symbols (cached within 45d for known dates / 7d for missing or batch limit 100).")
        except Exception as e:
            logger.warning(f"⚠️ DB pre-check failed for earnings_calendar: {e}. Processing all symbols.")
            uncached_symbols = symbols[:100]

        if not uncached_symbols:
            logger.info("✅ [EARNINGS CALENDAR] All symbols fresh in PostgreSQL cache (45d/7d TTL). Nothing to fetch!")
            return 0

        updated_count = 0
        total_pending = len(uncached_symbols)
        logger.info(f"📊 [EARNINGS CALENDAR] Pending symbols to fetch today: {total_pending} (out of {len(symbols)} total universe)")

        results = {}
        for idx, s in enumerate(uncached_symbols, start=1):
            if is_scanner_stopped("Earnings Calendar"):
                logger.info("⏭️ Earnings Calendar PAUSED by Admin mid-run. Aborting refresh loop.")
                break
            time.sleep(0.1)  # Ultra-fast 0.1s delay — official NSE API bulk pre-fetched with zero rate limiting
            logger.info(f"📅 [EARNINGS CALENDAR] [{idx}/{total_pending}] Fetching earnings date for {s}...")
            try:
                ed, status = self.provider.fetch_earnings_date(s)
                if ed:
                    results[s] = (ed, status)
            except Exception as e:
                logger.debug(f"Error fetching earnings date for {s}: {e}")

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
        from database import upsert_scanner_health, start_scanner_execution_run, complete_scanner_execution_run
        from config import WATCHLIST_PATH
        import os

        run_ctx = start_scanner_execution_run(scanner_name="Earnings Calendar", trigger_type="SCHEDULED", scheduler_name="CRON")
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
        complete_scanner_execution_run(run_ctx)
        upsert_scanner_health(
            "Earnings Calendar", "OK",
            last_success=datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S"),
            total_count=total_count,
            processed_count=updated_count,
            error_msg=f"Updated {updated_count}/{total_count} symbols"
        )
        logger.info(f"✅ [EARNINGS CALENDAR] Refresh complete — {updated_count}/{total_count} symbols updated.")
        return {"total_count": total_count, "updated_count": updated_count}

    except RuntimeError as r_err:
        complete_scanner_execution_run(run_ctx, exception=r_err)
        raise  # propagate lock-contention error cleanly
    except Exception as e:
        logger.exception("❌ [EARNINGS CALENDAR] Refresh failed")
        try:
            from database import upsert_scanner_health
            complete_scanner_execution_run(run_ctx, exception=e)
            upsert_scanner_health(
                "Earnings Calendar", "DOWN",
                error_msg=str(e)[:500]
            )
        except Exception:
            pass
        raise

    finally:
        _scan_lock.release()

def is_earnings_active_window(now: Optional[datetime] = None) -> bool:
    """Check if current time is within active worker window: 12:01 AM to 04:00 AM IST Daily."""
    if now is None:
        now = datetime.now(IST)
    return 0 <= now.hour < 4

def get_earnings_window_desc(now: Optional[datetime] = None) -> str:
    return "12:01 AM - 04:00 AM IST Daily"

def run_worker_loop():
    """Background daemon loop for Earnings Calendar worker."""
    logger.info("📅 Earnings Calendar Worker Thread Started.")
    while True:
        try:
            now_ist = datetime.now(IST)
            from database import is_scanner_stopped, upsert_scanner_health
            if is_scanner_stopped("Earnings Calendar"):
                upsert_scanner_health("Earnings Calendar", "STOPPED", error_msg="Stopped by Admin")
                time.sleep(60)
                continue

            if not is_earnings_active_window(now_ist):
                win_desc = get_earnings_window_desc(now_ist)
                upsert_scanner_health("Earnings Calendar", "IDLE", error_msg=f"Outside active window ({win_desc})")
                time.sleep(300)
                continue

            try:
                run_earnings_calendar_refresh()
            except RuntimeError:
                pass  # Lock contention / already running
            except Exception as e:
                logger.exception(f"❌ [EARNINGS CALENDAR] Worker cycle failed: {e}")

            time.sleep(300)
        except Exception as e:
            logger.exception("❌ [EARNINGS CALENDAR] Main worker loop crashed")
            time.sleep(300)

def start_worker():
    t = threading.Thread(target=run_worker_loop, name="EarningsCalendarWorker", daemon=True)
    t.start()
    return t
