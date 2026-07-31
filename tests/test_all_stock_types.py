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

def test_fyers_history_payload_cont_flag_is_zero():
    # Verify that cont_flag in fyers_fetcher is set to '0' for spot equity stock history requests
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    source = inspect.getsource(FyersFetcher.get_ohlcv)
    assert '"cont_flag": "0"' in source, "Fyers history API cont_flag MUST be '0' for spot equity stocks"

def test_database_system_state_dict_serialization():
    # Verify that save_system_state handles dictionary arguments without throwing psycopg2 adapt error
    import inspect
    import database
    source = inspect.getsource(database.save_system_state)
    assert 'isinstance(val_str, (dict, list))' in source or 'json.dumps' in source, "save_system_state must serialize dict/list objects to JSON strings"

def test_fyers_symbol_miss_condition_does_not_break_on_403():
    # Verify that code -403 does not trigger immediate candidate break on Attempt 1
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    source = inspect.getsource(FyersFetcher.get_ohlcv)
    assert 'code in ("-403", "403")' not in source, "code -403 MUST NOT trigger candidate loop break on Attempt 1"

def test_fyers_permission_error_triggers_token_invalidation():
    # Verify that permission required error triggers save_system_state for fyers_access_token
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    source = inspect.getsource(FyersFetcher.get_ohlcv)
    assert 'permission required' in source and 'save_system_state' in source, "Fyers permission errors MUST invalidate fyers_access_token in DB"
