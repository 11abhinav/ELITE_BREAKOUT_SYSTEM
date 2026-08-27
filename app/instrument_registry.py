# =====================================================================================
# app/instrument_registry.py
# CENTRAL AUTHORITATIVE INSTRUMENT REGISTRY (V1.0)
# =====================================================================================
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, List

logger = logging.getLogger("instrument_registry")

@dataclass
class InstrumentRecord:
    canonical_symbol: str
    exchange: str = "NSE"
    asset_type: str = "EQ"  # "EQ" or "INDEX"
    isin: Optional[str] = None
    company_name: str = ""
    first_trading_date: Optional[str] = None  # YYYY-MM-DD for IPO lifetime calculations
    fyers_symbol: Optional[str] = None
    upstox_instrument_key: Optional[str] = None
    yahoo_symbol: Optional[str] = None
    aliases: Set[str] = field(default_factory=set)
    master_version: str = "1.0"

class InstrumentRegistry:
    """
    Authoritative institutional instrument registry holding canonical symbols,
    asset classes, IPO first trading dates, and verified multi-broker identifiers.
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True
        self.records_by_symbol: Dict[str, InstrumentRecord] = {}
        self.records_by_alias: Dict[str, InstrumentRecord] = {}
        self._load_default_registry()

    def _register(self, record: InstrumentRecord) -> None:
        sym_clean = record.canonical_symbol.strip().upper()
        self.records_by_symbol[sym_clean] = record
        self.records_by_alias[sym_clean] = record
        for alias in record.aliases:
            alias_clean = alias.strip().upper()
            self.records_by_alias[alias_clean] = record

    def _load_default_registry(self) -> None:
        """Loads canonical index definitions and known IPO/Special Instruments."""
        
        # ── 1. BENCHMARK & SECTOR INDICES ──────────────────────────────────────────────
        indices = [
            InstrumentRecord("^NSEI", "NSE", "INDEX", aliases={"NIFTY 50", "NIFTY", "NIFTY50", "NIFTY-50"}, fyers_symbol="NSE:NIFTY50-INDEX", upstox_instrument_key="NSE_INDEX|Nifty 50", yahoo_symbol="^NSEI"),
            InstrumentRecord("^NSEBANK", "NSE", "INDEX", aliases={"BANKNIFTY", "BANK NIFTY", "NIFTYBANK"}, fyers_symbol="NSE:NIFTYBANK-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Bank", yahoo_symbol="^NSEBANK"),
            InstrumentRecord("^BSESN", "BSE", "INDEX", aliases={"SENSEX", "BSESENSEX"}, fyers_symbol="BSE:SENSEX-INDEX", upstox_instrument_key="BSE_INDEX|SENSEX", yahoo_symbol="^BSESN"),
            
            # CNX Sector Indices
            InstrumentRecord("^CNXIT", "NSE", "INDEX", aliases={"NIFTY IT", "NIFTYIT", "CNXIT"}, fyers_symbol="NSE:NIFTYIT-INDEX", upstox_instrument_key="NSE_INDEX|Nifty IT", yahoo_symbol="^CNXIT"),
            InstrumentRecord("^CNXAUTO", "NSE", "INDEX", aliases={"NIFTY AUTO", "NIFTYAUTO", "CNXAUTO"}, fyers_symbol="NSE:NIFTYAUTO-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Auto", yahoo_symbol="^CNXAUTO"),
            InstrumentRecord("^CNXPHARMA", "NSE", "INDEX", aliases={"NIFTY PHARMA", "NIFTYPHARMA", "CNXPHARMA"}, fyers_symbol="NSE:NIFTYPHARMA-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Pharma", yahoo_symbol="^CNXPHARMA"),
            InstrumentRecord("^CNXREALTY", "NSE", "INDEX", aliases={"NIFTY REALTY", "NIFTYREALTY", "CNXREALTY"}, fyers_symbol="NSE:NIFTYREALTY-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Realty", yahoo_symbol="^CNXREALTY"),
            InstrumentRecord("^CNXMETAL", "NSE", "INDEX", aliases={"NIFTY METAL", "NIFTYMETAL", "CNXMETAL"}, fyers_symbol="NSE:NIFTYMETAL-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Metal", yahoo_symbol="^CNXMETAL"),
            InstrumentRecord("^CNXENERGY", "NSE", "INDEX", aliases={"NIFTY ENERGY", "NIFTYENERGY", "CNXENERGY"}, fyers_symbol="NSE:NIFTYENERGY-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Energy", yahoo_symbol="^CNXENERGY"),
            InstrumentRecord("^CNXFMCG", "NSE", "INDEX", aliases={"NIFTY FMCG", "NIFTYFMCG", "CNXFMCG"}, fyers_symbol="NSE:NIFTYFMCG-INDEX", upstox_instrument_key="NSE_INDEX|Nifty FMCG", yahoo_symbol="^CNXFMCG"),
            InstrumentRecord("^CNXINFRA", "NSE", "INDEX", aliases={"NIFTY INFRA", "NIFTYINFRA", "CNXINFRA"}, fyers_symbol="NSE:NIFTYINFRA-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Infra", yahoo_symbol="^CNXINFRA"),
            InstrumentRecord("^CNXPSUBANK", "NSE", "INDEX", aliases={"NIFTY PSU BANK", "NIFTYPSUBANK", "CNXPSUBANK"}, fyers_symbol="NSE:NIFTYPSUBANK-INDEX", upstox_instrument_key="NSE_INDEX|Nifty PSU Bank", yahoo_symbol="^CNXPSUBANK"),
            InstrumentRecord("^CNXMEDIA", "NSE", "INDEX", aliases={"NIFTY MEDIA", "NIFTYMEDIA", "CNXMEDIA"}, fyers_symbol="NSE:NIFTYMEDIA-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Media", yahoo_symbol="^CNXMEDIA"),
            InstrumentRecord("^CNXSMALLCAP", "NSE", "INDEX", aliases={"NIFTY SMALLCAP", "NIFTYSMALLCAP", "CNXSMALLCAP", "NIFTY SMALLCAP 100"}, fyers_symbol="NSE:NIFTYSMALLCAP100-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Smallcap 100", yahoo_symbol="^CNXSMALLCAP"),
            InstrumentRecord("^CNXMIDCAP", "NSE", "INDEX", aliases={"NIFTY MIDCAP", "NIFTYMIDCAP", "CNXMIDCAP", "NIFTY MIDCAP 100"}, fyers_symbol="NSE:NIFTYMIDCAP100-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Midcap 100", yahoo_symbol="^CNXMIDCAP"),
            InstrumentRecord("^CNXCONSUMPTION", "NSE", "INDEX", aliases={"NIFTY CONSUMPTION", "CNXCONSUMPTION"}, fyers_symbol="NSE:NIFTYCONSUMPTION-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Consumption", yahoo_symbol="^CNXCONSUMPTION"),
            InstrumentRecord("^CNXSERVICE", "NSE", "INDEX", aliases={"NIFTY SERVICES", "CNXSERVICE"}, fyers_symbol="NSE:NIFTYSERVSECTOR-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Services", yahoo_symbol="^CNXSERVICE"),
            InstrumentRecord("^CNXFIN", "NSE", "INDEX", aliases={"FINNIFTY", "NIFTY FINANCE", "CNXFINANCE"}, fyers_symbol="NSE:FINNIFTY-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Financial Services", yahoo_symbol="^CNXFIN"),
            InstrumentRecord("^CNXCMDT", "NSE", "INDEX", aliases={"NIFTY COMMODITIES", "CNXCMDT"}, fyers_symbol="NSE:NIFTYCOMMODITIES-INDEX", upstox_instrument_key="NSE_INDEX|Nifty Commodities", yahoo_symbol="^CNXCMDT"),
        ]
        for idx in indices:
            self._register(idx)

        # ── 2. SPECIAL RECENT LISTINGS / IPOs ──────────────────────────────────────────
        special_equities = [
            InstrumentRecord("LOTUSDEV", "NSE", "EQ", isin="INE0V9Q01010", company_name="Sri Lotus Developers and Realty Ltd", first_trading_date="2026-06-01", fyers_symbol="NSE:LOTUSDEV-EQ", upstox_instrument_key="NSE_EQ|INE0V9Q01010", yahoo_symbol="LOTUSDEV.NS"),
            InstrumentRecord("MTARTECH", "NSE", "EQ", isin="INE864I01014", company_name="MTAR Technologies Ltd", first_trading_date="2021-03-15", fyers_symbol="NSE:MTARTECH-EQ", upstox_instrument_key="NSE_EQ|INE864I01014", yahoo_symbol="MTARTECH.NS"),
            InstrumentRecord("STLTECH", "NSE", "EQ", isin="INE089C01029", company_name="Sterlite Technologies Ltd", first_trading_date="2000-08-14", fyers_symbol="BSE:532374-EQ", upstox_instrument_key="NSE_EQ|INE089C01029", yahoo_symbol="STLTECH.NS"),
            InstrumentRecord("DIACABS", "NSE", "EQ", isin="INE944H01026", company_name="Diamond Power Infrastructure Ltd", first_trading_date="2008-04-10", fyers_symbol="BSE:532959-EQ", upstox_instrument_key="NSE_EQ|INE944H01026", yahoo_symbol="DIACABS.NS"),
        ]
        for eq in special_equities:
            self._register(eq)

        logger.info(f"✅ [InstrumentRegistry] Initialized registry with {len(self.records_by_symbol)} canonical records.")

    def lookup(self, symbol: str) -> Optional[InstrumentRecord]:
        """Resolves raw symbol or alias to canonical InstrumentRecord."""
        if not symbol or not isinstance(symbol, str):
            return None
        sym_clean = symbol.strip().upper()
        return self.records_by_symbol.get(sym_clean) or self.records_by_alias.get(sym_clean)

    def is_index(self, symbol: str) -> bool:
        """Returns True if symbol is registered as an INDEX asset class."""
        rec = self.lookup(symbol)
        if rec:
            return rec.asset_type == "INDEX"
        sym_upper = symbol.strip().upper()
        return sym_upper.startswith("^") or "NIFTY" in sym_upper or "SENSEX" in sym_upper or "INDEX" in sym_upper

# Global singleton helper
_registry_instance = None

def get_instrument_registry() -> InstrumentRegistry:
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = InstrumentRegistry()
    return _registry_instance
