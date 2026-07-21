with open("app/multi_tf_scanner.py", "r") as f:
    content = f.read()

# Add import
content = content.replace(
    "from technical_indicators import apply_indicators",
    "from technical_indicators import apply_indicators\nfrom memory_profiler import MemoryProfiler"
)

# 1H Price Fetch
content = content.replace(
    'ticker_data = fetch_watchlist_data(watchlist, period="60d", interval="1h")',
    '''profiler_fetch1 = MemoryProfiler("1H Price Fetch")
    profiler_fetch1.__enter__()
    try:
        ticker_data = fetch_watchlist_data(watchlist, period="60d", interval="1h")
    finally:
        profiler_fetch1.__exit__(None, None, None)'''
)

# 1H Process Symbols
content = content.replace(
    '''    for idx, row in watchlist.iterrows():''',
    '''    profiler_proc1 = MemoryProfiler("1H Process Symbols")
    profiler_proc1.__enter__()
    for idx, row in watchlist.iterrows():'''
)

content = content.replace(
    '''        f"approved={funnel['approved']}"
    )
            
    return {"fetched": len(ticker_data), "total": len(watchlist), "stale": stale_count, "save_failures": 0}''',
    '''        f"approved={funnel['approved']}"
    )
    profiler_proc1.__exit__(None, None, None)
            
    return {"fetched": len(ticker_data), "total": len(watchlist), "stale": stale_count, "save_failures": 0}'''
)


# MTF Price Fetch
content = content.replace(
    'data_30m = fetch_watchlist_data(pd.DataFrame({"Stock": needs_30m}), period="1mo", interval="30m") if needs_30m else {}',
    '''profiler_fetch2 = MemoryProfiler("MTF Price Fetch")
    profiler_fetch2.__enter__()
    try:
        data_30m = fetch_watchlist_data(pd.DataFrame({"Stock": needs_30m}), period="1mo", interval="30m") if needs_30m else {}
    finally:
        profiler_fetch2.__exit__(None, None, None)'''
)

# MTF Process Symbols
content = content.replace(
    '''    for item in active_items:''',
    '''    profiler_proc2 = MemoryProfiler("MTF Process Symbols")
    profiler_proc2.__enter__()
    for item in active_items:'''
)

content = content.replace(
    '''    logger.info(f"✅ Phase B/C/D Funnel | Armed candidates: {lower_funnel['armed_candidates']} → BB pass: {lower_funnel['bb_pass']} → Armed: {lower_funnel['armed']} | Entry candidates: {lower_funnel['entry_candidates']} → EMA15 pass: {lower_funnel['ema15_pass']} → Ready: {lower_funnel['entry_ready']} | Trigger candidates: {lower_funnel['trigger_candidates']} → Triggered: {lower_funnel['triggered']} | Demoted: {lower_funnel['demoted']}")
        
    return {"fetched": len(needs_30m), "total": len(active_items), "stale": stale_count, "save_failures": db_save_failures}''',
    '''    logger.info(f"✅ Phase B/C/D Funnel | Armed candidates: {lower_funnel['armed_candidates']} → BB pass: {lower_funnel['bb_pass']} → Armed: {lower_funnel['armed']} | Entry candidates: {lower_funnel['entry_candidates']} → EMA15 pass: {lower_funnel['ema15_pass']} → Ready: {lower_funnel['entry_ready']} | Trigger candidates: {lower_funnel['trigger_candidates']} → Triggered: {lower_funnel['triggered']} | Demoted: {lower_funnel['demoted']}")
    profiler_proc2.__exit__(None, None, None)
        
    return {"fetched": len(needs_30m), "total": len(active_items), "stale": stale_count, "save_failures": db_save_failures}'''
)


with open("app/multi_tf_scanner.py", "w") as f:
    f.write(content)
