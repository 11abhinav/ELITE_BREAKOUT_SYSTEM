import logging
import requests
import pandas as pd
import io
import time
import re
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

BULK_URL = "https://nsearchives.nseindia.com/content/equities/bulk.csv"
BLOCK_URL = "https://nsearchives.nseindia.com/content/equities/block.csv"

# Keywords that indicate institutional/fund buying
INSTITUTIONAL_KEYWORDS = [
    "FUND", "CAPITAL", "MANAGEMENT", "ASSET", "INVESTMENT", "LLP", 
    "HOLDING", "TRUST", "VENTURES", "GLOBAL", "INDIA", "PARTNERS", 
    "EQUITY", "SECURITIES", "WEALTH", "ADVISORS", "LTD", "LIMITED"
]

# Keywords that usually indicate retail or individual names to exclude
RETAIL_KEYWORDS = ["HUF", "INDIVIDUAL"]

def _get_robust_session():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Referer": "https://www.nseindia.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    }
    
    try:
        from curl_cffi import requests as cffi_requests
        session = cffi_requests.Session(impersonate="chrome120")
        session.headers.update(headers)
        return session
    except ImportError:
        session = requests.Session()
        retry = Retry(
            total=5,
            read=5,
            connect=5,
            backoff_factor=1.5,
            status_forcelist=(500, 502, 503, 504),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(headers)
        return session

def _is_institutional(client_name: str) -> bool:
    if not isinstance(client_name, str):
        return False
    name = client_name.upper()
    for retail in RETAIL_KEYWORDS:
        if retail in name:
            return False
    
    # Needs to have at least one institutional keyword
    for inst in INSTITUTIONAL_KEYWORDS:
        if inst in name:
            return True
            
    # Or if the name is very long, it's often a company
    if len(name.split()) >= 3:
        return True
        
    return False

def get_institutional_buys() -> dict[str, list[str]]:
    """
    Fetches today's (or latest) bulk and block deals from NSE.
    Returns a dict mapping Symbol -> list of Institutional Buyer Names.
    """
    session = _get_robust_session()
    buys = {}
    
    def process_url(url, deal_type):
        for attempt in range(3):
            try:
                logger.info(f"🔄 Attempting to fetch {deal_type} deals from {url} (Attempt {attempt+1})")
                r = session.get(url, timeout=15)
                
                # If direct request fails or gets HTML block page, try ScraperAPI
                if r.status_code != 200 or "<html" in r.text.lower() or "<!doctype" in r.text.lower():
                    from pledge_scraper import get_scraper_api_key, mark_key_exhausted_today
                    scraper_key = get_scraper_api_key()
                    if scraper_key:
                        try:
                            logger.info(f"🌐 [SCRAPERAPI BACKUP] Requesting {deal_type} deals: {url}")
                            s_resp = requests.get("http://api.scraperapi.com", params={"api_key": scraper_key, "url": url}, timeout=30)
                            if s_resp is not None and s_resp.status_code in (401, 403, 429):
                                mark_key_exhausted_today(scraper_key)
                            elif s_resp is not None and s_resp.status_code == 200:
                                logger.info(f"✅ [SCRAPERAPI SUCCESS] HTTP 200 for {deal_type} deals ({len(s_resp.content)} bytes)")
                                r = s_resp
                        except Exception as scraper_err:
                            logger.warning(f"❌ [SCRAPERAPI ERROR] ScraperAPI fetch failed for {deal_type}: {scraper_err}")

                if r.status_code == 200:
                    text = r.text.strip()
                    if not text or "NO RECORDS" in text or len(text.splitlines()) < 2:
                        return
                        
                    if "<html" in text.lower() or "<body" in text.lower() or "<!doctype" in text.lower():
                        logger.warning(f"⚠️ NSE returned an HTML block page instead of {deal_type} CSV (Attempt {attempt+1})")
                        if attempt < 2: time.sleep(2.5)
                        continue
                    
                    df = pd.read_csv(io.StringIO(text))
                    df.columns = [c.strip().upper() for c in df.columns]
                    
                    if "SYMBOL" not in df.columns or "BUY/SELL" not in df.columns or "CLIENT NAME" not in df.columns:
                        return
                    
                    # Filter for buys
                    df_buy = df[df["BUY/SELL"].astype(str).str.upper().isin(["BUY", "B"])]
                    
                    for _, row in df_buy.iterrows():
                        symbol = str(row["SYMBOL"]).strip()
                        client = str(row["CLIENT NAME"]).strip()
                        
                        if _is_institutional(client):
                            if symbol not in buys:
                                buys[symbol] = []
                            buys[symbol].append(f"[{deal_type}] {client}")
                    break # Success, break retry loop
                else:
                    if attempt < 2: time.sleep(2.5)
            except Exception as e:
                logger.warning(f"Failed to fetch {deal_type} deals (Attempt {attempt+1}): {e}")
                if attempt < 2: 
                    time.sleep(2.5)
                else:
                    try:
                        from push_service import send_push_to_all
                        send_push_to_all("⚠️ NSE API ERROR", f"Institutional {deal_type} deals fetch failed: {str(e)[:100]}")
                    except Exception: pass

    try:
        # Hit main page once for cookies
        session.get("https://www.nseindia.com", timeout=10)
    except Exception: pass
    time.sleep(2.5)
    
    process_url(BULK_URL, "BULK")
    time.sleep(2.5)
    process_url(BLOCK_URL, "BLOCK")
    
    logger.info(f"🏦 Found institutional buys in {len(buys)} stocks from bulk/block deals.")
    return buys

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    buys = get_institutional_buys()
    for sym, clients in list(buys.items())[:10]:
        print(f"{sym}: {clients}")
