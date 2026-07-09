import logging
logging.basicConfig(level=logging.WARNING)
from app.valuation_utils import fetch_full_universe_for_valuation
import traceback

def test_fetch():
    import os
    if os.path.exists("data/tradingview_universe_cache.pkl"):
        os.remove("data/tradingview_universe_cache.pkl")
    
    from tradingview_screener import Query, col
    fields = [
        "ticker", "name", "sector", "market_cap_basic", 
        "return_on_equity_fy", "total_revenue_yoy_growth_ttm",
        "price_earnings_ttm", "price_book_ratio"
    ]
    
    q = (
        Query()
        .set_markets("india")
        .select(*fields)
        .where(col("exchange") == "NSE")
        .limit(5000)
    )
    total, df = q.get_scanner_data()
    print(f"Type of df: {type(df)}")
    
    if df is not None and not df.empty:
        try:
            df = df.loc[:, ~df.columns.duplicated()].copy()
        except Exception as e:
            print("Error at duplicated:")
            traceback.print_exc()
            
        try:
            if "ticker" in df.columns:
                from app.valuation_utils import normalize_id
                df["ticker_norm"] = df["ticker"].apply(normalize_id)
        except Exception as e:
            print("Error at ticker norm:")
            traceback.print_exc()

        try:
            if "name" in df.columns:
                from app.valuation_utils import normalize_id
                df["name_norm"] = df["name"].apply(normalize_id)
        except Exception as e:
            print("Error at name norm:")
            traceback.print_exc()

        numeric_cols = [
            "market_cap_basic", "return_on_equity_fy", 
            "total_revenue_yoy_growth_ttm", "price_earnings_ttm", "price_book_ratio"
        ]
        for col_name in numeric_cols:
            if col_name in df.columns:
                try:
                    import pandas as pd
                    df[col_name] = pd.to_numeric(df[col_name], errors="coerce")
                except Exception as e:
                    print(f"Error at to_numeric for {col_name}:")
                    traceback.print_exc()

        try:
            if "return_on_equity_fy" in df.columns and "total_revenue_yoy_growth_ttm" in df.columns:
                print(f"Universe Refresh: ROE range [{df['return_on_equity_fy'].min():.1f}, {df['return_on_equity_fy'].max():.1f}], Growth range [{df['total_revenue_yoy_growth_ttm'].min():.1f}, {df['total_revenue_yoy_growth_ttm'].max():.1f}]")
        except Exception as e:
            print("Error at min/max logging:")
            traceback.print_exc()

test_fetch()
