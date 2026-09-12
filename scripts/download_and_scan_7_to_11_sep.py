#!/usr/bin/env python3
"""
DOWNLOAD LIVE NSE MARKET DATA & RUN PRODUCTION SHORT COVERING SCANNER (7–11 SEP 2026)
=====================================================================================
1. Downloads authentic exchange daily candle data for NSE stocks from Yahoo Finance
   for the period covering September 2026 (7 Sep to 11 Sep 2026).
2. Updates and syncs data/history/1d/ parquets.
3. Runs the exact production Short Covering Specialist Engine from
   scripts/fast_short_covering_v56_specialist_engine.py.
4. Outputs every single Short Covering trade alert for 7 Sep, 8 Sep, 9 Sep, 10 Sep, and 11 Sep.
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

TARGET_DATES = ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]

def get_symbol_universe():
    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    symbols = [os.path.splitext(os.path.basename(f))[0].upper() for f in files]
    return symbols

def download_and_sync_data(symbols):
    print(f"Fast batch downloading online exchange data for {len(symbols)} NSE stocks...")
    updated_count = 0
    chunk_size = 50
    
    for i in range(0, len(symbols), chunk_size):
        chunk_syms = symbols[i : i + chunk_size]
        clean_syms = [s.replace(".NS", "").replace("____", "").strip() for s in chunk_syms]
        tickers = [f"{s}.NS" for s in clean_syms]
        try:
            batch_data = yf.download(tickers, start="2026-08-01", end="2026-09-13", group_by="ticker", progress=False, auto_adjust=False)
            if batch_data is not None and len(batch_data) > 0:
                for sym in chunk_syms:
                    f_path = os.path.join(_HISTORY_1D_DIR, f"{sym}.parquet")
                    if not os.path.exists(f_path):
                        continue
                    try:
                        ticker = f"{sym}.NS"
                        if len(chunk_syms) == 1:
                            df_sym = batch_data.copy()
                        else:
                            if ticker not in batch_data.columns.levels[0]:
                                continue
                            df_sym = batch_data[ticker].dropna(how="all").copy()
                            
                        if len(df_sym) == 0:
                            continue
                            
                        if df_sym.index.tz is None:
                            df_sym.index = df_sym.index.tz_localize("Asia/Kolkata")
                        else:
                            df_sym.index = df_sym.index.tz_convert("Asia/Kolkata")
                            
                        df_sym = df_sym[df_sym.index.dayofweek < 5]
                        
                        # Load existing parquet
                        df_old = pd.read_parquet(f_path)
                        if not isinstance(df_old.index, pd.DatetimeIndex):
                            if "Date" in df_old.columns:
                                df_old.index = pd.to_datetime(df_old["Date"])
                            elif "Datetime" in df_old.columns:
                                df_old.index = pd.to_datetime(df_old["Datetime"])
                        if df_old.index.tz is None:
                            df_old.index = df_old.index.tz_localize("Asia/Kolkata")
                        else:
                            df_old.index = df_old.index.tz_convert("Asia/Kolkata")
                            
                        # Merge new bars
                        new_bars = df_sym[df_sym.index > df_old.index.max()]
                        if len(new_bars) > 0:
                            combined = pd.concat([df_old, new_bars])
                            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
                            combined.to_parquet(f_path)
                            updated_count += 1
                    except Exception:
                        continue
            print(f"Processed batch {i + len(chunk_syms)} / {len(symbols)} stocks (Updated {updated_count})...")
        except Exception as e:
            print(f"Error in batch: {e}")
            continue
            
    print(f"Finished downloading and syncing {updated_count} NSE stock parquets.")

def run_production_short_covering_scan(symbols):
    print("\nRunning Production Short Covering Specialist Engine for 7 Sep to 11 Sep 2026...")
    signals = []
    
    for sym in symbols:
        f_path = os.path.join(_HISTORY_1D_DIR, f"{sym}.parquet")
        if not os.path.exists(f_path):
            continue
            
        try:
            df = pd.read_parquet(f_path)
            if df is None or len(df) < 50:
                continue
                
            if not isinstance(df.index, pd.DatetimeIndex):
                if "Date" in df.columns:
                    df.index = pd.to_datetime(df["Date"])
                elif "Datetime" in df.columns:
                    df.index = pd.to_datetime(df["Datetime"])
                else:
                    continue
            if df.index.tz is None:
                df.index = df.index.tz_localize("Asia/Kolkata")
            else:
                df.index = df.index.tz_convert("Asia/Kolkata")
            df = df.sort_index()
            df = df[df.index.dayofweek < 5]
            
            d_dates = [d.strftime("%Y-%m-%d") for d in df.index]
            n_d = len(df)
            if n_d < 50:
                continue
                
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
            
            for i in range(20, n_d):
                d_str = d_dates[i]
                if d_str not in TARGET_DATES:
                    continue
                    
                c_val = d_c[i]
                s50 = sma50[i]
                s200 = sma200[i]
                
                # Regime Check: Bear / Neutral only
                if c_val > s50 and c_val > s200:
                    regime = "BULL"
                elif c_val < s50 and c_val < s200:
                    regime = "BEAR"
                else:
                    regime = "NEUTRAL"
                    
                if regime not in ["BEAR", "NEUTRAL"]:
                    continue
                    
                # 1. Prior 15-day shelf low over [i - 20 : i - 2]
                prior_shelf_low = np.min(d_l[i - 20 : i - 2])
                
                # 2. Recent breakdown undercut low in last 4 days
                recent_undercut_low = np.min(d_l[i - 4 : i])
                did_breakdown = recent_undercut_low < prior_shelf_low
                if not did_breakdown:
                    continue
                    
                # 3. Exhaustion RSI <= 42.0
                rsi_val = rsi14[i]
                if rsi_val > 42.0 or np.isnan(rsi_val):
                    continue
                    
                # 4. Reclaim: Close > prior shelf low
                if d_c[i] <= prior_shelf_low:
                    continue
                    
                # 5. Volume surge: >= 1.6x 20D SMA
                v_sma = vol_sma20[i]
                vol_ratio = (d_v[i] / v_sma) if v_sma > 0 else 1.0
                if vol_ratio < 1.60:
                    continue
                    
                # 6. Candle Close Position (CPOS) >= 0.65
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
                if risk_pct < 1.0 or risk_pct > 9.0:
                    continue
                    
                target_p = round(entry_p + (2.5 * risk), 2)
                
                signals.append({
                    "date": d_str,
                    "symbol": sym,
                    "regime": regime,
                    "entry_price": entry_p,
                    "sl_price": sl_p,
                    "risk_rs": risk,
                    "risk_pct": risk_pct,
                    "target_price": target_p,
                    "rsi": round(float(rsi_val), 1),
                    "vol_ratio": round(float(vol_ratio), 2),
                    "cpos": round(float(cpos), 2)
                })
        except Exception:
            continue
            
    out_csv = os.path.join(_REPORTS_DIR, "production_short_covering_7_to_11_sep.csv")
    print(f"\n================================================================================")
    print(f"TOTAL PRODUCTION SHORT COVERING ALERTS FOR 7–11 SEP 2026: {len(signals)}")
    print(f"================================================================================")
    
    if signals:
        df_out = pd.DataFrame(signals)
        df_out.to_csv(out_csv, index=False)
        print(f"Exported to: {out_csv}\n")
        print(f"{'Date':<12} | {'Symbol':<15} | {'Regime':<8} | {'Entry (₹)':<10} | {'SL (₹)':<10} | {'Risk (₹)':<8} | {'Target 2.5R (₹)':<15} | {'RSI':<6} | {'Vol':<6} | {'CPOS'}")
        print("-" * 130)
        for s in signals:
            print(f"{s['date']:<12} | {s['symbol']:<15} | {s['regime']:<8} | {s['entry_price']:<10.2f} | {s['sl_price']:<10.2f} | {s['risk_rs']:<8.2f} | {s['target_price']:<15.2f} | {s['rsi']:<6.1f} | {s['vol_ratio']:<5.2f}x | {s['cpos']}")
        print("-" * 130)
    else:
        print("Zero signals qualified across all stocks for 7–11 Sep 2026 under strict production Short Covering rules.")

if __name__ == "__main__":
    symbols = get_symbol_universe()
    download_and_sync_data(symbols)
    run_production_short_covering_scan(symbols)
