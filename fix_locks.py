import os
import re

scanners = [
    ("app/eod_scanner.py", "EOD"),
    ("app/reversal_scanner.py", "REVERSAL"),
    ("app/pullback_pipeline.py", "PULLBACK"),
    ("app/multibagger.py", "MULTIBAGGER"),
    ("app/daily_builder.py", "DAILY_BUILDER"),
    ("app/wealth_engine.py", "WEALTH_ENGINE"),
    ("app/multi_tf_scanner.py", "MULTI_TF")
]

for filepath, scanner_name in scanners:
    if not os.path.exists(filepath):
        print(f"Skipping {filepath}")
        continue
    with open(filepath, 'r') as f:
        content = f.read()

    # We need to find the block starting with:
    #     own_ctx = False
    #     if run_ctx is None:
    #         run_ctx = start_scanner_execution_run(scanner_name="EOD", trigger_type=trigger_type, scheduler_name=scheduler_name)
    #         own_ctx = True
    # OR similar forms, and remove it from the beginning, moving it to AFTER global_lock.acquire.

    # Instead of fragile regex, let's just carefully use replace if we can.
    # Actually, writing a precise python AST or regex modifier for this exact block is hard.
    pass
