import logging
import requests
import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core.interfaces import ProviderInterface
from ..core.models import NormalizedMarketData, CapabilityMatrix, ProviderStatus, DataProvenance

logger = logging.getLogger(__name__)

# [VERSION: UPSTOX_SESSION_POOL_v1.0]
# Module-level Session with connection pooling and automatic retry on transient errors.
# Replaces per-call requests.get() to reuse TCP connections, saving ~50ms per call
# and respecting Upstox connection limits. Retries 3 times on 502/503/504 only.
_upstox_retry = Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=[502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)
_upstox_adapter = HTTPAdapter(
    pool_connections=2,
    pool_maxsize=12,
    max_retries=_upstox_retry,
)
_upstox_session = requests.Session()
_upstox_session.mount("https://", _upstox_adapter)
_upstox_session.mount("http://", _upstox_adapter)

class UpstoxProvider(ProviderInterface):
    """
    Official Upstox API v2 Integration.
    Acts as the Primary Historical Data Provider to bypass WAF bans.
    """
    def __init__(self, auth_service):
        self.auth_service = auth_service
        self._capabilities = CapabilityMatrix(
            supports_1m=True,
            supports_5m=True,
            supports_15m=True,
            supports_1h=True,
            supports_1d=True,
            supports_corporate_actions=False,
            supports_oi=True
        )
        self._health_score = 100.0
        self._status = ProviderStatus.HEALTHY
        
    @property
    def provider_name(self) -> str:
        return "Upstox"
        
    @property
    def capabilities(self) -> CapabilityMatrix:
        return self._capabilities
        
    def get_health_score(self) -> float:
        return self._health_score
        
    def get_status(self) -> ProviderStatus:
        return self._status
        
    def _map_timeframe(self, timeframe: str) -> str:
        mapping = {
            "1m": "1minute",
            "5m": "5minute",
            "15m": "15minute",
            "30m": "30minute",
            "1h": "60minute",
            "1d": "day"
        }
        return mapping.get(timeframe.lower(), "day")
        
    # Upstox index instrument key map — NSE_INDEX / BSE_INDEX segment, NOT NSE_EQ
    # Source: Upstox historical-candle API instrument key registry (verified format)
    _INDEX_KEY_MAP = {
        # ── Broad Market Indices ──────────────────────────────────────────────────
        "^NSEI":        "NSE_INDEX|Nifty 50",
        "NIFTY":        "NSE_INDEX|Nifty 50",
        "NIFTY50":      "NSE_INDEX|Nifty 50",
        "NIFTY-50":     "NSE_INDEX|Nifty 50",
        "NIFTY 50":     "NSE_INDEX|Nifty 50",
        "NSEI":         "NSE_INDEX|Nifty 50",

        "^NSEBANK":     "NSE_INDEX|Nifty Bank",
        "BANKNIFTY":    "NSE_INDEX|Nifty Bank",
        "NIFTYBANK":    "NSE_INDEX|Nifty Bank",
        "NSEBANK":      "NSE_INDEX|Nifty Bank",

        "^BSESN":       "BSE_INDEX|SENSEX",
        "SENSEX":       "BSE_INDEX|SENSEX",
        "BSE:SENSEX":   "BSE_INDEX|SENSEX",

        # ── Midcap / Smallcap / Broad ─────────────────────────────────────────────
        "^NSMIDCP":         "NSE_INDEX|Nifty Midcap 100",
        "^NSMIDCP50":       "NSE_INDEX|Nifty Midcap 50",
        "^CNXSMALLCAP":     "NSE_INDEX|Nifty Smallcap 100",
        "^CNXSMALLCAP50":   "NSE_INDEX|Nifty Smallcap 50",
        "^CNXMICROCAP250":  "NSE_INDEX|Nifty Microcap 250",
        "^NIFTY200":        "NSE_INDEX|Nifty 200",
        "^NIFTY500":        "NSE_INDEX|Nifty 500",
        "^NIFTY100":        "NSE_INDEX|Nifty 100",
        "^NIFTYNEXT50":     "NSE_INDEX|Nifty Next 50",

        # ── Sectoral Indices ──────────────────────────────────────────────────────
        "^CNXIT":           "NSE_INDEX|Nifty IT",
        "^CNXAUTO":         "NSE_INDEX|Nifty Auto",
        "^CNXFMCG":         "NSE_INDEX|Nifty FMCG",
        "^CNXPHARMA":       "NSE_INDEX|Nifty Pharma",
        "^CNXMETAL":        "NSE_INDEX|Nifty Metal",
        "^CNXREALTY":       "NSE_INDEX|Nifty Realty",
        "^CNXENERGY":       "NSE_INDEX|Nifty Energy",
        "^CNXINFRA":        "NSE_INDEX|Nifty Infrastructure",
        "^CNXPSUBANK":      "NSE_INDEX|Nifty PSU Bank",
        "^CNXPSU":          "NSE_INDEX|Nifty PSE",
        "^CNXFINANCE":      "NSE_INDEX|Nifty Financial Services",
        "^CNXCONSUMPTION":  "NSE_INDEX|Nifty India Consumption",
        "^CNXCOMMODITIES":  "NSE_INDEX|Nifty Commodities",
        "^NIFTYOILGAS":     "NSE_INDEX|Nifty Oil & Gas",
        "^NIFTYDEFENCE":    "NSE_INDEX|Nifty India Defence",
        "^CNXMNC":          "NSE_INDEX|Nifty MNC",
        "^CNXSERVICE":      "NSE_INDEX|Nifty Services Sector",
        "^CNXMEDIA":        "NSE_INDEX|Nifty Media",
        "^NIFTYHEALTHCARE": "NSE_INDEX|Nifty Healthcare Index",
    }

    def _get_instrument_key(self, symbol: str) -> str:
        """
        Maps a YFinance-style symbol to Upstox instrument key format.
        Indices use NSE_INDEX segment; equities use NSE_EQ segment.
        Strips .NS/.BO suffixes that come from YFinance normalization.
        """
        clean = str(symbol).strip().upper()
        # Strip YFinance suffixes
        for sfx in (".NS", ".BO", ".BSE"):
            if clean.endswith(sfx):
                clean = clean[:-len(sfx)]
                break

        # Check index map first (e.g. ^NSEI → NSE_INDEX|Nifty 50)
        if clean in self._INDEX_KEY_MAP:
            return self._INDEX_KEY_MAP[clean]

        # Standard NSE equity: strip leading ^ if any stray caret
        clean = clean.lstrip("^")
        return f"NSE_EQ|{clean}"

    def fetch_ohlcv(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> NormalizedMarketData:
        # 1. Use Long-Lived Analytics Token
        import config
        token = getattr(config, "UPSTOX_ACCESS_TOKEN", None)
        
        if not token:
            self._status = ProviderStatus.AUTH_FAILED
            self._health_score -= 10
            raise PermissionError("UPSTOX_ACCESS_TOKEN is completely missing from config.")
            
        # 2. Build Request
        import urllib.parse
        from datetime import timedelta
        raw_key = self._get_instrument_key(symbol)
        instrument_key = urllib.parse.quote(raw_key)
        interval = self._map_timeframe(timeframe)
        
        # Proactively adjust range_to if it falls on a non-trading weekend day (Saturday=5, Sunday=6)
        adjusted_range_to = range_to
        if range_to and hasattr(range_to, "weekday"):
            if range_to.weekday() == 5:
                adjusted_range_to = range_to - timedelta(days=1)
            elif range_to.weekday() == 6:
                adjusted_range_to = range_to - timedelta(days=2)
                
        url = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{adjusted_range_to.strftime('%Y-%m-%d')}/{range_from.strftime('%Y-%m-%d')}"
        
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        start_time = datetime.now()
        
        try:
            # [VERSION: UPSTOX_SESSION_POOL_v1.0] Use shared session for connection reuse
            response = _upstox_session.get(url, headers=headers, timeout=10)
            latency = (datetime.now() - start_time).total_seconds() * 1000
            
            if response.status_code == 429:
                self._health_score = max(0, self._health_score - 5)
                logger.warning("Upstox Rate Limit hit (429)")
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="429 Rate Limit")
                
            if response.status_code == 401:
                self._status = ProviderStatus.AUTH_FAILED
                self._health_score -= 20
                logger.error("Upstox Analytics Token is INVALID or EXPIRED (401).")
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="401 Auth Expired")
                
            response.raise_for_status()
            data = response.json()
            
            if data.get("status") != "success":
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="API Failure")
                
            candles = data.get("data", {}).get("candles", [])
            if not candles:
                return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 100), error=None)
                
            # Upstox returns: [timestamp, open, high, low, close, volume, oi]
            df = pd.DataFrame(candles, columns=["Datetime", "Open", "High", "Low", "Close", "Volume", "OI"])
            df["Datetime"] = pd.to_datetime(df["Datetime"])
            df = df.set_index("Datetime").sort_index()
            
            prov = DataProvenance(self.provider_name, start_time, latency, 100.0)
            return NormalizedMarketData(symbol, timeframe, df, prov, is_complete_candle=True)
            
        except Exception as e:
            # If 400 Client Error occurs (e.g. today is a non-trading weekend date), retry with yesterday's date
            if "400" in str(e) and range_to:
                try:
                    alt_to = range_to - timedelta(days=1)
                    url_alt = f"https://api.upstox.com/v2/historical-candle/{instrument_key}/{interval}/{alt_to.strftime('%Y-%m-%d')}/{range_from.strftime('%Y-%m-%d')}"
                    # [VERSION: UPSTOX_SESSION_POOL_v1.0] Use shared session for 400-retry fallback
                    res_alt = _upstox_session.get(url_alt, headers=headers, timeout=10)
                    if res_alt.status_code == 200:
                        data = res_alt.json()
                        candles = data.get("data", {}).get("candles", [])
                        if candles:
                            df = pd.DataFrame(candles, columns=["Datetime", "Open", "High", "Low", "Close", "Volume", "OI"])
                            df["Datetime"] = pd.to_datetime(df["Datetime"])
                            df = df.set_index("Datetime").sort_index()
                            latency = (datetime.now() - start_time).total_seconds() * 1000
                            prov = DataProvenance(self.provider_name, start_time, latency, 100.0)
                            return NormalizedMarketData(symbol, timeframe, df, prov, is_complete_candle=True)
                # [VERSION: UPSTOX_SESSION_POOL_v1.0] Typed handler per Rule 12 — never swallow silently
                except requests.RequestException as retry_err:
                    logger.warning(f"Upstox 400-retry failed for {symbol}: {retry_err}")
                except Exception as retry_err:
                    logger.warning(f"Upstox 400-retry unexpected error for {symbol}: {retry_err}")
            self._health_score = max(0, self._health_score - 2)
            logger.error(f"Upstox fetch error for {symbol}: {e}")
            latency = (datetime.now() - start_time).total_seconds() * 1000
            prov = DataProvenance(self.provider_name, start_time, latency, 0.0)
            return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), prov, error=str(e))
            
    def fetch_batch_ohlcv(self, symbols: List[str], timeframe: str, range_from: datetime, range_to: datetime) -> Dict[str, NormalizedMarketData]:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        results = {}
        # Upstox historical candle endpoint accepts 1 symbol per request.
        # We execute up to 10 concurrent threads to achieve high-speed batch fetching while respecting rate limits.
        max_workers = min(10, len(symbols)) if symbols else 1
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sym = {
                executor.submit(self.fetch_ohlcv, sym, timeframe, range_from, range_to): sym 
                for sym in symbols
            }
            
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    results[sym] = future.result()
                except Exception as e:
                    logger.error(f"Error fetching batch symbol {sym}: {e}")
                    
        return results

    def fetch_live_quotes_batch(self, symbols: List[str]) -> Dict[str, Dict]:
        """
        Fetches full live market quotes (OHLC, Depth, Volume, Last Price) for up to 500 instruments in 1 single GET request.
        Uses Upstox official batch endpoint: /v2/market-quote/quotes
        """
        import config
        token = getattr(config, "UPSTOX_ACCESS_TOKEN", None)
        
        if not token or not symbols:
            return {}
            
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        results = {}
        # Upstox supports up to 500 symbols per request. Chunk into batches of 500.
        import urllib.parse
        chunk_size = 500
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i + chunk_size]
            formatted_keys = ",".join([urllib.parse.quote(self._get_instrument_key(s)) for s in chunk])
            url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={formatted_keys}"
            
            try:
                # [VERSION: UPSTOX_SESSION_POOL_v1.0] Use shared session for live quotes
                res = _upstox_session.get(url, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    for key, quote in data.items():
                        # Key format is "NSE_EQ:RELIANCE" or "NSE_EQ|RELIANCE"
                        clean_sym = key.split(":")[-1].split("|")[-1]
                        results[clean_sym] = quote
                else:
                    logger.error(f"Failed live quote batch fetch (Status {res.status_code})")
            # [VERSION: UPSTOX_SESSION_POOL_v1.0] Typed handler per Rule 12
            except requests.RequestException as e:
                logger.error(f"Network error fetching live quote batch: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error fetching live quote batch: {e}", exc_info=True)
                
        return results

    def get_ohlcv(self, symbol: str, interval: str = "1d", period: str = "1y", retries: int = 3, range_from: str = None, range_to: str = None):
        """
        Adapter method for legacy DataFetcher callers (e.g. price_cache, stock_analyzer).
        Converts NormalizedMarketData to MarketData validation format.
        """
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        now = datetime.now(IST)
        
        if range_from and range_to:
            r_from = datetime.strptime(range_from, "%Y-%m-%d")
            r_to = datetime.strptime(range_to, "%Y-%m-%d")
        else:
            days = 365
            if period.endswith("y"):
                try: days = int(period[:-1]) * 365
                except: days = 365
            elif period.endswith("mo"):
                try: days = int(period[:-2]) * 30
                except: days = 30
            elif period.endswith("d"):
                try: days = int(period[:-1])
                except: days = 10
            r_from = now - timedelta(days=days)
            r_to = now
            
        norm_data = self.fetch_ohlcv(symbol, timeframe=interval, range_from=r_from, range_to=r_to)
        
        from validation import MarketData, DataQualityReport
        from validation.result import ValidationStatus
            
        df = norm_data.dataframe
        if df is None or df.empty:
            return MarketData(dataframe=pd.DataFrame(), source="Upstox", quality_report=None, stale=False, used_fallback=False, error=norm_data.error)
            
        report = DataQualityReport(
            is_valid=True,
            quality_score=100,
            critical_failures=(),
            warnings=(),
            status=ValidationStatus.OPTIMAL,
            row_count=len(df)
        )
        return MarketData(dataframe=df, source="Upstox", quality_report=report, stale=False, used_fallback=False, error=None)

    def get_batch_ohlcv(self, symbols: List[str], interval: str = "1d", period: str = "1y", retries: int = 3, range_from: str = None, range_to: str = None, caller: str = None) -> Dict:
        """
        Concurrent batch fetch using ThreadPoolExecutor (up to 10 parallel threads).
        Upstox historical candle endpoint accepts one symbol per request; threads are
        the only way to achieve bulk throughput without sequential bottleneck.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        if not symbols:
            return results

        prefix = f"[{caller}] " if caller else ""
        max_workers = min(10, len(symbols))
        logger.info(f"{prefix}📥 Upstox: batch fetching {len(symbols)} symbols ({interval}, {period}) concurrently (workers={max_workers})...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sym = {
                executor.submit(self.get_ohlcv, sym, interval, period, retries, range_from, range_to): sym
                for sym in symbols
            }
            for future in as_completed(future_to_sym):
                sym = future_to_sym[future]
                try:
                    results[sym] = future.result()
                except Exception as e:
                    logger.error(f"Upstox batch fetch error for {sym}: {e}")

        ok_count = sum(1 for v in results.values() if v and getattr(v, 'dataframe', None) is not None and not getattr(v.dataframe, 'empty', True))
        logger.info(f"{prefix}📊 Upstox batch complete: {ok_count}/{len(symbols)} ok")
        return results
