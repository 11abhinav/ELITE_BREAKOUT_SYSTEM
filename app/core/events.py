from dataclasses import dataclass
from typing import Any, Callable, Dict, List

@dataclass
class DomainEvent:
    """Base class for all domain events in the pipeline."""
    payload: Any

@dataclass
class ValidationCompleted(DomainEvent): pass

@dataclass
class IndicatorsCalculated(DomainEvent): pass

@dataclass
class ScannerCompleted(DomainEvent): pass

@dataclass
class ScoresCalculated(DomainEvent): pass

@dataclass
class CandidateSelected(DomainEvent): pass

@dataclass
class SLTargetComputed(DomainEvent):
    """Payload requires: symbol, entry_price, stop_loss, target_1, target_2, target_3, target_4"""
    pass

@dataclass
class AlertCreated(DomainEvent):
    """Payload requires: symbol, timestamp, type, action, signals, score, stop_loss, target"""
    pass

@dataclass
class PipelineCompleted(DomainEvent):
    """Payload requires: symbol, status. Fired when pipeline finishes for a symbol."""
    pass

class EventSubscriber:
    def on_event(self, event: DomainEvent) -> None:
        raise NotImplementedError

class EventPublisher:
    def __init__(self):
        self._subscribers: List[EventSubscriber] = []
        
    def subscribe(self, subscriber: EventSubscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)
            
    def publish(self, event: DomainEvent) -> None:
        for subscriber in self._subscribers:
            subscriber.on_event(event)

class NoOpEventPublisher(EventPublisher):
    """Used in production when no event tracking is required."""
    def publish(self, event: DomainEvent) -> None:
        pass
