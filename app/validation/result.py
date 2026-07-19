from dataclasses import dataclass, field
from typing import List, Dict

@dataclass
class ValidationResult:
    """
    Intermediate state holding the raw output of a validator's execution.
    This is passed to the ScoreCalculator before the final report is generated.
    """
    schema_pass: bool = False
    business_pass: bool = False
    historical_pass: bool = False
    
    # Granular details for scoring and diagnostics
    warnings: List[str] = field(default_factory=list)
    critical_failures: List[str] = field(default_factory=list)
    metrics: Dict = field(default_factory=dict)
    
    @property
    def is_valid(self) -> bool:
        """A dataset is only valid if schema, business rules, and historical invariants pass."""
        return self.schema_pass and self.business_pass and self.historical_pass
