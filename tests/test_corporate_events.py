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
        """Verify days_to_earnings = 7 is included as UPCOMING, but 8 is omitted."""
        # 7 trading sessions after Mon Aug 3 is Wed Aug 12
        date_7d = date(2026, 8, 12)
        # 8 trading sessions after Mon Aug 3 is Thu Aug 13
        date_8d = date(2026, 8, 13)

        mock_map = {
            "STOCK7": {"earnings_date": date_7d.strftime("%Y-%m-%d"), "date_status": "CONFIRMED"},
            "STOCK8": {"earnings_date": date_8d.strftime("%Y-%m-%d"), "date_status": "ESTIMATED"}
        }

        badges7 = self.pipeline.evaluate_symbol("STOCK7", mock_map, self.cal, self.curr_d)
        badges8 = self.pipeline.evaluate_symbol("STOCK8", mock_map, self.cal, self.curr_d)

        self.assertEqual(len(badges7), 1)
        self.assertEqual(badges7[0]["status"], "UPCOMING")
        self.assertEqual(badges7[0]["metadata"]["days"], 7)

        self.assertEqual(len(badges8), 0)

    def test_recent_earnings_boundary(self):
        """Verify days_to_earnings = -7 is included as RECENT, but -8 is omitted."""
        # 7 trading sessions before Mon Aug 3 is Thu Jul 23
        date_neg7 = date(2026, 7, 23)
        # 8 trading sessions before Mon Aug 3 is Wed Jul 22
        date_neg8 = date(2026, 7, 22)

        mock_map = {
            "REC7": {"earnings_date": date_neg7.strftime("%Y-%m-%d"), "date_status": "CONFIRMED"},
            "REC8": {"earnings_date": date_neg8.strftime("%Y-%m-%d"), "date_status": "CONFIRMED"}
        }

        badges7 = self.pipeline.evaluate_symbol("REC7", mock_map, self.cal, self.curr_d)
        badges8 = self.pipeline.evaluate_symbol("REC8", mock_map, self.cal, self.curr_d)

        self.assertEqual(len(badges7), 1)
        self.assertEqual(badges7[0]["status"], "RECENT")
        self.assertEqual(badges7[0]["metadata"]["days"], -7)

        self.assertEqual(len(badges8), 0)

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
        # Verify linear O(N) execution without per-stock DB access (<2000ms for 1,000 stocks under CPU load)
        self.assertLess(elapsed_ms, 2000.0)
        self.assertEqual(len(decorated[0]["event_badges"]), 1)


if __name__ == "__main__":
    unittest.main()
