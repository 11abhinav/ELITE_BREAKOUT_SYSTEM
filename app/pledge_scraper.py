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



def get_scraper_api_key() -> str:
    """Parse comma-separated SCRAPERAPI_KEY env var and return the first non-exhausted one."""
    keys_str = os.getenv("SCRAPERAPI_KEY", "")
    if not keys_str:
        return ""
    keys = [k.strip() for k in keys_str.split(',') if k.strip()]
    
    for k in keys:
        if not _is_key_exhausted_today(k):
            return k
            
    # If all are exhausted, return empty string so the worker knows to stop
    return ""

def _get_exhausted_keys_file():
    return os.path.join(DATA_DIR, "exhausted_keys.json")

def _is_key_exhausted_today(key: str) -> bool:
    try:
        fpath = _get_exhausted_keys_file()
        if not os.path.exists(fpath): return False
        with open(fpath, 'r') as f:
            data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        return data.get(key) == today
    except Exception:
        return False

def mark_key_exhausted_today(key: str):
    try:
        fpath = _get_exhausted_keys_file()
        data = {}
        if os.path.exists(fpath):
            with open(fpath, 'r') as f:
                data = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        data[key] = today
        data = {k: v for k, v in data.items() if v == today}
        with open(fpath, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.debug(f"Failed to write exhausted keys cache: {e}")

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

    # 2. Return 0.0 if not in database
    # We DO NOT fallback to hitting the API synchronously because it halts the scanner.
    # The pledge_worker daemon will pick up the missing stock tonight.
    return 0.0
