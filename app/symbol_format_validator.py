"""
app/symbol_format_validator.py
==============================
Centralized symbol format gate that guarantees the correct ticker format
is dispatched to each data provider:

  Fyers API v3
  ─────────────
  ✅  NSE:SYMBOL-EQ          e.g. NSE:RELIANCE-EQ
  ✅  BSE:SYMBOL-EQ          e.g. BSE:ALKEM-EQ
  ✅  BSE:5XXXXX-EQ          e.g. BSE:524000-EQ   (numeric scrip codes)
  ✅  NSE:NIFTY50-INDEX
  ✅  NSE:NIFTYBANK-INDEX
  ✅  BSE:SENSEX-INDEX
  ✅  NSE:SYMBOL-FUT / NSE:SYMBOL-OPT (derivatives, cont_flag required)
  ❌  NSE:SYMBOL-BE          (BE series → map to -EQ)
  ❌  BSE:SYMBOL  (bare, no series suffix)
  ❌  SYMBOL.NS / SYMBOL.BO  (Yahoo format, rejected by Fyers)

  Yahoo Finance (yfinance)
  ─────────────────────────
  ✅  SYMBOL.NS              e.g. RELIANCE.NS
  ✅  SYMBOL.BO              e.g. ALKEM.BO
  ✅  ^NSEI / ^NSEBANK / ^BSESN  (index tickers)
  ❌  NSE:SYMBOL-EQ          (Fyers format, rejected by Yahoo)
  ❌  Bare SYMBOL without exchange suffix
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── Fyers valid patterns (including -BE, -SM, -ST, -T, -A, -B, -M, -XC, -XD, -XT) ───────
_FYERS_VALID_RE = re.compile(
    r"^(NSE|BSE|MCX):[A-Z0-9&\.\-]+(-EQ|-BE|-SM|-ST|-A|-B|-T|-M|-X|-XC|-XD|-XT|-INDEX|-FUT|-OPT)$"
)

# ── Yahoo Finance valid patterns (e.g. RELIANCE.NS, ALKEM.BO, ^NSEI, ^BSESN) ────────────
_YAHOO_VALID_RE = re.compile(
    r"^(\^[A-Z0-9]+|[A-Z0-9&\.\-]+(\.NS|\.BO))$"
)


def validate_fyers_symbol(sym: str) -> str:
    """
    Validates a Fyers API v3 symbol.
    """
    if not sym or not isinstance(sym, str):
        raise ValueError(f"Fyers symbol must be a non-empty string, got: {sym!r}")

    sym = sym.strip().upper()

    # Auto-fix: bare BSE symbol without series suffix
    if re.match(r"^BSE:[A-Z0-9&\.]+$", sym):
        fixed = sym + "-EQ"
        logger.warning(f"🔧 [FyersFormat] Auto-fixed bare BSE symbol: {sym!r} → {fixed!r}")
        sym = fixed

    if not _FYERS_VALID_RE.match(sym):
        raise ValueError(
            f"Invalid Fyers symbol format: {sym!r}. "
            "Expected format: EXCHANGE:SYMBOL-SERIES (e.g. NSE:RELIANCE-EQ, BSE:ALKEM-EQ, NSE:NIFTY50-INDEX)"
        )

    return sym


def validate_yahoo_symbol(sym: str) -> str:
    """
    Validates a Yahoo Finance symbol.

    Rules (enforced by yfinance):
    1. Index tickers must begin with '^' (e.g. ^NSEI, ^NSEBANK, ^BSESN).
    2. Equity tickers must end with '.NS' (NSE) or '.BO' (BSE).
    3. MUST NOT use Fyers-style exchange prefix (NSE:, BSE:).

    Returns the validated symbol or raises ValueError if fundamentally wrong.
    """
    if not sym or not isinstance(sym, str):
        raise ValueError(f"Yahoo symbol must be a non-empty string, got: {sym!r}")

    sym = sym.strip()
    upper = sym.upper()

    # Reject Fyers-format symbols passed to Yahoo by accident
    if upper.startswith("NSE:") or upper.startswith("BSE:") or upper.startswith("MCX:"):
        # Try to auto-recover to Yahoo format
        base = upper.split(":")[1]
        for sfx in ("-EQ", "-BE", "-SM", "-ST", "-A", "-B", "-INDEX", "-FUT", "-OPT"):
            if base.endswith(sfx):
                base = base[: -len(sfx)]
                break
        is_index = upper.endswith("-INDEX")
        if is_index:
            # Index → try known mapping
            _index_map = {
                "NIFTY50": "^NSEI",
                "NIFTYBANK": "^NSEBANK",
                "SENSEX": "^BSESN",
            }
            mapped = _index_map.get(base)
            if mapped:
                logger.warning(f"🔧 [YahooFormat] Auto-fixed Fyers index: {sym!r} → {mapped!r}")
                return mapped
        is_bse = upper.startswith("BSE:")
        fixed = base + (".BO" if is_bse else ".NS")
        logger.warning(f"🔧 [YahooFormat] Auto-fixed Fyers-format symbol for Yahoo: {sym!r} → {fixed!r}")
        return fixed

    if not _YAHOO_VALID_RE.match(upper):
        raise ValueError(
            f"Invalid Yahoo Finance symbol format: {sym!r}. "
            "Expected: SYMBOL.NS, SYMBOL.BO, or ^INDEX (e.g. RELIANCE.NS, ALKEM.BO, ^NSEI)"
        )

    return sym


def sanitize_fyers_candidate_list(candidates: list) -> list:
    """
    Filters a Fyers candidate list, removing any invalid formats with a warning log.
    Auto-fixes -BE → -EQ and bare BSE→-EQ. Returns only valid candidates.
    """
    validated = []
    for sym in candidates:
        try:
            validated.append(validate_fyers_symbol(sym))
        except ValueError as e:
            logger.warning(f"🚫 [FyersFormat] Dropping invalid candidate: {sym!r} — {e}")
    return validated


def sanitize_yahoo_ticker_list(tickers: list) -> list:
    """
    Filters a yfinance ticker list, removing any invalid formats with a warning log.
    Auto-fixes Fyers-format → Yahoo format where possible. Returns only valid tickers.
    """
    validated = []
    for sym in tickers:
        try:
            validated.append(validate_yahoo_symbol(sym))
        except ValueError as e:
            logger.warning(f"🚫 [YahooFormat] Dropping invalid ticker: {sym!r} — {e}")
    return validated
