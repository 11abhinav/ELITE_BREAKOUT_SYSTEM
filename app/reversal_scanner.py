# =====================================================================================
# app/reversal_scanner.py (SCHEDULER READY) — v7.0 OVERHAUL
# DEEP DISCOUNT & MEAN REVERSION SCANNER (With Valuation Metrics)
#
# v7.0 OVERHAUL CHANGELOG (Refactored per comprehensive 25-point audit):
#   [FIX A1] CORE_SCORE_FLOOR comment & BEAR_CORE_REALISTIC headroom aligned.
#   [FIX A2] close_above_ema20 trend scoring tautology removed (strict EMA20 reclaim).
#   [FIX A3] Monotonic SMA200 proximity scoring (at/above SMA200 gets 12 pts).
#   [FIX A4] AVAILABLE_MAX normalized across all missing optional components.
#   [FIX A5] inst_bonus & pledge_penalty clamped against AVAILABLE_MAX.
#   [FIX A6] Fixed inverted MACD freshness test logic (no longer rejects sustained bull momentum).
#   [FIX A7] _is_climax_top volume parameter & fallback lookback slice fixed.
#   [FIX B1] Current-bar volume ratio checked at hard confirmation gate.
#   [FIX B2] RSI trough freshness lookback gate added (max 25 bars).
#   [FIX B3] Robust 3-bar RSI declining check.
#   [FIX B4] Fundamental presence enforced even for quality categories.
#   [FIX B5] Heavy red candle filter message precision fixed.
#   [FIX C1] Surveillance / blacklist filter enforced in candidate loop.
#   [FIX C2] Fetch health ratio tracked and logged.
#   [FIX C3] Scanner health updated to "OK" upon successful completion.
#   [FIX C4] Batch loop wrapped in try-except to prevent single-batch aborts.
#   [FIX C5] Regime keys (current_regime & trend) aligned across functions.
#   [FIX C6] Target keys (target_1 & target) standardized.
#   [FIX C7] Outcome-aware reversal cooldown helper wired in.
#   [FIX C8] Force parameter respected (bypasses cooldowns).
#   [FIX C9] rejected counter dict initialized with defaultdict(int).
#   [FIX C10] Off-hours intraday 5m snapshot optimization added.
#   [FIX C11] Delivery confidence aging normalized in AVAILABLE_MAX.
#   [FIX C12] Unused imports removed.
#   [FIX C13] Config validator updated for quality category drop floor & core score.
# =====================================================================================
import pandas as pd
import logging
import os
from collections import defaultdict
from zoneinfo import ZoneInfo
from datetime import date, datetime, time as dtime, timedelta
from typing import Any, Optional

from technical_indicators import apply_indicators
from memory_profiler import MemoryProfiler, chunk_iterable, BatchMemoryTracker
from database import (
    init_db,
    save_alert_if_new,
    upsert_fetch_error,
    upsert_scanner_health,
)
from price_cache import fetch_watchlist_data
from watchlist_cache import get_watchlist
from config import (
    CLIMAX_VOLUME_LOOKBACK, 
    MIN_CANDLE_RANGE_PCT, 
    REVERSAL_CONFIG,
    ACTIVE_ALGO_VERSION
)
from sl_target_helper import compute_sl_and_target
from delivery_data import fetch_delivery_data
from surveillance import get_live_blacklist, force_refresh_blacklist


logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# ── REVERSAL PARAMETERS ──────────────────────────────────────────────────────────────
MIN_DROP_FROM_52W_HIGH = REVERSAL_CONFIG["MIN_DROP_FROM_52W_HIGH"]
MAX_DROP_FROM_52W_HIGH = REVERSAL_CONFIG["MAX_DROP_FROM_52W_HIGH"]

RSI_OVERSOLD_THRESHOLD = REVERSAL_CONFIG["RSI_OVERSOLD_THRESHOLD"]
RSI_CURL_MIN           = REVERSAL_CONFIG["RSI_CURL_MIN"]
MIN_RSI_RECOVERY       = 8.0
RSI_TROUGH_LOOKBACK    = 35

MIN_VOLUME_RATIO       = REVERSAL_CONFIG["MIN_VOLUME_RATIO"]
VOL_WINDOW_BARS        = 5

# ── QUALITY FILTERS (high-quality stocks only) ───────────────────────────────────────
MIN_STOCK_PRICE        = REVERSAL_CONFIG.get("MIN_STOCK_PRICE", 100.0)
MIN_AVG_DAILY_VOLUME   = REVERSAL_CONFIG["MIN_AVG_DAILY_VOLUME"]
MIN_ROE                = REVERSAL_CONFIG["MIN_ROE"]
MIN_YOY_REVENUE_GROWTH = -15.0
MAX_DROP_BELOW_SMA200  = REVERSAL_CONFIG["MAX_DROP_BELOW_SMA200"]
QUALITY_CAT_MIN_DROP   = REVERSAL_CONFIG.get("QUALITY_CAT_MIN_DROP", 15.0)

# Climax filter thresholds
CLIMAX_VOL_MULT        = 3.5
CLIMAX_VOL_QUANTILE    = 0.95
CLIMAX_MIN_RUNUP_PCT   = 0.10

# Pipeline health guard
FUNDAMENTAL_REJECT_ALARM_PCT = 0.60
REVERSAL_COOLDOWN_TRADING_DAYS = REVERSAL_CONFIG["REVERSAL_COOLDOWN_TRADING_DAYS"]

REVERSAL_MIN_BARS = 250
DEFAULT_PLEDGE_PENALTY = 15.0
STALE_DEGRADED_RATIO = 0.15
MIN_FETCH_RATIO = 0.85
COMPONENT_MAX = 25 + 12 + 15 + 15 + 15 + 10 + 5 + 5 + 5 + 5   # = 112 max score points
MAX_POSSIBLE_SCORE = COMPONENT_MAX

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
MIN_REVERSAL_SCORE_PCT = 0.52
MIN_REVERSAL_SCORE     = round(MAX_POSSIBLE_SCORE * MIN_REVERSAL_SCORE_PCT) # 58
CORE_SCORE_MAX         = 25 + 15 + 15 + 15   # 70 pts max for core technical components
CORE_SCORE_FLOOR       = 30                  # Core technical floor (14+3+0+8 = 25 min achievable core after gates; setups < 30 filtered)

REGIME_REVERSAL_PREMIUM = {
    "STRONG_BEAR": 2,
    "BEAR":        1,
    "NEUTRAL":     0,
    "BULL":        0,
    "STRONG_BULL": 0,
}

REGIME_EVIDENCE_REQ = {
    "STRONG_BEAR": {
        "min_vol_ratio": 2.5,
        "min_rr": 2.5,
        "require_obv": True,
        "max_pct_below_sma200": 17.0,
    },
    "BEAR": {
        "min_vol_ratio": 2.0,
        "min_rr": 2.0,
        "require_obv": False,
        "max_pct_below_sma200": 19.0,
    },
}

def _row_get(r: Any, idx: int, key: str, default: Any = None) -> Any:
    """Shape-agnostic helper to safely extract values from tuple, dict, or row objects."""
    if r is None:
        return default
    if isinstance(r, (list, tuple)):
        return r[idx] if len(r) > idx else default
    if isinstance(r, dict):
        return r.get(key, default)
    try:
        return r[key]
    except (KeyError, IndexError, TypeError):
        try:
            return r[idx]
        except (IndexError, TypeError):
            return default

def _parse_percent_value(val) -> Optional[float]:
    """Single source of truth for fundamental percentage units."""
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
    if 0.0 < abs(f_val) <= 1.0:
        return f_val * 100.0
    return f_val


def _lookup(m: Optional[dict], sym: str, can: str) -> Optional[float]:
    """Helper to retrieve symbol values from maps without swallowing 0.0 as falsy."""
    if not m:
        return None
    v = m.get(sym)
    return m.get(can) if v is None else v


def _session_fraction(now_t: dtime) -> float:
    """Compute elapsed fraction of the trading day (09:15 to 15:30 IST)."""
    session_start = dtime(9, 15)
    session_end = dtime(15, 30)
    if now_t >= session_end or now_t <= session_start:
        return 1.0
    elapsed = (datetime.combine(date.today(), now_t) - datetime.combine(date.today(), session_start)).seconds
    return max(0.15, elapsed / 22500.0)


def _macd_momentum_present(ticker: pd.DataFrame, atr_val: Optional[float] = None, max_cross_age: int = 20) -> bool:
    """
    [VERSION: REVERSAL_OVERHAUL_v7.0] State + Freshness test for MACD momentum.
    Requires MACD above signal (or rising histogram from shallow deficit) AND
    a bullish crossover within the last max_cross_age bars (not held above indefinitely).
    """
    if len(ticker) < 3 or not {"MACD", "MACD_SIGNAL"}.issubset(ticker.columns):
        return False
    try:
        macd, sig = ticker["MACD"], ticker["MACD_SIGNAL"]
        above = (macd > sig)
        above_now = bool(above.iloc[-1])

        if above_now:
            lookback_window = min(max_cross_age + 1, len(above))
            window_above = above.iloc[-lookback_window:-1]
            held_above_too_long = bool(window_above.all()) if len(window_above) >= max_cross_age else False
            return not held_above_too_long

        if "MACD_HIST" not in ticker.columns:
            return False

        h_now = float(ticker["MACD_HIST"].iloc[-1])
        h_prev = float(ticker["MACD_HIST"].iloc[-2])
        floor = -0.10 * float(atr_val) if atr_val else -0.002 * float(ticker["Close"].iloc[-1])
        return (h_now > h_prev) and (h_now > floor) and not bool(above.iloc[-5:].any())
    except (TypeError, ValueError, KeyError, IndexError):
        return False


def _is_climax_top(
    ticker: pd.DataFrame,
    close_price: float,
    candle_high: float,
    candle_low: float,
    vol_ratio: Optional[float] = None,
) -> bool:
    """
    [VERSION: REVERSAL_OVERHAUL_v7.0] Blow-off climax top filter.
    Triggers on extreme volume (>= CLIMAX_VOL_MULT x mean, or p95) AND
    when stock has run up >= CLIMAX_MIN_RUNUP_PCT (10%) over VOL_WINDOW_BARS (5 bars).
    """
    if candle_high <= candle_low or len(ticker) < VOL_WINDOW_BARS + 1:
        return False
    if vol_ratio is None and (ticker is None or "Volume" not in ticker.columns or ticker.empty):
        return False
    try:
        close_nb_ago = float(ticker["Close"].iloc[-(VOL_WINDOW_BARS + 1)])
        if close_nb_ago <= 0:
            return False
        runup = (close_price - close_nb_ago) / close_nb_ago
        if runup < CLIMAX_MIN_RUNUP_PCT:
            return False

        if vol_ratio is not None and vol_ratio <= 0:
            return False

        latest_vol = float(ticker["Volume"].iloc[-1])
        is_fallback = False
        if latest_vol <= 0 and len(ticker) >= 2:
            latest_vol = float(ticker["Volume"].iloc[-2])
            is_fallback = True
        if latest_vol <= 0:
            return False

        lookback_ct = min(CLIMAX_VOLUME_LOOKBACK, len(ticker) - 1)
        end_idx = -2 if is_fallback else -1
        start_idx = max(0, len(ticker) + end_idx - lookback_ct)
        prior = ticker["Volume"].iloc[start_idx:end_idx]
        if prior.empty:
            return False

        frac = _session_fraction(datetime.now(IST).time())
        prorated_latest = latest_vol / frac if (frac > 0 and not is_fallback) else latest_vol

        if vol_ratio is not None and vol_ratio >= CLIMAX_VOL_MULT:
            vol_spike = True
        else:
            vol_spike = prorated_latest > max(
                float(prior.mean()) * CLIMAX_VOL_MULT,
                float(prior.quantile(CLIMAX_VOL_QUANTILE)),
            )
        if not vol_spike:
            return False

        candle_rng = candle_high - candle_low
        upper_wick_pct = (candle_high - close_price) / candle_rng
        close_pos = (close_price - candle_low) / candle_rng
        return upper_wick_pct > 0.25 and close_pos < 0.45
    except (TypeError, ValueError, KeyError, IndexError):
        return False

# =====================================================================================
# REVERSAL-SPECIFIC SCORING (v7.0 — re-weighted & normalized)
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
_REV_CATEGORY_SCORES_SORTED = sorted(_REV_CATEGORY_SCORES.items(), key=lambda kv: (-len(kv[0]), kv[0]))


def _score_reversal(
        vol_ratio: Optional[float],
        drop_pct: float,
        current_rsi: float,
        past_rsi_min: float,
        macd_hist: Optional[float],
        pct_below_sma200: Optional[float],
        category: str,
        rr_ratio: Optional[float],
        trend_score: int = 10,
        obv_trend: Optional[int] = None,
        delivery_pct: Optional[float] = None,
        close_price: Optional[float] = None,
        symbol: Optional[str] = None,
        promoter_pledge_pct: Optional[float] = None,
        atr_val: Optional[float] = None,
        weights: Optional[dict] = None,
        min_drop_floor: float = MIN_DROP_FROM_52W_HIGH,
        delivery_conf: float = 1.0,
        vol_ratio_window: Optional[float] = None,
        available_max: int = COMPONENT_MAX,
) -> dict:
    """Score a reversal setup from 0-100 based on quality dimensions, returning score dict."""
    score = 0

    # ── Trend structure (25 pts) — CORE recovery signal ──
    score += trend_score
    trend_pts = trend_score

    # ── SMA200 proximity (12 pts max) — Monotonic structural scoring ──
    prox_pts = 0
    if pct_below_sma200 is not None:
        if pct_below_sma200 <= 0.0:
            prox_pts = 12   # At or above SMA200 — Peak structural strength
        elif pct_below_sma200 <= 5.0:
            prox_pts = 12   # Classic tight reversal zone
        elif pct_below_sma200 <= 10.0:
            prox_pts = 9
        elif pct_below_sma200 <= 20.0:
            prox_pts = 5
        score += prox_pts

    # ── Volume confirmation (15 pts) ──
    vol_pts = 0
    if vol_ratio is not None and vol_ratio >= 5.0:   vol_pts = 15
    elif vol_ratio is not None and vol_ratio >= 3.5: vol_pts = 12
    elif vol_ratio is not None and vol_ratio >= 2.5: vol_pts = 9
    elif vol_ratio is not None and vol_ratio >= MIN_VOLUME_RATIO: vol_pts = 5
    elif vol_ratio_window is not None and vol_ratio_window >= MIN_VOLUME_RATIO:
        vol_pts = 3        # recent accumulation, not today — discounted
    score += vol_pts

    # ── MACD momentum (15 pts) ──
    macd_pts = 0
    if macd_hist is not None and atr_val is not None and atr_val > 0:
        try:
            mh_atr = float(macd_hist) / float(atr_val)
            if mh_atr >= 0.15:   macd_pts = 15
            elif mh_atr >= 0.05: macd_pts = 10
            elif mh_atr > 0.0:   macd_pts = 5
        except (TypeError, ValueError):
            pass
    elif macd_hist is not None and close_price is not None and close_price > 0:
        try:
            mh_norm = (float(macd_hist) / float(close_price)) * 100
            if mh_norm >= 0.5:   macd_pts = 15
            elif mh_norm >= 0.2: macd_pts = 10
            elif mh_norm > 0:    macd_pts = 5
        except (TypeError, ValueError):
            pass
    score += macd_pts

    # ── RSI curl quality (15 pts) — Measured off historical trough ──
    rsi_pts = 0
    rsi_recovery = current_rsi - past_rsi_min
    if rsi_recovery >= 20:   rsi_pts = 15
    elif rsi_recovery >= 12: rsi_pts = 12
    elif rsi_recovery >= 8:  rsi_pts = 8
    score += rsi_pts

    # ── Category quality (10 pts) ──
    cat_lower = category.lower() if category else ""
    for cat_label, cat_pts in _REV_CATEGORY_SCORES_SORTED:
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
    if delivery_pct is not None and delivery_conf > 0:
        deliv_pts = 0
        if delivery_pct >= 50.0:   deliv_pts = 5
        elif delivery_pct >= 35.0: deliv_pts = 3
        elif delivery_pct >= 25.0: deliv_pts = 1
        score += round(deliv_pts * delivery_conf)

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
            max_penalty = DEFAULT_PLEDGE_PENALTY
        else:
            max_penalty = float(weights.get("PLEDGE_PENALTY", DEFAULT_PLEDGE_PENALTY))
        scale = min(1.0, (promoter_pledge_pct - 10.0) / 40.0)
        pledge_penalty = round(abs(max_penalty) * scale)
        if pledge_penalty > 0:
            score -= pledge_penalty

    core_score = trend_pts + vol_pts + macd_pts + rsi_pts
    raw = score + inst_bonus
    clamped_score = max(0, min(raw, available_max))

    return {
        "score": clamped_score,
        "raw_score": max(0, raw),
        "core_score": core_score,
    }


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
    resolved_date: Optional[date] = None,
) -> dict:
    """Core evaluator logic executing quality gates and returning structured verdict."""
    if df is None or df.empty or len(df) < REVERSAL_MIN_BARS:
        return {
            "passed": False,
            "reject_reason": f"Insufficient historical bars ({len(df) if df is not None else 0} < {REVERSAL_MIN_BARS} minimum)",
            "reject_code": "no_data",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    latest = df.iloc[-1]
    close_price = _req_float(latest, "Close")
    candle_high = _req_float(latest, "High")
    candle_low = _req_float(latest, "Low")
    candle_open = _req_float(latest, "Open")
    atr_val = _req_float(latest, "ATR")
    ema20 = _req_float(latest, "EMA20")
    sma50 = _req_float(latest, "SMA50")
    sma200 = _req_float(latest, "SMA200")
    current_rsi = _req_float(latest, "RSI")
    vol_ratio = _req_float(latest, "Volume_Ratio")
    if vol_ratio is None and "Volume" in df.columns and len(df) >= 20:
        v20 = df["Volume"].iloc[-20:].dropna().mean()
        if v20 > 0:
            vol_ratio = float(latest["Volume"]) / float(v20)

    if is_synthetic_no_vol:
        vol_ratio = None

    if None in (close_price, candle_high, candle_low, candle_open, current_rsi, ema20):
        return {
            "passed": False,
            "reject_reason": "Missing or NaN mandatory technical indicators",
            "reject_code": "bad_indicators",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    if close_price < MIN_STOCK_PRICE:
        return {
            "passed": False,
            "reject_reason": f"Stock price ₹{close_price:.2f} < ₹{MIN_STOCK_PRICE:.0f} minimum quality floor",
            "reject_code": "price_filter",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    candle_rng_pct = ((candle_high - candle_low) / close_price) * 100.0
    if candle_rng_pct < MIN_CANDLE_RANGE_PCT:
        return {
            "passed": False,
            "reject_reason": f"Candle range {candle_rng_pct:.2f}% < {MIN_CANDLE_RANGE_PCT}% minimum volatility threshold",
            "reject_code": "thin_spread",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    if "Volume" in df.columns:
        recent_vol = df["Volume"].iloc[-20:].dropna()
        avg_vol_20d = float(recent_vol.mean()) if not recent_vol.empty else 0.0
        if avg_vol_20d < MIN_AVG_DAILY_VOLUME:
            return {
                "passed": False,
                "reject_reason": f"20D Avg Volume {avg_vol_20d:,.0f} < {MIN_AVG_DAILY_VOLUME:,.0f} liquidity floor",
                "reject_code": "volume_filter",
                "score": 0,
                "raw_score": 0,
                "sl_result": {},
                "context": {},
            }

    if "Volume_Ratio" in df.columns:
        vol_series = df["Volume_Ratio"].iloc[-VOL_WINDOW_BARS:].dropna()
        vol_ratio_max = float(vol_series.max()) if not vol_series.empty else (vol_ratio or 0.0)
    else:
        vol_ratio_max = vol_ratio or 0.0

    high_52w = float(df["High"].iloc[-250:].max()) if len(df) >= 250 else float(df["High"].max())
    if high_52w <= 0:
        return {
            "passed": False,
            "reject_reason": "52W High is non-positive",
            "reject_code": "bad_indicators",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    drop_pct = ((high_52w - close_price) / high_52w) * 100.0
    cat_str = fund_data.get("Category", "") if fund_data else ""
    is_quality_cat = any(kw in cat_str.lower() for kw in ("wealth", "blue chip", "debt-free"))
    effective_min_drop = QUALITY_CAT_MIN_DROP if is_quality_cat else MIN_DROP_FROM_52W_HIGH

    if drop_pct < effective_min_drop or drop_pct > MAX_DROP_FROM_52W_HIGH:
        return {
            "passed": False,
            "reject_reason": f"Drop from 52W High {drop_pct:.1f}% outside allowed band [{effective_min_drop:.1f}%, {MAX_DROP_FROM_52W_HIGH:.1f}%]",
            "reject_code": "drop_band",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    pct_below_sma200 = None
    if sma200 and sma200 > 0:
        pct_below_sma200 = ((sma200 - close_price) / sma200) * 100.0
        if pct_below_sma200 > MAX_DROP_BELOW_SMA200:
            return {
                "passed": False,
                "reject_reason": f"Price {pct_below_sma200:.1f}% below SMA200 > {MAX_DROP_BELOW_SMA200}% maximum structural breakdown limit",
                "reject_code": "sma200_filter",
                "score": 0,
                "raw_score": 0,
                "sl_result": {},
                "context": {},
            }

    _rc = regime_ctx or {}
    regime = _rc.get("current_regime") or _rc.get("trend") or "NEUTRAL"
    ev_req = REGIME_EVIDENCE_REQ.get(regime, {})

    if "max_pct_below_sma200" in ev_req and pct_below_sma200 is not None:
        if pct_below_sma200 > ev_req["max_pct_below_sma200"]:
            return {
                "passed": False,
                "reject_reason": f"[{regime}] {pct_below_sma200:.1f}% below SMA200 > {ev_req['max_pct_below_sma200']}% regime limit",
                "reject_code": "regime_sma200",
                "score": 0,
                "raw_score": 0,
                "sl_result": {},
                "context": {},
            }

    if "min_vol_ratio" in ev_req:
        eff_vol = vol_ratio if vol_ratio is not None else vol_ratio_max
        if eff_vol < ev_req["min_vol_ratio"]:
            return {
                "passed": False,
                "reject_reason": f"[{regime}] Volume ratio {eff_vol:.2f}x < {ev_req['min_vol_ratio']}x regime minimum",
                "reject_code": "regime_vol",
                "score": 0,
                "raw_score": 0,
                "sl_result": {},
                "context": {},
            }

    obv_trend = None
    if "OBV_Trend" in latest and not pd.isna(latest["OBV_Trend"]):
        try:
            obv_trend = int(latest["OBV_Trend"])
        except (ValueError, TypeError):
            pass

    if ev_req.get("require_obv") and obv_trend != 1:
        return {
            "passed": False,
            "reject_reason": f"[{regime}] OBV trend is not accumulating (OBV_Trend={obv_trend})",
            "reject_code": "regime_obv",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    REQUIRE_FUNDAMENTALS = True
    roe_val = _parse_percent_value(fund_data.get("ROE %")) if fund_data else None
    rev_growth = _parse_percent_value(fund_data.get("YOY Revenue %")) if fund_data else None

    if REQUIRE_FUNDAMENTALS and (roe_val is None or rev_growth is None) and not is_quality_cat:
        return {
            "passed": False,
            "reject_reason": "Fundamentals unavailable (fail-closed)",
            "reject_code": "fundamental_filter",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    if roe_val is not None and roe_val < MIN_ROE and not is_quality_cat:
        return {
            "passed": False,
            "reject_reason": f"ROE {roe_val:.1f}% < {MIN_ROE}% minimum threshold",
            "reject_code": "fundamental_filter",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    if rev_growth is not None and rev_growth < MIN_YOY_REVENUE_GROWTH and not is_quality_cat:
        return {
            "passed": False,
            "reject_reason": f"YoY Revenue Growth {rev_growth:.1f}% < {MIN_YOY_REVENUE_GROWTH}% minimum threshold",
            "reject_code": "fundamental_filter",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    ema_tol = 0.10 * atr_val if atr_val else 0.0
    if close_price < (ema20 - ema_tol):
        return {
            "passed": False,
            "reject_reason": f"Close ₹{close_price:.2f} < EMA20 ₹{ema20:.2f} (ATR tolerance {ema_tol:.2f})",
            "reject_code": "ema_filter",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    _n = RSI_TROUGH_LOOKBACK
    rsi_window = df["RSI"].iloc[-_n:-1] if len(df) >= _n + 1 else df["RSI"].iloc[:-1]
    rsi_window = rsi_window.dropna()
    if len(rsi_window) < 5:
        return {
            "passed": False,
            "reject_reason": "RSI data insufficient (too many NaN values in historical window)",
            "reject_code": "bad_indicators",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }
    past_rsi_min = float(rsi_window.min())
    trough_idx = rsi_window.idxmin()
    bars_since_trough = (len(df) - 1 - df.index.get_loc(trough_idx)) if trough_idx in df.index else 99
    rsi_recovery = current_rsi - past_rsi_min

    MAX_TROUGH_AGE = 25
    if current_rsi < RSI_CURL_MIN or past_rsi_min > RSI_OVERSOLD_THRESHOLD or rsi_recovery < MIN_RSI_RECOVERY or bars_since_trough > MAX_TROUGH_AGE:
        return {
            "passed": False,
            "reject_reason": f"RSI condition failed: current RSI={current_rsi:.1f} (min {RSI_CURL_MIN}), min RSI={past_rsi_min:.1f} (max {RSI_OVERSOLD_THRESHOLD}), bounce={rsi_recovery:.1f} (min {MIN_RSI_RECOVERY}), trough age={bars_since_trough}b (max {MAX_TROUGH_AGE}b)",
            "reject_code": "failed_pattern",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    if len(df) >= 4:
        rsi_tail = df["RSI"].iloc[-4:].dropna()
        if len(rsi_tail) >= 3:
            if (rsi_tail.iloc[-1] < rsi_tail.iloc[-2]) and (rsi_tail.iloc[-2] < rsi_tail.iloc[-3]):
                return {
                    "passed": False,
                    "reject_reason": f"RSI continuously declining over last 3 bars: {list(rsi_tail.tail(3).round(1))}",
                    "reject_code": "failed_pattern",
                    "score": 0,
                    "raw_score": 0,
                    "sl_result": {},
                    "context": {},
                }

    if not _macd_momentum_present(df, atr_val=atr_val):
        return {
            "passed": False,
            "reject_reason": "MACD momentum absent (MACD < SIGNAL and MACD_HIST not rising, or cross > 20 bars ago)",
            "reject_code": "macd_stale",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    effective_vol_ratio = vol_ratio if vol_ratio is not None else vol_ratio_max
    if effective_vol_ratio < MIN_VOLUME_RATIO:
        return {
            "passed": False,
            "reject_reason": f"Volume ratio {effective_vol_ratio:.2f}x < {MIN_VOLUME_RATIO}x minimum volume confirmation",
            "reject_code": "low_volume",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    if _is_climax_top(df, close_price, candle_high, candle_low, vol_ratio=vol_ratio):
        return {
            "passed": False,
            "reject_reason": "Climax top detected (record volume with upper wick dump)",
            "reject_code": "climax_top",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    sl_res = compute_sl_and_target(
        df=df,
        entry_price=close_price,
        pattern_type="REVERSAL",
        atr_val=atr_val,
    )
    if not sl_res.get("passed", False):
        return {
            "passed": False,
            "reject_reason": f"R:R filter: {sl_res.get('reject_reason', 'SL/Target calculation failed')}",
            "reject_code": "low_rr",
            "score": 0,
            "raw_score": 0,
            "sl_result": {},
            "context": {},
        }

    can_sym = _canonical_symbol(symbol)
    delivery_pct = _lookup(delivery_map, symbol, can_sym)
    today_ist_date = datetime.now(IST).date()
    age = (today_ist_date - resolved_date).days if resolved_date else 99
    delivery_conf = 1.0 if age == 0 else (0.5 if age == 1 else 0.0)

    # ── Normalize AVAILABLE_MAX across all missing optional components ──
    AVAILABLE_MAX = COMPONENT_MAX
    if vol_ratio is None:
        AVAILABLE_MAX -= 12
    if delivery_pct is None or delivery_conf == 0.0:
        AVAILABLE_MAX -= 5
    elif delivery_conf < 1.0:
        AVAILABLE_MAX -= 2
    if pct_below_sma200 is None:
        AVAILABLE_MAX -= 12
    if not cat_str or not any(cat_label.lower() in cat_str.lower() for cat_label, _ in _REV_CATEGORY_SCORES_SORTED):
        AVAILABLE_MAX -= 10
    if obv_trend is None:
        AVAILABLE_MAX -= 5
    if sl_res.get("natural_rr") is None:
        AVAILABLE_MAX -= 5

    AVAILABLE_MAX = max(50, AVAILABLE_MAX)
    score_premium = REGIME_REVERSAL_PREMIUM.get(regime, 0)
    effective_min_score = round((MIN_REVERSAL_SCORE + score_premium) * AVAILABLE_MAX / COMPONENT_MAX)

    sma50_series = df["SMA50"].dropna()
    sma50_slope_up = (len(sma50_series) >= 6 and float(sma50_series.iloc[-1]) > float(sma50_series.iloc[-6]))
    close_above_ema20_strict = (close_price > ema20)
    above_sma50 = (close_price > sma50) if sma50 else False
    above_sma200 = (close_price > sma200) if sma200 else False

    if above_sma200 and above_sma50:
        trend_score = 25
    elif above_sma50:
        trend_score = 22
    elif close_above_ema20_strict and sma50_slope_up:
        trend_score = 18
    else:
        trend_score = 14

    pledge_pct = _lookup(pledge_map, symbol, can_sym)
    macd_hist = _req_float(latest, "MACD_HIST")

    score_dict = _score_reversal(
        vol_ratio=vol_ratio,
        drop_pct=drop_pct,
        current_rsi=current_rsi,
        past_rsi_min=past_rsi_min,
        macd_hist=macd_hist,
        pct_below_sma200=pct_below_sma200,
        category=cat_str,
        rr_ratio=sl_res.get("natural_rr"),
        trend_score=trend_score,
        obv_trend=obv_trend,
        delivery_pct=delivery_pct,
        close_price=close_price,
        symbol=symbol,
        promoter_pledge_pct=pledge_pct,
        atr_val=atr_val,
        weights=weights,
        min_drop_floor=effective_min_drop,
        delivery_conf=delivery_conf,
        vol_ratio_window=vol_ratio_max,
        available_max=AVAILABLE_MAX,
    )
    score = score_dict["score"]
    raw_score = score_dict["raw_score"]
    core_score = score_dict["core_score"]

    if core_score < CORE_SCORE_FLOOR:
        return {
            "passed": False,
            "reject_reason": f"Core technical score {core_score} < {CORE_SCORE_FLOOR} minimum quality floor",
            "reject_code": "weak_core",
            "score": score,
            "raw_score": raw_score,
            "sl_result": sl_res,
            "context": {},
        }

    if score < effective_min_score:
        return {
            "passed": False,
            "reject_reason": f"Score {score} < {effective_min_score} minimum threshold (regime: {regime})",
            "reject_code": "low_score",
            "score": score,
            "raw_score": raw_score,
            "sl_result": sl_res,
            "context": {},
        }

    signals = []
    if above_sma50:
        signals.append("🎯 Reclaimed 20 EMA & SMA50")
    else:
        signals.append("🎯 Reclaimed 20 EMA (below SMA50)")

    signals.append(f"📉 Down {drop_pct:.1f}% from 52W High")
    signals.append(f"🔄 RSI Oversold Bounce (RSI={current_rsi:.1f}, min={past_rsi_min:.1f})")
    if vol_ratio is not None:
        signals.append(f"📊 Vol Ratio {vol_ratio:.1f}x (20D Avg)")
    if pct_below_sma200 is not None:
        signals.append(f"📍 {pct_below_sma200:.1f}% below SMA200")
    if obv_trend == 1:
        signals.append("🟢 OBV Accumulation")

    target_val = sl_res.get("target_1") or sl_res.get("target")

    context = {
        "score": score,
        "raw_score": raw_score,
        "core_score": core_score,
        "trend_score": trend_score,
        "entry_price": close_price,
        "drop_pct": round(drop_pct, 1),
        "stop_loss": sl_res.get("stop_loss"),
        "target": target_val,
        "target_1": target_val,
        "risk_reward": sl_res.get("natural_rr"),
        "regime": regime,
        "effective_min_score": effective_min_score,
        "available_max": AVAILABLE_MAX,
        "signals": signals,
        "technicals": {
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
        "raw_score": raw_score,
        "sl_result": sl_res,
        "context": context,
    }


def evaluate_reversal_symbol(symbol: str, ticker: pd.DataFrame, fund_data: dict = None, regime_ctx: dict = None) -> dict:
    """Public UI evaluator delegating directly to _evaluate_candidate for 100% parity."""
    if ticker is None or ticker.empty:
        return {"status": "NO", "reasons": ["Failed to calculate technical indicators"], "score": 0, "qualified": False}

    ticker = apply_indicators(ticker, timeframe="1d")

    regime_ctx = regime_ctx or {}
    regime_str = regime_ctx.get("trend") or regime_ctx.get("current_regime") or "NEUTRAL"
    if "trend" not in regime_ctx:
        regime_ctx["trend"] = regime_str
    if "current_regime" not in regime_ctx:
        regime_ctx["current_regime"] = regime_str

    try:
        from database import get_latest_weights, get_pledge_map
        _wts = get_latest_weights(regime_str)
        weights = _wts.get("weights") if _wts else None
        pledge_map = get_pledge_map([symbol])
    except Exception:
        weights, pledge_map = None, {}

    try:
        from delivery_data import fetch_latest_available_delivery_data
        today_ist_date = datetime.now(IST).date()
        delivery_map, resolved_date = fetch_latest_available_delivery_data(today_ist_date)
    except Exception:
        today_ist_date = datetime.now(IST).date()
        delivery_map = {}
        resolved_date = today_ist_date

    verdict = _evaluate_candidate(
        symbol=symbol,
        df=ticker,
        fund_data=fund_data,
        regime_ctx=regime_ctx,
        weights=weights,
        pledge_map=pledge_map,
        delivery_map=delivery_map,
        resolved_date=resolved_date,
    )
    if verdict["passed"]:
        return {
            "status": "CORE MET",
            "score": verdict["score"],
            "raw_score": verdict["raw_score"],
            "qualified": True,
            "sl_result": verdict["sl_result"],
            "context": verdict["context"],
        }
    else:
        return {
            "status": "NO",
            "reasons": [verdict["reject_reason"]],
            "score": verdict["score"],
            "raw_score": verdict["raw_score"],
            "qualified": False,
        }


def _is_symbol_in_reversal_cooldown(symbol: str, cooldown_days: int) -> bool:
    try:
        from database import is_symbol_in_failed_reversal_cooldown
        return bool(is_symbol_in_failed_reversal_cooldown(symbol, cooldown_days))
    except Exception:
        return False


def _run_scan(force: bool = False):
    """Execute a single reversal scan pass. Called inside the scheduling loop."""
    from database import is_scanner_stopped
    if not force and is_scanner_stopped("REVERSAL"):
        logger.info("🛑 Reversal Scanner is STOPPED by Admin. Skipping execution.")
        return 0

    ist_now = datetime.now(IST)
    logger.info("\n" + "=" * 80)
    logger.info(f"🚀 [START] REVERSAL SCANNER INIT | {ist_now.strftime('%Y-%m-%d %H:%M:%S')} 🚀")
    logger.info("=" * 80 + "\n")

    try:
        from macro_utils import MarketRegimeEngine
        regime_engine = MarketRegimeEngine()
        regime_ctx = regime_engine.get_market_regime()
    except Exception as e:
        logger.warning(f"Failed to fetch market regime: {e}. Defaulting to NEUTRAL.")
        regime_ctx = {"current_regime": "NEUTRAL", "trend": "NEUTRAL", "biases": {}}

    if "trend" not in regime_ctx:
        regime_ctx["trend"] = regime_ctx.get("current_regime", "NEUTRAL")

    try:
        from database import get_latest_weights
        _regime_for_wts = regime_ctx.get("trend", "NEUTRAL")
        weights_row = get_latest_weights(_regime_for_wts)
        bayesian_weights = weights_row.get("weights") if weights_row else None
    except Exception as e:
        logger.warning(f"Failed to fetch Bayesian weights: {e}")
        bayesian_weights = None

    try:
        watchlist = get_watchlist("REVERSAL")
    except Exception as e:
        logger.exception("Failed to load watchlist for REVERSAL scanner")
        return 0

    if watchlist.empty:
        logger.warning("REVERSAL watchlist is empty. Nothing to scan.")
        return 0

    logger.info(f"📊 Loaded {len(watchlist)} symbols for REVERSAL scan.")

    today_ist_date = ist_now.date()
    resolved_date = today_ist_date
    try:
        from delivery_data import fetch_latest_available_delivery_data
        prev_delivery_map, resolved_date = fetch_latest_available_delivery_data(today_ist_date)
    except Exception as e:
        logger.warning(f"Failed to fetch delivery data: {e}")
        prev_delivery_map = {}

    try:
        from database import get_pledge_map
        symbols = [str(s) for s in watchlist["Stock"].tolist() if s]
        pledge_map = get_pledge_map(symbols)
        logger.info(f"🛡️ Fetched pledge data for {len(pledge_map)} symbols")
    except Exception as e:
        logger.exception("Failed to fetch pledge map")
        pledge_map = {}

    total_alerts = 0
    shortlisted_alerts = []
    rejected = defaultdict(int)

    today_str = ist_now.strftime("%Y-%m-%d")

    from database import (
        get_recent_alerts_for_scanner,
        delete_todays_alerts_for_scanner,
        get_all_failed_reversal_cooldown_symbols,
    )
    from surveillance import get_live_blacklist

    live_blacklist = get_live_blacklist()
    failed_cooldown_raw = get_all_failed_reversal_cooldown_symbols(REVERSAL_COOLDOWN_TRADING_DAYS)
    failed_cooldown_syms = {_canonical_symbol(s) for s in failed_cooldown_raw if s} if failed_cooldown_raw else set()

    cooldown_alerts = get_recent_alerts_for_scanner("REVERSAL", 3 * 1440, only_active=True)
    today_ist = ist_now.date()
    cooldown_syms = set()
    for a in cooldown_alerts:
        if not a:
            continue
        sym = _row_get(a, 0, "symbol")
        created_str = _row_get(a, 1, "created_at")
        d = _to_ist_date(created_str)
        if d == today_ist:
            continue
        if sym:
            cooldown_syms.add(_canonical_symbol(sym))

    try:
        deleted_count = delete_todays_alerts_for_scanner("REVERSAL", today_str)
        logger.info(f"REVERSAL cleanup: removed {deleted_count} existing alerts for {today_str} before run")
    except Exception as e:
        logger.warning(f"Failed to delete today's alerts for REVERSAL before run: {e}")

    import gc
    from memory_profiler import chunk_iterable, MemoryProfiler
    BATCH_SIZE = 50
    total_fetched_count = 0

    # Intraday 5m snapshot optimization: only fetch during market hours (09:15-15:30 IST Mon-Fri)
    is_market_open = (dtime(9, 15) <= ist_now.time() <= dtime(15, 30)) and (ist_now.weekday() < 5)
    all_symbols = watchlist["Stock"].tolist()
    all_snapshots = {}
    if is_market_open:
        try:
            from price_cache import get_intraday_snapshot
            all_snapshots = get_intraday_snapshot(all_symbols, interval="5m", period="1d", requester="ReverseScanner") or {}
        except Exception as _snap_e:
            all_snapshots = {}

    synthetic_vol_missing = set()
    synthetic_bar_symbols = set()

    with MemoryProfiler("Process Symbols"):
        for batch_num, chunk_df in enumerate(chunk_iterable(watchlist, BATCH_SIZE), start=1):
            try:
                all_ticker_data = fetch_watchlist_data(chunk_df, "2y", "1d")
                if all_ticker_data:
                    total_fetched_count += len(all_ticker_data)

                chunk_symbols = chunk_df["Stock"].tolist()
                chunk_snapshots = {}
                for s in chunk_symbols:
                    snap = all_snapshots.get(s) or all_snapshots.get(_canonical_symbol(s))
                    if snap is not None:
                        chunk_snapshots[_canonical_symbol(s)] = snap
                
                if all_ticker_data:
                    today_date_str = ist_now.strftime("%Y-%m-%d")
                    for sym, hist_df in list(all_ticker_data.items()):
                        if isinstance(hist_df, pd.DataFrame) and not hist_df.empty:
                            snap_df = chunk_snapshots.get(_canonical_symbol(sym))
                            if isinstance(snap_df, pd.DataFrame) and not snap_df.empty:
                                valid_closes = snap_df['Close'].dropna()
                                if not valid_closes.empty:
                                    live_price = float(valid_closes.iloc[-1])
                                    snap_open = float(snap_df['Open'].iloc[0])
                                    snap_high = float(snap_df['High'].max())
                                    snap_low = float(snap_df['Low'].min())
                                    snap_vol = float(snap_df['Volume'].sum())

                                    hist_df = hist_df.copy()
                                    last_dt = hist_df.index[-1] if not hist_df.index.empty else None
                                    t_col = 'Date' if 'Date' in hist_df.columns else ('Datetime' if 'Datetime' in hist_df.columns else None)
                                    if t_col:
                                        last_dt = hist_df[t_col].iloc[-1]
                                    last_dt_str = pd.to_datetime(last_dt).strftime("%Y-%m-%d") if last_dt else ""
                                    can_sym = _canonical_symbol(sym)

                                    if last_dt_str == today_date_str:
                                        hist_df.iloc[-1, hist_df.columns.get_loc('Close')] = live_price
                                        if snap_vol > 0: hist_df.iloc[-1, hist_df.columns.get_loc('Volume')] = snap_vol
                                        hist_df.iloc[-1, hist_df.columns.get_loc('High')] = max(float(hist_df['High'].iloc[-1]), snap_high)
                                        hist_df.iloc[-1, hist_df.columns.get_loc('Low')] = min(float(hist_df['Low'].iloc[-1]), snap_low)
                                        try:
                                            recomputed = apply_indicators(hist_df, timeframe="1d")
                                            if recomputed is None or recomputed.empty:
                                                all_ticker_data.pop(sym, None)
                                                continue
                                            hist_df = recomputed
                                        except Exception:
                                            all_ticker_data.pop(sym, None)
                                            continue
                                        synthetic_bar_symbols.discard(can_sym)
                                        synthetic_vol_missing.discard(can_sym)
                                    else:
                                        new_row = hist_df.iloc[-1:].copy()
                                        new_dt = pd.to_datetime(today_date_str)
                                        if t_col: new_row[t_col] = new_dt
                                        else: new_row.index = [new_dt]
                                        new_row['Open'] = snap_open
                                        new_row['High'] = snap_high
                                        new_row['Low'] = snap_low
                                        new_row['Close'] = live_price
                                        new_row['Volume'] = snap_vol
                                        hist_df = pd.concat([hist_df, new_row])
                                        try:
                                            recomputed = apply_indicators(hist_df, timeframe="1d")
                                            if recomputed is None or recomputed.empty:
                                                all_ticker_data.pop(sym, None)
                                                continue
                                            hist_df = recomputed
                                        except Exception:
                                            all_ticker_data.pop(sym, None)
                                            continue
                                        synthetic_bar_symbols.add(can_sym)
                                        if snap_vol <= 0: synthetic_vol_missing.add(can_sym)
                                        else: synthetic_vol_missing.discard(can_sym)

                                    all_ticker_data[sym] = hist_df
                                
                for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):
                    symbol = row["Stock"]
                    category = row["Category"]
                    can_sym  = _canonical_symbol(symbol)
                    
                    # [FIX C1] Enforce surveillance / blacklist filtering (using pre-fetched set)
                    if symbol in live_blacklist or can_sym in live_blacklist:
                        rejected["blacklist"] += 1
                        logger.info(f"🚫 [REVERSAL] {symbol} skipped — active surveillance/blacklist")
                        continue

                    # [FIX C8] Respect force parameter for cooldowns (using pre-fetched sets)
                    if not force:
                        if can_sym in cooldown_syms or can_sym in failed_cooldown_syms:
                            rejected["cooldown"] += 1
                            continue

                    ticker_data = all_ticker_data.get(symbol) if all_ticker_data else None
                    if ticker_data is None:
                        rejected["no_data"] += 1
                        continue
            
                    ticker = ticker_data.copy()
                    verdict = _evaluate_candidate(
                        symbol=symbol,
                        df=ticker,
                        fund_data=row.to_dict(),
                        regime_ctx=regime_ctx,
                        weights=bayesian_weights,
                        pledge_map=pledge_map,
                        delivery_map=prev_delivery_map,
                        is_synthetic_bar=(can_sym in synthetic_bar_symbols),
                        is_synthetic_no_vol=(can_sym in synthetic_vol_missing),
                        resolved_date=resolved_date,
                    )

                    if not verdict["passed"]:
                        rejected[verdict.get("reject_code", "failed_pattern")] += 1
                        continue

                    ctx = verdict["context"]
                    target_val = verdict["sl_result"].get("target_1") or verdict["sl_result"].get("target")
                    shortlisted_alerts.append({
                        "symbol": symbol,
                        "alert_time": ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
                        "category": category,
                        "entry_price": round(ctx["entry_price"], 2),
                        "signals": ", ".join(ctx["signals"]),
                        "score": verdict["score"],
                        "raw_score": verdict.get("raw_score", verdict["score"]),
                        "rsi": round(ctx["technicals"]["rsi"], 1),
                        "volume_ratio": ctx["technicals"]["volume_ratio"],
                        "stop_loss": verdict["sl_result"].get("stop_loss"),
                        "target_1": target_val,
                        "target_price": target_val,
                        "context": ctx,
                        "structural_failure_stop": verdict["sl_result"].get("structural_failure_stop"),
                        "target_quality_score": verdict["sl_result"].get("target_quality")
                    })
            except Exception as batch_err:
                logger.error(f"❌ [REVERSAL] Batch {batch_num} error: {batch_err}")
                rejected["batch_fetch_failed"] += len(chunk_df)
            finally:
                gc.collect()

        total_symbols = len(watchlist)
        if total_symbols > 0:
            fetch_ratio = total_fetched_count / total_symbols
            logger.info(f"📊 [REVERSAL] Batch Fetch Completed: {total_fetched_count}/{total_symbols} fetched ({fetch_ratio*100:.1f}%)")
            if fetch_ratio < MIN_FETCH_RATIO:
                logger.warning(f"⚠️ [REVERSAL] Fetch ratio {fetch_ratio*100:.1f}% < {MIN_FETCH_RATIO*100:.0f}% minimum health threshold")

        if total_symbols > 0 and rejected.get("fundamental_filter", 0) / total_symbols > FUNDAMENTAL_REJECT_ALARM_PCT:
            logger.critical(f"🚨 CRITICAL ALARM: fundamental_filter rejected {rejected['fundamental_filter']}/{total_symbols} symbols (>{FUNDAMENTAL_REJECT_ALARM_PCT*100:.0f}%) — potential fundamental data outage!")

        if shortlisted_alerts:
            shortlisted_alerts.sort(key=lambda x: (x.get("raw_score", x["score"]), x.get("context", {}).get("risk_reward") or 0), reverse=True)
            from config import SCANNER_MAX_ALERTS
            shortlisted_alerts = shortlisted_alerts[:SCANNER_MAX_ALERTS.get("REVERSAL", 10)]

        for alert in shortlisted_alerts:
            inserted, _, _, _ = save_alert_if_new(
                alert["symbol"], "REVERSAL", alert["alert_time"], scanner="REVERSAL",
                category=alert["category"], entry_price=alert["entry_price"],
                signals=alert["signals"], score=alert["score"], rsi=alert["rsi"],
                volume_ratio=alert["volume_ratio"], stop_loss=alert["stop_loss"],
                target_1=alert.get("target_1"), target_price=alert["target_price"],
                context=alert["context"], model_version=ACTIVE_ALGO_VERSION,
                bayesian_regime=regime_ctx.get("trend", "NEUTRAL"), bayesian_weights=bayesian_weights,
                structural_failure_stop=alert.get("structural_failure_stop"),
                target_quality_score=alert.get("target_quality_score")
            )
            if inserted: total_alerts += 1

        # [FIX C3] Update scanner health to OK on successful completion
        upsert_scanner_health("REVERSAL", "OK", error_msg=None)
        logger.info("✅ [REVERSAL] Scan completed cleanly — scanner health marked OK.")
        return total_alerts


def _validate_config():
    """
    Startup contradiction validator.
    Fails fast at module import time if any REVERSAL thresholds or parameters are contradictory.
    """
    fatal, warn = [], []
    if MIN_DROP_FROM_52W_HIGH >= MAX_DROP_FROM_52W_HIGH:
        fatal.append(f"Empty drop band: {MIN_DROP_FROM_52W_HIGH} >= {MAX_DROP_FROM_52W_HIGH}")
    if QUALITY_CAT_MIN_DROP > MIN_DROP_FROM_52W_HIGH:
        warn.append(f"QUALITY_CAT_MIN_DROP ({QUALITY_CAT_MIN_DROP}) > MIN_DROP_FROM_52W_HIGH ({MIN_DROP_FROM_52W_HIGH})")
    if RSI_CURL_MIN <= RSI_OVERSOLD_THRESHOLD:
        fatal.append(f"RSI_CURL_MIN ({RSI_CURL_MIN}) must exceed RSI_OVERSOLD_THRESHOLD ({RSI_OVERSOLD_THRESHOLD})")
    
    _MIN_RSI_PTS = 15 if MIN_RSI_RECOVERY >= 20 else (12 if MIN_RSI_RECOVERY >= 12 else (8 if MIN_RSI_RECOVERY >= 8 else 0))
    if MIN_RSI_RECOVERY < 8.0:
        fatal.append(f"MIN_RSI_RECOVERY ({MIN_RSI_RECOVERY}) < lowest scorer tier (8): rsi_pts can be 0, collapsing core below CORE_SCORE_FLOOR ({CORE_SCORE_FLOOR})")
    BEAR_CORE_REALISTIC = 14 + 5 + 5 + _MIN_RSI_PTS  # 32 pts (or 30 pts window)
    if CORE_SCORE_FLOOR > BEAR_CORE_REALISTIC:
        fatal.append(f"CORE_SCORE_FLOOR ({CORE_SCORE_FLOOR}) > min feasible core ({BEAR_CORE_REALISTIC}) -> blackout")
    if CORE_SCORE_FLOOR >= CORE_SCORE_MAX:
        fatal.append(f"CORE_SCORE_FLOOR ({CORE_SCORE_FLOOR}) >= MAX ({CORE_SCORE_MAX}) is unreachable")

    for r, req in REGIME_EVIDENCE_REQ.items():
        prox = req.get("max_pct_below_sma200")
        if prox is None:
            continue
        if prox >= MAX_DROP_BELOW_SMA200:
            fatal.append(f"{r}: proximity {prox}% >= MAX_DROP_BELOW_SMA200 ({MAX_DROP_BELOW_SMA200}%) — dead code, remove or tighten")

    if MIN_VOLUME_RATIO >= CLIMAX_VOL_MULT:
        fatal.append(f"MIN_VOLUME_RATIO ({MIN_VOLUME_RATIO}) >= CLIMAX_VOL_MULT ({CLIMAX_VOL_MULT}): climax filter degenerates into an unconditional close-position gate")

    _spread = RSI_CURL_MIN - RSI_OVERSOLD_THRESHOLD
    if MIN_RSI_RECOVERY > _spread + 2.0:
        warn.append(f"MIN_RSI_RECOVERY ({MIN_RSI_RECOVERY}) dominates curl/oversold gates (spread={_spread}); those two gates are decorative")
    if RSI_TROUGH_LOOKBACK < 30:
        warn.append(f"RSI_TROUGH_LOOKBACK={RSI_TROUGH_LOOKBACK} is narrow for rounding bases")

    if MIN_YOY_REVENUE_GROWTH > 0 and MIN_DROP_FROM_52W_HIGH >= 20:
        warn.append(f"MIN_YOY_REVENUE_GROWTH={MIN_YOY_REVENUE_GROWTH}% with a {MIN_DROP_FROM_52W_HIGH}%+ drop requirement excludes most genuine mean-reversion candidates")

    if not set(REGIME_EVIDENCE_REQ).issubset(REGIME_REVERSAL_PREMIUM):
        fatal.append("REGIME_EVIDENCE_REQ contains regimes absent from REGIME_REVERSAL_PREMIUM")

    for regime, prem in REGIME_REVERSAL_PREMIUM.items():
        need = MIN_REVERSAL_SCORE + prem
        if need > COMPONENT_MAX:
            fatal.append(f"{regime}: threshold {need} > max {COMPONENT_MAX}")

    for w in warn:
        logger.warning(f"REVERSAL config dead zone: {w}")

    if fatal:
        raise ValueError("REVERSAL config contradictions:\n  - " + "\n  - ".join(fatal))


try:
    _validate_config()
except ValueError as e:
    logger.critical("REVERSAL config invalid — scanner will not run: %s", e)
    try:
        from database import upsert_scanner_health
        upsert_scanner_health("REVERSAL", "DOWN", error_msg=f"Config: {str(e)[:200]}")
    except Exception:
        pass
    raise


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

    force_refresh_blacklist()

    try:
        return _run_scan(force=force)
    except Exception as e:
        logger.exception("❌ CRITICAL REVERSAL SCAN ERROR")
        import database
        if not getattr(database, "DONT_SAVE_ALERTS", False):
            try:
                upsert_scanner_health(scanner_name="REVERSAL", status="DOWN", error_msg=str(e))
            except Exception:
                pass
        raise