import re

with open('app/main.py', 'r') as f:
    content = f.read()

# Replace RESTARTABLE_THREADS
content = content.replace('"EODScanner":         run_eod_scanner,', '"EveningScanners":    run_evening_scanners,')
content = content.replace('    "ReversalScanner":    run_reversal_scanner,', '')

# We will write run_evening_scanners which orchestrates both
# The logic for eod and reversal should still be their respective functions, but they don't loop waiting for bhavcopy.
# Actually, wait. It's easier if I just look at the code first.
