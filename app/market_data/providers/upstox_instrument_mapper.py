"""
[VERSION: UPSTOX_INSTRUMENT_MAPPER_v1.0]
Upstox Instrument Key Mapper — Maps trading symbols to official Upstox instrument keys.

Upstox API v2 REST endpoints require exact instrument keys (e.g. NSE_EQ|INE467B01029 for TCS)
rather than bare equity tickers like NSE_EQ|TCS, which return HTTP 400 Bad Request.

Features:
  1. High-frequency static fallback map for Nifty 50 / Nifty 500 top stocks & major indices.
  2. Dynamic downloader for Upstox complete master CSV (https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz).
  3. PostgreSQL DB state persistence (upstox_instrument_map) & local disk caching.
  4. Automatic background refresh every 7 days.
"""

import os
import json
import logging
import gzip
import csv
import urllib.request
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# v3: fixed inst_type filter FUTSTK/FUTIDX (Upstox CSV never uses generic "FUT");
# also added next-month fallback in get_futures_instrument_key so the scanner
# resolves correctly when Upstox rolls their CSV before NSE expiry.
_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "artifacts", "cache", "upstox_instruments_v3.json"
)
_DB_STATE_KEY = "upstox_instrument_map_v3"

# ── Static Fallback Map for High-Frequency Stocks & Indices ──────────────────
# Prevents network dependency during cold starts or offline unit tests.
_STATIC_SYMBOL_MAP = {
    # Broad Indices
    "^NSEI": "NSE_INDEX|Nifty 50",
    "NIFTY": "NSE_INDEX|Nifty 50",
    "NIFTY50": "NSE_INDEX|Nifty 50",
    "NIFTY 50": "NSE_INDEX|Nifty 50",
    "NSEI": "NSE_INDEX|Nifty 50",
    "^NSEBANK": "NSE_INDEX|Nifty Bank",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "NIFTYBANK": "NSE_INDEX|Nifty Bank",
    "^BSESN": "BSE_INDEX|SENSEX",
    "SENSEX": "BSE_INDEX|SENSEX",
    "^INDIAVIX": "NSE_INDEX|India VIX",
    "INDIAVIX": "NSE_INDEX|India VIX",
    "INDIA VIX": "NSE_INDEX|India VIX",
    "VIX": "NSE_INDEX|India VIX",

    # Sectoral Indices
    "^CNXIT": "NSE_INDEX|Nifty IT",
    "NIFTYIT": "NSE_INDEX|Nifty IT",
    "^CNXAUTO": "NSE_INDEX|Nifty Auto",
    "^CNXFMCG": "NSE_INDEX|Nifty FMCG",
    "^CNXPHARMA": "NSE_INDEX|Nifty Pharma",
    "^CNXMETAL": "NSE_INDEX|Nifty Metal",
    "^CNXREALTY": "NSE_INDEX|Nifty Realty",
    "^CNXENERGY": "NSE_INDEX|Nifty Energy",
    "^CNXINFRA": "NSE_INDEX|Nifty Infra",
    "^CNXPSUBANK": "NSE_INDEX|Nifty PSU Bank",
    "^CNXFIN": "NSE_INDEX|Nifty Fin Service",
    "^CNXFINANCE": "NSE_INDEX|Nifty Fin Service",
    "^CNXCMDT": "NSE_INDEX|Nifty Commodities",
    "^CNXCOMMODITIES": "NSE_INDEX|Nifty Commodities",

    # Top Equities (ISIN Keys)
    "TCS": "NSE_EQ|INE467B01029",
    "RELIANCE": "NSE_EQ|INE002A01018",
    "INFY": "NSE_EQ|INE009A01021",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
    "SBIN": "NSE_EQ|INE062A01020",
    "LTIM": "NSE_EQ|INE214T01019",
    "ITC": "NSE_EQ|INE154A01025",
    "KOTAKBANK": "NSE_EQ|INE237A01036",
    "LT": "NSE_EQ|INE018A01030",
    "AXISBANK": "NSE_EQ|INE238A01034",
    "HINDUNILVR": "NSE_EQ|INE030A01027",
    "BAJFINANCE": "NSE_EQ|INE296A01024",
    "MARUTI": "NSE_EQ|INE585B01010",
    "ASIANPAINT": "NSE_EQ|INE021A01026",
    "SUNPHARMA": "NSE_EQ|INE044A01036",
    "TITAN": "NSE_EQ|INE280A01028",
    "ULTRACEMCO": "NSE_EQ|INE481G01011",
    "TATAMOTORS": "NSE_EQ|INE155A01022",
    "TATASTEEL": "NSE_EQ|INE081A01020",
    "TMPV": "NSE_EQ|INE155A01022",
    "TMCV": "NSE_EQ|INE155A01022",
    "NTPC": "NSE_EQ|INE733E01010",
    "POWERGRID": "NSE_EQ|INE752E01010",
    "ONGC": "NSE_EQ|INE213A01029",
    "JSWSTEEL": "NSE_EQ|INE019A01038",
    "COALINDIA": "NSE_EQ|INE522F01014",
    "M&M": "NSE_EQ|INE101A01026",
    "ADANIENT": "NSE_EQ|INE423A01024",
    "STLTECH": "NSE_EQ|INE089C01029",
    "LTF": "NSE_EQ|INE498L01015",
    "L&TFH": "NSE_EQ|INE498L01015",
    "L_TFH": "NSE_EQ|INE498L01015",
    "GMRAIRPORT": "NSE_EQ|INE776C01039",
    "GMRINFRA": "NSE_EQ|INE776C01039",
    "GUJGAS": "NSE_EQ|INE844O01030",
    "GUJGASLTD": "NSE_EQ|INE844O01030",
    "PEL": "NSE_EQ|INE140A01024",
    "UNITDSPR": "NSE_EQ|INE854D01024",
    "MCDOWELL-N": "NSE_EQ|INE854D01024",
}


class UpstoxInstrumentMapper:
    """Singleton Instrument Key Mapper for Upstox API v2."""

    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._symbol_map = dict(_STATIC_SYMBOL_MAP)
                cls._instance._last_download_ts = 0.0
                cls._instance._is_downloading = False
                cls._instance._load_cache()
            return cls._instance

    def _load_cache(self):
        """Loads cached instrument map from DB or local disk if available."""
        # [PHASE1_DIAG] Track source for warmup log
        _load_source = None

        # 1. Try local disk
        if os.path.exists(_CACHE_FILE):
            try:
                mtime = os.path.getmtime(_CACHE_FILE)
                if (time.time() - mtime) < (7 * 86400):  # 7 days
                    with open(_CACHE_FILE, "r") as f:
                        cached = json.load(f)
                    if isinstance(cached, dict) and len(cached) > 100:
                        self._symbol_map.update(cached)
                        self._last_download_ts = mtime
                        _load_source = "disk"
            except Exception as e:
                logger.warning(f"Failed to read disk cache for Upstox instruments: {e}")

        # 2. Try DB state
        if _load_source is None:
            try:
                from database import get_system_state
                db_raw = get_system_state(_DB_STATE_KEY)
                if db_raw:
                    db_data = json.loads(db_raw) if isinstance(db_raw, str) else db_raw
                    if isinstance(db_data, dict) and len(db_data) > 100:
                        self._symbol_map.update(db_data)
                        _load_source = "db"
            except Exception as e:
                logger.debug(f"DB load for Upstox instrument map failed: {e}")

        # [PHASE1_DIAG] Warmup verification log — confirms map is ready and its size
        static_count = len(_STATIC_SYMBOL_MAP)
        total_count = len(self._symbol_map)
        if _load_source:
            logger.info(
                f"[WARMUP] Upstox instrument map ready: {total_count} keys "
                f"(static={static_count}, dynamic={total_count - static_count}, source={_load_source})"
            )
        else:
            logger.info(
                f"[WARMUP] Upstox instrument map starting with static fallback only: "
                f"{total_count} keys — triggering background download"
            )
            self.trigger_background_download()

    def trigger_background_download(self, force: bool = False):
        """Downloads the Upstox complete master CSV in a background thread."""
        with self._lock:
            if self._is_downloading:
                return
            if not force and (time.time() - self._last_download_ts) < (86400 * 3):  # Avoid downloading more than once per 3 days
                return
            self._is_downloading = True

        threading.Thread(
            target=self._download_master_csv,
            name="UpstoxMasterDownload",
            daemon=True
        ).start()

    def _download_master_csv(self):
        """Worker that fetches, parses, and saves Upstox complete master contract CSV."""
        logger.info("📥 Downloading Upstox complete master instrument contract file...")
        url = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"
        import ssl
        ssl_ctx = ssl._create_unverified_context()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                content = resp.read()

            buf = gzip.decompress(content).decode("utf-8").splitlines()
            reader = csv.reader(buf)
            header = next(reader, None)

            if not header or len(header) < 12:
                logger.warning("Upstox master CSV header invalid.")
                return

            new_map = dict(_STATIC_SYMBOL_MAP)
            for row in reader:
                if len(row) >= 12:
                    inst_key = row[0]
                    tradingsymbol = row[2].strip().upper()
                    name = row[3].strip().upper()
                    inst_type = row[9].strip().upper()
                    exchange = row[11].strip().upper()

                    if inst_type in ("EQ", "EQUITY", "SM", "ST", "SME", "BE", "BZ") and exchange in ("NSE_EQ", "BSE_EQ"):
                        # Save both symbol alone (TCS) and exchange-prefixed (NSE_EQ:TCS)
                        if tradingsymbol not in new_map or exchange == "NSE_EQ":
                            new_map[tradingsymbol] = inst_key
                            new_map[f"{exchange}:{tradingsymbol}"] = inst_key

                    elif inst_type in ("FUTSTK", "FUTIDX") and exchange == "NSE_FO":
                        # [FIX: NSE_FO_FUTURES_INGESTION] Upstox CSV uses FUTSTK (stock
                        # futures) and FUTIDX (index futures) — never generic "FUT".
                        # Store under "NSE_FO:<TRADING_SYMBOL>" for get_futures_instrument_key().
                        if tradingsymbol:
                            new_map[f"NSE_FO:{tradingsymbol}"] = inst_key

                    elif inst_type == "INDEX" or exchange in ("NSE_INDEX", "BSE_INDEX"):
                        if tradingsymbol:
                            new_map[tradingsymbol] = inst_key
                            new_map[f"^{tradingsymbol}"] = inst_key
                        if name:
                            new_map[name] = inst_key
                            new_map[f"^{name}"] = inst_key

            # Ensure static index mappings (e.g. NSE_INDEX|Nifty 50) take top priority for indices
            index_static = {k: v for k, v in _STATIC_SYMBOL_MAP.items() if "INDEX" in v}
            new_map.update(index_static)

            with self._lock:
                self._symbol_map.update(new_map)
                self._last_download_ts = time.time()
                self._is_downloading = False

            # Persist to disk
            os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
            with open(_CACHE_FILE, "w") as f:
                json.dump(new_map, f)

            # Persist to DB (versioned key — v2 includes NSE_FO futures rows)
            try:
                from database import save_system_state
                save_system_state(_DB_STATE_KEY, json.dumps(new_map))
            except Exception as e:
                logger.warning(f"Could not save {_DB_STATE_KEY} to DB: {e}")

            nse_fo_count = sum(1 for k in new_map if k.startswith("NSE_FO:"))
            # [PHASE1_DIAG] Post-download warmup log
            logger.info(
                f"[WARMUP] Upstox instrument map updated via download: {len(new_map)} keys "
                f"(static={len(_STATIC_SYMBOL_MAP)}, dynamic={len(new_map) - len(_STATIC_SYMBOL_MAP)}, "
                f"nse_fo_futures={nse_fo_count})"
            )

        except Exception as e:
            logger.warning(f"Failed to download Upstox master instrument CSV: {e}")
        finally:
            with self._lock:
                self._is_downloading = False

    def get_futures_instrument_key(self, trading_symbol: str) -> Optional[str]:
        """Resolves the official Upstox NSE_FO instrument key for a futures trading symbol.

        The trading_symbol must be the exact NSE F&O contract name as generated by
        fno_contract_resolver, e.g. 'AARTIIND26SEPFUT', 'M_M26SEPFUT', 'BAJAJ_AUTO26SEPFUT'.

        Returns the NSE_FO instrument key (e.g. 'NSE_FO|53806') or None if not found.
        NEVER falls back to equity (NSE_EQ) keys — a futures lookup failure is explicit.

        Next-month fallback: Upstox sometimes drops near-month contracts from their
        master CSV before NSE expiry. If the near-month key is missing, we try the
        next calendar month so OI still resolves correctly during the rollover window.
        """
        if not trading_symbol:
            return None
        clean = str(trading_symbol).strip().upper()
        lookup_key = f"NSE_FO:{clean}"
        result = self._symbol_map.get(lookup_key)
        if result:
            logger.debug(f"[FUT_KEY] {clean} → {result}")
            return result

        # [ROLLOVER FALLBACK] Upstox CSV may drop near-month rows before NSE expiry.
        # Try swapping the 3-letter month code to the next calendar month.
        # e.g. AARTIIND26SEPFUT → AARTIIND26OCTFUT when Oct contracts replace Sep.
        import re
        _MONTH_ROLL = {
            "JAN": "FEB", "FEB": "MAR", "MAR": "APR",
            "APR": "MAY", "MAY": "JUN", "JUN": "JUL",
            "JUL": "AUG", "AUG": "SEP", "SEP": "OCT",
            "OCT": "NOV", "NOV": "DEC", "DEC": "JAN",
        }
        m = re.search(r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(FUT)$", clean)
        if m:
            yr, mon, suffix = m.group(1), m.group(2), m.group(3)
            next_mon = _MONTH_ROLL[mon]
            # If rolling Dec→Jan the year increments; derive from fno_contract_resolver at call time
            next_yr = str(int(yr) + 1).zfill(2) if mon == "DEC" else yr
            next_clean = clean[:m.start()] + next_yr + next_mon + suffix
            next_result = self._symbol_map.get(f"NSE_FO:{next_clean}")
            if next_result:
                logger.warning(
                    f"[FUT_KEY ROLLOVER] Near-month '{clean}' not in map — "
                    f"Upstox CSV rolled early. Using next-month '{next_clean}' → {next_result}"
                )
                return next_result

        logger.debug(
            f"⚠️ [FUT_KEY] NSE_FO key not found for '{clean}' — "
            f"master CSV may not have been downloaded yet or contract has expired."
        )
        return None


    def get_instrument_key(self, symbol: str, allow_fallback: bool = True) -> Optional[str]:
        """Maps symbol to official Upstox instrument key."""
        if not symbol:
            return None

        clean = str(symbol).strip().upper()
        # Strip YFinance suffixes
        for sfx in (".NS", ".BO", ".BSE"):
            if clean.endswith(sfx):
                clean = clean[:-len(sfx)]
                break

        if not clean or clean in ('?', 'NONE', 'NAN', 'NULL', 'UNKNOWN') or not any(c.isalnum() for c in clean):
            return None

        # 0. Check if clean is ISIN (e.g. INE989C01038)
        if clean.startswith("INE") and len(clean) == 12:
            isin_key = f"NSE_EQ|{clean}"
            if isin_key in self._symbol_map:
                return isin_key
            # Search values in _symbol_map for ISIN
            for k, v in self._symbol_map.items():
                if clean in v:
                    return v

        # 1. Check in-memory master map first (pre-loaded from Upstox master contract CSV)
        if clean in self._symbol_map:
            return self._symbol_map[clean]

        clean_caret = clean if clean.startswith("^") else f"^{clean}"
        if clean_caret in self._symbol_map:
            return self._symbol_map[clean_caret]

        raw_no_caret = clean.lstrip("^")
        if raw_no_caret in self._symbol_map:
            return self._symbol_map[raw_no_caret]

        # 2. Check institutional InstrumentRegistry if not found in master CSV
        try:
            from instrument_registry import get_instrument_registry
            rec = get_instrument_registry().lookup(clean)
            if rec and rec.upstox_instrument_key:
                return rec.upstox_instrument_key
        except Exception:
            pass

        if not allow_fallback:
            return None

        # [RULE 3C Architectural Fix] Do NOT manufacture fake NSE_EQ keys if symbol is not in Upstox master CSV or InstrumentRegistry.
        # Returning None forces explicit RESOLUTION_FAILED status instead of generating bad API requests.
        logger.warning(f"⚠️ [UPSTOX MAPPER] Symbol '{symbol}' not found in Upstox master contract CSV or InstrumentRegistry — returning RESOLUTION_FAILED.")
        return None


# Global accessor
mapper = UpstoxInstrumentMapper()

def get_upstox_instrument_key(symbol: str) -> str:
    """Global helper function to map any symbol to Upstox instrument key."""
    return mapper.get_instrument_key(symbol)


def get_upstox_futures_key(trading_symbol: str) -> Optional[str]:
    """Global helper: resolve NSE_FO instrument key for a futures trading symbol.

    Example: get_upstox_futures_key('AARTIIND26SEPFUT') → 'NSE_FO|53806'
    Returns None (never raises) if not found.
    """
    return mapper.get_futures_instrument_key(trading_symbol)
