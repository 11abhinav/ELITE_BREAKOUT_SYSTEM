import logging
import pandas as pd
from typing import List, Dict
from datetime import datetime

from ..core.interfaces import ProviderInterface
from ..core.models import NormalizedMarketData, CapabilityMatrix, ProviderStatus, DataProvenance

logger = logging.getLogger(__name__)

class FyersProvider(ProviderInterface):
    """
    Official Fyers API Integration via the Market Data Platform.
    Acts as the Secondary Fallback Provider.
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
            supports_oi=False
        )
        self._health_score = 95.0
        self._status = ProviderStatus.HEALTHY
        
    @property
    def provider_name(self) -> str:
        return "Fyers"
        
    @property
    def capabilities(self) -> CapabilityMatrix:
        return self._capabilities
        
    def get_health_score(self) -> float:
        return self._health_score
        
    _FYERS_INDEX_MAP = {
        "^NSEI": "NSE:NIFTY50-INDEX",
        "NIFTY 50": "NSE:NIFTY50-INDEX",
        "NIFTY50": "NSE:NIFTY50-INDEX",
        "NIFTY-50": "NSE:NIFTY50-INDEX",
        "^NSEBANK": "NSE:NIFTYBANK-INDEX",
        "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
        "NIFTYBANK": "NSE:NIFTYBANK-INDEX",
        "^CNXIT": "NSE:NIFTYIT-INDEX",
        "NIFTYIT": "NSE:NIFTYIT-INDEX",
        "^CNXAUTO": "NSE:NIFTYAUTO-INDEX",
        "NIFTYAUTO": "NSE:NIFTYAUTO-INDEX",
        "^CNXFMCG": "NSE:NIFTYFMCG-INDEX",
        "NIFTYFMCG": "NSE:NIFTYFMCG-INDEX",
        "^CNXPHARMA": "NSE:NIFTYPHARMA-INDEX",
        "NIFTYPHARMA": "NSE:NIFTYPHARMA-INDEX",
        "^CNXMETAL": "NSE:NIFTYMETAL-INDEX",
        "NIFTYMETAL": "NSE:NIFTYMETAL-INDEX",
        "^CNXREALTY": "NSE:NIFTYREALTY-INDEX",
        "NIFTYREALTY": "NSE:NIFTYREALTY-INDEX",
        "^CNXENERGY": "NSE:NIFTYENERGY-INDEX",
        "NIFTYENERGY": "NSE:NIFTYENERGY-INDEX",
        "^CNXINFRA": "NSE:NIFTYINFRA-INDEX",
        "NIFTYINFRA": "NSE:NIFTYINFRA-INDEX",
        "^CNXPSUBANK": "NSE:NIFTYPSUBANK-INDEX",
        "NIFTYPSUBANK": "NSE:NIFTYPSUBANK-INDEX",
        "^CNXMEDIA": "NSE:NIFTYMEDIA-INDEX",
        "NIFTYMEDIA": "NSE:NIFTYMEDIA-INDEX",
        "NIFTY MEDIA": "NSE:NIFTYMEDIA-INDEX",
        "^CNXFIN": "NSE:FINNIFTY-INDEX",
        "^CNXFINANCE": "NSE:FINNIFTY-INDEX",
        "^CNXCONSUMPTION": "NSE:NIFTYCONSUMPTION-INDEX",
        "^CNXCMDT": "NSE:NIFTYCOMMODITIES-INDEX",
        "^CNXCOMMODITIES": "NSE:NIFTYCOMMODITIES-INDEX",
        "^CNXMNC": "NSE:NIFTYMNC-INDEX",
        "^INDIAVIX": "NSE:INDIAVIX-INDEX",
        "INDIAVIX": "NSE:INDIAVIX-INDEX",
        "INDIA VIX": "NSE:INDIAVIX-INDEX",
        "VIX": "NSE:INDIAVIX-INDEX",
        "^CNXSERVICE": "NSE:NIFTYSERVSECTOR-INDEX",
        "^NSMIDCP": "NSE:NIFTYMIDCAP100-INDEX",
        "^CNXSMALLCAP": "NSE:NIFTYSMALLCAP100-INDEX",
    }

    def _get_fyers_symbol(self, symbol: str) -> str:
        clean = str(symbol).strip().upper()
        if clean in self._FYERS_INDEX_MAP:
            return self._FYERS_INDEX_MAP[clean]
        if clean.startswith("NSE:") or clean.startswith("BSE:"):
            return clean
        if clean.startswith("^"):
            bare = clean.lstrip("^")
            return f"NSE:{bare}-INDEX"
        return f"NSE:{clean}-EQ"

    def fetch_ohlcv(self, symbol: str, timeframe: str, range_from: datetime, range_to: datetime) -> NormalizedMarketData:
        import time
        import random
        
        start_time = datetime.now()
        logger.debug(f"🔄 [Fyers] Fetching {symbol} | {timeframe} | {range_from.date()} → {range_to.date()}")
        
        try:
            token = self.auth_service.get_valid_token(self.provider_name)
            if not token:
                logger.warning(f"⚠️ [Fyers] No valid token for {symbol} — skipping fetch.")
                raise PermissionError("Fyers token invalid")
                
            import fyers_auth
            client = fyers_auth.get_fyers_client()
            
            # Map intervals
            fyers_interval = {"1m": "1", "5m": "5", "15m": "15", "1h": "60", "1d": "1D"}.get(timeframe.lower(), "1D")
            
            fyers_symbol = self._get_fyers_symbol(symbol)
            
            data = {
                "symbol": fyers_symbol,
                "resolution": fyers_interval,
                "date_format": "1",
                "range_from": range_from.strftime("%Y-%m-%d"),
                "range_to": range_to.strftime("%Y-%m-%d"),
                "cont_flag": "1"
            }
            
            backoff = 1.0
            response = None
            latency = 0
            
            for attempt in range(3):
                attempt_start = datetime.now()
                response = client.history(data=data)
                latency = (datetime.now() - start_time).total_seconds() * 1000
                
                if not response or response.get("s") != "ok":
                    code = str(response.get("code", "")) if response else ""
                    # Retry on 403 (Rate limit/temporary ban) or generic API failures
                    if attempt < 2:
                        sleep_time = backoff + random.uniform(0.5, 1.5)
                        logger.warning(f"🚦 [Fyers] API Failure/Rate Limit for {symbol} (code {code}, attempt {attempt+1}/3) — sleeping {sleep_time:.2f}s")
                        time.sleep(sleep_time)
                        backoff *= 2.0
                        continue
                    
                    if code in ("-403", "403"):
                        self._health_score = max(0, self._health_score - 20)
                        self._status = ProviderStatus.DEGRADED
                        return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="403 Permission Denied")
                        
                    self._health_score = max(0, self._health_score - 2)
                    return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), DataProvenance(self.provider_name, start_time, latency, 0), error="API Failure")
                break
                
            candles = response.get("candles", [])
            df = pd.DataFrame(candles, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
            
            from zoneinfo import ZoneInfo
            IST = ZoneInfo("Asia/Kolkata")
            
            df["Datetime"] = pd.to_datetime(df["Timestamp"], unit="s", utc=True).dt.tz_convert(IST)
            df = df.set_index("Datetime").drop(columns=["Timestamp"]).sort_index()

            # [RULE 67 CHANGE-RATIONALE: SYSTEM-WIDE WEEKEND CANDLE BAN]
            from trading_calendar import enforce_trading_day_candles
            df = enforce_trading_day_candles(df, symbol)

            prov = DataProvenance(self.provider_name, start_time, latency, 100.0)
            return NormalizedMarketData(symbol, timeframe, df, prov, is_complete_candle=True)
            
        except Exception as e:
            self._health_score = max(0, self._health_score - 5)
            logger.error(f"Fyers fetch error for {symbol}: {e}")
            latency = (datetime.now() - start_time).total_seconds() * 1000
            prov = DataProvenance(self.provider_name, start_time, latency, 0.0)
            return NormalizedMarketData(symbol, timeframe, pd.DataFrame(), prov, error=str(e))
            
    def fetch_batch_ohlcv(self, symbols: List[str], timeframe: str, range_from: datetime, range_to: datetime) -> Dict[str, NormalizedMarketData]:
        import time
        results = {}
        total = len(symbols)
        logger.info(f"🔄 [Fyers] Starting batch fetch: {total} symbols | {timeframe} | {range_from.date()} → {range_to.date()}")
        t_batch_start = datetime.now()
        errors = 0
        last_request_time = 0
        
        for idx, sym in enumerate(symbols, 1):
            # Throttle to mathematically guarantee max ~9 requests/sec
            now_ts = time.time()
            elapsed_since_last = now_ts - last_request_time
            if elapsed_since_last < 0.11:
                time.sleep(0.11 - elapsed_since_last)
                
            t_sym_start = datetime.now()
            result = self.fetch_ohlcv(sym, timeframe, range_from, range_to)
            last_request_time = time.time()
            
            elapsed_ms = int((datetime.now() - t_sym_start).total_seconds() * 1000)
            results[sym] = result
            if result.error:
                errors += 1
                logger.warning(f"  ⚠️ [Fyers] [{idx}/{total}] {sym} — FAILED in {elapsed_ms}ms: {result.error}")
            else:
                logger.debug(f"  ✅ [Fyers] [{idx}/{total}] {sym} — OK in {elapsed_ms}ms")
            if idx % 25 == 0 or idx == total:
                batch_elapsed = int((datetime.now() - t_batch_start).total_seconds())
                logger.info(f"📊 [Fyers] Progress: {idx}/{total} symbols fetched | {errors} errors so far | Elapsed: {batch_elapsed}s")
        total_elapsed = int((datetime.now() - t_batch_start).total_seconds())
        logger.info(f"✅ [Fyers] Batch complete: {total - errors}/{total} ok | {errors} errors | Total: {total_elapsed}s")
        return results
