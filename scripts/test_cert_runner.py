#!/usr/bin/env python3
import os
import sys

print("STEP 1: Starting test script", flush=True)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "app")
for p in [REPO_ROOT, APP_DIR]:
    if p not in sys.path:
        sys.path.insert(0, p)

print(f"STEP 2: Paths added. REPO_ROOT={REPO_ROOT}", flush=True)

try:
    from app.trading_calendar import default_trading_calendar, is_trading_day
    print("STEP 3: Imported from app.trading_calendar", flush=True)
except Exception as e:
    print(f"STEP 3 FAILED: {e}", flush=True)

print("STEP 4: Testing trading calendar query", flush=True)
res = default_trading_calendar.is_trading_day("2026-09-25")
print(f"STEP 5: is_trading_day(2026-09-25) = {res}", flush=True)

print("STEP 6: Completed successfully!", flush=True)
