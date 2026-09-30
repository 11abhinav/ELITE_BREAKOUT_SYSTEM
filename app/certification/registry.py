# app/certification/registry.py
"""
Master Scanner Certification Registry.
Maintains the authoritative mapping of all production scanners to their certification adapters.
"""
from typing import Dict, List, Optional
from app.certification.models import ScannerType
from app.certification.adapters.base import BaseScannerAdapter
from app.certification.adapters.technical_adapter import TechnicalScannerAdapter
from app.certification.adapters.wealth_adapter import WealthScannerAdapter
from app.certification.adapters.daily_builder_adapter import DailyBuilderAdapter


class ScannerCertificationRegistry:
    """Master registry managing scanner certification adapters."""
    
    _adapters: Dict[str, BaseScannerAdapter] = {}

    @classmethod
    def initialize(cls):
        """Registers active and certified system production scanners."""
        cls.register(ScannerType.TECHNICAL.value, TechnicalScannerAdapter())
        cls.register(ScannerType.WEALTH.value, WealthScannerAdapter())
        cls.register(ScannerType.DAILY_BUILDER.value, DailyBuilderAdapter())

        # Aliases for CLI convenience
        cls.register("TECHNICAL", cls._adapters[ScannerType.TECHNICAL.value])
        cls.register("WEALTH", cls._adapters[ScannerType.WEALTH.value])
        cls.register("DAILY_BUILDER", cls._adapters[ScannerType.DAILY_BUILDER.value])
        cls.register("BUILDER", cls._adapters[ScannerType.DAILY_BUILDER.value])

    @classmethod
    def register(cls, scanner_name: str, adapter: BaseScannerAdapter):
        cls._adapters[scanner_name.upper()] = adapter

    @classmethod
    def get_adapter(cls, scanner_name: str) -> Optional[BaseScannerAdapter]:
        if not cls._adapters:
            cls.initialize()
        return cls._adapters.get(scanner_name.upper())

    @classmethod
    def list_scanners(cls) -> List[str]:
        if not cls._adapters:
            cls.initialize()
        return sorted([k for k in cls._adapters.keys() if "_" in k or k in ("EOD", "REVERSAL", "PULLBACK", "TECHNICAL")])


# Auto-initialize registry
ScannerCertificationRegistry.initialize()
