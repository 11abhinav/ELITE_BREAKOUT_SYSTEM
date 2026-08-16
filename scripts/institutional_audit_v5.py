import sys, os, json, time, traceback, re
import concurrent.futures
from collections import defaultdict
from datetime import datetime, timedelta
import pandas as pd

import logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

import config
import watchlist_cache
import price_cache
import fundamentals_cache
import database
import eod_scanner
import reversal_scanner
import multi_tf_scanner
import multibagger
import technical_indicators

import yfinance as yf

# =========================================================
# PHASE 1: Snapshot & Acquisition (Parallel Fetch + Freeze)
# =========================================================
SNAPSHOT = {
    "1d": {},
    "15m": {},
    "65m": {},
    "1wk": {},
    "fundamentals": {}
}

def mock_fetch_unified_historical(symbols, period="1y", interval="1d", requester=None):
    if interval == "1wk":
        return {sym: SNAPSHOT["1wk"].get(sym, pd.DataFrame()) for sym in symbols}
    return {sym: SNAPSHOT["1d"].get(sym, pd.DataFrame()) for sym in symbols}

def mock_get_intraday_snapshot(symbols, interval="5m", period="5d", wait_timeout=30, requester=None, cadence_override=None):
    if interval == "15m":
        return {sym: SNAPSHOT["15m"].get(sym, pd.DataFrame()) for sym in symbols}
    elif interval in ["65m", "60m"]:
        return {sym: SNAPSHOT["65m"].get(sym, pd.DataFrame()) for sym in symbols}
    return {sym: pd.DataFrame() for sym in symbols}

def mock_get_fundamentals(symbol):
    return SNAPSHOT["fundamentals"].get(symbol, {})

# Inject Mocks
price_cache.fetch_unified_historical = mock_fetch_unified_historical
price_cache.get_intraday_snapshot = mock_get_intraday_snapshot
fundamentals_cache.get_fundamentals = mock_get_fundamentals
database.init_db = lambda: None
database.is_fno = lambda sym: True
database.get_bhavcopy_cache = lambda date: {}
database.get_latest_bhavcopy_cache_with_date = lambda: ({}, None)

def _download_yfinance(symbols, interval, period):
    tickers = " ".join([f"{s}.NS" for s in symbols])
    try:
        data = yf.download(tickers, period=period, interval=interval, threads=True, progress=False)
        result = {}
        if len(symbols) == 1:
            if not data.empty:
                df = data.copy()
                df.reset_index(inplace=True)
                if "Date" in df.columns: df.rename(columns={"Date": "Datetime"}, inplace=True)
                if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
                result[symbols[0]] = df
            return result
            
        if isinstance(data.columns, pd.MultiIndex):
            for sym in symbols:
                if f"{sym}.NS" in data.columns.levels[1]:
                    try:
                        sym_data = data.xs(f"{sym}.NS", level=1, axis=1)
                        if not sym_data.empty:
                            df = sym_data.copy()
                            df.reset_index(inplace=True)
                            if "Date" in df.columns: df.rename(columns={"Date": "Datetime"}, inplace=True)
                            df.dropna(subset=["Close"], inplace=True)
                            result[sym] = df
                    except Exception:
                        pass
        return result
    except Exception as e:
        print(f"Error downloading {interval}: {e}")
        return {}

def fetch_watchlist_safe():
    try:
        import os
        path = os.path.join(os.path.dirname(__file__), '..', 'data', 'nifty500_watchlist.parquet')
        if os.path.exists(path):
            df = pd.read_parquet(path)
            if "Symbol" in df.columns: return df["Symbol"].tolist()
            return df.index.tolist()
        return ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN", "BHARTIARTL", "LT", "BAJFINANCE"]
    except Exception as e:
        print(f"Watchlist fetch failed, using fallback: {e}")
        return ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN", "BHARTIARTL", "LT", "BAJFINANCE"]

def build_frozen_snapshot(symbols):
    print(f"[*] Building Frozen Snapshot for {len(symbols)} symbols...")
    
    t0 = time.time()
    
    SNAPSHOT["1d"] = _download_yfinance(symbols, "1d", "1y")
    print("  -> 1D fetched")
    SNAPSHOT["1wk"] = _download_yfinance(symbols, "1wk", "2y")
    print("  -> 1wk fetched")
    SNAPSHOT["15m"] = _download_yfinance(symbols, "15m", "60d")
    print("  -> 15m fetched")
    SNAPSHOT["65m"] = _download_yfinance(symbols, "60m", "730d")
    print("  -> 65m fetched")

    for sym in symbols:
        df_1d = SNAPSHOT["1d"].get(sym)
        high_52w = 0
        if df_1d is not None and not df_1d.empty:
            high_52w = float(df_1d["High"].max())
            
        SNAPSHOT["fundamentals"][sym] = {
            "Category": "LARGE",
            "52W High": high_52w,
            "Market Cap(Cr)": 100000
        }
    
    print(f"[*] Snapshot built in {time.time()-t0:.2f}s")

# =========================================================
# PHASE 2: Evaluation (Sequential) & Near Miss Analysis
# =========================================================

# Hook original functions
orig_check_eod = eod_scanner._check_eod_conditions
_last_eod_res = {}
def hook_check_eod(*args, **kwargs):
    global _last_eod_res
    _last_eod_res = orig_check_eod(*args, **kwargs)
    return _last_eod_res
eod_scanner._check_eod_conditions = hook_check_eod

def get_near_misses(sym, scanner, df, regime_ctx, fund):
    misses = []
    regime_str = regime_ctx.get("trend", "NEUTRAL")
    
    if df is None or df.empty or len(df) < 50:
        return misses
        
    latest = df.iloc[-1]
    
    if scanner == "EOD":
        req_vol = config.MIN_VOL_RATIO.get(regime_str, 1.5)
        avg_vol = df["Volume"].iloc[-21:-1].mean()
        actual_vol = latest.get("Volume", 0) / avg_vol if avg_vol > 0 else 0
        
        misses.append({
            "constraint": "Volume",
            "required": req_vol,
            "actual": actual_vol,
            "distance_pct": ((actual_vol - req_vol) / req_vol) * 100 if req_vol > 0 else 0
        })
        
        req_rsi = config.MIN_RSI_BULL
        actual_rsi = latest.get("RSI", 0)
        misses.append({
            "constraint": "RSI_Min",
            "required": req_rsi,
            "actual": actual_rsi,
            "distance_pct": ((actual_rsi - req_rsi) / req_rsi) * 100 if req_rsi > 0 else 0
        })
        
        actual_close = latest.get("Close", 0)
        req_struct = df["High"].iloc[-21:-1].max()
        misses.append({
            "constraint": "Structural_High",
            "required": req_struct,
            "actual": actual_close,
            "distance_pct": ((actual_close - req_struct) / req_struct) * 100 if req_struct > 0 else 0
        })
        
    elif scanner == "REVERSAL":
        drop_range = config.PULLBACK_DEPTH_BANDS.get(regime_str, (0.15, 0.55))
        req_drop_min = drop_range[0] * 100
        
        actual_close = latest.get("Close", 0)
        high_52w = fund.get("52W High", 0)
        if high_52w == 0: high_52w = df["High"].rolling(252).max().iloc[-1] if len(df) >= 252 else df["High"].max()
        
        actual_drop = ((high_52w - actual_close) / high_52w * 100) if high_52w > 0 else 0
        misses.append({
            "constraint": "Pullback_Depth",
            "required": req_drop_min,
            "actual": actual_drop,
            "distance_pct": ((actual_drop - req_drop_min) / req_drop_min) * 100 if req_drop_min > 0 else 0
        })

    return misses

def categorize_reason(reason):
    if not reason: return "OTHER"
    r = reason.lower()
    if "volume" in r: return "VOLUME"
    if "rsi" in r: return "RSI"
    if "prior" in r or "high" in r and "<=" in r: return "STRUCTURE"
    if "ema" in r or "sma" in r or "adx" in r: return "TREND"
    if "zero" in r or "missing" in r or "insufficient" in r or "fundamentals" in r: return "DATA"
    if "drop from" in r or "depth" in r: return "STRUCTURE"
    return "OTHER"

def run_scanner(name, eval_func, symbols, regime_ctx):
    print(f"Running {name}...")
    
    results = {
        "processed": 0,
        "gates": defaultdict(int),
        "traces": [],
        "alerts_reachability": 0,
        "exceptions": []
    }
    
    for sym in symbols:
        try:
            df = SNAPSHOT["1d"].get(sym)
            if df is None or df.empty:
                results["gates"]["DATA"] += 1
                continue
                
            try:
                fund = SNAPSHOT["fundamentals"].get(sym, {})
                df_ind = technical_indicators.apply_indicators(df.copy(), "1d")
            except Exception as ind_e:
                results["exceptions"].append({
                    "symbol": sym,
                    "scanner_stage": "Indicator Prep",
                    "exception_type": type(ind_e).__name__,
                    "stack_trace": traceback.format_exc(),
                    "recovered": False,
                    "continued": False
                })
                continue

            
            global _last_eod_res, _last_rev_res
            _last_eod_res = {}
            _last_rev_res = {}
            
            # For MTF, we pass True for allow_live_fetch so it hits our mock
            if name == "MULTI_TF":
                out = eval_func(sym, df_ind, regime_ctx, pre_fetched_h1_df=None, allow_live_fetch=True)
            elif name == "MULTIBAGGER":
                out = eval_func(sym, df_ind, fund, regime_ctx, allow_live_fetch=True)
            else:
                out = eval_func(sym, df_ind, fund, regime_ctx)
                
            results["processed"] += 1
            
            passed = out.get("qualified", False) or out.get("status") == "YES"
            if passed:
                results["alerts_reachability"] += 1
                reason = "PASS"
                gate = "PASS"
            else:
                reason = "Unknown"
                if name == "EOD" and _last_eod_res.get("reason"): reason = _last_eod_res["reason"]
                elif name == "REVERSAL" and _last_rev_res.get("reason"): reason = _last_rev_res["reason"]
                elif "reasons" in out and out["reasons"]: reason = out["reasons"][0]
                elif "reason" in out: reason = out["reason"]
                
                gate = categorize_reason(reason)
                results["gates"][gate] += 1
            
            misses = get_near_misses(sym, name, df_ind, regime_ctx, fund)
            
            results["traces"].append({
                "symbol": sym,
                "decision": "PASSED" if passed else "REJECTED",
                "gate": gate,
                "reason": reason,
                "near_misses": misses,
                "score": out.get("score", 0),
                "1d_pct": df_ind["Close"].pct_change().iloc[-1] * 100 if len(df_ind) > 1 else 0
            })
            
            
        except Exception as e:
            results["exceptions"].append({
                "symbol": sym,
                "scanner_stage": "Evaluation",
                "exception_type": type(e).__name__,
                "stack_trace": traceback.format_exc(),
                "recovered": False,
                "continued": False
            })
            
    return results

# =========================================================
# PHASE 3 & 4: Aggregation, Markdown, JSON
# =========================================================

def prove_core_score_floor(regime):
    # Bull regime Core Score Max logic based on reversal_scanner.py
    # Trend alignment (10), Volatility Setup (4), Struct Setup (4), Support Prox (4), RSI Trough (4) = 26
    return {
        "max_attainable": 26,
        "floor": reversal_scanner.CORE_SCORE_FLOOR,
        "reachable": 26 >= reversal_scanner.CORE_SCORE_FLOOR
    }

def main():
    symbols = fetch_watchlist_safe()
    # To prevent massive timeouts on local run, slice to 200
    if len(symbols) > 200: symbols = symbols[:200]
    
    build_frozen_snapshot(symbols)
    
    regime_ctx = {"trend": "BULLISH", "biases": {}}
    
    metrics = {}
    metrics["EOD"] = run_scanner("EOD", eod_scanner.evaluate_eod_symbol, symbols, regime_ctx)
    metrics["REVERSAL"] = run_scanner("REVERSAL", reversal_scanner.evaluate_reversal_symbol, symbols, regime_ctx)
    metrics["MULTI_TF"] = run_scanner("MULTI_TF", multi_tf_scanner.evaluate_multi_tf_symbol, symbols, regime_ctx)
    metrics["MULTIBAGGER"] = run_scanner("MULTIBAGGER", multibagger.evaluate_multibagger_symbol, symbols, regime_ctx)
    
    # False Negative Analysis (Top 25)
    eod_traces = metrics["EOD"]["traces"]
    sorted_traces = sorted(eod_traces, key=lambda x: x["1d_pct"], reverse=True)
    top_25 = sorted_traces[:25]
    
    # Write JSON
    output_json = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "universe_size": len(symbols),
            "regime": "BULLISH"
        },
        "scanners": metrics,
        "core_score_proof": prove_core_score_floor(regime_ctx),
        "false_negatives": top_25
    }
    
    with open("/tmp/institutional_audit_v5.json", "w") as f:
        json.dump(output_json, f, indent=2)
        
    print("Done. Generated /tmp/institutional_audit_v5.json")

if __name__ == "__main__":
    main()
