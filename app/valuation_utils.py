import logging
import pandas as pd
import numpy as np
import time
import os
import requests
import threading

logger = logging.getLogger(__name__)

_thread_local = threading.local()

def get_tt_session():
    if not hasattr(_thread_local, "tt_session"):
        _thread_local.tt_session = requests.Session()
    return _thread_local.tt_session

UNIVERSE_CACHE_PATH = "data/tradingview_universe_cache.pkl"

def normalize_id(x: str) -> str:
    if pd.isna(x) or not isinstance(x, str): return ""
    if ":" in x: x = x.split(":")[-1]
    return x.upper().replace("-", "").replace("_", "").replace("&", "").strip()

def fetch_full_universe_for_valuation() -> pd.DataFrame:
    from tradingview_screener import Query, col
    fields = [
        "ticker", "name", "sector", "market_cap_basic", 
        "return_on_equity_fy", "total_revenue_yoy_growth_ttm",
        "price_earnings_ttm", "price_book_ratio",
        "enterprise_value_ebitda_ratio", "dividend_yield_recent"
    ]
    
    # Check cache freshness first
    if os.path.exists(UNIVERSE_CACHE_PATH):
        try:
            mtime = os.path.getmtime(UNIVERSE_CACHE_PATH)
            age_hours = (time.time() - mtime) / 3600
            if age_hours < 24:
                return pd.read_pickle(UNIVERSE_CACHE_PATH)
        except Exception as e:
            logger.warning(f"Failed to read universe cache age: {e}")
            
    for attempt in range(3):
        try:
            q = (
                Query()
                .set_markets("india")
                .select(*fields)
                .where(col("exchange") == "NSE")
                .order_by("ticker")
                .limit(5000)
            )
            total, df = q.get_scanner_data()
            
            if df is not None and not df.empty:
                # Paginate if needed
                while len(df) < total:
                    offset = len(df)
                    q_next = q.offset(offset)
                    next_total, next_df = q_next.get_scanner_data()
                    if next_df is None or next_df.empty:
                        break
                    df = pd.concat([df, next_df], ignore_index=True)
                
                if len(df) < total:
                    logger.warning(f"Universe pagination incomplete: fetched {len(df)} out of {total} total stocks.")
                
                # Remove duplicated columns (like 'ticker') returned by tradingview_screener
                df = df.loc[:, ~df.columns.duplicated()].copy()
                
                # Remove duplicate rows by ticker due to pagination overlap
                if "ticker" in df.columns:
                    df = df.drop_duplicates(subset=["ticker"], keep="first")
                
                # Add normalized columns for fast matching
                if "ticker" in df.columns:
                    df["ticker_norm"] = df["ticker"].apply(normalize_id)
                if "name" in df.columns:
                    df["name_norm"] = df["name"].apply(normalize_id)
                    
                # Coerce numeric fields to float
                numeric_cols = [
                    "market_cap_basic", "return_on_equity_fy", 
                    "total_revenue_yoy_growth_ttm", "price_earnings_ttm", "price_book_ratio",
                    "enterprise_value_ebitda_ratio", "dividend_yield_recent"
                ]
                for col_name in numeric_cols:
                    if col_name in df.columns:
                        df[col_name] = pd.to_numeric(df[col_name], errors="coerce")
                        
                if "return_on_equity_fy" in df.columns and "total_revenue_yoy_growth_ttm" in df.columns:
                    logger.info(f"Universe Refresh: ROE range [{df['return_on_equity_fy'].min():.1f}, {df['return_on_equity_fy'].max():.1f}], Growth range [{df['total_revenue_yoy_growth_ttm'].min():.1f}, {df['total_revenue_yoy_growth_ttm'].max():.1f}]")
                    
                # Save to cache atomically
                os.makedirs(os.path.dirname(UNIVERSE_CACHE_PATH), exist_ok=True)
                temp_path = f"{UNIVERSE_CACHE_PATH}.tmp"
                with open(temp_path, "wb") as f:
                    df.to_pickle(f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_path, UNIVERSE_CACHE_PATH)
                return df
        except Exception as e:
            logger.exception(f"Attempt {attempt + 1}: Failed to fetch market universe")
            time.sleep(2 ** attempt)
            
    # If fetch fails, try loading from cache
    if os.path.exists(UNIVERSE_CACHE_PATH):
        try:
            mtime = os.path.getmtime(UNIVERSE_CACHE_PATH)
            age_hours = (time.time() - mtime) / 3600
            logger.warning(f"Loading market universe from local cache ({age_hours:.1f} hours old) due to fetch failure.")
            df = pd.read_pickle(UNIVERSE_CACHE_PATH)
            return df
        except Exception as e:
            logger.exception(f"Failed to load universe cache")
            
    return pd.DataFrame()

def compute_peer_medians(symbols: list, known_sectors: dict = None) -> dict:
    """
    Compute median P/E, P/B, and ROE per stock dynamically using a peer subset from the overall market universe.
    Returns {symbol: {"median_pe": ..., "median_pb": ..., "median_roe": ...}}
    """
    try:
        universe_df = fetch_full_universe_for_valuation()
    except Exception as e:
        logger.exception(f"Failed to fetch full market universe")
        universe_df = None

    # Ensure normalized columns exist (for backward compatibility if loaded from old cache)
    if universe_df is not None and not universe_df.empty:
        if "ticker_norm" not in universe_df.columns and "ticker" in universe_df.columns:
            universe_df["ticker_norm"] = universe_df["ticker"].apply(normalize_id)
        if "name_norm" not in universe_df.columns and "name" in universe_df.columns:
            universe_df["name_norm"] = universe_df["name"].apply(normalize_id)

    medians_map = {}
    for symbol in symbols:
        medians_map[symbol] = {"median_pe": None, "median_pb": None, "median_roe": None, "peer_count": 0}
        
        if universe_df is None or universe_df.empty:
            continue
            
        stock_row = pd.DataFrame()
        norm_sym = normalize_id(symbol)
        
        # Try ticker_norm first
        if "ticker_norm" in universe_df.columns:
            matches = universe_df[universe_df["ticker_norm"] == norm_sym]
            if not matches.empty:
                stock_row = matches
        # Then try name_norm
        if stock_row.empty and "name_norm" in universe_df.columns:
            matches = universe_df[universe_df["name_norm"] == norm_sym]
            if not matches.empty:
                stock_row = matches
        # Then exact name fallback
        if stock_row.empty and "name" in universe_df.columns:
            matches = universe_df[universe_df["name"] == symbol]
            if not matches.empty:
                stock_row = matches
            
        sector = None
        mcap = None
        roe = None
        growth = None
        
        if not stock_row.empty:
            stock = stock_row.iloc[0]
            sector = stock.get("sector")
            mcap = stock.get("market_cap_basic")
            roe = stock.get("return_on_equity_fy")
            growth = stock.get("total_revenue_yoy_growth_ttm")
            
        if pd.isna(sector) or not sector:
            if known_sectors and symbol in known_sectors:
                sector = known_sectors[symbol]
                
        if not sector or pd.isna(sector):
            continue
            
        peers = universe_df[universe_df["sector"] == sector].copy()
        
        # Exclude the stock itself from the peer set
        if not stock_row.empty:
            current_ticker = stock_row.iloc[0].get("ticker")
            if pd.notna(current_ticker):
                peers = peers[peers["ticker"] != current_ticker]
        
        # Clean anomalous P/E, P/B, and ROE values so they don't pollute sector medians
        peers["price_earnings_ttm"] = peers["price_earnings_ttm"].apply(lambda x: x if pd.notnull(x) and 0 < x < 500 else np.nan)
        peers["price_book_ratio"] = peers["price_book_ratio"].apply(lambda x: x if pd.notnull(x) and 0 < x < 100 else np.nan)
        peers["return_on_equity_fy"] = peers["return_on_equity_fy"].apply(lambda x: x if pd.notnull(x) and -200 < x < 200 else np.nan)
        
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            raw_pe = peers["price_earnings_ttm"].median()
            raw_pb = peers["price_book_ratio"].median()
            raw_roe = peers["return_on_equity_fy"].median()
            
            if pd.isna(mcap) or pd.isna(roe) or pd.isna(growth):
                valid_pe_count = int(peers["price_earnings_ttm"].count())
                valid_pb_count = int(peers["price_book_ratio"].count())
                valid_roe_count = int(peers["return_on_equity_fy"].count())
                conservative_peer_count = min(valid_pe_count, valid_pb_count, valid_roe_count)
                medians_map[symbol] = {
                    "median_pe": float(raw_pe) if not pd.isna(raw_pe) else None,
                    "median_pb": float(raw_pb) if not pd.isna(raw_pb) else None,
                    "median_roe": float(raw_roe) if not pd.isna(raw_roe) else None,
                    "peer_count": conservative_peer_count,
                    "peer_count_pe": valid_pe_count,
                    "peer_count_pb": valid_pb_count,
                    "peer_count_roe": valid_roe_count,
                    "median_peg": None,
                    "dispersion_iqr_median": None,
                    "source_type": "FALLBACK"
                }
                
                continue
                
            # Refine 1: Sector + Size (0.2x to 5.0x) + Profitability (+/- 10) + Growth (+/- 15)
            # Require non-null ROE and Growth to truly be a "refined" peer
            p1 = peers[
                (peers["market_cap_basic"].between(mcap * 0.2, mcap * 5.0)) &
                (peers["return_on_equity_fy"].between(roe - 10, roe + 10)) &
                (peers["total_revenue_yoy_growth_ttm"].between(growth - 15, growth + 15))
            ]
            
            # Refine 2: Sector + Size + Profitability
            p2 = peers[
                (peers["market_cap_basic"].between(mcap * 0.2, mcap * 5.0)) &
                (peers["return_on_equity_fy"].between(roe - 10, roe + 10))
            ]
            
            # Refine 3: Sector + Size
            p3 = peers[
                (peers["market_cap_basic"].between(mcap * 0.2, mcap * 5.0))
            ]
            
            final_peers = peers
            refinement_level = "FULL"
            if len(p1) >= 8:
                final_peers = p1
                refinement_level = "P1"
                logger.debug(f"{symbol}: Selected {len(final_peers)} peers using Refine 1 (Size, ROE, Growth).")
            elif len(p2) >= 8:
                final_peers = p2
                refinement_level = "P2"
                logger.debug(f"{symbol}: Selected {len(final_peers)} peers using Refine 2 (Size, ROE).")
            elif len(p3) >= 8:
                final_peers = p3
                refinement_level = "P3"
                logger.debug(f"{symbol}: Selected {len(final_peers)} peers using Refine 3 (Size).")
            else:
                logger.debug(f"{symbol}: Selected {len(final_peers)} peers using Full Sector Fallback.")
                

            val_pe = final_peers["price_earnings_ttm"].median()
            val_pb = final_peers["price_book_ratio"].median()
            val_roe = final_peers["return_on_equity_fy"].median()
            
            val_ev_ebitda = None
            if "enterprise_value_ebitda_ratio" in final_peers.columns:
                val_ev_ebitda = final_peers["enterprise_value_ebitda_ratio"].median()
                
            val_div_yield = None
            if "dividend_yield_recent" in final_peers.columns:
                val_div_yield = final_peers["dividend_yield_recent"].median()
            
            # Compute median PEG
            median_peg = None
            if "total_revenue_yoy_growth_ttm" in final_peers.columns:
                # Note: TradingView returns total_revenue_yoy_growth_ttm in percentage points (e.g., 15 for 15%).
                # The .clip(lower=1) prevents division by zero or negative growth, relying on the percentage point assumption.
                peg_series = final_peers["price_earnings_ttm"] / final_peers["total_revenue_yoy_growth_ttm"].clip(lower=1)
                peg_series = peg_series.apply(lambda x: x if pd.notnull(x) and 0 < x < 10 else np.nan)
                median_peg = peg_series.median()
                
            # Compute Dispersion (IQR / Median)
            dispersion = None
            if len(final_peers["price_earnings_ttm"].dropna()) >= 4:
                pe_series = final_peers["price_earnings_ttm"].dropna()
                q75, q25 = np.percentile(pe_series, [75 ,25])
                iqr = q75 - q25
                med = pe_series.median()
                if med > 0:
                    dispersion = iqr / med
            
        valid_pe_count = int(final_peers["price_earnings_ttm"].count())
        valid_pb_count = int(final_peers["price_book_ratio"].count())
        valid_roe_count = int(final_peers["return_on_equity_fy"].count())
        conservative_peer_count = min(valid_pe_count, valid_pb_count, valid_roe_count)
        
        source_type = "REFINED" if refinement_level in ("P1", "P2", "P3") else "FALLBACK"
        
        medians_map[symbol] = {
            "median_pe": float(val_pe) if not pd.isna(val_pe) else None,
            "median_pb": float(val_pb) if not pd.isna(val_pb) else None,
            "median_roe": float(val_roe) if not pd.isna(val_roe) else None,
            "median_ev_ebitda": float(val_ev_ebitda) if val_ev_ebitda is not None and not pd.isna(val_ev_ebitda) else None,
            "median_div_yield": float(val_div_yield) if val_div_yield is not None and not pd.isna(val_div_yield) else None,
            "peer_count": conservative_peer_count,
            "peer_count_pe": valid_pe_count,
            "peer_count_pb": valid_pb_count,
            "peer_count_roe": valid_roe_count,
            "median_peg": float(median_peg) if not pd.isna(median_peg) else 1.0,
            "dispersion_iqr_median": float(dispersion) if not pd.isna(dispersion) else None,
            "source_type": source_type
        }
        
    return medians_map
def norm_num(x):
    if x is None or pd.isna(x): return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def norm_pct(x):
    if x is None or pd.isna(x): return None
    try:
        val = float(x)
        return val / 100.0 if abs(val) > 1 else val
    except (TypeError, ValueError):
        return None

def extract_raw_metrics(symbol, bse_code=None, ticker=None):
    try:
        import yfinance as yf
        from bse_mapping_utils import load_bse_mappings, save_bse_mapping
        
        clean_sym = symbol.strip().upper()
        mappings = load_bse_mappings()
        target_sym = f"{symbol}.NS"
        if clean_sym in mappings:
            target_sym = mappings[clean_sym]
        elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
            target_sym = mappings[clean_sym[:-3]]
        elif bse_code:
            target_sym = f"{bse_code}.BO"
            
        # [VERSION: VALUATION_RATE_LIMIT_FIX_v1.0] Wrap all yf calls under the global rate limiter
        # so this code path is visible to the circuit breaker and counts toward 429 detection.
        from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit
        
        if not ticker:
            # Check if primary ticker has data; if not, try BSE fallback
            yf_acquire(context=f"extract_raw_metrics | {target_sym}")
            try:
                ticker = yf.Ticker(target_sym)
                hist = ticker.history(period="1d")
            except Exception as hist_err:
                msg = str(hist_err).lower()
                if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                    record_rate_limit(context=f"extract_raw_metrics | {target_sym}")
                raise hist_err
            finally:
                yf_release()
                
            if hist.empty:
                if target_sym.endswith(".NS"):
                    bse_sym = target_sym[:-3] + ".BO"
                    yf_acquire(context=f"extract_raw_metrics | {bse_sym}")
                    try:
                        ticker = yf.Ticker(bse_sym)
                        hist2 = ticker.history(period="1d")
                    except Exception:
                        hist2 = pd.DataFrame()
                    finally:
                        yf_release()
                    if not hist2.empty:
                        # [BUG-7 FIX] Strip suffix from symbol before saving so bare key is stored in DB
                        bare_sym = clean_sym[:-3] if clean_sym.endswith(".NS") or clean_sym.endswith(".BO") else clean_sym
                        save_bse_mapping(bare_sym, bse_sym)
                    elif bse_code:
                        yf_acquire(context=f"extract_raw_metrics | {bse_code}.BO")
                        try:
                            ticker = yf.Ticker(f"{bse_code}.BO")
                            hist3 = ticker.history(period="1d")
                        except Exception:
                            hist3 = pd.DataFrame()
                        finally:
                            yf_release()
                        if not hist3.empty:
                            bare_sym = clean_sym[:-3] if clean_sym.endswith(".NS") or clean_sym.endswith(".BO") else clean_sym
                            save_bse_mapping(bare_sym, f"{bse_code}.BO")
                elif bse_code:
                    yf_acquire(context=f"extract_raw_metrics | {bse_code}.BO")
                    try:
                        ticker = yf.Ticker(f"{bse_code}.BO")
                        hist4 = ticker.history(period="1d")
                    except Exception:
                        hist4 = pd.DataFrame()
                    finally:
                        yf_release()
                    if not hist4.empty:
                        bare_sym = clean_sym[:-3] if clean_sym.endswith(".NS") or clean_sym.endswith(".BO") else clean_sym
                        save_bse_mapping(bare_sym, f"{bse_code}.BO")
                
        _acquired = False
        try:
            yf_acquire(context=f"extract_raw_metrics.info | {target_sym}")
            _acquired = True
            try:
                info = ticker.info
            except Exception as info_err:
                msg = str(info_err).lower()
                if '401' in str(info_err) or 'Invalid Crumb' in str(info_err):
                    # Release the lock before the heavy crumb-cleanup work
                    yf_release()
                    _acquired = False
                    logger.warning(f"⚠️ YFinance crumb stale for {symbol}, clearing tzcache and retrying...")
                    import shutil, os
                    from config import BASE_DIR
                    tz_path = os.path.join(BASE_DIR, "data", "tzcache")
                    if os.path.exists(tz_path):
                        shutil.rmtree(tz_path, ignore_errors=True)
                    mappings = load_bse_mappings()
                    target_sym = f"{symbol}.NS"
                    if clean_sym in mappings:
                        target_sym = mappings[clean_sym]
                    elif clean_sym.endswith(".NS") and clean_sym[:-3] in mappings:
                        target_sym = mappings[clean_sym[:-3]]
                    elif bse_code:
                        target_sym = f"{bse_code}.BO"
                    yf_acquire(context=f"extract_raw_metrics.info.retry | {target_sym}")
                    _acquired = True
                    try:
                        ticker = yf.Ticker(target_sym)
                        info = ticker.info
                    finally:
                        yf_release()
                        _acquired = False
                else:
                    if 'too many requests' in msg or 'rate limit' in msg or '429' in msg:
                        record_rate_limit(context=f"extract_raw_metrics | {target_sym}")
                    raise info_err
        finally:
            if _acquired:
                yf_release()
        
        sector = info.get('sector')
        raw_industry = info.get('industry')
        from config.industry_normalizer import normalize_industry
        canonical_industry = normalize_industry(raw_industry)
        pe = norm_num(info.get('trailingPE') or info.get('peRatio'))
        pb = norm_num(info.get('priceToBook') or info.get('pbRatio'))
        roe = norm_pct(info.get('returnOnEquity'))
        eps = norm_num(info.get('trailingEps') or info.get('forwardEps'))
        bvps = norm_num(info.get('bookValue'))
        div_yield = norm_pct(info.get('dividendYield') or info.get('divYield'))
        current_price = norm_num(info.get('currentPrice') or info.get('regularMarketPrice'))
        
        if pe is None and eps is not None and eps > 0 and current_price is not None:
            pe = current_price / eps
        if pb is None and bvps is not None and bvps > 0 and current_price is not None:
            pb = current_price / bvps
            
        return {
            'sector': sector,
            'canonical_industry': canonical_industry,
            'pe': pe,
            'pb': pb,
            'roe': roe,
            'eps': eps,
            'bvps': bvps,
            'div_yield': div_yield
        }
    except Exception as e:
        logger.warning(f"Failed to extract raw metrics for {symbol}: {e}")
        return None


def compute_sector_medians(all_stocks):
    import statistics
    sector_data = {}
    for stock in all_stocks:
        sector = stock.get('sector')
        if not sector or sector == "Unknown":
            continue

        if sector not in sector_data:
            sector_data[sector] = {"pe_list": [], "pb_list": [], "roe_list": []}

        pe = norm_num(stock.get('pe'))
        if pe is not None and pe > 0:
            sector_data[sector]["pe_list"].append(pe)

        pb = norm_num(stock.get('pb'))
        if pb is not None and pb > 0:
            sector_data[sector]["pb_list"].append(pb)

        roe = norm_pct(stock.get('roe'))
        if roe is not None and roe > 0:
            sector_data[sector]["roe_list"].append(roe)

    medians = {}
    for sector, data in sector_data.items():
        pe_list = data["pe_list"]
        pb_list = data["pb_list"]
        roe_list = data["roe_list"]

        medians[sector] = {
            "median_pe": statistics.median(pe_list) if len(pe_list) >= 3 else None,
            "median_pb": statistics.median(pb_list) if len(pb_list) >= 3 else None,
            "median_roe": statistics.median(roe_list) if len(roe_list) >= 3 else None,
            "peer_count_pe": len(pe_list),
            "peer_count_pb": len(pb_list),
            "peer_count_roe": len(roe_list),
            "peer_count": min(len(pe_list), len(pb_list), len(roe_list)) if len(pe_list) > 0 else 0
        }
    return medians


def multi_scenario_dcf(fcf: float, growth_rate: float, discount_rate: float = 0.12, terminal_growth: float = 0.04, shares: float = 1.0):
    if not fcf or fcf <= 0 or not shares:
        return None
        
    def calculate_pv(g):
        pv = 0
        current_fcf = fcf
        # 5 year explicit forecast
        for i in range(1, 6):
            current_fcf *= (1 + g)
            pv += current_fcf / ((1 + discount_rate) ** i)
        # Terminal value
        tv = (current_fcf * (1 + terminal_growth)) / (discount_rate - terminal_growth)
        pv += tv / ((1 + discount_rate) ** 5)
        return pv / shares

    base_g = max(min(growth_rate, 0.20), 0.05)
    bear_g = base_g * 0.5
    bull_g = min(base_g * 1.5, 0.25)
    
    base_val = calculate_pv(base_g)
    bear_val = calculate_pv(bear_g)
    bull_val = calculate_pv(bull_g)
    
    weighted_value = (0.30 * bear_val) + (0.50 * base_val) + (0.20 * bull_val)
    
    return {
        "fair_value": weighted_value,
        "bear_value": bear_val,
        "bull_value": bull_val
    }
