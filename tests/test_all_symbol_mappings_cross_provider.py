import sys
import os
import pytest

# Add app to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from symbol_resolution_engine import get_symbol_resolver
from market_data.providers.upstox_provider import UpstoxProvider
from data_providers.fyers_fetcher import FyersFetcher

# List of all broad market and 14 sector indices used across macro & sector rotation
TEST_INDICES = [
    # Broad Indices
    "^NSEI", "NIFTY", "NIFTY50", "NIFTY 50",
    "^NSEBANK", "BANKNIFTY", "NIFTYBANK",
    "^BSESN", "SENSEX",
    "^NSMIDCP", "^CNXSMALLCAP",
    # 14 Sectoral Indices
    "^CNXAUTO", "NIFTYAUTO",
    "^CNXIT", "NIFTYIT",
    "^CNXFMCG", "NIFTYFMCG",
    "^CNXPHARMA", "NIFTYPHARMA",
    "^CNXMETAL", "NIFTYMETAL",
    "^CNXREALTY", "NIFTYREALTY",
    "^CNXENERGY", "NIFTYENERGY",
    "^CNXINFRA", "NIFTYINFRA",
    "^CNXPSUBANK", "NIFTYPSUBANK",
    "^CNXFIN", "^CNXFINANCE", "NIFTYFINANCE",
    "^CNXCMDT", "^CNXCOMMODITIES", "NIFTYCOMMODITIES",
    "^CNXMEDIA", "NIFTYMEDIA",
    "^NIFTYHEALTHCARE", "^NIFTYOILGAS"
]

TEST_EQUITIES = [
    "TCS", "RELIANCE", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
    "TATAMOTORS", "TATASTEEL", "LTIM", "BHARTIARTL", "ITC", "AXISBANK"
]

def test_all_indices_resolve_across_all_providers():
    """Audits that every single sector and market index resolves cleanly across Upstox, Fyers, and Yahoo."""
    resolver = get_symbol_resolver()
    upstox_prov = UpstoxProvider()
    fyers_fetcher = FyersFetcher()
    
    missing_upstox = []
    missing_fyers = []
    missing_yahoo = []
    
    for idx_sym in TEST_INDICES:
        # 1. Test SymbolResolutionService
        res_upstox = resolver.resolve(idx_sym, "upstox")
        res_fyers = resolver.resolve(idx_sym, "fyers")
        res_yahoo = resolver.resolve(idx_sym, "yahoo")
        
        if not res_upstox or not res_upstox.is_valid or not res_upstox.mapped_symbol:
            missing_upstox.append((idx_sym, "Resolver"))
        if not res_fyers or not res_fyers.is_valid or not res_fyers.mapped_symbol:
            missing_fyers.append((idx_sym, "Resolver"))
        if not res_yahoo or not res_yahoo.is_valid or not res_yahoo.mapped_symbol:
            missing_yahoo.append((idx_sym, "Resolver"))
            
        # 2. Test UpstoxProvider direct instrument key lookup
        up_key = upstox_prov._get_instrument_key(idx_sym)
        if not up_key or not (up_key.startswith("NSE_INDEX|") or up_key.startswith("BSE_INDEX|")):
            missing_upstox.append((idx_sym, f"UpstoxProvider key: {up_key}"))
            
        # 3. Test FyersFetcher direct mapping lookup
        fy_key = fyers_fetcher._normalize_symbol(idx_sym)
        if not fy_key or not (fy_key.startswith("NSE:") or fy_key.startswith("BSE:")):
            missing_fyers.append((idx_sym, f"FyersFetcher key: {fy_key}"))

    assert not missing_upstox, f"Upstox index resolution failures: {missing_upstox}"
    assert not missing_fyers, f"Fyers index resolution failures: {missing_fyers}"
    assert not missing_yahoo, f"Yahoo index resolution failures: {missing_yahoo}"

def test_all_equities_resolve_across_all_providers():
    """Audits that equity tickers resolve cleanly across Upstox, Fyers, and Yahoo."""
    resolver = get_symbol_resolver()
    for eq_sym in TEST_EQUITIES:
        res_upstox = resolver.resolve(eq_sym, "upstox")
        res_fyers = resolver.resolve(eq_sym, "fyers")
        res_yahoo = resolver.resolve(eq_sym, "yahoo")
        
        assert res_upstox and res_upstox.is_valid and res_upstox.mapped_symbol, f"Upstox equity resolution failed for {eq_sym}"
        assert res_fyers and res_fyers.is_valid and res_fyers.mapped_symbol, f"Fyers equity resolution failed for {eq_sym}"
        assert res_yahoo and res_yahoo.is_valid and res_yahoo.mapped_symbol, f"Yahoo equity resolution failed for {eq_sym}"

