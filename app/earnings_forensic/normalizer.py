"""
app/earnings_forensic/normalizer.py
Converts data/pit_raw_filings/<symbol>.json into a ForensicFactSheet.

Design rules:
  - Prefers CONSOLIDATED over STANDALONE for the same period
  - Never mixes bases in a multi-period comparison
  - quarterly OCF is treated as None (SCREENER does not populate it)
  - All monetary values in INR Crore
  - EVIDENCE_INSUFFICIENT is explicit — never zero-filled
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .schema import (
    CashFlowMetrics,
    DataScope,
    EvidenceLevel,
    ForensicFactSheet,
    GrowthMetrics,
    PeriodRecord,
)

logger = logging.getLogger(__name__)

# BASE_DIR resolution — normalizer is in app/earnings_forensic/
_THIS_DIR = Path(__file__).resolve().parent          # app/earnings_forensic/
_APP_DIR  = _THIS_DIR.parent                         # app/
_BASE_DIR = _APP_DIR.parent                          # project root
PIT_RAW_DIR = _BASE_DIR / "data" / "pit_raw_filings"


def _safe_float(val: Any) -> Optional[float]:
    """Safely cast to float; returns None for None/NaN/invalid."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _parse_iso(s: Any) -> Optional[str]:
    """Return YYYY-MM-DD string or None."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _normalize_basis(raw: str) -> str:
    """Normalise to CONSOLIDATED or STANDALONE."""
    r = str(raw or "").upper()
    if "STANDALONE" in r or "STAND_ALONE" in r:
        return "STANDALONE"
    return "CONSOLIDATED"


def _infer_basis_from_source(source: str) -> str:
    """Infer basis from the source_provider field used by SCREENER records."""
    s = str(source or "").lower()
    if "standalone" in s:
        return "STANDALONE"
    return "CONSOLIDATED"


def _row_to_period_record(row: Dict[str, Any]) -> Optional[PeriodRecord]:
    """Convert a single PIT JSON row into a PeriodRecord."""
    period_end = _parse_iso(row.get("period_end_date"))
    if not period_end:
        return None

    st = str(row.get("statement_type") or "QUARTERLY").upper()
    if "ANNUAL" in st or "YEARLY" in st or "FULL_YEAR" in st:
        st = "ANNUAL"
    else:
        st = "QUARTERLY"

    # Basis: prefer explicit field, then infer from source
    raw_basis = str(row.get("basis") or "").strip()
    if raw_basis:
        basis = _normalize_basis(raw_basis)
    else:
        basis = _infer_basis_from_source(str(row.get("source") or row.get("source_provider") or ""))

    return PeriodRecord(
        period_end_date      = period_end,
        statement_type       = st,
        basis                = basis,
        source_provider      = str(row.get("source_provider") or row.get("source") or "UNKNOWN"),
        revenue              = _safe_float(row.get("revenue") or row.get("mapped_revenue")),
        operating_profit     = _safe_float(row.get("operating_profit")),
        net_profit           = _safe_float(row.get("net_profit")),
        eps                  = _safe_float(row.get("eps")),
        shares_outstanding   = _safe_float(row.get("shares_outstanding")),
        operating_cash_flow  = _safe_float(row.get("operating_cash_flow")),
        free_cash_flow       = _safe_float(row.get("free_cash_flow")),
        total_debt           = _safe_float(row.get("total_debt")),
        total_equity         = _safe_float(row.get("total_equity")),
        cash_and_equivalents = _safe_float(row.get("cash_and_equivalents")),
        depreciation_amortization = _safe_float(row.get("depreciation_amortization")),
        roce                 = _safe_float(row.get("roce")),
        roe                  = _safe_float(row.get("roe")),
        operating_margin     = _safe_float(row.get("operating_margin")),
        net_margin           = _safe_float(row.get("net_margin")),
        filing_id            = str(row.get("filing_id") or ""),
        filing_date          = _parse_iso(row.get("filing_date")),
    )


def _load_pit_series(symbol: str) -> List[Dict[str, Any]]:
    """Load all PIT records for a symbol from disk."""
    path = PIT_RAW_DIR / f"{symbol.strip().upper()}.json"
    if not path.exists():
        logger.warning(f"[NORMALIZER] PIT file not found: {path}")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning(f"[NORMALIZER] PIT file for {symbol} is not a list")
        return []
    except Exception as e:
        logger.error(f"[NORMALIZER] Failed to load PIT data for {symbol}: {e}")
        return []


def _select_preferred_basis(
    records: List[PeriodRecord], preferred_basis: str
) -> List[PeriodRecord]:
    """For each period, prefer CONSOLIDATED; fall back to STANDALONE."""
    by_period: Dict[str, Dict[str, PeriodRecord]] = {}
    for r in records:
        by_period.setdefault(r.period_end_date, {})[r.basis] = r

    result = []
    for period, basis_map in sorted(by_period.items(), reverse=True):
        if preferred_basis in basis_map:
            result.append(basis_map[preferred_basis])
        elif basis_map:
            # only fallback available
            fallback = list(basis_map.values())[0]
            result.append(fallback)
    return result


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None:
        return None
    if old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def _bps_change(new_pct: Optional[float], old_pct: Optional[float]) -> Optional[float]:
    """Change in basis points (bps) for margin comparisons."""
    if new_pct is None or old_pct is None:
        return None
    return round((new_pct - old_pct) * 100, 1)


def _compute_growth(
    current: PeriodRecord,
    prior_q: Optional[PeriodRecord],
    same_q_py: Optional[PeriodRecord],
    quarterly_series: List[PeriodRecord],
) -> GrowthMetrics:
    g = GrowthMetrics()

    # YoY (vs same Q prior year)
    if same_q_py:
        g.revenue_yoy_pct    = _pct_change(current.revenue, same_q_py.revenue)
        g.op_profit_yoy_pct  = _pct_change(current.operating_profit, same_q_py.operating_profit)
        g.net_profit_yoy_pct = _pct_change(current.net_profit, same_q_py.net_profit)
        g.eps_yoy_pct        = _pct_change(current.eps, same_q_py.eps)
        g.opm_yoy_delta_bps  = _bps_change(current.operating_margin, same_q_py.operating_margin)
        g.npm_yoy_delta_bps  = _bps_change(current.net_margin, same_q_py.net_margin)

    # QoQ (vs immediately prior quarter)
    if prior_q:
        g.revenue_qoq_pct    = _pct_change(current.revenue, prior_q.revenue)
        g.op_profit_qoq_pct  = _pct_change(current.operating_profit, prior_q.operating_profit)
        g.net_profit_qoq_pct = _pct_change(current.net_profit, prior_q.net_profit)
        g.opm_qoq_delta_bps  = _bps_change(current.operating_margin, prior_q.operating_margin)

    # TTM: sum of last 4 quarterly records (including current)
    last4 = [r for r in quarterly_series if r.period_end_date <= current.period_end_date][:4]
    if len(last4) == 4:
        def _sum4(field: str) -> Optional[float]:
            vals = [_safe_float(getattr(r, field)) for r in last4]
            if all(v is not None for v in vals):
                return round(sum(vals), 2)
            return None
        g.ttm_revenue    = _sum4("revenue")
        g.ttm_op_profit  = _sum4("operating_profit")
        g.ttm_net_profit = _sum4("net_profit")

    return g


def _compute_cash_flow(annual_series: List[PeriodRecord]) -> CashFlowMetrics:
    cf = CashFlowMetrics()

    # Filter to annual records with OCF
    usable = [r for r in annual_series if r.operating_cash_flow is not None and r.net_profit not in (None, 0)]
    if not usable:
        cf.data_scope = DataScope.NOT_APPLICABLE
        return cf

    cf.ocf_data_available = True
    cf.data_scope = DataScope.ANNUAL_ONLY

    def _ratio(r: PeriodRecord) -> Optional[float]:
        if r.operating_cash_flow is None or r.net_profit in (None, 0):
            return None
        return round(r.operating_cash_flow / r.net_profit, 3)

    ratios = [_ratio(r) for r in usable if _ratio(r) is not None]

    if ratios:
        cf.cfo_pat_current_year = ratios[0]
    if len(ratios) >= 3:
        cf.cfo_pat_3y_avg = round(sum(ratios[:3]) / 3, 3)
    if len(ratios) >= 5:
        cf.cfo_pat_5y_avg = round(sum(ratios[:5]) / 5, 3)

    if usable:
        cf.fcf_current_year = _safe_float(usable[0].free_cash_flow)

    return cf


def build_factsheet(
    symbol: str,
    period_end: Optional[str] = None,
    filing_id: Optional[str] = None,
    source_hash: Optional[str] = None,
    preferred_basis: str = "CONSOLIDATED",
) -> Optional[ForensicFactSheet]:
    """
    Build a ForensicFactSheet from local PIT data.
    If period_end is None, defaults to the latest available period.
    Returns None if the target period cannot be found.
    """
    raw_rows = _load_pit_series(symbol)
    if not raw_rows:
        logger.warning(f"[NORMALIZER] No PIT data for {symbol}")
        return None

    all_records: List[PeriodRecord] = []
    for row in raw_rows:
        r = _row_to_period_record(row)
        if r:
            all_records.append(r)

    if not all_records:
        return None

    # Split quarterly vs annual
    quarterly_all = [r for r in all_records if r.statement_type == "QUARTERLY"]
    annual_all    = [r for r in all_records if r.statement_type == "ANNUAL"]

    # Select preferred basis for each set (CONSOLIDATED > STANDALONE)
    quarterly_series = _select_preferred_basis(quarterly_all, preferred_basis)
    annual_series    = _select_preferred_basis(annual_all, preferred_basis)

    if not period_end:
        if quarterly_series:
            sorted_q = sorted(quarterly_series, key=lambda r: r.period_end_date, reverse=True)
            period_end = sorted_q[0].period_end_date
        elif annual_series:
            sorted_a = sorted(annual_series, key=lambda r: r.period_end_date, reverse=True)
            period_end = sorted_a[0].period_end_date
        else:
            return None

    # Find the target period
    target_norm = _parse_iso(period_end)
    if not target_norm:
        logger.error(f"[NORMALIZER] Invalid period_end '{period_end}' for {symbol}")
        return None

    # Try quarterly first, then annual
    current: Optional[PeriodRecord] = None
    resolved_st = "QUARTERLY"
    for r in quarterly_series:
        if r.period_end_date == target_norm:
            current = r
            break
    if current is None:
        for r in annual_series:
            if r.period_end_date == target_norm:
                current = r
                resolved_st = "ANNUAL"
                break

    if current is None:
        logger.warning(f"[NORMALIZER] Period {target_norm} not found in PIT series for {symbol}")
        return None

    # Build multi-period references (using the same basis as current)
    actual_basis = current.basis

    # Quarterly series filtered to matching basis, sorted descending
    q_same_basis = sorted(
        [r for r in quarterly_series if r.basis == actual_basis],
        key=lambda r: r.period_end_date,
        reverse=True
    )

    # Position of current in the sorted list
    q_dates = [r.period_end_date for r in q_same_basis]
    try:
        idx = q_dates.index(target_norm)
    except ValueError:
        idx = 0

    prior_quarter   = q_same_basis[idx + 1] if idx + 1 < len(q_same_basis) else None
    same_q_py       = q_same_basis[idx + 4] if idx + 4 < len(q_same_basis) else None

    annual_same_basis = sorted(
        [r for r in annual_series if r.basis == actual_basis],
        key=lambda r: r.period_end_date,
        reverse=True
    )

    # Determine evidence availability
    available: List[str] = []
    missing: List[str]   = []
    for field_name in ["revenue", "operating_profit", "net_profit", "eps",
                       "operating_margin", "net_margin", "roce", "roe"]:
        if getattr(current, field_name) is not None:
            available.append(field_name)
        else:
            missing.append(field_name)

    # Fields only in annual
    for field_name in ["operating_cash_flow", "free_cash_flow", "total_debt", "total_equity"]:
        if annual_same_basis and getattr(annual_same_basis[0], field_name) is not None:
            available.append(f"annual.{field_name}")
        else:
            missing.append(f"annual.{field_name}")

    # Sector detection (reuse existing ForensicEngine)
    is_fin = False
    try:
        import sys
        if str(_APP_DIR) not in sys.path:
            sys.path.insert(0, str(_APP_DIR))
        from forensic_engine import ForensicEngine
        fd_proxy = {"sector": "", "path": ""}  # minimal — sector from PIT source field
        # Infer from source_provider if it contains "bank"/"nii"
        src = str(current.source_provider).lower()
        if current.roce is None and "nii" in src:
            is_fin = True
        else:
            is_fin = ForensicEngine.is_financial_institution(fd_proxy)
    except Exception:
        pass

    # Compute growth and cash flow
    growth    = _compute_growth(current, prior_quarter, same_q_py, q_same_basis)
    cash_flow = _compute_cash_flow(annual_same_basis)

    return ForensicFactSheet(
        symbol                   = symbol.upper(),
        period_end               = target_norm,
        statement_type           = resolved_st,
        basis                    = actual_basis,
        filing_id                = filing_id,
        source_hash              = source_hash,
        current                  = current,
        prior_quarter            = prior_quarter,
        same_quarter_py          = same_q_py,
        annual_series            = annual_same_basis,
        quarterly_series         = q_same_basis,
        growth                   = growth,
        cash_flow                = cash_flow,
        is_financial_sector      = is_fin,
        evidence_available_fields= available,
        evidence_missing_fields  = missing,
    )
