import os
import requests
import logging
from bs4 import BeautifulSoup
import re
import random
import json
from datetime import datetime
from functools import lru_cache
from tenacity import retry, stop_after_attempt, wait_exponential
from database import get_connection, init_db
from data_fetch_status import mark_success, mark_failure
from config import DATA_DIR

logger = logging.getLogger(__name__)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True
)
def _fetch_pledge_from_api(api_key: str, target_url: str) -> requests.Response:
    """Fetch pledge data from ScraperAPI with retry logic."""
    payload = {
        'api_key': api_key,
        'url': target_url,
        'render': 'false'
    }
    return requests.get('https://api.scraperapi.com/', params=payload, timeout=10)

def get_scraper_api_key() -> str:
    """Parse comma-separated SCRAPERAPI_KEY env var and return a random one."""
    keys_str = os.getenv("SCRAPERAPI_KEY", "")
    if not keys_str:
        return ""
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    return random.choice(keys) if keys else ""

def _get_fail_file():
    return os.path.join(DATA_DIR, "pledge_failures.json")

def _is_failed_today(symbol: str) -> bool:
    try:
        fail_file = _get_fail_file()
        if not os.path.exists(fail_file): return False
        with open(fail_file, 'r') as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        return data.get(symbol) == today
    except Exception:
        return False

def _mark_failed_today(symbol: str):
    try:
        fail_file = _get_fail_file()
        data = {}
        if os.path.exists(fail_file):
            with open(fail_file, 'r') as f:
                data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        data[symbol] = today
        # Clean up old entries to prevent file from growing indefinitely
        data = {k: v for k, v in data.items() if v == today}
        with open(fail_file, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.debug(f"Failed to write pledge failure cache: {e}")

@lru_cache(maxsize=5000)
def fetch_promoter_pledge(symbol: str):
    """
    Fetches the promoter pledge percentage for a given NSE symbol.
    Primarily relies on the PostgreSQL cache populated by the pledge_worker.
    Makes ONE quick fallback attempt if cache is missing.
    """
    init_db()

    # 0. Check Daily Negative Cache
    if _is_failed_today(symbol):
        return 0.0

    # 1. Check DB Cache
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT pledge_pct 
                    FROM promoter_pledge_cache 
                    WHERE symbol = %s 
                      AND updated_at >= NOW() - INTERVAL '30 days'
                """, (symbol,))
                row = cur.fetchone()
                if row:
                    return float(row[0])
    except Exception as e:
        logger.warning(f"Database error checking pledge cache for {symbol}: {e}")

    # 2. Fast Fallback Attempt (One-Time)
    # The pledge_worker will properly resolve broken URLs asynchronously.
    api_key = get_scraper_api_key()
    if not api_key:
        return 0.0

    fallback_urls = {
        'HINDCOPPER': 'https://trendlyne.com/equity/551/HINDCOPPER/hindustan-copper-ltd/'
    }
    
    target_url = fallback_urls.get(symbol, f"https://trendlyne.com/stock/{symbol}/")
    
    pledge_val = None
    try:
        # Use retry-decorated API call for robustness
        res = _fetch_pledge_from_api(api_key, target_url)
        if res.status_code == 200:
            match = re.search(r'pledge[^\d]{1,30}?(\d+\.?\d*)\s*%', res.text, re.IGNORECASE)
            if match:
                pledge_val = float(match.group(1))
            else:
                soup = BeautifulSoup(res.text, 'html.parser')
                for div in soup.find_all(['div', 'span', 'td']):
                    if 'pledge' in div.text.lower() and '%' in div.text:
                        m = re.search(r'(\d+\.?\d*)\s*%', div.text)
                        if m:
                            pledge_val = float(m.group(1))
                            break
            try:
                mark_success('scraperapi')
            except Exception:
                pass
            
            if pledge_val is None:
                _mark_failed_today(symbol)
                
        elif res.status_code == 404:
            try:
                mark_failure('scraperapi', f'Fast fetch 404/Failed for {symbol} URL={target_url}')
            except Exception:
                pass
            _mark_failed_today(symbol)
        else:
            try:
                mark_failure('scraperapi', f'HTTP {res.status_code} for {symbol} URL={target_url}')
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Fast pledge fetch failed for {symbol}: {e}")
        try:
            mark_failure('scraperapi', f"Fast fetch Exception: {e} URL={target_url}")
        except Exception:
            pass

    # We DO NOT save to the database here. 
    # That is the sole responsibility of pledge_worker.py to prevent race conditions.
    if pledge_val is None:
        return 0.0
    return pledge_val
