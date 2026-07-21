import re

content = open('app/reversal_scanner.py').read()

# 1. Replace the initial fetch
old_fetch = """    # Pulling 1y data to ensure we catch the 52W High correctly
    with MemoryProfiler("Price Fetch"):
        all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")"""
new_fetch = """    # Pulling 1y data to ensure we catch the 52W High correctly
    # [VERSION: REV_CHUNK_FIX] Removed full watchlist fetch to chunk below
    # all_ticker_data is now fetched per-chunk"""
content = content.replace(old_fetch, new_fetch)

# 2. Replace the fetch validation (lines 364-398 roughly)
old_validation = """        fetched_count = len(all_ticker_data) if all_ticker_data else 0
        if fetched_count < len(watchlist) * 0.70:
            # Diagnose the exact reason why APIs are failing
            exact_reason = ""
            try:
                from data_providers.fyers_fetcher import _fyers_circuit_breaker
                from data_provider import _price_provider
                import time
                if _fyers_circuit_breaker.is_open:
                    exact_reason += "Fyers Circuit Breaker OPEN (API rate-limited). "
                if _price_provider.cooldown_until > time.time():
                    exact_reason += f"YFinance Circuit Breaker OPEN (cooldown={int(_price_provider.cooldown_until - time.time())}s). "
            except Exception:
                pass
                
            error_details = exact_reason if exact_reason else "Unknown APIs fail / no cache available"
            logger.warning(f"⚠️ Data Provider returned data for only {fetched_count}/{len(watchlist)} symbols. EXACT REASON: {error_details}")
            
            # [VERSION: REV_FETCH_ABORT_FIX] Gracefully degrade instead of crashing the runner
            if not is_test_mode:
                try:
                    upsert_scanner_health(scanner_name="REVERSAL", status="DEGRADED", error_msg=f"Partial Fetch: {fetched_count}/{len(watchlist)} | {error_details}")
                    from database import insert_notification
                    insert_notification("error", "⚠️ REVERSAL SCAN DEGRADED", f"Data fetched for only {fetched_count}/{len(watchlist)} symbols.\\nReason: {error_details}")
                except Exception:
                    pass
            # Proceed with partial data rather than aborting the entire nightly run
        else:
            logger.info(f"✅ Successfully fetched {fetched_count}/{len(watchlist)} symbols for Reversal scan")
            if not is_test_mode:
                try:
                    upsert_scanner_health(scanner_name="REVERSAL", status="RUNNING", error_msg=None)
                except Exception:
                    pass"""
new_validation = """        # validation moved to end of chunking"""
content = content.replace(old_validation, new_validation)

# 3. Replace the `with MemoryProfiler("Process Symbols"):` block up to `for idx, (_, row)`
old_loop_start = """    with MemoryProfiler("Process Symbols"):
        for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):"""

new_loop_start = """    import os, psutil, gc, time
    process = psutil.Process(os.getpid())
    BATCH_SIZE = int(os.environ.get("REVERSAL_FETCH_BATCH_SIZE", "50"))
    total_fetched_count = 0
    logger.info(f"📥 Processing REVERSAL phase in chunks of {BATCH_SIZE}...")

    with MemoryProfiler("Process Symbols"):
        for i in range(0, len(watchlist), BATCH_SIZE):
            batch_start_time = time.time()
            chunk_df = watchlist.iloc[i:i + BATCH_SIZE]
            rss_before = process.memory_info().rss / 1024 / 1024
            
            all_ticker_data = fetch_watchlist_data(chunk_df, "1y", "1d")
            if not all_ticker_data:
                continue
                
            total_fetched_count += len(all_ticker_data)
            rss_after_fetch = process.memory_info().rss / 1024 / 1024

            for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):"""
content = content.replace(old_loop_start, new_loop_start)

# 4. We need to indent everything inside `for idx, (_, row)` by 4 spaces.
# The loop ends before `# Insert scan failures via batch`.
# We'll split the content, find the boundaries, and indent.
lines = content.split('\n')
start_idx = -1
for i, line in enumerate(lines):
    if line.startswith("            for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):"):
        start_idx = i + 1
        break
        
end_idx = -1
for i, line in enumerate(lines):
    if line.strip() == "# Insert scan failures via batch":
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    for i in range(start_idx, end_idx):
        if lines[i].strip() != "":
            lines[i] = "    " + lines[i]

    cleanup_block = [
        "            rss_after_convert = process.memory_info().rss / 1024 / 1024",
        "            del all_ticker_data",
        "            locals().pop('ticker', None)",
        "            gc.collect()",
        "            rss_after_gc = process.memory_info().rss / 1024 / 1024",
        "            elapsed = time.time() - batch_start_time",
        "            logger.info(",
        "                f\"📊 REVERSAL Batch {i//BATCH_SIZE + 1}/{(len(watchlist) + BATCH_SIZE - 1)//BATCH_SIZE}\\n\"",
        "                f\"Symbols: {len(chunk_df)}\\n\"",
        "                f\"Time: {elapsed:.1f} s\\n\"",
        "                f\"RSS before fetch: {rss_before:.1f} MB\\n\"",
        "                f\"RSS after fetch: {rss_after_fetch:.1f} MB\\n\"",
        "                f\"RSS after convert: {rss_after_convert:.1f} MB\\n\"",
        "                f\"RSS after cleanup: {rss_after_gc:.1f} MB\"",
        "            )",
        "        ",
        "        if total_fetched_count < len(watchlist) * 0.70:",
        "            logger.warning(f\"⚠️ REVERSAL data fetch returned {total_fetched_count}/{len(watchlist)} symbols (70% minimum required). Results may be incomplete.\")",
        "        else:",
        "            logger.info(f\"✅ Successfully fetched {total_fetched_count} symbols for REVERSAL phase\")"
    ]
    lines = lines[:end_idx] + cleanup_block + lines[end_idx:]

    for i in range(end_idx, len(lines)):
        if "len(all_ticker_data)" in lines[i]:
            lines[i] = lines[i].replace("len(all_ticker_data)", "total_fetched_count")

    open('app/reversal_scanner.py', 'w').write('\n'.join(lines))
else:
    print(f"Error: could not find boundaries. start_idx={start_idx}, end_idx={end_idx}")

