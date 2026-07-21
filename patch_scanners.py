def patch_file(path, replacements):
    with open(path, "r") as f:
        content = f.read()

    # Add import
    content = content.replace(
        "from technical_indicators import apply_indicators",
        "from technical_indicators import apply_indicators\nfrom memory_profiler import MemoryProfiler"
    )
    
    for old, new in replacements:
        content = content.replace(old, new)
        
    with open(path, "w") as f:
        f.write(content)

eod_replacements = [
    (
        'all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")',
        '''profiler_fetch = MemoryProfiler("Price Fetch")\n    profiler_fetch.__enter__()\n    try:\n        all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")\n    finally:\n        profiler_fetch.__exit__(None, None, None)'''
    ),
    (
        '''    for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):''',
        '''    profiler_proc = MemoryProfiler("Process Symbols")\n    profiler_proc.__enter__()\n    for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):'''
    ),
    (
        '''        return total_alerts''',
        '''        profiler_proc.__exit__(None, None, None)\n        return total_alerts'''
    )
]

rev_replacements = [
    (
        'all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")',
        '''profiler_fetch = MemoryProfiler("Price Fetch")\n    profiler_fetch.__enter__()\n    try:\n        all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")\n    finally:\n        profiler_fetch.__exit__(None, None, None)'''
    ),
    (
        '''    for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):''',
        '''    profiler_proc = MemoryProfiler("Process Symbols")\n    profiler_proc.__enter__()\n    for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):'''
    ),
    (
        '''        return total_alerts''',
        '''        profiler_proc.__exit__(None, None, None)\n        return total_alerts'''
    )
]

patch_file("app/eod_scanner.py", eod_replacements)
patch_file("app/reversal_scanner.py", rev_replacements)

