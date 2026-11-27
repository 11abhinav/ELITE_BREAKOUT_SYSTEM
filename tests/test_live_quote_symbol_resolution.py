"""Tests for universal symbol resolution, ISIN lookup, and live quote key matching."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.data_providers.bse_security_master import BseSecurityEntry, BseSecurityMasterResolver
from app.data_providers.corporate_aliases import (
    CORPORATE_ACTION_ALIASES,
    get_alias_variants,
    resolve_corporate_alias,
)
from app.data_providers.fyers_symbol_mapper import FyersSymbolMapper
from app.market_data.providers.upstox_instrument_mapper import UpstoxInstrumentMapper
from app.market_data.providers.upstox_provider import (
    UpstoxProvider,
    _inst_key_cache,
    _is_valid_upstox_instrument_key,
)


def test_corporate_aliases_and_delimiter_variants():
    assert CORPORATE_ACTION_ALIASES["ICICIPRULI"] == "ICICILIFE"
    assert CORPORATE_ACTION_ALIASES["LTIM"] == "LTM"
    assert CORPORATE_ACTION_ALIASES["NOVARTIND"] == "EVONILE"
    assert CORPORATE_ACTION_ALIASES["GUJGASLTD"] == "GUJENERGY"
    assert CORPORATE_ACTION_ALIASES["GUJGAS"] == "GUJENERGY"
    assert resolve_corporate_alias("ICICIPRULI.NS") == "ICICILIFE"
    assert resolve_corporate_alias("NSE:LTIM") == "LTM"
    assert resolve_corporate_alias("GUJGASLTD.NS") == "GUJENERGY"
    assert "ARE&M" in get_alias_variants("ARE_M")
    assert "BAJAJ-AUTO" in get_alias_variants("BAJAJ_AUTO")
    assert "LTIM" in get_alias_variants("LTM", include_reverse=True)


def test_upstox_instrument_key_validation():
    assert _is_valid_upstox_instrument_key("NSE_EQ|INE726G01019") is True
    assert _is_valid_upstox_instrument_key("BSE_EQ|INE214T01019") is True
    assert _is_valid_upstox_instrument_key("NSE_INDEX|Nifty 50") is True
    assert _is_valid_upstox_instrument_key("NSE_FO|68443") is True
    assert _is_valid_upstox_instrument_key("NSE_EQ|ICICIPRULI") is False
    assert _is_valid_upstox_instrument_key("NSE_EQ|ANYTICKER") is False
    assert _is_valid_upstox_instrument_key("") is False
    assert _is_valid_upstox_instrument_key(None) is False


def test_upstox_mapper_resolves_real_renames_and_generic_isin():
    mapper = UpstoxInstrumentMapper()
    assert mapper.get_instrument_key("ICICIPRULI", allow_fallback=False) == "NSE_EQ|INE726G01019"
    assert mapper.get_instrument_key("ICICILIFE", allow_fallback=False) == "NSE_EQ|INE726G01019"
    assert mapper.get_instrument_key("LTIM", allow_fallback=False) == "NSE_EQ|INE214T01019"
    assert mapper.get_instrument_key("LTM", allow_fallback=False) == "NSE_EQ|INE214T01019"
    assert mapper.get_instrument_key("NOVARTIND", allow_fallback=False) == "NSE_EQ|INE234A01025"
    assert mapper.get_instrument_key("GUJGASLTD", allow_fallback=False) == "NSE_EQ|INE844O01030"
    assert mapper.get_instrument_key("GUJGAS", allow_fallback=False) == "NSE_EQ|INE844O01030"
    assert mapper.get_instrument_key("GUJENERGY", allow_fallback=False) == "NSE_EQ|INE844O01030"

    # Generic non-hardcoded ISIN resolution via BseSecurityMasterResolver
    fake_entry = BseSecurityEntry(
        canonical_symbol="OLDCORP",
        bse_security_id="OLDCORP",
        bse_scrip_code="599999",
        isin="INE999Z01099",
        company_name="Old Corp Ltd",
    )
    mapper._symbol_map["NEWCORP"] = "NSE_EQ|INE999Z01099"
    mapper._rebuild_isin_index()
    try:
        with patch.object(BseSecurityMasterResolver, "resolve", return_value=fake_entry):
            assert mapper.get_instrument_key("OLDCORP", allow_fallback=False) == "NSE_EQ|INE999Z01099"
    finally:
        mapper._symbol_map.pop("NEWCORP", None)
        mapper._symbol_map.pop("OLDCORP", None)
        mapper._rebuild_isin_index()


def test_upstox_provider_rejects_probing_async_and_matches_by_instrument_token():
    provider = UpstoxProvider()
    up_mod = sys.modules[provider.__class__.__module__]
    _inst_key_cache.pop("FAKEXYZ", None)
    up_mod._inst_key_cache.pop("FAKEXYZ", None)
    fake_probe_res = SimpleNamespace(
        is_valid=True,
        mapped_symbol="NSE_EQ|FAKEXYZ",
        source="PROBING_ASYNC",
    )
    with patch.object(up_mod, "_get_cached_mapper_func", return_value=lambda _s: None), \
         patch.object(up_mod, "_get_cached_resolver", return_value=SimpleNamespace(resolve=lambda *a, **k: fake_probe_res)):
        assert provider._get_instrument_key("FAKEXYZ") is None
        assert "FAKEXYZ" not in _inst_key_cache
        assert "FAKEXYZ" not in up_mod._inst_key_cache

    # Test quote resolution when Upstox keys response by new symbol while request uses old ticker / ISIN
    payload = {
        "status": "success",
        "data": {
            "NSE_EQ:ICICILIFE": {
                "instrument_token": "NSE_EQ|INE726G01019",
                "symbol": "ICICILIFE",
                "last_price": 467.05,
                "net_change": -2.9,
                "ohlc": {"close": 469.95},
            },
            "NSE_EQ:LTM": {
                "instrument_token": "NSE_EQ|INE214T01019",
                "symbol": "LTM",
                "last_price": 4320.0,
                "net_change": 12.0,
                "ohlc": {"close": 4308.0},
            },
            "NSE_EQ:ANYNEWTICKER": {
                "instrument_token": "NSE_EQ|INE999Z01099",
                "symbol": "ANYNEWTICKER",
                "last_price": 250.5,
                "net_change": 5.0,
                "ohlc": {"close": 245.5},
            },
        },
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = json.dumps(payload).encode("utf-8")

    def _fake_key(sym):
        return {
            "ICICIPRULI": "NSE_EQ|INE726G01019",
            "LTIM": "NSE_EQ|INE214T01019",
            "ANYOLDTICKER": "NSE_EQ|INE999Z01099",
        }.get(sym)

    with patch.object(provider, "_get_token", return_value="dummy-token"), \
         patch.object(provider, "_get_instrument_key", side_effect=_fake_key), \
         patch.object(up_mod._upstox_session, "get", return_value=mock_resp):
        quotes = provider.get_quotes(["ICICIPRULI", "LTIM", "ANYOLDTICKER"])

    assert quotes["ICICIPRULI"]["last_price"] == 467.05
    assert quotes["LTIM"]["last_price"] == 4320.0
    assert quotes["ANYOLDTICKER"]["last_price"] == 250.5


def test_fyers_mapper_prefers_active_alias_over_stale_cache_and_uses_isin():
    mapper = FyersSymbolMapper.__new__(FyersSymbolMapper)
    mapper._symbol_map = {
        "ICICIPRULI": "NSE:ICICIPRULI-EQ",  # stale historical entry
        "ICICILIFE": "NSE:ICICILIFE-EQ",
        "LTM": "NSE:LTM-EQ",
        "NEWCORP": "NSE:NEWCORP-EQ",
    }
    mapper._isin_map = {
        "INE726G01019": "NSE:ICICILIFE-EQ",
        "INE214T01019": "NSE:LTM-EQ",
        "INE999Z01099": "NSE:NEWCORP-EQ",
    }
    mapper._loaded = True
    mapper._lock = __import__("threading").RLock()
    mapper._last_refresh_ts = 9999999999.0
    mapper._last_demand_refresh_ts = 0.0
    mapper._is_refreshing = False

    assert mapper.get_fyers_symbol("ICICIPRULI") == "NSE:ICICILIFE-EQ"
    assert mapper.get_fyers_symbol("LTIM") == "NSE:LTM-EQ"
    assert mapper.get_fyers_symbol("GUJGASLTD") == "NSE:GUJENERGY-EQ"
    assert mapper.get_fyers_symbol("GUJGAS") == "NSE:GUJENERGY-EQ"

    fake_entry = BseSecurityEntry(
        canonical_symbol="OLDCORP",
        bse_security_id="OLDCORP",
        bse_scrip_code="599999",
        isin="INE999Z01099",
        company_name="Old Corp Ltd",
    )
    with patch.object(BseSecurityMasterResolver, "resolve", return_value=fake_entry):
        assert mapper.get_fyers_symbol("OLDCORP") == "NSE:NEWCORP-EQ"


def test_unified_fetcher_live_quotes_upstox_and_fyers_fallback():
    from app.data_providers.unified_fetcher import UnifiedFetcher

    uf = UnifiedFetcher()
    upstox_payload = {
        "NSE_EQ:ICICILIFE": {"instrument_token": "NSE_EQ|INE726G01019", "symbol": "ICICILIFE", "last_price": 467.05, "net_change": -2.0},
        "NSE_EQ|INE726G01019": {"instrument_token": "NSE_EQ|INE726G01019", "symbol": "ICICILIFE", "last_price": 467.05, "net_change": -2.0},
        "ICICIPRULI": {"instrument_token": "NSE_EQ|INE726G01019", "symbol": "ICICILIFE", "last_price": 467.05, "net_change": -2.0},
        "NSE_EQ:LTM": {"instrument_token": "NSE_EQ|INE214T01019", "symbol": "LTM", "last_price": 4320.0, "net_change": 10.0},
        "NSE_EQ|INE214T01019": {"instrument_token": "NSE_EQ|INE214T01019", "symbol": "LTM", "last_price": 4320.0, "net_change": 10.0},
        "LTIM": {"instrument_token": "NSE_EQ|INE214T01019", "symbol": "LTM", "last_price": 4320.0, "net_change": 10.0},
    }

    with patch.object(uf.selector, "get_providers", return_value=["upstox"]), \
         patch("market_data.providers.upstox_provider.UpstoxProvider.fetch_live_quotes_batch", return_value=upstox_payload), \
         patch("market_data.providers.upstox_provider.UpstoxProvider._get_instrument_key", side_effect=lambda s: {"ICICIPRULI": "NSE_EQ|INE726G01019", "LTIM": "NSE_EQ|INE214T01019"}.get(s)):
        out = uf.fetch_live_quotes(["ICICIPRULI", "LTIM", "NONEXISTENT_SYM"], consumer="test")

    assert out["ICICIPRULI"]["v"]["cmd"]["c"] == pytest.approx(467.05)
    assert out["LTIM"]["v"]["cmd"]["c"] == pytest.approx(4320.0)
    assert out["NONEXISTENT_SYM"]["v"]["cmd"]["c"] is None


def test_unified_fetcher_fyers_retry_recovers_stale_symbol():
    from app.data_providers.unified_fetcher import UnifiedFetcher

    uf = UnifiedFetcher()
    mock_fyers_client = MagicMock()
    # First batch returns error for stale NSE:ICICIPRULI-EQ, retry succeeds on NSE:ICICILIFE-EQ
    mock_fyers_client.quotes.side_effect = [
        {"s": "ok", "d": [{"n": "NSE:ICICIPRULI-EQ", "s": "error", "v": {}}]},
        {"s": "ok", "d": [{"n": "NSE:ICICILIFE-EQ", "s": "ok", "v": {"lp": 468.0, "ch": -0.5, "prev_close_price": 468.5}}]},
    ]

    with patch.object(uf.selector, "get_providers", return_value=["fyers"]), \
         patch.object(uf.fyers, "_normalize_symbol", return_value="NSE:ICICIPRULI-EQ"), \
         patch("fyers_auth.get_fyers_client", return_value=mock_fyers_client), \
         patch("data_providers.fyers_symbol_mapper.fyers_mapper.refresh_if_stale", return_value=True), \
         patch("data_providers.fyers_symbol_mapper.fyers_mapper.get_fyers_symbol", return_value="NSE:ICICILIFE-EQ"), \
         patch("data_providers.fyers_mapping_utils.save_fyers_mapping"):
        out = uf.fetch_live_quotes(["ICICIPRULI"], consumer="test_fyers_retry")

    assert out["ICICIPRULI"]["v"]["cmd"]["c"] == pytest.approx(468.0)
