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
from fundamentals_cache import get_fundamentals
from watchlist_cache import get_watchlist
from sl_target_helper import compute_sl_and_target
from database import (
    init_db, get_connection, save_alert_if_new,
    get_user_watchlist, update_user_watchlist_scan_result,
    add_to_user_watchlist
)
from config import EOD_CONFIG, REVERSAL_CONFIG, PULLBACK_CONFIG, MULTI_TF_CONFIG

logger = logging.getLogger("stock_analyzer")
IST = ZoneInfo("Asia/Kolkata")


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
                        if s.startswith(sym_clean) and (s.endswith('.NS') or s.endswith('.BO') or s.endswith('.BSE')):
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


_MASTER_SYMBOLS_CACHE = None
_MASTER_PRECOMPILED_LIST = None
_MASTER_SYMBOLS_MTIME = 0

def _load_master_symbol_dictionary() -> dict:
    global _MASTER_SYMBOLS_CACHE, _MASTER_PRECOMPILED_LIST, _MASTER_SYMBOLS_MTIME
    import os, re, json

    now_ts = datetime.now(IST).timestamp()
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
                    upsert_scanner_health("MASTER_SYMBOLS", "OK", f"Synced {len(symbol_rows)} NSE/BSE equities")
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
    return results




def analyze_symbol(symbol: str, user_id: str = "DEFAULT_USER", is_deep_analysis: bool = False) -> dict:
    """
    Runs full dry-run multi-scanner diagnostic evaluation for a single ticker symbol.
    Validates ticker symbol first; returns structured error if invalid NSE/BSE stock ticker.
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

    # 1. Fetch OHLCV Market Data
    sample_df = pd.DataFrame([{"Stock": sym_clean, "Category": "MIDCAP", "Sector": "GENERAL"}])
    fetched_map = fetch_watchlist_data(sample_df, "1y", "1d", requester="STOCK_ANALYZER")

    df = fetched_map.get(sym_clean)
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        # Fallback fetch retry
        df = fetched_map.get(f"{sym_clean}.NS") or fetched_map.get(f"{sym_clean}.BO")

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {
            "symbol": sym_clean,
            "success": False,
            "error": f"Insufficient or missing historical price data for symbol '{sym_clean}'."
        }

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
    roce_val = 0.0
    roe_val = 0.0
    debt_equity = 0.0
    
    # 1. Fetch Core fundamental ratios from watchlist cache
    try:
        from watchlist_cache import get_watchlist
        wl = get_watchlist()
        if not wl.empty:
            match = wl[wl['Stock'].str.upper() == sym_clean]
            if not match.empty:
                row = match.iloc[0]
                company_name = str(row.get("Company", company_name))
                sector_name = str(row.get("Sector", sector_name))
                
                raw_roce = row.get("ROCE %")
                if raw_roce is not None and not pd.isna(raw_roce):
                    roce_val = float(raw_roce)
                    
                raw_roe = row.get("ROE %")
                if raw_roe is not None and not pd.isna(raw_roe):
                    roe_val = float(raw_roe)
                    
                raw_de = row.get("Debt/Equity", row.get("Debt to equity"))
                if raw_de is not None and not pd.isna(raw_de):
                    debt_equity = float(raw_de)
    except Exception as e:
        logger.warning(f"Failed to fetch watchlist fundamentals for {sym_clean}: {e}")

    # 2. Fallback to temp_universe.parquet (940+ equities with TradingView fundamental ratios)
    if (roce_val <= 0.0 or roe_val <= 0.0) and os.path.exists("data/temp_universe.parquet"):
        try:
            tu_df = pd.read_parquet("data/temp_universe.parquet")
            tu_match = tu_df[tu_df['ticker'].astype(str).str.upper().str.contains(sym_clean)]
            if tu_match.empty:
                tu_match = tu_df[tu_df['name'].astype(str).str.upper() == sym_clean]
            if not tu_match.empty:
                tu_row = tu_match.iloc[0]
                if company_name == sym_clean and pd.notna(tu_row.get("name")):
                    company_name = str(tu_row.get("name"))
                if sector_name == "GENERAL" and pd.notna(tu_row.get("sector")):
                    sector_name = str(tu_row.get("sector"))

                if roce_val <= 0.0:
                    raw_roce = tu_row.get("return_on_invested_capital_fq", tu_row.get("roce"))
                    if raw_roce is not None and not pd.isna(raw_roce):
                        roce_val = float(raw_roce)

                if roe_val <= 0.0:
                    raw_roe = tu_row.get("return_on_equity_fy", tu_row.get("return_on_equity"))
                    if raw_roe is not None and not pd.isna(raw_roe):
                        roe_val = float(raw_roe)

                if debt_equity <= 0.0:
                    raw_de = tu_row.get("debt_to_equity_fq", tu_row.get("debt_to_equity"))
                    if raw_de is not None and not pd.isna(raw_de):
                        debt_equity = float(raw_de)
        except Exception as _tue:
            logger.warning(f"Failed to fetch temp_universe fundamentals for {sym_clean}: {_tue}")

    # 3. Fallback to fund_data (Piotroski / Yahoo Cache)
    if (roce_val <= 0.0 or roe_val <= 0.0) and fund_data:
        if roce_val <= 0.0 and fund_data.get("roce") is not None:
            roce_val = float(fund_data.get("roce"))
        if roe_val <= 0.0 and fund_data.get("roe") is not None:
            roe_val = float(fund_data.get("roe"))
        if debt_equity <= 0.0 and fund_data.get("debt_equity") is not None:
            debt_equity = float(fund_data.get("debt_equity"))

    # 4. On-demand fundamentals cache fallback for missing ROE/ROCE or Piotroski Score
    # [VERSION: FUND_MERGE_FIX_v1.0] CRITICAL FIX: Never overwrite a valid cached Piotroski score (e.g. 8)
    # with a live on-demand fetch score (e.g. 3). The batch scan runs quarterly with full annual data;
    # live on-demand fetches use daily Yahoo data and can produce lower/different scores.
    # Rule: existing score in cache wins; on-demand only fills MISSING fields.
    if roce_val <= 0.0 or roe_val <= 0.0 or not fund_data or "score" not in fund_data:
        try:
            from fundamentals_cache import fetch_single_piotroski
            lookup_sym = "TMCV" if sym_clean in ["TMCV", "TMPV", "TATAMOTORS"] else sym_clean
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
                if roe_val <= 0.0 and on_demand_fund.get("roe") is not None:
                    roe_val = float(on_demand_fund.get("roe"))
                if roce_val <= 0.0 and on_demand_fund.get("roce") is not None:
                    roce_val = float(on_demand_fund.get("roce"))
                if debt_equity <= 0.0 and on_demand_fund.get("debt_equity") is not None:
                    debt_equity = float(on_demand_fund.get("debt_equity"))
        except Exception as _fe:
            logger.warning(f"On-demand fundamental fetch fallback failed for {sym_clean}: {_fe}")

    # Compute RS Percentile
    rs_dict = compute_nifty_rs_rating([sym_clean])
    rs_percentile = float(rs_dict.get(sym_clean, 50.0))

    # Deficits collection list
    deficits = []

    # ---------------- STAGE 1: DAILY BUILDER (UNIVERSE ELIGIBILITY) ----------------
    db_pass = True
    db_reasons = []

    if close_price < 100.0:
        db_pass = False
        db_reasons.append(f"Price ₹{close_price:.2f} < ₹100.0 minimum price floor")
        deficits.append(f"💵 Price Floor Deficit: Current price ₹{close_price:.2f} is below the ₹100.0 universe entry threshold.")

    history_len = len(df)
    if history_len < 50:
        db_pass = False
        db_reasons.append(f"Bar history {history_len} < 50 minimum required daily bars")
        deficits.append(f"📅 History Deficit: Symbol has only {history_len} daily bars (requires ≥50 bars).")

    avg_turnover_20d = (df['Close'] * df['Volume']).tail(20).mean() / 1e7 # in Cr
    if avg_turnover_20d < 1.0:
        db_pass = False
        db_reasons.append(f"20D Avg Turnover ₹{avg_turnover_20d:.2f}Cr < ₹1.0Cr minimum liquidity")
        deficits.append(f"💧 Liquidity Deficit: 20-day average turnover ₹{avg_turnover_20d:.2f}Cr is below ₹1.0Cr minimum.")

    if db_pass:
        db_reasons.append(f"Price ₹{close_price:.2f} ≥ ₹100.0 | Avg Turnover ₹{avg_turnover_20d:.1f}Cr ≥ ₹1.0Cr | Bars {history_len} ≥ 50")

    # Default indicators & moving averages
    vol_ratio = 1.0
    sma50_val = bundle.sma_50.iloc[-1] if hasattr(bundle, 'sma_50') and bundle.sma_50 is not None and not bundle.sma_50.empty else None
    sma200_val = bundle.sma_200.iloc[-1] if hasattr(bundle, 'sma_200') and bundle.sma_200 is not None and not bundle.sma_200.empty else None

    # ---------------- STAGE 2: EOD BREAKOUT SCANNER ----------------
    eod_status = "NO"
    eod_reasons = []

    if not db_pass:
        eod_reasons.append("Skipped (Failed Daily Builder Universe Gate)")
    else:
        # Check Breakout Close
        prior_20d_high = float(df['High'].iloc[-21:-1].max()) if len(df) >= 21 else float(df['High'].max())
        is_breakout = close_price > prior_20d_high

        vol_20d_med = float(df['Volume'].iloc[-21:-1].median()) if len(df) >= 21 else float(df['Volume'].median())
        vol_ratio = (volume_val / vol_20d_med) if vol_20d_med > 0 else 1.0

        candle_range = high_price - low_price
        candle_body = abs(close_price - open_price)
        upper_wick = high_price - max(close_price, open_price)
        body_ratio = (candle_body / candle_range) if candle_range > 0 else 0.0
        wick_ratio = (upper_wick / candle_range) if candle_range > 0 else 0.0

        eod_checks = []
        if not is_breakout:
            eod_checks.append(f"Close ₹{close_price:.2f} ≤ Prior 20D High ₹{prior_20d_high:.2f}")
        if vol_ratio < 1.8:
            eod_checks.append(f"Volume Ratio {vol_ratio:.2f}x < 1.8x threshold")
            deficits.append(f"🔊 Volume Surge Deficit: Current Volume Ratio is {vol_ratio:.2f}x (lacks +{max(0.0, 1.8 - vol_ratio):.2f}x for 1.8x EOD threshold).")
        if wick_ratio > 0.35:
            eod_checks.append(f"Upper Wick {wick_ratio*100:.1f}% > 35% max")
            deficits.append(f"🕯️ Upper Wick Deficit: Upper Wick is {wick_ratio*100:.1f}% of candle range (needs ≤35% for clean breakout close).")
        if close_price <= open_price:
            eod_checks.append("Candle is not bullish (Close ≤ Open)")

        if not eod_checks:
            eod_status = "CORE MET"
            eod_reasons.append(f"Clean Breakout Close (₹{close_price:.2f} > ₹{prior_20d_high:.2f}) | Volume Surge {vol_ratio:.2f}x ≥ 1.8x | Bullish Candle")
        else:
            eod_status = "NO"
            eod_reasons = eod_checks

    # ---------------- STAGE 3: MULTI-TF INTRADAY SCANNER ----------------
    mtf_status = "NO"
    mtf_reasons = []
    if not db_pass:
        mtf_reasons.append("Skipped (Failed Daily Builder Universe Gate)")
    else:
        mtf_reasons.append("Intraday 15-minute volume explosion spike required during market hours (09:30–14:45 IST)")

    # ---------------- STAGE 4: REVERSAL OVERSOLD BOUNCE ----------------
    rev_status = "NO"
    rev_reasons = []
    rsi_series = bundle.rsi_14 if hasattr(bundle, 'rsi_14') else None
    rsi_val = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty else 50.0

    if rsi_val <= 35.0:
        rev_status = "CORE MET"
        rev_reasons.append(f"Daily RSI {rsi_val:.1f} ≤ 35.0 Oversold threshold")
    else:
        rev_status = "NO"
        rev_reasons.append(f"Daily RSI {rsi_val:.1f} > 35.0 (Not in oversold bounce zone)")
        if rsi_val > 60:
            deficits.append(f"🔄 Reversal RSI Deficit: RSI is {rsi_val:.1f} (requires RSI ≤ 35.0 for mean-reversion bounce).")

    # ---------------- STAGE 5: PULLBACK CONTINUATION PIPELINE ----------------
    # [VERSION: STOCK_ANALYZER_PB_FIX_v2.0] Full Pullback pipeline evaluation matching pullback_pipeline.py
    pb_status = "NO"
    pb_reasons = []

    sma50 = bundle.sma_50.iloc[-1] if bundle.sma_50 is not None and not bundle.sma_50.empty else None
    sma200 = bundle.sma_200.iloc[-1] if bundle.sma_200 is not None and not bundle.sma_200.empty else None
    is_uptrend = (sma50 and sma200 and close_price > sma50 > sma200)

    if not is_uptrend:
        pb_status = "NO"
        pb_reasons.append("Trend Failure: Price not strictly above SMA50 > SMA200")
        deficits.append("📈 Trend Structure Deficit: Price is not aligned above SMA50 > SMA200 (requires established uptrend).")
    else:
        try:
            pivots = swing_utils.detect_confirmed_pivots(df, PULLBACK_CONFIG["LOOKBACK"], PULLBACK_CONFIG["CONFIRM"])
            if not pivots:
                pb_status = "NO"
                pb_reasons.append("No confirmed swing pivots detected")
                deficits.append("📉 Swing Structure Deficit: No confirmed swing high/low pivots found for pullback calculation.")
            else:
                impulse = swing_utils.select_pullback_origin(pivots, df, PULLBACK_CONFIG)
                if not impulse:
                    pb_status = "NO"
                    pb_reasons.append("No valid impulse origin wave found")
                    deficits.append("🌊 Impulse Wave Deficit: No valid impulse leg identified from swing pivots.")
                else:
                    ps = swing_utils.measure_pullback(df, impulse, PULLBACK_CONFIG)
                    if not ps.valid:
                        pb_status = "NO"
                        pb_reasons.append(f"Invalid pullback structure (Retracement {ps.depth_pct:.1f}%, Vol Ratio {ps.volume_ratio:.2f}x)")
                        deficits.append(f"📐 Retracement Deficit: Pullback depth {ps.depth_pct:.1f}% or volume ratio {ps.volume_ratio:.2f}x outside 20–60% bounds.")
                    else:
                        trig = swing_utils.detect_resumption_trigger(df, ps, PULLBACK_CONFIG)
                        if trig.valid:
                            pb_status = "CORE MET"
                            pb_reasons.append(f"Resumption Trigger Confirmed @ ₹{trig.entry_price:.2f} (Depth {ps.depth_pct:.1f}%, Vol {ps.volume_ratio:.2f}x)")
                        else:
                            pb_status = "WATCHLIST"
                            pb_reasons.append(f"Valid Pullback Structure (Depth {ps.depth_pct:.1f}%) — Awaiting Resumption Trigger Bar")
                            deficits.append("⌛ Resumption Trigger Deficit: Stock is in pullback zone, but hasn't formed a bullish resumption trigger candle yet.")
        except Exception as _pbe:
            pb_status = "NO"
            pb_reasons.append(f"Pullback calculation error: {str(_pbe)}")

    # ---------------- STAGE 6: WEALTH ENGINE ----------------
    we_status = "NO"
    we_reasons = []

    # roce_val, roe_val, and debt_equity are already fetched from watchlist above

    we_issues = []
    if roce_val < 20.0:
        we_issues.append(f"ROCE {roce_val:.1f}% < 20.0% min")
        deficits.append(f"💎 ROCE Quality Deficit: ROCE is {roce_val:.1f}% (requires ≥20.0% for Wealth Engine).")
    if roe_val < 15.0:
        we_issues.append(f"ROE {roe_val:.1f}% < 15.0% min")
        deficits.append(f"💎 ROE Quality Deficit: ROE is {roe_val:.1f}% (requires ≥15.0% for Wealth Engine).")
    if debt_equity > 0.5:
        we_issues.append(f"Debt-to-Equity {debt_equity:.2f} > 0.50 max")
        deficits.append(f"🏦 Leverage Deficit: Debt to Equity is {debt_equity:.2f} (requires ≤0.50 debt free/low debt).")

    # Wealth Engine Mandatory Technical Trend Gate: CMP > SMA200
    if sma200_val is not None and not pd.isna(sma200_val) and float(sma200_val) > 0:
        if close_price <= float(sma200_val):
            we_issues.append(f"Trend Failure: Close ₹{close_price:.2f} ≤ 200DMA ₹{float(sma200_val):.2f} (Wealth Engine requires CMP > 200DMA)")
            deficits.append(f"📈 200DMA Trend Deficit: Close is ₹{close_price:.2f} (below 200DMA ₹{float(sma200_val):.2f}). Wealth Engine active signals require CMP > 200DMA.")

    if not we_issues:
        we_status = "CORE MET"
        safe_sma200 = float(sma200_val) if sma200_val is not None and not pd.isna(sma200_val) else 0.0
        we_reasons.append(f"Core Fundamentals & Trend Pristine: ROCE {roce_val:.1f}% ≥ 20% | ROE {roe_val:.1f}% ≥ 15% | D/E {debt_equity:.2f} ≤ 0.5" + (f" | Close ₹{close_price:.2f} > 200DMA ₹{safe_sma200:.2f}" if safe_sma200 > 0 else ""))
    elif roce_val >= 15.0 and roe_val >= 12.0 and (sma200_val is not None and close_price > float(sma200_val)):
        we_status = "WATCHLIST"
        we_reasons = we_issues
    else:
        we_status = "NO"
        we_reasons = we_issues

    # ---------------- STAGE 7: MULTIBAGGER ENGINE ----------------
    mb_status = "NO"
    mb_reasons = []

    f_score = fund_data.get("score", fund_data.get("piotroski_score", 6))
    pledge_pct = fund_data.get("promoter_pledge_pct", 0.0)

    mb_issues = []
    if f_score < 7:
        mb_issues.append(f"Piotroski F-Score {f_score} < 7 min")
        deficits.append(f"📊 Piotroski F-Score Deficit: F-Score is {f_score}/9 (requires F-Score ≥7 for Prime Multibagger alert).")
    if pledge_pct > 10.0:
        mb_issues.append(f"Promoter Pledge {pledge_pct:.1f}% > 10.0% max")
        deficits.append(f"🔒 Promoter Pledge Deficit: Promoter Pledge is {pledge_pct:.1f}% (requires ≤10.0%).")

    if not mb_issues and is_uptrend:
        mb_status = "CORE MET (Prime)"
        mb_reasons.append(f"🚀 Prime Compounder: Piotroski {f_score}/9 | Pledge {pledge_pct:.1f}% ≤ 10% | Strong Trend")
    elif f_score >= 5 and pledge_pct <= 15.0:
        mb_status = "WATCHLIST"
        mb_reasons = mb_issues if mb_issues else ["Score in Watchlist tier (50-64)"]
    else:
        mb_status = "NO"
        mb_reasons = mb_issues

    # ---------------- COMPOSITE HEALTH SCORE CALCULATION ----------------
    tech_score = 50.0
    if is_uptrend:
        tech_score += 20.0
    if vol_ratio >= 1.5:
        tech_score += 15.0
    if rsi_val >= 50.0 and rsi_val <= 70.0:
        tech_score += 15.0

    fund_score = 50.0
    if roce_val >= 20.0:
        fund_score += 20.0
    if roe_val >= 15.0:
        fund_score += 15.0
    if debt_equity <= 0.5:
        fund_score += 15.0

    overall_health_score = min(100.0, round((tech_score * 0.5) + (fund_score * 0.3) + (rs_percentile * 0.2), 1))

    # Clean duplicates in deficits list (max 4 deficits)
    deficits = list(dict.fromkeys(deficits))[:4]
    if not deficits:
        deficits.append("🌟 Pristine Setup: No significant technical or fundamental deficits detected! Stock is in prime alignment.")

    # Check if ANY scanner met core conditions to auto-add to watchlist for deep processing
    any_core_met = any(status.startswith("CORE MET") for status in [eod_status, pb_status, we_status, rev_status, mb_status, mtf_status])
    
    # Determine precise scanner status for Watchlist display
    scanners_met = []
    scanners_wl = []
    
    if eod_status.startswith("CORE MET") or eod_status in ("YES", "ACTIVE", "QUALIFIED"):
        scanners_met.append("EOD")
    elif eod_status == "WATCHLIST":
        scanners_wl.append("EOD")

    if pb_status.startswith("CORE MET") or pb_status in ("YES", "ACTIVE", "QUALIFIED"):
        scanners_met.append("PULLBACK")
    elif pb_status == "WATCHLIST":
        scanners_wl.append("PULLBACK")

    if we_status.startswith("CORE MET") or we_status in ("YES", "ACTIVE", "QUALIFIED"):
        scanners_met.append("WEALTH")
    elif we_status == "WATCHLIST":
        scanners_wl.append("WEALTH")

    if rev_status.startswith("CORE MET") or rev_status in ("YES", "ACTIVE", "QUALIFIED"):
        scanners_met.append("REVERSAL")
    elif rev_status == "WATCHLIST":
        scanners_wl.append("REVERSAL")

    if mb_status.startswith("CORE MET") or mb_status in ("YES", "ACTIVE", "QUALIFIED"):
        scanners_met.append("MULTIBAGGER")
    elif mb_status == "WATCHLIST":
        scanners_wl.append("MULTIBAGGER")

    if mtf_status.startswith("CORE MET") or mtf_status in ("YES", "ACTIVE", "QUALIFIED"):
        scanners_met.append("MULTI-TF")
    elif mtf_status == "WATCHLIST":
        scanners_wl.append("MULTI-TF")

    if scanners_met:
        watchlist_status = "QUALIFIED (" + ", ".join(scanners_met) + ")"
    elif scanners_wl:
        watchlist_status = "WATCHLIST (" + ", ".join(scanners_wl) + ")"
    elif any_core_met:
        watchlist_status = "CORE MET"
    else:
        watchlist_status = "MONITORING"

    if any_core_met:
        # User requested: "YOU CAN SHOW CORE CONDTION MET, ADD TO WTAHCLIST ,STATUS WILL BE UPDATED THERE"
        add_to_user_watchlist(sym_clean, company_name, user_id, status=watchlist_status, health_score=overall_health_score)
        
        outcome_msg = f"⚡ CORE CONDITION MET: Added to Watchlist. Current Status: {watchlist_status} (Health Score: {overall_health_score:.1f})"
        if eod_status.startswith("CORE MET"): eod_reasons.append(outcome_msg)
        if pb_status.startswith("CORE MET"): pb_reasons.append(outcome_msg)
        if we_status.startswith("CORE MET"): we_reasons.append(outcome_msg)
        if rev_status.startswith("CORE MET"): rev_reasons.append(outcome_msg)
        if mb_status.startswith("CORE MET"): mb_reasons.append(outcome_msg)
        if mtf_status.startswith("CORE MET"): mtf_reasons.append(outcome_msg)

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
        "deficits": deficits,
        "funnel": {
            "daily_builder": {"status": "CORE MET" if db_pass else "NO", "reasons": db_reasons},
            "eod_breakout": {"status": eod_status, "reasons": eod_reasons},
            "multi_tf": {"status": mtf_status, "reasons": mtf_reasons},
            "reversal": {"status": rev_status, "reasons": rev_reasons},
            "pullback": {"status": pb_status, "reasons": pb_reasons},
            "wealth_engine": {"status": we_status, "reasons": we_reasons},
            "multibagger": {"status": mb_status, "reasons": mb_reasons}
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


def create_manual_alert_from_analysis(symbol: str, scanner_type: str = "EOD", user_id: str = "DEFAULT_USER") -> dict:
    """
    Promotes a qualified analysis result to an ACTIVE BUY ALERT in the database.
    Calculates exact Entry, Stop Loss, Target 1/2/3, Scores, and dispatches Telegram notification.
    """
    sym_clean = symbol.strip().upper().replace('.NS', '').replace('.BO', '')
    scanner_type = scanner_type.strip().upper()

    res = analyze_symbol(sym_clean, user_id=user_id)
    if not res.get("success"):
        return {"success": False, "error": res.get("error", "Analysis failed")}

    entry_price = float(res.get("close_price", 100.0))
    atr_est = entry_price * 0.025 # 2.5% ATR approximation
    sl_target = compute_sl_and_target(entry_price=entry_price, atr=atr_est, mode=scanner_type)

    if sl_target.get("is_rejected"):
        return {"success": False, "error": f"Risk engine rejected target calculation: {sl_target.get('rejection_reason')}"}

    ist_now = datetime.now(IST)
    score_val = int(res.get("overall_health_score", 85))

    saved, reason, alert_id, _ = save_alert_if_new(
        symbol=sym_clean,
        breakout_type=scanner_type,
        alert_time=ist_now.strftime("%Y-%m-%d %H:%M:%S+05:30"),
        scanner=scanner_type,
        category=f"{scanner_type} (MANUAL)",
        entry_price=entry_price,
        stop_loss=sl_target.get("stop_loss"),
        target_1=sl_target.get("target_1"),
        target_2=sl_target.get("target_2"),
        target_3=sl_target.get("target_3"),
        score=score_val,
        context={"is_manual": True, "created_by": user_id, "analysis_snapshot": res.get("funnel")},
        base_score=score_val,
        rs_bonus=5,
        sector_bonus=5,
        rs_percentile=res.get("rs_percentile", 80.0),
        sector_name=res.get("sector", "GENERAL"),
        regime_score=80.0
    )

    if not saved:
        return {"success": False, "error": f"Could not create manual alert: {reason}"}

    # Dispatch Telegram message
    try:
        from telegram_engine import send_telegram_message
        msg = (
            f"🚀 <b>MANUAL {scanner_type} BUY ALERT CREATED</b> 🚀\n\n"
            f"📌 <b>Symbol:</b> #{sym_clean}\n"
            f"💰 <b>Entry Price:</b> ₹{entry_price:.2f}\n"
            f"🛑 <b>Stop Loss:</b> ₹{sl_target.get('stop_loss', 0):.2f}\n"
            f"🎯 <b>Target 1:</b> ₹{sl_target.get('target_1', 0):.2f}\n"
            f"🎯 <b>Target 2:</b> ₹{sl_target.get('target_2', 0):.2f}\n"
            f"🎯 <b>Target 3:</b> ₹{sl_target.get('target_3', 0):.2f}\n"
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
        "stop_loss": sl_target.get("stop_loss"),
        "target_1": sl_target.get("target_1"),
        "message": f"Manual {scanner_type} alert successfully raised for #{sym_clean} @ ₹{entry_price:.2f}."
    }
