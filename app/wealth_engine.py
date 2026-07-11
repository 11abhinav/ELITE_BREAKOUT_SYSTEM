from __future__ import annotations
import os
import time
import logging
import threading
import pandas as pd
from typing import Optional, Tuple
# Ensure tzcache writable location before importing yfinance (robust import to support different cwd)
try:
    import app.yf_bootstrap
except Exception:
    try:
        import yf_bootstrap
    except Exception:
        pass
import yfinance as yf
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
from price_fetcher import clear_price_cache
from database import get_recent_concall_analysis

# Concurrency and retry tuning
WORKER_COUNT = 3  # Hardcoded to 3 to prevent OOM kills on Railway (500MB RAM limit)
RETRY_ATTEMPTS = 3

logger = logging.getLogger(__name__)

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

_nifty_cache = {"ret_6m": None, "dist_52w": None, "ts": None}
_NIFTY_CACHE_TTL = 3600  # 1 hour max staleness

def fetch_nifty_macro_state() -> Tuple[Optional[float], Optional[float]]:
    """Fetch 6-month return and 52W distance of Nifty 50 for RS and Macro Regime Gate."""
    global _nifty_cache
    now = time.time()
    # Serve cache only if fresh
    if (
        _nifty_cache["ts"] is not None
        and (now - _nifty_cache["ts"]) < _NIFTY_CACHE_TTL
        and _nifty_cache["ret_6m"] is not None
    ):
        return (_nifty_cache["ret_6m"], _nifty_cache["dist_52w"])

    try:
        from macro_utils import get_nifty_6m_state
        ret_6m, dist_52w = get_nifty_6m_state()
        if ret_6m is not None:
            _nifty_cache = {"ret_6m": ret_6m, "dist_52w": dist_52w, "ts": now}
            return (ret_6m, dist_52w)
    except Exception as e:
        logger.exception(f"Failed to fetch Nifty Macro State")

    # Return stale cache rather than None if fetch fails
    logger.warning("Nifty fetch failed — serving stale cache if available")
    return (_nifty_cache["ret_6m"], _nifty_cache["dist_52w"])

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

            hist['sma_200'] = hist['Close'].rolling(window=200).mean()
            hist['sma_50']  = hist['Close'].rolling(window=50).mean()
            hist['ema_20']  = hist['Close'].ewm(span=20, adjust=False).mean()

            # Calculate 14-day RSI
            delta = hist['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs_val = gain / loss
            hist['RSI'] = 100 - (100 / (1 + rs_val))

            # Calculate 14-day ATR (True Range)
            hist['Prev_Close'] = hist['Close'].shift(1)
            tr1 = hist['High'] - hist['Low']
            tr2 = (hist['High'] - hist['Prev_Close']).abs()
            tr3 = (hist['Low'] - hist['Prev_Close']).abs()
            hist['TR'] = pd.DataFrame({'tr1': tr1, 'tr2': tr2, 'tr3': tr3}).max(axis=1)
            hist['ATR'] = hist['TR'].rolling(window=14).mean()
            
            last_row = hist.iloc[-1]
            cmp = float(last_row['Close'])

            # ATR as a percentage of CMP
            atr_pct = (float(last_row['ATR']) / cmp) * 100.0 if cmp > 0 and pd.notna(last_row['ATR']) else 0.0

            # 6-Month Relative Strength vs Nifty
            hist_6m = hist.tail(126)
            if len(hist_6m) > 0:
                start_6m = hist_6m['Close'].iloc[0]
                stock_6m_ret = ((cmp - start_6m) / start_6m) * 100.0 if start_6m > 0 else 0.0
                rs_6m = None if nifty_6m_ret is None else stock_6m_ret - nifty_6m_ret
            else:
                rs_6m = 0.0

            # Distance to 52-Week High
            high_52w = float(hist['High'].max())
            dist_52w_high = ((high_52w - cmp) / high_52w) * 100.0 if high_52w > 0 else 0.0

            # Liquidity (20-day Average Daily Volume * CMP)
            avg_vol = hist['Volume'].tail(20).mean()
            liquidity = float(avg_vol * cmp) if avg_vol > 0 else 0.0

            # Momentum Quality Evaluation
            from wealth_momentum_filter import calculate_momentum_quality_score
            mom_score, mom_conf = calculate_momentum_quality_score(hist)

            return {
                "sma_200": float(last_row['sma_200']) if not pd.isna(last_row['sma_200']) else None,
                "sma_50":  float(last_row['sma_50']) if not pd.isna(last_row['sma_50']) else None,
                "ema_20":  float(last_row['ema_20']) if not pd.isna(last_row['ema_20']) else None,
                "cmp": cmp,
                "rs_6m": rs_6m,
                "dist_52w_high": dist_52w_high,
                "liquidity": liquidity,
                "RSI": float(last_row['RSI']) if not pd.isna(last_row['RSI']) else 50.0,
                "ATR_Pct": atr_pct,
                "momentum_score": mom_score,
                "momentum_confidence": mom_conf,
                "data_quality": "STALE_INTRADAY" if is_stale else DataQuality.LIVE.value,
                "is_stale": is_stale,
                "above_sma200": bool(cmp >= float(last_row['sma_200'])) if not pd.isna(last_row['sma_200']) else False
            }
        except Exception as e:
            logger.warning(f"Attempt {attempt+1}/{RETRY_ATTEMPTS} failed for {symbol}: {e}")
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
            else:
                logger.exception(f"Failed to fetch technicals for {symbol} after {RETRY_ATTEMPTS} attempts")
                return defaults


# =====================================================================================
# 100-POINT SCORING ENGINE (v4 — With Durability & Consistency)
# =====================================================================================
#
#   Factor        | Weight | Rationale
#   --------------|--------|----------------------------------------------------------
#   Quality       |   22   | ROE, ROCE, Debt — Capital efficiency & safety
#   Growth        |   20   | YoY Revenue & Profit — Business velocity
#   Valuation     |   10   | PEG, P/E vs sector — Prevents overpaying
#   Momentum      |   15   | RS vs Nifty, 52W proximity, >200 SMA — Price leadership
#   Ownership     |    8   | Inst Accumulation tags — Smart money footprint
#   Cash Flow     |   15   | FCF Margin — Catches accounting red flags (Satyam/DHFL)
#   Consistency   |   10   | 5Y Revenue/EPS CAGR — Durability and compounding history
#
#   Total         |  100
#
# =====================================================================================



def map_watchlist_to_v5(raw_data: dict) -> dict:
    """Maps Screener export headers to V5 snake_case variables and reconstructs missing absolute metrics for Valuation Engine."""
    import pandas as pd
    
    def _safe_float(val, default=0.0):
        if val is None or pd.isna(val): return default
        try: return float(val)
        except: return default
        
    market_cap = _safe_float(raw_data.get('Market Cap Cr', raw_data.get('Market Capitalization')))
    price = _safe_float(raw_data.get('cmp', 0.0))
    pe = _safe_float(raw_data.get('PE Ratio', raw_data.get('Price to Earning')))
    pb = _safe_float(raw_data.get('Price to Book', raw_data.get('Price to book value')))
    
    # [VERSION: V5_VALUATION_FIX] The V5 Valuation Engine requires absolute numbers (EPS, BVPS, Shares) 
    # which aren't in the raw screener ratios. We must reconstruct them mathematically.
    shares = (market_cap / price) if price > 0 else 0.0
    eps = (price / pe) if pe > 0 else 0.0
    bvps = (price / pb) if pb > 0 else 0.0
        
    return {
        'market_cap': market_cap,
        'roce': _safe_float(raw_data.get('ROCE %', raw_data.get('ROCE'))) / 100.0,
        'roe': _safe_float(raw_data.get('ROE %', raw_data.get('ROE'))) / 100.0,
        'debt_to_equity': _safe_float(raw_data.get('Debt/Equity', raw_data.get('Debt to equity')), default=0.5),
        'interest_coverage': _safe_float(raw_data.get('Interest Coverage', raw_data.get('Interest coverage')), default=5.0),
        'operating_margin_ttm': _safe_float(raw_data.get('OPM %', raw_data.get('OPM'))) / 100.0,
        'yoy_revenue': _safe_float(raw_data.get('YOY Revenue %', raw_data.get('Sales growth'))) / 100.0,
        'yoy_profit': _safe_float(raw_data.get('YOY Profit %', raw_data.get('Profit growth'))) / 100.0,
        'revenue_cagr_3y': _safe_float(raw_data.get('YOY Revenue %', raw_data.get('Sales growth')), default=15.0) / 100.0,
        'revenue_growth_1y': _safe_float(raw_data.get('YOY Revenue %', raw_data.get('Sales growth')), default=15.0) / 100.0,
        'pat_cagr_3y': _safe_float(raw_data.get('YOY Profit %', raw_data.get('Profit growth')), default=15.0) / 100.0,
        'fcf_cagr_3y': _safe_float(raw_data.get('YOY Profit %', raw_data.get('Profit growth')), default=15.0) / 100.0, # Proxy FCF growth with Profit growth
        'reinvestment_rate': 0.50, # Proxy 50% retention if missing
        'peg': _safe_float(raw_data.get('PEG Ratio', raw_data.get('PEG Ratio')), default=1.0),
        'pe': pe,
        'ev_ebitda': _safe_float(raw_data.get('EV/EBITDA', raw_data.get('EV / EBITDA')), default=pe),
        'fcf_margin': _safe_float(raw_data.get('FCF Margin %', raw_data.get('FCF Margin')), default=10.0) / 100.0,
        'free_cash_flow': (eps * shares * 1.33 * 0.75) * (_safe_float(raw_data.get('FCF Margin %'), default=10.0) / 100.0), # Proxy FCF based on NOPAT and FCF Margin
        'price_to_book': pb,
        'gross_margin_stability': _safe_float(raw_data.get('gross_margin_stability'), default=5.0) / 100.0,
        'asset_turnover': _safe_float(raw_data.get('asset_turnover'), default=1.0),
        
        # Injected Reconstructed Fields for Valuation Models
        'eps': eps,
        'book_value_per_share': bvps,
        'shares_outstanding': shares,
        'tt_indpe': pe,  # Proxy industry PE with trailing PE if missing
        'ebit': eps * shares * 1.33,  # Proxy NOPAT assuming 25% tax
        
        # Technical fields that might be passed from wealth_technicals
        'pct_from_52w_high': _safe_float(raw_data.get('dist_52w_high', 0.0)) / -100.0,
        'rs_rating': _safe_float(raw_data.get('RS_Rating', 50.0)),
        'relative_volume_10d': 1.0,  # Proxy default
        'sector': str(raw_data.get('Sector', 'Unknown')),
        'price': price
    }

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


def determine_portfolio_bucket(r, nifty_dist_52w: float):
    """Assign stocks to Core / Growth / Opportunistic buckets based on hard filters."""
    import pandas as pd
    
    # [VERSION: WEALTH_BUCKET_FIX_v1.1] Handle pandas NaN semantics for missing data
    score      = r.get("FM_Score", 0)
    mcap       = r.get("Market Cap Cr")
    roce       = r.get("ROCE %")
    roe        = r.get("ROE %")
    de         = r.get("Debt/Equity")
    yoy_sales  = r.get("YOY Revenue %")
    yoy_profit = r.get("YOY Profit %")
    rs_6m      = r.get("rs_6m")
    dist_52w   = r.get("dist_52w_high")
    pledge     = r.get("Promoter_Pledge")
    liquidity  = r.get("liquidity", 0) or 0
    cats       = str(r.get("Category", ""))

    buckets = []

    # Instant Kill Gates
    from config import MIN_DAILY_LIQUIDITY_RUPEES_WEALTH
    
    if liquidity < MIN_DAILY_LIQUIDITY_RUPEES_WEALTH:
        return None

    # Helper for missing data bypass
    def _is_ok(val, threshold, is_lower_bound=True):
        if pd.isna(val):
            return True
        if is_lower_bound:
            return float(val) >= threshold
        else:
            return float(val) <= threshold

    # 1. Core Compounder — ₹10,000 Cr+ mega-quality
    if score >= 65 and _is_ok(mcap, 10000) and _is_ok(roce, 20) and _is_ok(roe, 15) and _is_ok(de, 0.5, False):
        buckets.append("Core")

    # 2. Growth Multiplier — ₹2,000 Cr+ emerging leaders
    if score >= 60 and _is_ok(mcap, 2000) and _is_ok(yoy_sales, 20) and _is_ok(yoy_profit, 20) and _is_ok(rs_6m, 0) and _is_ok(dist_52w, 15, False):
        buckets.append("Growth")

    # 3. Quality-On-Sale — high quality but correcting (52W high dist > 20%)
    if score >= 50 and _is_ok(roce, 15) and _is_ok(dist_52w, 20) and _is_ok(de, 1.0, False):
        buckets.append("Quality-On-Sale")

    # 4. Opportunistic / Turnaround — massive momentum + turnaround growth
    if score >= 55 and _is_ok(yoy_profit, 40) and _is_ok(rs_6m, 15) and "SME" not in cats:
        buckets.append("Opportunistic")

    return ", ".join(buckets) if buckets else "REVIEW"


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
    
    NEW: Includes drawdown circuit breaker (hard stop at 20% loss).
    """
    score = 0
    
    # 1. DRAWDOWN CIRCUIT BREAKER (NEW - Added Phase 1)
    # [VERSION: WEALTH_SAFE_NUM_v1.0] Fix NaN-vs-'or 0' silent suppression
    cmp = _safe_num(r.get("cmp"))
    entry_price = _safe_num(r.get("entry_price"))
    
    if not entry_price or entry_price <= 0:
        pass # Not an open holding, safely ignore drawdown circuit breaker
    
    if entry_price > 0 and cmp > 0:
        drawdown_pct = ((entry_price - cmp) / entry_price) * 100
        
        # CATASTROPHIC STOP: >20% loss (Unconditional early return)
        if drawdown_pct > 20:
            return 0  # Instant SELL signal (Hold_Score = 0 < 45)
        
        # WARNING: >10% loss  
        if drawdown_pct > 10:
            score -= 25  # Force below 45 → SELL REVIEW
    
    # 2. Technical Health (40 pts)
    ema20 = _safe_num(r.get("ema_20"))
    sma50 = _safe_num(r.get("sma_50"))
    sma200 = _safe_num(r.get("sma_200"))
    rs_6m = _safe_num(r.get("rs_6m"))
    
    if cmp > ema20 and ema20 > 0: score += 10
    if cmp > sma50 and sma50 > 0: score += 10
    if cmp > sma200 and sma200 > 0: score += 10
    if rs_6m > 0: score += 10
    
    # 3. Fundamental Integrity (30 pts)
    # Mapping Piotroski/Fundamentals to our existing FM_Score (V5 thresholds)
    fm_score = _safe_num(r.get("FM_Score"))
    if fm_score >= 65: score += 15
    elif fm_score >= 50: score += 5
    
    pledge = r.get("Promoter_Pledge")
    if pledge is not None and pledge == 0: score += 10
    
    yoy_profit = _safe_num(r.get("YOY Profit %"))
    if yoy_profit > 0: score += 5
    
    # 4. Sector & Momentum Regime (15 pts)
    # Using 6-month RS Rating (Percentile)
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
        bonus = round(10 * (days_to_ltcg / LTCG_BONUS_WINDOW), 1)
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

import pandas as pd
from datetime import datetime
import logging
logger = logging.getLogger(__name__)

# =====================================================================================
# LAYER 1: CANDIDATE SELECTION
# =====================================================================================
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
    wealth_df["Valuation_Score"] = scores_df["RVS"]
    wealth_df["Consistency_Score"] = scores_df["BQS"]
    wealth_df["Reliability"] = scores_df["Reliability"]
    wealth_df["Base_FV"] = scores_df["Base_FV"]
    wealth_df["Bull_FV"] = scores_df["Bull_FV"]
    
    wealth_df["Portfolio_Bucket"] = wealth_df.apply(lambda r: determine_portfolio_bucket(r, nifty_dist_52w), axis=1)

    def check_completeness(r):
        # [VERSION: WEALTH_COMPLETENESS_FIX_v1.0] 
        # Removed hard fundamental checks (ROE, ROCE, FCF Margin, etc.) to prevent double-penalizing
        # missing data. The V5 pipeline's FM_Score already accounts for and degrades on missing data.
        # We only require technicals and the final V5 scores to be present.
        mand_cols = ["cmp", "sma_200", "rs_6m", "FM_Score", "Valuation_Score", "Consistency_Score", "data_quality", "momentum_confidence"]
        for col in mand_cols:
            if pd.isna(r.get(col)): return False
            
        return True
        
    wealth_df["candidate_complete_for_buy"] = wealth_df.apply(check_completeness, axis=1)
    
    return wealth_df


# =====================================================================================
# LAYER 2: ENTRY TIMING
# =====================================================================================
def generate_entry_signal(candidate_df, buy_gate_active, suppression_reason):
    """Decides whether a candidate should be bought, suppressed, or watched."""
    if candidate_df.empty:
        return candidate_df
        
    def _get_entry_signal(r):
        score = r.get("FM_Score", 0)
        # [VERSION: WEALTH_SAFE_NUM_v1.0] Fix NaN-vs-'or 0' silent suppression in entry signal
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
                roce = _safe_num(r.get("ROCE %"))
                fcf_margin = r.get("FCF Margin %")
                path = r.get("Path", "")
                mom_conf = r.get("momentum_confidence", "")
                
                # [VERSION: WEALTH_SAFE_NUM_v1.0] Fix NaN-vs-'is not None' — np.nan passes 'is not None'
                fcf_ok = True if path == "Financial" else (not pd.isna(fcf_margin) and fcf_margin > 0)
                
                if (score >= 65 and cons_score >= 18 and val_score >= 10 and
                    cmp > 0 and sma > 0 and cmp >= 0.95 * sma and
                    rs > -10 and roce >= 15 and fcf_ok and mom_conf != "LOW"):
                    return pd.Series({"Signal_Code": "BUY", "Signal_Reason": f"Bear Market Value Add: {suppression_reason}"})
            return pd.Series({"Signal_Code": "SUPPRESS", "Signal_Reason": suppression_reason})
            
        # Baseline Active Entry Condition (V5 Thresholds)
        if score >= 55 and r.get("Consistency_Score", 0) >= 15 and r.get("Valuation_Score", 0) >= 5 and cmp > sma and sma > 0:
            if r.get("momentum_confidence", "") == "LOW":
                return pd.Series({"Signal_Code": "HOLD", "Signal_Reason": "Low Momentum Quality"})
            return pd.Series({"Signal_Code": "BUY", "Signal_Reason": f"Score: {score:.1f}, Valuation: {r.get('Valuation_Score', 0):.1f}"})
            
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
    
    # Core bucket Sector Caps
    core_capped = apply_sector_cap(candidate_df, "Portfolio_Bucket", "Core", max_stocks=15)
    core_symbols = set(core_capped["Stock"].tolist()) if not core_capped.empty else set()
    candidate_df["Core_Selected"] = candidate_df["Stock"].apply(lambda s: s in core_symbols)
    
    return candidate_df

# =====================================================================================
# LAYER 3: PORTFOLIO MANAGEMENT
# =====================================================================================
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
        
        final_hold_score = base_hold_score
        tax_info = {}
        try:
            entry_date = _coerce_to_date(r.get("entry_date"))
            entry_price = _safe_num(r.get("entry_price"))
            cmp_price = _safe_num(r.get("cmp"), default=entry_price)
            pnl_pct = ((cmp_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            if entry_date:
                tax_info = compute_tax_hold_bonus(entry_date, pnl_pct)
                final_hold_score = min(100, base_hold_score + tax_info.get('bonus', 0))
        except Exception:
            pass
            
        r["Hold_Score"] = final_hold_score
        
        from wealth_hold_tracking import HoldScoreTrendAnalyzer
        trend = HoldScoreTrendAnalyzer.analyze_trend(sym)
        hold_trend = trend["reason"] if trend["action"] != "HOLD" else "Stable"
        r["hold_trend"] = hold_trend

        cmp = _safe_num(r.get("cmp"))
        sma = _safe_num(r.get("sma_200"))
        rs = _safe_num(r.get("rs_6m"))
        data_quality = r.get("data_quality")
        used_fallback_data = r.get("used_fallback_data", False)
        
        exit_code = ""
        exit_reason = ""
        
        if used_fallback_data:
            # Fallback to HOLD and suppress SELL evaluations
            r["Exit_Code"] = ""
            r["Exit_Reason"] = ""
            return r
            
        if cmp > 0 and data_quality not in ["MISSING_PARTIAL", "CACHED_PREV_DAY"]:
            if "SELL REVIEW" in hold_trend or "Momentum Reversal" in hold_trend:
                exit_code, exit_reason = "SELL_REVIEW", hold_trend
            elif final_hold_score < 45:
                exit_code, exit_reason = "SELL_REVIEW", f"Hold Score: {final_hold_score}/100"
            elif rs < -40:
                exit_code, exit_reason = "SELL", "Catastrophic RS Breakdown"
            elif sma > 0 and cmp < (0.75 * sma):
                exit_code, exit_reason = "SELL", "Catastrophic Trend Collapse"
                
        if not exit_code and tax_info.get("harvest_signal"):
            exit_code, exit_reason = "TLH", f"Tax-Loss Harvest Opportunity: {pnl_pct:.1f}%"
            
        r["Exit_Code"] = exit_code
        r["Exit_Reason"] = exit_reason
        return r

    return portfolio_df.apply(_generate_exit_signal, axis=1)

# =====================================================================================
# MAIN PIPELINE WRAPPERS
# =====================================================================================
def run_wealth_scan(is_test_mode=False):
    # [VERSION: SYMBOL_FIX_v1.0] Graceful skip instead of RuntimeError when prior scan
    # is still running. The 5-min scheduler can overlap if a scan takes >5 min;
    # crashing pollutes the error log unnecessarily.
    if not _scan_lock.acquire(blocking=False):
        logger.warning("⏭️ Wealth Engine scan skipped — previous run still in progress.")
        return None
    try:
        return _run_wealth_scan_wrapper(is_test_mode)
    finally:
        _scan_lock.release()

def _run_wealth_scan_wrapper(is_test_mode=False):
    from config import WATCHLIST_PATH, DATA_DIR
    from database import upsert_scanner_health
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    import os

    WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
    logger.info("💰 Fund Manager Wealth Engine v3 Started Scan (Strict Layered).")
    
    import database
    if not getattr(database, "DONT_SAVE_WEALTH", False):
        upsert_scanner_health("Wealth Engine", "IDLE", last_success=None, today_alerts=0)

    try:
        if not os.path.exists(WATCHLIST_PATH):
            logger.warning("⚠️ Watchlist not found. Wealth Engine is forcing the Daily Builder to run.")
            try:
                from daily_builder import build_watchlist
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

        # ── DATA FETCH (Candidates + Orphans) ──
        nifty_6m_ret, nifty_dist_52w = fetch_nifty_macro_state()
        if nifty_6m_ret is None:
            logger.info("Nifty Macro: UNAVAILABLE — suppressing macro gates")
        else:
            logger.info(f"💰 [WEALTH ENGINE] Nifty 6M Return: {nifty_6m_ret:.1f}%")

        clear_price_cache()
        rejection_counts = {}
        import threading
        _rejection_lock = threading.Lock()

        all_symbols_to_fetch = list(candidate_symbols.union(set(orphan_symbols)))
        logger.info(f"💰 [WEALTH ENGINE] Batch fetching 1D data for {len(all_symbols_to_fetch)} symbols...")
        from price_cache import fetch_unified_historical
        all_historical_data = fetch_unified_historical(all_symbols_to_fetch, period="1y", interval="1d")
        
        if all_historical_data is None:
            all_historical_data = {}
        
        fetched_count = len(all_historical_data)
        required_count = int(len(df) * 0.70)
        
        if fetched_count < required_count:
            logger.warning(f"⚠️ Batch fetch returned {fetched_count}/{len(df)} candidates. Aborting.")
            if not getattr(database, "DONT_SAVE_WEALTH", False):
                try:
                    upsert_scanner_health("Wealth Engine", "DOWN", error_msg=f"INCOMPLETE DATA: Fetched {fetched_count}")
                except Exception:
                    pass
            raise Exception(f"INCOMPLETE DATA: Fetched {fetched_count}/{len(df)} symbols. Aborting.")
            
        def process_symbol(idx, sym, historical_cache=None):
            try:
                tech = calculate_wealth_technicals(sym, nifty_6m_ret, historical_cache=historical_cache)
                if tech.get("cmp") is None and not prev_wealth_df.empty and sym in prev_wealth_df["Stock"].values:
                    prev_row = prev_wealth_df[prev_wealth_df["Stock"] == sym].iloc[0]
                    tech["cmp"] = prev_row.get("cmp")
                    tech["sma_50"] = prev_row.get("sma_50")
                    tech["sma_200"] = prev_row.get("sma_200")
                    tech["rs_6m"] = prev_row.get("rs_6m")
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
                    concall = get_recent_concall_analysis(sym)
                    tech["AI_Confidence"] = int(concall["management_confidence"]) if concall and "management_confidence" in concall else 0
                except Exception:
                    tech["AI_Confidence"] = 0
                return tech
            except Exception as e:
                with _rejection_lock:
                    rejection_counts["processing_error"] = rejection_counts.get("processing_error", 0) + 1
                return {"Stock": sym}

        technicals = []
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKER_COUNT) as executor:
            futures = {executor.submit(process_symbol, i, sym, all_historical_data): i for i, sym in enumerate(all_symbols_to_fetch)}
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                try:
                    technicals.append(future.result())
                except Exception:
                    pass
                completed += 1
                if completed % 50 == 0 or completed == len(all_symbols_to_fetch):
                    logger.info(f"💰 [WEALTH ENGINE] Progress: {completed}/{len(all_symbols_to_fetch)} stocks processed...")

        tech_df = pd.DataFrame(technicals)
        
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
        # wealth_df consists ONLY of the fundamental watchlist candidates joined with technicals
        candidate_tech = tech_df[tech_df["Stock"].isin(candidate_symbols)]
        wealth_df = pd.merge(df, candidate_tech, on="Stock", how="left")
        
        from valuation_utils import compute_peer_medians
        sector_stats = compute_peer_medians(wealth_df["Stock"].tolist() if not wealth_df.empty else [])
        
        wealth_df = evaluate_candidates(wealth_df, sector_stats, nifty_dist_52w)
        
        # =====================================================================================
        # EXECUTE LAYER 2: ENTRY TIMING
        # =====================================================================================
        BUY_GATE_ACTIVE = False
        suppression_reason = None
        
        degraded = rejection_counts.get("stale_data", 0) + rejection_counts.get("no_data", 0)
        fresh_ratio = 1.0 - (degraded / max(len(candidate_symbols), 1))
        
        breadth_pct = None
        if not candidate_tech.empty and "above_sma200" in candidate_tech.columns:
            total_eval = len(candidate_tech)
            above_200 = candidate_tech["above_sma200"].sum()
            breadth_pct = (above_200 / total_eval) * 100
            
        if nifty_dist_52w is not None and nifty_dist_52w > 20:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Nifty {nifty_dist_52w:.1f}% below 52W high"
        elif nifty_6m_ret is not None and nifty_6m_ret < -15:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Nifty 6M return {nifty_6m_ret:.1f}%"
        elif breadth_pct is not None and breadth_pct < 30:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Breadth weak: {breadth_pct:.1f}% above SMA200"
        elif fresh_ratio < 0.70 and degraded >= 3:
            BUY_GATE_ACTIVE = True; suppression_reason = f"Fresh data only {fresh_ratio*100:.1f}%"
            if not getattr(database, "DONT_SAVE_WEALTH", False):
                try:
                    upsert_scanner_health("Wealth Engine", "DEGRADED", error_msg=f"Data stale ({fresh_ratio*100:.1f}% fresh). Signals suppressed.")
                    from telegram_engine import send_telegram_message
                    msg = f"🚨 <b>Wealth Engine Degraded</b>\nSignals suppressed due to missing/stale data.\nFresh ratio: {fresh_ratio*100:.1f}%"
                    send_telegram_message(msg)
                    from push_service import send_push_to_all
                    send_push_to_all("Wealth Engine Degraded", f"Signals suppressed. Data freshness dropped to {fresh_ratio*100:.1f}%")
                except Exception:
                    pass
            
        wealth_df = generate_entry_signal(wealth_df, BUY_GATE_ACTIVE, suppression_reason)
        
        # Persist BUY Signals
        try:
            from database import save_wealth_buy_alert, DONT_SAVE_WEALTH
            buy_signals = wealth_df[wealth_df["Signal_Code"] == "BUY"]
            for _, row in buy_signals.iterrows():
                if row.get("used_fallback_data", False) or not row.get("candidate_complete_for_buy", False):
                    continue
                try:
                    from config import get_wealth_admission_state
                    allow_new_admissions = get_wealth_admission_state()
                except Exception:
                    allow_new_admissions = True
                if not allow_new_admissions:
                    continue
                    
                symbol = row.get("Stock")
                cmp = row.get("cmp")
                if symbol and cmp and not DONT_SAVE_WEALTH:
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
                            data_quality=row.get("data_quality"), fallback_timestamp=row.get("fallback_timestamp")
                        )
                    if not inserted:
                        # Mutate the dataframe to reflect suppression so parquet dashboard is correct
                        wealth_df.loc[wealth_df["Stock"] == symbol, "Signal_Code"] = "SUPPRESSED"
                        wealth_df.loc[wealth_df["Stock"] == symbol, "Signal_Reason"] = "Rejected by DB Idempotency/Guard"
                        wealth_df.loc[wealth_df["Stock"] == symbol, "Signal"] = "SUPPRESSED (Rejected by DB)"

        except Exception: pass

        # =====================================================================================
        # EXECUTE LAYER 3: PORTFOLIO MANAGEMENT
        # =====================================================================================
        # Extract open positions into a separate dataframe
        portfolio_rows = []
        
        # 1. FETCH REAL-TIME PRICES BEFORE EVALUATION
        realtime_metrics = {}
        try:
            if open_symbols:
                import yfinance as yf
                from yf_rate_limiter import acquire as yf_acquire, release as yf_release
                yf_syms = [f"{s.replace('_', '-')}.NS" for s in open_symbols]
                try:
                    yf_acquire()
                    batch_data = yf.download(yf_syms, period="1d", progress=False)
                    closes = batch_data['Close'] if not batch_data.empty and 'Close' in batch_data else None
                except Exception: closes = None
                finally:
                    yf_release()
                
                for i, symbol in enumerate(open_symbols):
                    yf_sym = yf_syms[i]
                    current_price = None
                    if closes is not None:
                        if isinstance(closes, pd.Series):
                            if len(yf_syms) == 1 and not pd.isna(closes.iloc[-1]):
                                current_price = float(closes.iloc[-1])
                            elif len(yf_syms) > 1:
                                logger.warning(f"Batch downgraded to single-index. Discarding real-time price for {yf_sym} to prevent cross-pollution.")
                        elif yf_sym in closes and not pd.isna(closes[yf_sym].iloc[-1]):
                            current_price = float(closes[yf_sym].iloc[-1])
                    if current_price and current_price > 0:
                        realtime_metrics[symbol] = float(current_price)
        except Exception as e:
            logger.warning(f"Failed to fetch realtime prices: {e}")
            
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
                
            portfolio_rows.append(row)
            
        portfolio_df = pd.DataFrame(portfolio_rows)
        portfolio_df = evaluate_open_positions(portfolio_df, portfolio_dict)
        
        if not portfolio_df.empty:
            # Auto-close positions when SELL signal detected
            sell_signals = portfolio_df[portfolio_df["Exit_Code"] == "SELL"]
            for _, row in sell_signals.iterrows():
                symbol = row.get("Stock")
                cmp = row.get("cmp")
                exit_reason = row.get("Exit_Reason")
                if symbol and cmp:
                    if is_test_mode:
                        logger.info(f"🧪 [TEST MODE] Would close position {symbol} at {cmp} due to {exit_reason}")
                    elif not getattr(database, "DONT_SAVE_WEALTH", False):
                        from database import close_position
                        close_position(symbol, cmp, exit_reason)

            # Map Portfolio outputs back into wealth_df for Dashboard display
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
                            symbol=symbol, hold_score=current_score, fm_score=float(p_row.get("FM_Score", 0)),
                            rs_6m=float(p_row.get("rs_6m", 0)), cmp=float(p_row.get("cmp", 0)), sma_200=float(p_row.get("sma_200", 0))
                        )
                        
                if realtime_metrics and not is_test_mode and not getattr(database, "DONT_SAVE_WEALTH", False):
                    from database import update_position_real_time_prices
                    update_position_real_time_prices({s: {"price": p, "score": port_map.get(s, {}).get("Hold_Score")} for s, p in realtime_metrics.items()})
            except Exception: pass
        # Final Dashboard Export
        if not is_test_mode and not getattr(database, "DONT_SAVE_WEALTH", False):
            try:
                from database import upload_parquet_to_db
                cols = ['Stock', 'Sector', 'FM_Score', 'Consistency_Score', 'Valuation_Score', 'Reliability', 'Base_FV', 'Bull_FV', 'Portfolio_Bucket', 'Signal', 'Hold_Score', 'hold_trend', 'Core_Selected']
                for c in cols:
                    if c not in wealth_df.columns: wealth_df[c] = None
                wealth_df.to_parquet(WEALTH_PATH)
                upload_parquet_to_db("wealth_engine", WEALTH_PATH)
                from database import upsert_scanner_health
                upsert_scanner_health(
                    scanner_name="Wealth Engine", status="OK", last_success=datetime.now(IST).isoformat(),
                    today_alerts=len(wealth_df[wealth_df["Signal_Code"] == "BUY"]), total_count=len(wealth_df)
                )
            except Exception: pass

    except Exception as e:
        logger.exception("❌ CRITICAL ERROR in Wealth Engine")
        import database
        if not getattr(database, "DONT_SAVE_WEALTH", False):
            try:
                from database import upsert_scanner_health
                upsert_scanner_health("Wealth Engine", "DOWN", error_msg=str(e))
            except Exception: pass
