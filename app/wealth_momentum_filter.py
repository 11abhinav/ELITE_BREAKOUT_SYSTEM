"""
Minimal momentum-quality filter used by the Wealth Engine.

This module purposely implements a small, dependency-light routine that
derives a momentum "quality" score and a confidence label from a 1-year
price DataFrame produced by `fetch_historical_data` used elsewhere in the
project. The goal is to provide a defensive, easy-to-reason-about
implementation so the main engine can run even if the original, more
advanced module is not present.

API:
    calculate_momentum_quality_score(hist: pandas.DataFrame) -> (int, str)

Returned values:
    - score: int in range [0, 100]
    - confidence: one of "HIGH", "MEDIUM", "LOW"

This implementation follows the Ponytail guidance: only the minimum
amount of code required to be useful.
"""
from __future__ import annotations
import pandas as pd
from typing import Tuple


def _safe_last(series: pd.Series):
    try:
        return float(series.iloc[-1])
    except Exception:
        return None


def calculate_momentum_quality_score(hist: pd.DataFrame) -> Tuple[int, str]:
    """Compute a simple momentum-quality score and confidence label.

    Inputs
    - hist: expected to contain at least columns ['Close','High','Low','Volume']

    The score is a deterministic aggregation of:
      - 6-month return
      - 50-day vs 200-day SMA relationship
      - EMA(20) slope direction
      - RSI (14)
      - ATR(14) normalized

    Returns (score:int 0-100, confidence:str)
    """
    # Defensive checks
    if hist is None or not isinstance(hist, pd.DataFrame) or hist.empty:
        return 0, "LOW"

    # Ensure required columns exist
    for col in ("Close", "High", "Low", "Volume"):
        if col not in hist.columns:
            return 0, "LOW"

    try:
        df = hist.copy()

        # Normalize length; if very short, return low confidence
        confidence = "HIGH" if len(df) >= 200 else ("MEDIUM" if len(df) >= 60 else "LOW")

        # Simple indicators
        df["sma_50"] = df["Close"].rolling(window=50, min_periods=10).mean()
        df["sma_200"] = df["Close"].rolling(window=200, min_periods=50).mean()
        df["ema_20"] = df["Close"].ewm(span=20, adjust=False).mean()

        # RSI (14)
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=7).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=7).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # ATR(14)
        prev = df["Close"].shift(1)
        tr1 = (df["High"] - df["Low"]).abs()
        tr2 = (df["High"] - prev).abs()
        tr3 = (df["Low"] - prev).abs()
        df["TR"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["ATR"] = df["TR"].rolling(window=14, min_periods=7).mean()

        # 6-month return estimate (approx 126 trading days). If not available, use full-range
        if len(df) >= 126:
            start_price = df["Close"].iloc[-126]
        else:
            start_price = df["Close"].iloc[0]
        end_price = _safe_last(df["Close"])
        six_m_ret = ((end_price - start_price) / start_price) * 100 if start_price and end_price else 0.0

        # Momentum sub-scores (each 0-20)
        score = 0

        # 1) 6-month return
        if six_m_ret >= 25:
            score += 20
        elif six_m_ret >= 15:
            score += 15
        elif six_m_ret >= 5:
            score += 8

        # 2) Trend bias: price relative to SMA200 and SMA50
        last_close = _safe_last(df["Close"])
        last_sma50 = _safe_last(df["sma_50"]) or 0
        last_sma200 = _safe_last(df["sma_200"]) or 0
        if last_sma200 and last_close and last_close > last_sma200:
            score += 8
        if last_sma50 and last_close and last_close > last_sma50:
            score += 6

        # 3) EMA slope (short-term acceleration)
        try:
            ema20 = df["ema_20"].dropna()
            if len(ema20) >= 3 and ema20.iloc[-1] > ema20.iloc[-3]:
                score += 6
        except Exception:
            pass

        # 4) RSI sanity: penalize extreme overbought/oversold (gives quality signal)
        last_rsi = _safe_last(df.get("RSI", pd.Series([])))
        if last_rsi is not None:
            if 40 <= last_rsi <= 70:
                score += 6
            elif 30 <= last_rsi < 40:
                score += 3

        # 5) ATR relative: lower ATR (percent) => higher quality (0-10)
        last_atr = _safe_last(df.get("ATR", pd.Series([])))
        if last_atr and last_close:
            atr_pct = (last_atr / last_close) * 100
            if atr_pct < 1.5:
                score += 6
            elif atr_pct < 3.0:
                score += 3

        # Clamp
        final_score = int(max(0, min(100, score)))

        return final_score, confidence
    except Exception:
        return 0, "LOW"

