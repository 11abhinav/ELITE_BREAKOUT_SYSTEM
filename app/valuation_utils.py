import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def compute_peer_medians(symbols: list) -> dict:
    """
    Compute median P/E, P/B, and ROE per stock dynamically using a peer subset from the overall market universe.
    Returns {symbol: {"median_pe": ..., "median_pb": ..., "median_roe": ...}}
    """
    try:
        from daily_builder import fetch_universe
        # Fetch the entire active market universe from TradingView
        universe_df = fetch_universe()
    except Exception as e:
        logger.error(f"Failed to fetch market universe: {e}")
        universe_df = None

    medians_map = {}
    for symbol in symbols:
        medians_map[symbol] = {"median_pe": None, "median_pb": None, "median_roe": None}
        
        if universe_df is None or universe_df.empty:
            continue
            
        stock_row = universe_df[universe_df["name"] == symbol]
        if stock_row.empty:
            stock_row = universe_df[universe_df["name"] == symbol.replace("-", "_")]
            
        if stock_row.empty:
            continue
            
        stock = stock_row.iloc[0]
        sector = stock.get("sector")
        mcap = stock.get("market_cap_basic")
        roe = stock.get("return_on_equity_fy")
        growth = stock.get("total_revenue_yoy_growth_ttm")
        
        if pd.isna(sector):
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
                    "median_roe": float(raw_roe) if not pd.isna(raw_roe) else None
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
            "median_roe": float(val_roe) if not pd.isna(val_roe) else None
        }
        
    return medians_map
