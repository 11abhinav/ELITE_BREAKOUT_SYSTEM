with open("app/multi_tf_scanner.py", "r") as f:
    lines = f.readlines()

# Insert imports
lines.insert(7, "from memory_profiler import MemoryProfiler\n")

# Wrap 1H Price Fetch
for i, line in enumerate(lines):
    if 'ticker_data = fetch_watchlist_data(watchlist, period="60d", interval="1h")' in line:
        lines[i] = '    with MemoryProfiler("1H Price Fetch"):\n        ' + line.lstrip()
        break

# Wrap 1H Process Symbols
for i, line in enumerate(lines):
    if 'for idx, row in watchlist.iterrows():' in line:
        start_idx = i
        break

# Find end of 1H Process Symbols loop
for i in range(start_idx, len(lines)):
    if 'logger.info(' in lines[i] and 'Phase A Funnel:' in lines[i+1]:
        end_idx = i
        break

# Apply indentation
lines[start_idx] = '    with MemoryProfiler("1H Process Symbols"):\n        ' + lines[start_idx].lstrip()
for i in range(start_idx + 1, end_idx):
    if lines[i].strip():
        lines[i] = "    " + lines[i]


# Wrap MTF Price Fetch
for i, line in enumerate(lines):
    if 'data_30m = fetch_watchlist_data(pd.DataFrame({"Stock": needs_30m}), period="1mo", interval="30m") if needs_30m else {}' in line:
        lines[i] = '    with MemoryProfiler("MTF Price Fetch"):\n        ' + line.lstrip()
        break

# Wrap MTF Process Symbols
for i, line in enumerate(lines):
    if 'for item in active_items:' in line:
        start_idx = i
        break

# Find end of MTF Process Symbols loop
for i in range(start_idx, len(lines)):
    if 'return {"fetched": len(needs_30m)' in lines[i]:
        end_idx = i - 2
        break

# Apply indentation
lines[start_idx] = '    with MemoryProfiler("MTF Process Symbols"):\n        ' + lines[start_idx].lstrip()
for i in range(start_idx + 1, end_idx):
    if lines[i].strip():
        lines[i] = "    " + lines[i]

with open("app/multi_tf_scanner.py", "w") as f:
    f.writelines(lines)
