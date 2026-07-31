import pytest
from data_providers.fyers_fetcher import FyersFetcher

def test_all_stock_categories_candidate_resolution():
    fetcher = FyersFetcher()
    
    # 1. Standard NSE Equities
    c_reliance = fetcher._generate_fyers_candidate_symbols("RELIANCE")
    assert "NSE:RELIANCE-EQ" in c_reliance
    assert "NSE:RELIANCE-BE" in c_reliance
    
    c_itc = fetcher._generate_fyers_candidate_symbols("ITC")
    assert "NSE:ITC-EQ" in c_itc
    assert "NSE:ITC-BE" in c_itc
    
    # 2. Known Custom BSE Scrip Mapped Stocks (e.g. POONAWALLA -> 524000)
    c_poonawalla = fetcher._generate_fyers_candidate_symbols("POONAWALLA")
    assert "NSE:POONAWALLA-EQ" in c_poonawalla
    assert "BSE:524000-EQ" in c_poonawalla or "BSE:524000" in c_poonawalla
    
    c_pfc = fetcher._generate_fyers_candidate_symbols("PFC")
    assert "NSE:PFC-EQ" in c_pfc
    assert "BSE:532648-EQ" in c_pfc or "BSE:532648" in c_pfc
    
    # 3. Numeric BSE Scrip Codes
    c_bse = fetcher._generate_fyers_candidate_symbols("500290")
    assert "BSE:500290-EQ" in c_bse
    assert "BSE:500290" in c_bse
    
    # 4. Special Character Ampersand Handling
    c_mm = fetcher._generate_fyers_candidate_symbols("M_M")
    assert "NSE:M&M-EQ" in c_mm
    
    # 5. Invalid series -A, -B, -T must NOT be present
    for sym in ["ITC", "POONAWALLA", "RELIANCE", "FSL", "NYKAA"]:
        cand = fetcher._generate_fyers_candidate_symbols(sym)
        for invalid_suffix in ["-A", "-B", "-T"]:
            for c in cand:
                assert not c.endswith(invalid_suffix), f"Invalid suffix {invalid_suffix} found in candidates for {sym}: {cand}"

def test_yfinance_chrome_session_initialization():
    from price_provider import PriceProvider
    provider = PriceProvider()
    assert provider is not None
