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

from database import get_connection, save_multibagger_alert, close_multibagger_position, init_db, upsert_scanner_health
from telegram_engine import queue_telegram_message
from wealth_risk_adjusted_sizing import calculate_risk_adjusted_sizing
from core.multibagger_pipeline import run_pipeline_for_symbol

logger = logging.getLogger("multibagger")
IST = ZoneInfo("Asia/Kolkata")
CACHE_PATH = "data/multibagger_fundamentals_cache.json"

# Target URLs for NSE Archives
CONSTITUENT_URLS = {
    "Nifty 50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "Nifty Next 50": "https://nsearchives.nseindia.com/content/indices/ind_niftynext50list.csv",
    "Nifty Midcap 150": "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap150list.csv",
    "Nifty Smallcap 250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv",
    "Nifty 500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
    "Nifty Microcap 250": "https://nsearchives.nseindia.com/content/indices/ind_niftymicrocap250_list.csv",
}

# Browser-like headers to bypass NSE's strict user-agent checking
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
}

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
    atr_14: float
    ema_20: float
    latest_volume: float
    volume_sma20: float
    close_yesterday: float
    sma_200_yesterday: float
    closes_below_sma200_count: int = 0

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

def get_nse_session() -> requests.Session:
    """Returns a requests.Session configured with connection pool and retries."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=3)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HTTP_HEADERS)
    return session

def safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val):
            return default
        return float(val)
    except:
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

def fetch_constituents() -> list:
    """Download index lists from NSE and return unique, normalized symbol list."""
    symbols = set()
    session = get_nse_session()
    
    for name, url in CONSTITUENT_URLS.items():
        try:
            logger.info(f"📥 Downloading {name} constituents...")
            response = session.get(url, timeout=15)
            if response.status_code == 200:
                df = pd.read_csv(io.StringIO(response.text))
                if "Symbol" in df.columns:
                    for sym in df["Symbol"].dropna().unique():
                        clean_sym = str(sym).strip()
                        if clean_sym:
                            symbols.add(clean_sym)
                    logger.info(f"✅ Loaded {len(df)} constituents for {name}.")
            else:
                logger.warning(f"⚠️ Failed to fetch {name}: HTTP {response.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Error fetching {name}: {e}")
            
    # [VERSION: SYMBOL_FIX_v1.0] Normalize symbols for yfinance querying.
    # NSE constituents use '&' in symbol names (M&M, J&KBANK, GVT&D, M&MFIN).
    # These must NOT be replaced with hyphens — Yahoo Finance requires '&'.
    # Reuse daily_builder's SYMBOL_CORRECTIONS for consistent mapping.
    from daily_builder import SYMBOL_CORRECTIONS
    normalized = []
    for s in symbols:
        if s in SYMBOL_CORRECTIONS:
            clean = SYMBOL_CORRECTIONS[s]
        else:
            clean = s  # NSE CSV symbols are already correct (use & not _)
        if "DUMMY" in clean.upper():
            logger.info(f"🗑️ Skipping NSE placeholder symbol: {clean}")
            continue
        normalized.append(clean)
        
    logger.info(f"🎯 Total unique constituent symbols fetched: {len(normalized)}")
    return sorted(normalized)

def batch_download_market_data(symbols: list) -> dict:
    """Download historical price/volume data in bulk for all tickers using explicit auto_adjust=False."""
    ticker_names = [f"{sym}.NS" for sym in symbols]
    logger.info(f"📥 Batch downloading 1y history for {len(ticker_names)} tickers...")
    
    # [VERSION: MULTIBAGGER_PATCH_v1.0] Determine if we need to strip forming candle for exit monitor
    from datetime import datetime
    from market_utils import is_market_open
    ist_now = datetime.now(IST)
    strip_forming = is_market_open(ist_now)
    
    results = {}
    chunk_size = 150
    for i in range(0, len(ticker_names), chunk_size):
        chunk = ticker_names[i:i + chunk_size]
        logger.info(f"📥 Fetching chunk {i//chunk_size + 1} ({len(chunk)} tickers)...")
        batch_res = {}
        try:
            df = yf.download(chunk, period="1y", interval="1d", auto_adjust=False, group_by="ticker", progress=False, threads=False)
            if not df.empty:
                if df.index.tzinfo is None:
                    logger.debug("[TIMEZONE] Normalizing naive yfinance batch index to IST at fetch boundary")
                    df.index = df.index.tz_localize(IST)
                else:
                    df.index = df.index.tz_convert(IST)
                if len(chunk) == 1:
                    batch_res[chunk[0].replace('.NS', '')] = df
                elif isinstance(df.columns, pd.MultiIndex):
                    for sym in [t.replace('.NS', '') for t in chunk]:
                        ticker_name = f"{sym}.NS"
                        if ticker_name in df.columns.levels[0]:
                            batch_res[sym] = df[ticker_name]
                else:
                    logger.warning(f"Batch downgraded. Re-chunking {len(chunk)} tickers into groups of 10...")
                    sub_chunks = [chunk[j:j+10] for j in range(0, len(chunk), 10)]
                    for sub in sub_chunks:
                        try:
                            sub_df = yf.download(sub, period="1y", interval="1d", auto_adjust=False, group_by="ticker", progress=False, threads=False)
                            if sub_df.empty: continue
                            
                            if sub_df.index.tzinfo is None:
                                logger.debug("[TIMEZONE] Normalizing naive yfinance sub-batch index to IST at fetch boundary")
                                sub_df.index = sub_df.index.tz_localize(IST)
                            else:
                                sub_df.index = sub_df.index.tz_convert(IST)
                            if len(sub) == 1:
                                batch_res[sub[0].replace('.NS', '')] = sub_df
                            elif isinstance(sub_df.columns, pd.MultiIndex):
                                for sym in [t.replace('.NS', '') for t in sub]:
                                    ticker_name = f"{sym}.NS"
                                    if ticker_name in sub_df.columns.levels[0]:
                                        batch_res[sym] = sub_df[ticker_name]
                            else:
                                logger.warning(f"Sub-chunk of {len(sub)} downgraded. Skipping to preserve rate limits.")
                        except Exception as e:
                            logger.warning(f"Sub-chunk fetch failed: {e}")
        except Exception as e:
            logger.warning(f"Chunk fetch failed: {e}")

        for sym, ticker_df in batch_res.items():
            try:
                ticker_df = ticker_df.dropna(subset=["Close"])
                
                # --- PHASE 2 FIX: Preserve real-time price before stripping ---
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
                        
                if len(ticker_df) < 50: # Ensure we have enough data points for SMAs
                    continue
            
                close_series = ticker_df["Close"]
                vol_series = ticker_df["Volume"] if "Volume" in ticker_df.columns else pd.Series([0]*len(ticker_df))
                
                # Instead of overriding price with yesterday's close, use real_time_close
                close_price = real_time_close
                change_pct = real_time_change
                
                close_yesterday = float(close_series.iloc[-2]) if len(close_series) >= 2 else float(close_series.iloc[-1])
                
                # [FIX #13] Use intraday High/Low columns for true 52-week range
                if "High" in ticker_df.columns and "Low" in ticker_df.columns:
                    high_52w = float(ticker_df["High"].max())
                    low_52w = float(ticker_df["Low"].min())
                else:
                    high_52w = float(close_series.max())
                    low_52w = float(close_series.min())
                
                # Compute 20-day average liquidity (Volume * Close)
                recent_20 = ticker_df.tail(20)
                if not recent_20.empty and "Volume" in recent_20.columns:
                    avg_turnover = float((recent_20["Volume"] * recent_20["Close"]).mean())
                else:
                    avg_turnover = 0.0
                
                # Calculate rolling averages & windows using pandas
                sma_20 = float(close_series.rolling(20).mean().iloc[-1])
                sma_50 = float(close_series.rolling(50).mean().iloc[-1])
                
                # Safe 200-day rolling handle (falls back to max available window if data < 200 days)
                window_200 = min(200, len(close_series))
                sma_200_series = close_series.rolling(window_200).mean()
                sma_200 = float(sma_200_series.iloc[-1])
                sma_200_yesterday = float(sma_200_series.iloc[-2]) if len(sma_200_series) >= 2 else sma_200
                
                high_20d = float(close_series.rolling(20).max().iloc[-1])
                high_60d = float(close_series.rolling(60).max().iloc[-1]) if len(close_series) >= 60 else high_20d
                
                # 3-month momentum (60 trading days)
                hist_idx = min(60, len(close_series) - 1)
                close_3m_ago = float(close_series.iloc[-(hist_idx + 1)])
                mom_3m = ((close_price - close_3m_ago) / close_3m_ago) if close_3m_ago > 0 else 0.0
                
                latest_volume = float(vol_series.iloc[-1])
                volume_sma20 = float(vol_series.rolling(20).mean().iloc[-1]) if len(vol_series) >= 20 else latest_volume
                
                # ATR(14) calculation
                if "High" in ticker_df.columns and "Low" in ticker_df.columns:
                    high = ticker_df["High"]
                    low = ticker_df["Low"]
                    shifted_close = close_series.shift(1)
                    tr1 = high - low
                    tr2 = (high - shifted_close).abs()
                    tr3 = (low - shifted_close).abs()
                    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                    atr_14 = float(tr.rolling(14).mean().iloc[-1])
                else:
                    atr_14 = close_price * 0.05
                    
                # EMA(20) calculation
                ema_20 = float(close_series.ewm(span=20, adjust=False).mean().iloc[-1])
                
                # [FIX #4] SMA(200) breakdown persistence — reuse sma_200_series from above
                closes_below_sma200_count = 0
                if len(close_series) >= 5 and len(sma_200_series.dropna()) >= 5:
                    last_5_closes = close_series.iloc[-5:]
                    last_5_smas = sma_200_series.iloc[-5:]
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
                    latest_volume=latest_volume,
                    volume_sma20=volume_sma20,
                    close_yesterday=close_yesterday,
                    sma_200_yesterday=sma_200_yesterday,
                    atr_14=atr_14,
                    ema_20=ema_20,
                    closes_below_sma200_count=closes_below_sma200_count
                )
            except Exception as e:
                logger.debug(f"Error parsing market data for {sym}: {e}")
            
    logger.info(f"✅ Successfully parsed price data for {len(results)}/{len(ticker_names)} tickers.")
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
        
    pledge = safe_float(f.get("promoter_pledge_pct", 0.0))
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
            if safe_float(altman_z) < 1.8:
                return False, f"Altman-Z in distress zone ({safe_float(altman_z):.2f})"

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
        return "PRIME", composite
    elif composite >= 65 and cqs >= 60 and trend >= 10.0:
        return "HIGH_QUALITY", composite
    elif composite >= 50:
        return "WATCH_ONLY", composite
    else:
        return "REJECT", composite

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
            logger.warning(f"[TIMEZONE] Rejecting legacy naive cache timestamp for {symbol} to strictly enforce aware IST contract.")
            return None
            
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
    except: pass
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
    except: pass
    return None

def fetch_ticker_fundamentals(symbol: str) -> Optional[Dict[str, Any]]:
    ticker_name = f"{symbol}.NS"
    ticker = yf.Ticker(ticker_name)
    
    for attempt in range(3):
        try:
            yf_acquire(context=f"Multibagger Scanner | {symbol}")
            try:
                info = ticker.info
                fast_info = ticker.fast_info
                
                market_cap = info.get("marketCap")
                if market_cap is None:
                    market_cap = fast_info.get("marketCap")
                    
                # Always fetch financials independently of info
                fin = ticker.financials
                bs = ticker.balance_sheet
                cf = ticker.cashflow
            finally:
                yf_release()
                
            if market_cap:
                
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
                    
                    "debt_equity": (info.get("debtToEquity") or 0.0) / 100.0,
                    # [FIX #5] ICR: wrap with abs() to handle yfinance sign convention
                    "interest_coverage_ratio": (lambda ie: (abs(ebit) / abs(ie)) if (ebit and ie and abs(ie) > 1) else 100.0)(safe_extract(fin, 'Interest Expense')),
                    "debt_yoy_growth": 0.0, # Dummy for now
                    "altman_z": altman_z,
                    "current_ratio": info.get("currentRatio"),
                    
                    "price": price,
                    "is_financial": is_financial_sector(info.get("sector")),
                    "data_freshness": "LIVE",
                    "total_equity": total_equity
                }
                
                return fund
                
            # If we reach here, YF returned data but lacked market_cap (either obscure stock or silent block)
            # DO NOT call record_rate_limit here as it penalizes the whole system for obscure stocks!
            logger.debug(f"Multibagger Scanner | {symbol} returned empty fundamental data. Trying fallback.")
            break
            
        except CircuitOpenError as ce:
            logger.error(f"YFinance circuit open; aborting fetch for {symbol}: {ce}")
            return None
        except Exception as e:
            msg = str(e).lower()
            if "401" in msg or "crumb" in msg or "unauthorized" in msg:
                import shutil, os
                from config import BASE_DIR
                tz_path = os.path.join(BASE_DIR, "data", "tzcache")
                if os.path.exists(tz_path):
                    shutil.rmtree(tz_path, ignore_errors=True)
                ticker = yf.Ticker(f"{symbol}.NS")
            if "too many requests" in msg or "429" in msg or "crumb" in msg or "unauthorized" in msg:
                record_rate_limit(context=f"Multibagger Scanner | {symbol}")
            else:
                logger.warning(f"Error for {symbol}: {e}")
                
    # Fallback Alternative: If YF completely blocked fundamentals, salvage basic info
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
                    INSERT INTO stockupdates.watchlist 
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
                        status = EXCLUDED.status,
                        notes = EXCLUDED.notes,
                        last_alert_price = COALESCE(EXCLUDED.last_alert_price, stockupdates.watchlist.last_alert_price),
                        last_alert_at = COALESCE(EXCLUDED.last_alert_at, stockupdates.watchlist.last_alert_at),
                        last_updated = CURRENT_TIMESTAMP;
                """, data)
            conn.commit()
        logger.info(f"✅ Stored {len(results)} candidates in stockupdates.watchlist (execute_values).")
    except Exception as e:
        logger.exception(f"❌ Failed to bulk write to stockupdates.watchlist")

def save_scores_to_db(results: list):
    """Save scanned scores in bulk using psycopg2 execute_values."""
    if not results:
        return
    
    data = []
    for r in results:
        legacy_score = int(r.total_score) # total_score is now the 0-100 composite investment score
        data.append((r.symbol.upper(), r.price, r.change_pct, legacy_score, r.cqs, r.pas))
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO stockupdates.prices (symbol, latest_price, change_pct, fundamental_score, quality_score, value_score)
                    VALUES %s
                    ON CONFLICT (symbol) DO UPDATE SET
                        latest_price = EXCLUDED.latest_price,
                        change_pct = EXCLUDED.change_pct,
                        fundamental_score = EXCLUDED.fundamental_score,
                        quality_score = EXCLUDED.quality_score,
                        value_score = EXCLUDED.value_score,
                        last_fetched = CURRENT_TIMESTAMP;
                """, data)
            conn.commit()
        logger.info(f"✅ Stored {len(results)} stock scores in stockupdates.prices (execute_values).")
    except Exception as e:
        logger.exception(f"❌ Failed to bulk write to stockupdates.prices")

def format_telegram_message(categorized_stocks: dict) -> list:
    """Format categorized stocks into chunked Telegram messages (HTML)."""
    messages = []
    current_msg = "<b>🚀 SUNDAY MULTIBAGGER WATCHLIST SUMMARY</b>\n"
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
    logger.info("=================================================================")
    logger.info("🚀 STARTING ELITE MULTIBAGGER SCANNER V5.0")
    logger.info("=================================================================")
    
    # Ensure tables and functions are created
    init_db()

    from datetime import datetime
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
                # Query only open alerts with breakout_type = 'MULTIBAGGER'
                cur.execute("""
                    SELECT id, symbol, alert_price, alert_date
                    FROM multibagger_alerts 
                    WHERE is_closed = FALSE;
                """)
                open_positions = [dict(row) for row in cur.fetchall()]
                
        if not open_positions:
            logger.info("ℹ️ No open MULTIBAGGER positions found. Skipping exits.")
            return
            
        logger.info(f"🔄 Evaluating exits for {len(open_positions)} open MULTIBAGGER positions...")
        
        for pos in open_positions:
            try:
                symbol = pos["symbol"]
                entry_price = float(pos["alert_price"]) if pos.get("alert_price") is not None else 0.0
                alert_id = pos["id"]
                
                price_data = price_data_map.get(symbol)
                if not price_data:
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
                    from datetime import datetime
                    # [FIX #17] Use IST-aware datetime for grace period consistency
                    if pos.get("alert_date"):
                        adate = datetime.strptime(str(pos["alert_date"])[:10], "%Y-%m-%d").date()
                        days_held = (datetime.now(IST).date() - adate).days
                    else:
                        days_held = 999
                except Exception:
                    days_held = 999

                exit_triggered = False
                exit_reason = ""
                
                # Rule 1: Catastrophic Stop (Drawdown >= 25% from entry price)
                if entry_price <= 0:
                    logger.warning(f"⚠️ [EXIT MONITOR] {symbol}: Invalid entry_price ({entry_price}). Skipping drawdown check.")
                else:
                    drawdown_pct = ((entry_price - current_price) / entry_price) * 100.0
                    if drawdown_pct >= 25.0:
                        exit_triggered = True
                        exit_reason = f"Catastrophic Stop: Drawdown >= 25% ({drawdown_pct:.1f}% loss)"
                    
                technical_exit_allowed = (days_held >= 15)
                if not technical_exit_allowed and not exit_triggered:
                    logger.info(f"🛡️ [EXIT MONITOR] {symbol}: In 15-day grace period (held {days_held} days). Technical exits skipped.")
                    
                # Rule 2: Anti-Whipsaw 200-DMA exit
                if not exit_triggered and technical_exit_allowed and price_data.sma_200 > 0:
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
                    if is_test_mode:
                        logger.info(f"🧪 [TEST MODE] Would have closed {symbol} due to {exit_reason}")
                        close_success = False
                    else:
                        close_success = close_multibagger_position(symbol, current_price, exit_signal=exit_reason, force_close=True)
                    if close_success:
                        # Queue Telegram notification
                        calc_ret = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
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
    """Entry point for the 5-minute scheduler to check exits only.
    [FIX #1] Added is_test_mode parameter to avoid NameError.
    """
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        
        # 1. Fetch only ACTIVE open positions from DB
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT symbol, current_price, alert_price as entry_price 
                    FROM multibagger_alerts 
                    WHERE is_closed = FALSE 
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
        from fundamentals_cache import load_cache
        cache = load_cache()
        
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
    from valuation_utils import seed_universe_if_empty
    seed_universe_if_empty()
    
    # Load fundamentals cache
    cache = load_cache()
    
    # 1. Fetch constituents
    symbols = fetch_constituents()
    if not symbols:
        logger.error("❌ Failed to fetch any constituent stocks. Aborting scan.")
        return {}
        
    if debug_limit:
        logger.info(f"🧪 [DEBUG MODE] Limiting scan universe to {debug_limit} symbols.")
        symbols = symbols[:debug_limit]
        
    # 2. Phase 1: Batch Download Price & Volume Metrics (using auto_adjust=False)
    price_data_map = batch_download_market_data(symbols)
    if not price_data_map:
        logger.error("❌ Failed to download batch price data. Aborting scan.")
        return {}
        
    # Apply cheap filters to build shortlist:
    # Exclude penny stocks (< ₹10) and illiquid stocks (turnover_20d < ₹10 Lakhs)
    shortlist_candidates = []
    
    # Always include currently open positions in the shortlist so their fundamentals are fetched concurrently
    open_symbols = set()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT symbol FROM multibagger_alerts WHERE is_closed = FALSE")
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
        for future in as_completed(futures):
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
            error_msg = f"Incomplete data error: Only {total_fetched}/{total_expected} ({fetch_ratio:.1%}) stocks fetched. Minimum 70% required."
            logger.error(f"❌ {error_msg}")
            # [FIX #9] Mark scanner health DOWN before aborting so the terminal state is always set
            upsert_scanner_health("MULTIBAGGER", "DOWN", error_msg=error_msg)
            return {"total_count": total_expected, "processed_count": 0, "today_alerts": 0, "error": error_msg}
    
    # Check Market Regime (Explicitly fetch Nifty)
    # Default to BEAR (conservative fail-direction for quality-over-quantity)
    market_regime = "BEAR"
    try:
        nifty_df = yf.download("^NSEI", period="1y", interval="1d", progress=False)
        if not nifty_df.empty and len(nifty_df) >= 200:
            import pandas as pd
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
    

    
    # Init Rejection Log
    log_date = datetime.now().strftime('%Y-%m-%d')
    rejection_log_path = f"logs/rejections_{log_date}.jsonl"
    os.makedirs("logs", exist_ok=True)
    unverified_pledge_count = 0
    
    for f in fundamentals_list:
        sym = f.get("symbol")
        price_data = price_data_map.get(sym)
        if not price_data:
            continue
            
        # 1. Pass the raw dictionary directly to the V5 Pipeline
        raw_fundamentals = f.copy()
        
        # [FIX] Issue #2: Use actual forensic_flags instead of hardcoded False
        # forensic_flags >= 2 means auditor/accounting red flags detected
        forensic_count = raw_fundamentals.get("forensic_flags", 0)
        raw_fundamentals["auditor_flags"] = (forensic_count >= 2)
        
        # [FIX] Issue #3: Populate promoter_pledge_pct from pledge cache DB
        # so Gate Engine Kill Gate #2 can actually catch high-pledge stocks
        if "promoter_pledge_pct" not in raw_fundamentals or raw_fundamentals.get("promoter_pledge_pct") in (None, 0.0):
            try:
                from pledge_scraper import fetch_promoter_pledge
                pledge_val = fetch_promoter_pledge(sym)
                if pledge_val is not None:
                    # Gate engine expects a ratio (0.0-1.0), not a percentage
                    raw_fundamentals["promoter_pledge_pct"] = pledge_val / 100.0
                else:
                    # [FINDING-A FIX] Default missing pledge to 0.0 (no pledge assumed).
                    # Previously 0.99 which instantly killed the quality gate for ~50% of stocks
                    # when the pledge scraper returned None (network error, NSE format change, etc.)
                    unverified_pledge_count += 1
                    raw_fundamentals["promoter_pledge_pct"] = 0.0
                    logger.debug(f"⚠️ {sym}: Pledge data unavailable — defaulting to 0% (not penalizing)")
            except Exception:
                # [FINDING-A FIX] Same fix for exception branch
                unverified_pledge_count += 1
                raw_fundamentals["promoter_pledge_pct"] = 0.0
        
        technicals = {
            "price": price_data.price,
            "sma_50": price_data.sma_50,
            "sma_200": price_data.sma_200,
            "ema_20": price_data.ema_20,
            "atr": price_data.atr_14,
        }
        
        # 2. Early Ambiguity & Quality Gates
        if price_data.sma_200 <= 0 or price_data.ema_20 <= 0 or price_data.sma_50 <= 0 or price_data.price <= 0:
            with open(rejection_log_path, "a") as rf: rf.write(json.dumps({"symbol": sym, "timestamp": datetime.now().isoformat(), "phase": "PRE_GATE", "reason": "Ambiguous Technicals"}) + "\n")
            continue
            
        if raw_fundamentals.get("data_freshness") == "FALLBACK":
            with open(rejection_log_path, "a") as rf: rf.write(json.dumps({"symbol": sym, "timestamp": datetime.now().isoformat(), "phase": "PRE_GATE", "reason": "Fallback Fundamentals"}) + "\n")
            continue
            
        ok, reason = passes_multibagger_quality_gate(raw_fundamentals)
        if not ok:
            with open(rejection_log_path, "a") as rf: rf.write(json.dumps({"symbol": sym, "timestamp": datetime.now().isoformat(), "phase": "QUALITY_GATE", "reason": reason}) + "\n")
            continue

        # 3. Run the V5 Pipeline
        pipeline_result = run_pipeline_for_symbol(sym, raw_fundamentals, technicals)
        
        # Log rejection if invalidated by V5 gates
        if pipeline_result.is_invalidated:
            with open(rejection_log_path, "a") as rf: rf.write(json.dumps({"symbol": sym, "timestamp": pipeline_result.timestamp, "phase": "V5_GATE", "reason": pipeline_result.invalidation_reason}) + "\n")
            continue
                
        # Extract scores from the V5 pipeline
        cqs = pipeline_result.quality.score
        pas = pipeline_result.valuation.score
        trend = pipeline_result.market_structure.score
        total = pipeline_result.composite_score
        
        buy_low = pipeline_result.buy_zone.buy_zone_low
        buy_high = pipeline_result.buy_zone.buy_zone_high
        
        tier, composite = classify_conviction(cqs, pas, trend, total)
        
        # [VERSION: MULTIBAGGER_BEAR_FIX_v1.2] Removed BEAR regime downgrade entirely.
        # As per recent architectural decisions, the scoring engine naturally penalizes weak setups.
        # We do not forcibly downgrade HIGH_QUALITY alerts to WATCH_ONLY just because the market is BEAR.
        
        if tier not in ["PRIME", "HIGH_QUALITY"]:
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
                tier_val = 2 if tier == "PRIME" else 1
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
        top_n = alert_candidates[:5]
        logger.info(f"🏆 Top 5 Candidates selected out of {len(alert_candidates)} valid alerts.")
        
        for cand in top_n:
            sym = cand["symbol"]
            price = cand["price"]
            c_total = cand["total_score"]
            c_cqs = cand["cqs"]
            c_trend = cand["trend_score"]
            c_pas = cand["pas"]
            c_notes = cand["notes"]
            pipeline_res = cand["pipeline_result"]
            raw_fund = cand["raw_fundamentals"]
            
            logger.info(f"🌟 Alert Triggered for {sym}! Price={price:.1f}. Reason: In Buy Zone")
            
            scaled_score = int(c_total)
            sizing = calculate_risk_adjusted_sizing(price, 3.0, scaled_score)
            pos_shares = int(sizing["Position_Amount"] / price) if price > 0 else 0
            
            inserted = False
            if not is_test_mode:
                inserted = save_multibagger_alert(
                    symbol=sym,
                    alert_price=price,
                    breakout_type="MULTIBAGGER",
                    fm_score=scaled_score,
                    notes=c_notes,
                    position_pct=round(sizing["Position_Pct"] * 100, 2),
                    position_amount=sizing["Position_Amount"],
                    position_shares=pos_shares,
                    portfolio_bucket="MULTIBAGGER",
                    valuation_score=c_pas,
                    momentum_score=int(c_trend),
                    momentum_confidence="HIGH" if c_cqs >= 75.0 else "MEDIUM",
                    data_quality="LIVE",
                    fallback_timestamp=None
                )
            else:
                logger.info(f"🧪 [TEST MODE] Skipping save_wealth_buy_alert for {sym}")
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
    save_scores_to_db(results)
    
    # 6. Format and queue Telegram updates
    logger.info(f"📢 Formatting Telegram messages for {len(results)} watchlist items...")
    telegram_msgs = format_telegram_message(categorized_stocks)
    for msg in telegram_msgs:
        queue_telegram_message(msg)
        
    logger.info("✅ Multibagger Scanner execution finished.")
    alerts_count = sum(1 for r in results if r.status == "ALERT_TRIGGERED")
    try:
        from database import insert_notification
        insert_notification("info", "✅ Multibagger Scan Completed", f"Generated {alerts_count} alerts from {len(fundamentals_list)} stocks.")
    except Exception as e:
        logger.error(f"Could not insert admin notification: {e}")
    return {
        "total_count": len(fundamentals_list),
        "processed_count": len(results),
        "today_alerts": alerts_count
    }
