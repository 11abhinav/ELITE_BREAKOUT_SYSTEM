# =====================================================================================
# app/price_cache.py (BULLETPROOF EDITION)
# =====================================================================================

import logging
import threading
import time
import random
import gc
from memory_profiler import profile_function
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
from validation import ValidationEngine, MarketData, ValidationContext, registry as val_registry, DatasetType
from validation.result import ValidatedDataset, ValidationStatus
from validation.history import history_recorder
from config import SOURCE_RELIABILITY, MAX_HISTORY_SHRINK
import json
import os

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# [VERSION: V5_ACQUISITION_ROUTING_V1.0] Cache Schema & Metadata Invariants
CACHE_SCHEMA_VERSION = 3
INDICATOR_VERSION = "v5.2"

import hashlib

def compute_ohlcv_hash(df: pd.DataFrame) -> str:
    """Computes a fast deterministic hash of core OHLCV data for change detection."""
    if df is None or df.empty:
        return ""
    try:
        cols = [c for c in ["Date", "Datetime", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        if not cols and not df.index.empty:
            sample_data = str(df.index.tolist()[:5]) + str(df.iloc[:5].to_dict())
        else:
            sample_data = f"{len(df)}_{df[cols].iloc[0].to_dict()}_{df[cols].iloc[-1].to_dict()}"
        return hashlib.sha256(sample_data.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""

def validate_ohlcv_structure(df: pd.DataFrame) -> tuple[bool, str]:
    """
    Validates structural OHLCV integrity:
    1. Timestamp monotonicity (strictly increasing timestamps).
    2. Price sanity (High >= Low, Open & Close within Low/High bounds, Volume >= 0).
    3. Non-empty DataFrame.
    """
    if df is None or df.empty:
        return False, "EMPTY_DATAFRAME"
        
    try:
        # 1. Monotonicity
        time_col = 'Date' if 'Date' in df.columns else ('Datetime' if 'Datetime' in df.columns else None)
        if time_col:
            ts_series = pd.to_datetime(df[time_col])
        else:
            ts_series = pd.to_datetime(df.index)
            
        if not ts_series.is_monotonic_increasing:
            return False, "NON_MONOTONIC_TIMESTAMPS"
            
        # 2. Price Sanity
        if "High" in df.columns and "Low" in df.columns:
            if (df["High"] < df["Low"]).any():
                return False, "HIGH_LESS_THAN_LOW"

        # 3. Corporate Action Envelope Auto-Sanitization
        if all(col in df.columns for col in ["High", "Low", "Open", "Close"]):
            # Sanitize envelope bounds for corporate action / bonus / split adjusted historical candles
            df["High"] = df[["High", "Open", "Close"]].max(axis=1)
            df["Low"] = df[["Low", "Open", "Close"]].min(axis=1)

        if "Close" in df.columns and "High" in df.columns and "Low" in df.columns:
            if (df["Close"] > df["High"] * 1.015).any() or (df["Close"] < df["Low"] * 0.985).any():
                return False, "CLOSE_OUT_OF_BOUNDS"
                
        if "Open" in df.columns and "High" in df.columns and "Low" in df.columns:
            if (df["Open"] > df["High"] * 1.015).any() or (df["Open"] < df["Low"] * 0.985).any():
                return False, "OPEN_OUT_OF_BOUNDS"
                
        if "Volume" in df.columns:
            if (df["Volume"] < 0).any():
                return False, "NEGATIVE_VOLUME"
                
        return True, "VALID"
    except Exception as e:
        return False, f"VALIDATION_EXCEPTION_{e}"

_cache: dict[tuple, dict] = {}
_lock = threading.Lock()
_fetch_lock = threading.Lock()  # CRITICAL: Global lock to serialize API fetches across all scanners (prevents thundering herd)
CACHE_TTL_SECONDS = PRICE_CACHE_TTL_SECONDS

# Cache metrics tracking
_cache_hits = 0
_cache_misses = 0

def _log_cache_timeline():
    """Calculates and logs the current memory footprint of _cache."""
    with _lock:
        keys_count = len(_cache)
        if keys_count == 0:
            return
            
        total_dfs = 0
        total_mb = 0.0
        largest_key = None
        largest_key_mb = 0.0
        
        for k, entry in _cache.items():
            data = entry.get("data", {})
            key_mb = 0.0
            dfs_in_key = 0
            for sym, df in data.items():
                if df is not None and not df.empty:
                    dfs_in_key += 1
                    try:
                        key_mb += df.memory_usage(deep=False).sum() / (1024 * 1024)
                    except Exception:
                        pass
            
            total_dfs += dfs_in_key
            total_mb += key_mb
            
            if key_mb > largest_key_mb:
                largest_key_mb = key_mb
                largest_key = k
                
        logger.info(
            f"[CACHE_TIMELINE] Keys: {keys_count} | Memory: {total_mb:.1f} MB | "
            f"Largest: {largest_key} ({largest_key_mb:.1f} MB) | Total DFs: {total_dfs} | "
            f"Hits: {_cache_hits} | Misses: {_cache_misses}"
        )

def _cache_timeline_worker():
    while True:
        time.sleep(1800)  # Log every 30 mins
        try:
            _log_cache_timeline()
        except Exception as e:
            logger.warning(f"Failed to log cache timeline: {e}")

threading.Thread(target=_cache_timeline_worker, name="CacheTimeline", daemon=True).start()

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
        
    interval_lower = interval.lower()

    # [VERSION: DAILY_CACHE_TTL_FIX_v1.0] Daily intervals must cache until end of trading day.
    # Previously fell through to CACHE_TTL_SECONDS (60s), causing the Wealth Engine to
    # re-download 1 year of daily OHLCV data every minute. Daily bars only change once at
    # market close, so the cache should survive the entire trading session.
    if interval_lower in ('1d', 'daily', '1wk', '1mo'):
        market_close = now_dt.replace(hour=15, minute=30, second=0, microsecond=0)
        if now_dt < market_close:
            # Cache until today's market close
            secs = (market_close - now_dt).total_seconds()
            return max(300, int(secs))  # At least 5 min floor
        else:
            # After market close: cache for 12h (next run will be after next open)
            return 43200

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
    raw_cadence = max(5, int(secs) + 5)

    # [VERSION: CACHE_FLOOR_FIX_v1.0] Enforce a minimum cache floor per interval.
    # Problem: near a candle boundary (e.g. 11:14 AM for 1H candle at 11:15),
    # get_dynamic_cadence("1h") returned only ~60s. Any scanner run that started before
    # the boundary and checked the cache after would always get a miss, triggering a full
    # delta re-fetch for ALL symbols on EVERY run near that boundary.
    # Fix: floor = 50% of the interval's duration in seconds. Data within the same candle
    # period is always reused regardless of where in the cycle the scan falls.
    # Floors by interval: 5m→150s, 15m→450s, 30m→900s, 1h→1800s
    interval_floor_secs = int(val * 60 * 0.5)  # 50% of interval duration
    return max(raw_cadence, interval_floor_secs)


# [VERSION: MEMORY_RECALIBRATION_v1.0] Recalibrated profile budget from 350 MB to 500 MB to match steady-state process RSS.
@profile_function("Price Fetch", budget_mb=500.0)
def fetch_watchlist_data(watchlist: pd.DataFrame, period: str = "10d", interval: str = "15m", requester: str = None) -> dict[str, pd.DataFrame]:
    global _cache_hits, _cache_misses
    from telemetry_manager import telemetry
    # Standardize all daily (1d) requests to 1y period to maximize cross-scanner RAM cache sharing
    if interval == "1d" and period in ("6mo", "1mo", "10d", "3mo"):
        period = "1y"
    cache_key = (interval, period)
    cadence = get_dynamic_cadence(interval)
    now_mono = time.monotonic()

    with _lock:
        cache_dict = _cache.get(cache_key, {})
        cached_result = {}
        missing_symbols = []
        
        for s in watchlist["Stock"]:
            sym_entry = cache_dict.get(s)
            if not sym_entry and interval == "1d":
                sym_entry = _cache.get(("1d", "1y"), {}).get(s)
            if sym_entry and isinstance(sym_entry.get("data"), pd.DataFrame) and not sym_entry["data"].empty:
                age = now_mono - sym_entry["ts"]
                if age < cadence:
                    cached_result[s] = sym_entry["data"]
                    continue
            missing_symbols.append(s)
            
        if not missing_symbols:
            _cache_hits += len(watchlist)
            logger.debug(f"📦 Price cache hit | {interval} | {period} | All {len(watchlist)} symbols fresh in RAM")
            return cached_result
        else:
            _cache_hits += len(cached_result)
            _cache_misses += len(missing_symbols)
            logger.debug(f"📦 Price cache partial/miss | {interval} | {period} | Cached: {len(cached_result)}, Fetching: {len(missing_symbols)}")

    # CRITICAL FIX: Use global lock to serialize API fetches across all scanners
    # This prevents thundering herd where 5+ scanners fetch simultaneously
    # Lock ensures only 1 scanner fetches at a time; others hit cache (reducing API load 10-25×)
    logger.debug(f"🔒 Attempting to acquire global fetch lock for {interval}|{period}...")
    with _fetch_lock:
        logger.debug(f"🔓 Global fetch lock acquired for {interval}|{period}")
        
        # Double-check cache for missing symbols in case concurrent thread populated them while waiting for lock
        with _lock:
            cache_dict = _cache.get(cache_key, {})
            still_missing = []
            now_mono = time.monotonic()
            for s in missing_symbols:
                sym_entry = cache_dict.get(s)
                if sym_entry and isinstance(sym_entry.get("data"), pd.DataFrame) and not sym_entry["data"].empty:
                    if (now_mono - sym_entry["ts"]) < cadence:
                        cached_result[s] = sym_entry["data"]
                        continue
                still_missing.append(s)
                
            if not still_missing:
                logger.info(f"📦 Cache populated by concurrent thread for all requested symbols; reusing.")
                return {s: cached_result[s] for s in watchlist["Stock"] if s in cached_result}

        # Fetch only the missing symbols
        fetch_sub_watchlist = watchlist[watchlist["Stock"].isin(still_missing)].copy()
        if fetch_sub_watchlist.empty:
            return cached_result
            
        result = _download_all_robust(fetch_sub_watchlist, period=period, interval=interval, requester=requester)

    # Determine data_as_of timestamp from freshly fetched data
    data_as_of = None
    if result:
        timestamps = []
        for symbol, df in result.items():
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
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
                    
        if not timestamps and any(isinstance(df, pd.DataFrame) and not df.empty for df in result.values()):
            logger.error("DataFetchError: All dataframes returned malformed or missing timestamps. Aborting cache update.")
            raise ValueError("DataFetchError: Malformed timestamps across entire batch.")
            
        if timestamps:
            data_as_of = min(timestamps)
            if data_as_of.tzinfo is None:
                data_as_of = data_as_of.replace(tzinfo=IST)
            else:
                data_as_of = data_as_of.astimezone(IST)

    with _lock:
        if cache_key not in _cache:
            _cache[cache_key] = {}
        now_mono = time.monotonic()
        
        for symbol, df in result.items():
            if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                provider_name = getattr(df, 'attrs', {}).get('provider', 'unknown')
                _cache[cache_key][symbol] = {
                    "data": df,
                    "ts": now_mono,
                    "data_as_of": data_as_of,
                    "provider": provider_name,
                    "schema_version": "v8.4.0",
                    "fetch_interval": interval,
                    "fetch_period": period
                }
                cached_result[symbol] = df
            else:
                cached_result[symbol] = None

    return {s: cached_result.get(s) for s in watchlist["Stock"]}


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
    # for intraday candles (because new candles are forming right now).
    # However, for 1D candles, we consider them up to date if they have yesterday's close,
    # as live intraday CMP will be stitched natively in memory by scanners.
    if is_market_active and interval.lower() not in ('1d', 'daily', '1wk', '1mo'):
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
            except Exception: pass
            
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
                meta_path = file_path.replace('.parquet', '.meta.json')
                is_degraded = False
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r") as f:
                            meta = json.load(f)
                        if meta.get("validation_status") == "ValidationStatus.DEGRADED":
                            is_degraded = True
                    except Exception: pass
                    
                if not cached_df.empty:
                    # Find last timestamp
                    if 'Date' in cached_df.columns:
                        last_ts = pd.to_datetime(cached_df['Date'].iloc[-1])
                    elif 'Datetime' in cached_df.columns:
                        last_ts = pd.to_datetime(cached_df['Datetime'].iloc[-1])
                    else:
                        last_ts = pd.to_datetime(cached_df.index[-1])
                        
                    if pd.isna(last_ts):
                        raise ValueError("last_ts is NaT, cache file might be corrupt")
                        
                    if last_ts.tzinfo is None:
                        last_ts = last_ts.tz_localize(IST)
                    else:
                        last_ts = last_ts.tz_convert(IST)
                        
                    # 🚀 OPTIMIZATION: If data is already up to the last market close, skip DELTA fetch completely!
                    is_up_to_date = _is_cache_up_to_date(last_ts, interval)
                    is_long_enough = _is_cache_long_enough(cached_df, period, sym)
                    
                    if is_degraded:
                        is_up_to_date = False
                        logger.info(f"CACHE_POLICY | {sym} is marked DEGRADED. Forcing retry despite timestamp {last_ts}.")
                    
                    if is_up_to_date:
                        if is_long_enough:
                            # [VERSION: V5_ACQUISITION_ROUTING_V1.0] Enforce Cache Invariants: schema_version, indicator_version, ohlcv_hash
                            meta_valid = False
                            if os.path.exists(meta_path):
                                try:
                                    with open(meta_path, "r") as f:
                                        meta = json.load(f)
                                    if (meta.get("schema_version") == CACHE_SCHEMA_VERSION and 
                                        meta.get("indicator_version") == INDICATOR_VERSION and
                                        meta.get("ohlcv_hash") == compute_ohlcv_hash(cached_df)):
                                        meta_valid = True
                                except Exception:
                                    pass

                            if not cached_df.empty and (not meta_valid or "EMA20" not in cached_df.columns):
                                from technical_indicators import apply_indicators
                                cached_df = apply_indicators(cached_df, timeframe=interval)
                                try:
                                    cached_df.to_parquet(file_path)
                                    new_meta = {
                                        "schema_version": CACHE_SCHEMA_VERSION,
                                        "indicator_version": INDICATOR_VERSION,
                                        "ohlcv_hash": compute_ohlcv_hash(cached_df),
                                        "generated_at": time.time(),
                                        "row_count": len(cached_df)
                                    }
                                    with open(meta_path, "w") as f:
                                        json.dump(new_meta, f)
                                except Exception as e:
                                    logger.warning(f"Failed to resave enriched cache for {sym}: {e}")
                                    
                            all_data[sym] = cached_df
                            needs_full = False
                            fresh_count += 1
                            continue
                        else:
                            # It's up to date but not long enough (e.g. 5d requested before, but now 1y requested)
                            needs_full = True
                    else:
                        # Not up to date. If it is long enough, we can do a DELTA fetch.
                        if is_long_enough:
                            needs_full = False
                        else:
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
                batch_validation_items = []
                batch_indicator_jobs = []
                batch_symbol_meta = {}

                for sym in batch:
                    # Ingestion Boundary Canonical Symbol Lookup: Try sym, sym.NS, sym.BO, and base symbol
                    md = batch_results.get(sym)
                    if md is None:
                        md = batch_results.get(f"{sym}.NS") or batch_results.get(f"{sym}.BO") or batch_results.get(sym.split('.')[0])
                    
                    cached_df = next((item[1] for item in items if item[0] == sym), None)
                    
                    if md is None:
                        if cached_df is not None and not cached_df.empty:
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                        else:
                            all_data[sym] = None
                        continue
                        
                    new_df = md.dataframe
                    new_report = md.quality_report
                    remote_source = md.source
                    
                    if new_report:
                        batch_validation_items.append(
                            ValidatedDataset(
                                data=new_df, 
                                result=new_report, # DataQualityReport is compatible enough for history_recorder (it has score, status, etc)
                                score=new_report.quality_score, 
                                status=new_report.status
                            )
                        )
                        
                    if new_df is None:
                        if cached_df is not None and not cached_df.empty:
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                        else:
                            all_data[sym] = None
                        continue
                    
                    # Cache Decision Engine
                    if cached_df is not None and not cached_df.empty:
                        pipeline = val_registry.get_pipeline(DatasetType.PRICE)
                        engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
                        ctx = ValidationContext(cache_df=None, provider="Cache", period=period, interval=interval, range_from=range_from, range_to=range_to, fetch_mode="DELTA" if range_from else "FULL")
                        cache_report = engine.validate(cached_df, ctx)
                        
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
                            logger.info(f"CACHE_DECISION | Action=KEEP_CACHE | Reason=REMOTE_LOWER_QUALITY | Symbol={sym} | CacheScore={cache_score} | RemoteScore={remote_score}")
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
                            
                            # [VERSION: TIME_COLUMN_MERGE_FIX] Standardize to 'Datetime' to prevent NaN gaps during concat
                            if time_col == 'Date':
                                new_df = new_df.rename(columns={'Date': 'Datetime'})
                                time_col = 'Datetime'
                                
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
                                    
                                # [VERSION: TIME_COLUMN_MERGE_FIX] Standardize to 'Datetime' to prevent NaN gaps during concat
                                if c_time_col == 'Date':
                                    cached_df = cached_df.rename(columns={'Date': 'Datetime'})
                                    c_time_col = 'Datetime'
                                    
                            elif not cached_df.index.empty:
                                cached_df.index = pd.to_datetime(cached_df.index)
                                if cached_df.index.tz is None:
                                    cached_df.index = cached_df.index.tz_localize('Asia/Kolkata')
                                else:
                                    cached_df.index = cached_df.index.tz_convert('Asia/Kolkata')

                            # [VERSION: CACHE_MERGE_ALIGNMENT_FIX] Align structural mismatch (time in column vs index)
                            if time_col and not c_time_col:
                                cached_df = cached_df.reset_index()
                                if 'Date' in cached_df.columns:
                                    cached_df = cached_df.rename(columns={'Date': 'Datetime'})
                                elif 'index' in cached_df.columns:
                                    cached_df = cached_df.rename(columns={'index': 'Datetime'})
                                c_time_col = 'Datetime'
                            elif c_time_col and not time_col:
                                new_df = new_df.reset_index()
                                if 'Date' in new_df.columns:
                                    new_df = new_df.rename(columns={'Date': 'Datetime'})
                                elif 'index' in new_df.columns:
                                    new_df = new_df.rename(columns={'index': 'Datetime'})
                                time_col = 'Datetime'

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
                            
                            # [VERSION: CACHE_INDEX_FIX] If time is in a column, reset the index to prevent PyArrow
                            # from crashing on a mixed type index resulting from concat.
                            if time_col_comb:
                                combined = combined.reset_index(drop=True)
                            
                            all_data[sym] = combined
                        else:
                            # [VERSION: FRESH_DATA_SORT_FIX_v1.0] Deduplicate and sort fresh DataFrames by date before validation
                            if time_col:
                                new_df[time_col] = pd.to_datetime(new_df[time_col])
                                new_df = new_df.drop_duplicates(subset=[time_col], keep='last').sort_values(time_col).reset_index(drop=True)
                            elif not new_df.index.empty:
                                new_df.index = pd.to_datetime(new_df.index)
                                new_df = new_df[~new_df.index.duplicated(keep='last')].sort_index()
                            all_data[sym] = new_df
                            
                        # [VERSION: V5_ACQUISITION_ROUTING_V1.0] OHLCV Validation Stage before indicator calculation
                        if not all_data[sym].empty:
                            is_valid_struct, reason = validate_ohlcv_structure(all_data[sym])
                            if not is_valid_struct:
                                logger.warning(f"⚠️ OHLCV Structure Validation Failed for {sym}: {reason}. Reverting to stale cache if available.")
                                if cached_df is not None and not cached_df.empty:
                                    cached_df.attrs['is_stale'] = True
                                    all_data[sym] = cached_df
                                else:
                                    all_data[sym] = None
                                continue

                            batch_indicator_jobs.append({
                                "symbol": sym,
                                "timeframe": interval,
                                "dataframe": all_data[sym]
                            })
                            batch_symbol_meta[sym] = {
                                "new_df": new_df,
                                "new_report": new_report
                            }
                    else:
                        # Fallback to stale cached data if fresh fetch returned empty
                        if cached_df is not None and not cached_df.empty:
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df

                # Run indicator calculations concurrently for all symbols in this batch
                if batch_indicator_jobs:
                    from indicator_executor import indicator_executor
                    exec_res = indicator_executor.execute(batch_indicator_jobs)
                    for job in batch_indicator_jobs:
                        sym = job["symbol"]
                        if sym in exec_res and exec_res[sym] is not None:
                            all_data[sym] = exec_res[sym]

                        meta_info = batch_symbol_meta.get(sym, {})
                        n_df = meta_info.get("new_df")
                        n_rep = meta_info.get("new_report")

                        # Record earliest date
                        if group_key == "FULL" and n_df is not None and not n_df.empty and len(n_df) >= 10 and period.lower() in ("max", "10y", "5y", "2y", "1y", "ytd"):
                            try:
                                t_col = 'Date' if 'Date' in n_df.columns else ('Datetime' if 'Datetime' in n_df.columns else None)
                                earliest_ts = pd.to_datetime(n_df[t_col].iloc[0]) if t_col else pd.to_datetime(n_df.index[0])
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
                            if isinstance(all_data[sym].columns, pd.MultiIndex):
                                all_data[sym].columns = ['_'.join(map(str, col)).strip() for col in all_data[sym].columns.values]
                            all_data[sym].columns = all_data[sym].columns.astype(str)
                            
                            time_cols = ['Date', 'Datetime']
                            for col in all_data[sym].columns:
                                if col not in time_cols and all_data[sym][col].dtype == 'object':
                                    all_data[sym][col] = pd.to_numeric(all_data[sym][col], errors='coerce')
                                    
                            if all_data[sym].index.name in time_cols or isinstance(all_data[sym].index, pd.DatetimeIndex):
                                all_data[sym].index = pd.to_datetime(all_data[sym].index, errors='coerce')
                            elif not isinstance(all_data[sym].index, pd.RangeIndex):
                                all_data[sym].index = all_data[sym].index.astype(str)
                            
                            all_data[sym].to_parquet(file_path)
                            
                            meta_path = file_path.replace('.parquet', '.meta.json')
                            meta = {
                                "schema_version": CACHE_SCHEMA_VERSION,
                                "indicator_version": INDICATOR_VERSION,
                                "ohlcv_hash": compute_ohlcv_hash(all_data[sym]),
                                "generated_at": time.time(),
                                "row_count": len(all_data[sym]),
                                "validation_score": n_rep.quality_score if n_rep else 100,
                                "validation_status": str(n_rep.status) if n_rep else "ValidationStatus.VALID",
                                "validator_name": n_rep.validator_name if n_rep else "Unknown"
                            }
                            with open(meta_path, "w") as f:
                                json.dump(meta, f)
                        except Exception as e:
                            logger.exception(f"Failed to write disk cache for {sym}")
                
                # Record batch validation history
                history_recorder.record_batch(DatasetType.PRICE, batch_validation_items)
            else:
                logger.error(f"❌ Batch {desc} failed or returned empty for {len(batch)} symbols.")
                rate_limited = True
                
                # Record empty batch history
                history_recorder.record_batch(DatasetType.PRICE, [], fallback_status=ValidationStatus.INVALID)
                
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
        if df is None or isinstance(df, ProviderResult):
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

@profile_function("Hist Fetch", budget_mb=400.0)
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


def get_intraday_snapshot(symbols: list[str], interval: str = "5m", period: str = "5d", wait_timeout: int = 30, requester: str = None, cadence_override: int = None) -> dict[str, pd.DataFrame]:
    requester = requester or threading.current_thread().name or "Unknown"
    """
    Return cached intraday frames for (interval, period) for the provided symbols.
    If cache is stale or missing, a single thread will perform the fetch and others
    will wait up to `wait_timeout` seconds for the result. This guarantees only one
    fetch per cache key is in-flight at any time.

    cadence_override: If set, overrides the default per-interval cadence for the stale check.
    Use this when the caller can tolerate slightly older data to avoid unnecessary re-fetches.
    Example: Wealth Engine passes 900s (15 min) so it reuses cached data instead of
    triggering a full 10-minute re-fetch of 302 symbols on every scan cycle.

    Returns the raw mapping: { symbol: DataFrame }
    """
    cache_key = (interval, period)
    cadence = cadence_override if cadence_override is not None else _INTERVAL_CADENCE.get(interval, CACHE_TTL_SECONDS)
    jitter = _TTL_JITTER.get(interval, 0) if cadence_override is None else 0
    cadence_with_jitter = cadence + jitter

    # Quick cache check
    with _lock:
        cache_dict = _cache.get(cache_key)
        if cache_dict:
            now_mono = time.monotonic()
            res = {}
            all_hit = True
            for s in symbols:
                sym_entry = cache_dict.get(s)
                if sym_entry and isinstance(sym_entry.get("data"), pd.DataFrame) and not sym_entry["data"].empty:
                    age = now_mono - sym_entry.get("ts", 0)
                    if age < cadence_with_jitter:
                        res[s] = sym_entry["data"]
                        continue
                all_hit = False
                break
            if all_hit and len(res) == len(symbols):
                logger.debug(f"[{requester}] 📦 Intraday cache hit | {interval}|{period} | All {len(symbols)} symbols fresh")
                # [VERSION: DATA_FETCH_ACCELERATION_v1.0] Stitch 1-second live price tick into last candle
                from market_utils import is_market_open
                if is_market_open() and not os.environ.get("PYTEST_CURRENT_TEST"):
                    try:
                        from live_prices import get_live_prices
                        live_prices_map = get_live_prices(list(res.keys()))
                        for sym, df_item in res.items():
                            if isinstance(df_item, pd.DataFrame) and not df_item.empty and sym in live_prices_map:
                                lp = live_prices_map[sym]
                                if lp and float(lp) > 0:
                                    df_item.iloc[-1, df_item.columns.get_loc("Close")] = float(lp)
                    except Exception:
                        pass
                return res

        # If another thread is already fetching this key, wait for it to complete
        inflight = _inflight_fetches.get(cache_key)

    # If inflight exists, wait for completion then return cache (may still be missing)
    if inflight:
        inflight.wait(wait_timeout)
        with _lock:
            cache_dict = _cache.get(cache_key, {})
            return {s: cache_dict[s]["data"] if s in cache_dict and isinstance(cache_dict[s], dict) else None for s in symbols}

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
            cache_dict = _cache.get(cache_key, {})
            return {s: cache_dict[s]["data"] if s in cache_dict and isinstance(cache_dict[s], dict) else None for s in symbols}

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


def get_price_cache_stats() -> dict:
    """Calculates number of keys, total symbol dataframes, and estimated memory in MB."""
    with _lock:
        keys_count = len(_cache)
        total_dfs = 0
        total_bytes = 0
        for entry in _cache.values():
            if isinstance(entry, dict) and "data" in entry and isinstance(entry["data"], dict):
                total_dfs += len(entry["data"])
                for df in entry["data"].values():
                    if isinstance(df, pd.DataFrame):
                        try:
                            total_bytes += df.memory_usage(deep=False).sum()
                        except Exception:
                            pass
        return {
            "keys": keys_count,
            "entries": total_dfs,
            "memory_mb": round(total_bytes / (1024 * 1024), 2)
        }


def clear_price_cache():
    """Explicitly release all in-memory price dataframes and trim heap allocation."""
    stats_before = get_price_cache_stats()
    with _lock:
        for k, entry in list(_cache.items()):
            if isinstance(entry, dict):
                data = entry.get("data")
                if isinstance(data, dict):
                    data.clear()
                entry.clear()
        _cache.clear()
    gc.collect()
    try:
        import sys
        if sys.platform.startswith("linux"):
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    stats_after = get_price_cache_stats()
    logger.info(
        f"🧹 [PRICE_CACHE PURGE] Before: keys={stats_before['keys']} | entries={stats_before['entries']} | memory={stats_before['memory_mb']} MB → "
        f"After: keys={stats_after['keys']} | entries={stats_after['entries']} | memory={stats_after['memory_mb']} MB"
    )
    return stats_before, stats_after



