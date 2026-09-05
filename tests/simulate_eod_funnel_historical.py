#!/usr/bin/env python3
"""
tests/simulate_eod_funnel_historical.py
Comprehensive Empirical Funnel Simulation for EOD Scanner on Real Historical 1D Data.

Simulates the entire step-by-step filter cascade of app/eod_scanner.py:
1. Universe Watchlist
2. Data Sufficiency & Freshness
3. Breakout Detection (detect_breakouts)
4. Liquidity & Price Filters (Volume >= 1.5x, Avg Vol >= 50k, Close >= 100, RSI in [50, 88])
5. Structural 20D High Breakout (Close > PRIOR_20D_HIGH)
6. ATR Expansion (Range / ATR20 >= 0.8)
7. Moving Average Trend (Close >= EMA20, Close >= SMA50, ADX >= 18)
8. 52-Week High Proximity (Distance <= 5.0%)
9. Pre-Breakout Base Tightness (ATR10 <= 2.5% of Price)
10. Scoring Engine & Hard Disqualifiers (calculate_score)
11. BEAR Regime Score Hurdle (Score >= 80)
12. SL/RR Engine (Natural R:R >= 2.5R)
13. Final Alert Generation
"""

import os
import sys
import glob
from collections import defaultdict
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from config import (
    EOD_CONFIG,
    EOD_ADVANCED_CONFIG,
    SCORE_THRESHOLDS,
    MIN_NATURAL_RR,
    MIN_REWARD_POTENTIAL,
    MIN_STOCK_PRICE,
    ADX_MIN_THRESHOLD,
    REGIME_POLICIES
)
from technical_indicators import apply_indicators
from breakout_engine import detect_breakouts
from scoring_engine import calculate_score, check_hard_disqualifiers
from sl_target_helper import compute_sl_and_target
from eod_scanner import _check_eod_conditions, _safe_float


def run_eod_simulation(target_date: str = "2026-08-25", market_regime: str = "BEAR"):
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
    wl_path = os.path.join(base_dir, "elite_fundamental_watchlist.parquet")
    
    if os.path.exists(wl_path):
        wl = pd.read_parquet(wl_path)
        symbols = wl["Stock"].dropna().unique().tolist()
        symbol_fund_map = {row["Stock"]: row.to_dict() for _, row in wl.iterrows()}
    else:
        # Fallback to parquet filenames
        files = glob.glob(os.path.join(base_dir, "history", "1d", "*.parquet"))
        symbols = [os.path.basename(f).replace(".parquet", "") for f in files]
        symbol_fund_map = {}

    print("=" * 80)
    print(f"🚀 EOD SCANNER END-TO-END HISTORICAL FUNNEL SIMULATION")
    print("=" * 80)
    print(f"  • Target Date   : {target_date}")
    print(f"  • Market Regime : {market_regime}")
    print(f"  • Total Universe: {len(symbols)} stocks")
    print("=" * 80)

    funnel = defaultdict(int)
    disq_reasons = defaultdict(int)
    condition_reasons = defaultdict(int)
    qualified_candidates = []
    
    # Thresholds
    base_score_threshold = SCORE_THRESHOLDS.get("1d", 75)
    regime_mod = REGIME_POLICIES.get(market_regime, {}).get("score_modifier", 0)
    effective_score_threshold = min(base_score_threshold + regime_mod, 82)
    min_rr_threshold = MIN_NATURAL_RR.get("EOD", 2.5)

    print(f"  • Base Score Threshold      : {base_score_threshold}")
    print(f"  • Regime Strictness Penalty : +{regime_mod} ({market_regime})")
    print(f"  • Effective Min Score       : {effective_score_threshold}")
    print(f"  • Min Natural R:R           : {min_rr_threshold}R")
    print("-" * 80)

    for sym in symbols:
        funnel["1_TOTAL_UNIVERSE"] += 1
        p_path = os.path.join(base_dir, "history", "1d", f"{sym}.parquet")
        
        if not os.path.exists(p_path):
            funnel["2_NO_PRICE_DATA"] += 1
            continue

        try:
            df = pd.read_parquet(p_path)
        except Exception:
            funnel["2_READ_ERROR"] += 1
            continue

        if df.empty or len(df) < 50:
            funnel["2_INSUFFICIENT_BARS"] += 1
            continue

        # Slice up to target_date if present
        if isinstance(df.index, pd.DatetimeIndex):
            target_ts = pd.to_datetime(target_date).tz_localize("Asia/Kolkata") if df.index.tz is not None else pd.to_datetime(target_date)
            df_cut = df[df.index <= target_ts]
        elif "Date" in df.columns:
            df_cut = df[pd.to_datetime(df["Date"]) <= pd.to_datetime(target_date)]
        else:
            df_cut = df

        if len(df_cut) < 50:
            funnel["2_INSUFFICIENT_CUT_BARS"] += 1
            continue

        funnel["3_DATA_VALID"] += 1

        # Apply technical indicators
        try:
            ticker = apply_indicators(df_cut.copy(), timeframe="1d")
        except Exception as e:
            funnel["3_INDICATOR_FAIL"] += 1
            continue

        if ticker is None or ticker.empty:
            funnel["3_INDICATOR_FAIL"] += 1
            continue

        latest = ticker.iloc[-1]

        # Check individual technical gates
        candle_close = _safe_float(latest.get("Close"))
        candle_open = _safe_float(latest.get("Open"))
        candle_high = _safe_float(latest.get("High"))
        candle_low = _safe_float(latest.get("Low"))
        candle_range = candle_high - candle_low

        if len(ticker) >= 22:
            avg_volume = float(ticker["Volume"].iloc[-21:-1].mean())
        else:
            avg_volume = float(ticker["Volume"].iloc[:-1].mean())

        if avg_volume <= 0:
            funnel["4_ZERO_AVG_VOL"] += 1
            continue

        vol_ratio = _safe_float(latest.get("Volume")) / avg_volume
        rsi_val = _safe_float(latest.get("RSI"), 50.0)

        # Gate: Price floor (MIN_STOCK_PRICE = 100 or 20)
        if candle_close < MIN_STOCK_PRICE:
            funnel["4_PENNY_STOCK"] += 1
            continue

        # Gate: Average Volume floor (50k)
        if avg_volume < EOD_CONFIG.get("MIN_VOLUME_AVG", 50000):
            funnel["4_LOW_AVG_VOLUME"] += 1
            continue

        # Gate: Volume Ratio (1.5x)
        if vol_ratio < EOD_CONFIG.get("MIN_VOLUME_RATIO", 1.5):
            funnel["4_LOW_VOLUME_RATIO"] += 1
            continue

        # Gate: RSI Range [50, 88]
        if not (EOD_CONFIG.get("MIN_RSI", 50) <= rsi_val <= EOD_CONFIG.get("MAX_RSI", 88)):
            funnel["4_RSI_OUT_OF_BOUNDS"] += 1
            continue

        funnel["4_LIQUIDITY_PRICE_RSI_PASS"] += 1

        # Gate: Structural 20-Day High Breakout
        prior_20d_high = _safe_float(latest.get("PRIOR_20D_HIGH"))
        if prior_20d_high <= 0 or candle_close <= prior_20d_high:
            funnel["5_NO_STRUCTURAL_BREAKOUT"] += 1
            continue

        funnel["5_STRUCTURAL_BREAKOUT_PASS"] += 1

        # Gate: ATR Expansion
        atr20 = _safe_float(latest.get("ATR20"), _safe_float(latest.get("ATR"), candle_close * 0.025))
        min_atr_exp = EOD_ADVANCED_CONFIG.get("MIN_ATR_EXPANSION_RATIO", 0.8)
        if atr20 > 0:
            atr_exp = candle_range / atr20
            if atr_exp < min_atr_exp:
                funnel["6_NO_ATR_EXPANSION"] += 1
                continue
        funnel["6_ATR_EXPANSION_PASS"] += 1

        # Gate: Moving Average Alignment (EMA20 & SMA50)
        ema20 = _safe_float(latest.get("EMA20"))
        sma50 = _safe_float(latest.get("SMA50"))
        if ema20 > 0 and candle_close < ema20:
            funnel["7_BELOW_EMA20"] += 1
            continue
        if sma50 > 0 and candle_close < sma50:
            funnel["7_BELOW_SMA50"] += 1
            continue

        adx_val = _safe_float(latest.get("ADX"))
        if adx_val > 0 and adx_val < ADX_MIN_THRESHOLD:
            funnel["7_WEAK_ADX"] += 1
            continue

        funnel["7_TREND_ALIGNMENT_PASS"] += 1

        # Gate: 52-Week High Proximity (MAX_DISTANCE_FROM_52W_HIGH_PCT = 5.0%)
        high_52w = _safe_float(latest.get("HIGH_52W"))
        if high_52w > 0:
            pct_from_52w = (high_52w - candle_close) / high_52w * 100
            max_52w_dist = EOD_ADVANCED_CONFIG.get("MAX_DISTANCE_FROM_52W_HIGH_PCT", 5.0)
            if pct_from_52w > max_52w_dist:
                funnel["8_FAR_FROM_52W_HIGH"] += 1
                continue

        funnel["8_52W_PROXIMITY_PASS"] += 1

        # Gate: 10-Day Pre-Breakout ATR Base Tightness (<= 2.5% of Price)
        if len(ticker) >= 12 and candle_close > 0:
            h10 = ticker["High"].iloc[-11:-1]
            l10 = ticker["Low"].iloc[-11:-1]
            c10 = ticker["Close"].iloc[-12:-2]
            tr10 = np.maximum(h10 - l10, np.maximum(np.abs(h10 - c10), np.abs(l10 - c10)))
            atr10 = float(tr10.mean())
            max_atr10_pct = EOD_ADVANCED_CONFIG.get("MAX_BASE_ATR10_PCT", 2.5) / 100.0
            if atr10 > (candle_close * max_atr10_pct):
                funnel["9_BASE_ATR10_TOO_WIDE"] += 1
                continue

        funnel["9_BASE_TIGHTNESS_PASS"] += 1

        # Gate: detect_breakouts signals
        signals = detect_breakouts(ticker, timeframe="1d")
        if len(signals) < EOD_CONFIG.get("MIN_SIGNALS", 1):
            funnel["10_NO_BREAKOUT_SIGNALS"] += 1
            continue

        funnel["10_BREAKOUT_SIGNALS_PASS"] += 1

        # Check hard disqualifiers inside scoring engine
        disq, disq_reason = check_hard_disqualifiers(
            ticker=ticker,
            latest=latest,
            volume_ratio=vol_ratio,
            symbol=sym,
            timeframe="1d",
            min_vol=EOD_CONFIG.get("MIN_VOLUME_AVG", 50000)
        )
        if disq:
            funnel["11_SCORING_HARD_DISQUALIFIED"] += 1
            disq_reasons[disq_reason.split(":")[0] if ":" in disq_reason else disq_reason[:30]] += 1
            continue

        funnel["11_SCORING_DISQUALIFIERS_PASS"] += 1

        # Calculate Score
        fund_row = symbol_fund_map.get(sym, {})
        cat = fund_row.get("Category", "MIDCAP")
        regime_ctx = {"trend": market_regime, "market_score": 45.0 if market_regime == "BEAR" else 80.0}
        
        score, _, _ = calculate_score(
            category=cat,
            breakout_count=len(signals),
            rsi=rsi_val,
            volume_ratio=vol_ratio,
            breakout_signals=signals,
            ticker=ticker,
            latest=latest,
            symbol=sym,
            timeframe="1d",
            atr_val=atr20,
            nifty_ret=-3.5 if market_regime == "BEAR" else 5.0,
            regime_ctx=regime_ctx
        )

        if score < effective_score_threshold:
            funnel["12_LOW_SCORE"] += 1
            continue

        funnel["12_SCORE_THRESHOLD_PASS"] += 1

        # Gate: SL/RR Engine
        sl_result = compute_sl_and_target(
            entry_price=candle_close,
            atr=atr20,
            candle_range=candle_range,
            mode="EOD",
            adx=latest.get("ADX"),
            rsi=rsi_val,
            macd_hist=latest.get("MACD_HIST"),
            atr_pct=latest.get("ATR_PCT"),
            swing_low=latest.get("SWING_LOW"),
            swing_high=latest.get("SWING_HIGH"),
            bb_upper=latest.get("BB_UPPER"),
            bb_lower=latest.get("BB_LOWER"),
            bb_mid=latest.get("BB_MID"),
            s1=latest.get("S1"),
            s2=latest.get("S2"),
            r1=latest.get("R1"),
            r2=latest.get("R2"),
            swing_low_raw=latest.get("SWING_LOW_RAW"),
            swing_high_raw=latest.get("SWING_HIGH_RAW"),
            candle_low=candle_low,
            vwap=latest.get("VWAP"),
            ticker=ticker
        )

        if sl_result.get("is_rejected"):
            funnel["13_RISK_REJECTED"] += 1
            continue

        natural_rr = sl_result.get("natural_rr", 0.0)
        if natural_rr < min_rr_threshold:
            funnel["13_LOW_NATURAL_RR"] += 1
            continue

        funnel["14_FINAL_ALERT_QUALIFIED"] += 1
        qualified_candidates.append({
            "symbol": sym,
            "close": candle_close,
            "prior_20d_high": prior_20d_high,
            "vol_ratio": vol_ratio,
            "score": score,
            "natural_rr": natural_rr,
            "sl": sl_result.get("stop_loss"),
            "target": sl_result.get("target_1")
        })

    print("\n" + "=" * 80)
    print("📊 EOD SCANNER ATTRITION FUNNEL BREAKDOWN:")
    print("=" * 80)
    for k in sorted(funnel.keys()):
        print(f"  • {k:<32}: {funnel[k]:>5}")
    print("=" * 80)

    if disq_reasons:
        print("\n🚫 SCORING ENGINE DISQUALIFIER BREAKDOWN:")
        for r, c in disq_reasons.items():
            print(f"  • {r:<35}: {c}")

    print(f"\n🏆 FINAL QUALIFIED CANDIDATES: {len(qualified_candidates)}")
    for cand in qualified_candidates:
        print(f"  • 🟢 {cand['symbol']:<12} Close: ₹{cand['close']:.2f} (20D High: ₹{cand['prior_20d_high']:.2f}) | Vol: {cand['vol_ratio']:.2f}x | Score: {cand['score']} | R:R: {cand['natural_rr']:.2f}R | SL: ₹{cand['sl']} | Target: ₹{cand['target']}")

    return funnel, qualified_candidates


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="2026-08-25")
    parser.add_argument("--regime", default="BEAR")
    args = parser.parse_args()
    run_eod_simulation(args.date, args.regime)
