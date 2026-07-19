from dataclasses import dataclass, field
from typing import List, Tuple

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
    schema_failures: List[str] = field(default_factory=list)
    business_failures: List[str] = field(default_factory=list)
    historical_failures: List[str] = field(default_factory=list)
    
    # Granular details for scoring and diagnostics
    warnings: List[str] = field(default_factory=list)
    metrics: ValidationMetrics = field(default_factory=ValidationMetrics)
    
    @property
    def is_valid(self) -> bool:
        """A dataset is only valid if schema, business rules, and historical invariants pass."""
        return not self.schema_failures and not self.business_failures and not self.historical_failures
        
    @property
    def critical_failures(self) -> Tuple[str, ...]:
        """Flattened list of all critical failures."""
        return tuple(self.schema_failures + self.business_failures + self.historical_failures)
