#!/usr/bin/env python3
"""
scripts/run_11_scanners_master_replay.py
Master runner that invokes replay simulations for the 6 new scanner families:
1. Wealth Engine (tests/simulate_wealth_replay.py)
2. Multibagger Engine (tests/simulate_multibagger_replay.py)
3. Daily Builder (tests/simulate_daily_builder_replay.py)
4. Short Covering EOD (tests/simulate_short_covering_replay.py)
5. Multi-TF 5M Monitor (tests/simulate_multitf_5m_replay.py)
6. Technical Ahat (tests/simulate_technical_replay.py)
"""

import sys
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TESTS_DIR = os.path.join(_REPO_ROOT, "tests")
_APP_DIR = os.path.join(_REPO_ROOT, "app")
for _d in (_REPO_ROOT, _TESTS_DIR, _APP_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from simulate_wealth_replay import run_wealth_replay
from simulate_multibagger_replay import run_multibagger_replay
from simulate_daily_builder_replay import run_builder_replay
from simulate_short_covering_replay import run_short_covering_replay
from simulate_multitf_5m_replay import run_m5_replay
from simulate_technical_replay import run_tech_replay


def run_all():
    print("=" * 90)
    print("MASTER 11-SCANNER REPLAY PIPELINE (6 ADDITIONAL ENGINES)")
    print("=" * 90)

    print("\n>>> [1/6] Running Wealth Engine Replay...")
    run_wealth_replay(step=5)

    print("\n>>> [2/6] Running Multibagger Engine Replay...")
    run_multibagger_replay(step=5)

    print("\n>>> [3/6] Running Daily Builder Replay...")
    run_builder_replay(step=5)

    print("\n>>> [4/6] Running Short Covering EOD Replay...")
    run_short_covering_replay(step=5)

    print("\n>>> [5/6] Running Multi-TF 5M Monitor Replay...")
    run_m5_replay(step=2)

    print("\n>>> [6/6] Running Technical Ahat Replay...")
    run_tech_replay(step=5)

    print("\n" + "=" * 90)
    print("ALL 6 SCANNER REPLAYS COMPLETE AND EXPORTED TO REPORTS/")
    print("=" * 90)


if __name__ == "__main__":
    run_all()
