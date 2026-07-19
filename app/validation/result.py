from dataclasses import dataclass, field
from typing import List, Tuple, TypeVar, Generic, Any
from enum import Enum, auto
from .codes import ValidationFailure, Severity

@dataclass
class ValidationMetrics:
    """Strongly typed metrics container for the ScoreCalculator."""
    row_count: int = 0
    missing_pct: float = 0.0
    duplicate_rows: int = 0
    stale_days: int = 0
    invalid_prices: int = 0
    monotonic: bool = True

@dataclass
class ValidationResult:
    """
    Intermediate state holding the raw output of a validator's execution.
    This is passed to the ScoreCalculator before the final report is generated.
    """
    schema_failures: List[ValidationFailure] = field(default_factory=list)
    business_failures: List[ValidationFailure] = field(default_factory=list)
    historical_failures: List[ValidationFailure] = field(default_factory=list)
    
    # Granular details for scoring and diagnostics
    warnings: List[str] = field(default_factory=list)
    metrics: ValidationMetrics = field(default_factory=ValidationMetrics)
    
    @property
    def is_valid(self) -> bool:
        """A dataset is only valid if schema, business rules, and historical invariants pass."""
        return not self.schema_failures and not self.business_failures and not self.historical_failures
        
    @property
    def has_failures(self) -> bool:
        return len(self.schema_failures) > 0 or len(self.business_failures) > 0 or len(self.historical_failures) > 0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
        
    def is_degraded(self, score: float, threshold: float = 90.0) -> bool:
        """A valid dataset is degraded if it has warnings or falls below the quality threshold."""
        return self.is_valid and (self.has_warnings or score < threshold)
        
    @property
    def critical_failures(self) -> Tuple[str, ...]:
        """Flattened list of all critical failures as formatted strings."""
        return tuple(str(f) for f in self.schema_failures + self.business_failures + self.historical_failures)

class ValidationStatus(Enum):
    VALID = auto()
    DEGRADED = auto()
    INVALID = auto()
    STALE = auto()

T = TypeVar('T')

@dataclass(frozen=True)
class ValidatedDataset(Generic[T]):
    """
    Standardized payload for consumers after the validation lifecycle completes.
    Bundles the data with its definitive quality markers.
    """
    data: T
    result: ValidationResult
    score: float
    status: ValidationStatus
