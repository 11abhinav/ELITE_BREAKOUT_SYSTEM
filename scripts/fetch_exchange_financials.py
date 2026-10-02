"""
fetch_exchange_financials.py
============================
Phase 2: NSE/BSE Exchange-Filed Financial Fact Ingestion Pipeline.

PURPOSE:
  Build an immutable raw filing store from NSE/BSE exchange-filed XBRL/PDF data.
  This is the authoritative financial data source that replaces Screener-sourced PIT.

GOVERNANCE:
  - All filings are stored immutably (never overwrite, only add versions).
  - Source hash (SHA256) computed and stored for every raw payload.
  - PIT eligibility = broadcast_timestamp (when filing appeared on exchange).
  - NSE and BSE are reconciled; conflicts produce DATA_CONFLICT status.
  - No synthetic values, no fallback defaults.

STORAGE STRUCTURE:
  data/exchange_financials/
    {SYMBOL}/
      annual/
        {period_end}_{filing_id}_{version}.json
      quarterly/
        {period_end}_{filing_id}_{version}.json
      raw/
        {filing_id}_{version}.payload  (original response)
        {filing_id}_{version}.sha256
      metadata/
        symbol_metadata.json
        filing_index.json
        pit_snapshot.parquet

USAGE:
  python3 scripts/fetch_exchange_financials.py --symbol TATA --years 6
  python3 scripts/fetch_exchange_financials.py --universe data/certified_clean_universe_886.json --years 6

Phase 2 Status: STRUCTURAL SCAFFOLD — NSE/BSE API integration points defined.
The actual NSE/BSE API calls will be populated as endpoint documentation is confirmed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
except ImportError:
    IST = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXCHANGE_DIR = DATA_DIR / "exchange_financials"

NSE_BASE_URL = "https://www.nseindia.com"
BSE_BASE_URL = "https://api.bseindia.com"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RawFiling:
    """
    Immutable representation of a single exchange filing.
    Implements C13 (Immutable Raw Filing Store) and C14 (PIT Eligibility).
    """
    symbol: str
    isin: str
    exchange: str                     # "NSE" or "BSE"
    filing_id: str                    # Exchange-assigned filing reference
    filing_version: str               # "v1", "v2" (amendments)

    period_start_date: Optional[str]
    period_end_date: str
    statement_type: str               # "ANNUAL" or "QUARTERLY"
    basis: str                        # "CONSOLIDATED" or "STANDALONE"

    audited: bool
    amended: bool

    broadcast_timestamp: str          # When filing appeared on exchange
    retrieved_timestamp: str          # When we downloaded it
    pit_eligible_from: str            # = broadcast_timestamp (exchange PIT)

    raw_payload_path: str             # Relative path to raw payload file
    source_hash: str                  # SHA256 of raw payload

    # Normalized fact values (after unit conversion)
    facts: Dict[str, Any] = field(default_factory=dict)

    # Provenance metadata
    xbrl_tag_map: Dict[str, str] = field(default_factory=dict)   # fact → XBRL concept
    extraction_method: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NormalizedFact:
    """
    Single normalized financial fact from exchange filing.
    Implements the raw fact schema from Section 26 of the spec.
    """
    symbol: str
    isin: str

    period_start_date: Optional[str]
    period_end_date: str

    statement_type: str
    basis: str

    audited: bool
    amended: bool
    filing_version: str

    source_exchange: str
    source_filing_id: str

    broadcast_timestamp: str
    retrieved_timestamp: str
    pit_eligible_from: str

    fact_name: str
    raw_value: Optional[Any]
    raw_unit: str
    normalized_value: Optional[float]
    normalized_unit: str              # Always "INR_CRORE" for monetary, "MILLIONS" for shares

    xbrl_concept: str
    extraction_method: str
    source_hash: str


@dataclass
class PITSnapshot:
    """
    Point-in-time snapshot of a company's financial facts,
    computed from all eligible filings as of a given timestamp.
    """
    symbol: str
    isin: str
    as_of_timestamp: str
    basis: str
    statement_type: str
    period_end_date: str
    filing_id: str
    filing_version: str
    pit_eligible_from: str
    source_exchange: str

    # Raw financial facts (Section 27 required fields)
    revenue: Optional[float] = None
    operating_profit: Optional[float] = None
    ebit: Optional[float] = None
    depreciation: Optional[float] = None
    amortization: Optional[float] = None
    pat: Optional[float] = None
    eps_basic: Optional[float] = None
    eps_diluted: Optional[float] = None
    shares_outstanding: Optional[float] = None
    weighted_avg_shares: Optional[float] = None
    total_debt: Optional[float] = None
    cash_and_equivalents: Optional[float] = None
    total_equity: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    exceptional_items: Optional[float] = None
    minority_interest: Optional[float] = None

    # Derived fields (computed from above, not from pre-computed source)
    ebitda: Optional[float] = None
    ebitda_formula: str = ""
    debt_equity: Optional[float] = None

    # Status flags
    freshness_status: str = ""
    provenance_status: str = "UNCERTIFIED"


@dataclass
class ReconciliationResult:
    """
    Result of NSE/BSE cross-reconciliation for a fact.
    Implements C12.
    """
    symbol: str
    fact_name: str
    period_end_date: str
    basis: str

    nse_value: Optional[float]
    bse_value: Optional[float]
    difference: Optional[float]
    difference_pct: Optional[float]

    resolution_rule: str
    reconciliation_status: str   # "RECONCILED" / "DATA_CONFLICT" / "SINGLE_SOURCE"
    resolved_value: Optional[float]


# ---------------------------------------------------------------------------
# Unit normalization
# ---------------------------------------------------------------------------

# Supported source units and their normalization factors to INR Crores
_UNIT_TO_CRORE_FACTOR: Dict[str, float] = {
    "INR_LAKH":        0.01,      # 1 Lakh = 0.01 Crore
    "INR_CRORE":       1.0,
    "INR_MILLION":     0.1,       # 1 Million INR = 0.1 Crore
    "INR_THOUSAND":    0.0001,    # 1 Thousand INR = 0.0001 Crore
    "INR":             1e-7,      # 1 INR = 1e-7 Crore
    "USD_MILLION":     None,      # Requires FX — not auto-converted
}


def normalize_monetary_value(
    raw_value: Optional[Any],
    raw_unit: str,
    symbol: str,
    fact_name: str,
) -> Tuple[Optional[float], str, str]:
    """
    Normalizes a monetary value to INR Crores.

    Returns:
        (normalized_value, normalized_unit, normalization_status)
    """
    if raw_value is None:
        return None, "INR_CRORE", "MISSING"

    try:
        v = float(raw_value)
    except (TypeError, ValueError):
        logger.error(f"[UNIT_NORM] {symbol}/{fact_name}: Cannot parse raw_value={raw_value!r}")
        return None, "INR_CRORE", "PARSE_ERROR"

    unit_upper = raw_unit.upper().strip()
    if unit_upper not in _UNIT_TO_CRORE_FACTOR:
        logger.warning(
            f"[UNIT_NORM] {symbol}/{fact_name}: Unknown unit '{raw_unit}'. "
            f"Value={v} not normalized. Marking DATA_INVALID."
        )
        return None, "UNKNOWN", "UNIT_UNKNOWN"

    factor = _UNIT_TO_CRORE_FACTOR[unit_upper]
    if factor is None:
        logger.error(
            f"[UNIT_NORM] {symbol}/{fact_name}: Unit '{raw_unit}' requires FX conversion. "
            f"Not auto-normalized."
        )
        return None, "INR_CRORE", "FX_CONVERSION_REQUIRED"

    normalized = round(v * factor, 6)
    return normalized, "INR_CRORE", "OK"


# ---------------------------------------------------------------------------
# Symbol → ISIN resolution
# ---------------------------------------------------------------------------

def resolve_isin(symbol: str, data_dir: Path = DATA_DIR) -> Optional[str]:
    """
    Resolves NSE symbol to ISIN from the certified universe file.

    Args:
        symbol: NSE ticker.
        data_dir: Path to data directory.

    Returns:
        ISIN string or None.
    """
    universe_path = data_dir / "certified_clean_universe_886.json"
    if universe_path.exists():
        try:
            with open(universe_path) as f:
                universe = json.load(f)
            for entry in universe:
                if isinstance(entry, dict):
                    if entry.get("symbol", "").upper() == symbol.upper():
                        return entry.get("isin")
        except Exception as e:
            logger.error(f"[ISIN_RESOLVE] {symbol}: Failed to load universe: {e}")

    # Fallback: try bse_symbol_mappings
    bse_map_path = data_dir / "bse_symbol_mappings.json"
    if bse_map_path.exists():
        try:
            with open(bse_map_path) as f:
                bse_map = json.load(f)
            entry = bse_map.get(symbol.upper(), bse_map.get(symbol))
            if entry:
                return entry.get("isin") or entry.get("ISIN")
        except Exception as e:
            logger.debug(f"[ISIN_RESOLVE] {symbol}: BSE map lookup failed: {e}")

    return None


# ---------------------------------------------------------------------------
# Filing store I/O
# ---------------------------------------------------------------------------

def get_symbol_dir(symbol: str, exchange_dir: Path = EXCHANGE_DIR) -> Path:
    """Returns the directory for a symbol's exchange financial data."""
    return exchange_dir / symbol.upper()


def compute_sha256(payload: str) -> str:
    """Computes SHA256 of a string payload."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def store_raw_filing(
    symbol: str,
    filing_id: str,
    filing_version: str,
    statement_type: str,
    period_end: str,
    payload: Dict[str, Any],
    exchange_dir: Path = EXCHANGE_DIR,
) -> str:
    """
    Stores an immutable raw filing payload and returns its hash.

    Never overwrites an existing filing — appends a new version.

    Args:
        symbol: NSE ticker.
        filing_id: Exchange filing reference.
        filing_version: Version string (v1, v2 for amendments).
        statement_type: "ANNUAL" or "QUARTERLY".
        period_end: Filing period end date (YYYY-MM-DD).
        payload: Raw API response dict.
        exchange_dir: Root directory for exchange data.

    Returns:
        SHA256 hash of stored payload.
    """
    sym_dir = get_symbol_dir(symbol, exchange_dir)
    raw_dir = sym_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    payload_str = json.dumps(payload, sort_keys=True, default=str)
    sha256 = compute_sha256(payload_str)

    safe_period = period_end.replace("-", "")
    safe_id = filing_id.replace("/", "_").replace("\\", "_")
    base_name = f"{safe_period}_{safe_id}_{filing_version}"

    payload_path = raw_dir / f"{base_name}.payload.json"
    hash_path = raw_dir / f"{base_name}.sha256"

    if payload_path.exists():
        # Verify hash matches — do not overwrite if identical
        existing_hash = hash_path.read_text().strip() if hash_path.exists() else ""
        if existing_hash == sha256:
            logger.debug(f"[RAW_STORE] {symbol}/{filing_id}/{filing_version}: Already stored (hash match). Skipping.")
            return sha256
        else:
            logger.warning(
                f"[RAW_STORE] {symbol}/{filing_id}/{filing_version}: "
                f"Hash mismatch! existing={existing_hash[:16]} new={sha256[:16]}. "
                f"Storing as new version."
            )
            # Store with conflict suffix — never overwrite
            conflict_path = raw_dir / f"{base_name}_conflict_{sha256[:8]}.payload.json"
            conflict_path.write_text(payload_str)
            return sha256

    payload_path.write_text(payload_str)
    hash_path.write_text(sha256)

    logger.debug(
        f"[RAW_STORE] {symbol}: Stored {statement_type} filing "
        f"period={period_end} id={filing_id} v={filing_version} sha256={sha256[:16]}..."
    )
    return sha256


def store_normalized_filing(
    symbol: str,
    statement_type: str,
    period_end: str,
    filing_id: str,
    filing_version: str,
    normalized: Dict[str, Any],
    exchange_dir: Path = EXCHANGE_DIR,
) -> Path:
    """
    Stores a normalized filing JSON (after unit conversion and fact extraction).
    Returns the path to the stored file.
    """
    sym_dir = get_symbol_dir(symbol, exchange_dir)
    stmt_dir = sym_dir / statement_type.lower()
    stmt_dir.mkdir(parents=True, exist_ok=True)

    safe_period = period_end.replace("-", "")
    safe_id = filing_id.replace("/", "_").replace("\\", "_")
    fname = f"{safe_period}_{safe_id}_{filing_version}.json"
    fpath = stmt_dir / fname

    if fpath.exists():
        logger.debug(f"[NORM_STORE] {symbol}: {fname} already exists — skipping overwrite.")
        return fpath

    with open(fpath, "w") as f:
        json.dump(normalized, f, indent=2, default=str)

    return fpath


def load_filing_index(symbol: str, exchange_dir: Path = EXCHANGE_DIR) -> Dict[str, Any]:
    """Loads the filing index for a symbol."""
    sym_dir = get_symbol_dir(symbol, exchange_dir)
    meta_dir = sym_dir / "metadata"
    index_path = meta_dir / "filing_index.json"

    if index_path.exists():
        try:
            return json.loads(index_path.read_text())
        except Exception:
            pass
    return {}


def update_filing_index(
    symbol: str,
    filing_id: str,
    statement_type: str,
    period_end: str,
    filing_version: str,
    sha256: str,
    broadcast_timestamp: str,
    exchange: str,
    exchange_dir: Path = EXCHANGE_DIR,
) -> None:
    """Updates the filing index for a symbol with a new filing entry."""
    sym_dir = get_symbol_dir(symbol, exchange_dir)
    meta_dir = sym_dir / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)

    index_path = meta_dir / "filing_index.json"
    index = load_filing_index(symbol, exchange_dir)

    key = f"{exchange}_{filing_id}_{filing_version}"
    index[key] = {
        "filing_id": filing_id,
        "exchange": exchange,
        "statement_type": statement_type,
        "period_end_date": period_end,
        "filing_version": filing_version,
        "sha256": sha256,
        "broadcast_timestamp": broadcast_timestamp,
        "pit_eligible_from": broadcast_timestamp,
        "indexed_at": datetime.now(IST).isoformat() if IST else datetime.utcnow().isoformat(),
    }

    with open(index_path, "w") as f:
        json.dump(index, f, indent=2)


# ---------------------------------------------------------------------------
# NSE/BSE Reconciliation
# ---------------------------------------------------------------------------

MATERIAL_DIFF_THRESHOLD_PCT: float = 5.0  # 5% difference → DATA_CONFLICT


def reconcile_nse_bse(
    symbol: str,
    fact_name: str,
    period_end: str,
    basis: str,
    nse_value: Optional[float],
    bse_value: Optional[float],
) -> ReconciliationResult:
    """
    Reconciles NSE and BSE values for a financial fact.

    If only one source is available: SINGLE_SOURCE (accepted).
    If both available and diff < MATERIAL_DIFF_THRESHOLD_PCT: RECONCILED (NSE preferred).
    If diff >= threshold: DATA_CONFLICT (blocks BUY).

    Args:
        symbol: NSE ticker.
        fact_name: Financial fact (e.g. "revenue").
        period_end: Period end date.
        basis: CONSOLIDATED or STANDALONE.
        nse_value: NSE-filed value in INR Crores.
        bse_value: BSE-filed value in INR Crores.

    Returns:
        ReconciliationResult.
    """
    if nse_value is None and bse_value is None:
        return ReconciliationResult(
            symbol=symbol, fact_name=fact_name,
            period_end_date=period_end, basis=basis,
            nse_value=None, bse_value=None,
            difference=None, difference_pct=None,
            resolution_rule="BOTH_MISSING",
            reconciliation_status="DATA_CONFLICT",
            resolved_value=None,
        )

    if nse_value is None:
        return ReconciliationResult(
            symbol=symbol, fact_name=fact_name,
            period_end_date=period_end, basis=basis,
            nse_value=None, bse_value=bse_value,
            difference=None, difference_pct=None,
            resolution_rule="NSE_MISSING_BSE_ONLY",
            reconciliation_status="SINGLE_SOURCE",
            resolved_value=bse_value,
        )

    if bse_value is None:
        return ReconciliationResult(
            symbol=symbol, fact_name=fact_name,
            period_end_date=period_end, basis=basis,
            nse_value=nse_value, bse_value=None,
            difference=None, difference_pct=None,
            resolution_rule="BSE_MISSING_NSE_PREFERRED",
            reconciliation_status="SINGLE_SOURCE",
            resolved_value=nse_value,
        )

    diff = abs(nse_value - bse_value)
    ref = abs(nse_value) if abs(nse_value) > 1e-5 else abs(bse_value)
    diff_pct = (diff / ref * 100.0) if ref > 1e-5 else 0.0

    if diff_pct >= MATERIAL_DIFF_THRESHOLD_PCT:
        logger.error(
            f"[RECONCILE] {symbol}/{fact_name}/{period_end}: NSE={nse_value} vs BSE={bse_value} "
            f"diff={diff_pct:.1f}% ≥ threshold={MATERIAL_DIFF_THRESHOLD_PCT}%. DATA_CONFLICT."
        )
        return ReconciliationResult(
            symbol=symbol, fact_name=fact_name,
            period_end_date=period_end, basis=basis,
            nse_value=nse_value, bse_value=bse_value,
            difference=round(diff, 4),
            difference_pct=round(diff_pct, 2),
            resolution_rule="MATERIAL_DISCREPANCY",
            reconciliation_status="DATA_CONFLICT",
            resolved_value=None,
        )

    # NSE preferred when both available and within threshold
    return ReconciliationResult(
        symbol=symbol, fact_name=fact_name,
        period_end_date=period_end, basis=basis,
        nse_value=nse_value, bse_value=bse_value,
        difference=round(diff, 4),
        difference_pct=round(diff_pct, 2),
        resolution_rule=f"NSE_PREFERRED_DIFF={diff_pct:.1f}%",
        reconciliation_status="RECONCILED",
        resolved_value=nse_value,
    )


# ---------------------------------------------------------------------------
# Filing gap detection (reuses financial_data_integrity)
# ---------------------------------------------------------------------------

def detect_gaps_in_filing_store(
    symbol: str,
    exchange_dir: Path = EXCHANGE_DIR,
    statement_type: str = "ANNUAL",
) -> List[Tuple[str, str]]:
    """
    Detects gaps in the stored filing history for a symbol.

    Args:
        symbol: NSE ticker.
        exchange_dir: Root exchange data directory.
        statement_type: "ANNUAL" or "QUARTERLY".

    Returns:
        List of (period_A, period_B) gap tuples.
    """
    from app.financial_data_integrity import detect_annual_fiscal_gaps

    sym_dir = get_symbol_dir(symbol, exchange_dir)
    stmt_dir = sym_dir / statement_type.lower()

    if not stmt_dir.exists():
        return []

    rows = []
    for fpath in sorted(stmt_dir.glob("*.json")):
        try:
            with open(fpath) as f:
                data = json.load(f)
            period_end = data.get("period_end_date") or data.get("period_end")
            if period_end:
                rows.append({"period_end_date": period_end})
        except Exception:
            continue

    rows_sorted = sorted(rows, key=lambda r: r["period_end_date"])
    if statement_type == "ANNUAL":
        return detect_annual_fiscal_gaps(rows_sorted)
    return []


# ---------------------------------------------------------------------------
# PIT Snapshot Builder
# ---------------------------------------------------------------------------

def build_pit_snapshot(
    symbol: str,
    as_of_timestamp: str,
    exchange_dir: Path = EXCHANGE_DIR,
    basis: str = "CONSOLIDATED",
    statement_type: str = "ANNUAL",
) -> Optional[PITSnapshot]:
    """
    Builds a PIT snapshot for a symbol as of a given timestamp.

    Only includes filings with pit_eligible_from <= as_of_timestamp.

    Args:
        symbol: NSE ticker.
        as_of_timestamp: ISO timestamp — only filings broadcast by this time are used.
        exchange_dir: Root exchange data directory.
        basis: CONSOLIDATED or STANDALONE.
        statement_type: ANNUAL or QUARTERLY.

    Returns:
        PITSnapshot or None if no eligible filings.
    """
    filing_index = load_filing_index(symbol, exchange_dir)
    if not filing_index:
        logger.warning(f"[PIT_SNAPSHOT] {symbol}: No filing index found.")
        return None

    # Filter to PIT-eligible filings
    eligible = [
        entry for entry in filing_index.values()
        if (
            entry.get("statement_type", "").upper() == statement_type.upper()
            and entry.get("pit_eligible_from", "9999") <= as_of_timestamp
        )
    ]

    if not eligible:
        logger.warning(f"[PIT_SNAPSHOT] {symbol}: No PIT-eligible {statement_type} filings before {as_of_timestamp}.")
        return None

    # Select latest eligible filing
    latest = max(eligible, key=lambda e: (e.get("period_end_date", ""), e.get("filing_version", "v1")))

    # Load the normalized filing data
    sym_dir = get_symbol_dir(symbol, exchange_dir)
    stmt_dir = sym_dir / statement_type.lower()

    safe_period = (latest["period_end_date"] or "").replace("-", "")
    safe_id = (latest["filing_id"] or "").replace("/", "_").replace("\\", "_")
    fname = f"{safe_period}_{safe_id}_{latest['filing_version']}.json"
    fpath = stmt_dir / fname

    if not fpath.exists():
        logger.error(f"[PIT_SNAPSHOT] {symbol}: Normalized file not found: {fpath}")
        return None

    try:
        with open(fpath) as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"[PIT_SNAPSHOT] {symbol}: Failed to load {fpath}: {e}")
        return None

    isin = data.get("isin", "")
    snapshot = PITSnapshot(
        symbol=symbol,
        isin=isin,
        as_of_timestamp=as_of_timestamp,
        basis=data.get("basis", basis),
        statement_type=statement_type,
        period_end_date=latest["period_end_date"],
        filing_id=latest["filing_id"],
        filing_version=latest["filing_version"],
        pit_eligible_from=latest["pit_eligible_from"],
        source_exchange=latest.get("exchange", "NSE"),
        revenue=data.get("revenue"),
        operating_profit=data.get("operating_profit"),
        ebit=data.get("ebit"),
        depreciation=data.get("depreciation"),
        amortization=data.get("amortization"),
        pat=data.get("net_profit") or data.get("pat"),
        eps_basic=data.get("eps_basic") or data.get("eps"),
        eps_diluted=data.get("eps_diluted"),
        shares_outstanding=data.get("shares_outstanding"),
        weighted_avg_shares=data.get("weighted_avg_shares"),
        total_debt=data.get("total_debt"),
        cash_and_equivalents=data.get("cash_and_equivalents"),
        total_equity=data.get("total_equity"),
        operating_cash_flow=data.get("operating_cash_flow"),
        exceptional_items=data.get("exceptional_items"),
        minority_interest=data.get("minority_interest"),
        provenance_status="CERTIFIED",
    )

    # Compute EBITDA if possible
    if snapshot.operating_profit is not None and snapshot.depreciation is not None:
        da = (snapshot.depreciation or 0) + (snapshot.amortization or 0)
        snapshot.ebitda = round(snapshot.operating_profit + da, 4)
        snapshot.ebitda_formula = "EBIT + D&A"
    elif snapshot.operating_profit is not None:
        snapshot.ebitda = snapshot.operating_profit
        snapshot.ebitda_formula = "EBIT_PROXY (D&A missing)"

    return snapshot


# ---------------------------------------------------------------------------
# Data Quality Audit Report
# ---------------------------------------------------------------------------

@dataclass
class ExchangeDataAuditReport:
    """
    Machine-readable audit report for the exchange financial data pipeline.
    Section 34 of the spec.
    """
    universe_size: int = 0
    symbols_with_exchange_data: int = 0
    symbols_without_exchange_data: int = 0

    annual_filings_stored: int = 0
    quarterly_filings_stored: int = 0

    nse_sourced: int = 0
    bse_sourced: int = 0
    dual_sourced: int = 0

    nse_bse_conflicts: int = 0
    nse_bse_reconciled: int = 0

    fy_gaps_detected: int = 0
    symbols_with_gaps: List[str] = field(default_factory=list)

    amended_filings: int = 0

    pit_fresh: int = 0
    pit_stale: int = 0

    fully_reconstructable: int = 0
    provenance_certified: int = 0

    required_facts_complete: int = 0
    cash_missing: int = 0
    shares_missing: int = 0
    ocf_missing: int = 0

    symbol_level: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def to_summary(self) -> str:
        return (
            f"\n{'='*60}\n"
            f"EXCHANGE FINANCIAL DATA AUDIT\n"
            f"{'='*60}\n"
            f"Universe:                    {self.universe_size}\n"
            f"Symbols with exchange data:  {self.symbols_with_exchange_data}\n"
            f"Symbols without data:        {self.symbols_without_exchange_data}\n"
            f"Annual filings stored:       {self.annual_filings_stored}\n"
            f"Quarterly filings stored:    {self.quarterly_filings_stored}\n"
            f"NSE sourced:                 {self.nse_sourced}\n"
            f"BSE sourced:                 {self.bse_sourced}\n"
            f"Dual sourced:                {self.dual_sourced}\n"
            f"NSE/BSE conflicts:           {self.nse_bse_conflicts}\n"
            f"FY gaps detected:            {self.fy_gaps_detected}\n"
            f"PIT fresh:                   {self.pit_fresh}\n"
            f"PIT stale:                   {self.pit_stale}\n"
            f"Amended filings:             {self.amended_filings}\n"
            f"Cash missing:                {self.cash_missing}\n"
            f"Shares missing:              {self.shares_missing}\n"
            f"OCF missing:                 {self.ocf_missing}\n"
            f"Fully reconstructable:       {self.fully_reconstructable}\n"
            f"Provenance certified:        {self.provenance_certified}\n"
            f"{'='*60}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

def run_ingestion_pipeline(
    symbols: List[str],
    target_years: int = 6,
    include_quarterly: bool = True,
    exchange_dir: Path = EXCHANGE_DIR,
    dry_run: bool = False,
) -> ExchangeDataAuditReport:
    """
    Main Phase 2 ingestion pipeline entry point.

    For each symbol:
    1. Resolve ISIN.
    2. Fetch NSE annual filings (target_years + 1 years).
    3. Fetch BSE annual filings.
    4. Normalize units.
    5. Reconcile NSE/BSE.
    6. Detect filing gaps.
    7. Store immutably.
    8. Build PIT snapshot.
    9. Compute data quality metrics.

    NOTE: NSE/BSE API integration is a Phase 2 deliverable.
    This scaffold defines the pipeline structure; actual API calls
    are stubbed as _fetch_nse_filings / _fetch_bse_filings below.

    Args:
        symbols: List of NSE tickers to ingest.
        target_years: Number of fiscal years to ingest.
        include_quarterly: Whether to also ingest quarterly data.
        exchange_dir: Root exchange data directory.
        dry_run: If True, skip writing files.

    Returns:
        ExchangeDataAuditReport with per-symbol results.
    """
    from app.financial_data_integrity import check_pit_freshness, detect_annual_fiscal_gaps

    report = ExchangeDataAuditReport(universe_size=len(symbols))
    exchange_dir.mkdir(parents=True, exist_ok=True)

    scan_date = datetime.now(IST).date() if IST else date.today()

    for sym in symbols:
        try:
            sym = sym.strip().upper()
            isin = resolve_isin(sym)
            sym_result: Dict[str, Any] = {
                "symbol": sym,
                "isin": isin,
                "nse_annual_filings": 0,
                "bse_annual_filings": 0,
                "conflicts": [],
                "gaps": [],
                "freshness_status": "",
                "provenance_status": "UNCERTIFIED",
            }

            # ── 1. Fetch NSE annual filings ───────────────────────────────
            nse_filings = _fetch_nse_annual_filings(sym, isin, target_years)
            bse_filings = _fetch_bse_annual_filings(sym, isin, target_years)

            if not nse_filings and not bse_filings:
                logger.warning(f"[PIPELINE] {sym}: No filings from either exchange.")
                sym_result["provenance_status"] = "NO_EXCHANGE_DATA"
                report.symbol_level[sym] = sym_result
                report.symbols_without_exchange_data += 1
                continue

            report.symbols_with_exchange_data += 1

            # ── 2. Normalize and store NSE filings ────────────────────────
            for filing in nse_filings:
                sha256 = compute_sha256(json.dumps(filing, sort_keys=True, default=str))
                normalized = _normalize_filing(filing, "NSE", sym, isin or "")
                if not dry_run:
                    store_raw_filing(
                        symbol=sym,
                        filing_id=filing.get("filing_id", "UNKNOWN"),
                        filing_version=filing.get("version", "v1"),
                        statement_type=filing.get("statement_type", "ANNUAL"),
                        period_end=filing.get("period_end_date", ""),
                        payload=filing,
                        exchange_dir=exchange_dir,
                    )
                    store_normalized_filing(
                        symbol=sym,
                        statement_type=filing.get("statement_type", "ANNUAL"),
                        period_end=filing.get("period_end_date", ""),
                        filing_id=filing.get("filing_id", "UNKNOWN"),
                        filing_version=filing.get("version", "v1"),
                        normalized=normalized,
                        exchange_dir=exchange_dir,
                    )
                report.annual_filings_stored += 1
                report.nse_sourced += 1
                sym_result["nse_annual_filings"] += 1

            # ── 3. NSE/BSE reconciliation ─────────────────────────────────
            conflicts = []
            for nse_f, bse_f in _match_filings_by_period(nse_filings, bse_filings):
                for fact_name in ["revenue", "net_profit", "total_debt", "cash_and_equivalents"]:
                    nse_val = nse_f.get(fact_name) if nse_f else None
                    bse_val = bse_f.get(fact_name) if bse_f else None
                    rec = reconcile_nse_bse(
                        sym, fact_name,
                        (nse_f or bse_f or {}).get("period_end_date", ""),
                        (nse_f or bse_f or {}).get("basis", "CONSOLIDATED"),
                        nse_val, bse_val,
                    )
                    if rec.reconciliation_status == "DATA_CONFLICT":
                        conflicts.append(rec)
                        report.nse_bse_conflicts += 1
                    else:
                        report.nse_bse_reconciled += 1

            sym_result["conflicts"] = [asdict(c) for c in conflicts]

            # ── 4. Gap detection ──────────────────────────────────────────
            all_rows = [
                {"period_end_date": f.get("period_end_date")}
                for f in (nse_filings or bse_filings)
                if f.get("period_end_date")
            ]
            all_rows_sorted = sorted(all_rows, key=lambda r: r["period_end_date"])
            gaps = detect_annual_fiscal_gaps(all_rows_sorted)
            sym_result["gaps"] = gaps
            if gaps:
                report.fy_gaps_detected += len(gaps)
                if sym not in report.symbols_with_gaps:
                    report.symbols_with_gaps.append(sym)

            # ── 5. PIT freshness check ────────────────────────────────────
            if all_rows_sorted:
                latest_period = all_rows_sorted[-1]["period_end_date"]
                freshness = check_pit_freshness(sym, latest_period, scan_date=scan_date)
                sym_result["freshness_status"] = freshness.status.value
                if freshness.ok:
                    report.pit_fresh += 1
                else:
                    report.pit_stale += 1
            else:
                sym_result["freshness_status"] = "NO_DATA"

            # ── 6. Mark provenance ────────────────────────────────────────
            if not gaps and not conflicts:
                sym_result["provenance_status"] = "CERTIFIED"
                report.provenance_certified += 1
                report.fully_reconstructable += 1
            elif gaps:
                sym_result["provenance_status"] = "FILING_GAPS_DETECTED"
            elif conflicts:
                sym_result["provenance_status"] = "EXCHANGE_CONFLICT"

            report.symbol_level[sym] = sym_result

        except Exception as e:
            logger.error(f"[PIPELINE] {sym}: Unhandled error: {e}", exc_info=True)
            report.symbol_level[sym] = {"symbol": sym, "error": str(e), "provenance_status": "ERROR"}

    return report


# ---------------------------------------------------------------------------
# NSE/BSE API stubs (Phase 2 — to be implemented with confirmed endpoints)
# ---------------------------------------------------------------------------

def _fetch_nse_annual_filings(
    symbol: str,
    isin: Optional[str],
    target_years: int,
) -> List[Dict[str, Any]]:
    """
    Stub: Fetches annual financial filings from NSE XBRL/corporate filings API.

    Phase 2 Implementation:
    1. Call NSE corporate filings API endpoint.
    2. Filter for annual (standalone + consolidated) filings.
    3. Parse XBRL/JSON response.
    4. Return normalized list of filing dicts.

    Current status: STUB — returns empty list until NSE API is confirmed.
    """
    logger.info(f"[NSE_FETCH] {symbol}: Annual filing fetch — Phase 2 stub. Requires NSE API endpoint.")
    return []


def _fetch_bse_annual_filings(
    symbol: str,
    isin: Optional[str],
    target_years: int,
) -> List[Dict[str, Any]]:
    """
    Stub: Fetches annual financial filings from BSE corporate filings API.

    Phase 2 Implementation:
    1. Call BSE corporate filings API endpoint (api.bseindia.com/BseIndiaAPI).
    2. Filter for annual filings.
    3. Parse response.
    4. Return normalized list.

    Current status: STUB — returns empty list until BSE API is confirmed.
    """
    logger.info(f"[BSE_FETCH] {symbol}: Annual filing fetch — Phase 2 stub. Requires BSE API endpoint.")
    return []


def _normalize_filing(
    raw_filing: Dict[str, Any],
    exchange: str,
    symbol: str,
    isin: str,
) -> Dict[str, Any]:
    """
    Normalizes a raw exchange filing response to the canonical fact schema.

    Phase 2 implementation will apply XBRL concept mapping and unit normalization.
    Current version: passes through with metadata added.
    """
    normalized = dict(raw_filing)
    normalized["source_exchange"] = exchange
    normalized["symbol"] = symbol
    normalized["isin"] = isin
    normalized["normalized_at"] = datetime.now(IST).isoformat() if IST else datetime.utcnow().isoformat()
    return normalized


def _match_filings_by_period(
    nse_filings: List[Dict],
    bse_filings: List[Dict],
) -> List[Tuple[Optional[Dict], Optional[Dict]]]:
    """
    Pairs NSE and BSE filings by period_end_date for reconciliation.
    """
    nse_by_period = {f.get("period_end_date"): f for f in nse_filings if f.get("period_end_date")}
    bse_by_period = {f.get("period_end_date"): f for f in bse_filings if f.get("period_end_date")}
    all_periods = set(nse_by_period) | set(bse_by_period)
    return [(nse_by_period.get(p), bse_by_period.get(p)) for p in sorted(all_periods)]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="NSE/BSE Exchange Financial Fact Ingestion")
    parser.add_argument("--symbol", help="Single NSE symbol to ingest")
    parser.add_argument("--universe", help="Path to JSON universe file")
    parser.add_argument("--years", type=int, default=6, help="Years of history to ingest")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    args = parser.parse_args()

    if args.symbol:
        symbols = [args.symbol.upper()]
    elif args.universe:
        with open(args.universe) as f:
            universe = json.load(f)
        symbols = [
            e.get("symbol", e) if isinstance(e, dict) else str(e)
            for e in universe
        ]
    else:
        print("ERROR: Provide --symbol or --universe")
        raise SystemExit(1)

    print(f"Starting Phase 2 exchange financial ingestion for {len(symbols)} symbols...")
    report = run_ingestion_pipeline(
        symbols=symbols,
        target_years=args.years,
        dry_run=args.dry_run,
    )
    print(report.to_summary())
