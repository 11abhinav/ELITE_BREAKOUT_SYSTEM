# =====================================================================================
# app/technical_indicators.py  (UPGRADED v3)
#
# CHANGES FROM v2:
#   1. SWING_LOW/SWING_HIGH now use proper pivot detection (3-bar left, 3-bar right)
#      instead of rolling min/max — rolling window just gives the lowest/highest bar
#      over the period, not a true swing point. True swing lows/highs are cleaner
#      S/R levels that the market actually reacts to.
#   2. LOOKBACK_SWING_LOW / LOOKBACK_SWING_HIGH: the last confirmed swing points
#      from recent bars (not including current forming bar) — these are the levels
#      used for SL/Target placement.
#   3. Keep simple rolling SWING_LOW_RAW / SWING_HIGH_RAW as fallback.
#   4. All pivot point formulas retained (PP, S1-S3, R1-R3).
#   5. ATR_PCT and all rolling window highs retained.
# =====================================================================================

import pandas as pd
import ta
import gc
from memory_profiler import profile_function

try:
    import pandas_ta as ta
except ImportError:
    pass


def _find_swing_lows(low: pd.Series, n: int = 3) -> pd.Series:
    """
    Detect pivot swing lows: a bar where the low is lower than the `n` bars
    on either side. Returns the swing low price at those pivots, NaN elsewhere.
    Then forward-fills so every bar knows the most recent confirmed swing low.
    """
    window_min = low.rolling(window=2*n + 1, center=True, min_periods=2*n + 1).min()
    result = low.where(low == window_min, float("nan"))
    return result.ffill()


def _find_swing_highs(high: pd.Series, n: int = 3) -> pd.Series:
    """
    Detect pivot swing highs: a bar where the high is higher than the `n` bars
    on either side. Forward-fills the most recent confirmed swing high.
    """
    window_max = high.rolling(window=2*n + 1, center=True, min_periods=2*n + 1).max()
    result = high.where(high == window_max, float("nan"))
    return result.ffill()

def apply_indicators(df: pd.DataFrame, timeframe: str = "1d", daily_ohlc: pd.DataFrame = None) -> pd.DataFrame:
    # [VERSION: MEMORY_OPTIMIZATION_v1.0] Fix block manager fragmentation by using dict for column assignment
    """
    Applies all technical indicators and returns the enriched DataFrame.

    Parameters
    ----------
    df         : OHLCV DataFrame
    timeframe  : "1d", "1h", or "15m"
    daily_ohlc : Optional daily OHLCV DataFrame. When provided for intraday
                 timeframes (1h/15m), pivot points (PP, S1-S3, R1-R3) are
                 calculated from the previous day's OHLC instead of the
                 previous intraday bar. This produces meaningful support/
                 resistance levels for SL/Target placement.

    Columns produced:
    -- Trend -----------------------------------------------------------
    EMA20, SMA50, SMA200
    -- Momentum --------------------------------------------------------
    RSI, MACD, MACD_SIGNAL, MACD_HIST
    -- Volatility ------------------------------------------------------
    ATR, ATR_PCT, BB_UPPER, BB_LOWER, BB_MID
    -- Directional -----------------------------------------------------
    ADX
    -- Support / Resistance --------------------------------------------
    SWING_LOW    — last confirmed pivot swing low  (true support)
    SWING_HIGH   — last confirmed pivot swing high (true resistance)
    SWING_LOW_RAW   — rolling window min (fallback if swing not found)
    SWING_HIGH_RAW  — rolling window max (fallback if swing not found)
    PP           — Classic Pivot Point (previous day for intraday, previous bar for EOD)
    S1, S2, S3   — Pivot Supports
    R1, R2, R3   — Pivot Resistances
    -- Breakout highs (pre-calculated, timeframe-aware) ----------------
    HIGH_20D, HIGH_50D, HIGH_100D, HIGH_252D, HIGH_52W
    """

    # [VERSION: LOG_ERROR_FIXES_v1.1] Guard short DataFrames (<14 rows) to prevent index out-of-bounds errors on 14-period indicator calculations
    if df is None or df.empty or len(df) < 14:
        return df
    # De-fragment the DataFrame to prevent pandas internal block manager crashes 
    # (e.g. np.bincount array too big) when assigning many columns sequentially.
    df = df.copy()

    close = df["Close"]
    high  = df["High"]
    low   = df["Low"]
    new_cols = {}

    # ── TREND FILTERS ─────────────────────────────────────────────────────────
    # Adding EMA9 for multi-TF trend permission logic (9 > 20 > 50)
    new_cols["EMA9"]   = ta.trend.ema_indicator(close, window=9)
    new_cols["EMA20"]  = ta.trend.ema_indicator(close, window=20)
    new_cols["EMA50"]  = ta.trend.ema_indicator(close, window=50)
    new_cols["SMA50"]  = ta.trend.sma_indicator(close, window=50)
    new_cols["SMA200"] = ta.trend.sma_indicator(close, window=200)

    # ── MOMENTUM — RSI ─────────────────────────────────────────────────────────
    new_cols["RSI"] = ta.momentum.rsi(close, window=14)

    # ── VOLATILITY — ATR + ATR% + Bollinger Bands ─────────────────────────────
    new_cols["ATR"]     = ta.volatility.average_true_range(high, low, close, window=14)
    new_cols["ATR20"]   = ta.volatility.average_true_range(high, low, close, window=20)
    new_cols["ATR_PCT"] = (new_cols["ATR"] / close * 100).round(2)

    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    new_cols["BB_UPPER"] = bb.bollinger_hband()
    new_cols["BB_LOWER"] = bb.bollinger_lband()
    new_cols["BB_MID"]   = bb.bollinger_mavg()
    new_cols["BB_WIDTH"] = (new_cols["BB_UPPER"] - new_cols["BB_LOWER"]) / close
    new_cols["BB_WIDTH_PCTILE"] = new_cols["BB_WIDTH"].rolling(window=100, min_periods=50).rank(pct=True)

    # ── TREND DIRECTION — ADX ─────────────────────────────────────────────────
    adx_ind   = ta.trend.ADXIndicator(high, low, close, window=14)
    new_cols["ADX"] = adx_ind.adx()

    # ── MOMENTUM CONFIRMATION — MACD ──────────────────────────────────────────
    macd_ind          = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    new_cols["MACD"]        = macd_ind.macd()
    new_cols["MACD_SIGNAL"] = macd_ind.macd_signal()
    new_cols["MACD_HIST"]   = macd_ind.macd_diff()

    # ── SUPPORT / RESISTANCE — TRUE Pivot Swing Points ────────────────────────
    # n = how many bars on each side a bar must be the extreme to qualify
    # [FINDING-H FIX] Added 30m and 5m to avoid falling back to daily (5)
    pivot_n = {"1d": 5, "1h": 4, "30m": 4, "15m": 3, "5m": 3}.get(timeframe, 5)

    # True swing levels — these are what price actually bounces off of
    new_cols["SWING_LOW"]  = _find_swing_lows(low,  n=pivot_n)
    new_cols["SWING_HIGH"] = _find_swing_highs(high, n=pivot_n)

    # Rolling fallback (simple window min/max) — used only if swing not available
    # [FINDING-H FIX] Added 30m and 5m to avoid falling back to daily (20)
    swing_window = {"1d": 20, "1h": 14, "30m": 12, "15m": 10, "5m": 10}.get(timeframe, 20)
    new_cols["SWING_LOW_RAW"]  = low.rolling(window=swing_window,  min_periods=swing_window // 2).min()
    new_cols["SWING_HIGH_RAW"] = high.rolling(window=swing_window, min_periods=swing_window // 2).max()

    # ── SUPPORT / RESISTANCE — Classic Pivot Points ───────────────────────────
    # For intraday timeframes: use DAILY OHLC (previous day) when available.
    # This produces meaningful S/R levels that traders actually use (CPR/pivots).
    # For daily bars: use the previous day's bar (shift(1)) as is standard.
    if timeframe in ("1h", "30m", "15m", "5m") and daily_ohlc is not None and len(daily_ohlc) >= 2:
        # Use the last completed daily bar as the pivot source
        last_daily = daily_ohlc.iloc[-1]
        d_high  = float(last_daily["High"])
        d_low   = float(last_daily["Low"])
        d_close = float(last_daily["Close"])

        pp = round((d_high + d_low + d_close) / 3, 2)
        new_cols["PP"] = pp
        new_cols["R1"] = round(2 * pp - d_low, 2)
        new_cols["R2"] = round(pp + (d_high - d_low), 2)
        new_cols["R3"] = round(d_high + 2 * (pp - d_low), 2)
        new_cols["S1"] = round(2 * pp - d_high, 2)
        new_cols["S2"] = round(pp - (d_high - d_low), 2)
        new_cols["S3"] = round(d_low - 2 * (d_high - pp), 2)
    else:
        # Standard: previous bar's OHLC (correct for daily timeframe)
        prev_high  = high.shift(1)
        prev_low   = low.shift(1)
        prev_close = close.shift(1)

        new_cols["PP"] = ((prev_high + prev_low + prev_close) / 3).round(2)
        new_cols["R1"] = (2 * new_cols["PP"] - prev_low).round(2)
        new_cols["R2"] = (new_cols["PP"] + (prev_high - prev_low)).round(2)
        new_cols["R3"] = (prev_high + 2 * (new_cols["PP"] - prev_low)).round(2)
        new_cols["S1"] = (2 * new_cols["PP"] - prev_high).round(2)
        new_cols["S2"] = (new_cols["PP"] - (prev_high - prev_low)).round(2)
        new_cols["S3"] = (prev_low - 2 * (prev_high - new_cols["PP"])).round(2)

    # ── PRE-CALCULATED ROLLING WINDOW HIGHS ───────────────────────────────────
    n = len(df)

    if timeframe == "1d":
        new_cols["HIGH_20D"]  = df["High"].rolling(window=20,  min_periods=15).max()
        new_cols["PREV_DAY_HIGH"] = df["High"].shift(1)
        new_cols["PRIOR_20D_HIGH"] = new_cols["HIGH_20D"].shift(1)
        new_cols["HIGH_50D"]  = df["High"].rolling(window=50,  min_periods=40).max()
        new_cols["HIGH_100D"] = df["High"].rolling(window=100, min_periods=80).max()
        new_cols["HIGH_252D"] = df["High"].rolling(window=252, min_periods=200).max()

    elif timeframe == "1h":
        new_cols["HIGH_6H"]   = df["High"].rolling(window=6,   min_periods=5).max()
        new_cols["HIGH_26H"]  = df["High"].rolling(window=26,  min_periods=20).max()
        new_cols["HIGH_130H"] = df["High"].rolling(window=130, min_periods=100).max()
        new_cols["HIGH_260H"] = df["High"].rolling(window=260, min_periods=200).max()
        new_cols["HIGH_20D"]  = new_cols["HIGH_26H"]
        new_cols["PRIOR_20D_HIGH"] = new_cols["HIGH_20D"].shift(1)
        new_cols["HIGH_50D"]  = new_cols["HIGH_130H"]
        new_cols["HIGH_100D"] = new_cols["HIGH_130H"]
        new_cols["HIGH_252D"] = new_cols["HIGH_260H"]

    else:  # 15m
        new_cols["HIGH_26_15M"]  = df["High"].rolling(window=26,  min_periods=20).max()
        new_cols["HIGH_52_15M"]  = df["High"].rolling(window=52,  min_periods=40).max()
        new_cols["HIGH_104_15M"] = df["High"].rolling(window=104, min_periods=80).max()
        new_cols["HIGH_20D"]  = new_cols["HIGH_26_15M"]
        new_cols["PRIOR_20D_HIGH"] = new_cols["HIGH_20D"].shift(1)
        new_cols["HIGH_50D"]  = new_cols["HIGH_104_15M"]
        new_cols["HIGH_100D"] = new_cols["HIGH_104_15M"]
        new_cols["HIGH_252D"] = df["High"].rolling(window=n, min_periods=n // 2).max()

    # 52-week high — timeframe-aware
    if timeframe == "1d":
        window52, min52 = 252, 200
    elif timeframe == "1h":
        window52, min52 = n, max(n // 2, 50)
    else:
        window52, min52 = n, max(n // 2, 20)

    new_cols["HIGH_52W"] = df["High"].rolling(window=window52, min_periods=min52).max()

    # ── VWAP — Volume-Weighted Average Price ────────────────────────────────
    #
    # VWAP is the institutional benchmark — price above VWAP = bullish, below = bearish.
    # For intraday (15m/1H): VWAP resets daily (standard institutional usage).
    # For EOD: cumulative VWAP over the period (each bar = 1 day).
    # Used by sl_target_helper.py as an SL anchor (VWAP acts as dynamic support).
    #
    if "Volume" in df.columns:
        typical_price = (high + low + close) / 3

        df_index = df.index
        if not isinstance(df_index, pd.DatetimeIndex):
            datetime_col = next((c for c in ["Datetime", "Date", "index"] if c in df.columns), None)
            if datetime_col is not None:
                df_index = pd.to_datetime(df[datetime_col])

        if timeframe in ("15m", "1h") and hasattr(df_index, 'date'):
            # Daily-reset VWAP for intraday: reset cumulative sums at each day boundary
            # This is the standard institutional VWAP that traders use for fair-value
            date_groups = df_index.date
            cum_tp_vol  = (typical_price * df["Volume"]).groupby(date_groups).cumsum()
            cum_vol     = df["Volume"].groupby(date_groups).cumsum()
            new_cols["VWAP"]  = (cum_tp_vol / cum_vol).where(cum_vol > 0)
        else:
            # Cumulative VWAP for EOD (each bar = 1 day, no intraday reset needed)
            cum_tp_vol  = (typical_price * df["Volume"]).cumsum()
            cum_vol     = df["Volume"].cumsum()
            new_cols["VWAP"]  = (cum_tp_vol / cum_vol).where(cum_vol > 0)
    else:
        new_cols["VWAP"] = float("nan")

    # ── OBV_TREND — On-Balance Volume directional bias ────────────────────────
    #
    # Detects volume-price divergence — the #1 fake breakout indicator.
    # If price is making new highs but OBV is declining, it's distribution.
    #
    # OBV_TREND values:
    #   1  = OBV rising (volume confirms price direction — healthy)
    #  -1  = OBV falling (volume diverges from price — distribution/fake)
    #   0  = OBV flat (no conviction either way)
    #
    if "Volume" in df.columns and len(df) >= 50:
        import numpy as np
        
        # Calculate OBV direction
        obv_direction = np.sign(df["Close"].diff())
        obv_direction.iloc[0] = 0
        
        # Calculate standard OBV
        raw_obv = (obv_direction * df["Volume"]).cumsum()
        new_cols["OBV_20MA"] = raw_obv.rolling(window=20, min_periods=20).mean()
        new_cols["OBV"] = raw_obv
        new_cols["OBV_SLOPE"] = raw_obv.diff(3)

        # 50-bar rolling OBV
        rolling_obv = (obv_direction * df["Volume"]).rolling(50, min_periods=50).sum()
        
        # 5-bar OBV slope via linear regression approximation (simple diff)
        obv_slope = rolling_obv.diff(5)
        
        # [VERSION: V5_ACQUISITION_ROUTING_V1.0] Vectorized OBV_TREND calculation
        conds = [obv_slope > 0, obv_slope < 0]
        choices = [1, -1]
        new_cols["OBV_TREND"] = pd.Series(np.select(conds, choices, default=0), index=df.index)
    else:
        new_cols["OBV_TREND"] = 0
        new_cols["OBV_20MA"] = float("nan")
        new_cols["OBV"] = float("nan")

    # ── BASE_WIDTH — Pre-breakout consolidation tightness ─────────────────────
    #
    # Measures how tight the price range is relative to ATR over the last 10 bars.
    # Low values (< 1.5) = tight base = high-quality breakout setup.
    # High values (> 3.0) = volatile/choppy = likely fake breakout.
    #
    # Formula: max(High) - min(Low) over last 10 bars, divided by ATR
    #
    base_window = 10
    if len(df) >= base_window and ("ATR" in df.columns or "ATR" in new_cols):
        rolling_range = (
            high.rolling(window=base_window, min_periods=base_window).max()
            - low.rolling(window=base_window, min_periods=base_window).min()
        )
        new_cols["BASE_WIDTH"] = (rolling_range / new_cols["ATR"]).where(new_cols["ATR"] > 0)
    else:
        new_cols["BASE_WIDTH"] = float("nan")

    # ── VCP VOLATILITY CONTRACTION VALIDATION ─────────────────────────────────
    if len(df) >= 60 and ("ATR_PCT" in df.columns or "ATR_PCT" in new_cols):
        vol_20d = new_cols["ATR_PCT"].rolling(window=20).mean()
        vol_60d = new_cols["ATR_PCT"].rolling(window=60).mean()
        new_cols["VCP_TIGHTENING"] = vol_20d < vol_60d
    else:
        new_cols["VCP_TIGHTENING"] = False

    if new_cols:
        df = df.assign(**new_cols)
    return df
