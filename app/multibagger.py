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
from core_score_engine import CoreFundamentals, PeerMetrics, generate_core_scores, CorePriceData
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from psycopg2.extras import execute_values

from database import get_connection, save_wealth_buy_alert, close_position
from telegram_engine import queue_telegram_message
from wealth_risk_adjusted_sizing import calculate_risk_adjusted_sizing

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
    latest_volume: float
    volume_sma20: float
    close_yesterday: float
    sma_200_yesterday: float

@dataclass
class ExitPriceData:
    symbol: str
    price: float
    sma_50: float
    sma_200: float
    high_20d: float
    close_yesterday: float
    sma_200_yesterday: float

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
        return float(val) if val is not None else default
    except:
        return default

def load_cache() -> dict:
    """Load local fundamentals JSON cache file."""
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
            
    # Normalize symbols for yfinance querying, filtering out NSE dummy/placeholder entries
    normalized = []
    for s in symbols:
        clean = s.replace("_", "-")
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
    
    try:
        df = yf.download(ticker_names, period="1y", interval="1d", auto_adjust=False, group_by="ticker", progress=False, threads=False)
        
        results = {}
        for sym in symbols:
            ticker_name = f"{sym}.NS"
            try:
                if ticker_name not in df.columns.levels[0]:
                    continue
                ticker_df = df[ticker_name].dropna(subset=["Close"])
                if len(ticker_df) < 50: # Ensure we have enough data points for SMAs
                    continue
                
                close_series = ticker_df["Close"]
                vol_series = ticker_df["Volume"] if "Volume" in ticker_df.columns else pd.Series([0]*len(ticker_df))
                
                close_price = float(close_series.iloc[-1])
                close_yesterday = float(close_series.iloc[-2]) if len(close_series) >= 2 else close_price
                
                # 1-day change percent
                if len(close_series) >= 2:
                    prev_close = float(close_series.iloc[-2])
                    change_pct = ((close_price - prev_close) / prev_close) * 100.0
                else:
                    change_pct = 0.0
                
                low_52w = float(close_series.min())
                high_52w = float(close_series.max())
                
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
                    sma_200_yesterday=sma_200_yesterday
                )
            except Exception as e:
                logger.debug(f"Error parsing downloaded data for {sym}: {e}")
                
        logger.info(f"✅ Successfully parsed price data for {len(results)}/{len(symbols)} tickers.")
        return results
    except Exception as e:
        logger.exception(f"❌ Batch price download failed")
        return {}

def is_financial_sector(sector: str) -> bool:
    """Identify if the sector represents a bank, NBFC, or financial services firm."""
    if not sector:
        return False
    sec_lower = str(sector).lower()
    return any(keyword in sec_lower for keyword in ["financ", "bank", "nbfc", "insurance"])

def get_cached_fundamentals(symbol: str, cache: dict) -> CoreFundamentals:
    if symbol not in cache:
        return None
    try:
        data = cache[symbol]
        fetched_at = datetime.fromisoformat(data.get("fetched_at", datetime.now().isoformat()))
        age_days = (datetime.now(IST).replace(tzinfo=None) - fetched_at).days if fetched_at.tzinfo is None else (datetime.now(IST) - fetched_at).days
        if age_days < 7:
            return CoreFundamentals(**{k: v for k, v in data.items() if k != "fetched_at"})
    except Exception as e:
        logger.debug(f"Failed to parse cache entry for {symbol}: {e}")
    return None
    try:
        data = cache[symbol]
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        age_days = (datetime.now(IST).replace(tzinfo=None) - fetched_at).days if fetched_at.tzinfo is None else (datetime.now(IST) - fetched_at).days
        if age_days < 7:
            return CoreFundamentals(
                symbol=symbol,
                market_cap=data.get("market_cap"),
                debt_equity=data.get("debt_equity"),
                operating_cash_flow=data.get("operating_cash_flow"),
                roe=data.get("roe"),
                revenue_growth_1y=data.get("revenue_growth"),
                eps_growth_1y=data.get("earnings_growth"),
                operating_margin=data["operating_margin"],
                pe=data["pe"],
                pb=safe_float(data.get("pb")),
                div_yield=safe_float(data.get("div_yield")),
                sector=data.get("sector", "Unknown"),
                canonical_industry=data.get("canonical_industry", "DEFAULT"),
                eps=safe_float(data.get("eps")),
                bvps=safe_float(data.get("bvps")),
                roa=safe_float(data.get("roa"))
            )
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

def fetch_ticker_fundamentals(symbol: str) -> CoreFundamentals:
    ticker_name = f"{symbol}.NS"
    ticker = yf.Ticker(ticker_name)
    
    for attempt in range(3):
        try:
            yf_acquire(context=f"Multibagger Scanner | {symbol}")
            try:
                info = ticker.info
                if info and "marketCap" in info:
                    fin = ticker.financials
                    bs = ticker.balance_sheet
                    cf = ticker.cashflow
                else:
                    fin = bs = cf = None
            finally:
                yf_release()
                
            if info and "marketCap" in info:
                
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
                
                fund = CoreFundamentals(
                    symbol=symbol,
                    sector=info.get("sector", "Unknown"),
                    canonical_industry=info.get("industry", "DEFAULT"),
                    market_cap=market_cap,
                    pe=info.get("trailingPE"),
                    pb=safe_float(info.get("priceToBook")),
                    ev_ebitda=info.get("enterpriseToEbitda"),
                    roe=info.get("returnOnEquity"),
                    roce=roic, # proxy ROCE with ROIC if missing
                    debt_equity=info.get("debtToEquity"),
                    operating_margin=info.get("operatingMargins"),
                    gross_margin=info.get("grossMargins"),
                    fcf_margin=None, # Needs to be calculated later if needed
                    revenue_growth_1y=info.get("revenueGrowth"),
                    revenue_growth_3y=compute_cagr(fin, 'Total Revenue', 3),
                    revenue_growth_5y=compute_cagr(fin, 'Total Revenue', 5),
                    eps_growth_1y=info.get("earningsGrowth"),
                    eps_growth_3y=compute_cagr(fin, 'Net Income', 3),
                    eps_growth_5y=compute_cagr(fin, 'Net Income', 5),
                    fcf_growth_3y=compute_cagr(cf, 'Free Cash Flow', 3),
                    fcf_growth_5y=compute_cagr(cf, 'Free Cash Flow', 5),
                    cfo_pat_ratio=cfo_pat,
                    operating_cash_flow=cfo,
                    asset_turnover=ato,
                    div_yield=safe_float(info.get("dividendYield")),
                    altman_z=altman_z,
                    promoter_pledge=None, # Not in basic yf info usually
                    eps=safe_float(info.get("trailingEps")),
                    bvps=safe_float(info.get("bookValue")),
                    roa=safe_float(info.get("returnOnAssets")),
                    is_financial=is_financial_sector(info.get("sector")),
                    data_freshness="LIVE"
                )
                
                return fund
            time.sleep(1.0 + (2 ** attempt))
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
                time.sleep(get_backoff_delay(attempt))
            else:
                time.sleep(2 ** attempt)
            
    return None

def passes_kill_gates(f: CoreFundamentals) -> tuple[bool, str]:
    """Instant rejection checks with Golden Exceptions for hyper-growth microcaps and turnarounds. Returns (passed, reason)."""
    
    # Parse base metrics safely
    mcap = float(f.market_cap) if f.market_cap is not None else 0.0
    ocf = float(f.operating_cash_flow) if f.operating_cash_flow is not None else 0.0
    eps = float(f.eps) if f.eps is not None else 0.0
    opm = float(f.operating_margin) if f.operating_margin is not None else 0.0
    roe = float(f.roe) if f.roe is not None else 0.0
    rev_growth = float(f.revenue_growth_1y) if getattr(f, 'revenue_growth_1y', None) is not None else 0.0
    de_val = float(f.debt_equity) if f.debt_equity is not None else 0.0
    de_ratio = de_val / 100.0 if de_val > 10.0 else de_val
    
    is_fin = is_financial_sector(f.sector)
    
    # 1. Size Check (with Hidden Gem Exception)
    if mcap < 5000000000: # ₹500 Cr
        is_hidden_gem = (mcap >= 2000000000) and (roe > 0.15) and (rev_growth > 0.20) and (de_ratio < 0.5)
        if not is_hidden_gem:
            return False, f"Invalidated: Market Cap (₹{mcap/10000000:.1f} Cr) < 500 Cr and fails Hidden Gem exception."
            
    # 2. Debt/Equity check
    if not is_fin and de_ratio > 1.0:
        return False, f"Invalidated: Debt/Equity ({de_ratio:.2f}) > 1.0"
        
    # 3. Operating Cash Flow check (Must be positive)
    if ocf < 0:
        return False, "Invalidated: Negative Operating Cash Flow (Burning cash)."
        
    # 4. Earnings Check (with Turnaround Exception)
    if eps <= 0:
        is_turnaround = (ocf > 0) and (opm > 0) and (rev_growth > 0.25)
        if not is_turnaround:
            return False, "Invalidated: TTM Net Loss (EPS <= 0) and fails Turnaround exception."
            
    # 5. Core Operations check
    if not is_fin and opm < 0:
        return False, "Invalidated: Negative Operating Margin (Core operations losing money)."
            
    # 6. Capital Efficiency check
    if eps > 0 and roe < 0.05:
        return False, f"Invalidated: Abysmal Capital Efficiency (ROE {roe*100:.1f}% < 5%)."
        
    # 7. Deterioration check
    if rev_growth < -0.30:
        return False, f"Invalidated: Severe Revenue Collapse ({rev_growth*100:.1f}%)."
    if getattr(f, 'eps_growth_1y', None) is not None and float(f.eps_growth_1y) < -0.30:
        return False, f"Invalidated: Severe Earnings Collapse ({float(f.eps_growth_1y)*100:.1f}%)."
        
    return True, "Passed Kill Gates"
def should_trigger_alert(price_data: StockPriceData, scores) -> tuple:
    price = price_data.price
    
    # Eligibility
    if scores.business_quality_score < 18.0:
        return False, f"Fails quality guard: BQS ({scores.business_quality_score:.1f}) < 18.0"
        
    if scores.reliability_score < 12.0:
        return False, f"Fails reliability guard: Reliability ({scores.reliability_score:.1f}) < 12.0"
        
    if scores.composite_investment_score < 50.0:
        return False, f"Fails overall fundamental check: CIS ({scores.composite_investment_score:.1f}/100) is too low."
        
    if price < price_data.sma_200:
        return False, "Trend breakdown: Price < 200-DMA."
        
    if price < price_data.sma_50:
        return False, "Waiting for trend confirmation: Price < 50-DMA."
        
    # Buy Zone
    # The true technical buy zone is between the 200 SMA and 50 SMA (or just below 50 SMA).
    # Since we already verified Price > 50-DMA and Price > 200-DMA in the above guards,
    # wait... the above guards REJECT if price < 50-DMA!
    # If we want a technical breakout system, we buy when it crosses UP the 50-DMA.
    # Let's say if price > 50-DMA and price > 200-DMA, it's valid to buy.
    
    # We can just say any stock that passes the fundamental guards and is above 50-DMA is valid.
    # However, to avoid buying too extended, we can cap it at 10% above the 50-DMA.
    buy_zone_high = price_data.sma_50 * 1.10 if price_data.sma_50 > 0 else (price_data.sma_200 * 1.20)
    
    if price <= buy_zone_high:
        return True, f"Value Breakout: inside technical buy zone, BQS={scores.business_quality_score:.1f}, Reliability={scores.reliability_score:.1f}"
        
    # High Growth Rerating (Extended buy zone for Prime Multibaggers)
    if scores.business_quality_score >= 24.0 and scores.market_structure_score >= 10.0:
        extended_buy_zone = price_data.sma_50 * 1.25 if price_data.sma_50 > 0 else (price_data.sma_200 * 1.40)
        if price <= extended_buy_zone:
            return True, f"GARP Rerating: strong growth & trend, allowing extended breakout entry."
            
    return False, f"Price (₹{price:.1f}) > Buy Zone High (₹{buy_zone_high:.1f})"
        
def get_label(cqs: float, pas: float) -> str:
    """Return the output label category based on CQS and PAS."""
    if cqs >= 21.0 and pas >= 21.0:
        return "🚀 PRIME MULTIBAGGER CANDIDATE"
    elif cqs >= 21.0 and 12.0 <= pas <= 18.0:
        return "💎 HIGH QUALITY — FAIR ENTRY"
    elif cqs >= 21.0 and pas < 12.0:
        return "🏆 GREAT BUSINESS — WAIT FOR DIP"
    elif 15.0 <= cqs <= 18.0 and pas >= 18.0:
        return "💰 VALUE BUY — DECENT QUALITY"
    elif 15.0 <= cqs <= 18.0 and 12.0 <= pas <= 15.0:
        return "🟡 WATCHLIST CANDIDATE"
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

def run_exit_monitor(price_data_map: dict, cache: dict):
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
                    SELECT id, symbol, alert_price 
                    FROM wealth_buy_alert 
                    WHERE is_closed = FALSE AND breakout_type = 'MULTIBAGGER';
                """)
                open_positions = [dict(row) for row in cur.fetchall()]
                
        if not open_positions:
            logger.info("ℹ️ No open MULTIBAGGER positions found. Skipping exits.")
            return
            
        logger.info(f"🔄 Evaluating exits for {len(open_positions)} open MULTIBAGGER positions...")
        
        for pos in open_positions:
            symbol = pos["symbol"]
            entry_price = float(pos["alert_price"])
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
                from core_score_engine import score_business_quality, CoreFundamentals, PeerMetrics, CorePriceData
                cf = fund
                cqs = score_business_quality(cf, PeerMetrics(), CorePriceData(price=current_price)).business_quality_score
            else:
                cqs = 15.0 # fallback if no fundamentals
            exit_triggered = False
            exit_reason = ""
            
            # Rule 1: Catastrophic Stop (Drawdown > 20% from entry price)
            drawdown_pct = ((entry_price - current_price) / entry_price) * 100.0
            if drawdown_pct >= 20.0:
                exit_triggered = True
                exit_reason = f"Catastrophic Stop: Drawdown >20% ({drawdown_pct:.1f}% loss)"
                
            # Rule 2: Anti-Whipsaw 200-DMA exit
            # Checks:
            # - Price falls below 97% of 200-DMA today OR
            # - Price closes below 200-DMA for two consecutive days (today & yesterday)
            if not exit_triggered and price_data.sma_200 > 0:
                below_97 = (current_price < 0.97 * price_data.sma_200)
                consecutive_below = (current_price < price_data.sma_200 and price_data.close_yesterday < price_data.sma_200_yesterday)
                
                if below_97:
                    exit_triggered = True
                    exit_reason = f"Decisive breakdown: price closed below 97% of 200-DMA (Price: ₹{current_price:.1f}, 200-DMA: ₹{price_data.sma_200:.1f})"
                elif consecutive_below:
                    exit_triggered = True
                    exit_reason = f"SMA Breakdown: closed below 200-DMA for two consecutive days (Today: ₹{current_price:.1f}, Yesterday: ₹{price_data.close_yesterday:.1f})"
                    
            # Rule 3: Fundamental Deterioration (BQS < 15.0 or fails Kill Gates)
            if not exit_triggered and fund:
                if cqs < 15.0:
                    exit_triggered = True
                    exit_reason = f"Deteriorating Fundamentals: Quality score dropped below 15.0 (BQS: {cqs:.1f})"
                elif not passes_kill_gates(fund):
                    exit_triggered = True
                    exit_reason = "Fundamental failure: fails Layer 1 Kill Gates"
                    
            # Handle triggered exit
            if exit_triggered:
                logger.warning(f"🚨 SELL TRIGGERED for {symbol}: {exit_reason}")
                close_success = close_position(symbol, current_price, exit_reason, force_close=True)
                if close_success:
                    # Queue Telegram notification
                    sell_msg = (
                        f"🚨 <b>MULTIBAGGER SELL ALERT | {symbol}</b>\n"
                        f"----------------------------------------\n"
                        f"• Entry: ₹{entry_price:.1f}\n"
                        f"• Exit: ₹{current_price:.1f}\n"
                        f"• Return: {((current_price - entry_price) / entry_price * 100.0):.1f}%\n"
                        f"• Reason: <i>{exit_reason}</i>\n"
                    )
                    queue_telegram_message(sell_msg, symbol=symbol)
                    
    except Exception as e:
        logger.exception(f"❌ Failed to complete exit monitoring")

def run_standalone_exit_monitor():
    """Entry point for the 5-minute scheduler to check exits only"""
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        
        # 1. Fetch only ACTIVE open positions from DB
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT symbol, current_price, alert_price as entry_price 
                    FROM wealth_buy_alert 
                    WHERE is_closed = FALSE 
                    AND breakout_type = 'MULTIBAGGER'
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
                price_data_map[sym] = ExitPriceData(
                    symbol=sym,
                    price=stock_data.price,
                    sma_50=stock_data.sma_50,
                    sma_200=stock_data.sma_200,
                    high_20d=stock_data.high_20d,
                    close_yesterday=stock_data.close_yesterday,
                    sma_200_yesterday=stock_data.sma_200_yesterday
                )
                
        # 3. Use cache for fundamentals
        from fundamentals_cache import load_cache
        cache = load_cache()
        
        # 4. Run the core exit logic
        run_exit_monitor(price_data_map, cache)
        
    except Exception as e:
        logger.exception(f"Failed to run standalone exit monitor")

from lock_utils import ProcessLock
_scan_lock = ProcessLock("multibagger")

def start(debug_limit: int = None):
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("Scanner is already actively running!")
    try:
        return _start_wrapper(debug_limit)
    finally:
        _scan_lock.release()

def _start_wrapper(debug_limit: int = None):
    """Main scanning wrapper."""
    logger.info("🚀 Multibagger Scanner execution started...")
    from database import init_db
    init_db()
    from valuation_utils import seed_universe_if_empty
    seed_universe_if_empty()
    
    # Load fundamentals cache
    cache = load_cache()
    
    # 1. Fetch constituents
    symbols = fetch_constituents()
    if not symbols:
        logger.error("❌ Failed to fetch any constituent stocks. Aborting scan.")
        return
        
    if debug_limit:
        logger.info(f"🧪 [DEBUG MODE] Limiting scan universe to {debug_limit} symbols.")
        symbols = symbols[:debug_limit]
        
    # 2. Phase 1: Batch Download Price & Volume Metrics (using auto_adjust=False)
    price_data_map = batch_download_market_data(symbols)
    if not price_data_map:
        logger.error("❌ Failed to download batch price data. Aborting scan.")
        return
        
    # Run exit monitor first on open positions using downloaded price metrics
    run_exit_monitor(price_data_map, cache)
    
    # Apply cheap filters to build shortlist:
    # Exclude penny stocks (< ₹10) and illiquid stocks (turnover_20d < ₹10 Lakhs = 1,000,000 Rupees)
    shortlist_candidates = []
    for sym, price_data in price_data_map.items():
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
        for p in shortlist:
            sym = p.symbol
            cached = get_cached_fundamentals(sym, cache)
            if cached:
                logger.debug(f"💾 Cache hit for fundamentals of {sym}")
                fundamentals_list.append(cached)
            else:
                futures[executor.submit(fetch_ticker_fundamentals, sym)] = sym
                
        for future in as_completed(futures):
            sym = futures[future]
            try:
                fund = future.result()
                if fund:
                    fundamentals_list.append(fund)
                    # Update local cache memory
                    cache[sym] = {
                        "fetched_at": datetime.now(IST).isoformat(),
                        "market_cap": fund.market_cap,
                        "debt_equity": fund.debt_equity,
                        "operating_cash_flow": fund.operating_cash_flow,
                        "roe": fund.roe,
                        "revenue_growth": fund.revenue_growth_1y,
                        "earnings_growth": fund.eps_growth_1y,
                        "operating_margin": fund.operating_margin,
                        "pe": fund.pe,
                        "pb": fund.pb,
                        "div_yield": fund.div_yield,
                        "sector": fund.sector,
                        "canonical_industry": fund.canonical_industry,
                        "eps": fund.eps,
                        "bvps": fund.bvps,
                        "roa": fund.roa
                    }
            except Exception as e:
                logger.exception(f"Error fetching fundamentals for {sym}")
                
    # Save updated cache to JSON file
    save_fundamentals_cache(cache)
    
    # Check Market Regime
    market_regime = "BULL" # Defaulting for now
    try:
        nifty = price_data_map.get("^NSEI")
        if nifty and nifty.sma_200 > 0:
            if nifty.price > nifty.sma_200:
                market_regime = "BULL"
            else:
                market_regime = "BEAR"
    except Exception as e:
        logger.warning("Could not determine market regime, defaulting to BULL")
        
    logger.info(f"📊 Detected Market Regime: {market_regime}")
    
    # 4. Phase 3: Peer-aware scoring & buy zone assessment
    from valuation_utils import compute_peer_medians
    symbols_to_val = [f.symbol for f in fundamentals_list]
    peer_medians = compute_peer_medians(symbols_to_val)
            
    results = []
    categorized_stocks = {
        "🚀 PRIME MULTIBAGGER CANDIDATE": [],
        "💎 HIGH QUALITY — FAIR ENTRY": [],
        "🏆 GREAT BUSINESS — WAIT FOR DIP": [],
        "💰 VALUE BUY — DECENT QUALITY": [],
        "🟡 WATCHLIST CANDIDATE": []
    }
    

    
    # Init Rejection Log
    log_date = datetime.now().strftime('%Y-%m-%d')
    rejection_log_path = f"logs/rejections_{log_date}.jsonl"
    os.makedirs("logs", exist_ok=True)
    
    # Import the new V4 pipeline
    from core.multibagger_pipeline import run_pipeline_for_symbol
    
    for f in fundamentals_list:
        sym = f.symbol
        price_data = price_data_map.get(sym)
        if not price_data:
            continue
            
        # 1. Adapt legacy data structures to Dict for the pipeline
        raw_fundamentals = {
            "total_equity": None, # Should be fetched in deep metrics
            "promoter_pledge_pct": None, # Fallback
            "auditor_flags": False,
            "operating_cash_flow_ttm": f.operating_cash_flow,
            "debt_equity": f.debt_equity,
            "is_financial": f.is_financial,
            
            # Fundamentals for scoring
            "roic_ttm": f.roce, # Using roce as proxy for now
            "operating_margin_ttm": f.operating_margin,
            "revenue_growth_1y": f.revenue_growth_1y,
            "revenue_growth_3y": f.revenue_growth_3y,
            
            # Needed for adaptive history
            # In a full implementation, we'd pass raw time series here
        }
        
        technicals = {
            "price": price_data.price,
            "sma_50": price_data.sma_50,
            "sma_200": price_data.sma_200,
            "ema_20": price_data.sma_20, # using sma_20 as proxy for ema_20 for now
            "atr": price_data.price * 0.05, # dummy ATR for now
        }
        
        # 2. Run the V4 Pipeline
        pipeline_result = run_pipeline_for_symbol(sym, raw_fundamentals, technicals)
        
        if pipeline_result is None:
            # Rejected by Gate Engine
            # Log rejection
            rej_data = {
                "symbol": sym,
                "timestamp": datetime.now().isoformat(),
                "phase": "GATE_ENGINE",
                "reason": "Failed Kill Gates"
            }
            with open(rejection_log_path, "a") as rf:
                rf.write(json.dumps(rej_data) + "\n")
                
            status = "INVALIDATED"
            bucket = "Invalidated"
            notes = "Failed Layer 3 Gates"
            cqs = 0.0
            pas = 0.0
            trend = 0.0
            total = 0.0
            buy_low = 0.0
            buy_high = 0.0
        else:
            # Generate working scores using the proven core_score_engine
            real_scores = generate_core_scores(f, PeerMetrics(), price_data)
            
            cqs = real_scores.business_quality_score
            pas = real_scores.relative_valuation_score
            trend = real_scores.market_structure_score
            total = real_scores.composite_investment_score
            
            buy_low = pipeline_result.buy_zone_low
            buy_high = pipeline_result.buy_zone_high
            
            alert_triggered = pipeline_result.in_buy_zone
            status = "ALERT_TRIGGERED" if alert_triggered else "WAITING_BUY_ZONE"
            bucket = pipeline_result.classification.value
            
            # Formulate user-friendly notes instead of raw audit trail
            tech_status = "In Buy Zone" if alert_triggered else "Waiting for Pullback"
            if total >= 80:
                qual_str = "Exceptional Fundamentals"
            elif total >= 65:
                qual_str = "Strong Fundamentals"
            elif total >= 50:
                qual_str = "Decent Fundamentals"
            else:
                qual_str = "Average Fundamentals"
                
            notes = f"{qual_str} | {tech_status}"
            
            if alert_triggered:
                logger.info(f"🌟 Alert Triggered for {sym}! Price={price_data.price:.1f}. Reason: In Buy Zone")
                scaled_score = int(total)
                
                # Compute position sizing (total is 0-100 natively)
                momentum_for_sizing = int(total)
                sizing = calculate_risk_adjusted_sizing(price_data.price, 3.0, momentum_for_sizing)
                pos_shares = int(sizing["Position_Amount"] / price_data.price) if price_data.price > 0 else 0
                
                save_wealth_buy_alert(
                    symbol=sym,
                    alert_price=price_data.price,
                    breakout_type="MULTIBAGGER",
                    fm_score=scaled_score,
                    notes=notes,
                    position_pct=round(sizing["Position_Pct"] * 100, 2),
                    position_amount=sizing["Position_Amount"],
                    position_shares=pos_shares,
                    portfolio_bucket="MULTIBAGGER",
                    valuation_score=pas,
                    momentum_score=int(trend),
                    momentum_confidence="HIGH" if cqs >= 75.0 else "MEDIUM"
                )
                
            # Group only non-invalidated stocks for Telegram
            if status != "INVALIDATED":
                label = pipeline_result.classification.value
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
                
        res = ScreenerResult(
            symbol=sym,
            price=price_data.price,
            cqs=cqs,
            pas=pas,
            trend_score=trend,
            total_score=total,
            buy_zone_low=buy_low,
            buy_zone_high=buy_high,
            bucket=bucket,
            status=status,
            notes=notes,
            change_pct=price_data.change_pct
        )
        results.append(res)

            
    # 5. Bulk database persistence
    save_watchlist_to_db(results)
    save_scores_to_db(results)
    
    # 6. Format and queue Telegram updates
    logger.info(f"📢 Formatting Telegram messages for {len(results)} watchlist items...")
    telegram_msgs = format_telegram_message(categorized_stocks)
    for msg in telegram_msgs:
        queue_telegram_message(msg)
        
    logger.info("✅ Multibagger Scanner execution finished.")
