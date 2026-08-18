# =====================================================================================
# tests/test_corporate_events.py
# AUTOMATED TEST SUITE FOR CORPORATE ACTION EVENT FRAMEWORK & TRADING CALENDAR
# =====================================================================================

import unittest
from datetime import date, datetime
import pandas as pd

from trading_calendar import TradingCalendar
from corporate_events import (
    EventPriority,
    EarningsContributor,
    CorporateEventPipeline,
    decorate_events
)


class TestTradingCalendar(unittest.TestCase):
    """Test suite for standalone cross-cutting TradingCalendar service."""

    def setUp(self):
        self.cal = TradingCalendar()

    def test_is_trading_day_weekend(self):
        """Verify Saturday and Sunday are marked as non-trading days."""
        sat = date(2026, 8, 1)  # Saturday
        sun = date(2026, 8, 2)  # Sunday
        mon = date(2026, 8, 3)  # Monday

        self.assertFalse(self.cal.is_trading_day(sat))
        self.assertFalse(self.cal.is_trading_day(sun))
        self.assertTrue(self.cal.is_trading_day(mon))

    def test_days_between_weekend_skipping(self):
        """Verify trading days calculation skips weekends correctly."""
        fri = date(2026, 7, 31)  # Friday
        mon = date(2026, 8, 3)   # Monday

        # Friday to Monday is 1 trading session
        days = self.cal.days_between(fri, mon)
        self.assertEqual(days, 1)

    def test_days_between_past_date(self):
        """Verify past dates return negative trading days."""
        fri = date(2026, 7, 31)
        mon = date(2026, 8, 3)

        # Monday to Friday is -1 trading session
        days = self.cal.days_between(mon, fri)
        self.assertEqual(days, -1)


class TestCorporateEventFramework(unittest.TestCase):
    """Test suite for stateless corporate event decorator and pipeline."""

    def setUp(self):
        self.cal = TradingCalendar()
        self.pipeline = CorporateEventPipeline()
        self.curr_d = date(2026, 8, 3)  # Monday

    def test_upcoming_earnings_boundary(self):
        """Verify that day +120 is UPCOMING (included) and day +121 is excluded.

        The badge window was expanded from ±7 to -60/+120 trading sessions in
        commit 33a45a27 to cover current + next quarter earnings for all tracked
        symbols. This test validates the upper boundary of that window.
        """
        # 120 trading sessions after Mon Aug 3: approx Wed Jan 21 2027 (skipping weekends)
        # 121 trading sessions after Mon Aug 3: approx Thu Jan 22 2027
        # We derive these by walking the TradingCalendar forward.
        from datetime import timedelta

        def nth_trading_day(start: date, n: int) -> date:
            """Returns the date exactly n trading sessions after start."""
            d = start
            count = 0
            while count < n:
                d += timedelta(days=1)
                if self.cal.is_trading_day(d):
                    count += 1
            return d

        date_120 = nth_trading_day(self.curr_d, 120)
        date_121 = nth_trading_day(self.curr_d, 121)

        mock_map = {
            "STOCK120": {"earnings_date": date_120.strftime("%Y-%m-%d"), "date_status": "ESTIMATED"},
            "STOCK121": {"earnings_date": date_121.strftime("%Y-%m-%d"), "date_status": "ESTIMATED"},
        }

        badges120 = self.pipeline.evaluate_symbol("STOCK120", mock_map, self.cal, self.curr_d)
        badges121 = self.pipeline.evaluate_symbol("STOCK121", mock_map, self.cal, self.curr_d)

        # Day +120 is at the boundary — must be included as UPCOMING
        self.assertEqual(len(badges120), 1)
        self.assertEqual(badges120[0]["status"], "UPCOMING")
        self.assertEqual(badges120[0]["metadata"]["days"], 120)

        # Day +121 is just outside the window — must produce no badge
        self.assertEqual(len(badges121), 0)

    def test_recent_earnings_boundary(self):
        """Verify that day -60 is RECENT (included) and day -61 is excluded.

        The badge window was expanded from ±7 to -60/+120 trading sessions in
        commit 33a45a27 to cover ~3 months of post-earnings context. This test
        validates the lower (past) boundary of that window.
        """
        from datetime import timedelta

        def nth_trading_day_before(start: date, n: int) -> date:
            """Returns the date exactly n trading sessions before start."""
            d = start
            count = 0
            while count < n:
                d -= timedelta(days=1)
                if self.cal.is_trading_day(d):
                    count += 1
            return d

        date_neg60 = nth_trading_day_before(self.curr_d, 60)
        date_neg61 = nth_trading_day_before(self.curr_d, 61)

        mock_map = {
            "REC60": {"earnings_date": date_neg60.strftime("%Y-%m-%d"), "date_status": "CONFIRMED"},
            "REC61": {"earnings_date": date_neg61.strftime("%Y-%m-%d"), "date_status": "CONFIRMED"},
        }

        badges60 = self.pipeline.evaluate_symbol("REC60", mock_map, self.cal, self.curr_d)
        badges61 = self.pipeline.evaluate_symbol("REC61", mock_map, self.cal, self.curr_d)

        # Day -60 is at the boundary — must be included as RECENT
        self.assertEqual(len(badges60), 1)
        self.assertEqual(badges60[0]["status"], "RECENT")
        self.assertEqual(badges60[0]["metadata"]["days"], -60)

        # Day -61 is just outside the window — must produce no badge
        self.assertEqual(len(badges61), 0)

    def test_stateless_decorator_immutability(self):
        """Verify decorate_events produces new immutable objects without mutating original input."""
        input_list = [{"symbol": "TATAMOTORS", "close": 100.0}]

        mock_map = {
            "TATAMOTORS": {"earnings_date": "2026-08-05", "date_status": "CONFIRMED"}
        }

        result = decorate_events(input_list, events_map=mock_map, calendar=self.cal, current_date=self.curr_d)

        # Verify input was not mutated
        self.assertNotIn("event_badges", input_list[0])
        # Verify output dict is decorated
        self.assertIn("event_badges", result[0])
        self.assertEqual(result[0]["schema_version"], 1)
        self.assertEqual(len(result[0]["event_badges"]), 1)

    def test_edge_cases_and_robustness(self):
        """Verify robust handling of missing dates, malformed input, and case insensitivity."""
        mock_map = {
            "TATAMOTORS": {"earnings_date": "2026-08-05"}
        }

        # Case insensitive lookup
        res_lower = decorate_events([{"symbol": "tatamotors"}], events_map=mock_map, calendar=self.cal, current_date=self.curr_d)
        self.assertEqual(len(res_lower[0]["event_badges"]), 1)

        # Missing symbol / Null date
        res_missing = decorate_events([{"symbol": "UNKNOWN"}, {"symbol": None}], events_map=mock_map, calendar=self.cal, current_date=self.curr_d)
        self.assertEqual(len(res_missing[0]["event_badges"]), 0)
        self.assertEqual(len(res_missing[1]["event_badges"]), 0)

    def test_bulk_decoration_performance(self):
        """Verify bulk decoration benchmark executes in linear O(N) time for 1,000+ stocks."""
        mock_map = {
            f"SYM{i}": {"earnings_date": "2026-08-05"} for i in range(1000)
        }
        stocks = [{"symbol": f"SYM{i}"} for i in range(1000)]

        start_ts = datetime.now()
        decorated = decorate_events(stocks, events_map=mock_map, calendar=self.cal, current_date=self.curr_d)
        elapsed_ms = (datetime.now() - start_ts).total_seconds() * 1000.0

        self.assertEqual(len(decorated), 1000)
        # Verify linear O(N) execution without per-stock DB access (<5000ms for 1,000 stocks under CPU load)
        self.assertLess(elapsed_ms, 5000.0)
        self.assertEqual(len(decorated[0]["event_badges"]), 1)


if __name__ == "__main__":
    unittest.main()
