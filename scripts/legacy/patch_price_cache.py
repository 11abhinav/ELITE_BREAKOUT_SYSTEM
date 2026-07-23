import sys

with open("app/price_cache.py", "r") as f:
    content = f.read()

# 1. Imports
if "from data_quality import DataQualityValidator, MarketData" not in content:
    content = content.replace("from core_enums import ProviderResult", 
        "from core_enums import ProviderResult\nfrom data_quality import DataQualityValidator, MarketData\nfrom config import SOURCE_RELIABILITY, MAX_HISTORY_SHRINK")

# 2. _download_all_robust signature and internal logic
old_batch_call = """            batch_results = fetcher.get_batch_ohlcv(batch, interval=interval, period=period, retries=3, range_from=range_from, range_to=range_to, caller=requester)"""

# I need to change how it processes `batch_results`. `batch_results` is now dict[str, MarketData].

old_process = """                    new_df = batch_results.get(sym)
                    cached_df = next((item[1] for item in items if item[0] == sym), None)
                    
                    if isinstance(new_df, ProviderResult):
                        # Treat ProviderResult as missing/failure for this symbol
                        if cached_df is not None and not cached_df.empty:
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                        else:
                            all_data[sym] = new_df
                    elif new_df is not None and not new_df.empty:"""

new_process = """                    md = batch_results.get(sym)
                    cached_df = next((item[1] for item in items if item[0] == sym), None)
                    
                    if md is None or md.dataframe is None:
                        if cached_df is not None and not cached_df.empty:
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                        else:
                            all_data[sym] = None
                        continue
                        
                    new_df = md.dataframe
                    new_report = md.quality_report
                    remote_source = md.source
                    
                    # Cache Decision Engine
                    if cached_df is not None and not cached_df.empty:
                        cache_report = DataQualityValidator.validate(cached_df, period, interval, range_from, range_to)
                        
                        remote_score = (new_report.quality_score if new_report else 0) * SOURCE_RELIABILITY.get(remote_source, 1.0)
                        cache_score = cache_report.quality_score * SOURCE_RELIABILITY.get("Cache", 0.95)
                        
                        logger.debug(f"CACHE_DECISION | Symbol={sym} | RemoteScore={remote_score:.1f} ({remote_source}) | CacheScore={cache_score:.1f}")
                        
                        if remote_score >= cache_score or (not new_report and remote_score == cache_score):
                            # Accept and Merge
                            policy = "APPEND_TO_CACHE" if range_from else "REPLACE_CACHE"
                            
                            if policy == "REPLACE_CACHE" and new_report and cache_report:
                                if new_report.row_count < cache_report.row_count * (1.0 - MAX_HISTORY_SHRINK):
                                    logger.warning(f"Historical regression detected for {sym}. Previous rows={cache_report.row_count}, Incoming={new_report.row_count}")
                        else:
                            # Reject Remote Data
                            logger.info(f"CACHE_DECISION | Action=KEEP_CACHE | Reason=REMOTE_LOWER_QUALITY | Symbol={sym}")
                            cached_df.attrs['is_stale'] = True
                            all_data[sym] = cached_df
                            continue
                            
                    if new_df is not None and not new_df.empty:"""

content = content.replace(old_process, new_process)

with open("app/price_cache.py", "w") as f:
    f.write(content)
print("Patched price_cache.py loop logic")
