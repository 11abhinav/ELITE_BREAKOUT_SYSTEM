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

from database import get_connection, save_wealth_buy_alert
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

@dataclass
class ScreenerResult:
    symbol: str
    price: float
    cqs: float
    pas: float
    label: str
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
                        bse_code VARCHAR(20),
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
    """Download historical price/volume data in bulk for all tickers."""
    ticker_names = [f"{sym}.NS" for sym in symbols]
    logger.info(f"📥 Batch downloading 1y history for {len(ticker_names)} tickers...")
    
    try:
        df = yf.download(ticker_names, period="1y", interval="1d", group_by="ticker", progress=False)
        
        results = {}
        for sym in symbols:
            ticker_name = f"{sym}.NS"
            try:
                if ticker_name not in df.columns.levels[0]:
                    continue
                ticker_df = df[ticker_name].dropna(subset=["Close"])
                if ticker_df.empty:
                    continue
                
                close_price = float(ticker_df.iloc[-1]["Close"])
                
                # 1-day change percent
                if len(ticker_df) >= 2:
                    prev_close = float(ticker_df.iloc[-2]["Close"])
                    change_pct = ((close_price - prev_close) / prev_close) * 100.0
                else:
                    change_pct = 0.0
                
                low_52w = float(ticker_df["Close"].min())
                high_52w = float(ticker_df["Close"].max())
                
                # 20-day median trading turnover (average daily turnover)
                recent_20 = ticker_df.tail(20)
                if not recent_20.empty and "Volume" in recent_20.columns:
                    avg_turnover = float((recent_20["Volume"] * recent_20["Close"]).mean())
                else:
                    avg_turnover = 0.0
                
                results[sym] = StockPriceData(
                    symbol=sym,
                    price=close_price,
                    change_pct=change_pct,
                    low_52w=low_52w,
                    high_52w=high_52w,
                    turnover_20d=avg_turnover
                )
            except Exception as e:
                logger.debug(f"Error parsing downloaded data for {sym}: {e}")
                
        logger.info(f"✅ Successfully parsed price data for {len(results)}/{len(symbols)} tickers.")
        return results
    except Exception as e:
        logger.error(f"❌ Batch price download failed: {e}")
        return {}

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
                sector=entry["sector"]
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
                    sector=info.get("sector", "Unknown")
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
    """Instant rejection checks: Mcap < 500Cr, D/E > 1.5, or Operating Cash Flow < 0."""
    if f.market_cap is None or float(f.market_cap) < 5000000000: # ₹500 Cr
        return False
        
    # Debt/Equity check (Strict: reject if > 1.5)
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
    """Calculate Company Quality Score (CQS) out of 10 points (0 pts if missing)."""
    score = 0.0
    
    # 1. ROE (Max 3 pts: >=20% = 3, >=12% = 1)
    if f.roe is not None:
        roe_val = float(f.roe)
        if roe_val >= 0.20:
            score += 3.0
        elif roe_val >= 0.12:
            score += 1.0
            
    # 2. Debt/Equity (Max 2 pts: <=0.3 = 2, <=0.7 = 1)
    # Strict: missing D/E scores 0 points
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
    """Compute median P/E and P/B per sector across shortlist universe."""
    pe_groups = {}
    pb_groups = {}
    
    for f in fundamentals_list:
        if not f or not f.sector or f.sector == "Unknown":
            continue
        if f.pe is not None and f.pe > 0:
            pe_groups.setdefault(f.sector, []).append(f.pe)
        if f.pb is not None and f.pb > 0:
            pb_groups.setdefault(f.sector, []).append(f.pb)
            
    medians = {}
    all_sectors = set(pe_groups.keys()).union(pb_groups.keys())
    
    for sector in all_sectors:
        pes = pe_groups.get(sector, [])
        pbs = pb_groups.get(sector, [])
        medians[sector] = {
            "median_pe": float(np.median(pes)) if len(pes) >= 3 else None,
            "median_pb": float(np.median(pbs)) if len(pbs) >= 3 else None
        }
        
    return medians

def calculate_sector_aware_pas(f: StockFundamentals, price_data: StockPriceData, medians: dict) -> float:
    """Calculate Sector-Aware Price Attractiveness Score (PAS) out of 10 points."""
    score = 0.0
    
    # 1. P/E vs Sector (Max 4 pts: <= sector median = 4, <= 1.2 * sector median = 2)
    if f.pe is not None and f.pe > 0:
        sector_med = medians.get(f.sector, {}).get("median_pe") if f.sector else None
        if sector_med is not None:
            if f.pe <= sector_med:
                score += 4.0
            elif f.pe <= sector_med * 1.2:
                score += 2.0
        else:
            # Fallback to absolute
            if f.pe <= 20.0:
                score += 4.0
            elif f.pe <= 35.0:
                score += 2.0
                
    # 2. P/B vs Sector (Max 3 pts: <= sector median = 3, <= 1.2 * sector median = 1)
    if f.pb is not None and f.pb > 0:
        sector_med = medians.get(f.sector, {}).get("median_pb") if f.sector else None
        if sector_med is not None:
            if f.pb <= sector_med:
                score += 3.0
            elif f.pb <= sector_med * 1.2:
                score += 1.0
        else:
            # Fallback to absolute
            if f.pb <= 2.5:
                score += 3.0
            elif f.pb <= 4.5:
                score += 1.0
                
    # 3. Dividend Yield (Max 1 pt: >=0.5% = 1)
    if f.div_yield is not None and float(f.div_yield) >= 0.005:
        score += 1.0
        
    # 4. Near 52W Low (Max 2 pts: within 20% = 2)
    if price_data.price > 0 and price_data.low_52w > 0:
        if price_data.price <= price_data.low_52w * 1.20:
            score += 2.0
            
    return round(score, 1)

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

def save_results_to_db(results: list):
    """Save scanned scores and watchlist items to DB in a secure block transaction."""
    if not results:
        return
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for r in results:
                    # Update stockupdates.prices
                    legacy_score = int((r.cqs + r.pas) * 2.5)
                    cur.execute("""
                        INSERT INTO stockupdates.prices (symbol, latest_price, fundamental_score, quality_score, value_score, last_fetched)
                        VALUES (%s, %s, NULL, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (symbol) DO UPDATE SET
                            latest_price = EXCLUDED.latest_price,
                            fundamental_score = EXCLUDED.fundamental_score,
                            quality_score = EXCLUDED.quality_score,
                            value_score = EXCLUDED.value_score,
                            last_fetched = CURRENT_TIMESTAMP;
                    """, (r.symbol.upper(), r.price, r.cqs, r.pas))
                    
                    # Auto-inject watchlist
                    if r.cqs >= 6.0 and (r.cqs + r.pas) >= 11.0:
                        cur.execute("""
                            INSERT INTO stockupdates.watchlist (symbol)
                            VALUES (%s)
                            ON CONFLICT (symbol) DO NOTHING;
                        """, (r.symbol.upper(),))
            conn.commit()
        logger.info(f"✅ Successfully persisted {len(results)} stock scores and qualifiers to DB.")
    except Exception as e:
        logger.error(f"❌ Failed to save results to database: {e}")

def format_telegram_message(categorized_stocks: dict) -> list:
    """Format categorized stocks into chunked Telegram messages (HTML)."""
    messages = []
    current_msg = "<b>🚀 SUNDAY MULTIBAGGER SCREENER SUMMARY</b>\n"
    current_msg += f"<i>Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}</i>\n"
    current_msg += "========================================\n\n"
    
    has_results = False
    
    for label, stocks in categorized_stocks.items():
        if not stocks:
            continue
        has_results = True
        
        section_text = f"<b>{label}</b> ({len(stocks)} stocks):\n"
        for item in sorted(stocks, key=lambda x: x['cqs'] + x['pas'], reverse=True):
            sym = item['symbol']
            cqs = item['cqs']
            pas = item['pas']
            price = item['price']
            total = cqs + pas
            
            injected_marker = " *Auto-Injected*" if (cqs >= 6.0 and total >= 11.0) else ""
            line = f"• <b>{sym}</b> (₹{price:.1f}) | CQS: <b>{cqs:.1f}</b> | PAS: <b>{pas:.1f}</b> | Total: <b>{total:.1f}/20</b>{injected_marker}\n"
            
            if len(current_msg) + len(section_text) + len(line) > 3900:
                messages.append(current_msg)
                current_msg = "<b>🚀 MULTIBAGGER SCREENER SUMMARY (Cont.)</b>\n\n"
                
            section_text += line
            
        current_msg += section_text + "\n"

    if not has_results:
        current_msg += "ℹ️ No stocks qualified for multibagger categorization this week.\n"
        messages.append(current_msg)
    else:
        current_msg += "<i>Injection Rule: CQS &gt;= 6 AND (CQS + PAS) &gt;= 11.</i>"
        messages.append(current_msg)
        
    return messages

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
        
    # 2. Phase 1: Batch Download Price & Volume Metrics
    price_data_map = batch_download_market_data(symbols)
    if not price_data_map:
        logger.error("❌ Failed to download batch price data. Aborting scan.")
        return
        
    # Apply cheap filters to build shortlist:
    # Exclude penny stocks (< ₹10) and illiquid stocks (turnover_20d < ₹10 Lakhs = 1,000,000 Rupees)
    shortlist_candidates = []
    for sym, price_data in price_data_map.items():
        if price_data.price < 10.0:
            continue
        if price_data.turnover_20d < 1000000.0: # ₹10 Lakhs
            continue
        shortlist_candidates.append(price_data)
        
    # Sort by turnover descending and take a shortlist of top 120 most liquid
    shortlist_candidates = sorted(shortlist_candidates, key=lambda x: x.turnover_20d, reverse=True)
    shortlist = shortlist_candidates[:120]
    logger.info(f"📋 Shortlisted {len(shortlist)}/{len(price_data_map)} liquid stocks for fundamental screening.")
    
    # 3. Phase 2: Fetch Fundamentals (using cache if available, or yfinance query)
    fundamentals_list = []
    shortlist_symbols = [p.symbol for p in shortlist]
    
    # Single-threaded or multi-threaded loop to fetch details safely
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for p in shortlist:
            sym = p.symbol
            # Check cache first
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
                        "sector": fund.sector
                    }
            except Exception as e:
                logger.error(f"Error fetching fundamentals for {sym}: {e}")
                
    # Save updated cache to JSON file
    save_fundamentals_cache(cache)
    
    # Filter fundamentals matching Kill Gates
    valid_fundamentals = [f for f in fundamentals_list if passes_kill_gates(f)]
    logger.info(f"🛡️ {len(valid_fundamentals)}/{len(fundamentals_list)} shortlisted stocks passed Layer 1 Kill Gates.")
    
    # 4. Phase 3: Sector-aware PAS scoring & DB update
    sector_medians = compute_sector_medians(valid_fundamentals)
    
    results = []
    categorized_stocks = {
        "🚀 PRIME MULTIBAGGER CANDIDATE": [],
        "💎 HIGH QUALITY — FAIR ENTRY": [],
        "🏆 GREAT BUSINESS — WAIT FOR DIP": [],
        "💰 VALUE BUY — DECENT QUALITY": [],
        "🟡 WATCHLIST CANDIDATE": []
    }
    
    for f in valid_fundamentals:
        sym = f.symbol
        price_data = price_data_map[sym]
        
        # Calculate CQS & PAS
        cqs = calculate_cqs(f)
        pas = calculate_sector_aware_pas(f, price_data, sector_medians)
        total = cqs + pas
        
        label = get_label(cqs, pas)
        
        # Build detailed notes string
        de_ratio = (f.debt_equity / 100.0) if f.debt_equity and f.debt_equity > 10.0 else (f.debt_equity or 0.0)
        roe_val = (f.roe or 0.0)
        rev_g = (f.revenue_growth or 0.0)
        earn_g = (f.earnings_growth or 0.0)
        op_m = (f.operating_margin or 0.0)
        pe = (f.pe or 0.0)
        pb = (f.pb or 0.0)
        dy = (f.div_yield or 0.0)
        
        low_dist_pct = ((price_data.price - price_data.low_52w) / price_data.low_52w * 100.0) if price_data.low_52w > 0 else 0.0
        
        notes = (
            f"Multibagger Scanner Qualifier details:\n"
            f"• CQS: {cqs}/10 (ROE: {roe_val*100:.1f}%, D/E: {de_ratio:.2f}, RevG: {rev_g*100:.1f}%, EarnG: {earn_g*100:.1f}%, OPM: {op_m*100:.1f}%)\n"
            f"• PAS: {pas}/10 (P/E: {pe:.1f}, P/B: {pb:.1f}, DivY: {dy*100:.1f}%, 52W Low: +{low_dist_pct:.1f}%)\n"
            f"• Sector: {f.sector}"
        )
        
        results.append(ScreenerResult(
            symbol=sym,
            price=price_data.price,
            cqs=cqs,
            pas=pas,
            label=label,
            notes=notes
        ))
        
        # Trigger buy alert for qualifiers (CQS >= 6 AND CQS + PAS >= 11)
        if cqs >= 6.0 and total >= 11.0:
            logger.info(f"🌟 Stock {sym} QUALIFIES! CQS={cqs:.1f}, PAS={pas:.1f}, Total={total:.1f}/20")
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
            
        if label:
            categorized_stocks[label].append({
                'symbol': sym,
                'price': price_data.price,
                'cqs': cqs,
                'pas': pas
            })
            
    # Persist in bulk
    save_results_to_db(results)
    
    # 5. Format and queue Telegram updates
    logger.info(f"📢 Formatting Telegram messages for {len(results)} qualifiers...")
    telegram_msgs = format_telegram_message(categorized_stocks)
    for msg in telegram_msgs:
        queue_telegram_message(msg)
        
    logger.info("✅ Multibagger Scanner execution finished.")
