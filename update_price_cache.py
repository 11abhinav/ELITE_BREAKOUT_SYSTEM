import re
import sys

with open("app/price_cache.py", "r") as f:
    content = f.read()

# We want to replace _download_all_robust

new_impl = """
import os
from config import DATA_DIR
from datetime import timedelta

def _download_all_robust(watchlist: pd.DataFrame, period: str, interval: str, requester: str = None) -> dict[str, pd.DataFrame]:
    symbols = watchlist["Stock"].tolist()
    all_data: dict[str, pd.DataFrame] = {}
    total = len(symbols)
    batch_size = BATCH_DOWNLOAD_SIZE
    fetcher = get_fetcher()
    rate_limited = False

    history_dir = os.path.join(DATA_DIR, "history", interval)
    os.makedirs(history_dir, exist_ok=True)

    # Group symbols by what they need to fetch
    # Key: (range_from, range_to) or "FULL"
    # Value: list of (symbol, cached_df)
    fetch_groups = {}
    
    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    
    for sym in symbols:
        file_path = os.path.join(history_dir, f"{sym.replace(':', '_')}.parquet")
        needs_full = True
        cached_df = None
        
        if os.path.exists(file_path):
            try:
                cached_df = pd.read_parquet(file_path)
                if not cached_df.empty:
                    # Find last timestamp
                    if 'Date' in cached_df.columns:
                        last_ts = pd.to_datetime(cached_df['Date'].iloc[-1])
                    elif 'Datetime' in cached_df.columns:
                        last_ts = pd.to_datetime(cached_df['Datetime'].iloc[-1])
                    else:
                        last_ts = pd.to_datetime(cached_df.index[-1])
                        
                    # Back up 1 day to ensure we get overlapping candles to avoid gaps
                    range_from = (last_ts - timedelta(days=1)).strftime("%Y-%m-%d")
                    range_to = today_str
                    
                    group_key = (range_from, range_to)
                    if group_key not in fetch_groups:
                        fetch_groups[group_key] = []
                    fetch_groups[group_key].append((sym, cached_df))
                    needs_full = False
            except Exception as e:
                logger.warning(f"Failed to read disk cache for {sym}: {e}")
                
        if needs_full:
            if "FULL" not in fetch_groups:
                fetch_groups["FULL"] = []
            fetch_groups["FULL"].append((sym, None))

    # Process each group
    for group_key, items in fetch_groups.items():
        group_symbols = [item[0] for item in items]
        group_total = len(group_symbols)
        
        range_from, range_to = (None, None) if group_key == "FULL" else group_key
        desc = "FULL" if group_key == "FULL" else f"DELTA {range_from} to {range_to}"
        
        for i in range(0, group_total, batch_size):
            batch = group_symbols[i : i + batch_size]
            batch_end = min(i + batch_size, group_total)
            logger.info(f"[{requester}] 📥 Fetching Batch {desc} ({i}–{batch_end}/{group_total}) [{interval}]")
            
            batch_results = fetcher.get_batch_ohlcv(batch, interval=interval, period=period, retries=3, range_from=range_from, range_to=range_to)
            
            if batch_results:
                for sym in batch:
                    new_df = batch_results.get(sym)
                    
                    if new_df is not None and not new_df.empty:
                        # Find the matching cached_df
                        cached_df = next((item[1] for item in items if item[0] == sym), None)
                        
                        if cached_df is not None and not cached_df.empty:
                            # Merge them
                            combined = pd.concat([cached_df, new_df])
                            # Deduplicate based on timestamp
                            time_col = 'Date' if 'Date' in combined.columns else ('Datetime' if 'Datetime' in combined.columns else None)
                            if time_col:
                                combined = combined.drop_duplicates(subset=[time_col], keep='last')
                            else:
                                combined = combined[~combined.index.duplicated(keep='last')]
                                
                            combined = combined.sort_index() if time_col is None else combined.sort_values(time_col)
                            
                            # Keep reasonable history limit to prevent infinite growth
                            max_rows = 5000 if interval.endswith('m') else 2000
                            combined = combined.tail(max_rows).copy()
                            
                            all_data[sym] = combined
                        else:
                            all_data[sym] = new_df
                            
                        # Save back to disk
                        try:
                            file_path = os.path.join(history_dir, f"{sym.replace(':', '_')}.parquet")
                            all_data[sym].to_parquet(file_path)
                        except Exception as e:
                            logger.error(f"Failed to write disk cache for {sym}: {e}")
                            
            else:
                logger.error(f"❌ Batch {desc} failed or returned empty for {len(batch)} symbols.")
                rate_limited = True
                time.sleep(0.5)

    logger.info(f"✅ Data secured for {len(all_data)}/{total} symbols [{interval}]")

    # Record missing symbols but DON'T reject the entire fetch
    missing_count = 0
    for sym in symbols:
        if sym not in all_data:
            missing_count += 1
            try:
                upsert_fetch_error('yfinance', 'PRICE_CACHE', sym, interval, 'no_data_after_fetch', 'no_data_returned')
            except Exception:
                pass

    try:
        # Mark as success if we got ANY data, not just full coverage
        if len(all_data) > 0:
            mark_success(f"yfinance:{interval}")
        elif rate_limited:
            mark_failure(f"yfinance:{interval}", "Rate limited and no fallback data available")
        else:
            mark_failure(f"yfinance:{interval}", "No symbols returned after batch + fallback")
    except Exception:
        pass
    
    return all_data
"""

# Regex replacement
import re
pattern = re.compile(r"def _download_all_robust\(.*?return all_data", re.DOTALL)
new_content = pattern.sub(new_impl.strip(), content)

with open("app/price_cache.py", "w") as f:
    f.write(new_content)

print("Updated price_cache.py successfully.")
