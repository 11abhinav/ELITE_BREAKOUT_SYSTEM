import re
with open('app/database.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith('    '):
        new_lines.append(line[4:])
    else:
        new_lines.append(line)

with open('app/database.py', 'w') as f:
    f.writelines(new_lines)
