import io
import os
import time
import json
import logging
from dataclasses import dataclass
from typing import Optional

@dataclass
class FairValueResult:
    fair_value: float
    bear_value: float
    bull_value: float
    valuation_method: str
    valuation_confidence: str
    peer_count: Optional[int]
    target_multiple: Optional[float]
    current_multiple: Optional[float]
    peer_multiple: Optional[float]
    is_fallback: bool

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

import requests
import threading
import pandas as pd
import yfinance as yf
from typing import Dict, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from psycopg2.extras import execute_values

from database import get_connection, save_alert_if_new, close_position, update_alert_outcome, init_db, upsert_scanner_health
from telegram_engine import queue_telegram_message
from wealth_risk_adjusted_sizing import calculate_risk_adjusted_sizing
from core.multibagger_pipeline import run_pipeline_for_symbol

logger = logging.getLogger("multibagger")
IST = ZoneInfo("Asia/Kolkata")
CACHE_PATH = "data/multibagger_fundamentals_cache.json"



@dataclass
class StockPriceData:
    symbol: str
    price: float
    change_pct: float
    low_52w: float
    high_52w: float
    turnover_20d: float
    sma_20: float
    sma_50: float
    sma_200: float
    high_20d: float
    high_60d: float
    mom_3m: float
    mom_6m: float
    atr_14: float
    ema_20: float
    latest_volume: float
    volume_sma20: float
    close_yesterday: float
    sma_200_yesterday: float
    closes_below_sma200_count: int = 0
    last_trade_date: str = ""

@dataclass
class ExitPriceData:
    symbol: str
    price: float
    sma_50: float
    sma_200: float
    high_20d: float
    close_yesterday: float
    sma_200_yesterday: float
    atr_14: float
    ema_20: float
    closes_below_sma200_count: int = 0

@dataclass
class ScreenerResult:
    symbol: str
    price: float
    cqs: float
    pas: float
    trend_score: float
    total_score: float
    buy_zone_low: float
    buy_zone_high: float
    bucket: str
    status: str
    notes: str
    change_pct: float = 0.0

from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError, get_backoff_delay


def safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)
    except Exception:
        return default

def load_cache() -> dict:
    """Load local fundamentals JSON cache file."""
    if not os.path.exists(CACHE_PATH):
        try:
            from database import download_parquet_from_db
            if download_parquet_from_db("multibagger_cache", CACHE_PATH):
                logger.info("☁️ [CACHE] Restored multibagger fundamentals cache from Postgres DB")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore multibagger cache from DB: {e}")
            
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load fundamentals cache: {e}")
    return {}

def save_fundamentals_cache(cache_data: dict):
    """Write current fundamentals to local JSON cache file."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(cache_data, f, indent=4)
        logger.info(f"💾 Fundamentals cache saved with {len(cache_data)} entries.")
        
        # Backup to Postgres DB so it survives Railway restarts
        try:
            from database import upload_parquet_to_db
            upload_parquet_to_db("multibagger_cache", CACHE_PATH)
            logger.info("☁️ [CACHE] Uploaded multibagger fundamentals cache to Postgres DB")
        except Exception as e:
            logger.warning(f"⚠️ Failed to backup multibagger cache to DB: {e}")
            
    except Exception as e:
        logger.exception(f"❌ Failed to save fundamentals cache")


def batch_download_market_data(symbols: list) -> dict:
    """Download historical price/volume data in bulk for all tickers using the unified price cache.

    [VERSION: MULTIBAGGER_CACHE_FIX_v1.0] Previously called fetcher.get_batch_ohlcv() directly,
    bypassing price_cache entirely. This caused two problems:
    1. Every call re-fetched all symbols from YFinance with no caching (double-fetch observed in logs).
    2. The exit monitor and main scanner both fetched independently even within the same run.
    Now routes through fetch_unified_historical → fetch_watchlist_data → price_cache, which:
    - Caches 1D data until market close (TTL = seconds until 15:30 IST)
    - Shares the cache with EOD, Reversal, and Wealth Engine (1d, 1y key)
    - Eliminates redundant YFinance calls within the same scan cycle
    """
    from price_cache import fetch_unified_historical
    from market_utils import is_market_open
    import os, psutil, gc, time

    BATCH_SIZE = int(os.environ.get("MULTIBAGGER_FETCH_BATCH_SIZE", "50"))
    logger.info(f"📥 Centralized chunked downloading 1y history for {len(symbols)} tickers (Chunk size: {BATCH_SIZE})...")

    ist_now = datetime.now(IST)
    strip_forming = is_market_open(ist_now)

    results = {}

    from memory_profiler import chunk_iterable, BatchMemoryTracker
    total_batches = (len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE

    # Process symbols in chunks to flatten Peak Memory (O(BATCH_SIZE) instead of O(N))
    for batch_num, chunk in enumerate(chunk_iterable(symbols, BATCH_SIZE), start=1):
        with BatchMemoryTracker("MULTIBAGGER", batch_num, total_batches, len(chunk), collect_gc=True) as tracker:

            # 1. Fetch chunk DataFrames via price_cache (shared cache, avoids redundant API calls)
            raw_dict = fetch_unified_historical(chunk, period="1y", interval="1d", requester="multibagger")
            if not raw_dict:
                continue

            from core_enums import ProviderResult
            rows_fetched = sum(len(df) for df in raw_dict.values() if df is not None and isinstance(df, pd.DataFrame) and not df.empty)
            tracker.mark_fetch_complete(row_count=rows_fetched)
            batch_res = {sym: type("_MD", (), {"dataframe": df})() for sym, df in raw_dict.items() if df is not None}

        
        # 2. Convert DataFrames to StockPriceData
            for sym, md in batch_res.items():
                from core_enums import ProviderResult
                if md is None or isinstance(md, ProviderResult):
                    continue
            
                ticker_df = md.dataframe if hasattr(md, "dataframe") else md
                if ticker_df is None or getattr(ticker_df, "empty", True):
                    continue
                
                try:
                    ticker_df = ticker_df.dropna(subset=["Close"])
                    if ticker_df.empty:
                        continue
                    
                    if "Date" in ticker_df.columns:
                        ticker_df = ticker_df.set_index("Date")
                    elif "Datetime" in ticker_df.columns:
                        ticker_df = ticker_df.set_index("Datetime")
                    
                    if isinstance(ticker_df.index, pd.DatetimeIndex):
                        if ticker_df.index.tz is None:
                            ticker_df.index = ticker_df.index.tz_localize(IST)
                        else:
                            ticker_df.index = ticker_df.index.tz_convert(IST)
                    
                    real_time_close_series = ticker_df["Close"]
                    real_time_close = float(real_time_close_series.iloc[-1])
                    if len(real_time_close_series) >= 2:
                        real_time_prev = float(real_time_close_series.iloc[-2])
                        real_time_change = ((real_time_close - real_time_prev) / real_time_prev) * 100.0 if real_time_prev > 0 else 0.0
                    else:
                        real_time_change = 0.0

                    if strip_forming and len(ticker_df) > 0:
                        last_ts = ticker_df.index[-1]
                        if last_ts.date() == ist_now.date():
                            ticker_df = ticker_df.iloc[:-1]
                        
                    if len(ticker_df) < 50:
                        continue
                
                    last_trade_date = str(ticker_df.index[-1].date())
                
                    close_series = ticker_df["Close"]
                    vol_series = ticker_df["Volume"] if "Volume" in ticker_df.columns else pd.Series([0]*len(ticker_df))
                
                    close_price = real_time_close
                    change_pct = real_time_change
                
                    close_yesterday = float(close_series.iloc[-2]) if len(close_series) >= 2 else float(close_series.iloc[-1])
                
                    if "High" in ticker_df.columns and "Low" in ticker_df.columns:
                        high_52w = float(ticker_df["High"].max())
                        low_52w = float(ticker_df["Low"].min())
                    else:
                        high_52w = float(close_series.max())
                        low_52w = float(close_series.min())
                
                    recent_20 = ticker_df.tail(20)
                    if not recent_20.empty and "Volume" in recent_20.columns:
                        avg_turnover = float((recent_20["Volume"] * recent_20["Close"]).mean())
                    else:
                        avg_turnover = 0.0
                
                    hist_idx_6m = min(120, len(close_series) - 1)
                    close_6m_ago = float(close_series.iloc[-(hist_idx_6m + 1)])
                    mom_6m = ((close_price - close_6m_ago) / close_6m_ago) if close_6m_ago > 0 else 0.0
                    
                    high_20d = float(close_series.rolling(20).max().iloc[-1])
                    high_60d = float(close_series.rolling(60).max().iloc[-1]) if len(close_series) >= 60 else high_20d
                
                    hist_idx = min(60, len(close_series) - 1)
                    close_3m_ago = float(close_series.iloc[-(hist_idx + 1)])
                    mom_3m = ((close_price - close_3m_ago) / close_3m_ago) if close_3m_ago > 0 else 0.0
                
                    latest_volume = float(vol_series.iloc[-1])
                    volume_sma20 = float(vol_series.rolling(20).mean().iloc[-1]) if len(vol_series) >= 20 else latest_volume
                
                    from indicator_manager import manager
                    bundle = manager.compute_base_indicators(ticker_df, sym)
                    
                    sma_20 = float(bundle.sma_20.iloc[-1]) if bundle.sma_20 is not None and not bundle.sma_20.empty else close_price
                    sma_50 = float(bundle.sma_50.iloc[-1]) if bundle.sma_50 is not None and not bundle.sma_50.empty else close_price
                    sma_200 = float(bundle.sma_200.iloc[-1]) if bundle.sma_200 is not None and not bundle.sma_200.empty else close_price
                    sma_200_yesterday = float(bundle.sma_200.iloc[-2]) if bundle.sma_200 is not None and len(bundle.sma_200) >= 2 else sma_200
                    
                    atr_14 = float(bundle.atr_14.iloc[-1]) if bundle.atr_14 is not None and not bundle.atr_14.empty else (close_price * 0.05)
                    ema_20 = float(bundle.ema_20.iloc[-1]) if bundle.ema_20 is not None and not bundle.ema_20.empty else close_price
                
                    closes_below_sma200_count = 0
                    if len(close_series) >= 5 and bundle.sma_200 is not None and len(bundle.sma_200.dropna()) >= 5:
                        last_5_closes = close_series.iloc[-5:]
                        last_5_smas = bundle.sma_200.iloc[-5:]
                        closes_below_sma200_count = sum(1 for c, s in zip(last_5_closes, last_5_smas) if c < s)
                
                    results[sym] = StockPriceData(
                        symbol=sym,
                        price=close_price,
                        change_pct=change_pct,
                        low_52w=low_52w,
                        high_52w=high_52w,
                        turnover_20d=avg_turnover,
                        sma_20=sma_20,
                        sma_50=sma_50,
                        sma_200=sma_200,
                        high_20d=high_20d,
                        high_60d=high_60d,
                        mom_3m=mom_3m,
                        mom_6m=mom_6m,
                        latest_volume=latest_volume,
                        volume_sma20=volume_sma20,
                        close_yesterday=close_yesterday,
                        sma_200_yesterday=sma_200_yesterday,
                        atr_14=atr_14,
                        ema_20=ema_20,
                        closes_below_sma200_count=closes_below_sma200_count,
                        last_trade_date=last_trade_date
                    )
                except Exception as e:
                    logger.debug(f"Error parsing market data for {sym}: {e}")
                
        del batch_res
        locals().pop('ticker_df', None)
        locals().pop('md', None)
        locals().pop('close_series', None)
        locals().pop('vol_series', None)
        locals().pop('sma_200_series', None)
            
    logger.info(f"✅ Successfully parsed price data for {len(results)}/{len(symbols)} tickers.")
    return results

def is_financial_sector(sector: str) -> bool:
    """Identify if the sector represents a bank, NBFC, or financial services firm."""
    if not sector:
        return False
    sec_lower = str(sector).lower()
    return any(keyword in sec_lower for keyword in ["financ", "bank", "nbfc", "insurance"])

def passes_multibagger_quality_gate(f: dict) -> tuple[bool, str]:
    """
    Hard pre-scoring quality gate for Multibagger alerts.
    """
    # [VERSION: MULTIBAGGER_GATE_FIX_v1.1] Fixed missing data penalties & added minimum known metrics floor
    known_metrics_count = 0
    
    # Universal checks (non-financials prioritize ROCE, financials prioritize ROE checked below)
    is_fin = f.get("is_financial", False)
    if not is_fin:
        # Check if ROCE or ROE is present
        roce_val = f.get("roce", f.get("roe"))
        if roce_val is not None and not pd.isna(roce_val):
            known_metrics_count += 1
            roce = safe_float(roce_val)
            if roce < 0.10:
                return False, f"ROCE/ROE below 10% ({roce*100:.1f}%)"
    
    rev_cagr = f.get("revenue_cagr_3y")
    if rev_cagr is not None and not pd.isna(rev_cagr):
        known_metrics_count += 1
        if safe_float(rev_cagr) < 0.00:
            return False, f"Revenue CAGR 3Y negative ({safe_float(rev_cagr)*100:.1f}%)"
        
    # [VERSION: PLEDGE_GATE_FIX_v1.0] Safe handling of None/null for promoter_pledge_pct in quality gate
    pledge_val = f.get("promoter_pledge_pct")
    if pledge_val is not None and not pd.isna(pledge_val):
        pledge = safe_float(pledge_val)
        if pledge > 0.20:
            return False, f"High promoter pledge ({pledge*100:.1f}%)"
        
    if f.get("auditor_flags") is True:
        return False, "Auditor/Forensic red flags"

    if is_fin:
        roe = f.get("roe")
        if roe is not None and not pd.isna(roe):
            known_metrics_count += 1
            if safe_float(roe) < 0.10: # Financials allowed slightly lower ROE but still positive
                return False, f"Financial ROE below 10% ({safe_float(roe)*100:.1f}%)"
            
        gnpa = f.get("gnpa")
        if gnpa is not None and not pd.isna(gnpa):
            known_metrics_count += 1
            if safe_float(gnpa) > 0.05:
                return False, f"High GNPA ({safe_float(gnpa)*100:.1f}%)"
            
        car = f.get("capital_adequacy_ratio")
        if car is not None and not pd.isna(car):
            known_metrics_count += 1
            if safe_float(car) < 0.12:
                return False, f"Low CAR ({safe_float(car)*100:.1f}%)"
            
        roa = f.get("roa")
        if roa is not None and not pd.isna(roa):
            known_metrics_count += 1
            if safe_float(roa) < 0.01:
                return False, f"ROA below 1% ({safe_float(roa)*100:.2f}%)"
    else:
        opm = f.get("operating_margin_ttm")
        if opm is not None and not pd.isna(opm):
            known_metrics_count += 1
            if safe_float(opm) < 0.08:
                return False, f"Operating margin below 8% ({safe_float(opm)*100:.1f}%)"
            
        fcf_margin = f.get("fcf_margin")
        if fcf_margin is not None and not pd.isna(fcf_margin):
            known_metrics_count += 1
            if safe_float(fcf_margin) < 0.00:
                return False, f"Negative FCF conversion ({safe_float(fcf_margin)*100:.1f}%)"
            
        cfo_pat = f.get("cfo_pat_ratio")
        if cfo_pat is not None and not pd.isna(cfo_pat):
            known_metrics_count += 1
            if safe_float(cfo_pat) < 0.5:
                return False, f"Poor cash conversion CFO/PAT ({safe_float(cfo_pat):.2f})"
            
        de = f.get("debt_equity")
        if de is not None and not pd.isna(de):
            known_metrics_count += 1
            if safe_float(de) > 2.0:
                return False, f"Debt/Equity > 2.0 ({safe_float(de):.2f})"
            
        icr = f.get("interest_coverage_ratio")
        if icr is not None and not pd.isna(icr):
            known_metrics_count += 1
            if safe_float(icr) < 3.0:
                return False, f"Interest coverage < 3x ({safe_float(icr):.1f})"
            
        altman_z = f.get("altman_z")
        if altman_z is not None and not pd.isna(altman_z):
            known_metrics_count += 1
            # [VERSION: MULTIBAGGER_Z_FIX_v1.0] Check if service sector to apply solvent threshold of 1.10 vs 1.80 for manufacturing
            is_svc = any(k in str(f.get("sector", "")).lower() for k in ["technology", "communication", "services"])
            z_threshold = 1.10 if is_svc else 1.80
            if safe_float(altman_z) < z_threshold:
                return False, f"Altman-Z in distress zone ({safe_float(altman_z):.2f} < {z_threshold})"

    # Minimum data footprint check: At least 2 fundamental metrics must be known
    if known_metrics_count < 2:
        return False, f"Data Void: Only {known_metrics_count} fundamental metrics known"

    return True, ""

def classify_conviction(cqs: float, pas: float, trend: float, composite: float) -> tuple[str, float]:
    """
    Tiered classification for multibaggers.
    Returns (Tier, Score)
    """
    if composite >= 75 and cqs >= 65 and pas >= 50 and trend >= 10.0:
        return "🚀 Prime Multibagger", composite
    elif composite >= 65 and cqs >= 60 and trend >= 10.0:
        return "💎 High Quality", composite
    elif composite >= 50:
        return "🟡 Watchlist", composite
    else:
        return "Invalidated", composite

def entry_confirmed(price_data: StockPriceData) -> bool:
    """
    Ensures technical stabilization before entry.
    # [VERSION: MULTIBAGGER_EMA20_FIX_v1.0] Fix entry EMA block
    [FINDING-C FIX] Removed not_freefall (price >= yesterday). A fundamentally
    prime stock pulling back into its buy zone on a red day is the ideal entry.
    The V5 pipeline already validates the technical buy zone.
    """
    if price_data.price < price_data.sma_200:
        return False
        
    volume_ok = price_data.latest_volume >= 0.8 * price_data.volume_sma20 if price_data.volume_sma20 > 0 else True
    
    return volume_ok

def get_cached_fundamentals(symbol: str, cache: dict) -> Optional[Dict[str, Any]]:
    if symbol not in cache:
        return None
    try:
        data = cache[symbol]
        # 1. Check validity and freshness
        fetched_at_str = data.get("fetched_at", datetime.now(IST).isoformat())
        fetched_at = datetime.fromisoformat(fetched_at_str)
        now_dt = datetime.now(IST)
        # Ensure it has timezone info
        if fetched_at.tzinfo is None:
            logger.warning(f"[TIMEZONE] Upgrading legacy naive cache timestamp for {symbol} to IST.")
            # [VERSION: MB_CACHE_TZ_FIX] Upgrade naive timestamp instead of discarding cache to preserve rate limits
            fetched_at = fetched_at.replace(tzinfo=IST)
            
        age_days = (now_dt - fetched_at).days
        if age_days < 7:
            return {k: v for k, v in data.items() if k != "fetched_at"}
    except Exception as e:
        logger.debug(f"Failed to parse cache entry for {symbol}: {e}")
    return None


def safe_extract(df, row_name, col_idx=0, default=None):
    try:
        if row_name in df.index:
            val = df.loc[row_name].iloc[col_idx]
            if not pd.isna(val): return float(val)
    except (TypeError, ValueError, KeyError, IndexError) as e:
        logger.debug(f"Extract error for {row_name}: {e}")
    return default

def compute_cagr(df, row_name, years=3):
    try:
        if row_name not in df.index: return None
        row = df.loc[row_name].dropna()
        if len(row) < 2: return None
        latest = float(row.iloc[0])
        idx = min(years, len(row) - 1)
        oldest = float(row.iloc[idx])
        if oldest and oldest > 0 and latest and latest > 0:
            return ((latest / oldest) ** (1.0 / idx)) - 1.0
    except (TypeError, ValueError, KeyError, IndexError) as e:
        logger.debug(f"CAGR error for {row_name}: {e}")
    return None

def fetch_ticker_fundamentals(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        from bse_mapping_utils import load_bse_mappings, save_bse_mapping
        clean_sym = symbol.strip().upper()
        mappings = load_bse_mappings()
        if clean_sym in mappings:
            ticker_name = mappings[clean_sym]
        elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
            ticker_name = mappings[clean_sym[:-3]]
        else:
            ticker_name = f"{symbol}.NS"
    except Exception:
        ticker_name = f"{symbol}.NS"
        
    ticker = yf.Ticker(ticker_name)
    info, fast_info, fin, bs, cf = None, None, None, None, None
    success = False
    
    for attempt in range(3):
        try:
            yf_acquire(context=f"Multibagger Scanner | {symbol}")
            try:
                info = ticker.info
                fast_info = ticker.fast_info
                fin = ticker.financials
                bs = ticker.balance_sheet
                cf = ticker.cashflow
            finally:
                yf_release()
                
            mc = info.get("marketCap") if info else None
            if mc is None and fast_info:
                mc = fast_info.get("marketCap")
                
            if (fin is None or fin.empty or not mc) and ticker_name.endswith(".NS"):
                bse_sym = ticker_name[:-3] + ".BO"
                logger.info(f"🔄 Multibagger: financials/marketCap missing for {ticker_name}, retrying with {bse_sym}...")
                yf_acquire(context=f"Multibagger Scanner | {symbol}")
                try:
                    ticker = yf.Ticker(bse_sym)
                    info = ticker.info
                    fast_info = ticker.fast_info
                    fin = ticker.financials
                    bs = ticker.balance_sheet
                    cf = ticker.cashflow
                    ticker_name = bse_sym
                    if not (fin is None or fin.empty):
                        try:
                            from bse_mapping_utils import save_bse_mapping
                            save_bse_mapping(symbol, bse_sym)
                        except Exception:
                            pass
                finally:
                    yf_release()
            
            # [VERSION: MULTIBAGGER_REVERSE_FALLBACK_v1.0] Poisoned BO mapping → recover via NS
            # If the mapping pointed us to .BO but it returned empty financials, the BSE ticker
            # is likely delisted/suspended. Invalidate the mapping and retry via NSE.
            elif (fin is None or fin.empty) and ticker_name.endswith(".BO"):
                logger.info(f"🗑️ Multibagger: poisoned BSE mapping for {symbol} ({ticker_name}). Invalidating and retrying via NSE...")
                try:
                    from bse_mapping_utils import load_bse_mappings, invalidate_bse_mapping
                    orig_clean = symbol.strip().upper()
                    # Strip any suffix — DB stores bare symbol
                    bare_orig = orig_clean[:-3] if orig_clean.endswith(".NS") or orig_clean.endswith(".BO") else orig_clean
                    invalidate_bse_mapping(bare_orig)
                except Exception as inv_err:
                    logger.warning(f"Failed to invalidate poisoned mapping for {symbol}: {inv_err}")
                ns_sym = ticker_name[:-3] + ".NS"
                yf_acquire(context=f"Multibagger Scanner | {symbol} (NS recovery)")
                try:
                    ticker_ns = yf.Ticker(ns_sym)
                    fin_ns = ticker_ns.financials
                    bs_ns = ticker_ns.balance_sheet
                    if not (fin_ns is None or fin_ns.empty):
                        ticker = ticker_ns
                        info = ticker_ns.info
                        fast_info = ticker_ns.fast_info
                        fin = fin_ns
                        bs = bs_ns
                        cf = ticker_ns.cashflow
                        ticker_name = ns_sym
                        logger.info(f"✅ Multibagger: NSE recovery succeeded for {symbol} via {ns_sym}")
                    else:
                        logger.warning(f"⚠️ Multibagger: both .BO and .NS returned empty for {symbol}. Skipping.")
                except Exception as ns_err:
                    logger.warning(f"Multibagger NS recovery failed for {symbol}: {ns_err}")
                finally:
                    yf_release()
            success = True
            break
        except Exception as e:
            if ticker_name.endswith(".NS"):
                bse_sym = ticker_name[:-3] + ".BO"
                logger.info(f"🔄 Multibagger exception for {ticker_name}, retrying with BSE {bse_sym}...")
                try:
                    yf_acquire(context=f"Multibagger Scanner | {symbol}")
                    try:
                        ticker = yf.Ticker(bse_sym)
                        info = ticker.info
                        fast_info = ticker.fast_info
                        fin = ticker.financials
                        bs = ticker.balance_sheet
                        cf = ticker.cashflow
                        ticker_name = bse_sym
                        try:
                            from bse_mapping_utils import save_bse_mapping
                            save_bse_mapping(symbol, bse_sym)
                        except Exception:
                            pass
                        success = True
                        break
                    finally:
                        yf_release()
                except Exception:
                    pass
            
            msg = str(e).lower()
            if any(term in msg for term in ["too many requests", "429", "503", "502", "504", "crumb", "unauthorized", "connection termination", "upstream connect", "reset reason", "service unavailable"]):
                record_rate_limit(context=f"Multibagger Scanner | {symbol}")
                
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
            else:
                logger.warning(f"Error for {symbol}: {e}")

    # Fallback salvage function definition
    def try_salvage():
        try:
            fast = ticker.fast_info
            fallback_mc = fast.get("marketCap")
            fallback_price = fast.get("lastPrice")
            if fallback_mc and fallback_price:
                logger.info(f"🔄 Salvaging basic data for {symbol} via fast_info fallback.")
                return {
                    "symbol": symbol,
                    "sector": "Unknown",
                    "market_cap": fallback_mc,
                    "shares_outstanding": fallback_mc / fallback_price,
                    "price": fallback_price,
                    "data_freshness": "FALLBACK",
                    "is_financial": False
                }
        except Exception:
            pass
        return None

    if not success or fin is None or fin.empty:
        return try_salvage()
        
    market_cap = info.get("marketCap")
    if market_cap is None and fast_info is not None:
        market_cap = fast_info.get("marketCap")
        
    if not market_cap:
        return try_salvage()
        
    pat = safe_extract(fin, 'Net Income')
    cfo = safe_extract(cf, 'Operating Cash Flow') or info.get('operatingCashflow')
    revenue = safe_extract(fin, 'Total Revenue')
    assets = safe_extract(bs, 'Total Assets')
    ebit = safe_extract(fin, 'EBIT')
    current_liab = safe_extract(bs, 'Current Liabilities')
    working_capital = safe_extract(bs, 'Working Capital')
    retained_earnings = safe_extract(bs, 'Retained Earnings')
    total_liab = safe_extract(bs, 'Total Liabilities Net Minority Interest') or safe_extract(bs, 'Total Liabilities')
    
    cfo_pat = cfo / pat if pat and cfo and pat > 0 else None
    ato = revenue / assets if revenue and assets and assets > 0 else None
    roic = ebit / (assets - current_liab) if ebit and assets and current_liab and (assets - current_liab) > 0 else None
    
    altman_z = None
    market_cap = info.get('marketCap')
    if all(v is not None for v in [working_capital, retained_earnings, ebit, market_cap, total_liab, assets]) and assets > 0 and total_liab > 0:
        x1 = working_capital / assets
        x2 = retained_earnings / assets
        x3 = ebit / assets
        x4 = market_cap / total_liab
        
        # [VERSION: MULTIBAGGER_Z_FIX_v1.0] Determine Z''-score for service/non-manufacturing firms vs standard Z-score for manufacturing firms
        is_svc = any(k in str(info.get("sector", "")).lower() for k in ["technology", "communication", "services"]) or \
                 any(k in str(info.get("industry", "")).lower() for k in ["services", "software", "consulting", "internet", "retail", "media"])
                 
        if is_svc:
            altman_z = (6.56 * x1) + (3.26 * x2) + (6.72 * x3) + (1.05 * x4)
        else:
            x5 = revenue / assets if revenue else 0
            altman_z = (1.2 * x1) + (1.4 * x2) + (3.3 * x3) + (0.6 * x4) + (1.0 * x5)
    
    # Map to V5 Engine Expected Keys
    price = info.get("currentPrice")
    if not price:
        price = fast_info.get("lastPrice")
        logger.debug(f"[DATA] {symbol}: Primary currentPrice missing, falling back to fast_info.lastPrice")
    shares = info.get("sharesOutstanding")
    if not shares and market_cap and price is not None and price > 0:
        shares = market_cap / price
    elif not shares:
        shares = 1.0
        
    eps = safe_float(info.get("trailingEps"))
    if not eps and pat is not None:
        eps = pat / shares
        
    bv = safe_float(info.get("bookValue"))
    if not bv and assets and total_liab:
        bv = (assets - total_liab) / shares
        
    fcf = info.get("freeCashflow")
    if fcf is None and cfo is not None:
        capex = abs(safe_extract(cf, 'Capital Expenditure', default=0.0))
        fcf = cfo - capex
    
    total_equity = safe_extract(bs, 'Stockholders Equity') or safe_extract(bs, 'Total Stockholder Equity')
    if not total_equity and assets and total_liab:
        total_equity = assets - total_liab
    if not total_equity and bv and shares:
        total_equity = bv * shares
        
    roe = None
    # [VERSION: MULTIBAGGER_ROE_FIX_v1.0] Added ROE calculation with safeguards
    if pat is not None and not pd.isna(pat) and total_equity is not None and total_equity > 0:
        roe = pat / total_equity
    
    fund = {
        "symbol": symbol,
        "roe": roe,
        "sector": info.get("sector", "Unknown"),
        "market_cap": market_cap,
        "shares_outstanding": shares,
        "eps": eps,
        "book_value_per_share": bv,
        "free_cash_flow": fcf,
        "ebit": ebit,
        "tt_indpe": info.get("trailingPE"), # Proxy for industry PE if missing
        
        "operating_margin_ttm": info.get("operatingMargins"),
        "gross_margin_stability": (info.get("grossMargins") or 0.0) * 0.1, # Proxy
        "roce": roic,
        "cfo_pat_ratio": cfo_pat,
        "fcf_margin": fcf / revenue if revenue and fcf is not None else None,
        
        "revenue_cagr_3y": compute_cagr(fin, 'Total Revenue', 3),
        "pat_cagr_3y": compute_cagr(fin, 'Net Income', 3),  # [FIX #6] Renamed: this is PAT CAGR, not per-share EPS CAGR
        "fcf_cagr_3y": compute_cagr(cf, 'Free Cash Flow', 3),
        "reinvestment_rate": (retained_earnings or 0.0) / assets if assets else 0.0,
        
        "debt_equity": info.get("debtToEquity") / 100.0 if info.get("debtToEquity") is not None else None,
        # [FIX] ICR: do not use abs() on EBIT to preserve negative earnings signal.
        "interest_coverage_ratio": (lambda ie: (ebit / abs(ie)) if (ebit is not None and ie and abs(ie) > 1) else (100.0 if ebit is not None and ebit >= 0 else (-100.0 if ebit is not None else None)))(safe_extract(fin, 'Interest Expense')),
        "debt_yoy_growth": 0.0, # Dummy for now
        "altman_z": altman_z,
        "current_ratio": info.get("currentRatio"),
        
        "price": price,
        "is_financial": is_financial_sector(info.get("sector")),
        "data_freshness": "LIVE",
        "total_equity": total_equity
    }
    
    return fund



def save_watchlist_to_db(results: list):
    """Save watchlist candidates in bulk using psycopg2 execute_values."""
    if not results:
        return
    
    # Map ScreenerResult attributes to list of tuples for execute_values
    data = []
    for r in results:
        # Determine last alert fields (only write when alert is triggered)
        if r.status == "ALERT_TRIGGERED":
            last_price = r.price
            last_at = datetime.now(IST)
        else:
            last_price = None
            last_at = None
            
        data.append((
            r.symbol.upper(), r.buy_zone_low, r.buy_zone_high, r.price,
            r.cqs, r.pas, r.trend_score, r.total_score, r.bucket, r.status, r.notes,
            last_price, last_at
        ))
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Upsert query using execute_values
                execute_values(cur, """
                    INSERT INTO watchlist 
                    (symbol, buy_zone_low, buy_zone_high, latest_price, 
                     growth_score, value_score, trend_score, total_score, bucket, status, notes,
                     last_alert_price, last_alert_at)
                    VALUES %s
                    ON CONFLICT (symbol) DO UPDATE SET
                        buy_zone_low = EXCLUDED.buy_zone_low,
                        buy_zone_high = EXCLUDED.buy_zone_high,
                        latest_price = EXCLUDED.latest_price,
                        growth_score = EXCLUDED.growth_score,
                        value_score = EXCLUDED.value_score,
                        trend_score = EXCLUDED.trend_score,
                        total_score = EXCLUDED.total_score,
                        bucket = EXCLUDED.bucket,
                        status = CASE WHEN watchlist.status = 'REJECTED' THEN 'REJECTED' ELSE EXCLUDED.status END,
                        notes = EXCLUDED.notes,
                        last_alert_price = COALESCE(EXCLUDED.last_alert_price, watchlist.last_alert_price),
                        last_alert_at = COALESCE(EXCLUDED.last_alert_at, watchlist.last_alert_at),
                        last_updated = CURRENT_TIMESTAMP;
                """, data)
            conn.commit()
        logger.info(f"✅ Stored {len(results)} candidates in watchlist (execute_values).")
    except Exception as e:
        logger.exception(f"❌ Failed to bulk write to watchlist")


def format_telegram_message(categorized_stocks: dict) -> list:
    """Format categorized stocks into chunked Telegram messages (HTML)."""
    messages = []
    current_msg = "<b>🚀 DAILY MULTIBAGGER WATCHLIST SUMMARY</b>\n"
    current_msg += f"<i>Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}</i>\n"
    current_msg += "========================================\n\n"
    
    has_results = False
    for label, stocks in categorized_stocks.items():
        if not stocks:
            continue
        has_results = True
        
        section_text = f"<b>{label}</b> ({len(stocks)} stocks):\n"
        current_msg += section_text
        for item in sorted(stocks, key=lambda x: x['total'], reverse=True):
            sym = item['symbol']
            cqs = item['cqs']
            pas = item['pas']
            price = item['price']
            total = item['total']
            status = item['status']
            
            alert_marker = " 🔔 <b>BUY READY</b>" if status == "ALERT_TRIGGERED" else " ⏳ WAITING"
            line = f"• <b>{sym}</b> (₹{price:.1f}) | CQS: {cqs:.1f} | PAS: {pas:.1f} | Total: <b>{total:.1f}/20</b>{alert_marker}\n"
            
            if len(current_msg) + len(line) > 3900:
                messages.append(current_msg)
                current_msg = "<b>🚀 MULTIBAGGER WATCHLIST SUMMARY (Cont.)</b>\n\n"
                
            current_msg += line

        current_msg += "\n"

    if not has_results:
        current_msg += "ℹ️ No stocks qualified for multibagger categorization this week.\n"
        messages.append(current_msg)
    else:
        messages.append(current_msg)
        
    return messages

def run_scanner(debug_limit: int = None, is_test_mode: bool = False):
    """Main execution orchestrator for Multibagger Scanner V5."""
    import time
    start_time = time.time()
    logger.info("=================================================================")
    logger.info("🚀 STARTING ELITE MULTIBAGGER SCANNER V5.0")
    logger.info("=================================================================")
    
    # Clear pledge cache to ensure fresh values are fetched from DB today
    try:
        from pledge_scraper import fetch_promoter_pledge
        fetch_promoter_pledge.cache_clear()
        logger.info("🧹 Cleared fetch_promoter_pledge LRU cache for today's run.")
    except Exception as e:
        logger.warning(f"Failed to clear fetch_promoter_pledge cache: {e}")

    # Ensure tables and functions are created
    init_db()

    from zoneinfo import ZoneInfo
    today_str = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d')
    
    # ── VALIDATE UPSTREAM MANIFEST ──
    try:
        from database import get_latest_build_manifest
        manifest = get_latest_build_manifest(today_str)
        if not manifest or manifest.get("status") not in ("SUCCESS", "FALLBACK_SUCCESS"):
            logger.error(f"🛑 [MULTIBAGGER] Aborting run: No successful upstream build manifest found for {today_str}.")
            upsert_scanner_health("MULTIBAGGER", "DOWN", error_msg=f"Upstream manifest invalid/missing for {today_str}")
            return {}
    except Exception as e:
        logger.warning(f"⚠️ [MULTIBAGGER] Failed to validate upstream manifest: {e}. Proceeding cautiously.")
    
    upsert_scanner_health("MULTIBAGGER", "RUNNING")
    
    # Delegate to the actual scanning logic
    return _start_wrapper(debug_limit, is_test_mode)

def run_exit_monitor(price_data_map: dict, cache: dict, is_test_mode: bool = False):
    """
    Evaluates open MULTIBAGGER positions in the database for exit signals.
    Excludes other buy alerts generated by other scanners.
    """
    logger.info("🔍 Running Exit Monitor for open MULTIBAGGER positions...")
    try:
        from psycopg2.extras import RealDictCursor
        open_positions = []
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Query only open alerts with breakout_type/scanner = 'MULTIBAGGER'
                cur.execute("""
                    SELECT id, symbol, entry_price as alert_price, alert_date
                    FROM alerts 
                    WHERE scanner = 'MULTIBAGGER' AND status = 'OPEN' AND is_rejected = FALSE;
                """)
                open_positions = [dict(row) for row in cur.fetchall()]
                
        if not open_positions:
            logger.info("ℹ️ No open MULTIBAGGER positions found. Skipping exits.")
            return
            
        logger.info(f"🔄 Evaluating exits for {len(open_positions)} open MULTIBAGGER positions...")
        
        # [VERSION: MULTIBAGGER_EXIT_BATCH_v1.0] Pre-fetch price data for all open positions in batch to avoid loop network latency
        open_symbols = [pos["symbol"] for pos in open_positions]
        exit_prices = {}
        if open_symbols:
            try:
                exit_prices = batch_download_market_data(open_symbols)
            except Exception as e:
                logger.warning(f"Failed to batch download exit prices: {e}")

        for pos in open_positions:
            try:
                symbol = pos["symbol"]
                entry_price = float(pos["alert_price"]) if pos.get("alert_price") is not None else 0.0
                alert_id = pos["id"]
                
                # Try exit_prices first, then fall back to price_data_map
                price_data = exit_prices.get(symbol) or price_data_map.get(symbol)
                
                # Check for temporary provider outage vs permanent stale data
                if not price_data:
                    logger.error(f"🚨 [EXIT MONITOR] {symbol}: No price data available in batch. Stock might be suspended/delisted. Triggering REVIEW.")
                    # Trigger a review alert since we have zero price data (could be delisted/purged)
                    try:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    UPDATE alerts 
                                    SET status = 'SELL_REVIEW', 
                                        exit_signal = 'Review Alert: No price data returned by provider. Stock may be delisted or suspended.',
                                        exit_reason = 'Review Alert: No price data returned by provider. Stock may be delisted or suspended.',
                                        updated_at = CURRENT_TIMESTAMP
                                    WHERE id = %s
                                """, (alert_id,))
                            conn.commit()
                        logger.warning(f"🚨 SELL_REVIEW TRIGGERED for {symbol}: No price data returned by provider.")
                    except Exception as e:
                        logger.exception(f"Failed to update alert for {symbol} due to missing price data.")
                    continue
                    
                current_price = price_data.price

                
                # Fetch latest fundamentals (using cache first)
                fund = get_cached_fundamentals(symbol, cache)
                if not fund:
                    fund = fetch_ticker_fundamentals(symbol)
                    
                if fund:
                    technicals = {
                        "price": current_price,
                        "sma_50": price_data.sma_50,
                        "sma_200": price_data.sma_200,
                        "atr": price_data.atr_14
                    }
                    decision = run_pipeline_for_symbol(symbol, fund, technicals)
                    cqs = decision.quality.score
                    is_invalid = decision.is_invalidated
                    invalidation_reason = decision.invalidation_reason or ""
                else:
                    # No data available — NEVER exit a position just because Yahoo Finance
                    # returned nothing. Missing data is NOT a sign of deterioration.
                    logger.warning(f"[EXIT MONITOR] {symbol}: no fundamental data available — skipping fundamental exit check.")
                    cqs = 15.0
                    is_invalid = False
                    invalidation_reason = ""
                try:
                    # [FIX #17] Use IST-aware datetime for grace period consistency
                    if pos.get("alert_date"):
                        adate = datetime.strptime(str(pos["alert_date"])[:10], "%Y-%m-%d").date()
                        days_held = (datetime.now(IST).date() - adate).days
                    else:
                        days_held = 0
                except Exception as e:
                    logger.exception(f"Failed to parse alert_date for {symbol}: {e}")
                    days_held = 0

                exit_triggered = False
                exit_reason = ""
                
                # Watchdog: Stale Data Check (Suspension / Trading Halt)
                if hasattr(price_data, "last_trade_date") and price_data.last_trade_date:
                    import numpy as np
                    try:
                        start_date = np.datetime64(price_data.last_trade_date)
                        end_date = np.datetime64(datetime.now(IST).date())
                        bus_days = np.busday_count(start_date, end_date)
                        if bus_days >= 10:
                            exit_triggered = True
                            exit_reason = f"SELL_REVIEW: Stale Price Data. Last trade was {price_data.last_trade_date} ({bus_days} trading sessions ago). Stock may be suspended or delisted."
                    except Exception as e:
                        logger.warning(f"Stale data check failed for {symbol}: {e}")
                
                # Rule 1: Dynamic Catastrophic Stop (Market Cap & Trend Health)
                if not exit_triggered and entry_price <= 0:
                    logger.warning(f"⚠️ [EXIT MONITOR] {symbol}: Invalid entry_price ({entry_price}). Skipping drawdown check.")
                elif not exit_triggered:
                    drawdown_pct = ((entry_price - current_price) / entry_price) * 100.0
                    
                    # Base threshold by market cap
                    mcap_cr = fund.get("market_cap", 0) / 10000000.0 if fund else 0
                    if mcap_cr > 20000:
                        max_loss_pct = 20.0  # Large Cap
                        cap_tier = "Large Cap"
                    elif mcap_cr > 5000:
                        max_loss_pct = 25.0  # Mid Cap
                        cap_tier = "Mid Cap"
                    else:
                        max_loss_pct = 30.0  # Small/Micro Cap
                        cap_tier = "Small Cap"
                        
                    # Adjust for trend health (if price is deep below 200-DMA, tighten the stop)
                    if price_data.sma_200 > 0 and current_price < 0.90 * price_data.sma_200:
                        max_loss_pct -= 2.0  # Tighten stop by 2% if deeply bearish trend
                        trend_health = "Weak Trend"
                    else:
                        trend_health = "Strong/Neutral Trend"
                        
                    if drawdown_pct >= max_loss_pct:
                        exit_triggered = True
                        exit_reason = f"Catastrophic Stop [{cap_tier}, {trend_health}]: Drawdown >= {max_loss_pct:.1f}% ({drawdown_pct:.1f}% loss)"
                    
                # Rule 2: Anti-Whipsaw 200-DMA exit
                if not exit_triggered and price_data.sma_200 > 0:
                    closes_below_count = getattr(price_data, "closes_below_sma200_count", 0)
                    if closes_below_count >= 3:
                        if current_price < 0.93 * price_data.sma_200:
                            exit_triggered = True
                            exit_reason = f"Sustained 200-DMA breakdown: 3+ closes below, and >7% deep (Price: ₹{current_price:.1f}, 200-DMA: ₹{price_data.sma_200:.1f})"
                        
                # Rule 3: Fundamental Deterioration
                is_fallback = fund.get("data_freshness") == "FALLBACK" if fund else False
                
                if not exit_triggered and fund and not is_fallback:
                    ok, gate_reason = passes_multibagger_quality_gate(fund)
                    if not ok:
                        exit_triggered = True
                        exit_reason = f"Quality kill-gate breach: {gate_reason}"
                    elif cqs < 55.0:
                        exit_triggered = True
                        exit_reason = f"Deteriorating Fundamentals: Quality score decayed below hold-threshold 55 (CQS: {cqs:.1f})"
                elif is_invalid and not fund:
                    logger.warning(f"[EXIT MONITOR] {symbol} failed gates due to INCOMPLETE DATA — NOT exiting. Will retry next scan.")
                        
                # Handle triggered exit
                if exit_triggered:
                    logger.warning(f"🚨 SELL TRIGGERED for {symbol}: {exit_reason}")
                    calc_ret = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
                    
                    if is_test_mode:
                        logger.info(f"🧪 [TEST MODE] Would have closed {symbol} due to {exit_reason}")
                        close_success = False
                    else:
                        try:
                            update_alert_outcome(
                                alert_id=alert_id,
                                status="CLOSED",
                                exit_price=current_price,
                                pnl_pct=calc_ret,
                                pnl_rs=0.0,  # We don't track position size natively in alerts table without wealth engine
                                exit_signal=exit_reason
                            )
                            close_success = True
                            logger.info(f"💰 MULTIBAGGER CLOSED: {symbol} at {current_price} (P&L: {calc_ret:.2f}%)")
                        except Exception as e:
                            logger.error(f"❌ Failed to close MULTIBAGGER alert for {symbol}: {e}")
                            close_success = False
                            
                    if close_success:
                        # Queue Telegram notification
                        sell_msg = (
                            f"🚨 <b>MULTIBAGGER SELL ALERT | {symbol}</b>\n"
                            f"----------------------------------------\n"
                            f"• Entry: ₹{entry_price:.1f}\n"
                            f"• Exit: ₹{current_price:.1f}\n"
                            f"• Return: {calc_ret:.1f}%\n"
                            f"• Reason: <i>{exit_reason}</i>\n"
                        )
                        queue_telegram_message(sell_msg, symbol=symbol)
            except Exception as e:
                logger.error(f"❌ Unhandled exception in exit monitor for {pos.get('symbol', 'UNKNOWN')}: {e}", exc_info=True)
                    
    except Exception as e:
        logger.exception(f"❌ Failed to complete exit monitoring")

def run_standalone_exit_monitor(is_test_mode: bool = False):
    """Entry point for the 5-minute scheduler to check exits only."""
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        
        # 1. Fetch only ACTIVE MULTIBAGGER positions from alerts table
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, symbol, entry_price as alert_price, alert_date
                    FROM alerts 
                    WHERE scanner = 'MULTIBAGGER' AND status = 'OPEN' AND is_rejected = FALSE;
                """)
                open_positions = cur.fetchall()
                
        if not open_positions:
            return
            
        # 2. Fetch latest prices for just these symbols
        symbols = [p['symbol'] for p in open_positions]
        if not symbols:
            return
            
        price_data_map_raw = batch_download_market_data(symbols)
        
        price_data_map = {}
        for sym, stock_data in price_data_map_raw.items():
            if stock_data:
                # [FIX #2] Include closes_below_sma200_count in ExitPriceData construction
                price_data_map[sym] = ExitPriceData(
                    symbol=sym,
                    price=stock_data.price,
                    sma_50=stock_data.sma_50,
                    sma_200=stock_data.sma_200,
                    high_20d=stock_data.high_20d,
                    close_yesterday=stock_data.close_yesterday,
                    sma_200_yesterday=stock_data.sma_200_yesterday,
                    atr_14=stock_data.atr_14,
                    ema_20=stock_data.ema_20,
                    closes_below_sma200_count=stock_data.closes_below_sma200_count
                )
                
        # 3. Use cache for fundamentals
        from multibagger import load_cache as load_mb_cache
        cache = load_mb_cache()
        
        # 4. Run the core exit logic
        run_exit_monitor(price_data_map, cache, is_test_mode)
        
    except Exception as e:
        logger.exception(f"Failed to run standalone exit monitor")
        raise e

from lock_utils import ProcessLock
_scan_lock = ProcessLock("multibagger")

def start(debug_limit: int = None, is_test_mode: bool = False):
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("Scanner is already actively running!")
    try:
        return run_scanner(debug_limit, is_test_mode)
    finally:
        _scan_lock.release()

def _start_wrapper(debug_limit: int = None, is_test_mode: bool = False):
    """Main scanning wrapper."""
    logger.info("🚀 Multibagger Scanner execution started...")
    init_db()
    
    # Load fundamentals cache
    cache = load_cache()
    
    # 1. Fetch constituents
    from constituent_service import fetch_constituents
    symbols = fetch_constituents()
    if not symbols:
        logger.error("❌ Failed to fetch any constituent stocks. Aborting scan.")
        raise RuntimeError("Failed to fetch NSE constituent stocks. NSE API might be blocking the IP or rate-limiting.")

    # [VERSION: SCANNER_DIAG_LOG_v1.0] Watchlist fingerprint for cross-run comparison
    import hashlib
    _wl_stocks = sorted(symbols)
    _wl_hash = hashlib.md5("|".join(_wl_stocks).encode()).hexdigest()[:12]
    logger.info(f"📋 [MULTIBAGGER] Watchlist fingerprint: {len(symbols)} stocks | hash={_wl_hash}")
        
    if debug_limit:
        logger.info(f"🧪 [DEBUG MODE] Limiting scan universe to {debug_limit} symbols.")
        symbols = symbols[:debug_limit]
        
    # 2. Phase 1: Batch Download Price & Volume Metrics (using auto_adjust=False)
    price_data_map = batch_download_market_data(symbols)
    if not price_data_map:
        logger.error("❌ Failed to download batch price data. Aborting scan.")
        raise RuntimeError("Failed to download batch price data from YFinance/Fyers. Market data provider down.")
        
    # Apply cheap filters to build shortlist:
    # Exclude penny stocks (< ₹10) and illiquid stocks (turnover_20d < ₹10 Lakhs)
    shortlist_candidates = []
    
    # Always include currently open positions in the shortlist so their fundamentals are fetched concurrently
    open_symbols = set()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM alerts WHERE scanner = 'MULTIBAGGER' AND status = 'OPEN'")
                open_symbols = {row[0] for row in cur.fetchall()}
    except Exception as e:
        logger.error(f"Failed to fetch open positions for shortlist injection: {e}")
        
    for sym, price_data in price_data_map.items():
        if sym in open_symbols:
            shortlist_candidates.append(price_data)
            continue
            
        if price_data.price < 10.0:
            continue
        if price_data.turnover_20d < 1000000.0: # ₹10 Lakhs
            continue
        shortlist_candidates.append(price_data)
        
    # Sort by turnover descending (no arbitrary cap — all liquid stocks get evaluated)
    shortlist = sorted(shortlist_candidates, key=lambda x: x.turnover_20d, reverse=True)
    logger.info(f"📋 Shortlisted {len(shortlist)}/{len(price_data_map)} liquid stocks for fundamental screening.")
    
    # 3. Phase 2: Fetch Fundamentals
    fundamentals_list = []
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        cached_count = 0
        for p in shortlist:
            sym = p.symbol
            cached = get_cached_fundamentals(sym, cache)
            if cached:
                cached_count += 1
                fundamentals_list.append(cached)
            else:
                futures[executor.submit(fetch_ticker_fundamentals, sym)] = sym
                
        if cached_count > 0:
            logger.info(f"💾 Loaded fundamentals for {cached_count}/{len(shortlist)} stocks directly from DB cache.")
            
        fetch_total = len(futures)
        if fetch_total > 0:
            logger.info(f"📥 Fetching fresh fundamentals for the remaining {fetch_total} stocks via Yahoo Finance...")
        else:
            logger.info("✅ All fundamentals were loaded from cache. No fetching required!")
                
        fetched_count = 0
        try:
            import concurrent.futures
            for future in as_completed(futures, timeout=1800):
                sym = futures[future]
                try:
                    fund = future.result()
                    if fund:
                        fundamentals_list.append(fund)
                        # Update local cache memory
                        cache[sym] = fund
                        cache[sym]["fetched_at"] = datetime.now(IST).isoformat()
                        fetched_count += 1
                        
                        if fetched_count % 10 == 0 or fetched_count == fetch_total:
                            logger.info(f"⏳ Progress: Fetched {fetched_count}/{fetch_total} fresh fundamentals...")
                        
                        # Save in chunks to prevent data loss if restarted
                        if fetched_count % 50 == 0:
                            logger.info(f"💾 Intermediary chunk save: saving {fetched_count} newly fetched fundamentals to DB...")
                            save_fundamentals_cache(cache)
                    else:
                        logger.warning(f"⚠️ Failed to fetch fundamentals for {sym} (No data returned)")
                except Exception as e:
                    logger.error(f"❌ Error fetching fundamentals for {sym}: {e}")
        except concurrent.futures.TimeoutError:
            logger.error("❌ Timeout fetching fundamentals in multibagger. Aborting remaining fetches to prevent deadlock.")
                
        if fetched_count > 0:
            logger.info(f"💾 Final save: saving remaining newly fetched fundamentals to DB...")
            save_fundamentals_cache(cache)
        
        # Now that cache is fully populated concurrently, run exit monitor on open positions
        run_exit_monitor(price_data_map, cache, is_test_mode)
                
    # Save updated cache to JSON file
    save_fundamentals_cache(cache)
    
    # Enforce minimum 70% data integrity before proceeding
    total_expected = len(shortlist)
    total_fetched = len(fundamentals_list)
    
    if total_expected > 0:
        fetch_ratio = total_fetched / total_expected
        logger.info(f"📊 Data Integrity: {total_fetched}/{total_expected} ({fetch_ratio:.1%}) fundamentals loaded.")
        if fetch_ratio < 0.70:
            error_msg = f"Incomplete data error: Only {total_fetched}/{total_expected} ({fetch_ratio:.1%}) stocks fetched."
            logger.warning(f"⚠️ {error_msg}")
            # [VERSION: MB_FETCH_ABORT_FIX] Gracefully degrade instead of aborting the script
            if not is_test_mode:
                try:
                    upsert_scanner_health(scanner_name="MULTIBAGGER", status="DEGRADED", error_msg=error_msg)
                    from push_service import send_push_to_all
                    send_push_to_all("⚠️ MULTIBAGGER Scanner DEGRADED", error_msg, bypass_throttle=True)
                except Exception:
                    pass
            # Allow the valid subset to continue rather than raising an Exception
    
    # Check Market Regime (Explicitly fetch Nifty)
    # Default to BEAR (conservative fail-direction for quality-over-quantity)
    market_regime = "BEAR"
    try:
        from data_provider import get_fetcher
        nifty_md = get_fetcher().get_ohlcv("^NSEI", period="1y", interval="1d")
        if nifty_md:
            import pandas as pd
            nifty_df = nifty_md.dataframe if hasattr(nifty_md, "dataframe") else nifty_md
        else:
            import pandas as pd
            nifty_df = pd.DataFrame()
            
        if not nifty_df.empty and len(nifty_df) >= 200:
            close_col = nifty_df["Close"]
            if isinstance(close_col, pd.DataFrame):
                close_col = close_col.iloc[:, 0]
            nifty_close = float(close_col.iloc[-1])
            nifty_sma200 = float(close_col.rolling(200).mean().iloc[-1])
            if nifty_close > nifty_sma200:
                market_regime = "BULL"
            else:
                market_regime = "BEAR"
        else:
            logger.warning("Nifty data insufficient (<200 days). Defaulting to BEAR (conservative).")

    except Exception as e:
        logger.warning(f"Could not determine market regime, defaulting to BEAR (conservative): {e}")
        
    logger.info(f"📊 Detected Market Regime: {market_regime}")
    
    # 4. Phase 3: Peer-aware scoring & buy zone assessment
    from valuation_utils import compute_peer_medians
    symbols_to_val = [f.get("symbol") for f in fundamentals_list]
    peer_medians = compute_peer_medians(symbols_to_val)
            
    results = []
    alert_candidates = []
    categorized_stocks = {}
    

    
    # Init Rejection Log count
    unverified_pledge_count = 0
    
    for f in fundamentals_list:
        sym = f.get("symbol")
        price_data = price_data_map.get(sym)
        if not price_data:
            continue
            
        # 1. Pass the raw dictionary directly to the V5 Pipeline
        raw_fundamentals = f.copy()
        
        # Inject computed technical data for V5 Market Structure Engine (Momentum)
        if price_data.high_52w > 0:
            raw_fundamentals["pct_from_52w_high"] = (price_data.price - price_data.high_52w) / price_data.high_52w
        else:
            raw_fundamentals["pct_from_52w_high"] = 0.0
            
        if getattr(price_data, 'volume_sma20', 0) > 0:
            raw_fundamentals["relative_volume_10d"] = price_data.latest_volume / price_data.volume_sma20
        else:
            raw_fundamentals["relative_volume_10d"] = 1.0
            
        # Calculate proxy RS Rating from 6-month momentum
        mom = getattr(price_data, 'mom_6m', 0.0)
        if mom > 0.40: rs = 95.0
        elif mom > 0.20: rs = 85.0
        elif mom > 0.10: rs = 75.0
        elif mom > 0.05: rs = 65.0
        elif mom > 0.0: rs = 55.0
        elif mom > -0.10: rs = 45.0
        elif mom > -0.20: rs = 35.0
        else: rs = 25.0
        raw_fundamentals["rs_rating"] = rs
        
        # [FIX] Issue #2: Use actual forensic_flags instead of hardcoded False
        # forensic_flags >= 2 means auditor/accounting red flags detected
        forensic_count = raw_fundamentals.get("forensic_flags", 0)
        raw_fundamentals["auditor_flags"] = (forensic_count >= 2)
        
        # [VERSION: PLEDGE_EXTRACT_FIX_v1.0] Populate promoter_pledge_pct from pledge cache DB
        # Set to None/null if missing or unverified instead of defaulting to 0.0
        if "promoter_pledge_pct" not in raw_fundamentals or raw_fundamentals.get("promoter_pledge_pct") in (None, 0.0):
            try:
                from pledge_scraper import fetch_promoter_pledge
                pledge_val = fetch_promoter_pledge(sym)
                if pledge_val is not None:
                    # Gate engine expects a ratio (0.0-1.0), not a percentage
                    raw_fundamentals["promoter_pledge_pct"] = pledge_val / 100.0
                else:
                    unverified_pledge_count += 1
                    raw_fundamentals["promoter_pledge_pct"] = None
                    logger.debug(f"⚠️ {sym}: Pledge data unavailable — setting to None")
            except Exception:
                unverified_pledge_count += 1
                raw_fundamentals["promoter_pledge_pct"] = None
        
        technicals = {
            "price": price_data.price,
            "sma_50": price_data.sma_50,
            "sma_200": price_data.sma_200,
            "ema_20": price_data.ema_20,
            "atr": price_data.atr_14,
        }
        
        # 2. Early Ambiguity & Quality Gates
        if price_data.sma_200 <= 0 or price_data.ema_20 <= 0 or price_data.sma_50 <= 0 or price_data.price <= 0:
            logger.info(f"REJECTION: {sym} (Phase: PRE_GATE, Reason: Ambiguous Technicals)")
            continue
            
        if raw_fundamentals.get("data_freshness") == "FALLBACK":
            logger.info(f"REJECTION: {sym} (Phase: PRE_GATE, Reason: Fallback Fundamentals)")
            continue
            
        ok, reason = passes_multibagger_quality_gate(raw_fundamentals)
        if not ok:
            logger.info(f"REJECTION: {sym} (Phase: QUALITY_GATE, Reason: {reason})")
            continue

        # 3. Run the V5 Pipeline
        pipeline_result = run_pipeline_for_symbol(sym, raw_fundamentals, technicals)
        
        # Log rejection if invalidated by V5 gates
        if pipeline_result.is_invalidated:
            logger.info(f"REJECTION: {sym} (Phase: V5_GATE, Reason: {pipeline_result.invalidation_reason})")
            continue
                
        # Extract scores from the V5 pipeline
        cqs = pipeline_result.quality.score
        pas = pipeline_result.valuation.score
        trend = pipeline_result.market_structure.score
        total = pipeline_result.composite_score
        
        # Apply institutional, promoter, and super-investor bonuses
        try:
            from block_deal_detector import compute_inst_bonus
            inst_bonus = float(compute_inst_bonus(sym, int(total)))
        except Exception as e:
            logger.warning(f"Error checking institutional footprints in Multibagger: {e}")
            inst_bonus = 0.0
        total = min(100.0, total + inst_bonus)
        
        buy_low = pipeline_result.buy_zone.buy_zone_low
        buy_high = pipeline_result.buy_zone.buy_zone_high
        
        tier, composite = classify_conviction(cqs, pas, trend, total)
        
        # [VERSION: MULTIBAGGER_BEAR_FIX_v1.2] Removed BEAR regime downgrade entirely.
        # As per recent architectural decisions, the scoring engine naturally penalizes weak setups.
        # We do not forcibly downgrade HIGH_QUALITY alerts to WATCH_ONLY just because the market is BEAR.
        
        if tier not in ["🚀 Prime Multibagger", "💎 High Quality"]:
            status = "WAITING_BUY_ZONE"
            notes = f"Conviction: {tier} | CQS: {cqs:.1f}"
            alert_triggered = False
        else:
            if not pipeline_result.buy_zone.in_buy_zone:
                status = "WAITING_BUY_ZONE"
                notes = f"Conviction: {tier} | Waiting for Pullback"
                alert_triggered = False
            elif not entry_confirmed(price_data):
                status = "WAITING_BUY_ZONE"
                notes = f"Conviction: {tier} | In Zone, Awaiting Technical Stabilization"
                alert_triggered = False
            else:
                status = "ALERT_TRIGGERED"
                reclaim_ema = price_data.price > price_data.ema_20
                if reclaim_ema:
                    notes = f"Conviction: {tier} | 🟢 BUY CONFIRMED (EMA Reclaimed)"
                else:
                    notes = f"Conviction: {tier} | 🟢 BUY CONFIRMED (Deep Value Zone)"
                alert_triggered = True
                
        bucket = tier
        
        # Additional Valuation logging 
        if pipeline_result.valuation.fair_value > 0:
            notes += f" | FV: {pipeline_result.valuation.fair_value:.0f} (MoS: {pipeline_result.valuation.margin_of_safety:.0f}%)"
            
        if alert_triggered:
            skip_alert = False
            if sym in open_symbols:
                logger.info(f"⏭️ Skipping alert generation for {sym} - already an open MULTIBAGGER position.")
                skip_alert = True
                status = "WAITING_BUY_ZONE" # Already held, so don't fire an alert again
                
            if not skip_alert:
                tier_val = 2 if "Prime" in tier else 1
                alert_candidates.append({
                    "symbol": sym,
                    "price": price_data.price,
                    "tier_val": tier_val,
                    "total_score": total,
                    "cqs": cqs,
                    "trend_score": trend,
                    "pas": pas,
                    "notes": notes,
                    "pipeline_result": pipeline_result,
                    "raw_fundamentals": raw_fundamentals
                })

            if status != "INVALIDATED":
                label = bucket
                if skip_alert:
                    label = f"🛡️ {label} (Currently Held)"
                
                if label not in categorized_stocks:
                    categorized_stocks[label] = []
                    
                categorized_stocks[label].append({
                    'symbol': sym,
                    'price': price_data.price,
                    'cqs': cqs,
                    'pas': pas,
                    'total': total,
                    'status': status
                })

        # Assemble the display record
        bz_low = pipeline_result.buy_zone.buy_zone_low if pipeline_result.buy_zone else 0.0
        bz_high = pipeline_result.buy_zone.buy_zone_high if pipeline_result.buy_zone else 0.0
        
        results.append(ScreenerResult(
            symbol=sym,
            price=round(price_data.price, 2),
            cqs=round(cqs, 1),
            pas=round(pas, 1),
            trend_score=round(trend, 1),
            total_score=round(total, 1),
            buy_zone_low=round(bz_low, 2),
            buy_zone_high=round(bz_high, 2),
            bucket=bucket,
            status=status,
            notes=notes,
            change_pct=0.0
        ))
        
    # Process Top-N alerts
    if alert_candidates:
        # Sort by tier, total_score desc, cqs desc
        alert_candidates.sort(key=lambda x: (x.get("tier_val", 0), x["total_score"], x["cqs"]), reverse=True)
        top_n = alert_candidates
        logger.info(f"🏆 All {len(alert_candidates)} valid candidates selected.")
        
        # Batch fetch live prices
        try:
            from live_prices import get_live_prices
            live_prices_dict = get_live_prices([c["symbol"] for c in top_n])
        except Exception as e:
            logger.warning(f"Failed to batch fetch live prices: {e}")
            live_prices_dict = {}
        
        for cand in top_n:

            sym = cand["symbol"]
            price = cand["price"]
            
            # [VERSION: MULTIBAGGER_LIVE_PRICE_FIX_v1.0] Apply batched live price
            live_p = live_prices_dict.get(sym)
            if live_p and live_p > 0:
                price = live_p

            c_total = cand["total_score"]
            c_cqs = cand["cqs"]
            c_trend = cand["trend_score"]
            c_pas = cand["pas"]
            c_notes = cand["notes"]
            pipeline_res = cand["pipeline_result"]
            raw_fund = cand["raw_fundamentals"]
            c_tier = pipeline_res.classification if pipeline_res else "🟡 Watchlist"
            
            logger.info(f"🌟 Alert Triggered for {sym}! Price={price:.1f}. Reason: In Buy Zone")
            
            scaled_score = int(c_total)
            
            # Custom Capital Allocation based on tier
            if c_tier == "🚀 Prime Multibagger":
                alloc = 100000.0
            elif c_tier == "💎 High Quality":
                alloc = 50000.0
            else:
                alloc = 25000.0
                
            pos_shares = int(alloc / price) if price > 0 else 0
            
            inserted = False
            if not is_test_mode:
                context_dict = {
                    "multibagger_meta": {
                        "valuation_score": c_pas,
                        "momentum_score": int(c_trend),
                        "momentum_confidence": "HIGH" if c_cqs >= 75.0 else "MEDIUM",
                        "data_quality": "LIVE",
                        "pipeline_tier": c_tier
                    }
                }
                
                # [VERSION: SCANNER_DIAG_LOG_v1.0] Log full diagnostic for every triggered trade
                _last_bar_date = "unknown"
                try:
                    _price_data = price_data_map.get(sym)
                    if _price_data and hasattr(_price_data, 'timestamp'):
                        _last_bar_date = str(_price_data.timestamp)[:10]
                except Exception:
                    pass
                logger.info(
                    f"✅ [MULTIBAGGER] PASSED ALL FILTERS: {sym} | "
                    f"cqs={c_cqs:.1f} | pas={c_pas:.1f} | total_score={scaled_score:.1f} | "
                    f"entry=₹{price:.2f} | last_bar={_last_bar_date} | category={c_tier}"
                )
                
                # We use save_alert_if_new to insert into the main alerts table!
                from zoneinfo import ZoneInfo
                ist_now = datetime.now(ZoneInfo('Asia/Kolkata'))
                
                inserted, reason, _, _ = save_alert_if_new(
                    symbol=sym,
                    breakout_type="MULTIBAGGER",
                    alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                    scanner="MULTIBAGGER",
                    category=c_tier,
                    entry_price=round(price, 2),
                    stop_loss=0.0, # As requested: No SL for Multibagger
                    target_price=0.0,
                    signals="Value, Momentum, Quality",
                    score=scaled_score,
                    context=context_dict,
                    capital_allocated=alloc,
                    shares_bought=pos_shares
                )
            else:
                logger.info(f"🧪 [TEST MODE] Skipping save_alert_if_new for {sym}")
                inserted = True

            if inserted:
                from core.multibagger_pipeline import V5_CONFIG
                if V5_CONFIG.get("enable_telegram_alerts", True) and not is_test_mode:
                    msg = (
                        f"🚀 <b>MULTIBAGGER ALERT | {sym}</b>\n"
                        f"----------------------------------------\n"
                        f"• Price: ₹{price:.1f}\n"
                        f"• Classification: <b>{pipeline_res.classification}</b>\n"
                        f"• Composite Score: {pipeline_res.composite_score:.1f}/100\n"
                        f"• Confidence: {pipeline_res.confidence:.0f}%\n"
                        f"• Fair Value: ₹{pipeline_res.valuation.fair_value:.1f} (MoS: {pipeline_res.valuation.margin_of_safety:.1f}%)\n"
                        f"• Buy Zone: ₹{pipeline_res.buy_zone.buy_zone_low:.1f} - ₹{pipeline_res.buy_zone.buy_zone_high:.1f}\n"
                        f"• Sector: {raw_fund.get('sector', 'Unknown')}\n"
                        f"\n<i>System V5 Architecture</i>"
                    )
                    queue_telegram_message(msg, symbol=sym)

    # 5. Bulk database persistence
    save_watchlist_to_db(results)
    
    # 6. Format and queue Telegram updates
    logger.info(f"📢 Formatting Telegram messages for {len(results)} watchlist items...")
    telegram_msgs = format_telegram_message(categorized_stocks)
    for msg in telegram_msgs:
        queue_telegram_message(msg)
        
    logger.info("✅ Multibagger Scanner execution finished.")
    alerts_count = sum(1 for r in results if r.status == "ALERT_TRIGGERED")
    duration_sec = round(time.time() - start_time, 1)
    try:
        from database import insert_notification, upsert_scanner_health
        upsert_scanner_health(
            scanner_name="MULTIBAGGER",
            status="OK",
            last_success=datetime.now(IST).isoformat(),
            today_alerts=alerts_count,
            processed_count=len(results),
            total_count=len(fundamentals_list),
            duration_seconds=duration_sec
        )
        insert_notification("info", "✅ Multibagger Scan Completed", f"Generated {alerts_count} alerts from {len(fundamentals_list)} stocks in {duration_sec}s.")
        from push_service import send_push_to_all
        send_push_to_all("🚀 MULTIBAGGER Scanner OK", f"Found {alerts_count} new alerts.", bypass_throttle=True)
    except Exception as e:
        logger.error(f"Could not update health/notification for Multibagger: {e}")
    # ── Memory Cleanup Phase ──────────────────────────────────────────────
    
    # Store counts before deleting variables
    total_count = len(fundamentals_list) if 'fundamentals_list' in locals() else 0
    processed_count = len(results) if 'results' in locals() else 0
    
    try:
        import os, psutil, gc
        process = psutil.Process(os.getpid())
        rss_before = process.memory_info().rss / 1024 / 1024
        
        # Release large data structures
        if 'price_data_map' in locals(): del price_data_map
        if 'shortlist_candidates' in locals(): del shortlist_candidates
        if 'shortlist' in locals(): del shortlist
        if 'fundamentals_list' in locals(): del fundamentals_list
        if 'futures' in locals(): del futures
        
        rss_after_del = process.memory_info().rss / 1024 / 1024
        
        # Reclaim cyclic references
        gc.collect()
        
        rss_after_gc = process.memory_info().rss / 1024 / 1024
        logger.info(f"🧹 [MEMORY] Multibagger Scan | RSS Before: {rss_before:.1f}MB | After Del: {rss_after_del:.1f}MB | After GC: {rss_after_gc:.1f}MB")
    except Exception as e:
        logger.debug(f"Memory cleanup logging failed: {e}")

    return {
        "total_count": total_count,
        "processed_count": processed_count,
        "today_alerts": alerts_count
    }
