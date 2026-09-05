"""
universe_checklist.py
=====================
Phase 2A Daily Builder V2 — Eligibility Checklist and Handoff Contract.

Responsibilities:
  1. UniverseChecklist  — per-criterion CLEARED/FAILED/WARNING/NOT_APPLICABLE
  2. FundamentalProfile — frozen handoff contract for Phase 2B+ scanners (INV-3)
  3. EligibilityDecision — combined result: universe_status, exclusion_class,
                           near_qualified_mode, quality_tier, fundamental_profile
  4. CorporateActionRecord — structured corporate-action audit record (§3.8)
  5. build_universe_checklist()  — populates the checklist from raw metrics
  6. determine_eligibility()     — maps checklist + score → EligibilityDecision
  7. format_eligibility_report() — human-readable text for dashboard / logs

[INV-1] Nothing in this module may write to alerts, near_misses, or any Phase 1 table.
[INV-2] data_confidence is always surfaced explicitly — never hidden.
[INV-3] FundamentalProfile schema is frozen. Downstream scanners must not
        silently overwrite Daily Builder fields inside the original object.
[INV-4] No automatic scanner migration.
[INV-5] Q2–Q5 thresholds are provisional; calibrated from replay.

[VERSION: DAILY_BUILDER_V2_CHECKLIST_v1.0]
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from universe_quality_score import (
    ScoreBreakdown,
    GAP_TOLERANCE_BY_METRIC,
    ELITE_SCORE_THRESHOLD,
    NEAR_QUALIFIED_SCORE_MIN,
    NEAR_QUALIFIED_SCORE_MAX,
    NEAR_QUALIFIED_SINGLE_GAP_MIN_SCORE,
    _safe,
    _pct,
)


# ---------------------------------------------------------------------------
# Criterion status constants
# ---------------------------------------------------------------------------
CLEARED         = "CLEARED"
FAILED          = "FAILED"
WARNING         = "WARNING"
NOT_APPLICABLE  = "NOT_APPLICABLE"

# Exclusion classes
HARD_BLOCK  = "HARD_BLOCK"
QUALITY_FAIL  = "QUALITY_FAIL"
DATA_FAIL     = "DATA_FAIL"
SURVEILLANCE  = "SURVEILLANCE"

# Universe status
STATUS_ELITE            = "ELITE"
STATUS_NEAR_QUALIFIED   = "NEAR_QUALIFIED"
STATUS_EXCLUDED         = "EXCLUDED"
STATUS_DEGRADATION_WATCH = "UNIVERSE_DEGRADATION_WATCH"

# NEAR_QUALIFIED modes
NQ_MODE_SCORE_BAND  = "SCORE_BAND"
NQ_MODE_SINGLE_GAP  = "SINGLE_GAP"


# ---------------------------------------------------------------------------
# CorporateActionRecord
# Required for auditability and replay reproducibility (§3.8).
# ---------------------------------------------------------------------------
@dataclass
class CorporateActionRecord:
    """
    Structured record of a corporate action that affects quality metric comparability.

    When populated, affected_metrics are excluded from scoring
    (treated as OPTIONAL missing) and suppressed_in_score is set to True.
    The record is stored as `corporate_action_detail` (JSON) in the output row.
    """
    corporate_action_type: str        # SPLIT / BONUS / MERGER / DEMERGER / SYMBOL_CHANGE / DELISTING
    effective_date:        date
    adjustment_factor:     float = 1.0  # e.g. 2.0 for 1:2 split; 1.0 if N/A
    source:                str  = ""    # 'fundamentals_cache' | 'manual_override' | 'NSE_announcement'
    affected_metrics:      list = field(default_factory=list)  # e.g. ['yoy_profit', 'yoy_sales']
    suppressed_in_score:   bool = True   # True → affected metrics excluded from scoring

    def as_dict(self) -> dict:
        return {
            "corporate_action_type": self.corporate_action_type,
            "effective_date":        str(self.effective_date),
            "adjustment_factor":     self.adjustment_factor,
            "source":                self.source,
            "affected_metrics":      self.affected_metrics,
            "suppressed_in_score":   self.suppressed_in_score,
        }


# ---------------------------------------------------------------------------
# FundamentalProfile  [INV-3 — SCHEMA FROZEN]
# ---------------------------------------------------------------------------
@dataclass
class FundamentalProfile:
    """
    Authoritative handoff object from Daily Builder V2 to all downstream scanners.

    [INV-3] This schema is frozen. Downstream scanners (EOD Phase 2B, Multi-TF 2C,
    Pullback 2E, Multibagger, Wealth Engine) may enrich a COPY of this object with
    scanner-specific fields but must NEVER silently overwrite any field defined here.

    If a scanner believes a field value is wrong, the correct path is to file a spec
    change against Phase 2A — not to mutate the object at runtime.

    Serialised as JSONB in the output parquet row (`fundamental_profile` column).
    """
    quality_tier:           str   = ""        # 'A+' | 'A' | 'B' | 'C'
    primary_archetype:      str   = ""        # e.g. 'Long Term Compounder'
    primary_strength:       str   = ""        # e.g. 'FCF + ROE + 5Y Track Record'
    secondary_strengths:    list  = field(default_factory=list)
    business_quality:       int   = 0         # 0–30
    growth_quality:         int   = 0         # 0–35
    valuation_context:      int   = 0         # 0–20
    governance:             int   = 0         # 0–15
    institutional_interest: str   = "UNKNOWN" # 'STRONG' | 'MODERATE' | 'WEAK' | 'UNKNOWN'
    data_confidence:        str   = "LOW"     # 'HIGH' | 'MEDIUM' | 'LOW'  [INV-2]

    def as_dict(self) -> dict:
        return {
            "quality_tier":           self.quality_tier,
            "primary_archetype":      self.primary_archetype,
            "primary_strength":       self.primary_strength,
            "secondary_strengths":    self.secondary_strengths,
            "business_quality":       self.business_quality,
            "growth_quality":         self.growth_quality,
            "valuation_context":      self.valuation_context,
            "governance":             self.governance,
            "institutional_interest": self.institutional_interest,
            "data_confidence":        self.data_confidence,
        }

    def enrich_copy(self, **kwargs) -> "FundamentalProfile":
        """
        Returns a deep copy of this profile with scanner-specific additions.
        ONLY new keys may be added — existing keys are read-only from downstream.
        Raises ValueError if caller tries to overwrite a frozen field.
        """
        frozen_fields = set(self.as_dict().keys())
        for k in kwargs:
            if k in frozen_fields:
                raise ValueError(
                    f"[INV-3] Downstream scanner attempted to overwrite frozen "
                    f"FundamentalProfile field '{k}'. File a Phase 2A spec change instead."
                )
        enriched = copy.deepcopy(self)
        for k, v in kwargs.items():
            object.__setattr__(enriched, k, v)
        return enriched


# ---------------------------------------------------------------------------
# ChecklistCriterion
# ---------------------------------------------------------------------------
@dataclass
class ChecklistCriterion:
    """Single checklist entry for one gate/quality factor."""
    name:           str             # e.g. 'Price Floor', 'ROE', 'CFO/PAT'
    status:         str             # CLEARED | FAILED | WARNING | NOT_APPLICABLE
    actual_value:   object = None   # the observed metric value (for display)
    threshold:      object = None   # the threshold against which it was evaluated
    note:           str    = ""     # optional explanation (e.g. 'gap: 1.2 pp within 15% tolerance')
    exclusion_code: str    = ""     # primary code if this criterion is the failure reason

    def as_dict(self) -> dict:
        return {
            "name":           self.name,
            "status":         self.status,
            "actual_value":   self.actual_value,
            "threshold":      self.threshold,
            "note":           self.note,
            "exclusion_code": self.exclusion_code,
        }


# ---------------------------------------------------------------------------
# UniverseChecklist
# ---------------------------------------------------------------------------
@dataclass
class UniverseChecklist:
    """
    Per-stock eligibility checklist — one criterion per gate/quality factor.
    Mutual-exclusion contract: a criterion is exactly one of CLEARED / FAILED /
    WARNING / NOT_APPLICABLE. Never two statuses simultaneously.
    """
    symbol:      str
    path:        str                              # NON_FINANCIAL | BANK | NBFC_HFC | INSURANCE | AMC | FINANCIAL_UNCLASSIFIED
    criteria:    list = field(default_factory=list)  # list[ChecklistCriterion]
    turnaround:  bool = False
    corporate_action: Optional[CorporateActionRecord] = None

    # Aggregated exclusion information (set by build_universe_checklist)
    primary_exclusion_code:    str  = ""
    secondary_exclusion_codes: list = field(default_factory=list)
    exclusion_class:           str  = ""

    # Near-qualified gap tracking
    zero_scored_factors:  list = field(default_factory=list)   # factors that actively scored 0
    failed_below_threshold: list = field(default_factory=list) # factors that failed below gate threshold

    def add(self, criterion: ChecklistCriterion) -> None:
        self.criteria.append(criterion)
        if criterion.status == FAILED and criterion.exclusion_code:
            if not self.primary_exclusion_code:
                self.primary_exclusion_code = criterion.exclusion_code
                self.exclusion_class = _classify_exclusion_class(criterion.exclusion_code)
            elif criterion.exclusion_code not in self.secondary_exclusion_codes:
                self.secondary_exclusion_codes.append(criterion.exclusion_code)

    def has_hard_failure(self) -> bool:
        return any(c.status == FAILED for c in self.criteria)

    def cleared_count(self) -> int:
        return sum(1 for c in self.criteria if c.status == CLEARED)

    def as_dict(self) -> dict:
        return {c.name: c.as_dict() for c in self.criteria}


def _classify_exclusion_class(code: str) -> str:
    """Maps an exclusion code to its exclusion_class."""
    hard_blocks = {
        "BELOW_PRICE_FLOOR", "BELOW_MCAP_FLOOR", "LOW_LIQUIDITY", "LOW_VOLUME",
        "SHELL_RISK",  # PROVISIONAL — may be reclassified after replay
    }
    surveillance_codes = {
        "SURVEILLANCE_ASM", "SURVEILLANCE_GSM", "PROMOTER_BLACKLISTED",
    }
    data_codes = {
        "DATA_ABSENT", "DATA_CORRUPT", "INSUFFICIENT_HISTORY",
        "STATISTICAL_ANOMALY",
    }
    if code in hard_blocks:
        return HARD_BLOCK
    if code in surveillance_codes:
        return SURVEILLANCE
    if code in data_codes:
        return DATA_FAIL
    return QUALITY_FAIL


# ---------------------------------------------------------------------------
# EligibilityDecision
# ---------------------------------------------------------------------------
@dataclass
class EligibilityDecision:
    """
    Final eligibility result for a single stock.
    Returned by determine_eligibility().
    """
    universe_status:           str    # ELITE | NEAR_QUALIFIED | EXCLUDED
    exclusion_class:           str    # HARD_BLOCK | QUALITY_FAIL | DATA_FAIL | SURVEILLANCE | ""
    primary_exclusion_code:    str    = ""
    secondary_exclusion_codes: list   = field(default_factory=list)
    near_qualified_mode:       str    = ""   # SCORE_BAND | SINGLE_GAP | ""
    gap_quality_factors:       list   = field(default_factory=list)
    gap_count:                 int    = 0
    quality_tier:              str    = ""   # A+ | A | B | C  (ELITE only)
    fundamental_profile:       Optional[FundamentalProfile] = None   # ELITE and NQ only

    def as_dict(self) -> dict:
        d = {
            "universe_status":           self.universe_status,
            "exclusion_class":           self.exclusion_class,
            "primary_exclusion_code":    self.primary_exclusion_code,
            "secondary_exclusion_codes": self.secondary_exclusion_codes,
            "near_qualified_mode":       self.near_qualified_mode,
            "gap_quality_factors":       self.gap_quality_factors,
            "gap_count":                 self.gap_count,
            "quality_tier":              self.quality_tier,
        }
        if self.fundamental_profile:
            d["fundamental_profile"] = self.fundamental_profile.as_dict()
        return d


# ---------------------------------------------------------------------------
# build_universe_checklist()
# ---------------------------------------------------------------------------

def build_universe_checklist(symbol: str, metrics: dict, path: str) -> UniverseChecklist:
    """
    Builds a per-criterion eligibility checklist from raw metrics.

    Checks are applied in pipeline order:
      Stage 3: Pre-filter gates (HARD_BLOCK)
      Stage 4: Surveillance gates (HARD_BLOCK)
      Stage 6: Junk gates (QUALITY_FAIL — path-specific)
      Stage 1: Data quality checks (DATA_FAIL)

    Returns a UniverseChecklist. If any FAILED criterion is present,
    the caller (determine_eligibility) will classify the stock as EXCLUDED.

    Note: This function does NOT compute scores — see universe_quality_score.py.
    """
    cl = UniverseChecklist(symbol=symbol, path=path)

    # ------------------------------------------------------------------
    # STAGE 3: Pre-filter gates (HARD_BLOCK)
    # ------------------------------------------------------------------
    price = _safe(metrics, "close") or _safe(metrics, "price")
    try:
        price_f = float(price) if price is not None else None
    except (TypeError, ValueError):
        price_f = None

    if price_f is None or price_f <= 0:
        cl.add(ChecklistCriterion("Price", FAILED, actual_value=price_f, threshold=100,
                                   exclusion_code="DATA_CORRUPT"))
    elif price_f < 100:
        cl.add(ChecklistCriterion("Price", FAILED, actual_value=f"₹{price_f:.1f}", threshold="₹100",
                                   note="Below price floor", exclusion_code="BELOW_PRICE_FLOOR"))
    else:
        cl.add(ChecklistCriterion("Price", CLEARED, actual_value=f"₹{price_f:.1f}", threshold="₹100"))

    mcap = _safe(metrics, "market_cap_basic") or _safe(metrics, "market_cap")
    try:
        mcap_cr = float(mcap) / 1e7 if mcap is not None else None  # convert to ₹ Cr
    except (TypeError, ValueError):
        mcap_cr = None

    if mcap_cr is None:
        cl.add(ChecklistCriterion("Market Cap", WARNING, note="Market cap data unavailable"))
    elif mcap_cr < 1000:
        cl.add(ChecklistCriterion("Market Cap", FAILED,
                                   actual_value=f"₹{mcap_cr:.0f} Cr", threshold="₹1,000 Cr",
                                   note="Below market cap floor", exclusion_code="BELOW_MCAP_FLOOR"))
    else:
        cl.add(ChecklistCriterion("Market Cap", CLEARED,
                                   actual_value=f"₹{mcap_cr:.0f} Cr", threshold="₹1,000 Cr"))

    turnover = _safe(metrics, "turnover_20d")
    if turnover is None:
        vol = _safe(metrics, "average_volume_30d_calc") or _safe(metrics, "volume")
        price = _safe(metrics, "close") or _safe(metrics, "price")
        if vol is not None and price is not None:
            try:
                turnover = float(vol) * float(price)
            except (TypeError, ValueError):
                turnover = None

    try:
        turnover_cr = float(turnover) / 1e7 if turnover is not None else None
    except (TypeError, ValueError):
        turnover_cr = None

    if turnover_cr is None:
        cl.add(ChecklistCriterion("Liquidity (20D Turnover)", WARNING, note="Turnover data unavailable"))
    elif turnover_cr < 1.0:
        cl.add(ChecklistCriterion("Liquidity (20D Turnover)", FAILED,
                                   actual_value=f"₹{turnover_cr:.2f} Cr/day", threshold="₹1.0 Cr/day",
                                   exclusion_code="LOW_LIQUIDITY"))
    else:
        cl.add(ChecklistCriterion("Liquidity (20D Turnover)", CLEARED,
                                   actual_value=f"₹{turnover_cr:.1f} Cr/day", threshold="₹1.0 Cr/day"))

    avg_vol = _safe(metrics, "volume") or _safe(metrics, "average_volume_30d")
    try:
        avg_vol_f = float(avg_vol) if avg_vol is not None else None
    except (TypeError, ValueError):
        avg_vol_f = None

    if avg_vol_f is not None and avg_vol_f < 50000:
        cl.add(ChecklistCriterion("30D Avg Volume", FAILED,
                                   actual_value=f"{avg_vol_f:,.0f}", threshold="50,000 shares",
                                   exclusion_code="LOW_VOLUME"))
    else:
        cl.add(ChecklistCriterion("30D Avg Volume", CLEARED,
                                   actual_value=f"{avg_vol_f:,.0f}" if avg_vol_f else "N/A",
                                   threshold="50,000 shares"))

    # SHELL_RISK gate — PROVISIONAL HARD_BLOCK (§3.5)
    promoter_hold = _pct(_safe(metrics, "insider_hold") or _safe(metrics, "promoter_holding_pct"))
    mcap_cr_val = mcap_cr or 0.0
    promoter_mcap = None
    if promoter_hold is not None and mcap_cr_val > 0:
        promoter_mcap = promoter_hold * mcap_cr_val

    if promoter_mcap is not None:
        if promoter_mcap < 500:
            cl.add(ChecklistCriterion("Promoter Market Cap (SHELL_RISK)", FAILED,
                                       actual_value=f"₹{promoter_mcap:.0f} Cr",
                                       threshold="₹500 Cr",
                                       note="PROVISIONAL HARD_BLOCK — pending replay validation. "
                                            "May be reclassified to QUALITY_FAIL if false-exclusion rate > 5%.",
                                       exclusion_code="SHELL_RISK"))
        else:
            cl.add(ChecklistCriterion("Promoter Market Cap (SHELL_RISK)", CLEARED,
                                       actual_value=f"₹{promoter_mcap:.0f} Cr", threshold="₹500 Cr"))
    else:
        cl.add(ChecklistCriterion("Promoter Market Cap (SHELL_RISK)", WARNING,
                                   note="Promoter holding data unavailable — gate not evaluated"))

    # ------------------------------------------------------------------
    # STAGE 4: Surveillance gates
    # ------------------------------------------------------------------
    asm = bool(_safe(metrics, "asm_listed", False))
    gsm = bool(_safe(metrics, "gsm_listed", False))
    blacklist = bool(_safe(metrics, "promoter_blacklisted", False))

    if asm:
        cl.add(ChecklistCriterion("Surveillance (ASM)", FAILED,
                                   note="Stock on ASM list", exclusion_code="SURVEILLANCE_ASM"))
    elif gsm:
        cl.add(ChecklistCriterion("Surveillance (GSM)", FAILED,
                                   note="Stock on GSM list", exclusion_code="SURVEILLANCE_GSM"))
    elif blacklist:
        cl.add(ChecklistCriterion("Surveillance (Promoter)", FAILED,
                                   note="Promoter on blacklist", exclusion_code="PROMOTER_BLACKLISTED"))
    else:
        cl.add(ChecklistCriterion("Surveillance", CLEARED, note="No ASM/GSM/blacklist"))

    # ------------------------------------------------------------------
    # STAGE 1: Data quality / required fields
    # ------------------------------------------------------------------
    bar_history = _safe(metrics, "bar_history") or _safe(metrics, "bars_count")
    try:
        bars = int(bar_history) if bar_history is not None else None
    except (TypeError, ValueError):
        bars = None

    if bars is not None and bars < 50:
        cl.add(ChecklistCriterion("Bar History", FAILED,
                                   actual_value=f"{bars} bars", threshold="50 bars",
                                   exclusion_code="INSUFFICIENT_HISTORY"))
    elif bars is None:
        cl.add(ChecklistCriterion("Bar History", WARNING, note="Bar count unavailable"))
    else:
        cl.add(ChecklistCriterion("Bar History", CLEARED, actual_value=f"{bars} bars"))

    # Required fields check (path-specific)
    roe = _pct(_safe(metrics, "return_on_equity_fy") or _safe(metrics, "roe"))
    if roe is None and path != "BANK":  # BANK uses ROA as primary
        cl.add(ChecklistCriterion("ROE (Required)", FAILED,
                                   note="ROE is a required field — absent data → DATA_ABSENT",
                                   exclusion_code="DATA_ABSENT"))
    elif roe is not None:
        cl.add(ChecklistCriterion("ROE", CLEARED, actual_value=f"{roe*100:.1f}%"))

    roa = _pct(_safe(metrics, "return_on_assets_fq") or _safe(metrics, "roa"))
    if path in ("BANK", "NBFC_HFC", "INSURANCE"):
        if roa is None:
            cl.add(ChecklistCriterion("ROA (Required — Financial)", FAILED,
                                       note="ROA required for financial stocks",
                                       exclusion_code="DATA_ABSENT"))
        else:
            cl.add(ChecklistCriterion("ROA", CLEARED, actual_value=f"{roa*100:.2f}%"))

    # ------------------------------------------------------------------
    # STAGE 6: Junk gates (path-specific)
    # ------------------------------------------------------------------
    if path == "BANK":
        _add_bank_junk_gates(cl, metrics, roa)
    elif path in ("NBFC_HFC",):
        _add_nbfc_junk_gates(cl, metrics, roa)
    else:
        _add_nonfin_junk_gates(cl, metrics, roe, path=path)

    return cl


def _add_nonfin_junk_gates(cl: UniverseChecklist, metrics: dict, roe, path: str = "") -> None:
    """Adds non-financial junk gates to the checklist."""
    if path in {"BANK", "NBFC_HFC", "INSURANCE", "AMC"}:
        raise RuntimeError(
            f"_add_nonfin_junk_gates() received financial path={path}; "
            "sector routing violation"
        )

    # OPM gate
    opm = _pct(_safe(metrics, "operating_margin_ttm") or _safe(metrics, "opm"))
    if opm is not None:
        if opm < 0:
            cl.add(ChecklistCriterion("OPM (Junk Gate)", FAILED,
                                       actual_value=f"{opm*100:.1f}%", threshold="≥ 0%",
                                       note="Negative operating margin",
                                       exclusion_code="NEGATIVE_OPM"))
        elif opm < 0.05:
            cl.add(ChecklistCriterion("OPM", WARNING,
                                       actual_value=f"{opm*100:.1f}%", threshold="≥ 5%",
                                       note="OPM below 5% (quality penalty, not hard block for mega-caps)"))
        else:
            cl.add(ChecklistCriterion("OPM", CLEARED, actual_value=f"{opm*100:.1f}%"))
    else:
        cl.add(ChecklistCriterion("OPM", WARNING, note="Operating margin unavailable (OPTIONAL)"))

    # D/E gate
    de = _safe(metrics, "debt_equity") or _safe(metrics, "debt_to_equity")
    if de is not None:
        try:
            de_f = float(de)
            if de_f > 1.5:
                cl.add(ChecklistCriterion("D/E (Junk Gate)", FAILED,
                                           actual_value=f"{de_f:.2f}", threshold="≤ 1.5",
                                           exclusion_code="EXCESS_LEVERAGE"))
            else:
                cl.add(ChecklistCriterion("D/E", CLEARED, actual_value=f"{de_f:.2f}", threshold="≤ 1.5"))
        except (TypeError, ValueError):
            cl.add(ChecklistCriterion("D/E", WARNING, note="D/E parse error"))
    else:
        cl.add(ChecklistCriterion("D/E", WARNING, note="D/E data unavailable (OPTIONAL)"))

    # CFO/PAT gate
    cfo_pat = _safe(metrics, "cfo_pat_ratio")
    if cfo_pat is not None:
        try:
            cp = float(cfo_pat)
            if cp < 0.5:
                cl.add(ChecklistCriterion("CFO/PAT (Junk Gate)", FAILED,
                                           actual_value=f"{cp:.2f}", threshold="≥ 0.50",
                                           note="Poor earnings quality",
                                           exclusion_code="EARNINGS_QUALITY"))
            else:
                cl.add(ChecklistCriterion("CFO/PAT", CLEARED, actual_value=f"{cp:.2f}",
                                           threshold="≥ 0.50"))
        except (TypeError, ValueError):
            cl.add(ChecklistCriterion("CFO/PAT", WARNING, note="CFO/PAT parse error"))
    else:
        cl.add(ChecklistCriterion("CFO/PAT", WARNING, note="CFO/PAT unavailable (OPTIONAL)"))

    # Forensic gate
    forensic = _safe(metrics, "forensic_flags") or _safe(metrics, "forensic_risk")
    if forensic is not None:
        try:
            fv = int(forensic)
            if fv >= 2:
                cl.add(ChecklistCriterion("Forensic Flags (Junk Gate)", FAILED,
                                           actual_value=fv, threshold="< 2",
                                           exclusion_code="FORENSIC_FLAGS"))
            elif fv == 1:
                cl.add(ChecklistCriterion("Forensic Flags", WARNING, actual_value=fv,
                                           note="1 forensic flag — quality penalty applied"))
            else:
                cl.add(ChecklistCriterion("Forensic Flags", CLEARED, actual_value=fv))
        except (TypeError, ValueError):
            cl.add(ChecklistCriterion("Forensic Flags", WARNING, note="Forensic data non-numeric"))
    else:
        cl.add(ChecklistCriterion("Forensic Flags", WARNING, note="Forensic data unavailable (OPTIONAL)"))

    # EPS / ROE — turnaround exception check
    eps = _safe(metrics, "earnings_per_share_diluted_fy") or _safe(metrics, "eps_basic_ttm")
    yoy_profit = _pct(_safe(metrics, "earnings_per_share_diluted_yoy_growth_ttm") or _safe(metrics, "yoy_profit"))
    yoy_sales  = _pct(_safe(metrics, "total_revenue_yoy_growth_ttm") or _safe(metrics, "yoy_revenue"))

    is_turnaround = False
    if roe is not None and roe < 0.05:
        if (yoy_profit is not None and yoy_profit > 0
                and yoy_sales is not None and yoy_sales > 0
                and cfo_pat is not None):
            try:
                if float(cfo_pat) > 0:
                    is_turnaround = True
                    cl.turnaround = True
                    cl.add(ChecklistCriterion("ROE / Turnaround Gate", WARNING,
                                               actual_value=f"{roe*100:.1f}%",
                                               note="ROE < 5% but TURNAROUND exception granted "
                                                    "(YoY profit > 0, YoY sales > 0, CFO/PAT > 0). "
                                                    "Score capped at 50.",
                                               exclusion_code=""))
            except (TypeError, ValueError):
                pass

        if not is_turnaround:
            cl.add(ChecklistCriterion("ROE (Junk Gate)", FAILED,
                                       actual_value=f"{roe*100:.1f}%" if roe else "N/A",
                                       threshold="≥ 5%",
                                       note="ROE below 5% with no turnaround evidence",
                                       exclusion_code="WEAK_ROE"))
    elif roe is not None:
        cl.add(ChecklistCriterion("ROE", CLEARED, actual_value=f"{roe*100:.1f}%", threshold="≥ 5%"))


def _add_bank_junk_gates(cl: UniverseChecklist, metrics: dict, roa) -> None:
    """Adds BANK-specific junk gates."""
    if roa is not None and roa < 0.008:
        cl.add(ChecklistCriterion("ROA (Bank Junk Gate)", FAILED,
                                   actual_value=f"{roa*100:.2f}%", threshold="≥ 0.8%",
                                   exclusion_code="WEAK_ROA"))
    elif roa is not None:
        cl.add(ChecklistCriterion("ROA", CLEARED, actual_value=f"{roa*100:.2f}%", threshold="≥ 0.8%"))

    gnpa = _pct(_safe(metrics, "gnpa") or _safe(metrics, "gross_npa_ratio"))
    if gnpa is not None:
        if gnpa > 0.07:
            cl.add(ChecklistCriterion("GNPA (Bank Junk Gate)", FAILED,
                                       actual_value=f"{gnpa*100:.1f}%", threshold="≤ 7%",
                                       exclusion_code="HIGH_GNPA"))
        else:
            cl.add(ChecklistCriterion("GNPA", CLEARED, actual_value=f"{gnpa*100:.1f}%", threshold="≤ 7%"))
    else:
        cl.add(ChecklistCriterion("GNPA", WARNING, note="GNPA data unavailable"))

    forensic = _safe(metrics, "forensic_flags")
    if forensic is not None:
        try:
            if int(forensic) >= 2:
                cl.add(ChecklistCriterion("Forensic Flags (Junk Gate)", FAILED,
                                           actual_value=int(forensic), threshold="< 2",
                                           exclusion_code="FORENSIC_FLAGS"))
            else:
                cl.add(ChecklistCriterion("Forensic Flags", CLEARED, actual_value=int(forensic)))
        except (TypeError, ValueError):
            pass


def _add_nbfc_junk_gates(cl: UniverseChecklist, metrics: dict, roa) -> None:
    """Adds NBFC_HFC junk gates (similar to bank with slightly different thresholds)."""
    if roa is not None and roa < 0.008:
        cl.add(ChecklistCriterion("ROA (NBFC Junk Gate)", FAILED,
                                   actual_value=f"{roa*100:.2f}%", threshold="≥ 0.8%",
                                   exclusion_code="WEAK_ROA"))
    elif roa is not None:
        cl.add(ChecklistCriterion("ROA", CLEARED, actual_value=f"{roa*100:.2f}%"))

    gnpa = _pct(_safe(metrics, "gnpa") or _safe(metrics, "gross_npa_ratio"))
    if gnpa is not None:
        if gnpa > 0.07:
            cl.add(ChecklistCriterion("GNPA (NBFC Junk Gate)", FAILED,
                                       actual_value=f"{gnpa*100:.1f}%", threshold="≤ 7%",
                                       exclusion_code="HIGH_GNPA"))
        else:
            cl.add(ChecklistCriterion("GNPA", CLEARED, actual_value=f"{gnpa*100:.1f}%"))

    forensic = _safe(metrics, "forensic_flags")
    if forensic is not None:
        try:
            if int(forensic) >= 2:
                cl.add(ChecklistCriterion("Forensic Flags (Junk Gate)", FAILED,
                                           actual_value=int(forensic), threshold="< 2",
                                           exclusion_code="FORENSIC_FLAGS"))
            else:
                cl.add(ChecklistCriterion("Forensic Flags", CLEARED, actual_value=int(forensic)))
        except (TypeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# determine_eligibility()
# ---------------------------------------------------------------------------

def determine_eligibility(
    checklist:          UniverseChecklist,
    score_breakdown:    ScoreBreakdown,
    institutional_ctx:  dict,
    corporate_action:   Optional[CorporateActionRecord] = None,
) -> EligibilityDecision:
    """
    Maps checklist + score → EligibilityDecision.

    Decision hierarchy (in order):
      1. Any FAILED criterion → EXCLUDED
      2. Score ≥ ELITE_SCORE_THRESHOLD → ELITE (with quality_tier)
      3. NEAR_QUALIFIED check: SCORE_BAND or SINGLE_GAP criterion
      4. All others → EXCLUDED (QUALITY_FAIL)

    Returns an EligibilityDecision with fundamental_profile populated
    for ELITE and NEAR_QUALIFIED stocks.
    """
    total = score_breakdown.total

    # Apply turnaround score cap
    if checklist.turnaround:
        total = min(50, total)

    # Step 1: Any hard failure → EXCLUDED
    if checklist.has_hard_failure():
        return EligibilityDecision(
            universe_status=STATUS_EXCLUDED,
            exclusion_class=checklist.exclusion_class,
            primary_exclusion_code=checklist.primary_exclusion_code,
            secondary_exclusion_codes=checklist.secondary_exclusion_codes,
        )

    # Step 2: ELITE
    if total >= ELITE_SCORE_THRESHOLD and not checklist.turnaround:
        profile = _build_fundamental_profile(score_breakdown, institutional_ctx, checklist)
        return EligibilityDecision(
            universe_status=STATUS_ELITE,
            exclusion_class="",
            quality_tier=score_breakdown.quality_tier,
            fundamental_profile=profile,
        )

    # Step 3: NEAR_QUALIFIED (SCORE_BAND or SINGLE_GAP)
    nq_result = _check_near_qualified(total, score_breakdown, checklist)
    if nq_result is not None:
        mode, gap_factors, gap_count = nq_result
        profile = _build_fundamental_profile(score_breakdown, institutional_ctx, checklist)
        profile.quality_tier = ""  # NQ has no quality tier
        return EligibilityDecision(
            universe_status=STATUS_NEAR_QUALIFIED,
            exclusion_class="",
            near_qualified_mode=mode,
            gap_quality_factors=gap_factors,
            gap_count=gap_count,
            fundamental_profile=profile,
        )

    # Also admit turnaround-exception stocks as NEAR_QUALIFIED (score capped at 50)
    if checklist.turnaround and total >= NEAR_QUALIFIED_SCORE_MIN:
        profile = _build_fundamental_profile(score_breakdown, institutional_ctx, checklist)
        profile.quality_tier = ""
        return EligibilityDecision(
            universe_status=STATUS_NEAR_QUALIFIED,
            exclusion_class="",
            near_qualified_mode=NQ_MODE_SCORE_BAND,
            gap_quality_factors=["TURNAROUND"],
            gap_count=1,
            fundamental_profile=profile,
        )

    # Step 4: EXCLUDED (quality failure)
    return EligibilityDecision(
        universe_status=STATUS_EXCLUDED,
        exclusion_class=QUALITY_FAIL,
        primary_exclusion_code=checklist.primary_exclusion_code or "QUALITY_FAIL",
        secondary_exclusion_codes=checklist.secondary_exclusion_codes,
    )


def _check_near_qualified(
    total: int,
    score: ScoreBreakdown,
    checklist: UniverseChecklist,
) -> Optional[tuple[str, list, int]]:
    """
    Returns (mode, gap_factors, gap_count) if stock qualifies as NEAR_QUALIFIED,
    else None.

    SCORE_BAND: score in [35, 44] AND ≤ 2 quality factors actively scored zero.
    SINGLE_GAP: score ≥ 38 AND exactly 1 factor failed below threshold
                AND gap within GAP_TOLERANCE_BY_METRIC.
    """
    # Identify zero-scored quality factors from WARNING criteria
    zero_factors = [
        c.name for c in checklist.criteria
        if c.status == WARNING and c.exclusion_code == ""
    ]
    # Identify failed-below-threshold factors (those that scored 0 because metric is poor)
    below_threshold = [
        c for c in checklist.criteria
        if c.status == FAILED and c.exclusion_code not in (
            "BELOW_PRICE_FLOOR", "BELOW_MCAP_FLOOR", "LOW_LIQUIDITY", "LOW_VOLUME",
            "SHELL_RISK", "SURVEILLANCE_ASM", "SURVEILLANCE_GSM", "PROMOTER_BLACKLISTED",
            "INSUFFICIENT_HISTORY", "DATA_ABSENT", "DATA_CORRUPT", "STATISTICAL_ANOMALY",
            "FORENSIC_FLAGS",
        )
    ]

    # SCORE_BAND criterion
    if NEAR_QUALIFIED_SCORE_MIN <= total <= NEAR_QUALIFIED_SCORE_MAX:
        zero_count = len([c for c in checklist.criteria
                          if c.status == WARNING and "zero" in c.note.lower()])
        # Use warnings as a proxy for zero-scored factors (conservative: use len(below_threshold))
        weak_count = len(below_threshold)
        if weak_count <= 2:
            gap_factors = [c.exclusion_code or c.name for c in below_threshold]
            return NQ_MODE_SCORE_BAND, gap_factors, weak_count

    # SINGLE_GAP criterion
    if total >= NEAR_QUALIFIED_SINGLE_GAP_MIN_SCORE and len(below_threshold) == 1:
        failed_crit = below_threshold[0]
        metric_key = _map_criterion_to_metric_key(failed_crit.exclusion_code)
        tolerance = GAP_TOLERANCE_BY_METRIC.get(metric_key)
        if tolerance is not None and failed_crit.actual_value is not None and failed_crit.threshold is not None:
            gap_ok = _gap_within_tolerance(failed_crit.actual_value, failed_crit.threshold, tolerance)
            if gap_ok:
                return NQ_MODE_SINGLE_GAP, [failed_crit.exclusion_code or failed_crit.name], 1

    return None


def _map_criterion_to_metric_key(exclusion_code: str) -> str:
    """Maps exclusion_code to the GAP_TOLERANCE_BY_METRIC key."""
    mapping = {
        "WEAK_ROE":          "roe",
        "WEAK_ROCE":         "roce",
        "NEGATIVE_OPM":      "opm",
        "EXCESS_LEVERAGE":   "debt_equity",
        "EARNINGS_QUALITY":  "cfo_pat_ratio",
        "WEAK_FCF":          "fcf_margin",
        "WEAK_REVENUE":      "yoy_sales",
        "WEAK_PROFIT":       "yoy_profit",
        "WEAK_ROA":          "roa",
        "HIGH_GNPA":         "gnpa",
    }
    return mapping.get(exclusion_code, "")


def _gap_within_tolerance(actual, threshold, tolerance: float) -> bool:
    """
    Returns True if abs(actual - threshold) / abs(threshold) ≤ tolerance.
    Handles string-formatted values like '13.8%' and '15%'.
    """
    try:
        def _to_float(v) -> float:
            s = str(v).replace("%", "").replace("₹", "").replace(",", "").strip()
            return float(s)

        a = _to_float(actual)
        t = _to_float(threshold)
        if t == 0:
            return False
        relative_gap = abs(a - t) / abs(t)
        return relative_gap <= tolerance
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# _build_fundamental_profile()
# ---------------------------------------------------------------------------

def _build_fundamental_profile(
    score: ScoreBreakdown,
    institutional_ctx: dict,
    checklist: UniverseChecklist,
) -> FundamentalProfile:
    """
    Constructs the FundamentalProfile handoff object from scoring results.
    This is the frozen contract consumed by downstream scanners.
    """
    archetype = _classify_archetype(score, checklist)
    primary_strength, secondary_strengths = _identify_strengths(score, checklist)

    return FundamentalProfile(
        quality_tier           = score.quality_tier,
        primary_archetype      = archetype,
        primary_strength       = primary_strength,
        secondary_strengths    = secondary_strengths,
        business_quality       = score.business_quality,
        growth_quality         = score.growth_quality,
        valuation_context      = score.valuation_context,
        governance             = score.governance,
        institutional_interest = institutional_ctx.get("institutional_interest", "UNKNOWN"),
        data_confidence        = score.data_coverage.overall_data_confidence,
    )


def _classify_archetype(score: ScoreBreakdown, checklist: UniverseChecklist) -> str:
    """Classifies the primary investment archetype based on sub-score profile."""
    bq = score.business_quality
    gq = score.growth_quality
    vc = score.valuation_context
    gv = score.governance

    if bq >= 24 and gq >= 28 and gv >= 12:
        return "Long Term Compounder"
    if gq >= 30 and bq >= 18:
        return "High Momentum Grower"
    if bq >= 24 and vc >= 15:
        return "Value + Quality"
    if gv >= 13 and bq >= 20:
        return "Quality + Governance"
    if checklist.turnaround:
        return "Turnaround Candidate"
    if gq >= 25:
        return "Growth Play"
    return "Broad Market"


def _identify_strengths(
    score: ScoreBreakdown,
    checklist: UniverseChecklist,
) -> tuple[str, list]:
    """Identifies primary and secondary strengths from the sub-score breakdown."""
    strengths = []

    if score.business_quality >= 24:
        strengths.append("FCF + ROE + 5Y Track Record")
    elif score.business_quality >= 18:
        strengths.append("Solid Business Quality")

    if score.growth_quality >= 28:
        strengths.append("Strong Growth (YoY + QoQ)")
    elif score.growth_quality >= 20:
        strengths.append("Consistent Growth")

    if score.governance >= 13:
        strengths.append("Clean Governance")
    if score.valuation_context >= 15:
        strengths.append("Attractive Valuation")

    # Check for specific cleared criteria
    low_de = any(c.name.startswith("D/E") and c.status == CLEARED
                 and c.actual_value and "0." in str(c.actual_value)
                 for c in checklist.criteria)
    if low_de:
        strengths.append("Low D/E")

    primary = strengths[0] if strengths else "No Dominant Strength Identified"
    secondary = strengths[1:] if len(strengths) > 1 else []
    return primary, secondary


# ---------------------------------------------------------------------------
# format_eligibility_report()
# ---------------------------------------------------------------------------

def format_eligibility_report(
    checklist: UniverseChecklist,
    decision:  EligibilityDecision,
    score:     ScoreBreakdown,
) -> str:
    """
    Produces a human-readable eligibility report for the Admin dashboard and logs.
    Covers both admitted (ELITE, NEAR_QUALIFIED) and excluded stocks.
    """
    lines = [
        f"{checklist.symbol}",
        f"",
        f"UNIVERSE STATUS: {decision.universe_status}"
        + (f" (Tier {decision.quality_tier})" if decision.quality_tier else ""),
        f"Score: {score.total} / 100  |  Data Confidence: {score.data_coverage.overall_data_confidence}",
    ]

    if decision.universe_status == STATUS_NEAR_QUALIFIED:
        lines.append(f"NEAR_QUALIFIED MODE: {decision.near_qualified_mode}")
        if decision.gap_quality_factors:
            lines.append(f"Gap factors: {', '.join(decision.gap_quality_factors)}")

    lines.append("")
    lines.append("CHECKLIST:")
    for crit in checklist.criteria:
        icon = {"CLEARED": "✅", "FAILED": "✗", "WARNING": "⚠", "NOT_APPLICABLE": "—"}.get(crit.status, "?")
        val_str = f"  ({crit.actual_value})" if crit.actual_value else ""
        note_str = f"  ← {crit.note}" if crit.note else ""
        lines.append(f"  {icon} {crit.name}{val_str}{note_str}")

    if decision.universe_status == STATUS_EXCLUDED:
        lines.append("")
        lines.append(f"PRIMARY EXCLUSION: {decision.primary_exclusion_code}")
        lines.append(f"EXCLUSION CLASS: {decision.exclusion_class}")
        if decision.secondary_exclusion_codes:
            lines.append(f"SECONDARY: {', '.join(decision.secondary_exclusion_codes)}")

    if decision.fundamental_profile:
        fp = decision.fundamental_profile
        lines.extend([
            "",
            f"PRIMARY STRENGTH:    {fp.primary_strength}",
            f"SECONDARY STRENGTHS: {', '.join(fp.secondary_strengths) or 'None'}",
            f"PRIMARY ARCHETYPE:   {fp.primary_archetype}",
            f"INSTITUTIONAL CONTEXT: {fp.institutional_interest}",
            f"QUALITY TIER: {fp.quality_tier or 'N/A'}",
            "",
            "FUNDAMENTAL PROFILE:",
            f"  business_quality:  {fp.business_quality} / 30",
            f"  growth_quality:    {fp.growth_quality} / 35",
            f"  valuation_context: {fp.valuation_context} / 20",
            f"  governance:        {fp.governance} / 15",
            f"  data_confidence:   {fp.data_confidence}",
        ])

    if checklist.turnaround:
        lines.append("")
        lines.append("⚠ TURNAROUND exception applied — score capped at 50.")

    if checklist.corporate_action:
        ca = checklist.corporate_action
        lines.append(f"⚠ CORPORATE ACTION: {ca.corporate_action_type} ({ca.effective_date}) "
                     f"— affected metrics suppressed in score: {ca.affected_metrics}")

    return "\n".join(lines)
