from typing import List, Type, Dict
from core.events import EventSubscriber, DomainEvent
from core.invariants import (
    Invariant, InvariantViolation,
    ScoreAboveThresholdInvariant, StopBelowEntryInvariant,
    TargetAboveEntryInvariant, ValidOriginatingCandidateInvariant,
    DataSufficientInvariant
)
from core.pipeline_invariants import PipelineOrderingInvariant, PipelineCoverageInvariant
from core.policies import ExecutionPolicy, ProductionPolicy
import logging

logger = logging.getLogger(__name__)

class InvariantEngine(EventSubscriber):
    """
    Subscribes to domain events and evaluates registered business invariants 
    using a configurable ExecutionPolicy.
    """
    def __init__(self, policy: ExecutionPolicy = None):
        self.policy = policy or ProductionPolicy()
        self.registry: Dict[str, List[Invariant]] = {}
        self._register_invariants()
        
    def _register(self, events: List[str], invariant: Invariant):
        for ev in events:
            if ev not in self.registry:
                self.registry[ev] = []
            self.registry[ev].append(invariant)
            
    def _register_invariants(self):
        # Specific event bindings for O(1) routing
        self._register(["CandidateSelected"], ScoreAboveThresholdInvariant())
        self._register(["SLTargetComputed"], StopBelowEntryInvariant())
        self._register(["SLTargetComputed"], TargetAboveEntryInvariant())
        self._register(["AlertCreated"], ValidOriginatingCandidateInvariant())
        self._register(["ValidationCompleted"], DataSufficientInvariant())
        
        # Architectural invariants subscribe to all major pipeline events (or specific ones)
        pipeline_events = [
            "ValidationCompleted", "IndicatorsCalculated", "ScannerCompleted", 
            "ScoresCalculated", "CandidateSelected", "SLTargetComputed", "AlertCreated",
            "PipelineCompleted"
        ]
        self._register(pipeline_events, PipelineOrderingInvariant())
        self._register(["PipelineCompleted"], PipelineCoverageInvariant())

    def on_event(self, event: DomainEvent) -> None:
        """Evaluates relevant invariants for the given event."""
        event_name = type(event).__name__
        invariants = self.registry.get(event_name, [])
        
        for inv in invariants:
            result = inv.evaluate(event)
            if result is not None:
                self.policy.handle_result(result)
