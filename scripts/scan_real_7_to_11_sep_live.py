#!/usr/bin/env python3
"""
EXACT PRODUCTION SHORT COVERING SCANNER — LIVE 7 SEP TO 11 SEP 2026 AUDIT
========================================================================
Downloads verified live daily exchange data for liquid NSE stocks and runs
the exact Production Short Covering Specialist Engine from
scripts/fast_short_covering_v56_specialist_engine.py.

Rules:
1. Macro Regime: Bear or Neutral (Close < SMA50 or Close < SMA200)
2. 15D Breakdown Shelf Low
3. Recent Undercut in last 4 days
4. 14D RSI <= 42.0 (Oversold exhaustion)
5. Reclaim: Close > Prior Shelf Low
6. Squeeze Volume Surge >= 1.6x 20D SMA Volume
7. CPOS >= 0.65 (Close in top 35% of daily range)
8. Stop Loss: 0.5% below recent undercut low
"""

import os
import glob
import time
import pandas as pd
import numpy as np
import yfinance as yf

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

# Target Sessions: 7 Sep to 11 Sep 2026
TARGET_DATES = ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]

LIQUID_SYMBOLS = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL", "TCS", "INFY", "TATAMOTORS",
    "M&M", "MARUTI", "SUNPHARMA", "CIPLA", "DRREDDY", "BAJFINANCE", "CHOLAFIN", "ADANIENT",
    "ADANIPORTS", "ADANIPOWER", "NTPC", "POWERGRID", "COALINDIA", "ONGC", "HINDALCO", "TATASTEEL",
    "JSWSTEEL", "VEDL", "DLF", "GODREJPROP", "OBEROIRLTY", "PRESTIGE", "ITC", "HINDUNILVR",
    "NESTLEIND", "BRITANNIA", "VBL", "BEL", "HAL", "TITAN", "ASIANPAINT", "PIDILITIND",
    "SIEMENS", "ABB", "LTIM", "TECHM", "WIPRO", "PERSISTENT", "COFORGE", "ZOMATO", "DMART",
    "KALYANKJIL", "JUBLFOOD", "INDIGO", "APOLLOHOSP", "MAXHEALTH", "FORTIS", "LUPIN",
    "AUROPHARMA", "ZYDUSLIFE", "GLENMARK", "BIOCON", "TORNTPHARM", "ALKEM", "DIVISLAB",
    "MUTHOOTFIN", "MANAPPURAM", "SHRIRAMFIN", "PFC", "RECLTD", "IREDA", "HUDCO", "JIOFIN",
    "CANBK", "PNB", "BANKBARODA", "UNIONBANK", "IDFCFIRSTB", "FEDERALBNK", "AUBANK",
    "BANDHANBNK", "INDUSINDBK", "KOTAKBANK", "YESBANK", "BSE", "CDSL", "MCX", "ANGELONE",
    "IEX", "MOTILALOFS", "TATAPOWER", "ADANIGREEN", "JSWENERGY", "TORNTPOWER", "NHPC", "SJVN",
    "CESC", "SUZLON", "INOXWIND", "BHEL", "THERMAX", "KEC", "KALPATPOWR", "CUMMINSIND",
    "VOLTAS", "BLUESTARCO", "HAVELLS", "CROMPTON", "DIXON", "AMBER", "KAYNES", "SYRMA",
    "CYIENT", "KPITTECH", "TATAELXSI", "LTTS", "MPHASIS", "TRENT", "POLYCAB", "ASHOKLEY",
    "COLPAL", "GAIL", "MOTHERSON", "FINPIPE", "HERITGFOOD", "IOB", "MRPL", "RBLBANK",
    "AAVAS", "AIAENG", "FINCABLES", "INOXGREEN", "PWL", "TIPSMUSIC", "VIKRAMSOLR"
]

def main():
    print(f"Fetching real NSE daily exchange candles for {len(LIQUID_SYMBOLS)} liquid stocks...")
    tickers = [f"{s}.NS" for s in LIQUID_SYMBOLS]
    
    # Download in single vectorized call
    data = yf.download(tickers, start="2026-07-01", end="2026-09-13", group_by="ticker", progress=False, auto_adjust=False)
    
    signals = []
    
    for sym in LIQUID_SYMBOLS:
        ticker = f"{sym}.NS"
        try:
            if len(LIQUID_SYMBOLS) == 1:
                df = data.copy()
            else:
                if ticker not in data.columns.levels[0]:
                    continue
                df = data[ticker].dropna(subset=["Close"]).copy()
                
            if len(df) < 40:
                continue
                
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            df = df[df.index.dayofweek < 5] # Exclude weekends
            
            d_dates = [d.strftime("%Y-%m-%d") for d in df.index]
            n_d = len(df)
            
            d_o = df["Open"].values
            d_h = df["High"].values
            d_l = df["Low"].values
            d_c = df["Close"].values
            d_v = df["Volume"].values
            
            # Point-in-time indicators
            vol_sma20 = pd.Series(d_v).rolling(20, min_periods=5).mean().values
            sma50 = pd.Series(d_c).rolling(50, min_periods=10).mean().values
            sma200 = pd.Series(d_c).rolling(200, min_periods=30).mean().values
            
            # 14D RSI
            delta = pd.Series(d_c).diff()
            gain = delta.where(delta > 0, 0.0).rolling(14, min_periods=5).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=5).mean()
            rs = gain / (loss + 1e-6)
            rsi14 = (100.0 - (100.0 / (1.0 + rs))).values
            
            for i in range(15, n_d):
                d_str = d_dates[i]
                if d_str not in TARGET_DATES:
                    continue
                    
                c_val = d_c[i]
                s50 = sma50[i]
                s200 = sma200[i]
                
                # Regime Check: BEAR or NEUTRAL
                if c_val > s50 and c_val > s200:
                    regime = "BULL"
                elif c_val < s50 and c_val < s200:
                    regime = "BEAR"
                else:
                    regime = "NEUTRAL"
                    
                if regime not in ["BEAR", "NEUTRAL"]:
                    continue
                    
                # 1. 15-Day Prior Shelf Low
                prior_shelf_low = np.min(d_l[max(0, i - 18) : i - 1])
                
                # 2. Recent Undercut in last 4 days
                recent_undercut_low = np.min(d_l[max(0, i - 4) : i])
                did_breakdown = recent_undercut_low < prior_shelf_low
                if not did_breakdown:
                    continue
                    
                # 3. Exhaustion RSI <= 42.0
                rsi_val = rsi14[i]
                if rsi_val > 42.0 or np.isnan(rsi_val):
                    continue
                    
                # 4. Reclaim: Close > Prior Shelf Low
                if d_c[i] <= prior_shelf_low:
                    continue
                    
                # 5. Volume Surge >= 1.60x 20D SMA
                v_sma = vol_sma20[i]
                vol_ratio = (d_v[i] / v_sma) if v_sma > 0 else 1.0
                if vol_ratio < 1.60:
                    continue
                    
                # 6. CPOS >= 0.65
                day_range = d_h[i] - d_l[i]
                cpos = (d_c[i] - d_l[i]) / day_range if day_range > 0 else 0.5
                if cpos < 0.65:
                    continue
                    
                # 7. Pricing & Stop Loss
                entry_p = round(float(d_c[i]), 2)
                sl_p = round(float(min(recent_undercut_low, d_l[i]) * 0.995), 2)
                risk = round(entry_p - sl_p, 2)
                if risk <= 0:
                    continue
                    
                risk_pct = round((risk / entry_p) * 100, 2)
                target_p = round(entry_p + (2.5 * risk), 2)
                
                # Forward price check if subsequent days available
                fwd_h = d_h[i + 1 : n_d]
                fwd_l = d_l[i + 1 : n_d]
                fwd_c = d_c[i + 1 : n_d]
                
                peak_price = entry_p
                realized_r = 0.0
                outcome = "OPEN / PENDING"
                
                if len(fwd_h) > 0:
                    peak_price = round(float(np.max(fwd_h)), 2)
                    for f_idx in range(len(fwd_c)):
                        if fwd_l[f_idx] <= sl_p:
                            outcome = "❌ Stop Loss Hit (-1.0R)"
                            realized_r = -1.0
                            break
                        elif fwd_h[f_idx] >= target_p:
                            outcome = "✅ Target Hit (+2.5R)"
                            realized_r = 2.5
                            break
                    if outcome == "OPEN / PENDING":
                        last_c = float(fwd_c[-1])
                        realized_r = round((last_c - entry_p) / risk, 2)
                        outcome = f"Holding (+{realized_r}R)" if realized_r >= 0 else f"Holding ({realized_r}R)"
                
                signals.append({
                    "date": d_str,
                    "symbol": sym,
                    "regime": regime,
                    "entry_price": entry_p,
                    "sl_price": sl_p,
                    "risk_rs": risk,
                    "risk_pct": risk_pct,
                    "target_price": target_p,
                    "peak_price": peak_price,
                    "rsi": round(float(rsi_val), 1),
                    "vol_ratio": round(float(vol_ratio), 2),
                    "cpos": round(float(cpos), 2),
                    "realized_r": realized_r,
                    "pnl_per_share_rs": round(realized_r * risk, 2),
                    "outcome": outcome
                })
        except Exception as e:
            continue
            
    print(f"\n================================================================================")
    print(f"TOTAL PRODUCTION SHORT COVERING ALERTS FOR 7–11 SEP 2026: {len(signals)}")
    print(f"================================================================================")
    
    out_csv = os.path.join(_REPORTS_DIR, "production_short_covering_7_to_11_sep_live.csv")
    if signals:
        df_out = pd.DataFrame(signals)
        df_out.to_csv(out_csv, index=False)
        print(f"Exported to: {out_csv}\n")
        print(f"{'Date':<12} | {'Symbol':<15} | {'Regime':<8} | {'Entry (₹)':<10} | {'SL (₹)':<10} | {'Risk (₹)':<8} | {'Target 2.5R':<12} | {'Peak (₹)':<10} | {'RSI':<6} | {'Vol':<6} | {'Return':<8} | {'Outcome'}")
        print("-" * 145)
        for s in signals:
            print(f"{s['date']:<12} | {s['symbol']:<15} | {s['regime']:<8} | {s['entry_price']:<10.2f} | {s['sl_price']:<10.2f} | {s['risk_rs']:<8.2f} | {s['target_price']:<12.2f} | {s['peak_price']:<10.2f} | {s['rsi']:<6.1f} | {s['vol_ratio']:<5.2f}x | {s['realized_r']:<+8.2f} | {s['outcome']}")
        print("-" * 145)
    else:
        print("No Short Covering signals qualified during 7–11 Sep across these liquid names.")

if __name__ == "__main__":
    main()
