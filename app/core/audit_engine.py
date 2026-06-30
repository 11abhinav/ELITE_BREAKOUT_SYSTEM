from typing import List, Any
import json
from datetime import datetime
from core.models import AuditTrailEntry
import logging

logger = logging.getLogger(__name__)

class AuditEngine:
    def __init__(self):
        self.trail: List[AuditTrailEntry] = []

    def log(self, symbol: str, layer: str, status: str, reason: str, metric: str, value: Any):
        """Record a decision (Passed/Warning/Failed)."""
        entry = AuditTrailEntry(
            symbol=symbol,
            layer=layer,
            status=status,
            reason=reason,
            metric=metric,
            value=value
        )
        self.trail.append(entry)

    def get_trail_for_symbol(self, symbol: str) -> List[AuditTrailEntry]:
        return [t for t in self.trail if t.symbol == symbol]

    def export_trail(self, symbol: str = None) -> List[dict]:
        """Export current trail as a list of dictionaries."""
        if symbol:
            return [t.__dict__ for t in self.trail if t.symbol == symbol]
        return [t.__dict__ for t in self.trail]
        
    def clear(self, symbol: str):
        self.trail = [t for t in self.trail if t.symbol != symbol]

# Global audit instance for the current pipeline run
audit_engine = AuditEngine()
