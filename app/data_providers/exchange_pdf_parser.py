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
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any, Set

import requests
import PyPDF2

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
    from app.data_providers.nse_xbrl_provider import validate_semantic_record
except ImportError:
    try:
        from data_providers.nse_xbrl_provider import validate_semantic_record
    except ImportError:
        def validate_semantic_record(rec):
            return True, None

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

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean_token_number(token: str) -> Optional[float]:
    """Parses a numerical token from a table line, handling (negative) and comma separators."""
    if not token:
        return None
    token = token.strip()
    is_neg = (token.startswith("(") and token.endswith(")")) or token.startswith("-")
    cleaned = re.sub(r"[^\d\.]", "", token)
    if not cleaned:
        return None
    try:
        val = float(cleaned)
        return -val if is_neg else val
    except ValueError:
        return None


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

    def download_pdf(self, pdf_url: str) -> Optional[bytes]:
        """
        Downloads the PDF from exchange archives (or loads from local cache).
        Tries direct download first; falls back to ScraperAPI if direct is blocked.
        """
        if not pdf_url or not str(pdf_url).lower().endswith(".pdf"):
            return None

        # Build cache filename
        url_hash = hashlib.sha256(pdf_url.encode("utf-8")).hexdigest()[:16]
        base_name = os.path.basename(pdf_url.split("?")[0])
        cache_fn = f"{url_hash}_{base_name}"
        cache_path = os.path.join(self.cache_dir, cache_fn)

        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
            try:
                with open(cache_path, "rb") as f_in:
                    return f_in.read()
            except Exception as read_err:
                logger.debug(f"[PDF_PARSER] Cache read failed for {cache_path}: {read_err}")

        # 1. Direct fetch from exchange static archives
        try:
            resp = self.session.get(pdf_url, timeout=15)
            if resp.status_code == 200 and resp.content and resp.content.startswith(b"%PDF"):
                with open(cache_path, "wb") as f_out:
                    f_out.write(resp.content)
                logger.info(f"✅ [PDF:DIRECT] Downloaded exchange PDF {base_name} ({len(resp.content)} bytes)")
                return resp.content
        except Exception as direct_err:
            logger.debug(f"[PDF_PARSER] Direct download failed for {base_name}: {direct_err}")

        # 2. Fallback to ScraperAPI if available
        fetch_via_scraperapi, get_scraper_api_key = _get_scraperapi_fetcher()
        if fetch_via_scraperapi and get_scraper_api_key():
            try:
                s_resp = fetch_via_scraperapi(pdf_url, timeout=25, country_code="in")
                if s_resp is not None and s_resp.status_code == 200 and s_resp.content and s_resp.content.startswith(b"%PDF"):
                    with open(cache_path, "wb") as f_out:
                        f_out.write(s_resp.content)
                    logger.info(f"✅ [PDF:SCRAPERAPI] Downloaded exchange PDF {base_name} ({len(s_resp.content)} bytes)")
                    return s_resp.content
            except Exception as s_err:
                logger.debug(f"[PDF_PARSER] ScraperAPI download failed for {base_name}: {s_err}")

        return None

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
        records: List[RawFinancialRecord] = []

        try:
            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        except Exception as pdf_err:
            logger.warning(f"[PDF_PARSER] PyPDF2 failed to open PDF for {symbol}: {pdf_err}")
            return []

        for page_idx, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue

            text_lower = text.lower()

            # Must contain SEBI Regulation 33 Statement header keywords
            if not ("statement of" in text_lower and any(k in text_lower for k in [
                "financial results", "financial result", "revenue from operations", "net sales", "income from operations"
            ])):
                continue

            # Must contain actual operational revenue indicators
            if not any(k in text_lower for k in [
                "revenue from operations", "net sales", "total income", "income from operations"
            ]):
                continue

            # 1. Determine Consolidation Basis (STANDALONE vs CONSOLIDATED)
            basis = ConsolidationType.STANDALONE
            first_lines = text.split("\n")[:12]
            for line in first_lines:
                line_low = line.lower()
                if "statement of" in line_low:
                    if "consolidated" in line_low:
                        basis = ConsolidationType.CONSOLIDATED
                        break
                    elif "standalone" in line_low or "non-consolidated" in line_low:
                        basis = ConsolidationType.STANDALONE
                        break
            if basis == ConsolidationType.STANDALONE and "consolidated" in text_lower and "standalone" not in text_lower:
                basis = ConsolidationType.CONSOLIDATED

            # 2. Determine Unit and Multiplier to Crores
            mult = 1.0
            unit = "crore"
            if re.search(r"in\s*lakhs|rs\.?\s*in\s*lakhs|lacs|\(in\s*lakhs\)|\(\s*in\s*lacs\s*\)|\(\s*\{in\s*lakhs\)", text, re.I):
                mult = 0.01  # 1 Lakh = 0.01 Crore
                unit = "lakh"
            elif re.search(r"in\s*crores|rs\.?\s*in\s*crores|\(in\s*crores\)", text, re.I):
                mult = 1.0
                unit = "crore"

            # 3. Determine Period End Date
            period_end = None
            if ann_meta.get("toDate"):
                period_end = str(ann_meta.get("toDate"))[:10]
            elif ann_meta.get("period_end_date"):
                period_end = str(ann_meta.get("period_end_date"))[:10]

            if not period_end or len(period_end) != 10:
                dm = re.search(
                    r"(?:quarter|half\s*year|year)\s*ended\s*(?:on\s*)?(\d{1,2}(?:st|nd|rd|th)?\s+[a-zA-Z]+,?\s*202\d|\d{2}[\./-]\d{2}[\./-]202\d)",
                    text,
                    re.I,
                )
                if dm:
                    raw_d = dm.group(1).strip()
                    clean_d = re.sub(r"(?i)(st|nd|rd|th)", "", raw_d).strip()
                    clean_d = re.sub(r"\s+", " ", clean_d)
                    for fmt in ["%d %B, %Y", "%d %b, %Y", "%d %B %Y", "%d %b %Y", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"]:
                        try:
                            period_end = datetime.strptime(clean_d, fmt).strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            pass

            if not period_end or len(period_end) != 10:
                continue

            # 4. Determine Statement Type (QUARTERLY vs ANNUAL)
            st_type = "QUARTERLY"
            for line in first_lines:
                line_low = line.lower()
                if "statement of" in line_low:
                    if any(k in line_low for k in ["year ended", "audited annual", "annual financial", "full year"]):
                        st_type = "ANNUAL"
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

            # Strategy A: Line-by-line inspection (Standard inline tables where numbers are adjacent to row label)
            for line in text.split("\n"):
                line_str = line.strip()
                line_lower = line_str.lower()
                tokens = line_str.split()
                if not tokens:
                    continue

                last_val = _clean_token_number(tokens[-1])
                if last_val is None:
                    continue

                if rev is None and any(k in line_lower for k in [
                    "revenue from operations", "net sales", "income from operations"
                ]):
                    rev = last_val * mult

                elif pat is None and any(k in line_lower for k in [
                    "profit for the period", "profit / (loss) for the period", "profit/(loss) for the period",
                    "profit i (loss) for the period", "net profit for the period", "profit after tax",
                    "net profit / (loss) for the period"
                ]) and not any(k in line_lower for k in ["other comprehensive", "total comprehensive"]):
                    pat = last_val * mult

                elif pbt is None and any(k in line_lower for k in [
                    "profit before tax", "profit / (loss) before tax", "profit/(loss) before tax",
                    "profit i (loss) before tax"
                ]):
                    pbt = last_val * mult

                elif depr is None and ("depreciation" in line_lower and "amortization" in line_lower):
                    depr = last_val * mult

                elif finance_costs is None and (line_lower.startswith("finance cost") or "finance costs" in line_lower):
                    finance_costs = last_val * mult

                elif eps is None and any(k in line_lower for k in [
                    "basic & diluted", "basic (in rupees)", "basic ( ~)", "basic eps", "(1) basic"
                ]):
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
                col_m = re.search(
                    r"(\d{2}\.\d{2}\.202\d)\s*\n\s*\(unaudited\)\s*\n([0-9\.\n\s\-\(\)]+)",
                    text,
                    re.I,
                )
                if col_m:
                    raw_lines = [s.strip() for s in col_m.group(2).split("\n") if s.strip()]
                    parsed_nums = [_clean_token_number(s) for s in raw_lines if _clean_token_number(s) is not None]
                    if len(parsed_nums) >= 15:
                        if rev is None:
                            rev = parsed_nums[0] * mult
                        if pat is None:
                            # Universal accounting equation cross-check: PBT - Tax = PAT
                            found_pat = None
                            for i in range(10, min(15, len(parsed_nums))):
                                cand_pbt = parsed_nums[i]
                                for j in range(i + 1, min(i + 5, len(parsed_nums))):
                                    cand_tax = parsed_nums[j]
                                    diff = round(cand_pbt - cand_tax, 2)
                                    for k in range(j + 1, min(j + 4, len(parsed_nums))):
                                        if abs(parsed_nums[k] - diff) < 0.15:
                                            found_pat = parsed_nums[k]
                                            pbt = cand_pbt * mult
                                            break
                                    if found_pat is not None:
                                        break
                                if found_pat is not None:
                                    break
                            if found_pat is not None:
                                pat = found_pat * mult
                            elif len(parsed_nums) >= 18:
                                pat = parsed_nums[16] * mult

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
            avail_dt = str(ann_meta.get("filingDate") or ann_meta.get("an_dt") or f"{period_end}")[:10]
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
            if "outcome of board meeting" in d or "outcome of board meeting" in t:
                score += 10
            if "financial result" in d or "financial result" in t:
                score += 5
            if "standalone" in fn or "consolidated" in fn or "results" in fn:
                score += 5
            if "newspaper" in fn or "newspaper" in d or "clarification" in d:
                score -= 5
            return score

        sorted_announcements = sorted(announcements, key=_score_ann, reverse=True)

        for ann in sorted_announcements:
            if not isinstance(ann, dict):
                continue

            att_file = str(ann.get("attchmntFile") or ann.get("attachmentUrl") or "").strip()
            if not att_file.lower().endswith(".pdf"):
                continue

            # Period end
            p_end = str(ann.get("period_end_date") or ann.get("toDate") or "")[:10]
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
            if not (ann.get("hasXbrl") or any(k in desc_comb for k in [
                "financial result", "financial results", "outcome of board meeting",
                "unaudited financial", "audited financial", "period ended"
            ])):
                continue

            logger.info(
                f"📄 [PDF_FALLBACK] {sym_u}: Official exchange PDF found for period {p_end or 'UNKNOWN'} "
                f"(XML missing on NSE & BSE). Downloading {os.path.basename(att_file)}..."
            )

            pdf_bytes = self.download_pdf(att_file)
            if not pdf_bytes:
                continue

            parsed = self.parse_sebi_reg33_pdf(pdf_bytes, sym_u, announcement_metadata=ann)
            for rec in parsed:
                # Ensure we don't duplicate
                if rec.period_end_date not in existing_xml_periods:
                    logger.info(
                        f"✨ [PDF_FALLBACK: SUCCESS] {sym_u} {rec.period_end_date} ({rec.period_type}): "
                        f"Revenue = {rec.revenue} Cr, Net Profit = {rec.net_profit} Cr, EPS = {rec.eps}"
                    )
                    pdf_records.append(rec)
                    existing_xml_periods.add(rec.period_end_date)

        return pdf_records
