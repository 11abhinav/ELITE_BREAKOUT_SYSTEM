with open("app/data_providers/unified_fetcher.py", "r") as f:
    lines = f.readlines()

for i in range(140, 219): # lines 141 to 219 (0-indexed 140 to 218)
    # The elif statements should be at 16 spaces. They are currently at 12 spaces.
    # The blocks inside should be at 20 spaces. They are currently at 16 spaces.
    # So we just add 4 spaces to every line from 141 to 219!
    if lines[i].strip(): # if not empty
        lines[i] = "    " + lines[i]

with open("app/data_providers/unified_fetcher.py", "w") as f:
    f.writelines(lines)
