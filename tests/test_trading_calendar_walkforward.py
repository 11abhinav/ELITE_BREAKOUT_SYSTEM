#!/usr/bin/env python3
"""
tests/test_trading_calendar_walkforward.py
=============================================================================
UNIT TEST: CALENDAR-VERIFIED WALK-FORWARD EXIT TIMESTAMP ENGINE
Tests that exit_timestamp is computed by strictly walking forward the official
NSE trading calendar (skipping weekends and official exchange holidays) by
holding_period_bars_or_days sessions from entry_timestamp.
=============================================================================
"""

import sys
import os
from datetime import datetime, date, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "app"))
sys.path.insert(0, BASE_DIR)

from trading_calendar import default_trading_calendar


def compute_calendar_exit_timestamp(entry_timestamp: str, holding_period_days: int) -> str:
    """
    Computes exit timestamp by walking forward the official NSE trading calendar
    skipping weekends and official exchange holidays.
    """
    # Parse entry date
    clean_date_str = entry_timestamp.split(" ")[0].split("T")[0]
    curr_date = datetime.strptime(clean_date_str, "%Y-%m-%d").date()

    if holding_period_days <= 0:
        return f"{curr_date} 15:30:00 IST"

    days_advanced = 0
    test_date = curr_date

    while days_advanced < holding_period_days:
        test_date += timedelta(days=1)
        if default_trading_calendar.is_trading_day(test_date):
            days_advanced += 1

    return f"{test_date} 15:30:00 IST"


def test_5_known_trades_calendar_walkforward():
    """
    Tests 5 known trade setups with calendar-verified exit dates across weekends and holidays.
    """
    test_cases = [
        {
            "id": "Trade 1 (Weekend + Independence Day)",
            "entry": "2026-08-14 09:15:00 IST",  # Friday before Aug 15
            "holding": 1,
            "expected_exit": "2026-08-17 15:30:00 IST"  # Monday
        },
        {
            "id": "Trade 2 (Republic Day Holiday Jan 26)",
            "entry": "2026-01-21 09:15:00 IST",  # Wednesday
            "holding": 4,
            "expected_exit": "2026-01-28 15:30:00 IST"  # Wednesday (Jan 22=1, Jan 23=2, Jan 26 Holiday, Jan 27=3, Jan 28=4)
        },
        {
            "id": "Trade 3 (Holi Holiday Mar 10)",
            "entry": "2026-03-06 09:15:00 IST",  # Friday
            "holding": 3,
            "expected_exit": "2026-03-12 15:30:00 IST"  # Thu (Mon Mar 9 is 1, Mar 10 Holi, Wed Mar 11 is 2, Thu Mar 12 is 3)
        },
        {
            "id": "Trade 4 (Ganesh Chaturthi Sep 14)",
            "entry": "2026-09-08 09:15:00 IST",  # Tuesday
            "holding": 5,
            "expected_exit": "2026-09-16 15:30:00 IST"  # Wed (Sep 9=1, 10=2, 11=3, Sep 14 Holiday, Sep 15=4, Sep 16=5)
        },
        {
            "id": "Trade 5 (14-Day Holding across multiple weekends & holiday)",
            "entry": "2026-08-03 09:15:00 IST",  # Monday
            "holding": 14,
            "expected_exit": "2026-08-21 15:30:00 IST"  # Friday (14 trading days later)
        }
    ]

    print("\n--- RUNNING TRADING CALENDAR WALK-FORWARD VERIFICATION ---")
    all_passed = True
    for tc in test_cases:
        actual_exit = compute_calendar_exit_timestamp(tc["entry"], tc["holding"])
        passed = (actual_exit == tc["expected_exit"])
        if not passed:
            all_passed = False
            print(f"❌ {tc['id']}: FAIL | Expected {tc['expected_exit']} but got {actual_exit}")
        else:
            print(f"✅ {tc['id']}: PASS | Entry {tc['entry']} + {tc['holding']} days -> Exit {actual_exit}")

    assert all_passed, "Trading calendar walk-forward verification failed!"
    print("🎯 ALL 5 TEST CASES PASSED PERFECTLY!\n")


if __name__ == "__main__":
    test_5_known_trades_calendar_walkforward()
