from __future__ import annotations
import os
import time
import logging
import threading
import pandas as pd
from typing import Optional, Tuple
import json
from config import ACTIVE_ALGO_VERSION, DATA_DIR
_last_parquet_upload = 0
# Ensure tzcache writable location before importing yfinance (robust import to support different cwd)
try:
    import yf_bootstrap
except Exception:
    pass
from datetime import datetime, date
from zoneinfo import ZoneInfo
IST = ZoneInfo("Asia/Kolkata")
from enum import Enum

# [VERSION: WEALTH_SAFE_NUM_v1.0] Null-safe numeric extractor for Pandas rows.
# np.nan is truthy, so the common 'r.get(field, 0) or 0' pattern silently
# carries NaN through — causing all downstream comparisons to return False.
def _safe_num(val, default=0):
    """Convert NaN/None to default; pass valid numbers through."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

from config import ENABLE_AI_SENTIMENT_SCORE
from collections import defaultdict
import concurrent.futures
import database
from database import get_recent_concall_analysis
from perf_utils import stage_timer, flush_timing_report, reset_stage_timers, log_api_cost

# Concurrency and retry tuning
WORKER_COUNT = 3  # Hardcoded to 3 to prevent OOM kills on Railway (500MB RAM limit)
RETRY_ATTEMPTS = 3

def evaluate_wealth_symbol(symbol: str, df: pd.DataFrame, fund_data: dict = None) -> dict:
    """
    Evaluates a single symbol against the production Wealth Engine rules.
    Evaluates 4 buckets (Core Compounder, Growth Multiplier, Quality-On-Sale, Opportunistic), CMP > SMA200 trend gate, PEG <= 3.0 valuation ceiling, and computes targets without side effects.
    """
    if df is None or df.empty or len(df) < 50:
        return {
            "status": "NO",
            "reasons": [f"Insufficient historical price data ({len(df) if df is not None else 0} bars < 50 minimum)"],
            "score": 0.0,
            "qualified": False
        }

    ticker = df.copy()
    if isinstance(ticker.columns, pd.MultiIndex):
        ticker.columns = ticker.columns.get_level_values(0)
    ticker = ticker.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

    if len(ticker) < 50:
        return {"status": "NO", "reasons": [f"Insufficient valid bars ({len(ticker)} < 50)"], "score": 0.0, "qualified": False}

    latest = ticker.iloc[-1]
    close_price = float(latest["Close"])
    high_52w = float(ticker["High"].iloc[-252:].max()) if len(ticker) >= 252 else float(ticker["High"].max())
    drop_pct = ((high_52w - close_price) / high_52w) * 100.0 if high_52w > 0 else 0.0

    sma200_val = None
    if "SMA200" in ticker.columns and not pd.isna(latest.get("SMA200")):
        sma200_val = float(latest["SMA200"])
    else:
        sma200_val = float(ticker["Close"].tail(200).mean()) if len(ticker) >= 200 else None

    is_trend_ok = (sma200_val is not None and sma200_val > 0 and close_price > sma200_val)

    fd = fund_data or {}
    roce = _safe_num(fd.get("roce", fd.get("ROCE %", fd.get("roce_val", 0.0))))
    roe = _safe_num(fd.get("roe", fd.get("ROE %", fd.get("roe_val", 0.0))))
    debt_equity = _safe_num(fd.get("debt_to_equity", fd.get("Debt/Equity", fd.get("debt_equity", 0.0))))
    peg_val = fd.get("peg_ratio", fd.get("PEG Ratio", fd.get("peg")))

    yoy_sales_pct = _parse_yoy_percent(fd, "yoy_revenue", "YOY Revenue %")
    yoy_profit_pct = _parse_yoy_percent(fd, "yoy_profit", "YOY Profit %")

    peg_num = float(peg_val) if (peg_val is not None and not pd.isna(peg_val)) else None

    buckets = []
    # 1. Core Compounder (ROCE >= 20%, ROE >= 15%, D/E <= 0.50)
    if roce >= 20.0 and roe >= 15.0 and debt_equity <= 0.50:
        buckets.append("Core Compounder")

    # 2. Growth Multiplier (YoY Sales >= 20%, YoY Profit >= 20%, ROCE >= 15%)
    sales_ok = (yoy_sales_pct is not None and yoy_sales_pct >= 20.0)
    profit_ok = (yoy_profit_pct is not None and yoy_profit_pct >= 20.0)
    if sales_ok and profit_ok and roce >= 15.0:
        buckets.append("Growth Multiplier")

    # 3. Quality-On-Sale (ROCE >= 15%, D/E <= 1.0, Drop 52W High >= 10%)
    # [FIX P5-12] Lowered drop threshold from 15%→10%. A 10% correction from 52W high
    # is already meaningful for high-quality stocks. 15% was too deep and missed
    # quality stocks that had healthy 10-14% pullbacks during consolidation.
    if roce >= 15.0 and debt_equity <= 1.0 and drop_pct >= 10.0:
        buckets.append("Quality-On-Sale")

    # 4. Opportunistic (YoY Profit >= 40%)
    if yoy_profit_pct is not None and yoy_profit_pct >= 40.0:
        buckets.append("Opportunistic")

    peg_ok = (peg_num is None or peg_num <= 3.0)
    is_qualified = bool(buckets and is_trend_ok and peg_ok)

    reasons = []
    if is_qualified:
        reasons.append(f"Wealth Engine Qualified ({', '.join(buckets)}) | Close ₹{close_price:.2f} > 200DMA ₹{sma200_val:.2f}")
    elif buckets and not is_trend_ok:
        reasons.append(f"Wealth Setup Met ({', '.join(buckets)}) — Trend Failure: Close ₹{close_price:.2f} ≤ 200DMA ₹{sma200_val if sma200_val else 0:.2f}")
    elif buckets and not peg_ok:
        reasons.append(f"Wealth Setup Met ({', '.join(buckets)}) — PEG Ceiling Rejection: PEG {peg_num:.2f} > 3.0 max limit")
    else:
        reasons.append(f"Lacks Wealth Engine Setup (ROCE {roce:.1f}%, D/E {debt_equity:.2f})")

    from sl_target_helper import compute_sl_and_target
    atr_val = float(latest.get("ATR", close_price * 0.025)) if "ATR" in ticker.columns else (close_price * 0.025)
    sl_result = compute_sl_and_target(entry_price=close_price, atr=atr_val, mode="WEALTH", ticker=ticker)

    status_str = "CORE MET" if is_qualified else ("WATCHLIST" if buckets else "NO")

    # [FIX P5-11] Gradient scoring instead of binary 85/50.
    # Each bucket contributes to a weighted score:
    # Core Compounder=30, Growth Multiplier=25, Quality-On-Sale=20, Opportunistic=15
    # Trend OK = +10, PEG OK = +5
    bucket_scores = {
        "Core Compounder": 30,
        "Growth Multiplier": 25,
        "Quality-On-Sale": 20,
        "Opportunistic": 15,
    }
    gradient_score = sum(bucket_scores.get(b, 0) for b in buckets)
    if is_trend_ok:
        gradient_score += 10
    if peg_ok:
        gradient_score += 5
    gradient_score = min(100.0, max(50.0, float(gradient_score)))

    return {
        "status": status_str,
        "reasons": reasons,
        "buckets": buckets,
        "score": gradient_score if is_qualified else 50.0,
        "qualified": is_qualified,
        "entry_price": close_price,
        "stop_loss": sl_result.get("stop_loss"),
        "target_1": sl_result.get("target_1"),
        "target_2": sl_result.get("target_2"),
        "target_3": sl_result.get("target_3"),
        "target_4": sl_result.get("target_4"),
        "atr_20": atr_val
    }

WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")

# =====================================================================================
# CONSTANTS — Sector Concentration Limits
# =====================================================================================
MAX_SECTOR_PCT  = 0.25   # Max 25% of portfolio from one sector

# =====================================================================================
# MACRO GATES & LIMITS
# =====================================================================================
# Using centralized config for liquidity
# MAX_PROMOTER_PLEDGE = 20     # Disabled: pledge is intentionally neutralized

# =====================================================================================
# NIFTY BENCHMARK
# =====================================================================================

_nifty_cache_fallback = {"ret_6m": None, "dist_52w": None, "ts": None}
_NIFTY_CACHE_TTL = 3600  # 1 hour max staleness

def _get_nifty_cache() -> dict:
    try:
        from application_context import ApplicationContext
        ctx = ApplicationContext.get_instance()
        if ctx.session_context is not None:
            return ctx.session_context.market_regime_manager.cache
        else:
            logger.debug("[SESSION_ARCH] No active session. Using nifty fallback.")
    except Exception:
        pass
    return _nifty_cache_fallback

def fetch_nifty_macro_state() -> Tuple[Optional[float], Optional[float]]:
    """Fetch 6-month return and 52W distance of Nifty 50 for RS and Macro Regime Gate."""
    now = time.time()
    cache = _get_nifty_cache()
    # Serve cache only if fresh
    if (
        cache["ts"] is not None
        and (now - cache["ts"]) < _NIFTY_CACHE_TTL
        and cache["ret_6m"] is not None
    ):
        return (cache["ret_6m"], cache["dist_52w"])

    try:
        from macro_utils import get_nifty_6m_state
        ret_6m, dist_52w = get_nifty_6m_state()
        if ret_6m is not None:
            cache.update({"ret_6m": ret_6m, "dist_52w": dist_52w, "ts": now})
            return (ret_6m, dist_52w)
    except Exception as e:
        logger.exception(f"Failed to fetch Nifty Macro State")

    # Return stale cache rather than None if fetch fails
    logger.warning("Nifty fetch failed — serving stale cache if available")
    return (cache["ret_6m"], cache["dist_52w"])

# =====================================================================================
# PER-STOCK TECHNICAL OVERLAY
# =====================================================================================

class DataQuality(str, Enum):
    LIVE = "LIVE"
    CACHED_PREV_DAY = "CACHED_PREV_DAY"
    CACHED_MULTI_DAY = "CACHED_MULTI_DAY"
    MISSING_PARTIAL = "MISSING_PARTIAL"

def calculate_wealth_technicals(symbol: str, nifty_6m_ret: float, historical_cache: dict = None) -> dict:
    """Fetch MAs, 6-month RS vs Nifty, distance to 52W high, Liquidity, RSI, and ATR."""
    defaults = {
        "sma_200": None, "sma_50": None, "ema_20": None, "cmp": None, 
        "rs_6m": None, "dist_52w_high": None, "liquidity": 0.0,
        "RSI": 50.0, "ATR_Pct": 0.0, "data_quality": DataQuality.MISSING_PARTIAL.value
    }
    for attempt in range(RETRY_ATTEMPTS):
        try:
            # 🔧 CRITICAL FIX: Use pre-fetched historical cache if available
            if historical_cache is not None:
                hist = historical_cache.get(symbol)
                if hist is not None and not hist.empty:
                    pass
                else:
                    hist = None
            else:
                # We enforce bulk fetching in the caller. No 1-by-1 fallback allowed.
                logger.warning(f"No historical cache provided for {symbol}, skipping technicals to prevent rate limits.")
                hist = None
            
            if hist is None or hist.empty or len(hist) < 200:
                return defaults

            is_stale = getattr(hist, 'attrs', {}).get('is_stale', False)

            # Extract sliced indicators for O(1) performance (avoid full-frame rolling math)
            sma_200 = float(hist['Close'].tail(200).mean()) if len(hist) >= 200 else None
            sma_50  = float(hist['Close'].tail(50).mean()) if len(hist) >= 50 else None
            ema_20  = float(hist['Close'].tail(100).ewm(span=20, adjust=False).mean().iloc[-1]) if len(hist) >= 20 else None

            # Calculate 14-day RSI on tail
            delta = hist['Close'].tail(15).diff()
            gain = float(delta.where(delta > 0, 0).tail(14).mean())
            loss = float((-delta.where(delta < 0, 0)).tail(14).mean())
            rs_val = gain / loss if loss > 0 else float('inf')
            rsi = 100 - (100 / (1 + rs_val)) if loss > 0 else 100.0

            # Calculate 14-day ATR on tail
            tail_high = hist['High'].tail(15)
            tail_low = hist['Low'].tail(15)
            tail_close = hist['Close'].tail(15)
            prev_close = tail_close.shift(1)
            
            import numpy as np
            tr1 = tail_high - tail_low
            tr2 = (tail_high - prev_close).abs()
            tr3 = (tail_low - prev_close).abs()
            tr = np.maximum(tr1, np.maximum(tr2, tr3))
            atr = float(tr.tail(14).mean())
            
            last_row = hist.iloc[-1]
            cmp = _safe_num(last_row.get('Close'))

            # ATR as a percentage of CMP
            atr_pct = (atr / cmp) * 100.0 if cmp > 0 and not pd.isna(atr) else 0.0

            # 6-Month Relative Strength vs Nifty
            hist_6m = hist.tail(126)
            is_macro_proxy = (nifty_6m_ret is None)
            if len(hist_6m) > 0:
                start_6m = hist_6m['Close'].iloc[0]
                stock_6m_ret = ((cmp - start_6m) / start_6m) * 100.0 if start_6m > 0 else 0.0
                rs_6m = stock_6m_ret if is_macro_proxy else (stock_6m_ret - nifty_6m_ret)
            else:
                rs_6m = 0.0

            # Distance to 52-Week High
            high_52w = _safe_num(hist['High'].max())
            dist_52w_high = ((high_52w - cmp) / high_52w) * 100.0 if high_52w > 0 else 0.0

            # Liquidity (20-day Average Daily Volume * CMP)
            avg_vol = hist['Volume'].tail(20).mean()
            liquidity = float(avg_vol * cmp) if avg_vol > 0 else 0.0

            # Momentum Quality Evaluation
            from wealth_momentum_filter import calculate_momentum_quality_score
            mom_score, mom_conf = calculate_momentum_quality_score(hist, symbol=symbol)

            return {
                "sma_200": sma_200,
                "sma_50":  sma_50,
                "ema_20":  ema_20,
                "cmp": cmp,
                "rs_6m": rs_6m,
                "rs_is_absolute_proxy": is_macro_proxy,
                "dist_52w_high": dist_52w_high,
                "liquidity": liquidity,
                "RSI": rsi,
                "ATR_Pct": atr_pct,
                "momentum_score": mom_score,
                "momentum_confidence": mom_conf,
                "data_quality": "STALE_INTRADAY" if is_stale else DataQuality.LIVE.value,
                "is_stale": is_stale,
                "above_sma200": bool(cmp >= sma_200) if sma_200 is not None else False
            }
        except Exception as e:
            logger.warning(f"Attempt {attempt+1}/{RETRY_ATTEMPTS} failed for {symbol}: {e}")
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
            else:
                logger.exception(f"Failed to fetch technicals for {symbol} after {RETRY_ATTEMPTS} attempts")
                return defaults


# =====================================================================================
# FINANCIAL CLASSIFIER & UNIT CONVERTERS
# =====================================================================================

FINANCIAL_SECTORS = {
    'financial services', 'financials', 'banking', 'banks', 
    'nbfc', 'housing finance', 'insurance', 'asset management'
}

FINANCIAL_INDUSTRIES = {
    'private sector bank', 'public sector bank', 'non banking financial company (nbfc)',
    'housing finance company', 'life insurance', 'general insurance',
    'asset management company', 'capital market services', 'stockbroking'
}

FINANCIAL_SYMBOL_OVERRIDES = {
    'HDFCBANK', 'ICICIBANK', 'SBIN', 'KOTAKBANK', 'AXISBANK', 
    'BAJFINANCE', 'BAJAJFINSV', 'CHOLAFIN', 'MUTHOOTFIN', 'SHRIRAMFIN'
}

def is_financial_sector(r: dict) -> bool:
    """
    Safely identifies Financial sector entities using normalized Sector/Industry fields,
    falling back to an explicit symbol whitelist if metadata is empty.
    """
    if not isinstance(r, dict):
        return False
    sector = str(r.get('Sector', r.get('sector', ''))).strip().lower()
    industry = str(r.get('Industry', r.get('industry', ''))).strip().lower()
    symbol = str(r.get('Stock', r.get('symbol', ''))).strip().upper()
    
    if sector in FINANCIAL_SECTORS or any(s in sector for s in FINANCIAL_SECTORS):
        return True
    if industry in FINANCIAL_INDUSTRIES or any(i in industry for i in FINANCIAL_INDUSTRIES):
        return True
    if not sector and not industry and symbol in FINANCIAL_SYMBOL_OVERRIDES:
        return True
        
    return False

def watchlist_percent_to_ratio(val, default: float = 0.0) -> float:
    """Converts a known 0-100 percentage value from watchlist to 0.0-1.0 ratio scale."""
    num = _safe_num(val, default=default)
    return num / 100.0

def ratio_to_percent(val, default: float = 0.0) -> float:
    """Converts a known 0.0-1.0 ratio float to 0-100 percentage scale."""
    num = _safe_num(val, default=default)
    return num * 100.0

def _parse_yoy_percent(fd: dict, ratio_key: str, percent_key: str) -> Optional[float]:
    """
    Parses YoY growth to a 0-100 percentage scale by key origin:
    - Ratio-scale key (from map_watchlist_to_v5 dict, e.g. 'yoy_revenue': 0.20) is converted: 0.20 * 100 = 20.0
    - Percent-scale key (from raw Screener export, e.g. 'YOY Revenue %': 20.0) is used as-is.
    Eliminates magnitude guessing completely.
    """
    if not isinstance(fd, dict):
        return None
    # 1. Ratio-scale key takes precedence (V5 dictionary format)
    v_ratio = fd.get(ratio_key)
    if v_ratio is not None and not pd.isna(v_ratio) and v_ratio != "":
        try:
            return float(v_ratio) * 100.0
        except (ValueError, TypeError):
            pass
    # 2. Percent-scale key (Raw Screener export format)
    v_pct = fd.get(percent_key)
    if v_pct is not None and not pd.isna(v_pct) and v_pct != "":
        try:
            return float(v_pct)
        except (ValueError, TypeError):
            pass
    return None

# =====================================================================================
# SHARED BUCKET PREDICATES
# =====================================================================================

def check_core_compounder_rules(score: float, mcap: float, roce: float, roe: float, de: float, is_fin: bool) -> bool:
    """Core Compounder: ₹10,000 Cr+ mcap. Non-financials enforce D/E <= 0.50; Financials omit D/E ceiling."""
    prof_ok = (roe >= 15.0) if is_fin else (roce >= 20.0 and roe >= 15.0)
    de_ok = True if is_fin else (de <= 0.50)
    return score >= 65 and mcap >= 10000 and prof_ok and de_ok

def check_growth_multiplier_rules(score: float, mcap: float, yoy_sales: float, yoy_profit: float, rs_6m, dist_52w: float) -> bool:
    """
    Growth Multiplier: ₹2,000 Cr+ emerging leaders.
    [VERSION: WEALTH_RS_NONE_FIX_v1.0] rs_6m=None → UNKNOWN → benefit of doubt.
    A new stock with insufficient history should not be blocked from this bucket solely
    because relative strength cannot be computed yet. Score + growth evidence is sufficient.
    """
    rs_ok = (rs_6m is None) or (rs_6m >= 0)  # None = UNKNOWN = not actively underperforming
    return score >= 60 and mcap >= 2000 and yoy_sales >= 20.0 and yoy_profit >= 20.0 and rs_ok and dist_52w <= 15.0

def check_quality_on_sale_rules(score: float, roce: float, roe: float, dist_52w: float, de: float, is_fin: bool) -> bool:
    """Quality-On-Sale: score >= 50, dist_52w >= 10%. Financials omit D/E ceiling."""
    prof_ok = (roe >= 15.0) if is_fin else (roce >= 15.0)
    de_ok = True if is_fin else (de <= 1.0)
    return score >= 50 and prof_ok and dist_52w >= 10.0 and de_ok

def check_opportunistic_rules(score: float, yoy_profit: float, rs_6m, cats: str) -> bool:
    """
    Opportunistic / Turnaround: massive momentum + turnaround growth.
    [VERSION: WEALTH_RS_NONE_FIX_v1.0] rs_6m=None → cannot qualify for Opportunistic.
    This bucket's core thesis is confirmed momentum — unknown RS is not sufficient evidence.
    Unlike Growth Multiplier, this is not benefit of doubt; momentum must be demonstrated.
    """
    if rs_6m is None:
        return False  # Momentum evidence required — UNKNOWN ≠ PASS for turnaround thesis
    return score >= 55 and yoy_profit >= 40.0 and rs_6m >= 15.0 and "SME" not in cats

# =====================================================================================
# SCORING ENGINE: V5 Pipeline (core/multibagger_pipeline.py)
# =====================================================================================
# Scores are derived from run_pipeline_for_symbol() -> PipelineDecision.
# CIS = composite_score, BQS = quality.score, RVS = valuation.score
# See core/score_engine.py for V5 factor weights and canonical rubric.
# =====================================================================================

from memory_profiler import profile_function

@profile_function("Wealth: map_watchlist_to_v5")
def map_watchlist_to_v5(raw_data: dict) -> dict:
    """Maps Screener export headers to V5 snake_case variables and reconstructs missing absolute metrics for Valuation Engine."""
    import pandas as pd
    
    def _safe_float(val, default=0.0):
        if val is None or pd.isna(val) or val == "": return default
        try: return float(val)
        except Exception: return default
        
    market_cap = _safe_float(raw_data.get('Market Cap Cr', raw_data.get('Market Capitalization')))
    price = _safe_float(raw_data.get('cmp', 0.0))
    pe = _safe_float(raw_data.get('PE Ratio', raw_data.get('Price to Earning')))
    pb = _safe_float(raw_data.get('Price to Book', raw_data.get('Price to book value')))
    
    def _safe_float_allow_missing(val):
        if val is None or pd.isna(val) or val == "": return None
        try: return float(val)
        except Exception: return None

    # [VERSION: V5_VALUATION_FIX] The V5 Valuation Engine requires absolute numbers (EPS, BVPS, Shares) 
    # which aren't in the raw screener ratios. We must reconstruct them mathematically.
    shares = (market_cap / price) if price > 0 else 0.0
    eps = (price / pe) if pe is not None and pe != 0 else 0.0
    bvps = (price / pb) if pb is not None and pb != 0 else 0.0
    
    peg_raw = raw_data.get('PEG Ratio', raw_data.get('PEG'))
    peg_val = _safe_float_allow_missing(peg_raw)
    
    is_fin = is_financial_sector(raw_data)

    # [VERSION: WEALTH_PROXY_FIX_v1.0] Remove positive proxy defaults for absent growth and FCF
    # metrics. Previously, missing revenue_cagr_3y/pat_cagr_3y/fcf_margin defaulted to +15%/+10%,
    # silently inflating FM_Score for data-void stocks and potentially pushing them above BUY thresholds.
    # Missing data should propagate as None so the V5 scoring engine can apply its own penalty
    # or neutral treatment rather than assuming healthy financial characteristics.
    _yoy_rev_raw    = _safe_float_allow_missing(raw_data.get('YOY Revenue %', raw_data.get('Sales growth')))
    _yoy_profit_raw = _safe_float_allow_missing(raw_data.get('YOY Profit %', raw_data.get('Profit growth')))
    _fcf_margin_raw = _safe_float_allow_missing(raw_data.get('FCF Margin %', raw_data.get('FCF Margin')))
    _rev_cagr_3y    = (_yoy_rev_raw    / 100.0) if _yoy_rev_raw    is not None else None
    _pat_cagr_3y    = (_yoy_profit_raw / 100.0) if _yoy_profit_raw is not None else None
    _fcf_cagr_3y    = _pat_cagr_3y  # Proxy: FCF growth approximated by profit growth when absent
    _fcf_margin     = (_fcf_margin_raw / 100.0) if _fcf_margin_raw is not None else None

    return {
        'market_cap': market_cap,
        'roce': _safe_float(raw_data.get('ROCE %', raw_data.get('ROCE'))) / 100.0,
        'roe': _safe_float(raw_data.get('ROE %', raw_data.get('ROE'))) / 100.0,
        'debt_to_equity': _safe_float_allow_missing(raw_data.get('Debt/Equity', raw_data.get('Debt to equity'))),
        'interest_coverage': _safe_float_allow_missing(raw_data.get('Interest Coverage', raw_data.get('Interest coverage'))),
        'operating_margin_ttm': _safe_float(raw_data.get('OPM %', raw_data.get('OPM'))) / 100.0,
        'yoy_revenue': _safe_float(raw_data.get('YOY Revenue %', raw_data.get('Sales growth'))) / 100.0,
        'yoy_profit': _safe_float(raw_data.get('YOY Profit %', raw_data.get('Profit growth'))) / 100.0,
        'revenue_cagr_3y':  _rev_cagr_3y,   # None if absent — V5 pipeline handles UNKNOWN
        'revenue_growth_1y': _rev_cagr_3y,   # Same YoY Revenue source
        'pat_cagr_3y':      _pat_cagr_3y,    # None if absent
        'fcf_cagr_3y':      _fcf_cagr_3y,    # None if absent (proxied from profit growth)
        'reinvestment_rate': 0.50,            # Portfolio theory assumption — not a stock metric
        'peg': peg_val,
        'pe': pe,
        'ev_ebitda': _safe_float(raw_data.get('EV/EBITDA', raw_data.get('EV / EBITDA')), default=pe),
        'fcf_margin': _fcf_margin,            # None if absent — V5 pipeline handles UNKNOWN
        'free_cash_flow': (eps * shares * 1.33 * 0.75) * (_safe_float(raw_data.get('FCF Margin %'), default=10.0) / 100.0), # Proxy FCF based on NOPAT and FCF Margin
        'price_to_book': pb,
        'gross_margin_stability': _safe_float(raw_data.get('gross_margin_stability'), default=5.0) / 100.0,
        'asset_turnover': _safe_float(raw_data.get('asset_turnover'), default=1.0),
        
        # Injected Reconstructed Fields for Valuation Models
        'eps': eps,
        'book_value_per_share': bvps,
        'shares_outstanding': shares,
        'tt_indpe': pe,  # Proxy industry PE with trailing PE if missing
        'ebit': (eps * shares * 1.33) if eps is not None and shares is not None else 0.0,  # Proxy NOPAT assuming 25% tax
        
        # Technical fields that might be passed from wealth_technicals
        'pct_from_52w_high': _safe_float(raw_data.get('dist_52w_high', 0.0)) / -100.0,
        'rs_rating': _safe_float(raw_data.get('RS_Rating', 50.0)),
        'relative_volume_10d': 1.0,  # Proxy default
        'sector': str(raw_data.get('Sector', 'Unknown')),
        'Path': 'Financial' if is_fin else 'Standard',
        'is_financial': is_fin,
        'price': price
    }

@profile_function("Wealth: apply_core_engine_scores")
def apply_core_engine_scores(r, sector_stats: dict = None) -> pd.Series:
    """
    Migrated to V5 Pipeline architecture since core.deprecated.core_score_engine was removed.
    Maps V5 component scores to legacy FM_Score (CIS), Valuation_Score (RVS), and Consistency_Score (BQS).
    """
    from core.multibagger_pipeline import run_pipeline_for_symbol
    
    symbol = str(r.get("Stock", ""))
    raw_data = r.to_dict()
    
    try:
        # The V5 pipeline expects a dict of the watchlist row
        decision = run_pipeline_for_symbol(symbol, map_watchlist_to_v5(raw_data))
        
        return pd.Series({
            "CIS": decision.composite_score,
            "RVS": decision.valuation.score if decision.valuation else 0,
            "BQS": decision.quality.score if decision.quality else 0,
            "Reliability": decision.quality.confidence if decision.quality else 0,
            "Base_FV": None,  # Deprecated in V5
            "Bull_FV": None   # Deprecated in V5
        })
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"V5 Pipeline failed for {symbol}: {e}")
        return pd.Series({
            "CIS": 0, "RVS": 0, "BQS": 0, "Reliability": 0, "Base_FV": None, "Bull_FV": None
        })


@profile_function("Wealth: determine_portfolio_bucket")
def determine_portfolio_bucket(r, nifty_dist_52w: float):
    """Assign stocks to Core / Growth / Opportunistic buckets based on hard filters."""
    import pandas as pd

    # [VERSION: WEALTH_BUCKET_FIX_v1.1] Handle pandas NaN semantics for missing data
    score      = _safe_num(r.get("FM_Score", 0))
    mcap       = _safe_num(r.get("Market Cap Cr", 0))
    roce       = _safe_num(r.get("ROCE %", 0))
    roe        = _safe_num(r.get("ROE %", 0))
    de         = _safe_num(r.get("Debt/Equity", 0))
    yoy_sales  = _safe_num(r.get("YOY Revenue %", 0))
    yoy_profit = _safe_num(r.get("YOY Profit %", 0))
    # [VERSION: WEALTH_RS_NONE_FIX_v1.0] rs_6m propagated as None when absent.
    # Default 0 silently passed the rs_6m >= 0 Growth Multiplier gate — UNKNOWN ≠ neutral.
    rs_6m_raw  = r.get("rs_6m")
    rs_6m      = None if (rs_6m_raw is None or (isinstance(rs_6m_raw, float) and pd.isna(rs_6m_raw))) else float(rs_6m_raw)
    dist_52w   = _safe_num(r.get("dist_52w_high", 0))
    liquidity  = _safe_num(r.get("liquidity", 0))
    cats       = str(r.get("Category", ""))
    is_fin     = is_financial_sector(r.to_dict() if hasattr(r, 'to_dict') else r)

    buckets = []

    # Instant Kill Gate 1: Liquidity Floor
    from config import MIN_DAILY_LIQUIDITY_RUPEES_WEALTH
    if liquidity < MIN_DAILY_LIQUIDITY_RUPEES_WEALTH:
        return None

    # Instant Kill Gate 2: Extreme Valuation Ceiling (PEG <= 3.0)
    peg_raw = r.get("PEG Ratio", r.get("PEG"))
    if peg_raw is not None and not pd.isna(peg_raw) and peg_raw != "":
        try:
            peg_val = float(peg_raw)
            if peg_val > 3.0:
                return None
        except (ValueError, TypeError):
            pass

    # [VERSION: WEALTH_FIN_GNPA_GATE_v1.0] Instant Kill Gate 3 (financial sector only):
    # The D/E ceiling is waived for banks/NBFCs (correctly — they are naturally leveraged).
    # That exemption creates a gap: a bank with GNPA=8% and ROE=16% would pass unchecked.
    # This gate substitutes D/E with a banking-specific NPA quality check.
    # GNPA present and > 5% → FAIL (known bad).  GNPA absent → UNKNOWN → benefit of doubt.
    if is_fin:
        gnpa_raw = r.get("GNPA %", r.get("gnpa"))
        gnpa = None if (gnpa_raw is None or (isinstance(gnpa_raw, float) and pd.isna(gnpa_raw))) else _safe_num(gnpa_raw)
        if gnpa is not None and gnpa > 5.0:
            return None  # Known-bad NPA: FAIL — not a data void, data is present and negative

    if check_core_compounder_rules(score, mcap, roce, roe, de, is_fin):
        buckets.append("Core")

    if check_growth_multiplier_rules(score, mcap, yoy_sales, yoy_profit, rs_6m, dist_52w):
        buckets.append("Growth")

    if check_quality_on_sale_rules(score, roce, roe, dist_52w, de, is_fin):
        buckets.append("Quality-On-Sale")

    if check_opportunistic_rules(score, yoy_profit, rs_6m, cats):
        buckets.append("Opportunistic")

    return ", ".join(buckets) if buckets else "REVIEW"



@profile_function("Wealth: apply_sector_cap")
def apply_sector_cap(df: pd.DataFrame, bucket_col: str, bucket_name: str, max_stocks: int) -> pd.DataFrame:
    """
    Enforce sector concentration limits on a bucket:
      - Max 25% of max_stocks per sector
      - Max 2 stocks per specific industry (sector sub-group)
    Returns a filtered DataFrame.
    """
    bucket_df = df[df[bucket_col].str.contains(bucket_name, na=False)].copy()
    bucket_df = bucket_df.sort_values(by="FM_Score", ascending=False)

    import math
    sector_limit = max(1, math.ceil(max_stocks * MAX_SECTOR_PCT))
    sector_counts = defaultdict(int)
    industry_counts = defaultdict(int)
    selected = []

    for _, row in bucket_df.iterrows():
        sector = row.get("Sector", "Unknown")
        industry = row.get("Industry", row.get("Sector", "Unknown"))

        if sector_counts[sector] >= sector_limit:
            continue
        if industry_counts[industry] >= 2:
            continue

        sector_counts[sector] += 1
        industry_counts[industry] += 1
        selected.append(row)

        if len(selected) >= max_stocks:
            break

    return pd.DataFrame(selected).reset_index(drop=True) if selected else pd.DataFrame()


# =====================================================================================
# MAIN LOOP
# =====================================================================================

# =====================================================================================
# DAILY HOLD SCORE ENGINE (0-100)
# =====================================================================================
def calculate_hold_score(r: pd.Series) -> int:
    """
    Evaluates existing holdings based on a 100-point exit rubric.
    Score < 45 = SELL REVIEW
    Pure scoring function without side effects.
    """
    score = 0
    
    cmp = _safe_num(r.get("cmp"))
    entry_price = _safe_num(r.get("entry_price"))
    
    if entry_price > 0 and cmp > 0:
        drawdown_pct = ((entry_price - cmp) / entry_price) * 100.0
        if drawdown_pct >= 20.0:
            return 0  # Zero score for >=20% drawdown
        elif drawdown_pct > 10.0:
            score -= 25  # Soft drawdown penalty (-25 pts) for 10-20% loss zone
    
    # 2. Technical Health (40 pts)
    ema20 = _safe_num(r.get("ema_20"))
    sma50 = _safe_num(r.get("sma_50"))
    sma200 = _safe_num(r.get("sma_200"))
    rs_6m = _safe_num(r.get("rs_6m"))
    
    if cmp > ema20 and ema20 > 0: score += 10
    if cmp > sma50 and sma50 > 0: score += 10
    if cmp > sma200 and sma200 > 0: score += 10
    if rs_6m > 0: score += 10
    
    # 3. Fundamental Integrity (30 pts - 10 pts reallocated from hardcoded None pledge)
    fm_score = _safe_num(r.get("FM_Score"))
    if fm_score >= 65: score += 25
    elif fm_score >= 50: score += 10
    
    yoy_profit = _safe_num(r.get("YOY Profit %"))
    if yoy_profit > 0: score += 5
    
    # 4. Sector & Momentum Regime (15 pts)
    rs_rating = _safe_num(r.get("RS_Rating"))
    if rs_rating > 80: score += 15
    elif rs_rating > 50: score += 5
    
    # 5. Portfolio Context / Alpha Adjustments (15 pts)
    ai_conf = _safe_num(r.get("AI_Confidence"))
    if ai_conf >= 7: score += 15
    elif ai_conf >= 4: score += 5
    
    return min(100, max(0, score))


from datetime import date, timedelta

LTCG_THRESHOLD_DAYS = 365  # Indian LTCG: > 12 months
LTCG_BONUS_WINDOW   = 30   # Apply bonus in final 30 days before 1-year mark

def compute_tax_hold_bonus(entry_date: date, unrealized_pnl_pct: float) -> dict:
    today = datetime.now(IST).date()
    holding_days = (today - entry_date).days
    days_to_ltcg = LTCG_THRESHOLD_DAYS - holding_days

    harvest_signal = False
    if unrealized_pnl_pct < -10 and holding_days < LTCG_THRESHOLD_DAYS:
        harvest_signal = True

    if holding_days >= LTCG_THRESHOLD_DAYS:
        return {"bonus": 0, "reason": "Already LTCG — no penalty for selling", "harvest_signal": harvest_signal}
    
    if 0 < days_to_ltcg <= LTCG_BONUS_WINDOW:
        bonus = round(10 * ((LTCG_BONUS_WINDOW - days_to_ltcg + 1) / LTCG_BONUS_WINDOW), 1)
        return {
            "bonus": bonus,
            "reason": f"LTCG in {days_to_ltcg}d",
            "ltcg_date": entry_date + timedelta(days=LTCG_THRESHOLD_DAYS),
            "telegram_alert": days_to_ltcg in [30, 15, 7],
            "harvest_signal": harvest_signal
        }
    
    return {"bonus": 0, "reason": "Normal STCG zone", "harvest_signal": harvest_signal}


from lock_utils import ProcessLock
_scan_lock = ProcessLock("wealth_engine")
_global_lock = ProcessLock("global_scanner_lock")

# =====================================================================================
# MAIN PIPELINE WRAPPERS
# =====================================================================================
def run_wealth_scan(is_test_mode=False):
    from database import is_scanner_stopped, upsert_scanner_health
    from lock_utils import print_scanner_start_banner, print_scanner_end_banner
    if is_scanner_stopped("Wealth Engine"):
        logger.info("🛑 Wealth Engine is STOPPED by Admin. Skipping execution.")
        return None

    queued_at = None
    if not _global_lock.acquire(blocking=False):
        queued_at = time.monotonic()
        logger.info("⏳ [WEALTH ENGINE] Global lock busy — marking status QUEUED and waiting...")
        upsert_scanner_health("Wealth Engine", "QUEUED", error_msg="Waiting in queue for active scanner to complete...")
        if not _global_lock.acquire(blocking=True):
            raise RuntimeError("Failed to acquire global scanner lock.")
        logger.info(f"✅ [WEALTH ENGINE] Global lock acquired after {round(time.monotonic()-queued_at,1)}s wait. Starting scan...")

    if not _scan_lock.acquire(blocking=False):
        _global_lock.release()
        logger.warning("⏭️ Wealth Engine scan skipped — previous run still in progress.")
        return None

    _scan_start = print_scanner_start_banner("wealth_engine", queued_at=queued_at)
    try:
        return _run_wealth_scan_wrapper(is_test_mode)
    finally:
        print_scanner_end_banner("wealth_engine", _scan_start)
        _scan_lock.release()
        _global_lock.release()

import pandas as pd
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

# =====================================================================================
# LAYER 1: CANDIDATE SELECTION
# =====================================================================================
@profile_function("Wealth: evaluate_candidates")
def evaluate_candidates(wealth_df, sector_stats, nifty_dist_52w):
    """Evaluates core fundamentals and technicals for candidates, omitting entry logic."""
    if wealth_df.empty:
        return wealth_df

    if "rs_6m" in wealth_df.columns:
        wealth_df["RS_Rating"] = wealth_df["rs_6m"].rank(pct=True, ascending=True) * 100
    else:
        wealth_df["RS_Rating"] = 0

    scores_df = wealth_df.apply(lambda r: apply_core_engine_scores(r, sector_stats), axis=1)
    wealth_df["FM_Score"] = scores_df["CIS"]
    
    unique_symbols = wealth_df["Stock"].astype(str).unique()
    try:
        from block_deal_detector import compute_inst_bonus
        bonus_map = {sym: float(compute_inst_bonus(sym)) for sym in unique_symbols}
    except Exception as e:
        logging.getLogger(__name__).warning(f"Error building institutional bonus map in Wealth: {e}")
        bonus_map = {sym: 0.0 for sym in unique_symbols}
        
    # [VERSION: BUSINESS_LOGIC_FIX_v1.0] Block Deal Bonus Logic 
    base_score = wealth_df["FM_Score"]
    bonuses = wealth_df["Stock"].map(bonus_map).fillna(0.0)
    applied_bonus = bonuses.where(base_score >= 50, 0.0)
    final_score = base_score + applied_bonus
    wealth_df["FM_Score"] = final_score.clip(upper=100.0)
    
    wealth_df["base_fm_score"] = base_score
    wealth_df["inst_bonus_applied"] = applied_bonus
    
    wealth_df["Valuation_Score"] = scores_df["RVS"]
    wealth_df["Consistency_Score"] = scores_df["BQS"]
    wealth_df["Reliability"] = scores_df["Reliability"]
    wealth_df["Base_FV"] = scores_df["Base_FV"]
    wealth_df["Bull_FV"] = scores_df["Bull_FV"]
    
    wealth_df["Portfolio_Bucket"] = wealth_df.apply(lambda r: determine_portfolio_bucket(r, nifty_dist_52w), axis=1)

    def check_completeness(r):
        # [VERSION: WEALTH_COMPLETENESS_SPLIT_v1.0] Tiered mandatory fields.
        # Previously all 8 fields were treated identically — momentum_confidence=NaN caused
        # the same hard suppress as cmp=NaN. These are fundamentally different data voids.
        #
        # Hard mandatory: missing = cannot produce a valid entry decision → suppress
        hard_mandatory = ["cmp", "sma_200", "FM_Score"]
        for col in hard_mandatory:
            val = r.get(col)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                return False
        # Soft mandatory: missing = reduce scoring confidence, not suppress.
        # momentum_confidence, rs_6m, Valuation_Score, Consistency_Score are handled
        # downstream — missing values get conservative defaults in the entry signal logic,
        # not a blanket suppress. data_quality is a metadata tag, not a quality metric.
        return True

    wealth_df["candidate_complete_for_buy"] = wealth_df.apply(check_completeness, axis=1)
    
    return wealth_df


# =====================================================================================
# LAYER 2: ENTRY TIMING
# =====================================================================================
@profile_function("Wealth: generate_entry_signal")
def generate_entry_signal(candidate_df, buy_gate_active, suppression_reason, open_symbols=None):
    """Decides whether a candidate should be bought, suppressed, or watched."""
    if candidate_df.empty:
        return candidate_df
        
    if open_symbols is None: open_symbols = []
    
    # 1. Compute the strict Top-N capped universe
    core_capped = apply_sector_cap(candidate_df, "Portfolio_Bucket", "Core", max_stocks=15)
    growth_capped = apply_sector_cap(candidate_df, "Portfolio_Bucket", "Growth", max_stocks=10)
    opp_capped = apply_sector_cap(candidate_df, "Portfolio_Bucket", "Opportunistic", max_stocks=10)
    qos_capped = apply_sector_cap(candidate_df, "Portfolio_Bucket", "Quality-On-Sale", max_stocks=5)
    
    approved_symbols = set()
    for df_capped in [core_capped, growth_capped, opp_capped, qos_capped]:
        if not df_capped.empty:
            approved_symbols.update(df_capped["Stock"].tolist())
            

    def _get_entry_signal(r):
        score = r.get("FM_Score", 0)
        cmp = _safe_num(r.get("cmp"))
        sma = _safe_num(r.get("sma_200"))
        rs = _safe_num(r.get("rs_6m"))
        used_fallback = r.get("used_fallback_data", False)
        bucket = str(r.get("Portfolio_Bucket", ""))
        is_complete = r.get("candidate_complete_for_buy", False)
        
        if not is_complete:
            return pd.Series({"Signal_Code": "SUPPRESS", "Signal_Reason": "Incomplete Fundamentals/Technicals"})
            
        if used_fallback:
            return pd.Series({"Signal_Code": "SUPPRESS", "Signal_Reason": "Stale Data — Prevented Fake Buy"})
            
        if buy_gate_active:
            if "Quality-On-Sale" in bucket:
                cons_score = r.get("Consistency_Score", 0)
                val_score = r.get("Valuation_Score", 0)
                is_fin = is_financial_sector(r.to_dict() if hasattr(r, 'to_dict') else r)
                
                def passes_profitability_gate(record: pd.Series) -> bool:
                    roce = _safe_num(record.get("ROCE %"))
                    roe = _safe_num(record.get("ROE %"))
                    if is_financial_sector(record.to_dict() if hasattr(record, 'to_dict') else record):
                        return roe >= 15.0
                    return roce >= 15.0
                    
                profitability_ok = passes_profitability_gate(r)
                fcf_margin = r.get("FCF Margin %")
                mom_conf = r.get("momentum_confidence", "")
                
                fcf_ok = True if is_fin else (pd.isna(fcf_margin) or fcf_margin > 0)
                
                # Missing PEG policy: if PEG is missing, require val_score (RVS) >= 50
                peg_raw = r.get("PEG Ratio", r.get("PEG"))
                has_missing_peg = (peg_raw is None or pd.isna(peg_raw) or peg_raw == "")
                peg_val_ok = (val_score >= 50.0) if has_missing_peg else True
                
                if (score >= 65 and cons_score >= 18 and val_score >= 10 and peg_val_ok and
                    cmp > 0 and sma > 0 and cmp >= 0.95 * sma and
                    rs > -10 and profitability_ok and fcf_ok and mom_conf != "LOW"):
                    return pd.Series({"Signal_Code": "BUY", "Signal_Reason": f"Bear Market Value Add: {suppression_reason}"})
            return pd.Series({"Signal_Code": "SUPPRESS", "Signal_Reason": suppression_reason})
            
        if bucket in ("REVIEW", "None", "", "nan") or pd.isna(r.get("Portfolio_Bucket")):
            return pd.Series({"Signal_Code": "WAIT", "Signal_Reason": "Failed Bucket Quality Gates"})
            
        symbol = r.get("Stock")
        if open_symbols and symbol in open_symbols:
            return pd.Series({"Signal_Code": "HOLD", "Signal_Reason": "Position Already Open"})
            
        if symbol not in approved_symbols:
            return pd.Series({"Signal_Code": "WAIT", "Signal_Reason": "Ranked Out (Top N / Sector Cap limit)"})
            
        # Baseline Active Entry Condition (V5 Thresholds)
        if score >= 55 and r.get("Consistency_Score", 0) >= 15 and r.get("Valuation_Score", 0) >= 5 and cmp > sma and sma > 0:
            mom_conf = r.get("momentum_confidence", "")
            mom_score = r.get("momentum_score", 0)
            is_qos = ("Quality-On-Sale" in bucket)
            
            min_mom_score = 20 if (is_qos and mom_conf == "LOW") else (15 if is_qos else 25)
            
            if mom_conf == "LOW" and not is_qos:
                return pd.Series({"Signal_Code": "HOLD", "Signal_Reason": "Low Momentum Quality"})
            
            if mom_score < min_mom_score:
                return pd.Series({"Signal_Code": "HOLD", "Signal_Reason": f"Waiting for Momentum (Score {mom_score} < {min_mom_score})"})
                
            return pd.Series({"Signal_Code": "BUY", "Signal_Reason": f"Score: {score:.1f}, Mom: {mom_score}"})
            
        from wealth_mean_reversion import get_mean_reversion_signal
        mr_code, mr_reason = get_mean_reversion_signal(r)
        if mr_code:
            return pd.Series({"Signal_Code": mr_code, "Signal_Reason": mr_reason})
            
        # Provide contextual WAIT messages instead of blanks
        if cmp <= sma and sma > 0:
            return pd.Series({"Signal_Code": "WAIT", "Signal_Reason": "Below 200 SMA"})
        if score < 55:
            return pd.Series({"Signal_Code": "WAIT", "Signal_Reason": f"Score {score:.1f} < 55"})
        if r.get("Consistency_Score", 0) < 15:
            return pd.Series({"Signal_Code": "WAIT", "Signal_Reason": "Low Consistency"})
        if r.get("Valuation_Score", 0) < 5:
            return pd.Series({"Signal_Code": "WAIT", "Signal_Reason": "Overvalued"})
            
        return pd.Series({"Signal_Code": "WAIT", "Signal_Reason": "Building Base"})

    entry_signals = candidate_df.apply(_get_entry_signal, axis=1)
    candidate_df["Signal_Code"] = entry_signals["Signal_Code"]
    candidate_df["Signal_Reason"] = entry_signals["Signal_Reason"]
    candidate_df["Signal"] = entry_signals.apply(lambda x: f"{x['Signal_Code']} ({x['Signal_Reason']})" if x['Signal_Code'] and x['Signal_Reason'] else x['Signal_Code'], axis=1)
    
    def calculate_position_sizing(r):
        if r.get("Signal_Code", "") != "BUY":
            r["position_pct"] = None
            r["position_amount"] = None
            r["position_shares"] = None
            r["alloc_category"] = "NONE"
            return r
            
        cmp = r.get("cmp", 0)
        atr_pct = r.get("ATR_Pct", 0)
        used_fallback = r.get("used_fallback_data", False)
        momentum_score = r.get("momentum_score", 0)
        
        if used_fallback or cmp == 0:
            r["position_pct"] = 0.0
            r["position_amount"] = 0.0
            r["position_shares"] = 0
            r["alloc_category"] = "SUPPRESSED"
            return r
            
        from wealth_risk_adjusted_sizing import calculate_risk_adjusted_sizing
        sizing = calculate_risk_adjusted_sizing(cmp, atr_pct, momentum_score)
        
        r["position_pct"] = sizing["Position_Pct"]
        r["position_amount"] = sizing["Position_Amount"]
        r["position_shares"] = max(1, int(sizing["Position_Amount"] / cmp)) if cmp > 0 else 0
        r["alloc_category"] = sizing["Alloc_Category"]
        return r

    candidate_df = candidate_df.apply(calculate_position_sizing, axis=1)
    
    # Flag Core_Selected for the UI based on the cap calculated at the top
    core_symbols = set(core_capped["Stock"].tolist()) if not core_capped.empty else set()
    candidate_df["Core_Selected"] = candidate_df["Stock"].apply(lambda s: s in core_symbols)
    
    return candidate_df

# =====================================================================================
# LAYER 3: PORTFOLIO MANAGEMENT
# =====================================================================================
@profile_function("Wealth: evaluate_open_positions")
def evaluate_open_positions(portfolio_df, portfolio_dict):
    """Generates HOLD/SELL/SELL_REVIEW/TLH signals for independently fetched open positions."""
    if portfolio_df.empty:
        return portfolio_df

    def _coerce_to_date(value):
        from datetime import date, datetime
        import pandas as pd
        if value is None: return None
        if isinstance(value, date) and not isinstance(value, datetime): return value
        if isinstance(value, datetime): return value.date()
        try: return pd.to_datetime(value).date()
        except Exception: return None

    def _generate_exit_signal(r):
        base_hold_score = calculate_hold_score(r)
        sym = r.get("Stock")
        
        cmp = _safe_num(r.get("cmp"))
        entry_price = _safe_num(r.get("entry_price"))
        prev_close = _safe_num(r.get("prev_close"))  # Genuine prev_close (None if missing)
        used_fallback = r.get("used_fallback_data", False)
        data_quality = str(r.get("data_quality", ""))
        import math
        
        # 1. Strict Live Price & Genuine Prev Close Validation
        has_genuine_prev_close = prev_close is not None and prev_close > 0
        is_live_valid = (
            math.isfinite(cmp) and 
            cmp > 0 and 
            not used_fallback and 
            data_quality == "LIVE" and 
            has_genuine_prev_close and 
            (abs(cmp - prev_close) / prev_close <= 0.50)
        )
        
        # 2. Hard Risk Stop (Calculated BEFORE Tax Bonus)
        drawdown_pct = 0.0
        if entry_price > 0 and cmp > 0:
            drawdown_pct = ((entry_price - cmp) / entry_price) * 100.0

        if entry_price > 0 and is_live_valid and drawdown_pct >= 20.0:
            r["Hold_Score"] = 0
            r["Exit_Code"] = "SELL"
            r["Exit_Reason"] = f"Hard Drawdown Stop Triggered: -{drawdown_pct:.1f}% (>= 20.0% max loss threshold)"
            return r

        # 3. Tax Bonus Computation (Applied ONLY if no hard stop triggered)
        tax_info = {}
        final_hold_score = base_hold_score
        try:
            entry_date = _coerce_to_date(r.get("entry_date"))
            if entry_date:
                tax_info = compute_tax_hold_bonus(entry_date, -drawdown_pct)
                final_hold_score = min(100, base_hold_score + tax_info.get('bonus', 0))
        except Exception:
            pass

        r["Hold_Score"] = final_hold_score
        
        from wealth_hold_tracking import HoldScoreTrendAnalyzer
        trend = HoldScoreTrendAnalyzer.analyze_trend(sym)
        hold_trend = trend["reason"] if trend["action"] != "HOLD" else "Stable"
        r["hold_trend"] = hold_trend

        # 4. Soft Exits & Review Signals
        # [VERSION: WEALTH_STALE_STATE_v1.0] Expose DATA_STALE as an explicit exit state.
        # Previously returned Exit_Code="" making two very different situations indistinguishable:
        # (a) all checks passed → no exit needed  (b) price data too stale to evaluate.
        # Operators and dashboards can now filter/alert on DATA_STALE rather than seeing silence.
        # Per design intent: no automatic SELL or SELL_REVIEW is triggered on stale data —
        # only the state is surfaced. The exit decision is deferred until fresh data arrives.
        if not is_live_valid:
            r["Exit_Code"] = "DATA_STALE"
            r["Exit_Reason"] = "Stale/Missing price data — exit evaluation deferred until fresh quote"
            return r

        from macro_utils import get_macro_regime
        macro_regime = get_macro_regime()
        
        rs_threshold = -40
        if macro_regime in ("BEAR", "WEAK_BEAR", "RANGEBOUND"):
            rs_threshold = -55
        elif macro_regime == "STRONG_BEAR":
            rs_threshold = -60
            
        rs = _safe_num(r.get("rs_6m"))
        sma = _safe_num(r.get("sma_200"))
        
        rs_exit_triggered = False
        if rs < rs_threshold:
            if macro_regime in ("BEAR", "WEAK_BEAR", "STRONG_BEAR", "RANGEBOUND"):
                if final_hold_score < 50 or (sma > 0 and cmp < sma):
                    rs_exit_triggered = True
            else:
                rs_exit_triggered = True

        exit_code = ""
        exit_reason = ""

        # Correct Priority Order:
        # 1. RS Exit (with confirmed weakness)
        # 2. Catastrophic Trend Collapse (cmp < 0.75 * sma)
        # 3. Hold Score Degradation (< 45)
        # 4. Hold Trend Warnings
        # 5. Tax Loss Harvesting
        if rs_exit_triggered and sma > 0 and cmp < sma:
            exit_code, exit_reason = "SELL", f"Confirmed RS Breakdown [{macro_regime}] (RS: {rs:.1f} < {rs_threshold} & CMP < 200SMA)"
        elif sma > 0 and cmp < (0.75 * sma):
            exit_code, exit_reason = "SELL", "Catastrophic Trend Collapse (CMP < 75% 200SMA)"
        elif "SELL REVIEW" in hold_trend or "Momentum Reversal" in hold_trend:
            exit_code, exit_reason = "SELL_REVIEW", hold_trend
        elif final_hold_score < 45:
            exit_code, exit_reason = "SELL_REVIEW", f"Hold Score Degraded: {final_hold_score}/100"
        elif tax_info.get("harvest_signal"):
            exit_code, exit_reason = "TLH", f"Tax-Loss Harvest Opportunity: -{drawdown_pct:.1f}%"

        r["Exit_Code"] = exit_code
        r["Exit_Reason"] = exit_reason
        return r

    return portfolio_df.apply(_generate_exit_signal, axis=1)

# =====================================================================================
# MAIN PIPELINE WRAPPERS
# =====================================================================================


def _run_wealth_scan_wrapper(is_test_mode=False):
    import time
    start_time = time.time()
    # [VERSION: PERF_PHASE0_v1.0] Reset stage timer ring buffer at scan start
    from perf_utils import reset_stage_timers, ScannerStageTracker
    reset_stage_timers()
    stage_tracker = ScannerStageTracker("WEALTH_ENGINE")
    stage_tracker.start_stage(1, "Watchlist & Portfolio Prep", "Loading watchlist parquet and portfolio positions from Postgres")
    from config import WATCHLIST_PATH, DATA_DIR
    from database import upsert_scanner_health
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    
    logger.info(f"🚀 [START] WEALTH ENGINE INIT | {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")

    import os

    WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
    logger.info("💰 Fund Manager Wealth Engine v3 Started Scan (Strict Layered).")
    
    import database
    if not getattr(database, "DONT_SAVE_WEALTH", False):
        upsert_scanner_health("Wealth Engine", "RUNNING", error_msg="Wealth Engine Scan in progress...")

    try:
        if not os.path.exists(WATCHLIST_PATH):
            logger.warning("⚠️ Watchlist not found. Wealth Engine is forcing the Daily Builder to run.")
            try:
                from daily_builder import main as build_watchlist
                build_watchlist()
            except Exception as e:
                logger.exception(f"❌ Wealth Engine failed to build watchlist")
                if not getattr(database, "DONT_SAVE_WEALTH", False):
                    upsert_scanner_health("Wealth Engine", "IDLE", error_msg="Watchlist build failed")
                return

        from database import download_parquet_from_db
        if not os.path.exists(WEALTH_PATH):
            download_parquet_from_db("wealth_engine", WEALTH_PATH)

        prev_wealth_df = pd.DataFrame()
        if os.path.exists(WEALTH_PATH):
            try:
                prev_wealth_df = pd.read_parquet(WEALTH_PATH)
            except Exception as e:
                logger.exception("Failed to load prev_wealth_df")

        # ── PREP LAYER 1 (Candidates) ──
        df = pd.read_parquet(WATCHLIST_PATH)
        df = df.drop_duplicates(subset=["Stock"]).reset_index(drop=True)
        candidate_symbols = set(df["Stock"].astype(str).tolist()) if "Stock" in df.columns else set()
        
        # [VERSION: SCANNER_DIAG_LOG_v1.0] Watchlist fingerprint for cross-run comparison
        import hashlib
        _wl_stocks = sorted(list(candidate_symbols))
        _wl_hash = hashlib.md5("|".join(_wl_stocks).encode()).hexdigest()[:12]
        logger.info(f"📋 [WEALTH ENGINE] Watchlist fingerprint: {len(candidate_symbols)} stocks | hash={_wl_hash}")

        # ── PREP LAYER 3 (Orphan Open Positions) ──
        portfolio_dict = {}
        try:
            from database import get_connection
            from psycopg2.extras import RealDictCursor
            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT symbol, entry_price, added_at::date AS entry_date
                        FROM manual_portfolio
                    """)
                    for r in cur.fetchall():
                        portfolio_dict[r["symbol"]] = {"entry_price": r["entry_price"], "entry_date": r["entry_date"]}
                    
                    cur.execute("""
                        SELECT symbol, alert_price AS entry_price, alert_date::date AS entry_date
                        FROM wealth_buy_alert
                        WHERE is_closed = FALSE
                    """)
                    for r in cur.fetchall():
                        portfolio_dict[r["symbol"]] = {"entry_price": r["entry_price"], "entry_date": r["entry_date"]}
        except Exception as e:
            logger.warning(f"Failed to load active portfolio prices: {e}")
            
        open_symbols = list(portfolio_dict.keys())
        orphan_symbols = [sym for sym in open_symbols if sym not in candidate_symbols]

        logger.info(f"💰 [WEALTH ENGINE] Candidates: {len(df)} | Orphans: {len(orphan_symbols)}")

        # [PERFORMANCE FIX] Pre-fetch sector peer medians in the background while fetching price history.
        # This overlaps the heavy TradingView API call and local pickle parsing.
        peer_medians_thread = None
        # ── DATA FETCH (Candidates + Orphans) ──
        nifty_6m_ret, nifty_dist_52w = fetch_nifty_macro_state()
        if nifty_6m_ret is None:
            logger.info("Nifty Macro: UNAVAILABLE — rs_6m running in absolute-return proxy mode (rs_is_absolute_proxy=True)")
        else:
            logger.info(f"💰 [WEALTH ENGINE] Nifty 6M Return: {nifty_6m_ret:.1f}%")

        rejection_counts = {}
        import threading
        _rejection_lock = threading.Lock()

        all_symbols_to_fetch = list(candidate_symbols.union(set(orphan_symbols)))
        BATCH_SIZE = int(os.environ.get("WEALTH_BATCH_SIZE", "50"))
        logger.info(f"💰 [WEALTH ENGINE] Processing {len(all_symbols_to_fetch)} symbols in chunks of {BATCH_SIZE}...")

        if all_symbols_to_fetch:
            from valuation_utils import compute_peer_medians
            prefetch_symbols = list(all_symbols_to_fetch)
            
            def prefetch_medians():
                t_name = threading.current_thread().name
                try:
                    logger.info(f"🚀 [BACKGROUND WORKER START] Worker='{t_name}' | InitiatedBy='WealthEngineMain' | Action='Pre-fetching sector peer medians for {len(prefetch_symbols)} symbols'")
                    _t_start = time.perf_counter()
                    res = compute_peer_medians(prefetch_symbols)
                    dur_s = time.perf_counter() - _t_start
                    logger.info(f"✅ [BACKGROUND WORKER COMPLETE] Worker='{t_name}' | Action='Pre-fetch peer medians' | SymbolsProcessed={len(res)} | Duration={dur_s:.2f}s")
                except Exception as ex:
                    logger.warning(f"⚠️ [BACKGROUND WORKER FAIL] Worker='{t_name}' | Action='Pre-fetch peer medians' | Error={ex}")
            
            peer_medians_thread = threading.Thread(target=prefetch_medians, name="PeerMediansPrefetch", daemon=True)
            peer_medians_thread.start()

        
        from price_cache import fetch_unified_historical, get_intraday_snapshot
        from database import get_bulk_recent_concall_analysis
        from memory_profiler import chunk_iterable, BatchMemoryTracker, MemoryProfiler
        import concurrent.futures
        
        global_fetched_count = 0
        technicals = []
        total_batches = (len(all_symbols_to_fetch) + BATCH_SIZE - 1) // BATCH_SIZE

        def process_symbol(idx, sym, historical_cache=None, concall_cache=None):
            try:
                tech = calculate_wealth_technicals(sym, nifty_6m_ret, historical_cache=historical_cache)
                if tech.get("cmp") is None and not prev_wealth_df.empty and sym in prev_wealth_df["Stock"].values:
                    prev_row = prev_wealth_df[prev_wealth_df["Stock"] == sym].iloc[0]
                    tech["cmp"] = prev_row.get("cmp")
                    tech["sma_50"] = prev_row.get("sma_50")
                    tech["sma_200"] = prev_row.get("sma_200")
                    tech["rs_6m"] = prev_row.get("rs_6m")
                    tech["rs_is_absolute_proxy"] = prev_row.get("rs_is_absolute_proxy", (nifty_6m_ret is None))
                    tech["dist_52w_high"] = prev_row.get("dist_52w_high")
                    tech["liquidity"] = prev_row.get("liquidity", 0.0)
                    tech["RSI"] = prev_row.get("RSI", 50.0)
                    tech["ATR_Pct"] = prev_row.get("ATR_Pct", 0.0)
                    tech["momentum_score"] = prev_row.get("momentum_score", 0)
                    tech["momentum_confidence"] = prev_row.get("momentum_confidence", "LOW")
                    tech["used_fallback_data"] = True
                    tech["data_quality"] = "CACHED_PREV_DAY"
                    tech["fallback_timestamp"] = prev_row.get("fallback_timestamp", datetime.now(IST).isoformat())
                    with _rejection_lock:
                        rejection_counts["stale_data"] = rejection_counts.get("stale_data", 0) + 1
                elif tech.get("is_stale"):
                    tech["used_fallback_data"] = True
                    tech["data_quality"] = "STALE_INTRADAY"
                    tech["fallback_timestamp"] = datetime.now(IST).isoformat()
                    with _rejection_lock:
                        rejection_counts["stale_data"] = rejection_counts.get("stale_data", 0) + 1
                elif tech.get("cmp") is None:
                    with _rejection_lock:
                        rejection_counts["no_data"] = rejection_counts.get("no_data", 0) + 1
                    return {"Stock": sym}
                else:
                    tech["used_fallback_data"] = False
                    tech["fallback_timestamp"] = None
                    
                tech["Stock"] = sym
                tech["Promoter_Pledge"] = None
                
                try:
                    if concall_cache is not None:
                        concall = concall_cache.get(sym)
                    else:
                        concall = {}
                    tech["AI_Confidence"] = int(concall["management_confidence"]) if concall and "management_confidence" in concall else 0
                except Exception:
                    tech["AI_Confidence"] = 0
                return tech
            except Exception as e:
                # [FIX-W3] Log full traceback so per-symbol failures are never silently swallowed.
                logger.exception(f"❌ [WEALTH ENGINE] process_symbol failed for {sym}: {e}")
                with _rejection_lock:
                    rejection_counts["processing_error"] = rejection_counts.get("processing_error", 0) + 1
                return {"Stock": sym}

        stage_tracker.end_stage(f"Found {len(candidate_symbols)} candidates, {len(portfolio_dict)} portfolio positions")
        stage_tracker.start_stage(2, "Live Quote CMP Fetch (Upstox)", f"Target: {len(all_symbols_to_fetch)} symbols")

        # [VERSION: WEALTH_SPEEDUP_v1.0] Replace 5m snapshot bulk fetch with direct live_prices fetch.
        # Fetching 1D of 5m historical data for 300 stocks took too long and defeated the purpose
        # of just getting the current CMP delta. We now use UnifiedFetcher directly.
        # [VERSION: PERF_PHASE0_v1.0] Stage timing: live CMP fetch
        logger.info(f"💰 [WEALTH ENGINE] Fetching live CMP for {len(all_symbols_to_fetch)} symbols...")
        _t_live = time.perf_counter()
        try:
            from live_prices import get_live_prices
            all_live_prices = get_live_prices(list(all_symbols_to_fetch)) or {}
            log_api_cost("live_quotes", cache_hit=False)
        except Exception as _snap_e:
            logger.warning(f"⚠️ [WEALTH ENGINE] Live price fetch failed: {_snap_e}. Falling back to 1D close.")
            all_live_prices = {}
        _stage_ms_live = (time.perf_counter() - _t_live) * 1000
        logger.info(f"⏱ [STAGE] live_quote_fetch: {_stage_ms_live:.0f}ms for {len(all_symbols_to_fetch)} symbols")
        stage_tracker.end_stage(f"Fetched CMP for {len(all_live_prices)} symbols")

        stage_tracker.start_stage(3, "Historical Candle & Concall Acquisition", f"Pre-fetching 1D candles for {len(all_symbols_to_fetch)} symbols")

        # [VERSION: WEALTH_PREFETCH_OPT_v1.0] Pre-fetch concall data for ALL symbols once.
        # Previously caused 7 separate DB round-trips (one per batch). Single bulk query is faster.
        # [VERSION: PERF_PHASE0_v1.0] Stage timing: concall DB prefetch
        logger.info(f"💰 [WEALTH ENGINE] Pre-fetching concall cache for {len(all_symbols_to_fetch)} symbols...")
        _t_concall = time.perf_counter()
        try:
            all_concalls = get_bulk_recent_concall_analysis(all_symbols_to_fetch, max_age_days=60) or {}
        except Exception as _concall_e:
            logger.warning(f"⚠️ [WEALTH ENGINE] Concall pre-fetch failed: {_concall_e}. AI_Confidence defaults to 0.")
            all_concalls = {}
        _stage_ms_concall = (time.perf_counter() - _t_concall) * 1000
        logger.info(f"⏱ [STAGE] concall_prefetch: {_stage_ms_concall:.0f}ms for {len(all_symbols_to_fetch)} symbols")

        # [VERSION: BULK_PREFETCH_OPT_v1.0] Single-pass bulk fetch for all watchlist symbols.
        # PriceCache handles provider-level batching internally while populating per-symbol RAM cache.
        logger.info(f"💰 [WEALTH ENGINE] Pre-fetching 1D historical data for {len(all_symbols_to_fetch)} symbols...")
        _t_hist_bulk = time.perf_counter()
        all_historical_data = fetch_unified_historical(list(all_symbols_to_fetch), period="1y", interval="1d", requester="WEALTH_ENGINE_1D") or {}
        _stage_ms_hist_bulk = (time.perf_counter() - _t_hist_bulk) * 1000
        logger.info(f"⏱ [STAGE] 1D bulk_historical_fetch: {_stage_ms_hist_bulk:.0f}ms for {len(all_symbols_to_fetch)} symbols")
        stage_tracker.end_stage(f"Acquired {len(all_historical_data)} historical dataframes")

        stage_tracker.start_stage(4, "Indicator Calculation & Scoring", f"Workers={SCAN_WORKER_THREADS if 'SCAN_WORKER_THREADS' in locals() or 'SCAN_WORKER_THREADS' in globals() else 3}")

        _t_hist_total = time.perf_counter()
        _t_indicator_total_ms = 0.0
        for batch_num, chunk in enumerate(chunk_iterable(all_symbols_to_fetch, BATCH_SIZE), start=1):
            with BatchMemoryTracker("WealthPhaseA", batch_num, total_batches, len(chunk), collect_gc=True) as tracker:
                # Slice chunk historical data from bulk pre-fetched dictionary
                chunk_historical_data = {sym: all_historical_data[sym] for sym in chunk if sym in all_historical_data}

                # Slice from pre-fetched dicts — no additional API/DB calls per batch
                chunk_live_prices = {sym: all_live_prices.get(sym) for sym in chunk}
                chunk_concalls  = {sym: all_concalls.get(sym)  for sym in chunk}
                
                if chunk_historical_data is None:
                    chunk_historical_data = {}
                else:
                    # [ARCHITECTURAL FIX] Stitch live intraday price into 1D historical data 
                    # so 1D delta fetches aren't spammed every 5 minutes during market hours.
                    now_ist = datetime.now(IST)
                    today_date_str = now_ist.strftime("%Y-%m-%d")
                    from market_utils import is_market_open
                    is_mkt_open = is_market_open(now_ist)
                    
                    for sym, hist_df in chunk_historical_data.items():
                        if isinstance(hist_df, pd.DataFrame) and not hist_df.empty:
                            live_price = chunk_live_prices.get(sym)
                            if live_price and float(live_price) > 0:
                                live_price = float(live_price)
                                
                                last_dt = hist_df.index[-1] if not hist_df.index.empty else None
                                t_col = 'Date' if 'Date' in hist_df.columns else ('Datetime' if 'Datetime' in hist_df.columns else None)
                                if t_col:
                                    last_dt = hist_df[t_col].iloc[-1]
                                
                                last_dt_ts = pd.to_datetime(last_dt)
                                last_dt_str = last_dt_ts.strftime("%Y-%m-%d") if last_dt else ""
                                
                                # Quote Timestamp Validation
                                if not is_mkt_open and last_dt_ts.date() >= now_ist.date():
                                    pass
                                elif last_dt_str == today_date_str:
                                    # Update today's existing candle (preserve Open, update High/Low/Close)
                                    hist_df = hist_df.copy()
                                    curr_high = float(hist_df['High'].iloc[-1])
                                    curr_low = float(hist_df['Low'].iloc[-1])
                                    idx = hist_df.index[-1]
                                    hist_df.at[idx, 'High'] = max(curr_high, live_price)
                                    hist_df.at[idx, 'Low']  = min(curr_low, live_price)
                                    hist_df.at[idx, 'Close'] = live_price
                                    chunk_historical_data[sym] = hist_df
                                else:
                                    # Append a new live candle for today
                                    hist_df = hist_df.copy()
                                    new_row = hist_df.iloc[-1:].copy()
                                    new_dt = pd.to_datetime(today_date_str).tz_localize(IST)
                                    if t_col:
                                        new_row[t_col] = new_dt
                                    else:
                                        new_row.index = [new_dt]
                                    new_row['Open']  = live_price
                                    new_row['High']  = live_price
                                    new_row['Low']   = live_price
                                    new_row['Close'] = live_price
                                    hist_df = pd.concat([hist_df, new_row])
                                    chunk_historical_data[sym] = hist_df
                    
                valid_fetches = sum(1 for v in chunk_historical_data.values() if isinstance(v, pd.DataFrame) and not v.empty)
                global_fetched_count += valid_fetches
                rows_fetched = sum(len(df) for df in chunk_historical_data.values() if isinstance(df, pd.DataFrame))
                
                tracker.mark_fetch_complete(row_count=rows_fetched)
                
                # [VERSION: PERF_PHASE0_v1.0 & PHASE2_v1.0] Stage timing: indicator calc per symbol
                from config import FEATURE_PARALLEL_SCANNERS_V1, SCAN_WORKER_THREADS
                if FEATURE_PARALLEL_SCANNERS_V1 and len(chunk) > 1:
                    _t_parallel = time.perf_counter()
                    with concurrent.futures.ThreadPoolExecutor(max_workers=SCAN_WORKER_THREADS, thread_name_prefix="WealthWorker") as executor:
                        future_to_sym = {
                            executor.submit(process_symbol, i, sym, chunk_historical_data, chunk_concalls): sym
                            for i, sym in enumerate(chunk)
                        }
                        for future in concurrent.futures.as_completed(future_to_sym):
                            sym = future_to_sym[future]
                            try:
                                result = future.result()
                                technicals.append(result)
                            except Exception as e:
                                logger.error(f"❌ Error processing symbol {sym} in parallel: {e}")
                    _parallel_ms = (time.perf_counter() - _t_parallel) * 1000
                    logger.info(f"⚡ [PARALLEL_SCANNER] Batch {batch_num}/{total_batches}: processed {len(chunk)} symbols in {_parallel_ms:.1f}ms (workers={SCAN_WORKER_THREADS})")
                else:
                    for i, sym in enumerate(chunk):
                        try:
                            _t_sym = time.perf_counter()
                            result = process_symbol(i, sym, chunk_historical_data, chunk_concalls)
                            technicals.append(result)
                            _t_indicator_total_ms += (time.perf_counter() - _t_sym) * 1000
                        except Exception as e:
                            logger.error(f"❌ Error processing symbol {sym}: {e}")
                    
                # Explicit cleanup of large DataFrame references
                del chunk_historical_data
                del chunk_live_prices
                del chunk_concalls
                logger.info(f"⏳ [WEALTH ENGINE] Evaluated Batch {batch_num}/{total_batches} ({min(batch_num * BATCH_SIZE, len(all_symbols_to_fetch))}/{len(all_symbols_to_fetch)} stocks) | Technicals evaluated so far: {len(technicals)}")

        required_count = int(len(df) * 0.70)
        if global_fetched_count < required_count:
            exact_reason = ""
            try:
                from data_providers.fyers_fetcher import _fyers_circuit_breaker
                from data_provider import _price_provider
                import time
                if _fyers_circuit_breaker.is_open:
                    exact_reason += "Fyers Circuit Breaker OPEN. "
                if _price_provider.cooldown_until > time.time():
                    exact_reason += f"YFinance Circuit Breaker OPEN ({int(_price_provider.cooldown_until - time.time())}s). "
            except Exception:
                pass
            
            error_details = exact_reason if exact_reason else "Unknown APIs fail / no cache available"
            logger.error(f"❌ INCOMPLETE DATA: Fetched {global_fetched_count}/{len(df)} symbols. Aborting to protect dashboard. {error_details}")
            
            if not getattr(database, "DONT_SAVE_WEALTH", False):
                try:
                    upsert_scanner_health("Wealth Engine", "DOWN", error_msg=f"Data fetch failed: {global_fetched_count}/{len(df)}")
                    from database import insert_notification
                    insert_notification("error", "⚠️ WEALTH ENGINE DEGRADED", f"Data fetched for only {global_fetched_count}/{len(df)} symbols.\nReason: {error_details}")
                except Exception:
                    pass
            return pd.DataFrame()

        tech_df = pd.DataFrame(technicals)
        
        # Per-symbol quality set tracking for candidate vs orphan degradation
        candidate_stale_symbols = set()
        candidate_missing_symbols = set()
        orphan_stale_symbols = set()
        orphan_missing_symbols = set()

        for item in technicals:
            sym = item.get("Stock")
            if not sym: continue
            is_cand = sym in candidate_symbols
            data_q = item.get("data_quality")
            if data_q in ["CACHED_PREV_DAY", "STALE_INTRADAY"] or item.get("used_fallback_data"):
                if is_cand: candidate_stale_symbols.add(sym)
                else: orphan_stale_symbols.add(sym)
            elif item.get("cmp") is None:
                if is_cand: candidate_missing_symbols.add(sym)
                else: orphan_missing_symbols.add(sym)
        
        # Memory release
        del technicals
        del all_live_prices
        del all_concalls
        import gc; gc.collect()
        
        if not tech_df.empty and "cmp" in tech_df.columns and (tech_df["cmp"].isnull().all() or (tech_df["cmp"] == 0).all()):
            logger.error("❌ API returned 0 prices. Rate limited.")
            if not getattr(database, "DONT_SAVE_WEALTH", False):
                try:
                    upsert_scanner_health("Wealth Engine", "DOWN", error_msg="CRITICAL: API rate limited.")
                except Exception:
                    pass
            return
            
        # =====================================================================================
        # EXECUTE LAYER 1: CANDIDATE SELECTION
        # =====================================================================================
        _prof_l1 = MemoryProfiler("Wealth: Candidate Selection").__enter__()
        # wealth_df consists ONLY of the fundamental watchlist candidates joined with technicals
        candidate_tech = tech_df[tech_df["Stock"].isin(candidate_symbols)]
        wealth_df = pd.merge(df, candidate_tech, on="Stock", how="left")
        
        if peer_medians_thread is not None:
            logger.info("⏱ [WEALTH ENGINE] Waiting for background peer medians thread to complete...")
            peer_medians_thread.join(timeout=15.0) # [PERF OPT] Reduced from 180s to 15s to prevent long scanner stalls
            if peer_medians_thread.is_alive():
                logger.warning("⚠️ [WEALTH ENGINE] Background peer medians thread timed out after 15s. Continuing with cached/available data.")
        
        from valuation_utils import compute_peer_medians
        sector_stats = compute_peer_medians(wealth_df["Stock"].tolist() if not wealth_df.empty else [])
        
        wealth_df = evaluate_candidates(wealth_df, sector_stats, nifty_dist_52w)
        
        _prof_l1.__exit__(None, None, None)
        
        # =====================================================================================
        # EXECUTE LAYER 2: ENTRY TIMING
        # =====================================================================================
        _prof_l2 = MemoryProfiler("Wealth: Entry Timing").__enter__()
        BUY_GATE_ACTIVE = False
        suppression_reason = None
        
        candidate_degraded_count = len(candidate_stale_symbols | candidate_missing_symbols)
        fresh_ratio = 1.0 - (candidate_degraded_count / max(len(candidate_symbols), 1))
        MIN_WEALTH_FRESH_RATIO = float(os.environ.get("MIN_WEALTH_FRESH_RATIO", "0.95"))
        
        breadth_pct = None
        if not candidate_tech.empty and "above_sma200" in candidate_tech.columns:
            valid_sma200 = candidate_tech["above_sma200"].dropna()
            if len(valid_sma200) >= 10:
                above_200 = valid_sma200.sum()
                breadth_pct = (above_200 / len(valid_sma200)) * 100.0
            else:
                logger.warning(f"⚠️ Market breadth calculation skipped — insufficient valid sample size ({len(valid_sma200)} < 10)")
            
        if nifty_dist_52w is not None and nifty_dist_52w > 20:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Nifty {nifty_dist_52w:.1f}% below 52W high"
        elif nifty_6m_ret is not None and nifty_6m_ret < -15:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Nifty 6M return {nifty_6m_ret:.1f}%"
        elif breadth_pct is not None and breadth_pct < 30:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Breadth weak: {breadth_pct:.1f}% above SMA200"
        elif fresh_ratio < MIN_WEALTH_FRESH_RATIO:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Fresh candidate data only {fresh_ratio*100:.1f}% (< {MIN_WEALTH_FRESH_RATIO*100:.0f}% required)"
            if not getattr(database, "DONT_SAVE_WEALTH", False):
                try:
                    upsert_scanner_health("Wealth Engine", "DEGRADED", error_msg=f"Data stale ({fresh_ratio*100:.1f}% fresh). Signals suppressed.")
                    from telegram_engine import send_telegram_message
                    msg = f"🚨 <b>Wealth Engine Degraded</b>\nSignals suppressed due to missing/stale data.\nFresh ratio: {fresh_ratio*100:.1f}%"
                    send_telegram_message(msg)
                    from push_service import send_push_to_all
                    send_push_to_all("⚠️ Wealth Engine Degraded", f"Signals suppressed. Data freshness dropped to {fresh_ratio*100:.1f}%")
                except Exception:
                    pass
            
        wealth_df = generate_entry_signal(wealth_df, BUY_GATE_ACTIVE, suppression_reason, open_symbols)
        
        # Persist BUY Signals
        saved_alerts_count = 0
        try:
            from database import save_wealth_buy_alert, DONT_SAVE_WEALTH
            buy_signals = wealth_df[wealth_df["Signal_Code"] == "BUY"]
            # [FIX-W6] Read admission state ONCE before the loop to prevent race-condition inconsistency
            # where some signals are saved and others blocked within the same run.
            try:
                from config import get_wealth_admission_state
                allow_new_admissions = get_wealth_admission_state()
            except Exception:
                allow_new_admissions = True
            for _, row in buy_signals.iterrows():
                if row.get("used_fallback_data", False) or not row.get("candidate_complete_for_buy", False):
                    continue
                if not allow_new_admissions:
                    continue
                    
                symbol = row.get("Stock")
                cmp = row.get("cmp")
                if symbol and cmp and not DONT_SAVE_WEALTH:
                    # [VERSION: SCANNER_DIAG_LOG_v1.0] Log full diagnostic for every triggered trade
                    _last_bar_date = "unknown"
                    if "fallback_timestamp" in row and row["fallback_timestamp"]:
                        _last_bar_date = str(row["fallback_timestamp"])[:10]
                    elif not row.get("used_fallback_data", False):
                        _last_bar_date = "live/cache"
                        
                    logger.info(
                        f"📍 PICKED [WEALTH ENGINE: BUY SIGNAL]: {symbol} @ ₹{cmp:.2f} | "
                        f"fm_score={row.get('FM_Score', 0):.1f} | mom={row.get('momentum_score', 0)} | "
                        f"bucket={row.get('Portfolio_Bucket', 'Unknown')} | entry=₹{cmp:.2f} | last_bar={_last_bar_date}"
                    )
                    
                    if is_test_mode:
                        logger.info(f"🧪 [TEST MODE] Skipping save_wealth_buy_alert for {symbol}")
                        inserted = True
                    else:
                        inserted = save_wealth_buy_alert(
                            symbol, cmp, breakout_type="Strength" if row.get("dist_52w_high", 100) > 5 else "Value", 
                            fm_score=row.get("FM_Score"), position_pct=row.get("position_pct"),
                            position_amount=row.get("position_amount"), position_shares=max(1, int(row.get("position_amount", 0) / cmp)) if cmp > 0 else 0,
                            portfolio_bucket=row.get("Portfolio_Bucket", "Unknown"), valuation_score=row.get("Valuation_Score", 0),
                            momentum_score=row.get("momentum_score"), momentum_confidence=row.get("momentum_confidence"),
                            data_quality=row.get("data_quality"), fallback_timestamp=row.get("fallback_timestamp"),
                            engine_version=ACTIVE_ALGO_VERSION, config_version=json.dumps({"WEALTH_ENGINE_ENABLED": True})
                        )
                    if not inserted:
                        logger.info(f"REJECTION: {symbol} (Phase: DB_IDEMPOTENCY, Reason: Rejected by DB Idempotency/Guard)")
                        wealth_df.loc[wealth_df["Stock"] == symbol, "Signal_Code"] = "SUPPRESSED"
                        wealth_df.loc[wealth_df["Stock"] == symbol, "Signal_Reason"] = "Rejected by DB Idempotency/Guard"
                        wealth_df.loc[wealth_df["Stock"] == symbol, "Signal"] = "SUPPRESSED (Rejected by DB)"
                    else:
                        saved_alerts_count += 1

        except Exception:
            # [FIX-W2] Always log full traceback — never swallow BUY alert persistence failures silently.
            logger.exception("❌ [WEALTH ENGINE] BUY alert persistence block failed. Alerts may not have been saved.")

        passed_layer1 = len(wealth_df[wealth_df["Portfolio_Bucket"] != "REVIEW"]) if "Portfolio_Bucket" in wealth_df.columns else 0
        logger.info(
            f"=== [WEALTH ENGINE PIPELINE SUMMARY] ===\n"
            f"Universe Loaded: {len(candidate_symbols)}\n"
            f"Historical Data Fetched: {global_fetched_count}\n"
            f"Passed Layer 1 (Fundamentals & Scoring): {passed_layer1}\n"
            f"Passed Layer 2 (Technical Entry Gates): {len(buy_signals) if 'buy_signals' in locals() else 0}\n"
            f"Alerts Persisted to DB: {saved_alerts_count}\n"
            f"=========================================="
        )

        _prof_l2.__exit__(None, None, None)

        # =====================================================================================
        # EXECUTE LAYER 3: PORTFOLIO MANAGEMENT
        # =====================================================================================
        _prof_l3 = MemoryProfiler("Wealth: Portfolio Mgmt").__enter__()
        # Extract open positions into a separate dataframe
        portfolio_rows = []
        
        # 1. FETCH REAL-TIME PRICES BEFORE EVALUATION
        realtime_metrics = {}
        try:
            if open_symbols:
                from live_prices import get_live_prices
                realtime_metrics = get_live_prices(open_symbols)
        except Exception as e:
            logger.warning(f"Failed to fetch real-time prices for wealth engine evaluation: {e}")
            
        for sym, p_info in portfolio_dict.items():
            if sym in wealth_df["Stock"].values:
                row = wealth_df[wealth_df["Stock"] == sym].iloc[0].to_dict()
            elif sym in tech_df["Stock"].values:
                row = tech_df[tech_df["Stock"] == sym].iloc[0].to_dict()
            else:
                row = {"Stock": sym}
            row["entry_price"] = p_info["entry_price"]
            row["entry_date"] = p_info["entry_date"]
            
            # INJECT REAL-TIME PRICE SO EXIT MONITOR SEES LIVE CRASHES
            if sym in realtime_metrics:
                row["cmp"] = realtime_metrics[sym]
                row["used_fallback_data"] = False
                
            portfolio_rows.append(row)
            
        portfolio_df = pd.DataFrame(portfolio_rows)
        portfolio_df = evaluate_open_positions(portfolio_df, portfolio_dict)
        
        if not portfolio_df.empty:
            # Map Portfolio outputs back into wealth_df for Dashboard display (Actual position closing is handled by dedicated WEALTH_EXIT monitor)
            port_map = portfolio_df.set_index("Stock")[["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]].to_dict('index')
            def map_port(r):
                sym = r["Stock"]
                if sym in port_map:
                    r["Hold_Score"] = port_map[sym]["Hold_Score"]
                    r["hold_trend"] = port_map[sym]["hold_trend"]
                    # Priority: Exit signals > Entry signals
                    if port_map[sym].get("Exit_Code"):
                        r["Signal_Code"] = port_map[sym]["Exit_Code"]
                        r["Signal_Reason"] = port_map[sym]["Exit_Reason"]
                        r["Signal"] = f"{r['Signal_Code']} ({r['Signal_Reason']})" if r['Signal_Reason'] else r['Signal_Code']
                return r
            wealth_df = wealth_df.apply(map_port, axis=1)
            
            # Append orphaned holdings back into wealth_df for dashboard visibility
            orphan_df = portfolio_df[~portfolio_df["Stock"].isin(wealth_df["Stock"])].copy()
            if not orphan_df.empty:
                orphan_df["Signal_Code"] = orphan_df["Exit_Code"]
                orphan_df["Signal_Reason"] = orphan_df["Exit_Reason"]
                orphan_df["Signal"] = orphan_df.apply(lambda x: f"{x['Signal_Code']} ({x['Signal_Reason']})" if x.get('Signal_Reason') else x.get('Signal_Code', ''), axis=1)
                wealth_df = pd.concat([wealth_df, orphan_df], ignore_index=True)
                
            # DB Persistence
            try:
                for symbol in open_symbols:
                    if symbol not in port_map: continue
                    current_score = port_map[symbol].get("Hold_Score")
                    if not is_test_mode and not getattr(database, "DONT_SAVE_WEALTH", False):
                        from database import save_hold_score_history
                        p_row = portfolio_df[portfolio_df["Stock"] == symbol].iloc[0]
                        save_hold_score_history(
                            symbol=symbol, hold_score=current_score, fm_score=_safe_num(p_row.get("FM_Score", 0)),
                            rs_6m=_safe_num(p_row.get("rs_6m", 0)), cmp=_safe_num(p_row.get("cmp", 0)), sma_200=_safe_num(p_row.get("sma_200", 0))
                        )
                        
                if realtime_metrics and not is_test_mode and not getattr(database, "DONT_SAVE_WEALTH", False):
                    from database import update_position_real_time_prices
                    update_position_real_time_prices({s: {"price": p, "score": port_map.get(s, {}).get("Hold_Score")} for s, p in realtime_metrics.items()})
            except Exception as _rt_e: logger.exception(f"Error updating real-time prices: {_rt_e}")
            
        _prof_l3.__exit__(None, None, None)
        
        # Final Dashboard Export
        _prof_l4 = MemoryProfiler("Wealth: Dashboard Export").__enter__()
        if not is_test_mode and not getattr(database, "DONT_SAVE_WEALTH", False):
            try:
                cols = ['Stock', 'Sector', 'FM_Score', 'Consistency_Score', 'Valuation_Score', 'Reliability', 'Base_FV', 'Bull_FV', 'Portfolio_Bucket', 'Signal', 'Hold_Score', 'hold_trend', 'Core_Selected']
                
                # Bulk assign missing columns to prevent DataFrame block fragmentation
                new_cols = {c: None for c in cols if c not in wealth_df.columns}
                if new_cols:
                    wealth_df = wealth_df.assign(**new_cols)
                    
                wealth_df.to_parquet(WEALTH_PATH)
                
                def bg_db_sync():
                    global _last_parquet_upload
                    try:
                        import time
                        from database import upload_parquet_to_db, upsert_scanner_health
                        now = time.time()
                        if now - _last_parquet_upload > 3600:
                            upload_parquet_to_db("wealth_engine", WEALTH_PATH)
                            _last_parquet_upload = now
                            
                        duration_sec = round(time.time() - start_time, 1)
                        upsert_scanner_health(
                            scanner_name="Wealth Engine", status="OK", last_success=datetime.now(IST).isoformat(),
                            today_alerts=len(wealth_df[wealth_df["Signal_Code"] == "BUY"]), total_count=len(wealth_df),
                            duration_seconds=duration_sec
                        )
                    except Exception as _sh_e: logger.exception(f"Error in bg_db_sync: {_sh_e}")

                from database import submit_background_upload
                submit_background_upload(bg_db_sync)
                
                # Free large intermediate dataframes
                del tech_df, candidate_tech, prev_wealth_df
                
            except Exception as _sh_e: logger.exception(f"Error initiating dashboard export: {_sh_e}")
            
        _prof_l4.__exit__(None, None, None)

        # ── WEALTH ENGINE MANDATORY PIPELINE SUMMARY LOG ──
        total_eval = len(all_symbols_to_fetch)
        buy_count = len(wealth_df[wealth_df["Signal_Code"] == "BUY"]) if "Signal_Code" in wealth_df.columns else 0
        core_count = len(wealth_df[wealth_df["Portfolio_Bucket"] == "CORE"]) if "Portfolio_Bucket" in wealth_df.columns else 0
        watch_count = len(wealth_df[wealth_df["Portfolio_Bucket"] == "WATCH"]) if "Portfolio_Bucket" in wealth_df.columns else 0
        review_count = len(wealth_df[wealth_df["Portfolio_Bucket"] == "REVIEW"]) if "Portfolio_Bucket" in wealth_df.columns else 0
        
        stale_count = rejection_counts.get("stale_data", 0)
        no_data_count = rejection_counts.get("no_data", 0)
        fresh_count = max(0, total_eval - stale_count - no_data_count)
        data_status = "DEGRADED (Stale Data > 10%)" if (stale_count / max(total_eval, 1)) > 0.10 else "OK"
        duration_sec = round(time.time() - start_time, 1)

        summary_lines = [
            "======================================================================",
            "=== [WEALTH ENGINE PIPELINE SUMMARY] ===",
            "======================================================================",
            "📊 DATA QUALITY SNAPSHOT:",
            f"  • Total Watchlist Requested : {total_eval}",
            f"  • Fresh Data OK             : {fresh_count}",
            f"  • Stale Data                : {stale_count}",
            f"  • Missing / No Data         : {no_data_count}",
            f"  • Data Health Status        : {data_status}",
            "",
            "🎯 CRITERIA & FILTER BREAKDOWN:"
        ]
        for k, v in rejection_counts.items():
            if v > 0:
                summary_lines.append(f"  • {k:<27}: {v}")

        summary_lines.extend([
            "",
            "🏆 FINAL OUTCOME:",
            f"  • BUY Signals Generated     : {buy_count}",
            f"  • Bucket Allocation         : CORE={core_count} | WATCH={watch_count} | REVIEW={review_count}",
            f"  • Total Execution Time      : {duration_sec}s",
            "======================================================================"
        ])
        logger.info("\n".join(summary_lines))
        try:
            stage_tracker.end_stage(f"BUY signals: {buy_count} | Processed: {total_eval} symbols")
            stage_tracker.print_summary(alerts_found=buy_count)
        except Exception:
            pass


        try:
            from memory_profiler import run_purge_with_telemetry
            run_purge_with_telemetry("Wealth Engine Complete")
        except Exception as me:
            logger.debug(f"Wealth Engine memory purge failed: {me}")

        logger.info(f"✅ [STOP] WEALTH ENGINE COMPLETED | {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')}")
        # [VERSION: PERF_PHASE0_v1.0] Human-readable stage summary for log-based verification
        try:
            _hist_total_ms   = (time.perf_counter() - _t_hist_total) * 1000 if '_t_hist_total' in dir() else 0
            _live_ms         = _stage_ms_live if '_stage_ms_live' in dir() else 0
            _concall_ms      = _stage_ms_concall if '_stage_ms_concall' in dir() else 0
            _indicator_ms    = _t_indicator_total_ms if '_t_indicator_total_ms' in dir() else 0
            _sym_count       = len(all_symbols_to_fetch) if 'all_symbols_to_fetch' in dir() else 0
            _total_s         = time.time() - start_time
            _total_ms        = _total_s * 1000
            logger.info(
                "\n"
                "┌─────────────────────────────────────────────────────────────────┐\n"
                "│           📊 WEALTH ENGINE — PERF STAGE SUMMARY (Phase 0)       │\n"
                "├──────────────────────────────────────┬──────────────────────────┤\n"
                f"│  Symbols processed                   │  {_sym_count:<24} │\n"
                f"│  Total scan time                     │  {_total_s:.1f}s{' '*(22 - len(f'{_total_s:.1f}s'))} │\n"
                "├──────────────────────────────────────┼──────────────────────────┤\n"
                f"│  [STAGE] live_quote_fetch            │  {_live_ms:.0f}ms{' '*(22 - len(f'{_live_ms:.0f}ms'))} │\n"
                f"│  [STAGE] concall_prefetch            │  {_concall_ms:.0f}ms{' '*(22 - len(f'{_concall_ms:.0f}ms'))} │\n"
                f"│  [STAGE] historical_fetch (total)    │  {_hist_total_ms:.0f}ms{' '*(22 - len(f'{_hist_total_ms:.0f}ms'))} │\n"
                f"│  [STAGE] indicator_calc  (total)     │  {_indicator_ms:.0f}ms{' '*(22 - len(f'{_indicator_ms:.0f}ms'))} │\n"
                "├──────────────────────────────────────┼──────────────────────────┤\n"
                f"│  Phase 1 target (live_quote ≤1%)     │  ≤ {_total_ms * 0.01:.0f}ms{' '*(20 - len(f'≤ {_total_ms * 0.01:.0f}ms'))} │\n"
                f"│  live_quote % of total               │  {(_live_ms / _total_ms * 100) if _total_ms else 0:.1f}%{' '*(21 - len(f'{(_live_ms / _total_ms * 100) if _total_ms else 0:.1f}%'))} │\n"
                "└──────────────────────────────────────┴──────────────────────────┘"
            )
        except Exception as _log_e:
            logger.debug(f"Non-critical: Stage summary log failed: {_log_e}")

        # [VERSION: PERF_PHASE0_v1.0] Flush Phase 0 JSON timing report to artifacts/profiling/
        try:
            flush_timing_report(
                phase="Phase0_Baseline",
                run_type="cold_start",
                feature_flags=[],
                extra={
                    "scanner": "WealthEngine",
                    "symbols_processed": _sym_count,
                    "stage_live_quote_ms": round(_live_ms, 1),
                    "stage_concall_prefetch_ms": round(_concall_ms, 1),
                    "stage_historical_fetch_ms": round(_hist_total_ms, 1),
                    "stage_indicator_calc_ms": round(_indicator_ms, 1),
                    "total_scan_s": round(_total_s, 2),
                }
            )
        except Exception as _perf_e:
            logger.debug(f"Non-critical: Failed to write timing report: {_perf_e}")

    except Exception as e:

        logger.exception("❌ CRITICAL ERROR in Wealth Engine")
        import database
        if not getattr(database, "DONT_SAVE_WEALTH", False):
            try:
                from database import upsert_scanner_health, insert_notification
                from push_service import send_push_to_all
                upsert_scanner_health("Wealth Engine", "DOWN", error_msg=str(e))
                insert_notification("admin", f"❌ Wealth Engine CRASHED (DOWN)", f"Error: {str(e)[:200]}")
                send_push_to_all("❌ Wealth Engine DOWN", f"Crash: {str(e)[:100]}")
            except Exception as _fb_e: logger.exception(f"Fallback reporting failed: {_fb_e}")

def run_wealth_intraday_update(is_test_mode=False, write_health=True):
    """
    Lightweight, ultra-fast market-hours update (< 3 seconds) for the 5-minute Wealth Engine schedule.
    Fetches real-time prices for active portfolio holdings, evaluates open position exit rules,
    updates DB position metrics, and refreshes the Wealth Engine dashboard parquet.
    Does NOT re-download or re-calculate full 1Y historical technicals for 300+ symbols.
    """
    start_time = time.time()
    from perf_utils import ScannerStageTracker
    stage_tracker = ScannerStageTracker("WEALTH_INTRADAY_5M")
    logger.info("⚡ [WEALTH ENGINE 5M] Starting lightweight intraday portfolio update...")
    
    try:
        if not os.path.exists(WEALTH_PATH):
            logger.info("⚠️ WEALTH_PATH parquet not found for intraday update. Running full scan once...")
            return run_wealth_scan(is_test_mode=is_test_mode)

        wealth_df = pd.read_parquet(WEALTH_PATH)
        if wealth_df.empty or "Stock" not in wealth_df.columns:
            return run_wealth_scan(is_test_mode=is_test_mode)

        stage_tracker.start_stage(1, "Postgres Portfolio Query", "Querying open holdings from manual_portfolio and wealth_buy_alert")
        portfolio_dict = {}
        try:
            from database import get_connection
            from psycopg2.extras import RealDictCursor
            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT symbol, entry_price, added_at::date AS entry_date FROM manual_portfolio")
                    for r in cur.fetchall():
                        portfolio_dict[r["symbol"]] = {"entry_price": r["entry_price"], "entry_date": r["entry_date"], "source": "MANUAL"}
                    cur.execute("SELECT symbol, alert_price AS entry_price, alert_date::date AS entry_date FROM wealth_buy_alert WHERE is_closed = FALSE")
                    for r in cur.fetchall():
                        portfolio_dict[r["symbol"]] = {"entry_price": r["entry_price"], "entry_date": r["entry_date"], "source": "ALERT"}
        except Exception as _pe:
            logger.warning(f"Failed to load open portfolio: {_pe}")

        open_symbols = list(portfolio_dict.keys())
        stage_tracker.end_stage(f"Loaded {len(open_symbols)} open positions")

        stage_tracker.start_stage(2, "Live Quote CMP Fetch (Upstox)", f"Target: {len(open_symbols)} positions")
        realtime_metrics = {}
        if open_symbols:
            try:
                from live_prices import get_live_prices
                realtime_metrics = get_live_prices(open_symbols) or {}
            except Exception as e:
                logger.warning(f"Failed to fetch live prices for wealth intraday update: {e}")
        stage_tracker.end_stage(f"Fetched CMP for {len(realtime_metrics)} positions")

        stage_tracker.start_stage(3, "Exit Rule & Trailing Stop Loss Evaluation", "Evaluating exit triggers")
        # Update position CMPs and check exit triggers
        portfolio_rows = []
        for sym, p_info in portfolio_dict.items():
            row = wealth_df[wealth_df["Stock"] == sym].iloc[0].to_dict() if sym in wealth_df["Stock"].values else {"Stock": sym}
            row["entry_price"] = p_info["entry_price"]
            row["entry_date"] = p_info["entry_date"]
            row["position_source"] = p_info.get("source", "ALERT")
            if sym in realtime_metrics:
                row["cmp"] = realtime_metrics[sym]
                row["used_fallback_data"] = False
            portfolio_rows.append(row)

        sell_signal_count = 0
        if portfolio_rows:
            portfolio_df = pd.DataFrame(portfolio_rows)
            portfolio_df = evaluate_open_positions(portfolio_df, portfolio_dict)
            if not portfolio_df.empty:
                sell_signals = portfolio_df[portfolio_df["Exit_Code"] == "SELL"]
                sell_signal_count = len(sell_signals)
                for _, row in sell_signals.iterrows():
                    symbol = row.get("Stock")
                    cmp = row.get("cmp")
                    exit_reason = row.get("Exit_Reason")
                    p_source = row.get("position_source", "ALERT")
                    if symbol and cmp:
                        if is_test_mode:
                            logger.info(f"🧪 [TEST MODE] Would close position {symbol} at {cmp} due to {exit_reason} (Source: {p_source})")
                        elif not getattr(database, "DONT_SAVE_WEALTH", False):
                            from database import close_position_atomic
                            close_position_atomic(symbol, cmp, exit_reason, position_source=p_source)

                port_map = portfolio_df.set_index("Stock")[["Hold_Score", "hold_trend", "Exit_Code", "Exit_Reason"]].to_dict('index')
                for sym, info in port_map.items():
                    if sym in wealth_df["Stock"].values:
                        idx = wealth_df[wealth_df["Stock"] == sym].index
                        if "Hold_Score" in wealth_df.columns:
                            wealth_df.loc[idx, "Hold_Score"] = info.get("Hold_Score")
                        if "hold_trend" in wealth_df.columns:
                            wealth_df.loc[idx, "hold_trend"] = info.get("hold_trend")
                        if info.get("Exit_Code") and "Signal_Code" in wealth_df.columns:
                            wealth_df.loc[idx, "Signal_Code"] = info.get("Exit_Code")
                            wealth_df.loc[idx, "Signal_Reason"] = info.get("Exit_Reason")
        stage_tracker.end_stage(f"Evaluated positions: {sell_signal_count} SELL triggers")

        stage_tracker.start_stage(4, "Dashboard Parquet & Health DB Sync", "Syncing parquet to Postgres")
        # Save updated parquet and DB cache
        if not is_test_mode and not getattr(database, "DONT_SAVE_WEALTH", False):
            wealth_df.to_parquet(WEALTH_PATH)
            
            def bg_db_sync_intraday():
                global _last_parquet_upload
                try:
                    import time
                    from database import upload_parquet_to_db, update_position_real_time_prices, upsert_scanner_health
                    now = time.time()
                    if now - _last_parquet_upload > 3600:
                        upload_parquet_to_db("wealth_engine", WEALTH_PATH)
                        _last_parquet_upload = now
                        
                    if realtime_metrics:
                        update_position_real_time_prices({s: {"price": p, "score": wealth_df[wealth_df["Stock"] == s]["Hold_Score"].iloc[0] if "Hold_Score" in wealth_df.columns and s in wealth_df["Stock"].values else None} for s, p in realtime_metrics.items()})

                    if write_health:
                        duration_sec = round(time.time() - start_time, 1)
                        today_buys = len(wealth_df[wealth_df["Signal_Code"] == "BUY"]) if "Signal_Code" in wealth_df.columns else 0
                        upsert_scanner_health(
                            scanner_name="Wealth Engine", status="OK", last_success=datetime.now(IST).isoformat(),
                            today_alerts=today_buys, total_count=len(wealth_df),
                            duration_seconds=duration_sec
                        )
                except Exception as _e: logger.exception(f"Error in bg_db_sync_intraday: {_e}")

            from database import submit_background_upload
            submit_background_upload(bg_db_sync_intraday)
            
        stage_tracker.end_stage("Dashboard DB sync completed")

        stage_tracker.print_summary(alerts_found=sell_signal_count)
        duration_sec = round(time.time() - start_time, 1)
        logger.info(f"⚡ [WEALTH ENGINE 5M] Intraday portfolio update completed in {duration_sec}s")
        return wealth_df

    except Exception as e:
        logger.exception(f"Error in wealth intraday update: {e}")
        return run_wealth_scan(is_test_mode=is_test_mode)
