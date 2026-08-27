# =====================================================================================
# app/security_identity_resolver.py — UNIVERSAL CANONICAL SECURITY IDENTITY RESOLVER
# =====================================================================================
import os
import json
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ResolvedSecurityIdentity:
    input_key: str
    canonical_symbol: str
    isin: Optional[str]
    company_name: Optional[str]
    bse_scrip_code: Optional[str]
    exchange_primary: str  # "NSE" or "BSE"
    segment: str           # "EQ", "BE", "SM", "ST", "T"
    fyers_instrument_key: Optional[str]
    upstox_instrument_key: Optional[str]
    resolution_status: str # "RESOLVED", "UNSUPPORTED_INSTRUMENT"

class SecurityIdentityResolver:
    """
    Universal 4-Tier Security Identity Engine.
    Resolves ANY input format (NSE Ticker, BSE Scrip Code, ISIN, Yahoo suffix)
    into a canonical security record mapped to verified Fyers & Upstox master contract instruments.
    """
    def __init__(self):
        self._bse_scrip_map: Dict[str, str] = {}  # "532959" -> "DIACABS", "532374" -> "STLTECH"
        self._isin_map: Dict[str, str] = {}       # "INE989C01038" -> "DIACABS"
        self._loaded = False
        self._init_mappings()

    def _init_mappings(self):
        if self._loaded:
            return
        
        # 1. Built-in institutional BSE scrip code map
        known_bse_codes = {
            "532959": "DIACABS",
            "532374": "STLTECH",
            "543270": "MTARTECH",
            "500325": "RELIANCE",
            "500400": "TATAMOTORS",
            "532540": "TCS",
            "500209": "INFY",
            "500180": "HDFCBANK",
            "532454": "BHARTIARTL",
            "500510": "LT",
            "500696": "HINDUNILVR",
            "500290": "MRF",
            "544175": "AADHARHFC",
            "544216": "AMBEY",
            "543310": "KNAGRI",
        }
        for code, sym in known_bse_codes.items():
            self._bse_scrip_map[code] = sym

        # Load dynamically from bse_mapping_utils if available
        try:
            from bse_mapping_utils import load_bse_mappings
            bse_dict = load_bse_mappings()
            for sym, code in bse_dict.items():
                scrip = str(code).upper().replace(".BO", "").replace("BSE:", "").strip()
                if scrip.isdigit():
                    self._bse_scrip_map[scrip] = sym
        except Exception:
            pass

        self._loaded = True

    def resolve(self, input_key: str) -> ResolvedSecurityIdentity:
        """
        Resolves input_key (e.g. 'DIACABS', '532959', 'INE989C01038', 'STLTECH.NS')
        into a canonical ResolvedSecurityIdentity object.
        """
        self._init_mappings()
        raw = str(input_key).strip().upper()

        # 1. Strip Yahoo suffixes (.NS, .BO, .BSE)
        clean = raw
        for sfx in (".NS", ".BO", ".BSE"):
            if clean.endswith(sfx):
                clean = clean[:-len(sfx)]
                break

        canonical_symbol = clean
        bse_code = None
        isin = None

        # 2. Check if input is a numeric BSE scrip code (e.g. 532959)
        if clean.isdigit():
            bse_code = clean
            if clean in self._bse_scrip_map:
                canonical_symbol = self._bse_scrip_map[clean]

        # 3. Check if input is an ISIN (e.g. INE989C01038)
        if clean.startswith("INE") and len(clean) == 12:
            isin = clean
            if clean in self._isin_map:
                canonical_symbol = self._isin_map[clean]

        # 4. Lookup institutional InstrumentRegistry for ISIN & metadata
        try:
            from instrument_registry import get_instrument_registry
            rec = get_instrument_registry().lookup(canonical_symbol)
            if rec:
                isin = rec.isin or isin
                canonical_symbol = rec.symbol or canonical_symbol
        except Exception:
            pass

        # 5. Resolve Fyers Tradable Instrument Key via Official Fyers Master Contract
        fyers_key = None
        try:
            from data_providers.fyers_symbol_mapper import fyers_mapper
            fyers_key = fyers_mapper.get_fyers_symbol(canonical_symbol, isin=isin)
        except Exception as fe:
            logger.debug(f"Fyers key resolution error for {canonical_symbol}: {fe}")

        # 6. Resolve Upstox Tradable Instrument Key via Official Upstox Master Contract
        upstox_key = None
        try:
            from market_data.providers.upstox_instrument_mapper import get_upstox_instrument_key
            upstox_key = get_upstox_instrument_key(canonical_symbol)
        except Exception as ue:
            logger.debug(f"Upstox key resolution error for {canonical_symbol}: {ue}")

        # Determine segment
        segment = "EQ"
        if fyers_key and "-BE" in fyers_key:
            segment = "BE"
        elif fyers_key and "-SM" in fyers_key:
            segment = "SM"
        elif fyers_key and "-T" in fyers_key:
            segment = "T"

        exchange_primary = "BSE" if (clean.isdigit() or (fyers_key and "BSE:" in fyers_key)) else "NSE"
        status = "RESOLVED" if (fyers_key or upstox_key) else "UNSUPPORTED_INSTRUMENT"

        return ResolvedSecurityIdentity(
            input_key=input_key,
            canonical_symbol=canonical_symbol,
            isin=isin,
            company_name=None,
            bse_scrip_code=bse_code,
            exchange_primary=exchange_primary,
            segment=segment,
            fyers_instrument_key=fyers_key,
            upstox_instrument_key=upstox_key,
            resolution_status=status
        )

# Global Accessor Singleton
identity_resolver = SecurityIdentityResolver()
