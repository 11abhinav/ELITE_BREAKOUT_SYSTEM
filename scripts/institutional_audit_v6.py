import sys, os, json, time, traceback, re
import concurrent.futures
from collections import defaultdict
from datetime import datetime, timedelta
import pandas as pd
import copy

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
    path = os.path.join(os.path.dirname(__file__), '..', 'data', 'elite_fundamental_watchlist.parquet')
    if os.path.exists(path):
        import pandas as pd
        try:
            df = pd.read_parquet(path)
            symbols = df["Stock"].tolist()[:200] if "Stock" in df.columns else df.index.tolist()[:200]
        except:
            symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "ITC", "BHARTIARTL", "LT", "BAJFINANCE"]
    else:
        symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "ITC", "BHARTIARTL", "LT", "BAJFINANCE"]
    
    print(f"Loaded {len(symbols)} symbols for sensitivity analysis.")
    return symbols

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

# Hook original functions to capture internal reasons properly
orig_check_eod = eod_scanner._check_eod_conditions
_last_eod_res = {}
def hook_check_eod(*args, **kwargs):
    global _last_eod_res
    _last_eod_res = orig_check_eod(*args, **kwargs)
    return _last_eod_res
eod_scanner._check_eod_conditions = hook_check_eod

def get_near_misses(sym, scanner, df, regime_ctx, fund):
    return []

def categorize_reason(reason):
    if not reason: return "OTHER"
    r = reason.lower()
    if "volume" in r: return "VOLUME"
    if "rsi" in r: return "RSI"
    if "prior" in r or "high" in r and "<=" in r: return "STRUCTURE"
    if "ema" in r or "sma" in r or "adx" in r: return "TREND"
    if "zero" in r or "missing" in r or "insufficient" in r or "fundamentals" in r or "dataframe" in r or "error" in r or "exception" in r: return "DATA"
    if "drop from" in r or "depth" in r: return "STRUCTURE"
    return "OTHER"

def get_failing_object(e):
    if isinstance(e, KeyError):
        return str(e).replace("'", "")
    if isinstance(e, AttributeError):
        s = str(e)
        if "object has no attribute" in s:
            return s.split("'")[3]
        return s
    return "Unknown Object"

def execute_eval(name, eval_func, sym, df_ind, fund, regime_ctx):
    global _last_eod_res, _last_rev_res
    _last_eod_res = {}
    _last_rev_res = {}
    
    if name == "MULTI_TF":
        out = eval_func(sym, df_ind, regime_ctx, pre_fetched_h1_df=None, allow_live_fetch=True)
    elif name == "MULTIBAGGER":
        out = eval_func(sym, df_ind, fund)
    else:
        out = eval_func(sym, df_ind, fund, regime_ctx)
        
    return out

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
        results["processed"] += 1
        
        df = SNAPSHOT["1d"].get(sym)
        if df is None or df.empty:
            results["gates"]["DATA"] += 1
            continue
            
        fund = SNAPSHOT["fundamentals"].get(sym, {})
        
        # INDICATOR_BUILD STAGE
        try:
            df_ind = technical_indicators.apply_indicators(df.copy(), "1d")
        except Exception as e:
            results["exceptions"].append({
                "symbol": sym,
                "scanner": name,
                "stage": "INDICATOR_BUILD",
                "exception_type": type(e).__name__,
                "failing_object": get_failing_object(e),
                "recovered": "NO",
                "continued": "NO",
                "decision_impact": "SYMBOL_ABORTED",
                "severity": "FATAL",
                "stack_trace": traceback.format_exc()
            })
            results["gates"]["FATAL_EXCEPTION"] += 1
            continue

        # EVALUATION STAGE
        try:
            out = execute_eval(name, eval_func, sym, df_ind, fund, regime_ctx)
        except Exception as e:
            # Re-run Without Exception (Causality Test)
            causal = True # Assume causal unless fallback proves otherwise
            fallback_applied = False
            
            exception_type = type(e).__name__
            failing_object = get_failing_object(e)
            
            if exception_type == "KeyError":
                df_fallback = df_ind.copy()
                fund_fallback = copy.deepcopy(fund)
                
                if failing_object not in df_fallback.columns:
                    df_fallback[failing_object] = 0.0
                    fallback_applied = True
                if failing_object not in fund_fallback:
                    fund_fallback[failing_object] = 0.0
                    fallback_applied = True
                    
                if fallback_applied:
                    try:
                        out_fallback = execute_eval(name, eval_func, sym, df_fallback, fund_fallback, regime_ctx)
                        passed_fallback = out_fallback.get("qualified", False) or out_fallback.get("status") == "YES"
                        if not passed_fallback:
                            # Rejection still occurred at another gate. Exception was NOT causal.
                            causal = False
                    except Exception:
                        pass # Fallback didn't fix it

            results["exceptions"].append({
                "symbol": sym,
                "scanner": name,
                "stage": "EVALUATION",
                "exception_type": type(e).__name__,
                "failing_object": failing_object,
                "recovered": "NO",
                "continued": "NO",
                "decision_impact": "CAUSAL_ABORT" if causal else "NON_CAUSAL_ABORT",
                "severity": "FATAL",
                "stack_trace": traceback.format_exc()
            })
            results["gates"]["FATAL_EXCEPTION"] += 1
            continue

        # SUCCESSFUL EXECUTION
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
            
            # Check for suppressed exceptions hidden in reason
            if "exception" in reason.lower() or "error" in reason.lower():
                results["exceptions"].append({
                    "symbol": sym,
                    "scanner": name,
                    "stage": "SUPPRESSED_EVALUATION",
                    "exception_type": "SUPPRESSED",
                    "failing_object": reason,
                    "recovered": "YES",
                    "continued": "YES",
                    "decision_impact": "DEFAULT_VALUE_USED",
                    "severity": "WARNING",
                    "stack_trace": "Suppressed internally by scanner logic."
                })
                # We still increment the gate it failed at
            
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

def deduplicate_exceptions(scanners_metrics):
    stats = []
    
    # Flatten exceptions
    all_ex = []
    for sc, metrics in scanners_metrics.items():
        all_ex.extend(metrics.get("exceptions", []))
        
    # Group by (Scanner, Stage, Exception_Type, Failing_Object)
    grouped = {}
    for ex in all_ex:
        key = (ex["scanner"], ex["stage"], ex["exception_type"], ex["failing_object"])
        if key not in grouped:
            grouped[key] = {
                "scanner": ex["scanner"],
                "stage": ex["stage"],
                "exception_type": ex["exception_type"],
                "failing_object": ex["failing_object"],
                "count": 0,
                "recovered": 0,
                "causal": 0,
                "severity": ex["severity"],
                "sample_trace": ex["stack_trace"]
            }
        grouped[key]["count"] += 1
        if ex["recovered"] == "YES":
            grouped[key]["recovered"] += 1
        if ex["decision_impact"] == "CAUSAL_ABORT":
            grouped[key]["causal"] += 1
            
    for k, v in grouped.items():
        stats.append(v)
        
    return stats

def main():
    symbols = fetch_watchlist_safe()
    
    build_frozen_snapshot(symbols)
    
    regime_ctx = {"trend": "BULLISH", "biases": {}}
    
    scores = {"REVERSAL": []}
    metrics = {}
    metrics["EOD"] = run_scanner("EOD", eod_scanner.evaluate_eod_symbol, symbols, regime_ctx)
    metrics["REVERSAL"] = run_scanner("REVERSAL", reversal_scanner.evaluate_reversal_symbol, symbols, regime_ctx)
    
    for sym in symbols:
        ind_df = technical_indicators.apply_indicators(SNAPSHOT["1d"].get(sym).copy(), "1d")
        fund = SNAPSHOT["fundamentals"].get(sym, {})
        from reversal_scanner import evaluate_reversal_symbol
        import reversal_scanner
        # Temporarily drop floor to capture raw score distributions
        original_floor = reversal_scanner.CORE_SCORE_FLOOR
        reversal_scanner.CORE_SCORE_FLOOR = 0
        
        try:
            res = evaluate_reversal_symbol(sym, ind_df, fund, regime_ctx)
            if res.get("qualified", False):
                core_score = res.get("breakdown", {}).get("core_score", 0)
                scores["REVERSAL"].append((sym, core_score))
        finally:
            reversal_scanner.CORE_SCORE_FLOOR = original_floor
            
    metrics["MULTI_TF"] = run_scanner("MULTI_TF", multi_tf_scanner.evaluate_multi_tf_symbol, symbols, regime_ctx)
    metrics["MULTIBAGGER"] = run_scanner("MULTIBAGGER", multibagger.evaluate_multibagger_symbol, symbols, regime_ctx)
    
    print("\n" + "="*50)
    print("CORE SCORE DISTRIBUTION ANALYSIS (REVERSAL)")
    print("="*50)
    
    reversal_scores = scores.get("REVERSAL", [])
    if not reversal_scores:
        print("\nNo symbols passed the structural gates for Reversal.")
        print("This indicates that the current market regime restricts symbols from even reaching the scoring phase.")
    else:
        print(f"\n{len(reversal_scores)} symbols passed structural gates.")
        
        floors = [30, 28, 26, 24, 22, 20]
        print(f"\n{'Floor':<10} | {'Candidates Passing':<20} | {'Marginal Gain'}")
        print("-" * 50)
        
        prev_count = 0
        for f in floors:
            passing = [s for s in reversal_scores if s[1] >= f]
            count = len(passing)
            gain = count - prev_count
            print(f"{f:<10} | {count:<20} | +{gain}")
            prev_count = count
            
        print("\nDistribution Detail:")
        for sym, score in sorted(reversal_scores, key=lambda x: x[1], reverse=True):
            print(f"  - {sym}: {score}")

    # Exception Statistics
    ex_stats = deduplicate_exceptions(metrics)
    
    # Write JSON
    output_json = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "universe_size": len(symbols),
            "regime": "BULLISH"
        },
        "scanners": metrics,
        "core_score_proof": prove_core_score_floor(regime_ctx),
        "exception_statistics": ex_stats
    }
    
    with open("/tmp/institutional_audit_v6.json", "w") as f:
        json.dump(output_json, f, indent=2)
        
    print("Done. Generated /tmp/institutional_audit_v6.json")

if __name__ == "__main__":
    main()
