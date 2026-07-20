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
class SLTargetComputed(DomainEvent): pass

@dataclass
class AlertCreated(DomainEvent): pass

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
