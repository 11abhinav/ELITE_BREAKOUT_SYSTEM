import os, sys

lines = open('app/eod_scanner.py').read().splitlines()

# 1. Remove the ThreadPoolExecutor that fetches both delivery and prices
start_idx = -1
end_idx = -1
for i, line in enumerate(lines):
    if line.strip() == "pool = ThreadPoolExecutor(max_workers=2)":
        start_idx = i - 1 # include the comment before it
    if line.strip() == "logger.info(f\"✅ Successfully fetched {fetched_count}/{len(watchlist)} symbols for EOD scan\")":
        end_idx = i + 1
        break

if start_idx != -1 and end_idx != -1:
    replacement = [
        "        # [VERSION: EOD_CHUNK_FIX] Removed ThreadPoolExecutor for price fetch. Fetch delivery synchronously.",
        "        try:",
        "            delivery_map = fetch_delivery_data(ist_now.date())",
        "        except Exception as e:",
        "            logger.error(f\"❌ Delivery fetch failed: {e}\")",
        "            delivery_map = {}",
    ]
    lines = lines[:start_idx] + replacement + lines[end_idx:]

# 2. Wrap the processing loop with a chunk loop
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
    chunk_setup = [
        "        import os, psutil, gc, time",
        "        process = psutil.Process(os.getpid())",
        "        BATCH_SIZE = int(os.environ.get(\"EOD_FETCH_BATCH_SIZE\", \"50\"))",
        "        ",
        "        total_fetched_count = 0",
        "        logger.info(f\"📥 Processing EOD phase in chunks of {BATCH_SIZE}...\")",
        "",
        "        with MemoryProfiler(\"Process Symbols\"):",
        "            for i in range(0, len(watchlist), BATCH_SIZE):",
        "                batch_start_time = time.time()",
        "                chunk_df = watchlist.iloc[i:i + BATCH_SIZE]",
        "                rss_before = process.memory_info().rss / 1024 / 1024",
        "                ",
        "                all_ticker_data = fetch_watchlist_data(chunk_df, \"2y\", \"1d\")",
        "                if not all_ticker_data:",
        "                    continue",
        "                ",
        "                total_fetched_count += len(all_ticker_data)",
        "                rss_after_fetch = process.memory_info().rss / 1024 / 1024",
        "                ",
        "                for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):"
    ]
    
    # Indent the loop body
    for i in range(loop_start + 2, loop_end):
        if lines[i].strip() != "":
            lines[i] = "    " + lines[i]
            
    # add the cleanup block at the end of the chunk loop (inside the chunk loop, outside the row loop)
    cleanup_block = [
        "                rss_after_convert = process.memory_info().rss / 1024 / 1024",
        "                del all_ticker_data",
        "                locals().pop('ticker', None)",
        "                gc.collect()",
        "                rss_after_gc = process.memory_info().rss / 1024 / 1024",
        "                elapsed = time.time() - batch_start_time",
        "                logger.info(",
        "                    f\"📊 EOD Batch {i//BATCH_SIZE + 1}/{(len(watchlist) + BATCH_SIZE - 1)//BATCH_SIZE}\\n\"",
        "                    f\"Symbols: {len(chunk_df)}\\n\"",
        "                    f\"Time: {elapsed:.1f} s\\n\"",
        "                    f\"RSS before fetch: {rss_before:.1f} MB\\n\"",
        "                    f\"RSS after fetch: {rss_after_fetch:.1f} MB\\n\"",
        "                    f\"RSS after convert: {rss_after_convert:.1f} MB\\n\"",
        "                    f\"RSS after cleanup: {rss_after_gc:.1f} MB\"",
        "                )",
        "            ",
        "            # Check if we fetched enough data overall",
        "            if total_fetched_count < len(watchlist) * 0.70:",
        "                logger.warning(f\"⚠️ EOD data fetch returned {total_fetched_count}/{len(watchlist)} symbols (70% minimum required). EOD results may be incomplete.\")",
        "            else:",
        "                logger.info(f\"✅ Successfully fetched {total_fetched_count} symbols for EOD phase\")"
    ]
    
    lines = lines[:loop_start] + chunk_setup + lines[loop_start+2:loop_end] + cleanup_block + lines[loop_end:]

open('app/eod_scanner.py', 'w').write('\n'.join(lines) + '\n')
