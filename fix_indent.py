lines = open('app/reversal_scanner.py').read().splitlines()

loop_start = -1
for i, line in enumerate(lines):
    if line.startswith("        with MemoryProfiler(\"Process Symbols\"):"):
        loop_start = i
        break

if loop_start != -1:
    lines[loop_start] = "    with MemoryProfiler(\"Process Symbols\"):"
    
    # We want to dedent everything from `for i in range(...)` down to the `symbol = "UNKNOWN"` part?
    # No, the base level of the function is 4 spaces.
    # So `with MemoryProfiler` should be 4 spaces.
    
    for i in range(loop_start + 1, len(lines)):
        if "    process = psutil.Process" in lines[i]:
            continue # already processed
            
        # The inner loop `for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):` is at 12 spaces if `with` is at 4 spaces and `for i in range` is at 8 spaces.
        if lines[i].startswith("                for idx, (_, row)"):
            lines[i] = "            for idx, (_, row) in enumerate(chunk_df.iterrows(), start=1):"
            continue
            
        if lines[i].startswith("                symbol = \"UNKNOWN\""):
            # indent everything from here to end of chunk loop by 16 spaces
            for j in range(i, len(lines)):
                if lines[j].strip() == "":
                    continue
                if lines[j].startswith("                rss_after_convert"):
                    # This marks the end of the symbol loop and the start of the chunk cleanup
                    # Chunk cleanup should be at 12 spaces
                    break
                lines[j] = "                " + lines[j].lstrip()
            break

open('app/reversal_scanner.py', 'w').write('\n'.join(lines) + '\n')
