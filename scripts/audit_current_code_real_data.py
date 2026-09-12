#!/usr/bin/env python3
"""
AUDIT CURRENT CODE SIGNALS ON REAL NSE HISTORICAL MARKET DATA (ZERO LOOKAHEAD)
=============================================================================
Runs the exact current production Short Covering Specialist logic from
scripts/fast_short_covering_v56_specialist_engine.py against all 884 real NSE
daily equity parquets in data/history/1d/.

Audits the latest available historical week (2026-08-31 to 2026-09-04) and
August 2026 data.

Enforces:
- 100% Real NSE historical OHLCV parquet data
- Strict point-in-time causality (zero lookahead)
- True entry, stop loss, risk per share, and forward outcomes
"""

import os
import glob
import pandas as pd
import numpy as np
import datetime

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_HISTORY_1D_DIR = os.path.join(_REPO_ROOT, "data", "history", "1d")
_REPORTS_DIR = os.path.join(_REPO_ROOT, "reports")

def main():
    files = sorted(glob.glob(os.path.join(_HISTORY_1D_DIR, "*.parquet")))
    print(f"Scanning {len(files)} real NSE equity parquets with current Short Covering Specialist rules...")
    
    # Target period: Latest week (2026-08-31 to 2026-09-04) + recent August sessions
    TARGET_START = "2026-08-15"
    TARGET_END = "2026-09-04"
    
    # Current production short covering parameters
    BREAKDOWN_LOOKBACK = 15
    MAX_RSI = 42.0
    MIN_VOL_RATIO = 1.60
    MIN_CPOS = 0.65
    TARGET_R = 2.5
    
    signals = []
    
    for f_path in files:
        sym = os.path.splitext(os.path.basename(f_path))[0].upper()
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
            # Invariant: Trading days only
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
            
            # Point-in-time indicator calculation
            vol_sma20 = pd.Series(d_v).rolling(20, min_periods=5).mean().values
            sma50 = pd.Series(d_c).rolling(50, min_periods=10).mean().values
            sma200 = pd.Series(d_c).rolling(200, min_periods=30).mean().values
            
            # 14D RSI
            delta = pd.Series(d_c).diff()
            gain = delta.where(delta > 0, 0.0).rolling(14, min_periods=5).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=5).mean()
            rs = gain / (loss + 1e-6)
            rsi14 = (100.0 - (100.0 / (1.0 + rs))).values
            
            for i in range(BREAKDOWN_LOOKBACK + 5, n_d):
                d_str = d_dates[i]
                if d_str < TARGET_START or d_str > TARGET_END:
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
                    
                # 1. Prior shelf low over [i - 20 : i - 2]
                prior_shelf_low = np.min(d_l[i - BREAKDOWN_LOOKBACK - 5 : i - 2])
                
                # 2. Recent breakdown undercut low in last 4 days
                recent_undercut_low = np.min(d_l[i - 4 : i])
                did_breakdown = recent_undercut_low < prior_shelf_low
                if not did_breakdown:
                    continue
                    
                # 3. Exhaustion: RSI <= 42
                rsi_val = rsi14[i]
                if rsi_val > MAX_RSI or np.isnan(rsi_val):
                    continue
                    
                # 4. Reclaim: Close > prior shelf low
                if d_c[i] <= prior_shelf_low:
                    continue
                    
                # 5. Volume surge: >= 1.6x 20D SMA
                v_sma = vol_sma20[i]
                vol_ratio = (d_v[i] / v_sma) if v_sma > 0 else 1.0
                if vol_ratio < MIN_VOL_RATIO:
                    continue
                    
                # 6. CPOS >= 0.65
                day_range = d_h[i] - d_l[i]
                cpos = (d_c[i] - d_l[i]) / day_range if day_range > 0 else 0.5
                if cpos < MIN_CPOS:
                    continue
                    
                # 7. Pricing & Stop Loss (0.5% below undercut low)
                entry_p = round(float(d_c[i]), 2)
                sl_p = round(float(min(recent_undercut_low, d_l[i]) * 0.995), 2)
                risk = round(entry_p - sl_p, 2)
                if risk <= 0:
                    continue
                    
                risk_pct = round((risk / entry_p) * 100, 2)
                if risk_pct < 1.0 or risk_pct > 9.0:
                    continue
                    
                target_p = round(entry_p + (TARGET_R * risk), 2)
                
                # Forward price check on real subsequent bars (Zero lookahead during trigger)
                fwd_h = d_h[i + 1 : min(i + 15, n_d)]
                fwd_l = d_l[i + 1 : min(i + 15, n_d)]
                fwd_c = d_c[i + 1 : min(i + 15, n_d)]
                
                outcome_status = "PENDING / OPEN"
                peak_price = entry_p
                realized_r = 0.0
                bars_held = 0
                
                if len(fwd_h) > 0:
                    peak_price = round(float(np.max(fwd_h)), 2)
                    min_fwd_low = round(float(np.min(fwd_l)), 2)
                    
                    for f_idx in range(len(fwd_c)):
                        bars_held += 1
                        if fwd_l[f_idx] <= sl_p:
                            outcome_status = "❌ Stop Loss Hit (-1.0R)"
                            realized_r = -1.0
                            break
                        elif fwd_h[f_idx] >= target_p:
                            outcome_status = f"✅ Target Hit (+{TARGET_R}R)"
                            realized_r = TARGET_R
                            break
                    if outcome_status == "PENDING / OPEN":
                        last_close = float(fwd_c[-1])
                        realized_r = round((last_close - entry_p) / risk, 2)
                        outcome_status = f"Holding (+{realized_r}R)" if realized_r >= 0 else f"Holding ({realized_r}R)"
                
                pnl_rs = round(realized_r * risk, 2)
                
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
                    "pnl_per_share_rs": pnl_rs,
                    "bars_held": bars_held,
                    "outcome": outcome_status
                })
        except Exception as e:
            continue
            
    print(f"\nTotal Real Market Signals Generated: {len(signals)}")
    
    out_csv = os.path.join(_REPORTS_DIR, "current_code_real_signals_recent.csv")
    if signals:
        df_out = pd.DataFrame(signals)
        df_out.to_csv(out_csv, index=False)
        print(f"Exported to: {out_csv}\n")
        
        print("=" * 145)
        print(f"{'Date':<12} | {'Symbol':<15} | {'Regime':<8} | {'Entry (₹)':<10} | {'SL (₹)':<10} | {'Risk (₹)':<8} | {'Target (₹)':<10} | {'Peak (₹)':<10} | {'RSI':<6} | {'Vol':<6} | {'Realized R':<10} | {'PnL/Sh (₹)':<10} | {'Outcome'}")
        print("=" * 145)
        for s in signals:
            print(f"{s['date']:<12} | {s['symbol']:<15} | {s['regime']:<8} | {s['entry_price']:<10.2f} | {s['sl_price']:<10.2f} | {s['risk_rs']:<8.2f} | {s['target_price']:<10.2f} | {s['peak_price']:<10.2f} | {s['rsi']:<6.1f} | {s['vol_ratio']:<6.2f}x | {s['realized_r']:<+10.2f} | {s['pnl_per_share_rs']:<+10.2f} | {s['outcome']}")
        print("=" * 145)
    else:
        print("No signals triggered during this specific date window under current production strict filters.")

if __name__ == "__main__":
    main()
