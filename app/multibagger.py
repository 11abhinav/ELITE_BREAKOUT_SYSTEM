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
    "Nifty Smallcap 250": "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv"
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
class StockFundamentals:
    symbol: str
    market_cap: float
    debt_equity: float
    operating_cash_flow: float
    roe: float
    revenue_growth: float
    earnings_growth: float
    operating_margin: float
    pe: float
    pb: float
    div_yield: float
    sector: str
    eps: float
    bvps: float
    roa: float = None
    tt_indpe: float = None
    tt_indpb: float = None

@dataclass
class ScreenerResult:
    symbol: str
    price: float
    cqs: float
    pas: float
    trend_score: float
    total_score: float
    fair_value: float
    buy_zone_low: float
    buy_zone_high: float
    bucket: str
    status: str
    notes: str
    change_pct: float = 0.0
    bear_value: float = None
    bull_value: float = None
    valuation_method: str = None
    valuation_confidence: str = None
    peer_count: int = None
    target_multiple: float = None
    current_multiple: float = None
    peer_multiple: float = None

class YFinanceRateLimitGuard:
    """Manages rate limit state and backoff lock-freely (sleep outside lock)."""
    def __init__(self):
        self.lock = threading.Lock()
        self.consecutive_failures = 0
        self.cooldown_until = 0.0

    def record_success(self):
        with self.lock:
            self.consecutive_failures = 0

    def record_failure(self):
        with self.lock:
            self.consecutive_failures += 1
            if self.consecutive_failures >= 5:
                # Trigger a safety cooldown
                sleep_time = min(60.0, self.consecutive_failures * 3.0)
                self.cooldown_until = time.time() + sleep_time
                logger.warning(f"⚠️ yfinance rate limit guard triggered. Cooldown scheduled for {sleep_time} seconds...")

    def wait_if_needed(self):
        while True:
            with self.lock:
                cooldown_time = self.cooldown_until - time.time()
            if cooldown_time <= 0:
                break
            logger.info(f"⏳ Thread cooling down for {cooldown_time:.1f} seconds...")
            time.sleep(min(cooldown_time, 5.0))

rate_limiter = YFinanceRateLimitGuard()

def get_nse_session() -> requests.Session:
    """Returns a requests.Session configured with connection pool and retries."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=3)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HTTP_HEADERS)
    return session

def init_db_schema():
    """Create dedicated schema and tables for stockupdates watchlist and scores."""
    logger.info("🛠️ Initializing stockupdates schema and tables in database...")
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS stockupdates;")
                
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS stockupdates.watchlist (
                        symbol VARCHAR(50) PRIMARY KEY,
                        fair_value NUMERIC(10, 2),
                        buy_zone_low NUMERIC(10, 2),
                        buy_zone_high NUMERIC(10, 2),
                        latest_price NUMERIC(10, 2),
                        growth_score NUMERIC(4, 1),
                        value_score NUMERIC(4, 1),
                        trend_score NUMERIC(4, 1),
                        total_score NUMERIC(4, 1),
                        bucket VARCHAR(50),
                        status VARCHAR(50),
                        notes TEXT,
                        last_alert_price NUMERIC(10, 2),
                        last_alert_at TIMESTAMP,
                        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS stockupdates.prices (
                        symbol VARCHAR(50) PRIMARY KEY,
                        latest_price NUMERIC(10, 2),
                        change_pct NUMERIC(10, 2),
                        fundamental_score INTEGER,
                        quality_score NUMERIC(4, 1),
                        value_score NUMERIC(4, 1),
                        last_fetched TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS stockupdates.universe (
                        symbol VARCHAR(50) PRIMARY KEY,
                        bse_code VARCHAR(20),
                        sector VARCHAR(100),
                        pe NUMERIC(10, 2),
                        pb NUMERIC(10, 2),
                        roe NUMERIC(10, 4),
                        eps NUMERIC(10, 2),
                        bvps NUMERIC(10, 2),
                        div_yield NUMERIC(10, 4),
                        tt_indpe NUMERIC(10, 2),
                        tt_indpb NUMERIC(10, 2),
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        fetch_status VARCHAR(50),
                        last_error TEXT
                    );
                """)
                
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS stockupdates.fundamental_snapshots (
                        id SERIAL PRIMARY KEY,
                        symbol VARCHAR(50) NOT NULL,
                        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        sector VARCHAR(100),
                        pe NUMERIC(10, 2),
                        pb NUMERIC(10, 2),
                        roe NUMERIC(10, 4),
                        eps NUMERIC(10, 2),
                        bvps NUMERIC(10, 2),
                        div_yield NUMERIC(10, 4),
                        revenue_growth NUMERIC(10, 4),
                        earnings_growth NUMERIC(10, 4),
                        operating_margin NUMERIC(10, 4),
                        debt_equity NUMERIC(10, 2),
                        operating_cashflow NUMERIC(15, 2),
                        roa NUMERIC(10, 4),
                        tt_indpe NUMERIC(10, 2),
                        tt_indpb NUMERIC(10, 2)
                    );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_fund_snap_sym_date ON stockupdates.fundamental_snapshots(symbol, fetched_at DESC);")
                
                # Add new valuation and confidence columns if they don't exist
                columns_to_add = [
                    ("bear_value", "NUMERIC(10, 2)"),
                    ("bull_value", "NUMERIC(10, 2)"),
                    ("valuation_method", "VARCHAR(50)"),
                    ("valuation_confidence", "VARCHAR(20)"),
                    ("peer_count", "INTEGER"),
                    ("target_multiple", "NUMERIC(10, 2)"),
                    ("current_multiple", "NUMERIC(10, 2)"),
                    ("peer_multiple", "NUMERIC(10, 2)")
                ]
                for col_name, col_type in columns_to_add:
                    cur.execute(f"ALTER TABLE stockupdates.watchlist ADD COLUMN IF NOT EXISTS {col_name} {col_type};")
                    
                conn.commit()
        logger.info("✅ Database tables validated successfully.")
        
        from valuation_utils import seed_universe_if_empty
        seed_universe_if_empty()
        
    except Exception as e:
        logger.exception(f"❌ Failed to initialize database schema")

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
            
    # Normalize symbols for yfinance querying
    normalized = []
    for s in symbols:
        clean = s.replace("_", "-")
        normalized.append(clean)
        
    logger.info(f"🎯 Total unique constituent symbols fetched: {len(normalized)}")
    return sorted(normalized)

def batch_download_market_data(symbols: list) -> dict:
    """Download historical price/volume data in bulk for all tickers using explicit auto_adjust=False."""
    ticker_names = [f"{sym}.NS" for sym in symbols]
    logger.info(f"📥 Batch downloading 1y history for {len(ticker_names)} tickers...")
    
    try:
        df = yf.download(ticker_names, period="1y", interval="1d", auto_adjust=False, group_by="ticker", progress=False)
        
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
    return any(keyword in sec_lower for keyword in ["financial", "bank", "nbfc", "insurance"])

def get_cached_fundamentals(symbol: str, cache: dict) -> StockFundamentals:
    """Fetch fundamentals from cache if it is < 7 days old."""
    if symbol not in cache:
        return None
    try:
        entry = cache[symbol]
        fetched_at = datetime.fromisoformat(entry["fetched_at"])
        age_days = (datetime.now() - fetched_at).days
        if age_days < 7:
            return StockFundamentals(
                symbol=symbol,
                market_cap=entry["market_cap"],
                debt_equity=entry["debt_equity"],
                operating_cash_flow=entry["operating_cash_flow"],
                roe=entry["roe"],
                revenue_growth=entry["revenue_growth"],
                earnings_growth=entry["earnings_growth"],
                operating_margin=entry["operating_margin"],
                pe=entry["pe"],
                pb=entry["pb"],
                div_yield=entry["div_yield"],
                sector=entry["sector"],
                eps=entry.get("eps"),
                bvps=entry.get("bvps"),
                roa=entry.get("roa"),
                tt_indpe=entry.get("tt_indpe"),
                tt_indpb=entry.get("tt_indpb")
            )
    except Exception as e:
        logger.debug(f"Failed to parse cache entry for {symbol}: {e}")
    return None

def fetch_ticker_fundamentals(symbol: str) -> StockFundamentals:
    """Fetch deeper company info metadata from yfinance."""
    rate_limiter.wait_if_needed()
    ticker_name = f"{symbol}.NS"
    ticker = yf.Ticker(ticker_name)
    
    for attempt in range(3):
        try:
            info = ticker.info
            if info and "marketCap" in info:
                from valuation_utils import fetch_tickertape_industry_metrics
                tt_indpe, tt_indpb = fetch_tickertape_industry_metrics(symbol)
                rate_limiter.record_success()
                
                fund = StockFundamentals(
                    symbol=symbol,
                    market_cap=info.get("marketCap"),
                    debt_equity=info.get("debtToEquity"),
                    operating_cash_flow=info.get("operatingCashflow"),
                    roe=info.get("returnOnEquity"),
                    revenue_growth=info.get("revenueGrowth"),
                    earnings_growth=info.get("earningsGrowth"),
                    operating_margin=info.get("operatingMargins"),
                    pe=info.get("trailingPE"),
                    pb=info.get("priceToBook"),
                    div_yield=info.get("dividendYield"),
                    sector=info.get("sector", "Unknown"),
                    eps=info.get("trailingEps"),
                    bvps=info.get("bookValue"),
                    roa=info.get("returnOnAssets"),
                    tt_indpe=tt_indpe,
                    tt_indpb=tt_indpb
                )
                
                try:
                    from database import insert_fundamental_snapshot
                    insert_fundamental_snapshot(
                        symbol=symbol,
                        sector=fund.sector,
                        pe=fund.pe,
                        pb=fund.pb,
                        roe=fund.roe,
                        eps=fund.eps,
                        bvps=fund.bvps,
                        div_yield=fund.div_yield,
                        revenue_growth=fund.revenue_growth,
                        earnings_growth=fund.earnings_growth,
                        operating_margin=fund.operating_margin,
                        debt_equity=fund.debt_equity,
                        operating_cashflow=fund.operating_cash_flow,
                        roa=fund.roa,
                        tt_indpe=fund.tt_indpe,
                        tt_indpb=fund.tt_indpb
                    )
                except Exception as snap_err:
                    logger.warning(f"Failed to save fundamental snapshot for {symbol}: {snap_err}")
                    
                return fund
            time.sleep(1.0 + (2 ** attempt))
        except Exception as e:
            msg = str(e).lower()
            if "too many requests" in msg or "429" in msg:
                rate_limiter.record_failure()
            time.sleep(2 ** attempt)
            
    # Record non-critical error in DB
    try:
        from database import upsert_fetch_error
        upsert_fetch_error(
            source_name='yfinance',
            scanner_name='MULTIBAGGER',
            symbol=symbol,
            interval='fundamental',
            category='fetch_failed',
            error_msg="Failed to fetch info after 3 retries"
        )
    except Exception as db_err:
        logger.warning(f"Failed to record fetch error in DB for {symbol}: {db_err}")
        
    return None

def passes_kill_gates(f: StockFundamentals) -> bool:
    """Instant rejection checks: Mcap < 500Cr, D/E > 1.5 (non-financials only), or Operating Cash Flow < 0."""
    if f.market_cap is None or float(f.market_cap) < 5000000000: # ₹500 Cr
        return False
        
    # Debt/Equity check (Strict: reject if > 1.0, except for Banks / NBFC / Financial sectors)
    if not is_financial_sector(f.sector):
        if f.debt_equity is not None:
            de_val = float(f.debt_equity)
            de_ratio = de_val / 100.0 if de_val > 10.0 else de_val
            if de_ratio > 1.0:
                return False
                
    # Operating Cash Flow check (Strict: reject if negative)
    if f.operating_cash_flow is not None and float(f.operating_cash_flow) < 0:
        return False
        
    return True

def calculate_cqs(f: StockFundamentals) -> float:
    """Calculate Company Quality / Growth Score out of 10 points (0 pts if missing)."""
    score = 0.0
    
    # 1. ROE (Max 2 pts: >=15% = 2, >=10% = 1)
    if f.roe is not None:
        roe_val = float(f.roe)
        if roe_val >= 0.15:
            score += 2.0
        elif roe_val >= 0.10:
            score += 1.0
            
    # 2. Leverage/Quality (Max 2 pts)
    if is_financial_sector(f.sector):
        # Financials use ROA instead of D/E
        if getattr(f, "roa", None) is not None:
            roa_val = float(f.roa)
            if roa_val >= 0.08:
                score += 2.0
            elif roa_val >= 0.04:
                score += 1.0
    else:
        # Non-Financials use D/E: <=0.50 = 2, <=1.00 = 1
        if f.debt_equity is not None:
            de_val = float(f.debt_equity)
            de_ratio = de_val / 100.0 if de_val > 10.0 else de_val
            if de_ratio <= 0.50:
                score += 2.0
            elif de_ratio <= 1.00:
                score += 1.0
            
    # 3. Revenue Growth (Max 2 pts: >=10% = 2, >=5% = 1)
    if f.revenue_growth is not None:
        rg = float(f.revenue_growth)
        if rg >= 0.10:
            score += 2.0
        elif rg >= 0.05:
            score += 1.0
        
    # 4. Earnings Growth (Max 2 pts: >=10% = 2, >=5% = 1)
    if f.earnings_growth is not None:
        eg = float(f.earnings_growth)
        if eg >= 0.10:
            score += 2.0
        elif eg >= 0.05:
            score += 1.0
        
    # 5. Operating Margin (Max 1 pt: >=15% = 1, >=10% = 0.5)
    if f.operating_margin is not None:
        opm = float(f.operating_margin)
        if opm >= 0.15:
            score += 1.0
        elif opm >= 0.10:
            score += 0.5

    # 6. EPS Floor (Max 1 pt)
    if f.eps is not None:
        eps_val = float(f.eps)
        eg = float(f.earnings_growth) if f.earnings_growth is not None else 0.0
        if eps_val >= 5.0 or (eps_val >= 1.0 and eg >= 0.05):
            score += 1.0
        
    return round(score, 1)

# The compute_peer_medians function has been moved to valuation_utils.py

def calculate_value_score(f: StockFundamentals, medians: dict) -> float:
    """Calculate Value Score (PAS) out of 10 points (0 pts if missing). Use peer medians."""
    score = 0.0
    
    # Financial Sector valuation (rely on P/B and ROE compared to sector peer bands instead of P/E)
    if is_financial_sector(f.sector):
        # 1. P/B vs Sector (Max 5 pts: <= sector median = 5, <= 1.2 * sector median = 3)
        if f.pb is not None and f.pb > 0:
            peer_pb = f.tt_indpb if getattr(f, "tt_indpb", None) else medians.get(f.sector, {}).get("median_pb")
            if peer_pb is not None:
                if f.pb <= peer_pb:
                    score += 5.0
                elif f.pb <= peer_pb * 1.2:
                    score += 3.0
            else:
                # Fallback to absolute
                if f.pb <= 2.0:
                    score += 5.0
                elif f.pb <= 3.5:
                    score += 3.0
                    
        # 2. ROE Profitability vs Sector (Max 5 pts: >= sector median = 5, >= 0.8 * sector median = 3)
        if f.roe is not None:
            peer_roe = medians.get(f.sector, {}).get("median_roe")
            if peer_roe is not None:
                if f.roe >= peer_roe:
                    score += 5.0
                elif f.roe >= peer_roe * 0.8:
                    score += 3.0
            else:
                # Fallback to absolute
                if f.roe >= 0.16:
                    score += 5.0
                elif f.roe >= 0.12:
                    score += 3.0
    else:
        # Non-Financials: standard P/E (Max 4) & P/B (Max 3) vs Sector, Div Yield (Max 1), and Low 52W (evaluated at alert step, now rescaled)
        # Note: 2 points for 52W low distance is evaluated globally in should_trigger_alert.
        # This function returns a fundamental-only value score out of 10 points.
        
        # 1. P/E vs Sector (Max 5 pts: <= median = 5, <= 1.2 * median = 3)
        if f.pe is not None and f.pe > 0:
            peer_pe = f.tt_indpe if getattr(f, "tt_indpe", None) else medians.get(f.sector, {}).get("median_pe")
            if peer_pe is not None:
                if f.pe <= peer_pe:
                    score += 5.0
                elif f.pe <= peer_pe * 1.2:
                    score += 3.0
            else:
                if f.pe <= 20.0:
                    score += 5.0
                elif f.pe <= 35.0:
                    score += 3.0
                    
        # 2. P/B vs Sector (Max 4 pts: <= median = 4, <= 1.2 * median = 2)
        if f.pb is not None and f.pb > 0:
            peer_pb = f.tt_indpb if getattr(f, "tt_indpb", None) else medians.get(f.sector, {}).get("median_pb")
            if peer_pb is not None:
                if f.pb <= peer_pb:
                    score += 4.0
                elif f.pb <= peer_pb * 1.2:
                    score += 2.0
            else:
                if f.pb <= 2.5:
                    score += 4.0
                elif f.pb <= 4.5:
                    score += 2.0
                    
        # 3. Dividend Yield (Max 1 pt)
        if f.div_yield is not None and float(f.div_yield) >= 0.005:
            score += 1.0
            
    return round(score, 1)

def calculate_trend_score(price_data: StockPriceData) -> float:
    """Calculate Trend Score out of 10 points based on broad accumulation and breakout triggers."""
    score = 0.0
    
    # 1. Broad Accumulation (Max 6 pts)
    # Price above 50-DMA (+3)
    if price_data.price > price_data.sma_50:
        score += 3.0
    # Price above 200-DMA (+3)
    if price_data.price > price_data.sma_200:
        score += 3.0
        
    # 2. Breakout day/strength signal (Max 4 pts)
    # Volume spike: latest volume > 1.5x average 20-day volume (+2)
    if price_data.latest_volume > (1.5 * price_data.volume_sma20):
        score += 2.0
    # Price breakout: close price matches or exceeds 20-day high (+2)
    if price_data.price >= price_data.high_20d:
        score += 2.0
        
    return round(score, 1)

def calculate_fair_value(f: StockFundamentals, price_data: StockPriceData, medians: dict) -> FairValueResult:
    info = medians.get(f.sector, {}) if medians else {}

    peer_count_raw = info.get("peer_count", 0)
    peer_count_pe = info.get("peer_count_pe", peer_count_raw)
    peer_count_pb = info.get("peer_count_pb", peer_count_raw)
    
    # Prefer Tickertape, fallback to DB medians
    peer_pe = f.tt_indpe if getattr(f, "tt_indpe", None) else info.get("median_pe")
    peer_pb = f.tt_indpb if getattr(f, "tt_indpb", None) else info.get("median_pb")
    
    # If using Tickertape, assume enough peers exist for the metric
    if getattr(f, "tt_indpe", None):
        peer_count_pe = max(peer_count_pe, 8)
    if getattr(f, "tt_indpb", None):
        peer_count_pb = max(peer_count_pb, 8)

    min_peer_count = 8

    try:
        if is_financial_sector(f.sector):
            current_pb = float(f.pb) if f.pb and f.pb > 0 else None
            bvps = float(f.bvps) if f.bvps and f.bvps > 0 else None
            peer_count = peer_count_pb

            if bvps and peer_pb and peer_count and peer_count >= min_peer_count:
                raw_target_pb = (0.65 * float(peer_pb)) + (0.35 * current_pb if current_pb else 0.0)
                target_pb = clamp(raw_target_pb, 0.8, 1.5 * float(peer_pb))
                fair_value = target_pb * bvps
                fair_value = min(fair_value, price_data.price * 2.0)
                bear_value = max(bvps * max(0.85 * target_pb, 0.8), price_data.price * 0.85)
                bull_value = bvps * min(target_pb * 1.15, 1.75 * float(peer_pb))

                confidence = "HIGH" if peer_count >= 15 else "MEDIUM"
                return FairValueResult(
                    fair_value=round(fair_value, 2),
                    bear_value=round(bear_value, 2),
                    bull_value=round(bull_value, 2),
                    valuation_method="BLENDED_SECTOR_PB",
                    valuation_confidence=confidence,
                    peer_count=peer_count,
                    target_multiple=round(target_pb, 2),
                    current_multiple=round(current_pb, 2) if current_pb else None,
                    peer_multiple=round(float(peer_pb), 2),
                    is_fallback=False
                )

            fallback_fv = price_data.price * 0.95
            return FairValueResult(
                fair_value=round(fallback_fv, 2),
                bear_value=round(fallback_fv * 0.92, 2),
                bull_value=round(fallback_fv * 1.08, 2),
                valuation_method="FALLBACK_PRICE_ANCHORED_PB",
                valuation_confidence="LOW",
                peer_count=peer_count,
                target_multiple=None,
                current_multiple=current_pb,
                peer_multiple=float(peer_pb) if peer_pb else None,
                is_fallback=True
            )

        current_pe = float(f.pe) if f.pe and f.pe > 0 else None
        eps = float(f.eps) if f.eps and f.eps > 0 else None
        peer_count = peer_count_pe

        if eps and peer_pe and peer_count and peer_count >= min_peer_count:
            peer_pe = float(peer_pe)

            if current_pe:
                raw_target_pe = (0.60 * peer_pe) + (0.40 * current_pe)
            else:
                raw_target_pe = peer_pe

            sector_cap = 1.35 * peer_pe
            absolute_cap = 30.0 if (f.revenue_growth or 0) < 0.15 else 50.0
            target_pe = clamp(raw_target_pe, 6.0, min(sector_cap, absolute_cap))

            fair_value = target_pe * eps
            fair_value = min(fair_value, price_data.price * 2.0)
            bear_pe = max(0.85 * target_pe, 0.85 * current_pe if current_pe else 6.0)
            bull_pe = min(1.15 * target_pe, sector_cap, absolute_cap)

            bear_value = bear_pe * eps
            bull_value = bull_pe * eps

            if current_pe and current_pe > peer_pe * 2.0:
                confidence = "LOW"
            elif current_pe and peer_pe > current_pe * 1.75:
                confidence = "MEDIUM"
            else:
                confidence = "HIGH" if peer_count >= 15 else "MEDIUM"

            if current_pe and fair_value > price_data.price * 1.50 and (f.revenue_growth or 0) < 0.15:
                fair_value = price_data.price * 1.50
                bull_value = min(bull_value, price_data.price * 1.65)
                confidence = "MEDIUM"

            return FairValueResult(
                fair_value=round(fair_value, 2),
                bear_value=round(bear_value, 2),
                bull_value=round(bull_value, 2),
                valuation_method="BLENDED_SECTOR_PE",
                valuation_confidence=confidence,
                peer_count=peer_count,
                target_multiple=round(target_pe, 2),
                current_multiple=round(current_pe, 2) if current_pe else None,
                peer_multiple=round(peer_pe, 2),
                is_fallback=False
            )

        if current_pe and eps:
            target_pe = clamp(current_pe * 0.85, 6.0, 30.0)
            fair_value = target_pe * eps
            fair_value = min(fair_value, price_data.price * 2.0)
            return FairValueResult(
                fair_value=round(fair_value, 2),
                bear_value=round(fair_value * 0.90, 2),
                bull_value=round(fair_value * 1.10, 2),
                valuation_method="CURRENT_PE_FALLBACK",
                valuation_confidence="LOW",
                peer_count=peer_count,
                target_multiple=round(target_pe, 2),
                current_multiple=round(current_pe, 2),
                peer_multiple=float(peer_pe) if peer_pe else None,
                is_fallback=True
            )

    except Exception as e:
        logger.exception(f"Fair value derivation exception for {f.symbol}")

    fallback_fv = price_data.price * 0.95
    return FairValueResult(
        fair_value=round(fallback_fv, 2),
        bear_value=round(fallback_fv * 0.92, 2),
        bull_value=round(fallback_fv * 1.08, 2),
        valuation_method="PRICE_FALLBACK",
        valuation_confidence="LOW",
        peer_count=peer_count,
        target_multiple=None,
        current_multiple=float(f.pe) if f.pe else None,
        peer_multiple=float(peer_pe) if peer_pe else None,
        is_fallback=True
    )

def should_trigger_alert(price_data: StockPriceData, fv: FairValueResult, cqs: float, value_score: float, trend_score: float) -> tuple:
    price = price_data.price
    buy_zone_low = price_data.sma_200

    if fv.valuation_confidence == "HIGH":
        buy_zone_high = fv.fair_value * 1.03
    elif fv.valuation_confidence == "MEDIUM":
        buy_zone_high = fv.fair_value * 1.00
    else:
        buy_zone_high = min(fv.fair_value, price * 1.08)

    if cqs < 5.0 or price < price_data.sma_200:
        return False, f"Fails base entry guards: CQS ({cqs:.1f}) < 5.0 or Price < 200-DMA."

    in_buy_zone = (price >= buy_zone_low and price <= buy_zone_high)
    trend_ok = (trend_score >= 5.0 and price > price_data.sma_50)

    if in_buy_zone and trend_ok:
        return True, f"Value Breakout: inside buy zone, FV={fv.fair_value:.1f}, confidence={fv.valuation_confidence}"

    is_high_growth = (cqs >= 8.0)
    strong_trend = (trend_score >= 7.0)
    premium_cap = fv.bull_value if fv.valuation_confidence != "LOW" else fv.fair_value * 1.08

    if is_high_growth and strong_trend and price <= premium_cap:
        return True, f"GARP Rerating: using bull case cap, confidence={fv.valuation_confidence}"

    if in_buy_zone and not trend_ok:
        return False, f"Waiting for trend confirmation: Trend Score ({trend_score:.1f}) < 5.0."

    return False, "Watchlist waiting: price outside buy zone."
        
def get_label(cqs: float, pas: float) -> str:
    """Return the output label category based on CQS and PAS."""
    if cqs >= 7.0 and pas >= 7.0:
        return "🚀 PRIME MULTIBAGGER CANDIDATE"
    elif cqs >= 7.0 and 4.0 <= pas <= 6.0:
        return "💎 HIGH QUALITY — FAIR ENTRY"
    elif cqs >= 7.0 and pas < 4.0:
        return "🏆 GREAT BUSINESS — WAIT FOR DIP"
    elif 5.0 <= cqs <= 6.0 and pas >= 6.0:
        return "💰 VALUE BUY — DECENT QUALITY"
    elif 5.0 <= cqs <= 6.0 and 4.0 <= pas <= 5.0:
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
            last_at = datetime.now()
        else:
            last_price = None
            last_at = None
            
        data.append((
            r.symbol.upper(), r.fair_value, r.buy_zone_low, r.buy_zone_high, r.price,
            r.cqs, r.pas, r.trend_score, r.total_score, r.bucket, r.status, r.notes,
            last_price, last_at,
            r.bear_value, r.bull_value, r.valuation_method, r.valuation_confidence,
            r.peer_count, r.target_multiple, r.current_multiple, r.peer_multiple
        ))
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Upsert query using execute_values
                execute_values(cur, """
                    INSERT INTO stockupdates.watchlist 
                    (symbol, fair_value, buy_zone_low, buy_zone_high, latest_price, 
                     growth_score, value_score, trend_score, total_score, bucket, status, notes,
                     last_alert_price, last_alert_at, bear_value, bull_value, valuation_method,
                     valuation_confidence, peer_count, target_multiple, current_multiple, peer_multiple)
                    VALUES %s
                    ON CONFLICT (symbol) DO UPDATE SET
                        fair_value = EXCLUDED.fair_value,
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
                        bear_value = EXCLUDED.bear_value,
                        bull_value = EXCLUDED.bull_value,
                        valuation_method = EXCLUDED.valuation_method,
                        valuation_confidence = EXCLUDED.valuation_confidence,
                        peer_count = EXCLUDED.peer_count,
                        target_multiple = EXCLUDED.target_multiple,
                        current_multiple = EXCLUDED.current_multiple,
                        peer_multiple = EXCLUDED.peer_multiple,
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
        legacy_score = int(r.total_score * 2.5) # scaled out of 50
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
                
            cqs = calculate_cqs(fund) if fund else 5.0 # fallback if no fundamentals
            
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
                    
            # Rule 3: Fundamental Deterioration (CQS < 5.0 or fails Kill Gates)
            if not exit_triggered and fund:
                if cqs < 5.0:
                    exit_triggered = True
                    exit_reason = f"Deteriorating Fundamentals: Quality score dropped below 5.0 (CQS: {cqs:.1f})"
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
                    queue_telegram_message(sell_msg, symbol=symbol, alert_id=alert_id)
                    
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

def start(debug_limit: int = None):
    """Main scanning wrapper."""
    logger.info("🚀 Multibagger Scanner execution started...")
    init_db_schema()
    
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
        
    # Sort by turnover descending and shortlist top 120 most liquid
    shortlist_candidates = sorted(shortlist_candidates, key=lambda x: x.turnover_20d, reverse=True)
    shortlist = shortlist_candidates[:120]
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
                        "fetched_at": datetime.now().isoformat(),
                        "market_cap": fund.market_cap,
                        "debt_equity": fund.debt_equity,
                        "operating_cash_flow": fund.operating_cash_flow,
                        "roe": fund.roe,
                        "revenue_growth": fund.revenue_growth,
                        "earnings_growth": fund.earnings_growth,
                        "operating_margin": fund.operating_margin,
                        "pe": fund.pe,
                        "pb": fund.pb,
                        "div_yield": fund.div_yield,
                        "sector": fund.sector,
                        "eps": fund.eps,
                        "bvps": fund.bvps,
                        "roa": fund.roa,
                        "tt_indpe": fund.tt_indpe,
                        "tt_indpb": fund.tt_indpb
                    }
            except Exception as e:
                logger.exception(f"Error fetching fundamentals for {sym}")
                
    # Save updated cache to JSON file
    save_fundamentals_cache(cache)
    
    # Filter fundamentals matching Kill Gates for sector median calculations
    valid_fundamentals = [f for f in fundamentals_list if passes_kill_gates(f)]
    logger.info(f"🛡️ {len(valid_fundamentals)}/{len(fundamentals_list)} shortlisted stocks passed Layer 1 Kill Gates.")
    
    # 4. Phase 3: Peer-aware scoring & buy zone assessment (Using Persistent Universe)
    from database import get_all_universe_fundamentals
    from valuation_utils import compute_sector_medians
    
    universe_rows = get_all_universe_fundamentals()
    if not universe_rows:
        logger.warning("Universe table is empty! Sector medians will be unavailable.")
        
    peer_medians = compute_sector_medians(universe_rows)
    
    # Explicit schema verification on the returned peer medians object
    if peer_medians and isinstance(peer_medians, dict):
        sample_val = next(iter(peer_medians.values()), None)
        if sample_val and not all(k in sample_val for k in ["median_pe", "median_pb", "median_roe", "peer_count"]):
            logger.error("❌ peer_medians schema invalid. Expected keys missing.")
            peer_medians = {}
            
    results = []
    categorized_stocks = {
        "🚀 PRIME MULTIBAGGER CANDIDATE": [],
        "💎 HIGH QUALITY — FAIR ENTRY": [],
        "🏆 GREAT BUSINESS — WAIT FOR DIP": [],
        "💰 VALUE BUY — DECENT QUALITY": [],
        "🟡 WATCHLIST CANDIDATE": []
    }
    
    for f in fundamentals_list:
        sym = f.symbol
        price_data = price_data_map.get(sym)
        if not price_data:
            continue
            
        alert_triggered = False
        alert_reason = ""
        
        # Enforce Kill Gates to flag INVALIDATED early
        if not passes_kill_gates(f):
            status = "INVALIDATED"
            bucket = "Invalidated"
            notes = "Invalidated: Fails Layer 1 Kill Gates (Market Cap / Debt / Cash Flow)"
            cqs = 0.0
            pas = 0.0
            trend = 0.0
            total = 0.0
            
            base_fv = price_data.price * 0.95
            buy_low = base_fv * 0.5
            buy_high = min(base_fv, price_data.price * 1.08)
            bear_fv = base_fv * 0.92
            bull_fv = base_fv * 1.08
            
            fair_val_result = FairValueResult(
                fair_value=round(base_fv, 2),
                bear_value=round(bear_fv, 2),
                bull_value=round(bull_fv, 2),
                valuation_method="INVALIDATED",
                valuation_confidence="LOW",
                peer_count=None,
                target_multiple=None,
                current_multiple=None,
                peer_multiple=None,
                is_fallback=True
            )
        else:
            # Calculate scores for valid stocks
            cqs = calculate_cqs(f)
            pas = calculate_value_score(f, peer_medians)
            trend = calculate_trend_score(price_data)
            total = cqs + pas
            
            fair_val_result = calculate_fair_value(f, price_data, peer_medians)
            base_fv = fair_val_result.fair_value
            bear_fv = fair_val_result.bear_value
            bull_fv = fair_val_result.bull_value
            
            buy_low = price_data.sma_200 if price_data.sma_200 > 0 else (base_fv * 0.5)
            
            if fair_val_result.valuation_confidence == "HIGH":
                buy_high = base_fv * 1.03
            elif fair_val_result.valuation_confidence == "MEDIUM":
                buy_high = base_fv * 1.00
            else:
                buy_high = min(base_fv, price_data.price * 1.08)
            
            # Enforce invariant: buy zone low must be strictly less than buy zone high
            if buy_low >= buy_high:
                buy_low = base_fv * 0.8  # Fallback to a 20% discount if 200-DMA is structurally too high
                notes_prefix = "(Buy Zone Synthetically Repaired) "
            else:
                notes_prefix = ""
            
            # Check alerts
            alert_triggered, alert_reason = should_trigger_alert(price_data, fair_val_result, cqs, pas, trend)
            
            # Determine buckets
            if alert_triggered:
                status = "ALERT_TRIGGERED"
                if "GARP" in alert_reason:
                    bucket = "GARP Rerating"
                else:
                    bucket = "Value Breakout"
            else:
                status = "WAITING_BUY_ZONE"
                bucket = "Watchlist Waiting"
                
            notes = notes_prefix + alert_reason
            
            if fair_val_result.is_fallback:
                missing_details = f"eps={f.eps}, pe={f.pe}, peer_pe={fair_val_result.peer_multiple}, peer_count={fair_val_result.peer_count}"
                notes += f"\n⚠️ (Estimated Fallback: Valuation metrics missing. {missing_details})"
                logger.warning(f"⚠️ Yahoo/Peer data missing for {sym} valuation (Estimated Fallback used). Details: {missing_details}")
            
        res = ScreenerResult(
            symbol=sym,
            price=price_data.price,
            cqs=cqs,
            pas=pas,
            trend_score=trend,
            total_score=total,
            fair_value=base_fv,
            buy_zone_low=buy_low,
            buy_zone_high=buy_high,
            bucket=bucket,
            status=status,
            notes=notes,
            change_pct=price_data.change_pct,
            bear_value=bear_fv,
            bull_value=bull_fv,
            valuation_method=fair_val_result.valuation_method,
            valuation_confidence=fair_val_result.valuation_confidence,
            peer_count=fair_val_result.peer_count,
            target_multiple=fair_val_result.target_multiple,
            current_multiple=fair_val_result.current_multiple,
            peer_multiple=fair_val_result.peer_multiple
        )
        results.append(res)
        
        # Trigger buy alert for ready positions
        if alert_triggered:
            logger.info(f"🌟 Alert Triggered for {sym}! FV={base_fv:.1f}, Price={price_data.price:.1f}. Reason: {alert_reason}")
            scaled_score = int(total * 5.0)
            
            # Compute position sizing (total_score 0-20 → momentum 0-100)
            momentum_for_sizing = min(100, int(total * 5.0))
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
                momentum_confidence="HIGH" if cqs >= 7.0 else "MEDIUM"
            )
            
        # Group only non-invalidated stocks for Telegram
        if status != "INVALIDATED":
            label = get_label(cqs, pas)
            if label:
                categorized_stocks[label].append({
                    'symbol': sym,
                    'price': price_data.price,
                    'cqs': cqs,
                    'pas': pas,
                    'total': total,
                    'status': status
                })
            
    # 5. Bulk database persistence
    save_watchlist_to_db(results)
    save_scores_to_db(results)
    
    # 6. Format and queue Telegram updates
    logger.info(f"📢 Formatting Telegram messages for {len(results)} watchlist items...")
    telegram_msgs = format_telegram_message(categorized_stocks)
    for msg in telegram_msgs:
        queue_telegram_message(msg)
        
    logger.info("✅ Multibagger Scanner execution finished.")
