import json
import glob
import os
from collections import defaultdict

profiling_dir = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/artifacts/profiling"
files = glob.glob(os.path.join(profiling_dir, "*.jsonl"))

# Dictionary to hold durations: {scanner_name: [list of durations]}
scanner_times = defaultdict(list)

for file_path in files:
    # determine scanner name from filename
    basename = os.path.basename(file_path)
    scanner_name = "Unknown"
    if basename.startswith("eod_scanner"):
        scanner_name = "EOD Scanner"
    elif basename.startswith("multi_tf_scanner_run_hourly"):
        scanner_name = "Multi-TF (Hourly Phase)"
    elif basename.startswith("multi_tf_scanner_run_lower_tf"):
        scanner_name = "Multi-TF (Lower TF Phase)"
    elif basename.startswith("reversal_scanner"):
        scanner_name = "Reversal Scanner"

    try:
        with open(file_path, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if 'duration_s' in data:
                        scanner_times[scanner_name].append(data['duration_s'])
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        print(f"Failed to read {file_path}: {e}")

print(f"{'Scanner Phase':<30} | {'Count':<10} | {'Avg (s)':<10} | {'Min (s)':<10} | {'Max (s)':<10}")
print("-" * 75)
for name, times in sorted(scanner_times.items()):
    if not times:
        continue
    avg = sum(times) / len(times)
    min_t = min(times)
    max_t = max(times)
    print(f"{name:<30} | {len(times):<10} | {avg:<10.2f} | {min_t:<10.2f} | {max_t:<10.2f}")
