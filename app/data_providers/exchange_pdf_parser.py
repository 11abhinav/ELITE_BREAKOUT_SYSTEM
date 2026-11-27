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
import shutil
import hashlib
import logging
import subprocess
import tempfile
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
try:
    from PIL import Image
    import pytesseract
    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False
import threading
import concurrent.futures

os.environ.setdefault("OMP_THREAD_LIMIT", "1")

_HAS_PDFTOTEXT = bool(shutil.which("pdftotext"))
_HAS_PDFTOPPM = bool(shutil.which("pdftoppm"))

# Concurrency semaphores: cap simultaneous heavy PDF parses on cloud containers to prevent CPU/RAM thrash
_DOWNLOAD_SEMAPHORE = threading.Semaphore(12)
_PARSE_SEMAPHORE = threading.Semaphore(int(os.getenv("PDF_PARSE_CONCURRENCY", "4")))


def _extract_pdf_pages_fast(pdf_path: str, max_pages: int = 20, timeout: float = 8.0) -> Optional[Dict[int, str]]:
    """
    Uses Poppler C++ `pdftotext` subprocess to extract pages 1..max_pages in ~0.1-0.4s with
    zero Python GIL overhead and OS-level SIGKILL enforcement on timeout (zero wedged threads).
    """
    if not _HAS_PDFTOTEXT or not pdf_path:
        return None
    try:
        res = subprocess.run(
            ["pdftotext", "-layout", "-f", "1", "-l", str(max_pages), pdf_path, "-"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode != 0 or not res.stdout:
            return None
        raw = res.stdout.decode("utf-8", errors="ignore")
        # Poppler separates pages with form-feed (\x0c)
        chunks = raw.split("\x0c")
        if chunks and chunks[-1] == "":
            chunks = chunks[:-1]
        return {idx: txt for idx, txt in enumerate(chunks[:max_pages])}
    except Exception:
        return None


def _extract_page_layout_fast(pdf_path: str, page_idx: int, timeout: float = 3.5) -> str:
    """Runs Poppler `pdftotext -layout` for a single 0-indexed page with SIGKILL timeout."""
    if not _HAS_PDFTOTEXT or not pdf_path:
        return ""
    try:
        p1 = str(page_idx + 1)
        res = subprocess.run(
            ["pdftotext", "-layout", "-f", p1, "-l", p1, pdf_path, "-"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0 and res.stdout:
            return res.stdout.decode("utf-8", errors="ignore")
    except Exception:
        pass
    return ""


_UPSIDE_DOWN_TOKENS = (
    "alindi", "suopelado", "s3liigyt", "powpneun", "suomesado", "suadx3", "juauneis",
    "sepicmber", "papuz", "a1eus", "asuadx", "asuadr", "ousoout", "popsad", "sumupuos",
    "onsusyasduoa", "aeduoa", "s3l", "poysad",
)


def _score_ocr_readability(txt: str) -> int:
    """Scores OCR orientation quality using clean financial/document anchors rather than raw length."""
    if not txt or len(txt.strip()) < 40:
        return -1000
    low = re.sub(r"\s+", " ", txt.lower())
    _pos_terms = (
        "revenue from operations", "total income", "interest earned", "profit before tax",
        "profit/(loss)", "profit / (loss)", "profit for the period", "profit after tax",
        "net profit", "independent auditor", "limited review", "based on our review conducted",
        "assets and liabilities", "cash flows from operating", "statement of cash flows",
        "cost of revenues", "earnings per share", "total expenses", "particulars",
    )
    score = sum(25 for k in _pos_terms if k in low)
    if "revenues" in low and ("profit before" in low or "income tax" in low):
        score += 30
    score += min(10, len(re.findall(r"\b202\d\b", low))) * 4
    score -= sum(40 for k in _UPSIDE_DOWN_TOKENS if k in low)
    return score


def _ocr_image_with_osd(img: "Image.Image", timeout: float = 12.0, prefer_180: bool = False) -> str:
    """
    Runs Tesseract OCR on a PIL image and automatically detects/corrects 90/180/270-degree
    rotated landscape financial tables via Tesseract OSD and targeted orientation fallback
    when upright OCR yields gibberish (e.g. upright letterhead above rotated statement table).
    """
    if not _OCR_AVAILABLE or img is None:
        return ""
    txt = pytesseract.image_to_string(img, timeout=timeout, config="--psm 6") or ""
    low = re.sub(r"\s+", " ", txt.lower())
    _has_inverted = any(k in low for k in _UPSIDE_DOWN_TOKENS)
    _strong_upright = (
        "revenue from operations", "total income", "interest earned", "profit before tax",
        "profit for the period", "profit/(loss) for the period", "net profit",
        "independent auditor", "limited review", "based on our review conducted",
        "balance sheet", "assets and liabilities", "assets & liabilities",
        "cash flows from operating", "statement of cash flows",
        "dear sir", "dear madam", "listing department",
    )
    _is_ifrs_upright = ("revenues" in low and ("profit before" in low or "income tax" in low))
    if not _has_inverted and len(txt.strip()) >= 150 and (_is_ifrs_upright or any(k in low for k in _strong_upright)):
        return txt.strip()

    best_txt = txt.strip()
    best_score = _score_ocr_readability(best_txt)
    tried_degs: Set[int] = {0}

    # If inverted upside-down glyphs detected in embedded text or 0-deg pass, probe 180 first (~3-4s)
    if prefer_180 or _has_inverted:
        tried_degs.add(180)
        try:
            rot_180 = img.rotate(-180, expand=True)
            txt_180 = pytesseract.image_to_string(rot_180, timeout=min(9.0, timeout), config="--psm 6") or ""
            low_180 = re.sub(r"\s+", " ", txt_180.lower())
            if len(txt_180.strip()) >= 120 and (any(k in low_180 for k in _strong_upright) or ("revenues" in low_180 and "profit" in low_180)):
                return txt_180.strip()
            sc_180 = _score_ocr_readability(txt_180)
            if sc_180 > best_score:
                best_score = sc_180
                best_txt = txt_180.strip()
        except Exception:
            pass

    try:
        osd_raw = pytesseract.image_to_osd(img, timeout=4.0)
        m_rot = re.search(r"Rotate:\s*(\d+)", str(osd_raw))
        deg = int(m_rot.group(1)) if m_rot else 0
        if deg in (90, 180, 270) and deg not in tried_degs:
            tried_degs.add(deg)
            rot_img = img.rotate(-deg, expand=True)
            rot_txt = pytesseract.image_to_string(rot_img, timeout=timeout, config="--psm 6") or ""
            rot_low = re.sub(r"\s+", " ", rot_txt.lower())
            if len(rot_txt.strip()) >= 120 and any(k in rot_low for k in _strong_upright):
                return rot_txt.strip()
            sc_rot = _score_ocr_readability(rot_txt)
            if sc_rot > best_score or (sc_rot == best_score and len(rot_txt.strip()) > len(best_txt)):
                best_score = sc_rot
                best_txt = rot_txt.strip()
    except Exception:
        pass

    # Mixed pages (upright letterhead + rotated landscape/upside-down table) often fool OSD into returning 0.
    # Probe remaining orthogonal angles and return immediately on first financial table hit.
    probe_order = (180, 270, 90) if (prefer_180 or _has_inverted) else ((270, 90, 180) if img.width >= img.height else (90, 270, 180))
    for deg in probe_order:
        if deg in tried_degs:
            continue
        tried_degs.add(deg)
        try:
            rot_img = img.rotate(-deg, expand=True)
            rot_txt = pytesseract.image_to_string(rot_img, timeout=min(9.0, timeout), config="--psm 6") or ""
            rot_low = re.sub(r"\s+", " ", rot_txt.lower())
            if len(rot_txt.strip()) >= 120 and (any(k in rot_low for k in _strong_upright) or ("revenues" in rot_low and "profit" in rot_low)):
                return rot_txt.strip()
            sc_rot = _score_ocr_readability(rot_txt)
            if sc_rot > best_score or (sc_rot == best_score and len(rot_txt.strip()) > len(best_txt) * 1.15):
                best_score = sc_rot
                best_txt = rot_txt.strip()
        except Exception:
            pass
    return best_txt


def _render_page_image_fast(pdf_path: str, page_idx: int, timeout: float = 6.0, dpi: int = 150) -> Optional["Image.Image"]:
    """Renders a composite full-page PNG via `pdftoppm` for multi-strip scanned PDFs."""
    if not _OCR_AVAILABLE or not _HAS_PDFTOPPM or not pdf_path:
        return None
    tmp_dir = None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="pdfppm_")
        out_prefix = os.path.join(tmp_dir, "page")
        p1 = str(page_idx + 1)
        res = subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), "-singlefile", "-f", p1, "-l", p1, pdf_path, out_prefix],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        png_path = f"{out_prefix}.png"
        if res.returncode == 0 and os.path.exists(png_path):
            with Image.open(png_path) as im:
                return im.copy()
    except Exception:
        pass
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return None


def _safe_extract_text(
    page,
    timeout: float = 3.5,
    extraction_mode: Optional[str] = None,
    pdf_path: Optional[str] = None,
    page_idx: Optional[int] = None,
) -> str:
    """
    Extracts text from a single PDF page with hard timeout isolation.
    Prefers Poppler `pdftotext` subprocess (SIGKILL on hang, zero thread leaks);
    falls back to an isolated per-call executor that never poisons a global thread pool.
    """
    if pdf_path and page_idx is not None and _HAS_PDFTOTEXT:
        if extraction_mode == "layout":
            txt = _extract_page_layout_fast(pdf_path, page_idx, timeout=timeout)
            if txt:
                return txt
        else:
            try:
                p1 = str(page_idx + 1)
                res = subprocess.run(
                    ["pdftotext", "-f", p1, "-l", p1, pdf_path, "-"],
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                )
                if res.returncode == 0 and res.stdout is not None:
                    return res.stdout.decode("utf-8", errors="ignore")
            except Exception:
                pass

    if page is None:
        return ""

    def _do_extract():
        try:
            if extraction_mode:
                return page.extract_text(extraction_mode=extraction_mode) or ""
            return page.extract_text() or ""
        except Exception:
            try:
                return page.extract_text() or ""
            except Exception:
                return ""

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf_pg_iso")
    try:
        future = pool.submit(_do_extract)
        return future.result(timeout=timeout) or ""
    except concurrent.futures.TimeoutError:
        return ""
    except Exception:
        return ""
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _is_char_fragmented(text: str) -> bool:
    """Detects PDFs where pypdf extracts one character per line (e.g. 'A\\nY\\nE\\n...')."""
    if not text:
        return False
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 40:
        return False
    short_lines = sum(1 for ln in lines[:200] if len(ln) <= 2)
    sample_len = min(len(lines), 200)
    return (short_lines / max(1, sample_len)) >= 0.35


def _is_garbled_embedded_ocr(text: str) -> bool:
    """
    Detects PDF pages whose embedded OCR text layer is severely corrupted (e.g. 'i S e s Pt 112',
    corrupted column dates like '0 Sepicmber 2036', missing/garbled PAT or Revenue labels like
    'promm betore tax'/'prfit(Loss)'/'Revenve Gom', or upside-down mirrored glyphs) so Phase 3
    runs fresh image RE_OCR instead of trusting broken embedded text.
    """
    s = (text or "").strip()
    if len(s) < 50:
        return False
    low = re.sub(r"\s+", " ", s.lower())
    dec_nums = re.findall(r"\b\d[\d,]*\.\d{1,2}\b", s)
    toks = low.split()
    short_noise = sum(1 for t in toks if len(t.strip(".,;:|[](){}-_=+*\"'")) <= 2)
    short_ratio = short_noise / max(1, len(toks))

    # Upside-down / mirrored glyph layers (rotated landscape tables read upright)
    if any(k in low for k in _UPSIDE_DOWN_TOKENS):
        return True

    # Heavy 1-2 char fragment noise without real decimal table rows
    if len(toks) >= 25 and short_ratio >= 0.32 and len(dec_nums) < 4:
        return True

    # Embedded OCR punctuation corruption inside words or replacement glyph (\ufffd)
    inline_punct_corrupt = len(re.findall(r"[a-zA-Z][\\':;\^~`\*!\ufffd][a-zA-Z]", s))
    if "\ufffd" in s or inline_punct_corrupt >= 5:
        return True

    # Broken financial statement OCR: missing clean revenue OR missing clean profit anchor alongside garble signatures
    has_clean_rev = any(k in low for k in (
        "revenue from operations", "interest earned", "total income", "net sales",
        "net premium income", "total revenue from operations", "income from operations",
    ))
    has_clean_profit = any(k in low for k in (
        "profit before tax", "profit/(loss)", "profit / (loss)", "profit/ (loss)",
        "profit for the period", "profit after tax", "net profit", "profit from ordinary",
    ))
    if not (has_clean_rev and has_clean_profit):
        garble_hits = len(re.findall(
            r"\b(?:203\d|200[0-9]|0\s+sep|sepicmber|seplember|revenve|revenv|totalincome|totatncome|"
            r"brofit|prfit|promm|profibetors|stanbatone|coreotbated|woaudited|audired|powpneun|suomesado|juauneis|"
            r"operatians|unauctited|rcvcnt|tollll|llncomc)\b",
            low,
        ))
        if garble_hits >= 2 or (not has_clean_rev and len(dec_nums) < 4 and "financial" in low) or (inline_punct_corrupt >= 2 and not has_clean_rev and "statement" in low):
            return True
    return False


def _is_fiscal_period_end(iso_date: Optional[str]) -> bool:
    """Returns True only if YYYY-MM-DD represents a standard Indian fiscal quarter/year end date."""
    if not iso_date or len(iso_date) != 10:
        return False
    try:
        mm = int(iso_date[5:7])
        dd = int(iso_date[8:10])
    except Exception:
        return False
    return mm in (3, 6, 9, 12) and dd >= 28

# Size & scanning parameters:
# Official SEBI Reg 33 outcome statements are typically 1-5MB, but high-DPI scans can reach 15-20MB.
# We set a generous 25MB ceiling for results to ensure zero valid disclosures are dropped.
_PDF_MAX_RESULT_BYTES = 50 * 1024 * 1024  # 50 MB for confirmed Financial Results (covers high-DPI scans)
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
    # Normalize "Sept" -> "Sep" so datetime.strptime("%b") matches
    clean_d = re.sub(r"(?i)\bsept\.?(?=\s+\d|\s*202)", "Sep", clean_d)
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


def _decode_truetype_shifted_glyphs(text: str) -> str:
    """Decodes Windows TrueType subset shifted glyphs (Identity-H without ToUnicode map)."""
    if not text:
        return ""
    if not ("8QDXGLWHG" in text or "5HYHQXH" in text or "\x11\x1b" in text or "7RWDO" in text or "3URILW" in text):
        return text
    out = []
    for c in text:
        o = ord(c)
        if 14 <= o <= 60:
            out.append(chr(o + 29))
        elif 68 <= o <= 90 or 100 <= o <= 122:
            out.append(chr(o - 3))
        elif o == 3:
            out.append(" ")
        elif o == 11:
            out.append("(")
        elif o == 12:
            out.append(")")
        elif o == 18:
            out.append("-")
        else:
            out.append(c)
    return "".join(out)


def _clean_token_number(token: str) -> Optional[float]:
    """Parses a numerical token from a table line, handling (negative) and comma separators."""
    if not token:
        return None
    token = token.strip().lstrip("·•₹t#@~_`*|[]")
    # Filter out tokens containing alphabetical letters or bracket equations like [5-6]
    if re.search(r"[a-zA-Z]", token) or "[" in token or "]" in token:
        return None
    is_neg = (token.startswith("(") and token.endswith(")")) or token.startswith("-") or token.endswith(")")
    # Multi-dot fix: keep only the last dot as decimal point, drop prior dots (thousands separators in Indian accounting)
    token = re.sub(r"\.(?=.*\.)", "", token)
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
    text = _decode_truetype_shifted_glyphs(text)
    # --- Universal glyph substitution (applies to ALL scanned exchange PDFs) ---
    text = text.replace("€", "e").replace("$", "s").replace("~", "").replace("•", "").replace("·", "")
    text = re.sub(r"(\d)\s*[_]\s*(\d)", r"\g<1>.\g<2>", text)
    text = re.sub(r"(\d)[tIl](\d)", r"\g<1>1\g<2>", text)

    # --- Spaced-out numbers cleanup (e.g. '17,483 .09' -> '17,483.09', '4 ,385.86' -> '4,385.86') ---
    text = re.sub(r"\b(\d{1,2}),(\d{2})\.(202\d)\b", r"\1.\2.\3", text)  # OCR date comma: 30,09.2026 -> 30.09.2026
    text = re.sub(r"(?<=\d);(?=\d)", ",", text)                         # OCR semicolon: 13;646.84 -> 13,646.84
    text = re.sub(r"(?<=\d):(?=\d{2}(?:\s|$|\)))", ".", text)           # OCR colon decimal: 1,069:04 -> 1,069.04
    text = re.sub(r"(?<=\d)\s*,\s*(?=\d)", ",", text)
    text = re.sub(r"(?<=\d),(?=\d{2}(?:\s|$|\)))", ".", text)           # OCR comma decimal: 276,90 -> 276.90
    text = re.sub(r'(\d+[\d,]*)\s*\.\s*(\d+)', r'\1.\2', text)
    # Repair OCR dot/comma swaps in Indian numbers: '1.29,783' -> '1,29,783', '68.547.37' -> '68,547.37', '5.484 42' -> '5,484.42'
    text = re.sub(r'\b(\d{1,3})\.(\d{2,3}),(\d{3})\b', r'\1,\2,\3', text)
    text = re.sub(r'\b(\d{1,3})\.(\d{3})\.(\d{2})\b', r'\1,\2.\3', text)
    text = re.sub(r'(?<![\d\.])(\d{1,2})\.(\d{3})\s+(\d{2})(?=\s|$|\))', r'\1,\2.\3', text)
    text = re.sub(r'(?<![\d\.])(\d+,\d{3})\s+(\d{2})(?=\s|$|\))', r'\1.\2', text)
    # Fix spaced numbers in Indian balance sheets: "1 747.30" -> "1747.30", "4 882.27" -> "4882.27"
    text = re.sub(r"(?<![\d\.])(\d{1,2})\s+(\d{3}(?:[,\.]\d{2})?\b)", r"\1\2", text)

    # Clean OCR vertical bar artifacts before words (e.g. lTotal -> Total, |Income -> Income)
    text = re.sub(r"\b[l|1iI](total|income|revenue|other|profit|expenses)\b", r"\1", text, flags=re.I)
    # Repair common Tesseract 1/l/i/c confusions in key P&L anchor words
    text = re.sub(r"\b(?:operatlons|operalions|operat1ons|opciat1011s|operattons)\b", "operations", text, flags=re.I)
    text = re.sub(r"\b(?:lncome|inc\.?ornc|incomc|1ncome)\b", "income", text, flags=re.I)
    text = re.sub(r"\b(?:rcvcnt1c|revenuc|revcnue)\b", "revenue", text, flags=re.I)
    text = re.sub(r"\b(?:tollll|tolal|tota1)\b", "total", text, flags=re.I)
    # Split merged label words (e.g. expensesProfit -> expenses Profit)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Formula equation removal (e.g. (3-4), (1+2), (5-6), (III-IV)) — require an arithmetic operator (-+*/) so unit captions like (Zin lacs) are NEVER stripped!
    text = re.sub(r"\(\s*(?:\d{1,2}|[IVXivx]{1,4}|[A-Za-z])\s*[-+*/]\s*(?:\d{1,2}|[IVXivx]{1,4}|[A-Za-z])(?:\s*[-+*/]\s*(?:\d{1,2}|[IVXivx]{1,4}|[A-Za-z]))*\s*\)", "", text)

    # --- Universal kerning de-spacing for corporate financial statement vocabulary ---
    _FINANCIAL_DE_SPACE_TERMS = [
        'statement', 'unaudited', 'audited', 'financial', 'results', 'result',
        'half', 'year', 'quarter', 'ended', 'period', 'particulars', 'revenue',
        'income', 'operations', 'expenses', 'profit', 'loss', 'before', 'after',
        'exceptional', 'extraordinary', 'tax', 'taxes', 'earnings', 'share', 'shares',
        'nominal', 'basic', 'diluted', 'equity', 'liabilities', 'assets',
        'capital', 'reserves', 'surplus', 'borrowings', 'provisions', 'payables',
        'receivables', 'inventories', 'cash', 'balances', 'operating', 'ordinary', 'activities',
        'september', 'december', 'march', 'june', 'january', 'february',
        'april', 'august', 'october', 'november', 'comprehensive', 'continuing', 'discontinued'
    ]
    for term in _FINANCIAL_DE_SPACE_TERMS:
        pattern = r'\b' + r'\s*'.join(list(term)) + r'\b'
        text = re.sub(pattern, term, text, flags=re.I)

    # --- Month name OCR damage (generic for any stock/filing) ---
    text = re.sub(r"(?i)\bmon[l1|t]{2,3}s\b", "months", text)
    text = re.sub(r"(?i)\bsjx\b", "six", text)
    text = re.sub(r"(?i)\bthrec\b", "three", text)
    text = re.sub(r"['\`~>]{1,2}(?=\d{1,2}[\.,/-]\d{1,2}[\.,/-]202\d)", "", text)
    text = re.sub(r"\bSep[rt]e?mber\b", "September", text)         # Seprember, Seprtmber, Septmber
    text = re.sub(r"\bSEP[RT]E?MBER\b", "SEPTEMBER", text)
    text = re.sub(r"(?i)\bsep[rt]e?mber\b", "September", text)
    text = re.sub(r"(?i)\bseptermber\b", "September", text)         # septermber
    text = re.sub(r"(?i)\bseptember\s+jo\b", "September 30", text)  # OCR: SEPTEMBER JO -> SEPTEMBER 30
    text = re.sub(r"(?i)\b(september|june|december)\s*[,\./\*]+\s*(\d{2})\b", r"\1 \2", text)
    text = re.sub(r"(?i)\bfeburary\b", "February", text)
    text = re.sub(r"(?i)\bjanurary\b", "January", text)
    text = re.sub(r"(?i)\bDecemher\b", "December", text)            # h<->b
    text = re.sub(r"(?i)\bNovemher\b", "November", text)

    # --- Ordinal suffix OCR damage: 30lh -> 30th, 31sl -> 31st ---
    text = re.sub(r"\b(\d{1,2})lh\b", r"\1th", text)                # l confused with t
    text = re.sub(r"\b(\d{1,2})sl\b", r"\1st", text)                # s+l confused with s+t
    text = re.sub(r"\b(\d{1,2})nd\b", r"\1nd", text)                # keep nd
    text = re.sub(r"\b(\d{1,2})rd\b", r"\1rd", text)                # keep rd

    text = re.sub(r"(?i)\bRESTJLTS\b", "results", text)
    text = re.sub(r"(?i)\bSepiember\b|\bSEPIEMBER\b", "September", text)
    text = re.sub(r"\b(30|31)\s*SEPIEMBER\b", r"\1 September", text, flags=re.I)
    text = re.sub(r"(?i)\boperatioas\b", "operations", text)
    text = re.sub(r"(?i)\broral\b", "total", text)
    text = re.sub(r"(?i)\brncome\b|\brNcoME\b", "income", text)
    text = re.sub(r"(?i)\bPERTOD\b", "period", text)
    text = re.sub(r"\b(\d{2})1(\d{2})1(202\d)\b", r"\1/\2/\3", text)
    text = re.sub(r"(?i)\bmllllon\b|m[il1]{3,5}on\b", "million", text)
    text = re.sub(r"(?i)\bUnaudlted\b|\bUNAUDlTED\b", "unaudited", text)
    text = re.sub(r"(?i)\bin\s+LaRU\b", "in Lakhs", text)
    text = re.sub(r"(?i)\bLaRU\b", "Lakhs", text)

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
    text = re.sub(r"\bop[ce]r[a-z]{2,6}ns\b", "operations", text, flags=re.I)
    text = re.sub(r"\bopera\s+tions\b", "operations", text, flags=re.I)
    text = re.sub(r"\boper[--\.\d]+tions\b", "operations", text, flags=re.I)
    text = re.sub(r"\bfinan\s+cial\b", "financial", text, flags=re.I)
    text = re.sub(r"\bunaudit\s+ed\b", "unaudited", text, flags=re.I)
    text = re.sub(r"\bunaud\s*ited\b", "unaudited", text, flags=re.I)
    text = re.sub(r"\bs[ir],?ltmcnl\b", "statement", text, flags=re.I)
    text = re.sub(r"\bsi,lcmcnl\b", "statement", text, flags=re.I)
    text = re.sub(r"(?:\b[lI1\|])?[,;:\.'’]rofi[tl1]\b", "profit", text, flags=re.I)
    text = re.sub(r"\b[no]rofit\b", "profit", text, flags=re.I)
    text = re.sub(r"\bpro\s*fit\b", "profit", text, flags=re.I)
    text = re.sub(r"\bprofl[~t]\b", "profit", text, flags=re.I)
    text = re.sub(r"\bp\'ofit\b", "profit", text, flags=re.I)
    text = re.sub(r"\bprofi[1tl]\b|\bprof[il1]t\b", "profit", text, flags=re.I)
    text = re.sub(r"\bproft\b", "profit", text, flags=re.I)
    text = re.sub(r"[\(\{\[]\s*lo\s*ss\s*[\)\}\]]", "(loss)", text, flags=re.I)
    text = re.sub(r"\bnet profit[!/|]\s*\(loss\)", "net profit / (loss)", text, flags=re.I)
    text = re.sub(r"\bl+nter[ueo]t\s*income\b", "interest income", text, flags=re.I)
    text = re.sub(r"\blotber\s*income\b", "other income", text, flags=re.I)
    text = re.sub(r"\bfotal\s*expenses\b", "total expenses", text, flags=re.I)
    text = re.sub(r"\bexce[op]tional\b", "exceptional", text, flags=re.I)
    text = re.sub(r"\bth\s+e\b|\b1he\b|\blhe\b", "the", text, flags=re.I)
    text = re.sub(r"\bb[ce]f[ao]re\b", "before", text, flags=re.I)
    text = re.sub(r"\bprofic\b", "profit", text, flags=re.I)
    text = re.sub(r"\byea\s+r\b", "year", text, flags=re.I)
    text = re.sub(r"\bparti\s+culars\b", "particulars", text, flags=re.I)
    text = re.sub(r"\bstanda\s*lone\b", "standalone", text, flags=re.I)
    text = re.sub(r"\bconsoli\s*dated\b", "consolidated", text, flags=re.I)
    text = re.sub(r"\b[cr]onsolidar[ce]d\b", "consolidated", text, flags=re.I)
    text = re.sub(r"\bta[•\*\.]\b", "tax", text, flags=re.I)
    text = re.sub(r",-1(\d{2})\b", r",4\1", text)
    text = re.sub(r"(?i)\btotalincome\b|\btotatncome\b", "total income", text)
    text = re.sub(r"(?i)\breven[uv][ce]\b|\brevcn[uv]e\b|\brevenuc\b", "revenue", text)
    text = re.sub(r"(?i)\boperat[il1]ans\b|\boperatians\b|\bop[ce]rat[a-z1]{2,6}ns\b", "operations", text)
    text = re.sub(r"(?i)\b([a-z]+)\s+gom\s+operations\b", r"\1 from operations", text)
    text = re.sub(r"(?i)\bmill[il1c]{1,2}[eoc][nr]s?\b", "million", text)
    text = re.sub(r"(?i)\bsep[il1r][ce]?mber\b|\bseplember\b", "September", text)
    text = re.sub(r"(?i)\bbrofit\b|\bprofibet[oa]rs\b", "profit", text)
    text = re.sub(r"(?i)\bnded\b", "ended", text)
    return text


def score_page_content(text: str) -> Tuple[int, ConsolidationType]:
    """
    Weighted candidate page scoring algorithm for SEBI LODR Regulation 33 Financial Statements.
    Enforces Safeguard A: Weighted multi-signal scoring rather than rigid co-occurrence.
    Ensures actual P&L financial results pages always rank above Auditor Review Reports,
    Balance Sheets, Cash Flows, Notes, and Regulation 32 Deviation statements.
    """
    if not text or len(text.strip()) < 30:
        return 0, ConsolidationType.STANDALONE

    text_norm = _normalize_ocr_text(text)
    text_lower = text_norm.lower()
    norm_text_lower = re.sub(r"\s+", " ", text_lower)

    score = 0

    # 1. Financial statement heading (+40)
    if any(k in norm_text_lower for k in [
        "statement of", "financial results", "financial result",
        "results for the quarter", "results for the period",
        "unaudited results", "audited results", "financial performance",
        "statement of profit and loss", "profit and loss statement"
    ]):
        score += 40

    # 2. Revenue / Income keywords (+30)
    has_rev_kw = any(k in norm_text_lower for k in [
        "revenue from operations", "net sales", "total income",
        "income from operations", "interest earned", "total revenue from operations",
        "total revenue", "interest income", "net premium income", "gross premium income"
    ])
    if has_rev_kw:
        score += 30

    # 3. PAT / PBT profit keywords (+30)
    if any(k in norm_text_lower for k in [
        "profit for the period", "profit after tax", "net profit",
        "profit before tax", "profit/(loss)", "profit / (loss)",
        "profit/ (loss)", "profit (loss)", "net profit for the period",
        "net profit / (loss)", "net profit/(loss)", "profit before exceptional"
    ]):
        score += 30

    # 4. Period / Date indicators (+15)
    if any(k in norm_text_lower for k in [
        "quarter ended", "half year ended", "period ended", "year ended",
        "months ended", "ended on"
    ]) or re.search(r"\b(?:30|31)[\./-](?:09|06|03|12)[\./-]202\d\b", norm_text_lower) or re.search(r"\b(?:september|june|march|december)\s+\d{1,2},?\s*202\d\b", norm_text_lower):
        score += 15

    # 5. Standalone / Consolidated basis (+15)
    basis = ConsolidationType.STANDALONE
    first_lines = text_norm.split("\n")[:12]
    header_area = " ".join(first_lines).lower()
    if "consolidated" in header_area:
        basis = ConsolidationType.CONSOLIDATED
        score += 15
    elif "standalone" in header_area:
        basis = ConsolidationType.STANDALONE
        score += 15
    elif "consolidated" in norm_text_lower and "standalone" not in norm_text_lower:
        basis = ConsolidationType.CONSOLIDATED
        score += 15
    elif "standalone" in norm_text_lower:
        basis = ConsolidationType.STANDALONE
        score += 15

    # 6. Supporting financial metrics (+10 / +15 strict P&L table structure)
    has_pl_table_rows = any(k in norm_text_lower for k in [
        "total expenses", "expenses", "tax expense", "earnings per share",
        "basic eps", "paid-up equity", "equity share capital"
    ])
    if has_pl_table_rows:
        score += 10

    has_strict_pnl_structure = (
        any(k in norm_text_lower for k in ("total expenses", "total expenditure", "fotal expenses", "cost of materials", "employee benefits expense"))
        and any(k in norm_text_lower for k in ("revenue from operations", "interest earned", "total revenue from operations", "net premium income", "total income", "net sales"))
    )
    if has_strict_pnl_structure or ("particulars" in norm_text_lower and any(k in norm_text_lower for k in ("total expenses", "profit before tax"))):
        score += 15

    # 7. Penalty for Cover / Transmittal Letters & Auditor Limited Review Reports
    head_half = " ".join(text_norm.split("\n")[:25]).lower().replace("&", " and ")
    norm_and_lower = norm_text_lower.replace("&", " and ")
    is_cover_letter = any(k in norm_and_lower for k in (
        "dear sir", "dear madam", "dear sirs", "yours faithfully", "yours sincerely",
        "listing department", "corporate relationship department", "exchange plaza", "dalal street"
    )) and not any(k in norm_and_lower for k in ("total expenses", "profit before tax", "profit/(loss)", "particulars"))
    if is_cover_letter:
        score -= 80

    if any(k in head_half for k in [
        "independent auditor", "limited review report", "auditor's review report",
        "chartered accountants", "we have reviewed the accompanying", "limited review conducted",
        "based on our review conducted", "procedures performed as stated", "nothing has come to our attention",
    ]) and not has_strict_pnl_structure:
        score -= 85

    # 8. Penalty for Cash Flow Statement / Balance Sheet / Reg 32 / Ratio / Annexure Notes without P&L structure
    # Note: Ind-AS Cash Flows always list 'finance costs' and 'profit before tax', so guard with has_strict_pnl_structure.
    if any(k in norm_and_lower for k in [
        "cash flows from operating activities", "cash flow from operating activities",
        "statement of cash flows", "cash flow statement", "cash flow from operating",
        "operating profit before working capital", "operating cash flows before working capital",
        "cash flows from investing activities", "cash flows from financing activities",
    ]) and not has_strict_pnl_structure:
        score -= 120

    if any(k in head_half for k in [
        "statement of assets and liabilities", "assets and liabilities", "a ts and liabilities",
        "balance sheet", "non-current assets", "non-current assels", "equity and liabilities"
    ]) and not has_strict_pnl_structure and not any(k in norm_and_lower for k in ["revenue from operations", "interest earned"]):
        score -= 70

    if any(k in head_half for k in [
        "deviation or variation", "statement of deviation", "analytical ratios",
        "pursuant to regulation 52(4)", "regulation 52 (4)", "ratio analysis", "annexure i"
    ]) and not has_strict_pnl_structure:
        score -= 65

    # 9. Penalty for standalone Notes pages (prose paragraphs explaining revenue/tax figures, not financial table rows)
    first_12_lower = " ".join(text_norm.split("\n")[:12]).lower()
    is_notes_only = (
        bool(re.search(r"(?:^|\b)(?:notes?\s*[:\-]|[\ufffd\W]?otes\s*[:1\-]|\[\s*notes?\b|\bnotes?\s+to\s+(?:the\s+)?(?:unaudited|audited|standalone|consolidated|statement|financial))", first_12_lower))
        or ("the above standalone financial results" in norm_and_lower and "total expenses" not in norm_and_lower)
        or ("the above consolidated financial results" in norm_and_lower and "total expenses" not in norm_and_lower)
        or ("have been prepared in accordance with indian accounting" in norm_and_lower and "total expenses" not in norm_and_lower)
        or ("reviewed by the audit committee" in norm_and_lower and "chief operating decision maker" in norm_and_lower)
        or ("approved by the board of directors" in norm_and_lower and "limited review" in norm_and_lower and "total expenses" not in norm_and_lower)
    )
    if is_notes_only and not has_strict_pnl_structure and "total expenses" not in norm_and_lower:
        score -= 75

    return score, basis


def is_financial_result_announcement(ann: Optional[Dict[str, Any]], pdf_url: str = "") -> bool:
    """
    Checks announcement subject, description, and file metadata to determine if the document
    is an official SEBI LODR Regulation 33 Financial Results / Board Meeting Outcome statement.
    """
    desc = str(((ann.get("desc") or ann.get("headline") or ann.get("HEADLINE") or ann.get("NEWSSUB")) if ann else "") or "").lower()
    text = str((ann.get("attchmntText") if ann else "") or "").lower()
    fn = str((ann.get("attchmntFile") or ann.get("attachmentUrl") if ann else "") or "").lower()
    url_fn = os.path.basename(str(pdf_url).split("?")[0]).lower()
    combined = f"{desc} {text} {fn} {url_fn}"

    if url_fn.endswith(".zip") or (fn.endswith(".zip") and ".pdf" not in combined):
        return False

    # Negative check: skip non-statement attachments (press release, transcript, concall, presentation, newspaper,
    # monitoring agency, investor handouts, non-financial board actions, nomenclature corrections,
    # Reg 74(5)/Asset Cover, Structural Digital Database (SDD) certificates, trading window notices)
    if any(k in combined for k in [
        "newspaper", "clipping", "advertisement", "publication of",
        "investor presentation", "investor update", "investor handout", "investor_handout", "investorhandout",
        "monitoring agency", "monitoringagency",
        "earnings call", "transcript", "audio recording", "video recording", "concall", "con. call", "press release",
        "correction_of_nomenclature", "correction of nomenclature", "nomenclature_correction", "nomenclature correction",
        "change in management", "change_in_management", "change of management",
        "regulation 74", "reg 74", "reg. 74", "74(5)", "74_5",
        "dp_regulation", "dp regulation", "depository participant",
        "asset cover", "assetcover", "security cover", "securitycover",
        "structural digital database", "regulation 3(5)", "regulation 3(6)",
        "_sdd", "sdd.pdf", "intimation_sdd", "compliance certificate",
        "reconciliation of share capital", "closure of trading window", "tradingwindow", "trading window",
    ]):
        return False

    has_explicit_fin_keyword = any(k in combined for k in [
        "financial result", "financial results", "reg 33", "regulation 33",
        "unaudited financial", "audited financial", "results for the quarter",
        "results for the period", "quick results", "quickresult", "statement of financial"
    ])
    if not has_explicit_fin_keyword and any(k in combined for k in [
        "acquisition", "disinvestment", "sale or disposal", "retirement",
        "allotment", "esop", "postal ballot", "agm", "egm", "record date",
        "credit rating", "resignation", "appointment of", "_app_of_", " app of ",
        "trading window", "bm_app_", "re-appointment", "reappointment",
        "business update", "businessupdate",
        "incorporation of", "wholly-owned subsidiary", "wholly owned subsidiary",
    ]):
        return False

    # Positive check: subject / description / filename indicates financial results
    return has_explicit_fin_keyword or any(k in combined for k in [
        "outcome of board meeting", "board meeting outcome",
        "quarter ended", "period ended", "ufr", "afr", "fr_dsc", "results"
    ]) or bool(ann and ann.get("hasXbrl"))


def record_extraction_failure_rca(symbol: str, reason_code: str, details: str, source: str = "EXCHANGE_FILINGS"):
    """
    Records an extraction or availability failure under error maintenance (fetch_errors & scan_failures)
    so the Admin Error UI and audit logs clearly show what was attempted and the exact RCA why it failed.
    """
    if not symbol:
        return
    sym_u = symbol.strip().upper()
    try:
        try:
            from app.database import upsert_fetch_error, get_connection
        except ImportError:
            from database import upsert_fetch_error, get_connection

        error_msg = f"[{reason_code}] {details}"
        upsert_fetch_error(
            source_name=source,
            scanner_name="FILING_WATCHER",
            symbol=sym_u,
            interval="1D",
            category=reason_code,
            error_msg=error_msg,
        )

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO scan_failures (scan_id, scanner_name, symbol, provider, failure_reason, failed_at)
                    VALUES (%s, %s, %s, %s, %s, NOW());
                """, (
                    f"FAIL_PDF_{sym_u}_{int(time.time())}",
                    "FILING_WATCHER",
                    sym_u,
                    source,
                    error_msg[:1000],
                ))
                conn.commit()
        logger.warning(f"❌ [FILING_WATCHER: FAILURE_RCA] {sym_u} | Reason: {reason_code} | Details: {details}")
    except Exception as e:
        logger.debug(f"[FAILURE_RCA] Could not record DB failure for {sym_u}: {e}")


def clear_extraction_failure_rca(symbol: str, source: str = "EXCHANGE_FILINGS"):
    """Clears any previous extraction failures from fetch_errors once fresh data is extracted."""
    if not symbol:
        return
    sym_u = symbol.strip().upper()
    try:
        try:
            from app.database import get_connection
        except ImportError:
            from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM fetch_errors
                    WHERE source_name = %s AND scanner_name = %s AND symbol = %s;
                """, (source, "FILING_WATCHER", sym_u))
                conn.commit()
    except Exception as e:
        logger.debug(f"[FAILURE_RCA] Could not clear fetch_errors for {sym_u}: {e}")


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
        self.last_failure_reason: Dict[str, Tuple[str, str]] = {}
        self._failed_pdf_hashes: Set[Tuple[str, str]] = set()
        self._failed_hash_lock = threading.Lock()

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
        pdf_url_low = str(pdf_url or "").lower()
        if not pdf_url or not (".pdf" in pdf_url_low or "annpdfopen" in pdf_url_low):
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

        # Acquire download semaphore without skipping (with 45s safety timeout)
        if not _DOWNLOAD_SEMAPHORE.acquire(timeout=45.0):
            logger.warning(f"[PDF_PARSER] Download semaphore busy (>45s wait) for {base_name}. Skipping download.")
            return None
        try:
            # 1. Try ScraperAPI first (Indian proxy bypasses foreign VPS Akamai/NSE tarpitting)
            fetch_via_scraperapi, get_scraper_api_key = _get_scraperapi_fetcher()
            if fetch_via_scraperapi and get_scraper_api_key():
                try:
                    s_resp = fetch_via_scraperapi(pdf_url, timeout=(40 if is_result else 18), country_code="in")
                    if s_resp is not None and s_resp.status_code == 200 and s_resp.content and s_resp.content.startswith(b"%PDF"):
                        if len(s_resp.content) <= ceiling_bytes and b"%%EOF" in s_resp.content[-2048:]:
                            with open(cache_path, "wb") as f_out:
                                f_out.write(s_resp.content)
                            logger.info(f"✅ [PDF:SCRAPERAPI] Downloaded exchange PDF {base_name} ({len(s_resp.content)} bytes, is_result={is_result})")
                            return s_resp.content
                        elif len(s_resp.content) > ceiling_bytes:
                            logger.warning(f"[PDF_PARSER] Skipping PDF {base_name}: ScraperAPI size {len(s_resp.content)/1024/1024:.1f}MB exceeds limit.")
                            return None
                        else:
                            logger.warning(f"[PDF_PARSER] ScraperAPI download for {base_name} truncated ({len(s_resp.content)} bytes, missing %%EOF).")
                except Exception as s_err:
                    logger.debug(f"[PDF_PARSER] ScraperAPI download failed for {base_name}: {s_err}")

            # 2. Direct fetch fallback from exchange static archives (8s connect, 60s read, with bounded 4MB Range resume for large PDFs)
            try:
                content = b""
                expected_cl = None
                _chunks: List[bytes] = []
                _tot = 0
                try:
                    with self.session.get(pdf_url, headers=_HEADERS, timeout=(8, 60), stream=True) as resp:
                        if resp.status_code == 200:
                            cl = resp.headers.get("Content-Length")
                            if cl:
                                expected_cl = int(cl)
                                if expected_cl > ceiling_bytes:
                                    logger.warning(
                                        f"[PDF_PARSER] Skipping PDF {base_name}: size {expected_cl/1024/1024:.1f}MB exceeds "
                                        f"{ceiling_bytes//1024//1024}MB limit (is_result={is_result})"
                                    )
                                    return None
                            for chunk in resp.iter_content(chunk_size=65536):
                                if not chunk:
                                    continue
                                _tot += len(chunk)
                                if _tot > ceiling_bytes:
                                    logger.warning(
                                        f"[PDF_PARSER] Skipping PDF {base_name}: exceeded "
                                        f"{ceiling_bytes//1024//1024}MB limit during stream (is_result={is_result})"
                                    )
                                    return None
                                _chunks.append(chunk)
                except Exception as _stream_err:
                    logger.debug(f"[PDF_PARSER] Initial stream interrupted for {base_name} at {_tot} bytes: {_stream_err}")
                content = b"".join(_chunks)

                # If a large exchange PDF stream truncated mid-transfer (e.g. Akamai 8MB cap), resume in bounded Range chunks
                # Retry transient chunk failures (instead of breaking on first empty/exception) and prefer Accept-Encoding: identity.
                try:
                    from curl_cffi import requests as _cffi_rng_reqs
                except Exception:
                    _cffi_rng_reqs = None

                _consec_rng_fails = 0
                for _rng_attempt in range(20):
                    if not (content.startswith(b"%PDF") and b"%%EOF" not in content[-8192:]):
                        break
                    if expected_cl and len(content) >= expected_cl:
                        break
                    _start = len(content)
                    _chunk_span = 2 * 1024 * 1024  # 2MB chunks survive Akamai mid-stream drops much better than 4MB
                    _end = _start + _chunk_span - 1
                    if expected_cl:
                        _end = min(_end, expected_cl - 1)
                    _rng_hdrs = {
                        **_HEADERS,
                        "Range": f"bytes={_start}-{_end}",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                    }
                    _extra: List[bytes] = []
                    _extra_tot = 0
                    try:
                        # Prefer curl_cffi Chrome TLS on Range requests (Akamai honors 206 Range on Chrome TLS
                        # even when standard requests.get receives ignored 200 full-stream truncation at 8.2MB).
                        if _cffi_rng_reqs is not None and (_consec_rng_fails % 2 == 0 or _consec_rng_fails >= 1):
                            _cr = _cffi_rng_reqs.get(
                                pdf_url,
                                headers=_rng_hdrs,
                                impersonate="chrome",
                                timeout=(10, 60),
                            )
                            if _cr.status_code in (200, 206):
                                if _cr.status_code == 200 and len(_cr.content) > len(content) and b"%%EOF" in _cr.content[-8192:]:
                                    content = _cr.content
                                    break
                                if _cr.status_code == 206 and _cr.content:
                                    _extra.append(_cr.content)
                        else:
                            with requests.get(pdf_url, headers=_rng_hdrs, cookies=self.session.cookies, timeout=(10, 60), stream=True) as r_res:
                                if r_res.status_code == 200:
                                    logger.warning(f"[PDF_PARSER] Range chunk {_rng_attempt+1} for {base_name} ignored Range (status=200)")
                                    _consec_rng_fails += 1
                                    if _consec_rng_fails >= 3 or _cffi_rng_reqs is None:
                                        break
                                    time.sleep(0.5)
                                    continue
                                if r_res.status_code not in (206,):
                                    logger.warning(f"[PDF_PARSER] Range chunk {_rng_attempt+1} for {base_name} returned status={r_res.status_code}")
                                    _consec_rng_fails += 1
                                    if _consec_rng_fails >= 3:
                                        break
                                    time.sleep(1.0 * _consec_rng_fails)
                                    continue
                                for chunk in r_res.iter_content(chunk_size=65536):
                                    if not chunk:
                                        continue
                                    _extra_tot += len(chunk)
                                    if len(content) + _extra_tot > ceiling_bytes:
                                        return None
                                    _extra.append(chunk)
                    except Exception as _rng_err:
                        logger.warning(f"[PDF_PARSER] Range chunk {_rng_attempt+1} interrupted for {base_name}: {_rng_err}")
                    if not _extra:
                        _consec_rng_fails += 1
                        if _consec_rng_fails >= 3:
                            break
                        time.sleep(1.0 * _consec_rng_fails)
                        continue
                    _consec_rng_fails = 0
                    content = content + b"".join(_extra)

                # If requests session failed on direct stream or Range, try one clean Chrome-impersonated curl_cffi full fetch
                if (not content.startswith(b"%PDF") or b"%%EOF" not in content[-8192:]) and _cffi_rng_reqs is not None:
                    try:
                        _cr_full = _cffi_rng_reqs.get(pdf_url, headers=_HEADERS, impersonate="chrome", timeout=(12, 90))
                        if _cr_full.status_code == 200 and _cr_full.content and _cr_full.content.startswith(b"%PDF"):
                            if len(_cr_full.content) <= ceiling_bytes and b"%%EOF" in _cr_full.content[-8192:]:
                                content = _cr_full.content
                    except Exception as _cffi_err:
                        logger.debug(f"[PDF_PARSER] curl_cffi fallback failed for {base_name}: {_cffi_err}")

                if content.startswith(b"%PDF"):
                    if b"%%EOF" in content[-8192:]:
                        with open(cache_path, "wb") as f_out:
                            f_out.write(content)
                        logger.info(f"✅ [PDF:DIRECT] Downloaded exchange PDF {base_name} ({len(content)} bytes, is_result={is_result})")
                        return content
                    else:
                        # Last-resort ScraperAPI retry with extended timeout for >8MB result PDFs truncated by Akamai
                        if is_result and fetch_via_scraperapi and get_scraper_api_key():
                            try:
                                s_resp2 = fetch_via_scraperapi(pdf_url, timeout=65, country_code="in")
                                if s_resp2 is not None and s_resp2.status_code == 200 and s_resp2.content and s_resp2.content.startswith(b"%PDF") and len(s_resp2.content) <= ceiling_bytes and b"%%EOF" in s_resp2.content[-8192:]:
                                    with open(cache_path, "wb") as f_out:
                                        f_out.write(s_resp2.content)
                                    logger.info(f"✅ [PDF:SCRAPERAPI_RETRY] Downloaded large exchange PDF {base_name} ({len(s_resp2.content)} bytes)")
                                    return s_resp2.content
                            except Exception:
                                pass
                        logger.warning(f"[PDF_PARSER] Direct download for {base_name} truncated ({len(content)} bytes, missing %%EOF).")
            except Exception as direct_err:
                logger.debug(f"[PDF_PARSER] Direct download failed for {base_name}: {direct_err}")

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

        # Acquire parse semaphore with timeout (300s safety timeout to prevent dropping queued worker threads)
        if not _PARSE_SEMAPHORE.acquire(timeout=300.0):
            logger.warning(f"[PDF_PARSER] {symbol}: Parse semaphore busy (>300s wait). Skipping PDF.")
            if hasattr(self, "last_failure_reason"):
                self.last_failure_reason[symbol] = ("SEMAPHORE_TIMEOUT", "Parse semaphore busy (>300s wait). Skipping PDF.")
            return []
        tmp_pdf_path: Optional[str] = None
        try:
            if _HAS_PDFTOTEXT or (_OCR_AVAILABLE and _HAS_PDFTOPPM):
                try:
                    clean_bytes = pdf_bytes
                    eof_pos = clean_bytes.rfind(b"%%EOF")
                    if eof_pos != -1:
                        clean_bytes = clean_bytes[:eof_pos + 5]
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
                        tf.write(clean_bytes)
                        tmp_pdf_path = tf.name
                except Exception:
                    tmp_pdf_path = None

            records = self._parse_sebi_reg33_pdf_inner(pdf_bytes, pdf_hash, ann_meta, symbol, tmp_pdf_path=tmp_pdf_path)
            if records:
                logger.info(f"📄 [PDF_PARSER] {symbol}: Successfully extracted {len(records)} record(s) from PDF ({len(pdf_bytes)} bytes)")
            else:
                logger.debug(f"ℹ️ [PDF_PARSER] {symbol}: 0 structured statement records extracted from PDF ({len(pdf_bytes)} bytes).")
            return records
        except Exception as e:
            logger.warning(f"[PDF_PARSER] {symbol}: Error during PDF parsing: {e}")
            return []
        finally:
            if tmp_pdf_path and os.path.exists(tmp_pdf_path):
                try:
                    os.unlink(tmp_pdf_path)
                except Exception:
                    pass
            _PARSE_SEMAPHORE.release()

    def _parse_sebi_reg33_pdf_inner(
        self,
        pdf_bytes: bytes,
        pdf_hash: str,
        ann_meta: Dict[str, Any],
        symbol: str,
        tmp_pdf_path: Optional[str] = None,
    ) -> List["RawFinancialRecord"]:
        """Inner parse logic, runs under _PARSE_SEMAPHORE — do not call directly."""
        records: List["RawFinancialRecord"] = []
        t_parse_start = time.time()

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
                if hasattr(self, "last_failure_reason"):
                    self.last_failure_reason[symbol] = (
                        "PDF_CANNOT_BE_READ",
                        f"PDF cannot be read (corrupt or invalid PDF structure: {pdf_err}).",
                    )
                logger.warning(f"[PDF_PARSER] PDF cannot be read for {symbol}: {pdf_err}")
                return []

        total_doc_pages = len(reader.pages)
        max_scan_pages = min(32, total_doc_pages)

        # Fast C++ batch page text extraction via Poppler `pdftotext` (~0.1-0.5s for 32 pages, SIGKILL on hang)
        cached_page_texts: Dict[int, str] = {}
        if tmp_pdf_path and _HAS_PDFTOTEXT:
            fast_pages = _extract_pdf_pages_fast(tmp_pdf_path, max_pages=max_scan_pages, timeout=10.0)
            if fast_pages:
                for p_idx, p_t in fast_pages.items():
                    if _is_char_fragmented(p_t):
                        lay_t = _extract_page_layout_fast(tmp_pdf_path, p_idx, timeout=3.5)
                        if lay_t and len(lay_t.strip()) >= 50:
                            p_t = lay_t
                    cached_page_texts[p_idx] = p_t

        # Determine document-wide unit and multiplier (explicit patterns only, scanning digital pages beyond cover letters)
        doc_mult = 1.0
        doc_unit = "crore"
        _UNIT_MILLION_RE = r"(?:in\s*m[il1]{1,2}[lr1]{0,2}[il1][co][nr]s?|\([\{\[\*a-z0-9\|%\s]{0,8}in\s*m[il1]{1,2}[lr1]{0,2}[il1][co][nr]s?[^\)\n]*\)|(?:rs\.?|inr|n|r|z|a)\s*(?:in\s*)?m[il1]{1,2}[lr1]{0,2}[il1][co][nr]s?\b|(?:\d[\d,]*\s*mn\b|\bin\s*mn\b|\(\s*mn\s*\)))"
        _UNIT_LAKH_RE = r"(?:\([a-z%tflz\?\'\s]{0,5}in\s*l(?:[an]khs?|acs?)\)|(?:\b[ztf%]{1,2}|\brs\.?|\binr)\s*in\s*l(?:[an]khs?|acs?)\b|\bin\s+l(?:[an]khs?|acs?)\b|\bl(?:[an]khs?|acs?)\s*(?:except|unless)\b|(?:\bin\s+|/\s*\d*\s*)luc[,\.\s]|luc[^\w\n]{0,4}ex)"
        _UNIT_CRORE_RE = r"(?:\([a-z%tflz\?\'\s]{0,5}in\s*cr[o0]ree?s?\)|\b(?:rs\.?|inr|z|t|f)\s*(?:in|ln)?\s*cr[o0]ree?s?\b|\b(?:in|ln)\s*cr[o0]ree?s?\b|<n\s*crore|\bcr[o0]res?\s*(?:except|unless)\b)"

        # Phase 2: Lightweight document-wide text pre-scan & candidate scoring
        # Enforces Safeguard A: Multi-signal weighted scoring under strict bounded budget
        page_scores: List[Tuple[int, int, ConsolidationType]] = []
        garbled_pages: Set[int] = set()
        non_pnl_pages: Set[int] = set()
        zero_text_page_count = 0

        def _is_scanned_or_sig_only(txt: str) -> bool:
            s = (txt or "").strip()
            if len(s) < 50:
                return True
            return len(s) < 300 and "digitally signed" in s.lower()

        t_prescan_start = time.time()
        for p_idx in range(max_scan_pages):
            if p_idx not in cached_page_texts and (time.time() - t_prescan_start > 15.0 or time.time() - t_parse_start > 22.0):
                logger.debug(f"[PDF_PARSER] {symbol}: Pre-scan reached budget at page {p_idx+1}/{total_doc_pages}. Proceeding with scored pages.")
                break
            p_text = cached_page_texts.get(p_idx)
            if p_text is None:
                p_text = _safe_extract_text(reader.pages[p_idx], timeout=3.5, pdf_path=tmp_pdf_path, page_idx=p_idx)
                if _is_char_fragmented(p_text):
                    lay_t = _safe_extract_text(reader.pages[p_idx], timeout=3.5, extraction_mode="layout", pdf_path=tmp_pdf_path, page_idx=p_idx)
                    if lay_t and len(lay_t.strip()) >= 50:
                        p_t_clean = lay_t
                        p_text = p_t_clean
                cached_page_texts[p_idx] = p_text
            if _is_scanned_or_sig_only(p_text):
                zero_text_page_count += 1
            sc, b = score_page_content(p_text)
            if sc < 0:
                non_pnl_pages.add(p_idx)
            elif _is_garbled_embedded_ocr(p_text):
                garbled_pages.add(p_idx)
                # Garbled financial-statement pages (e.g. broken scanner OCR overlay or rotated P&L table)
                # must jump ahead of blank/trailing pages for clean 200 DPI OSD RE_OCR.
                if p_idx >= 1 or "financial result" in (p_text or "").lower():
                    p_low_hdr = (p_text or "").lower()[:600]
                    boost_sc = 85 if ("statement of" in p_low_hdr or "financial result" in p_low_hdr) else 60
                    sc = max(sc, boost_sc)
            if sc >= 30:
                page_scores.append((p_idx, sc, b))

        doc_sample_text = "\n".join(cached_page_texts.get(p, "") or "" for p in range(max_scan_pages))
        if re.search(_UNIT_MILLION_RE, doc_sample_text, re.I):
            doc_mult = 0.1
            doc_unit = "million"
        elif re.search(_UNIT_LAKH_RE, doc_sample_text, re.I):
            doc_mult = 0.01
            doc_unit = "lakh"
        elif re.search(_UNIT_CRORE_RE, doc_sample_text, re.I):
            doc_mult = 1.0
            doc_unit = "crore"

        _rev_kws = (
            "revenue from operations", "net sales", "total income", "income from operations",
            "interest earned", "interest income", "total revenue", "premium earned", "insurance revenue",
            "net premium income", "gross premium income", "first year premium", "renewal premium",
        )
        _auditor_markers = (
            "independent auditor", "limited review", "review report", "auditor's report",
            "sre 2410", "chartered accountants", "udin:", "membership no",
            "based on our review conducted", "procedures performed as stated", "nothing has come to our attention",
        )

        # Early exit for Regulation 30 non-financial announcements (e.g. subsidiary incorporation)
        _head_sample = re.sub(
            r"\s+", " ",
            " ".join((cached_page_texts.get(p, "") or "") for p in range(min(3, total_doc_pages))).lower(),
        )
        if (
            not page_scores
            and len(_head_sample) >= 200
            and any(k in _head_sample for k in (
                "incorporation of a wholly-owned subsidiary", "details of subsidiary to be incorporated",
                "reconciliation of share capital", "certificate under regulation 74",
            ))
            and not any(k in _head_sample for k in _rev_kws)
            and "profit before tax" not in _head_sample
        ):
            if hasattr(self, "last_failure_reason"):
                self.last_failure_reason[symbol] = (
                    "NON_FINANCIAL_ANNOUNCEMENT",
                    "Skipped non-financial Regulation 30 corporate announcement (no financial results table).",
                )
            logger.info(f"⏭️ [PDF_PARSER] {symbol}: Skipping non-financial Reg 30 announcement.")
            return []

        # Candidate selection (Safeguard A): prioritize digital/text statement pages FIRST in <1s
        # before falling back to Tesseract OCR for any missing scanned or garbled pages.
        doc_total_chars = sum(0 if _is_scanned_or_sig_only(t) else len(t.strip()) for t in cached_page_texts.values())
        has_digital_rev_page = any(
            any(k in (cached_page_texts.get(p, "")).lower() for k in _rev_kws)
            for p, sc, _ in page_scores if sc >= 55
        )
        is_pure_scanned = bool(doc_total_chars < 150 and zero_text_page_count >= max(1, len(cached_page_texts)))
        ocr_attempted_pages: Set[int] = set()

        # Defer bulky Phase 2 pre-OCR until after fast digital pages are attempted (prevents burning 189s on digital+cover hybrid PDFs like SBILIFE);
        # Only run up-front Phase 2 OCR when document has zero usable digital revenue pages.
        if is_pure_scanned and _OCR_AVAILABLE and (time.time() - t_parse_start < 45.0):
            ocr_recovered = False
            try:
                scan_candidates = [p for p in range(min(14, total_doc_pages)) if _is_scanned_or_sig_only(cached_page_texts.get(p, ""))]
                # Scan statement pages starting from page 2 (p>=1) before cover letter (p=0)
                scan_candidates.sort(key=lambda p: (0 if p >= 1 else 1, p))
                logger.info(f"🔍 [PDF_PARSER: OCR_ATTEMPT] {symbol}: Attempting OCR extraction on {len(scan_candidates[:10])} scanned image pages (pure_scanned=True)...")
                t_ocr_start = time.time()
                _ocr_rev_pages = 0
                for p_idx in scan_candidates[:10]:
                    if time.time() - t_ocr_start > 75.0 or time.time() - t_parse_start > 95.0:
                        logger.debug(f"[PDF_PARSER: OCR] {symbol}: OCR prescan reached budget at page {p_idx+1}. Breaking.")
                        break
                    ocr_attempted_pages.add(p_idx)
                    ocr_page_text = ""
                    comp_img = _render_page_image_fast(tmp_pdf_path, p_idx, timeout=8.0, dpi=150) if tmp_pdf_path else None
                    if comp_img is not None:
                        try:
                            if comp_img.width > 1600 or comp_img.height > 2000:
                                comp_img.thumbnail((1600, 2000))
                            t = _ocr_image_with_osd(comp_img, timeout=10.0)
                            if t and len(t.strip()) > 30:
                                ocr_page_text = t.strip()
                        except Exception:
                            pass
                    if not ocr_page_text:
                        page_obj = reader.pages[p_idx]
                        try:
                            page_images = getattr(page_obj, "images", [])
                        except Exception:
                            page_images = []
                        for img_obj in page_images[:2]:
                            try:
                                img = Image.open(io.BytesIO(img_obj.data))
                                if img.width >= 150 and img.height >= 150:
                                    if img.width > 1400 or img.height > 1800:
                                        img.thumbnail((1200, 1600))
                                    t = pytesseract.image_to_string(img, timeout=4.0, config="--psm 6")
                                    if t and len(t.strip()) > 30:
                                        ocr_page_text += "\n" + t.strip()
                            except Exception:
                                pass
                    if ocr_page_text:
                        cached_page_texts[p_idx] = ocr_page_text
                        _ocr_low = re.sub(r"\s+", " ", ocr_page_text.lower())
                        _has_rev = any(k in _ocr_low for k in _rev_kws)
                        _is_aud = any(k in _ocr_low[:1200] for k in _auditor_markers) and not _has_rev
                        if _is_aud:
                            continue
                        sc, b = score_page_content(ocr_page_text)
                        if sc >= 25:
                            page_scores.append((p_idx, sc, b))
                            ocr_recovered = True
                            if _has_rev and sc >= 50:
                                _ocr_rev_pages += 1
                                if _ocr_rev_pages >= 2:
                                    break
                if ocr_recovered:
                    logger.info(f"✅ [PDF_PARSER: OCR_SUCCESS] {symbol}: OCR successfully recovered text across {len([p for p in page_scores if p[0] in scan_candidates])} scanned pages.")
            except Exception as _ocr_err:
                logger.debug(f"[PDF_PARSER: OCR] {symbol} OCR attempt note: {_ocr_err}")

            if not ocr_recovered:
                if hasattr(self, "last_failure_reason"):
                    self.last_failure_reason[symbol] = (
                        "PDF_CANNOT_BE_READ",
                        f"PDF cannot be read (scanned image without readable text/OCR across {zero_text_page_count}/{max_scan_pages} pages).",
                    )
                logger.warning(
                    f"⚠️ [PDF_PARSER: PDF_CANNOT_BE_READ] {symbol}: PDF cannot be read "
                    f"(scanned image without readable text across {zero_text_page_count}/{max_scan_pages} pages)."
                )
                return []

        # Candidate selection (Safeguard A): prioritize clean digital revenue P&L pages FIRST,
        # then garbled P&L statement pages for fresh RE_OCR, then other statement pages (p>=1), in descending score order.
        # Strictly exclude pages already identified in pre-scan as Cover/Auditor/CashFlow/BalanceSheet/Notes (non_pnl_pages).
        candidate_page_indices: List[int] = []
        _sorted_scores = sorted(
            page_scores,
            key=lambda x: (
                0 if (x[0] not in garbled_pages and any(k in (cached_page_texts.get(x[0], "")).lower() for k in _rev_kws)) else 1,
                0 if x[0] >= 1 else 1,
                -x[1],
            ),
        )
        for p_idx, sc, b in _sorted_scores:
            if p_idx not in non_pnl_pages and p_idx not in candidate_page_indices:
                candidate_page_indices.append(p_idx)
        for p_idx, sc, b in _sorted_scores:
            if p_idx + 1 < total_doc_pages and (p_idx + 1) not in non_pnl_pages and (p_idx + 1) not in candidate_page_indices:
                candidate_page_indices.append(p_idx + 1)

        # Append remaining scanned/unscored pages (2..max_scan_pages, then page 1), preferring pages right after
        # Auditor Reports (which often precede scanned P&L tables) and completely excluding known Cover/Auditor/CF/BS/Notes pages.
        _fallback_end = min(max_scan_pages, total_doc_pages)
        _tail_pages = [
            _fp for _fp in (list(range(1, _fallback_end)) + ([0] if _fallback_end > 0 else []))
            if _fp not in candidate_page_indices and _fp not in non_pnl_pages
        ]
        _tail_pages.sort(
            key=lambda fp: (
                0 if fp in garbled_pages else 1,
                0 if any(k in (cached_page_texts.get(fp, "")).lower() for k in _rev_kws) else 1,
                0 if (fp > 0 and any(m in (cached_page_texts.get(fp - 1, "")).lower()[:1200] for m in _auditor_markers)) else 1,
                0 if len((cached_page_texts.get(fp, "") or "").strip()) < 250 else 1,
                fp,
            )
        )
        candidate_page_indices.extend(_tail_pages)

        t_start = time.time()
        for page_idx in candidate_page_indices:
            page = reader.pages[page_idx]
            _p3_elapsed = time.time() - t_start
            if (records and _p3_elapsed > 25.0) or _p3_elapsed > 105.0 or (_p3_elapsed > 85.0 and time.time() - t_parse_start > 140.0):
                if records:
                    logger.info(
                        f"✅ [PDF_PARSER] {symbol}: Extracted {len(records)} record(s); "
                        f"stopping remaining tail scan at page {page_idx+1} ({_p3_elapsed:.1f}s)."
                    )
                else:
                    logger.warning(
                        f"⚠️ [PDF_PARSER] {symbol}: Exceeded parse budget on page {page_idx+1} "
                        f"(0 records extracted). Breaking early."
                    )
                break

            # Smart early exit: finish immediately if both Consolidated and Standalone statements were extracted
            has_cons = any(r.consolidation == ConsolidationType.CONSOLIDATED for r in records)
            has_std = any(r.consolidation == ConsolidationType.STANDALONE for r in records)
            if has_cons and has_std:
                break

            if page_idx in cached_page_texts:
                text = cached_page_texts[page_idx]
            else:
                text = _safe_extract_text(page, timeout=3.5, pdf_path=tmp_pdf_path, page_idx=page_idx)
                if _is_char_fragmented(text):
                    lay_t = _safe_extract_text(page, timeout=3.5, extraction_mode="layout", pdf_path=tmp_pdf_path, page_idx=page_idx)
                    if lay_t and len(lay_t.strip()) >= 50:
                        text = lay_t
                cached_page_texts[page_idx] = text

            # Fast Digital Text First Invariant (with on-demand OCR fallback for scanned pages when digital didn't yield both statements yet)
            _prefer_180_hint = any(k in (text or "").lower() for k in _UPSIDE_DOWN_TOKENS)
            if _is_scanned_or_sig_only(text):
                if (not (has_cons and has_std)) and _OCR_AVAILABLE and tmp_pdf_path and page_idx >= 1 and _p3_elapsed < 90.0 and (time.time() - t_parse_start < 130.0):
                    try:
                        ocr_attempted_pages.add(page_idx)
                        _p3_img = _render_page_image_fast(tmp_pdf_path, page_idx, timeout=8.0, dpi=150)
                        if _p3_img is not None:
                            if _p3_img.width > 1600 or _p3_img.height > 2000:
                                _p3_img.thumbnail((1600, 2000))
                            _p3_ocr = _ocr_image_with_osd(_p3_img, timeout=10.0, prefer_180=_prefer_180_hint)
                            if _p3_ocr and not _is_scanned_or_sig_only(_p3_ocr):
                                text = _p3_ocr.strip()
                                cached_page_texts[page_idx] = text
                    except Exception:
                        pass
                if _is_scanned_or_sig_only(text):
                    continue

            _was_garbled_pre_ocr = (page_idx in garbled_pages) or _is_garbled_embedded_ocr(text)
            text = _normalize_ocr_text(text)
            text_lower = text.lower()
            norm_text_lower = re.sub(r"\s+", " ", text_lower)
            norm_and_lower = norm_text_lower.replace("&", " and ")

            # Skip standalone auditor review report or pure prose Notes pages that lack financial statement tables
            first_12_lines = text.split("\n")[:12]
            header_preamble = " ".join(first_12_lines).lower().replace("&", " and ")
            if any(k in header_preamble for k in ["independent auditor", "auditor's review report", "limited review report", "auditor's report", "based on our review conducted"]):
                if not any(k in header_preamble for k in ["statement of", "financial results", "unaudited financial", "audited financial"]):
                    continue
            first_8_lines_low = [ln.strip().lower() for ln in text.split("\n")[:8] if ln.strip()]
            if any(re.match(r"^(?:notes?|[\ufffd\W]?otes?)\s*[:1\-]?$", ln) for ln in first_8_lines_low):
                has_tab_rows = sum(1 for ln in text.split("\n")[:35] if re.match(r"^\s*(?:1\.|i\.|total income|revenue from operations)", ln.strip(), re.I)) >= 2
                if not has_tab_rows:
                    continue
            # Pass 1 extracts P&L statements; strictly skip Cover / Balance Sheet / Cash Flow / Segment / Notes / Auditor pages
            _page_head_1500 = norm_and_lower[:1500]
            _has_strict_pnl = (
                any(k in norm_and_lower for k in ("revenue from operations", "interest earned", "total revenue from operations", "net premium income", "net sales"))
                and any(k in norm_and_lower for k in ("total expenses", "total expenditure", "fotal expenses", "cost of materials", "employee benefits expense"))
            )
            if any(k in norm_and_lower for k in (
                "dear sir", "dear madam", "dear sirs", "yours faithfully", "yours sincerely", "listing department"
            )) and not _has_strict_pnl and "profit/(loss)" not in norm_and_lower and not _prefer_180_hint:
                continue
            if any(k in _page_head_1500 for k in [
                "statement of assets and liabilities", "assets and liabilities", "a ts and liabilities", "balance sheet",
                "non-current assets", "non-current assels", "equity and liabilities",
                "statement of cash flows", "cash flows -", "cash flow statement",
                "cash flow from operating activities", "cash flows from operating activities",
                "cash flows from investing activities", "cash flows from financing activities",
                "segment wise information", "segment revenue", "capital employed",
            ]):
                if "profit and loss" not in _page_head_1500 and not _has_strict_pnl and not _prefer_180_hint:
                    continue
            if not _has_strict_pnl and not _prefer_180_hint and any(k in _page_head_1500 for k in _auditor_markers):
                continue

            # Must contain SEBI Regulation 33 Statement header keywords (or unambiguous P&L / IFRS table structure)
            _has_ifrs_pnl = (
                bool(re.search(r"\brevenues\b", norm_text_lower))
                and any(k in norm_text_lower for k in ("profit before tax", "profit before income tax", "profit after tax", "profit for the period"))
            )
            is_stmt_header = any(k in norm_text_lower for k in [
                "statement of", "financial results", "financial result", "results for the quarter", "results for the period",
                "unaudited results", "audited results", "financial performance"
            ]) or (
                any(k in norm_text_lower for k in ("revenue from operations", "total income", "interest income", "gross premium"))
                and any(k in norm_text_lower for k in ("profit before tax", "profit for the period", "net profit", "profit after tax"))
            ) or _has_ifrs_pnl
            if not is_stmt_header and (page_idx == 0 or records) and not _was_garbled_pre_ocr:
                continue

            # Must contain actual operational revenue indicators (or banking/NBFC/IFRS revenue headers)
            _rev_indicators = (
                "revenue from operations", "net sales", "total income", "income from operations",
                "interest earned", "interest income", "total revenue from operations", "total revenue",
                "premium earned", "gross premium", "net premium", "insurance revenue", "total insurance revenue",
            )
            _has_rev_anchor = any(k in norm_text_lower for k in _rev_indicators) or _has_ifrs_pnl
            if _was_garbled_pre_ocr or (not is_stmt_header) or (not _has_rev_anchor):
                # Garbled embedded OCR layers (e.g. SAMHI, AIMTRON p2, VIDHIING p7-8, rotated SGFIN, PARADEEP) pass len>=50
                # yet contain broken keywords/years/digits. Skip pure cover/auditor/BS/CF/Notes unless P&L/inverted cues exist.
                _has_pnl_cues = _has_rev_anchor or any(
                    k in norm_and_lower for k in ("statement of unaudited", "statement of audited", "financial results for", "total expenses", "profit before tax", "profit/(loss)", "earnings per share")
                )
                _is_cover_or_auditor = any(
                    k in _page_head_1500
                    for k in (
                        "dear sir", "dear madam", "dear sirs", "listing department", "corporate relationship", "sub:", "subject:",
                        "bse limited", "national stock exchange", "exchange plaza", "dalal street",
                        "regulation 30", "outcome of board meeting", "outcome of the board meeting",
                        "yours faithfully", "yours sincerely", "company secretary",
                        "independent auditor", "limited review", "review report", "auditor's report",
                        "based on our review conducted", "procedures performed as stated", "nothing has come to our attention",
                        "sre 2410", "chartered accountants", "udin:", "membership no",
                        "assets and liabilities", "a ts and liabilities", "statement of assets", "total assets",
                        "non-current assets", "non-current assels", "equity and liabilities",
                        "cash flow from operating", "cash flows from operating", "statement of cash flow",
                        "cash flow statement", "cash flows from investing", "cash flows from financing",
                        "cash flor", "operating activit", "adjustments for:",
                        "annexure 1", "annexure i", "annexure -", "additional disclosures", "additiona i d",
                        "regulation 52(4)", "regul.uon", "regulation 32", "deviation or variation",
                        "details with respect to acquisition", "indicative time period", "cost of acquisition",
                        "target company", "sebi master circular", "listing obligations and disclosure requirements",
                        "notes to the unaudited", "notes to the audited", "notes to statement", "note to the statement",
                        "chief operating decision maker", "reviewed by the audit committee",
                        "the above standalone financial results", "the above consolidated financial results",
                        "prepared in accordance with indian accounting standards", "not66 to th", "otes: 1 the above",
                    )
                ) and not _has_pnl_cues and not _prefer_180_hint
                _re_ocr_ok = _has_rev_anchor and not _was_garbled_pre_ocr
                if (not _is_cover_or_auditor) and (page_idx not in ocr_attempted_pages) and _OCR_AVAILABLE and tmp_pdf_path and (time.time() - t_start < 110.0) and (time.time() - t_parse_start < 150.0):
                    _orig_text, _orig_text_lower, _orig_norm_lower = text, text_lower, norm_text_lower
                    _orig_score = _score_ocr_readability(_orig_text)
                    try:
                        ocr_attempted_pages.add(page_idx)
                        _fresh_ocr = ""
                        _comp_img = _render_page_image_fast(tmp_pdf_path, page_idx, timeout=8.0, dpi=150)
                        if _comp_img is not None:
                            if _comp_img.width > 1600 or _comp_img.height > 2000:
                                _comp_img.thumbnail((1600, 2000))
                            _fresh_ocr = _ocr_image_with_osd(_comp_img, timeout=10.0, prefer_180=_prefer_180_hint)
                            if _fresh_ocr and len(_fresh_ocr.strip()) >= 50:
                                _cand_text = _normalize_ocr_text(_fresh_ocr.strip())
                                _cand_lower = _cand_text.lower()
                                _cand_norm = re.sub(r"\s+", " ", _cand_lower)
                                _cand_norm_and = _cand_norm.replace("&", " and ")
                                _cand_has_ifrs = (
                                    bool(re.search(r"\brevenues\b", _cand_norm))
                                    and any(k in _cand_norm for k in ("profit before tax", "profit before income tax", "profit after tax", "profit for the period"))
                                )
                                _cand_rev = (
                                    any(k in _cand_norm for k in _rev_indicators)
                                    or _cand_has_ifrs
                                    or "revenue from operat" in _cand_norm
                                    or "income from operat" in _cand_norm
                                    or ("total expenses" in _cand_norm and ("profit before tax" in _cand_norm or "earnings per share" in _cand_norm))
                                )
                                _re_is_non_pnl = any(
                                    k in _cand_norm_and[:1500]
                                    for k in ("cash flow from operating", "cash flows from operating", "statement of cash flow", "assets and liabilities", "total assets", "independent auditor", "limited review", "based on our review conducted", "dear sir", "dear madam")
                                ) and "revenue from operations" not in _cand_norm_and and "interest earned" not in _cand_norm_and and not _cand_has_ifrs
                                _cand_score = _score_ocr_readability(_cand_text)
                                if (not _re_is_non_pnl) and (_cand_rev or (_cand_score > _orig_score)):
                                    text, text_lower, norm_text_lower = _cand_text, _cand_lower, _cand_norm
                                    cached_page_texts[page_idx] = text
                                    _re_ocr_ok = _cand_rev
                                elif _has_rev_anchor and not _re_is_non_pnl:
                                    # Keep stronger pre-existing embedded text (e.g. partial OCR layer with clean Revenue rows)
                                    text, text_lower, norm_text_lower = _orig_text, _orig_text_lower, _orig_norm_lower
                                    _re_ocr_ok = True
                            logger.info(
                                f"🔄 [PDF_PARSER: RE_OCR] {symbol} p{page_idx+1}: ok={_re_ocr_ok}, chars={len(_fresh_ocr or '')}"
                            )
                    except Exception as _re_err:
                        logger.info(f"⚠️ [PDF_PARSER: RE_OCR_ERR] {symbol} p{page_idx+1}: {_re_err}")
                if not _re_ocr_ok:
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
            explicit_unit = False
            header_text = "\n".join(text.split("\n")[:28])
            if re.search(_UNIT_MILLION_RE, header_text, re.I):
                mult = 0.1  # 1 Million = 0.1 Crore (10 Lakhs)
                unit = "million"
                explicit_unit = True
            elif re.search(_UNIT_LAKH_RE, header_text, re.I):
                mult = 0.01  # 1 Lakh = 0.01 Crore
                unit = "lakh"
                explicit_unit = True
            elif re.search(_UNIT_CRORE_RE, header_text, re.I):
                mult = 1.0
                unit = "crore"
                explicit_unit = True
            else:
                if re.search(_UNIT_MILLION_RE, text, re.I):
                    mult = 0.1
                    unit = "million"
                    explicit_unit = True
                elif re.search(_UNIT_LAKH_RE, text, re.I):
                    mult = 0.01
                    unit = "lakh"
                    explicit_unit = True
                elif re.search(_UNIT_CRORE_RE, text, re.I):
                    mult = 1.0
                    unit = "crore"
                    explicit_unit = True

            # 3. Determine Period End Date (Safeguard B: Precedence Rule)
            # Precedence: (1) Explicit fiscal in-table date regex, (2) Official filing metadata, (3) Description regex
            in_table_period = None
            first_non_fiscal_period = None
            _date_patterns = [
                r"(?:quarter|half\s*year|year|\d+\s*months?|months?|period)\s*ended\s*(?:(?:and|to)\s*[^\n]*ended\s*)?(?:on\s*)?([a-zA-Z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*202\d|\d{1,2}(?:st|nd|rd|th)?\s*[a-zA-Z]+,?\s*202\d|\d{2}[\./-]\d{2}[\./-]202\d)",
                r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:January|Jan|February|Feb|March|Mar|April|Apr|May|June|Jun|July|Jul|August|Aug|September|Sept|Sep|October|Oct|November|Nov|December|Dec)\.?,?\s*202\d)\b",
                r"\b((?:January|Jan|February|Feb|March|Mar|April|Apr|May|June|Jun|July|Jul|August|Aug|September|Sept|Sep|October|Oct|November|Nov|December|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*202\d)\b",
                r"((?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2},?)[\s\n]+.*?(202\d)",
                r"\b(\d{2}[\./-]\d{2}[\./-]202\d)\b",
            ]
            _today_str = datetime.now().strftime("%Y-%m-%d")
            for d_pat in _date_patterns:
                for dm in re.finditer(d_pat, text, re.I | re.DOTALL):
                    if dm.lastindex and dm.lastindex >= 2:
                        raw_d = f"{dm.group(1)} {dm.group(2)}".strip()
                    else:
                        raw_d = dm.group(1).strip()
                    cand_p = _parse_exchange_date(raw_d)
                    if cand_p and len(cand_p) == 10 and cand_p <= _today_str:
                        if _is_fiscal_period_end(cand_p):
                            in_table_period = cand_p
                            break
                        elif first_non_fiscal_period is None:
                            first_non_fiscal_period = cand_p
                if in_table_period:
                    break
            if not in_table_period:
                in_table_period = first_non_fiscal_period

            meta_period = None
            if ann_meta.get("toDate"):
                meta_period = _parse_exchange_date(ann_meta.get("toDate"))
            elif ann_meta.get("period_end_date"):
                meta_period = _parse_exchange_date(ann_meta.get("period_end_date"))

            desc_period = None
            ann_desc = f"{ann_meta.get('desc', '')} {ann_meta.get('attchmntText', '')} {ann_meta.get('sub', '')} {ann_meta.get('subject', '')}"
            dm_ann = re.search(
                r"(?:quarter|half\s*year|year)\s*ended\s*(?:on\s*)?([a-zA-Z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*202\d|\d{1,2}(?:st|nd|rd|th)?\s*[a-zA-Z]+,?\s*202\d|\d{2}[\./-]\d{2}[\./-]202\d)",
                ann_desc,
                re.I,
            )
            if dm_ann:
                desc_period = _parse_exchange_date(dm_ann.group(1).strip())

            # Apply Safeguard B Precedence:
            # Prefer explicit fiscal quarter/annual period-end dates (never let a board-meeting signing date like 2026-11-03 override 2026-09-30).
            period_end = None
            if in_table_period and (_is_fiscal_period_end(in_table_period) or not meta_period or not _is_fiscal_period_end(meta_period)):
                period_end = in_table_period
                if meta_period and meta_period != in_table_period and _is_fiscal_period_end(in_table_period):
                    logger.warning(
                        f"⚠️ [PDF_PARSER] {symbol}: Period discrepancy on page {page_idx+1}: "
                        f"in-table={in_table_period} vs metadata={meta_period}. Respecting explicit in-table date (Safeguard B)."
                    )
            elif meta_period:
                period_end = meta_period
            elif desc_period:
                period_end = desc_period
            elif in_table_period:
                period_end = in_table_period

            # Safeguard B Extension: Never let an OCR-misread historical OR future in-table date override a valid fiscal metadata date.
            # E.g. OCR misreads in-table "2026" as "2020" or "2028" → meta_period=2026-09-30 is authoritative.
            if period_end and period_end > _today_str:
                _fallback_p = meta_period if (meta_period and meta_period <= _today_str) else (desc_period if (desc_period and desc_period <= _today_str) else None)
                if _fallback_p:
                    logger.warning(
                        f"⚠️ [PDF_PARSER] {symbol}: Safeguard B Extension: future OCR-misread period {period_end} > today {_today_str}. Overriding → using {_fallback_p}."
                    )
                    period_end = _fallback_p
                else:
                    continue
            if period_end and meta_period and _is_fiscal_period_end(meta_period) and period_end != meta_period:
                try:
                    from datetime import datetime as _sfb_dt
                    _days_diff = (_sfb_dt.strptime(meta_period, "%Y-%m-%d") - _sfb_dt.strptime(period_end, "%Y-%m-%d")).days
                    if _days_diff > 60:
                        logger.warning(
                            f"⚠️ [PDF_PARSER] {symbol}: Safeguard B Extension: in-table period {period_end} is "
                            f"{_days_diff}d older than valid fiscal metadata {meta_period}. Overriding → using meta_period."
                        )
                        period_end = meta_period
                except Exception:
                    pass

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

            # 5. Extract Financial Metrics (Safeguard C: Metric Separation)
            rev_from_ops: Optional[float] = None
            other_income: Optional[float] = None
            total_income: Optional[float] = None
            total_expenses: Optional[float] = None
            pbt: Optional[float] = None
            pat: Optional[float] = None
            ebit: Optional[float] = None
            depr: Optional[float] = None
            finance_costs: Optional[float] = None
            eps: Optional[float] = None
            equity_cap: Optional[float] = None
            total_equity: Optional[float] = None

            # Detect column headers for multi-column tables (never match phone/fax numbers like 91-44-22!)
            col_idx = 0
            num_cols = 1
            best_date_cols = 1
            for line in text.split("\n")[:30]:
                # Require valid calendar day (01-31), valid month (01-12 or 3-letter month), and year (2015-2035 or 15-35)
                dm = re.findall(
                    r"(?<![+\d-])((?:0?[1-9]|[12]\d|3[01])[\./-](?:0?[1-9]|1[0-2]|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[\./-](?:20)?(?:1\d|2\d|3[0-5]))\b",
                    line,
                    re.I,
                )
                if len(dm) > best_date_cols:
                    best_date_cols = len(dm)
            if best_date_cols >= 2:
                num_cols = min(best_date_cols, 8)
                col_idx = 0

            # Word date headers check (e.g. "September 30, June 30, September 30, ...")
            months_pat = r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
            for line in text.split("\n")[:25]:
                m_matches = re.findall(months_pat, line, re.I)
                if len(m_matches) > num_cols:
                    num_cols = min(len(m_matches), 8)
                y_matches = re.findall(r"\b202\d\b", line)
                if len(y_matches) > num_cols:
                    num_cols = min(len(y_matches), 8)

            # Expand lines if multiple table rows were concatenated without newlines
            raw_lines = []
            for l in text.split("\n"):
                sub_lines = re.split(r'\s+(?=(?:[A-Z0-9]+[\.\)]|\b[A-Z]\b)\s+[A-Za-z])', l.strip())
                for sl in sub_lines:
                    if sl:
                        raw_lines.append(sl)

            def _extract_row_nums(line_str_val: str) -> Tuple[str, List[float]]:
                """Normalizes a table row, strips serial/formula tokens, and extracts column values."""
                lc = re.sub(r"\(\s*(?:\d{1,2}|[IVXivx]{1,4}|[A-Za-z])\s*[-+*/]\s*(?:\d{1,2}|[IVXivx]{1,4}|[A-Za-z])(?:\s*[-+*/]\s*(?:\d{1,2}|[IVXivx]{1,4}|[A-Za-z]))*\s*\)", "", line_str_val)
                lc = re.sub(r"\[\s*(?:\d{1,2}|[IVXivx]{1,4}|[a-zA-Z])[^\]]{0,25}\]", "", lc)
                lc = re.sub(r"\(\s*refer\s+notes?[^\)]*\)", "", lc, flags=re.I)
                lc = re.sub(r"(\d+,\d{2})\s+(\d)\s+(\d{2}\b)", r"\1\2.\3", lc)
                lc = re.sub(r"(\d+,\d{3})\s+(\d{2})(?=\s|$|\))", r"\1.\2", lc)
                lc = re.sub(r"(?<![\d\.])(\d{1,2})\.(\d{3})\s+(\d{2})(?=\s|$|\))", r"\1,\2.\3", lc)
                lc = re.sub(r"(\d{2}),(\d{3})(\d{2}\b)", r"\1,\2.\3", lc)
                nm = re.sub(r"\s+", " ", lc)
                nm = re.sub(r"\b[l|1iI](total|income|revenue|other|profit|expenses)\b", r"\1", nm, flags=re.I)
                nm = re.sub(r"([a-z])([A-Z])", r"\1 \2", nm)
                l_cl = re.sub(r"[,;:\.']rofit", "profit", nm, flags=re.I)
                l_cl = re.sub(r"\b[l1iI']?['`]?rofi[tl]\b", "profit", l_cl, flags=re.I)
                l_cl = re.sub(r"\b[no]rofit\b", "profit", l_cl, flags=re.I)
                l_cl = re.sub(r"\bnet profit[!/|]\s*\(loss\)", "net profit / (loss)", l_cl, flags=re.I)
                toks = l_cl.split()
                if not toks:
                    return l_cl.lower(), []
                extracted: List[float] = []
                extracted_raw: List[str] = []
                for t in toks:
                    if t.count(".") >= 2:
                        continue
                    t_digits = re.sub(r"[,()\s]", "", t)
                    if re.match(r"^(?:28|29|30|31)(?:0[1-9]|1[0-2])?202\d$", t_digits) or re.match(r"^202\d(?:0[1-9]|1[0-2])(?:28|29|30|31)$", t_digits):
                        continue
                    cl_val = _clean_token_number(t)
                    if cl_val is not None:
                        if (not re.search(r"\d,\d", t)) and ("." not in t or t.endswith(".")) and 2020 <= cl_val <= 2030 and abs(cl_val - int(cl_val)) < 1e-6:
                            continue
                        extracted.append(cl_val)
                        extracted_raw.append(t)
                # Strip leading serial item number (e.g. "1 Total income ...", "9 Net profit ...")
                first_tok = toks[0].strip(".)][(")
                if first_tok.isdigit() and extracted and abs(extracted[0] - int(first_tok)) < 1e-6 and int(first_tok) <= 35:
                    extracted = extracted[1:]
                elif len(toks) >= 2 and toks[1].strip(".)][(").isdigit():
                    sec_tok = toks[1].strip(".)][(")
                    if extracted and abs(extracted[0] - int(sec_tok)) < 1e-6 and int(sec_tok) <= 35 and "." not in toks[1]:
                        extracted = extracted[1:]
                return l_cl.lower(), extracted

            # Infer true table column count from actual multi-column P&L rows (prevents OCR-dropped header dates
            # like 30062026 / 30-Se p-25 from shrinking num_cols from 6 to 5/3 and shifting Col 0 rightwards!)
            from collections import Counter
            row_col_counts = []
            max_pnl_table_num = 0.0
            for _rl in raw_lines:
                _rl_low, _rl_nums = _extract_row_nums(_rl.strip())
                if _rl_nums:
                    max_pnl_table_num = max(max_pnl_table_num, max(abs(x) for x in _rl_nums))
                if any(k in _rl_low for k in [
                    "revenue from operations", "total revenue from operations", "interest earned",
                    "total income", "total expenses", "profit before tax",
                    "profit for the period", "profit after tax", "net profit", "net premium income"
                ]) and 2 <= len(_rl_nums) <= 8:
                    row_col_counts.append(len(_rl_nums))
            if row_col_counts:
                row_mode_cols = Counter(row_col_counts).most_common(1)[0][0]
                if row_mode_cols > num_cols:
                    num_cols = row_mode_cols
                col_idx = 0

            if col_idx is None:
                col_idx = 0

            # Priority guards so aggregate P&L rows ("Total revenue from operations", "Net profit for the period")
            # override sub-items ("Interest income", "Net profit attributable") even if a sub-row appears earlier/later
            rev_is_exact_total = False
            pat_priority = 0  # 1 = generic match, 2 = explicit period PAT match
            pat_raw_tok_had_no_dot = False

            _PAT_EXCLUDE_KWS = [
                "other comprehensive", "total comprehensive", "before", "operating", "attributable",
                "share of profit", "share of net profit", "share of loss", "jointly controlled",
                "joint venture", "associates", "associate", "non-controlling", "non controlling",
                "minority interest", "on sale of", "disposal of", "fixed asset", "property",
                "investment", "subsidiar", "per share", "margin", "ratio", "carried to",
                "appropriat", "capital gain", "net of amorti", "amortisation", "retained",
                "discontinued",
            ]

            # Strategy A: Line-by-line inspection (Standard inline tables where numbers are adjacent to row label)
            for _line_i, line in enumerate(raw_lines):
                line_str = line.strip()
                line_lower, nums = _extract_row_nums(line_str)
                # Next-line numeric alignment recovery: if label row has 0 nums (e.g. M&MFIN "XI profit / (loss) for the period"
                # followed immediately by numbers on the next line before OCI numbers), peek at raw_lines[_line_i + 1].
                if not nums and _line_i + 1 < len(raw_lines) and not any(k in line_lower for k in _PAT_EXCLUDE_KWS):
                    if any(k in line_lower for k in [
                        "profit / (loss) for the period", "profit/(loss) for the period", "profit for the period",
                        "profit after tax", "total revenue from operations", "revenue from operations", "total income"
                    ]):
                        _, _next_nums = _extract_row_nums(raw_lines[_line_i + 1].strip())
                        if len(_next_nums) >= max(2, num_cols if num_cols >= 2 else 2):
                            nums = _next_nums
                            line_str = f"{line_str} {raw_lines[_line_i + 1].strip()}"
                if not nums:
                    continue

                # Repair kerning-split single-digit Indian decimal pair (e.g. "5,053.64" -> "5.05 3.64" in TATASTEEL P11)
                # ONLY when len(nums) == num_cols + 1, first two are < 10.0 decimals, and remaining columns are >= 100.0
                if (
                    num_cols >= 4
                    and len(nums) == num_cols + 1
                    and 0.0 < nums[0] < 10.0
                    and 0.0 < nums[1] < 10.0
                    and all(abs(x) >= 100.0 for x in nums[2:])
                    and not any(ek in line_lower for ek in ("eps", "earnings per", "ratio", "margin", "tax"))
                ):
                    m_sp = re.search(r"(?<![\d\.])(\d)\.(\d{2})\s+(\d)\.(\d{2})(?=\s|$)", line_str)
                    if m_sp:
                        rejoined = float(f"{m_sp.group(1)}{m_sp.group(2)}{m_sp.group(3)}.{m_sp.group(4)}")
                        nums = [rejoined] + nums[2:]

                # Skip prose narrative sentences that happen to mention financial keywords & 1 note figure (e.g. INTELLECT Notes p8)
                if len(line_str) > 160 and len(nums) <= 2:
                    continue

                if len(line_str.split()) > 0 and line_str.split()[0].strip(".)][(").isdigit() and len(nums) == 0:
                    continue

                # Skip gross revenue when net revenue is reported
                if "(gross)" in line_lower and "net" not in line_lower:
                    continue

                # Col 0 is always the latest reporting period in SEBI Reg 33 format
                if len(nums) >= num_cols:
                    last_val = nums[-num_cols]
                elif len(nums) >= 1:
                    # In OCR-recovered tables or non-standard column counts, Col 0 is the first column
                    last_val = nums[0]
                else:
                    continue

                # Reject detached serial/note integers (e.g. PyPDF2 SBILIFE "Net premium income 1") when page has large financial figures
                is_stray_small_int = (len(nums) == 1 and num_cols >= 3 and abs(last_val) <= 25.0 and abs(last_val - int(last_val)) < 1e-6 and max_pnl_table_num >= 500.0)
                if is_stray_small_int:
                    continue

                is_exact_rev_total = any(k in line_lower for k in [
                    "total revenue from operations", "revenue from operations", "net sales",
                    "interest earned", "net premium income", "total insurance revenue", "insurance revenue"
                ]) or bool(re.match(r"^(?:[ivx0-9a-z]+[\.\)]\s*)?revenues?\b", line_lower))
                is_sub_rev = any(k in line_lower for k in [
                    "interest income", "premium earned", "gross premium written",
                    "net premium written", "gross premium income"
                ])

                if (is_exact_rev_total or is_sub_rev) and not any(k in line_lower for k in ["total income", "comprehensive", "expenses", "cash flow", "other income", "less:", "cost of", "% of", "segment"]):
                    if rev_from_ops is None or (is_exact_rev_total and not rev_is_exact_total):
                        rev_from_ops = last_val * mult
                        if is_exact_rev_total:
                            rev_is_exact_total = True

                elif other_income is None and "other income" in line_lower and not any(k in line_lower for k in ["total", "comprehensive", "expenses"]):
                    other_income = last_val * mult

                elif total_income is None and any(k in line_lower for k in [
                    "total income", "total revenue", "income from operations"
                ]) and not any(k in line_lower for k in ["total comprehensive", "other comprehensive", "expenses", "cash flow"]):
                    total_income = last_val * mult

                elif total_expenses is None and any(k in line_lower for k in [
                    "total expenses", "total expenditure", "fotal expenses"
                ]) and not any(k in line_lower for k in ["income", "profit"]):
                    total_expenses = last_val * mult

                elif (any(k in line_lower for k in [
                    "profit for the period", "profit / (loss) for the period", "profit/(loss) for the period",
                    "profit/ (loss) for the period", "profit i (loss) for the period", "profit | (loss) for the period",
                    "net profit for the period", "profit after tax", "ordinary activities (after tax)", "ordinary activities after tax",
                    "net profit / (loss) for the period", "net profit/(loss) for the period",
                    "profit / (loss)", "profit/ (loss)", "profit/(loss)", "profit i (loss)", "profit | (loss)", "profit/ {loss)",
                    "profit/{loss)", "profit (loss)", "net profit / (loss)", "net profit/(loss)", "net profit/ (loss)", "net profit"
                ]) or ("ordinary activities" in line_lower and "after tax" in line_lower)) and not any(k in line_lower for k in _PAT_EXCLUDE_KWS):
                    is_high_pat = any(k in line_lower for k in [
                        "profit for the period", "profit / (loss) for the period", "profit/(loss) for the period",
                        "profit/ (loss) for the period", "net profit for the period", "profit after tax",
                        "ordinary activities (after tax)", "ordinary activities after tax",
                        "net profit / (loss) for the period", "net profit/(loss) for the period"
                    ]) or ("ordinary activities" in line_lower and "after tax" in line_lower)
                    cand_p_pri = 2 if is_high_pat else 1
                    if cand_p_pri > pat_priority:
                        pat = last_val * mult
                        pat_priority = cand_p_pri
                        # Check whether the matched Col 0 value had an omitted decimal point (e.g. MANORAMA "878190")
                        _raw_int_str = str(int(abs(last_val))) if abs(last_val - int(last_val)) < 1e-6 else ""
                        pat_raw_tok_had_no_dot = bool(_raw_int_str and len(_raw_int_str) >= 5 and _raw_int_str in line_str and f"{_raw_int_str}." not in line_str)

                elif pbt is None and any(k in line_lower for k in [
                    "profit before tax", "profit / (loss) before tax", "profit/(loss) before tax",
                    "profit/ (loss) before tax", "profit/ (loss) before exceptional", "profit before exceptional",
                    "profit from continuing operations before tax",
                    "profit i (loss) before tax", "profit / (loss) belore tax"
                ]):
                    pbt = last_val * mult

                elif depr is None and ("depreciation" in line_lower and "amortization" in line_lower):
                    depr = last_val * mult

                elif finance_costs is None and (line_lower.startswith("finance cost") or "finance costs" in line_lower):
                    finance_costs = last_val * mult

                elif eps is None and any(k in line_lower for k in [
                    "basic and diluted", "basic & diluted", "basic (in rupees)", "basic ( ~)", "basic ( `)", "basic eps", "(1) basic", "basic (", "basic(", "basic ", "earnings per share", "earnings per equity share"
                ]) and not any(k in line_lower for k in ["share capital", "other equity", "face value", "nominal value", "each)"]):
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

            if max_pnl_table_num * mult >= 50.0:
                if rev_from_ops is not None and rev_from_ops < 1.0:
                    rev_from_ops = None
                if total_income is not None and total_income < 1.0:
                    total_income = None
                if pat is not None and abs(pat) < 1.0:
                    pat = None

            # Decoupled layout fallback: if standard text failed to resolve both rev and pat,
            # retry with layout-mode extraction for decoupled tables (NEVER overwrite fresh RE_OCR text with broken embedded PDF text)
            if ((rev_from_ops is None and total_income is None) or pat is None) and (page_idx not in ocr_attempted_pages):
                try:
                    text_lay = _safe_extract_text(page, timeout=4.0, extraction_mode="layout", pdf_path=tmp_pdf_path, page_idx=page_idx)
                    if text_lay and len(text_lay.strip()) >= 50:
                        norm_lay = _normalize_ocr_text(text_lay)
                        for lay_line in norm_lay.split("\n"):
                            lay_str = lay_line.strip()
                            if not lay_str:
                                continue
                            # Repair split numbers with embedded space like '3,94 2' -> '3,942' or '3, 77 6' -> '3,776'
                            lay_str = re.sub(r'(\b\d+,\s*\d{1,2})\s+(\d\b)', r'\1\2', lay_str)
                            lay_str = re.sub(r'(\b\d+),\s+(\d{3}\b)', r'\1,\2', lay_str)
                            lay_low = lay_str.lower()
                            if "(gross)" in lay_low and "net" not in lay_low:
                                continue
                            lay_toks = lay_str.split()
                            lay_nums = [_clean_token_number(t) for t in lay_toks if _clean_token_number(t) is not None]
                            if not lay_nums or (len(lay_str) > 160 and len(lay_nums) <= 2):
                                continue
                            if lay_toks[0].strip(".)][(").isdigit():
                                if len(lay_nums) == 1:
                                    continue
                                lay_nums = lay_nums[1:]
                            if len(lay_nums) >= 1:
                                lay_val = lay_nums[0]
                            else:
                                continue

                            lay_norm = re.sub(r"\s+", " ", lay_low)
                            if any(k in lay_norm for k in ["revenue from operations", "total revenue from operations", "net sales", "interest earned", "interest income", "net premium income"]) and not any(k in lay_norm for k in ["total income", "comprehensive", "expenses", "cash flow", "other income", "less:"]):
                                if rev_from_ops is None or "total revenue" in lay_norm or "interest earned" in lay_norm:
                                    rev_from_ops = lay_val * mult
                            elif total_income is None and any(k in lay_norm for k in ["total income", "total revenue", "income from operations"]) and not any(k in lay_norm for k in ["total comprehensive", "other comprehensive", "expenses", "cash flow"]):
                                total_income = lay_val * mult
                            elif pat is None and any(k in lay_norm for k in ["profit for the period", "profit / (loss) for the period", "profit/(loss) for the period", "profit after tax", "net profit for the period", "net profit / (loss)", "net profit/(loss)", "net profit"]) and not any(k in lay_norm for k in _PAT_EXCLUDE_KWS):
                                pat = lay_val * mult
                            elif pbt is None and any(k in lay_norm for k in ["profit before tax", "profit / (loss) before tax", "profit/(loss) before tax", "profit before exceptional"]):
                                pbt = lay_val * mult
                except Exception as _lay_err:
                    logger.debug(f"[PDF_PARSER] Layout fallback error on page {page_idx+1}: {_lay_err}")

            # Strategy B: Column Block inspection (Tables where text labels appear in one block and column values appear in another block)
            if (rev_from_ops is None and total_income is None) or pat is None:
                parsed_nums: List[float] = []
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

                # Universal accounting equation cross-check: PBT - Tax = PAT
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
                            if 0.10 * cand_pbt <= cand_tax <= 0.45 * cand_pbt:
                                diff = round(cand_pbt - cand_tax, 2)
                                for k in range(j + 1, min(j + 10, len(parsed_nums))):
                                    cand_pat = parsed_nums[k]
                                    if cand_pat > 0.5 and abs(cand_pat - diff) < 0.25:
                                        valid_sols.append((cand_pbt, cand_tax, cand_pat, i, k))
                                        break
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
                        large_sols = [s for s in valid_sols if s[0] >= 25.0]
                        pool = large_sols if large_sols else valid_sols
                        chosen_sol = max(pool, key=lambda s: (s[4], s[3]))
                        found_pbt, found_tax, found_pat, pbt_idx, _ = chosen_sol
                        if pat is None:
                            pat = found_pat * mult
                        if pbt is None and found_pbt is not None:
                            pbt = found_pbt * mult

                    if rev_from_ops is None and total_income is None and pbt_idx != -1:
                        def _is_non_date_num(n: float) -> bool:
                            if abs(n - int(n)) < 1e-6:
                                ni = int(n)
                                if 2020 <= ni <= 2030:
                                    return False
                                ns = str(ni)
                                if re.match(r"^(?:28|29|30|31)(?:0[1-9]|1[0-2])?202\d$", ns) or re.match(r"^202\d(?:0[1-9]|1[0-2])(?:28|29|30|31)$", ns):
                                    return False
                            return True

                        all_cand_nums = [
                            n for n in parsed_nums[:pbt_idx]
                            if n >= 50.0 and _is_non_date_num(n)
                        ]
                        if not all_cand_nums:
                            all_cand_nums = [
                                n for n in parsed_nums[:pbt_idx]
                                if n > 0 and _is_non_date_num(n)
                            ]

                        found_rev_row = None
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
                            rev_from_ops = found_rev_row * mult
                        else:
                            large = [n for n in all_cand_nums if n > (found_pbt or 0)]
                            if large:
                                rev_from_ops = large[0] * mult
                            elif all_cand_nums:
                                rev_from_ops = all_cand_nums[0] * mult

            # Safeguard C Precedence for Primary Revenue:
            # Prefer rev_from_ops; fall back to total_income if rev_from_ops is absent OR looks like a stray tiny integer (e.g. footnote "5.48 Cr") while total_income is large
            if rev_from_ops is not None and total_income is not None and rev_from_ops < 10.0 and total_income >= 20.0:
                rev = total_income
            else:
                rev = rev_from_ops if rev_from_ops is not None else total_income

            # Calculate EBIT: PBT + Finance Costs (if both present)
            if pbt is not None and finance_costs is not None:
                ebit = pbt + finance_costs
            elif pbt is not None:
                ebit = pbt

            # Sanity check: Accounting cross-check (filter stray small rev BEFORE checking PAT ratio so valid PAT like TATASTEEL 9588 / LTF 968 isn't discarded!)
            if rev is not None and (rev <= 0 or rev > 500_000.0):
                rev = None
            # Reject decoupled-label tiny serial extractions (e.g. SBILIFE PyPDF2 "Net premium income 1" -> 0.01 Cr on a 24,000 Cr table)
            if max_pnl_table_num * mult >= 50.0 and (rev is not None and rev < 1.0) and (pat is None or abs(pat) < 1.0):
                rev = None
                pat = None
            if rev is not None and pat is not None and rev < 10.0 and abs(pat) > 50.0:
                if total_income is not None and total_income >= 10.0 and total_income <= 500_000.0:
                    rev = total_income
                else:
                    # Reject BOTH spurious tiny revenue and reject partial record if no valid revenue exists (lets remaining pages match real P&L!)
                    logger.warning(f"⚠️ [PDF_PARSER] {symbol}: Rejecting spurious tiny revenue={rev} Cr vs PAT={pat} Cr on page {page_idx+1}")
                    rev = None
                    pat = None
            # PBT conservation guard: PAT cannot exceed positive PBT * 2.5 unless an OCR decimal point was dropped in a whole integer token (e.g. MANORAMA "878190" = 8,781.90)
            if pbt is not None and pbt > 0 and pat is not None and pat > max(50.0, pbt * 2.5):
                if pat_raw_tok_had_no_dot and 0.0 < (pat / 100.0) <= pbt * 1.1:
                    logger.info(f"🔧 [PDF_PARSER] {symbol}: Repaired missing OCR decimal in PAT {pat} -> {round(pat / 100.0, 4)} Cr (bounded by PBT={pbt} Cr)")
                    pat = round(pat / 100.0, 4)
                else:
                    logger.warning(f"⚠️ [PDF_PARSER] {symbol}: Rejecting PAT={pat} Cr inconsistent with PBT={pbt} Cr (rev={rev} Cr)")
                    pat = None
            if pat is not None and (abs(pat) > 100_000.0 or (rev is not None and rev > 0 and abs(pat) > max(500.0, rev * 2.5))):
                logger.warning(f"⚠️ [PDF_PARSER] {symbol}: Rejecting physically implausible PAT={pat} Cr (rev={rev} Cr)")
                pat = None

            if rev is None and pat is None:
                continue

            shares_out = None
            if equity_cap is not None and equity_cap > 0:
                shares_out = round((equity_cap * 10_000_000) / 10.0, 0)

            bcast_ts = str(ann_meta.get("broadCastDate") or ann_meta.get("an_dt") or f"{period_end}T12:00:00")
            avail_dt = _parse_exchange_date(ann_meta.get("filingDate") or ann_meta.get("an_dt")) or str(period_end)
            seq_id = str(ann_meta.get("seqNumber") or ann_meta.get("seq_id") or "1")

            exch = str(ann_meta.get("exchange") or ("BSE" if "bseindia" in str(ann_meta.get("attchmntFile", "")).lower() else "NSE")).upper()
            pdf_source = f"{exch}_ANNOUNCEMENT_PDF"

            record = RawFinancialRecord(
                symbol=symbol.upper(),
                source=pdf_source,
                period_end_date=period_end,
                period_type=st_type,
                consolidation=basis,
                revenue=round(rev, 4) if rev is not None else None,
                net_profit=round(pat, 4) if pat is not None else None,
                ebit=round(ebit, 4) if ebit is not None else None,
                total_debt=None,
                total_equity=round(total_equity, 4) if total_equity is not None else None,
                operating_cash_flow=None,
                eps=round(eps, 2) if eps is not None else None,
                shares_outstanding=shares_out,
                availability_date=avail_dt,
                broadcast_timestamp=bcast_ts,
                unit="crores",
                currency="INR",
                source_document_hash=pdf_hash,
                filing_id=f"{exch}_PDF_{symbol.upper()}_{seq_id}_{period_end}",
                revision_indicator="N",
                raw_fact_details={
                    "page_number": page_idx + 1,
                    "extracted_via": "ExchangePdfParser_v2.0_Adaptive",
                    "unit_detected": unit,
                    "explicit_unit": explicit_unit,
                    "rev_from_ops_cr": round(rev_from_ops, 4) if rev_from_ops is not None else None,
                    "other_income_cr": round(other_income, 4) if other_income is not None else None,
                    "total_income_cr": round(total_income, 4) if total_income is not None else None,
                    "total_expenses_cr": round(total_expenses, 4) if total_expenses is not None else None,
                    "pbt_cr": round(pbt, 4) if pbt is not None else None,
                    "pat_cr": round(pat, 4) if pat is not None else None,
                    "depreciation_cr": round(depr, 4) if depr is not None else None,
                    "finance_costs_cr": round(finance_costs, 4) if finance_costs is not None else None,
                    "attachment_url": ann_meta.get("attchmntFile") or ann_meta.get("attachmentUrl"),
                },
            )

            is_valid, _ = validate_semantic_record(record)
            if is_valid:
                existing_idx = next(
                    (i for i, r in enumerate(records) if r.period_end_date == record.period_end_date and r.consolidation == record.consolidation),
                    None,
                )
                if existing_idx is None:
                    records.append(record)
                else:
                    ex_r = records[existing_idx]
                    ex_score = (1 if ex_r.revenue is not None else 0) + (1 if ex_r.net_profit is not None else 0)
                    new_score = (1 if record.revenue is not None else 0) + (1 if record.net_profit is not None else 0)
                    if new_score > ex_score:
                        records[existing_idx] = record
                has_cons = any(r.consolidation == ConsolidationType.CONSOLIDATED for r in records)
                has_std = any(r.consolidation == ConsolidationType.STANDALONE for r in records)
                if has_cons and has_std:
                    logger.debug(f"🎯 [PDF_PARSER] {symbol}: Both Consolidated and Standalone extracted cleanly by page {page_idx + 1}.")
                    break

        # Cross-statement unit consistency safeguard:
        # If one statement in this PDF explicitly declared Lakhs (mult=0.01) while the companion statement
        # defaulted to Crores (explicit_unit=False due to OCR-mangled header like "n /11 Luc") and appears 25x-500x larger, rescale by 0.01.
        lakh_revs = [
            float(r.revenue) for r in records
            if r.revenue and float(r.revenue) > 0 and (r.raw_fact_details or {}).get("unit_detected") == "lakh"
        ]
        if lakh_revs:
            ref_rev = min(lakh_revs)
            for rec in records:
                det = rec.raw_fact_details or {}
                if det.get("unit_detected") == "crore" and not det.get("explicit_unit") and rec.revenue and ref_rev > 0:
                    ratio = float(rec.revenue) / ref_rev
                    if 25.0 <= ratio <= 500.0 and float(rec.revenue) > 5000.0:
                        logger.info(f"🔧 [PDF_PARSER] {symbol}: Auto-normalized companion {rec.consolidation.value} statement unit from Lakh to Crore (ratio={ratio:.1f}x).")
                        rec.revenue = round(float(rec.revenue) * 0.01, 4)
                        if rec.net_profit is not None and abs(float(rec.net_profit)) > 500.0:
                            rec.net_profit = round(float(rec.net_profit) * 0.01, 4)
                        if rec.ebit is not None and abs(float(rec.ebit)) > 500.0:
                            rec.ebit = round(float(rec.ebit) * 0.01, 4)
                        if rec.total_equity is not None:
                            rec.total_equity = round(float(rec.total_equity) * 0.01, 4)
                        det["unit_detected"] = "lakh"

        # Enrich extracted records with Balance Sheet (Debt & Equity) and Cash Flow from subsequent pages if present
        t_pass2_start = time.time()
        for rec in records:
            if time.time() - t_pass2_start > 10.0 or time.time() - t_parse_start > 62.0:
                break
            if rec.total_debt is None or rec.total_equity is None or rec.operating_cash_flow is None:
                for p_idx in candidate_page_indices:
                    if time.time() - t_pass2_start > 10.0 or time.time() - t_parse_start > 65.0:
                        break
                    try:
                        p_raw = cached_page_texts.get(p_idx)
                        if p_raw is None:
                            p_raw = _safe_extract_text(reader.pages[p_idx], timeout=3.5, pdf_path=tmp_pdf_path, page_idx=p_idx)
                            cached_page_texts[p_idx] = p_raw
                        if not p_raw or len(p_raw.strip()) < 20:
                            continue
                        p_norm = _normalize_ocr_text(p_raw)
                        p_norm_low = p_norm.lower()

                        # Multiplier for this page
                        page_mult = doc_mult
                        if re.search(r"in\s*lakhs?|\(in\s*lakhs?\)|inr\s*in\s*lakhs?|\(t\s*in\s*lakhs\)", p_norm, re.I):
                            page_mult = 0.01
                        elif re.search(r"in\s*crores?|\(in\s*crores?\)", p_norm, re.I):
                            page_mult = 1.0

                        # 1. Statement of Assets and Liabilities (Balance Sheet)
                        is_bs_page = any(k in p_norm_low for k in ["assets and liabilities", "statement of assets", "balance sheet"])
                        if is_bs_page:
                            # Look for Total Debt: borrowings
                            if rec.total_debt is None:
                                lt_debt = None
                                st_debt = None
                                for el in p_norm.split("\n"):
                                    el_low = el.lower()
                                    if "long term borrowings" in el_low or "non-current borrowings" in el_low:
                                        el_nums = [_clean_token_number(t) for t in el.split() if _clean_token_number(t) is not None]
                                        if el_nums:
                                            lt_debt = el_nums[0] * page_mult
                                    elif "short term borrowings" in el_low or "current borrowings" in el_low:
                                        el_nums = [_clean_token_number(t) for t in el.split() if _clean_token_number(t) is not None]
                                        if el_nums:
                                            st_debt = el_nums[0] * page_mult
                                if lt_debt is not None or st_debt is not None:
                                    rec.total_debt = round((lt_debt or 0.0) + (st_debt or 0.0), 4)

                            # Look for Total Equity
                            if rec.total_equity is None:
                                eq_cap_val = None
                                res_val = None
                                for el in p_norm.split("\n"):
                                    el_low = el.lower()
                                    if ("total equity" in el_low or "total shareholders" in el_low or "shareholders' funds" in el_low) and "liabilities" not in el_low:
                                        el_nums = [_clean_token_number(t) for t in el.split() if _clean_token_number(t) is not None]
                                        if el_nums:
                                            rec.total_equity = round(el_nums[0] * page_mult, 4)
                                            break
                                    elif "share capital" in el_low and "paid" not in el_low:
                                        el_nums = [_clean_token_number(t) for t in el.split() if _clean_token_number(t) is not None]
                                        if el_nums:
                                            eq_cap_val = el_nums[0] * page_mult
                                    elif "reserves" in el_low and ("surplus" in el_low or "other equity" in el_low):
                                        el_nums = [_clean_token_number(t) for t in el.split() if _clean_token_number(t) is not None]
                                        if el_nums:
                                            res_val = el_nums[0] * page_mult
                                if rec.total_equity is None and (eq_cap_val is not None or res_val is not None):
                                    rec.total_equity = round((eq_cap_val or 0.0) + (res_val or 0.0), 4)

                        # 2. Statement of Cash Flows
                        is_cf_page = any(k in p_norm_low for k in ["cash flow from operating", "cash flows from operating", "operating activities"])
                        if is_cf_page and rec.operating_cash_flow is None:
                            for cl in p_norm.split("\n"):
                                cl_low = cl.lower()
                                if any(k in cl_low for k in ["net cash flow from", "net cash generated from", "cash generated from operating"]) and "operating" in cl_low:
                                    cl_nums = [_clean_token_number(t) for t in cl.split() if _clean_token_number(t) is not None]
                                    if cl_nums:
                                        rec.operating_cash_flow = round(cl_nums[0] * page_mult, 4)
                                        break
                    except Exception:
                        pass

        # Strategy C: Certified SEBI Reg 33 Outcome / Press Release Fallback
        # When table pages are scanned raster images, official financial results are frequently disclosed
        # in the accompanying certified press release or board outcome announcement text.
        if not records:
            doc_text = ""
            t_fallback_start = time.time()
            for p_idx in candidate_page_indices:
                if time.time() - t_fallback_start > 15.0:
                    break
                p_txt = cached_page_texts.get(p_idx)
                if p_txt is None:
                    p_txt = _safe_extract_text(reader.pages[p_idx], timeout=5.0, pdf_path=tmp_pdf_path, page_idx=p_idx)
                    cached_page_texts[p_idx] = p_txt
                if p_txt:
                    doc_text += "\n" + p_txt

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
                    # Require explicit currency/scale unit (crore/cr/lakh/million) and reject percentages (%)
                    # so prose like "Revenue grew 2.0%" or "PAT up 7.56%" is never misread as Crores.
                    pr_pat_m = re.search(
                        r"(?:consolidated\s+)?profit after tax[^:\d\n%]{0,35}[:\s]+(?:₹|rs\.?\s*)?([\d,]+\.?\d*)\s*(crores?|cr\.?|lakhs?|lacs?|millions?|mn)\b(?!\s*%)",
                        clean_doc, re.I
                    )
                    pr_rev_m = re.search(
                        r"(?:consolidated\s+)?revenue(?:\s+from\s+operations)?[^:\d\n%]{0,35}[:\s]+(?:₹|rs\.?\s*)?([\d,]+\.?\d*)\s*(crores?|cr\.?|lakhs?|lacs?|millions?|mn)\b(?!\s*%)",
                        clean_doc, re.I
                    )
                    if pr_pat_m and pr_rev_m:
                        pat_val = _clean_token_number(pr_pat_m.group(1))
                        rev_val = _clean_token_number(pr_rev_m.group(1))
                        pat_unit = (pr_pat_m.group(2) or "").lower()
                        rev_unit = (pr_rev_m.group(2) or "").lower()

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

                            exch_pr = str(ann_meta.get("exchange") or ("BSE" if "bseindia" in str(ann_meta.get("attchmntFile", "")).lower() else "NSE")).upper()
                            pdf_src_pr = f"{exch_pr}_ANNOUNCEMENT_PDF"

                            pr_rec = RawFinancialRecord(
                                symbol=symbol.upper(),
                                source=pdf_src_pr,
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
                                filing_id=f"{exch_pr}_PDF_{symbol.upper()}_{seq_id}_{period_end}",
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
        skip_urls: Optional[Set[str]] = None,
    ) -> List[RawFinancialRecord]:
        """
        Executes the Tier-3 Fallback:
        Checks official announcements with PDF attachments ONLY for periods where
        neither NSE XML nor BSE XML was provided.
        """
        sym_u = symbol.strip().upper()
        pdf_records: List[RawFinancialRecord] = []
        if not hasattr(self, "last_attempted_urls"):
            self.last_attempted_urls: Dict[str, List[str]] = {}
        self.last_attempted_urls[sym_u] = []
        skip_set = {str(u).split("?")[0].lower() if "nseindia" in str(u).lower() else str(u).lower() for u in (skip_urls or set()) if u}
        skip_raw_set = {str(u) for u in (skip_urls or set()) if u}

        def _score_ann(ann: Dict[str, Any]) -> int:
            d = str(ann.get("desc") or ann.get("headline") or ann.get("NEWSSUB") or "").lower()
            t = str(ann.get("attchmntText") or "").lower()
            fn = str(ann.get("attchmntFile") or ann.get("attachmentUrl") or "").lower()
            score = 0
            if "financial result" in d or "financial results" in d or "financial result" in t or "financial results" in t:
                score += 40
            if "reg 33" in d or "regulation 33" in d or "reg 33" in t or "regulation 33" in t:
                score += 30
            if "unaudited" in d or "unaudited" in t or "audited" in d or "audited" in t:
                score += 20
            if "outcome of board meeting" in d or "outcome of board meeting" in t or "outcome of the board meeting" in d:
                score += 50
            if "considered and approved" in d or "considered and approved" in t:
                score += 40
            if "standalone" in fn or "consolidated" in fn or "results" in fn:
                score += 15
            # Heavy penalty for advance meeting notices, prior intimations, and investor presentations
            if any(k in d for k in ("meeting of the board of directors of the company is scheduled", "meeting of the board is scheduled", "meeting scheduled", "prior intimation", "notice of board meeting", "board meeting notice")):
                score -= 100
            if "intimation" in fn or "intimation" in d:
                score -= 40
            if any(k in f"{d} {t} {fn}" for k in ("newspaper", "clipping", "clarification", "presentation", "press release", "transcript", "audio", "concall", "con. call", "allotment", "appointment", "customsorder", "investor update")):
                score -= 50
            return score

        sorted_announcements = sorted(announcements, key=_score_ann, reverse=True)
        downloaded_pdf_count = 0

        for ann in sorted_announcements:
            if not isinstance(ann, dict):
                continue

            # Check up to top 2 scored announcement attachments (stops early as soon as results are extracted)
            if downloaded_pdf_count >= 2:
                break

            # If we already parsed records for the requested cutoff, early exit immediately
            if pdf_records and any(r.period_end_date >= (min_cutoff or "") for r in pdf_records):
                break

            att_file = str(ann.get("attchmntFile") or ann.get("attachmentUrl") or "").strip()
            att_lower = att_file.lower()
            if not (".pdf" in att_lower or "annpdfopen" in att_lower):
                continue

            att_norm_key = att_file.split("?")[0].lower() if "nseindia" in att_lower else att_lower
            if att_norm_key in skip_set or att_file in skip_raw_set:
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

            if not is_financial_result_announcement(ann, att_file):
                continue

            # Deduplicate identical URLs within the same announcements list
            skip_set.add(att_norm_key)
            skip_raw_set.add(att_file)

            logger.info(
                f"📄 [PDF_FALLBACK] {sym_u}: Official exchange PDF found for period {p_end or 'UNKNOWN'} "
                f"(XML missing on NSE & BSE). Downloading {os.path.basename(att_file)}..."
            )

            pdf_bytes = self.download_pdf(att_file, announcement_metadata=ann)
            if not pdf_bytes:
                # Record attempted URL even when fetch fails so Step 3 doesn't immediately re-attempt the same dead URL
                self.last_attempted_urls[sym_u].append(att_file)
                record_extraction_failure_rca(
                    sym_u,
                    "UNABLE_TO_FETCH",
                    f"PDF not found or download failed from exchange ({os.path.basename(att_file)}).",
                )
                continue
            downloaded_pdf_count += 1

            pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
            hash_key = (sym_u, pdf_sha)
            with getattr(self, "_failed_hash_lock", threading.Lock()):
                already_failed_hash = hash_key in getattr(self, "_failed_pdf_hashes", set())
            if already_failed_hash:
                self.last_attempted_urls[sym_u].append(att_file)
                logger.debug(f"⏭️ [PDF_FALLBACK] {sym_u}: Skipping identical PDF sha256={pdf_sha[:10]} already known to yield 0 facts.")
                continue

            if hasattr(self, "last_failure_reason"):
                self.last_failure_reason.pop(sym_u, None)
            parsed = self.parse_sebi_reg33_pdf(pdf_bytes, sym_u, announcement_metadata=ann)
            rca_code, rca_desc = getattr(self, "last_failure_reason", {}).get(
                sym_u,
                (
                    "DATA_NOT_FOUND_IN_PDF",
                    f"Data not found in PDF: {os.path.basename(att_file)} was read, but no financial statement metrics could be extracted.",
                ),
            )
            if rca_code != "SEMAPHORE_TIMEOUT":
                self.last_attempted_urls[sym_u].append(att_file)
            if not parsed:
                if rca_code == "NON_FINANCIAL_ANNOUNCEMENT":
                    downloaded_pdf_count = max(0, downloaded_pdf_count - 1)
                    logger.info(f"⏭️ [PDF_FALLBACK] {sym_u}: {rca_desc} ({os.path.basename(att_file)})")
                    continue
                if rca_code != "SEMAPHORE_TIMEOUT" and hasattr(self, "_failed_pdf_hashes"):
                    with getattr(self, "_failed_hash_lock", threading.Lock()):
                        if len(self._failed_pdf_hashes) > 2000:
                            self._failed_pdf_hashes.clear()
                        self._failed_pdf_hashes.add(hash_key)
                record_extraction_failure_rca(sym_u, rca_code, f"{rca_desc} (File: {os.path.basename(att_file)})")
                logger.warning(
                    f"⚠️ [PDF_FALLBACK: {rca_code}] {sym_u}: {rca_desc} "
                    f"(file={os.path.basename(att_file)}, bytes={len(pdf_bytes)}, period={p_end or 'UNKNOWN'})"
                )
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

        if pdf_records:
            clear_extraction_failure_rca(sym_u)
        elif downloaded_pdf_count == 0:
            record_extraction_failure_rca(
                sym_u,
                "PDF_NOT_FOUND",
                f"PDF not found on NSE/BSE for period >= {min_cutoff or 'latest'}.",
            )

        return pdf_records
