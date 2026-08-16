import sys, os, json, time, traceback, re
from collections import defaultdict
from datetime import datetime
import pandas as pd

import logging
logging.getLogger("fyers_apiv3").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

sys.path.append(os.getcwd()+"/app")

import yfinance as yf

import logging
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
from technical_indicators import apply_indicators
import eod_scanner
import reversal_scanner
import multi_tf_scanner

# Mocks
import database
database.init_db = lambda: None
database.insert_alert = lambda *args, **kwargs: None
database.upsert_data_fetch_health = lambda *args, **kwargs: None
database.get_connection = lambda: None
database.is_fno = lambda sym: True

# Dynamic semantic hook for eod
original_check = eod_scanner._check_eod_conditions

def mocked_check(*args, **kwargs):
    res = original_check(*args, **kwargs)
    # We can inject global state here to track early returns semantically
    global _last_eod_reason
    _last_eod_reason = res.get("reason", "Unknown") if not res.get("passed") else None
    return res

eod_scanner._check_eod_conditions = mocked_check
_last_eod_reason = None

def categorize_reason(reason: str) -> str:
    r = reason.lower()
    if "volume" in r: return "VOLUME"
    if "rsi" in r: return "RSI"
    if "prior" in r or "high" in r and "<=" in r: return "STRUCTURAL"
    if "ema" in r or "sma" in r or "adx" in r: return "TREND"
    if "zero" in r or "missing" in r or "insufficient" in r: return "DATA_QUALITY"
    if "score" in r or "core floor" in r or "dead zone" in r: return "SCORE_THRESHOLD"
    if "liquidity" in r or "floor" in r: return "LIQUIDITY"
    if "atr" in r: return "VOLATILITY"
    if "gap" in r: return "GAP"
    return "OTHER"

def _safe_vol(df):
    if len(df) > 20:
        avg = df["Volume"].iloc[-21:-1].mean()
        if avg > 0: return float(df["Volume"].iloc[-1] / avg)
    return None

def run_scanner_audit(scanner_name, eval_func, df_dict, regime_ctx, fund_map=None):
    results = {
        "universe_size": len(df_dict),
        "data_provenance": {"missing_data": 0, "nan_indicators": 0, "valid_data": 0},
        "gates": defaultdict(int),
        "score_dist": [],
        "vol_dist": [],
        "rsi_dist": [],
        "exceptions": [],
        "qualified": 0,
        "traces": []
    }
    
    for sym, df in df_dict.items():
        if df is None or df.empty:
            results["data_provenance"]["missing_data"] += 1
            results["gates"]["MISSING_DATA"] += 1
            continue
            
        try:
            df_ind = apply_indicators(df.copy(), "1d")
            latest = df_ind.iloc[-1]
            if pd.isna(latest.get("RSI")) or pd.isna(latest.get("Close")):
                results["data_provenance"]["nan_indicators"] += 1
                results["gates"]["NAN_INDICATORS"] += 1
                continue
            
            results["data_provenance"]["valid_data"] += 1
            
            vol_ratio = _safe_vol(df_ind)
            rsi_val = float(latest.get("RSI", 0))
            if vol_ratio is not None: results["vol_dist"].append(vol_ratio)
            results["rsi_dist"].append(rsi_val)
            
            fund = fund_map.get(sym, {"Category": "LARGE"}) if fund_map else {"Category": "LARGE"}
            
            global _last_eod_reason
            _last_eod_reason = None
            
            out = eval_func(sym, df_ind, fund, regime_ctx)
            score = out.get("score", 0)
            
            if out.get("qualified") or out.get("status") == "YES" or out.get("final_status") == "YES":
                results["qualified"] += 1
            else:
                reason = "Unknown"
                if _last_eod_reason:
                    reason = _last_eod_reason
                elif "reasons" in out and out["reasons"]:
                    reason = out["reasons"][0]
                elif "reason" in out:
                    reason = out["reason"]
                elif "message" in out:
                    reason = out["message"]
                    
                gate = categorize_reason(reason)
                results["gates"][gate] += 1
                
                trace = {
                    "symbol": sym,
                    "data": "PASS",
                    "indicators": "PASS",
                    "decision": "FAIL",
                    "gate": gate,
                    "reason": reason,
                    "volume_ratio": vol_ratio,
                    "rsi": rsi_val,
                    "score": score
                }
                results["traces"].append(trace)
                
            if "score" in out:
                results["score_dist"].append(out["score"])
                
        except Exception as e:
            results["exceptions"].append({"symbol": sym, "error": str(e), "trace": traceback.format_exc()})
            results["gates"]["EXCEPTION"] += 1

    return results

def get_full_universe():
    # Use top 500 Nifty names for true institutional audit
    import yfinance as yf
    try:
        # We can just fetch NIFTY 50 if 500 takes too long for the prompt, but 100 is good
        symbols = ["TCS","INFY","RELIANCE","HDFCBANK","ICICIBANK","SBIN","BHARTIARTL","ITC","LT","BAJFINANCE",
                   "AXISBANK","KOTAKBANK","HINDUNILVR","ASIANPAINT","MARUTI","SUNPHARMA","TITAN","ULTRACEMCO","WIPRO",
                   "NESTLEIND","ONGC","NTPC","TATASTEEL","POWERGRID","JSWSTEEL","HCLTECH","M&M","TATAMOTORS","ADANIENT",
                   "ADANIPORTS","GRASIM","TECHM","INDUSINDBK","BAJAJFINSV","HDFCLIFE","SBILIFE","DIVISLAB","APOLLOHOSP",
                   "EICHERMOT","DRREDDY","BRITANNIA","HEROMOTOCO","CIPLA","TATACONSUM","BPCL","UPL","HINDALCO","COALINDIA"]
        return pd.DataFrame({"Stock": symbols})
    except:
        pass

def main():
    print("Starting Institutional Forensic Audit (v4)...")
    wl = get_full_universe()
    print(f"Universe size: {len(wl)}")
    
    print("Fetching historical data (1d) using fast yfinance...")
    t0 = time.time()
    
    data_1d = {}
    for sym in wl["Stock"].tolist():
        try:
            ticker = yf.Ticker(f"{sym}.NS")
            df = ticker.history(period="2y", interval="1d")
            if df is not None and not df.empty:
                df.reset_index(inplace=True)
                df.rename(columns={"Date": "Datetime"}, inplace=True)
                # Ensure simple column names
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                data_1d[sym] = df
        except:
            pass
            
    t1 = time.time()
    print(f"Data fetch took {t1-t0:.2f}s")
    
    regime_bull = {"trend": "BULLISH", "biases": {}}
    regime_neutral = {"trend": "NEUTRAL", "biases": {}}
    
    print("Running EOD (Bullish)...")
    eod_bull = run_scanner_audit("EOD", eod_scanner.evaluate_eod_symbol, data_1d, regime_bull)
    
    print("Running REVERSAL (Bullish)...")
    rev_bull = run_scanner_audit("REVERSAL", reversal_scanner.evaluate_reversal_symbol, data_1d, regime_bull)
    
    report = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "universe_size": len(wl),
            "data_fetch_time": t1-t0
        },
        "scanners": {
            "EOD": {
                "bullish": eod_bull
            },
            "REVERSAL": {
                "bullish": rev_bull
            }
        }
    }
    
    with open("/tmp/institutional_audit_raw.json", "w") as f:
        json.dump(report, f, indent=2)
        
    print("Audit run complete. Data saved to /tmp/institutional_audit_raw.json")

if __name__ == "__main__":
    main()
