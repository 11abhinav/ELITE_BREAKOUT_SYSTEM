# app/certification/adapters/base.py
"""
Base Scanner Certification Adapter.
Defines the uniform contract for all production scanner certification adapters.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
import pandas as pd

from app.certification.models import (
    ProductionDecisionRecord,
    ReplayMode,
    ScannerType
)


class BaseScannerAdapter(ABC):
    """
    Abstract adapter for a production scanner.
    Connects the generic certification orchestrator to the specific scanner core.
    """
    def __init__(self, scanner_type: ScannerType):
        self.scanner_type = scanner_type

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        evaluation_date: str,
        mode: ReplayMode,
        prod_record: Optional[ProductionDecisionRecord] = None,
        custom_data: Optional[Dict[str, Any]] = None,
        effective_config: Optional[Dict[str, Any]] = None
    ) -> ProductionDecisionRecord:
        """
        Executes deterministic evaluation for the scanner under the specified mode.
        If mode == PRODUCTION_REPLAY: must reproduce exact production state.
        If mode == CLEAN_HISTORICAL_REPLAY: evaluates using sanitized market data.
        """
        pass
