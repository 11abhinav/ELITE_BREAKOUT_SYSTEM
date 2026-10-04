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
  Tier 2 — Independent Diagnostic / Approved Broker Source:
    - FYERS (where an authorized machine-readable or approved access path exists)
  Tier 3 — Forensic / Reference-Only:
    - Screener.in (FORENSIC_REFERENCE_ONLY, strictly offline discovery, never write to PIT, never BUY)

Core Classifications (Mandatory Governance Rule):
  1. PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE (DATA PROVIDER DISCREPANCY)
     Upstox = Missing, NSE = Missing, FYERS = Available, Screener = Available
  2. SCREENER_ONLY_DATA_SOURCE
     Upstox = Missing, NSE = Missing, PIT = Missing, FYERS = Missing/Unavailable, Screener = Available
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
from datetime import datetime, date
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
    FYERS_ONLY_DATA_SOURCE = "FYERS_ONLY_DATA_SOURCE"
    DATA_UNAVAILABLE_VERIFIED = "DATA_UNAVAILABLE_VERIFIED"
    INSUFFICIENT_HISTORICAL_DEPTH = "INSUFFICIENT_HISTORICAL_DEPTH"
    STALE_PIT = "STALE_PIT"
    UNPROCESSED_FILING = "UNPROCESSED_FILING"
    PARSER_MAPPING_FAILURE = "PARSER_MAPPING_FAILURE"
    CALCULATION_FAILURE = "CALCULATION_FAILURE"
    STRUCTURAL_INELIGIBLE = "STRUCTURAL_INELIGIBLE"
    TIER1_RECOVERY_NOT_EXHAUSTED = "TIER1_RECOVERY_NOT_EXHAUSTED"


_SEVERITY: Dict[AvailabilityClassification, str] = {
    AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE: "CRITICAL",
    AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE: "HIGH",
    AvailabilityClassification.FYERS_ONLY_DATA_SOURCE: "HIGH",
    AvailabilityClassification.PARSER_MAPPING_FAILURE: "HIGH",
    AvailabilityClassification.CALCULATION_FAILURE: "WARNING",
    AvailabilityClassification.UNPROCESSED_FILING: "WARNING",
    AvailabilityClassification.STALE_PIT: "WARNING",
    AvailabilityClassification.TIER1_RECOVERY_NOT_EXHAUSTED: "WARNING",
    AvailabilityClassification.INSUFFICIENT_HISTORICAL_DEPTH: "INFO",
    AvailabilityClassification.STRUCTURAL_INELIGIBLE: "INFO",
    AvailabilityClassification.DATA_UNAVAILABLE_VERIFIED: "INFO",
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
    production_value_written: bool = field(default=False)
    buy_allowed: bool = field(default=False)
    admin_alert_generated: bool = field(default=False)
    checked_at: str = field(default="")
    upstox_key_ratios: str = field(default="N/A")
    severity: str = field(default="INFO")
    admin_action: str = field(default="")


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
    if prefix == "upstox" and not trace.get("isin_resolved", False):
        return "ISIN_UNRESOLVED"
    n = int(trace.get(f"{prefix}_records", 0) or 0)
    if n == 0:
        return "MISSING"
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
                       today: Optional[date] = None) -> AvailabilityAuditRecord:
        fld = normalize_field(field_name)
        trace = trace or {}
        checks = {r.name: r.check(symbol, fld, today=today) for r in self.references}
        fyers = checks.get("FYERS")
        screener = checks.get("SCREENER")

        upstox = _statement_status(trace, "upstox")
        nse = _statement_status(trace, "nse")
        local_raw = _statement_status(trace, "local")
        isin = trace.get("isin", "")

        if fld == "current_ev_ebitda":
            kr = trace.get("key_ratios_status") if trace.get("key_ratios_attempted") else "NOT_ATTEMPTED"
        else:
            kr = "N/A"

        fyers_avail = fyers and fyers.status == ReferenceStatus.AVAILABLE
        screener_avail = screener and screener.status == ReferenceStatus.AVAILABLE
        fyers_missing = fyers and fyers.status == ReferenceStatus.MISSING
        screener_missing = screener and screener.status == ReferenceStatus.MISSING

        max_annual = max(int(trace.get("upstox_annual", 0) or 0), int(trace.get("nse_annual", 0) or 0),
                         int(trace.get("local_annual", 0) or 0))
        min_needed = _MIN_ANNUAL_PERIODS.get(fld)
        primary_has_enough = min_needed is not None and max_annual >= min_needed

        # ── 3 Core Mandatory Governance Conditions ─────────────────────────
        if fyers_avail and screener_avail:
            cls = AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE
            action = "INVESTIGATE_UPSTOX_NSE_PARSER_OR_MAPPING"
        elif screener_avail and not fyers_avail:
            cls = AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE
            action = "INVESTIGATE_WHY_VALUE_IN_SCREENER_CANNOT_BE_RECOVERED_FROM_VERIFIED_SOURCE"
        elif fyers_avail and not screener_avail:
            cls = AvailabilityClassification.FYERS_ONLY_DATA_SOURCE
            action = "INVESTIGATE_FYERS_API_INGESTION_FOR_VERIFIED_PIPELINE"
        elif primary_has_enough:
            cls = AvailabilityClassification.PARSER_MAPPING_FAILURE
            action = "INVESTIGATE_PARSER_CALCULATION_OR_NEGATIVE_BASE"
        elif fyers_missing and screener_missing:
            cls = AvailabilityClassification.DATA_UNAVAILABLE_VERIFIED
            action = "NONE (hard data block confirmed across all providers)"
        elif max_annual > 0 and min_needed is not None and max_annual < min_needed:
            cls = AvailabilityClassification.INSUFFICIENT_HISTORICAL_DEPTH
            action = f"CONFIRMED_SHORT_HISTORY (has {max_annual} annual periods, requires {min_needed})"
        elif not trace or (fld == "current_ev_ebitda" and kr == "NOT_ATTEMPTED"):
            cls = AvailabilityClassification.TIER1_RECOVERY_NOT_EXHAUSTED
            action = "RUN_TIER1_PRIMARY_RECOVERY"
        else:
            cls = AvailabilityClassification.DATA_UNAVAILABLE_VERIFIED
            action = "ATTEST_INDEPENDENT_AVAILABILITY"

        gate_name = "VALUATION" if fld == "current_ev_ebitda" else "QUALITY"

        return AvailabilityAuditRecord(
            symbol=symbol.upper(),
            isin=isin or (trace.get("isin") or "UNKNOWN"),
            scanner=self.scanner_name,
            field=fld,
            required_for_gate=gate_name,
            upstox_status=upstox,
            nse_status=nse,
            exchange_filing_status=trace.get("exchange_filing_status", "MISSING"),
            pit_status="MISSING",
            local_cache_status=local_raw,
            fyers_status=(fyers.status.value if fyers else ReferenceStatus.NOT_CONFIGURED.value),
            screener_status=(screener.status.value if screener else ReferenceStatus.NOT_CONFIGURED.value),
            classification=cls.value,
            production_value_written=False,
            buy_allowed=False,
            admin_alert_generated=False,
            checked_at=datetime.now(IST).isoformat(),
            upstox_key_ratios=kr,
            severity=_SEVERITY.get(cls, "INFO"),
            admin_action=action,
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
                                upstox_status, nse_status, exchange_filing_status, pit_status,
                                local_cache_status, fyers_status, screener_status, classification,
                                severity, upstox_key_ratios, admin_action, production_value_written,
                                buy_allowed, admin_alert_generated, run_id
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (audit_date, symbol, field) DO UPDATE SET
                                classification = EXCLUDED.classification,
                                severity = EXCLUDED.severity,
                                upstox_status = EXCLUDED.upstox_status,
                                nse_status = EXCLUDED.nse_status,
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
                              rec.upstox_status, rec.nse_status, rec.exchange_filing_status, rec.pit_status,
                              rec.local_cache_status, rec.fyers_status, rec.screener_status, rec.classification,
                              rec.severity, rec.upstox_key_ratios, rec.admin_action, rec.production_value_written,
                              rec.buy_allowed, rec.admin_alert_generated, run_id))
                conn.commit()
        except Exception as e:
            logger.warning(f"[DATA_AVAILABILITY_AUDIT] DB persist failed (diagnostic only): {e}")
        return changed

    def _notify_admin(self, records: List[AvailabilityAuditRecord], changed: set) -> None:
        if insert_notification is None or not self.persist_to_db:
            return
        for rec in records:
            if (rec.symbol, rec.field) not in changed:
                continue

            if rec.classification == AvailabilityClassification.SCREENER_ONLY_DATA_SOURCE.value:
                rec.admin_alert_generated = True
                title = f"🚨 SCREENER-ONLY DATA FOUND — {rec.symbol}"
                body = (
                    f"🚨 SCREENER-ONLY DATA FOUND — {rec.symbol}\n\n"
                    f"Missing Field:\n{rec.field}\n\n"
                    f"Verified Production Sources:\n"
                    f"❌ Upstox Fundamentals: {rec.upstox_status}\n"
                    f"❌ Upstox Key Ratios: {rec.upstox_key_ratios}\n"
                    f"❌ NSE/XBRL: {rec.nse_status}\n"
                    f"❌ Exchange/PIT Filings: {rec.exchange_filing_status}\n"
                    f"❌ Local Verified Filing Cache: {rec.local_cache_status}\n"
                    f"⚠️ FYERS: {rec.fyers_status}\n"
                    f"✅ Screener: Data Found\n\n"
                    f"Classification:\nSCREENER_ONLY_DATA_SOURCE\n\n"
                    f"Production Value:\nNOT WRITTEN\n\n"
                    f"BUY Decision:\nBLOCKED\n\n"
                    f"Required Admin Action:\n"
                    f"Investigate why the value available in Screener cannot be recovered "
                    f"from an authorized verified production source.\n\n"
                    f"IMPORTANT:\nScreener data must NOT be promoted to production truth."
                )
                try:
                    insert_notification("admin", title, body, symbol=rec.symbol)
                except Exception as e:
                    logger.debug(f"[DATA_AVAILABILITY_AUDIT] admin notification failed for {rec.symbol}: {e}")

            elif rec.classification == AvailabilityClassification.PRIMARY_RECOVERY_FAILURE_DATA_EXISTS_ELSEWHERE.value:
                rec.admin_alert_generated = True
                title = f"🚨 DATA PROVIDER DISCREPANCY — {rec.symbol}"
                body = (
                    f"🚨 DATA PROVIDER DISCREPANCY — {rec.symbol}\n\n"
                    f"Required Field: {rec.field}\n\n"
                    f"Primary/Verified Sources: DATA NOT RECOVERED\n"
                    f"FYERS: ✅ DATA FOUND\n"
                    f"Screener: ✅ DATA FOUND\n\n"
                    f"Likely Issue:\n"
                    f"provider ingestion / parser / field mapping / normalization / PIT integration\n\n"
                    f"Production:\nBLOCKED"
                )
                try:
                    insert_notification("admin", title, body, symbol=rec.symbol)
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
