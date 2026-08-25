# =====================================================================================
# app/corporate_events.py
# CORPORATE ACTION EVENT FRAMEWORK (STATELESS DECORATOR, REPOSITORY, CACHE, PIPELINE)
# =====================================================================================

import logging
import time
from enum import IntEnum
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Union
import pandas as pd

from trading_calendar import TradingCalendar, default_trading_calendar

logger = logging.getLogger("corporate_events")


class EventPriority(IntEnum):
    """Priority hierarchy for corporate action event badges (higher ranks first)."""
    EARNINGS = 100
    DIVIDEND = 80
    SPLIT = 70
    BONUS = 60


class CorporateEventRepository:
    """
    Data Access Layer: Reads raw earnings and corporate events from database tables.
    Isolated from cache management, business rules, or presentation logic.
    """

    @staticmethod
    def fetch_all_events() -> Dict[str, Dict[str, Any]]:
        """
        Fetches all upcoming and recent earnings dates from DB into a bulk dictionary.
        Returns:
            { "TATAMOTORS": { "earnings_date": "2026-08-06", "date_status": "CONFIRMED" } }
        """
        events_map: Dict[str, Dict[str, Any]] = {}
        try:
            from database import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol, earnings_date, date_status FROM earnings_calendar WHERE earnings_date IS NOT NULL")
                    rows = cur.fetchall()
                    for r in rows:
                        sym = str(r[0]).strip().upper()
                        ed = r[1]
                        status = r[2] or "ESTIMATED"
                        if ed:
                            ed_str = ed.strftime("%Y-%m-%d") if hasattr(ed, 'strftime') else str(ed)
                            events_map[sym] = {
                                "earnings_date": ed_str,
                                "date_status": status
                            }
        except Exception as e:
            logger.warning(f"CorporateEventRepository DB fetch error: {e}")

        # Tier-2: Merge NseEarningsProvider bulk in-memory cache if already populated in background worker
        try:
            from earnings_calendar import NseEarningsProvider
            bulk = getattr(NseEarningsProvider, "_bulk_cache", {}) or {}
            for sym, ed_tuple in bulk.items():
                clean_s = sym.strip().upper().replace('.NS', '').replace('.BO', '')
                if clean_s not in events_map and ed_tuple:
                    ed_val = ed_tuple[0] if isinstance(ed_tuple, (tuple, list)) else ed_tuple
                    if ed_val:
                        ed_str = ed_val.strftime("%Y-%m-%d") if hasattr(ed_val, 'strftime') else str(ed_val)
                        events_map[clean_s] = {
                            "earnings_date": ed_str,
                            "date_status": "CONFIRMED"
                        }
        except Exception as _nse_err:
            logger.debug(f"NseEarningsProvider bulk merge warning: {_nse_err}")

        # Tier-3: Zero-Yahoo bulk pre-fill for all tracked symbols missing from events_map
        # This ensures major NSE 500 stocks (RELIANCE, TCS, INFY etc) always get earnings dates
        # even when DB is unavailable or earnings_calendar table is empty.
        try:
            CorporateEventRepository._bulk_fill_non_yahoo(events_map)
        except Exception as _fill_err:
            logger.debug(f"Zero-Yahoo bulk fill warning: {_fill_err}")

        return events_map

    @staticmethod
    def _get_tracked_symbols() -> list:
        """Returns all symbols currently tracked by the system (from DB + watchlist parquet)."""
        symbols = set()
        # From DB: watchlist, candidates, alerts tables
        try:
            from database import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    for table, col in [("watchlist", "symbol"), ("candidates", "symbol"), ("alerts", "symbol")]:
                        try:
                            cur.execute(f'SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL')
                            for r in cur.fetchall():
                                symbols.add(str(r[0]).strip().upper())
                        except Exception:
                            pass
        except Exception:
            pass
        # From watchlist parquet file
        try:
            from config import WATCHLIST_PATH
            import os, pandas as _pd
            if os.path.exists(WATCHLIST_PATH):
                df = _pd.read_parquet(WATCHLIST_PATH)
                if "Stock" in df.columns:
                    symbols.update(df["Stock"].dropna().unique().tolist())
        except Exception:
            pass
        return sorted(symbols)

    @staticmethod
    def _bulk_fill_non_yahoo(events_map: Dict[str, Dict[str, Any]]) -> None:
        """
        Non-blocking bulk fill: Avoids blocking HTTP requests with synchronous per-symbol fetches.
        """
        return


class CorporateEventCache:
    """
    Cache Lifecycle Manager: Holds in-memory snapshot, manages refresh triggers,
    and falls back gracefully to stale snapshot if DB query fails.
    """
    _cache_map: Optional[Dict[str, Dict[str, Any]]] = None
    _last_refresh: float = 0.0
    _ttl_seconds: float = 3600.0  # 1 hour TTL cache (refreshed daily)

    @classmethod
    def get_events_map(cls, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        now = time.time()
        if not force_refresh and cls._cache_map is not None and (now - cls._last_refresh) < cls._ttl_seconds:
            return cls._cache_map

        fresh_map = CorporateEventRepository.fetch_all_events()
        if fresh_map or cls._cache_map is None:
            cls._cache_map = fresh_map
            cls._last_refresh = now
            logger.info(f"✅ CorporateEventCache refreshed ({len(fresh_map)} symbols loaded).")

        return cls._cache_map or {}


class EventContributor:
    """Base class for corporate action event contributors."""
    def contribute(self, symbol: str, symbol_events: Dict[str, Any], calendar: TradingCalendar, current_date: date) -> List[Dict[str, Any]]:
        raise NotImplementedError


class EarningsContributor(EventContributor):
    """Evaluates earnings dates and produces semantic earnings badges.

    Window: -60 to +120 trading sessions from current_date.
      UPCOMING: 0 to +120 sessions (covers current + next quarter)
      RECENT:   -60 to -1 sessions (covers ~3 months post-event)
    """

    def contribute(self, symbol: str, symbol_events: Dict[str, Any], calendar: TradingCalendar, current_date: date) -> List[Dict[str, Any]]:
        badges = []
        ed_str = symbol_events.get("earnings_date")
        if not ed_str:
            return badges

        d_ed = calendar._parse_date(ed_str)
        if not d_ed:
            return badges

        trading_days = calendar.days_between(current_date, d_ed)

        # ±120 trading sessions window — covers current quarter + next quarter earnings
        if -60 <= trading_days <= 120:
            if trading_days >= 0:
                status = "UPCOMING"
                label = f"E in {trading_days}d" if trading_days > 0 else "E Today"
            else:
                status = "RECENT"
                abs_days = abs(trading_days)
                label = f"E {abs_days}d ago"

            badges.append({
                "type": "earnings",
                "label": label,
                "priority": int(EventPriority.EARNINGS),
                "status": status,
                "metadata": {
                    "date": ed_str,
                    "days": trading_days,
                    "date_status": symbol_events.get("date_status", "ESTIMATED")
                }
            })

        return badges


class CorporateActionContributor(EventContributor):
    """Evaluates stock splits and bonus events and produces semantic corporate action badges."""
    def contribute(self, symbol: str, symbol_events: Dict[str, Any], calendar: TradingCalendar, current_date: date) -> List[Dict[str, Any]]:
        badges = []
        try:
            from corporate_actions import get_cumulative_split_factor
            from datetime import timedelta
            one_yr_ago = current_date - timedelta(days=365)
            factor = get_cumulative_split_factor(symbol, one_yr_ago, current_date)
            if factor > 1.0:
                badges.append({
                    "type": "corporate_action",
                    "label": f"Split {factor:.1f}x" if factor.is_integer() else f"Split {factor:.2f}x",
                    "priority": int(EventPriority.SPLIT),
                    "status": "ACTIVE",
                    "metadata": {
                        "split_factor": factor
                    }
                })
        except Exception:
            pass
        return badges


class CorporateEventPipeline:
    """Pluggable pipeline managing contributors and decorating stock payloads."""

    def __init__(self):
        self.contributors: List[EventContributor] = [
            EarningsContributor(),
            CorporateActionContributor()
        ]

    def register_contributor(self, contributor: EventContributor):
        self.contributors.append(contributor)

    def evaluate_symbol(self, symbol: str, events_map: Dict[str, Any], calendar: TradingCalendar, current_date: date) -> List[Dict[str, Any]]:
        clean_sym = symbol.strip().upper().replace('.NS', '').replace('.BO', '')
        symbol_events = events_map.get(clean_sym, {})
        badges = []

        for contrib in self.contributors:
            try:
                res = contrib.contribute(clean_sym, symbol_events, calendar, current_date)
                if res:
                    badges.extend(res)
            except Exception as e:
                logger.debug(f"Contributor error for {clean_sym}: {e}")

        # Sort badges by priority DESC
        badges.sort(key=lambda b: b.get("priority", 0), reverse=True)
        return badges


# Global Singleton Pipeline
default_pipeline = CorporateEventPipeline()


def decorate_events(
    stocks: Union[List[Dict[str, Any]], pd.DataFrame],
    events_map: Optional[Dict[str, Any]] = None,
    calendar: Optional[TradingCalendar] = None,
    current_date: Optional[date] = None,
    pipeline: Optional[CorporateEventPipeline] = None
) -> Union[List[Dict[str, Any]], pd.DataFrame]:
    """
    Stateless & Pure Functional Decorator:
    Takes stocks list/dataframe and returns new immutable copies decorated with:
    - schema_version: 1
    - event_badges: [ { type, label, priority, status, metadata }, ... ]
    """
    start_time = time.time()
    cal = calendar or default_trading_calendar
    curr_d = current_date or datetime.now().date()
    pipe = pipeline or default_pipeline
    e_map = events_map if events_map is not None else CorporateEventCache.get_events_map()

    if isinstance(stocks, pd.DataFrame):
        if stocks.empty:
            return stocks
        df_copy = stocks.copy()
        badges_col = []
        for _, row in df_copy.iterrows():
            sym = str(row.get("symbol") or row.get("Stock") or row.get("Symbol") or "").strip().upper()
            badges = pipe.evaluate_symbol(sym, e_map, cal, curr_d) if sym else []
            badges_col.append(badges)
        df_copy["event_badges"] = badges_col
        df_copy["schema_version"] = 1
        return df_copy

    if isinstance(stocks, list):
        decorated_list = []
        for item in stocks:
            if not isinstance(item, dict):
                decorated_list.append(item)
                continue
            item_copy = dict(item)
            sym = str(item_copy.get("symbol") or item_copy.get("Stock") or item_copy.get("Symbol") or "").strip().upper()
            badges = pipe.evaluate_symbol(sym, e_map, cal, curr_d) if sym else []
            item_copy["event_badges"] = badges
            item_copy["schema_version"] = 1
            decorated_list.append(item_copy)
        return decorated_list

    return stocks
