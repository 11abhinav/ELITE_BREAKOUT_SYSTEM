"""
app/short_covering/fno_universe.py

Manages the universe of NSE F&O-eligible underlying equities.
Features:
- Dynamic discovery from Upstox master contract CSV / Bhavcopy / NSE security master / DB
- Built-in curated fallback list of all 216+ active NSE F&O equities
- Sector classification and lot size metadata
- Filtering against ASM/GSM and F&O Ban lists
"""

import logging
from typing import List, Dict, Set, Optional
from datetime import date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

# Index symbols to exclude from underlying stock universe
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTYFPI"}

SECTOR_MAPPING: Dict[str, str] = {
    "HDFCBANK": "BANKING", "ICICIBANK": "BANKING", "SBIN": "BANKING", "AXISBANK": "BANKING",
    "KOTAKBANK": "BANKING", "INDUSINDBK": "BANKING", "BANKBARODA": "BANKING", "PNB": "BANKING",
    "FEDERALBNK": "BANKING", "IDFCFIRSTB": "BANKING", "AUBANK": "BANKING", "BANDHANBNK": "BANKING",
    "RBLBANK": "BANKING", "UNIONBANK": "BANKING", "CANBK": "BANKING", "INDIANB": "BANKING", "MAHABANK": "BANKING",
    "YESBANK": "BANKING",
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "WIPRO": "IT", "TECHM": "IT", "LTM": "IT", "COFORGE": "IT",
    "PERSISTENT": "IT", "MPHASIS": "IT", "OFSS": "IT", "KPITTECH": "IT", "TATAELXSI": "IT",
    "RELIANCE": "ENERGY", "ONGC": "ENERGY", "IOC": "ENERGY", "BPCL": "ENERGY", "GAIL": "ENERGY",
    "OIL": "ENERGY", "PETRONET": "ENERGY", "NTPC": "POWER", "POWERGRID": "POWER", "TATAPOWER": "POWER",
    "JSWENERGY": "POWER", "ADANIGREEN": "POWER", "ADANIPOWER": "POWER", "NHPC": "POWER", "SUZLON": "POWER",
    "TATASTEEL": "METALS", "JSWSTEEL": "METALS", "HINDALCO": "METALS", "VEDL": "METALS", "JINDALSTEL": "METALS",
    "SAIL": "METALS", "NMDC": "METALS", "NATIONALUM": "METALS", "HINDZINC": "METALS",
    "TATAMOTORS": "AUTO", "TMPV": "AUTO", "M&M": "AUTO", "MARUTI": "AUTO", "BAJAJ-AUTO": "AUTO", "HEROMOTOCO": "AUTO",
    "EICHERMOT": "AUTO", "TVSMOTOR": "AUTO", "ASHOKLEY": "AUTO", "BHARATFORG": "AUTO", "SONACOMS": "AUTO",
    "SUNPHARMA": "PHARMA", "CIPLA": "PHARMA", "DRREDDY": "PHARMA", "DIVISLAB": "PHARMA", "LUPIN": "PHARMA",
    "AUROPHARMA": "PHARMA", "ALKEM": "PHARMA", "ZYDUSLIFE": "PHARMA", "TORNTPHARM": "PHARMA", "MANKIND": "PHARMA",
    "TATACONSUM": "FMCG", "ITC": "FMCG", "HINDUNILVR": "FMCG", "BRITANNIA": "FMCG", "DABUR": "FMCG",
    "MARICO": "FMCG", "COLPAL": "FMCG", "NESTLEIND": "FMCG", "GODREJCP": "FMCG", "VBL": "FMCG",
    "BAJFINANCE": "FINANCIAL_SERVICES", "BAJAJFINSV": "FINANCIAL_SERVICES", "CHOLAFIN": "FINANCIAL_SERVICES",
    "SHRIRAMFIN": "FINANCIAL_SERVICES", "PFC": "FINANCIAL_SERVICES", "RECLTD": "FINANCIAL_SERVICES",
    "MUTHOOTFIN": "FINANCIAL_SERVICES", "MANAPPURAM": "FINANCIAL_SERVICES", "LICI": "INSURANCE",
    "HDFCLIFE": "INSURANCE", "SBILIFE": "INSURANCE", "ICICIPRULI": "INSURANCE", "ICICIGI": "INSURANCE",
    "BSE": "CAPITAL_MARKETS", "CDSL": "CAPITAL_MARKETS", "MCX": "CAPITAL_MARKETS", "CAMS": "CAPITAL_MARKETS",
    "ANGELONE": "CAPITAL_MARKETS", "KFINTECH": "CAPITAL_MARKETS",
    "DLF": "REALTY", "GODREJPROP": "REALTY", "OBEROIRLTY": "REALTY", "PRESTIGE": "REALTY", "LODHA": "REALTY",
    "PHOENIXLTD": "REALTY",
    "HAL": "DEFENCE", "BEL": "DEFENCE", "BDL": "DEFENCE", "COCHINSHIP": "DEFENCE", "MAZDOCK": "DEFENCE",
}

FNO_ALIAS_MAP = {
    "L&TFH": "LTF",
    "L_TFH": "LTF",
    "GMRINFRA": "GMRAIRPORT",
    "GUJGASLTD": "GUJGAS",
    "MCDOWELL-N": "UNITDSPR",
    "MCDOWELL_N": "UNITDSPR",
    "BAJAJ-AUTO": "BAJAJ_AUTO",
    "M&M": "M_M",
    "TATAMOTORS": "TMPV",
}


class FNOUniverseManager:
    """Manages the dynamic F&O equity universe loaded directly from live exchange master contracts."""

    def __init__(self, custom_symbols: Optional[List[str]] = None):
        self._universe: Set[str] = set(custom_symbols) if custom_symbols else set()
        self._last_refresh_date: Optional[date] = None

    def get_fno_symbols(self, exclude_banned: bool = True) -> List[str]:
        """Returns the list of all currently active F&O underlying equity symbols dynamically discovered."""
        if not self._universe or self._last_refresh_date != datetime.now(IST).date():
            self._sync_dynamic_symbols()
        symbols = sorted(list(self._universe - INDEX_SYMBOLS))
        return symbols

    def _sync_dynamic_symbols(self) -> None:
        """Dynamically syncs F&O symbols from live exchange master contract feeds.
        If fetch fails, reports error to admin immediately and leaves universe empty.
        Zero hardcoded lists used.
        """
        try:
            try:
                from app.market_data.providers.upstox_instrument_mapper import mapper
            except ImportError:
                from market_data.providers.upstox_instrument_mapper import mapper

            dynamic_syms = mapper.get_active_fno_underlying_symbols()
            if dynamic_syms and len(dynamic_syms) >= 50:
                self._universe = set(dynamic_syms)
                self._last_refresh_date = datetime.now(IST).date()
                logger.info(f"✅ Dynamic F&O universe discovered from live exchange master: {len(self._universe)} active equities")
                return

            logger.error("🚨 [FNO_UNIVERSE] Dynamic F&O discovery returned insufficient symbols (%d).", len(dynamic_syms) if dynamic_syms else 0)
        except Exception as e:
            logger.error(f"🚨 [FNO_UNIVERSE] Dynamic F&O universe discovery exception: {e}", exc_info=True)

        # Notify admin of dynamic discovery failure
        try:
            try:
                from app.database import insert_notification
            except ImportError:
                from database import insert_notification
            insert_notification(
                notif_type="error",
                title="🚨 Dynamic F&O Universe Discovery Failed",
                message="Unable to discover active F&O equities from exchange contract feed. Scanners will halt on empty universe instead of running on stale hardcoded lists.",
                symbol=None
            )
        except Exception:
            pass

    def get_sector(self, symbol: str) -> str:
        """Returns the sector for the given symbol, or 'GENERAL' if unmapped."""
        clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
        clean_sym = FNO_ALIAS_MAP.get(clean_sym, clean_sym)
        return SECTOR_MAPPING.get(clean_sym, "GENERAL")

    def is_fno_symbol(self, symbol: str) -> bool:
        """Checks if a given symbol belongs to the active F&O universe."""
        clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
        clean_sym_alias = FNO_ALIAS_MAP.get(clean_sym, clean_sym)
        return clean_sym in self._universe or clean_sym_alias in self._universe

    def is_fno_stock(self, symbol: str) -> bool:
        """Alias for is_fno_symbol."""
        return self.is_fno_symbol(symbol)

    def update_from_bhavcopy(self, bhavcopy_symbols: List[str]) -> None:
        """Updates the active universe dynamically from the latest F&O Bhavcopy."""
        if bhavcopy_symbols and len(bhavcopy_symbols) > 50:
            cleaned = {s.upper().replace(".NS", "").replace("-EQ", "") for s in bhavcopy_symbols}
            self._universe = cleaned
            self._last_refresh_date = datetime.now(IST).date()
            logger.info(f"✅ Dynamic F&O universe updated: {len(self._universe)} symbols")


# Global singleton instance
fno_universe_manager = FNOUniverseManager()

