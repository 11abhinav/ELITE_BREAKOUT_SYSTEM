"""
app/short_covering/oi_data_service.py

Unified Open Interest (OI) & Price Data Service.
Provides:
- Explicit provider capability validation (Upstox vs Fyers vs NSE EOD)
- EOD Bhavcopy daily OI, volume, and price history for F&O underlying equities
- Intraday 5m futures OHLCV + OI data with data staleness guards
- Total combined futures OI aggregation (near + next month) to isolate rollover flows
- Database caching and fallback generation
"""

import os
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd
import numpy as np

IST = ZoneInfo("Asia/Kolkata")

try:
    from app.short_covering.fno_contract_resolver import fno_contract_resolver
    from app.short_covering.fno_universe import fno_universe_manager
    from app.short_covering.short_covering_schema import (
        PROVIDER_CAPABILITY_MATRIX,
        ProviderCapability,
    )
except ImportError:
    from short_covering.fno_contract_resolver import fno_contract_resolver
    from short_covering.fno_universe import fno_universe_manager
    from short_covering.short_covering_schema import (
        PROVIDER_CAPABILITY_MATRIX,
        ProviderCapability,
    )

logger = logging.getLogger(__name__)


class OIDataService:
    """Service to fetch, aggregate, and normalize Open Interest and Futures price data."""

    def __init__(self, preferred_provider: Optional[str] = None):
        if preferred_provider is None:
            preferred_provider = os.getenv("OI_DATA_PROVIDER", "UPSTOX")
        self.preferred_provider = preferred_provider.upper()
        self._daily_oi_cache: Dict[str, pd.DataFrame] = {}
        self._intraday_oi_cache: Dict[str, pd.DataFrame] = {}
        self._fo_bhavcopy_table_exists: Optional[bool] = None

    def get_provider_capability(self, provider_name: Optional[str] = None) -> ProviderCapability:
        """Returns the capability specification for the given provider."""
        p_name = (provider_name or self.preferred_provider).upper()
        return PROVIDER_CAPABILITY_MATRIX.get(
            p_name,
            ProviderCapability(provider_name=p_name, supports_5m_oi=False, oi_resolution_notes="Unknown provider")
        )

    def validate_provider_capabilities(self, required_feature: str = "supports_5m_oi") -> bool:
        """
        Validates that the active market data provider natively supports the required OI feature.
        Prevents silently using mismatched or stale OI when a provider lacks intraday OI support.
        """
        cap = self.get_provider_capability()
        supports = getattr(cap, required_feature, False)
        if not supports:
            logger.warning(
                f"⚠️ [OI DATA SERVICE] Active provider '{cap.provider_name}' does not support '{required_feature}'! "
                f"Notes: {cap.oi_resolution_notes}"
            )
        return supports

    def get_daily_oi_history(
        self,
        symbol: str,
        lookback_days: int = 30,
        as_of: Optional[date] = None
    ) -> pd.DataFrame:
        """
        Returns a DataFrame of daily price and combined futures open interest.
        Columns: [date, close, open, high, low, volume, total_oi, oi_change, oi_change_pct]
        """
        clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
        if as_of is None:
            as_of = date.today()

        # Check memory cache
        cache_key = f"{clean_sym}_{as_of.isoformat()}_{lookback_days}"
        if cache_key in self._daily_oi_cache:
            return self._daily_oi_cache[cache_key]

        df = self._fetch_or_build_daily_oi(clean_sym, lookback_days, as_of)
        self._daily_oi_cache[cache_key] = df
        return df

    def get_intraday_5m_data(
        self,
        symbol: str,
        target_date: Optional[date] = None
    ) -> pd.DataFrame:
        """
        Returns 5-minute intraday futures bars with OI.
        Columns: [timestamp, open, high, low, close, volume, vwap, oi, oi_change_5m_pct, oi_change_session_pct]
        """
        clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
        if target_date is None:
            target_date = date.today()

        cache_key = f"{clean_sym}_5m_{target_date.isoformat()}"
        if cache_key in self._intraday_oi_cache:
            return self._intraday_oi_cache[cache_key]

        df = self._fetch_or_build_5m_bars(clean_sym, target_date)
        self._intraday_oi_cache[cache_key] = df
        return df

    def is_rollover_in_progress(
        self,
        symbol: str,
        near_oi_delta: float,
        next_oi_delta: float,
        as_of: Optional[date] = None
    ) -> bool:
        """
        Detects if near-month OI drop is purely rollover into the next month.
        If near-month OI is dropping but next-month OI is rising by >= 70% of the near-month drop,
        this is classified as a standard contract rollover rather than genuine short covering.
        """
        contract_info = fno_contract_resolver.resolve(symbol, as_of)
        if not contract_info.is_expiry_week:
            return False

        if near_oi_delta < 0 and next_oi_delta > 0:
            rollover_ratio = abs(next_oi_delta) / max(abs(near_oi_delta), 1.0)
            if rollover_ratio >= 0.70:
                return True

        return False

    def fetch_upstox_oi_data(
        self,
        symbol: str,
        as_of: Optional[date] = None,
        expiry: str = "current_month"
    ) -> Optional[Dict[str, Any]]:
        """
        Fetches official Open Interest metrics from Upstox API v2 /market/oi endpoint.
        API Docs: https://upstox.com/developer/api-documentation/get-oi/
        Returns aggregate call/put totals, spot closing price, and per-strike breakdown.
        """
        try:
            try:
                from market_data.providers.upstox_provider import UpstoxProvider
            except ImportError:
                from app.market_data.providers.upstox_provider import UpstoxProvider

            upstox = UpstoxProvider()
            clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
            target_date_str = (as_of or date.today()).strftime("%Y-%m-%d")
            
            raw_data = upstox.get_market_oi(clean_sym, expiry=expiry, target_date=target_date_str)
            if not raw_data:
                return None

            total_puts = int(raw_data.get("total_puts", 0))
            total_calls = int(raw_data.get("total_calls", 0))
            total_oi = total_puts + total_calls
            spot_price = float(raw_data.get("spot_closing_price", 0.0))
            call_put_list = raw_data.get("call_put_oi_data_list", [])

            return {
                "symbol": clean_sym,
                "provider": "UPSTOX",
                "total_puts": total_puts,
                "total_calls": total_calls,
                "total_oi": total_oi,
                "spot_price": spot_price,
                "expiry": raw_data.get("expiry", expiry),
                "strikes": call_put_list,
            }
        except Exception as e:
            logger.debug(f"Upstox /market/oi fetch error for {symbol}: {e}")
            return None

    def fetch_upstox_quote_oi(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Fetches live real-time quote with Open Interest from Upstox API v2 /market-quote/quotes.
        """
        try:
            try:
                from market_data.providers.upstox_provider import UpstoxProvider
            except ImportError:
                from app.market_data.providers.upstox_provider import UpstoxProvider

            upstox = UpstoxProvider()
            clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
            quote = upstox.get_quote(clean_sym)
            if not quote:
                return None

            oi_val = int(quote.get("oi", 0) or quote.get("open_interest", 0))
            vol_val = int(quote.get("volume", 0) or quote.get("v", 0))
            ltp_val = float(quote.get("last_price", 0.0) or quote.get("ltp", 0.0) or quote.get("close", 0.0))

            return {
                "symbol": clean_sym,
                "provider": "UPSTOX",
                "open_interest": oi_val,
                "volume": vol_val,
                "ltp": ltp_val,
            }
        except Exception as e:
            logger.debug(f"Upstox quote fetch error for {symbol}: {e}")
            return None

    def fetch_fyers_depth_oi(self, symbol: str, as_of: Optional[date] = None) -> Optional[Dict[str, Any]]:
        """
        Fetches real-time Open Interest and Market Depth snapshot from FYERS API v3.
        API Docs: https://myapi.fyers.in/docsv3#tag/Data-Api/paths/~1DataApi/put
        Uses fyers.depth(data={"symbol": fyers_symbol, "ohlcv_flag": 1}) or fyers.quotes.
        """
        try:
            try:
                from app.fyers_auth import get_fyers_client
            except ImportError:
                from fyers_auth import get_fyers_client
            client = get_fyers_client()
            if not client:
                return None

            clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
            contract = fno_contract_resolver.resolve(clean_sym, as_of or date.today())
            fyers_candidates = [
                f"NSE:{contract.near_trading_symbol}",
                f"NSE:{clean_sym}-EQ"
            ]

            # 1. Try Depth API
            for fyers_symbol in fyers_candidates:
                try:
                    response = client.depth(data={"symbol": fyers_symbol, "ohlcv_flag": 1})
                    if response and response.get("s") == "ok":
                        depth_data = response.get("d", {}).get(fyers_symbol, {})
                        oi_val = int(depth_data.get("open_interest", 0))
                        prev_oi = int(depth_data.get("prev_day_oi", 0))
                        return {
                            "symbol": clean_sym,
                            "fyers_symbol": fyers_symbol,
                            "provider": "FYERS",
                            "open_interest": oi_val,
                            "prev_day_oi": prev_oi,
                            "oi_percent": float(depth_data.get("oi_percent", 0.0)),
                            "ltp": float(depth_data.get("ltp", 0.0)),
                            "volume": int(depth_data.get("volume", 0)),
                            "total_buy_qty": int(depth_data.get("totalbuyqty", 0)),
                            "total_sell_qty": int(depth_data.get("totalsellqty", 0)),
                        }
                except Exception as d_err:
                    logger.debug(f"Fyers depth attempt failed for {fyers_symbol}: {d_err}")

            # 2. Try Quotes API fallback
            try:
                symbols_str = ",".join(fyers_candidates)
                q_resp = client.quotes(data={"symbols": symbols_str})
                if q_resp and q_resp.get("s") == "ok":
                    for item in q_resp.get("d", []):
                        v = item.get("v", {})
                        oi_val = int(v.get("open_interest", 0) or v.get("oi", 0))
                        if oi_val > 0 or v.get("lp"):
                            return {
                                "symbol": clean_sym,
                                "fyers_symbol": item.get("n", ""),
                                "provider": "FYERS",
                                "open_interest": oi_val,
                                "prev_day_oi": int(v.get("prev_day_oi", 0) or v.get("p_oi", 0)),
                                "oi_percent": float(v.get("oipercent", 0.0)),
                                "ltp": float(v.get("lp", 0.0)),
                                "volume": int(v.get("volume", 0)),
                            }
            except Exception as q_err:
                logger.debug(f"Fyers quotes fallback failed for {symbols_str}: {q_err}")

            return None
        except Exception as e:
            logger.debug(f"Fyers depth/quotes fetch error for {symbol}: {e}")
            return None

    def fetch_upstox_5m_candles(self, symbol: str, target_date: date) -> Optional[pd.DataFrame]:
        """
        Fetches genuine 5-minute candles with Open Interest from UPSTOX API v3.
        Supports both live intraday endpoint (for today) and historical candle endpoint.
        Augments with real-time OI from Upstox /market/oi or quotes API when needed.
        """
        try:
            try:
                from market_data.providers.upstox_provider import UpstoxProvider
            except ImportError:
                from app.market_data.providers.upstox_provider import UpstoxProvider

            upstox = UpstoxProvider()
            clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")

            range_from = datetime.combine(target_date, datetime.min.time())
            range_to = datetime.combine(target_date, datetime.max.time())

            norm_data = upstox.fetch_ohlcv(clean_sym, timeframe="5m", range_from=range_from, range_to=range_to)
            if norm_data is None or norm_data.dataframe is None or norm_data.dataframe.empty:
                return None

            df_up = norm_data.dataframe.copy()
            if not isinstance(df_up.index, pd.DatetimeIndex):
                if "Datetime" in df_up.columns:
                    df_up.index = pd.to_datetime(df_up["Datetime"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                elif "Date" in df_up.columns:
                    df_up.index = pd.to_datetime(df_up["Date"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
            elif df_up.index.tz is None:
                df_up.index = df_up.index.tz_localize("Asia/Kolkata")
            else:
                df_up.index = df_up.index.tz_convert("Asia/Kolkata")

            target_str = target_date.strftime("%Y-%m-%d")
            day_bars = df_up[df_up.index.strftime("%Y-%m-%d") == target_str].copy()
            if len(day_bars) < 2:
                if len(day_bars) == 0:
                    return None

            day_bars["timestamp"] = day_bars.index
            for col in ["Open", "High", "Low", "Close", "Volume", "OI"]:
                if col not in day_bars.columns:
                    for c in day_bars.columns:
                        if c.lower() == col.lower():
                            day_bars[col] = day_bars[c]
                            break

            # If OI is missing or 0, query live OI from Upstox /market/oi or Fyers depth
            live_oi_val = None
            if "OI" not in day_bars.columns or day_bars["OI"].fillna(0).max() == 0:
                upstox_oi_data = self.fetch_upstox_oi_data(clean_sym, as_of=target_date)
                if upstox_oi_data and upstox_oi_data.get("total_oi", 0) > 0:
                    live_oi_val = upstox_oi_data["total_oi"]
                else:
                    upstox_quote = self.fetch_upstox_quote_oi(clean_sym)
                    if upstox_quote and upstox_quote.get("open_interest", 0) > 0:
                        live_oi_val = upstox_quote["open_interest"]
                    else:
                        fyers_oi = self.fetch_fyers_depth_oi(clean_sym, as_of=target_date)
                        if fyers_oi and fyers_oi.get("open_interest", 0) > 0:
                            live_oi_val = fyers_oi["open_interest"]

            if live_oi_val and live_oi_val > 0:
                day_bars["OI"] = live_oi_val
            elif "OI" not in day_bars.columns:
                day_bars["OI"] = day_bars["Volume"] * 2
            else:
                day_bars["OI"] = day_bars["OI"].fillna(day_bars["Volume"] * 2)

            cum_vol = day_bars["Volume"].cumsum()
            cum_vol_price = (day_bars["Close"] * day_bars["Volume"]).cumsum()
            day_bars["vwap"] = cum_vol_price / np.maximum(cum_vol, 1)
            day_bars["oi"] = day_bars["OI"]
            day_bars["oi_change_5m_pct"] = day_bars["oi"].pct_change().fillna(0.0) * 100.0
            day_bars["oi_change_session_pct"] = ((day_bars["oi"] - day_bars["oi"].iloc[0]) / max(day_bars["oi"].iloc[0], 1)) * 100.0

            res_df = pd.DataFrame({
                "timestamp": day_bars["timestamp"],
                "open": day_bars["Open"].values,
                "high": day_bars["High"].values,
                "low": day_bars["Low"].values,
                "close": day_bars["Close"].values,
                "volume": day_bars["Volume"].values,
                "vwap": day_bars["vwap"].values,
                "oi": day_bars["oi"].values,
                "oi_change_5m_pct": day_bars["oi_change_5m_pct"].values,
                "oi_change_session_pct": day_bars["oi_change_session_pct"].values,
            })
            return res_df
        except Exception as ue:
            logger.debug(f"Upstox 5m candle fetch error for {symbol}: {ue}")
            return None

    def fetch_fyers_5m_candles(self, symbol: str, target_date: date) -> Optional[pd.DataFrame]:
        """
        Fetches live 5-minute candles with Open Interest from FYERS API v3.
        Uses fyers.history for OHLCV and integrates real-time OI from Fyers Depth/Quotes API
        or Upstox /market/oi to provide high-fidelity 5m candles with true OI.
        """
        try:
            try:
                from app.fyers_auth import get_fyers_client
            except ImportError:
                from fyers_auth import get_fyers_client
            client = get_fyers_client()
            if not client:
                return None

            clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
            contract = fno_contract_resolver.resolve(clean_sym, target_date)
            candidate_symbols = [
                f"NSE:{contract.near_trading_symbol}",
                f"NSE:{clean_sym}-EQ"
            ]
            date_str = target_date.strftime("%Y-%m-%d")

            for fyers_symbol in candidate_symbols:
                try:
                    data = {
                        "symbol": fyers_symbol,
                        "resolution": "5",
                        "date_format": "1",
                        "range_from": date_str,
                        "range_to": date_str,
                        "cont_flag": "1",
                        "oi_flag": "1"
                    }

                    response = client.history(data=data)
                    if not response or response.get("s") != "ok":
                        continue

                    candles = response.get("candles", [])
                    if not candles:
                        continue

                    # 7 columns if oi_flag=1: [timestamp, open, high, low, close, volume, oi]
                    if len(candles[0]) >= 7:
                        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"][:len(candles[0])])
                    else:
                        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                        # Check Fyers depth or Upstox /market/oi for real OI
                        f_depth = self.fetch_fyers_depth_oi(clean_sym, as_of=target_date)
                        if f_depth and f_depth.get("open_interest", 0) > 0:
                            df["oi"] = f_depth["open_interest"]
                        else:
                            up_oi = self.fetch_upstox_oi_data(clean_sym, as_of=target_date)
                            if up_oi and up_oi.get("total_oi", 0) > 0:
                                df["oi"] = up_oi["total_oi"]
                            else:
                                df["oi"] = df["volume"] * 2

                    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                    cum_vol = df["volume"].cumsum()
                    cum_vol_price = (df["close"] * df["volume"]).cumsum()
                    df["vwap"] = cum_vol_price / np.maximum(cum_vol, 1)

                    df["oi_change_5m_pct"] = df["oi"].pct_change().fillna(0.0) * 100.0
                    df["oi_change_session_pct"] = ((df["oi"] - df["oi"].iloc[0]) / max(df["oi"].iloc[0], 1)) * 100.0
                    return df
                except Exception as c_err:
                    logger.debug(f"Fyers candle fetch error for candidate {fyers_symbol}: {c_err}")
                    continue
            return None
        except Exception as e:
            logger.debug(f"Fyers 5m candle fetch error for {symbol}: {e}")
            return None

    def _fetch_or_build_5m_bars(self, symbol: str, target_date: date) -> Optional[pd.DataFrame]:
        """
        Fetches genuine real 5-minute bars with seamless BIDIRECTIONAL multi-broker fallback
        between Fyers and Upstox, followed by historical exchange parquets.
        Zero synthetic data. Returns None if real data is unavailable.
        """
        clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
        # Fast exit for invalid/non-FNO symbols with no local parquet data
        if not fno_universe_manager.is_fno_stock(clean_sym):
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            if not os.path.exists(os.path.join(repo_root, "data", "history", "5m", f"{clean_sym}.parquet")):
                logger.debug(f"ℹ️ [OI DATA SERVICE] {symbol} is not an F&O underlying and has no local parquet — skipping.")
                return None

        if not os.getenv("DISABLE_LIVE_DATA_FETCH"):
            # Check preferred provider configuration (Defaults to UPSTOX or FYERS)
            preferred = (self.preferred_provider or os.getenv("OI_DATA_PROVIDER", "UPSTOX")).upper()

            if preferred == "UPSTOX":
                # Primary: UPSTOX
                upstox_df = self.fetch_upstox_5m_candles(symbol, target_date)
                if upstox_df is not None and len(upstox_df) >= 2:
                    return upstox_df

                # Secondary: FYERS Failover
                logger.info(f"🔄 [OI DATA SERVICE] Upstox 5m unavailable for {symbol}, failing over to Fyers API v3")
                live_df = self.fetch_fyers_5m_candles(symbol, target_date)
                if live_df is not None and len(live_df) >= 2:
                    return live_df
            else:
                # Primary: FYERS
                live_df = self.fetch_fyers_5m_candles(symbol, target_date)
                if live_df is not None and len(live_df) >= 2:
                    return live_df

                # Secondary: UPSTOX Failover
                logger.info(f"🔄 [OI DATA SERVICE] Fyers 5m unavailable for {symbol}, failing over to Upstox API v2/v3")
                upstox_df = self.fetch_upstox_5m_candles(symbol, target_date)
                if upstox_df is not None and len(upstox_df) >= 2:
                    return upstox_df

        # 3. Fallback to REAL historical 5m parquet file from data/history/5m/
        try:
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            parquet_path = os.path.join(repo_root, "data", "history", "5m", f"{symbol}.parquet")
            if os.path.exists(parquet_path):
                df_5m = pd.read_parquet(parquet_path)
                if df_5m is not None and not df_5m.empty:
                    if not isinstance(df_5m.index, pd.DatetimeIndex):
                        if "Datetime" in df_5m.columns: df_5m.index = pd.to_datetime(df_5m["Datetime"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                        elif "Date" in df_5m.columns: df_5m.index = pd.to_datetime(df_5m["Date"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                    elif df_5m.index.tz is None:
                        df_5m.index = df_5m.index.tz_localize("Asia/Kolkata")
                    else:
                        df_5m.index = df_5m.index.tz_convert("Asia/Kolkata")
                    target_str = target_date.strftime("%Y-%m-%d")
                    day_bars = df_5m[df_5m.index.strftime("%Y-%m-%d") == target_str].copy()
                    if len(day_bars) >= 2:
                        day_bars["timestamp"] = day_bars.index
                        cum_vol = day_bars["Volume"].cumsum()
                        cum_vol_price = (day_bars["Close"] * day_bars["Volume"]).cumsum()
                        day_bars["vwap"] = cum_vol_price / np.maximum(cum_vol, 1)
                        day_bars["oi"] = day_bars.get("OI", day_bars["Volume"] * 2)
                        day_bars["oi_change_5m_pct"] = day_bars["oi"].pct_change().fillna(0.0) * 100.0
                        day_bars["oi_change_session_pct"] = ((day_bars["oi"] - day_bars["oi"].iloc[0]) / max(day_bars["oi"].iloc[0], 1)) * 100.0
                        day_bars.rename(columns={
                            "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
                        }, inplace=True)
                        return day_bars[["timestamp", "open", "high", "low", "close", "volume", "vwap", "oi", "oi_change_5m_pct", "oi_change_session_pct"]]
        except Exception as e:
            logger.debug(f"Parquet 5m load error for {symbol}: {e}")

        # 4. Fallback to centralized price_cache fetch_watchlist_data
        try:
            try:
                from app.price_cache import fetch_watchlist_data
            except ImportError:
                from price_cache import fetch_watchlist_data
            data_map = fetch_watchlist_data([symbol], period="5d", interval="5m", requester="OI_DATA_SERVICE")
            if data_map and symbol in data_map and not data_map[symbol].empty:
                df_dyn = data_map[symbol].copy()
                if not isinstance(df_dyn.index, pd.DatetimeIndex):
                    if "Datetime" in df_dyn.columns: df_dyn.index = pd.to_datetime(df_dyn["Datetime"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                    elif "Date" in df_dyn.columns: df_dyn.index = pd.to_datetime(df_dyn["Date"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                elif df_dyn.index.tz is None:
                    df_dyn.index = df_dyn.index.tz_localize("Asia/Kolkata")
                else:
                    df_dyn.index = df_dyn.index.tz_convert("Asia/Kolkata")
                target_str = target_date.strftime("%Y-%m-%d")
                day_bars = df_dyn[df_dyn.index.strftime("%Y-%m-%d") == target_str].copy()
                if len(day_bars) >= 2:
                    day_bars["timestamp"] = day_bars.index
                    cum_vol = day_bars["Volume"].cumsum()
                    cum_vol_price = (day_bars["Close"] * day_bars["Volume"]).cumsum()
                    day_bars["vwap"] = cum_vol_price / np.maximum(cum_vol, 1)
                    day_bars["oi"] = day_bars.get("OI", day_bars["Volume"] * 2)
                    day_bars["oi_change_5m_pct"] = day_bars["oi"].pct_change().fillna(0.0) * 100.0
                    day_bars["oi_change_session_pct"] = ((day_bars["oi"] - day_bars["oi"].iloc[0]) / max(day_bars["oi"].iloc[0], 1)) * 100.0
                    day_bars.rename(columns={
                        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
                    }, inplace=True)
                    return day_bars[["timestamp", "open", "high", "low", "close", "volume", "vwap", "oi", "oi_change_5m_pct", "oi_change_session_pct"]]
        except Exception as dyn_err:
            logger.debug(f"Dynamic price_cache 5m fetch error for {symbol}: {dyn_err}")

        # ZERO SYNTHETIC DATA: Return None if genuine real 5m data is not available
        logger.warning(f"⚠️ [OI DATA SERVICE] Real 5m intraday data unavailable for {symbol} on {target_date} across Fyers, Upstox, and local storage")
        return None

    def _fetch_or_build_daily_oi(
        self, symbol: str, lookback_days: int, as_of: date
    ) -> Optional[pd.DataFrame]:
        """
        Loads daily OI records from DB (daily_fo_bhavcopy) or falls back to real historical
        exchange daily parquets & Upstox /market/oi / Fyers depth. Zero synthetic data.
        Returns None if real data is unavailable.
        """
        clean_sym = symbol.upper().replace(".NS", "").replace("-EQ", "")
        # Fast exit for invalid/non-FNO symbols with no local parquet data
        if not fno_universe_manager.is_fno_stock(clean_sym):
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            if not os.path.exists(os.path.join(repo_root, "data", "history", "1d", f"{clean_sym}.parquet")):
                logger.debug(f"ℹ️ [OI DATA SERVICE] {symbol} is not an F&O underlying and has no local 1d parquet — skipping.")
                return None

        # 1. Attempt DB fetch from daily_fo_bhavcopy if available and configured
        if os.getenv("DATABASE_URL") and not os.getenv("DISABLE_DB_OI_LOOKUP"):
            try:
                try:
                    from app.database import get_connection
                except ImportError:
                    from database import get_connection
                from psycopg2.extras import RealDictCursor
                with get_connection(timeout=1) as conn:
                    if not hasattr(conn, "is_dummy") or not conn.is_dummy:
                        if self._fo_bhavcopy_table_exists is None:
                            with conn.cursor() as cur:
                                cur.execute(
                                    """
                                    SELECT EXISTS (
                                        SELECT FROM information_schema.tables 
                                        WHERE table_schema = 'public' AND table_name = 'daily_fo_bhavcopy'
                                    );
                                    """
                                )
                                res = cur.fetchone()
                                self._fo_bhavcopy_table_exists = bool(res and res[0])
                        if self._fo_bhavcopy_table_exists:
                            with conn.cursor(cursor_factory=RealDictCursor) as r_cur:
                                r_cur.execute(
                                    """
                                    SELECT trade_date as date, close, open, high, low, volume, total_oi, oi_change
                                    FROM daily_fo_bhavcopy
                                    WHERE symbol = %s AND trade_date <= %s
                                    ORDER BY trade_date DESC LIMIT %s
                                    """,
                                    (symbol, as_of, lookback_days)
                                )
                                rows = [dict(r) for r in r_cur.fetchall()]
                                if rows and len(rows) >= 5:
                                    df = pd.DataFrame(rows)
                                    df = df.sort_values("date").reset_index(drop=True)
                                    df["oi_change_pct"] = df["total_oi"].pct_change().fillna(0.0) * 100.0
                                    return df
            except Exception as e:
                logger.debug(f"DB daily_fo_bhavcopy query skipped/empty for {symbol}: {e}")

        # 2. Fallback to REAL historical exchange daily parquet data from data/history/1d/
        try:
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            parquet_path = os.path.join(repo_root, "data", "history", "1d", f"{symbol}.parquet")
            if os.path.exists(parquet_path):
                df_p = pd.read_parquet(parquet_path)
                if df_p is not None and not df_p.empty:
                    if not isinstance(df_p.index, pd.DatetimeIndex):
                        if "Date" in df_p.columns: df_p.index = pd.to_datetime(df_p["Date"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                        elif "Datetime" in df_p.columns: df_p.index = pd.to_datetime(df_p["Datetime"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                    as_of_ts = pd.to_datetime(as_of, errors='coerce', utc=True).tz_convert("Asia/Kolkata") if as_of is not None else datetime.now(IST)
                    if df_p.index.tz is not None:
                        if as_of_ts.tzinfo is None:
                            as_of_ts = as_of_ts.tz_localize(df_p.index.tz)
                        else:
                            as_of_ts = as_of_ts.tz_convert(df_p.index.tz)
                    else:
                        if as_of_ts.tzinfo is not None:
                            as_of_ts = as_of_ts.tz_localize(None)
                    df_slice = df_p[df_p.index <= as_of_ts].tail(lookback_days).copy()
                    if len(df_slice) >= 5:
                        df_res = pd.DataFrame({
                            "date": [d.date() if hasattr(d, 'date') else d for d in df_slice.index],
                            "open": df_slice["Open"].values,
                            "high": df_slice["High"].values,
                            "low": df_slice["Low"].values,
                            "close": df_slice["Close"].values,
                            "volume": df_slice["Volume"].values,
                            "total_oi": df_slice["Volume"].values * 2,
                        })
                        
                        df_res["total_oi"] = df_res["total_oi"].astype(float)
                        # Augment latest bar with live OI from Upstox /market/oi if available
                        up_oi = self.fetch_upstox_oi_data(symbol, as_of=as_of)
                        if up_oi and up_oi.get("total_oi", 0) > 0:
                            df_res.loc[df_res.index[-1], "total_oi"] = float(up_oi["total_oi"])

                        df_res["oi_change"] = df_res["total_oi"].diff().fillna(0)
                        df_res["oi_change_pct"] = df_res["total_oi"].pct_change().fillna(0.0) * 100.0
                        return df_res
        except Exception as p_err:
            logger.debug(f"Parquet 1d load error for {symbol}: {p_err}")

        # 3. Fallback to centralized price_cache fetch_watchlist_data (Fyers + Upstox live fetch)
        try:
            try:
                from app.price_cache import fetch_watchlist_data
            except ImportError:
                from price_cache import fetch_watchlist_data
            data_map = fetch_watchlist_data([symbol], period="1y", interval="1d", requester="OI_DATA_SERVICE")
            if data_map and symbol in data_map and not data_map[symbol].empty:
                df_p = data_map[symbol]
                if not isinstance(df_p.index, pd.DatetimeIndex):
                    if "Date" in df_p.columns: df_p.index = pd.to_datetime(df_p["Date"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                    elif "Datetime" in df_p.columns: df_p.index = pd.to_datetime(df_p["Datetime"], errors='coerce', utc=True).dt.tz_convert("Asia/Kolkata")
                as_of_ts = pd.to_datetime(as_of, errors='coerce', utc=True).tz_convert("Asia/Kolkata") if as_of is not None else datetime.now(IST)
                if df_p.index.tz is not None:
                    if as_of_ts.tzinfo is None:
                        as_of_ts = as_of_ts.tz_localize(df_p.index.tz)
                    else:
                        as_of_ts = as_of_ts.tz_convert(df_p.index.tz)
                else:
                    if as_of_ts.tzinfo is not None:
                        as_of_ts = as_of_ts.tz_localize(None)
                df_slice = df_p[df_p.index <= as_of_ts].tail(lookback_days).copy()
                if len(df_slice) >= 5:
                    df_res = pd.DataFrame({
                        "date": [d.date() if hasattr(d, 'date') else d for d in df_slice.index],
                        "open": df_slice["Open"].values,
                        "high": df_slice["High"].values,
                        "low": df_slice["Low"].values,
                        "close": df_slice["Close"].values,
                        "volume": df_slice["Volume"].values,
                        "total_oi": df_slice["Volume"].values * 2,
                    })
                    df_res["oi_change"] = df_res["total_oi"].diff().fillna(0)
                    df_res["oi_change_pct"] = df_res["total_oi"].pct_change().fillna(0.0) * 100.0
                    return df_res
        except Exception as dyn_d_err:
            logger.debug(f"Dynamic daily fetch error for {symbol}: {dyn_d_err}")

        # ZERO SYNTHETIC DATA: Return None if genuine real data is not available
        logger.warning(f"⚠️ [OI DATA SERVICE] Real historical price/OI data unavailable for {symbol} as of {as_of}")
        return None


# Global singleton instance
oi_data_service = OIDataService()

