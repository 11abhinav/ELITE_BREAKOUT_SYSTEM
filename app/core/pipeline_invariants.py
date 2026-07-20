from typing import Optional, Dict, List
from core.events import DomainEvent
from core.invariants import Invariant, InvariantResult, Severity

EXPECTED_EVENT_ORDER = [
    "ValidationCompleted",
    "IndicatorsCalculated",
    "ScannerCompleted",
    "ScoresCalculated",
    "CandidateSelected",
    "SLTargetComputed",
    "AlertCreated",
    "PipelineCompleted"
]

class PipelineOrderingInvariant(Invariant):
    id = "INV-PIPE-001"
    business_rule = "Pipeline events must fire in canonical order"
    severity = Severity.CRITICAL
    snapshot = "N/A"
    owner = "Architecture"
    
    def __init__(self):
        # We need to track state per symbol
        self._state: Dict[str, int] = {}
        
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        event_name = type(event).__name__
        if event_name not in EXPECTED_EVENT_ORDER:
            return None
            
        data = getattr(event, "payload", {})
        symbol = data.get("symbol", "UNKNOWN")
        
        current_idx = EXPECTED_EVENT_ORDER.index(event_name)
        last_idx = self._state.get(symbol, -1)
        
        # Check ordering
        if current_idx <= last_idx and event_name != "PipelineCompleted":
            # Event occurred out of order!
            msg = f"Event ordering violation. Received {event_name} but already processed index {last_idx}"
            return InvariantResult("FAIL", self.severity, self.id, msg, symbol)
            
        self._state[symbol] = current_idx
        
        # Cleanup state when pipeline completes for the symbol
        if event_name == "PipelineCompleted":
            if symbol in self._state:
                del self._state[symbol]
                
        return InvariantResult("PASS", self.severity, self.id, f"Valid order: {event_name}", symbol)

class PipelineCoverageInvariant(Invariant):
    id = "INV-PIPE-002"
    business_rule = "Every executed stage must produce a valid snapshot"
    severity = Severity.CRITICAL
    snapshot = "N/A"
    owner = "Architecture"
    
    def __init__(self, snapshot_collector=None):
        self.snapshot_collector = snapshot_collector
        
    def evaluate(self, event: DomainEvent) -> Optional[InvariantResult]:
        if type(event).__name__ == "PipelineCompleted":
            data = getattr(event, "payload", {})
            symbol = data.get("symbol", "UNKNOWN")
            status = data.get("status", "SUCCESS")
            
            # If the pipeline did not complete successfully, we do not assert full coverage
            if status != "SUCCESS":
                return InvariantResult("PASS", Severity.INFO, self.id, f"Skipping coverage check for failed/rejected symbol {symbol}", symbol)
                
            if self.snapshot_collector:
                try:
                    self.snapshot_collector.assert_coverage()
                    return InvariantResult("PASS", self.severity, self.id, "All snapshots successfully collected", symbol)
                except Exception as e:
                    return InvariantResult("FAIL", self.severity, self.id, f"Snapshot coverage assertion failed: {str(e)}", symbol)
            else:
                return InvariantResult("PASS", Severity.INFO, self.id, "No SnapshotCollector bound, skipping coverage check", symbol)
                
        return None
