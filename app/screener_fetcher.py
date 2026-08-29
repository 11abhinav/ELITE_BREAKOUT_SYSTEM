"""
screener_fetcher.py
===================
Direct scraper and persistent DB cache for Screener.in financial fundamentals.
Serves as the primary high-precision replacement for Yahoo Finance on Indian Equities (NSE/BSE).

Caches data in local file `data/screener_fundamentals_cache.json` and Postgres DB `screener_cache`
with a 30-day TTL to eliminate repetitive network requests across scan runs.
"""

import os
import json
import time
import logging
import re
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
SCREENER_CACHE_FILE = "data/screener_fundamentals_cache.json"
_screener_ram_cache: Dict[str, Any] = {}
_cache_loaded: bool = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.screener.in/"
}


def _load_screener_cache() -> Dict[str, Any]:
    global _screener_ram_cache, _cache_loaded
    if _cache_loaded:
        return _screener_ram_cache

    _screener_ram_cache = {}
    if os.path.exists(SCREENER_CACHE_FILE):
        try:
            with open(SCREENER_CACHE_FILE, "r") as f:
                _screener_ram_cache = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load Screener local cache file: {e}")

    # Restore from DB if RAM cache is sparse
    if not _screener_ram_cache:
        try:
            from database import download_parquet_from_db
            if download_parquet_from_db("screener_cache", SCREENER_CACHE_FILE):
                with open(SCREENER_CACHE_FILE, "r") as f:
                    _screener_ram_cache = json.load(f)
                logger.info(f"✅ Restored {_screener_ram_cache.get('count', len(_screener_ram_cache))} Screener cache entries from DB.")
        except Exception as e:
            logger.debug(f"DB screener_cache restore skipped: {e}")

    _cache_loaded = True
    return _screener_ram_cache


def _save_screener_cache(cache: Dict[str, Any]):
    os.makedirs(os.path.dirname(SCREENER_CACHE_FILE), exist_ok=True)
    try:
        tmp_path = SCREENER_CACHE_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(cache, f, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, SCREENER_CACHE_FILE)

        # Enqueue background DB upload
        try:
            from durable_upload_queue import enqueue_durable_upload
            enqueue_durable_upload("screener_cache", SCREENER_CACHE_FILE)
        except Exception as up_err:
            logger.debug(f"Screener DB upload enqueue skipped: {up_err}")
    except Exception as e:
        logger.error(f"Failed to save Screener cache: {e}")


def _clean_num(val_str: str) -> Optional[float]:
    if not val_str:
        return None
    try:
        cleaned = re.sub(r"[^\d\.\-]", "", val_str)
        if not cleaned:
            return None
        return float(cleaned)
    except Exception:
        return None


def parse_screener_html(html: str, symbol: str) -> Dict[str, Any]:
    """Parse key financial ratios, balance sheet, and cash flow numbers from Screener.in HTML."""
    data: Dict[str, Any] = {
        "symbol": symbol,
        "fetched_at": datetime.now(IST).isoformat(),
        "source": "SCREENER_IN",
        "cache_tier": "DEEP_V5"
    }

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # 1. Top Ratios List (#top-ratios)
        top_ratios = soup.find("ul", id="top-ratios")
        if top_ratios:
            for li in top_ratios.find_all("li"):
                name_el = li.find("span", class_="name")
                val_el = li.find("span", class_="number")
                if name_el and val_el:
                    name_text = name_el.get_text(strip=True).lower()
                    val_num = _clean_num(val_el.get_text(strip=True))
                    if val_num is not None:
                        if "market cap" in name_text:
                            # Screener market cap is in Cr -> Convert to absolute rupees
                            data["market_cap"] = val_num * 10000000.0
                        elif "book value" in name_text:
                            data["book_value_per_share"] = val_num
                        elif "stock p/e" in name_text or "p/e" in name_text:
                            data["pe_ratio"] = val_num
                        elif "roce" in name_text:
                            data["roce"] = val_num
                        elif "roe" in name_text:
                            data["roe"] = val_num
                        elif "dividend yield" in name_text:
                            data["div_yield"] = val_num

        # Helper to get the most recent annual value from a table section
        def _get_latest_table_row(section_id: str, row_label_pattern: str) -> Optional[float]:
            sec = soup.find("section", id=section_id)
            if not sec:
                return None
            table = sec.find("table")
            if not table:
                return None
            for tr in table.find_all("tr"):
                tds = tr.find_all(["td", "th"])
                if not tds:
                    continue
                label = tds[0].get_text(strip=True).lower()
                if re.search(row_label_pattern, label, re.IGNORECASE):
                    # Get the last numerical cell in the row
                    for cell in reversed(tds[1:]):
                        txt = cell.get_text(strip=True)
                        num = _clean_num(txt)
                        if num is not None:
                            return num
            return None

        # 2. Balance Sheet (#balance-sheet) — Values in ₹ Crores
        share_capital_cr = _get_latest_table_row("balance-sheet", r"share\s+capital")
        reserves_cr = _get_latest_table_row("balance-sheet", r"reserves")
        borrowings_cr = _get_latest_table_row("balance-sheet", r"borrowings")
        total_assets_cr = _get_latest_table_row("balance-sheet", r"total\s+assets")

        if share_capital_cr is not None and reserves_cr is not None:
            # Total Equity = Share Capital + Reserves (in Cr -> Convert to absolute Rupees)
            data["total_equity"] = (share_capital_cr + reserves_cr) * 10000000.0
        elif data.get("market_cap") and data.get("book_value_per_share"):
            # Fallback estimation if table parse fails
            pass

        if borrowings_cr is not None:
            data["total_debt"] = borrowings_cr * 10000000.0

        if total_assets_cr is not None:
            data["total_assets"] = total_assets_cr * 10000000.0

        # 3. Cash Flow (#cash-flow) — Values in ₹ Crores
        cfo_cr = _get_latest_table_row("cash-flow", r"cash\s+from\s+operating")
        if cfo_cr is not None:
            data["operating_cash_flow"] = cfo_cr * 10000000.0

        # 4. Profit & Loss (#profit-loss) — Values in ₹ Crores
        net_profit_cr = _get_latest_table_row("profit-loss", r"net\s+profit")
        if net_profit_cr is not None:
            data["net_profit"] = net_profit_cr * 10000000.0

    except Exception as parse_err:
        logger.warning(f"Error parsing Screener HTML for {symbol}: {parse_err}")

    return data


def fetch_screener_fundamentals(symbol: str, force_refresh: bool = False) -> Dict[str, Any]:
    """
    Fetch fundamental financial data for a stock from Screener.in.
    Returns a dict with total_equity, total_debt, operating_cash_flow, net_profit, ROE, P/E, P/B.
    """
    clean_sym = symbol.split(":")[-1].replace(".NS", "").replace(".BO", "").strip().upper()
    cache = _load_screener_cache()

    now_ts = time.time()
    if not force_refresh and clean_sym in cache:
        cached = cache[clean_sym]
        fetched_at_str = cached.get("fetched_at", "")
        # 30-day TTL (2,592,000 seconds)
        try:
            dt = datetime.fromisoformat(fetched_at_str)
            age_days = (now_ts - dt.timestamp()) / 86400.0
            if age_days < 30 and (cached.get("total_equity") is not None or cached.get("market_cap") is not None):
                return cached
        except Exception:
            pass

    # Attempt Direct HTTP Request to Screener.in (Consolidated first, then Standalone)
    urls = [
        f"https://www.screener.in/company/{clean_sym}/consolidated/",
        f"https://www.screener.in/company/{clean_sym}/"
    ]

    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 200 and "top-ratios" in resp.text:
                parsed = parse_screener_html(resp.text, clean_sym)
                if parsed.get("total_equity") is not None or parsed.get("market_cap") is not None:
                    cache[clean_sym] = parsed
                    _save_screener_cache(cache)
                    logger.info(f"✅ [SCREENER.IN] Direct scrape success for {clean_sym} | Equity={parsed.get('total_equity')}")
                    return parsed
        except Exception as e:
            logger.debug(f"Direct Screener HTTP request failed for {url}: {e}")

    # Fallback to ScraperAPI if Direct HTTP fails
    try:
        from config import SCRAPER_API_KEY
        if SCRAPER_API_KEY:
            target_url = f"https://www.screener.in/company/{clean_sym}/consolidated/"
            payload = {"api_key": SCRAPER_API_KEY, "url": target_url}
            res = requests.get("https://api.scraperapi.com/", params=payload, timeout=20)
            if res.status_code == 200 and "top-ratios" in res.text:
                parsed = parse_screener_html(res.text, clean_sym)
                if parsed.get("total_equity") is not None or parsed.get("market_cap") is not None:
                    cache[clean_sym] = parsed
                    _save_screener_cache(cache)
                    logger.info(f"✅ [SCREENER.IN] ScraperAPI scrape success for {clean_sym} | Equity={parsed.get('total_equity')}")
                    return parsed
    except Exception as sc_err:
        logger.debug(f"ScraperAPI Screener fallback failed for {clean_sym}: {sc_err}")

    # Mark failed attempt so we don't spam network
    fail_entry = {
        "symbol": clean_sym,
        "fetched_at": datetime.now(IST).isoformat(),
        "failed": True,
        "cache_tier": "TV_BASELINE"
    }
    cache[clean_sym] = fail_entry
    _save_screener_cache(cache)
    return fail_entry
