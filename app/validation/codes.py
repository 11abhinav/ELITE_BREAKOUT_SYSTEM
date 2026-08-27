from enum import Enum
from dataclasses import dataclass

class Severity(Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"

class FailureCode(Enum):
    SCH001 = "Missing required column"
    SCH002 = "Invalid column type"
    BUS001 = "Business rule violation"
    BUS002 = "Invalid value range"
    HIS001 = "Historical shrink"
    HIS002 = "Timestamp rollback"
    HIS003 = "Duplicate primary key"
    QLT001 = "Excessive missing values"
    SRC001 = "Provider unavailable"
    HIS004 = "Insufficient history (< 50 bars)"
    SYM001 = "Symbol not found"
    RAT001 = "Rate limited by provider"
    STL001 = "Stale data payload"

@dataclass(frozen=True)
class ValidationFailure:
    """
    A structured record of a validation failure.
    Includes the standard failure code, its severity, and an optional detailed message.
    """
    code: FailureCode
    severity: Severity
    message: str
    
    def __str__(self) -> str:
        return f"[{self.severity.value}][{self.code.name}] {self.message}"
