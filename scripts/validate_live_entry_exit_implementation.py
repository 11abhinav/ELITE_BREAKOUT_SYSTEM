#!/usr/bin/env python3
"""
scripts/validate_live_entry_exit_implementation.py
==================================================
Wrapper validation script executing the master validation battery.
Produces 'PASS' and exits 0 only when all mandatory checks pass.
"""

import sys
import os

BASE_DIR = "/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM"
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.validate_live_fundamental_entry_exit import main

if __name__ == "__main__":
    main()
