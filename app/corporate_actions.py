# =====================================================================================
# app/corporate_actions.py
# CORPORATE ACTION (SPLIT & BONUS) COST BASIS & SL/TARGET ADJUSTMENT FRAMEWORK
# =====================================================================================

import logging
import time
import pandas as pd
from datetime import datetime, date
from typing import Dict, Any, Optional, List

from database import get_connection

logger = logging.getLogger("corporate_actions")

# In-memory cache for corporate action split/bonus factors to avoid DB/API spam
_SPLIT_FACTOR_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_CACHE_LAST_REFRESH: float = 0.0
_CACHE_TTL_SECONDS: float = 3600.0  # 1 hour in-memory cache

# ── Bulk split-factor map (loaded once from DB, keyed by symbol) ──────────────
# Format: { "RELIANCE": [(ex_date, split_factor), ...], ... }
_BULK_SPLIT_MAP: Dict[str, List] = {}
_BULK_SPLIT_MAP_TS: float = 0.0
_BULK_SPLIT_MAP_TTL: float = 3600.0  # 1 hour TTL


import threading as _threading
_bulk_load_lock = _threading.Lock()
_bulk_load_bg_started = False


def _load_bulk_split_map(force: bool = False) -> Dict[str, List]:
    """Loads ALL corporate action split records from DB in a single query.
    Returns dict keyed by symbol → list of (ex_date, split_factor) tuples.
    On cold start: triggers a background thread load and returns empty dict immediately.
    This prevents blocking the HTTP handler for 15-55s on first request.
    """
    global _BULK_SPLIT_MAP, _BULK_SPLIT_MAP_TS, _bulk_load_bg_started
    now = time.monotonic()
    if not force and _BULK_SPLIT_MAP and (now - _BULK_SPLIT_MAP_TS) < _BULK_SPLIT_MAP_TTL:
        return _BULK_SPLIT_MAP

    # Cold start: kick off background load, return empty dict immediately
    if not _BULK_SPLIT_MAP and not force:
        with _bulk_load_lock:
            if not _bulk_load_bg_started:
                _bulk_load_bg_started = True
                _threading.Thread(target=_load_bulk_split_map, kwargs={"force": True}, daemon=True, name="bulk-split-loader").start()
                logger.info("[CorporateActions] Bulk split map loading in background thread...")
        return _BULK_SPLIT_MAP  # empty dict — CorporateActionContributor returns no badge this request

    # Foreground load (force=True from CorporateEventCache pre-warm or background thread)
    try:
        init_corporate_actions_db()
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, ex_date, split_factor FROM corporate_actions_history "
                    "WHERE split_factor > 1.0 ORDER BY ex_date ASC"
                )
                rows = cur.fetchall() or []
        new_map: Dict[str, List] = {}
        for r in rows:
            sym = str(r[0]).strip().upper()
            ex_date = r[1]
            factor = float(r[2]) if r[2] else 1.0
            if factor > 1.0:
                new_map.setdefault(sym, []).append((ex_date, factor))
        _BULK_SPLIT_MAP = new_map
        _BULK_SPLIT_MAP_TS = time.monotonic()
        logger.info(f"[CorporateActions] Bulk split map loaded: {len(new_map)} symbols with splits")
    except Exception as e:
        logger.debug(f"[CorporateActions] Bulk split map load warning: {e}")

    return _BULK_SPLIT_MAP


def get_bulk_split_factor(symbol: str, entry_date: date, exit_date: Optional[date] = None) -> float:
    """Fast per-symbol split factor lookup from the pre-loaded bulk map.
    Zero DB calls — reads from in-memory dict loaded by _load_bulk_split_map().
    Used by CorporateActionContributor.contribute() to avoid 200+ DB queries per request.
    """
    if not symbol:
        return 1.0
    clean_sym = symbol.strip().upper().replace(".NS", "").replace(".BO", "")
    d_exit = exit_date or datetime.now().date()
    if isinstance(d_exit, datetime):
        d_exit = d_exit.date()

    bulk = _load_bulk_split_map()
    sym_rows = bulk.get(clean_sym, [])
    cumulative = 1.0
    for ex_date, factor in sym_rows:
        ex_d = ex_date if isinstance(ex_date, date) else pd.to_datetime(str(ex_date)).date()
        if entry_date < ex_d <= d_exit:
            cumulative *= factor
    return round(cumulative, 4)


_CORP_DB_INITIALIZED = False

def init_corporate_actions_db() -> None:
    """Ensures corporate_actions_history table exists in PostgreSQL database (runs once)."""
    global _CORP_DB_INITIALIZED
    if _CORP_DB_INITIALIZED:
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS corporate_actions_history (
                        id SERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        ex_date DATE NOT NULL,
                        action_type VARCHAR(30) NOT NULL, -- 'SPLIT' or 'BONUS'
                        split_factor NUMERIC(10, 4) NOT NULL DEFAULT 1.0, -- e.g. 5.0 for 1:5 split, 2.0 for 1:1 bonus
                        notes TEXT,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        CONSTRAINT uq_corp_action_sym_date_type UNIQUE (symbol, ex_date, action_type)
                    );
                    CREATE INDEX IF NOT EXISTS idx_corp_actions_sym_date ON corporate_actions_history (symbol, ex_date);
                """)
                conn.commit()
                _CORP_DB_INITIALIZED = True
    except Exception as e:
        logger.warning(f"Failed to init corporate_actions_history table: {e}")


def register_corporate_action(symbol: str, ex_date: str, action_type: str, split_factor: float, notes: str = "") -> None:
    """Manually or programmatically register a corporate action event in the database."""
    init_corporate_actions_db()
    sym = symbol.strip().upper().replace('.NS', '').replace('.BO', '')
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO corporate_actions_history (symbol, ex_date, action_type, split_factor, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (symbol, ex_date, action_type) 
                    DO UPDATE SET split_factor = EXCLUDED.split_factor, notes = EXCLUDED.notes;
                """, (sym, ex_date, action_type.upper(), float(split_factor), notes))
                conn.commit()
        # Invalidate in-memory cache on write
        _SPLIT_FACTOR_CACHE.clear()
        logger.info(f"✅ Registered corporate action for {sym}: {action_type} x{split_factor} on {ex_date}")
    except Exception as e:
        logger.error(f"Failed to register corporate action for {sym}: {e}")


def get_cumulative_split_factor(symbol: str, entry_date_val: Any, exit_date_val: Any = None) -> float:
    """
    Computes cumulative corporate action factor (splits & bonuses) between entry_date and exit_date (or today).
    Cached in-memory to prevent DB connection pool exhaustion.
    """
    if not symbol:
        return 1.0

    clean_sym = symbol.strip().upper().replace('.NS', '').replace('.BO', '')
    
    # Parse entry date
    try:
        if isinstance(entry_date_val, (datetime, date)):
            d_entry = entry_date_val if isinstance(entry_date_val, date) else entry_date_val.date()
        else:
            d_entry = pd.to_datetime(str(entry_date_val)).date()
    except Exception:
        return 1.0

    # Parse exit date
    try:
        if exit_date_val:
            d_exit = exit_date_val if isinstance(exit_date_val, date) else pd.to_datetime(str(exit_date_val)).date()
        else:
            d_exit = datetime.now().date()
    except Exception:
        d_exit = datetime.now().date()

    if d_entry >= d_exit:
        return 1.0

    cache_key = f"{clean_sym}:{d_entry}:{d_exit}"
    now_mono = time.monotonic()
    if cache_key in _SPLIT_FACTOR_CACHE:
        c_val, c_ts = _SPLIT_FACTOR_CACHE[cache_key]
        if (now_mono - c_ts) < _CACHE_TTL_SECONDS:
            return c_val

    init_corporate_actions_db()

    cumulative_factor = 1.0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT ex_date, split_factor 
                    FROM corporate_actions_history
                    WHERE symbol = %s AND ex_date > %s AND ex_date <= %s AND split_factor > 1.0
                    ORDER BY ex_date ASC
                """, (clean_sym, d_entry, d_exit))
                rows = cur.fetchall()
                for r in rows:
                    factor = float(r[1])
                    if factor > 1.0:
                        cumulative_factor *= factor
    except Exception as e:
        logger.warning(f"Error querying corporate_actions_history for {clean_sym}: {e}")

    res_factor = round(cumulative_factor, 4)
    _SPLIT_FACTOR_CACHE[cache_key] = (res_factor, now_mono)
    return res_factor


def adjust_trade_for_corporate_actions(trade: dict) -> dict:
    """
    Pure Functional Trade Adjuster:
    Before evaluating exit monitors / SL / targets on any trade payload:
    Adjusts cost basis, stop loss, targets, and share quantities by the cumulative split factor.
    Uses pre-loaded bulk split map (zero DB calls) — fast path for HTTP handlers.
    Returns the mutated trade payload with updated prices, quantities, and metadata flags.
    """
    if not isinstance(trade, dict) or not trade.get("symbol"):
        return trade

    # Skip if already adjusted
    if trade.get("_corporate_actions_adjusted"):
        return trade

    sym = trade["symbol"]
    entry_d = trade.get("entry_date") or trade.get("alert_date") or trade.get("created_at")
    exit_d = trade.get("closed_at") or trade.get("exit_date")

    # Use bulk map (RAM) instead of per-symbol DB query
    factor = get_bulk_split_factor(sym, entry_d, exit_d)
    if factor <= 1.0:
        trade["_corporate_actions_adjusted"] = True
        trade["split_factor"] = 1.0
        return trade


    logger.info(f"🔄 [CORPORATE ACTION ADJUSTMENT] {sym} | Entry: {entry_d} | Split Factor: {factor}x")

    # Adjust prices by dividing by factor F
    for p_key in [
        "entry_price", "stop_loss", "initial_stop_loss",
        "target_price", "target_1", "target_2", "target_3", "target_4",
        "breakout_level", "structural_failure_stop"
    ]:
        val = trade.get(p_key)
        if val is not None and float(val) > 0:
            trade[p_key] = round(float(val) / factor, 2)

    # Adjust share quantities by multiplying by factor F
    for q_key in ["shares_bought", "remaining_shares", "quantity", "position_shares"]:
        val = trade.get(q_key)
        if val is not None and int(val) > 0:
            trade[q_key] = int(round(int(val) * factor))

    trade["_corporate_actions_adjusted"] = True
    trade["split_factor"] = factor
    return trade
