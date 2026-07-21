import os, sys

lines = open('app/reversal_scanner.py').read().splitlines()

loop_start = -1
for i, line in enumerate(lines):
    if line.strip() == "with MemoryProfiler(\"Process Symbols\"):":
        loop_start = i
        break

loop_end = -1
for i, line in enumerate(lines):
    if line.strip() == "# Insert scan failures via batch":
        loop_end = i
        break

if loop_start != -1 and loop_end != -1:
    
    # Need to remove the original fetch
    fetch_idx = -1
    for i in range(len(lines)):
        if "all_ticker_data = fetch_watchlist_data(watchlist, period=\"1y\", interval=\"1d\")" in lines[i]:
            fetch_idx = i
            break
            
    if fetch_idx != -1:
        lines[fetch_idx] = "        # [VERSION: REV_CHUNK_FIX] Removed full watchlist fetch to chunk below"
        
    # Also need to find and remove: fetched_count = len(all_ticker_data) if all_ticker_data else 0
    for i in range(len(lines)):
        if "fetched_count = len(all_ticker_data) if all_ticker_data else 0" in lines[i]:
            lines[i] = "        # fetched_count calculated below"
        if "if fetched_count < len(watchlist) * 0.70:" in lines[i]:
            lines[i] = "        if False: # Replaced by post-chunk check"

    chunk_setup = [
        "        import os, psutil, gc, time",
        "        process = psutil.Process(os.getpid())",
        "        BATCH_SIZE = int(os.environ.get(\"REVERSAL_FETCH_BATCH_SIZE\", \"50\"))",
        "        ",
        "        total_fetched_count = 0",
        "        logger.info(f\"📥 Processing REVERSAL phase in chunks of {BATCH_SIZE}...\")",
        "",
        "        with MemoryProfiler(\"Process Symbols\"):",
        "            for i in range(0, len(watchlist), BATCH_SIZE):",
        "                batch_start_time = time.time()",
        "                chunk_df = watchlist.iloc[i:i + BATCH_SIZE]",
        "                rss_before = process.memory_info().rss / 1024 / 1024",
        "                ",
        "                all_ticker_data = fetch_watchlist_data(chunk_df, \"1y\", \"1d\")",
        "                if not all_ticker_data:",
        "                    continue",
        "                ",
        "                total_fetched_count += len(all_ticker_data)",
        "                rss_after_fetch = process.memory_info().rss / 1024 / 1024",
        "                ",
        "                for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):"
    ]
    
    for i in range(loop_start + 2, loop_end):
        if lines[i].strip() != "":
            lines[i] = "    " + lines[i]
            
    cleanup_block = [
        "                rss_after_convert = process.memory_info().rss / 1024 / 1024",
        "                del all_ticker_data",
        "                locals().pop('ticker', None)",
        "                gc.collect()",
        "                rss_after_gc = process.memory_info().rss / 1024 / 1024",
        "                elapsed = time.time() - batch_start_time",
        "                logger.info(",
        "                    f\"📊 REVERSAL Batch {i//BATCH_SIZE + 1}/{(len(watchlist) + BATCH_SIZE - 1)//BATCH_SIZE}\\n\"",
        "                    f\"Symbols: {len(chunk_df)}\\n\"",
        "                    f\"Time: {elapsed:.1f} s\\n\"",
        "                    f\"RSS before fetch: {rss_before:.1f} MB\\n\"",
        "                    f\"RSS after fetch: {rss_after_fetch:.1f} MB\\n\"",
        "                    f\"RSS after convert: {rss_after_convert:.1f} MB\\n\"",
        "                    f\"RSS after cleanup: {rss_after_gc:.1f} MB\"",
        "                )",
        "            ",
        "            if total_fetched_count < len(watchlist) * 0.70:",
        "                logger.warning(f\"⚠️ REVERSAL data fetch returned {total_fetched_count}/{len(watchlist)} symbols (70% minimum required). Results may be incomplete.\")",
        "            else:",
        "                logger.info(f\"✅ Successfully fetched {total_fetched_count} symbols for REVERSAL phase\")"
    ]
    
    lines = lines[:loop_start] + chunk_setup + lines[loop_start+2:loop_end] + cleanup_block + lines[loop_end:]

open('app/reversal_scanner.py', 'w').write('\n'.join(lines) + '\n')
