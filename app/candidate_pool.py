"""
CandidatePool — Abstract interface and V1 in-memory implementation.

The CandidatePool is intentionally dumb. It only stores candidates.
OpportunityManager owns all business logic (freshness, expiry, deduplication).

Evolution Path:
  V1: InMemoryCandidatePool (current) — list scoped to one scanner sweep
  V2: RedisCandidatePool              — shared across scanners, same day
  V3: KafkaCandidatePool              — distributed, streaming, real-time

No scanner changes are needed to upgrade between versions.
Only the pool implementation changes.
"""
from abc import ABC, abstractmethod


class CandidatePool(ABC):
    """
    Abstract interface. All implementations must honour this contract.
    """

    @abstractmethod
    def add(self, candidate: dict) -> None:
        """Add a candidate to the pool."""

    @abstractmethod
    def remove(self, symbol: str) -> None:
        """Remove a candidate by symbol."""

    @abstractmethod
    def get_candidates(self) -> list[dict]:
        """Return all candidates currently in the pool."""

    @abstractmethod
    def clear(self) -> None:
        """Empty the pool."""


class InMemoryCandidatePool(CandidatePool):
    """
    V1 implementation — a plain in-memory list.
    Scoped to the lifecycle of a single scanner sweep.
    Thread-safety not required for V1 (single-threaded scanner).
    """

    def __init__(self):
        self._store: list[dict] = []

    def add(self, candidate: dict) -> None:
        self._store.append(candidate)

    def remove(self, symbol: str) -> None:
        self._store = [c for c in self._store if c.get("symbol") != symbol]

    def get_candidates(self) -> list[dict]:
        return list(self._store)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
