import sys

with open('app/multibagger.py', 'r') as f:
    content = f.read()

target = """        # 2. Fetch latest prices for just these symbols
        symbols = [p['symbol'] for p in open_positions]
        if not symbols:
            return
            
        from price_provider import get_prices
        prices_df = get_prices(symbols)
        
        price_data_map = {}
        for _, row in prices_df.iterrows():
            sym = row.get("Stock")
            if sym and row.get("cmp"):
                price_data_map[sym] = ExitPriceData(
                    symbol=sym,
                    price=row.get("cmp", 0.0),
                    sma_50=row.get("sma_50", 0),
                    sma_200=row.get("sma_200", 0),
                    high_20d=row.get("high_20d", 0),
                    close_yesterday=row.get("close_yesterday", 0),
                    sma_200_yesterday=row.get("sma_200_yesterday", 0)
                )
                
        run_exit_monitor(price_data_map, cache={})"""

replacement = """        # 2. Fetch latest prices for just these symbols
        symbols = [p['symbol'] for p in open_positions]
        if not symbols:
            return
            
        price_data_map_raw = batch_download_market_data(symbols)
        
        price_data_map = {}
        for sym, stock_data in price_data_map_raw.items():
            if stock_data:
                price_data_map[sym] = ExitPriceData(
                    symbol=sym,
                    price=stock_data.price,
                    sma_50=stock_data.sma_50,
                    sma_200=stock_data.sma_200,
                    high_20d=stock_data.high_20d,
                    close_yesterday=stock_data.close_yesterday,
                    sma_200_yesterday=stock_data.sma_200_yesterday
                )
                
        run_exit_monitor(price_data_map, cache={})"""

if target in content:
    with open('app/multibagger.py', 'w') as f:
        f.write(content.replace(target, replacement))
    print("Patched successfully")
else:
    print("Target not found")
