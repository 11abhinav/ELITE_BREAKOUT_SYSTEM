import io
import os
import time
import json
import logging
import requests
import threading
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
from zoneinfo import ZoneInfo
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from psycopg2.extras import execute_values

from database import get_connection, save_wealth_buy_alert, close_position
from telegram_engine import queue_telegram_message

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
                conn.commit()
        logger.info("✅ Database tables validated successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to initialize database schema: {e}")

def load_fundamentals_cache() -> dict:
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
        logger.error(f"❌ Failed to save fundamentals cache: {e}")

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
        logger.error(f"❌ Batch price download failed: {e}")
        return {}

def is_financial_sector(sector: str) -> bool:
    """Identify if the sector represents a bank, NBFC, or financial services firm."""
    if not sector:
        return False
    sec_lower = str(sector).lower()
    return any(keyword in sec_lower for keyword in ["financial", "bank", "nbfc", "insurance", "holding"])

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
                bvps=entry.get("bvps")
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
                rate_limiter.record_success()
                return StockFundamentals(
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
                    bvps=info.get("bookValue")
                )
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
    except Exception:
        pass
        
    return None

def passes_kill_gates(f: StockFundamentals) -> bool:
    """Instant rejection checks: Mcap < 500Cr, D/E > 1.5 (non-financials only), or Operating Cash Flow < 0."""
    if f.market_cap is None or float(f.market_cap) < 5000000000: # ₹500 Cr
        return False
        
    # Debt/Equity check (Strict: reject if > 1.5, except for Banks / NBFC / Financial sectors)
    if not is_financial_sector(f.sector):
        if f.debt_equity is not None:
            de_val = float(f.debt_equity)
            de_ratio = de_val / 100.0 if de_val > 10.0 else de_val
            if de_ratio > 1.5:
                return False
                
    # Operating Cash Flow check (Strict: reject if negative)
    if f.operating_cash_flow is not None and float(f.operating_cash_flow) < 0:
        return False
        
    return True

def calculate_cqs(f: StockFundamentals) -> float:
    """Calculate Company Quality / Growth Score out of 10 points (0 pts if missing)."""
    score = 0.0
    
    # 1. ROE (Max 3 pts: >=20% = 3, >=12% = 1)
    if f.roe is not None:
        roe_val = float(f.roe)
        if roe_val >= 0.20:
            score += 3.0
        elif roe_val >= 0.12:
            score += 1.0
            
    # 2. Debt/Equity (Max 2 pts: <=0.3 = 2, <=0.7 = 1) - Only scored for Non-Financials
    # Financials skip this logic and receive a flat +2 quality adjustment (rescaled for leverage)
    if is_financial_sector(f.sector):
        score += 2.0
    else:
        if f.debt_equity is not None:
            de_val = float(f.debt_equity)
            de_ratio = de_val / 100.0 if de_val > 10.0 else de_val
            if de_ratio <= 0.3:
                score += 2.0
            elif de_ratio <= 0.7:
                score += 1.0
            
    # 3. Revenue Growth (Max 2 pts: >=15% = 2)
    if f.revenue_growth is not None and float(f.revenue_growth) >= 0.15:
        score += 2.0
        
    # 4. Earnings Growth (Max 2 pts: >=15% = 2)
    if f.earnings_growth is not None and float(f.earnings_growth) >= 0.15:
        score += 2.0
        
    # 5. Operating Margin (Max 1 pt: >=18% = 1)
    if f.operating_margin is not None and float(f.operating_margin) >= 0.18:
        score += 1.0
        
    return round(score, 1)

def compute_sector_medians(fundamentals_list: list) -> dict:
    """Compute median P/E, P/B, and ROE per sector across shortlist universe."""
    pe_groups = {}
    pb_groups = {}
    roe_groups = {}
    
    for f in fundamentals_list:
        if not f or not f.sector or f.sector == "Unknown":
            continue
        if f.pe is not None and f.pe > 0:
            pe_groups.setdefault(f.sector, []).append(f.pe)
        if f.pb is not None and f.pb > 0:
            pb_groups.setdefault(f.sector, []).append(f.pb)
        if f.roe is not None and f.roe > 0:
            roe_groups.setdefault(f.sector, []).append(f.roe)
            
    medians = {}
    all_sectors = set(pe_groups.keys()).union(pb_groups.keys()).union(roe_groups.keys())
    
    for sector in all_sectors:
        pes = pe_groups.get(sector, [])
        pbs = pb_groups.get(sector, [])
        roes = roe_groups.get(sector, [])
        medians[sector] = {
            "median_pe": float(np.median(pes)) if len(pes) >= 3 else None,
            "median_pb": float(np.median(pbs)) if len(pbs) >= 3 else None,
            "median_roe": float(np.median(roes)) if len(roes) >= 3 else None
        }
        
    return medians

def calculate_value_score(f: StockFundamentals, medians: dict) -> float:
    """Calculate Value Score (PAS) out of 10 points (0 pts if missing). Use sector medians."""
    score = 0.0
    
    # Financial Sector valuation (rely on P/B and ROE compared to sector peer bands instead of P/E)
    if is_financial_sector(f.sector):
        # 1. P/B vs Sector (Max 5 pts: <= sector median = 5, <= 1.2 * sector median = 3)
        if f.pb is not None and f.pb > 0:
            sector_pb = medians.get(f.sector, {}).get("median_pb") if f.sector else None
            if sector_pb is not None:
                if f.pb <= sector_pb:
                    score += 5.0
                elif f.pb <= sector_pb * 1.2:
                    score += 3.0
            else:
                # Fallback to absolute
                if f.pb <= 2.0:
                    score += 5.0
                elif f.pb <= 3.5:
                    score += 3.0
                    
        # 2. ROE Profitability vs Sector (Max 5 pts: >= sector median = 5, >= 0.8 * sector median = 3)
        if f.roe is not None:
            sector_roe = medians.get(f.sector, {}).get("median_roe") if f.sector else None
            if sector_roe is not None:
                if f.roe >= sector_roe:
                    score += 5.0
                elif f.roe >= sector_roe * 0.8:
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
        # This function returns a fundamental-only value score out of 8 points.
        
        # 1. P/E vs Sector (Max 5 pts: <= median = 5, <= 1.2 * median = 3)
        if f.pe is not None and f.pe > 0:
            sector_pe = medians.get(f.sector, {}).get("median_pe") if f.sector else None
            if sector_pe is not None:
                if f.pe <= sector_pe:
                    score += 5.0
                elif f.pe <= sector_pe * 1.2:
                    score += 3.0
            else:
                if f.pe <= 20.0:
                    score += 5.0
                elif f.pe <= 35.0:
                    score += 3.0
                    
        # 2. P/B vs Sector (Max 4 pts: <= median = 4, <= 1.2 * median = 2)
        if f.pb is not None and f.pb > 0:
            sector_pb = medians.get(f.sector, {}).get("median_pb") if f.sector else None
            if sector_pb is not None:
                if f.pb <= sector_pb:
                    score += 4.0
                elif f.pb <= sector_pb * 1.2:
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

def calculate_fair_value(f: StockFundamentals, price_data: StockPriceData, medians: dict) -> float:
    """Calculate Company Fair Value. Uses sector median valuation overrides."""
    try:
        if is_financial_sector(f.sector):
            # Fair Value for financials = (Sector Median P/B) * BVPS
            sector_pb = medians.get(f.sector, {}).get("median_pb")
            bvps = f.bvps
            if sector_pb and bvps and float(bvps) > 0:
                return float(sector_pb * float(bvps))
        else:
            # Fair Value for non-financials = (Sector Median P/E) * EPS
            sector_pe = medians.get(f.sector, {}).get("median_pe")
            eps = f.eps
            if sector_pe and eps and float(eps) > 0:
                return float(sector_pe * float(eps))
    except Exception as e:
        logger.debug(f"Fair value derivation exception for {f.symbol}: {e}")
        
    # Fallback default: 90% of current close price
    return price_data.price * 0.90

def should_trigger_alert(price_data: StockPriceData, fair_value: float, cqs: float, value_score: float, trend_score: float) -> tuple:
    """
    Evaluates whether a candidate triggers an active BUY alert.
    Returns: (should_alert: bool, reason: str)
    """
    price = price_data.price
    buy_zone_low = fair_value * 0.90
    buy_zone_high = fair_value * 1.05
    
    # 1. Normal Value Breakout: Undervalued, trend confirmed (above 50DMA), inside buy zone
    in_buy_zone = (price >= buy_zone_low and price <= buy_zone_high)
    trend_ok = (trend_score >= 5.0 and price > price_data.sma_50)
    
    if in_buy_zone and trend_ok:
        return True, "Value Breakout: inside buy zone with confirmed trend."
        
    # 2. GARP (Growth at a Reasonable Price) Override:
    # High growth outruns valuation: premium entry up to 115% of fair value allowed
    is_high_growth = (cqs >= 8.0)
    strong_trend = (trend_score >= 7.0)
    below_premium_cap = (price <= fair_value * 1.15)
    
    if is_high_growth and strong_trend and below_premium_cap:
        return True, f"GARP Rerating: Growth override active (CQS={cqs}) with strong trend."
        
    return False, "Watchlist waiting: price is outside correct buy zone."
        
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
            last_price, last_at
        ))
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Upsert query using execute_values
                execute_values(cur, """
                    INSERT INTO stockupdates.watchlist 
                    (symbol, fair_value, buy_zone_low, buy_zone_high, latest_price, 
                     growth_score, value_score, trend_score, total_score, bucket, status, notes,
                     last_alert_price, last_alert_at)
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
                        last_updated = CURRENT_TIMESTAMP;
                """, data)
            conn.commit()
        logger.info(f"✅ Stored {len(results)} candidates in stockupdates.watchlist (execute_values).")
    except Exception as e:
        logger.error(f"❌ Failed to bulk write to stockupdates.watchlist: {e}")

def save_scores_to_db(results: list):
    """Save scanned scores in bulk using psycopg2 execute_values."""
    if not results:
        return
    
    data = []
    for r in results:
        legacy_score = int(r.total_score * 2.5) # scaled out of 50
        data.append((r.symbol.upper(), r.price, legacy_score, r.cqs, r.pas))
        
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                execute_values(cur, """
                    INSERT INTO stockupdates.prices (symbol, latest_price, fundamental_score, quality_score, value_score)
                    VALUES %s
                    ON CONFLICT (symbol) DO UPDATE SET
                        latest_price = EXCLUDED.latest_price,
                        fundamental_score = EXCLUDED.fundamental_score,
                        quality_score = EXCLUDED.quality_score,
                        value_score = EXCLUDED.value_score,
                        last_fetched = CURRENT_TIMESTAMP;
                """, data)
            conn.commit()
        logger.info(f"✅ Stored {len(results)} stock scores in stockupdates.prices (execute_values).")
    except Exception as e:
        logger.error(f"❌ Failed to bulk write to stockupdates.prices: {e}")

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
        for item in sorted(stocks, key=lambda x: x['total'], reverse=True):
            sym = item['symbol']
            cqs = item['cqs']
            pas = item['pas']
            price = item['price']
            total = item['total']
            status = item['status']
            
            alert_marker = " 🔔 <b>BUY READY</b>" if status == "ALERT_TRIGGERED" else " ⏳ WAITING"
            line = f"• <b>{sym}</b> (₹{price:.1f}) | CQS: {cqs:.1f} | PAS: {pas:.1f} | Total: <b>{total:.1f}/20</b>{alert_marker}\n"
            
            if len(current_msg) + len(section_text) + len(line) > 3900:
                messages.append(current_msg)
                current_msg = "<b>🚀 MULTIBAGGER WATCHLIST SUMMARY (Cont.)</b>\n\n"
                
            section_text += line
            current_msg += section_text + "\n"

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
                close_success = close_position(symbol, current_price, exit_reason)
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
        logger.error(f"❌ Failed to complete exit monitoring: {e}")

def start(debug_limit: int = None):
    """Main scanning wrapper."""
    logger.info("🚀 Multibagger Scanner execution started...")
    init_db_schema()
    
    # Load fundamentals cache
    cache = load_fundamentals_cache()
    
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
                        "bvps": fund.bvps
                    }
            except Exception as e:
                logger.error(f"Error fetching fundamentals for {sym}: {e}")
                
    # Save updated cache to JSON file
    save_fundamentals_cache(cache)
    
    # Filter fundamentals matching Kill Gates for sector median calculations
    valid_fundamentals = [f for f in fundamentals_list if passes_kill_gates(f)]
    logger.info(f"🛡️ {len(valid_fundamentals)}/{len(fundamentals_list)} shortlisted stocks passed Layer 1 Kill Gates.")
    
    # 4. Phase 3: Sector-aware scoring & buy zone assessment
    sector_medians = compute_sector_medians(valid_fundamentals)
    
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
        price_data = price_data_map[sym]
        
        # Calculate scores
        cqs = calculate_cqs(f)
        pas = calculate_value_score(f, sector_medians)
        trend = calculate_trend_score(price_data)
        total = cqs + pas
        
        fair_val = calculate_fair_value(f, price_data, sector_medians)
        buy_low = fair_val * 0.90
        buy_high = fair_val * 1.05
        
        # Enforce Kill Gates to flag INVALIDATED
        if not passes_kill_gates(f):
            status = "INVALIDATED"
            bucket = "Invalidated"
            alert_triggered = False
            alert_reason = "Fails Layer 1 Kill Gates (Market Cap / Debt / Cash Flow)"
            notes = f"Watchlist Item Invalidated: {alert_reason}"
            cqs = 0.0
            pas = 0.0
            total = 0.0
        else:
            # Check alerts
            alert_triggered, alert_reason = should_trigger_alert(price_data, fair_val, cqs, pas, trend)
            
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
                
            notes = (
                f"Multi-Layer Analysis Summary:\n"
                f"• CQS (Growth): {cqs:.1f}/10\n"
                f"• PAS (Value): {pas:.1f}/10\n"
                f"• Trend: {trend:.1f}/10\n"
                f"• Fair Value: ₹{fair_val:.1f} (Buy zone: ₹{buy_low:.1f} to ₹{buy_high:.1f})\n"
                f"• Decision: {alert_reason}"
            )
            
        res = ScreenerResult(
            symbol=sym,
            price=price_data.price,
            cqs=cqs,
            pas=pas,
            trend_score=trend,
            total_score=total,
            fair_value=fair_val,
            buy_zone_low=buy_low,
            buy_zone_high=buy_high,
            bucket=bucket,
            status=status,
            notes=notes
        )
        results.append(res)
        
        # Trigger buy alert for ready positions
        if alert_triggered:
            logger.info(f"🌟 Alert Triggered for {sym}! FV={fair_val:.1f}, Price={price_data.price:.1f}. Reason: {alert_reason}")
            scaled_score = int(total * 5.0)
            
            save_wealth_buy_alert(
                symbol=sym,
                alert_price=price_data.price,
                breakout_type="MULTIBAGGER",
                fm_score=scaled_score,
                notes=notes,
                valuation_score=pas,
                momentum_score=int(cqs),
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
