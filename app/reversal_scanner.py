# =====================================================================================
# app/reversal_scanner.py (SCHEDULER READY) — v6 REVISED
# DEEP DISCOUNT & MEAN REVERSION SCANNER (With Valuation Metrics)
#
# v6 CHANGELOG (backtest improvement pass — target: lift 44% win rate):
#   [FIX 1] Failed-reversal cooldown: suppress re-alerts on stocks that recently
#           stopped out / failed follow-through (biggest strategy leak).
#   [FIX 2] Trend structure: strict close > SMA50 gate is now MANDATORY (recovery,
#           not just oversold bounce). Extra preference when also > SMA200.
#   [FIX 3] Trend scoring order fixed (strongest state evaluated first).
#   [FIX 4] Single volume threshold source of truth — removed hidden 2.0 gate.
#   [FIX 5] Reduced volume scoring weight (confirmation, not primary driver).
#   [FIX 6] Removed fake regime decoration (was hard-coded BEAR). Now honest NEUTRAL.
#   [FIX 7] One clean fixed drop band (regime-based flex removed).
#   [FIX 8] Removed non-portable macd > 2.0 hard cap.
#   [FIX 9] MACD normalization deferred (kept simple raw macd_hist scoring).
#   [FIX 10] Score comments aligned exactly with implementation.
# =====================================================================================
import pandas as pd
import logging
import os
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta
from typing import Any, Optional

from technical_indicators import apply_indicators
from memory_profiler import MemoryProfiler
from database import init_db, save_alert_if_new, save_candidate, upsert_fetch_error, upsert_scanner_health, verify_alerts_saved_today
from price_cache import fetch_watchlist_data
from watchlist_cache import get_watchlist
from config import (
    CLIMAX_VOLUME_LOOKBACK, 
    MIN_CANDLE_RANGE_PCT, 
    MIN_STOCK_PRICE,
    REVERSAL_CONFIG,
    ALERT_COOLDOWN_MINUTES,
    ACTIVE_ALGO_VERSION
)
from sl_target_helper import compute_sl_and_target
from delivery_data import fetch_delivery_data
from trade_ranking_engine import TradeRankingEngine
from macro_utils import MarketRegimeEngine, get_macro_regime, get_nifty_20d_return
from strategy_policy import StrategyPolicyEngine
from core_enums import ProviderResult
from core_models import ScanFailure


logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── REVERSAL PARAMETERS ──────────────────────────────────────────────────────────────
# [FIX 7] Single clean fixed drop band. Tightened lower bound to 20% (was 18%) to
#         avoid shallow, low-conviction pullbacks; capped at 45% sweet-spot ceiling
#         (was 60%) to avoid deep falling knives. Regime-based flex removed (FIX 6).
MIN_DROP_FROM_52W_HIGH = REVERSAL_CONFIG["MIN_DROP_FROM_52W_HIGH"]
MAX_DROP_FROM_52W_HIGH = REVERSAL_CONFIG["MAX_DROP_FROM_52W_HIGH"]

RSI_OVERSOLD_THRESHOLD = REVERSAL_CONFIG["RSI_OVERSOLD_THRESHOLD"]
RSI_CURL_MIN           = REVERSAL_CONFIG["RSI_CURL_MIN"]

# [FIX 4] SINGLE SOURCE OF TRUTH for volume. Previously MIN_VOLUME_RATIO=1.5 was
#         shadowed by a hidden `if vol_ratio < 2.0: continue`. Now exactly one gate.
MIN_VOLUME_RATIO       = REVERSAL_CONFIG["MIN_VOLUME_RATIO"]

# ── QUALITY FILTERS (high-quality stocks only) ───────────────────────────────────────
# MIN_STOCK_PRICE imported from config (₹100)
MIN_AVG_DAILY_VOLUME   = REVERSAL_CONFIG["MIN_AVG_DAILY_VOLUME"]
MIN_ROE                = REVERSAL_CONFIG["MIN_ROE"]
MIN_YOY_REVENUE_GROWTH = REVERSAL_CONFIG["MIN_YOY_REVENUE_GROWTH"]
MAX_DROP_BELOW_SMA200  = REVERSAL_CONFIG["MAX_DROP_BELOW_SMA200"]
# ─────────────────────────────────────────────────────────────────────────────────────

# [FIX 1] FAILED-REVERSAL COOLDOWN ────────────────────────────────────────────────────
# Suppress new reversal alerts on a symbol for N trading days after its previous
# reversal alert stopped out OR failed to follow through. This directly attacks the
# biggest strategy leak: the same beaten-down stock alerting, failing, and re-alerting.
REVERSAL_COOLDOWN_TRADING_DAYS = REVERSAL_CONFIG["REVERSAL_COOLDOWN_TRADING_DAYS"]
# ─────────────────────────────────────────────────────────────────────────────────────

REVERSAL_MIN_BARS = 250
DEFAULT_PLEDGE_PENALTY = 15.0
STALE_DEGRADED_RATIO = 0.15
MIN_FETCH_RATIO = 0.85

def _canonical_symbol(s: str) -> str:
    if not s:
        return ""
    return str(s).split('.')[0].upper()

def _to_ist_date(v: Any) -> date:
    if v is None:
        return datetime.now(IST).date()
    try:
        ts = pd.to_datetime(v)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(IST).date()
    except Exception as e:
        logger.warning(f"Failed to parse timestamp '{v}': {e}. Defaulting to today IST.")
        return datetime.now(IST).date()

def _req_float(series, key: str) -> Optional[float]:
    if isinstance(series, pd.DataFrame):
        raise TypeError(f"_req_float called with DataFrame (key={key}); use .iloc[-1] to pass a single row")
    if series is None or key not in series:
        return None
    val = series.get(key)
    if isinstance(val, pd.Series):
        val = val.iloc[0] if len(val) > 0 else None
    if val is None or pd.isna(val):
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None

def _opt_float(series, key: str, default: float) -> float:
    res = _req_float(series, key)
    return default if res is None else res

# ── REVERSAL SCORE THRESHOLDS ────────────────────────────────────────────────────────
# [FINDING-8B FIX] Lowered from 67 to 62 to work with wider MACD window.
MIN_REVERSAL_SCORE = 62   # minimum to generate an alert (out of 100)

REGIME_REVERSAL_PREMIUM = {
    "STRONG_BEAR": 8,
    "BEAR":        4,
    "NEUTRAL":     0,
    "BULL":        0,
    "STRONG_BULL": 0,
}
def _parse_percent_value(val) -> Optional[float]:
    """
    [FIX 4 — PERCENT_PARITY_v1.0] Single source of truth for fundamental percentage units.
    """
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    try:
        f_val = float(val)
    except (ValueError, TypeError):
        return None
    if abs(f_val) < 2.0:
        return f_val * 100.0
    return f_val


def _macd_momentum_present(ticker: pd.DataFrame) -> bool:
    """
    [FIX 6 — MACD_PRESENT_TENSE_v1.0] The 20-bar crossover window alone let JUNK through:
    a stock that crossed 19 bars ago and has since rolled over still passed the gate, and
    the scorer awards 0 MACD points when macd_hist <= 0, so these arrived as low-quality
    alerts that squeezed past the threshold on volume/category points alone.

    Momentum is "present" when MACD is still above its signal line, OR the histogram is
    rising vs the previous bar (i.e. a cross is imminent / deceleration is reversing).
    The 20-bar window is retained as the FRESHNESS test; this is the STATE test.
    """
    if "MACD" not in ticker.columns or "MACD_SIGNAL" not in ticker.columns or len(ticker) < 2:
        return False
    try:
        macd_now = float(ticker["MACD"].iloc[-1])
        signal_now = float(ticker["MACD_SIGNAL"].iloc[-1])
        if macd_now > signal_now:
            return True
        if "MACD_HIST" in ticker.columns:
            hist_now = float(ticker["MACD_HIST"].iloc[-1])
            hist_prev = float(ticker["MACD_HIST"].iloc[-2])
            return hist_now > hist_prev
    except (TypeError, ValueError, KeyError, IndexError):
        return False
    return False


def _is_climax_top(ticker: pd.DataFrame, close_price: float, candle_high: float, candle_low: float, vol_ratio: Optional[float]) -> bool:
    """
    [FIX 8 — UI_PARITY_v1.0] Extracted from _run_scan so evaluate_reversal_symbol applies the
    IDENTICAL climax-top disqualifier. Returns False if vol_ratio is None (authorized bypass).
    """
    if vol_ratio is None or candle_high <= candle_low:
        return False
    lookback_ct = min(CLIMAX_VOLUME_LOOKBACK, len(ticker) - 1)
    if lookback_ct < 5:
        return False
    try:
        latest_vol = float(ticker["Volume"].iloc[-1])
        max_prior_vol = float(ticker["Volume"].iloc[-lookback_ct - 1:-1].max())
        candle_rng = candle_high - candle_low
        if candle_rng <= 0 or latest_vol <= max_prior_vol:
            return False
        upper_wick_pct = (candle_high - close_price) / candle_rng
        close_pos = (close_price - candle_low) / candle_rng
        return upper_wick_pct > 0.25 and close_pos < 0.40
    except (TypeError, ValueError, KeyError, IndexError):
        return False

# =====================================================================================
# REVERSAL-SPECIFIC SCORING (v6 — re-weighted)
#
# [FIX 5 + FIX 10] Volume de-emphasized; trend structure promoted. Weights below
# match the implementation EXACTLY (no divergence between comment and code):
#
#   Trend structure    — 25 pts max  (SMA50 reclaim + SMA200 = recovery, core signal)
#   SMA200 proximity   — 15 pts max  (closer to SMA200 = less falling-knife risk)
#   Volume confirmation— 15 pts max  (REDUCED: confirmation, not primary driver)
#   MACD momentum      — 15 pts max  (stronger MACD flip = stronger reversal)
#   RSI curl quality   — 15 pts max  (faster RSI recovery from oversold)
#   Category quality   — 10 pts max  (fundamental tier from daily builder)
#   Drop sweet spot    —  5 pts max  (sweet-spot bonus within the fixed band)
#   R:R quality        —  5 pts max  (reward > 2.5:1 risk-reward setups)
#   Delivery + OBV     — bonus pts   (institutional accumulation confirmation)
#   ──────────────────────────────────────
#   Capped at 100.
# =====================================================================================

_REV_CATEGORY_SCORES = {
    "Debt-Free Cash Generator": 10, "Wealth Compounder": 10, "Top Bank/NBFC": 10,
    "Long Term Compounder": 10,
    "Dividend Aristocrat": 9,
    "Capital Efficient": 9, "Efficient Lender": 9,
    "Undervalued Growth": 8, "High Momentum": 8, "Fast Growing Financial": 8,
    "Consistent Performer": 6,
    "Blue Chip Stable": 5, "Blue Chip Financial": 5,
    "Recovery Play": 3, "Financial Recovery": 3,
}


def _score_reversal(
        vol_ratio: Optional[float],
        drop_pct: float,
        current_rsi: float,
        past_10_rsi_min: float,
        macd_hist: Optional[float],
        pct_below_sma200: Optional[float],
        category: str,
        rr_ratio: Optional[float],
        trend_score: int = 10,
        above_sma50: Optional[bool] = None,
        above_sma200: Optional[bool] = None,
        obv_trend: Optional[int] = None,
        delivery_pct: Optional[float] = None,
        close_price: Optional[float] = None,
        symbol: Optional[str] = None,
        promoter_pledge_pct: Optional[float] = None,
        atr_val: Optional[float] = None,
        weights: Optional[dict] = None,
        min_drop_floor: float = MIN_DROP_FROM_52W_HIGH,
) -> int:
    """Score a reversal setup from 0-100 based on quality dimensions (v6 weights)."""
    score = 0

    # ── Trend structure (25 pts) — CORE recovery signal ──
    score += trend_score

    # ── SMA200 proximity (15 pts) — closer = safer entry ──
    if pct_below_sma200 is not None:
        if pct_below_sma200 <= 3.0:    score += 15  # very close to / above SMA200
        elif pct_below_sma200 <= 8.0:  score += 11
        elif pct_below_sma200 <= 15.0: score += 7
        elif pct_below_sma200 <= 20.0: score += 3

    # ── Volume confirmation (15 pts) ──
    if vol_ratio is not None:
        if vol_ratio >= 5.0:   score += 15
        elif vol_ratio >= 3.5: score += 12
        elif vol_ratio >= 2.5: score += 9
        elif vol_ratio >= MIN_VOLUME_RATIO: score += 5

    # ── MACD momentum (15 pts) ──
    if macd_hist is not None and atr_val is not None and atr_val > 0:
        try:
            mh_atr = float(macd_hist) / float(atr_val)
            if mh_atr >= 0.15:   score += 15   # strong bullish momentum (>15% of daily ATR)
            elif mh_atr >= 0.05: score += 10   # moderate bullish momentum (>5% of daily ATR)
            elif mh_atr > 0.0:   score += 5    # turning positive
        except (TypeError, ValueError):
            pass
    elif macd_hist is not None and close_price is not None and close_price > 0:
        try:
            mh_norm = (float(macd_hist) / float(close_price)) * 100
            if mh_norm >= 0.5:   score += 15
            elif mh_norm >= 0.2: score += 10
            elif mh_norm > 0:    score += 5
        except (TypeError, ValueError):
            pass

    # ── RSI curl quality (15 pts) — bigger recovery = stronger signal ──
    rsi_recovery = current_rsi - past_10_rsi_min
    if rsi_recovery >= 20:   score += 15
    elif rsi_recovery >= 12: score += 12
    elif rsi_recovery >= 8:  score += 8
    elif rsi_recovery >= 5:  score += 5

    # ── Category quality (10 pts) ──
    cat_lower = category.lower() if category else ""
    for cat_label, cat_pts in _REV_CATEGORY_SCORES.items():
        if cat_label.lower() in cat_lower:
            score += cat_pts
            break

    # ── Drop sweet spot / penalty (5 pts) ──
    if 25.0 <= drop_pct <= 40.0:
        score += 5
    elif min_drop_floor <= drop_pct < 25.0:
        score += 3
    elif 40.0 < drop_pct <= MAX_DROP_FROM_52W_HIGH:
        score += 3

    # ── R:R quality (5 pts) ──
    if rr_ratio is not None:
        if rr_ratio >= 3.5:   score += 5
        elif rr_ratio >= 2.5: score += 3
        elif rr_ratio >= 2.0: score += 1

    # ── OBV confirmation bonus (5 pts) ──
    if obv_trend is not None and obv_trend == 1:
        score += 5

    # ── Delivery conviction bonus (5 pts) ──
    if delivery_pct is not None:
        if delivery_pct >= 50.0:   score += 5
        elif delivery_pct >= 35.0: score += 3
        elif delivery_pct >= 25.0: score += 1

    inst_bonus = 0
    if symbol is not None:
        try:
            from block_deal_detector import compute_inst_bonus
            inst_bonus = compute_inst_bonus(symbol, score)
        except Exception as e:
            logger.warning(f"Error checking institutional footprints in Reversal: {e}")
            inst_bonus = 0

    # ── Bayesian Pledge Penalty ──
    pledge_penalty = 0
    if promoter_pledge_pct is not None and promoter_pledge_pct > 10.0:
        if weights is None:
            logger.warning(f"Pledge penalty fallback applied for {symbol}: DB weights table unavailable")
            max_penalty = DEFAULT_PLEDGE_PENALTY
        else:
            max_penalty = float(weights.get("PLEDGE_PENALTY", DEFAULT_PLEDGE_PENALTY))
        scale = min(1.0, (promoter_pledge_pct - 10.0) / 40.0)
        pledge_penalty = round(abs(max_penalty) * scale)
        if pledge_penalty > 0:
            score -= pledge_penalty
            if symbol:
                logger.warning(f"  -{pledge_penalty} [{symbol}] Promoter Pledge Penalty ({promoter_pledge_pct:.1f}% pledge)")

    return max(0, min(score + inst_bonus, 100))


def _evaluate_candidate(
    symbol: str,
    df: pd.DataFrame,
    fund_data: Optional[dict] = None,
    regime_ctx: Optional[dict] = None,
    weights: Optional[dict] = None,
    pledge_map: Optional[dict] = None,
    delivery_map: Optional[dict] = None,
    is_synthetic_bar: bool = False,
    is_synthetic_no_vol: bool = False,
) -> dict:
    """
    Pure evaluation engine called by BOTH _run_scan and evaluate_reversal_symbol.
    """
    if df is None or df.empty or len(df) < REVERSAL_MIN_BARS:
        return {
            "passed": False,
            "reject_reason": f"Insufficient historical bars ({len(df) if df is not None else 0} < {REVERSAL_MIN_BARS})",
            "reject_code": "insufficient_history",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    latest = df.iloc[-1]
    close_price = _req_float(latest, "Close")
    candle_high = _req_float(latest, "High")
    candle_low = _req_float(latest, "Low")
    candle_open = _req_float(latest, "Open")
    high_52w = _req_float(latest, "HIGH_52W")
    current_rsi = _req_float(latest, "RSI")
    ema20 = _req_float(latest, "EMA20")
    sma50 = _req_float(latest, "SMA50")
    sma200 = _req_float(latest, "SMA200")
    atr_val = _req_float(latest, "ATR")

    if any(v is None for v in [close_price, candle_high, candle_low, candle_open, high_52w, current_rsi, ema20, sma50, atr_val]):
        return {
            "passed": False,
            "reject_reason": "Missing or NaN mandatory technical indicators",
            "reject_code": "bad_indicators",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    candle_range = candle_high - candle_low

    # Unconditionally compute avg_vol_20d so it's always available for volume threshold checks
    avg_vol_20d = float(df["Volume"].iloc[-21:-1].mean()) if len(df) >= 22 else float(df["Volume"].mean())

    if is_synthetic_bar and is_synthetic_no_vol:
        vol_ratio = None
    elif is_synthetic_bar:
        snap_vol = _req_float(latest, "Volume") or 0.0
        vol_ratio = snap_vol / avg_vol_20d if avg_vol_20d > 0 else 1.0
    else:
        latest_vol = _req_float(latest, "Volume") or 0.0
        vol_ratio = latest_vol / avg_vol_20d if avg_vol_20d > 0 else 1.0

    if is_synthetic_bar and candle_range <= 0:
        return {
            "passed": False,
            "reject_reason": "Synthetic bar zero range",
            "reject_code": "thin_spread",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    if candle_range <= 0 or close_price <= 0:
        return {
            "passed": False,
            "reject_reason": f"Zero or invalid candle range ({candle_range:.2f}) / price ({close_price:.2f})",
            "reject_code": "thin_spread",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    # 1. 52W High Drop Band Check
    drop_pct = ((high_52w - close_price) / high_52w) * 100.0 if high_52w > 0 else 0.0
    cat_str = str(fund_data.get("Category", "")) if fund_data else ""
    is_quality_cat = any(q in cat_str.lower() for q in ["wealth", "blue chip", "debt-free"])
    effective_min_drop = 15.0 if is_quality_cat else MIN_DROP_FROM_52W_HIGH

    if drop_pct < effective_min_drop or drop_pct > MAX_DROP_FROM_52W_HIGH:
        return {
            "passed": False,
            "reject_reason": f"Drop from 52W High {drop_pct:.1f}% outside {effective_min_drop:.1f}%–{MAX_DROP_FROM_52W_HIGH:.1f}% correction band",
            "reject_code": "drop_band",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    # 2. Minimum Stock Price
    if close_price < MIN_STOCK_PRICE:
        return {
            "passed": False,
            "reject_reason": f"Close ₹{close_price:.2f} < ₹{MIN_STOCK_PRICE:.0f} minimum price floor",
            "reject_code": "low_price",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    # 3. Minimum Average Daily Volume Liquidity
    if avg_vol_20d < MIN_AVG_DAILY_VOLUME:
        return {
            "passed": False,
            "reject_reason": f"20D Avg Volume {avg_vol_20d:.0f} < {MIN_AVG_DAILY_VOLUME:.0f} shares minimum liquidity",
            "reject_code": "low_liquidity",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    # 4. SMA200 Proximity & Falling Knife Floor
    pct_below_sma200 = None
    if sma200 is not None and sma200 > 0:
        pct_below_sma200 = ((sma200 - close_price) / sma200) * 100.0
        if pct_below_sma200 > MAX_DROP_BELOW_SMA200:
            return {
                "passed": False,
                "reject_reason": f"Stock is {pct_below_sma200:.1f}% below SMA200 (max allowed: {MAX_DROP_BELOW_SMA200}%)",
                "reject_code": "drop_band",
                "score": 0,
                "sl_result": {},
                "context": {},
            }

    # 5. Fundamentals Quality Gates
    if fund_data:
        roe_val = _parse_percent_value(fund_data.get("ROE %"))
        if roe_val is not None and roe_val < MIN_ROE:
            return {
                "passed": False,
                "reject_reason": f"ROE {roe_val:.1f}% < {MIN_ROE}% minimum threshold",
                "reject_code": "fundamental_filter",
                "score": 0,
                "sl_result": {},
                "context": {},
            }
        rev_growth = _parse_percent_value(fund_data.get("YOY Revenue %"))
        if rev_growth is not None and rev_growth < MIN_YOY_REVENUE_GROWTH:
            return {
                "passed": False,
                "reject_reason": f"YoY Revenue Growth {rev_growth:.1f}% < {MIN_YOY_REVENUE_GROWTH}% minimum threshold",
                "reject_code": "fundamental_filter",
                "score": 0,
                "sl_result": {},
                "context": {},
            }

    # 6. Technical Indicator Gates
    if close_price < ema20:
        return {
            "passed": False,
            "reject_reason": f"Close ₹{close_price:.2f} < EMA20 ₹{ema20:.2f} (must hold 20 EMA for reversal)",
            "reject_code": "ema_filter",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    rsi_window = df["RSI"].iloc[-25:] if len(df) >= 25 else df["RSI"]
    rsi_window = rsi_window.dropna()
    if len(rsi_window) < 5:
        return {
            "passed": False,
            "reject_reason": "RSI data insufficient (too many NaN values in 25-bar window)",
            "reject_code": "bad_indicators",
            "score": 0,
            "sl_result": {},
            "context": {},
        }
    past_rsi_min = float(rsi_window.min())
    if current_rsi < RSI_CURL_MIN or past_rsi_min > RSI_OVERSOLD_THRESHOLD:
        return {
            "passed": False,
            "reject_reason": f"RSI condition failed: current RSI={current_rsi:.1f} (min {RSI_CURL_MIN}), 25-bar min RSI={past_rsi_min:.1f} (max {RSI_OVERSOLD_THRESHOLD})",
            "reject_code": "failed_pattern",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    if len(df) >= 3:
        rsi_tail = df["RSI"].iloc[-3:]
        rsi_diffs = rsi_tail.diff().dropna()
        if not rsi_diffs.empty and (rsi_diffs < -0.5).all():
            return {
                "passed": False,
                "reject_reason": f"RSI declining over last 3 bars: {list(rsi_tail.round(1))}",
                "reject_code": "failed_pattern",
                "score": 0,
                "sl_result": {},
                "context": {},
            }

    if not _macd_momentum_present(df):
        return {
            "passed": False,
            "reject_reason": "MACD momentum absent (MACD < SIGNAL and MACD_HIST not rising)",
            "reject_code": "macd_stale",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    if vol_ratio is not None and vol_ratio < MIN_VOLUME_RATIO:
        return {
            "passed": False,
            "reject_reason": f"Volume ratio {vol_ratio:.2f}x < {MIN_VOLUME_RATIO}x minimum volume confirmation",
            "reject_code": "low_volume",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    if _is_climax_top(df, close_price, candle_high, candle_low, vol_ratio=vol_ratio):
        return {
            "passed": False,
            "reject_reason": "Climax top detected (record volume with upper wick dump)",
            "reject_code": "climax_top",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    # 7. Compute Stop Loss and Target
    sl_args = {
        "df": df,
        "entry_price": close_price,
        "scanner": "REVERSAL",
        "adx": _opt_float(latest, "ADX", 20.0),
        "rsi": current_rsi,
        "macd_hist": _opt_float(latest, "MACD_HIST", 0.0),
        "atr_pct": _opt_float(latest, "ATR_PCT", 2.0),
        "swing_low": _opt_float(latest, "SWING_LOW", candle_low),
        "swing_high": _opt_float(latest, "SWING_HIGH", candle_high),
        "bb_upper": _opt_float(latest, "BB_UPPER", candle_high),
        "bb_lower": _opt_float(latest, "BB_LOWER", candle_low),
        "bb_mid": _opt_float(latest, "BB_MID", close_price),
        "s1": _opt_float(latest, "S1", candle_low),
        "s2": _opt_float(latest, "S2", candle_low),
        "r1": _opt_float(latest, "R1", candle_high),
        "r2": _opt_float(latest, "R2", candle_high),
        "swing_low_raw": _opt_float(latest, "SWING_LOW_RAW", candle_low),
        "swing_high_raw": _opt_float(latest, "SWING_HIGH_RAW", candle_high),
        "candle_low": candle_low,
        "vwap": _opt_float(latest, "VWAP", close_price),
        "ema20": ema20,
        "sma50": sma50,
        "sma200": sma200,
    }

    try:
        sl_res = compute_sl_and_target(**sl_args)
    except Exception as e:
        logger.warning(f"[REVERSAL] {symbol} compute_sl_and_target failed: {e}")
        return {
            "passed": False,
            "reject_reason": f"SL/Target computation error: {e}",
            "reject_code": "low_rr",
            "score": 0,
            "sl_result": {},
            "context": {},
        }

    if sl_res.get("is_rejected"):
        return {
            "passed": False,
            "reject_reason": sl_res.get("rejection_reason", "SL/Target engine rejected setup"),
            "reject_code": "low_rr",
            "score": 0,
            "sl_result": sl_res,
            "context": {},
        }

    # 8. Score Candidate
    above_sma50 = (close_price > sma50) if sma50 else False
    above_sma200 = (close_price > sma200) if sma200 else False
    trend_score = 25 if (above_sma50 and above_sma200) else (18 if above_sma50 else 10)
    can_sym = _canonical_symbol(symbol)
    obv_trend = int(_opt_float(latest, "OBV_TREND", 0))
    delivery_pct = (delivery_map.get(symbol) or delivery_map.get(can_sym)) if delivery_map else None
    pledge_pct = (pledge_map.get(symbol) or pledge_map.get(can_sym)) if pledge_map else None
    macd_hist = _req_float(latest, "MACD_HIST")

    past_10_rsi = df["RSI"].iloc[-10:].dropna() if len(df) >= 10 else df["RSI"].dropna()
    past_10_rsi_min = float(past_10_rsi.min()) if len(past_10_rsi) > 0 else current_rsi

    score = _score_reversal(
        vol_ratio=vol_ratio,
        drop_pct=drop_pct,
        current_rsi=current_rsi,
        past_10_rsi_min=past_10_rsi_min,
        macd_hist=macd_hist,
        pct_below_sma200=pct_below_sma200,
        category=cat_str,
        rr_ratio=sl_res.get("natural_rr"),
        trend_score=trend_score,
        close_price=close_price,
        obv_trend=obv_trend,
        delivery_pct=delivery_pct,
        symbol=symbol,
        promoter_pledge_pct=pledge_pct,
        atr_val=atr_val,
        weights=weights,
        min_drop_floor=effective_min_drop,
    )

    regime = regime_ctx.get("current_regime", "NEUTRAL") if regime_ctx else "NEUTRAL"
    score_premium = REGIME_REVERSAL_PREMIUM.get(regime, 0)
    effective_min_score = MIN_REVERSAL_SCORE + score_premium

    if score < effective_min_score:
        return {
            "passed": False,
            "reject_reason": f"Score {score} < {effective_min_score} minimum threshold (regime: {regime})",
            "reject_code": "low_score",
            "score": score,
            "sl_result": sl_res,
            "context": {},
        }

    # Build Signal & Context
    signals = []
    if above_sma50:
        signals.append("🎯 Reclaimed 20 EMA & SMA50")
    else:
        signals.append("🎯 Reclaimed 20 EMA (below SMA50)")

    signals.append(f"📉 Down {drop_pct:.1f}% from 52W High")
    signals.append(f"🔄 RSI Oversold Bounce (RSI={current_rsi:.1f}, 25B min={past_rsi_min:.1f})")
    if vol_ratio is not None:
        signals.append(f"📊 Vol Ratio {vol_ratio:.1f}x (20D Avg)")
    if pct_below_sma200 is not None:
        signals.append(f"📍 {pct_below_sma200:.1f}% below SMA200")
    if obv_trend == 1:
        signals.append("🟢 OBV Accumulation")

    context = {
        "score": score,
        "trend_score": trend_score,
        "entry_price": close_price,
        "drop_pct": round(drop_pct, 1),
        "stop_loss": sl_res.get("stop_loss"),
        "target": sl_res.get("target"),
        "risk_reward": sl_res.get("natural_rr"),
        "signals": signals,
        "technicals": {
            "close": close_price,
            "rsi": current_rsi,
            "ema20": ema20,
            "sma50": sma50,
            "sma200": sma200,
            "volume_ratio": round(vol_ratio, 2) if vol_ratio is not None else None,
            "volume_bypassed": True if vol_ratio is None else False,
            "drop_from_52w_high": round(drop_pct, 1),
            "pct_below_sma200": round(pct_below_sma200, 1) if pct_below_sma200 is not None else None,
        }
    }

    return {
        "passed": True,
        "reject_reason": None,
        "score": score,
        "sl_result": sl_res,
        "context": context,
    }


def evaluate_reversal_symbol(symbol: str, ticker: pd.DataFrame, fund_data: dict = None, regime_ctx: dict = None) -> dict:
    """
    Public UI evaluator delegating directly to _evaluate_candidate for 100% parity.
    """
    if ticker is None or ticker.empty:
        return {"status": "NO", "reasons": ["Failed to calculate technical indicators"], "score": 0, "qualified": False}

    ticker = apply_indicators(ticker, timeframe="1d")
    verdict = _evaluate_candidate(
        symbol=symbol,
        df=ticker,
        fund_data=fund_data,
        regime_ctx=regime_ctx or {"current_regime": "NEUTRAL"},
    )
    if verdict["passed"]:
        return {
            "status": "CORE MET",
            "score": verdict["score"],
            "qualified": True,
            "sl_result": verdict["sl_result"],
            "context": verdict["context"],
        }
    else:
        return {
            "status": "NO",
            "reasons": [verdict["reject_reason"]],
            "score": verdict["score"],
            "qualified": False,
        }


# [FIX 1] FAILED-REVERSAL COOLDOWN HELPER ──────────────────────────────────────────────
def _is_symbol_in_reversal_cooldown(symbol: str, cooldown_days: int) -> bool:
    try:
        from database import is_symbol_in_failed_reversal_cooldown
        return bool(is_symbol_in_failed_reversal_cooldown(symbol, cooldown_days))
    except (ImportError, AttributeError, ModuleNotFoundError):
        logger.warning(f"⚠️ Outcome tracking helper missing for {symbol}; cooldown protection weakened.")
        return False
    except Exception:
        logger.exception(f"cooldown check (outcome-aware) failed for {symbol}")
        return False
# ─────────────────────────────────────────────────────────────────────────────────────


def _run_scan(force: bool = False):
    """Execute a single reversal scan pass. Called inside the scheduling loop."""
    from database import is_scanner_stopped
    if is_scanner_stopped("REVERSAL"):
        logger.info("🛑 Reversal Scanner is STOPPED by Admin. Skipping execution.")
        return 0

    ist_now = datetime.now(IST)
    scan_start = datetime.now(IST)
    logger.info("\n" + "=" * 80)
    logger.info(f"🚀 [START] REVERSAL SCANNER INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')} 🚀")
    logger.info("=" * 80 + "\n")

    # Check if we are outside the valid REVERSAL window (21:00 - 23:59:59)
    now_time = ist_now.time()
    scheduled_start = datetime.strptime("21:00", "%H:%M").time()
    scheduled_end = datetime.strptime("23:59:59", "%H:%M:%S").time()
    import database
    if force:
        is_test_mode = False
    else:
        is_test_mode = getattr(database, "DONT_SAVE_ALERTS", False) or not (scheduled_start <= now_time <= scheduled_end)
    if is_test_mode:
        logger.info("🧪 [TEST MODE] Outside scheduled window (21:00-23:59). Alerts will NOT be saved to DB.")

    try:
        from delivery_data import fetch_delivery_data
        prev_delivery_map = {}
        today_ist = ist_now.date()
        delivery_map, resolved_date = {}, None
        candidate = today_ist

        for _ in range(7):
            while candidate.weekday() >= 5:
                candidate -= timedelta(days=1)
            is_today = (candidate == today_ist)
            try:
                m = fetch_delivery_data(candidate, skip_db_save=not is_today)
                if m:
                    delivery_map, resolved_date = m, candidate
                    break
            except Exception as e:
                logger.error(f"Delivery fetch failed for {candidate}: {e}")
            candidate -= timedelta(days=1)

        if delivery_map:
            prev_delivery_map = delivery_map
            if resolved_date != today_ist:
                logger.info(f"✅ Reversal Scanner using FALLBACK Bhavcopy from: {resolved_date}")
                try:
                    from push_service import send_push_to_all
                    from database import insert_notification
                    msg = f"Reversal Scanner is using stale Bhavcopy (fallback from {resolved_date}) because today's data is not yet published."
                    insert_notification("warning", "⚠️ Stale Bhavcopy Used", msg)
                    send_push_to_all("⚠️ Stale Bhavcopy Used", msg)
                except Exception as ne:
                    logger.error(f"Failed to send stale Bhavcopy notification: {ne}")
            else:
                logger.info(f"✅ Reversal Scanner using TODAY'S Bhavcopy from: {resolved_date}")
        else:
            logger.warning("⚠️ Failed to fetch delivery data for all recent days. Reverting to empty map (no delivery bonus).")
    except Exception as e:
        logger.warning(f"⚠️ Critical failure in delivery data fetch loop: {e}")
        prev_delivery_map = {}
        
    try:
        nifty_ret = get_nifty_20d_return()
        regime_ctx = MarketRegimeEngine.get_regime_context(nifty_ret)
        policy = StrategyPolicyEngine.get_policy(regime_ctx, "REVERSAL")
        regime_ctx["policy"] = policy
    except Exception:
        regime_ctx = {"trend": "NEUTRAL", "biases": {}}

    try:
        from database import get_latest_weights
        regime_str = regime_ctx.get("trend", "NEUTRAL")
        latest_db_weights = get_latest_weights(regime_str)
        if latest_db_weights:
            bayesian_weights = latest_db_weights.get("weights")
            bayesian_version = latest_db_weights.get("version", "v1")
        else:
            bayesian_weights = None
            bayesian_version = "v1"
    except Exception:
        bayesian_weights = None
        bayesian_version = "v1"


    try:
        watchlist = get_watchlist()
    except Exception:
        logger.error("Failed to load watchlist, skipping run.")
        return 0

    if watchlist.empty:
        logger.info("🛡️ Reversal Scanner | Watchlist is empty. Exiting cleanly.")
        if not is_test_mode:
            try:
                from database import insert_notification
                upsert_scanner_health("REVERSAL", status="OK", last_success=datetime.now(IST).isoformat(), today_alerts=0, total_count=0)
                insert_notification("admin", "🚀 Reversal Scanner ran successfully. Found 0 new alerts.", "Generated 0 alerts. The watchlist is currently empty.")
                from push_service import send_push_to_all
                send_push_to_all("🚀 REVERSAL Scanner Summary", "Found 0 new alerts.")
            except Exception:
                pass
        return 0

    watchlist = watchlist.drop_duplicates(subset=["Stock"]).copy()
    # Canonical dedup: two rows that canonicalize to the same symbol (e.g. "RELIANCE" and "RELIANCE.NS")
    # must not both be processed, since synthetic flags are keyed canonically.
    watchlist["_canonical"] = watchlist["Stock"].apply(_canonical_symbol)
    watchlist = watchlist.drop_duplicates(subset=["_canonical"], keep="first").drop(columns=["_canonical"])

    import hashlib
    import uuid
    _wl_stocks = sorted(watchlist["Stock"].tolist())
    _wl_hash = hashlib.md5("|".join(_wl_stocks).encode()).hexdigest()[:12]
    scan_id = str(uuid.uuid4())
    logger.info(f"📋 [REVERSAL] Watchlist fingerprint: {len(watchlist)} stocks | hash={_wl_hash} | scan_id={scan_id}")

    try:
        from database import get_pledge_map
        symbols = [str(s) for s in watchlist["Stock"].tolist() if s]
        pledge_map = get_pledge_map(symbols)
        logger.info(f"🛡️ Fetched pledge data for {len(pledge_map)} symbols")
    except Exception as e:
        logger.exception("Failed to fetch pledge map")
        pledge_map = {}

    if not is_test_mode:
        try:
            upsert_scanner_health(scanner_name="REVERSAL", status="RUNNING", error_msg=None)
        except Exception:
            pass

    total_alerts = 0
    queued_count = 0
    shortlisted_alerts = []
    rejected = {
        "no_data": 0,
        "bad_indicators": 0,
        "insufficient_history": 0,
        "stale_data": 0,
        "cooldown": 0,
        "failed_pattern": 0,
        "drop_band": 0,
        "low_price": 0,
        "low_liquidity": 0,
        "fundamental_filter": 0,
        "ema_filter": 0,
        "low_volume": 0,
        "low_score": 0,
        "climax_top": 0,
        "thin_spread": 0,
        "low_rr": 0,
        "macd_stale": 0,
        "blacklist": 0,
        "batch_fetch_failed": 0,
        "processing_error": 0,
    }
    stats = {
        "soft_sma50_pass": 0,
        "ranked_out": 0
    }
    
    provider_stats_counts = {
        "SUCCESS": 0,
        "NOT_FOUND": 0,
        "RATE_LIMIT": 0,
        "NETWORK_ERROR": 0,
        "TIMEOUT": 0,
        "EMPTY_DATA": 0
    }
    scan_failures = []
    
    today_str = ist_now.strftime("%Y-%m-%d")

    # ── MAKE EXPORT IDEMPOTENT ──
    if not os.environ.get("RAILWAY_ENVIRONMENT"):
        try:
            import csv
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(base_dir, "data")
            export_path = os.path.join(data_dir, "reversal_alerts_export.csv")
            if os.path.exists(export_path):
                df_export = pd.read_csv(export_path)
                if 'date' in df_export.columns:
                    df_export = df_export[df_export['date'] != today_str]
                    df_export.to_csv(export_path, index=False)
        except Exception as e:
            logger.warning(f"Could not clean up today's export rows: {e}")

    # Fetch cooldown alerts BEFORE delete (C18)
    from database import get_recent_alerts_for_scanner, get_all_failed_reversal_cooldown_symbols, delete_todays_alerts_for_scanner
    cooldown_alerts = get_recent_alerts_for_scanner("REVERSAL", ALERT_COOLDOWN_MINUTES.get("REVERSAL", 10080))
    today_ist = ist_now.date()
    cooldown_syms = {
        _canonical_symbol(a.get("symbol", "")) for a in cooldown_alerts
        if _to_ist_date(a.get("created_at")) != today_ist
    }
    failed_reversal_cooldown_symbols = get_all_failed_reversal_cooldown_symbols(REVERSAL_COOLDOWN_TRADING_DAYS)

    try:
        deleted_count = delete_todays_alerts_for_scanner("REVERSAL", today_str)
        logger.info(f"REVERSAL cleanup: removed {deleted_count} existing alerts for {today_str} before run")
    except Exception as e:
        logger.warning(f"Failed to delete today's alerts for REVERSAL before run: {e}")

    import gc, time
    BATCH_SIZE = 50
    total_fetched_count = 0
    logger.info(f"📥 Processing REVERSAL phase in chunks of {BATCH_SIZE}...")

    from memory_profiler import chunk_iterable, BatchMemoryTracker
    total_batches = (len(watchlist) + BATCH_SIZE - 1) // BATCH_SIZE

    from price_cache import get_intraday_snapshot
    all_symbols = watchlist["Stock"].tolist()
    logger.info(f"📥 [REVERSAL] Pre-fetching intraday snapshots for {len(all_symbols)} symbols...")
    try:
        all_snapshots = get_intraday_snapshot(all_symbols, interval="5m", period="1d", requester="ReverseScanner") or {}
    except Exception as _snap_e:
        logger.warning(f"⚠️ [REVERSAL] Snapshot pre-fetch failed: {_snap_e}. Falling back to empty snapshots.")
        all_snapshots = {}

    synthetic_vol_missing = set()
    synthetic_bar_symbols = set()

    with MemoryProfiler("Process Symbols"):
        for batch_num, chunk_df in enumerate(chunk_iterable(watchlist, BATCH_SIZE), start=1):
            with BatchMemoryTracker("REVERSAL", batch_num, total_batches, len(chunk_df), collect_gc=True) as tracker:
                all_ticker_data = fetch_watchlist_data(chunk_df, "2y", "1d")

                chunk_symbols = chunk_df["Stock"].tolist()
                chunk_snapshots = {}
                for s in chunk_symbols:
                    snap = all_snapshots.get(s)
                    if snap is None:
                        snap = all_snapshots.get(_canonical_symbol(s))
                    if snap is not None:
                        chunk_snapshots[_canonical_symbol(s)] = snap
                
                if all_ticker_data:
                    now_ist = datetime.now(IST)
                    today_date_str = now_ist.strftime("%Y-%m-%d")
                    for sym, hist_df in all_ticker_data.items():
                        if isinstance(hist_df, pd.DataFrame) and not hist_df.empty:
                            snap_df = chunk_snapshots.get(_canonical_symbol(sym)) if chunk_snapshots else None
                            if isinstance(snap_df, pd.DataFrame) and not snap_df.empty:
                                valid_closes = snap_df['Close'].dropna()
                                if not valid_closes.empty:
                                    live_price = float(valid_closes.iloc[-1])

                                    def _snap_val(col, agg, fallback):
                                        if col not in snap_df.columns:
                                            return fallback
                                        series = snap_df[col].dropna()
                                        if series.empty:
                                            return fallback
                                        return float(agg(series))

                                    snap_open = _snap_val('Open', lambda s: s.iloc[0], live_price)
                                    snap_high = _snap_val('High', lambda s: s.max(), max(live_price, snap_open))
                                    snap_low = _snap_val('Low', lambda s: s.min(), min(live_price, snap_open))
                                    snap_vol = _snap_val('Volume', lambda s: s.sum(), 0.0)
                                    snap_high = max(snap_high, live_price, snap_open)
                                    snap_low = min(snap_low, live_price, snap_open)

                                    hist_df = hist_df.copy()
                                    last_dt = hist_df.index[-1] if not hist_df.index.empty else None
                                    t_col = 'Date' if 'Date' in hist_df.columns else ('Datetime' if 'Datetime' in hist_df.columns else None)
                                    if t_col:
                                        last_dt = hist_df[t_col].iloc[-1]

                                    last_dt_str = pd.to_datetime(last_dt).strftime("%Y-%m-%d") if last_dt else ""

                                    can_sym = _canonical_symbol(sym)

                                    if last_dt_str == today_date_str:
                                        hist_df.iloc[-1, hist_df.columns.get_loc('Close')] = live_price
                                        if snap_vol > 0 and 'Volume' in hist_df.columns:
                                            hist_df.iloc[-1, hist_df.columns.get_loc('Volume')] = snap_vol
                                        if 'High' in hist_df.columns:
                                            hist_df.iloc[-1, hist_df.columns.get_loc('High')] = max(
                                                float(hist_df['High'].iloc[-1]), snap_high
                                            )
                                        if 'Low' in hist_df.columns:
                                            hist_df.iloc[-1, hist_df.columns.get_loc('Low')] = min(
                                                float(hist_df['Low'].iloc[-1]), snap_low
                                            )
                                        try:
                                            recomputed = apply_indicators(hist_df, timeframe="1d")
                                            if recomputed is not None and not recomputed.empty:
                                                hist_df = recomputed
                                        except Exception as _ind_e:
                                            logger.debug(f"[REVERSAL] {sym} indicator recompute on fresh bar failed: {_ind_e}")
                                        # Today's data is fresh — discard from synthetic sets
                                        synthetic_bar_symbols.discard(can_sym)
                                        synthetic_vol_missing.discard(can_sym)
                                    else:
                                        new_row = hist_df.iloc[-1:].copy()
                                        last_tz = getattr(pd.to_datetime(last_dt), 'tz', None) if last_dt is not None else None
                                        new_dt = pd.to_datetime(today_date_str).tz_localize(last_tz) if last_tz else pd.to_datetime(today_date_str)
                                        if t_col:
                                            new_row[t_col] = new_dt
                                        else:
                                            new_row.index = [new_dt]
                                        new_row['Open'] = snap_open
                                        new_row['High'] = snap_high
                                        new_row['Low'] = snap_low
                                        new_row['Close'] = live_price
                                        new_row['Volume'] = snap_vol
                                        hist_df = pd.concat([hist_df, new_row])
                                        try:
                                            recomputed = apply_indicators(hist_df, timeframe="1d")
                                            if recomputed is not None and not recomputed.empty:
                                                hist_df = recomputed
                                        except Exception as _ind_e:
                                            logger.debug(f"[REVERSAL] {sym} indicator recompute on synthetic bar failed: {_ind_e}")
                                        # Synthetic bar appended — flag it
                                        synthetic_bar_symbols.add(can_sym)
                                        if snap_vol <= 0:
                                            synthetic_vol_missing.add(can_sym)
                                        else:
                                            synthetic_vol_missing.discard(can_sym)

                                    all_ticker_data[sym] = hist_df
                                
                if not all_ticker_data:
                    rejected["batch_fetch_failed"] = rejected.get("batch_fetch_failed", 0) + len(chunk_df)
                    try:
                        del all_ticker_data
                    except Exception:
                        pass
                    gc.collect()
                    continue
                    
                from core_enums import ProviderResult
                valid_fetches = sum(1 for v in all_ticker_data.values() if isinstance(v, pd.DataFrame) and not v.empty)
                total_fetched_count += valid_fetches
                rows_fetched = sum(len(df) for df in all_ticker_data.values() if isinstance(df, pd.DataFrame))
                tracker.mark_fetch_complete(row_count=rows_fetched)

                for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):
                    symbol = "UNKNOWN"
                    try:
                        symbol   = row["Stock"]
                        category = row["Category"]
                        can_sym  = _canonical_symbol(symbol)

                        from surveillance import get_live_blacklist
                        if symbol in get_live_blacklist() or can_sym in get_live_blacklist():
                            rejected["blacklist"] += 1
                            continue

                        if symbol in failed_reversal_cooldown_symbols or can_sym in failed_reversal_cooldown_symbols:
                            rejected["cooldown"] += 1
                            logger.info(f"[REVERSAL] {symbol} skipped: failed reversal cooldown active")
                            continue

                        if can_sym in cooldown_syms:
                            rejected["cooldown"] += 1
                            logger.info(f"[REVERSAL] {symbol} skipped: recent same-session cooldown active")
                            continue

                        ticker_data = all_ticker_data.get(symbol)
                        if ticker_data is None:
                            ticker_data = all_ticker_data.get(f"{symbol}.NS") or all_ticker_data.get(f"{symbol}.BO") or all_ticker_data.get(symbol.split('.')[0])

                        if ticker_data is None:
                            logger.info(f"REJECTION: {symbol} (Phase: FETCH, Reason: Missing historical data)")
                            rejected["no_data"] += 1
                            provider_stats_counts["EMPTY_DATA"] += 1
                            scan_failures.append(ScanFailure(symbol=symbol, scanner_name="REVERSAL", provider="unknown", failure_reason="missing data", scan_id=scan_id, stage="data_fetch"))
                            continue
                
                        if isinstance(ticker_data, ProviderResult):
                            res = ticker_data
                            provider_stats_counts[res.name] += 1
                            scan_failures.append(ScanFailure(symbol=symbol, scanner_name="REVERSAL", provider="unknown", failure_reason=f"Provider error: {res.name}", scan_id=scan_id, stage="data_fetch"))
                            rejected["no_data"] += 1
                            continue
                        else:
                            provider_stats_counts["SUCCESS"] += 1

                        ticker = ticker_data.copy()
            
                        if ticker.empty:
                            logger.debug(f"[REVERSAL] {symbol} rejected: no historical data")
                            rejected["no_data"] += 1
                            continue
                
                        if getattr(ticker, 'attrs', {}).get('is_stale'):
                            logger.warning(f"[REVERSAL] {symbol} rejected: stale historical data")
                            rejected["stale_data"] += 1
                            continue

                        if isinstance(ticker.columns, pd.MultiIndex):
                            ticker.columns = ticker.columns.get_level_values(0)
                        ticker = ticker.dropna(subset=["Open", "High", "Low", "Close", "Volume"])

                        is_synth_bar = (can_sym in synthetic_bar_symbols)
                        is_synth_no_vol = (can_sym in synthetic_vol_missing)
                        verdict = _evaluate_candidate(
                            symbol=symbol,
                            df=ticker,
                            fund_data=row.to_dict(),
                            regime_ctx=regime_ctx,
                            weights=bayesian_weights,
                            pledge_map=pledge_map,
                            delivery_map=prev_delivery_map,
                            is_synthetic_bar=is_synth_bar,
                            is_synthetic_no_vol=is_synth_no_vol,
                        )

                        if not verdict["passed"]:
                            code = verdict.get("reject_code", "failed_pattern")
                            rejected[code] = rejected.get(code, 0) + 1
                            continue

                        ctx = verdict["context"]
                        sl_res = verdict["sl_result"]
                        reversal_score = verdict["score"]
                        close_price = ctx["entry_price"]

                        sma50_last = _req_float(ticker.iloc[-1], "SMA50")
                        if sma50_last and close_price < sma50_last:
                            stats["soft_sma50_pass"] += 1

                        dedup_key = f"{category}|{symbol}|{today_str}|REVERSAL"

                        shortlisted_alerts.append({
                            "symbol": symbol,
                            "dedup_key": dedup_key,
                            "alert_time": ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                            "category": category,
                            "entry_price": round(close_price, 2),
                            "signals": ", ".join(ctx["signals"]),
                            "score": reversal_score,
                            "rsi": round(ctx["technicals"]["rsi"], 1),
                            "volume_ratio": ctx["technicals"]["volume_ratio"],
                            "stop_loss": sl_res.get("stop_loss"),
                            "target_1": sl_res.get("target_1"),
                            "target_2": sl_res.get("target_2"),
                            "target_3": sl_res.get("target_3"),
                            "target_price": sl_res.get("target_1"),
                            "context": ctx,
                            "structural_failure_stop": sl_res.get("structural_failure_stop"),
                            "target_quality_score": sl_res.get("target_quality")
                        })

                        if not os.environ.get("RAILWAY_ENVIRONMENT"):
                            try:
                                import csv
                                base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                                data_dir = os.path.join(base_dir, "data")
                                os.makedirs(data_dir, exist_ok=True)
                                export_path = os.path.join(data_dir, "reversal_alerts_export.csv")
                                header = [
                                    "symbol", "date", "score", "drop_pct", "volume_ratio", "delivery_pct",
                                    "trend_score", "rsi", "macd", "result_5d", "result_10d", "result_20d",
                                    "max_runup", "max_drawdown"
                                ]
                                export_row = {
                                    "symbol": symbol,
                                    "date": today_str,
                                    "score": reversal_score,
                                    "drop_pct": round(ctx["drop_pct"], 2),
                                    "volume_ratio": round(ctx["technicals"]["volume_ratio"], 2) if ctx["technicals"]["volume_ratio"] is not None else None,
                                    "delivery_pct": round((prev_delivery_map.get(symbol) or prev_delivery_map.get(can_sym)), 2) if (prev_delivery_map.get(symbol) or prev_delivery_map.get(can_sym)) is not None else None,
                                    "trend_score": ctx.get("trend_score"),
                                    "rsi": round(ctx["technicals"]["rsi"], 2),
                                    "macd": float(ticker["MACD"].iloc[-1]) if "MACD" in ticker.columns and not pd.isna(ticker["MACD"].iloc[-1]) else None,
                                    "result_5d": None,
                                    "result_10d": None,
                                    "result_20d": None,
                                    "max_runup": None,
                                    "max_drawdown": None
                                }
                                write_header = not os.path.exists(export_path)
                                with open(export_path, "a", newline="") as f:
                                    writer = csv.DictWriter(f, fieldnames=header)
                                    if write_header:
                                        writer.writeheader()
                                    writer.writerow(export_row)
                            except Exception:
                                logger.exception(f"Failed to export reversal alert for {symbol}")

                    except Exception as e:
                        logger.exception(f'❌ Error processing {symbol}')
                        rejected["processing_error"] = rejected.get("processing_error", 0) + 1
                        if not is_test_mode:
                            try:
                                upsert_fetch_error('yfinance', 'REVERSAL', symbol, '1d', 'processing_error', str(e))
                            except Exception:
                                logger.exception(f'Failed to upsert fetch error for {symbol}')

            try:
                del all_ticker_data
            except Exception:
                pass
            gc.collect()
        
        fired_rev = {k: v for k, v in rejected.items() if v > 0}
        elapsed_rev = round((datetime.now(IST) - ist_now).total_seconds(), 1)
        total_symbols = len(watchlist)
        stale_count = rejected.get("stale_data", 0)
        no_data_count = rejected.get("no_data", 0)
        fresh_count = max(0, total_fetched_count - stale_count)

        summary_lines = [
            "======================================================================",
            "=== [REVERSAL SCANNER PIPELINE SUMMARY] ===",
            "======================================================================",
            "📊 DATA QUALITY SNAPSHOT:",
            f"  • Total Watchlist Requested : {total_symbols}",
            f"  • Fresh Data OK             : {fresh_count}",
            f"  • Stale Data                : {stale_count}",
            f"  • Missing / No Data         : {no_data_count}",
            "",
            "🎯 CRITERIA & FILTER BREAKDOWN:"
        ]
        for k, v in fired_rev.items():
            summary_lines.append(f"  • {k:<27}: {v}")

        summary_lines.extend([
            "",
            "📊 EXTRA PIPELINE STATS:",
            f"  • Soft SMA50 Pass Count     : {stats['soft_sma50_pass']}",
            f"  • Max Alerts Ranked Out     : {stats['ranked_out']}",
            "",
            "🏆 FINAL OUTCOME:",
            f"  • Shortlisted Alerts        : {len(shortlisted_alerts)}",
            f"  • Total Execution Time      : {elapsed_rev}s",
            "======================================================================"
        ])
        logger.info("\n".join(summary_lines))

        # ── PERSISTENCE ───────────────────────────────────────────────────────────
        total_alerts = 0
        queued_count = 0
        if shortlisted_alerts:
            logger.info(f"📊 Reversal Candidates Discovered: {len(shortlisted_alerts)}")
            for cand in shortlisted_alerts:
                vol_str = f"{cand['volume_ratio']:.2f}x" if cand['volume_ratio'] is not None else "Bypassed"
                logger.info(f"  • 🟢 {cand['symbol']} @ ₹{cand['entry_price']:.2f} (Score: {cand['score']}, RSI: {cand['rsi']}, Vol Ratio: {vol_str})")
        else:
            logger.info("📊 Reversal Candidates Discovered: 0")

        if not is_test_mode and not getattr(database, "DONT_SAVE_ALERTS", False):
            try:
                if shortlisted_alerts:
                    shortlisted_alerts.sort(key=lambda x: x["score"], reverse=True)
                    from config import SCANNER_MAX_ALERTS
                    max_alerts = SCANNER_MAX_ALERTS.get("REVERSAL", 10)
                    if len(shortlisted_alerts) > max_alerts:
                        logger.info(f"Limiting REVERSAL alerts from {len(shortlisted_alerts)} to {max_alerts}")
                        ranked_out_alerts = shortlisted_alerts[max_alerts:]
                        stats["ranked_out"] += len(ranked_out_alerts)
                        from database import save_rejected_alert
                        for alert in ranked_out_alerts:
                            logger.info(f"🚫 {alert['symbol']} alert SUPPRESSED: Exceeded MAX_ALERTS_PER_SCAN limit (Score: {alert['score']})")
                            try:
                                save_rejected_alert(alert['symbol'], "REVERSAL", "RANKED_OUT", context={"score": alert['score']})
                            except Exception:
                                pass
                        shortlisted_alerts = shortlisted_alerts[:max_alerts]

                for alert in shortlisted_alerts:
                    _bayesian_regime = regime_ctx.get("trend", "NEUTRAL") if isinstance(regime_ctx, dict) else "NEUTRAL"
                    inserted, reason, _, _ = database.save_alert_if_new(
                        alert["symbol"],
                        "REVERSAL",
                        alert["alert_time"],
                        scanner="REVERSAL",
                        category=alert["category"],
                        entry_price=alert["entry_price"],
                        signals=alert["signals"],
                        score=alert["score"],
                        rsi=alert["rsi"],
                        volume_ratio=alert["volume_ratio"],
                        stop_loss=alert["stop_loss"],
                        target_1=alert.get("target_1"),
                        target_2=alert.get("target_2"),
                        target_3=alert.get("target_3"),
                        target_price=alert["target_price"],
                        context=alert["context"],
                        model_version=ACTIVE_ALGO_VERSION,
                        bayesian_regime=_bayesian_regime,
                        bayesian_weights=bayesian_weights,
                        structural_failure_stop=alert.get("structural_failure_stop"),
                        target_quality_score=alert.get("target_quality_score")
                    )
                    if inserted:
                        total_alerts += 1
                    elif reason == "CANDIDATE_QUEUED":
                        queued_count += 1

                if total_alerts > 0:
                    if not verify_alerts_saved_today("REVERSAL", total_alerts):
                        logger.critical(f"🚨 CRITICAL ERROR: REVERSAL scanner generated {total_alerts} alerts but save failed!")
            except Exception as e:
                logger.exception("Failed to save REVERSAL alerts")

        accounted = sum(rejected.values()) + len(shortlisted_alerts) + stats.get("ranked_out", 0)
        unaccounted = total_symbols - accounted
        if unaccounted != 0:
            logger.warning(f"⚠️ REVERSAL pipeline reconciliation mismatch: {unaccounted} symbols unaccounted (total={total_symbols}, rejected={sum(rejected.values())}, shortlisted={len(shortlisted_alerts)})")

        status = "OK"
        error_msgs = []
        fetched_count = total_fetched_count

        if fetched_count == 0:
            status = "DOWN"
            outcome = "FAILED"
            error_msgs.append("🚫 CRITICAL BLOCKER: 0 symbols fetched")
        elif total_symbols > 0 and no_data_count >= total_symbols * 0.25:
            status = "DOWN"
            outcome = "PARTIAL"
            error_msgs.append(f"🚫 CRITICAL BLOCKER: {no_data_count}/{total_symbols} symbols unfetched")
        elif fetched_count > 0 and (stale_count / max(fetched_count, 1)) > STALE_DEGRADED_RATIO:
            status = "DEGRADED"
            outcome = "PARTIAL" if fetched_count < (total_symbols * MIN_FETCH_RATIO) else "SUCCESS"
            error_msgs.append(f"High stale data: {stale_count}/{max(fetched_count, 1)} fetched symbols rejected")
        elif total_symbols > 0 and fetched_count < (total_symbols * MIN_FETCH_RATIO):
            status = "DEGRADED"
            outcome = "PARTIAL"
            error_msgs.append(f"Partial Fetch: {fetched_count}/{total_symbols} symbols")
        else:
            status = "OK"
            outcome = "SUCCESS"

        error_msg = "; ".join(error_msgs) if error_msgs else None
        elapsed_time = (datetime.now(IST) - scan_start).total_seconds()

        upsert_scanner_health(
            scanner_name="REVERSAL",
            status=status,
            last_success=ist_now.isoformat() if status != "DOWN" else None,
            today_alerts=total_alerts,
            processed_count=fetched_count,
            total_count=total_symbols,
            error_msg=error_msg,
            outcome=outcome,
            provider_stats=provider_stats_counts,
            duration_seconds=elapsed_time
        )

        try:
            from database import insert_notification
            from push_service import send_push_to_all
            if status in ("OK", "DEGRADED"):
                push_title = "⚠️ REVERSAL Scanner Summary (DEGRADED)" if status == "DEGRADED" else "🚀 REVERSAL Scanner Summary"
                notif_title = "⚠️ Reversal Scanner DEGRADED" if status == "DEGRADED" else "🚀 Reversal Scanner ran successfully"
                detail = error_msg or f"Generated {total_alerts} alerts from {len(watchlist)} scanned stocks."
                insert_notification("admin", f"{notif_title}. Found {total_alerts} new alerts.", detail)
                send_push_to_all(push_title, f"Found {total_alerts} new alerts (status: {status}).")
            elif status == "DOWN":
                insert_notification("admin", f"❌ REVERSAL Scanner CRASHED (DOWN)", error_msg or "Unknown failure")
                send_push_to_all("❌ REVERSAL Scanner DOWN", error_msg or "Crash / Data outage", bypass_throttle=True)
        except Exception:
            pass
                
    try:
        from funnel_telemetry import log_funnel_metrics
        regime_str = regime_ctx.get("trend", "NEUTRAL") if isinstance(regime_ctx, dict) else "NEUTRAL"
        log_funnel_metrics("REVERSAL", regime_str, len(watchlist), rejected, total_alerts)
    except Exception as e:
        logger.warning(f"Failed to log funnel telemetry: {e}")

    elapsed_time_final = (datetime.now(IST) - scan_start).total_seconds()
    logger.info(f"📊 Provider Stats: {dict(provider_stats_counts)}")
    logger.info(f"📊 Final Rejections: {dict(rejected)}")
    logger.info(f"✅ [COMPLETE] REVERSAL SCAN DONE | {elapsed_time_final:.2f}s | Found {total_alerts} bottoming stocks.")

    try:
        from memory_profiler import run_purge_with_telemetry
        run_purge_with_telemetry("Reversal Scanner Complete")
    except Exception as me:
        logger.debug(f"Reversal memory purge failed: {me}")

    return total_alerts


from lock_utils import ProcessLock
_scan_lock = ProcessLock("reversal_scanner")

def start(force: bool = False) -> int:
    if not _scan_lock.acquire(blocking=False):
        raise RuntimeError("Scanner is already actively running!")
    try:
        return _start_wrapper(force)
    finally:
        _scan_lock.release()

def _start_wrapper(force: bool = False) -> int:
    """
    Single-shot scan. Called once by main.py at the 21:00 window.
    Returns the number of alerts generated (0 = no setups found).
    Raises on failure so main.py can send a Telegram crash alert.
    """
    init_db()

    try:
        upsert_scanner_health("REVERSAL", "RUNNING", error_msg="Reversal Scan in progress...")
    except Exception:
        logger.warning("⚠️ Could not mark Reversal as RUNNING")

    from surveillance import force_refresh_blacklist
    force_refresh_blacklist()

    try:
        return _run_scan(force=force)
    except Exception as e:
        logger.exception("❌ CRITICAL REVERSAL SCAN ERROR")
        import database
        if not getattr(database, "DONT_SAVE_ALERTS", False):
            try:
                upsert_scanner_health(scanner_name="REVERSAL", status="DOWN", error_msg=str(e))
                from push_service import send_push_to_all
                send_push_to_all("❌ REVERSAL Scanner DOWN", f"Crash: {str(e)[:100]}")
            except Exception:
                pass
        raise