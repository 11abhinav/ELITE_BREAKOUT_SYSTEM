# app/certification/models.py
"""
Data models and schemas for Exact Production Replay & Deterministic Backtest Certification.
Supports multi-scanner architectures, dual replay modes (PRODUCTION_REPLAY vs CLEAN_HISTORICAL_REPLAY),
lifecycle tracing, and data health tracking.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from enum import Enum
from typing import Any, Dict, List, Optional
import json


class ReplayMode(str, Enum):
    """
    Mode 1: PRODUCTION_REPLAY -> 'Can we reproduce exactly what production did using exact production snapshot?'
    Mode 2: CLEAN_HISTORICAL_REPLAY -> 'What would production have done if historical data was completely sanitized?'
    """
    PRODUCTION_REPLAY = "PRODUCTION_REPLAY"
    CLEAN_HISTORICAL_REPLAY = "CLEAN_HISTORICAL_REPLAY"


class ScannerCertificationStatus(str, Enum):
    """Overall certification grading status."""
    NOT_CERTIFIED = "NOT_CERTIFIED"
    PARTIALLY_CERTIFIED = "PARTIALLY_CERTIFIED"
    FULLY_CERTIFIED = "FULLY_CERTIFIED"


class ScannerType(str, Enum):
    """Registered production scanner modules."""
    EOD_BREAKOUT = "EOD_BREAKOUT"
    MULTITF_15M = "MULTITF_15M"
    MULTITF_5M = "MULTITF_5M"
    SHORT_COVERING_EOD = "SHORT_COVERING_EOD"
    SHORT_COVERING_5M = "SHORT_COVERING_5M"
    REVERSAL = "REVERSAL"
    PULLBACK = "PULLBACK"
    TECHNICAL = "TECHNICAL"
    ACCUMULATION_VCP = "ACCUMULATION_VCP"
    INSTITUTIONAL_ACCUMULATION = "INSTITUTIONAL_ACCUMULATION"
    WEALTH = "WEALTH"
    MULTIBAGGER = "MULTIBAGGER"
    DAILY_BUILDER = "DAILY_BUILDER"


@dataclass
class Tolerances:
    """Strict evaluation tolerances for intermediate calculations."""
    price: float = 0.01
    percentage: float = 0.01
    ratio: float = 0.0001
    score: float = 0.01
    # Gate results, rejection reasons, and decisions MUST be EXACT (zero tolerance)


@dataclass
class FrozenDataSnapshot:
    """Captures exact input data footprint for point-in-time provenance."""
    symbol: str
    row_count: int
    start_date: str
    end_date: str
    sha256_hash: str
    source_file: Optional[str] = None
    provider: str = "CACHE"
    delivery_pct: Optional[float] = None
    delivery_source: Optional[str] = None
    market_regime: Optional[str] = None
    evaluated_at: Optional[str] = None
    timeframe: str = "1d"
    raw_data_json: Optional[str] = None  # Exact serialized input rows if captured

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateAuditResult:
    """Audit of an individual gate evaluation."""
    name: str
    passed: bool
    status: str  # PASS / FAIL
    actual: Optional[float] = None
    threshold: Optional[float] = None
    operator: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MultiTFLifecycleTrace:
    """Detailed lifecycle progression for multi-timeframe scanners."""
    symbol: str
    h15_timestamp: Optional[str] = None
    h15_candidate_created: bool = False
    h15_trigger_level: Optional[float] = None
    h15_validated: bool = False
    m5_polling_states: List[Dict[str, Any]] = field(default_factory=list)
    m5_confirmed: bool = False
    entry_ready: bool = False
    stop_loss: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    final_alert_fired: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShortCoveringDataHealth:
    """Data health audit state for Short Covering F&O scanner."""
    symbol: str
    in_fo_universe: bool = True
    futures_contract: Optional[str] = None
    oi_source: Optional[str] = None
    oi_available: bool = True
    price_source: Optional[str] = None
    m5_data_source: Optional[str] = None
    health_status: str = "HEALTHY"  # HEALTHY / DATA_INSUFFICIENT / BLOCKED
    provider_fallback_used: bool = False
    oi_change_pct: Optional[float] = None
    price_change_pct: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProductionDecisionRecord:
    """
    Standardized, immutable record of a production (or reference) scanner evaluation.
    Contains metadata, exact data snapshot, indicators, gates, score, and final decision.
    """
    symbol: str
    scanner_name: str
    evaluation_date: str  # YYYY-MM-DD
    evaluation_timestamp: str  # ISO / IST formatted
    run_id: str
    git_commit: str
    scanner_file_hash: str
    config_hash: str
    effective_config: Dict[str, Any]
    market_regime: str
    data_snapshot: FrozenDataSnapshot
    indicators: Dict[str, Any]
    gate_results: Dict[str, GateAuditResult]
    score_breakdown: Dict[str, Any]
    final_score: float
    terminal_decision: str  # SELECTED / REJECTED / CANDIDATE
    alert_generated: bool
    rejection_reason: Optional[str] = None
    primary_gate: Optional[str] = None
    replay_mode: str = ReplayMode.PRODUCTION_REPLAY.value
    multitf_trace: Optional[MultiTFLifecycleTrace] = None
    short_covering_health: Optional[ShortCoveringDataHealth] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "ProductionDecisionRecord":
        snap_data = data.get("data_snapshot", {})
        data_snapshot = FrozenDataSnapshot(**snap_data) if isinstance(snap_data, dict) else snap_data
        
        gates_raw = data.get("gate_results", {})
        gate_results = {}
        for g_name, g_val in gates_raw.items():
            if isinstance(g_val, dict):
                gate_results[g_name] = GateAuditResult(
                    name=g_name,
                    passed=bool(g_val.get("passed", False)),
                    status=g_val.get("status", "FAIL"),
                    actual=g_val.get("actual"),
                    threshold=g_val.get("threshold"),
                    operator=g_val.get("operator"),
                    reason=g_val.get("reason"),
                )
            elif isinstance(g_val, GateAuditResult):
                gate_results[g_name] = g_val

        mtf_data = data.get("multitf_trace")
        multitf_trace = MultiTFLifecycleTrace(**mtf_data) if isinstance(mtf_data, dict) else mtf_data

        sc_data = data.get("short_covering_health")
        short_covering_health = ShortCoveringDataHealth(**sc_data) if isinstance(sc_data, dict) else sc_data

        return cls(
            symbol=data["symbol"],
            scanner_name=data["scanner_name"],
            evaluation_date=data["evaluation_date"],
            evaluation_timestamp=data["evaluation_timestamp"],
            run_id=data.get("run_id", "unknown"),
            git_commit=data.get("git_commit", "unknown"),
            scanner_file_hash=data.get("scanner_file_hash", "unknown"),
            config_hash=data.get("config_hash", "unknown"),
            effective_config=data.get("effective_config", {}),
            market_regime=data.get("market_regime", "NEUTRAL"),
            data_snapshot=data_snapshot,
            indicators=data.get("indicators", {}),
            gate_results=gate_results,
            score_breakdown=data.get("score_breakdown", {}),
            final_score=float(data.get("final_score", 0.0)),
            terminal_decision=data.get("terminal_decision", "REJECTED"),
            alert_generated=bool(data.get("alert_generated", False)),
            rejection_reason=data.get("rejection_reason"),
            primary_gate=data.get("primary_gate"),
            replay_mode=data.get("replay_mode", ReplayMode.PRODUCTION_REPLAY.value),
            multitf_trace=multitf_trace,
            short_covering_health=short_covering_health
        )


@dataclass
class FieldDelta:
    """Represents a single field comparison between production and replay."""
    field_name: str
    prod_value: Any
    replay_value: Any
    delta: Optional[float] = None
    tolerance: Optional[float] = None
    matches: bool = True
    category: str = "INDICATOR"  # METADATA, CONFIG, DATA, INDICATOR, GATE, SCORE, DECISION, LIFECYCLE, HEALTH


@dataclass
class DifferentialReport:
    """Comprehensive report produced by the Difference Engine."""
    symbol: str
    scanner_name: str
    evaluation_date: str
    replay_mode: str = "PRODUCTION_REPLAY"
    certified: bool = False
    first_divergence: Optional[str] = None
    root_input_divergence: Optional[str] = None
    downstream_impact: List[str] = field(default_factory=list)
    field_comparisons: List[FieldDelta] = field(default_factory=list)
    code_version_match: bool = True
    config_match: bool = True
    data_match: bool = True
    indicators_match: bool = True
    gates_match: bool = True
    decision_match: bool = True
    point_in_time_valid: bool = True
    lifecycle_match: bool = True
    health_match: bool = True
    primary_mismatch_category: Optional[str] = None
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScannerMatrixRow:
    """Single row in the Master Scanner Certification Matrix."""
    scanner: str
    mode: str
    production_cases: int
    replay_cases: int
    trace_match_pct: float
    decision_match_pct: float
    status: str  # CERTIFIED / PARTIAL / NOT_CERTIFIED

    def to_dict(self) -> dict:
        return asdict(self)
