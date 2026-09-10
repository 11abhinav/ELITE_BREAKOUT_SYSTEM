#!/usr/bin/env python3
"""
scripts/run_all_replays_fast.py
Runs the historical replay simulations for the 6 new scanner families:
1. Wealth Engine
2. Multibagger Engine
3. Daily Builder
4. Short Covering EOD
5. Multi-TF 5M Monitor
6. Technical Ahat Scanner
"""

import subprocess
import sys
import os

scripts = [
    "tests/simulate_wealth_replay.py",
    "tests/simulate_multibagger_replay.py",
    "tests/simulate_daily_builder_replay.py",
    "tests/simulate_short_covering_replay.py",
    "tests/simulate_multitf_5m_replay.py",
    "tests/simulate_technical_replay.py",
]

for s in scripts:
    print(f"\n=======================================================")
    print(f"RUNNING: {s}")
    print(f"=======================================================")
    cmd = ["./venv/bin/python", "-u", s]
    res = subprocess.run(cmd, capture_output=True, text=True)
    print(res.stdout)
    if res.stderr:
        print("ERRORS / WARNINGS:", res.stderr[-500:])
    print(f"Return code: {res.returncode}")

print("\nALL 6 REPLAYS COMPLETED.")
