# app/certification/registry.py
"""
Master Scanner Certification Registry.
Maintains the authoritative mapping of all production scanners to their certification adapters.
"""
from typing import Dict, List, Optional
from app.certification.models import ScannerType
from app.certification.adapters.base import BaseScannerAdapter
from app.certification.adapters.eod_adapter import EODScannerAdapter
from app.certification.adapters.multitf_adapter import MultiTFScannerAdapter
from app.certification.adapters.short_covering_adapter import ShortCoveringScannerAdapter
from app.certification.adapters.reversal_adapter import ReversalScannerAdapter
from app.certification.adapters.pullback_adapter import PullbackScannerAdapter
from app.certification.adapters.technical_adapter import TechnicalScannerAdapter
from app.certification.adapters.accumulation_adapter import AccumulationVCPAdapter
from app.certification.adapters.institutional_accumulation_adapter import InstitutionalAccumulationAdapter
from app.certification.adapters.wealth_adapter import WealthScannerAdapter
from app.certification.adapters.multibagger_adapter import MultibaggerScannerAdapter
from app.certification.adapters.daily_builder_adapter import DailyBuilderAdapter


class ScannerCertificationRegistry:
    """Master registry managing scanner certification adapters."""
    
    _adapters: Dict[str, BaseScannerAdapter] = {}

    @classmethod
    def initialize(cls):
        """Registers all system production scanners."""
        cls.register(ScannerType.EOD_BREAKOUT.value, EODScannerAdapter())
        cls.register(ScannerType.MULTITF_15M.value, MultiTFScannerAdapter(scanner_type=ScannerType.MULTITF_15M))
        cls.register(ScannerType.MULTITF_5M.value, MultiTFScannerAdapter(scanner_type=ScannerType.MULTITF_5M))
        cls.register(ScannerType.SHORT_COVERING_EOD.value, ShortCoveringScannerAdapter(scanner_type=ScannerType.SHORT_COVERING_EOD))
        cls.register(ScannerType.SHORT_COVERING_5M.value, ShortCoveringScannerAdapter(scanner_type=ScannerType.SHORT_COVERING_5M))
        cls.register(ScannerType.REVERSAL.value, ReversalScannerAdapter())
        cls.register(ScannerType.PULLBACK.value, PullbackScannerAdapter())
        cls.register(ScannerType.TECHNICAL.value, TechnicalScannerAdapter())
        cls.register(ScannerType.ACCUMULATION_VCP.value, AccumulationVCPAdapter())
        cls.register(ScannerType.INSTITUTIONAL_ACCUMULATION.value, InstitutionalAccumulationAdapter())
        cls.register(ScannerType.WEALTH.value, WealthScannerAdapter())
        cls.register(ScannerType.MULTIBAGGER.value, MultibaggerScannerAdapter())
        cls.register(ScannerType.DAILY_BUILDER.value, DailyBuilderAdapter())

        # Aliases for CLI convenience
        cls.register("EOD", cls._adapters[ScannerType.EOD_BREAKOUT.value])
        cls.register("MULTITF", cls._adapters[ScannerType.MULTITF_15M.value])
        cls.register("SHORT_COVERING", cls._adapters[ScannerType.SHORT_COVERING_EOD.value])
        cls.register("VCP", cls._adapters[ScannerType.ACCUMULATION_VCP.value])
        cls.register("ACCUMULATION", cls._adapters[ScannerType.INSTITUTIONAL_ACCUMULATION.value])
        cls.register("WEALTH", cls._adapters[ScannerType.WEALTH.value])
        cls.register("MULTIBAGGER", cls._adapters[ScannerType.MULTIBAGGER.value])
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
