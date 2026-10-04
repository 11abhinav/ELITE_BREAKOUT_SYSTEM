"""
app/data_providers/data_availability_auditor.py
===============================================
QUALITY_DATA_AVAILABILITY_AUDITOR — Recovery-Diagnostics Layer.

Source Hierarchy:
  Tier 1 — Verified / Production-Authoritative:
    - Upstox Fundamentals API
    - Upstox Key Ratios
    - NSE/XBRL
    - Exchange filings / PIT filing store
    - Local verified raw filing cache
  Tier 2 — Approved Diagnostic Broker Source:
    - FYERS: Approved diagnostic broker source for the capabilities exposed by its public API v3
      (market quotes /data/quotes, LTP, volume, depth). Public REST fundamentals / key-ratios
      are NOT AVAILABLE / DOCUMENTED. Web/platform fundamentals (EV/EBITDA, ROCE, ROE) serve as
      reference evidence only unless an explicitly authorized machine-readable interface exists.
  Tier 3 — Forensic / Reference-Only:
    - Screener.in (FORENSIC_REFERENCE_ONLY, strictly offline discovery, never write to PIT, never BUY)

Core Classifications (Mandatory Governance Rule):
  1. PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE (DATA PROVIDER DISCREPANCY)
     Upstox = Missing, NSE = Missing, FYERS = Available, Screener = Available
  2. SCREENER_ONLY_DATA_SOURCE (ONLY VERIFIED/ACCESSIBLE REFERENCE SOURCE FOUND)
     Upstox = Missing, NSE = Missing, PIT = Missing, FYERS = Missing/Unavailable, Screener = Available.
     Semantic Meaning: Among the sources we are authorized and able to verify programmatically in our
     audit layer, only Screener currently exposes this reference information. It highlights a potential
     upstream data ingestion gap in our primary pipeline, but does NOT assert that the data is absent
     from other non-public screens or web platform UI views.
  3. DATA_UNAVAILABLE_VERIFIED (GENUINELY UNAVAILABLE)
     Upstox = Missing, NSE = Missing, PIT = Missing, FYERS = Missing, Screener = Missing
  4. FYERS_ONLY_DATA_SOURCE
     Upstox = Missing, NSE = Missing, PIT = Missing, Screener = Missing, FYERS = Available
  5. Other granular diagnoses: INSUFFICIENT_HISTORICAL_DEPTH, STALE_PIT, UNPROCESSED_FILING,
     PARSER_MAPPING_FAILURE, CALCULATION_FAILURE, STRUCTURAL_INELIGIBLE.

Hard Governance Invariant:
  Even when Screener or Fyers reports availability:
    canonical_pit_write = FALSE
    production_metric_write = FALSE
    buy_decision = BLOCKED
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    from database import get_connection, insert_notification
except Exception:
    try:
        from app.database import get_connection, insert_notification
    except Exception:
        get_connection = None
        insert_notification = None

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REFERENCE_DIR = os.path.join(BASE_DIR, "data", "reference_availability")
REPORT_DIR = os.path.join(BASE_DIR, "data", "reports")
FYERS_ATTESTATION_FILE = os.path.join(REFERENCE_DIR, "fyers_availability.csv")
SCREENER_ATTESTATION_FILE = os.path.join(REFERENCE_DIR, "screener_availability.csv")
DEFAULT_ATTESTATION_MAX_AGE_DAYS = int(os.getenv("AVAILABILITY_ATTESTATION_MAX_AGE_DAYS", "30"))

_FORBIDDEN_VALUE_COLUMNS = {"value", "reference_value", "metric_value", "val", "number", "amount"}

_MIN_ANNUAL_PERIODS: Dict[str, int] = {
    "sales_cagr_5y": 6,
    "pat_cagr_5y": 6,
    "ROCE": 5,
    "cfo_pat_5y": 5,
    "debt": 1,
}

_FIELD_ALIASES: Dict[str, str] = {
    "roce": "ROCE", "roce_5y_avg": "ROCE",
    "sales_cagr": "sales_cagr_5y", "sales_cagr_5y": "sales_cagr_5y",
    "pat_cagr": "pat_cagr_5y", "pat_cagr_5y": "pat_cagr_5y",
    "cfo_pat": "cfo_pat_5y", "cfo_pat_5y": "cfo_pat_5y", "cfo_pat_5y_ratio": "cfo_pat_5y",
    "debt": "debt", "debt_to_equity": "debt", "d/e": "debt",
    "current_ev_ebitda": "current_ev_ebitda", "ev_ebitda": "current_ev_ebitda",
}


def normalize_field(name: str) -> str:
    key = str(name or "").strip()
    return _FIELD_ALIASES.get(key.lower(), key)


class AuthorityTier(str, Enum):
    TIER_1_PRODUCTION = "TIER_1_PRODUCTION"
    TIER_2_DIAGNOSTIC = "TIER_2_DIAGNOSTIC"
    TIER_3_FORENSIC = "TIER_3_FORENSIC"


class ReferenceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"            # independent source indicates metric exists
    MISSING = "MISSING"                # independent source checked and metric absent
    STALE = "STALE"                    # attestation older than max age
    NOT_CHECKED = "NOT_CHECKED"        # symbol/field not attested
    NOT_CONFIGURED = "NOT_CONFIGURED"  # access mechanism not configured
    REJECTED = "REJECTED"              # attestation file violated value boundary
    ERROR = "ERROR"


class AvailabilityClassification(str, Enum):
    PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE = "PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE"
    SCREENER_ONLY_DATA_SOURCE = "SCREENER_ONLY_DATA_SOURCE"
    REFERENCE_ONLY_AVAILABLE = "REFERENCE_ONLY_AVAILABLE"
    FYERS_ONLY_DATA_SOURCE = "FYERS_ONLY_DATA_SOURCE"
    DATA_UNAVAILABLE_VERIFIED = "DATA_UNAVAILABLE_VERIFIED"
    CONFIRMED_NO_DATA_ANYWHERE = "CONFIRMED_NO_DATA_ANYWHERE"
    INSUFFICIENT_HISTORICAL_DEPTH = "INSUFFICIENT_HISTORICAL_DEPTH"
    HISTORICAL_FILING_GAP = "HISTORICAL_FILING_GAP"
    INVALID_CAGR_BASE = "INVALID_CAGR_BASE"
    SYMBOL_MAPPING_FAILURE = "SYMBOL_MAPPING_FAILURE"
    STALE_PIT = "STALE_PIT"
    UNPROCESSED_FILING = "UNPROCESSED_FILING"
    PARSER_OR_FIELD_MAPPING_FAILURE = "PARSER_OR_FIELD_MAPPING_FAILURE"
    PROVIDER_PARSER_FAILURE = "PARSER_OR_FIELD_MAPPING_FAILURE"  # backward-compatible alias
    PARSER_MAPPING_FAILURE = "PARSER_OR_FIELD_MAPPING_FAILURE"   # backward-compatible alias
    CALCULATION_FAILURE = "CALCULATION_FAILURE"
    STRUCTURAL_INELIGIBLE = "STRUCTURAL_INELIGIBLE"
    STRUCTURALLY_UNSUPPORTED = "STRUCTURALLY_UNSUPPORTED"
    NOT_APPLICABLE_FOR_INSTRUMENT = "STRUCTURALLY_UNSUPPORTED"  # alias
    TIER1_RECOVERY_NOT_EXHAUSTED = "TIER1_RECOVERY_NOT_EXHAUSTED"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


_SEVERITY: Dict[AvailabilityClassification, str] = {
    AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE: "CRITICAL",
    AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE: "HIGH",
    AvailabilityClassification.REFERENCE_ONLY_AVAILABLE: "HIGH",
    AvailabilityClassification.FYERS_ONLY_DATA_SOURCE: "HIGH",
    AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE: "HIGH",
    AvailabilityClassification.SYMBOL_MAPPING_FAILURE: "HIGH",
    AvailabilityClassification.HISTORICAL_FILING_GAP: "WARNING",
    AvailabilityClassification.INVALID_CAGR_BASE: "WARNING",
    AvailabilityClassification.CALCULATION_FAILURE: "WARNING",
    AvailabilityClassification.UNPROCESSED_FILING: "WARNING",
    AvailabilityClassification.STALE_PIT: "WARNING",
    AvailabilityClassification.TIER1_RECOVERY_NOT_EXHAUSTED: "WARNING",
    AvailabilityClassification.PROVIDER_FAILURE: "WARNING",
    AvailabilityClassification.INSUFFICIENT_HISTORICAL_DEPTH: "INFO",
    AvailabilityClassification.STRUCTURAL_INELIGIBLE: "INFO",
    AvailabilityClassification.STRUCTURALLY_UNSUPPORTED: "INFO",
    AvailabilityClassification.DATA_UNAVAILABLE_VERIFIED: "INFO",
    AvailabilityClassification.CONFIRMED_NO_DATA_ANYWHERE: "INFO",
}


@dataclass(frozen=True)
class ReferenceCheck:
    source: str
    tier: AuthorityTier
    status: ReferenceStatus
    checked_at: Optional[str] = None


@dataclass
class AvailabilityAuditRecord:
    symbol: str
    isin: str
    scanner: str
    field: str
    required_for_gate: str
    upstox_status: str
    nse_status: str
    exchange_filing_status: str
    pit_status: str
    local_cache_status: str
    fyers_status: str
    screener_status: str
    classification: str
    bse_status: str = field(default="NOT_QUERIED")
    production_value_written: bool = field(default=False)
    buy_allowed: bool = field(default=False)
    admin_alert_generated: bool = field(default=False)
    checked_at: str = field(default="")
    upstox_key_ratios: str = field(default="N/A")
    severity: str = field(default="INFO")
    admin_action: str = field(default="")
    admin_message: str = field(default="")
    # [RULE 67 CHANGE-RATIONALE: Distinct Telemetry Fields (§ Pending Item 5)]
    source_of_truth: str = field(default="NONE")
    availability_status: str = field(default="UNAVAILABLE")
    production_eligibility: str = field(default="INELIGIBLE")
    block_reason: str = field(default="NONE")
    quarantine_action: str = field(default="NONE")
    quarantine_until: Optional[str] = field(default=None)
    selected_periods: List[str] = field(default_factory=list)


class OperatorAttestedReferenceSource:
    """Reads attested availability (YES/NO) per symbol+field. Never loads values."""

    def __init__(self, name: str, tier: AuthorityTier, path: str,
                 max_age_days: int = DEFAULT_ATTESTATION_MAX_AGE_DAYS):
        self.name = name
        self.tier = tier
        self.path = path
        self.max_age_days = max_age_days
        self._entries: Dict[Tuple[str, str], Tuple[bool, Optional[date], str]] = {}
        self._load_status = ReferenceStatus.NOT_CONFIGURED
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._load_status = ReferenceStatus.NOT_CONFIGURED
            return
        try:
            with open(self.path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                cols = {str(c or "").strip().lower() for c in (reader.fieldnames or [])}
                leaked = {c for c in cols if c in _FORBIDDEN_VALUE_COLUMNS or "value" in c or "evidence" in c}
                if leaked:
                    logger.error(
                        f"🚫 [AVAILABILITY_AUDIT] {self.name} attestation file {self.path} REJECTED: "
                        f"value-like columns {sorted(leaked)} violate availability-only boundary."
                    )
                    self._load_status = ReferenceStatus.REJECTED
                    return
                for row in reader:
                    sym = str(row.get("symbol") or "").strip().upper()
                    fld = normalize_field(row.get("field") or "")
                    avail_raw = str(row.get("available") or "").strip().upper()
                    if not sym or not fld or avail_raw not in ("YES", "NO", "Y", "N", "TRUE", "FALSE"):
                        continue
                    checked = None
                    raw_dt = str(row.get("checked_at") or "").strip()
                    if raw_dt:
                        try:
                            checked = datetime.fromisoformat(raw_dt[:10]).date()
                        except ValueError:
                            checked = None
                    self._entries[(sym, fld)] = (avail_raw in ("YES", "Y", "TRUE"), checked, raw_dt)
            self._load_status = ReferenceStatus.NOT_CHECKED
        except Exception as e:
            logger.warning(f"[AVAILABILITY_AUDIT] Failed to load {self.name} attestations ({self.path}): {e}")
            self._load_status = ReferenceStatus.ERROR

    def check(self, symbol: str, field_name: str, today: Optional[date] = None) -> ReferenceCheck:
        if self._load_status in (ReferenceStatus.NOT_CONFIGURED, ReferenceStatus.REJECTED, ReferenceStatus.ERROR):
            return ReferenceCheck(self.name, self.tier, self._load_status)
        entry = self._entries.get((symbol.upper(), normalize_field(field_name)))
        if entry is None:
            return ReferenceCheck(self.name, self.tier, ReferenceStatus.NOT_CHECKED)
        available, checked, raw_dt = entry
        today = today or datetime.now(IST).date()
        if checked is None or (today - checked).days > self.max_age_days:
            return ReferenceCheck(self.name, self.tier, ReferenceStatus.STALE, raw_dt or None)
        status = ReferenceStatus.AVAILABLE if available else ReferenceStatus.MISSING
        return ReferenceCheck(self.name, self.tier, status, raw_dt)


class FyersReferenceSource(OperatorAttestedReferenceSource):
    def __init__(self, path: str = FYERS_ATTESTATION_FILE, **kw):
        super().__init__("FYERS", AuthorityTier.TIER_2_DIAGNOSTIC, path, **kw)


class ScreenerReferenceSource(OperatorAttestedReferenceSource):
    def __init__(self, path: str = SCREENER_ATTESTATION_FILE, **kw):
        super().__init__("SCREENER", AuthorityTier.TIER_3_FORENSIC, path, **kw)


def _statement_status(trace: Dict[str, Any], prefix: str) -> str:
    if not trace:
        return "NOT_QUERIED"
    explicit_status = trace.get(f"{prefix}_status")
    if explicit_status in ("NOT_CHECKED", "UNKNOWN", "NOT_ATTEMPTED", "ERROR", "TIMEOUT"):
        return str(explicit_status)
    if prefix == "upstox" and trace.get("isin_resolved") is False and not trace.get("isin"):
        return "ISIN_UNRESOLVED"
    n = int(trace.get(f"{prefix}_records", 0) or 0)
    if n == 0:
        return explicit_status if explicit_status else "MISSING"
    annual = int(trace.get(f"{prefix}_annual", 0) or 0)
    return f"RECORDS_PRESENT(annual={annual})"


class DataAvailabilityAuditor:
    """Classifies every field still missing after Tier-1 recovery."""

    def __init__(self, scanner_name: str = "QUALITY_COMPOUNDER",
                 references: Optional[List[OperatorAttestedReferenceSource]] = None,
                 persist_to_db: Optional[bool] = None):
        self.scanner_name = scanner_name
        self.references = references if references is not None else [FyersReferenceSource(), ScreenerReferenceSource()]
        self.persist_to_db = bool(os.getenv("DATABASE_URL")) if persist_to_db is None else persist_to_db

    def classify_field(self, symbol: str, field_name: str, trace: Dict[str, Any],
                       today: Optional[date] = None,
                       screener: Optional[ReferenceCheck] = None,
                       fyers: Optional[ReferenceCheck] = None) -> AvailabilityAuditRecord:
        fld = normalize_field(field_name)
        trace = trace or {}
        checks = {r.name: r.check(symbol, fld, today=today) for r in self.references}
        if fyers is None:
            fyers = checks.get("FYERS")
        if screener is None:
            screener = checks.get("SCREENER")

        upstox = _statement_status(trace, "upstox")
        is_bse_only = bool(trace.get("is_bse_only"))
        # BSE status resolution
        is_bse_only = bool(trace.get("is_bse_only"))
        bse = trace.get("bse_status")
        if not bse:
            if is_bse_only:
                bse = "BSE_CHECKED"
            elif trace.get("bse_raw_count", 0) > 0 and trace.get("bse_usable_count", 0) == 0:
                bse = f"RAW_DATA_PRESENT_PARSER_FAILURE(raw={trace.get('bse_raw_count')},usable=0)"
            elif trace.get("bse_records", 0) > 0:
                bse = _statement_status(trace, "bse")
            else:
                bse = "NOT_QUERIED" if (not is_bse_only and trace.get("nse_records", 0) > 0) else "NO_DATA"

        if is_bse_only:
            nse = "NOT_APPLICABLE"
            exchange_filing_status = "BSE_CHECKED"
        elif trace.get("nse_raw_count", 0) > 0 and trace.get("nse_usable_count", 0) == 0:
            nse = f"RAW_DATA_PRESENT_PARSER_FAILURE(raw={trace.get('nse_raw_count')},usable=0)"
            exchange_filing_status = "NSE_HTTP_200_PARSER_FAILED"
        elif trace.get("nse_records", 0) > 0:
            nse = _statement_status(trace, "nse")
            exchange_filing_status = trace.get("exchange_filing_status", "NSE_CHECKED")
        else:
            nse = "CHECKED_NO_DATA"
            exchange_filing_status = trace.get("exchange_filing_status", "MISSING")

        local_raw = _statement_status(trace, "local")
        isin = trace.get("isin", "")

        if fld == "current_ev_ebitda":
            kr = trace.get("key_ratios_status") if trace.get("key_ratios_attempted") else "NOT_ATTEMPTED"
        else:
            kr = "N/A"

        # [RULE 67 CHANGE-RATIONALE: FYERS Real Exhaustion Stage.
        # FYERS must NEVER remain NOT_CHECKED. If not explicitly verified, fundamental balance-sheet
        # ratio fields evaluate to UNSUPPORTED_FIELD under official FYERS REST API v3 capabilities:
        # "FYERS adapter classifies fields as UNSUPPORTED_FIELD when the API surface available to
        # the application does not expose the required fundamental field."]
        fyers_status_str: str
        if trace.get("fyers_status"):
            fyers_status_str = str(trace.get("fyers_status"))
        elif fyers and fyers.status not in (ReferenceStatus.NOT_CHECKED, ReferenceStatus.NOT_CONFIGURED):
            fyers_status_str = fyers.status.value
        elif trace.get("fyers_key_ratios_status"):
            fyers_status_str = "UNSUPPORTED_FIELD"
        else:
            if fld in ("roce", "ROCE", "roe", "ROE", "debt", "sales_cagr_5y", "pat_cagr_5y", "cfo_pat_5y", "current_ev_ebitda"):
                fyers_status_str = "UNSUPPORTED_FIELD"
            else:
                fyers_status_str = "NO_DATA"

        fyers_avail = (fyers_status_str == "AVAILABLE")
        screener_avail = screener and screener.status == ReferenceStatus.AVAILABLE
        fyers_missing = (fyers_status_str in ("MISSING", "NO_DATA", "UNSUPPORTED_FIELD"))
        screener_missing = screener and screener.status == ReferenceStatus.MISSING

        max_annual = max(int(trace.get("upstox_annual", 0) or 0), int(trace.get("nse_annual", 0) or 0),
                         int(trace.get("bse_annual", 0) or 0), int(trace.get("local_annual", 0) or 0))
        min_needed = _MIN_ANNUAL_PERIODS.get(fld)
        primary_has_enough = min_needed is not None and max_annual >= min_needed

        raw_records = max(
            int(trace.get("nse_records", 0) or 0),
            int(trace.get("bse_records", 0) or 0),
            int(trace.get("upstox_records", 0) or 0),
            int(trace.get("local_records", 0) or 0),
            int(trace.get("raw_rows_returned", 0) or 0),
            int(trace.get("nse_raw_count", 0) or 0),
            int(trace.get("bse_raw_count", 0) or 0),
        )
        usable_fields = trace.get("usable_fields", trace.get("nse_usable_count"))
        parser_error = trace.get("parser_error")
        http_ok = trace.get("http_status") == 200 or raw_records > 0 or trace.get("nse_raw_count", 0) > 0 or trace.get("bse_raw_count", 0) > 0

        # Scenario: Provider returned HTTP 200 & raw records, but 0 usable fields were extracted
        is_parser_failure = (
            parser_error is not None
            or (http_ok and raw_records > 0 and (usable_fields == 0 or usable_fields is None))
            or (trace.get("nse_parser_status") == "PARSER_OR_FIELD_MAPPING_FAILURE")
            or (trace.get("bse_status") == "BSE_PARSE_FAILURE")
        )

        # ── Mandatory Precedence Hierarchy (User Frozen Order) ───────────────
        has_filing_gap = bool(trace.get("filing_gap_detected") or trace.get("filing_gap") or trace.get("has_gap"))
        has_invalid_base = bool(trace.get("invalid_base") or trace.get("base_value_non_positive") or trace.get("negative_base"))
        isin_unresolved = bool(
            trace.get("symbol_mapping_failure")
            or trace.get("symbol_not_found")
            or (trace.get("isin_resolved") is False and not trace.get("isin"))
            or (upstox == "ISIN_UNRESOLVED")
        )

        is_bank_or_financial = bool(
            symbol.upper() in {"HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "YESBANK", "PNB", "BANKBARODA", "INDUSINDBK", "CANBK", "IDFCFIRSTB", "FEDERALBNK"}
            or trace.get("is_bank")
            or "BANK" in str(trace.get("industry") or "").upper()
            or "FINANCIAL" in str(trace.get("industry") or "").upper()
        )
        bse_st = trace.get("bse_status") or bse
        nse_status_val = trace.get("nse_status")
        nse_parser_val = trace.get("nse_parser_status")
        if nse_status_val in ("NOT_CHECKED", "UNKNOWN", "NOT_ATTEMPTED") or nse_parser_val in ("NOT_CHECKED", "UNKNOWN", "NOT_ATTEMPTED"):
            nse_st = "NOT_CHECKED"
        else:
            nse_st = nse_parser_val or nse_status_val or nse
        is_provider_failure = (
            bse_st in ("BSE_FEED_ACCESS_REQUIRED", "BSE_HTTP_ERROR")
            or "HTTP_ERROR" in str(nse_st)
            or "ERROR" in str(upstox)
        )

        if is_bank_or_financial and fld in ("roce_5y", "ROCE", "debt_to_equity", "debt", "sales_cagr_5y"):
            cls = AvailabilityClassification.STRUCTURALLY_UNSUPPORTED
            action = f"STRUCTURALLY_NOT_APPLICABLE_FOR_FINANCIAL_INSTITUTION ({fld.upper()})"
        elif screener_avail and (is_parser_failure or trace.get("exhausted") or trace.get("all_providers_exhausted")):
            # User acceptance case: full provider exhaustion with Screener reference available
            cls = AvailabilityClassification.REFERENCE_ONLY_AVAILABLE
            action = "INVESTIGATE_UPSTREAM_PARSER_OR_MAPPING_REFERENCE_FOUND_ON_SCREENER"
        elif is_parser_failure:
            cls = AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE
            action = f"INVESTIGATE_UPSTREAM_PARSER_OR_MAPPING (HTTP 200 raw filings present ({raw_records} records), but 0 usable fields extracted)"
        elif isin_unresolved and not is_bse_only:
            cls = AvailabilityClassification.SYMBOL_MAPPING_FAILURE
            action = "RESOLVE_SECURITY_ISIN_OR_SYMBOL_MAPPING"
        elif has_filing_gap:
            cls = AvailabilityClassification.HISTORICAL_FILING_GAP
            action = f"HISTORICAL_FILING_GAP_BLOCKING_{fld.upper()}"
        elif max_annual > 0 and min_needed is not None and max_annual < min_needed:
            cls = AvailabilityClassification.INSUFFICIENT_HISTORICAL_DEPTH
            action = f"CONFIRMED_SHORT_HISTORY (has {max_annual} annual periods, requires {min_needed})"
        elif has_invalid_base:
            cls = AvailabilityClassification.INVALID_CAGR_BASE
            action = f"INVALID_BASE_PERIOD_VALUE_FOR_{fld.upper()}"
        elif fyers_avail and screener_avail:
            cls = AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE
            action = "INVESTIGATE_UPSTOX_NSE_PARSER_OR_MAPPING"
        elif screener_avail and not fyers_avail:
            cls = AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE
            action = "INVESTIGATE_WHY_VALUE_IN_SCREENER_CANNOT_BE_RECOVERED_FROM_VERIFIED_SOURCE"
        elif fyers_avail and not screener_avail:
            cls = AvailabilityClassification.FYERS_ONLY_DATA_SOURCE
            action = "INVESTIGATE_FYERS_API_INGESTION_FOR_VERIFIED_PIPELINE"
        elif is_provider_failure:
            cls = AvailabilityClassification.PROVIDER_FAILURE
            action = f"PROVIDER_FAILURE ({bse_st or nse_st or upstox}) (operational retry; no 7-day quarantine)"
        elif primary_has_enough:
            cls = AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE
            action = "INVESTIGATE_PARSER_CALCULATION_OR_NEGATIVE_BASE"
        elif fyers_missing and (not screener_avail):
            # Acceptance Rule: CONFIRMED_NO_DATA_ANYWHERE must only be emitted when:
            # NSE = terminal result, BSE = terminal result, Upstox = terminal result, FYERS = terminal result
            # and none produced a valid metric.
            # NOT_CHECKED or gateway/auth failures make that classification impossible.
            nse_terminal = (
                is_bse_only
                or nse_st in ("CHECKED_NO_DATA", "NO_DATA_RETURNED", "NOT_APPLICABLE", "NO_RECORDS_FOUND", "PARSE_SUCCESS")
            )
            bse_terminal = (
                bse_st in ("BSE_NO_DATA", "BSE_SYMBOL_NOT_FOUND", "NSE_SUFFICIENT", "NO_DATA", "BSE_AVAILABLE")
            )
            upstox_terminal = (
                upstox in ("MISSING", "NO_DATA", "ISIN_UNRESOLVED", "NO_RECORDS_FOUND")
            )
            fyers_terminal = (
                fyers_status_str in ("UNSUPPORTED_FIELD", "NO_DATA", "MISSING")
            )

            any_non_terminal = (
                (not is_bse_only and nse_st in ("NOT_CHECKED", "UNKNOWN", "NOT_ATTEMPTED", "HTTP_ERROR"))
                or (bse_st in ("NOT_CHECKED", "UNKNOWN", "NOT_ATTEMPTED", "BSE_HTTP_ERROR", "BSE_FEED_ACCESS_REQUIRED"))
                or (upstox in ("NOT_CHECKED", "UNKNOWN", "NOT_ATTEMPTED", "ERROR", "TIMEOUT"))
                or (fyers_status_str in ("NOT_CHECKED", "UNKNOWN", "NOT_ATTEMPTED", "ERROR"))
            )
            all_terminal = (
                raw_records == 0
                and not any_non_terminal
                and nse_terminal
                and bse_terminal
                and upstox_terminal
                and fyers_terminal
            )
            if (
                trace.get("confirmed_no_data")
                or trace.get("all_providers_exhausted")
                or bool(trace.get("bse_status"))
            ) and all_terminal:
                cls = AvailabilityClassification.CONFIRMED_NO_DATA_ANYWHERE
                action = "CONFIRMED_NO_DATA_ANYWHERE (hard data block confirmed across all providers; 7-day quarantine applied)"
            elif all_terminal:
                cls = AvailabilityClassification.DATA_UNAVAILABLE_VERIFIED
                action = "DATA_UNAVAILABLE_VERIFIED (hard data block confirmed across all providers; 7-day quarantine applied)"
            else:
                cls = AvailabilityClassification.TIER1_RECOVERY_NOT_EXHAUSTED
                action = "RECOVERY_INCOMPLETE_OR_UNCHECKED (operational retry; CONFIRMED_NO_DATA_ANYWHERE impossible)"
        elif not trace or (fld == "current_ev_ebitda" and kr == "NOT_ATTEMPTED"):
            cls = AvailabilityClassification.TIER1_RECOVERY_NOT_EXHAUSTED
            action = "RUN_TIER1_PRIMARY_RECOVERY"
        else:
            cls = AvailabilityClassification.TIER1_RECOVERY_NOT_EXHAUSTED
            action = "TIER1_RECOVERY_NOT_EXHAUSTED (operational retry)"

        gate_name = "VALUATION" if fld == "current_ev_ebitda" else "QUALITY"

        # Pre-format exact custom notification text
        if cls in (AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE, AvailabilityClassification.REFERENCE_ONLY_AVAILABLE):
            admin_msg = (
                f"🚨 DATA SOURCE NOTICE: {symbol.upper()} — {fld} found on Screener.in but unavailable from "
                f"primary authoritative providers (Upstox/NSE/Exchange). This data will NOT be used for trading decisions. "
                f"Potential upstream ingestion gap flagged for review."
            )
        elif cls == AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE:
            admin_msg = (
                f"🚨 DATA PROVIDER DISCREPANCY: {symbol.upper()} — {fld} failed on Upstox/NSE but exists in "
                f"independent diagnostic source (FYERS/Screener). Ingestion gap flagged for review."
            )
        elif cls == AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE:
            admin_msg = (
                f"🚨 PARSER OR FIELD MAPPING FAILURE: {symbol.upper()} — {fld}. HTTP 200 raw filings were received "
                f"and parsed ({raw_records} raw records), but 0 usable fields were extracted. Upstream parser inspection required."
            )
        else:
            admin_msg = ""

        # Resolve distinct telemetry concepts (§ Pending Item 5)
        source_of_truth = "NONE"
        availability_status = "UNAVAILABLE"
        production_eligibility = "INELIGIBLE"
        block_reason = "NONE"
        quarantine_action = "NONE"
        quarantine_until = None

        field_resolved_source = trace.get("field_sources", {}).get(fld)
        if field_resolved_source is None and trace.get("recovered_source") == "CANONICAL_LOCAL":
            field_resolved_source = "CANONICAL_LOCAL"
        canonical_hit = bool(field_resolved_source == "CANONICAL_LOCAL")

        if canonical_hit:
            source_of_truth = "CANONICAL_LOCAL"
            availability_status = "AVAILABLE"
            production_eligibility = "ELIGIBLE"
            block_reason = "NONE"
            quarantine_action = "NONE"
        elif cls == AvailabilityClassification.STRUCTURALLY_UNSUPPORTED:
            source_of_truth = "NONE"
            availability_status = "STRUCTURALLY_UNSUPPORTED"
            production_eligibility = "INELIGIBLE"
            block_reason = "STRUCTURALLY_NOT_APPLICABLE_FOR_FINANCIAL_INSTITUTION"
            quarantine_action = "NONE"
        elif cls == AvailabilityClassification.INVALID_CAGR_BASE:
            source_of_truth = "NONE"
            availability_status = "INVALID_BASE"
            production_eligibility = "INELIGIBLE"
            block_reason = "MATHEMATICALLY_INVALID_BASE"
            quarantine_action = "NONE"
        elif cls in (AvailabilityClassification.REFERENCE_ONLY_AVAILABLE, AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE):
            source_of_truth = "NONE"
            availability_status = "REFERENCE_ONLY"
            production_eligibility = "INELIGIBLE"
            block_reason = "SCREENER_REFERENCE_ONLY_GOVERNANCE_BLOCKED"
            quarantine_action = "NONE"
        elif cls == AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE:
            source_of_truth = "NONE"
            availability_status = "PARSER_FAILURE"
            production_eligibility = "INELIGIBLE"
            block_reason = "UPSTREAM_PARSER_OR_MAPPING_FAILURE"
            quarantine_action = "NONE"
        elif cls in (AvailabilityClassification.CONFIRMED_NO_DATA_ANYWHERE, AvailabilityClassification.DATA_UNAVAILABLE_VERIFIED):
            source_of_truth = "NONE"
            availability_status = "UNAVAILABLE"
            production_eligibility = "INELIGIBLE"
            block_reason = "CONFIRMED_ABSENT_ACROSS_ALL_PROVIDERS"
            quarantine_action = "7_DAY_QUARANTINE"
            quarantine_until = (datetime.now(IST) + timedelta(days=7)).isoformat()
        elif cls == AvailabilityClassification.INSUFFICIENT_HISTORICAL_DEPTH:
            source_of_truth = "NONE"
            availability_status = "SHORT_HISTORY"
            production_eligibility = "INELIGIBLE"
            block_reason = f"INSUFFICIENT_ANNUAL_OBSERVATIONS: {max_annual} < {min_needed}"
            quarantine_action = "7_DAY_QUARANTINE"
            quarantine_until = (datetime.now(IST) + timedelta(days=7)).isoformat()
        elif cls == AvailabilityClassification.HISTORICAL_FILING_GAP:
            source_of_truth = "NONE"
            availability_status = "FILING_GAP"
            production_eligibility = "INELIGIBLE"
            block_reason = "HISTORICAL_FILING_GAP_IN_SERIES"
            quarantine_action = "7_DAY_QUARANTINE"
            quarantine_until = (datetime.now(IST) + timedelta(days=7)).isoformat()
        elif cls == AvailabilityClassification.PROVIDER_FAILURE:
            source_of_truth = "NONE"
            availability_status = "PROVIDER_ERROR"
            production_eligibility = "INELIGIBLE"
            block_reason = f"PROVIDER_FAILURE: {bse_st or nse_st or upstox}"
            quarantine_action = "OPERATIONAL_RETRY"
        elif cls == AvailabilityClassification.SYMBOL_MAPPING_FAILURE:
            source_of_truth = "NONE"
            availability_status = "SYMBOL_MAPPING_FAILURE"
            production_eligibility = "INELIGIBLE"
            block_reason = "ISIN_OR_SYMBOL_RESOLUTION_FAILED"
            quarantine_action = "OPERATIONAL_RETRY"
        else:
            source_of_truth = "NONE"
            availability_status = "UNAVAILABLE"
            production_eligibility = "INELIGIBLE"
            block_reason = str(action)
            quarantine_action = "NONE"

        selected_periods = list(trace.get("selected_periods", []) or trace.get("annual_periods", []) or [])

        record = AvailabilityAuditRecord(
            symbol=symbol.upper(),
            isin=isin or (trace.get("isin") or "UNKNOWN"),
            scanner=self.scanner_name,
            field=fld,
            required_for_gate=gate_name,
            upstox_status=upstox,
            nse_status=nse,
            exchange_filing_status=exchange_filing_status,
            pit_status="AVAILABLE" if canonical_hit else "MISSING",
            local_cache_status=local_raw,
            fyers_status=fyers_status_str,
            screener_status=(screener.status.value if screener else ReferenceStatus.NOT_CONFIGURED.value),
            classification=cls.value,
            bse_status=bse,
            production_value_written=False,
            buy_allowed=False,
            admin_alert_generated=bool(admin_msg),
            checked_at=datetime.now(IST).isoformat(),
            upstox_key_ratios=kr,
            severity=_SEVERITY.get(cls, "INFO"),
            admin_action=action,
            admin_message=admin_msg,
            source_of_truth=source_of_truth,
            availability_status=availability_status,
            production_eligibility=production_eligibility,
            block_reason=block_reason,
            quarantine_action=quarantine_action,
            quarantine_until=quarantine_until,
            selected_periods=selected_periods,
        )
        self.validate_telemetry_consistency(record)
        return record

    @staticmethod
    def validate_telemetry_consistency(record: AvailabilityAuditRecord) -> None:
        """
        Reconciliation assertion that rejects contradictory telemetry (§ Pending Item 5):
        1. Canonical HIT cannot have source_of_truth='NONE' or availability_status='PARSER_FAILURE'.
        2. PARSER_FAILURE cannot be production ELIGIBLE or assigned 7_DAY_QUARANTINE.
        3. REFERENCE_ONLY cannot be production ELIGIBLE or written to production, and must have quarantine='NONE'.
        """
        if record.source_of_truth == "CANONICAL_LOCAL" or record.pit_status == "AVAILABLE":
            if record.source_of_truth == "NONE":
                raise AssertionError(
                    f"Contradictory telemetry for {record.symbol}.{record.field}: "
                    f"canonical HIT reported, but source_of_truth is 'NONE'"
                )
            if record.availability_status == "PARSER_FAILURE" or record.classification == AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE.value:
                raise AssertionError(
                    f"Contradictory telemetry for {record.symbol}.{record.field}: "
                    f"canonical HIT reported, but availability_status is PARSER_FAILURE"
                )
        if record.availability_status == "PARSER_FAILURE" or record.classification == AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE.value:
            if record.production_eligibility == "ELIGIBLE":
                raise AssertionError(
                    f"Contradictory telemetry for {record.symbol}.{record.field}: "
                    f"PARSER_FAILURE cannot be production ELIGIBLE"
                )
            if "7-day" in str(record.admin_action).lower() or record.quarantine_action == "7_DAY_QUARANTINE":
                raise AssertionError(
                    f"Contradictory telemetry for {record.symbol}.{record.field}: "
                    f"PARSER_FAILURE must NOT receive 7-day quarantine"
                )
        if record.availability_status == "REFERENCE_ONLY" or record.classification == AvailabilityClassification.REFERENCE_ONLY_AVAILABLE.value:
            if record.production_eligibility == "ELIGIBLE" or record.production_value_written or record.buy_allowed:
                raise AssertionError(
                    f"Contradictory telemetry for {record.symbol}.{record.field}: "
                    f"REFERENCE_ONLY cannot be production ELIGIBLE or written to production"
                )
            if record.quarantine_action != "NONE":
                raise AssertionError(
                    f"Contradictory telemetry for {record.symbol}.{record.field}: "
                    f"REFERENCE_ONLY must have quarantine=NONE"
                )


    def audit(self, unresolved: Dict[str, List[str]], traces: Dict[str, Dict[str, Any]],
              run_id: Optional[str] = None) -> List[AvailabilityAuditRecord]:
        records: List[AvailabilityAuditRecord] = []
        for sym, fields in sorted(unresolved.items()):
            for f in fields:
                rec = self.classify_field(sym, f, traces.get(sym, {}))
                assert rec.production_value_written is False and rec.buy_allowed is False
                records.append(rec)

        for rec in records:
            self._log(rec)
        self._write_reports(records)
        changed = self._persist(records, run_id) if self.persist_to_db else set()
        self._notify_admin(records, changed)
        self._log_summary(records)
        return records

    def _log(self, rec: AvailabilityAuditRecord) -> None:
        msg = (
            f"[DATA_AVAILABILITY_AUDIT] scanner={rec.scanner} symbol={rec.symbol} field={rec.field} | "
            f"UPSTOX={rec.upstox_status} NSE={rec.nse_status} KEY_RATIOS={rec.upstox_key_ratios} "
            f"FYERS={rec.fyers_status} SCREENER={rec.screener_status} | "
            f"classification={rec.classification} | action={rec.admin_action}"
        )
        if rec.classification == AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE.value:
            logger.error(f"🚨 [DATA_PROVIDER_DISCREPANCY] {msg}")
        elif rec.classification == AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE.value:
            logger.warning(f"🚨 [SCREENER_ONLY_DATA_FOUND] {msg}")
        elif rec.severity in ("HIGH", "WARNING"):
            logger.warning(f"⚠️ {msg}")
        else:
            logger.info(msg)

    def _write_reports(self, records: List[AvailabilityAuditRecord]) -> None:
        try:
            os.makedirs(REPORT_DIR, exist_ok=True)
            rows = [asdict(r) for r in records]
            json_p = os.path.join(REPORT_DIR, "data_availability_audit_latest.json")
            csv_p = os.path.join(REPORT_DIR, "data_availability_audit_latest.csv")
            with open(json_p + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"generated_at": datetime.now(IST).isoformat(), "scanner": self.scanner_name,
                           "records": rows}, f, indent=2)
            os.replace(json_p + ".tmp", json_p)
            if rows:
                with open(csv_p + ".tmp", "w", encoding="utf-8", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                    w.writeheader()
                    w.writerows(rows)
                os.replace(csv_p + ".tmp", csv_p)
        except Exception as e:
            logger.warning(f"[DATA_AVAILABILITY_AUDIT] report write failed: {e}")

    def _persist(self, records: List[AvailabilityAuditRecord], run_id: Optional[str]) -> set:
        changed: set = set()
        if get_connection is None or not records:
            return changed
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS data_availability_audit (
                            audit_date DATE NOT NULL,
                            symbol TEXT NOT NULL,
                            isin TEXT,
                            scanner TEXT NOT NULL,
                            field TEXT NOT NULL,
                            required_for_gate TEXT,
                            upstox_status TEXT,
                            nse_status TEXT,
                            bse_status TEXT,
                            exchange_filing_status TEXT,
                            pit_status TEXT,
                            local_cache_status TEXT,
                            fyers_status TEXT,
                            screener_status TEXT,
                            classification TEXT NOT NULL,
                            severity TEXT NOT NULL,
                            upstox_key_ratios TEXT,
                            admin_action TEXT,
                            production_value_written BOOLEAN NOT NULL DEFAULT FALSE
                                CHECK (production_value_written = FALSE),
                            buy_allowed BOOLEAN NOT NULL DEFAULT FALSE
                                CHECK (buy_allowed = FALSE),
                            admin_alert_generated BOOLEAN NOT NULL DEFAULT FALSE,
                            run_id TEXT,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            updated_at TIMESTAMPTZ DEFAULT NOW(),
                            PRIMARY KEY (audit_date, symbol, field)
                        );
                        ALTER TABLE data_availability_audit ADD COLUMN IF NOT EXISTS bse_status TEXT;
                        CREATE INDEX IF NOT EXISTS idx_daa_class_date
                            ON data_availability_audit (classification, audit_date DESC);
                        CREATE INDEX IF NOT EXISTS idx_daa_severity_date
                            ON data_availability_audit (severity, audit_date DESC);
                    """)
                    today = datetime.now(IST).date()
                    cur.execute(
                        "SELECT symbol, field, classification FROM data_availability_audit WHERE audit_date = %s",
                        (today,),
                    )
                    prior = {(r[0], r[1]): r[2] for r in cur.fetchall()}
                    for rec in records:
                        if prior.get((rec.symbol, rec.field)) != rec.classification:
                            changed.add((rec.symbol, rec.field))
                        cur.execute("""
                            INSERT INTO data_availability_audit (
                                audit_date, symbol, isin, scanner, field, required_for_gate,
                                upstox_status, nse_status, bse_status, exchange_filing_status, pit_status,
                                local_cache_status, fyers_status, screener_status, classification,
                                severity, upstox_key_ratios, admin_action, production_value_written,
                                buy_allowed, admin_alert_generated, run_id
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (audit_date, symbol, field) DO UPDATE SET
                                classification = EXCLUDED.classification,
                                severity = EXCLUDED.severity,
                                upstox_status = EXCLUDED.upstox_status,
                                nse_status = EXCLUDED.nse_status,
                                bse_status = EXCLUDED.bse_status,
                                exchange_filing_status = EXCLUDED.exchange_filing_status,
                                pit_status = EXCLUDED.pit_status,
                                local_cache_status = EXCLUDED.local_cache_status,
                                fyers_status = EXCLUDED.fyers_status,
                                screener_status = EXCLUDED.screener_status,
                                upstox_key_ratios = EXCLUDED.upstox_key_ratios,
                                admin_action = EXCLUDED.admin_action,
                                admin_alert_generated = EXCLUDED.admin_alert_generated,
                                run_id = EXCLUDED.run_id,
                                updated_at = NOW()
                        """, (today, rec.symbol, rec.isin, rec.scanner, rec.field, rec.required_for_gate,
                              rec.upstox_status, rec.nse_status, rec.bse_status, rec.exchange_filing_status, rec.pit_status,
                              rec.local_cache_status, rec.fyers_status, rec.screener_status, rec.classification,
                              rec.severity, rec.upstox_key_ratios, rec.admin_action, rec.production_value_written,
                              rec.buy_allowed, rec.admin_alert_generated, run_id))
                conn.commit()
        except Exception as e:
            logger.warning(f"[DATA_AVAILABILITY_AUDIT] DB persist failed (diagnostic only): {e}")
        return changed

    def _notify_admin(self, records: List[AvailabilityAuditRecord], changed: set) -> None:
        for rec in records:
            title = None
            body = None

            if rec.classification in (
                AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE.value,
                AvailabilityClassification.REFERENCE_ONLY_AVAILABLE.value,
            ):
                rec.admin_alert_generated = True
                title = f"🚨 DATA SOURCE NOTICE: {rec.symbol} — {rec.field}"
                body = (
                    f"🚨 DATA SOURCE NOTICE: {rec.symbol} — {rec.field} found on Screener.in but unavailable from "
                    f"primary authoritative providers (Upstox/NSE/Exchange). This data will NOT be used for trading decisions. "
                    f"Potential upstream ingestion gap flagged for review.\n\n"
                    f"Missing Field:\n{rec.field}\n\n"
                    f"Verified Production Sources:\n"
                    f"❌ Upstox Fundamentals: {rec.upstox_status}\n"
                    f"❌ Upstox Key Ratios: {rec.upstox_key_ratios}\n"
                    f"❌ NSE/XBRL: {rec.nse_status}\n"
                    f"❌ BSE Corporate: {rec.bse_status}\n"
                    f"❌ Exchange/PIT Filings: {rec.exchange_filing_status}\n"
                    f"❌ Local Verified Filing Cache: {rec.local_cache_status}\n"
                    f"⚠️ FYERS: {rec.fyers_status}\n"
                    f"✅ Screener: Data Found (Forensic Reference Only)\n\n"
                    f"Classification:\n{rec.classification}\n\n"
                    f"Production Value:\nNOT WRITTEN (NULL)\n\n"
                    f"BUY Decision:\nBLOCKED\n\n"
                    f"Required Admin Action:\n"
                    f"Investigate why the value available in Screener cannot be recovered "
                    f"from an authorized verified production source.\n\n"
                    f"IMPORTANT:\nScreener data must NOT be promoted to production truth."
                )
                rec.admin_message = body

            elif rec.classification == AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE.value:
                rec.admin_alert_generated = True
                title = f"🚨 DATA PROVIDER DISCREPANCY — {rec.symbol} — {rec.field}"
                body = (
                    f"🚨 DATA PROVIDER DISCREPANCY — {rec.symbol} — {rec.field}\n\n"
                    f"Required Field: {rec.field}\n\n"
                    f"Primary/Verified Sources: DATA NOT RECOVERED\n"
                    f"FYERS: ✅ DATA FOUND\n"
                    f"Screener: ✅ DATA FOUND\n\n"
                    f"Likely Issue:\n"
                    f"provider ingestion / parser / field mapping / normalization / PIT integration\n\n"
                    f"Production:\nBLOCKED"
                )
                rec.admin_message = body

            elif rec.classification == AvailabilityClassification.PARSER_OR_FIELD_MAPPING_FAILURE.value:
                rec.admin_alert_generated = True
                title = f"🚨 PARSER/MAPPING FAILURE: {rec.symbol} — {rec.field}"
                body = (
                    f"🚨 PARSER OR FIELD MAPPING FAILURE: {rec.symbol} — {rec.field}\n\n"
                    f"HTTP 200 raw filings were received and parsed, but target metric {rec.field} "
                    f"could not be extracted (0 usable fields extracted).\n\n"
                    f"Likely Issue: official taxonomy tag change, standalone vs consolidated divergence, or regex parsing failure.\n\n"
                    f"Required Action: Inspect raw JSON/XML filings for {rec.symbol} and update tag mapping.\n\n"
                    f"Production: BLOCKED"
                )
                rec.admin_message = body

            if title and body and self.persist_to_db and insert_notification:
                try:
                    if get_connection:
                        with get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(
                                    "SELECT 1 FROM global_notifications WHERE symbol = %s AND title = %s AND created_at > NOW() - INTERVAL '24 hours' LIMIT 1",
                                    (rec.symbol, title)
                                )
                                if cur.fetchone():
                                    continue
                    insert_notification("admin", title, body, symbol=rec.symbol)
                    logger.info(f"🔔 [ADMIN_NOTIFICATION_DELIVERED] {title} dispatched to global_notifications")
                except Exception as e:
                    logger.debug(f"[DATA_AVAILABILITY_AUDIT] admin notification failed for {rec.symbol}: {e}")

    def _log_summary(self, records: List[AvailabilityAuditRecord]) -> None:
        counts: Dict[str, int] = {}
        for r in records:
            counts[r.classification] = counts.get(r.classification, 0) + 1
        syms = len({r.symbol for r in records})
        logger.info(
            f"📋 [DATA_AVAILABILITY_AUDIT] {self.scanner_name}: {len(records)} unresolved field(s) "
            f"across {syms} symbol(s) | " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
        )
