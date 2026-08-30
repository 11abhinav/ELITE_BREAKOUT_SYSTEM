import json
import os
import hashlib
import pandas as pd
import numpy as np
from datetime import datetime
import zoneinfo

IST = zoneinfo.ZoneInfo("Asia/Kolkata")

TELEMETRY_PATH = "logs/scanner_telemetry.jsonl"
WATCHLIST_PATH = "data/elite_fundamental_watchlist.csv"
HISTORY_DIR = "data/history/1d"
OUTPUT_CSV = "artifacts/canonical_analytics_dataset.csv"

SECTOR_MAP = {
    "^CNXAUTO": "NIFTY AUTO",
    "^NSEBANK": "NIFTY BANK",
    "^CNXIT": "NIFTY IT",
    "^CNXREALTY": "NIFTY REALTY",
    "^CNXPHARMA": "NIFTY PHARMA",
    "^CNXMETAL": "NIFTY METAL",
    "^CNXENERGY": "NIFTY ENERGY",
    "^CNXFMCG": "NIFTY FMCG",
    "^CNXINFRA": "NIFTY INFRA",
    "^CNXFIN": "NIFTY FINANCIAL SERVICES",
    "^CNXMEDIA": "NIFTY MEDIA",
    "^CNXPSUBANK": "NIFTY PSU BANK",
    "^CNXCMDT": "NIFTY COMMODITIES"
}

TV_SECTOR_TO_ROTATION = {
    "Technology": "IT", "Software": "IT",
    "Health Technology": "Pharma", "Health Services": "Pharma", "Pharmaceuticals": "Pharma", "Healthcare": "Pharma",
    "Banks": "Banking", "Commercial Banks": "Banking", "Public Sector Banks": "PSU Bank", "PSU Banks": "PSU Bank",
    "Finance": "Financials", "Financial Services": "Financials", "Insurance": "Financials", "Diversified Financials": "Financials",
    "Consumer Non-Durables": "FMCG", "Food & Beverages": "FMCG", "Beverages": "FMCG", "Tobacco": "FMCG", "Household Products": "FMCG",
    "Producer Manufacturing": "Auto", "Consumer Durables": "Auto", "Automobiles": "Auto", "Auto Components": "Auto",
    "Non-Energy Minerals": "Metal", "Metals & Mining": "Metal", "Steel": "Metal", "Aluminum": "Metal",
    "Energy Minerals": "Energy", "Oil & Gas": "Energy", "Utilities": "Energy", "Power": "Energy", "Renewable Energy": "Energy",
    "Industrial Services": "Infrastructure", "Transportation": "Infrastructure", "Engineering": "Infrastructure", "Construction": "Infrastructure",
    "Real Estate": "Realty", "Real Estate Investment Trusts": "Realty",
    "Process Industries": "Chemicals", "Chemicals": "Chemicals", "Specialty Chemicals": "Chemicals",
    "Communications": "Telecom", "Telecommunication Services": "Telecom", "Telecom": "Telecom",
    "Retail Trade": "Consumption", "Consumer Services": "Consumption", "Food Service": "Consumption",
    "Electronic Technology": "Electronics", "Electronics": "Electronics", "Semiconductors": "Electronics",
    "Capital Goods": "Capital Goods", "Electrical Equipment": "Capital Goods", "Industrial Machinery": "Capital Goods",
    "Defence": "Defence", "Aerospace & Defence": "Defence"
}

# CNXFIN Sanitizer logic
def sanitize_cnxfin_df(df):
    df_sorted = df.copy().sort_index()
    pct_change = df_sorted["Close"].pct_change() * 100
    jump_mask = pct_change > 100
    ratio = 1.0
    jump_idx = None
    if jump_mask.any():
        jump_idx = pct_change[jump_mask].index[0]
        ratio = float(df_sorted["Close"].loc[jump_idx] / df_sorted["Close"].shift(1).loc[jump_idx])
        before_mask = df_sorted.index < jump_idx
        for col in ["Open", "High", "Low", "Close"]:
            df_sorted.loc[before_mask, col] = df_sorted.loc[before_mask, col] * ratio
    return df_sorted, jump_idx, ratio

# Pre-calculate Sector Hysteresis & Rankings
def precompute_sector_history():
    print("Pre-computing PIT Sector Rankings & Hysteresis...")
    sector_dfs = {}
    cnxfin_jump_idx = None
    cnxfin_ratio = 1.0
    
    for sym in SECTOR_MAP.keys():
        p = os.path.join(HISTORY_DIR, f"{sym}.parquet")
        if os.path.exists(p):
            df = pd.read_parquet(p)
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df.loc[~df.index.duplicated(keep='first')].sort_index()
            if sym == "^CNXFIN":
                df, cnxfin_jump_idx, cnxfin_ratio = sanitize_cnxfin_df(df)
            sector_dfs[sym] = df
            
    if "^CNXIT" not in sector_dfs:
        return {}, cnxfin_jump_idx, cnxfin_ratio
        
    all_dates = sorted(list(sector_dfs["^CNXIT"].index))
    consec_top3 = {sym: 0 for sym in SECTOR_MAP.keys()}
    consec_bottom3 = {sym: 0 for sym in SECTOR_MAP.keys()}
    
    sector_history = {}
    
    for d in all_dates:
        blended_returns = {}
        for sym in SECTOR_MAP.keys():
            if sym in sector_dfs:
                df_sliced = sector_dfs[sym].loc[:d]
                if len(df_sliced) >= 63:
                    p_curr = float(df_sliced["Close"].iloc[-1])
                    p_21 = float(df_sliced["Close"].iloc[-21])
                    p_63 = float(df_sliced["Close"].iloc[-63])
                    ret_21 = ((p_curr - p_21)/p_21)*100.0 if p_21 > 0 else 0.0
                    ret_63 = ((p_curr - p_63)/p_63)*100.0 if p_63 > 0 else 0.0
                    blended = (0.7 * ret_63) + (0.3 * ret_21)
                    blended_returns[sym] = round(blended, 2)
                else:
                    blended_returns[sym] = 0.0
            else:
                blended_returns[sym] = 0.0
                
        sorted_secs = sorted(blended_returns.items(), key=lambda x: x[1], reverse=True)
        raw_ranks = {sym: r + 1 for r, (sym, _) in enumerate(sorted_secs)}
        
        day_res = {}
        for sym in SECTOR_MAP.keys():
            rank = raw_ranks.get(sym, 7)
            if rank <= 3:
                consec_top3[sym] += 1
                consec_bottom3[sym] = 0
            elif rank >= len(SECTOR_MAP) - 2:
                consec_top3[sym] = 0
                consec_bottom3[sym] += 1
            else:
                consec_top3[sym] = 0
                consec_bottom3[sym] = 0
                
            if consec_top3[sym] >= 3:
                status = 'TAILWIND'
            elif consec_bottom3[sym] >= 3:
                status = 'HEADWIND'
            else:
                status = 'NEUTRAL'
                
            day_res[sym] = {
                "rank": rank,
                "blended_score": blended_returns.get(sym, 0.0),
                "status": status
            }
        sector_history[d.strftime("%Y-%m-%d")] = day_res
        
    return sector_history, cnxfin_jump_idx, cnxfin_ratio

# Counterfactual Outcome Evaluator
def evaluate_counterfactual(stock_df, dt_naive, entry_p, sl_p, target_p):
    if dt_naive not in stock_df.index:
        return {
            "eligibility": "NOT_ELIGIBLE",
            "reason": "EVALUATION_DATE_NOT_IN_PARQUET",
            "realized_r": None, "mfe_r": None, "mae_r": None,
            "label_A": False, "label_B": False, "label_C": False, "label_D": False, "label_E": False,
            "bars_to_t1": None, "bars_to_sl": None
        }
        
    loc_idx = stock_df.index.get_loc(dt_naive)
    future_bars = stock_df.iloc[loc_idx:] # Strict PIT: evaluate from decision day forward
    
    if len(future_bars) <= 1:
        return {
            "eligibility": "NOT_ELIGIBLE",
            "reason": "INSUFFICIENT_FUTURE_BARS",
            "realized_r": None, "mfe_r": None, "mae_r": None,
            "label_A": False, "label_B": False, "label_C": False, "label_D": False, "label_E": False,
            "bars_to_t1": None, "bars_to_sl": None
        }
        
    risk_unit = abs(entry_p - sl_p)
    if risk_unit <= 0:
        return {
            "eligibility": "NOT_ELIGIBLE",
            "reason": "ZERO_RISK_UNIT",
            "realized_r": None, "mfe_r": None, "mae_r": None,
            "label_A": False, "label_B": False, "label_C": False, "label_D": False, "label_E": False,
            "bars_to_t1": None, "bars_to_sl": None
        }
        
    max_fav = 0.0
    max_adv = 0.0
    t1_hit = False
    sl_hit = False
    bars_to_t1 = None
    bars_to_sl = None
    
    for bar_num, (_, row) in enumerate(future_bars.iterrows()):
        high = float(row["High"])
        low = float(row["Low"])
        
        fav = max(0.0, high - entry_p)
        adv = max(0.0, entry_p - low)
        
        if fav > max_fav: max_fav = fav
        if adv > max_adv: max_adv = adv
        
        if not t1_hit and high >= target_p:
            t1_hit = True
            bars_to_t1 = bar_num
            
        if not sl_hit and low <= sl_p:
            sl_hit = True
            bars_to_sl = bar_num
            
        if bar_num >= 10 or t1_hit or sl_hit:
            break
            
    mfe_r = round(max_fav / risk_unit, 2)
    mae_r = round(max_adv / risk_unit, 2)
    
    if t1_hit and not sl_hit:
        realized_r = round((target_p - entry_p) / risk_unit, 2)
    elif sl_hit and not t1_hit:
        realized_r = -1.0
    elif t1_hit and sl_hit:
        # First event determines outcome
        if (bars_to_t1 or 0) <= (bars_to_sl or 0):
            realized_r = round((target_p - entry_p) / risk_unit, 2)
        else:
            realized_r = -1.0
    else:
        # Trade open after lookforward window
        last_close = float(future_bars.iloc[-1]["Close"])
        realized_r = round((last_close - entry_p) / risk_unit, 2)
        
    label_A = t1_hit
    label_B = mfe_r >= 2.0
    label_C = mfe_r >= 2.0 and mae_r < 1.0
    label_D = t1_hit and (bars_to_t1 or 99) <= 5
    label_E = sl_hit and not t1_hit
    
    return {
        "eligibility": "ELIGIBLE",
        "reason": "OK",
        "realized_r": realized_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "label_A": label_A, "label_B": label_B, "label_C": label_C, "label_D": label_D, "label_E": label_E,
        "bars_to_t1": bars_to_t1,
        "bars_to_sl": bars_to_sl
    }

def main():
    print("Building Canonical Analytics Dataset...")
    
    # Calculate file hash for reproducibility
    hasher = hashlib.md5()
    with open(TELEMETRY_PATH, "rb") as tf:
        hasher.update(tf.read(1024*1024))
    telemetry_hash = hasher.hexdigest()
    
    # Pre-compute sector history
    sector_history, cnxfin_jump_idx, cnxfin_ratio = precompute_sector_history()
    
    # Load fundamental watchlist
    watchlist_df = pd.read_csv(WATCHLIST_PATH)
    watchlist_map = dict(zip(watchlist_df["Stock"].str.strip(), watchlist_df["Sector"].str.strip()))
    
    parquet_cache = {}
    path_exists_cache = {}
    date_cache = {}
    
    records = []
    
    with open(TELEMETRY_PATH, "r") as f:
        for idx, line in enumerate(f):
            data = json.loads(line)
            
            symbol = data.get("symbol")
            run_id = data.get("run_id")
            scanner = data.get("scanner")
            
            ts_str = data.get("timestamp")
            regime_source = "PRODUCTION_SNAPSHOT"
            
            if not ts_str and run_id and run_id.startswith("run_"):
                try:
                    val = int(run_id.split("_")[1])
                    ts_str = datetime.fromtimestamp(val, tz=IST).strftime("%Y-%m-%d %H:%M:%S IST")
                    regime_source = "DERIVED_FROM_RUN_ID"
                except Exception:
                    pass
                    
            if not ts_str:
                continue
                
            dt_str = ts_str.split(" ")[0]
            if dt_str not in date_cache:
                date_cache[dt_str] = pd.to_datetime(dt_str)
            dt_naive = date_cache[dt_str]
            
            eval_id = f"eval_{idx:06d}_{symbol}_{run_id}"
            
            # Watchlist sector
            stock_sector_raw = watchlist_map.get(symbol, "Unknown")
            rotation_sector_key = TV_SECTOR_TO_ROTATION.get(stock_sector_raw, stock_sector_raw)
            
            # Sector Context & CNXFIN Sanitization tracking
            day_sectors = sector_history.get(dt_str, {})
            # Find matching sector index
            sec_symbol = next((k for k, v in SECTOR_MAP.items() if v == rotation_sector_key or k == symbol), None)
            sec_info = day_sectors.get(sec_symbol, {}) if sec_symbol else {}
            
            sec_rank = sec_info.get("rank")
            sec_score = sec_info.get("blended_score")
            sec_status = sec_info.get("status", "NEUTRAL")
            
            # Auditability fields for CNXFIN
            is_cnxfin = (symbol == "^CNXFIN")
            cnxfin_sanitized = is_cnxfin and cnxfin_jump_idx and (dt_naive < cnxfin_jump_idx)
            
            # Stock PIT History Lookup
            p_path = os.path.join(HISTORY_DIR, f"{symbol}.parquet")
            if p_path not in path_exists_cache:
                path_exists_cache[p_path] = os.path.exists(p_path)
                
            has_history = path_exists_cache[p_path]
            stock_df = None
            if has_history:
                if symbol not in parquet_cache:
                    df = pd.read_parquet(p_path)
                    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                    df = df.loc[~df.index.duplicated(keep='first')].sort_index()
                    parquet_cache[symbol] = df
                stock_df = parquet_cache[symbol]
                
            # Telemetry extract
            telemetry_values = data.get("all_values", {})
            
            # Core Indicators
            def get_tel_val(key_name):
                k = next((x for x in telemetry_values.keys() if x.lower() == key_name.lower()), None)
                if k:
                    return telemetry_values[k].get("value")
                return None
                
            close_p = get_tel_val("Close")
            vol_val = get_tel_val("Volume")
            rsi_val = get_tel_val("RSI")
            sma50_val = get_tel_val("SMA50")
            sma200_val = get_tel_val("SMA200")
            
            # Trade Setup & Counterfactual Evaluation
            sl_target = data.get("sl_target", {})
            entry_p = sl_target.get("entry_price") or close_p
            sl_p = sl_target.get("sl_price")
            target_p = sl_target.get("target_price")
            rr_ratio = sl_target.get("rr_ratio")
            
            # Counterfactual outcomes
            if has_history and stock_df is not None and entry_p and sl_p and target_p and float(entry_p) > 0 and float(sl_p) > 0:
                cf = evaluate_counterfactual(stock_df, dt_naive, float(entry_p), float(sl_p), float(target_p))
            else:
                cf = {
                    "eligibility": "NOT_ELIGIBLE",
                    "reason": "MISSING_ENTRY_OR_SL_TARGET",
                    "realized_r": None, "mfe_r": None, "mae_r": None,
                    "label_A": False, "label_B": False, "label_C": False, "label_D": False, "label_E": False,
                    "bars_to_t1": None, "bars_to_sl": None
                }
                
            row_dict = {
                # Evaluation Identification
                "evaluation_id": eval_id,
                "scanner_run_id": run_id,
                "symbol": symbol,
                "scanner": scanner,
                "decision_timestamp": ts_str,
                "terminal_decision": data.get("terminal_decision"),
                "primary_reason": data.get("primary_reason"),
                "alert_generated": data.get("alert_generated", False),
                
                # Context & Auditability
                "regime_source": regime_source,
                "stock_sector": stock_sector_raw,
                "sector_rotation_key": rotation_sector_key,
                "sector_rank": sec_rank,
                "sector_blended_score": sec_score,
                "sector_status": sec_status,
                
                # CNXFIN Sanitization Auditability
                "raw_cnxfin_scaling": cnxfin_ratio if is_cnxfin else 1.0,
                "cnxfin_sanitization_applied": cnxfin_sanitized,
                "sanitization_method": "HISTORICAL_RESCALE_BEFORE_JUMP" if cnxfin_sanitized else "NONE",
                "sanitization_version": "v1.0",
                
                # Features & Indicators
                "close_price": close_p,
                "volume": vol_val,
                "rsi": rsi_val,
                "sma50": sma50_val,
                "sma200": sma200_val,
                
                # Trade Setup
                "entry_price": entry_p,
                "sl_price": sl_p,
                "target_price": target_p,
                "rr_ratio": rr_ratio,
                
                # Counterfactual Outcomes
                "trade_eligibility_status": cf["eligibility"],
                "eligibility_reason": cf["reason"],
                "cf_realized_r": cf["realized_r"],
                "cf_mfe_r": cf["mfe_r"],
                "cf_mae_r": cf["mae_r"],
                "label_A_t1_hit": cf["label_A"],
                "label_B_mfe_gt_2r": cf["label_B"],
                "label_C_mfe_2r_mae_lt_1r": cf["label_C"],
                "label_D_fast_t1": cf["label_D"],
                "label_E_sl_hit": cf["label_E"],
                "bars_to_t1": cf["bars_to_t1"],
                "bars_to_sl": cf["bars_to_sl"],
                
                # Reproducibility Metadata
                "dataset_version": "1.0.0",
                "telemetry_source_hash": telemetry_hash[:10],
                "generated_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            }
            records.append(row_dict)
            
            if (idx + 1) % 5000 == 0:
                print(f"Processed {idx + 1} records into dataset...", flush=True)
                
    df_out = pd.DataFrame(records)
    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    df_out.to_csv(OUTPUT_CSV, index=False)
    
    print(f"Canonical Dataset Compiled Successfully!")
    print(f"  Total Rows: {len(df_out)}")
    print(f"  Eligible Trade Replays: {(df_out['trade_eligibility_status'] == 'ELIGIBLE').sum()}")
    print(f"  Not Eligible: {(df_out['trade_eligibility_status'] == 'NOT_ELIGIBLE').sum()}")
    print(f"  Saved to: {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
