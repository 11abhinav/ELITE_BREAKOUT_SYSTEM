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
        logger.error(f"Failed to fetch Nifty Macro State: {e}")

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
                "data_quality": DataQuality.LIVE.value,
                "is_stale": is_stale
            }
        except Exception as e:
            logger.warning(f"Attempt {attempt+1}/{RETRY_ATTEMPTS} failed for {symbol}: {e}")
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
            else:
                logger.error(f"Failed to fetch technicals for {symbol} after {RETRY_ATTEMPTS} attempts: {e}")
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

def calculate_valuation_score(r, sector_stats: dict = None) -> int:
    """
    Valuation scoring module (10 pts max).
    Prevents overpaying for quality stocks.
    
    Metrics:
    - PEG Ratio (6 pts max): Ideal < 1.0 (growth justified by valuation)
    - P/E vs Sector (4 pts max): Discount to sector median = quality value
    """
    score = 0
    
    def _safe_float(val, default=0.0):
        if val is None: return default
        try:
            f = float(val)
            return default if pd.isna(f) else f
        except (ValueError, TypeError):
            return default
    
    peg = _safe_float(r.get("PEG Ratio"), None)
    pe = _safe_float(r.get("P/E Ratio"), None)
    
    # PEG scoring (6 pts max)
    if peg is not None:
        if peg < 1.0:
            score += 6  # Excellent: growth > valuation
        elif peg < 1.5:
            score += 3  # Good: growth roughly justified
        # else: 0 pts (overvalued relative to growth)
    
    # P/E vs sector scoring (4 pts max)
    if pe is not None and sector_stats:
        sector = str(r.get("Sector", "Unknown"))
        sector_median_pe = sector_stats.get(sector, {}).get("median_pe", None)
        
        if sector_median_pe is not None and sector_median_pe > 0:
            pe_discount = (sector_median_pe - pe) / sector_median_pe
            
            if pe_discount > 0.15:  # 15%+ discount to sector = value opportunity
                score += 4
            elif pe_discount > 0.05:  # 5%+ discount
                score += 2
            # else: 0 pts (trading at or above sector median)
    
    return min(10, score)


def calculate_consistency_score(r) -> int:
    """
    Consistency scoring module (10 pts max).
    Rewards stable operators and penalizes missing long-term data.
    """
    score = 0
    
    def _safe_float(val, default=None):
        if val is None: return default
        try:
            f = float(val)
            return default if pd.isna(f) else f
        except (ValueError, TypeError):
            return default
            
    rev_5y = _safe_float(r.get("5Y Revenue %"))
    eps_5y = _safe_float(r.get("5Y EPS %"))
    roe = _safe_float(r.get("ROE %"))
    roce = _safe_float(r.get("ROCE %"))
    
    if rev_5y is not None and rev_5y >= 12:
        score += 3
    elif rev_5y is not None and rev_5y >= 8:
        score += 1
        
    if eps_5y is not None and eps_5y >= 15:
        score += 3
    elif eps_5y is not None and eps_5y >= 10:
        score += 1
        
    if roe is not None and roe >= 15:
        score += 2
        
    if roce is not None and roce >= 18:
        score += 2
        
    # Penalties for missing/negative long-term data
    cats = str(r.get("Category", ""))
    path = str(r.get("Path", ""))
    is_turnaround = any(x in cats for x in ["Recovery Play", "Financial Recovery", "Turnaround"])
    
    if not is_turnaround and path != "Financial":
        if rev_5y is None or eps_5y is None:
            score -= 3
        elif rev_5y < 0 or eps_5y < 0:
            score -= 5
            
    return max(-5, min(10, score))


def calculate_100_point_score(r, sector_stats: dict = None) -> int:
    """Calculates a strict 100-point Fund Manager score for a single stock."""
    
    def _safe_float(val, default=0.0):
        if val is None: return default
        try:
            f = float(val)
            return default if pd.isna(f) else f
        except (ValueError, TypeError):
            return default

    score = 0

    # ── QUALITY (22 pts) ──────────────────────────────────────────────────────
    roe  = _safe_float(r.get("ROE %"), 0)
    roce = _safe_float(r.get("ROCE %"), 0)
    de   = _safe_float(r.get("Debt/Equity"), 0)

    if roe >= 15:  score += 7
    elif roe >= 10: score += 3
    if roce >= 20:  score += 8
    elif roce >= 15: score += 4
    if de <= 0.1:   score += 7
    elif de <= 0.5:  score += 3

    # ── GROWTH (20 pts) ───────────────────────────────────────────────────────
    yoy_sales  = _safe_float(r.get("YOY Revenue %"), 0)
    yoy_profit = _safe_float(r.get("YOY Profit %"), 0)

    if yoy_sales >= 20:   score += 10
    elif yoy_sales >= 12:  score += 6
    if yoy_profit >= 20:   score += 10
    elif yoy_profit >= 15: score += 6

    # ── VALUATION (10 pts) ────────────────────────────────────────────────────
    valuation_score = calculate_valuation_score(r, sector_stats)
    score += valuation_score
    
    rs_rating_raw = r.get("RS_Rating")
    rs_rating = None if rs_rating_raw is None or (isinstance(rs_rating_raw, float) and pd.isna(rs_rating_raw)) else _safe_float(rs_rating_raw, 0)
    dist_52w  = _safe_float(r.get("dist_52w_high"), 100)
    cmp_price = _safe_float(r.get("cmp"), 0)
    sma_200_raw = r.get("sma_200")
    sma_200   = None if sma_200_raw is None else _safe_float(sma_200_raw, 0)

    # ── MOMENTUM (15 pts) ─────────────────────────────────────────────────────
    if rs_rating is not None:
        if rs_rating > 90 and sma_200 is not None and cmp_price > sma_200 and sma_200 > 0: 
            score += 7
        elif rs_rating > 80: score += 4
        elif rs_rating > 60: score += 2

    if dist_52w <= 5:  score += 4
    elif dist_52w <= 10: score += 2

    if sma_200 is not None and cmp_price > sma_200 and sma_200 > 0:
        score += 4

    # ── OWNERSHIP (8 pts) ─────────────────────────────────────────────────────
    cats = str(r.get("Category", ""))
    ownership_score = 0
    if "Inst Accumulation" in cats: ownership_score += 8
    elif "Consistent Performer" in cats or "Dividend Aristocrat" in cats: ownership_score += 4
    score += min(8, ownership_score)

    # ── CASH FLOW QUALITY (15 pts) ────────────────────────────────────────────
    fcf_margin = r.get("FCF Margin %")
    opm        = r.get("OPM %", 0) or 0
    path       = str(r.get("Path", ""))

    if fcf_margin is not None:
        if fcf_margin > 0 and fcf_margin >= opm * 0.5:
            score += 15
        elif fcf_margin > 0:
            score += 8
        elif path != "Financial":
            score -= 10   # Negative FCF is a severe red flag for non-financials
    else:
        if path != "Financial":
            score -= 5    # Missing FCF penalty for non-financials
            
    # ── CONSISTENCY (10 pts) ──────────────────────────────────────────────────
    consistency_score = calculate_consistency_score(r)
    score += consistency_score

    # ── AI SENTIMENT (+5 or -5 pts) — Based on management guidance ───────────
    if ENABLE_AI_SENTIMENT_SCORE:
        ai_conf = r.get("AI_Confidence", 0)
        if ai_conf >= 8:
            score += 5   # Upward guidance / Record margins
        elif ai_conf == 7:
            score += 2   # Solid / Consistent guidance
        elif 1 <= ai_conf <= 4:
            score -= 5   # Headwinds / Guidance cuts

    return max(0, min(100, score))


# =====================================================================================
# PORTFOLIO BUCKETING — with Sector Concentration Cap
# =====================================================================================

def determine_portfolio_bucket(r, nifty_dist_52w: float):
    """Assign stocks to Core / Growth / Opportunistic buckets based on hard filters."""
    score      = r.get("FM_Score", 0)
    mcap       = r.get("Market Cap Cr", 0) or 0
    roce       = r.get("ROCE %", 0) or 0
    roe        = r.get("ROE %", 0) or 0
    de         = r.get("Debt/Equity", 0) or 0
    yoy_sales  = r.get("YOY Revenue %", 0) or 0
    yoy_profit = r.get("YOY Profit %", 0) or 0
    rs_6m      = r.get("rs_6m", 0) or 0
    dist_52w   = r.get("dist_52w_high", 100) or 100
    pledge     = r.get("Promoter_Pledge")
    liquidity  = r.get("liquidity", 0) or 0
    cats       = str(r.get("Category", ""))

    buckets = []

    # Instant Kill Gates
    # if pledge is not None and pledge > MAX_PROMOTER_PLEDGE:
    #     return None
    from config import MIN_DAILY_LIQUIDITY_RUPEES_WEALTH
    
    if liquidity < MIN_DAILY_LIQUIDITY_RUPEES_WEALTH:
        return None

    # Core Compounder — ₹10,000 Cr+ mega-quality
    if score >= 80 and mcap >= 10000 and roce >= 20 and roe >= 15 and de <= 0.5:
        buckets.append("Core")

    # Growth Multiplier — ₹2,000 Cr+ emerging leaders
    if score >= 75 and mcap >= 2000 and yoy_sales >= 20 and yoy_profit >= 20 and rs_6m > 0 and dist_52w <= 15:
        buckets.append("Growth")

    # Opportunistic Momentum — massive acceleration
    if score >= 65 and yoy_profit >= 40 and rs_6m >= 15 and "SME" not in cats:
        buckets.append("Opportunistic")

    # Quality-On-Sale — Temporarily out of favor but high quality
    peg = r.get("PEG Ratio", 1.0)
    if peg is None: peg = 1.0
    
    cons_score = r.get("Consistency_Score", 0)
    fcf_margin = r.get("FCF Margin %")
    
    if score >= 60 and mcap >= 500 and de <= 1.0 and "SME" not in cats and roce >= 15 and (cons_score >= 6 or (fcf_margin is not None and fcf_margin > 0)):
        is_qos = (dist_52w > 10 and dist_52w <= 30 and peg < 1.0 and rs_6m > 0)
        
        # MACRO REGIME GATE: If Nifty is >15% below 52W high, loosen QOS criteria
        if nifty_dist_52w is not None and nifty_dist_52w > 15:
            is_qos = is_qos or (dist_52w > 10 and dist_52w <= 45 and peg < 1.5 and rs_6m > -15)
            
        if is_qos:
            buckets.append("Quality-On-Sale")

    return ", ".join(buckets) if buckets else None


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
    cmp = r.get("cmp", 0) or 0
    entry_price = r.get("entry_price", 0) or 0
    
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
    ema20 = r.get("ema_20", 0) or 0
    sma50 = r.get("sma_50", 0) or 0
    sma200 = r.get("sma_200", 0) or 0
    rs_6m = r.get("rs_6m", 0) or 0
    
    if cmp > ema20 and ema20 > 0: score += 10
    if cmp > sma50 and sma50 > 0: score += 10
    if cmp > sma200 and sma200 > 0: score += 10
    if rs_6m > 0: score += 10
    
    # 3. Fundamental Integrity (30 pts)
    # Mapping Piotroski/Fundamentals to our existing FM_Score
    fm_score = r.get("FM_Score", 0) or 0
    if fm_score >= 70: score += 15
    elif fm_score >= 50: score += 5
    
    pledge = r.get("Promoter_Pledge")
    if pledge is not None and pledge == 0: score += 10
    
    yoy_profit = r.get("YOY Profit %", 0) or 0
    if yoy_profit > 0: score += 5
    
    # 4. Sector & Momentum Regime (15 pts)
    # Using 6-month RS Rating (Percentile)
    rs_rating = r.get("RS_Rating", 0) or 0
    if rs_rating > 80: score += 15
    elif rs_rating > 50: score += 5
    
    # 5. Portfolio Context / Alpha Adjustments (15 pts)
    ai_conf = r.get("AI_Confidence", 0) or 0
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


def run_wealth_scan():
    """Runs a single iteration of the Wealth Engine scan."""
    from config import WATCHLIST_PATH, DATA_DIR, MIN_DAILY_LIQUIDITY_RUPEES_WEALTH
    from database import upsert_scanner_health

    WEALTH_PATH = os.path.join(DATA_DIR, "elite_wealth_system.parquet")
    logger.info("💰 Fund Manager Wealth Engine v2 Started Scan.")
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
                logger.error(f"❌ Wealth Engine failed to build watchlist: {e}")
                import database
                if not getattr(database, "DONT_SAVE_WEALTH", False):
                    upsert_scanner_health("Wealth Engine", "IDLE", error_msg="Watchlist build failed")
                return


        from database import download_parquet_from_db, upload_parquet_to_db
        
        # If cold boot (no local file), try to restore from DB instantly so dashboard isn't blank
        if not os.path.exists(WEALTH_PATH):
            download_parquet_from_db("wealth_engine", WEALTH_PATH)

        prev_wealth_df = pd.DataFrame()
        if os.path.exists(WEALTH_PATH):
            try:
                prev_wealth_df = pd.read_parquet(WEALTH_PATH)
            except Exception as e:
                logger.error(f"Failed to load prev_wealth_df: {e}")

        df = pd.read_parquet(WATCHLIST_PATH)

        # INJECT ORPHANED OPEN POSITIONS: If a stock is currently held but fell out of the fundamental watchlist,
        # we MUST still evaluate it so it can trigger a SELL signal.
        try:
            from database import get_connection
            from psycopg2.extras import RealDictCursor
            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT DISTINCT symbol FROM wealth_buy_alert WHERE is_closed = FALSE")
                    open_symbols = [row['symbol'] for row in cur.fetchall()]
            
            missing_symbols = [sym for sym in open_symbols if sym not in df["Stock"].values]
            if missing_symbols:
                logger.info(f"Injecting {len(missing_symbols)} orphaned open positions back into evaluation pipeline...")
                missing_df = pd.DataFrame([{"Stock": sym} for sym in missing_symbols])
                df = pd.concat([df, missing_df], ignore_index=True)
        except Exception as e:
            logger.warning(f"Failed to fetch open positions for injection: {e}")

        logger.info(f"💰 [WEALTH ENGINE] Calculating Fund Manager v2 metrics for {len(df)} elite stocks...")

        nifty_6m_ret, nifty_dist_52w = fetch_nifty_macro_state()
        if nifty_6m_ret is None:
            logger.info("Nifty Macro: UNAVAILABLE — suppressing macro gates")
        else:
            logger.info(f"💰 [WEALTH ENGINE] Nifty 6M Return: {nifty_6m_ret:.1f}%")

        clear_price_cache()
        rejection_counts = {}
        _rejection_lock = threading.Lock()

        # 🔧 CRITICAL FIX: Fetch ALL watchlist symbols in one batch BEFORE threading
        # This prevents cache pollution where subsequent threads get incomplete cache hits
        # and fallback to stale data from yesterday.
        logger.info(f"💰 [WEALTH ENGINE] Batch fetching 1D data for {len(df)} symbols...")
        all_symbols = df["Stock"].tolist()
        from price_cache import fetch_unified_historical
        all_historical_data = fetch_unified_historical(all_symbols, period="1y", interval="1d")
        
        # Handle rate limiting or fetch failures gracefully
        # Return empty dict (not None) so thread logic can fallback to individual fetches
        if all_historical_data is None:
            logger.warning(f"⚠️ Batch fetch returned None (rate-limited or API down). Threads will use fallback data.")
            all_historical_data = {}
        
        fetched_count = len(all_historical_data) if all_historical_data else 0
        logger.info(f"💰 [WEALTH ENGINE] Batch fetch complete. {fetched_count}/{len(df)} symbols have fresh data.")


        def process_symbol(idx, row, historical_cache=None):
            try:
                sym = row["Stock"]
                # Use pre-fetched historical data instead of fetching single symbol in thread
                tech = calculate_wealth_technicals(sym, nifty_6m_ret, historical_cache=historical_cache)
                
                # Fallback if Yahoo Finance fails
                if tech.get("cmp") is None and not prev_wealth_df.empty and sym in prev_wealth_df["Stock"].values:
                    prev_row = prev_wealth_df[prev_wealth_df["Stock"] == sym].iloc[0]
                    tech["cmp"] = prev_row.get("cmp")
                    tech["sma_50"] = prev_row.get("sma_50")
                    tech["sma_200"] = prev_row.get("sma_200")
                    tech["rs_6m"] = prev_row.get("rs_6m")
                    tech["dist_52w_high"] = prev_row.get("dist_52w_high")
                    tech["liquidity"] = prev_row.get("liquidity", 0.0)
                    
                    # New derived technicals
                    tech["RSI"] = prev_row.get("RSI", 50.0)
                    tech["ATR_Pct"] = prev_row.get("ATR_Pct", 0.0)
                    tech["momentum_score"] = prev_row.get("momentum_score", 0)
                    tech["momentum_confidence"] = prev_row.get("momentum_confidence", "LOW")
                    
                    # Explicit flag so signal logic can downgrade new buys
                    tech["used_fallback_data"] = True
                    tech["data_quality"] = DataQuality.CACHED_PREV_DAY.value
                    tech["fallback_timestamp"] = prev_row.get("fallback_timestamp", datetime.now(IST).isoformat())
                    
                    with _rejection_lock:
                        rejection_counts["stale_data"] = rejection_counts.get("stale_data", 0) + 1
                elif tech.get("is_stale"):
                    tech["used_fallback_data"] = True
                    tech["data_quality"] = "STALE_INTRADAY"
                    tech["fallback_timestamp"] = datetime.now(IST).isoformat()
                    with _rejection_lock:
                        rejection_counts["stale_data"] = rejection_counts.get("stale_data", 0) + 1
                    try:
                        from database import upsert_fetch_error
                        upsert_fetch_error('yfinance', 'WEALTH', sym, '1d', 'stale_data', 'using_yesterdays_cache')
                    except Exception:
                        pass
                    logger.warning(f"⚠️ YFinance failed for {sym}, using cached technicals from yesterday.")
                elif tech.get("cmp") is None:
                    with _rejection_lock:
                        rejection_counts["no_data"] = rejection_counts.get("no_data", 0) + 1
                    try:
                        from database import upsert_fetch_error
                        upsert_fetch_error('yfinance', 'WEALTH', sym, '1d', 'no_data', 'missing_data_no_fallback')
                    except Exception:
                        pass
                    return {"Stock": sym}
                else:
                    tech["used_fallback_data"] = False
                    tech["fallback_timestamp"] = None
                    
                tech["Stock"] = sym
                # PLEDGE SCRAPER DISABLED PER USER REQUEST
                # Note: The pledge dimension is currently intentionally inactive.
                # Any hold-score bonus or kill-gate relying on clean pledge is structurally neutralized.
                tech["Promoter_Pledge"] = None
                
                # Extract AI Concall Confidence
                try:
                    concall = get_recent_concall_analysis(sym)
                    if concall and isinstance(concall, dict) and "management_confidence" in concall:
                        tech["AI_Confidence"] = int(concall["management_confidence"])
                    else:
                        tech["AI_Confidence"] = 0
                except Exception as e:
                    logger.warning(f"AI Concall fetch failed for {sym}: {e}")
                    tech["AI_Confidence"] = 0

                return tech
            except Exception as e:
                logger.exception(f"❌ Error processing {row['Stock']}")
                try:
                    from database import upsert_fetch_error
                    upsert_fetch_error('yfinance', 'WEALTH', row.get('Stock', 'UNKNOWN'), '1d', 'processing_error', str(e))
                except Exception as e:
                    logger.exception(f"Failed to process {row['Stock']}: {e}")
                
                with _rejection_lock:
                    rejection_counts["processing_error"] = rejection_counts.get("processing_error", 0) + 1
                return {"Stock": row.get("Stock", "UNKNOWN")}

        technicals = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKER_COUNT) as executor:
            futures = {executor.submit(process_symbol, i, row, all_historical_data): i for i, row in df.iterrows()}
            completed = 0
            for future in concurrent.futures.as_completed(futures):
                try:
                    technicals.append(future.result())
                except Exception as e:
                    logger.exception(f"Worker failed unexpectedly: {e}")
                    # skip or append minimal row handled inside process_symbol
                completed += 1
                if completed % 50 == 0 or completed == len(df):
                    logger.info(f"💰 [WEALTH ENGINE] Progress: {completed}/{len(df)} stocks processed...")

        tech_df = pd.DataFrame(technicals)
        if not tech_df.empty and "cmp" in tech_df.columns and (tech_df["cmp"].isnull().all() or (tech_df["cmp"] == 0).all()):
            logger.error("❌ YFinance returned 0 prices. API might be down or rate-limited. Aborting this scan cycle.")
            import database
            if not getattr(database, "DONT_SAVE_WEALTH", False):
                try:
                    upsert_scanner_health("Wealth Engine", "DOWN", error_msg="CRITICAL: YFinance returned 0 prices. Rate limited.")
                except Exception:
                    pass
            return

        wealth_df = pd.merge(df, tech_df, on="Stock", how="left")

        # ── SECTOR VALUATION PRECOMPUTE (Requires N >= 3) ──
        sector_stats = {}
        if "Sector" in wealth_df.columns and "P/E Ratio" in wealth_df.columns:
            for sector, group in wealth_df.groupby("Sector"):
                valid_pes = group["P/E Ratio"].dropna()
                if len(valid_pes) >= 3:
                    sector_stats[sector] = {"median_pe": float(valid_pes.median())}

        if "rs_6m" in wealth_df.columns:
            wealth_df["RS_Rating"] = wealth_df["rs_6m"].rank(pct=True, ascending=True) * 100
        else:
            wealth_df["RS_Rating"] = 0

        # Apply 100-point score
        wealth_df["FM_Score"] = wealth_df.apply(lambda r: calculate_100_point_score(r, sector_stats), axis=1)
        
        # Calculate valuation & consistency score separately for dashboard visibility
        wealth_df["Valuation_Score"] = wealth_df.apply(lambda r: calculate_valuation_score(r, sector_stats), axis=1)
        wealth_df["Consistency_Score"] = wealth_df.apply(calculate_consistency_score, axis=1)
        wealth_df["Portfolio_Bucket"] = wealth_df.apply(lambda r: determine_portfolio_bucket(r, nifty_dist_52w), axis=1)

        if nifty_dist_52w is None:
            logger.warning("Using NO Nifty benchmark — macro gates suppressed")
            
        # ── MACRO SUPPRESSION & BEAR-MARKET VALUE ADD ──
        GLOBAL_BUY_SUPPRESSED = False
        suppression_reason = None
        
        degraded = rejection_counts.get("stale_data", 0) + rejection_counts.get("no_data", 0)
        fresh_ratio = 1.0 - (degraded / max(len(df), 1))
        breadth_df = wealth_df.dropna(subset=["cmp", "sma_200"])
        breadth_df = breadth_df[breadth_df["sma_200"] > 0]
        breadth_pct = ((breadth_df["cmp"] > breadth_df["sma_200"]).sum() / len(breadth_df) * 100) if len(breadth_df) else None
        
        if breadth_pct is not None and 30 <= breadth_pct <= 40:
            logger.warning(f"⚠️ Market Breadth Caution: Only {breadth_pct:.1f}% stocks above SMA200")
            
        if nifty_dist_52w is not None and nifty_dist_52w > 20:
            GLOBAL_BUY_SUPPRESSED = True
            suppression_reason = f"Nifty {nifty_dist_52w:.1f}% below 52W high"
        elif nifty_6m_ret is not None and nifty_6m_ret < -15:
            GLOBAL_BUY_SUPPRESSED = True
            suppression_reason = f"Nifty 6M return {nifty_6m_ret:.1f}%"
        elif breadth_pct is not None and breadth_pct < 30:
            GLOBAL_BUY_SUPPRESSED = True
            suppression_reason = f"Breadth weak: {breadth_pct:.1f}% above SMA200"
        elif fresh_ratio < 0.70:
            GLOBAL_BUY_SUPPRESSED = True
            suppression_reason = f"Fresh data only {fresh_ratio*100:.1f}%"
            
        if GLOBAL_BUY_SUPPRESSED:
            logger.warning(f"🚨 GLOBAL BUY SUPPRESSED: {suppression_reason}")

        # Load manual portfolio and active buy alerts to securely inject entry_price for drawdown protection
        portfolio_dict = {}
        try:
            from database import get_connection
            from psycopg2.extras import RealDictCursor
            with get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # 1. Manual portfolio (lowest priority)
                    cur.execute("SELECT symbol, entry_price, added_at::date::text as entry_date FROM manual_portfolio")
                    for r in cur.fetchall():
                        portfolio_dict[r['symbol']] = {'entry_price': r['entry_price'], 'entry_date': r['entry_date']}
                    
                    # 2. Wealth system active alerts (overrides manual)
                    cur.execute("SELECT symbol, alert_price as entry_price, alert_date::text as entry_date FROM wealth_buy_alert WHERE is_closed = FALSE")
                    for r in cur.fetchall():
                        portfolio_dict[r['symbol']] = {'entry_price': r['entry_price'], 'entry_date': r['entry_date']}
        except Exception as e:
            logger.warning(f"Failed to load active portfolio prices: {e}")

        # Inject entry_price into the dataframe BEFORE evaluation
        wealth_df["entry_price"] = wealth_df["Stock"].map(lambda s: portfolio_dict.get(s, {}).get("entry_price", 0.0))

        def apply_hold_score_with_tax(r):
            base_hold_score = calculate_hold_score(r)
            sym = r.get("Stock")
            if sym in portfolio_dict:
                p = portfolio_dict[sym]
                try:
                    from datetime import datetime
                    entry_date = datetime.strptime(p['entry_date'], "%Y-%m-%d").date()
                    cmp_price = r.get("cmp", p['entry_price']) or p['entry_price']
                    pnl_pct = ((cmp_price - p['entry_price']) / p['entry_price']) * 100 if p['entry_price'] > 0 else 0
                    tax_info = compute_tax_hold_bonus(entry_date, pnl_pct)
                    return min(100, base_hold_score + tax_info['bonus'])
                except Exception:
                    pass
            return base_hold_score

        # Apply Hold Score evaluation
        wealth_df["Hold_Score"] = wealth_df.apply(apply_hold_score_with_tax, axis=1)

        # Apply Hold Trend Analysis for open positions
        def get_hold_trend(r):
            sym = r.get("Stock")
            if sym in portfolio_dict:
                from wealth_hold_tracking import HoldScoreTrendAnalyzer
                trend = HoldScoreTrendAnalyzer.analyze_trend(sym)
                # If the trend analyzer flags a warning or sell, we can surface it
                if trend["action"] != "HOLD":
                    return trend["reason"]
                return "Stable"
            return "Not Held"

        wealth_df["hold_trend"] = wealth_df.apply(get_hold_trend, axis=1)

        # Buy/Sell Signals
        def get_signal(r):
            score = r.get("FM_Score", 0)
            hold_score = r.get("Hold_Score", 0)
            cmp = r.get("cmp", 0) or 0
            sma = r.get("sma_200", 0) or 0
            rs = r.get("rs_6m", 0) or 0
            sym = r.get("Stock")
            used_fallback = r.get("used_fallback_data", False)
            # 1. Exit Logic & Catastrophic Breakdown (Highest Precedence)
            hold_trend = r.get("hold_trend", "Stable")
            if "SELL REVIEW" in hold_trend or "Momentum Reversal" in hold_trend:
                return pd.Series({"Signal_Code": "SELL_REVIEW", "Signal_Reason": hold_trend})
            if hold_score < 45:
                return pd.Series({"Signal_Code": "SELL_REVIEW", "Signal_Reason": f"Hold Score: {hold_score}/100"})
            if rs < -40:
                return pd.Series({"Signal_Code": "SELL", "Signal_Reason": "Catastrophic RS Breakdown"})
            if sma > 0 and cmp > 0 and cmp < (0.75 * sma):
                return pd.Series({"Signal_Code": "SELL", "Signal_Reason": "Catastrophic Trend Collapse"})
                
            # 2. Check for Tax-Loss Harvesting signal (HOLD overrides BUY/neutral)
            if sym in portfolio_dict:
                p = portfolio_dict[sym]
                try:
                    from datetime import datetime
                    entry_date = datetime.strptime(p['entry_date'], "%Y-%m-%d").date()
                    cmp_price = r.get("cmp", p['entry_price']) or p['entry_price']
                    pnl_pct = ((cmp_price - p['entry_price']) / p['entry_price']) * 100 if p['entry_price'] > 0 else 0
                    tax_info = compute_tax_hold_bonus(entry_date, pnl_pct)
                    if tax_info.get("harvest_signal"):
                        return pd.Series({"Signal_Code": "HOLD", "Signal_Reason": f"Tax-Loss Harvest Opportunity: {pnl_pct:.1f}%"})
                except Exception:
                    pass

            # 3. Macro Suppression & Bear-Market Value-Add Logic
            bucket = str(r.get("Portfolio_Bucket", ""))
            
            if GLOBAL_BUY_SUPPRESSED:
                if "Quality-On-Sale" in bucket:
                    cons_score = r.get("Consistency_Score", 0)
                    val_score = r.get("Valuation_Score", 0)
                    roce = r.get("ROCE %", 0) or 0
                    fcf_margin = r.get("FCF Margin %")
                    path = r.get("Path", "")
                    mom_conf = r.get("momentum_confidence", "")
                    
                    fcf_ok = True if path == "Financial" else (fcf_margin is not None and fcf_margin > 0)
                    
                    if (
                        score >= 78 and
                        cons_score >= 6 and
                        val_score >= 5 and
                        cmp > 0 and sma > 0 and
                        cmp >= 0.95 * sma and
                        rs > -10 and
                        not used_fallback and
                        roce >= 15 and
                        fcf_ok and
                        mom_conf != "LOW"
                    ):
                        return pd.Series({"Signal_Code": "BUY", "Signal_Reason": f"Bear Market Value Add: {suppression_reason}"})
                return pd.Series({"Signal_Code": "SUPPRESS", "Signal_Reason": suppression_reason})
                
            # 4. Normal Market Accumulation (Stricter Gate)
            if score >= 82 and r.get("Consistency_Score", 0) >= 5 and r.get("Valuation_Score", 0) >= 3 and cmp > sma and sma > 0:
                if used_fallback:
                    return pd.Series({"Signal_Code": "SUPPRESS", "Signal_Reason": "Stale Data — Prevented Fake Buy"})
                if r.get("momentum_confidence", "") == "LOW":
                    return pd.Series({"Signal_Code": "HOLD", "Signal_Reason": "Low Momentum Quality"})
                return pd.Series({"Signal_Code": "BUY", "Signal_Reason": f"Score: {score}, Consistency: {r.get('Consistency_Score', 0)}"})
                
            # Mean Reversion Check (Only if not already a standard breakout BUY)
            if not used_fallback:
                from wealth_mean_reversion import get_mean_reversion_signal
                mr_code, mr_reason = get_mean_reversion_signal(r)
                if mr_code:
                    return pd.Series({"Signal_Code": mr_code, "Signal_Reason": mr_reason})
                
            return pd.Series({"Signal_Code": "", "Signal_Reason": ""})

        signal_df = wealth_df.apply(get_signal, axis=1)
        wealth_df["Signal_Code"] = signal_df["Signal_Code"]
        wealth_df["Signal_Reason"] = signal_df["Signal_Reason"]
        wealth_df["Signal"] = signal_df.apply(lambda x: f"{x['Signal_Code']} ({x['Signal_Reason']})" if x['Signal_Code'] and x['Signal_Reason'] else x['Signal_Code'], axis=1)
        
        # Calculate position sizing for all BUY signals
        def calculate_position_sizing(r):
            sig_code = r.get("Signal_Code", "")
            if sig_code != "BUY":
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
            r["position_shares"] = int(sizing["Position_Amount"] / cmp) if cmp > 0 else 0
            r["alloc_category"] = sizing["Alloc_Category"]
            return r

        wealth_df = wealth_df.apply(calculate_position_sizing, axis=1)

        # Apply sector caps to Core bucket for the dashboard
        core_capped = apply_sector_cap(wealth_df, "Portfolio_Bucket", "Core", max_stocks=15)
        core_symbols = set(core_capped["Stock"].tolist()) if not core_capped.empty else set()
        wealth_df["Core_Selected"] = wealth_df["Stock"].apply(lambda s: s in core_symbols)

        # Save BUY signals to wealth_buy_alert table for historical tracking
        try:
            from database import save_wealth_buy_alert, close_position, update_position_real_time_prices, DONT_SAVE_WEALTH
            buy_signals = wealth_df[wealth_df["Signal_Code"] == "BUY"]
            for _, row in buy_signals.iterrows():
                # HARD DEPLOYMENT GUARD: Never persist a BUY if it was somehow generated from fallback data
                if row.get("used_fallback_data", False):
                    logger.warning(f"🛡️ Deployment Guard Blocked persistence of BUY for {row.get('Stock')} due to used_fallback_data=True")
                    continue
                    
                symbol = row.get("Stock")
                cmp = row.get("cmp")
                fm_score = row.get("FM_Score")
                breakout = "Strength" if row.get("dist_52w_high", 100) > 5 else "Value"
                position_pct = row.get("position_pct")
                position_amount = row.get("position_amount")
                portfolio_bucket = row.get("Portfolio_Bucket", "Unknown")
                valuation_score = row.get("Valuation_Score", 0)
                position_shares = int(position_amount / cmp) if cmp and cmp > 0 and position_amount else 0
                if symbol and cmp:
                    if not DONT_SAVE_WEALTH:
                        save_wealth_buy_alert(
                            symbol, 
                            cmp, 
                            breakout_type=breakout, 
                            fm_score=fm_score,
                            position_pct=position_pct,
                            position_amount=position_amount,
                            position_shares=position_shares,
                            portfolio_bucket=portfolio_bucket,
                            valuation_score=valuation_score,
                            momentum_score=row.get("momentum_score"),
                            momentum_confidence=row.get("momentum_confidence"),
                            data_quality=row.get("data_quality"),
                            fallback_timestamp=row.get("fallback_timestamp")
                        )
            
            # Fetch REAL-TIME prices for all open positions (for accurate P&L calculation)
            try:
                # ONLY FETCH OPEN POSITIONS to prevent rate-limiting and timeouts!
                open_symbols = list(portfolio_dict.keys())
                realtime_metrics = {}
                if open_symbols:
                    logger.info(f"🔄 Fetching real-time prices for {len(open_symbols)} open positions...")
                    from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError
                    yf_syms = [f"{s.replace('_', '-')}.NS" for s in open_symbols]
                    
                    try:
                        yf_acquire()
                        try:
                            # Batch fetch
                            batch_data = yf.download(yf_syms, period="1d", progress=False)
                            closes = batch_data['Close'] if not batch_data.empty and 'Close' in batch_data else None
                        finally:
                            yf_release()
                    except CircuitOpenError as ce:
                        logger.error(f"YFinance circuit open; abort realtime batch fetch: {ce}")
                        closes = None
                    except Exception as e:
                        msg = str(e).lower()
                        if 'too many requests' in msg or 'rate limit' in msg:
                            record_rate_limit()
                        logger.warning(f"Batch real-time fetch failed for open positions: {e}")
                        closes = None

                    for i, symbol in enumerate(open_symbols):
                        yf_sym = yf_syms[i]
                        current_price = None
                        if closes is not None:
                            if isinstance(closes, pd.Series):
                                if not pd.isna(closes.iloc[-1]):
                                    current_price = float(closes.iloc[-1])
                            elif yf_sym in closes and not pd.isna(closes[yf_sym].iloc[-1]):
                                current_price = float(closes[yf_sym].iloc[-1])
                        
                        symbol_row = wealth_df[wealth_df["Stock"] == symbol]
                        current_score = None
                        if not symbol_row.empty:
                            r = symbol_row.iloc[0]
                            val = r.get("Hold_Score")
                            if pd.notna(val):
                                current_score = float(val)
                                
                            # Save hold score history for tracking trends
                            if not DONT_SAVE_WEALTH:
                                from database import save_hold_score_history
                                save_hold_score_history(
                                    symbol=symbol,
                                    hold_score=current_score,
                                    fm_score=float(r.get("FM_Score", 0)),
                                    rs_6m=float(r.get("rs_6m", 0)),
                                    cmp=current_price or float(r.get("cmp", 0)),
                                    sma_200=float(r.get("sma_200", 0))
                                )

                        if current_price and current_price > 0:
                            realtime_metrics[symbol] = {"price": float(current_price), "score": current_score}
                    
                    if realtime_metrics:
                        if not DONT_SAVE_WEALTH:
                            update_position_real_time_prices(realtime_metrics)
            except Exception as e:
                logger.warning(f"⚠️  Could not fetch real-time prices: {e}")
            
            # Auto-close positions when SELL signal detected
            sell_signals = wealth_df[wealth_df["Signal_Code"] == "SELL"]
            for _, row in sell_signals.iterrows():
                symbol = row.get("Stock")
                cmp = row.get("cmp")
                signal_text = row.get("Signal")
                if symbol and cmp:
                    if not DONT_SAVE_WEALTH:
                        close_position(symbol, cmp, signal_text)
        except Exception as e:
            logger.warning(f"⚠️  Could not process buy/sell alerts: {e}")

        # Persist wealth dataframe unless dry-run mode is active
        from database import DONT_SAVE_WEALTH
        if not DONT_SAVE_WEALTH:
            wealth_df.to_parquet(WEALTH_PATH, index=False)
            upload_parquet_to_db("wealth_engine", WEALTH_PATH)
        else:
            logger.info("🧪 DONT_SAVE_WEALTH enabled — skipping parquet save and DB upload")

        if "used_fallback_data" in wealth_df.columns:
            valid_buys = wealth_df[(wealth_df["Signal_Code"] == "BUY") & ~wealth_df["used_fallback_data"]]
        else:
            valid_buys = wealth_df[wealth_df["Signal_Code"] == "BUY"]
        buy_count = len(valid_buys)
        
        core_count = len(core_capped)
        logger.info(f"✅ [WEALTH ENGINE] Updated | Core: {core_count} | Buys: {buy_count} | Total: {len(wealth_df)}")
        
        import database
        if not getattr(database, "DONT_SAVE_WEALTH", False):
            if GLOBAL_BUY_SUPPRESSED:
                health_status = "DEGRADED" if fresh_ratio < 0.70 else "OK"
                upsert_scanner_health("Wealth Engine", health_status, last_success=datetime.now(IST).isoformat(), today_alerts=buy_count, error_msg=f"BUY SUPPRESSED: {suppression_reason}")
            else:
                upsert_scanner_health("Wealth Engine", "OK", last_success=datetime.now(IST).isoformat(), today_alerts=buy_count)

        # Weekly Telegram Alert removed (2026-06-17)

    except Exception as e:
        logger.exception(f"❌ [WEALTH ENGINE] Scan crashed: {e}")
        import database
        if not getattr(database, "DONT_SAVE_WEALTH", False):
            try:
                upsert_scanner_health("Wealth Engine", "DOWN", error_msg=str(e))
            except Exception:
                pass
        raise e
