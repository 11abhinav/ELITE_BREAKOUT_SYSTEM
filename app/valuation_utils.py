import logging
import pandas as pd
import numpy as np
import time
import os

logger = logging.getLogger(__name__)

UNIVERSE_CACHE_PATH = "data/tradingview_universe_cache.pkl"

def fetch_full_universe_for_valuation() -> pd.DataFrame:
    from tradingview_screener import Query, col
    fields = [
        "name", "sector", "market_cap_basic", 
        "return_on_equity_fy", "total_revenue_yoy_growth_ttm",
        "price_earnings_ttm", "price_book_ratio"
    ]
    
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
                # Save to cache
                os.makedirs(os.path.dirname(UNIVERSE_CACHE_PATH), exist_ok=True)
                df.to_pickle(UNIVERSE_CACHE_PATH)
                return df
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}: Failed to fetch market universe: {e}")
            time.sleep(2 ** attempt)
            
    # If fetch fails, try loading from cache
    if os.path.exists(UNIVERSE_CACHE_PATH):
        logger.info("Loading market universe from local cache due to fetch failure.")
        try:
            df = pd.read_pickle(UNIVERSE_CACHE_PATH)
            return df
        except Exception as e:
            logger.error(f"Failed to load universe cache: {e}")
            
    return pd.DataFrame()

def compute_peer_medians(symbols: list, known_sectors: dict = None) -> dict:
    """
    Compute median P/E, P/B, and ROE per stock dynamically using a peer subset from the overall market universe.
    Returns {symbol: {"median_pe": ..., "median_pb": ..., "median_roe": ...}}
    """
    try:
        universe_df = fetch_full_universe_for_valuation()
    except Exception as e:
        logger.error(f"Failed to fetch full market universe: {e}")
        universe_df = None

    medians_map = {}
    for symbol in symbols:
        medians_map[symbol] = {"median_pe": None, "median_pb": None, "median_roe": None, "peer_count": 0}
        
        if universe_df is None or universe_df.empty:
            continue
            
        stock_row = universe_df[universe_df["name"] == symbol]
        if stock_row.empty:
            stock_row = universe_df[universe_df["name"] == symbol.replace("-", "_")]
            
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
        
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            raw_pe = peers["price_earnings_ttm"].median()
            raw_pb = peers["price_book_ratio"].median()
            raw_roe = peers["return_on_equity_fy"].median()
            
            if pd.isna(mcap) or pd.isna(roe) or pd.isna(growth):
                medians_map[symbol] = {
                    "median_pe": float(raw_pe) if not pd.isna(raw_pe) else None,
                    "median_pb": float(raw_pb) if not pd.isna(raw_pb) else None,
                    "median_roe": float(raw_roe) if not pd.isna(raw_roe) else None,
                    "peer_count": len(peers) if not peers.empty else 0
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
            if len(p1) >= 5:
                final_peers = p1
            elif len(p2) >= 5:
                final_peers = p2
            elif len(p3) >= 5:
                final_peers = p3
                
            val_pe = final_peers["price_earnings_ttm"].median()
            val_pb = final_peers["price_book_ratio"].median()
            val_roe = final_peers["return_on_equity_fy"].median()
            
        medians_map[symbol] = {
            "median_pe": float(val_pe) if not pd.isna(val_pe) else None,
            "median_pb": float(val_pb) if not pd.isna(val_pb) else None,
            "median_roe": float(val_roe) if not pd.isna(val_roe) else None,
            "peer_count": len(final_peers) if not final_peers.empty else 0
        }
        
    return medians_map
