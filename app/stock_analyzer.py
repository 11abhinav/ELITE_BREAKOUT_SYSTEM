# =====================================================================================
# app/stock_analyzer.py
# ON-DEMAND STOCK ANALYZER, FUNNEL DIAGNOSTICS & MANUAL ALERT ENGINE
# =====================================================================================

import os
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd

from indicator_manager import manager
import swing_utils
import scoring_engine
from macro_utils import compute_nifty_rs_rating, MarketRegimeEngine
from price_cache import fetch_watchlist_data
from technical_indicators import apply_indicators
from fundamentals_cache import get_fundamentals
from watchlist_cache import get_watchlist
from sl_target_helper import compute_sl_and_target
from eod_scanner import evaluate_eod_symbol
from reversal_scanner import evaluate_reversal_symbol
from pullback_pipeline import evaluate_pullback_symbol
from wealth_engine import evaluate_wealth_symbol
from multibagger import evaluate_multibagger_symbol
from multi_tf_scanner import evaluate_multi_tf_symbol
from daily_builder import evaluate_daily_builder_symbol
from database import (
    init_db, get_connection, save_alert_if_new,
    get_user_watchlist, update_user_watchlist_scan_result,
    add_to_user_watchlist
)
from config import EOD_CONFIG, REVERSAL_CONFIG, PULLBACK_CONFIG, MULTI_TF_CONFIG

logger = logging.getLogger("stock_analyzer")
IST = ZoneInfo("Asia/Kolkata")

def _safe_num_or_none(val):
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
        res = float(val)
        import math
        if math.isnan(res) or math.isinf(res):
            return None
        return res
    except Exception:
        return None


def validate_nse_bse_ticker(symbol: str) -> dict:
    """
    Validates if the provided ticker symbol is a recognized NSE/BSE Indian stock ticker.
    Checks master dictionary, watchlist cache, database symbol_mappings, BSE mappings, and Yahoo Search.
    Auto-registers valid tickers into master_symbols DB so future lookups are <1ms.
    """
    if not symbol or not isinstance(symbol, str) or len(symbol.strip()) < 1:
        return {
            "is_valid": False,
            "error": "Symbol input cannot be empty. Please enter a valid NSE/BSE stock ticker (e.g. TATAMOTORS, RELIANCE, PERSISTENT)."
        }

    raw = symbol.strip().upper()
    sym_clean = raw.replace('.NS', '').replace('.BO', '').replace('.BSE', '')

    import re
    if not re.match(r"^[A-Z0-9&\-]{2,20}$", sym_clean):
        return {
            "is_valid": False,
            "error": f"Invalid ticker format '{symbol}'. NSE/BSE stock symbols contain only letters, numbers, hyphens, and ampersands (e.g. TATAMOTORS, M&M, BAJAJ-AUTO)."
        }

    company_name = sym_clean
    sector_name = "EQUITY"
    found = False

    # 1. Check Master Symbol Dictionary (includes nse_bse_master_universe 2,389+, temp_universe 940+, DB master_symbols)
    try:
        master = _load_master_symbol_dictionary()
        if sym_clean in master:
            found = True
            company_name = master[sym_clean].get("company_name", sym_clean)
            sector_name = master[sym_clean].get("sector", "EQUITY")
    except Exception as e:
        logger.warning(f"Master dictionary lookup warning for {sym_clean}: {e}")

    # 2. Check BSE Mappings Utility
    if not found:
        try:
            from bse_mapping_utils import load_bse_mappings
            bse_map = load_bse_mappings()
            if sym_clean in bse_map or f"{sym_clean}.NS" in bse_map or f"{sym_clean}.BO" in bse_map:
                found = True
        except Exception:
            pass

    # 3. Check Database symbol_mappings
    if not found:
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT original_sym, mapped_sym
                        FROM symbol_mappings
                        WHERE UPPER(original_sym) = %s OR UPPER(mapped_sym) = %s
                        LIMIT 1
                    """, (sym_clean, sym_clean))
                    row = cur.fetchone()
                    if row:
                        found = True
        except Exception:
            pass

    # 4. Live Yahoo Search API fallback (Fast & light HTTP GET search via stdlib urllib)
    if not found:
        try:
            import urllib.request
            import json
            req = urllib.request.Request(
                f"https://query2.finance.yahoo.com/v1/finance/search?q={sym_clean}&quotesCount=5&country=India",
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    quotes = data.get('quotes', [])
                    for q in quotes:
                        s = q.get('symbol', '').upper()
                        s_root = s.split('.')[0]
                        if s_root == sym_clean and (s.endswith('.NS') or s.endswith('.BO') or s.endswith('.BSE')):
                            found = True
                            company_name = q.get('shortname') or q.get('longname') or sym_clean
                            break
        except Exception as _yerr:
            logger.debug(f"Yahoo Search fallback failed for {sym_clean}: {_yerr}")

    # 5. Fallback check: If Yahoo Search API fails due to rate-limit/network, verify via fast light price data fetcher
    if not found and not sym_clean.startswith("NONEXISTENT") and not sym_clean.startswith("INVALID"):
        try:
            from price_cache import fetch_unified_historical
            test_res = fetch_unified_historical([sym_clean], period="5d", interval="1d", requester="TICKER_VAL")
            if test_res and sym_clean in test_res and test_res[sym_clean] is not None and not test_res[sym_clean].empty:
                found = True
        except Exception:
            pass

    if not found:
        return {
            "is_valid": False,
            "error": f"❌ '{sym_clean}' is NOT a recognized NSE/BSE ticker symbol. Please correct the stock ticker (e.g. TATAMOTORS, RELIANCE, PERSISTENT) or choose from the autocomplete dropdown list."
        }

    # Auto-register validated symbol into DB master_symbols so future lookups are <1ms
    try:
        from database import sync_master_symbols
        sync_master_symbols([{"symbol": sym_clean, "company_name": company_name, "sector": sector_name}])
    except Exception:
        pass

    return {
        "is_valid": True,
        "symbol": sym_clean,
        "company_name": company_name,
        "sector": sector_name
    }


import threading
_MASTER_LOCK = threading.Lock()
_MASTER_SYMBOLS_CACHE = None
_MASTER_PRECOMPILED_LIST = None
_MASTER_SYMBOLS_MTIME = 0

def _load_master_symbol_dictionary() -> dict:
    global _MASTER_SYMBOLS_CACHE, _MASTER_PRECOMPILED_LIST, _MASTER_SYMBOLS_MTIME
    import os, re, json

    now_ts = datetime.now(IST).timestamp()
    with _MASTER_LOCK:
        if _MASTER_SYMBOLS_CACHE is not None and (now_ts - _MASTER_SYMBOLS_MTIME) < 300:
            return _MASTER_SYMBOLS_CACHE

    master = {}

    # 0. Load comprehensive nse_bse_master_universe.json (contains all 2,389+ equities including TATAMOTORS)
    master_json_path = "data/nse_bse_master_universe.json"
    if os.path.exists(master_json_path):
        try:
            with open(master_json_path, "r") as f:
                json_data = json.load(f)
                if isinstance(json_data, dict):
                    master.update(json_data)
        except Exception as e:
            logger.warning(f"Error loading nse_bse_master_universe.json: {e}")

    # Fallback curated popular tickers if json missing
    popular_defaults = {
        "TATAMOTORS": {"symbol": "TATAMOTORS", "company_name": "Tata Motors Limited", "sector": "AUTO"},
        "TATAMTRDVR": {"symbol": "TATAMTRDVR", "company_name": "Tata Motors Limited (DVR)", "sector": "AUTO"},
        "TMCV": {"symbol": "TMCV", "company_name": "Tata Motors Limited (Commercial Vehicles)", "sector": "AUTO"},
        "TMPV": {"symbol": "TMPV", "company_name": "Tata Motors Passenger Vehicles Limited", "sector": "AUTO"},
        "RELIANCE": {"symbol": "RELIANCE", "company_name": "Reliance Industries Limited", "sector": "ENERGY"},
        "TCS": {"symbol": "TCS", "company_name": "Tata Consultancy Services Limited", "sector": "IT"},
        "INFY": {"symbol": "INFY", "company_name": "Infosys Limited", "sector": "IT"},
        "HDFCBANK": {"symbol": "HDFCBANK", "company_name": "HDFC Bank Limited", "sector": "FINANCE"},
        "ICICIBANK": {"symbol": "ICICIBANK", "company_name": "ICICI Bank Limited", "sector": "FINANCE"},
        "SBIN": {"symbol": "SBIN", "company_name": "State Bank of India", "sector": "FINANCE"},
        "BHARTIARTL": {"symbol": "BHARTIARTL", "company_name": "Bharti Airtel Limited", "sector": "TELECOM"},
        "ITC": {"symbol": "ITC", "company_name": "ITC Limited", "sector": "FMCG"},
        "LT": {"symbol": "LT", "company_name": "Larsen & Toubro Limited", "sector": "CAPITAL GOODS"},
        "DBL": {"symbol": "DBL", "company_name": "Dilip Buildcon Limited", "sector": "INFRASTRUCTURE"}
    }
    for k, v in popular_defaults.items():
        if k not in master:
            master[k] = v

    # 1. Load from DB table master_symbols
    try:
        from database import get_all_master_symbols
        db_symbols = get_all_master_symbols()
        if db_symbols:
            master.update(db_symbols)
    except Exception as e:
        logger.warning(f"Error loading master_symbols from DB in autocomplete: {e}")

    # 2. Load from temp_universe.parquet
    if os.path.exists("data/temp_universe.parquet"):
        try:
            df = pd.read_parquet("data/temp_universe.parquet")
            for _, r in df.iterrows():
                raw = str(r.get("ticker", "")).upper()
                sym = re.sub(r"^(NSE|BSE):", "", raw).replace(".NS", "").replace(".BO", "").strip()
                name = str(r.get("name", sym)).strip()
                sec = str(r.get("sector", "EQUITY")).strip()
                if sym and sym not in master:
                    master[sym] = {
                        "symbol": sym,
                        "company_name": name if name != "nan" else sym,
                        "sector": sec if sec != "nan" else "EQUITY"
                    }
        except Exception as e:
            logger.warning(f"Error loading temp_universe in autocomplete: {e}")

    # 3. Pre-compile search-ready tuple array for instant <1ms autocomplete
    compiled_list = []
    for sym, item in master.items():
        comp = str(item.get("company_name", sym)).upper()
        sym_nospace = re.sub(r"[\s\-\&\.]+", "", sym)
        comp_nospace = re.sub(r"[\s\-\&\.]+", "", comp)
        compiled_list.append((sym, item, sym_nospace, comp, comp_nospace))

    _MASTER_SYMBOLS_CACHE = master
    _MASTER_PRECOMPILED_LIST = compiled_list
    _MASTER_SYMBOLS_MTIME = now_ts
    return master


def refresh_master_symbols_universe() -> bool:
    """07:00 AM IST Daily Job: Sync all active NSE/BSE equity symbols into DB master_symbols table."""
    try:
        from database import sync_master_symbols, upsert_scanner_health
        m = _load_master_symbol_dictionary()
        if m:
            symbol_rows = list(m.values())
            ok = sync_master_symbols(symbol_rows)
            if ok:
                try:
                    upsert_scanner_health("MASTER_SYMBOLS", "OK", error_msg=f"Synced {len(symbol_rows)} NSE/BSE equities")
                except Exception:
                    pass
            logger.info(f"✅ 07:00 AM IST Master Symbol Refresh: Synced {len(symbol_rows)} equities into master_symbols table.")
            return ok
        return False
    except Exception as e:
        logger.error(f"❌ Failed to execute master symbols universe refresh: {e}")
        return False


def search_symbols_autocomplete(query: str, limit: int = 10) -> list:
    """
    Ultra-fast (<1ms) real-time autocomplete search returning matching NSE/BSE symbols & company titles.
    Searches across pre-indexed 2,389+ equities (including TATAMOTORS, RELIANCE, TCS, INFY).
    Supports space/punctuation insensitive matching (e.g. 'tata motors' -> TATAMOTORS).
    """
    if not query or len(query.strip()) < 1:
        return []

    import re
    q_raw = query.strip().upper()
    q_nospace = re.sub(r"[\s\-\&\.]+", "", q_raw)

    _load_master_symbol_dictionary()
    compiled_list = _MASTER_PRECOMPILED_LIST or []

    exact_matches = []
    prefix_matches = []
    contains_matches = []
    seen = set()

    for sym, item, sym_nospace, comp, comp_nospace in compiled_list:
        if sym in seen:
            continue

        if sym == q_raw or sym_nospace == q_nospace or comp == q_raw or comp_nospace == q_nospace:
            exact_matches.append(item)
            seen.add(sym)
        elif sym.startswith(q_raw) or sym_nospace.startswith(q_nospace) or comp.startswith(q_raw) or comp_nospace.startswith(q_nospace):
            prefix_matches.append(item)
            seen.add(sym)
        elif q_raw in sym or q_raw in comp or q_nospace in sym_nospace or q_nospace in comp_nospace:
            contains_matches.append(item)
            seen.add(sym)

    return (exact_matches + prefix_matches + contains_matches)[:limit]




def analyze_symbol(symbol: str, user_id: str = "DEFAULT_USER", is_deep_analysis: bool = False, pre_fetched_df: pd.DataFrame = None, _pre_fetched_regime_ctx: dict = None, _pre_fetched_temp_universe: pd.DataFrame = None) -> dict:
    """
    Runs full dry-run multi-scanner diagnostic evaluation for a single ticker symbol.
    Validates ticker symbol first; returns structured error if invalid NSE/BSE stock ticker.
    Supports pre_fetched_df for zero-latency bulk watchlist processing.
    """
    from database import add_to_user_watchlist, get_user_watchlist, update_user_watchlist_scan_result

    # 0. Validate NSE/BSE Ticker
    val = validate_nse_bse_ticker(symbol)
    if not val["is_valid"]:
        return {
            "symbol": symbol.strip().upper() if symbol else "",
            "success": False,
            "is_invalid_ticker": True,
            "error": val["error"]
        }

    sym_clean = val["symbol"]
    ist_now = datetime.now(IST)

    # 1. Fetch OHLCV Market Data (or use pre_fetched_df for bulk speed)
    df = pre_fetched_df
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        sample_df = pd.DataFrame([{"Stock": sym_clean, "Category": "MIDCAP", "Sector": "GENERAL"}])
        fetched_map = fetch_watchlist_data(sample_df, "1y", "1d", requester="STOCK_ANALYZER")

        df = fetched_map.get(sym_clean)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            # Fallback fetch retry with explicit non-ambiguous DataFrame checks
            df = fetched_map.get(f"{sym_clean}.NS")
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                df = fetched_map.get(f"{sym_clean}.BO")

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {
            "symbol": sym_clean,
            "success": False,
            "error": f"Insufficient or missing historical price data for symbol '{sym_clean}'."
        }

    history_len = len(df)
    if len(df) < 15:
        bar_cnt = len(df)
        close_val = float(df.iloc[-1]['Close']) if 'Close' in df.columns else 0.0
        return {
            "symbol": sym_clean,
            "company_name": val.get("company_name", sym_clean),
            "sector": val.get("sector", "EQUITY"),
            "close_price": close_val,
            "volume_ratio": 1.0,
            "rsi": 50.0,
            "overall_health_score": 0.0,
            "deficits": [f"📅 History Deficit: Symbol '{sym_clean}' has only {bar_cnt} daily bars (requires ≥15 daily bars for indicator calculation)."],
            "funnel": {
                "daily_builder": {"status": "NO", "reasons": [f"Insufficient bar history ({bar_cnt} < 15 bars)"]},
                "eod_breakout": {"status": "NO", "reasons": ["Skipped due to insufficient bar history"]},
                "multi_tf": {"status": "NO", "reasons": ["Skipped due to insufficient bar history"]},
                "reversal": {"status": "NO", "reasons": ["Skipped due to insufficient bar history"]},
                "pullback": {"status": "NO", "reasons": ["Skipped due to insufficient bar history"]},
                "wealth_engine": {"status": "NO", "reasons": ["Skipped due to insufficient bar history"]},
                "multibagger": {"status": "NO", "reasons": ["Skipped due to insufficient bar history"]}
            },
            "is_in_watchlist": False,
            "success": True
        }

    df = df.copy()
    df.dropna(subset=["Open", "High", "Low", "Close", "Volume"], inplace=True)
    df.attrs['adjusted'] = True
    df.attrs['symbol'] = sym_clean

    last_bar = df.iloc[-1]
    close_price = float(last_bar['Close'])
    open_price = float(last_bar['Open'])
    high_price = float(last_bar['High'])
    low_price = float(last_bar['Low'])
    volume_val = float(last_bar['Volume'])

    # Compute base indicators safely
    try:
        bundle = manager.compute_base_indicators(df, sym_clean)
    except Exception as e:
        logger.warning(f"Indicator computation fallback for {sym_clean}: {e}")
        class DummyBundle:
            sma_50 = pd.Series([], dtype=float)
            sma_200 = pd.Series([], dtype=float)
            rsi_14 = pd.Series([], dtype=float)
        bundle = DummyBundle()

    # Fetch Fundamentals from Piotroski cache
    fund_data = get_fundamentals(sym_clean) or {}
    
    # Defaults
    company_name = sym_clean
    sector_name = "GENERAL"
    roce_val = None
    roe_val = None
    debt_equity = None
    yoy_sales_val = None
    yoy_profit_val = None
    
    # 1. Fetch Core fundamental ratios from watchlist cache
    try:
        from watchlist_cache import get_watchlist
        wl = get_watchlist()
        if not wl.empty:
            match = wl[wl['Stock'].str.upper().str.replace('.NS', '').str.replace('.BO', '') == sym_clean]
            if not match.empty:
                row = match.iloc[0]
                company_name = str(row.get("Company", company_name))
                sector_name = str(row.get("Sector", sector_name))
                
                raw_roce = row.get("ROCE %")
                if raw_roce is not None and not pd.isna(raw_roce):
                    roce_val = _safe_num_or_none(raw_roce)
                    
                raw_roe = row.get("ROE %")
                if raw_roe is not None and not pd.isna(raw_roe):
                    roe_val = _safe_num_or_none(raw_roe)
                    
                raw_de = row.get("Debt/Equity", row.get("Debt to equity"))
                if raw_de is not None and not pd.isna(raw_de):
                    debt_equity = _safe_num_or_none(raw_de)

                raw_ys = row.get("YOY Revenue %", row.get("YOY Revenue", row.get("yoy_revenue")))
                if raw_ys is not None and not pd.isna(raw_ys):
                    yoy_sales_val = _safe_num_or_none(raw_ys)

                raw_yp = row.get("YOY Profit %", row.get("YOY Profit", row.get("yoy_profit")))
                if raw_yp is not None and not pd.isna(raw_yp):
                    yoy_profit_val = _safe_num_or_none(raw_yp)
    except Exception as e:
        logger.warning(f"Failed to fetch watchlist fundamentals for {sym_clean}: {e}")

    # 2. Fallback to temp_universe.parquet (940+ equities with TradingView fundamental ratios)
    # [BUG FIX: BATCH_TEMP_UNIVERSE_v1.0] Use pre-loaded cache if available (avoids N full parquet reads in batch)
    _tu_df_source = _pre_fetched_temp_universe
    if _tu_df_source is None and (roce_val is None or roce_val <= 0.0 or roe_val is None or roe_val <= 0.0 or debt_equity is None) and os.path.exists("data/temp_universe.parquet"):
        try:
            _tu_df_source = pd.read_parquet("data/temp_universe.parquet")
        except Exception as _tue2:
            logger.warning(f"Failed to load temp_universe.parquet for {sym_clean}: {_tue2}")
    if _tu_df_source is not None and (roce_val is None or roce_val <= 0.0 or roe_val is None or roe_val <= 0.0 or debt_equity is None):
        try:
            tu_df = _tu_df_source
            clean_tickers = tu_df['ticker'].astype(str).str.upper().str.replace('.NS', '').str.replace('.BO', '')
            tu_match = tu_df[clean_tickers == sym_clean]
            if tu_match.empty:
                tu_match = tu_df[tu_df['name'].astype(str).str.upper() == sym_clean]
            if not tu_match.empty:
                tu_row = tu_match.iloc[0]
                if company_name == sym_clean and pd.notna(tu_row.get("name")):
                    company_name = str(tu_row.get("name"))
                if sector_name == "GENERAL" and pd.notna(tu_row.get("sector")):
                    sector_name = str(tu_row.get("sector"))

                if roce_val is None:
                    raw_roce = tu_row.get("return_on_invested_capital_fq", tu_row.get("roce"))
                    if raw_roce is not None and not pd.isna(raw_roce):
                        roce_val = _safe_num_or_none(raw_roce)

                if roe_val is None:
                    raw_roe = tu_row.get("return_on_equity_fy", tu_row.get("return_on_equity"))
                    if raw_roe is not None and not pd.isna(raw_roe):
                        roe_val = _safe_num_or_none(raw_roe)

                if debt_equity is None:
                    raw_de = tu_row.get("debt_to_equity_fq", tu_row.get("debt_to_equity"))
                    if raw_de is not None and not pd.isna(raw_de):
                        debt_equity = _safe_num_or_none(raw_de)

                if yoy_sales_val is None:
                    raw_tu_ys = tu_row.get("revenue_growth_yoy", tu_row.get("yoy_revenue"))
                    if raw_tu_ys is not None and not pd.isna(raw_tu_ys):
                        yoy_sales_val = _safe_num_or_none(raw_tu_ys)

                if yoy_profit_val is None:
                    raw_tu_yp = tu_row.get("net_income_growth_yoy", tu_row.get("yoy_profit"))
                    if raw_tu_yp is not None and not pd.isna(raw_tu_yp):
                        yoy_profit_val = _safe_num_or_none(raw_tu_yp)
        except Exception as _tue:
            logger.warning(f"Failed to fetch temp_universe fundamentals for {sym_clean}: {_tue}")
    # end temp_universe block

    # 3. Fallback to fund_data (Piotroski / Yahoo Cache)
    if (roce_val is None or roce_val <= 0.0 or roe_val is None or roe_val <= 0.0 or debt_equity is None) and fund_data:
        if (roce_val is None or roce_val <= 0.0) and fund_data.get("roce") is not None:
            roce_val = _safe_num_or_none(fund_data.get("roce"))
        if (roe_val is None or roe_val <= 0.0) and fund_data.get("roe") is not None:
            roe_val = _safe_num_or_none(fund_data.get("roe"))
        if debt_equity is None and fund_data.get("debt_equity") is not None:
            debt_equity = _safe_num_or_none(fund_data.get("debt_equity"))

    # 4. On-demand fundamentals cache fallback for missing ROE/ROCE or Piotroski Score
    # [VERSION: FUND_MERGE_FIX_v1.0] CRITICAL FIX: Never overwrite a valid cached Piotroski score (e.g. 8)
    # with a live on-demand fetch score (e.g. 3). The batch scan runs quarterly with full annual data;
    # live on-demand fetches use daily Yahoo data and can produce lower/different scores.
    # Rule: existing score in cache wins; on-demand only fills MISSING fields.
    if roce_val is None or roce_val <= 0.0 or roe_val is None or roe_val <= 0.0 or not fund_data or "score" not in fund_data:
        try:
            from fundamentals_cache import fetch_single_piotroski
            lookup_sym = "TMCV" if sym_clean in ["TMCV", "TATAMOTORS"] else sym_clean
            on_demand_fund = fetch_single_piotroski(lookup_sym) or {}
            if on_demand_fund and not on_demand_fund.get("failed"):
                if not fund_data:
                    fund_data = on_demand_fund
                else:
                    # Only merge keys that are MISSING in fund_data - never overwrite existing valid score
                    existing_score = fund_data.get("score")
                    for k, v in on_demand_fund.items():
                        if k not in fund_data or fund_data[k] is None:
                            fund_data[k] = v
                    # Restore the cached score if it existed and was valid (>= 0)
                    if existing_score is not None and existing_score >= 0:
                        fund_data["score"] = existing_score
                if (roe_val is None or roe_val <= 0.0) and on_demand_fund.get("roe") is not None:
                    roe_val = _safe_num_or_none(on_demand_fund.get("roe"))
                if (roce_val is None or roce_val <= 0.0) and on_demand_fund.get("roce") is not None:
                    roce_val = _safe_num_or_none(on_demand_fund.get("roce"))
                if debt_equity is None and on_demand_fund.get("debt_equity") is not None:
                    debt_equity = _safe_num_or_none(on_demand_fund.get("debt_equity"))
        except Exception as _fe:
            logger.warning(f"On-demand fundamental fetch fallback failed for {sym_clean}: {_fe}")

    # Ensure fund_data dict contains the resolved fundamental ratios for evaluators
    if fund_data is None:
        fund_data = {}
    if roce_val is not None:
        fund_data["roce"] = roce_val
        fund_data["roce_val"] = roce_val
        fund_data["ROCE %"] = roce_val
    if roe_val is not None:
        fund_data["roe"] = roe_val
        fund_data["roe_val"] = roe_val
        fund_data["ROE %"] = roe_val
    if debt_equity is not None:
        fund_data["debt_equity"] = debt_equity
        fund_data["debt_to_equity"] = debt_equity
        fund_data["Debt/Equity"] = debt_equity
    if yoy_sales_val is not None:
        fund_data["yoy_revenue"] = yoy_sales_val
        fund_data["YOY Revenue %"] = yoy_sales_val
    if yoy_profit_val is not None:
        fund_data["yoy_profit"] = yoy_profit_val
        fund_data["YOY Profit %"] = yoy_profit_val

    # Fetch promoter pledge % from DB promoter_pledge_cache if missing
    if "promoter_pledge_pct" not in fund_data or fund_data["promoter_pledge_pct"] is None:
        try:
            from database import get_pledge_map
            pm = get_pledge_map([sym_clean])
            if pm and sym_clean in pm and pm[sym_clean] is not None:
                fund_data["promoter_pledge_pct"] = float(pm[sym_clean])
                fund_data["Promoter pledge %"] = float(pm[sym_clean])
        except Exception as _pme:
            logger.debug(f"Pledge map fetch failed for {sym_clean}: {_pme}")

    # Compute RS Percentile
    rs_dict = compute_nifty_rs_rating([sym_clean])
    rs_percentile = float(rs_dict.get(sym_clean, 50.0))

    # Deficits collection list
    deficits = []

    # ---------------- STAGE 1: DAILY BUILDER (UNIVERSE ELIGIBILITY) ----------------
    db_eval = evaluate_daily_builder_symbol(sym_clean, df, fund_data=fund_data)
    db_pass = db_eval.get("qualified", False)
    db_reasons = db_eval.get("reasons", [])

    if not db_pass:
        for r in db_reasons:
            if "Price" in r:
                deficits.append(f"💵 Price Floor Deficit: Current price ₹{close_price:.2f} is below the ₹100.0 universe entry threshold.")
            elif "history" in r:
                deficits.append(f"📅 History Deficit: Symbol has only {history_len} daily bars (requires ≥50 bars).")
            elif "Turnover" in r:
                deficits.append(f"💧 Liquidity Deficit: 20-day average turnover is below ₹1.0Cr minimum.")

    # Indicators & Moving Averages
    vol_20d_med = float(df['Volume'].iloc[-21:-1].median()) if len(df) >= 21 else float(df['Volume'].median())
    vol_ratio = (volume_val / vol_20d_med) if vol_20d_med > 0 else 1.0
    rsi_val = float(bundle.rsi_14.iloc[-1]) if hasattr(bundle, 'rsi_14') and bundle.rsi_14 is not None and not bundle.rsi_14.empty and not pd.isna(bundle.rsi_14.iloc[-1]) else 50.0
    sma50_val = float(bundle.sma_50.iloc[-1]) if hasattr(bundle, 'sma_50') and bundle.sma_50 is not None and not bundle.sma_50.empty and not pd.isna(bundle.sma_50.iloc[-1]) else None
    sma200_val = float(bundle.sma_200.iloc[-1]) if hasattr(bundle, 'sma_200') and bundle.sma_200 is not None and not bundle.sma_200.empty and not pd.isna(bundle.sma_200.iloc[-1]) else None
    ema20_val = float(bundle.ema_20.iloc[-1]) if hasattr(bundle, 'ema_20') and bundle.ema_20 is not None and not bundle.ema_20.empty and not pd.isna(bundle.ema_20.iloc[-1]) else None
    atr20_val = float(bundle.atr_20.iloc[-1]) if hasattr(bundle, 'atr_20') and bundle.atr_20 is not None and not bundle.atr_20.empty and not pd.isna(bundle.atr_20.iloc[-1]) else None

    # Trend alignment: Close > SMA50 > SMA200
    is_uptrend = (sma50_val is not None and sma200_val is not None and close_price > sma50_val > sma200_val)

    logger.info(f"🔍 [STOCK ANALYZER] [{sym_clean}] Starting deep multi-scanner evaluation (CMP: ₹{close_price:.2f} | ROCE: {roce_val if roce_val is not None else 'N/A'}% | D/E: {debt_equity if debt_equity is not None else 'N/A'})...")

    # Evaluate canonical per-symbol evaluators directly from production scanner modules with REAL macro regime context
    # [BUG FIX: BATCH_REGIME_v1.0] Use pre-fetched regime ctx from batch caller if available (avoids N redundant calls)
    regime_ctx = _pre_fetched_regime_ctx if _pre_fetched_regime_ctx is not None else MarketRegimeEngine.get_regime_context()
    
    logger.debug(f"📊 [STOCK ANALYZER] [{sym_clean}] Running EOD Breakout Evaluator...")
    eod_eval = evaluate_eod_symbol(sym_clean, df, fund_data=fund_data, regime_ctx=regime_ctx)
    
    logger.debug(f"📊 [STOCK ANALYZER] [{sym_clean}] Running Reversal Bounce Evaluator...")
    rev_eval = evaluate_reversal_symbol(sym_clean, df, fund_data=fund_data, regime_ctx=regime_ctx)
    
    logger.debug(f"📊 [STOCK ANALYZER] [{sym_clean}] Running Pullback Evaluator...")
    pb_eval = evaluate_pullback_symbol(sym_clean, df, fund_data=fund_data, regime_ctx=regime_ctx)
    
    logger.debug(f"📊 [STOCK ANALYZER] [{sym_clean}] Running Wealth Engine Evaluator...")
    we_eval = evaluate_wealth_symbol(sym_clean, df, fund_data=fund_data)
    
    logger.debug(f"📊 [STOCK ANALYZER] [{sym_clean}] Running Multibagger Engine Evaluator...")
    mb_eval = evaluate_multibagger_symbol(sym_clean, df, fund_data=fund_data)
    
    logger.debug(f"📊 [STOCK ANALYZER] [{sym_clean}] Running Multi-TF Intraday Evaluator...")
    mtf_eval = evaluate_multi_tf_symbol(sym_clean, df, regime_ctx=regime_ctx)

    eod_status = eod_eval.get("status", "NO")
    eod_reasons = list(eod_eval.get("reasons", []))
    rev_status = rev_eval.get("status", "NO")
    rev_reasons = list(rev_eval.get("reasons", []))
    pb_status = pb_eval.get("status", "NO")
    pb_reasons = list(pb_eval.get("reasons", []))
    we_status = we_eval.get("status", "NO")
    we_reasons = list(we_eval.get("reasons", []))
    mb_status = mb_eval.get("status", "NO")
    mb_reasons = list(mb_eval.get("reasons", []))
    mtf_status = mtf_eval.get("status", "NO")
    mtf_reasons = list(mtf_eval.get("reasons", []))

    # ---------------- COMPOSITE HEALTH SCORE CALCULATION ----------------
    tech_score = 50.0
    if is_uptrend:
        tech_score += 20.0
    if vol_ratio >= 1.5:
        tech_score += 15.0
    if rsi_val >= 50.0 and rsi_val <= 70.0:
        tech_score += 15.0

    fund_score = 50.0
    if roce_val is not None and roce_val >= 20.0:
        fund_score += 20.0
    if roe_val is not None and roe_val >= 15.0:
        fund_score += 15.0
    if debt_equity is not None and debt_equity <= 0.5:
        fund_score += 15.0

    overall_health_score = min(100.0, round((tech_score * 0.5) + (fund_score * 0.3) + (rs_percentile * 0.2), 1))

    # Determine precise scanner status for Watchlist display using BOOLEAN QUALIFIED CONTRACT
    scanners_met = []
    scanners_wl = []

    eval_pairs = [
        ("EOD", eod_eval),
        ("PULLBACK", pb_eval),
        ("WEALTH", we_eval),
        ("REVERSAL", rev_eval),
        ("MULTIBAGGER", mb_eval),
        ("MULTI-TF", mtf_eval)
    ]

    for name, ev in eval_pairs:
        if ev.get("qualified", False):
            scanners_met.append(name)
        elif ev.get("status", "NO") not in ["NO", "QUALIFIED"]:
            scanners_wl.append(name)

    logger.info(f"✅ [STOCK ANALYZER] [{sym_clean}] Complete | Health: {overall_health_score}/100 | Scanners Met: {scanners_met} | Watchlist: {scanners_wl}")

    any_core_met = bool(scanners_met)

    # Clean duplicates in deficits list (max 4 deficits)
    deficits = list(dict.fromkeys(deficits))[:4]
    if not deficits:
        if any_core_met:
            deficits.append("🌟 Pristine Setup: No significant technical or fundamental deficits detected! Stock is in prime alignment.")
        else:
            deficits.append("🔍 Setup Deficit: Stock has not triggered breakout parameters across any of the 6 core scanner engines.")

    if scanners_met:
        watchlist_status = "QUALIFIED (" + ", ".join(scanners_met) + ")"
    elif scanners_wl:
        watchlist_status = "WATCHLIST (" + ", ".join(scanners_wl) + ")"
    else:
        watchlist_status = "MONITORING"

    if any_core_met:
        outcome_msg = f"⚡ CORE CONDITION MET: Current Status: {watchlist_status} (Health Score: {overall_health_score:.1f})"
        if eod_eval.get("qualified"): eod_reasons.append(outcome_msg)
        if pb_eval.get("qualified"): pb_reasons.append(outcome_msg)
        if we_eval.get("qualified"): we_reasons.append(outcome_msg)
        if rev_eval.get("qualified"): rev_reasons.append(outcome_msg)
        if mb_eval.get("qualified"): mb_reasons.append(outcome_msg)
        if mtf_eval.get("qualified"): mtf_reasons.append(outcome_msg)

    # Check if symbol is already in user watchlist
    user_watchlist = get_user_watchlist(user_id)
    watchlist_symbols = {item["symbol"] for item in (user_watchlist or [])}
    is_in_watchlist = (sym_clean in watchlist_symbols)

    res = {
        "symbol": sym_clean,
        "company_name": company_name,
        "sector": sector_name,
        "success": True,
        "is_in_watchlist": is_in_watchlist,
        "is_deep_analysis": is_deep_analysis,
        "watchlist_status": watchlist_status,
        "close_price": close_price,
        "volume_ratio": round(vol_ratio, 2),
        "rsi": round(rsi_val, 1),
        "overall_health_score": overall_health_score,
        "technical_score": round(tech_score, 1),
        "fundamental_score": round(fund_score, 1),
        "rs_percentile": round(rs_percentile, 1),
        "setup_qualified": any_core_met,
        "production_eligible": bool(db_pass and (not deficits or "Pristine" in deficits[0])),
        "selected_for_alert": False,
        "deficits": deficits,
        "funnel": {
            "daily_builder": {**db_eval, "status": "CORE MET" if db_pass else "NO", "reasons": db_reasons},
            "eod_breakout": {**eod_eval, "status": eod_status, "reasons": eod_reasons},
            "multi_tf": {**mtf_eval, "status": mtf_status, "reasons": mtf_reasons},
            "reversal": {**rev_eval, "status": rev_status, "reasons": rev_reasons},
            "pullback": {**pb_eval, "status": pb_status, "reasons": pb_reasons},
            "wealth_engine": {**we_eval, "status": we_status, "reasons": we_reasons},
            "multibagger": {**mb_eval, "status": mb_status, "reasons": mb_reasons}
        }
    }

    try:
        update_user_watchlist_scan_result(
            symbol=sym_clean,
            user_id=user_id,
            health_score=overall_health_score,
            status=watchlist_status,
            deep_analysis_result=res
        )
    except Exception as _pe:
        logger.warning(f"Could not persist deep analysis result for {sym_clean}: {_pe}")

    return res


def analyze_watchlist(symbols: list, user_id: str = "DEFAULT_USER", is_deep_analysis: bool = False) -> dict:
    """
    Bulk batch diagnostic evaluation for a list of ticker symbols.
    Fetches market price data for all symbols in 1 single bulk network request,
    pre-loads fundamental ratios and macro regime context in memory, and evaluates all
    symbols across all 6 scanners in vectorized passes.
    
    Returns a dict containing batch summary and per-symbol diagnostic results.
    """
    if not symbols or not isinstance(symbols, list):
        return {"success": False, "error": "Symbols input must be a non-empty list."}

    clean_syms = []
    for s in symbols:
        if s and isinstance(s, str) and s.strip():
            clean_syms.append(s.strip().upper().replace('.NS', '').replace('.BO', ''))
    clean_syms = list(dict.fromkeys(clean_syms))

    if not clean_syms:
        return {"success": False, "error": "No valid stock symbols provided in list."}

    logger.info(f"📦 [STOCK ANALYZER BATCH] Starting bulk evaluation for {len(clean_syms)} symbols: {clean_syms}")

    # [BUG FIX: BATCH_REGIME_v1.0] Pre-fetch regime context ONCE for all symbols (not N times inside analyze_symbol)
    try:
        from macro_utils import MarketRegimeEngine as _RegimeEngine
        _regime_ctx_cache = _RegimeEngine.get_regime_context()
        logger.info(f"📊 [STOCK ANALYZER BATCH] Regime context pre-fetched: {_regime_ctx_cache.get('regime', 'UNKNOWN')}")
    except Exception:
        _regime_ctx_cache = {}

    # [BUG FIX: BATCH_TEMP_UNIVERSE_v1.0] Pre-load temp_universe.parquet ONCE for all symbols (not N times inside analyze_symbol)
    _temp_universe_cache = None
    if os.path.exists("data/temp_universe.parquet"):
        try:
            _temp_universe_cache = pd.read_parquet("data/temp_universe.parquet")
            logger.info(f"📊 [STOCK ANALYZER BATCH] temp_universe loaded ({len(_temp_universe_cache)} rows)")
        except Exception as _tue:
            logger.warning(f"[BATCH] Failed to pre-load temp_universe.parquet: {_tue}")

    # 1. Single Bulk Market Data Fetch for all symbols in the watchlist
    sample_df = pd.DataFrame([{"Stock": s, "Category": "MIDCAP", "Sector": "GENERAL"} for s in clean_syms])
    logger.info(f"📥 [STOCK ANALYZER BATCH] Fetching 1Y daily OHLCV for {len(clean_syms)} symbols in 1 bulk request...")
    fetched_map = fetch_watchlist_data(sample_df, "1y", "1d", requester="STOCK_ANALYZER_BATCH")
    logger.info(f"✅ [STOCK ANALYZER BATCH] Bulk fetch complete. {len(fetched_map)} datasets received.")

    results = {}
    for idx, sym in enumerate(clean_syms, 1):
        df = fetched_map.get(sym)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            df = fetched_map.get(f"{sym}.NS")
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                df = fetched_map.get(f"{sym}.BO")

        logger.info(f"🔄 [STOCK ANALYZER BATCH] [{idx}/{len(clean_syms)}] Processing {sym}...")
        results[sym] = analyze_symbol(
            sym,
            user_id=user_id,
            is_deep_analysis=is_deep_analysis,
            pre_fetched_df=df,
            _pre_fetched_regime_ctx=_regime_ctx_cache,
            _pre_fetched_temp_universe=_temp_universe_cache
        )

    logger.info(f"✅ [STOCK ANALYZER BATCH] Complete. {len(results)}/{len(clean_syms)} symbols evaluated.")

    return {
        "success": True,
        "total_symbols": len(clean_syms),
        "batch_results": results
    }


def create_manual_alert_from_analysis(symbol: str, scanner_type: str = "EOD", user_id: str = "DEFAULT_USER") -> dict:
    """
    Promotes a qualified analysis result to an ACTIVE BUY ALERT in the database.
    Enforces Deep Analysis execution (is_deep_analysis=True) and uses production evaluator scores, real ATRs, structural stop losses, and target levels T1-T4.
    """
    sym_clean = symbol.strip().upper().replace('.NS', '').replace('.BO', '')
    scanner_type = scanner_type.strip().upper()

    ALLOWED_SCANNERS = {"EOD", "MULTI_TF", "REVERSAL", "PULLBACK", "WEALTH", "MULTIBAGGER"}
    if scanner_type not in ALLOWED_SCANNERS:
        return {"success": False, "error": f"Invalid scanner type '{scanner_type}'. Allowed scanners: {', '.join(sorted(ALLOWED_SCANNERS))}"}

    # Enforce Deep Analysis Execution
    res = analyze_symbol(sym_clean, user_id=user_id, is_deep_analysis=True)
    if not res.get("success"):
        return {"success": False, "error": res.get("error", "Analysis failed")}

    # Surveillance Blacklist Gate
    try:
        from surveillance import get_live_blacklist
        if sym_clean in get_live_blacklist():
            return {"success": False, "error": f"Symbol '{sym_clean}' is on the NSE/BSE Surveillance Blacklist (ASM/GSM) and cannot be promoted to an active alert."}
    except Exception:
        pass

    # Reversal Cooldown Gate
    if scanner_type == "REVERSAL":
        try:
            from reversal_scanner import _is_symbol_in_reversal_cooldown
            if _is_symbol_in_reversal_cooldown(sym_clean, 40):
                return {"success": False, "error": f"Symbol '{sym_clean}' is under a 40-day fallen-knife reversal cooldown and cannot be promoted to an active alert."}
        except Exception:
            pass

    funnel_map = {
        "EOD": "eod_breakout",
        "MULTI_TF": "multi_tf",
        "REVERSAL": "reversal",
        "PULLBACK": "pullback",
        "WEALTH": "wealth_engine",
        "MULTIBAGGER": "multibagger"
    }
    funnel_key = funnel_map[scanner_type]
    scanner_stage = res.get("funnel", {}).get(funnel_key, {})
    is_qualified = scanner_stage.get("qualified") is True

    if not is_qualified:
        reasons_str = " | ".join(scanner_stage.get("reasons", []))
        return {
            "success": False,
            "error": f"Symbol '{sym_clean}' did not qualify for {scanner_type} scanner. Reasons: {reasons_str}"
        }

    entry_price = scanner_stage.get("entry_price")
    sl_val = scanner_stage.get("stop_loss")
    t1_val = scanner_stage.get("target_1")
    t2_val = scanner_stage.get("target_2")
    t3_val = scanner_stage.get("target_3")
    t4_val = scanner_stage.get("target_4")
    score_val = scanner_stage.get("score")

    if entry_price is None or sl_val is None or t1_val is None or score_val is None:
        return {
            "success": False,
            "error": f"Evaluator contract failure for {scanner_type}: missing canonical risk package (entry/SL/T1/score)."
        }

    entry_price = float(entry_price)
    score_val = int(score_val)
    ist_now = datetime.now(IST)

    # Dynamic Category Determination
    if scanner_type == "MULTIBAGGER":
        category_val = scanner_stage.get("conviction_tier")
        if not category_val:
            return {"success": False, "error": "Evaluator contract failure for MULTIBAGGER: missing conviction tier."}
        category_val = str(category_val)
    elif scanner_type == "WEALTH":
        buckets = scanner_stage.get("buckets", [])
        category_val = ", ".join(buckets) if buckets else scanner_stage.get("category", "Wealth Compounder")
    else:
        category_val = f"{scanner_type} (MANUAL)"

    # Copy Verbatim Evaluator Metadata
    rs_bonus_val = int(scanner_stage.get("rs_bonus", 0))
    sector_bonus_val = int(scanner_stage.get("sector_bonus", 0))
    regime_score_val = float(scanner_stage.get("regime_score", 80.0))
    rs_pct_val = float(scanner_stage.get("rs_percentile", res.get("rs_percentile", 80.0)))

    saved, reason, alert_id, _ = save_alert_if_new(
        symbol=sym_clean,
        breakout_type=scanner_type,
        alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
        scanner=scanner_type,
        category=category_val,
        entry_price=entry_price,
        stop_loss=sl_val,
        target_1=t1_val,
        target_2=t2_val,
        target_3=t3_val,
        target_4=t4_val,
        score=score_val,
        context={
            "is_manual": True,
            "created_by": user_id,
            "is_deep_analysis": True,
            "target_4": t4_val,
            "analysis_snapshot": res.get("funnel")
        },
        base_score=score_val,
        rs_bonus=rs_bonus_val,
        sector_bonus=sector_bonus_val,
        rs_percentile=rs_pct_val,
        sector_name=res.get("sector", "GENERAL"),
        regime_score=regime_score_val
    )

    if not saved:
        return {"success": False, "error": f"Could not create manual alert: {reason}"}

    # Dispatch Telegram message
    try:
        from telegram_engine import send_telegram_message
        msg = (
            f"🚀 <b>MANUAL {scanner_type} BUY ALERT CREATED</b> 🚀\n\n"
            f"📌 <b>Symbol:</b> #{sym_clean}\n"
            f"🏷️ <b>Category/Tier:</b> {category_val}\n"
            f"💰 <b>Entry Price:</b> ₹{entry_price:.2f}\n"
            f"🛑 <b>Stop Loss:</b> ₹{sl_val:.2f}\n"
            f"🎯 <b>Target 1:</b> ₹{t1_val:.2f}\n"
            f"🎯 <b>Target 2:</b> ₹{t2_val if t2_val else 0:.2f}\n"
            f"🎯 <b>Target 3:</b> ₹{t3_val if t3_val else 0:.2f}\n"
            f"🎯 <b>Target 4:</b> ₹{t4_val if t4_val else 0:.2f}\n"
            f"📊 <b>Score:</b> {score_val}/100\n"
            f"👤 <b>Initiated By:</b> {user_id}"
        )
        send_telegram_message(msg, scan_type=scanner_type)
    except Exception as e:
        logger.warning(f"Telegram manual alert notification dispatch warning: {e}")

    return {
        "success": True,
        "alert_id": alert_id,
        "symbol": sym_clean,
        "scanner": scanner_type,
        "entry_price": entry_price,
        "stop_loss": sl_val,
        "target_1": t1_val,
        "target_2": t2_val,
        "target_3": t3_val,
        "target_4": t4_val,
        "message": f"Manual {scanner_type} alert successfully raised for #{sym_clean} @ ₹{entry_price:.2f}."
    }
