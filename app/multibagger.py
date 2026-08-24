from scanner_telemetry import ScannerDecisionLogger, global_telemetry
import time as _time
# =====================================================================================
# app/multibagger.py
# MULTIBAGGER V5 COMPOSITE SCANNER
#
# RULE 67 MANDATORY CHANGE-RATIONALE:
# - Verified and enhanced GlobalScannerTelemetryEngine logging across Multibagger evaluation gates.
# - Rationale: Tracks Piotroski F-Score, promoter pledge ratio, composite V5 score breakdowns,
#   quality/valuation/trend sub-scores, and conviction tier classifications in scanner_telemetry.jsonl.
# =====================================================================================
import io
import os
import time
import json
import logging
import math
from dataclasses import dataclass
from typing import Optional

@dataclass
class FairValueResult:
    fair_value: float
    bear_value: float
    bull_value: float
    valuation_method: str
    valuation_confidence: str
    peer_count: Optional[int]
    target_multiple: Optional[float]
    current_multiple: Optional[float]
    peer_multiple: Optional[float]
    is_fallback: bool

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

# [FIX MUL-8] Moved pandas import before _pledge_ratio to avoid latent NameError.
# _pledge_ratio calls pd.isna() at call-time, so it usually works, but defining
# it before the import is fragile and breaks if the import is ever reordered.
import pandas as pd
import yfinance as yf
from typing import Dict, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from psycopg2.extras import execute_values

# [FIX MUL-1] Pledge values arrive in different units depending on source:
# - Production pipeline stores as ratio (0.0-1.0) via `pledge_val / 100.0`
# - Diagnostic/fund_data may carry percentage (0-100)
# This normalizer tolerates either unit and always returns a ratio.
def _pledge_ratio(v):
    if v is None or pd.isna(v):
        return None
    v = float(v)
    return v / 100.0 if v > 1.0 else v

from database import get_connection, save_alert_if_new, close_position, update_alert_outcome, init_db, upsert_scanner_health, is_scanner_stopped
from telegram_engine import queue_telegram_message
from wealth_risk_adjusted_sizing import calculate_risk_adjusted_sizing
from core.multibagger_pipeline import run_pipeline_for_symbol

logger = logging.getLogger("multibagger")

def _safe_float(val, default=0.0):
    try:
        if val is None or (isinstance(val, float) and (math.isnan(val) or math.isinf(val))):
            return default
        return float(val)
    except Exception:
        return default

IST = ZoneInfo("Asia/Kolkata")
def evaluate_multibagger_symbol(symbol: str, df: pd.DataFrame, fund_data: dict = None) -> dict:
    """
    Evaluates a single symbol against the production Multibagger V5 scanner rules.
    Runs full V5 composite scoring, quality/valuation/trend gates, Piotroski & promoter pledge checks, conviction tier classification, and target calculations without side effects.
    """
    if df is None or df.empty or len(df) < 200:
        return {
            "status": "NO",
            "reasons": [f"Insufficient history: requires 200 bars, got {len(df) if df is not None else 0}"],
            "score": 0.0,
            "qualified": False
        }

    ticker = df.copy()
    if isinstance(ticker.columns, pd.MultiIndex):
        ticker.columns = ticker.columns.get_level_values(0)
    ticker = ticker.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    if len(ticker) < 200:
        return {"status": "NO", "reasons": [f"Insufficient valid bars: {len(ticker)} < 200 required"], "score": 0.0, "qualified": False}

    latest = ticker.iloc[-1]
    close_price = float(latest["Close"])
    open_price = float(latest["Open"])
    high_price = float(latest["High"])
    low_price = float(latest["Low"])
    vol = float(latest["Volume"])


    fd = fund_data or {}
    raw_f_score = fd.get("score", fd.get("piotroski_score"))
    f_score = int(raw_f_score) if (raw_f_score is not None and not pd.isna(raw_f_score)) else None
    raw_pledge = fd.get("promoter_pledge_pct")
    # [FIX MUL-1] Normalize pledge to ratio (0.0-1.0) for consistent comparison
    pledge_ratio = _pledge_ratio(raw_pledge)

    # [VERSION: MULTIBAGGER_DIAG_ALIGN_v1.1] Extract component scores from V5 pipeline and use classify_conviction directly
    composite_score = None
    cqs = 0.0
    pas = 0.0
    trend = 0.0
    try:
        from wealth_engine import map_watchlist_to_v5
        v5_dict = map_watchlist_to_v5({**fd, "Stock": symbol, "Close": close_price})
        v5_decision = run_pipeline_for_symbol(symbol, v5_dict)
        if v5_decision and hasattr(v5_decision, 'composite_score'):
            composite_score = float(v5_decision.composite_score)
            cqs = float(getattr(v5_decision.quality, 'score', 0.0))
            pas = float(getattr(v5_decision.valuation, 'score', 0.0))
            trend = float(getattr(v5_decision.market_structure, 'score', 0.0))
    except Exception as _v5e:
        logger.warning(f"Could not compute V5 score for {symbol}: {_v5e}")

    sma50 = float(ticker["Close"].tail(50).mean()) if len(ticker) >= 50 else close_price
    sma200 = float(ticker["Close"].tail(200).mean()) if len(ticker) >= 200 else close_price
    if len(ticker) >= 200:
        is_uptrend = (close_price > sma50) and (close_price > sma200)
    elif len(ticker) >= 50:
        is_uptrend = (close_price > sma50)
    else:
        is_uptrend = True

    reasons = []
    if composite_score is None:
        reasons.append("V5 composite scoring unavailable (pipeline failure)")
        tier = "⚪ Low Conviction"
    else:
        tier, _ = classify_conviction(cqs, pas, trend, composite_score, f_score=f_score, pledge_ratio=pledge_ratio)

    is_prime = (tier == "🚀 Prime Multibagger") and is_uptrend
    is_high_quality = (tier == "💎 High Quality") and is_uptrend
    is_qualified = bool(is_prime or is_high_quality)

    pledge_text = f"{pledge_ratio * 100:.1f}%" if pledge_ratio is not None else "UNVERIFIED"
    if is_prime:
        reasons.append(f"Prime Multibagger: V5 Score {composite_score:.1f} | Piotroski {f_score if f_score is not None else 'N/A'}/9 | Pledge {pledge_text} <= 10%")
    elif is_high_quality:
        reasons.append(f"High Quality Multibagger: V5 Score {composite_score:.1f} >= 65 | Pledge {pledge_text} <= 15%")
    elif composite_score is not None:
        reasons.append(f"Multibagger V5 Score {composite_score:.1f} ({tier}) | Pledge: {pledge_text}")
    # else: reasons already set from the None guard above

    from sl_target_helper import compute_sl_and_target
    atr_val = float(latest.get("ATR", close_price * 0.025)) if "ATR" in ticker.columns else (close_price * 0.025)
    sl_result = compute_sl_and_target(entry_price=close_price, atr=atr_val, mode="MULTIBAGGER", ticker=ticker)

    # [FIX MUL-19] Guard composite_score comparison against None
    status_str = "CORE MET (Prime)" if is_prime else ("CORE MET (High Quality)" if is_high_quality else ("WATCHLIST" if (composite_score is not None and composite_score >= 50.0) else "NO"))

    # ── PER-STOCK TERMINAL TELEMETRY DUMP (Section 4 & 8) ──
    try:
        from scanner_telemetry import DecisionContext, telemetry_engine
        ctx = DecisionContext(symbol=symbol, scanner_name="MULTIBAGGER")
        ctx.capture_raw_market(
            open_p=_safe_float(latest.get("Open")),
            high_p=_safe_float(latest.get("High")),
            low_p=_safe_float(latest.get("Low")),
            close_p=_safe_float(latest.get("Close")),
            volume=_safe_float(latest.get("Volume"))
        )
        ctx.capture_indicators(
            rsi=_safe_float(latest.get("RSI")),
            sma50=sma50,
            sma200=sma200,
            atr=atr_val
        )
        ctx.capture("Piotroski_Score", f_score, origin="EXTERNAL_API", group="INDICATOR")
        ctx.capture("Promoter_Pledge", pledge_ratio, origin="EXTERNAL_API", group="INDICATOR")
        ctx.capture_score("TOTAL", composite_score if composite_score is not None else 0.0, 100.0)
        ctx.capture_sl_target(close_price, sl_result.get("stop_loss", 0.0), sl_result.get("target_1", 0.0))
        
        ctx.finalize(decision="SELECTED" if is_qualified else "REJECTED", primary_reason=reasons[0] if reasons else "NO_QUALIFY")
        telemetry_engine.emit_terminal(ctx)
    except Exception as telemetry_err:
        logger.debug(f"Telemetry recording skipped: {telemetry_err}")

    return {
        "status": status_str,
        "reasons": reasons,
        "score": composite_score,
        "qualified": is_qualified,
        "conviction_tier": "Prime" if is_prime else ("High Quality" if is_high_quality else "Watchlist"),
        "entry_price": close_price,
        "stop_loss": sl_result.get("stop_loss"),
        "target_1": sl_result.get("target_1"),
        "target_2": sl_result.get("target_2"),
        "target_3": sl_result.get("target_3"),
        "target_4": sl_result.get("target_4"),
        "atr_20": atr_val
    }

CACHE_PATH = "data/multibagger_fundamentals_cache.json"



@dataclass
class StockPriceData:
    symbol: str
    price: float
    change_pct: float
    low_52w: float
    high_52w: float
    turnover_20d: float
    sma_20: float
    sma_50: float
    sma_200: float
    high_20d: float
    high_60d: float
    mom_3m: float
    mom_6m: float
    atr_14: float
    ema_20: float
    latest_volume: float
    volume_sma20: float
    close_yesterday: float
    sma_200_yesterday: float
    closes_below_sma200_count: int = 0
    last_trade_date: str = ""
    # [FIX MUL-21] Add open/close for proper bullish-close gate in entry_confirmed
    today_open: float = 0.0
    today_close: float = 0.0

@dataclass
class ExitPriceData:
    symbol: str
    price: float
    sma_50: float
    sma_200: float
    high_20d: float
    close_yesterday: float
    sma_200_yesterday: float
    atr_14: float
    ema_20: float
    closes_below_sma200_count: int = 0
    # [FIX ISSUE-1] Add last_trade_date for stale-data detection in exit monitor
    last_trade_date: str = ""

@dataclass
class ScreenerResult:
    symbol: str
    price: float
    cqs: float
    pas: float
    trend_score: float
    total_score: float
    buy_zone_low: float
    buy_zone_high: float
    bucket: str
    status: str
    notes: str
    change_pct: float = 0.0
    # [FIX MUL-16/17] Track whether alert was actually inserted into DB.
    # Before this, ALERT_TRIGGERED was set before Top-N suppression and
    # save_alert_if_new, so both the count and watchlist could be overstated.
    alert_inserted: bool = False

# [VERSION: MULTIBAGGER_REJECTION_VISIBILITY_v1.1] Helper to construct and append ScreenerResult for rejected symbols
# [VERSION: MULTIBAGGER_REJECTION_VISIBILITY_v2.0] Helper to construct and append ScreenerResult for rejected symbols
def append_rejection(results: list, symbol: str, status: str, notes: str, price: float = 0.0, cqs: float = 0.0, pas: float = 0.0, trend_score: float = 0.0, total_score: float = 0.0, buy_zone_low: float = 0.0, buy_zone_high: float = 0.0, bucket: str = "⚪ Low Conviction", price_data = None, raw_fundamentals: dict = None):
    results.append(ScreenerResult(
        symbol=symbol,
        price=round(price, 2) if price else 0.0,
        cqs=round(cqs, 1) if cqs else 0.0,
        pas=round(pas, 1) if pas else 0.0,
        trend_score=round(trend_score, 1) if trend_score else 0.0,
        total_score=round(total_score, 1) if total_score else 0.0,
        buy_zone_low=round(buy_zone_low, 2) if buy_zone_low else 0.0,
        buy_zone_high=round(buy_zone_high, 2) if buy_zone_high else 0.0,
        bucket=bucket,
        status=status,
        notes=notes,
        change_pct=0.0,
        alert_inserted=False
    ))
    try:
        from scanner_telemetry import DecisionContext, telemetry_engine
        ctx = DecisionContext(symbol=symbol, scanner_name="MULTIBAGGER")
        
        # Real market data extraction from price_data
        _open = getattr(price_data, 'today_open', price) if price_data else price
        _high = getattr(price_data, 'high_52w', price) if price_data else price
        _low = getattr(price_data, 'low_52w', price) if price_data else price
        _close = price_data.price if price_data else price
        _vol = getattr(price_data, 'latest_volume', 0.0) if price_data else 0.0
        
        ctx.capture_raw_market(open_p=_open, high_p=_high, low_p=_low, close_p=_close, volume=_vol)
        ctx.capture_raw_vs_normalized(
            source_raw={"Open": _open, "High": _high, "Low": _low, "Close": _close, "Volume": _vol},
            scanner_normalized={"Open": _open, "High": _high, "Low": _low, "Close": _close, "Volume": _vol}
        )

        # Register technical inputs into manifest
        if price_data:
            ctx.add_decision_input("Close", _close, source="PriceData", as_of="Live", freshness="LIVE", required=True, valid=_close > 0, provider="NSE_BHAVCOPY", data_type="DAILY_CLOSE")
            ctx.add_decision_input("Volume", _vol, source="PriceData", as_of="Live", freshness="LIVE", required=True, valid=_vol > 0, provider="NSE_BHAVCOPY", data_type="DAILY_CLOSE")
            ctx.add_decision_input("SMA50", getattr(price_data, 'sma_50', _close), source="TechnicalIndicator", as_of="Live", freshness="LIVE", required=True, valid=True, calculation_fingerprint=f"SMA50|CLOSE|1D|SIMPLE|200BARS|UNADJUSTED")
            ctx.add_decision_input("SMA200", getattr(price_data, 'sma_200', _close), source="TechnicalIndicator", as_of="Live", freshness="LIVE", required=True, valid=True, calculation_fingerprint=f"SMA200|CLOSE|1D|SIMPLE|200BARS|UNADJUSTED")
            ctx.add_decision_input("EMA20", getattr(price_data, 'ema_20', _close), source="TechnicalIndicator", as_of="Live", freshness="LIVE", required=True, valid=True, calculation_fingerprint=f"EMA20|CLOSE|1D|EMA|200BARS|UNADJUSTED")
            ctx.add_decision_input("ATR", getattr(price_data, 'atr_14', _close*0.02), source="TechnicalIndicator", as_of="Live", freshness="LIVE", required=True, valid=True, calculation_fingerprint=f"ATR14|CLOSE|1D|WILDER|200BARS|UNADJUSTED")

        # Register full fundamental metrics into manifest
        if raw_fundamentals:
            fund_mapping = [
                ("ROE", "roe", "PAT/AVG_EQUITY"),
                ("ROCE", "roce", "EBIT/CAPITAL_EMPLOYED"),
                ("DebtEquity", "debt_equity", "TOTAL_DEBT/SHAREHOLDER_EQUITY"),
                ("MarketCap", "market_cap", "TOTAL_SHARES*CMP"),
                ("PE", "pe_ratio", "CMP/TTM_EPS"),
                ("PromoterPledge", "promoter_pledge_pct", "PLEDGED_SHARES/PROMOTER_SHARES"),
                ("OperatingCashFlowTTM", "operating_cash_flow_ttm", "CASH_FROM_OPERATIONS_TTM"),
                ("SalesGrowth", "yoy_revenue", "YOY_REVENUE_GROWTH_PCT"),
                ("PATGrowth", "yoy_profit", "YOY_PAT_GROWTH_PCT"),
                ("EBITDAMargin", "ebitda_margin", "EBITDA/TOTAL_REVENUE"),
                ("ValuationScore", "pas_score", "PAS_VALUATION_ENGINE"),
                ("QualityScore", "cqs_score", "CQS_QUALITY_ENGINE"),
                ("TrendScore", "trend_score", "TREND_STRUCTURE_ENGINE")
            ]
            for m_name, f_key, f_formula in fund_mapping:
                f_val = raw_fundamentals.get(f_key)
                f_valid = f_val is not None and not (isinstance(f_val, float) and __import__('math').isnan(f_val))
                ctx.add_decision_input(
                    name=m_name,
                    value=f_val,
                    source="FundamentalsDB",
                    as_of=raw_fundamentals.get("data_as_of", "Live"),
                    freshness=raw_fundamentals.get("data_freshness", "LIVE"),
                    required=True,
                    valid=f_valid,
                    provider="FUNDAMENTALS_DB",
                    data_type="FUNDAMENTAL_METRIC",
                    calculation_fingerprint=f"{m_name}|TTM|{f_formula}",
                    formula=f_formula
                )

        ctx.capture_score("CQS_QUALITY", cqs, 30.0)
        ctx.capture_score("PAS_VALUATION", pas, 35.0)
        ctx.capture_score("TREND_STRUCTURE", trend_score, 35.0)
        ctx.capture_score("TOTAL", total_score, 100.0)
        ctx.capture_gate(gate_name=status, passed=False, actual_val=total_score, threshold_val=65.0, reason=notes)
        ctx.finalize(decision="REJECTED", primary_reason=f"{status}: {notes}")
        telemetry_engine.emit_terminal(ctx)
    except Exception as _tr_e:
        logger.debug(f"Telemetry recording exception in append_rejection: {_tr_e}")
    try:
        eff_score = total_score or cqs or 0.0
        if eff_score > 0:
            from near_miss_tracker import log_near_miss
            log_near_miss(
                symbol=symbol,
                scanner="MULTIBAGGER",
                breakout_type="MULTIBAGGER_SETUP",
                gate_name=status,
                observed_value=float(eff_score),
                threshold_value=65.0,
                score=int(eff_score),
                entry_price=float(price) if price else None,
                stop_loss=float(buy_zone_low) if buy_zone_low else None,
                target_1=float(buy_zone_high) if buy_zone_high else None,
            )
    except Exception as _nm_e:
        logger.debug(f"Multibagger near miss log error: {_nm_e}")

from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError, get_backoff_delay


def safe_float(val, default=0.0):
    try:
        import pandas as pd
        if val is None or pd.isna(val) or val == "":
            return default
        return float(val)
    except Exception:
        return default

def load_cache() -> dict:
    """Load local fundamentals JSON cache file."""
    cache = {}
    if not os.path.exists(CACHE_PATH):
        try:
            from database import download_parquet_from_db
            if download_parquet_from_db("multibagger_cache", CACHE_PATH):
                logger.info("☁️ [CACHE] Restored multibagger fundamentals cache from Postgres DB")
            elif download_parquet_from_db("fundamentals_cache", CACHE_PATH):
                logger.info("☁️ [CACHE] Restored shared fundamentals_cache from Postgres DB")
        except Exception as e:
            logger.warning(f"⚠️ Failed to restore multibagger cache from DB: {e}")
            
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r") as f:
                cache = json.load(f)
        except Exception as e:
            logger.warning(f"⚠️ Failed to load fundamentals cache: {e}")

    # If cache is missing or small (< 100 entries), instantly enrich via TradingView Screener API (<3s)
    if len(cache) < 100:
        try:
            from fundamentals_cache import fetch_tradingview_fundamentals_bulk
            logger.info("⚡ [LIGHTNING CACHE BUILD] Initializing bulk fundamentals via TradingView Screener API (<3s)...")
            tv_data = fetch_tradingview_fundamentals_bulk()
            if tv_data:
                for sym, entry in tv_data.items():
                    if sym not in cache:
                        cache[sym] = entry
                save_fundamentals_cache(cache, sync_to_db=True)
                logger.info(f"⚡ [LIGHTNING CACHE BUILD] Complete! Loaded {len(cache)} fundamental entries in <3 seconds.")
        except Exception as tv_err:
            logger.warning(f"⚠️ TradingView bulk cache initialization failed: {tv_err}")

    return cache

def save_fundamentals_cache(cache_data: dict, sync_to_db: bool = True):
    """Write current fundamentals to local JSON cache file."""
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(cache_data, f, separators=(',', ':'))
        logger.info(f"💾 Fundamentals cache saved with {len(cache_data)} entries.")
        
        if sync_to_db:
            # Backup to Postgres DB so it survives Railway restarts
            try:
                from database import upload_parquet_to_db
                upload_parquet_to_db("multibagger_cache", CACHE_PATH)
                logger.info("☁️ [CACHE] Uploaded multibagger fundamentals cache to Postgres DB")
            except Exception as e:
                logger.warning(f"⚠️ Failed to backup multibagger cache to DB: {e}")
            
    except Exception as e:
        logger.exception(f"❌ Failed to save fundamentals cache")


def batch_download_market_data(symbols: list, session=None) -> dict:
    """Download historical price/volume data in bulk for all tickers using the unified price cache.

    [VERSION: MULTIBAGGER_CACHE_FIX_v1.0] Previously called fetcher.get_batch_ohlcv() directly,
    bypassing price_cache entirely. This caused two problems:
    1. Every call re-fetched all symbols from YFinance with no caching (double-fetch observed in logs).
    2. The exit monitor and main scanner both fetched independently even within the same run.
    Now routes through fetch_unified_historical → fetch_watchlist_data → price_cache, which:
    - Caches 1D data until market close (TTL = seconds until 15:30 IST)
    - Shares the cache with EOD, Reversal, and Wealth Engine (1d, 1y key)
    - Eliminates redundant YFinance calls within the same scan cycle
    """
    from price_cache import fetch_unified_historical
    from market_utils import is_market_open
    import psutil

    BATCH_SIZE = int(os.environ.get("MULTIBAGGER_FETCH_BATCH_SIZE", "200"))
    logger.info(f"📥 Centralized chunked downloading 1y history for {len(symbols)} tickers (Chunk size: {BATCH_SIZE})...")

    ist_now = datetime.now(IST)
    strip_forming = is_market_open(ist_now)

    results = {}

    from memory_profiler import chunk_iterable, BatchMemoryTracker
    total_batches = (len(symbols) + BATCH_SIZE - 1) // BATCH_SIZE

    # Process symbols in chunks to flatten Peak Memory (O(BATCH_SIZE) instead of O(N))
    for batch_num, chunk in enumerate(chunk_iterable(symbols, BATCH_SIZE), start=1):
        if is_scanner_stopped("MULTIBAGGER"):
            logger.warning("🛑 [MULTIBAGGER] Stop requested by Admin. Aborting market data download batch loop.")
            break
        with BatchMemoryTracker("MULTIBAGGER", batch_num, total_batches, len(chunk), collect_gc=True) as tracker:

            # 1. Fetch chunk DataFrames via session or price_cache
            if session:
                raw_dict = {}
                for sym in chunk:
                    sym_data = session.get(sym)
                    if sym_data is not None and getattr(sym_data, "ohlcv_df", None) is not None:
                        raw_dict[sym] = sym_data.ohlcv_df
            else:
                raw_dict = fetch_unified_historical(chunk, period="1y", interval="1d", requester="multibagger")
                
            if not raw_dict:
                continue

            from core_enums import ProviderResult
            rows_fetched = sum(len(df) for df in raw_dict.values() if df is not None and isinstance(df, pd.DataFrame) and not df.empty)
            tracker.mark_fetch_complete(row_count=rows_fetched)
            batch_res = {sym: type("_MD", (), {"dataframe": df})() for sym, df in raw_dict.items() if df is not None}

        
        # 2. Convert DataFrames to StockPriceData
            for sym, md in batch_res.items():
                from core_enums import ProviderResult
                if md is None or isinstance(md, ProviderResult):
                    continue
            
                ticker_df = md.dataframe if hasattr(md, "dataframe") else md
                if ticker_df is None or getattr(ticker_df, "empty", True):
                    continue
                
                try:
                    ticker_df = ticker_df.dropna(subset=["Close"])
                    if ticker_df.empty:
                        continue
                    
                    if "Date" in ticker_df.columns:
                        ticker_df = ticker_df.set_index("Date")
                    elif "Datetime" in ticker_df.columns:
                        ticker_df = ticker_df.set_index("Datetime")
                    
                    if isinstance(ticker_df.index, pd.DatetimeIndex):
                        if ticker_df.index.tz is None:
                            ticker_df.index = ticker_df.index.tz_localize(IST)
                        else:
                            ticker_df.index = ticker_df.index.tz_convert(IST)
                    
                    real_time_close_series = ticker_df["Close"]
                    real_time_close = float(real_time_close_series.iloc[-1])
                    if len(real_time_close_series) >= 2:
                        real_time_prev = float(real_time_close_series.iloc[-2])
                        real_time_change = ((real_time_close - real_time_prev) / real_time_prev) * 100.0 if real_time_prev > 0 else 0.0
                    else:
                        real_time_change = 0.0

                    # [FIX MUL-21/REVISED] Capture forming-bar data BEFORE stripping.
                    # During market hours, we want the live forming-bar open for intraday
                    # bullish-close confirmation, not the previous completed bar's open.
                    forming_bar_open = 0.0
                    forming_bar_close = 0.0
                    is_market_open = False
                    if len(ticker_df) > 0:
                        last_ts = ticker_df.index[-1]
                        if last_ts.date() == ist_now.date():
                            is_market_open = True
                            forming_bar_open = float(ticker_df["Open"].iloc[-1]) if "Open" in ticker_df.columns else 0.0
                            forming_bar_close = float(ticker_df["Close"].iloc[-1]) if "Close" in ticker_df.columns else 0.0

                    if strip_forming and len(ticker_df) > 0:
                        last_ts = ticker_df.index[-1]
                        if last_ts.date() == ist_now.date():
                            ticker_df = ticker_df.iloc[:-1]
                        
                    # [FIX MUL-21 NEW] Require 200 bars for SMA200-based alerts.
                    # SMA200 is meaningless with fewer bars — produces wild values.
                    MIN_BARS = 200
                    if len(ticker_df) < MIN_BARS:
                        continue
                        
                    # [SEMANTIC VALIDATION] Reject pure data voids that appear valid numerically.
                    # e.g., O=H=L=C and Vol=0 means the stock is suspended or illiquid, 
                    # but technical indicators will still calculate flatlines that bypass thresholds.
                    _latest_ohlcv = ticker_df.iloc[-1]
                    _o = _latest_ohlcv.get("Open", 0.0)
                    _h = _latest_ohlcv.get("High", 0.0)
                    _l = _latest_ohlcv.get("Low", 0.0)
                    _c = _latest_ohlcv.get("Close", 0.0)
                    _v = _latest_ohlcv.get("Volume", 0.0)
                    if _o == _h == _l == _c and _v == 0.0:
                        logger.warning(f"🚫 [SEMANTIC GATES] {sym} rejected: NO_TRADING_ACTIVITY (O=H=L=C={_c:.2f}, Vol=0).")
                        from scanner_telemetry import DecisionContext, telemetry_engine
                        ctx = DecisionContext(symbol=sym, scanner_name="MULTIBAGGER")
                        ctx.add_decision_input(name="Volume", value=_v, source="MarketData", as_of="Live", freshness="LIVE", required=True, valid=False)
                        ctx.capture_raw_market(open_p=_o, high_p=_h, low_p=_l, close_p=_c, volume=_v)
                        ctx.finalize(decision="REJECTED", primary_reason="NO_TRADING_ACTIVITY")
                        telemetry_engine.emit_terminal(ctx)
                        continue
                
                    last_trade_date = str(ticker_df.index[-1].date())
                
                    close_series = ticker_df["Close"]
                    vol_series = ticker_df["Volume"] if "Volume" in ticker_df.columns else pd.Series([0]*len(ticker_df))

                    # [FIX MUL-21] Use forming-bar data during market hours; completed-bar otherwise
                    if is_market_open and forming_bar_open > 0:
                        today_open = forming_bar_open
                        today_close = forming_bar_close if forming_bar_close > 0 else real_time_close
                    else:
                        today_open = float(ticker_df["Open"].iloc[-1]) if "Open" in ticker_df.columns and len(ticker_df) > 0 else 0.0
                        today_close = float(close_series.iloc[-1]) if len(close_series) > 0 else 0.0

                    close_price = real_time_close
                    change_pct = real_time_change
                
                    close_yesterday = float(close_series.iloc[-2]) if len(close_series) >= 2 else float(close_series.iloc[-1])
                
                    if "High" in ticker_df.columns and "Low" in ticker_df.columns:
                        high_52w = float(ticker_df["High"].max())
                        low_52w = float(ticker_df["Low"].min())
                    else:
                        high_52w = float(close_series.max())
                        low_52w = float(close_series.min())
                
                    recent_20 = ticker_df.tail(20)
                    if not recent_20.empty and "Volume" in recent_20.columns:
                        avg_turnover = float((recent_20["Volume"] * recent_20["Close"]).mean())
                    else:
                        avg_turnover = 0.0
                
                    hist_idx_6m = min(120, len(close_series) - 1)
                    close_6m_ago = float(close_series.iloc[-(hist_idx_6m + 1)])
                    mom_6m = ((close_price - close_6m_ago) / close_6m_ago) if close_6m_ago > 0 else 0.0
                    
                    high_20d = float(close_series.tail(20).max())
                    high_60d = float(close_series.tail(60).max()) if len(close_series) >= 60 else high_20d
                
                    hist_idx = min(60, len(close_series) - 1)
                    close_3m_ago = float(close_series.iloc[-(hist_idx + 1)])
                    mom_3m = ((close_price - close_3m_ago) / close_3m_ago) if close_3m_ago > 0 else 0.0
                
                    latest_volume = float(vol_series.iloc[-1])
                    volume_sma20 = float(vol_series.tail(20).mean()) if len(vol_series) >= 20 else latest_volume
                
                    from indicator_manager import manager
                    bundle = manager.compute_base_indicators(ticker_df, sym)
                    
                    sma_20 = float(bundle.sma_20.iloc[-1]) if bundle.sma_20 is not None and not bundle.sma_20.empty else close_price
                    sma_50 = float(bundle.sma_50.iloc[-1]) if bundle.sma_50 is not None and not bundle.sma_50.empty else close_price
                    sma_200 = float(bundle.sma_200.iloc[-1]) if bundle.sma_200 is not None and not bundle.sma_200.empty else close_price
                    sma_200_yesterday = float(bundle.sma_200.iloc[-2]) if bundle.sma_200 is not None and len(bundle.sma_200) >= 2 else sma_200
                    
                    atr_14 = float(bundle.atr_14.iloc[-1]) if bundle.atr_14 is not None and not bundle.atr_14.empty else (close_price * 0.05)
                    ema_20 = float(bundle.ema_20.iloc[-1]) if bundle.ema_20 is not None and not bundle.ema_20.empty else close_price
                
                    closes_below_sma200_count = 0
                    if len(close_series) >= 5 and bundle.sma_200 is not None and len(bundle.sma_200.dropna()) >= 5:
                        last_5_closes = close_series.iloc[-5:]
                        last_5_smas = bundle.sma_200.iloc[-5:]
                        closes_below_sma200_count = sum(1 for c, s in zip(last_5_closes, last_5_smas) if c < s)
                
                    results[sym] = StockPriceData(
                        symbol=sym,
                        price=close_price,
                        change_pct=change_pct,
                        low_52w=low_52w,
                        high_52w=high_52w,
                        turnover_20d=avg_turnover,
                        sma_20=sma_20,
                        sma_50=sma_50,
                        sma_200=sma_200,
                        high_20d=high_20d,
                        high_60d=high_60d,
                        mom_3m=mom_3m,
                        mom_6m=mom_6m,
                        latest_volume=latest_volume,
                        volume_sma20=volume_sma20,
                        close_yesterday=close_yesterday,
                        sma_200_yesterday=sma_200_yesterday,
                        atr_14=atr_14,
                        ema_20=ema_20,
                        closes_below_sma200_count=closes_below_sma200_count,
                        last_trade_date=last_trade_date,
                        today_open=today_open,  # [FIX MUL-21]
                        today_close=today_close  # [FIX MUL-21]
                    )
                except Exception as e:
                    logger.debug(f"Error parsing market data for {sym}: {e}")
                
        del batch_res
            
    logger.info(f"✅ Successfully parsed price data for {len(results)}/{len(symbols)} tickers.")
    return results

def is_financial_sector(sector: str) -> bool:
    """Identify if the sector represents a bank, NBFC, or financial services firm."""
    if not sector:
        return False
    sec_lower = str(sector).lower()
    return any(keyword in sec_lower for keyword in ["financ", "bank", "nbfc", "insurance"])

def passes_multibagger_quality_gate(f: dict) -> tuple[bool, str]:
    """
    Hard pre-scoring quality gate for Multibagger alerts.
    Implements a Turnaround Alternative Quality Profile for recovering businesses.
    """
    if not isinstance(f, dict):
        return False, "Invalid fundamental dataset"
    
    known_metrics = []
    missing_metrics = []
    has_solvency_metric = False
    
    is_fin = f.get("is_financial", False)
    is_turnaround = "TURNAROUND" in str(f.get("category", "")).upper()
    
    def safe_float(val, default=0.0):
        import pandas as pd
        if val is None or pd.isna(val) or val == "": return default
        try: return float(val)
        except Exception: return default

    # 1. Auditor / Fraud checks (Universal)
    if f.get("auditor_flags") is True:
        return False, "Auditor/Forensic red flags"

    # 2. Promoter Pledge check (Universal)
    pledge_val = f.get("promoter_pledge_pct")
    pr = _pledge_ratio(pledge_val)
    if pr is not None:
        if pr > 0.20:
            return False, f"High promoter pledge ({pr*100:.1f}%)"
        known_metrics.append("Promoter Pledge")
    else:
        missing_metrics.append("Promoter Pledge")

    # 3. Piotroski F-Score / Quality Score check
    piot_score = f.get("score", f.get("piotroski_f_score", f.get("piotroski_score")))
    if piot_score is not None and not __import__('pandas').isna(piot_score):
        known_metrics.append(f"Piotroski ({safe_float(piot_score):.0f}/9)")
        if safe_float(piot_score) >= 1:
            has_solvency_metric = True
    else:
        missing_metrics.append("Piotroski Score")

    # 4. Financial Sector Logic
    if is_fin:
        # Tier 1: CAR
        car = normalize_ratio(f.get("capital_adequacy_ratio"))
        if car is not None:
            known_metrics.append(f"CAR ({car:.1%})")
            has_solvency_metric = True
            if car < 0.11:
                return False, f"Solvency fail: CAR {car:.2%}"
        else:
            missing_metrics.append("CAR")
            # Proxies
            roe_for_tier2 = f.get("roe")
            gnpa_for_tier2 = f.get("gnpa")
            roe_val_t2 = safe_float(roe_for_tier2) if (roe_for_tier2 is not None) else None
            gnpa_val_t2 = safe_float(gnpa_for_tier2) if (gnpa_for_tier2 is not None) else None
            
            tier2_roe_ok = (roe_val_t2 is not None and roe_val_t2 >= 0.12)
            tier2_gnpa_ok = (gnpa_val_t2 is None or gnpa_val_t2 <= 0.05)
            
            if tier2_roe_ok:
                has_solvency_metric = True
            elif gnpa_val_t2 is not None and gnpa_val_t2 <= 0.05:
                has_solvency_metric = True
            else:
                if roe_val_t2 is not None and roe_val_t2 < 0.05:
                    return False, f"Financial solvency UNKNOWN and ROE below 5% ({roe_val_t2*100:.1f}%)"
                if gnpa_val_t2 is not None and gnpa_val_t2 > 0.07:
                    return False, f"Financial solvency UNKNOWN and High GNPA ({gnpa_val_t2*100:.1f}%)"

        gnpa = f.get("gnpa")
        if gnpa is not None and not __import__('pandas').isna(gnpa):
            known_metrics.append(f"GNPA ({safe_float(gnpa)*100:.1f}%)")
            if safe_float(gnpa) > 0.05:
                return False, f"High GNPA ({safe_float(gnpa)*100:.1f}%)"
        else:
            missing_metrics.append("GNPA")

        # ROE Profile Check
        roe = f.get("roe")
        if roe is not None and not __import__('pandas').isna(roe):
            known_metrics.append(f"ROE ({safe_float(roe)*100:.1f}%)")
            if is_turnaround:
                evidence_score = 0
                yoy_rev = safe_float(f.get("yoy_revenue"))
                yoy_prof = safe_float(f.get("yoy_profit"))
                
                if yoy_rev > 0: evidence_score += 1
                if yoy_prof > 0: evidence_score += 1
                if gnpa is not None and safe_float(gnpa) <= 0.03: evidence_score += 1
                if car is not None and car >= 0.15: evidence_score += 1
                
                if evidence_score < 2:
                    return False, f"Fin Turnaround lacks momentum evidence (Score: {evidence_score}/2)"
            else:
                if safe_float(roe) < 0.10:
                    return False, f"Financial ROE below 10% ({safe_float(roe)*100:.1f}%)"
        else:
            missing_metrics.append("ROE")

    else:
        # Non-Financial Logic
        fcf_margin = f.get("fcf_margin")
        if fcf_margin is not None and not __import__('pandas').isna(fcf_margin):
            known_metrics.append("FCF Margin")
            if safe_float(fcf_margin) < 0.00:
                return False, f"Negative FCF conversion ({safe_float(fcf_margin)*100:.1f}%)"
        else:
            missing_metrics.append("FCF Margin")

        cfo_pat = f.get("cfo_pat_ratio")
        if cfo_pat is not None and not __import__('pandas').isna(cfo_pat):
            known_metrics.append("CFO/PAT")
            if safe_float(cfo_pat) < 0.5:
                return False, f"Poor cash conversion CFO/PAT ({safe_float(cfo_pat):.2f})"
        else:
            missing_metrics.append("CFO/PAT")

        de = f.get("debt_equity")
        if de is not None and not __import__('pandas').isna(de):
            known_metrics.append(f"D/E ({safe_float(de):.2f})")
            has_solvency_metric = True
            if safe_float(de) > 2.0:
                return False, f"Debt/Equity > 2.0 ({safe_float(de):.2f})"
        else:
            missing_metrics.append("Debt/Equity")

        icr = f.get("interest_coverage_ratio")
        if icr is not None and not __import__('pandas').isna(icr):
            known_metrics.append(f"ICR ({safe_float(icr):.1f}x)")
            has_solvency_metric = True
            if safe_float(icr) < 3.0:
                return False, f"Interest coverage < 3x ({safe_float(icr):.1f})"
        else:
            missing_metrics.append("Interest Coverage")

        altman_z = f.get("altman_z")
        if altman_z is not None and not __import__('pandas').isna(altman_z):
            known_metrics.append(f"Altman-Z ({safe_float(altman_z):.2f})")
            has_solvency_metric = True
            is_svc = any(k in str(f.get("sector", "")).lower() for k in ["technology", "communication", "services"])
            z_threshold = 1.10 if is_svc else 1.80
            if safe_float(altman_z) < z_threshold:
                return False, f"Altman-Z in distress zone ({safe_float(altman_z):.2f} < {z_threshold})"
        else:
            missing_metrics.append("Altman-Z")

        # Profitability Gates
        roce_val = f.get("roce", f.get("roe"))
        opm = f.get("operating_margin_ttm")
        rev_cagr = f.get("revenue_cagr_3y")
        
        if rev_cagr is not None and not __import__('pandas').isna(rev_cagr):
            known_metrics.append("Revenue CAGR 3Y")
            if safe_float(rev_cagr) < -0.10:
                return False, f"Revenue CAGR 3Y highly negative ({safe_float(rev_cagr)*100:.1f}%)"
        else:
            missing_metrics.append("Revenue CAGR 3Y")

        if is_turnaround:
            evidence_score = 0
            yoy_rev = safe_float(f.get("yoy_revenue"))
            yoy_prof = safe_float(f.get("yoy_profit"))
            
            if yoy_rev > 0: evidence_score += 1
            if yoy_prof > 0: evidence_score += 1
            if opm is not None and safe_float(opm) > 0: evidence_score += 1
            if de is not None and safe_float(de) < 1.0: evidence_score += 1
            if cfo_pat is not None and safe_float(cfo_pat) >= 1.0: evidence_score += 1
            
            if evidence_score < 3:
                return False, f"Turnaround lacks momentum evidence (Score: {evidence_score}/3 required)"
        else:
            if roce_val is not None and not __import__('pandas').isna(roce_val):
                known_metrics.append(f"ROCE ({safe_float(roce_val)*100:.1f}%)")
                roce = safe_float(roce_val)
                if roce < 0.05:
                    return False, f"ROCE/ROE below 5% ({roce*100:.1f}%)"
            else:
                missing_metrics.append("ROCE/ROE")
            
            if opm is not None and not __import__('pandas').isna(opm):
                known_metrics.append(f"OPM ({safe_float(opm)*100:.1f}%)")
                if safe_float(opm) < 0.08:
                    return False, f"Operating margin below 8% ({safe_float(opm)*100:.1f}%)"
            else:
                missing_metrics.append("Operating Margin")

        # Valuation / Fallback proxies in lightweight cache
        pe_fb = f.get("pe_fallback")
        if pe_fb is not None and not __import__('pandas').isna(pe_fb):
            known_metrics.append("P/E Ratio")

        pb_fb = f.get("pb_fallback")
        if pb_fb is not None and not __import__('pandas').isna(pb_fb):
            known_metrics.append("P/B Ratio")

        # Fallback solvency assumption for lightweight cache entries with valid Piotroski/ROE
        if not has_solvency_metric and len(known_metrics) >= 2:
            has_solvency_metric = True

    known_count = len(known_metrics)
    fin_tier3_exempt = is_fin and not has_solvency_metric
    if known_count < 2 or (not has_solvency_metric and not fin_tier3_exempt):
        known_str = ", ".join(known_metrics) if known_metrics else "None"
        missing_str = ", ".join(missing_metrics) if missing_metrics else "None"
        solv_str = "Present" if has_solvency_metric else "MISSING"
        return False, f"Data Void: Incomplete dataset ({known_count} populated: [{known_str}] | Missing: [{missing_str}] | Solvency: {solv_str})"
        
    return True, "OK"


def classify_conviction(cqs: float, pas: float, trend: float, composite: float, f_score: int = None, pledge_ratio: float = None) -> tuple[str, float]:
    """
    Tiered classification for multibaggers.
    Enforces Piotroski F-Score >= 7 for Top Tier ("🚀 Prime Multibagger").
    Returns (Tier, Score)
    """
    # [FIX MUL-11] Prime tier requires F-Score >= 7. The original `f_score is None`
    # let missing data pass as valid, silently bypassing the Piotroski requirement.
    is_prime_fscore = f_score is not None and f_score >= 7
    # [FIX MUL-23 REVISED] High Quality requires a verified clean pledge (<= 15%), not just "pledge is known".
    # A stock with pledge=25% should never get the "💎 High Quality" label.
    clean_pledge = pledge_ratio is not None and pledge_ratio <= 0.15

    if composite >= 75 and cqs >= 65 and pas >= 50 and trend >= 10.0 and is_prime_fscore and clean_pledge:
        return "🚀 Prime Multibagger", composite
    # [FIX ISSUE-5] High Quality requires clean pledge — Piotroski alone does not exempt from pledge check.
    # A stock with a great F-Score but 25% promoter pledge is still risky.
    elif composite >= 65 and cqs >= 60 and trend >= 10.0 and clean_pledge:
        return "💎 High Quality", composite
    elif composite >= 50:
        return "🟡 Watchlist", composite
    else:
        return "Invalidated", composite

def entry_confirmed(price_data: StockPriceData) -> bool:
    """
    Ensures technical stabilization before entry.
    Refined for Multibagger buy-zone pullbacks:
    1. Price at/above SMA200 support band (>= 0.96 * SMA200)
    2. Completed-bar volume >= 30% of 20-day average (allows quiet buy-zone pullbacks)
    3. Stabilized close (close >= 0.995 * open, or fallback if EOD open unverified)
    4. Near key support level (EMA20, SMA50, or SMA200)
    """
    if price_data.price < price_data.sma_200 * 0.96:
        return False

    if price_data.volume_sma20 <= 0:
        return False

    # Completed-bar volume: Allow >= 30% of 20-day average volume during buy-zone pullbacks
    completed_bar_volume_ok = price_data.latest_volume >= 0.30 * price_data.volume_sma20

    # Stabilized close: Allow green, flat, or mild consolidation doji
    if price_data.today_open and price_data.today_open > 0 and price_data.today_close and price_data.today_close > 0:
        stabilized_close = price_data.today_close >= (price_data.today_open * 0.995)
    else:
        stabilized_close = True  # EOD bar fallback when open is unverified

    # Support proximity: price is near EMA20, SMA50, or SMA200
    ema20 = price_data.ema_20 if price_data.ema_20 > 0 else price_data.price
    sma50 = price_data.sma_50 if price_data.sma_50 > 0 else price_data.price
    sma200 = price_data.sma_200 if price_data.sma_200 > 0 else price_data.price

    atr_allowance = (1.2 * price_data.atr_14) if price_data.atr_14 > 0 else (price_data.price * 0.04)
    max_upper_price = max(ema20 * 1.06, ema20 + atr_allowance)
    min_lower_price = min(ema20 * 0.92, sma50 * 0.96, sma200 * 0.96)

    near_support = (price_data.price >= min_lower_price) and (price_data.price <= max_upper_price)

    return completed_bar_volume_ok and stabilized_close and near_support

def _is_fundamental_cache_fresh(data: dict) -> bool:
    try:
        # Check both "fetched_at" (multibagger format) and "date" (global cache format)
        date_str = data.get("fetched_at") or data.get("date")
        if not date_str:
            return False
            
        # Parse it
        try:
            # Try isoformat first (fetched_at)
            fetched_at = datetime.fromisoformat(date_str)
        except ValueError:
            # Fallback to YYYY-MM-DD (date)
            fetched_at = datetime.strptime(date_str, "%Y-%m-%d")
            
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=IST)
            
        now_dt = datetime.now(IST)
        age_days = (now_dt - fetched_at).days
        
        # Fundamentals: 15 days TTL normally, 7 days during Saturday 06:00-10:00 AM IST window
        is_saturday_window = (now_dt.weekday() == 5 and 6 <= now_dt.hour < 10)
        max_age_days = 7 if is_saturday_window else 15
        
        return age_days < max_age_days
    except Exception as e:
        logger.debug(f"Freshness check failed: {e}")
        return False

def get_cached_fundamentals(symbol: str, cache: dict) -> Optional[Dict[str, Any]]:
    clean_sym = symbol.strip().upper()
    variants = [
        clean_sym,
        clean_sym.replace("&", "_"),
        clean_sym.replace("-", "_"),
        clean_sym.replace("&", "AND"),
        clean_sym.replace("_", "&"),
        clean_sym.replace("_", "-"),
        clean_sym.replace(".NS", ""),
        clean_sym.replace(".BO", "")
    ]
    for v in variants:
        if v in cache:
            try:
                data = cache[v]
                if _is_fundamental_cache_fresh(data):
                    res = {k: val for k, val in data.items() if k not in ("fetched_at", "date")}
                    res["symbol"] = clean_sym
                    return res
            except Exception as e:
                logger.debug(f"Failed to parse cache entry for {v}: {e}")
        
    # Fallback to shared global fundamentals_cache (from Postgres DB)
    try:
        from fundamentals_cache import get_fundamentals
        for v in variants:
            g_fund = get_fundamentals(v)
            if g_fund and not g_fund.get("failed", False):
                if _is_fundamental_cache_fresh(g_fund):
                    res = {k: val for k, val in g_fund.items() if k not in ("fetched_at", "date")}
                    res["symbol"] = clean_sym
                    return res
                else:
                    logger.debug(f"Global cache for {v} is stale.")
    except Exception as _g_err:
        logger.debug(f"Global fundamentals_cache fallback failed for {symbol}: {_g_err}")

    return None


def safe_extract(df, row_name, col_idx=0, default=None):
    try:
        if row_name in df.index:
            val = df.loc[row_name].iloc[col_idx]
            if not pd.isna(val): return float(val)
    except (TypeError, ValueError, KeyError, IndexError) as e:
        logger.debug(f"Extract error for {row_name}: {e}")
    return default

# [FIX ISSUE-2/3] Normalize ratio values that providers may return as either
# percentage (15.0) or decimal (0.15). Always normalizes to 0.0-1.0 scale.
# [VERSION: MULTIBAGGER_STALE_DATE_FIX_v1.1] Detect stale trade dates for new entries.
# Checks if last_trade_date is >= max_business_days (default 3 business days).
# Fails closed (returns True) on parsing exception or missing date to protect against unverified freshness.
def _is_stale_trade_date(last_trade_date, max_business_days=3):
    if not last_trade_date:
        return True  # No date => treat as stale
    try:
        import numpy as np
        clean_date_str = str(last_trade_date)[:10]
        start = np.datetime64(clean_date_str)
        end = np.datetime64(datetime.now(IST).date())
        # [FIX ISSUE-4] >= max_business_days: trade exactly 3 business days old is stale
        return int(np.busday_count(start, end)) >= max_business_days
    except Exception as exc:
        logger.warning(f"Unable to validate trade date {last_trade_date}: {exc}")
        return True  # [FIX ISSUE-3] Fail closed on error => treat as stale

def normalize_ratio(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        ratio = float(value)
        if ratio > 1.0:
            return ratio / 100.0
        return ratio
    except (TypeError, ValueError):
        return None

def compute_cagr(df, row_name, years=3):
    try:
        if row_name not in df.index: return None
        row = df.loc[row_name].dropna()
        if len(row) < 2: return None
        latest = float(row.iloc[0])
        idx = min(years, len(row) - 1)
        oldest = float(row.iloc[idx])
        if oldest and oldest > 0 and latest and latest > 0:
            return ((latest / oldest) ** (1.0 / idx)) - 1.0
    except (TypeError, ValueError, KeyError, IndexError) as e:
        logger.debug(f"CAGR error for {row_name}: {e}")
    return None

def fetch_ticker_fundamentals(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        from bse_mapping_utils import load_bse_mappings, save_bse_mapping
        clean_sym = symbol.strip().upper()
        mappings = load_bse_mappings()
        if clean_sym in mappings:
            ticker_name = mappings[clean_sym]
        elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
            ticker_name = mappings[clean_sym[:-3]]
        else:
            ticker_name = f"{symbol}.NS"
    except Exception:
        ticker_name = f"{symbol}.NS"
        
    info, fast_info, fin, bs, cf = None, None, None, None, None
    success = False
    
    # Spacing delay is regulated by global yf_rate_limiter.py instead of hardcoded sleeps

    
    for attempt in range(3):
        try:
            yf_acquire(context=f"Multibagger Scanner | {symbol}")
            try:
                ticker = yf.Ticker(ticker_name)
                info = ticker.info
                fast_info = getattr(ticker, 'fast_info', {})
                fin = ticker.financials
                bs = ticker.balance_sheet
                cf = ticker.cashflow
            finally:
                yf_release()
                
            mc = info.get("marketCap") if info else None
            if mc is None and fast_info:
                mc = fast_info.get("marketCap")
                
            if (fin is None or fin.empty or not mc) and ticker_name.endswith(".NS"):
                bse_sym = ticker_name[:-3] + ".BO"
                logger.info(f"🔄 Multibagger: financials/marketCap missing for {ticker_name}, retrying with {bse_sym}...")
                yf_acquire(context=f"Multibagger Scanner | {symbol}")
                try:
                    ticker = yf.Ticker(bse_sym)
                    info = ticker.info
                    fast_info = ticker.fast_info
                    fin = ticker.financials
                    bs = ticker.balance_sheet
                    cf = ticker.cashflow
                    ticker_name = bse_sym
                    if not (fin is None or fin.empty):
                        try:
                            from bse_mapping_utils import save_bse_mapping
                            save_bse_mapping(symbol, bse_sym)
                        except Exception:
                            pass
                finally:
                    yf_release()
            
            # [VERSION: MULTIBAGGER_REVERSE_FALLBACK_v1.0] Poisoned BO mapping → recover via NS
            # If the mapping pointed us to .BO but it returned empty financials, the BSE ticker
            # is likely delisted/suspended. Invalidate the mapping and retry via NSE.
            elif (fin is None or fin.empty) and ticker_name.endswith(".BO"):
                logger.info(f"🗑️ Multibagger: poisoned BSE mapping for {symbol} ({ticker_name}). Invalidating and retrying via NSE...")
                try:
                    from bse_mapping_utils import load_bse_mappings, invalidate_bse_mapping
                    orig_clean = symbol.strip().upper()
                    # Strip any suffix — DB stores bare symbol
                    bare_orig = orig_clean[:-3] if orig_clean.endswith(".NS") or orig_clean.endswith(".BO") else orig_clean
                    invalidate_bse_mapping(bare_orig)
                except Exception as inv_err:
                    logger.warning(f"Failed to invalidate poisoned mapping for {symbol}: {inv_err}")
                ns_sym = ticker_name[:-3] + ".NS"
                yf_acquire(context=f"Multibagger Scanner | {symbol} (NS recovery)")
                try:
                    ticker_ns = yf.Ticker(ns_sym)
                    fin_ns = ticker_ns.financials
                    bs_ns = ticker_ns.balance_sheet
                    if not (fin_ns is None or fin_ns.empty):
                        ticker = ticker_ns
                        info = ticker_ns.info
                        fast_info = ticker_ns.fast_info
                        fin = fin_ns
                        bs = bs_ns
                        cf = ticker_ns.cashflow
                        ticker_name = ns_sym
                        logger.info(f"✅ Multibagger: NSE recovery succeeded for {symbol} via {ns_sym}")
                    else:
                        logger.warning(f"⚠️ Multibagger: both .BO and .NS returned empty for {symbol}. Skipping.")
                except Exception as ns_err:
                    logger.warning(f"Multibagger NS recovery failed for {symbol}: {ns_err}")
                finally:
                    yf_release()
            success = True
            break
        except Exception as e:
            if ticker_name.endswith(".NS"):
                bse_sym = ticker_name[:-3] + ".BO"
                logger.info(f"🔄 Multibagger exception for {ticker_name}, retrying with BSE {bse_sym}...")
                try:
                    yf_acquire(context=f"Multibagger Scanner | {symbol}")
                    try:
                        ticker = yf.Ticker(bse_sym)
                        info = ticker.info
                        fast_info = ticker.fast_info
                        fin = ticker.financials
                        bs = ticker.balance_sheet
                        cf = ticker.cashflow
                        ticker_name = bse_sym
                        try:
                            from bse_mapping_utils import save_bse_mapping
                            save_bse_mapping(symbol, bse_sym)
                        except Exception:
                            pass
                        success = True
                        break
                    finally:
                        yf_release()
                except Exception:
                    pass
            
            msg = str(e).lower()
            if any(term in msg for term in ["too many requests", "429", "503", "502", "504", "crumb", "unauthorized", "connection termination", "upstream connect", "reset reason", "service unavailable"]):
                record_rate_limit(context=f"Multibagger Scanner | {symbol}")
                
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
            else:
                logger.warning(f"Error for {symbol}: {e}")

    # Fallback salvage function definition
    def try_salvage():
        try:
            fast = ticker.fast_info
            fallback_mc = fast.get("marketCap")
            fallback_price = fast.get("lastPrice")
            if fallback_mc and fallback_price:
                logger.info(f"🔄 Salvaging basic data for {symbol} via fast_info fallback.")
                return {
                    "symbol": symbol,
                    "sector": "Unknown",
                    "market_cap": fallback_mc,
                    "shares_outstanding": fallback_mc / fallback_price,
                    "price": fallback_price,
                    "data_freshness": "FALLBACK",
                    "is_financial": False
                }
        except Exception:
            pass
        return None

    if not success or fin is None or fin.empty:
        return try_salvage()
        
    market_cap = info.get("marketCap")
    if market_cap is None and fast_info is not None:
        market_cap = fast_info.get("marketCap")
        
    if not market_cap:
        return try_salvage()
        
    pat = safe_extract(fin, 'Net Income')
    cfo = safe_extract(cf, 'Operating Cash Flow') or info.get('operatingCashflow')
    revenue = safe_extract(fin, 'Total Revenue')
    assets = safe_extract(bs, 'Total Assets')
    ebit = safe_extract(fin, 'EBIT')
    current_liab = safe_extract(bs, 'Current Liabilities')
    working_capital = safe_extract(bs, 'Working Capital')
    retained_earnings = safe_extract(bs, 'Retained Earnings')
    total_liab = safe_extract(bs, 'Total Liabilities Net Minority Interest') or safe_extract(bs, 'Total Liabilities')
    
    cfo_pat = cfo / pat if pat and cfo and pat > 0 else None
    ato = revenue / assets if revenue and assets and assets > 0 else None
    roic = ebit / (assets - current_liab) if ebit and assets and current_liab and (assets - current_liab) > 0 else None
    
    altman_z = None
    market_cap = info.get('marketCap')
    if all(v is not None for v in [working_capital, retained_earnings, ebit, market_cap, total_liab, assets]) and assets > 0 and total_liab > 0:
        x1 = working_capital / assets
        x2 = retained_earnings / assets
        x3 = ebit / assets
        x4 = market_cap / total_liab
        
        # [VERSION: MULTIBAGGER_Z_FIX_v1.0] Determine Z''-score for service/non-manufacturing firms vs standard Z-score for manufacturing firms
        is_svc = any(k in str(info.get("sector", "")).lower() for k in ["technology", "communication", "services"]) or \
                 any(k in str(info.get("industry", "")).lower() for k in ["services", "software", "consulting", "internet", "retail", "media"])
                 
        if is_svc:
            altman_z = (6.56 * x1) + (3.26 * x2) + (6.72 * x3) + (1.05 * x4)
        else:
            x5 = revenue / assets if revenue else 0
            altman_z = (1.2 * x1) + (1.4 * x2) + (3.3 * x3) + (0.6 * x4) + (1.0 * x5)
    
    # Map to V5 Engine Expected Keys
    price = info.get("currentPrice")
    if not price:
        price = fast_info.get("lastPrice")
        logger.debug(f"[DATA] {symbol}: Primary currentPrice missing, falling back to fast_info.lastPrice")
    shares = info.get("sharesOutstanding")
    if not shares and market_cap and price is not None and price > 0:
        shares = market_cap / price
    elif not shares:
        shares = 1.0
        
    eps = safe_float(info.get("trailingEps"))
    if not eps and pat is not None:
        eps = pat / shares
        
    bv = safe_float(info.get("bookValue"))
    if not bv and assets and total_liab:
        bv = (assets - total_liab) / shares
        
    fcf = info.get("freeCashflow")
    if fcf is None and cfo is not None:
        capex = abs(safe_extract(cf, 'Capital Expenditure', default=0.0))
        fcf = cfo - capex
    
    total_equity = safe_extract(bs, 'Stockholders Equity') or safe_extract(bs, 'Total Stockholder Equity')
    if not total_equity and assets and total_liab:
        total_equity = assets - total_liab
    if not total_equity and bv and shares:
        total_equity = bv * shares
        
    roe = None
    # [VERSION: MULTIBAGGER_ROE_FIX_v1.0] Added ROE calculation with safeguards
    if pat is not None and not pd.isna(pat) and total_equity is not None and total_equity > 0:
        roe = pat / total_equity

    # [FIX MUL-20] Compute ROA from net income / total assets — used by financial solvency gate
    roa = None
    if pat is not None and not pd.isna(pat) and assets is not None and assets > 0:
        roa = pat / assets
    
    fund = {
        "symbol": symbol,
        "roe": roe,
        "sector": info.get("sector", "Unknown"),
        "market_cap": market_cap,
        "shares_outstanding": shares,
        "eps": eps,
        "book_value_per_share": bv,
        "free_cash_flow": fcf,
        "ebit": ebit,
        "tt_indpe": info.get("trailingPE"), # Proxy for industry PE if missing
        
        "operating_margin_ttm": info.get("operatingMargins"),
        "gross_margin_stability": (info.get("grossMargins") or 0.0) * 0.1, # Proxy
        "roce": roic,
        "cfo_pat_ratio": cfo_pat,
        "fcf_margin": fcf / revenue if revenue and fcf is not None else None,
        
        "revenue_cagr_3y": compute_cagr(fin, 'Total Revenue', 3),
        "pat_cagr_3y": compute_cagr(fin, 'Net Income', 3),  # [FIX #6] Renamed: this is PAT CAGR, not per-share EPS CAGR
        "fcf_cagr_3y": compute_cagr(cf, 'Free Cash Flow', 3),
        "reinvestment_rate": (retained_earnings or 0.0) / assets if assets else 0.0,
        
        "debt_equity": info.get("debtToEquity") / 100.0 if info.get("debtToEquity") is not None else None,
        # [FIX] ICR: do not use abs() on EBIT to preserve negative earnings signal.
        "interest_coverage_ratio": (lambda ie: (ebit / abs(ie)) if (ebit is not None and ie and abs(ie) > 1) else (100.0 if ebit is not None and ebit >= 0 else (-100.0 if ebit is not None else None)))(safe_extract(fin, 'Interest Expense')),
        "debt_yoy_growth": None,  # [FIX ISSUE-12] Set None (unknown) instead of 0.0 (confirmed no growth)
        "altman_z": altman_z,
        "current_ratio": info.get("currentRatio"),
        "roa": roa,  # [FIX MUL-20] Added ROA for financial solvency gate
        # [FIX ISSUE-2/3] Populate CAR from yfinance info for financial-sector solvency gate
        "capital_adequacy_ratio": normalize_ratio(info.get("capitalAdequacyRatio") or info.get("capitalToRiskWeightedAssets")),

        "price": price,
        "is_financial": is_financial_sector(info.get("sector")),
        "data_freshness": "LIVE",
        "total_equity": total_equity,
        "score": (lambda: (__import__('fundamentals_cache').compute_piotroski(info, fin, balance_sheet=bs) if (info and fin is not None and not fin.empty) else None))(),
        "piotroski_score": (lambda: (__import__('fundamentals_cache').compute_piotroski(info, fin, balance_sheet=bs) if (info and fin is not None and not fin.empty) else None))(),
        "piotroski_f_score": (lambda: (__import__('fundamentals_cache').compute_piotroski(info, fin, balance_sheet=bs) if (info and fin is not None and not fin.empty) else None))()
    }
    
    return fund



def save_watchlist_to_db(results: list):
    """Save watchlist candidates in bulk using psycopg2 execute_values."""
    if not results:
        return
    
    # Map ScreenerResult attributes to list of tuples for execute_values
    data = []
    for r in results:
        # [FIX MUL-17] Only mark last_alert_price/at for results that actually
        # inserted into the DB. Before this, any result with ALERT_TRIGGERED status
        # (including those suppressed by Top-N or rejected by save_alert_if_new)
        # would update last_alert_price in the watchlist.
        if getattr(r, 'alert_inserted', False):
            last_price = r.price
            last_at = datetime.now(IST)
        else:
            last_price = None
            last_at = None
            
        data.append((
            r.symbol.upper(), r.buy_zone_low, r.buy_zone_high, r.price,
            r.cqs, r.pas, r.trend_score, r.total_score, r.bucket, r.status, r.notes,
            last_price, last_at
        ))
        
    try:
        with get_connection() as conn:
            if hasattr(conn, "is_dummy") and getattr(conn, "is_dummy", False):
                logger.info("Local DummyConnection active — skipping watchlist DB execute_values.")
                return
            with conn.cursor() as cur:
                if not hasattr(cur, "connection"):
                    logger.info("Cursor has no connection attribute — skipping execute_values.")
                    return
                # Upsert query using execute_values
                execute_values(cur, """
                    INSERT INTO watchlist 
                    (symbol, buy_zone_low, buy_zone_high, latest_price, 
                     growth_score, value_score, trend_score, total_score, bucket, status, notes,
                     last_alert_price, last_alert_at)
                    VALUES %s
                    ON CONFLICT (symbol) DO UPDATE SET
                        buy_zone_low = EXCLUDED.buy_zone_low,
                        buy_zone_high = EXCLUDED.buy_zone_high,
                        latest_price = EXCLUDED.latest_price,
                        growth_score = EXCLUDED.growth_score,
                        value_score = EXCLUDED.value_score,
                        trend_score = EXCLUDED.trend_score,
                        total_score = EXCLUDED.total_score,
                        bucket = EXCLUDED.bucket,
                        status = CASE WHEN watchlist.status = 'REJECTED' THEN 'REJECTED' ELSE EXCLUDED.status END,
                        notes = EXCLUDED.notes,
                        last_alert_price = COALESCE(EXCLUDED.last_alert_price, watchlist.last_alert_price),
                        last_alert_at = COALESCE(EXCLUDED.last_alert_at, watchlist.last_alert_at),
                        last_updated = CURRENT_TIMESTAMP;
                """, data)
            conn.commit()
        logger.info(f"✅ Stored {len(results)} candidates in watchlist (execute_values).")
    except Exception as e:
        logger.exception(f"❌ Failed to bulk write to watchlist")


def format_telegram_message(categorized_stocks: dict) -> list:
    """Format categorized stocks into chunked Telegram messages (HTML)."""
    messages = []
    current_msg = "<b>🚀 DAILY MULTIBAGGER WATCHLIST SUMMARY</b>\n"
    current_msg += f"<i>Time: {datetime.now(IST).strftime('%Y-%m-%d %H:%M IST')}</i>\n"
    current_msg += "========================================\n\n"
    
    has_results = False
    for label, stocks in categorized_stocks.items():
        if not stocks:
            continue
        has_results = True
        
        section_text = f"<b>{label}</b> ({len(stocks)} stocks):\n"
        current_msg += section_text
        for item in sorted(stocks, key=lambda x: x['total'], reverse=True):
            sym = item['symbol']
            cqs = item['cqs']
            pas = item['pas']
            price = item['price']
            total = item['total']
            status = item['status']
            
            alert_marker = " 🔔 <b>BUY READY</b>" if status == "ALERT_TRIGGERED" else (" ⏳ WAITING" if status in ("WAITING_BUY_ZONE", "REJECTED") else f" ⛔ {status}")
            line = f"• <b>{sym}</b> (₹{price:.1f}) | CQS: {cqs:.1f} | PAS: {pas:.1f} | Total: <b>{total:.1f}/100</b>{alert_marker}\n"
            
            if len(current_msg) + len(line) > 3900:
                messages.append(current_msg)
                current_msg = "<b>🚀 MULTIBAGGER WATCHLIST SUMMARY (Cont.)</b>\n\n"
                
            current_msg += line

        current_msg += "\n"

    if not has_results:
        current_msg += "ℹ️ No stocks qualified for multibagger categorization this week.\n"
        messages.append(current_msg)
    else:
        messages.append(current_msg)
        
    return messages

def run_scanner(debug_limit: int = None, is_test_mode: bool = False, session=None, run_ctx=None):
    """Main execution orchestrator for Multibagger Scanner V5."""
    import time
    start_time = time.time()
    logger.info("=================================================================")
    logger.info("🚀 STARTING ELITE MULTIBAGGER SCANNER V5.0")
    logger.info("=================================================================")
    
    # Clear pledge cache to ensure fresh values are fetched from DB today
    try:
        from pledge_scraper import fetch_promoter_pledge
        fetch_promoter_pledge.cache_clear()
        logger.info("🧹 Cleared fetch_promoter_pledge LRU cache for today's run.")
    except Exception as e:
        logger.warning(f"Failed to clear fetch_promoter_pledge cache: {e}")

    # Ensure tables and functions are created
    init_db()

    from zoneinfo import ZoneInfo
    today_str = datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d')
    
    # ── VALIDATE UPSTREAM MANIFEST ──
    try:
        from database import get_latest_build_manifest
        from config import WATCHLIST_PATH
        manifest = get_latest_build_manifest(today_str)
        if not manifest or manifest.get("status") not in ("SUCCESS", "FALLBACK_SUCCESS"):
            if os.path.exists(WATCHLIST_PATH) or is_test_mode:
                logger.warning(f"⚠️ [MULTIBAGGER] No build_manifest record for {today_str}, but valid watchlist parquet file exists. Proceeding with scan...")
            else:
                logger.error(f"🛑 [MULTIBAGGER] Aborting run: No build manifest or watchlist file found for {today_str}.")
                upsert_scanner_health("MULTIBAGGER", "DOWN", error_msg=f"Upstream manifest invalid/missing for {today_str}")
                return {}
    except Exception as e:
        logger.warning(f"⚠️ [MULTIBAGGER] Failed to validate upstream manifest: {e}. Proceeding cautiously.")
    
    upsert_scanner_health("MULTIBAGGER", "RUNNING")
    
    # Delegate to the actual scanning logic
    return _start_wrapper(debug_limit, is_test_mode, session, run_ctx)

def _persist_sell_review(alert_id, reason):
    """[VERSION: MULTIBAGGER_PERSIST_REVIEW_v1.1] Persist SELL_REVIEW status in the database without closing the position.
    Matches status IN ('OPEN', 'SELL_REVIEW') so existing reviewed positions update reason and timestamp."""
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE alerts
                    SET status = 'SELL_REVIEW',
                        exit_signal = %s,
                        exit_reason = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND status IN ('OPEN', 'SELL_REVIEW')
                """, (reason, reason, alert_id))
            conn.commit()
        logger.info(f"📝 SELL_REVIEW persisted for alert_id={alert_id}: {reason}")
    except Exception as e:
        logger.error(f"Failed to persist SELL_REVIEW for alert_id={alert_id}: {e}")

def _clear_sell_review_to_open(alert_id, symbol):
    """Restores a position currently in SELL_REVIEW back to OPEN when fundamental metrics are resolved."""
    try:
        from database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE alerts
                    SET status = 'OPEN',
                        exit_signal = NULL,
                        exit_reason = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                      AND status = 'SELL_REVIEW'
                """, (alert_id,))
                if cur.rowcount > 0:
                    logger.info(f"🔄 Auto-cleared SELL_REVIEW for {symbol} (Alert #{alert_id}) back to OPEN status as fundamental metrics are resolved.")
            conn.commit()
    except Exception as e:
        logger.error(f"Failed to clear SELL_REVIEW for alert_id={alert_id}: {e}")

def run_exit_monitor(price_data_map: dict, cache: dict, is_test_mode: bool = False):
    """
    Evaluates open MULTIBAGGER positions in the database for exit signals.
    Excludes other buy alerts generated by other scanners.
    """
    logger.info("🔍 Running Exit Monitor for open MULTIBAGGER positions...")
    try:
        from psycopg2.extras import RealDictCursor
        open_positions = []
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # [VERSION: MULTIBAGGER_EXIT_FUND_FIX_v1.1] Include both OPEN and SELL_REVIEW so reviewed positions remain monitored
                cur.execute("""
                    SELECT id, symbol, entry_price as alert_price, alert_date, status
                    FROM alerts 
                    WHERE scanner = 'MULTIBAGGER' AND status IN ('OPEN', 'SELL_REVIEW') AND is_rejected = FALSE;
                """)
                open_positions = [dict(row) for row in cur.fetchall()]
                
        if not open_positions:
            logger.info("ℹ️ No open MULTIBAGGER positions found. Skipping exits.")
            return
            
        logger.info(f"🔄 Evaluating exits for {len(open_positions)} open MULTIBAGGER positions...")
        
        # [VERSION: MULTIBAGGER_EXIT_BATCH_v1.1] Only fetch open positions missing from price_data_map
        open_symbols = [pos["symbol"] for pos in open_positions]
        exit_prices = {}
        missing_open_symbols = [s for s in open_symbols if s not in price_data_map]
        if missing_open_symbols:
            try:
                exit_prices = batch_download_market_data(missing_open_symbols)
            except Exception as e:
                logger.warning(f"Failed to batch download exit prices: {e}")

        for pos in open_positions:
            try:
                symbol = pos["symbol"]
                entry_price = float(pos["alert_price"]) if pos.get("alert_price") is not None else 0.0
                alert_id = pos["id"]
                current_status = pos.get("status")
                
                # Try exit_prices first, then fall back to price_data_map
                price_data = exit_prices.get(symbol) or price_data_map.get(symbol)
                
                # Check for temporary provider outage vs permanent stale data
                if not price_data:
                    logger.error(f"🚨 [EXIT MONITOR] {symbol}: No price data available in batch. Skipping evaluation to prevent false exit.")
                    # [VERSION: EXIT_MONITOR_MISSING_PRICE_FIX_v1.0] Safely skip instead of triggering SELL_REVIEW
                    try:
                        msg = f"🚨 <b>Exit Monitor Error</b>\nUnable to fetch live price for {symbol}. Skipping evaluation to prevent false exit. Providers may be rate-limited or stock suspended."
                        queue_telegram_message(msg, symbol=symbol)
                    except Exception as e:
                        logger.exception(f"Failed to send telegram alert for {symbol} missing price data.")
                    continue
                    
                current_price = price_data.price

                
                # [VERSION: MULTIBAGGER_EXIT_FRESHNESS_v1.0] Exit freshness validation (fail closed)
                last_trade_date = getattr(price_data, "last_trade_date", None)
                if not last_trade_date:
                    logger.warning(f"⚠️ {symbol}: SELL_REVIEW: Trade date unavailable")
                    if not is_test_mode:
                        _persist_sell_review(alert_id, "SELL_REVIEW: Trade date unavailable")
                    continue

                try:
                    import numpy as np
                    start_date = np.datetime64(str(last_trade_date)[:10])
                    end_date = np.datetime64(datetime.now(IST).date())
                    bus_days = np.busday_count(start_date, end_date)
                    if bus_days >= 10:
                        stale_reason = f"SELL_REVIEW: Stale Price Data. Last trade was {last_trade_date} ({bus_days} trading sessions ago). Stock may be suspended or delisted."
                        logger.warning(f"⚠️ {symbol}: {stale_reason}")
                        if not is_test_mode:
                            _persist_sell_review(alert_id, stale_reason)
                        continue
                except Exception as exc:
                    logger.warning(f"Stale data check failed for {symbol}: {exc}")
                    if not is_test_mode:
                        _persist_sell_review(
                            alert_id,
                            "SELL_REVIEW: Unable to verify price freshness",
                        )
                    continue

                exit_triggered = False
                exit_reason = ""

                # Fetch fundamentals (using cache first)
                fund = get_cached_fundamentals(symbol, cache)
                if not fund:
                    fund = fetch_ticker_fundamentals(symbol)

                # [VERSION: MULTIBAGGER_EXIT_HIERARCHY_v1.0] Rule 1: Emergency Catastrophic Stop Loss ALWAYS runs first
                # to protect capital against severe drawdown (>= 20-30% loss) even if fundamental data is missing or degraded.
                if entry_price > 0:
                    drawdown_pct = ((entry_price - current_price) / entry_price) * 100.0

                    mcap_cr = (safe_float(fund.get("market_cap")) / 10000000.0) if fund else 0.0
                    if mcap_cr > 20000:
                        max_loss_pct = 20.0  # Large Cap
                        cap_tier = "Large Cap"
                    elif mcap_cr > 5000:
                        max_loss_pct = 25.0  # Mid Cap
                        cap_tier = "Mid Cap"
                    else:
                        max_loss_pct = 30.0  # Small/Micro Cap
                        cap_tier = "Small Cap"

                    if price_data.sma_200 > 0 and current_price < 0.90 * price_data.sma_200:
                        max_loss_pct -= 2.0  # Tighten stop by 2% if deeply bearish trend
                        trend_health = "Weak Trend"
                    else:
                        trend_health = "Strong/Neutral Trend"

                    if drawdown_pct >= max_loss_pct:
                        exit_triggered = True
                        exit_reason = f"Catastrophic Stop [{cap_tier}, {trend_health}]: Drawdown >= {max_loss_pct:.1f}% ({drawdown_pct:.1f}% loss)"

                # Handle missing fundamental data: if Catastrophic Stop did NOT trigger, persist SELL_REVIEW and finish.
                if not exit_triggered and not fund:
                    logger.warning(f"[EXIT MONITOR] {symbol}: fundamentals unavailable — SELL_REVIEW")
                    if not is_test_mode:
                        _persist_sell_review(
                            alert_id,
                            "SELL_REVIEW: Fundamental data unavailable",
                        )
                    continue

                # If fundamentals present and Catastrophic Stop did NOT trigger, run fundamental checks & V5 pipeline
                if not exit_triggered and fund:
                    technicals = {
                        "price": current_price,
                        "sma_50": price_data.sma_50,
                        "sma_200": price_data.sma_200,
                        "atr": price_data.atr_14
                    }
                    cqs = None
                    is_invalid = False
                    invalidation_reason = ""
                    try:
                        decision = run_pipeline_for_symbol(symbol, fund, technicals)
                        cqs = decision.quality.score
                        is_invalid = decision.is_invalidated
                        invalidation_reason = decision.invalidation_reason or ""
                    except Exception as pipeline_exc:
                        logger.warning(f"[EXIT MONITOR] {symbol}: fundamental pipeline failed: {pipeline_exc}")

                    is_fallback = fund.get("data_freshness") == "FALLBACK"
                    is_review_only_gate = False
                    if not is_fallback:
                        # [VERSION: MULTIBAGGER_EXIT_GATE_FIX_v1.2]
                        # Entry screening rules (passes_multibagger_quality_gate) should NOT forcibly close
                        # open Multibagger positions due to minor single-quarter metric dips (e.g. temporary FCF or OPM variance).
                        # Only severe structural breaches (auditor/forensic flags, promoter pledge > 40%) trigger forced exits.
                        ok, gate_reason = passes_multibagger_quality_gate(fund)
                        if not ok:
                            is_review_only_gate = True
                            severe_breach = False
                            if "Auditor" in gate_reason or "Forensic" in gate_reason:
                                severe_breach = True
                                exit_triggered = True
                                exit_reason = f"Severe Forensic Breach: {gate_reason}"
                            elif "pledge" in gate_reason.lower() and _pledge_ratio(fund.get("promoter_pledge_pct")) > 0.40:
                                severe_breach = True
                                exit_triggered = True
                                exit_reason = f"Severe Promoter Pledge Breach (>40%): {gate_reason}"

                            if not severe_breach:
                                logger.info(f"[EXIT MONITOR] {symbol}: {gate_reason} — flagging as SELL_REVIEW (keeping position open)")
                                if not is_test_mode:
                                    _persist_sell_review(alert_id, f"SELL_REVIEW: {gate_reason}")

                    # If review-only gate breach occurred, skip fundamental decay and 200-DMA breakdown exits
                    if not exit_triggered and not is_review_only_gate:
                        # [VERSION: MULTIBAGGER_DATA_SAFETY_v2.0]
                        # Incomplete or invalid data MUST NEVER trigger a trade sell/exit.
                        # Convert to SELL_REVIEW, keep position open, and notify admin.
                        if is_invalid or is_fallback:
                            is_review_only_gate = True
                            inv_msg = invalidation_reason or "Incomplete or Fallback data used"
                            logger.warning(f"⚠️ [EXIT MONITOR] {symbol}: Incomplete data ({inv_msg}) — flagging as SELL_REVIEW (keeping position OPEN)")
                            if not is_test_mode:
                                _persist_sell_review(alert_id, f"SELL_REVIEW: Incomplete Data ({inv_msg})")
                                
                                # Use atomic throttling for admin notifications
                                def _should_notify_sell_review(sym: str) -> bool:
                                    try:
                                        from database import get_connection
                                        from datetime import datetime
                                        today_str = datetime.now(IST).strftime("%Y-%m-%d")
                                        key = f"sell_review_notif_{sym}"
                                        with get_connection() as conn:
                                            with conn.cursor() as cur:
                                                cur.execute("""
                                                    INSERT INTO system_state (key, value) 
                                                    VALUES (%s, %s) 
                                                    ON CONFLICT (key) DO UPDATE 
                                                    SET value = EXCLUDED.value 
                                                    WHERE system_state.value != EXCLUDED.value
                                                    RETURNING value
                                                """, (key, today_str))
                                                row = cur.fetchone()
                                                # If row is returned, it means the insert/update was successful -> first time today
                                                if not row:
                                                    return False
                                            conn.commit()
                                        return True
                                    except Exception as _th_err:
                                        logger.warning(f"Throttle check failed: {_th_err}")
                                        return True

                                if _should_notify_sell_review(symbol):
                                    try:
                                        from database import insert_notification
                                        insert_notification("admin", f"⚠️ [SELL REVIEW] {symbol}: Incomplete Data", f"Multibagger position {symbol} flagged for Sell Review due to incomplete data: {inv_msg}. Position kept OPEN. We are looking into this.")
                                    except Exception as _notif_err:
                                        logger.warning(f"Could not insert admin notification for {symbol}: {_notif_err}")
                                    try:
                                        from telegram_engine import queue_telegram_message
                                        queue_telegram_message(f"⚠️ <b>[SELL REVIEW] {symbol}</b>\nExit evaluation deferred due to incomplete data: <i>{inv_msg}</i>.\nPosition remains OPEN under review.", symbol=symbol)
                                    except Exception as _tg_err:
                                        logger.warning(f"Could not queue Telegram sell review message for {symbol}: {_tg_err}")
                        # Check CQS score decay (< 55)
                        elif cqs is not None and cqs < 55.0:
                            exit_triggered = True
                            exit_reason = f"Deteriorating Fundamentals: Quality score decayed below hold-threshold 55 (CQS: {cqs:.1f})"
                        # Rule 2: Anti-Whipsaw 200-DMA exit
                        elif price_data.sma_200 > 0:
                            closes_below_count = getattr(price_data, "closes_below_sma200_count", 0)
                            if closes_below_count >= 3 and current_price < 0.93 * price_data.sma_200:
                                exit_triggered = True
                                exit_reason = f"Sustained 200-DMA breakdown: 3+ closes below, and >7% deep (Price: ₹{current_price:.1f}, 200-DMA: ₹{price_data.sma_200:.1f})"
                        
                        # Auto-resolution: If position was in SELL_REVIEW but now passes quality gate cleanly, restore to OPEN
                        if current_status == "SELL_REVIEW" and not is_test_mode:
                            _clear_sell_review_to_open(alert_id, symbol)
                        
                # Handle triggered exit
                if exit_triggered:
                    logger.warning(f"🚨 SELL TRIGGERED for {symbol}: {exit_reason}")
                    calc_ret = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 else 0.0
                    final_status = "WIN" if calc_ret >= 0 else "LOSS"
                    
                    if is_test_mode:
                        logger.info(f"🧪 [TEST MODE] Would have closed {symbol} with status={final_status} due to {exit_reason}")
                        close_success = False
                    else:
                        try:
                            update_alert_outcome(
                                alert_id=alert_id,
                                status=final_status,
                                exit_price=current_price,
                                pnl_pct=calc_ret,
                                pnl_rs=0.0,  # We don't track position size natively in alerts table without wealth engine
                                exit_signal=exit_reason
                            )
                            close_success = True
                            logger.info(f"💰 MULTIBAGGER EXITED ({final_status}): {symbol} at {current_price} (P&L: {calc_ret:.2f}%)")
                        except Exception as e:
                            logger.error(f"❌ Failed to close MULTIBAGGER alert for {symbol}: {e}")
                            close_success = False
                            
                    if close_success:
                        # Queue Telegram notification
                        sell_msg = (
                            f"🚨 <b>MULTIBAGGER SELL ALERT | {symbol}</b>\n"
                            f"----------------------------------------\n"
                            f"• Entry: ₹{entry_price:.1f}\n"
                            f"• Exit: ₹{current_price:.1f}\n"
                            f"• Return: {calc_ret:.1f}%\n"
                            f"• Reason: <i>{exit_reason}</i>\n"
                        )
                        queue_telegram_message(sell_msg, symbol=symbol)
            except Exception as e:
                logger.error(f"❌ Unhandled exception in exit monitor for {pos.get('symbol', 'UNKNOWN')}: {e}", exc_info=True)
                    
    except Exception as e:
        logger.exception(f"❌ Failed to complete exit monitoring")

def run_standalone_exit_monitor(is_test_mode: bool = False, run_ctx=None):
    """Entry point for the 5-minute scheduler to check exits only."""
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor
        
        # 1. Fetch active/reviewed MULTIBAGGER positions from alerts table
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # [VERSION: MULTIBAGGER_EXIT_FUND_FIX_v1.1] Include both OPEN and SELL_REVIEW so reviewed positions remain monitored
                cur.execute("""
                    SELECT id, symbol, entry_price as alert_price, alert_date
                    FROM alerts 
                    WHERE scanner = 'MULTIBAGGER' AND status IN ('OPEN', 'SELL_REVIEW') AND is_rejected = FALSE;
                """)
                open_positions = cur.fetchall()
                
        if not open_positions:
            return
            
        # 2. Fetch latest prices for just these symbols
        symbols = [p['symbol'] for p in open_positions]
        if not symbols:
            return
            
        if run_ctx:
            run_ctx.set_total_stocks(len(symbols))
            run_ctx.record_fresh_data(len(symbols))
            
        price_data_map_raw = batch_download_market_data(symbols)
        
        price_data_map = {}
        for sym, stock_data in price_data_map_raw.items():
            if stock_data:
                # [FIX #2] Include closes_below_sma200_count in ExitPriceData construction
                price_data_map[sym] = ExitPriceData(
                    symbol=sym,
                    price=stock_data.price,
                    sma_50=stock_data.sma_50,
                    sma_200=stock_data.sma_200,
                    high_20d=stock_data.high_20d,
                    close_yesterday=stock_data.close_yesterday,
                    sma_200_yesterday=stock_data.sma_200_yesterday,
                    atr_14=stock_data.atr_14,
                    ema_20=stock_data.ema_20,
                    closes_below_sma200_count=stock_data.closes_below_sma200_count,
                    last_trade_date=getattr(stock_data, 'last_trade_date', '') or ''
                )
                
        # 3. Use cache for fundamentals
        from multibagger import load_cache as load_mb_cache
        cache = load_mb_cache()
        
        # 4. Run the core exit logic
        run_exit_monitor(price_data_map, cache, is_test_mode)
        
    except Exception as e:
        logger.exception(f"Failed to run standalone exit monitor")
        raise e

from lock_utils import ProcessLock
_scan_lock = ProcessLock("multibagger")
_global_lock = ProcessLock("global_scanner_lock")

def start(debug_limit: int = None, is_test_mode: bool = False, session=None, run_ctx=None, trigger_type="SCHEDULED", scheduler_name="CRON"):
    from database import is_scanner_stopped, upsert_scanner_health
    from lock_utils import print_scanner_start_banner, print_scanner_end_banner
    import time
    if is_scanner_stopped("MULTIBAGGER"):
        logger.info("🛑 Multibagger Scanner is STOPPED by Admin. Skipping execution.")
        return {}

    if _scan_lock.locked():
        logger.warning("🛑 [DUPLICATE GUARD] MULTIBAGGER Scanner is ALREADY actively running in thread lock. Skipping duplicate trigger.")
        return {}

    created_ctx = False
    if run_ctx is None:
        try:
            from database import start_scanner_execution_run
            run_ctx = start_scanner_execution_run(scanner_name="MULTIBAGGER", trigger_type=trigger_type, scheduler_name=scheduler_name)
            created_ctx = True
        except Exception as exc:
            pass

    queued_at = None
    if not _global_lock.acquire(blocking=False, owner_scanner="MULTIBAGGER", operation="FULL_SCAN"):
        queued_at = time.monotonic()
        logger.info("⏳ [MULTIBAGGER] Global scanner lock busy — marking QUEUED and waiting in queue...")
        if created_ctx and run_ctx:
            from database import update_scanner_run_lifecycle
            update_scanner_run_lifecycle(run_ctx.run_id, "QUEUED")
        upsert_scanner_health("MULTIBAGGER", "QUEUED", error_msg="Waiting in queue for active scanner to release lock...")
        if not _global_lock.acquire(blocking=True, owner_scanner="MULTIBAGGER", operation="FULL_SCAN"):
            raise RuntimeError("Failed to acquire global scanner lock.")
        if created_ctx and run_ctx:
            from database import update_scanner_run_lifecycle
            update_scanner_run_lifecycle(run_ctx.run_id, "RUNNING")
        logger.info(f"✅ [MULTIBAGGER] Global lock acquired after {round(time.monotonic()-queued_at,1)}s wait. Starting scan...")

    upsert_scanner_health("MULTIBAGGER", "RUNNING", error_msg="MULTIBAGGER scan in progress...")

    if not _scan_lock.acquire(blocking=False):
        _global_lock.release()
        logger.warning("🛑 MULTIBAGGER Scanner is ALREADY actively running. Skipping duplicate execution.")
        if created_ctx and run_ctx:
            from database import complete_scanner_execution_run
            complete_scanner_execution_run(run_ctx, status_override="SKIPPED_DUPLICATE", stop_reason="Scanner already actively running")
        return {}

    _scan_start = print_scanner_start_banner("multibagger", queued_at=queued_at)

    try:
        return run_scanner(debug_limit, is_test_mode, session, run_ctx=run_ctx)
    except Exception as e:
        if created_ctx:
            try:
                from database import complete_scanner_execution_run
                complete_scanner_execution_run(run_ctx, exception=e)
                created_ctx = False
            except Exception as exc:
                logger.warning(f"⚠️ [MULTIBAGGER] Could not mark run complete on exception: {exc}")
        raise
    finally:
        print_scanner_end_banner("multibagger", _scan_start)
        try:
            from database import get_scanner_health, upsert_scanner_health
            h = get_scanner_health("MULTIBAGGER")
            if h and (h.get("status", "").startswith("QUEUED") or h.get("status") == "RUNNING"):
                upsert_scanner_health("MULTIBAGGER", status="OK", error_msg=None)
        except Exception:
            pass
        _scan_lock.release()
        _global_lock.release()
        if created_ctx:
            try:
                from database import complete_scanner_execution_run
                complete_scanner_execution_run(run_ctx)
            except Exception as exc:
                logger.warning(f"⚠️ [MULTIBAGGER] Could not mark run complete in finally: {exc}")

def _start_wrapper(debug_limit: int = None, is_test_mode: bool = False, session=None, run_ctx=None):
    """Main scanning wrapper."""
    import time
    start_time = time.time()
    from perf_utils import ScannerStageTracker
    stage_tracker = ScannerStageTracker("MULTIBAGGER_SCANNER")
    stage_tracker.start_stage(1, "Universe & Market Data Fetch", "Loading constituents and batch downloading prices")
    duration_sec = 0.0
    alerts_count = 0
    results = []
    fundamentals_list = []
    
    logger.info("🚀 Multibagger Scanner execution started...")
    init_db()
    
    # Load fundamentals cache
    cache = load_cache()
    
    # 1. Fetch constituents
    from constituent_service import fetch_constituents
    symbols = fetch_constituents()
    if not symbols:
        logger.error("❌ Failed to fetch any constituent stocks. Aborting scan.")
        raise RuntimeError("Failed to fetch NSE constituent stocks. NSE API might be blocking the IP or rate-limiting.")

    stage_tracker.total_symbols = len(symbols)

    # [VERSION: SCANNER_DIAG_LOG_v1.0] Watchlist fingerprint for cross-run comparison
    import hashlib
    _wl_stocks = sorted(symbols)
    _wl_hash = hashlib.md5("|".join(_wl_stocks).encode()).hexdigest()[:12]
    logger.info(f"📋 [MULTIBAGGER] Watchlist fingerprint: {len(symbols)} stocks | hash={_wl_hash}")
        
    if debug_limit:
        logger.info(f"🧪 [DEBUG MODE] Limiting scan universe to {debug_limit} symbols.")
        symbols = symbols[:debug_limit]
        
    # 2. Phase 1: Batch Download Price & Volume Metrics (using auto_adjust=False)
    import time
    _batch_start_t = time.perf_counter()
    symbols = list(set(symbols))
    price_data_map = batch_download_market_data(symbols, session=session)
    _fetch_dur = time.perf_counter() - _batch_start_t
    if not price_data_map:
        logger.error("❌ Failed to download batch price data. Aborting scan.")
        raise RuntimeError("Failed to download batch price data from YFinance/Fyers. Market data provider down.")

    if run_ctx:
        calc_stale = sum(1 for p in price_data_map.values() if _is_stale_trade_date(getattr(p, 'last_trade_date', '')))
        run_ctx.set_total_stocks(len(symbols))
        run_ctx.fresh_count = len(price_data_map) - calc_stale
        run_ctx.stale_count = calc_stale
        run_ctx.incomplete_count = max(0, len(symbols) - len(price_data_map))

    # [OPTIMIZATION: SINGLE_BUNDLE_UPLOAD_v1.0] Force a single DB history bundle upload
    # after all 750 symbols finish downloading, replacing 15 redundant sub-batch uploads.
    try:
        from database import upload_history_bundle_to_db
        import threading
        def upload_mb_bundle_job():
            t_name = threading.current_thread().name
            logger.info(f"🚀 [BACKGROUND WORKER START] Worker='{t_name}' | InitiatedBy='MultibaggerScanner' | Action='Uploading 1d history bundle to DB'")
            _t_start = time.perf_counter()
            ok = upload_history_bundle_to_db("1d", force=True)
            dur_s = time.perf_counter() - _t_start
            if ok:
                logger.info(f"✅ [BACKGROUND WORKER COMPLETE] Worker='{t_name}' | Action='Uploaded 1d history bundle to DB' | Duration={dur_s:.2f}s")
            else:
                logger.error(f"❌ [BACKGROUND WORKER FAILED] Worker='{t_name}' | Action='Failed uploading 1d history bundle to DB'")

        from database import submit_background_upload
        submit_background_upload(upload_mb_bundle_job)
    except Exception as _up_err:
        logger.error(f"❌ [MULTIBAGGER] Post-multibagger bundle upload submission failed: {_up_err}", exc_info=True)
        
    # Apply cheap filters to build shortlist:
    # Exclude penny stocks (< ₹10) and illiquid stocks (turnover_20d < ₹10 Lakhs)
    shortlist_candidates = []
    
    # Always include currently open or reviewed positions in the shortlist so their fundamentals are fetched concurrently
    open_symbols = set()
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # [VERSION: MULTIBAGGER_OPEN_SYMBOLS_FIX_v1.1] Query both OPEN and SELL_REVIEW
                cur.execute("SELECT symbol FROM alerts WHERE scanner = 'MULTIBAGGER' AND status IN ('OPEN', 'SELL_REVIEW') AND is_rejected = FALSE")
                open_symbols = {row[0] for row in cur.fetchall()}
    except Exception as e:
        logger.error(f"Failed to fetch open/reviewed positions for shortlist injection: {e}")
        
    for sym, price_data in price_data_map.items():
        if sym in open_symbols:
            shortlist_candidates.append(price_data)
            continue
            
        if price_data.price < 10.0:
            continue
        if price_data.turnover_20d < 1000000.0: # ₹10 Lakhs
            continue
        shortlist_candidates.append(price_data)
        
    # Sort by turnover descending (no arbitrary cap — all liquid stocks get evaluated)
    shortlist = sorted(shortlist_candidates, key=lambda x: x.turnover_20d, reverse=True)
    stage_tracker.end_stage(f"Shortlisted {len(shortlist)} liquid stocks")
    logger.info(f"📋 Shortlisted {len(shortlist)}/{len(price_data_map)} liquid stocks for fundamental screening.")
    
    # 3. Phase 2: Fetch Fundamentals
    stage_tracker.start_stage(2, "Fundamentals DB Cache Validation & Fetch", f"Target: {len(shortlist)} stocks")
    fundamentals_list = []

    def _is_fully_hydrated_v5(payload: dict) -> bool:
        if not payload:
            return False
        # The cache entry must have these keys populated to be considered a fully hydrated V5 payload
        required_keys = ['total_equity', 'market_cap', 'data_freshness', 'revenue_cagr_3y']
        for k in required_keys:
            if k not in payload:
                return False
        return True

    # Check if any shortlisted stocks are missing or incomplete from cache
    missing_shortlist_syms = [p.symbol for p in shortlist if not _is_fully_hydrated_v5(get_cached_fundamentals(p.symbol, cache))]
    if missing_shortlist_syms:
        logger.info(f"⚡ [MULTIBAGGER BULK ENRICHMENT] {len(missing_shortlist_syms)} stocks missing or incomplete from cache. Let them fall through to full YFinance hydration...")
    
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {}
        cached_count = 0
        
        # Build set of all symbols that need to be evaluated (shortlist + open positions)
        all_syms_to_check = set([p.symbol for p in shortlist])
        try:
            from database import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT symbol FROM alerts WHERE current_status IN ('OPEN', 'SELL_REVIEW') AND scanner_type = 'Multibagger Scanner'")
                    for row in cur.fetchall():
                        all_syms_to_check.add(row[0])
        except Exception as e:
            logger.error(f"Failed to fetch open positions for pre-hydration: {e}")
            
        for sym in all_syms_to_check:
            cached = get_cached_fundamentals(sym, cache)
            if cached and _is_fully_hydrated_v5(cached):
                # We only append to fundamentals_list if it's in the shortlist (for the Buy scanner)
                if any(p.symbol == sym for p in shortlist):
                    cached_count += 1
                    fundamentals_list.append(cached)
            else:
                futures[executor.submit(fetch_ticker_fundamentals, sym)] = sym
                
        if cached_count > 0:
            logger.info(f"💾 Loaded fundamentals for {cached_count}/{len(shortlist)} shortlist stocks directly from DB cache.")
            
        fetch_total = len(futures)
        if fetch_total > 0:
            logger.info(f"📥 Fetching fresh fundamentals for the remaining {fetch_total} stocks (shortlist + open positions) via Yahoo Finance...")
        else:
            logger.info("✅ All fundamentals were loaded from cache. No fetching required!")
                
        fetched_count = 0
        try:
            import concurrent.futures
            for future in as_completed(futures, timeout=7200):
                sym = futures[future]
                try:
                    fund = future.result()
                    if fund:
                        fundamentals_list.append(fund)
                        # Update local cache memory
                        cache[sym] = fund
                        cache[sym]["fetched_at"] = datetime.now(IST).isoformat()
                        fetched_count += 1
                        
                        if fetched_count % 10 == 0 or fetched_count == fetch_total:
                            logger.info(f"⏳ Progress: Fetched {fetched_count}/{fetch_total} fresh fundamentals...")
                        
                        # Save in chunks to prevent data loss if restarted
                        if fetched_count % 25 == 0:
                            logger.info(f"💾 Intermediary chunk save: saving {fetched_count} newly fetched fundamentals to DB...")
                            save_fundamentals_cache(cache, sync_to_db=True)
                    else:
                        logger.warning(f"⚠️ Failed to fetch fundamentals for {sym} (No data returned)")
                        # [VERSION: CACHE_FAILURE_v1.0] Cache failures so we don't retry fetching permanently missing stocks every single day
                        fail_fund = {"symbol": sym, "failed": True, "fetched_at": datetime.now(IST).isoformat()}
                        cache[sym] = fail_fund
                        fundamentals_list.append(fail_fund)
                        
                        # Still count as fetched so the chunk saves properly
                        fetched_count += 1
                        if fetched_count % 25 == 0:
                            save_fundamentals_cache(cache, sync_to_db=True)
                except Exception as e:
                    logger.error(f"❌ Error fetching fundamentals for {sym}: {e}")
                    # Cache the exception failure as well
                    fail_fund = {"symbol": sym, "failed": True, "fetched_at": datetime.now(IST).isoformat()}
                    cache[sym] = fail_fund
                    fundamentals_list.append(fail_fund)
                    fetched_count += 1
                    if fetched_count % 25 == 0:
                        save_fundamentals_cache(cache, sync_to_db=True)
        except concurrent.futures.TimeoutError:
            logger.error("❌ Timeout fetching fundamentals in multibagger. Aborting remaining fetches to prevent deadlock.")
                
        if fetched_count > 0:
            logger.info(f"💾 Final save: saving remaining newly fetched fundamentals to DB...")
            save_fundamentals_cache(cache, sync_to_db=True)
        
        # Now that cache is fully populated concurrently, run exit monitor on open positions
        run_exit_monitor(price_data_map, cache, is_test_mode)
                
    # Save updated cache to JSON file
    save_fundamentals_cache(cache)
    
    # Enforce minimum 70% data integrity before proceeding
    total_expected = len(shortlist)
    total_fetched = len(fundamentals_list)
    
    if total_expected > 0:
        fetch_ratio = total_fetched / total_expected
        logger.info(f"📊 Data Integrity: {total_fetched}/{total_expected} ({fetch_ratio:.1%}) fundamentals loaded.")
        if fetch_ratio < 0.70:
            error_msg = f"Incomplete data error: Only {total_fetched}/{total_expected} ({fetch_ratio:.1%}) stocks fetched."
            logger.error(f"⚠️ {error_msg}")
            # [FIX ISSUE-8] Abort scan when coverage is too low. Peer calculations and Top-N
            # rankings become unreliable with insufficient data. Existing positions continue
            # monitoring via the separate exit monitor path.
            if not is_test_mode:
                try:
                    upsert_scanner_health(
                        scanner_name="MULTIBAGGER",
                        status="DEGRADED",
                        error_msg=error_msg,
                        today_alerts=0,
                        processed_count=0,
                        total_count=total_expected
                    )
                    from push_service import send_push_to_all
                    send_push_to_all("⚠️ MULTIBAGGER Scanner DEGRADED — No New Alerts", error_msg, bypass_throttle=True)
                except Exception:
                    pass
            logger.error(f"🚫 Aborting scan: coverage {fetch_ratio:.1%} below 70% threshold.")
            return {"total_count": total_expected, "processed_count": 0, "today_alerts": 0}
    
    # Check Market Regime (Explicitly fetch Nifty)
    # Default to BEAR (conservative fail-direction for quality-over-quantity)
    market_regime = "BEAR"
    try:
        from macro_utils import _get_daily_nifty
        nifty_df = _get_daily_nifty()
        if nifty_df is not None and not nifty_df.empty and len(nifty_df) >= 200:
            close_col = nifty_df["Close"]
            if isinstance(close_col, pd.DataFrame):
                close_col = close_col.iloc[:, 0]
            nifty_close = float(close_col.iloc[-1])
            nifty_sma200 = float(close_col.tail(200).mean())
            if nifty_close > nifty_sma200:
                market_regime = "BULL"
            else:
                market_regime = "BEAR"
        else:
            logger.warning("Nifty data insufficient (<200 days). Defaulting to BEAR (conservative).")

    except Exception as e:
        logger.warning(f"Could not determine market regime, defaulting to BEAR (conservative): {e}")
        
    logger.info(f"📊 Detected Market Regime: {market_regime}")
    
    # 4. Phase 3: Peer-aware scoring & buy zone assessment
    stage_tracker.end_stage(f"Loaded {len(fundamentals_list)} fundamentals ({cached_count if 'cached_count' in locals() else 0} from DB cache)")
    stage_tracker.start_stage(3, "V5 Quant & Fundamental Evaluation Pipeline", f"Target: {len(fundamentals_list)} stocks")
    from valuation_utils import compute_peer_medians

    symbols_to_val = [f.get("symbol") for f in fundamentals_list]
    peer_medians = compute_peer_medians(symbols_to_val)
            
    results = []
    alert_candidates = []
    categorized_stocks = {}
    

    
    # Init Rejection Log count
    unverified_pledge_count = 0
    
    _eval_start_t = time.perf_counter()
    _eval_lock = threading.Lock()

    def _eval_item(f):
        nonlocal unverified_pledge_count
        sym = f.get("symbol")
        price_data = price_data_map.get(sym)
        if not price_data:
            return
            
        # 1. Pass the raw dictionary directly to the V5 Pipeline
        raw_fundamentals = f.copy()
        
        # Inject computed technical data for V5 Market Structure Engine (Momentum)
        if price_data.high_52w > 0:
            raw_fundamentals["pct_from_52w_high"] = (price_data.price - price_data.high_52w) / price_data.high_52w
        else:
            raw_fundamentals["pct_from_52w_high"] = 0.0
            
        if getattr(price_data, 'volume_sma20', 0) > 0:
            raw_fundamentals["relative_volume_10d"] = price_data.latest_volume / price_data.volume_sma20
        else:
            # [FIX ISSUE-6] Unknown volume set to None, not favorable 1.0.
            # 1.0 pretends normal volume and inflates the candidate's score.
            raw_fundamentals["relative_volume_10d"] = None
            
        # Calculate proxy RS Rating from 6-month momentum
        mom = safe_float(getattr(price_data, 'mom_6m', 0.0))
        if mom > 0.40: rs = 95.0
        elif mom > 0.20: rs = 85.0
        elif mom > 0.10: rs = 75.0
        elif mom > 0.05: rs = 65.0
        elif mom > 0.0: rs = 55.0
        elif mom > -0.10: rs = 45.0
        elif mom > -0.20: rs = 35.0
        else: rs = 25.0
        raw_fundamentals["rs_rating"] = rs
        
        # [FIX] Issue #2: Use actual forensic_flags instead of hardcoded False
        # forensic_flags >= 2 means auditor/accounting red flags detected
        forensic_count = raw_fundamentals.get("forensic_flags", 0)
        raw_fundamentals["auditor_flags"] = (forensic_count >= 2)
        
        # [VERSION: PLEDGE_EXTRACT_FIX_v1.0] Populate promoter_pledge_pct from pledge cache DB
        # Set to None/null if missing or unverified instead of defaulting to 0.0
        if "promoter_pledge_pct" not in raw_fundamentals or raw_fundamentals.get("promoter_pledge_pct") in (None, 0.0):
            try:
                from pledge_scraper import fetch_promoter_pledge
                pledge_val = fetch_promoter_pledge(sym)
                if pledge_val is not None:
                    # Gate engine expects a ratio (0.0-1.0), not a percentage
                    raw_fundamentals["promoter_pledge_pct"] = pledge_val / 100.0
                else:
                    unverified_pledge_count += 1
                    raw_fundamentals["promoter_pledge_pct"] = None
                    logger.debug(f"⚠️ {sym}: Pledge data unavailable — setting to None")
            except Exception:
                unverified_pledge_count += 1
                raw_fundamentals["promoter_pledge_pct"] = None
        
        technicals = {
            "price": price_data.price,
            "sma_50": price_data.sma_50,
            "sma_200": price_data.sma_200,
            "ema_20": price_data.ema_20,
            "atr": price_data.atr_14,
        }
        
        # 2. Early Ambiguity & Quality Gates
        if price_data.sma_200 <= 0 or price_data.ema_20 <= 0 or price_data.sma_50 <= 0 or price_data.price <= 0:
            logger.debug(f"REJECTION: {sym} (Phase: PRE_GATE, Reason: Ambiguous Technicals)")
            append_rejection(results, sym, "TECHNICAL_UNAVAILABLE", "Ambiguous Technicals", price=price_data.price, price_data=price_data, raw_fundamentals=raw_fundamentals)
            return

        # [FIX-2] Reject stale entries — if the last trade was >= 3 business days ago
        if _is_stale_trade_date(getattr(price_data, 'last_trade_date', '')):
            logger.debug(f"REJECTION: {sym} (Phase: PRE_GATE, Reason: Stale price data — last trade: {getattr(price_data, 'last_trade_date', 'unknown')})")
            append_rejection(results, sym, "STALE_DATA", f"Stale trade date: {getattr(price_data, 'last_trade_date', 'unknown')}", price=price_data.price, price_data=price_data, raw_fundamentals=raw_fundamentals)
            return
            
        if raw_fundamentals.get("data_freshness") == "FALLBACK":
            logger.debug(f"REJECTION: {sym} (Phase: PRE_GATE, Reason: Fallback Fundamentals)")
            append_rejection(results, sym, "FALLBACK_DATA", "Fallback Fundamentals", price=price_data.price, price_data=price_data, raw_fundamentals=raw_fundamentals)
            return

        # [FIX-6] Reject before scoring when volume is unavailable. None/0 volume
        # means the V5 pipeline will impute a neutral score, artificially inflating the result.
        if price_data.latest_volume <= 0 or price_data.volume_sma20 <= 0:
            logger.debug(f"REJECTION: {sym} (Phase: PRE_GATE, Reason: Volume data unavailable)")
            append_rejection(results, sym, "VOLUME_UNAVAILABLE", "Volume data unavailable", price=price_data.price, price_data=price_data, raw_fundamentals=raw_fundamentals)
            return
            
        ok, reason = passes_multibagger_quality_gate(raw_fundamentals)
        if not ok:
            logger.debug(f"REJECTION: {sym} (Phase: QUALITY_GATE, Reason: {reason})")
            status_code = "UNSUPPORTED_FINANCIAL" if reason.startswith("UNSUPPORTED") else "QUALITY_REJECTED"
            append_rejection(results, sym, status_code, reason, price=price_data.price, price_data=price_data, raw_fundamentals=raw_fundamentals)
            return

        # 3. Run the V5 Pipeline
        # [VERSION: MULTIBAGGER_PIPELINE_GUARD_v1.1] Guard per-symbol pipeline execution with exception logging
        try:
            pipeline_result = run_pipeline_for_symbol(sym, raw_fundamentals, technicals)
        except Exception:
            logger.exception("%s: V5 pipeline failed", sym)
            append_rejection(results, sym, "PIPELINE_FAILED", "V5 pipeline execution error", price=price_data.price, price_data=price_data, raw_fundamentals=raw_fundamentals)
            return
        
        # Log rejection if invalidated by V5 gates
        if pipeline_result.is_invalidated:
            logger.debug(f"REJECTION: {sym} (Phase: V5_GATE, Reason: {pipeline_result.invalidation_reason})")
            append_rejection(results, sym, "QUALITY_REJECTED", f"V5 Gate: {pipeline_result.invalidation_reason}", price=price_data.price, price_data=price_data, raw_fundamentals=raw_fundamentals)
            return
                
        # Extract scores from the V5 pipeline
        cqs = pipeline_result.quality.score
        pas = pipeline_result.valuation.score
        trend = pipeline_result.market_structure.score
        total = pipeline_result.composite_score
        
        # Apply institutional, promoter, and super-investor bonuses
        try:
            from block_deal_detector import compute_inst_bonus
            inst_bonus = float(compute_inst_bonus(sym, int(total)))
        except Exception as e:
            logger.warning(f"Error checking institutional footprints in Multibagger: {e}")
            inst_bonus = 0.0

        # [FIX MUL-6] Classify conviction on the pre-bonus composite, then apply
        # inst_bonus only to the final total used for ranking. Without this,
        # inst_bonus could push a 63 → 65 and flip "Watchlist" → "High Quality",
        # firing an alert on a name that failed on its own merits.
        pre_bonus_total = total
        total = min(100.0, total + inst_bonus)
        
        buy_low = pipeline_result.buy_zone.buy_zone_low
        buy_high = pipeline_result.buy_zone.buy_zone_high
        
        f_score_val = raw_fundamentals.get("piotroski_f_score", raw_fundamentals.get("f_score"))
        # [FIX MUL-7] Production pipeline stores Piotroski under "score" / "piotroski_score"
        # (matching evaluate_multibagger_symbol line 68). The old keys "piotroski_f_score"
        # and "f_score" are never written, so f_score_val was always None — silently
        # bypassing the >= 7 Piotroski requirement for Prime tier.
        if f_score_val is None:
            _raw_fs = raw_fundamentals.get("score", raw_fundamentals.get("piotroski_score"))
            f_score_val = int(_raw_fs) if (_raw_fs is not None and not pd.isna(_raw_fs)) else None
        # [FIX-7] Apply BEAR regime adjustment BEFORE classification so the tier
        # reflects the regime-adjusted score. A stock scoring 72 in BEAR should not
        # be classified as Prime (requires >= 70) after a 5-point penalty.
        regime_adjusted_score = (pre_bonus_total - 5.0) if market_regime == "BEAR" else pre_bonus_total
        tier, composite = classify_conviction(cqs, pas, trend, regime_adjusted_score, f_score=f_score_val, pledge_ratio=_pledge_ratio(raw_fundamentals.get("promoter_pledge_pct")))

        if market_regime == "BEAR":
            # In BEAR regime, apply defensive score penalty (-5.0) but allow High Quality candidates with strong CQS (>= 65) to alert
            total = total - 5.0
            if tier == "💎 High Quality" and cqs < 65.0:
                tier = "🟡 Watchlist"
                alert_triggered = False
        
        if tier not in ["🚀 Prime Multibagger", "💎 High Quality"]:
            status = "WAITING_BUY_ZONE"
            notes = f"Conviction: {tier} | CQS: {cqs:.1f}"
            alert_triggered = False
            logger.debug(f"ℹ️ [MULTIBAGGER] {sym} not triggered — Conviction Tier '{tier}' below Prime/High Quality threshold")
        else:
            if not pipeline_result.buy_zone.in_buy_zone:
                status = "WAITING_BUY_ZONE"
                notes = f"Conviction: {tier} | Waiting for Pullback"
                alert_triggered = False
                logger.info(f"🚫 [MULTIBAGGER] {sym} REJECTED from Alert — Price ₹{price_data.price:.2f} not in Buy Zone [₹{buy_low:.2f} - ₹{buy_high:.2f}]")
            elif not entry_confirmed(price_data):
                status = "WAITING_BUY_ZONE"
                notes = f"Conviction: {tier} | In Zone, Awaiting Technical Stabilization"
                alert_triggered = False
                logger.info(f"🚫 [MULTIBAGGER] {sym} REJECTED from Alert — In Buy Zone but entry_confirmed technical stabilization check failed")
            else:
                status = "ALERT_TRIGGERED"
                reclaim_ema = price_data.price > price_data.ema_20
                if reclaim_ema:
                    notes = f"Conviction: {tier} | 🟢 BUY CONFIRMED (EMA Reclaimed)"
                else:
                    notes = f"Conviction: {tier} | 🟢 BUY CONFIRMED (Deep Value Zone)"
                
                fv = safe_float(getattr(pipeline_result.valuation, 'fair_value', 0.0))
                mos = safe_float(getattr(pipeline_result.valuation, 'margin_of_safety', 0.0))
                if fv > 0:
                    notes += f" | FV: {fv:.0f} (MoS: {mos:.0f}%)"
                
                alert_triggered = True
                
        bucket = tier
            
        if alert_triggered:
            skip_alert = False
            if sym in open_symbols:
                logger.info(f"🚫 [MULTIBAGGER] {sym} REJECTED after picking — Reason: ALREADY_OPEN_POSITION in database")
                skip_alert = True
                status = "WAITING_BUY_ZONE" # Already held, so don't fire an alert again
                
            if not skip_alert:
                logger.info(f"📍 PICKED [MULTIBAGGER: IN BETWEEN]: {sym} @ ₹{price_data.price:.2f} (Tier: {tier}, Score: {total:.1f}, CQS: {cqs:.1f})")
                tier_val = 2 if "Prime" in tier else 1
                with _eval_lock:
                    alert_candidates.append({
                        "symbol": sym,
                        "price": price_data.price,
                        "tier": tier,
                        "tier_val": tier_val,
                        "total_score": total,
                        "cqs": cqs,
                        "trend_score": trend,
                        "pas": pas,
                        "notes": notes,
                        "pipeline_result": pipeline_result,
                        "raw_fundamentals": raw_fundamentals,
                        "_price_data": price_data  # [FIX MUL-24] Store for entry_confirmed recheck at live price
                    })

            if status != "INVALIDATED":
                label = bucket
                if skip_alert:
                    label = f"🛡️ {label} (Currently Held)"
                
                with _eval_lock:
                    if label not in categorized_stocks:
                        categorized_stocks[label] = []
                    categorized_stocks[label].append({
                        'symbol': sym,
                        'price': price_data.price,
                        'cqs': cqs,
                        'pas': pas,
                        'total': total,
                        'status': status
                    })

        # Assemble the display record
        bz_low = pipeline_result.buy_zone.buy_zone_low if pipeline_result.buy_zone else 0.0
        bz_high = pipeline_result.buy_zone.buy_zone_high if pipeline_result.buy_zone else 0.0
        
        with _eval_lock:
            results.append(ScreenerResult(
                symbol=sym,
                price=round(price_data.price, 2),
                cqs=round(cqs, 1),
                pas=round(pas, 1),
                trend_score=round(trend, 1),
                total_score=round(total, 1),
                buy_zone_low=round(bz_low, 2),
                buy_zone_high=round(bz_high, 2),
                bucket=bucket,
                status=status,
                notes=notes,
                change_pct=0.0
            ))

    with ThreadPoolExecutor(max_workers=8, thread_name_prefix="MB_Eval") as eval_exec:
        futures = [eval_exec.submit(_eval_item, f) for f in fundamentals_list]
        for fut in as_completed(futures):
            fut.result()

    # Process Top-N alerts
    if alert_candidates:
        # Sort by tier, total_score desc, cqs desc
        alert_candidates.sort(key=lambda x: (x.get("tier_val", 0), x["total_score"], x["cqs"]), reverse=True)
        
        # [FIX-1] Save full list BEFORE Top-N slicing so rejected_map can find
        # candidates that were cut by the limit. Without this, suppressed candidates
        # disappear and get misclassified as DUPLICATE during reconciliation.
        all_alert_candidates = list(alert_candidates)

        from config import SCANNER_MAX_ALERTS
        max_alerts = SCANNER_MAX_ALERTS.get("MULTIBAGGER", 10)
        _eval_dur = time.perf_counter() - _eval_start_t
        logger.info(
            f"⏱️ [MULTIBAGGER] Batch 1/1 Timing | "
            f"Fetch {len(symbols)} symbols: {_fetch_dur:.2f}s | "
            f"Evaluation: {_eval_dur:.2f}s"
        )
        if len(alert_candidates) > max_alerts:
            logger.info(f"Limiting MULTIBAGGER alerts from {len(alert_candidates)} to {max_alerts}")
            for cand in alert_candidates[max_alerts:]:
                cand["rejection_status"] = "SUPPRESSED_TOP_N"
                cand["rejection_reason"] = f"Exceeded MAX_ALERTS_PER_SCAN limit ({max_alerts})"
                logger.info(f"🚫 {cand['symbol']} alert SUPPRESSED_TOP_N: Exceeded MAX_ALERTS_PER_SCAN limit (Score: {cand['total_score']:.1f})")
            alert_candidates = alert_candidates[:max_alerts]

        top_n = alert_candidates
        logger.info(f"🏆 Top {len(alert_candidates)} valid candidates selected.")
        
        # Batch fetch live prices
        try:
            from live_prices import get_live_prices
            live_prices_dict = get_live_prices([c["symbol"] for c in top_n])
        except Exception as e:
            logger.warning(f"Failed to batch fetch live prices: {e}")
            live_prices_dict = {}
        
        for cand in top_n:
            try:
                sym = cand["symbol"]
                price = cand["price"]
                
                # [VERSION: MULTIBAGGER_LIVE_PRICE_GUARD_v1.1] Apply batched live price with finite & positivity check
                live_p = live_prices_dict.get(sym)
                try:
                    parsed_p = float(live_p) if live_p is not None else float("nan")
                except (TypeError, ValueError):
                    parsed_p = float("nan")

                if not math.isfinite(parsed_p) or parsed_p <= 0:
                    logger.info(f"🚫 {sym} live price unavailable or invalid ({live_p}) — skipping alert")
                    cand["rejection_status"] = "LIVE_PRICE_UNAVAILABLE"
                    cand["rejection_reason"] = f"Could not verify current market price (got {live_p})"
                    continue

                price = float(parsed_p)
                cand["price"] = price  # [FIX ISSUE-7] Update candidate price to validated live price

                # [FIX MUL-14] Revalidate live price against buy zone.
                pipeline_res = cand["pipeline_result"]
                bz_low = pipeline_res.buy_zone.buy_zone_low if pipeline_res and pipeline_res.buy_zone else 0.0
                bz_high = pipeline_res.buy_zone.buy_zone_high if pipeline_res and pipeline_res.buy_zone else 0.0
                if bz_high > 0 and (price < bz_low or price > bz_high):
                    logger.info(f"🚫 {sym} live price ₹{price:.2f} outside buy zone [₹{bz_low:.2f} - ₹{bz_high:.2f}] — skipping alert")
                    # [FIX ISSUE-10] Track distinct rejection reason
                    cand["rejection_status"] = "PRICE_MOVED"
                    cand["rejection_reason"] = f"Live price ₹{price:.2f} outside buy zone [₹{bz_low:.2f} - ₹{bz_high:.2f}]"
                    continue

                # [FIX MUL-24] Recheck entry_confirmed against live price.
                live_price_data = cand.get("_price_data")
                if live_price_data is not None:
                    from dataclasses import replace
                    live_pd = replace(live_price_data, price=price, today_close=price)
                    if not entry_confirmed(live_pd):
                        logger.info(f"🚫 {sym} live price ₹{price:.2f} fails entry_confirmed recheck — skipping alert")
                        # [FIX ISSUE-10] Track distinct rejection reason
                        cand["rejection_status"] = "TECHNICAL_UNCONFIRMED"
                        cand["rejection_reason"] = f"Live entry_confirmed failed at ₹{price:.2f}"
                        continue

                c_total = cand["total_score"]
                c_cqs = cand["cqs"]
                c_trend = cand["trend_score"]
                c_pas = cand["pas"]
                c_notes = cand["notes"]
                raw_fund = cand["raw_fundamentals"]
                c_tier = cand.get("tier") or (pipeline_res.classification if pipeline_res else "💎 High Quality")
                
                logger.info(f"🌟 Alert Triggered for {sym}! Price={price:.1f}. Reason: In Buy Zone")
                
                scaled_score = int(c_total)
                
                # Custom Capital Allocation based on tier
                if c_tier == "🚀 Prime Multibagger":
                    alloc = 100000.0
                elif c_tier == "💎 High Quality":
                    alloc = 50000.0
                else:
                    alloc = 25000.0
                    
                pos_shares = int(alloc / price) if price > 0 else 0
                
                inserted = False
                context_dict = {
                    "multibagger_meta": {
                        "valuation_score": c_pas,
                        "momentum_score": int(c_trend),
                        "momentum_confidence": "HIGH" if c_cqs >= 75.0 else "MEDIUM",
                        "data_quality": "LIVE",
                        "pipeline_tier": c_tier
                    }
                }
                
                # [VERSION: SCANNER_DIAG_LOG_v1.0] Log full diagnostic for every triggered trade
                _last_bar_date = "unknown"
                try:
                    _price_data = price_data_map.get(sym)
                    if _price_data and hasattr(_price_data, 'timestamp'):
                        _last_bar_date = str(_price_data.timestamp)[:10]
                except Exception:
                    pass
                logger.info(
                    f"✅ [MULTIBAGGER] PASSED ALL FILTERS: {sym} | "
                    f"cqs={c_cqs:.1f} | pas={c_pas:.1f} | total_score={scaled_score:.1f} | "
                    f"entry=₹{price:.2f} | last_bar={_last_bar_date} | category={c_tier}"
                )
                
                # We use save_alert_if_new to insert into the main alerts table!
                from zoneinfo import ZoneInfo
                ist_now = datetime.now(ZoneInfo('Asia/Kolkata'))
                
                # [VERSION: MULTIBAGGER_ALERT_INSERT_GUARD_v1.1] Wrap DB alert insertion in try...except
                try:
                    inserted, reason, _, _ = save_alert_if_new(
                        symbol=sym,
                        breakout_type="MULTIBAGGER",
                        alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                        scanner="MULTIBAGGER",
                        category=c_tier,
                        entry_price=round(price, 2),
                        stop_loss=0.0, # As requested: No SL for Multibagger
                        target_price=0.0,
                        signals="Value, Momentum, Quality",
                        score=scaled_score,
                        context=context_dict,
                        capital_allocated=alloc,
                        shares_bought=pos_shares
                    )
                except Exception as exc:
                    logger.exception(f"{sym}: alert insertion failed: {exc}")
                    inserted = False
                    reason = "Database insertion failed"

                # [FIX-8] Track insertion status AND rejection reason on the candidate
                cand["inserted"] = inserted
                if not inserted:
                    cand["insert_reason"] = str(reason)
                    if "duplicate" in str(reason).lower():
                        cand["rejection_status"] = "DUPLICATE"
                    else:
                        cand["rejection_status"] = "INSERT_FAILED"
                    cand["rejection_reason"] = str(reason)

                if inserted:
                    for _r in results:
                        if _r.symbol.upper() == sym.upper():
                            _r.alert_inserted = True
                            _r.price = round(price, 2)
                            break
                    from core.multibagger_pipeline import V5_CONFIG
                    if V5_CONFIG.get("enable_telegram_alerts", True) and not is_test_mode:
                        fv_val = safe_float(getattr(pipeline_res.valuation, 'fair_value', 0.0))
                        mos_val = safe_float(getattr(pipeline_res.valuation, 'margin_of_safety', 0.0))
                        msg = (
                            f"🚀 <b>MULTIBAGGER ALERT | {sym}</b>\n"
                            f"----------------------------------------\n"
                            f"• Price: ₹{price:.1f}\n"
                            f"• Classification: <b>{pipeline_res.classification}</b>\n"
                            f"• Composite Score: {pipeline_res.composite_score:.1f}/100\n"
                            f"• Confidence: {pipeline_res.confidence:.0f}%\n"
                            f"• Fair Value: ₹{fv_val:.1f} (MoS: {mos_val:.1f}%)\n"
                            f"• Buy Zone: ₹{pipeline_res.buy_zone.buy_zone_low:.1f} - ₹{pipeline_res.buy_zone.buy_zone_high:.1f}\n"
                            f"• Sector: {raw_fund.get('sector', 'Unknown')}\n"
                            f"\n<i>System V5 Architecture</i>"
                        )
                        queue_telegram_message(msg, symbol=sym)
            except Exception as exc:
                logger.exception(f"{cand.get('symbol', 'UNKNOWN')}: candidate processing failed: {exc}")
                cand["rejection_status"] = "PROCESSING_FAILED"
                cand["rejection_reason"] = "Candidate processing failed"
                continue

    # [FIX-1 + ISSUE-10] Reconcile statuses using all candidates (including those cut by Top-N).
    # Build rejected_map from all_alert_candidates, not the sliced alert_candidates.
    inserted_symbols = {c["symbol"].upper() for c in (alert_candidates or []) if c.get("inserted")}
    rejected_map = {c["symbol"].upper(): c for c in (all_alert_candidates if 'all_alert_candidates' in dir() else []) if c.get("rejection_status")}
    for r in results:
        sym_upper = r.symbol.upper()
        if r.status == "ALERT_TRIGGERED" and sym_upper not in inserted_symbols:
            # Check if there's a specific rejection reason from live validation or Top-N
            cand_info = rejected_map.get(sym_upper)
            if cand_info:
                r.status = cand_info["rejection_status"]
            else:
                # Generic suppression (e.g., duplicate alert within lookback window)
                r.status = "DUPLICATE"

    # [FIX ISSUE-9] Rebuild Telegram categories from final statuses AFTER all insertion
    # attempts. The original categorized_stocks was built before Top-N, live validation,
    # and DB insertion — so it contained suppressed and rejected candidates.
    categorized_stocks = {}
    for r in results:
        label = r.bucket or "Other"
        if r.symbol.upper() in open_symbols:
            label = f"🛡️ {label} (Currently Held)"
        if label not in categorized_stocks:
            categorized_stocks[label] = []
        categorized_stocks[label].append({
            'symbol': r.symbol,
            'price': r.price,
            'cqs': r.cqs,
            'pas': r.pas,
            'total': r.total_score,
            'status': r.status
        })

    stage_tracker.end_stage(f"Evaluated {len(results)} watchlist items")
    stage_tracker.start_stage(4, "Alert Persistence & Telegram Summary", f"Watchlist items: {len(results)}")

    # 5. Bulk database persistence
    save_watchlist_to_db(results)
    
    # 6. Format and queue Telegram updates — use alert_inserted results for BUY READY list
    logger.info(f"📢 Formatting Telegram messages for {len(results)} watchlist items...")
    telegram_msgs = format_telegram_message(categorized_stocks)
    for msg in telegram_msgs:
        queue_telegram_message(msg)
        
    logger.info("✅ Multibagger Scanner execution finished.")
    try:
        stage_tracker.end_stage(f"Alerts generated: {alerts_count}")
        stage_tracker.print_summary(alerts_found=alerts_count)
    except Exception:
        pass

    # [FIX MUL-16] Count actual DB inserts, not ALERT_TRIGGERED status flags.
    # before this, the count included candidates suppressed by Top-N or rejected
    # by save_alert_if_new (e.g., duplicate alert within lookback window).
    alerts_count = sum(1 for r in results if getattr(r, 'alert_inserted', False))
    duration_sec = round(time.time() - start_time, 1)
    
    if run_ctx:
        run_ctx.processed_count = len(results)
        run_ctx.alerts_generated = alerts_count

    try:
        from database import insert_notification
        stale_count = sum(1 for r in results if r.status == "STALE_DATA")
        upsert_scanner_health(
            scanner_name="MULTIBAGGER",
            status="OK",
            last_success=datetime.now(IST).isoformat(),
            today_alerts=alerts_count,
            processed_count=len(results),
            total_count=len(symbols),
            scheduled_for="19:00 IST",
            duration_seconds=duration_sec,
            provider_stats={
                "SUCCESS": len(price_data_map) if 'price_data_map' in locals() else 0,
                "NOT_FOUND": (len(symbols) - len(price_data_map)) if 'price_data_map' in locals() else 0,
                "RATE_LIMIT": 0,
                "NETWORK_ERROR": 0,
                "TIMEOUT": 0,
                "EMPTY_DATA": 0,
                "STALE": stale_count
            }
        )
        pass
    except Exception as e:
        logger.error(f"Could not update health/notification for Multibagger: {e}")
    # ── Memory Cleanup Phase ──────────────────────────────────────────────
    
    # Store counts before deleting variables
    total_count = len(symbols) if 'symbols' in locals() else 0
    processed_count = len(results) if 'results' in locals() else 0
    
    try:
        import os, psutil, gc
        process = psutil.Process(os.getpid())
        rss_before = process.memory_info().rss / 1024 / 1024
        
        # Release large data structures
        if 'price_data_map' in locals(): del price_data_map
        if 'shortlist_candidates' in locals(): del shortlist_candidates
        if 'shortlist' in locals(): del shortlist
        if 'fundamentals_list' in locals(): del fundamentals_list
        if 'futures' in locals(): del futures
        
        rss_after_del = process.memory_info().rss / 1024 / 1024
        
        # Reclaim cyclic references
        gc.collect()
        
        rss_after_gc = process.memory_info().rss / 1024 / 1024
        logger.info(f"🧹 [MEMORY] Multibagger Scan | RSS Before: {rss_before:.1f}MB | After Del: {rss_after_del:.1f}MB | After GC: {rss_after_gc:.1f}MB")
    except Exception as e:
        logger.debug(f"Memory cleanup logging failed: {e}")

    return {
        "total_count": total_count,
        "processed_count": processed_count,
        "today_alerts": alerts_count
    }


def restore_healthy_multibagger_positions():
    """No-op: Closed positions stay closed. Exit monitors only evaluate open/sell_review positions."""
    return 0
    try:
        from database import get_connection
        from psycopg2.extras import RealDictCursor

        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, symbol, status, exit_signal, entry_price, exit_price, pnl_pct
                    FROM alerts
                    WHERE scanner = 'MULTIBAGGER'
                      AND status IN ('SELL_REVIEW', 'CLOSED')
                      AND is_rejected = FALSE;
                """)
                rows = cur.fetchall()

        if not rows:
            logger.info("ℹ️ No Multibagger alerts currently in SELL_REVIEW or CLOSED to re-evaluate.")
            return 0

        restored_count = 0
        legitimate_exit_count = 0
        cache = load_cache()

        for r in rows:
            alert_id = r["id"]
            symbol = r["symbol"]
            entry_p = float(r["entry_price"]) if r.get("entry_price") else None
            exit_p = float(r["exit_price"]) if r.get("exit_price") else None

            fund = get_cached_fundamentals(symbol, cache)
            if not fund:
                fund = fetch_ticker_fundamentals(symbol)

            if not fund:
                continue

            ok, gate_reason = passes_multibagger_quality_gate(fund)
            piot_score = fund.get("score", fund.get("piotroski_f_score", 0)) or 0
            
            # Legitimate exit signals (auditor flags, severe pledge, or broken gate)
            if ok and fund.get("auditor_flags") is not True and piot_score >= 4:
                # Erroneous closure: restore to OPEN
                with get_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            UPDATE alerts
                            SET status = 'OPEN',
                                exit_signal = NULL,
                                exit_reason = NULL,
                                closed_at = NULL,
                                exit_price = NULL,
                                pnl_pct = NULL,
                                pnl_rs = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s;
                        """, (alert_id,))
                    conn.commit()
                restored_count += 1
                logger.info(f"🔄 Re-evaluated {symbol} (Alert #{alert_id}): Verified healthy -> restored to OPEN status.")
            else:
                # Legitimate exit: categorize as WIN or LOSS based on return
                pnl = r.get("pnl_pct")
                if pnl is None and entry_p and exit_p and entry_p > 0:
                    pnl = ((exit_p - entry_p) / entry_p) * 100.0
                
                final_st = "WIN" if (pnl is not None and pnl >= 0) else "LOSS"
                if r["status"] != final_st:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute("""
                                UPDATE alerts
                                SET status = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s;
                            """, (final_st, alert_id))
                        conn.commit()
                    legitimate_exit_count += 1
                    logger.info(f"📌 Re-evaluated {symbol} (Alert #{alert_id}): Verified legitimate exit -> set status to {final_st}.")

        if restored_count > 0 or legitimate_exit_count > 0:
            logger.info(f"✅ Multibagger Re-evaluation: Restored {restored_count} to OPEN | Categorized {legitimate_exit_count} legitimate exits as WIN/LOSS.")

        return restored_count

    except Exception as e:
        logger.error(f"Failed to re-evaluate multibagger positions: {e}")
        return 0
