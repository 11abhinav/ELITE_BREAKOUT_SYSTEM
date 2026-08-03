import io
import time
import logging
import threading
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger("constituent_service")

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

def get_nse_session():
    """Returns a curl_cffi Session to bypass NSE WAF, with fallback to requests."""
    try:
        from curl_cffi import requests as cffi_requests
        session = cffi_requests.Session(impersonate="chrome")
        session.headers.update(HTTP_HEADERS)
        return session
    except ImportError:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=3)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(HTTP_HEADERS)
        return session

class ConstituentService:
    _cache_lock = threading.Lock()
    _cached_symbols = None
    _cache_trading_date = None
    
    # Statistics
    hits = 0
    misses = 0
    last_refresh = None
    last_download_ms = 0.0
    symbol_count = 0

    @classmethod
    def fetch_constituents(cls) -> list:
        """Download index lists from NSE and return unique, normalized symbol list with thread-safe caching."""
        current_market_date = datetime.now(ZoneInfo("Asia/Kolkata")).date()

        # 1. Fast path without lock
        if cls._cached_symbols is not None and cls._cache_trading_date == current_market_date:
            cls.hits += 1
            logger.debug(f"✅ Using cached constituents ({cls.symbol_count} symbols) [Hits: {cls.hits}]")
            return list(cls._cached_symbols) # Return a shallow copy of the list

        # 2. Slow path with lock
        with cls._cache_lock:
            # Double-check inside lock
            if cls._cached_symbols is not None and cls._cache_trading_date == current_market_date:
                cls.hits += 1
                logger.debug(f"✅ Using cached constituents ({cls.symbol_count} symbols) [Hits: {cls.hits}]")
                return list(cls._cached_symbols)
            
            cls.misses += 1
            start_t = time.time()
            
            symbols = set()
            session = get_nse_session()
            
            success_count = 0
            
            for name, url in CONSTITUENT_URLS.items():
                try:
                    logger.info(f"📥 Downloading {name} constituents...")
                    
                    response = None
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            response = session.get(url, timeout=30)
                            if response.status_code == 200:
                                break
                        except Exception as e:
                            # Try Crawlora fallback before sleeping
                            from config import CRAWLORA_API_KEY
                            if CRAWLORA_API_KEY:
                                try:
                                    c_resp = requests.get('https://api.crawlora.net/v1/scrape', params={'api_key': CRAWLORA_API_KEY, 'url': url}, timeout=30)
                                    if c_resp.status_code == 200:
                                        response = c_resp
                                        logger.info(f"✅ Downloaded {name} via Crawlora fallback.")
                                        break
                                except Exception as crawlora_err:
                                    logger.debug(f"Crawlora fallback failed for {name}: {crawlora_err}")

                            if attempt == max_retries - 1:
                                logger.error(f"Failed to download {name} constituents after {max_retries} attempts: {e}")
                                try:
                                    from database import insert_notification
                                    insert_notification(
                                        notif_type="error",
                                        title=f"🚨 NSE API Timeout ({name})",
                                        message=f"Failed to fetch {url}. The NSE server is throttling or down. Error: {str(e)[:200]}"
                                    )
                                except Exception:
                                    pass
                                raise
                            
                            backoff = (2 ** attempt) * 5
                            logger.warning(f"⚠️ NSE API error for {name} (attempt {attempt+1}): {e}. Retrying in {backoff}s...")
                            time.sleep(backoff)
                    
                    if response and response.status_code == 200:
                        df = pd.read_csv(io.StringIO(response.text))
                        if "Symbol" in df.columns:
                            for sym in df["Symbol"].dropna().unique():
                                clean_sym = str(sym).strip()
                                if clean_sym:
                                    symbols.add(clean_sym)
                            logger.info(f"✅ Loaded {len(df)} constituents for {name}.")
                            success_count += 1
                    else:
                        logger.warning(f"⚠️ Failed to fetch {name}: HTTP {response.status_code if response else 'Unknown'}")
                except Exception as e:
                    logger.warning(f"⚠️ Error fetching {name}: {e}")
                
                time.sleep(2.5)
            
            if success_count == 0:
                logger.error("❌ Failed to download ANY constituents from NSE.")
                if cls._cached_symbols is not None:
                    logger.warning("⚠️ Retaining previous constituent cache due to complete network failure.")
                    return list(cls._cached_symbols)
                else:
                    return []
                    
            from daily_builder import SYMBOL_CORRECTIONS
            normalized = []
            for s in symbols:
                if s in SYMBOL_CORRECTIONS:
                    clean = SYMBOL_CORRECTIONS[s]
                else:
                    clean = s
                if "DUMMY" in clean.upper():
                    continue
                normalized.append(clean)
                
            sorted_symbols = sorted(normalized)
            
            cls.last_download_ms = (time.time() - start_t) * 1000
            cls.symbol_count = len(sorted_symbols)
            cls.last_refresh = time.time()
            
            logger.info(f"🎯 Total unique constituent symbols fetched: {cls.symbol_count}")
            
            cls._cached_symbols = sorted_symbols
            cls._cache_trading_date = current_market_date
            
            return list(cls._cached_symbols)

def fetch_constituents() -> list:
    """Helper method to easily fetch from the service."""
    return ConstituentService.fetch_constituents()
