"""
app/nse_pledge_fetcher.py
=========================
[RULE 67 CHANGE-RATIONALE: NSE_OFFICIAL_PLEDGE_INGESTION_v1.0]
Official Exchange Ingestion Engine for Promoter Pledge & Encumbrance Data.
Fetches bulk daily/quarterly pledged data directly from the National Stock Exchange of India (NSE)
portal via official endpoint (/api/corporate-pledgedata?csv=true).

Replaces legacy 750-roundtrip ScraperAPI web-scraping with a single authoritative bulk download:
  1. Primary Source: NSE Corporate Filings Pledged Data (Depository System Data via NSDL/CDSL)
  2. TLS Impersonation: curl_cffi with Chrome 120 profile (zero proxy fees, zero 3rd-party keys)
  3. Provenance: SOURCE=NSE, SOURCE_TYPE=OFFICIAL_EXCHANGE
  4. Reliability Contracts: NSE_FETCH_FAILED, NSE_SCHEMA_CHANGED, SYMBOL_UNMAPPED
"""

import os
import csv
import io
import re
import json
import logging
import uuid
from datetime import datetime, date
from typing import Dict, List, Tuple, Any, Optional
from zoneinfo import ZoneInfo

from config import DATA_DIR

logger = logging.getLogger(__name__)
IST_ZONE = ZoneInfo("Asia/Kolkata")

NSE_HOME_URL = "https://www.nseindia.com"
NSE_PLEDGED_PAGE_URL = "https://www.nseindia.com/companies-listing/corporate-filings-pledged-data"
NSE_PLEDGED_CSV_URL = "https://www.nseindia.com/api/corporate-pledgedata?csv=true"

# Expected core column names in NSE bulk pledged CSV
EXPECTED_CORE_HEADERS = [
    "NAME OF COMPANY",
    "TOTAL NO. OF ISSUED SHARES A+B+C",
    "TOTAL PROMOTER HOLDING NO. OF SHARES (A)",
    "TOTAL PROMOTER HOLDING % A /(A+B+C)",
    "PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER NO. OF SHARES (X)",
    "PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER % OF PROMOTER SHARES (X/A)"
]

_NAME_TO_SYMBOL_MAP: Optional[Dict[str, str]] = None
_NORM_NAME_TO_SYMBOL_MAP: Optional[Dict[str, str]] = None


def _normalize_company_name(name: str) -> str:
    """
    Normalizes company names for fuzzy fallback matching:
    - Uppercase and strip whitespace
    - Remove legal entity descriptors (LIMITED, LTD, PVT, PRIVATE, CORP, INDIA, PLC, etc.)
    - Strip all punctuation and special characters
    """
    if not name:
        return ""
    cleaned = name.upper()
    cleaned = re.sub(r'[\.,\-\(\)\/\&\+\'"]', ' ', cleaned)
    tokens = cleaned.split()
    noise_words = {
        "LIMITED", "LTD", "PVT", "PRIVATE", "CORP", "CORPORATION", "CO",
        "INC", "HOLDINGS", "HOLDING", "ENTERPRISES", "INDUSTRIES", "INDIA"
    }
    filtered = [t for t in tokens if t not in noise_words]
    return "".join(filtered) if filtered else "".join(tokens)


def _load_symbol_name_directory() -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Loads company name -> symbol mapping from local master equities caches:
    1. data/nse_master_equities.json
    2. data/nse_bse_master_universe.json (supplemental fallback)
    """
    global _NAME_TO_SYMBOL_MAP, _NORM_NAME_TO_SYMBOL_MAP
    if _NAME_TO_SYMBOL_MAP is not None and _NORM_NAME_TO_SYMBOL_MAP is not None:
        return _NAME_TO_SYMBOL_MAP, _NORM_NAME_TO_SYMBOL_MAP

    exact_map: Dict[str, str] = {}
    norm_map: Dict[str, str] = {}

    paths = [
        os.path.join(DATA_DIR, "nse_master_equities.json"),
        os.path.join(DATA_DIR, "nse_bse_master_universe.json"),
    ]

    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    for sym, info in data.items():
                        cname = (
                            info.get("company_name")
                            or info.get("name")
                            or info.get("Name")
                            or ""
                        ).strip().upper()
                        clean_sym = sym.replace("NSE:", "").replace("BSE:", "").strip().upper()
                        if cname and clean_sym:
                            exact_map[cname] = clean_sym
                            norm_key = _normalize_company_name(cname)
                            if norm_key:
                                norm_map[norm_key] = clean_sym
        except Exception as exc:
            logger.warning(f"Failed to load symbol directory from {p}: {exc}")

    _NAME_TO_SYMBOL_MAP = exact_map
    _NORM_NAME_TO_SYMBOL_MAP = norm_map
    logger.info(f"📋 Loaded {len(exact_map)} exact and {len(norm_map)} normalized NSE company-symbol pairs.")
    return exact_map, norm_map


def _clean_numeric(val: Any) -> Optional[float]:
    """Safely converts string or number to float, removing commas, whitespace, or sentinel values."""
    if val is None:
        return None
    val_str = str(val).replace(",", "").strip()
    if not val_str or val_str in ("-", "NA", "N/A", "NONE", "null"):
        return None
    try:
        return float(val_str)
    except ValueError:
        return None


def _clean_int(val: Any) -> Optional[int]:
    """Safely converts string or number to integer."""
    flt = _clean_numeric(val)
    if flt is None:
        return None
    return int(round(flt))


def _parse_broadcast_date(dt_str: str) -> date:
    """Parses NSE broadcast date formats like '04-Sep-2026 16:31:29' or '31-03-2015' into date."""
    if not dt_str:
        return datetime.now(IST_ZONE).date()
    dt_str = dt_str.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(dt_str[:19].strip(), fmt).date()
        except ValueError:
            continue
    return datetime.now(IST_ZONE).date()


def download_official_nse_pledged_csv() -> Tuple[str, Optional[str]]:
    """
    Downloads the official bulk Pledged Data CSV from NSE using curl_cffi with Chrome 120 TLS fingerprint.
    Returns (csv_text, error_message).
    """
    try:
        from curl_cffi import requests
    except ImportError:
        logger.error("❌ curl_cffi is required for NSE official ingestion but is not installed.")
        return "", "curl_cffi not installed"

    session = requests.Session(impersonate="chrome120")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_PLEDGED_PAGE_URL,
    }

    try:
        # Step 1: Handshake with NSE homepage to acquire initial anti-bot session cookies
        r_home = session.get(NSE_HOME_URL, headers=headers, timeout=15)
        if r_home.status_code != 200:
            logger.warning(f"⚠️ NSE home handshake status {r_home.status_code}")

        # Step 2: Establish corporate filings page context
        session.get(NSE_PLEDGED_PAGE_URL, headers=headers, timeout=15)

        # Step 3: Fetch the bulk CSV payload
        r_csv = session.get(NSE_PLEDGED_CSV_URL, headers=headers, timeout=20)
        if r_csv.status_code != 200:
            err = f"NSE returned HTTP {r_csv.status_code}"
            logger.error(f"❌ [NSE_FETCH_FAILED] {err}")
            return "", err

        csv_text = r_csv.text.lstrip("\ufeff")  # Strip UTF-8 BOM if present
        if not csv_text or "NAME OF COMPANY" not in csv_text:
            err = "NSE CSV payload empty or missing expected headers"
            logger.error(f"❌ [NSE_SCHEMA_CHANGED] {err}")
            return "", err

        logger.info(f"✅ [NSE_PLEDGE_DOWNLOAD] Successfully downloaded official NSE CSV ({len(csv_text)} bytes)")
        return csv_text, None

    except Exception as exc:
        err = f"Exception downloading NSE CSV: {exc}"
        logger.exception(f"❌ [NSE_FETCH_FAILED] {err}")
        return "", err


def parse_nse_pledged_csv(csv_text: str) -> Dict[str, Any]:
    """
    Parses the NSE official Pledged Data CSV and maps each company row to an NSE symbol.
    Returns:
      {
        "snapshot_id": str,
        "snapshot_date": date,
        "total_rows": int,
        "matched_count": int,
        "unmapped_count": int,
        "records": list[dict],
        "unmapped_companies": list[str]
      }
    """
    exact_map, norm_map = _load_symbol_name_directory()
    reader = csv.DictReader(io.StringIO(csv_text))
    
    # Validate schema
    if not reader.fieldnames:
        raise ValueError("NSE_SCHEMA_CHANGED: Empty headers in CSV")
    
    missing_headers = [h for h in EXPECTED_CORE_HEADERS if h not in reader.fieldnames]
    if missing_headers:
        raise ValueError(f"NSE_SCHEMA_CHANGED: Missing required headers: {missing_headers}")

    records = []
    unmapped_companies = []
    snapshot_id = f"nse_pledge_{datetime.now(IST_ZONE).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    snapshot_date = datetime.now(IST_ZONE).date()

    for row in reader:
        company_name = row.get("NAME OF COMPANY", "").strip()
        if not company_name:
            continue

        cname_upper = company_name.upper()
        # 1. Exact Match
        sym = exact_map.get(cname_upper)
        # 2. Normalized Fuzzy Match
        if not sym:
            norm_key = _normalize_company_name(cname_upper)
            sym = norm_map.get(norm_key)

        if not sym:
            unmapped_companies.append(company_name)
            continue

        # Extract core encumbrance metrics
        # Percent of promoter shares encumbered (X/A)
        enc_pct = _clean_numeric(row.get("PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER % OF PROMOTER SHARES (X/A)"))
        if enc_pct is None:
            enc_pct = 0.0

        # Total promoter holding percent
        promo_holding_pct = _clean_numeric(row.get("TOTAL PROMOTER HOLDING % A /(A+B+C)"))
        
        # Share counts
        pledged_shares = _clean_int(row.get("PROMOTER SHARES ENCUMBERED AS OF LAST QUARTER NO. OF SHARES (X)"))
        promoter_shares = _clean_int(row.get("TOTAL PROMOTER HOLDING NO. OF SHARES (A)"))
        total_shares = _clean_int(row.get("TOTAL NO. OF ISSUED SHARES A+B+C"))
        
        # Depository metrics
        dep_pledged = _clean_int(row.get("NO. OF SHARES PLEDGED IN THE DEPOSITORY SYSTEM NO. OF SHARES PLEDGED"))
        dep_demat_pct = _clean_numeric(row.get("(%) PLEDGE / DEMAT"))

        # As of date
        as_of = _parse_broadcast_date(row.get("BROADCAST DATE") or row.get("DISCLOSURE MADE BY PROMOTERS"))

        records.append({
            "symbol": sym,
            "company_name": company_name,
            "pledge_pct": round(enc_pct, 4),
            "promoter_holding_pct": round(promo_holding_pct, 4) if promo_holding_pct is not None else None,
            "pledged_shares": pledged_shares,
            "promoter_shares": promoter_shares,
            "total_shares": total_shares,
            "depository_pledged_shares": dep_pledged,
            "depository_pledge_demat_pct": round(dep_demat_pct, 4) if dep_demat_pct is not None else None,
            "as_of_date": as_of,
            "source": "NSE",
            "snapshot_id": snapshot_id
        })

    total_rows = len(records) + len(unmapped_companies)
    logger.info(
        f"📊 [NSE_PLEDGE_PARSE] Total={total_rows} | Matched={len(records)} ({len(records)*100/max(1, total_rows):.1f}%) | "
        f"Unmapped={len(unmapped_companies)}"
    )
    if unmapped_companies:
        logger.debug(f"ℹ️ Sample unmapped NSE companies (first 5): {unmapped_companies[:5]}")

    return {
        "snapshot_id": snapshot_id,
        "snapshot_date": snapshot_date,
        "total_rows": total_rows,
        "matched_count": len(records),
        "unmapped_count": len(unmapped_companies),
        "records": records,
        "unmapped_companies": unmapped_companies
    }


def fetch_and_parse_nse_pledged_data() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    High-level orchestrator: Downloads and parses the official NSE Pledged Data CSV.
    Returns (result_dict, error_string).
    """
    csv_text, err = download_official_nse_pledged_csv()
    if err:
        return None, err
    try:
        parsed = parse_nse_pledged_csv(csv_text)
        return parsed, None
    except Exception as e:
        logger.exception(f"❌ Failed to parse NSE pledged CSV: {e}")
        return None, str(e)
