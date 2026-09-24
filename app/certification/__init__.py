# app/certification/__init__.py
"""
Exact Production Replay & Deterministic Backtest Certification Package.
"""
from app.certification.models import (
    ProductionDecisionRecord,
    FrozenDataSnapshot,
    GateAuditResult,
    FieldDelta,
    DifferentialReport,
    Tolerances
)
from app.certification.difference_engine import DifferenceEngine
from app.certification.provenance import (
    get_git_commit,
    get_file_hash,
    get_config_hash,
    get_dataframe_hash
)
from app.certification.point_in_time import validate_point_in_time
from app.certification.data_auditor import ParquetAuditor
from app.certification.replay import ProductionReplayOrchestrator

__all__ = [
    "ProductionDecisionRecord",
    "FrozenDataSnapshot",
    "GateAuditResult",
    "FieldDelta",
    "DifferentialReport",
    "Tolerances",
    "DifferenceEngine",
    "get_git_commit",
    "get_file_hash",
    "get_config_hash",
    "get_dataframe_hash",
    "validate_point_in_time",
    "ParquetAuditor",
    "ProductionReplayOrchestrator"
]
