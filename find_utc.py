import os
import re

directory = 'app/'
pattern1 = re.compile(r'datetime\.now\(\)')
pattern2 = re.compile(r'datetime\.utcnow\(\)')
pattern3 = re.compile(r'pd\.Timestamp\.now\(\)')

for root, dirs, files in os.walk(directory):
    for file in files:
        if file.endswith('.py'):
            path = os.path.join(root, file)
            with open(path, 'r') as f:
                lines = f.readlines()
                for i, line in enumerate(lines):
                    if pattern1.search(line) or pattern2.search(line) or pattern3.search(line):
                        print(f"{path}:{i+1}:{line.strip()}")
