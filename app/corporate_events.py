# =====================================================================================
# app/corporate_events.py
# CORPORATE ACTION EVENT FRAMEWORK — SPLIT BADGES ONLY
# Earnings Calendar removed entirely (was unused, added latency, hit whole NSE universe).
# Now only decorates symbols with stock split/bonus badges using pre-loaded bulk map.
# =====================================================================================

import logging
import time
from enum import IntEnum
from datetime import datetime, date, timedelta
from typing import Dict, List, Any, Optional, Union
import pandas as pd

logger = logging.getLogger("corporate_events")


class EventPriority(IntEnum):
    """Priority hierarchy for corporate action event badges (higher ranks first)."""
    SPLIT = 70
    BONUS = 60


class CorporateEventCache:
    """
    Cache Lifecycle Manager: Pre-warms the bulk split factor map in background
    so CorporateActionContributor.contribute() reads from RAM with zero DB calls.
    Earnings Calendar data removed — was unused and caused full NSE-universe DB queries.
    """
    _warmed: bool = False
    _warm_lock = __import__("threading").Lock()

    @classmethod
    def prime(cls, force: bool = False) -> None:
        """Pre-warms the bulk split factor map once (background thread on first call)."""
        if cls._warmed and not force:
            return
        with cls._warm_lock:
            if cls._warmed and not force:
                return
            cls._warmed = True
        try:
            from corporate_actions import _load_bulk_split_map
            _load_bulk_split_map(force=force)
        except Exception as e:
            logger.debug(f"CorporateEventCache prime warning: {e}")

    @classmethod
    def get_events_map(cls, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """Returns empty map — earnings calendar removed. Split badges come from get_bulk_split_factor()."""
        # Pre-warm split map on first call (non-blocking background thread)
        if not cls._warmed:
            import threading
            threading.Thread(target=cls.prime, kwargs={"force": force_refresh},
                             daemon=True, name="corp-events-loader").start()
        return {}


class EventContributor:
    """Base class for corporate action event contributors."""
    def contribute(self, symbol: str, symbol_events: Dict[str, Any], current_date: date) -> List[Dict[str, Any]]:
        raise NotImplementedError


class CorporateActionContributor(EventContributor):
    """Decorates symbols that had a stock split in the last 365 days.
    Uses pre-loaded bulk split map (ONE DB query for all symbols) — zero per-symbol DB calls.
    """
    def contribute(self, symbol: str, symbol_events: Dict[str, Any], current_date: date) -> List[Dict[str, Any]]:
        badges = []
        try:
            from corporate_actions import get_bulk_split_factor
            one_yr_ago = current_date - timedelta(days=365)
            factor = get_bulk_split_factor(symbol, one_yr_ago, current_date)
            if factor > 1.0:
                badges.append({
                    "type": "corporate_action",
                    "label": f"Split {factor:.1f}x" if float(factor).is_integer() else f"Split {factor:.2f}x",
                    "priority": int(EventPriority.SPLIT),
                    "status": "ACTIVE",
                    "metadata": {"split_factor": factor}
                })
        except Exception:
            pass
        return badges


class CorporateEventPipeline:
    """Pluggable pipeline — only CorporateActionContributor remains after earnings removal."""

    def __init__(self):
        self.contributors: List[EventContributor] = [
            CorporateActionContributor()
        ]

    def evaluate_symbol(self, symbol: str, symbol_events: Dict[str, Any], current_date: date) -> List[Dict[str, Any]]:
        clean_sym = symbol.strip().upper().replace('.NS', '').replace('.BO', '')
        badges = []
        for contrib in self.contributors:
            try:
                res = contrib.contribute(clean_sym, symbol_events, current_date)
                if res:
                    badges.extend(res)
            except Exception as e:
                logger.debug(f"Contributor error for {clean_sym}: {e}")
        badges.sort(key=lambda b: b.get("priority", 0), reverse=True)
        return badges


# Global Singleton Pipeline
default_pipeline = CorporateEventPipeline()


def decorate_events(
    stocks: Union[List[Dict[str, Any]], pd.DataFrame],
    events_map: Optional[Dict[str, Any]] = None,
    current_date: Optional[date] = None,
    pipeline: Optional[CorporateEventPipeline] = None
) -> Union[List[Dict[str, Any]], pd.DataFrame]:
    """
    Stateless & Pure Functional Decorator.
    Decorates each stock row with event_badges containing split/bonus badges.
    Earnings Calendar badges removed — no longer generated.
    """
    curr_d = current_date or datetime.now().date()
    pipe = pipeline or default_pipeline
    e_map = events_map if events_map is not None else {}  # always empty — no earnings

    if isinstance(stocks, pd.DataFrame):
        if stocks.empty:
            return stocks
        df_copy = stocks.copy()
        badges_col = []
        for _, row in df_copy.iterrows():
            sym = str(row.get("symbol") or row.get("Stock") or row.get("Symbol") or "").strip().upper()
            badges = pipe.evaluate_symbol(sym, e_map, curr_d) if sym else []
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
            badges = pipe.evaluate_symbol(sym, e_map, curr_d) if sym else []
            item_copy["event_badges"] = badges
            item_copy["schema_version"] = 1
            decorated_list.append(item_copy)
        return decorated_list

    return stocks
