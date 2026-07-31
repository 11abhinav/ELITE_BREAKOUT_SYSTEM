"""
tests/test_symbol_format_validator.py
Validates that symbol_format_validator enforces strict format rules for both
Fyers API v3 and Yahoo Finance and auto-corrects known edge cases.
"""
import pytest
from symbol_format_validator import (
    validate_fyers_symbol,
    validate_yahoo_symbol,
    sanitize_fyers_candidate_list,
    sanitize_yahoo_ticker_list,
)


# ─── Fyers valid symbols ───────────────────────────────────────────────────────
@pytest.mark.parametrize("sym", [
    "NSE:RELIANCE-EQ",
    "BSE:ALKEM-EQ",
    "BSE:524000-EQ",
    "NSE:NIFTY50-INDEX",
    "NSE:NIFTYBANK-INDEX",
    "BSE:SENSEX-INDEX",
    "NSE:NIFTY-FUT",
    "NSE:NIFTY-OPT",
])
def test_fyers_valid_symbols_pass(sym):
    assert validate_fyers_symbol(sym) == sym


# ─── Fyers auto-fix: -BE → -EQ ────────────────────────────────────────────────
@pytest.mark.parametrize("sym, expected", [
    ("NSE:ALOKINDS-BE", "NSE:ALOKINDS-EQ"),
    ("NSE:ALKEM-BE",    "NSE:ALKEM-EQ"),
])
def test_fyers_be_series_auto_fixed(sym, expected):
    assert validate_fyers_symbol(sym) == expected


# ─── Fyers auto-fix: bare BSE → -EQ ──────────────────────────────────────────
@pytest.mark.parametrize("sym, expected", [
    ("BSE:ALKEM",       "BSE:ALKEM-EQ"),
    ("BSE:524000",      "BSE:524000-EQ"),
])
def test_fyers_bare_bse_auto_fixed(sym, expected):
    assert validate_fyers_symbol(sym) == expected


# ─── Fyers invalid: Yahoo format sent to Fyers ────────────────────────────────
@pytest.mark.parametrize("sym", [
    "RELIANCE.NS",
    "ALKEM.BO",
    "RELIANCE",          # bare, no exchange
    "^NSEI",             # Yahoo index
])
def test_fyers_yahoo_format_rejected(sym):
    with pytest.raises(ValueError):
        validate_fyers_symbol(sym)


# ─── Yahoo valid symbols ──────────────────────────────────────────────────────
@pytest.mark.parametrize("sym", [
    "RELIANCE.NS",
    "ALKEM.BO",
    "^NSEI",
    "^NSEBANK",
    "^BSESN",
])
def test_yahoo_valid_symbols_pass(sym):
    assert validate_yahoo_symbol(sym) == sym


# ─── Yahoo auto-fix: Fyers format → Yahoo format ─────────────────────────────
@pytest.mark.parametrize("sym, expected", [
    ("NSE:RELIANCE-EQ",          "RELIANCE.NS"),
    ("BSE:ALKEM-EQ",             "ALKEM.BO"),
    ("NSE:NIFTY50-INDEX",        "^NSEI"),
    ("NSE:NIFTYBANK-INDEX",      "^NSEBANK"),
    ("BSE:SENSEX-INDEX",         "^BSESN"),
])
def test_yahoo_fyers_format_auto_fixed(sym, expected):
    assert validate_yahoo_symbol(sym) == expected


# ─── Yahoo invalid: bare symbols with no suffix ───────────────────────────────
@pytest.mark.parametrize("sym", [
    "RELIANCE",       # no exchange suffix
    "ALKEM",
])
def test_yahoo_bare_symbol_rejected(sym):
    with pytest.raises(ValueError):
        validate_yahoo_symbol(sym)


# ─── Batch sanitization drops invalid, auto-fixes correctable ones ────────────
def test_sanitize_fyers_candidate_list():
    candidates = [
        "NSE:ALOKINDS-BE",   # auto-fixed → NSE:ALOKINDS-EQ
        "NSE:RELIANCE-EQ",   # valid
        "BSE:ALKEM",         # auto-fixed → BSE:ALKEM-EQ
        "RELIANCE.NS",       # invalid → dropped
    ]
    result = sanitize_fyers_candidate_list(candidates)
    assert "NSE:ALOKINDS-EQ" in result
    assert "NSE:RELIANCE-EQ" in result
    assert "BSE:ALKEM-EQ" in result
    assert "RELIANCE.NS" not in result
    assert "NSE:ALOKINDS-BE" not in result


def test_sanitize_yahoo_ticker_list():
    tickers = [
        "RELIANCE.NS",      # valid
        "NSE:RELIANCE-EQ",  # auto-fixed → RELIANCE.NS
        "ALKEM.BO",         # valid
        "BSE:ALKEM-EQ",     # auto-fixed → ALKEM.BO
        "BARESTOCK",        # invalid → dropped
    ]
    result = sanitize_yahoo_ticker_list(tickers)
    assert "RELIANCE.NS" in result
    assert "ALKEM.BO" in result
    assert "NSE:RELIANCE-EQ" not in result
    assert "BSE:ALKEM-EQ" not in result
    assert "BARESTOCK" not in result


def test_no_crossover_between_providers():
    """
    validate_yahoo_symbol auto-recovers recognizable Fyers symbols to Yahoo format
    and logs a warning. It must NOT silently accept bare Fyers formats without correction.
    validate_fyers_symbol must always reject Yahoo-style symbols (no auto-fix possible).
    """
    fyers_syms = ["NSE:RELIANCE-EQ", "BSE:ALKEM-EQ", "NSE:NIFTY50-INDEX"]
    yahoo_syms = ["RELIANCE.NS", "ALKEM.BO", "^NSEI"]

    # Fyers format sent to Yahoo validator → auto-corrected (not silently accepted as-is)
    for sym in fyers_syms:
        recovered = validate_yahoo_symbol(sym)
        # Must have been transformed (not returned unchanged)
        assert recovered != sym, f"validate_yahoo_symbol({sym!r}) should have auto-corrected it, not accepted as-is"
        # Must now be a valid Yahoo format
        assert recovered.endswith(".NS") or recovered.endswith(".BO") or recovered.startswith("^"), \
            f"Auto-corrected symbol {recovered!r} is not a valid Yahoo format"

    # Yahoo format sent to Fyers validator → must always raise ValueError
    for sym in yahoo_syms:
        with pytest.raises(ValueError):
            validate_fyers_symbol(sym)


def test_fyers_candidate_generation_never_produces_be_or_bare_bse():
    """Integration test: FyersFetcher must never emit -BE or bare BSE: candidates."""
    from data_providers.fyers_fetcher import FyersFetcher
    fetcher = FyersFetcher()

    for sym in ["RELIANCE", "ALKEM", "ALOKINDS", "PFC", "POONAWALLA"]:
        candidates = fetcher._generate_fyers_candidate_symbols(sym)
        for c in candidates:
            assert not c.endswith("-BE"), f"Candidate {c!r} has invalid -BE suffix for symbol {sym!r}"
            if c.startswith("BSE:"):
                # BSE candidates must end with a valid series suffix
                assert any(
                    c.endswith(sfx) for sfx in ("-EQ", "-INDEX", "-FUT", "-OPT", "-SM", "-ST", "-A", "-B", "-T", "-M", "-X")
                ), f"Bare BSE candidate {c!r} has no series suffix for symbol {sym!r}"
