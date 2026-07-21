import re

def patch_file(path, fetch_str, proc_str, end_proc_str):
    with open(path, "r") as f:
        lines = f.readlines()
        
    # Insert import
    for i, line in enumerate(lines):
        if line.startswith("from technical_indicators import apply_indicators"):
            lines.insert(i+1, "from memory_profiler import MemoryProfiler\n")
            break

    # Patch fetch block
    for i, line in enumerate(lines):
        if fetch_str in line:
            indent = line[:len(line) - len(line.lstrip())]
            lines[i] = f'{indent}with MemoryProfiler("Price Fetch"):\n{indent}    ' + line.lstrip()
            break
            
    # Patch process block
    for i, line in enumerate(lines):
        if proc_str in line:
            start_idx = i
            indent = line[:len(line) - len(line.lstrip())]
            break
            
    for i in range(start_idx, len(lines)):
        if end_proc_str in lines[i]:
            end_idx = i
            break
            
    lines.insert(start_idx, f'{indent}with MemoryProfiler("Process Symbols"):\n')
    
    for i in range(start_idx + 1, end_idx + 1):
        if lines[i].strip():
            lines[i] = "    " + lines[i]
            
    with open(path, "w") as f:
        f.writelines(lines)

# EOD Scanner
eod_fetch = 'all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")'
eod_proc = 'for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):'
eod_end = '        return total_alerts'
patch_file("app/eod_scanner.py", eod_fetch, eod_proc, eod_end)

# Reversal Scanner
rev_fetch = 'all_ticker_data = fetch_watchlist_data(watchlist, period="1y", interval="1d")'
rev_proc = 'for idx, (_, row) in enumerate(watchlist.iterrows(), start=1):'
rev_end = '        return total_alerts'
patch_file("app/reversal_scanner.py", rev_fetch, rev_proc, rev_end)

