import logging
import requests
import pandas as pd
from typing import List, Dict, Optional
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..core.interfaces import ProviderInterface
from ..core.models import NormalizedMarketData, CapabilityMatrix, ProviderStatus, DataProvenance
from validation import MarketData

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
    def __init__(self, auth_service=None):
        self.auth_service = auth_service
        self._capabilities = CapabilityMatrix(
            supports_1m=True,
            supports_5m=True,
            supports_15m=True,
            supports_1h=False,  # Resampled from 30m candles
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
        
    # [VERSION: UPSTOX_DATE_NORM_v1.0]
    # Normalise the DataFrame column naming so that Upstox output matches Fyers/Yahoo convention:
    #   - Daily intervals (1d, day) → 'Date' column (date-only, no time component)
    #   - Intraday intervals        → 'Datetime' column (full timestamp)
    # This is required because 14 downstream consumers branch on 'if "Date" in df.columns'
    # to detect daily candles and derive delta fetch timestamps.
    # Without this, price_cache.py, eod_scanner.py, request_planner.py etc. would treat
    # daily Upstox candles as intraday — causing wrong indicator windows and stale deltas.
    def _build_ohlcv_df(self, candles: list, timeframe: str) -> 'pd.DataFrame':
        """Build a normalized OHLCV DataFrame from Upstox candle list.
        Daily intervals emit a 'Date' column; intraday emits 'Datetime'.
        """
        df = pd.DataFrame(candles, columns=["Datetime", "Open", "High", "Low", "Close", "Volume", "OI"])
        df["Datetime"] = pd.to_datetime(df["Datetime"])
        is_daily = timeframe.lower() in ("1d", "day", "1day", "d")
        if is_daily:
            # Rename to 'Date' (date-only string) to match Fyers/Yahoo convention
            df["Date"] = df["Datetime"].dt.normalize()  # midnight-normalised Timestamp
            df = df.drop(columns=["Datetime"])
            df = df.sort_values("Date").reset_index(drop=True)
        else:
            df = df.set_index("Datetime").sort_index()
        return df

    def _map_timeframe(self, timeframe: str) -> str:
        tf_clean = str(timeframe).lower().strip()
        mapping = {
            "1m": "1minute",
            "1min": "1minute",
            "1minute": "1minute",
            "3m": "3minute",
            "3min": "3minute",
            "3minute": "3minute",
            "5m": "5minute",
            "5min": "5minute",
            "5minute": "5minute",
            "10m": "10minute",
            "10min": "10minute",
            "10minute": "10minute",
            "15m": "15minute",
            "15min": "15minute",
            "15minute": "15minute",
            "30m": "30minute",
            "30min": "30minute",
            "30minute": "30minute",
            "60m": "60minute",
            "60min": "60minute",
            "60minute": "60minute",
            "1h": "60minute",
            "1hour": "60minute",
            "1d": "day",
            "d": "day",
            "daily": "day",
            "day": "day",
            "1w": "week",
            "w": "week",
            "weekly": "week",
            "week": "week",
            "1mo": "month",
            "mo": "month",
            "monthly": "month",
            "month": "month"
        }
        return mapping.get(tf_clean, "day")
        
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
        """Maps symbol to official Upstox instrument key using SymbolResolutionService."""
        clean = str(symbol).strip().upper()
        for sfx in (".NS", ".BO", ".BSE"):
            if clean.endswith(sfx):
                clean = clean[:-len(sfx)]
                break

        # 1. Fast-path for indices: Always use exact case-sensitive Upstox index keys first
        if clean in self._INDEX_KEY_MAP:
            return self._INDEX_KEY_MAP[clean]
        raw_clean = str(symbol).strip()
        if raw_clean in self._INDEX_KEY_MAP:
            return self._INDEX_KEY_MAP[raw_clean]

        # 2. Dynamic symbol resolution service
        try:
            from symbol_resolution_engine import get_symbol_resolver
            resolved = get_symbol_resolver().resolve(symbol, provider="upstox")
            if resolved and resolved.is_valid and resolved.mapped_symbol:
                return resolved.mapped_symbol
        except Exception:
            pass

        # 3. Dynamic Upstox Instrument Mapper lookup
        try:
            from market_data.providers.upstox_instrument_mapper import get_upstox_instrument_key
            return get_upstox_instrument_key(symbol)
        except Exception:
            clean_bare = clean.lstrip("^")
            return f"NSE_EQ|{clean_bare}"

    def fetch_ohlcv(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> NormalizedMarketData:
        # Handle 1h / 60m by fetching 30m candles from Upstox API and resampling
        tf_clean = str(timeframe).lower()
        if tf_clean in ("1h", "60m", "60minute", "1hour"):
            res_30m = self.fetch_ohlcv(symbol, timeframe="30m", range_from=range_from, range_to=range_to)
            if not res_30m or res_30m.dataframe is None or res_30m.dataframe.empty:
                return res_30m
            df_30m = res_30m.dataframe.copy()
            agg_dict = {
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            }
            if "OI" in df_30m.columns:
                agg_dict["OI"] = "last"
            df_1h = df_30m.resample("1h").agg(agg_dict).dropna(subset=["Close"])
            prov = DataProvenance(self.provider_name, datetime.now(), 0.0, 100.0)
            return NormalizedMarketData(
                symbol=symbol,
                timeframe="1h",
                dataframe=df_1h,
                provenance=prov,
                is_complete_candle=True
            )

        # Handle 15m / 15minute by fetching 1m candles from Upstox API and resampling
        if tf_clean in ("15m", "15min", "15minute"):
            res_1m = self.fetch_ohlcv(symbol, timeframe="1m", range_from=range_from, range_to=range_to)
            if res_1m and res_1m.dataframe is not None and not res_1m.dataframe.empty:
                df_1m = res_1m.dataframe.copy()
                agg_dict = {
                    "Open": "first",
                    "High": "max",
                    "Low": "min",
                    "Close": "last",
                    "Volume": "sum"
                }
                if "OI" in df_1m.columns:
                    agg_dict["OI"] = "last"
                df_15m = df_1m.resample("15m").agg(agg_dict).dropna(subset=["Close"])
                prov = DataProvenance(self.provider_name, datetime.now(), 0.0, 100.0)
                return NormalizedMarketData(
                    symbol=symbol,
                    timeframe="15m",
                    dataframe=df_15m,
                    provenance=prov,
                    is_complete_candle=True
                )
        import config
        import urllib.parse
        from datetime import timedelta
        
        token = getattr(config, "UPSTOX_ACCESS_TOKEN", None)
        raw_key = self._get_instrument_key(symbol)
        instrument_key = urllib.parse.quote(raw_key)
        interval = self._map_timeframe(timeframe)
        
        # Upstox V2 API does not support intraday historical candles for indices (NSE_INDEX / BSE_INDEX)
        if (raw_key.startswith("NSE_INDEX|") or raw_key.startswith("BSE_INDEX|")) and interval not in ("day", "week", "month"):
            logger.debug(f"Upstox API does not support intraday candles for index {symbol} ({raw_key}); deferring to fallback.")
            start_time = datetime.now()
            prov = DataProvenance(self.provider_name, start_time, 0.0, 0)
            return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), prov, error="Intraday index candles not supported by Upstox")
        
        if not token:
            self._status = ProviderStatus.AUTH_FAILED
            self._health_score -= 10
            raise PermissionError("UPSTOX_ACCESS_TOKEN is completely missing from config.")
            
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
                
            df = self._build_ohlcv_df(candles, timeframe)
            
            prov = DataProvenance(
                provider_name=self.provider_name,
                timestamp=start_time,
                latency_ms=latency,
                quality_score=100.0
            )
            
            return NormalizedMarketData(
                symbol=symbol,
                timeframe=timeframe,
                dataframe=df,
                provenance=prov,
                is_complete_candle=True,
                error=None
            )
            
        except requests.HTTPError as e:
            self._health_score = max(0, self._health_score - 2)
            logger.error(f"Upstox fetch HTTP error for {symbol}: {e}")
            latency = (datetime.now() - start_time).total_seconds() * 1000
            prov = DataProvenance(self.provider_name, start_time, latency, 0.0)
            return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), prov, error=f"HTTP {e.response.status_code if e.response is not None else 'Error'}")
        except Exception as e:
            self._health_score = max(0, self._health_score - 2)
            logger.error(f"Upstox fetch error for {symbol}: {e}")
            latency = (datetime.now() - start_time).total_seconds() * 1000
            prov = DataProvenance(self.provider_name, start_time, latency, 0.0)
            return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), prov, error=str(e))

    def fetch_batch_ohlcv(self, symbols: List[str], timeframe: str, range_from: datetime, range_to: datetime) -> Dict[str, NormalizedMarketData]:
        """Fetches batch normalized market data concurrently for multiple symbols."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results = {}
        if not symbols:
            return results
        max_workers = min(10, len(symbols))
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

    def get_quote(self, symbol: str) -> dict:
        """Fetches live market quote for a single symbol from Upstox v2 API."""
        quotes = self.get_quotes([symbol])
        return quotes.get(symbol, {})

    def get_quotes(self, symbols: List[str]) -> Dict[str, dict]:
        """Fetches live market quotes for multiple symbols from Upstox v2 API."""
        if not symbols:
            return {}
            
        import config
        import urllib.parse
        token = getattr(config, "UPSTOX_ACCESS_TOKEN", None)
        if not token:
            return {}
            
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}"
        }
        
        results = {}
        chunk_size = 500
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i:i + chunk_size]
            formatted_keys = ",".join([urllib.parse.quote(self._get_instrument_key(s)) for s in chunk])
            url = f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={formatted_keys}"
            
            try:
                res = _upstox_session.get(url, headers=headers, timeout=10)
                if res.status_code == 200:
                    data = res.json().get("data", {})
                    for key, quote in data.items():
                        clean_sym = key.split(":")[-1].split("|")[-1]
                        results[clean_sym] = quote
                else:
                    logger.error(f"Failed live quote batch fetch (Status {res.status_code})")
            except requests.RequestException as e:
                logger.error(f"Network error fetching live quote batch: {e}", exc_info=True)
            except Exception as e:
                logger.error(f"Unexpected error fetching live quote batch: {e}", exc_info=True)
                
        return results

    def get_ohlcv(self, symbol: str, interval: str = "1d", period: str = "1y", retries: int = 3, range_from = None, range_to = None) -> MarketData:
        """
        Adapter method for legacy DataFetcher callers.
        Converts NormalizedMarketData to MarketData validation format.
        """
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")
        now = datetime.now(IST)
        
        if range_from and range_to:
            r_from = datetime.strptime(range_from, "%Y-%m-%d") if isinstance(range_from, str) else range_from
            r_to = datetime.strptime(range_to, "%Y-%m-%d") if isinstance(range_to, str) else range_to
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
        
        from validation import ValidationEngine, MarketData, ValidationContext, registry as val_registry, DatasetType
            
        df = norm_data.dataframe
        if df is None or df.empty:
            return MarketData(dataframe=pd.DataFrame(), source="Upstox", quality_report=None, stale=False, used_fallback=False, error=norm_data.error or "Empty DataFrame")
            
        try:
            pipeline = val_registry.get_pipeline(DatasetType.PRICE)
            engine = ValidationEngine(pipeline.validator, pipeline.score_calculator)
            r_from_str = r_from.strftime("%Y-%m-%d") if hasattr(r_from, "strftime") else str(r_from)
            r_to_str = r_to.strftime("%Y-%m-%d") if hasattr(r_to, "strftime") else str(r_to)
            ctx = ValidationContext(provider="Upstox", period=period, interval=interval, range_from=r_from_str, range_to=r_to_str, fetch_mode="DELTA" if range_from else "FULL")
            report = engine.validate(df, ctx)
            
            if not report.is_valid:
                return MarketData(dataframe=None, source="Upstox", quality_report=report, stale=False, used_fallback=False, error="Quality Check Failed")
            return MarketData(dataframe=df, source="Upstox", quality_report=report, stale=False, used_fallback=False, error=None)
        except Exception as val_err:
            logger.warning(f"ValidationEngine exception for Upstox {symbol}: {val_err}")
            return MarketData(dataframe=df, source="Upstox", quality_report=None, stale=False, used_fallback=False, error=None)

    def get_batch_ohlcv(self, symbols: List[str], interval: str = "1d", period: str = "1y", retries: int = 3, range_from = None, range_to = None, caller: str = None) -> Dict:
        """
        Concurrent batch fetch using ThreadPoolExecutor.
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
                    logger.error(f"Upstox batch fetch exception for {sym}: {e}")

        ok_count = sum(1 for v in results.values() if v and getattr(v, 'dataframe', None) is not None and not getattr(v.dataframe, 'empty', True))
        logger.info(f"{prefix}📊 Upstox batch complete: {ok_count}/{len(symbols)} ok")
        return results
