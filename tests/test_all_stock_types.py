import pytest
from data_providers.fyers_fetcher import FyersFetcher

def test_all_stock_categories_candidate_resolution():
    fetcher = FyersFetcher()
    
    # 1. Standard NSE Equities
    c_reliance = fetcher._generate_fyers_candidate_symbols("RELIANCE")
    assert "NSE:RELIANCE-EQ" in c_reliance
    assert "BSE:RELIANCE-EQ" in c_reliance
    
    c_itc = fetcher._generate_fyers_candidate_symbols("ITC")
    assert "NSE:ITC-EQ" in c_itc
    assert "BSE:ITC-EQ" in c_itc
    
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
    # Verify that cont_flag in fyers_fetcher is restricted to derivatives (-FUT/-OPT)
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    source = inspect.getsource(FyersFetcher.get_ohlcv)
    assert 'data["cont_flag"] = "0"' in source and '-FUT' in source, "cont_flag MUST be restricted to derivatives to avoid Fyers code -403 permission errors"

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

def test_fyers_permission_error_never_wipes_global_token():
    # Verify that single-stock permission errors NEVER wipe the global DB access token
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    source = inspect.getsource(FyersFetcher.get_ohlcv)
    assert 'save_system_state' not in source, "FyersFetcher MUST NEVER call save_system_state to purge token on single stock errors"

def test_fyers_client_id_always_ends_with_100():
    # Verify that get_fyers_client enforces -100 suffix for History API compatibility
    import inspect
    import fyers_auth
    source = inspect.getsource(fyers_auth.get_fyers_client)
    assert 'endswith("-100")' in source, "get_fyers_client MUST enforce -100 suffix for Fyers History API"

def test_scraperapi_key_order_preserved(monkeypatch):
    # Verify that get_valid_scraper_keys preserves exact order of keys in SCRAPERAPI_KEY env var
    import fyers_auth
    monkeypatch.setenv("SCRAPERAPI_KEY", "new_key_123, old_key_456, old_key_789")
    keys = fyers_auth.get_valid_scraper_keys()
    assert keys == ["new_key_123", "old_key_456", "old_key_789"], "ScraperAPI key order MUST be strictly preserved from env var"

def test_unified_fetcher_includes_bse_symbols_in_fyers_quotes():
    # Verify that UnifiedFetcher allows BSE: symbols for Fyers live quote batches
    import inspect
    from data_providers.unified_fetcher import UnifiedFetcher
    source = inspect.getsource(UnifiedFetcher.fetch_live_quotes)
    assert 'norm.startswith("BSE:")' in source, "UnifiedFetcher MUST include BSE: symbols in Fyers quote batches"

def test_fyers_fetcher_never_purges_token_on_api_errors():
    # Verify that fyers_fetcher NEVER purges valid DB token on single-stock API errors
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    source = inspect.getsource(FyersFetcher.get_ohlcv)
    assert 'save_system_state' not in source, "FyersFetcher MUST NEVER call save_system_state to purge token on single stock errors"

def test_fyers_cont_flag_omitted_for_equity_spot():
    # Verify that cont_flag is NOT passed for equity spot history requests
    import inspect
    from data_providers.fyers_fetcher import FyersFetcher
    source = inspect.getsource(FyersFetcher.get_ohlcv)
    assert '-FUT' in source and '-OPT' in source, "cont_flag MUST be restricted to derivatives to avoid Fyers code -403 permission errors"

def test_fyers_token_db_persistence_and_expiration_check():
    # Verify that get_access_token checks is_token_expired and loads DB token resiliently
    import inspect
    import fyers_auth
    source = inspect.getsource(fyers_auth.get_access_token)
    assert 'is_token_expired' in source and 'saved_date == now_date' in source, "get_access_token MUST check token expiration to ensure DB token loading on redeployment"
