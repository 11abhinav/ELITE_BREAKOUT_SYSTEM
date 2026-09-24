# app/certification/models.py
"""
Data models and schemas for Exact Production Replay & Deterministic Backtest Certification.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any, Dict, List, Optional
import json


@dataclass
class Tolerances:
    """Strict evaluation tolerances for intermediate calculations."""
    price: float = 0.01
    percentage: float = 0.01
    ratio: float = 0.0001
    score: float = 0.01
    # Gate results, rejection reasons, and decisions MUST be EXACT (no tolerance)


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
    category: str = "INDICATOR"  # METADATA, CONFIG, DATA, INDICATOR, GATE, SCORE, DECISION


@dataclass
class DifferentialReport:
    """Comprehensive report produced by the Difference Engine."""
    symbol: str
    scanner_name: str
    evaluation_date: str
    certified: bool
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
    summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
