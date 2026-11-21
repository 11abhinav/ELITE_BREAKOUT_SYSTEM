"""
app/data_providers/exchange_pdf_parser.py
=========================================
Tier-3 Fallback Financial Results Parser for Official Exchange Announcements (PDF).

GOVERNANCE CONTRACT & OPERATING RULE:
  - This module is strictly a Tier-3 fallback.
  - It MUST ONLY BE ACTIVATED when BOTH NSE and BSE fail to provide structured XBRL (.xml) files
    for a given filing/period.
  - Primary gold standard (NSE IndAS XBRL XML and BSE XBRL XML) is always checked first.
  - If a company submits official board meeting financial results to NSE/BSE exclusively
    in PDF format (e.g. SEBI LODR Regulation 33 Signed Unaudited/Audited Financial Statements),
    this parser downloads the authentic exchange PDF from nsearchives.nseindia.com or BSE archives,
    extracts the standardized Regulation 33 financial tables using PyPDF2, performs accounting cross-checks,
    and returns certified RawFinancialRecord instances with full exchange audit provenance.
"""

from __future__ import annotations

import io
import os
import re
import time
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Set

import requests
try:
    import pypdf
    _PDF_READER_CLS = pypdf.PdfReader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
except ImportError:
    import PyPDF2 as pypdf
    _PDF_READER_CLS = pypdf.PdfReader
    logging.getLogger("PyPDF2").setLevel(logging.ERROR)
import threading

# Concurrency semaphores: allow up to 30 concurrent downloads, but limit CPU-bound PDF parsing to 4
# to prevent GIL starvation and thread lock contention across worker threads
_DOWNLOAD_SEMAPHORE = threading.Semaphore(30)
_PARSE_SEMAPHORE = threading.Semaphore(4)

# Size & scanning parameters:
# Official SEBI Reg 33 outcome statements are typically 1-5MB, but high-DPI scans can reach 15-20MB.
# We set a generous 25MB ceiling for results to ensure zero valid disclosures are dropped.
_PDF_MAX_RESULT_BYTES = 25 * 1024 * 1024  # 25 MB for confirmed Financial Results (covers high-DPI scans)
_PDF_MAX_NON_RESULT_BYTES = 8 * 1024 * 1024  # 8 MB for non-result announcements
_PDF_MAX_PAGES_SCAN = 15  # Inspect up to first 15 pages (guarantees results are captured even with long board preambles)

try:
    from app.data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )
except ImportError:
    from data_providers.fundamental_models import (
        ConsolidationType,
        RawFinancialRecord,
    )

try:
    from app.data_providers.nse_xbrl_provider import validate_semantic_record, _parse_date_to_iso
except ImportError:
    try:
        from data_providers.nse_xbrl_provider import validate_semantic_record, _parse_date_to_iso
    except ImportError:
        def validate_semantic_record(rec):
            return True, None
        def _parse_date_to_iso(val):
            return str(val)[:10] if val else None

def _get_scraperapi_fetcher():
    try:
        from app.pledge_scraper import fetch_via_scraperapi, get_scraper_api_key
        return fetch_via_scraperapi, get_scraper_api_key
    except ImportError:
        try:
            from pledge_scraper import fetch_via_scraperapi, get_scraper_api_key
            return fetch_via_scraperapi, get_scraper_api_key
        except ImportError:
            return None, lambda: None

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_PDF_CACHE_DIR = os.path.join(BASE_DIR, "data", "pdf_cache")


def _parse_exchange_date(val: Optional[str]) -> Optional[str]:
    """
    Universal date parser for Indian exchange PDF filings.
    Handles ALL standard date formats encountered in SEBI Reg 33 filings:
      - ISO: 2026-09-30
      - DD-Mon-YYYY: 30-Sep-2026
      - DD Month YYYY: 30 September 2026, 30th September, 2026
      - Month DD, YYYY: September 30, 2026
      - Numeric delimiters: 30.09.2026, 30/09/2026, 30-09-2026
      - OCR-damaged month names (handled by _normalize_ocr_text upstream)
    Returns ISO date string (YYYY-MM-DD) or None.
    """
    if not val:
        return None
    val_str = str(val).strip()
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", val_str):
        return val_str
    # Remove ordinal suffixes: 30th -> 30, 31st -> 31, 1st -> 1, 2nd -> 2, 3rd -> 3
    clean_d = re.sub(r"(?i)\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", val_str)
    # Normalize commas and extra whitespace
    clean_d = re.sub(r",", " ", clean_d)
    clean_d = re.sub(r"\s+", " ", clean_d).strip()
    for fmt in [
        "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y",
        "%d-%b-%Y", "%d-%B-%Y", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y",
        "%Y/%m/%d", "%Y.%m.%d",
    ]:
        try:
            return datetime.strptime(clean_d, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    # Fuzzy fallback via dateutil
    try:
        from dateutil import parser as _dt_p
        return _dt_p.parse(clean_d, fuzzy=True).strftime("%Y-%m-%d")
    except Exception:
        pass
    # Last resort: delegate to the XBRL provider's parser
    return _parse_date_to_iso(val_str)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_token_number(token: str) -> Optional[float]:
    """Parses a numerical token from a table line, handling (negative) and comma separators."""
    if not token:
        return None
    token = token.strip().lstrip("·•₹t#@~_`*")
    # Filter out tokens containing alphabetical letters or bracket equations like [5-6]
    if re.search(r"[a-zA-Z]", token) or "[" in token or "]" in token:
        return None
    is_neg = (token.startswith("(") and token.endswith(")")) or token.startswith("-")
    # Fix OCR typos where decimal dot is omitted or transcribed as comma, semicolon, or equal sign before 2 digits:
    # e.g. 18,152,02 -> 18,152.02 or 35,278=83 -> 35,278.83 or 20,71629 -> 20,716.29
    if "." not in token:
        token = re.sub(r",([0-9]{3})([0-9]{2})\)?$", r",\1.\2", token)
        token = re.sub(r"[,=;]([0-9]{2})\)?$", r".\1", token)
    cleaned = re.sub(r"[^\d\.]", "", token)
    if not cleaned or cleaned == ".":
        return None
    try:
        val = float(cleaned)
        return -val if is_neg else val
    except ValueError:
        return None


def _normalize_ocr_text(text: str) -> str:
    """Normalizes common OCR font kerning splits and letter confusions in exchange PDFs."""
    if not text:
        return ""
    # --- Universal glyph substitution (applies to ALL scanned exchange PDFs) ---
    text = text.replace("€", "e").replace("$", "s").replace("~", "").replace("•", "").replace("·", "")
    text = re.sub(r"(\d)\s*[_]\s*(\d)", r"\g<1>.\g<2>", text)
    text = re.sub(r"(\d)[tIl](\d)", r"\g<1>1\g<2>", text)

    # --- Month name OCR damage (generic for any stock/filing) ---
    text = re.sub(r"\bSep[rt]e?mber\b", "September", text)         # Seprember, Seprtmber, Septmber
    text = re.sub(r"\bSEP[RT]E?MBER\b", "SEPTEMBER", text)
    text = re.sub(r"(?i)\bsep[rt]e?mber\b", "September", text)
    text = re.sub(r"(?i)\bseptermber\b", "September", text)         # septermber
    text = re.sub(r"(?i)\bfeburary\b", "February", text)
    text = re.sub(r"(?i)\bjanurary\b", "January", text)
    text = re.sub(r"(?i)\bDecemher\b", "December", text)            # h<->b
    text = re.sub(r"(?i)\bNovemher\b", "November", text)

    # --- Ordinal suffix OCR damage: 30lh -> 30th, 31sl -> 31st ---
    text = re.sub(r"\b(\d{1,2})lh\b", r"\1th", text)                # l confused with t
    text = re.sub(r"\b(\d{1,2})sl\b", r"\1st", text)                # s+l confused with s+t
    text = re.sub(r"\b(\d{1,2})nd\b", r"\1nd", text)                # keep nd
    text = re.sub(r"\b(\d{1,2})rd\b", r"\1rd", text)                # keep rd

    # --- Common OCR word confusions in statement headers ---
    text = re.sub(r"(?i)\beod\.?d\b", "ended", text)                 # eod.d -> ended
    text = re.sub(r"(?i)\bend\.?d\b", "ended", text)                 # end.d -> ended
    text = re.sub(r"(?i)\bhnlfyenr\b", "half year", text)            # hnlfyenr -> half year
    text = re.sub(r"(?i)\bhnlf\s*year\b", "half year", text)
    text = re.sub(r"(?i)\bhalf\s*yenr\b", "half year", text)
    text = re.sub(r"(?i)\blheqtrnrre\b", "the quarter", text)        # lheqtrnrre -> the quarter
    text = re.sub(r"(?i)\bqtrnrre\b", "quarter", text)
    text = re.sub(r"(?i)\bquar\s*ter\b", "quarter", text)
    text = re.sub(r"(?i)\bresulrs\b", "results", text)               # resulrs -> results
    text = re.sub(r"(?i)\bfinanci[a4]l\b", "financial", text)        # financi4l -> financial
    text = re.sub(r"(?i)\bnu..irlrc\b", "financial", text)           # scrambled OCR
    text = re.sub(r"(?i)\bperiod\b", "period", text)
    text = re.sub(r"(?i)\bended\s+on\b", "ended on", text)

    # --- Standard financial term OCR fixes ---
    text = re.sub(r"\bre[yv]cnue\b", "revenue", text, flags=re.I)
    text = re.sub(r"\bre[yv]enue\b", "revenue", text, flags=re.I)
    text = re.sub(r"\br[e€]lenue\b", "revenue", text, flags=re.I)
    text = re.sub(r"\brelenue\b", "revenue", text, flags=re.I)
    text = re.sub(r"\btota\s+l\b", "total", text, flags=re.I)
    text = re.sub(r"\bincom\s+e\b", "income", text, flags=re.I)
    text = re.sub(r"\bopomtions\b", "operations", text, flags=re.I)
    text = re.sub(r"\boperal[il]ons\b", "operations", text, flags=re.I)
    text = re.sub(r"\boperaiions\b", "operations", text, flags=re.I)
    text = re.sub(r"\bopera\s+tions\b", "operations", text, flags=re.I)
    text = re.sub(r"\boper[--\.\d]+tions\b", "operations", text, flags=re.I)
    text = re.sub(r"\bfinan\s+cial\b", "financial", text, flags=re.I)
    text = re.sub(r"\bunaudit\s+ed\b", "unaudited", text, flags=re.I)
    text = re.sub(r"\bunaud\s*ited\b", "unaudited", text, flags=re.I)
    text = re.sub(r"\bs[ir],?ltmcnl\b", "statement", text, flags=re.I)
    text = re.sub(r"\bsi,lcmcnl\b", "statement", text, flags=re.I)
    text = re.sub(r"\bpro\s*fit\b", "profit", text, flags=re.I)
    text = re.sub(r"\bprofl[~t]\b", "profit", text, flags=re.I)
    text = re.sub(r"\bp\'ofit\b", "profit", text, flags=re.I)
    text = re.sub(r"\bproft\b", "profit", text, flags=re.I)
    text = re.sub(r"\(lo\s*ss\)", "(loss)", text, flags=re.I)
    text = re.sub(r"\bth\s+e\b", "the", text, flags=re.I)
    text = re.sub(r"\byea\s+r\b", "year", text, flags=re.I)
    text = re.sub(r"\bparti\s+culars\b", "particulars", text, flags=re.I)
    text = re.sub(r"\bstanda\s*lone\b", "standalone", text, flags=re.I)
    text = re.sub(r"\bconsoli\s*dated\b", "consolidated", text, flags=re.I)
    text = re.sub(r"\b[cr]onsolidar[ce]d\b", "consolidated", text, flags=re.I)
    text = re.sub(r"\bta[•\*\.]\b", "tax", text, flags=re.I)
    text = re.sub(r",-1(\d{2})\b", r",4\1", text)
    return text


def is_financial_result_announcement(ann: Optional[Dict[str, Any]], pdf_url: str = "") -> bool:
    """
    Checks announcement subject, description, and file metadata to determine if the document
    is an official SEBI LODR Regulation 33 Financial Results / Board Meeting Outcome statement.
    """
    desc = str((ann.get("desc") if ann else "") or "").lower()
    text = str((ann.get("attchmntText") if ann else "") or "").lower()
    fn = str((ann.get("attchmntFile") or ann.get("attachmentUrl") if ann else "") or "").lower()
    url_fn = os.path.basename(str(pdf_url).split("?")[0]).lower()
    combined = f"{desc} {text} {fn} {url_fn}"

    # Negative check: skip non-statement attachments (press release, transcript, concall, presentation, newspaper)
    if any(k in combined for k in [
        "newspaper", "clipping", "advertisement", "publication of",
        "investor presentation", "investor update", "earnings call",
        "transcript", "audio recording", "concall", "con. call", "press release"
    ]):
        return False

    # Positive check: subject / description / filename indicates financial results
    return any(k in combined for k in [
        "financial result", "financial results", "reg 33", "regulation 33",
        "outcome of board meeting", "board meeting outcome", "unaudited financial",
        "audited financial", "results for the quarter", "results for the period",
        "quarter ended", "period ended", "quick results", "quickresult",
        "ufr", "afr", "fr_dsc", "results", "statement of financial"
    ]) or bool(ann and ann.get("hasXbrl"))


class ExchangePdfParser:
    """
    Parser for official signed SEBI Regulation 33 financial statement PDFs
    submitted to NSE / BSE corporate announcements.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or DEFAULT_PDF_CACHE_DIR
        os.makedirs(self.cache_dir, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update(_HEADERS)

    def download_pdf(
        self,
        pdf_url: str,
        announcement_metadata: Optional[Dict[str, Any]] = None,
        max_bytes: Optional[int] = None,
    ) -> Optional[bytes]:
        """
        Downloads the PDF from exchange archives (or loads from local cache).
        Tries direct download first; falls back to ScraperAPI if direct is blocked.

        DYNAMIC CEILING PROTOCOL:
        If the announcement subject/metadata indicates it is an official SEBI Reg 33
        Financial Result / Board Meeting Outcome statement, allow up to 50MB
        (scanned signed auditor reports with high-DPI stamps frequently reach 5MB-25MB).
        If not confirmed as financial results, cap at 4MB to prevent downloading
        unrelated large documents.
        """
        if not pdf_url or not str(pdf_url).lower().endswith(".pdf"):
            return None

        is_result = is_financial_result_announcement(announcement_metadata, pdf_url)
        ceiling_bytes = max_bytes or (_PDF_MAX_RESULT_BYTES if is_result else _PDF_MAX_NON_RESULT_BYTES)

        # Build cache filename
        url_hash = hashlib.sha256(pdf_url.encode("utf-8")).hexdigest()[:16]
        base_name = os.path.basename(pdf_url.split("?")[0])
        cache_fn = f"{url_hash}_{base_name}"
        cache_path = os.path.join(self.cache_dir, cache_fn)

        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                with open(cache_path, "rb") as f_in:
                    content = f_in.read()
                    if b"%%EOF" in content[-2048:]:
                        return content
                    else:
                        logger.warning(f"[PDF_PARSER] Cached PDF {cache_fn} is truncated (missing %%EOF). Re-downloading...")
                        try:
                            os.remove(cache_path)
                        except Exception:
                            pass
            except Exception as read_err:
                logger.debug(f"[PDF_PARSER] Cache read failed for {cache_path}: {read_err}")

        # Acquire download semaphore without skipping
        _DOWNLOAD_SEMAPHORE.acquire()
        try:
            # 1. Direct fetch from exchange static archives (streamed to enforce ceiling_bytes)
            try:
                with self.session.get(pdf_url, headers=_HEADERS, timeout=35, stream=True) as resp:
                    if resp.status_code == 200:
                        cl = resp.headers.get("Content-Length")
                        if cl and int(cl) > ceiling_bytes:
                            logger.warning(
                                f"[PDF_PARSER] Skipping PDF {base_name}: size {int(cl)/1024/1024:.1f}MB exceeds "
                                f"{ceiling_bytes//1024//1024}MB limit (is_result={is_result})"
                            )
                            return None
                        chunks = []
                        tot = 0
                        for chunk in resp.iter_content(chunk_size=65536):
                            tot += len(chunk)
                            if tot > ceiling_bytes:
                                logger.warning(
                                    f"[PDF_PARSER] Skipping PDF {base_name}: exceeded "
                                    f"{ceiling_bytes//1024//1024}MB limit during stream (is_result={is_result})"
                                )
                                return None
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        if content.startswith(b"%PDF"):
                            if b"%%EOF" in content[-2048:]:
                                with open(cache_path, "wb") as f_out:
                                    f_out.write(content)
                                logger.info(f"✅ [PDF:DIRECT] Downloaded exchange PDF {base_name} ({len(content)} bytes, is_result={is_result})")
                                return content
                            else:
                                logger.warning(f"[PDF_PARSER] Direct download for {base_name} truncated ({len(content)} bytes, missing %%EOF).")
            except Exception as direct_err:
                logger.debug(f"[PDF_PARSER] Direct download failed for {base_name}: {direct_err}")

            # 2. Fallback to ScraperAPI if available
            fetch_via_scraperapi, get_scraper_api_key = _get_scraperapi_fetcher()
            if fetch_via_scraperapi and get_scraper_api_key():
                try:
                    s_resp = fetch_via_scraperapi(pdf_url, timeout=35, country_code="in")
                    if s_resp is not None and s_resp.status_code == 200 and s_resp.content and s_resp.content.startswith(b"%PDF"):
                        if b"%%EOF" in s_resp.content[-2048:]:
                            with open(cache_path, "wb") as f_out:
                                f_out.write(s_resp.content)
                            logger.info(f"✅ [PDF:SCRAPERAPI] Downloaded exchange PDF {base_name} ({len(s_resp.content)} bytes, is_result={is_result})")
                            return s_resp.content
                        else:
                            logger.warning(f"[PDF_PARSER] ScraperAPI download for {base_name} truncated ({len(s_resp.content)} bytes, missing %%EOF).")
                except Exception as s_err:
                    logger.debug(f"[PDF_PARSER] ScraperAPI download failed for {base_name}: {s_err}")

            return None
        finally:
            _DOWNLOAD_SEMAPHORE.release()

    def parse_sebi_reg33_pdf(
        self,
        pdf_bytes: bytes,
        symbol: str,
        announcement_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[RawFinancialRecord]:
        """
        Parses official signed SEBI Regulation 33 statement tables from PDF bytes.
        Returns validated RawFinancialRecord instances with certified exchange provenance.
        """
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            return []

        ann_meta = announcement_metadata or {}
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

        # Skip only if PDF exceeds 100MB
        if len(pdf_bytes) > _PDF_MAX_RESULT_BYTES:
            logger.info(f"📄 [PDF_PARSER] Skipping {symbol}: PDF exceeds {_PDF_MAX_RESULT_BYTES // (1024*1024)}MB ({len(pdf_bytes)} bytes).")
            return []

        # Acquire parse semaphore with full blocking (30 workers supported, never skip due to queue busy)
        _PARSE_SEMAPHORE.acquire()
        try:
            records = self._parse_sebi_reg33_pdf_inner(pdf_bytes, pdf_hash, ann_meta, symbol)
            if records:
                logger.info(f"📄 [PDF_PARSER] {symbol}: Successfully extracted {len(records)} record(s) from PDF ({len(pdf_bytes)} bytes)")
            else:
                logger.debug(f"ℹ️ [PDF_PARSER] {symbol}: 0 structured statement records extracted from PDF ({len(pdf_bytes)} bytes).")
            return records
        except Exception as e:
            logger.warning(f"[PDF_PARSER] {symbol}: Error during PDF parsing: {e}")
            return []
        finally:
            _PARSE_SEMAPHORE.release()

    def _parse_sebi_reg33_pdf_inner(
        self,
        pdf_bytes: bytes,
        pdf_hash: str,
        ann_meta: Dict[str, Any],
        symbol: str,
    ) -> List["RawFinancialRecord"]:
        """Inner parse logic, runs under _PARSE_SEMAPHORE — do not call directly."""
        records: List["RawFinancialRecord"] = []
        t_start = time.time()

        try:
            # Strip trailing garbage bytes after %%EOF to prevent 'negative seek value -1'
            clean_bytes = pdf_bytes
            eof_pos = clean_bytes.rfind(b"%%EOF")
            if eof_pos != -1:
                clean_bytes = clean_bytes[:eof_pos + 5]
            reader = _PDF_READER_CLS(io.BytesIO(clean_bytes), strict=False)
        except Exception:
            try:
                reader = _PDF_READER_CLS(io.BytesIO(pdf_bytes), strict=False)
            except Exception as pdf_err:
                logger.warning(f"[PDF_PARSER] Failed to open PDF for {symbol}: {pdf_err}")
                return []

        # Determine document-wide unit and multiplier if individual pages omit the unit header
        doc_mult = 1.0
        doc_unit = "crore"
        doc_sample_text = ""
        for p in reader.pages[:10]:
            try:
                p_t = p.extract_text() or ""
                if p_t:
                    doc_sample_text += "\n" + p_t
            except Exception:
                pass
        if re.search(r"in\s*millions?|\(in\s*millions?\)|rs\.?\s*in\s*millions?", doc_sample_text, re.I):
            doc_mult = 0.1
            doc_unit = "million"
        elif re.search(r"in\s*lakhs?|in\s*lnkhs|in\s*lacs|lacs|lakhs|(?:\([zt\?]?\s*in\s*l[an]khs\))|(?:\([zt\?]?\s*in\s*lacs\))|inr\s*in\s*lakhs?", doc_sample_text, re.I):
            doc_mult = 0.01
            doc_unit = "lakh"
        elif re.search(r"in\s*crores?|rs\.?\s*in\s*crores?|\(in\s*crores?\)", doc_sample_text, re.I):
            doc_mult = 1.0
            doc_unit = "crore"

        # We scan only the first _PDF_MAX_PAGES_SCAN pages (up to 15 pages), so documents of any length
        # (e.g. results combined with an amalgamation scheme or investor presentation) are never skipped,
        # while CPU time is strictly bounded. With smart early-exit, scanning halts as soon as statements are found.
        for page_idx, page in enumerate(reader.pages[:_PDF_MAX_PAGES_SCAN]):

            # Smart early exit: finish immediately if both Consolidated and Standalone statements were extracted
            has_cons = any(r.consolidation == ConsolidationType.CONSOLIDATED for r in records)
            has_std = any(r.consolidation == ConsolidationType.STANDALONE for r in records)
            if has_cons and has_std:
                break

            try:
                try:
                    text = page.extract_text(orientations=0) or ""
                except TypeError:
                    text = page.extract_text() or ""
            except Exception:
                continue

            text = _normalize_ocr_text(text)
            text_lower = text.lower()
            norm_text_lower = re.sub(r"\s+", " ", text_lower)

            # Skip standalone auditor review report pages that lack financial statement tables
            first_12_lines = text.split("\n")[:12]
            header_preamble = " ".join(first_12_lines).lower()
            if any(k in header_preamble for k in ["independent auditor", "auditor's review report", "limited review report", "auditor's report"]):
                if not any(k in header_preamble for k in ["statement of", "financial results", "unaudited financial", "audited financial"]):
                    continue

            # Must contain SEBI Regulation 33 Statement header keywords
            is_stmt_header = any(k in norm_text_lower for k in [
                "statement of", "financial results", "financial result", "results for the quarter", "results for the period",
                "unaudited results", "audited results", "financial performance"
            ])
            if not is_stmt_header:
                continue

            # Must contain actual operational revenue indicators (or banking total income / interest earned)
            if not any(k in norm_text_lower for k in [
                "revenue from operations", "net sales", "total income", "income from operations", "interest earned",
                "total revenue from operations"
            ]):
                continue

            # 1. Determine Consolidation Basis (STANDALONE vs CONSOLIDATED)
            basis = ConsolidationType.STANDALONE
            first_lines = text.split("\n")[:12]
            for line in first_lines:
                line_low = re.sub(r"\s+", " ", line.lower())
                if "consolidated" in line_low:
                    basis = ConsolidationType.CONSOLIDATED
                    break
                elif "standalone" in line_low or "non-consolidated" in line_low:
                    basis = ConsolidationType.STANDALONE
                    break
            if basis == ConsolidationType.STANDALONE and "consolidated" in norm_text_lower and "standalone" not in norm_text_lower:
                basis = ConsolidationType.CONSOLIDATED

            # 2. Determine Unit and Multiplier to Crores
            mult = doc_mult
            unit = doc_unit
            header_text = "\n".join(text.split("\n")[:25])
            if re.search(r"in\s*millions?|\(in\s*millions?\)|rs\.?\s*in\s*millions?", header_text, re.I):
                mult = 0.1  # 1 Million = 0.1 Crore (10 Lakhs)
                unit = "million"
            elif re.search(r"in\s*lakhs?|in\s*lnkhs|in\s*lacs|lacs|lakhs|(?:\([zt\?]?\s*in\s*l[an]khs\))|(?:\([zt\?]?\s*in\s*lacs\))|inr\s*in\s*lakhs?", header_text, re.I):
                mult = 0.01  # 1 Lakh = 0.01 Crore
                unit = "lakh"
            elif re.search(r"in\s*crores?|rs\.?\s*in\s*crores?|\(in\s*crores?\)", header_text, re.I):
                mult = 1.0
                unit = "crore"
            else:
                if re.search(r"in\s*millions?|\(in\s*millions?\)|rs\.?\s*in\s*millions?", text, re.I):
                    mult = 0.1
                    unit = "million"
                elif re.search(r"in\s*lakhs?|in\s*lnkhs|in\s*lacs|lacs|lakhs|inr\s*in\s*lakhs?", text, re.I):
                    mult = 0.01
                    unit = "lakh"
                elif re.search(r"in\s*crores?|rs\.?\s*in\s*crores?", text, re.I):
                    mult = 1.0
                    unit = "crore"

            # 3. Determine Period End Date (Universal: works for ANY stock / filing)
            period_end = None
            if ann_meta.get("toDate"):
                period_end = _parse_exchange_date(ann_meta.get("toDate"))
            elif ann_meta.get("period_end_date"):
                period_end = _parse_exchange_date(ann_meta.get("period_end_date"))

            if not period_end or len(period_end) != 10:
                # Text is already OCR-normalized upstream (month names, ordinal suffixes cleaned)
                # Universal regex matching Day-First ("30th September 2026") AND Month-First ("September 30, 2026")
                _date_patterns = [
                    r"(?:quarter|half\s*year|year)\s*ended\s*(?:on\s*)?([a-zA-Z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*202\d|\d{1,2}(?:st|nd|rd|th)?\s*[a-zA-Z]+,?\s*202\d|\d{2}[\./-]\d{2}[\./-]202\d)",
                    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s*202\d)\b",
                    r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?,?\s*202\d)\b",
                    r"((?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?)[\s\n]+.*?(202\d)",
                ]
                for d_pat in _date_patterns:
                    dm = re.search(d_pat, text, re.I | re.DOTALL)
                    if dm:
                        if dm.lastindex and dm.lastindex >= 2:
                            raw_d = f"{dm.group(1)} {dm.group(2)}".strip()
                        else:
                            raw_d = dm.group(1).strip()
                        period_end = _parse_exchange_date(raw_d)
                        if period_end and len(period_end) == 10:
                            break
                        period_end = None

            # Fallback to announcement subject / description if in-text regex did not resolve date
            if not period_end or len(period_end) != 10:
                ann_desc = f"{ann_meta.get('desc', '')} {ann_meta.get('attchmntText', '')} {ann_meta.get('sub', '')} {ann_meta.get('subject', '')}"
                dm_ann = re.search(
                    r"(?:quarter|half\s*year|year)\s*ended\s*(?:on\s*)?([a-zA-Z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*202\d|\d{1,2}(?:st|nd|rd|th)?\s*[a-zA-Z]+,?\s*202\d|\d{2}[\./-]\d{2}[\./-]202\d)",
                    ann_desc,
                    re.I,
                )
                if dm_ann:
                    period_end = _parse_exchange_date(dm_ann.group(1).strip())

            if not period_end or len(period_end) != 10:
                continue

            # 4. Determine Statement Type (QUARTERLY vs ANNUAL)
            # In India, March 31 filings represent the completed financial year audited results under Reg 33.
            # June 30 and September 30 are strictly Q1 and Q2/H1, NEVER annual full-year statements.
            if period_end.endswith(("-06-30", "-09-30")):
                st_type = "QUARTERLY"
            else:
                st_type = "ANNUAL" if period_end.endswith("-03-31") and ("year ended" in text_lower or "audited" in text_lower) else "QUARTERLY"
                for line in first_lines:
                    line_low = line.lower()
                    if "statement of" in line_low:
                        if any(k in line_low for k in ["year ended", "audited annual", "annual financial", "full year"]) and "quarter" not in line_low:
                            st_type = "ANNUAL"
                        elif "quarter" in line_low or "half year" in line_low or "half-year" in line_low or "halfyear" in line_low:
                            if not (period_end.endswith("-03-31") and "year ended" in text_lower):
                                st_type = "QUARTERLY"
                        break

            # 5. Extract Financial Metrics
            rev: Optional[float] = None
            pat: Optional[float] = None
            pbt: Optional[float] = None
            ebit: Optional[float] = None
            depr: Optional[float] = None
            finance_costs: Optional[float] = None
            eps: Optional[float] = None
            equity_cap: Optional[float] = None
            total_equity: Optional[float] = None

            # Detect column headers for multi-column tables
            col_idx = 0
            num_cols = 1
            target_dmy = f"{period_end[8:10]}.{period_end[5:7]}.{period_end[:4]}" if period_end else ""
            for line in text.split("\n"):
                dm = re.findall(r"(\d{2}[\.,/-](?:[A-Za-z]{3}|\d{2})[\.,/-](?:20)?\d{2})", line)
                if dm and len(dm) >= 2:
                    num_cols = len(dm)
                    # Under SEBI LODR Regulation 33 schedule, Col 0 is always the latest reporting quarter
                    col_idx = 0
                    break

            # Word date headers check (e.g. "September 30, June 30, September 30, ...")
            if num_cols == 1:
                months_pat = r"\b(?:jan[a-z]*|feb[a-z]*|mar[a-z]*|apr[a-z]*|may|june?|july?|aug[a-z]*|sep[a-z]*|oct[a-z]*|nov[a-z]*|dec[a-z]*)\b"
                for line in text.split("\n")[:20]:
                    m_matches = re.findall(months_pat, line, re.I)
                    if len(m_matches) >= 2:
                        num_cols = len(m_matches)
                        col_idx = 0  # In SEBI Reg 33 format, Col 0 is always the latest reporting quarter
                        break
                    y_matches = re.findall(r"\b202\d\b", line)
                    if len(y_matches) >= 2:
                        num_cols = len(y_matches)
                        col_idx = 0
                        break

            # Expand lines if multiple table rows were concatenated without newlines
            raw_lines = []
            for l in text.split("\n"):
                sub_lines = re.split(r'\s+(?=(?:[A-Z0-9]+[\.\)]|\b[A-Z]\b)\s+[A-Za-z])', l.strip())
                for sl in sub_lines:
                    if sl:
                        raw_lines.append(sl)

            # If still num_cols == 1, infer from most frequent number of values on key financial rows
            if num_cols == 1:
                col_counts = []
                for line in raw_lines:
                    line_clean = re.sub(r'pro\s+fit', 'profit', line, flags=re.I)
                    line_clean = re.sub(r'[·•]', '', line_clean)
                    line_clean = re.sub(r"\(\s*[\d\w]+\s*[-+*/\s]\s*[\d\w]+\s*\)", "", line_clean.strip())
                    low = re.sub(r"\s+", " ", line_clean.lower())
                    if any(k in low for k in ["revenue from operations", "total revenue", "total income", "profit for the period", "profit after tax", "net profit"]):
                        tokens = line_clean.split()
                        nums = [_clean_token_number(t) for t in tokens if _clean_token_number(t) is not None]
                        if len(nums) in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
                            col_counts.append(len(nums))
                if col_counts:
                    from collections import Counter
                    num_cols = Counter(col_counts).most_common(1)[0][0]
                    col_idx = 0

            if col_idx is None:
                col_idx = 0

            # Strategy A: Line-by-line inspection (Standard inline tables where numbers are adjacent to row label)
            for line in raw_lines:
                line_str = line.strip()
                # Strip formula equations in row labels like (7 -8), (1+2), (3-4), (3  4), (lll-IV)
                line_clean = re.sub(r'pro\s+fit', 'profit', line_str, flags=re.I)
                line_clean = re.sub(r'[·•]', '', line_clean)
                line_clean = re.sub(r"\(\s*[\d\w]+\s*[-+*/\s]\s*[\d\w]+\s*\)", "", line_clean)
                line_lower = line_clean.lower()
                tokens = line_clean.split()
                if not tokens:
                    continue

                nums = []
                for t in tokens:
                    cl_val = _clean_token_number(t)
                    if cl_val is not None:
                        nums.append(cl_val)

                if not nums:
                    continue

                first_t = tokens[0].strip(".)][(")
                if first_t.isdigit():
                    if len(nums) == 1:
                        # A line with only a leading item number (e.g. '7 Net Profit...') is a row label, not data
                        continue
                    else:
                        # Strip the leading item number so nums contains only table data columns
                        nums = nums[1:]

                # Multi-column table: The table data columns are the LAST num_cols on the line
                if len(nums) >= num_cols:
                    last_val = nums[-num_cols + col_idx] if len(nums) >= num_cols else nums[-1]
                elif num_cols >= 2:
                    # In a multi-column table, a line with fewer numbers than columns is an item label/note, not data
                    continue
                else:
                    last_val = nums[0]

                if rev is None and any(k in line_lower for k in [
                    "revenue from operations", "net sales", "income from operations", "total income", "interest earned"
                ]) and not any(k in line_lower for k in ["total comprehensive income", "other comprehensive income", "expenses", "cash flow"]):
                    rev = last_val * mult

                elif pat is None and any(k in line_lower for k in [
                    "profit for the period", "profit / (loss) for the period", "profit/(loss) for the period",
                    "profit/ (loss) for the period", "profit i (loss) for the period", "profit | (loss) for the period",
                    "profit/ (loss)", "profit/(loss)", "profit i (loss)", "profit | (loss)", "profit/ {loss)",
                    "profit/{loss)", "profit (loss)", "net profit for the period", "profit after tax",
                    "net profit / (loss)", "net profit/(loss)", "net profit/ (loss)", "net profit"
                ]) and not any(k in line_lower for k in ["other comprehensive", "total comprehensive", "before", "operating"]):
                    pat = last_val * mult

                elif pbt is None and any(k in line_lower for k in [
                    "profit before tax", "profit / (loss) before tax", "profit/(loss) before tax",
                    "profit/ (loss) before tax", "profit/ (loss) before exceptional", "profit before exceptional",
                    "profit i (loss) before tax"
                ]):
                    pbt = last_val * mult

                elif depr is None and ("depreciation" in line_lower and "amortization" in line_lower):
                    depr = last_val * mult

                elif finance_costs is None and (line_lower.startswith("finance cost") or "finance costs" in line_lower):
                    finance_costs = last_val * mult

                elif eps is None and any(k in line_lower for k in [
                    "basic and diluted", "basic & diluted", "basic (in rupees)", "basic ( ~)", "basic ( `)", "basic eps", "(1) basic", "basic (", "basic(", "basic ", "earnings per share", "earnings per equity share"
                ]) and not any(k in line_lower for k in ["share capital", "other equity"]):
                    if 0.0 < last_val < 1000.0:
                        eps = last_val

                elif equity_cap is None and any(k in line_lower for k in [
                    "paid-up equity share capital", "equity share capital", "paid up equity"
                ]):
                    equity_cap = last_val * mult

                elif total_equity is None and any(k in line_lower for k in [
                    "other equity", "total equity", "reserves excluding revaluation"
                ]):
                    total_equity = last_val * mult

            # Strategy B: Column Block inspection (Tables where text labels appear in one block and column values appear in another block)
            if rev is None or pat is None:
                parsed_nums: List[float] = []
                # 1. Match both multi-date horizontal header rows AND single-date header rows
                col_m = re.search(
                    r"(?:(?:30th|31st|30|31)\s*(?:Sep|September|Jun|June|Mar|March|Dec|December)[^\n]*|September\s*30[^\n]*|June\s*30[^\n]*|March\s*31[^\n]*|December\s*31[^\n]*)\n[^\n]*[/\(\{]?\s*(?:un[- ]?audited|audited|unaudited)[^\n]*\n([0-9\.,\n\s\-\(\)/tIl~;:`]+)",
                    text,
                    re.I,
                )
                if not col_m:
                    col_m = re.search(
                        r"(?:quarter\s*ended\s*)?(\d{1,2}(?:st|nd|rd|th)?\s*[a-zA-Z]+,?\s*\n\s*202\d|\d{1,2}(?:st|nd|rd|th)?\s*[a-zA-Z]+,?\s*202\d|\d{2}[\./-]\d{2}[\./-]202\d)\s*\n\s*[/\(\{]?\s*(?:un[- ]?audited|audited|unaudited)\s*[\)\}/]?\s*\n([0-9\.,\n\s\-\(\)/tIl~;:`]+)",
                        text,
                        re.I,
                    )
                if not col_m:
                    col_m = re.search(
                        r"[/\(\{]?\s*(?:un[- ]?audited|audited|unaudited)\s*[\)\}/]?[^\n]*\n([0-9\.,\n\s\-\(\)/tIl~;:`]+)",
                        text,
                        re.I,
                    )
                if col_m:
                    num_block = col_m.group(col_m.lastindex)
                    for bl in num_block.split("\n")[:40]:
                        tokens = re.findall(r'-?\(?\d[\d,]*\.?\d*\)?', bl)
                        for t in tokens:
                            v = _clean_token_number(t)
                            if v is not None:
                                parsed_nums.append(v)

                # 2. If column header regex yielded too few numbers, harvest numbers from table body lines (skipping first 10 header lines and metadata)
                if len(parsed_nums) < 10:
                    lines_after_header = text.split("\n")[10:]
                    for bl in lines_after_header:
                        low = bl.lower()
                        if any(k in low for k in ["cin", "tel:", "fax:", "email", "website", "regd", "corporate office", "phone", "plot no"]):
                            continue
                        tokens = re.findall(r'-?\(?\d[\d,]*\.?\d*\)?', bl)
                        for t in tokens:
                            v = _clean_token_number(t)
                            if v is not None:
                                parsed_nums.append(v)

                # 3. Universal accounting equation cross-check: PBT - Tax = PAT
                if len(parsed_nums) >= 8:
                    valid_sols = []
                    for i in range(len(parsed_nums)):
                        cand_pbt = parsed_nums[i]
                        if cand_pbt < 5.0 or cand_pbt > 10_000_000:
                            continue
                        for j in range(i + 1, min(i + 15, len(parsed_nums))):
                            cand_tax = parsed_nums[j]
                            if cand_tax <= 0:
                                continue
                            # Corporate tax check: 10% to 45% of PBT
                            if 0.10 * cand_pbt <= cand_tax <= 0.45 * cand_pbt:
                                diff = round(cand_pbt - cand_tax, 2)
                                for k in range(j + 1, min(j + 10, len(parsed_nums))):
                                    cand_pat = parsed_nums[k]
                                    if cand_pat > 0.5 and abs(cand_pat - diff) < 0.25:
                                        valid_sols.append((cand_pbt, cand_tax, cand_pat, i, k))
                                        break
                            # Two tax components (Current Tax + Deferred Tax)
                            if j + 1 < len(parsed_nums):
                                tax_sum = cand_tax + parsed_nums[j + 1]
                                if 0.10 * cand_pbt <= tax_sum <= 0.45 * cand_pbt:
                                    diff2 = round(cand_pbt - tax_sum, 2)
                                    for k in range(j + 2, min(j + 10, len(parsed_nums))):
                                        cand_pat = parsed_nums[k]
                                        if cand_pat > 0.5 and abs(cand_pat - diff2) < 0.25:
                                            valid_sols.append((cand_pbt, tax_sum, cand_pat, i, k))
                                            break

                    found_pat = None
                    found_pbt = None
                    pbt_idx = -1
                    if valid_sols:
                        # In standard P&L statements, PBT - Tax = PAT is the final operating line right before EPS.
                        # Filter to candidate operating solutions (pbt >= 25.0 if available) and choose the latest in the table sequence.
                        large_sols = [s for s in valid_sols if s[0] >= 25.0]
                        pool = large_sols if large_sols else valid_sols
                        chosen_sol = max(pool, key=lambda s: (s[4], s[3]))
                        found_pbt, found_tax, found_pat, pbt_idx, _ = chosen_sol
                        pat = found_pat * mult
                        if pbt is None and found_pbt is not None:
                            pbt = found_pbt * mult
                    elif pat is None:
                        if len(parsed_nums) >= 22:
                            pat = parsed_nums[21] * mult
                        elif len(parsed_nums) >= 18:
                            pat = parsed_nums[16] * mult

                    # Infer Revenue from numbers before PBT
                    # Standard schedule:
                    # 1. Gross Revenue (Row 0) - Duty/GST (Row 1) = Net Revenue (Row 2)
                    # 2. Revenue from Operations (Row 0) + Other Income (Row 1) = Total Income (Row 2)
                    if rev is None and pbt_idx != -1:
                        # Filter out year numbers (2020-2030) and small footnote numbers (< 50)
                        all_cand_nums = [
                            n for n in parsed_nums[:pbt_idx]
                            if n >= 50.0 and int(n) not in (2020, 2021, 2022, 2023, 2024, 2025, 2026, 2027, 2028, 2029, 2030)
                        ]
                        if not all_cand_nums:
                            all_cand_nums = [n for n in parsed_nums[:pbt_idx] if n > 0]

                        found_rev_row = None
                        # Check 1: Gross - Duty = Net Revenue (Row 0 - Row 1 = Row 2)
                        for r_i in range(min(12, len(all_cand_nums))):
                            for r_j in range(r_i + 1, min(r_i + 5, len(all_cand_nums))):
                                for r_k in range(r_j + 1, min(r_j + 5, len(all_cand_nums))):
                                    if abs((all_cand_nums[r_i] - all_cand_nums[r_j]) - all_cand_nums[r_k]) <= 1.5 and all_cand_nums[r_k] > 0:
                                        found_rev_row = all_cand_nums[r_k]
                                        break
                                if found_rev_row is not None:
                                    break
                            if found_rev_row is not None:
                                break

                        # Check 2: Revenue from Operations + Other Income = Total Income (Row 0 + Row 1 = Row 2)
                        if found_rev_row is None:
                            for r_i in range(min(12, len(all_cand_nums))):
                                for r_j in range(r_i + 1, min(r_i + 5, len(all_cand_nums))):
                                    sum_ij = round(all_cand_nums[r_i] + all_cand_nums[r_j], 2)
                                    for r_k in range(r_j + 1, min(r_j + 5, len(all_cand_nums))):
                                        if abs(all_cand_nums[r_k] - sum_ij) <= 1.5:
                                            found_rev_row = all_cand_nums[r_i]
                                            break
                                    if found_rev_row is not None:
                                        break
                                if found_rev_row is not None:
                                    break

                        if found_rev_row is not None:
                            rev = found_rev_row * mult
                        else:
                            large = [n for n in all_cand_nums if n > (found_pbt or 0)]
                            if large:
                                rev = large[0] * mult
                            elif all_cand_nums:
                                rev = all_cand_nums[0] * mult
                    elif rev is None and parsed_nums:
                        cand_revs = [n for n in parsed_nums[:8] if n > (found_pbt or 0)]
                        if cand_revs:
                            rev = cand_revs[0] * mult
                        elif parsed_nums:
                            rev = parsed_nums[0] * mult

                    if eps is None and len(parsed_nums) >= 23:
                        eps = parsed_nums[-2]
                    if equity_cap is None and len(parsed_nums) >= 22:
                        equity_cap = parsed_nums[-3] * mult

            # Calculate EBIT: PBT + Finance Costs (if both present)
            if pbt is not None and finance_costs is not None:
                ebit = pbt + finance_costs
            elif pbt is not None:
                ebit = pbt

            # Sanity check: Accounting cross-check
            if rev is not None and rev <= 0:
                rev = None

            if rev is None and pat is None:
                continue

            # Build shares outstanding proxy if equity capital and face value exist
            shares_out = None
            if equity_cap is not None and equity_cap > 0:
                # Standard face value is Rs. 10 or Rs. 5 or Rs. 2; Rs 1 Cr / 10 = 0.1 Cr shares = 10 Lakh shares
                # Store shares in raw count or leave shares to existing master if unknown
                shares_out = round((equity_cap * 10_000_000) / 10.0, 0)

            bcast_ts = str(ann_meta.get("broadCastDate") or ann_meta.get("an_dt") or f"{period_end}T12:00:00")
            avail_dt = _parse_exchange_date(ann_meta.get("filingDate") or ann_meta.get("an_dt")) or str(period_end)
            seq_id = str(ann_meta.get("seqNumber") or ann_meta.get("seq_id") or "1")

            record = RawFinancialRecord(
                symbol=symbol.upper(),
                source="NSE_ANNOUNCEMENT_PDF",
                period_end_date=period_end,
                period_type=st_type,
                consolidation=basis,
                revenue=round(rev, 4) if rev is not None else None,
                net_profit=round(pat, 4) if pat is not None else None,
                ebit=round(ebit, 4) if ebit is not None else None,
                total_debt=None,  # Balance sheet debt not present in quarterly Reg 33 P&L table
                total_equity=round(total_equity, 4) if total_equity is not None else None,
                operating_cash_flow=None,
                eps=round(eps, 2) if eps is not None else None,
                shares_outstanding=shares_out,
                availability_date=avail_dt,
                broadcast_timestamp=bcast_ts,
                unit="crores",
                currency="INR",
                source_document_hash=pdf_hash,
                filing_id=f"NSE_PDF_{symbol.upper()}_{seq_id}_{period_end}",
                revision_indicator="N",
                raw_fact_details={
                    "page_number": page_idx + 1,
                    "extracted_via": "ExchangePdfParser_v1.0",
                    "pbt_cr": round(pbt, 4) if pbt is not None else None,
                    "depreciation_cr": round(depr, 4) if depr is not None else None,
                    "finance_costs_cr": round(finance_costs, 4) if finance_costs is not None else None,
                    "attachment_url": ann_meta.get("attchmntFile") or ann_meta.get("attachmentUrl"),
                },
            )

            is_valid, _ = validate_semantic_record(record)
            if is_valid:
                records.append(record)
                # Early stop: halt processing once Consolidated results (or both Consolidated and Standalone) are captured
                has_cons = any(r.consolidation == ConsolidationType.CONSOLIDATED for r in records)
                has_std = any(r.consolidation == ConsolidationType.STANDALONE for r in records)
                if has_cons:
                    logger.debug(f"🎯 [PDF_PARSER] {symbol}: Consolidated financial statement extracted on page {page_idx + 1}. Halting further page extraction.")
                    break

        # Strategy C: Certified SEBI Reg 33 Outcome / Press Release Fallback
        # When table pages are scanned raster images, official financial results are frequently disclosed
        # in the accompanying certified press release or board outcome announcement text.
        if not records:
            doc_text = ""
            for p in reader.pages:
                try:
                    p_txt = p.extract_text() or ""
                    if p_txt:
                        doc_text += "\n" + p_txt
                except Exception:
                    pass

            if doc_text and any(k in doc_text.lower() for k in ["profit after tax", "revenue", "net profit"]):
                clean_doc = _normalize_ocr_text(doc_text)

                period_end = None
                if ann_meta.get("toDate"):
                    period_end = _parse_exchange_date(ann_meta.get("toDate"))
                elif ann_meta.get("period_end_date"):
                    period_end = _parse_exchange_date(ann_meta.get("period_end_date"))
                if not period_end or len(period_end) != 10:
                    # Text is already OCR-normalized via clean_doc — use _parse_exchange_date universally
                    for d_pat in [
                        r"\b(?:ended|as\s+at|ended\s+on)\s*([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s*202\d)",
                        r"\b(?:ended|as\s+at|ended\s+on)\s*([A-Za-z]+\s+[0-9]{1,2}(?:st|nd|rd|th)?,?\s*202\d)",
                        r"\b([0-9]{1,2}(?:st|nd|rd|th)?\s+(?:January|February|March|April|May|June|July|August|September|October|November|December),?\s*202\d)\b",
                        r"\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+[0-9]{1,2}(?:st|nd|rd|th)?,?\s*202\d)\b",
                        r"\b([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s*202\d)",
                        r"\b([A-Za-z]+\s+[0-9]{1,2}(?:st|nd|rd|th)?,?\s*202\d)",
                    ]:
                        d_m = re.search(d_pat, clean_doc, re.I)
                        if d_m:
                            cand = _parse_exchange_date(d_m.group(1))
                            if cand and cand.endswith(("-03-31", "-06-30", "-09-30", "-12-31")):
                                period_end = cand
                                break

                if period_end and len(period_end) == 10:
                    pr_pat_m = re.search(
                        r"(?:consolidated\s+)?profit after tax[^:\d\n]{0,35}[:\s]+(?:₹|rs\.?)?\s*([\d,]+\.?\d*)\s*(crore|cr|lakh|million)?",
                        clean_doc, re.I
                    )
                    pr_rev_m = re.search(
                        r"(?:consolidated\s+)?revenue\d?[^:\d\n]{0,35}[:\s]+(?:₹|rs\.?)?\s*([\d,]+\.?\d*)\s*(crore|cr|lakh|million)?",
                        clean_doc, re.I
                    )
                    if pr_pat_m and pr_rev_m:
                        pat_val = _clean_token_number(pr_pat_m.group(1))
                        rev_val = _clean_token_number(pr_rev_m.group(1))
                        pat_unit = (pr_pat_m.group(2) or "crore").lower()
                        rev_unit = (pr_rev_m.group(2) or "crore").lower()

                        def _pr_mult(u: str) -> float:
                            if "lakh" in u:
                                return 0.01
                            if "million" in u:
                                return 0.1
                            return 1.0

                        if pat_val is not None and rev_val is not None and rev_val > 0:
                            c_pat = round(pat_val * _pr_mult(pat_unit), 4)
                            c_rev = round(rev_val * _pr_mult(rev_unit), 4)
                            bcast_ts = str(ann_meta.get("broadCastDate") or ann_meta.get("an_dt") or f"{period_end}T12:00:00")
                            avail_dt = _parse_exchange_date(ann_meta.get("filingDate") or ann_meta.get("an_dt")) or str(period_end)
                            seq_id = str(ann_meta.get("seqNumber") or ann_meta.get("seq_id") or "1")

                            basis = (
                                ConsolidationType.CONSOLIDATED
                                if "consolidated" in pr_pat_m.group(0).lower() or "consolidated" in pr_rev_m.group(0).lower()
                                else ConsolidationType.STANDALONE
                            )
                            st_type = "QUARTERLY" if period_end.endswith(("-06-30", "-09-30", "-12-31")) else "ANNUAL"

                            pr_rec = RawFinancialRecord(
                                symbol=symbol.upper(),
                                source="NSE_ANNOUNCEMENT_PDF",
                                period_end_date=period_end,
                                period_type=st_type,
                                consolidation=basis,
                                revenue=c_rev,
                                net_profit=c_pat,
                                ebit=None,
                                total_debt=None,
                                total_equity=None,
                                operating_cash_flow=None,
                                eps=None,
                                shares_outstanding=None,
                                availability_date=avail_dt,
                                broadcast_timestamp=bcast_ts,
                                unit="crores",
                                currency="INR",
                                source_document_hash=pdf_hash,
                                filing_id=f"NSE_PDF_{symbol.upper()}_{seq_id}_{period_end}",
                                revision_indicator="N",
                                raw_fact_details={
                                    "page_number": 1,
                                    "extracted_via": "ExchangePdfParser_v1.0_PressReleaseFallback",
                                    "attachment_url": ann_meta.get("attchmntFile") or ann_meta.get("attachmentUrl"),
                                },
                            )
                            is_valid, _ = validate_semantic_record(pr_rec)
                            if is_valid:
                                records.append(pr_rec)
                                logger.info(
                                    f"📑 [PDF_PARSER] {symbol}: Extracted via Press Release Fallback ({period_end}): "
                                    f"Rev = {c_rev} Cr, PAT = {c_pat} Cr"
                                )

        return records

    def fetch_pdf_fallback_records(
        self,
        symbol: str,
        announcements: List[Dict[str, Any]],
        existing_xml_periods: Set[str],
        min_cutoff: Optional[str] = None,
    ) -> List[RawFinancialRecord]:
        """
        Executes the Tier-3 Fallback:
        Checks official announcements with PDF attachments ONLY for periods where
        neither NSE XML nor BSE XML was provided.
        """
        sym_u = symbol.strip().upper()
        pdf_records: List[RawFinancialRecord] = []

        def _score_ann(ann: Dict[str, Any]) -> int:
            d = str(ann.get("desc") or "").lower()
            t = str(ann.get("attchmntText") or "").lower()
            fn = str(ann.get("attchmntFile") or ann.get("attachmentUrl") or "").lower()
            score = 0
            if "financial result" in d or "financial results" in d or "financial result" in t or "financial results" in t:
                score += 30
            if "reg 33" in d or "regulation 33" in d or "reg 33" in t or "regulation 33" in t:
                score += 25
            if "unaudited" in d or "unaudited" in t or "audited" in d or "audited" in t:
                score += 15
            if "outcome of board meeting" in d or "outcome of board meeting" in t:
                score += 10
            if "standalone" in fn or "consolidated" in fn or "results" in fn:
                score += 10
            if "intimation" in fn or "intimation" in d:
                score -= 10
            if any(k in f"{d} {t} {fn}" for k in ("newspaper", "clipping", "clarification", "presentation", "press release", "transcript")):
                score -= 30
            return score

        sorted_announcements = sorted(announcements, key=_score_ann, reverse=True)
        downloaded_pdf_count = 0

        for ann in sorted_announcements:
            if not isinstance(ann, dict):
                continue

            # Check up to top 3 scored announcement attachments (stops early as soon as results are extracted)
            if downloaded_pdf_count >= 3:
                break

            # If we already parsed records for the requested cutoff, early exit immediately
            if pdf_records and any(r.period_end_date >= (min_cutoff or "") for r in pdf_records):
                break

            att_file = str(ann.get("attchmntFile") or ann.get("attachmentUrl") or "").strip()
            att_lower = att_file.lower()
            if not (".pdf" in att_lower or "annpdfopen" in att_lower):
                continue

            # Period end
            p_end = _parse_exchange_date(ann.get("period_end_date") or ann.get("toDate"))
            if not p_end or len(p_end) != 10:
                desc = str(ann.get("desc") or "")
                txt = str(ann.get("attchmntText") or "")
                dm = re.search(r"(\d{4}-\d{2}-\d{2})", f"{desc} {txt}")
                if dm:
                    p_end = dm.group(1)

            # [RULE ENFORCEMENT]: MUST ONLY BE USED IF NSE AND BSE BOTH FAILED FOR XML
            # If this period is already covered by a valid XML record, skip PDF parsing!
            if p_end and p_end in existing_xml_periods:
                continue

            if min_cutoff and p_end and p_end < min_cutoff:
                continue

            # Check if this announcement is a financial results / board meeting outcome
            desc_comb = f"{ann.get('desc', '')} {ann.get('attchmntText', '')}".lower()
            att_lower = att_file.lower()

            # Exclude non-statement attachments (newspaper publications, earnings calls, presentations, transcripts)
            if any(k in desc_comb or k in att_lower for k in [
                "newspaper", "clipping", "advertisement", "publication of",
                "investor presentation", "investor update", "earnings call",
                "transcript", "audio recording", "concall", "con. call", "press release"
            ]):
                continue

            if not (ann.get("hasXbrl") or any(k in desc_comb for k in [
                "financial result", "financial results", "outcome of board meeting",
                "unaudited financial", "audited financial", "period ended"
            ])):
                continue

            logger.info(
                f"📄 [PDF_FALLBACK] {sym_u}: Official exchange PDF found for period {p_end or 'UNKNOWN'} "
                f"(XML missing on NSE & BSE). Downloading {os.path.basename(att_file)}..."
            )

            downloaded_pdf_count += 1
            pdf_bytes = self.download_pdf(att_file, announcement_metadata=ann)
            if not pdf_bytes:
                continue

            parsed = self.parse_sebi_reg33_pdf(pdf_bytes, sym_u, announcement_metadata=ann)
            existing_scopes = {(r.period_end_date, r.consolidation) for r in pdf_records}
            for rec in parsed:
                rec_scope = (rec.period_end_date, rec.consolidation)
                if rec_scope not in existing_scopes:
                    logger.info(
                        f"✨ [PDF_FALLBACK: SUCCESS] {sym_u} {rec.period_end_date} ({rec.period_type} {rec.consolidation}): "
                        f"Revenue = {rec.revenue} Cr, Net Profit = {rec.net_profit} Cr, EPS = {rec.eps}"
                    )
                    pdf_records.append(rec)
                    existing_scopes.add(rec_scope)

            # If desired period was successfully parsed, early exit to avoid downloading more PDFs
            if pdf_records and any(r.period_end_date >= (min_cutoff or "") for r in pdf_records):
                break

        return pdf_records
