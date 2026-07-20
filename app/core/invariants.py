from abc import ABC, abstractmethod
from typing import Any, List, Optional
from enum import Enum
from dataclasses import dataclass
from core.events import DomainEvent

class Severity(Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"

class InvariantViolation(Exception):
    """Raised when a business invariant is violated and the policy dictates a halt."""
    def __init__(self, invariant_id: str, severity: Severity, symbol: str, message: str):
        self.invariant_id = invariant_id
        self.severity = severity
        self.symbol = symbol
        self.message = message
        super().__init__(f"[{severity.value}] {invariant_id} for {symbol}: {message}")

@dataclass
class InvariantResult:
    status: str # "PASS" or "FAIL"
    severity: Severity
    invariant_id: str
    message: str
    symbol: str = "UNKNOWN"

class Invariant(ABC):
    """Base class for all executable business invariants."""
    id: str = "INV-UNKNOWN"
    business_rule: str = "UNKNOWN"
    severity: Severity = Severity.CRITICAL
    owner: str = "Architecture"
    snapshot: str = "N/A"
    tests: List[str] = []
    
    @abstractmethod
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        """Evaluate the invariant against the event. Returns an InvariantResult or None if skipped."""
        pass

# --- CRITICAL INVARIANTS (Output Validation) ---

class ScoreAboveThresholdInvariant(Invariant):
    id = "INV-SCORE-001"
    business_rule = "Score must be at least 50.0"
    severity = Severity.CRITICAL
    snapshot = "05_candidate"
    
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        if type(event).__name__ == "CandidateSelected":
            data = event.payload
            symbol = data.get("symbol", "UNKNOWN")
            score = data.get("total_score", 0.0)
            if score < 50.0:
                return InvariantResult("FAIL", self.severity, self.id, f"Candidate selected with insufficient score: {score}", symbol)
            return InvariantResult("PASS", self.severity, self.id, f"Score {score} >= 50.0", symbol)
        return None

class StopBelowEntryInvariant(Invariant):
    id = "INV-SL-001"
    business_rule = "Stop Loss must be strictly below Entry"
    severity = Severity.CRITICAL
    snapshot = "06_sl_target"
    
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        if type(event).__name__ == "SLTargetComputed":
            data = event.payload
            symbol = data.get("symbol", "UNKNOWN")
            entry = data.get("entry_price")
            sl = data.get("stop_loss")
            
            if entry is not None and sl is not None:
                if sl >= entry:
                    return InvariantResult("FAIL", self.severity, self.id, f"Stop Loss ({sl}) must be strictly below Entry ({entry})", symbol)
                return InvariantResult("PASS", self.severity, self.id, "SL is valid", symbol)
        return None

class TargetAboveEntryInvariant(Invariant):
    id = "INV-TGT-001"
    business_rule = "Target 1 must be strictly above Entry"
    severity = Severity.CRITICAL
    snapshot = "06_sl_target"
    
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        if type(event).__name__ == "SLTargetComputed":
            data = event.payload
            symbol = data.get("symbol", "UNKNOWN")
            entry = data.get("entry_price")
            target = data.get("target_1")
            
            if entry is not None and target is not None:
                if target <= entry:
                    return InvariantResult("FAIL", self.severity, self.id, f"Target 1 ({target}) must be strictly above Entry ({entry})", symbol)
                return InvariantResult("PASS", self.severity, self.id, "Target is valid", symbol)
        return None

class ValidOriginatingCandidateInvariant(Invariant):
    id = "INV-ALRT-001"
    business_rule = "Alert payload must contain originating candidate signals"
    severity = Severity.CRITICAL
    snapshot = "07_alert"
    
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        if type(event).__name__ == "AlertCreated":
            data = event.payload
            symbol = data.get("symbol", "UNKNOWN")
            signals = data.get("signals", {})
            if not signals:
                return InvariantResult("FAIL", self.severity, self.id, "Alert payload missing originating candidate signals", symbol)
            return InvariantResult("PASS", self.severity, self.id, "Signals present", symbol)
        return None

# --- WARNING INVARIANTS (Input Quality) ---

class DataSufficientInvariant(Invariant):
    id = "INV-DATA-001"
    business_rule = "Data quality must not fail due to missing/empty inputs"
    severity = Severity.WARNING
    snapshot = "01_validation"
    
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        if type(event).__name__ == "ValidationCompleted":
            data = event.payload
            symbol = data.get("symbol", "UNKNOWN")
            status = data.get("status")
            if status == "REJECTED":
                reason = data.get("rejection_reason", "")
                if "missing" in reason.lower() or "empty" in reason.lower():
                    return InvariantResult("FAIL", self.severity, self.id, f"Data validation failed due to missing/empty data: {reason}", symbol)
            return InvariantResult("PASS", self.severity, self.id, "Data sufficiently complete", symbol)
        return None
