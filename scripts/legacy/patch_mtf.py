lines = open('app/multi_tf_scanner.py').read().splitlines()
in_try_block = False
for i, line in enumerate(lines):
    if line == "            continue" and lines[i+1] == "    # ── Log the funnel so we can see exactly where stocks drop off ────────":
        break
        
    if line == "        try:":
        in_try_block = True
    elif line == "        except Exception as e:":
        in_try_block = False
        
    if in_try_block and line.startswith("            try:") == False and line.startswith("        except") == False and line.startswith("        try:") == False:
        if line.startswith("            ") == False and line.strip() != "":
            lines[i] = "    " + line

open('app/multi_tf_scanner.py', 'w').write('\n'.join(lines) + '\n')
