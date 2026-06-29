import logging
import pandas as pd
import numpy as np
import time
import os

logger = logging.getLogger(__name__)

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
        "price_earnings_ttm", "price_book_ratio"
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
                .limit(5000)
            )
            total, df = q.get_scanner_data()
            if df is not None and not df.empty:
                # Add normalized columns for fast matching
                if "ticker" in df.columns:
                    df["ticker_norm"] = df["ticker"].apply(normalize_id)
                if "name" in df.columns:
                    df["name_norm"] = df["name"].apply(normalize_id)
                    
                # Coerce numeric fields to float
                numeric_cols = [
                    "market_cap_basic", "return_on_equity_fy", 
                    "total_revenue_yoy_growth_ttm", "price_earnings_ttm", "price_book_ratio"
                ]
                for col_name in numeric_cols:
                    if col_name in df.columns:
                        df[col_name] = pd.to_numeric(df[col_name], errors="coerce")
                        
                if "return_on_equity_fy" in df.columns and "total_revenue_yoy_growth_ttm" in df.columns:
                    logger.info(f"Universe Refresh: ROE range [{df['return_on_equity_fy'].min():.1f}, {df['return_on_equity_fy'].max():.1f}], Growth range [{df['total_revenue_yoy_growth_ttm'].min():.1f}, {df['total_revenue_yoy_growth_ttm'].max():.1f}]")
                    
                # Save to cache
                os.makedirs(os.path.dirname(UNIVERSE_CACHE_PATH), exist_ok=True)
                df.to_pickle(UNIVERSE_CACHE_PATH)
                return df
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}: Failed to fetch market universe: {e}")
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
                    "peer_count_roe": valid_roe_count
                }
                continue
                
            # Refine 1: Sector + Size (0.2x to 5.0x) + Profitability (+/- 10) + Growth (+/- 15)
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
            if len(p1) >= 8:
                final_peers = p1
            elif len(p2) >= 8:
                final_peers = p2
            elif len(p3) >= 8:
                final_peers = p3
                
            val_pe = final_peers["price_earnings_ttm"].median()
            val_pb = final_peers["price_book_ratio"].median()
            val_roe = final_peers["return_on_equity_fy"].median()
            
        valid_pe_count = int(final_peers["price_earnings_ttm"].count())
        valid_pb_count = int(final_peers["price_book_ratio"].count())
        valid_roe_count = int(final_peers["return_on_equity_fy"].count())
        conservative_peer_count = min(valid_pe_count, valid_pb_count, valid_roe_count)
        
        medians_map[symbol] = {
            "median_pe": float(val_pe) if not pd.isna(val_pe) else None,
            "median_pb": float(val_pb) if not pd.isna(val_pb) else None,
            "median_roe": float(val_roe) if not pd.isna(val_roe) else None,
            "peer_count": conservative_peer_count,
            "peer_count_pe": valid_pe_count,
            "peer_count_pb": valid_pb_count,
            "peer_count_roe": valid_roe_count
        }
        
    return medians_map
