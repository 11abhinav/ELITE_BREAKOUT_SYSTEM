import sys

with open("app/eod_scanner.py", "r") as f:
    content = f.read()

# Fix how it gets data out of all_ticker_data
old_check_missing = """                if symbol not in all_ticker_data or all_ticker_data[symbol] is None:
                    rejection_reasons["no_data"] += 1
                    logger.debug(f"[EOD] {symbol} rejected: No data available")
                    continue
                    
                # Handle ProviderResult errors gracefully
                if isinstance(all_ticker_data[symbol], ProviderResult):
                    res = all_ticker_data[symbol]
                    if res == ProviderResult.RATE_LIMIT:
                        rejection_reasons["no_data"] += 1
                        logger.debug(f"[EOD] {symbol} rejected: Rate limited")
                    else:
                        rejection_reasons["no_data"] += 1
                        logger.debug(f"[EOD] {symbol} rejected: Data fetch error ({res})")
                    continue
                    
                ticker = all_ticker_data[symbol].copy()"""

new_check_missing = """                if symbol not in all_ticker_data or all_ticker_data[symbol] is None:
                    rejection_reasons["no_data"] += 1
                    logger.debug(f"[EOD] {symbol} rejected: No data available")
                    continue
                    
                md = all_ticker_data[symbol]
                # Backward compatibility in case it's a raw DataFrame or ProviderResult
                if hasattr(md, 'dataframe'):
                    df = md.dataframe
                    if df is None:
                        rejection_reasons["no_data"] += 1
                        logger.debug(f"[EOD] {symbol} rejected: No valid MarketData (error={md.error})")
                        continue
                else:
                    if isinstance(md, ProviderResult):
                        rejection_reasons["no_data"] += 1
                        logger.debug(f"[EOD] {symbol} rejected: Data fetch error ({md})")
                        continue
                    df = md
                    
                if df is None or (hasattr(df, 'empty') and df.empty):
                    rejection_reasons["no_data"] += 1
                    logger.debug(f"[EOD] {symbol} rejected: Empty DataFrame")
                    continue
                    
                ticker = df.copy()"""

content = content.replace(old_check_missing, new_check_missing)
with open("app/eod_scanner.py", "w") as f:
    f.write(content)
print("Patched eod_scanner.py")
