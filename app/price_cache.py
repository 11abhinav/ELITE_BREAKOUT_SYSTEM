# =====================================================================================
# app/price_cache.py (BULLETPROOF EDITION)
# =====================================================================================

import logging
import threading
import time
import random
from datetime import time as dt_time
import pandas as pd
import re
from typing import Optional
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from database import upsert_fetch_error
from data_provider import get_fetcher
from config import BATCH_DOWNLOAD_SIZE, PRICE_CACHE_TTL_SECONDS, DATA_DIR
from core_enums import ProviderResult
from validation import ValidationEngine, PriceValidator, PriceScoreCalculator, MarketData
from config import SOURCE_RELIABILITY, MAX_HISTORY_SHRINK
import json
import os

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_cache: dict[tuple, dict] = {}
_lock = threading.Lock()
_fetch_lock = threading.Lock()  # CRITICAL: Global lock to serialize API fetches across all scanners (prevents thundering herd)
CACHE_TTL_SECONDS = PRICE_CACHE_TTL_SECONDS

from market_utils import is_market_open

def _is_market_hours() -> bool:
    return is_market_open()

def get_dynamic_cadence(interval: str) -> int:
    """Calculates exact seconds until the next NSE candle boundary for any given interval."""
    now_dt = datetime.now(IST)
    market_open = now_dt.replace(hour=9, minute=15, second=0, microsecond=0)
    
    if not _is_market_hours():
        # If after 15:30, target tomorrow's open
        if now_dt.time() > dt_time(15, 30):
            next_open = (now_dt + timedelta(days=1)).replace(hour=9, minute=15, second=0, microsecond=0)
        else:
            # It's before 9:15 AM today
            next_open = market_open
            
        # Fast-forward through weekends (Saturday=5, Sunday=6)
        while next_open.weekday() >= 5:
            next_open += timedelta(days=1)
        
        secs_to_open = (next_open - now_dt).total_seconds()
        return max(3600, int(secs_to_open))  # Cache until market opens
        

    # If it's before market open, the next boundary is market open
    if now_dt < market_open:
        secs = (market_open - now_dt).total_seconds()
        return max(5, int(secs))
        
    # Parse the interval (e.g., '15m', '45m', '1h', '1d')
    interval_lower = interval.lower()
    if interval_lower in ('1d', 'daily'):
        # For daily data, update on 5-minute boundaries during market hours
        # This prevents the 60s fallback spam while keeping live data fresh
        interval_lower = '5m'

    match = re.match(r'^(\d+)(m|h)$', interval_lower)
    if not match:
        return CACHE_TTL_SECONDS
        
    val = int(match.group(1))
    unit = match.group(2)
    
    if unit == 'h':
        val = val * 60
        
    if val <= 0:
        return CACHE_TTL_SECONDS
        
    # Calculate minutes since market open (9:15 AM)
    minutes_since_open = (now_dt - market_open).total_seconds() / 60.0
    
    # Find the next multiple of the interval
    next_multiple = ((int(minutes_since_open) // val) + 1) * val
    
    # Calculate the exact timestamp of the next boundary
    next_boundary = market_open + timedelta(minutes=next_multiple)
    secs = (next_boundary - now_dt).total_seconds()
    
    # Add a small 5s buffer to allow broker data to settle on their end before fetching
    return max(5, int(secs) + 5)


def fetch_watchlist_data(watchlist: pd.DataFrame, period: str = "10d", interval: str = "15m", requester: str = None) -> dict[str, pd.DataFrame]:
    requester = requester or threading.current_thread().name or "Unknown"
    cache_key = (interval, period)
    cadence = get_dynamic_cadence(interval)

    with _lock:
        entry = _cache.get(cache_key)
        if entry is not None:
            age = time.monotonic() - entry["ts"]
            if age < cadence:
                cached_data = entry["data"]
                missing_symbols = [s for s in watchlist["Stock"] if s not in cached_data]
                if not missing_symbols:
                    logger.debug(f"📦 Price cache hit | {interval} | {period} | age={age:.1f}s < cadence={cadence:.0f}s")
                    return {s: cached_data[s] for s in watchlist["Stock"]}
            else:
                logger.info(f"Price cache stale for {interval} (age={age:.1f}s >= cadence={cadence:.0f}s). Forcing fresh download.")

    # CRITICAL FIX: Use global lock to serialize API fetches across all scanners
    # This prevents thundering herd where 5+ scanners fetch simultaneously
    # Lock ensures only 1 scanner fetches at a time; others hit cache (reducing API load 10-25×)
    logger.debug(f"🔒 Attempting to acquire global fetch lock for {interval}|{period}...")
    with _fetch_lock:
        logger.debug(f"🔓 Global fetch lock acquired for {interval}|{period}")
        
        # Double-check cache in case another thread just populated it while we waited for lock
        with _lock:
            entry = _cache.get(cache_key)
            if entry is not None:
                age = time.monotonic() - entry["ts"]
                if age < cadence:
                    cached_data = entry["data"]
                    missing_symbols = [s for s in watchlist["Stock"] if s not in cached_data]
                    if not missing_symbols:
                        logger.info(f"📦 Cache was populated by concurrent thread; reusing instead of refetching.")
                        return {s: cached_data[s] for s in watchlist["Stock"]}
        
        # Cache miss or stale — download fresh data
        result = _download_all_robust(watchlist, period=period, interval=interval, requester=requester)

    # Determine oldest timestamp in batch
    data_as_of = None
    if result:
        timestamps = []
        for symbol, df in result.items():
            if df is not None and not df.empty:
                try:
                    ts = None
                    if "Datetime" in df.columns:
                        ts = df["Datetime"].iloc[-1]
                    elif "Date" in df.columns:
                        ts = df["Date"].iloc[-1]
                    else:
                        ts = df.index[-1]
                    ts = pd.to_datetime(ts)
                    if ts.tzinfo is None:
                        ts = ts.tz_localize(IST)
                    else:
                        ts = ts.tz_convert(IST)
                    timestamps.append(ts)
                except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
                    logger.warning(f"Failed to parse timestamp for {symbol} in price_cache: {e}")
                    pass
                    
        # CRITICAL FIX: If we expected timestamps (result is not empty) but failed to parse ALL of them,
        # we cannot confidently determine data freshness. We must invalidate this fetch.
        if not timestamps and any(df is not None and not df.empty for df in result.values()):
            logger.error("DataFetchError: All dataframes returned malformed or missing timestamps. Aborting cache update.")
            raise ValueError("DataFetchError: Malformed timestamps across entire batch.")
            
        if timestamps:
            data_as_of = min(timestamps)
            if data_as_of.tzinfo is None:
                data_as_of = data_as_of.replace(tzinfo=IST)
            else:
                data_as_of = data_as_of.astimezone(IST)

    with _lock:
        _cache[cache_key] = {
            "data": result,
            "ts": time.monotonic(),
            "data_as_of": data_as_of
        }

    return result

import os
from config import DATA_DIR
from datetime import timedelta

def _is_cache_up_to_date(last_ts: pd.Timestamp, interval: str) -> bool:
    """Checks if the cached data already contains the most recent market close."""
    now_dt = datetime.now(IST)
    market_open = now_dt.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
    
    is_weekend = now_dt.weekday() >= 5
    is_market_active = not is_weekend and (market_open <= now_dt <= market_close)
    
    # If the market is currently OPEN, the cache is NEVER fully up to date
    # (because new candles are forming right now). We MUST fetch the delta.
    if is_market_active:
        return False
        
    if is_weekend:
        last_close = market_close - timedelta(days=now_dt.weekday() - 4)
    elif now_dt > market_close:
        last_close = market_close
    elif now_dt.weekday() == 0:
        last_close = market_close - timedelta(days=3)
    else:
        last_close = market_close - timedelta(days=1)
        
    if interval.lower() in ('1d', 'daily', '1wk', '1mo'):
        return last_ts.date() >= last_close.date()
    else:
        # Intraday candles: allow a 30m buffer for early broker closures (e.g. 15:25 candle)
        return last_ts >= (last_close - timedelta(minutes=30))

def _is_cache_long_enough(cached_df: pd.DataFrame, period: str, sym: str = "") -> bool:
    """Check if the cached dataframe has enough calendar days to satisfy the requested period."""
    if cached_df.empty:
        return False
    try:
        if 'Date' in cached_df.columns:
            first_ts = pd.to_datetime(cached_df['Date'].iloc[0])
            last_ts = pd.to_datetime(cached_df['Date'].iloc[-1])
        elif 'Datetime' in cached_df.columns:
            first_ts = pd.to_datetime(cached_df['Datetime'].iloc[0])
            last_ts = pd.to_datetime(cached_df['Datetime'].iloc[-1])
        else:
            first_ts = pd.to_datetime(cached_df.index[0])
            last_ts = pd.to_datetime(cached_df.index[-1])
            
        days_diff = (last_ts - first_ts).days
        
        req = 0
        p = period.lower()
        if p == "10y": req = 3600
        elif p == "5y": req = 1800
        elif p == "2y": req = 700
        elif p == "1y": req = 300
        elif p == "6mo": req = 150
        elif p == "3mo": req = 75
        elif p == "1mo": req = 20
        elif p.endswith("d"):
            try: req = int(p[:-1]) - 1
            except: pass
            
        if req > 0:
            # A requested period of N calendar days will have at least N * 0.65 calendar days diff
            # between the first and last candle. If days_diff is smaller, we are missing historical data.
            if days_diff < (req * 0.65):
                # Check if we already hit the beginning of history (IPO/recent listing)
                # [VERSION: CACHE_POISON_FIX] Ignore earliest_dates if we have fewer than 10 bars to prevent 1-bar starvation
                if len(cached_df) >= 10:
                    earliest_path = os.path.join(DATA_DIR, "earliest_dates.json")
                    if os.path.exists(earliest_path):
                        try:
                            with open(earliest_path, "r") as f:
                                earliest_dates = json.load(f)
                                if sym and earliest_dates.get(sym):
                                    first_dt = first_ts.date().isoformat() if hasattr(first_ts, 'date') else None
                                    if first_dt == earliest_dates[sym]:
                                        # We have hit the absolute beginning of history for this symbol
                                        return True
                        except Exception:
                            pass
                return False
            
        return True
    except Exception:
        return True

def _download_all_robust(watchlist: pd.DataFrame, period: str, interval: str, requester: str = None) -> dict[str, pd.DataFrame]:
    symbols = watchlist["Stock"].tolist()
    all_data: dict[str, pd.DataFrame] = {}
    total = len(symbols)
    batch_size = BATCH_DOWNLOAD_SIZE
    fetcher = get_fetcher()
    rate_limited = False

    history_dir = os.path.join(DATA_DIR, "history", interval)
    os.makedirs(history_dir, exist_ok=True)

    # Group symbols by what they need to fetch
    # Key: (range_from, range_to) or "FULL"
    # Value: list of (symbol, cached_df)
    fetch_groups = {}
    
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    fresh_count = 0
    
    for sym in symbols:
        file_path = os.path.join(history_dir, f"{sym.replace(':', '_')}.parquet")
        needs_full = True
        cached_df = None
        
        if os.path.exists(file_path):
            try:
                cached_df = pd.read_parquet(file_path)
                if not cached_df.empty:
                    # Find last timestamp
                    if 'Date' in cached_df.columns:
                        last_ts = pd.to_datetime(cached_df['Date'].iloc[-1])
                    elif 'Datetime' in cached_df.columns:
                        last_ts = pd.to_datetime(cached_df['Datetime'].iloc[-1])
                    else:
                        last_ts = pd.to_datetime(cached_df.index[-1])
                        
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.tz_localize(IST)
                    else:
                        last_ts = last_ts.tz_convert(IST)
                        
                    # 🚀 OPTIMIZATION: If data is already up to the last market close, skip DELTA fetch completely!
                    is_up_to_date = _is_cache_up_to_date(last_ts, interval)
                    is_long_enough = _is_cache_long_enough(cached_df, period, sym)
                    
                    if is_up_to_date:
                        if is_long_enough:
                            all_data[sym] = cached_df
                            needs_full = False
                            fresh_count += 1
                            continue
                        else:
                            # It's up to date but not long enough (e.g. 5d requested before, but now 1y requested)
                            needs_full = True
                            
                    if not is_long_enough:
                        needs_full = True
                        
                    if not needs_full:
                        # Back up 1 day to ensure we get overlapping candles to avoid gaps
                        range_from = (last_ts - timedelta(days=1)).strftime("%Y-%m-%d")
                        range_to = today_str
                        
                        group_key = (range_from, range_to)
                        if group_key not in fetch_groups:
                            fetch_groups[group_key] = []
                        fetch_groups[group_key].append((sym, cached_df))
                        needs_full = False
            except Exception as e:
                logger.warning(f"Failed to read disk cache for {sym}: {e}")
                
        if needs_full:
            if "FULL" not in fetch_groups:
                fetch_groups["FULL"] = []
            fetch_groups["FULL"].append((sym, None))

    # Process each group
    for group_key, items in fetch_groups.items():
        group_symbols = [item[0] for item in items]
        group_total = len(group_symbols)
        
        range_from, range_to = (None, None) if group_key == "FULL" else group_key
        desc = "FULL" if group_key == "FULL" else f"DELTA {range_from} to {range_to}"
        
        for i in range(0, group_total, batch_size):
            batch = group_symbols[i : i + batch_size]
            batch_end = min(i + batch_size, group_total)
            logger.info(f"[{requester}] 📥 Fetching Batch {desc} ({i}–{batch_end}/{group_total}) [{interval}]")
            
            batch_results = fetcher.get_batch_ohlcv(batch, interval=interval, period=period, retries=3, range_from=range_from, range_to=range_to, caller=requester)
            
            if batch_results:
                for sym in batch:
                    md = batch_results.get(sym)
                    cached_df = next((item[1] for item in items if item[0] == sym), None)
                    
                    if md is None or md.dataframe is None:
                        if cached_df is not None and not cached_df.empty:
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                        else:
                            all_data[sym] = None
                        continue
                        
                    new_df = md.dataframe
                    new_report = md.quality_report
                    remote_source = md.source
                    
                    # Cache Decision Engine
                    if cached_df is not None and not cached_df.empty:
                        engine = ValidationEngine(PriceValidator(), PriceScoreCalculator(period, interval, range_from, range_to))
                        cache_report = engine.validate(cached_df)
                        
                        remote_score = (new_report.quality_score if new_report else 0) * SOURCE_RELIABILITY.get(remote_source, 1.0)
                        cache_score = cache_report.quality_score * SOURCE_RELIABILITY.get("Cache", 0.95)
                        
                        logger.debug(f"CACHE_DECISION | Symbol={sym} | RemoteScore={remote_score:.1f} ({remote_source}) | CacheScore={cache_score:.1f}")
                        
                        # 1. Critical Cache Validation
                        reject_reason = None
                        if not range_from and new_report and cache_report:
                            if new_report.row_count < cache_report.row_count * (1.0 - MAX_HISTORY_SHRINK):
                                reject_reason = "HISTORICAL_SHRINK"

                        if reject_reason:
                            logger.warning(f"Critical Cache Validation Failed for {sym}: {reject_reason}. REJECTING remote data to protect cache.")
                            logger.info(f"CACHE_DECISION | Action=KEEP_CACHE | Reason={reject_reason} | Symbol={sym} | ExistingRows={cache_report.row_count} | IncomingRows={new_report.row_count} | Threshold={MAX_HISTORY_SHRINK*100}%")
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                            continue
                        elif remote_score >= cache_score or (not new_report and remote_score == cache_score):
                            # Accept and Merge
                            pass
                        else:
                            # Reject Remote Data (Lower Quality)
                            logger.info(f"CACHE_DECISION | Action=KEEP_CACHE | Reason=REMOTE_LOWER_QUALITY | Symbol={sym}")
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                            continue
                            
                    if new_df is not None and not new_df.empty:
                        # [VERSION: TIMEZONE_FIX_v1.0] True timezone normalization at ingestion boundary
                        time_col = 'Date' if 'Date' in new_df.columns else ('Datetime' if 'Datetime' in new_df.columns else None)
                        if time_col:
                            new_df[time_col] = pd.to_datetime(new_df[time_col])
                            if new_df[time_col].dt.tz is None:
                                new_df[time_col] = new_df[time_col].dt.tz_localize('Asia/Kolkata')
                            else:
                                new_df[time_col] = new_df[time_col].dt.tz_convert('Asia/Kolkata')
                        elif not new_df.index.empty:
                            new_df.index = pd.to_datetime(new_df.index)
                            if new_df.index.tz is None:
                                new_df.index = new_df.index.tz_localize('Asia/Kolkata')
                            else:
                                new_df.index = new_df.index.tz_convert('Asia/Kolkata')
                                
                        fresh_count += 1
                        if cached_df is not None and not cached_df.empty:
                            # [VERSION: TIMEZONE_FIX_v1.1] Normalize cached_df timezone before concat
                            c_time_col = 'Date' if 'Date' in cached_df.columns else ('Datetime' if 'Datetime' in cached_df.columns else None)
                            if c_time_col:
                                cached_df[c_time_col] = pd.to_datetime(cached_df[c_time_col])
                                if cached_df[c_time_col].dt.tz is None:
                                    cached_df[c_time_col] = cached_df[c_time_col].dt.tz_localize('Asia/Kolkata')
                                else:
                                    cached_df[c_time_col] = cached_df[c_time_col].dt.tz_convert('Asia/Kolkata')
                            elif not cached_df.index.empty:
                                cached_df.index = pd.to_datetime(cached_df.index)
                                if cached_df.index.tz is None:
                                    cached_df.index = cached_df.index.tz_localize('Asia/Kolkata')
                                else:
                                    cached_df.index = cached_df.index.tz_convert('Asia/Kolkata')

                            # Merge them
                            combined = pd.concat([cached_df, new_df])
                            # Deduplicate based on timestamp
                            time_col_comb = 'Date' if 'Date' in combined.columns else ('Datetime' if 'Datetime' in combined.columns else None)
                            if time_col_comb:
                                combined = combined.drop_duplicates(subset=[time_col_comb], keep='last')
                            else:
                                combined = combined[~combined.index.duplicated(keep='last')]
                                
                            combined = combined.sort_index() if time_col_comb is None else combined.sort_values(time_col_comb)
                            
                            # Keep reasonable history limit to prevent infinite growth
                            max_rows = 5000 if interval.endswith('m') else 2000
                            combined = combined.tail(max_rows).copy()
                            
                            all_data[sym] = combined
                        else:
                            all_data[sym] = new_df
                            
                        # If this was a FULL fetch for a long historical period, record the earliest available date
                        # [VERSION: CACHE_POISON_FIX] Require at least 10 bars to prevent a failed 1-bar fallback from poisoning the IPO date
                        if group_key == "FULL" and not new_df.empty and len(new_df) >= 10 and period.lower() in ("max", "10y", "5y", "2y", "1y", "ytd"):
                            try:
                                t_col = 'Date' if 'Date' in new_df.columns else ('Datetime' if 'Datetime' in new_df.columns else None)
                                earliest_ts = pd.to_datetime(new_df[t_col].iloc[0]) if t_col else pd.to_datetime(new_df.index[0])
                                earliest_dt_str = earliest_ts.date().isoformat() if hasattr(earliest_ts, 'date') else None
                                if earliest_dt_str:
                                    earliest_path = os.path.join(DATA_DIR, "earliest_dates.json")
                                    earliest_dates = {}
                                    if os.path.exists(earliest_path):
                                        with open(earliest_path, "r") as f:
                                            earliest_dates = json.load(f)
                                    earliest_dates[sym] = earliest_dt_str
                                    with open(earliest_path, "w") as f:
                                        json.dump(earliest_dates, f)
                            except Exception as e:
                                logger.debug(f"Failed to record earliest date for {sym}: {e}")
                            
                        # Save back to disk
                        try:
                            file_path = os.path.join(history_dir, f"{sym.replace(':', '_')}.parquet")
                            all_data[sym].to_parquet(file_path)
                        except Exception as e:
                            logger.exception(f"Failed to write disk cache for {sym}")
                    else:
                        # Fallback to stale cached data if fresh fetch returned empty
                        if cached_df is not None and not cached_df.empty:
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
            else:
                logger.error(f"❌ Batch {desc} failed or returned empty for {len(batch)} symbols.")
                rate_limited = True
                # Fallback entire batch to stale cache
                for sym in batch:
                    cached_df = next((item[1] for item in items if item[0] == sym), None)
                    if cached_df is not None and not cached_df.empty:
                        cached_df.attrs['is_stale'] = True
                        all_data[sym] = cached_df
                time.sleep(0.5)

    logger.info(f"✅ Data secured for {len(all_data)}/{total} symbols [{interval}]")

    for sym in symbols:
        df = all_data.get(sym)
        if df is None or getattr(df, 'attrs', {}).get('is_stale', False) or isinstance(df, ProviderResult):
            try:
                upsert_fetch_error('yfinance', 'PRICE_CACHE', sym, interval, 'no_data_after_fetch', 'no_data_returned')
            except Exception:
                pass
            all_data[sym] = None
        else:
            try:
                from database import delete_fetch_error_on_success
                delete_fetch_error_on_success('yfinance', 'PRICE_CACHE', sym, interval, 'no_data_after_fetch')
            except Exception:
                pass

    try:
        from data_fetch_status import mark_success, mark_failure
        
        failed_fresh = total - fresh_count
        
        if total > 0:
            failure_rate = failed_fresh / total
            if failure_rate > 0.25:
                mark_failure(f"yfinance:{interval}", f"Scanner failed: >25% stale/missing ({failed_fresh}/{total} records failed fresh fetch)")
            else:
                # >= 75% success is acceptable
                mark_success(f"yfinance:{interval}")
        else:
            mark_failure(f"yfinance:{interval}", "No data returned (completely empty)")
    except Exception:
        pass
    
    return all_data

def fetch_unified_historical(symbols: list, period: str = "1y", interval: str = "1d", requester: str = None) -> dict[str, pd.DataFrame]:
    """
    Unified data fetcher for wealth_engine, eod_scanner, and reversal_scanner.
    Uses unified cache key (interval, period) to allow cross-scanner reuse.
    
    OPTIMIZATION: All 1D data now shares cache key (1d, 1y) instead of
    having separate cache per module (price_fetcher vs price_cache).
    """
    watchlist_df = pd.DataFrame({"Stock": symbols})
    return fetch_watchlist_data(watchlist_df, period=period, interval=interval, requester=requester)


# -----------------------------------------------------------------------------
# MARKET-HOUR & INTRADAY SHARED SNAPSHOT
# -----------------------------------------------------------------------------

import os
from config import DATA_DIR
WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")


# [BUG FIX 2026-06-24] _INTERVAL_CADENCE and _TTL_JITTER were used inside
# get_intraday_snapshot() but were never defined anywhere in this file.
# This was a latent NameError crash waiting to happen if any scanner called
# get_intraday_snapshot(). Defined here to match the dynamic cadence logic
# in get_dynamic_cadence() above.
_INTERVAL_CADENCE: dict[str, int] = {
    "1m":  60,
    "5m":  300,
    "15m": 900,
    "30m": 1800,
    "1h":  3600,
    "1d":  86400,
}

# Jitter adds a small buffer after the candle closes to allow broker data to settle
_TTL_JITTER: dict[str, int] = {
    "1m":  5,
    "5m":  10,
    "15m": 15,
    "30m": 20,
    "1h":  30,
    "1d":  60,
}

# Tracks in-progress fetches so only one thread fetches per (interval, period)
_inflight_fetches: dict[tuple, threading.Event] = {}


def get_intraday_snapshot(symbols: list[str], interval: str = "5m", period: str = "5d", wait_timeout: int = 30, requester: str = None) -> dict[str, pd.DataFrame]:
    requester = requester or threading.current_thread().name or "Unknown"
    """
    Return cached intraday frames for (interval, period) for the provided symbols.
    If cache is stale or missing, a single thread will perform the fetch and others
    will wait up to `wait_timeout` seconds for the result. This guarantees only one
    fetch per cache key is in-flight at any time.

    Returns the raw mapping: { symbol: DataFrame }
    """
    cache_key = (interval, period)
    cadence = _INTERVAL_CADENCE.get(interval, CACHE_TTL_SECONDS)
    jitter = _TTL_JITTER.get(interval, 0)
    cadence_with_jitter = cadence + jitter

    # Quick cache check
    with _lock:
        entry = _cache.get(cache_key)
        if entry is not None:
            age = time.monotonic() - entry["ts"]
            if age < cadence_with_jitter:
                logger.debug(f"[{requester}] 📦 Intraday cache hit | {interval}|{period} | age={age:.1f}s")
                # Return subset for requested symbols
                return {s: entry["data"].get(s) for s in symbols}

        # If another thread is already fetching this key, wait for it to complete
        inflight = _inflight_fetches.get(cache_key)
        if inflight:
            logger.debug(f"[{requester}] ⏳ Waiting for in-flight fetch for {cache_key} (wait_timeout={wait_timeout}s)")
            # Release lock while waiting
            # Wait outside lock
            pass

    # If inflight exists, wait for completion then return cache (may still be missing)
    inflight = _inflight_fetches.get(cache_key)
    if inflight:
        inflight.wait(wait_timeout)
        with _lock:
            entry = _cache.get(cache_key)
            if entry is None:
                logger.warning(f"Intraday fetch completed but cache missing for {cache_key}")
                return {s: None for s in symbols}
            return {s: entry["data"].get(s) for s in symbols}

    # No inflight — attempt to become the fetcher
    evt = threading.Event()
    with _lock:
        # Double-check in case someone set it while creating event
        if cache_key in _inflight_fetches:
            inflight = _inflight_fetches[cache_key]
        else:
            _inflight_fetches[cache_key] = evt
            inflight = None

    if inflight:
        # Race lost — wait for the actual fetcher
        inflight.wait(wait_timeout)
        with _lock:
            entry = _cache.get(cache_key)
            if entry is None:
                return {s: None for s in symbols}
            return {s: entry["data"].get(s) for s in symbols}

    # This thread is responsible for fetching
    try:
        logger.info(f"[{requester}] 🔁 Performing single fetch for intraday key {cache_key} for {len(symbols)} symbols")
        watchlist_df = pd.DataFrame({"Stock": symbols})
        # Use existing serialized path which already uses a global fetch lock
        result = fetch_watchlist_data(watchlist_df, period=period, interval=interval)
        # Return subset for requested symbols
        return {s: result.get(s) for s in symbols} if result else {s: None for s in symbols}
    finally:
        # Signal completion so waiters can proceed
        try:
            evt.set()
        except Exception:
            pass
        with _lock:
            _inflight_fetches.pop(cache_key, None)


def fetch_market_hour_snapshot(symbols: list[str], recent_period: str = "5d", requester: str = None) -> dict:
    requester = requester or threading.current_thread().name or "Unknown"
    """
    Fetch a small, shared snapshot optimized for market-hours:
      - Recent daily OHLCV for `recent_period` (default 5d) via cached batch fetch
      - SMA200 lookup from persisted Wealth parquet (fast) when available
      - Compute SMA200 from recent data only if enough bars exist (avoid 1y re-fetch)

    Returns: {
      "daily": dict[str, pd.DataFrame],
      "sma_200": dict[str, Optional[float]],
      "data_as_of": datetime or None
    }
    """
    result = {
        "daily": {},
        "sma_200": {},
        "data_as_of": None,
    }

    if not symbols:
        return result

    # 1) Fetch recent daily bars using the unified cached path (this is serialized by fetch_watchlist_data)
    try:
        daily = fetch_unified_historical(symbols, period=recent_period, interval="1d", requester=requester)
    except Exception as e:
        logger.warning(f"[{requester}] Failed to fetch recent daily data for snapshot: {e}")
        daily = {}

    result["daily"] = daily or {}

    # Determine data_as_of (oldest/latest timestamp across fetched frames)
    timestamps = []
    for df in result["daily"].values():
        try:
            if df is None or df.empty:
                continue
            if "Datetime" in df.columns:
                ts = pd.to_datetime(df["Datetime"].iloc[-1])
            elif "Date" in df.columns:
                ts = pd.to_datetime(df["Date"].iloc[-1])
            else:
                ts = pd.to_datetime(df.index[-1])
            if ts.tzinfo is None:
                ts = ts.tz_localize(IST)
            else:
                ts = ts.tz_convert(IST)
            timestamps.append(ts)
        except Exception:
            continue

    if timestamps:
        result["data_as_of"] = min(timestamps)

    # 2) Load SMA200 values from persisted wealth parquet (fast lookup)
    sma_map = {}
    try:
        if os.path.exists(WEALTH_PATH):
            prev = pd.read_parquet(WEALTH_PATH)
            if "Stock" in prev.columns and "sma_200" in prev.columns:
                for _, row in prev[["Stock", "sma_200"]].iterrows():
                    sym = row["Stock"]
                    try:
                        sma_map[sym] = float(row["sma_200"]) if not pd.isna(row["sma_200"]) else None
                    except Exception:
                        sma_map[sym] = None
    except Exception as e:
        logger.warning(f"Could not read wealth parquet for SMA lookup: {e}")

    # 3) Fill missing SMA200 by computing from available recent daily frames only when possible
    for sym in symbols:
        if sym in sma_map and sma_map[sym] is not None:
            result["sma_200"][sym] = sma_map[sym]
            continue
        df = result["daily"].get(sym)
        try:
            if df is None or df.empty:
                result["sma_200"][sym] = None
                continue
            # If we have at least 200 bars in the recent fetch (unlikely for 5d), compute; else leave None
            if len(df) >= 200 and "Close" in df.columns:
                sma_val = float(df["Close"].rolling(window=200).mean().iloc[-1])
                result["sma_200"][sym] = sma_val
            else:
                result["sma_200"][sym] = None
        except Exception:
            result["sma_200"][sym] = None

    return result
