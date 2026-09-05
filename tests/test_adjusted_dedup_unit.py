import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

# Add app to path
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app"))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from database import save_alert_if_new

def test_adjusted_friday_alert_dedup_suppresses_duplicate():
    """
    Scenario 1: Alert already persisted for source_trading_date (Friday).
    Re-running on Saturday/Sunday must return inserted=False with Duplicate reason.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # Mock prior_adjusted_alert query finding an existing alert
    # Row: (id, alert_date, alert_time, entry_price, status, scanner, breakout_type, src_date)
    existing_row = (101, date(2026, 9, 4), "2026-09-04 15:30:00+05:30", 642.10, "OPEN", "PULLBACK", "PULLBACK", date(2026, 9, 4))
    mock_cur.fetchone.return_value = existing_row

    IST = ZoneInfo("Asia/Kolkata")
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S+05:30")

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="ELGIEQUIP",
        breakout_type="PULLBACK",
        alert_time=now_str,
        scanner="PULLBACK",
        category="PULLBACK",
        entry_price=642.10,
        stop_loss=620.0,
        target_1=680.0,
        score=85,
        source_trading_date=date(2026, 9, 4),
        conn=mock_conn
    )

    assert inserted is False, f"Expected inserted=False for existing adjusted alert, got {inserted}"
    assert "Duplicate" in reason, f"Expected Duplicate in reason, got '{reason}'"
    print("✅ test_adjusted_friday_alert_dedup_suppresses_duplicate passed!")


def test_adjusted_friday_alert_dedup_allows_new_setup():
    """
    Scenario 2: No prior alert exists for this setup on Friday or in prior history.
    Must proceed to INSERT and return inserted=True.
    """
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur

    # First fetchone: prior_adjusted_alert check -> None (no duplicate)
    # Second fetchone: existing_alert (active OPEN position check) -> None (no open trade)
    # Third fetchone: INSERT INTO alerts ... RETURNING id -> (202,)
    mock_cur.fetchone.side_effect = [None, None, (202,)]

    IST = ZoneInfo("Asia/Kolkata")
    now_str = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S+05:30")

    inserted, reason, cap, shares = save_alert_if_new(
        symbol="NEW_SETUP_STOCK",
        breakout_type="PULLBACK",
        alert_time=now_str,
        scanner="PULLBACK",
        category="PULLBACK",
        entry_price=450.0,
        stop_loss=430.0,
        target_1=490.0,
        score=88,
        source_trading_date=date(2026, 9, 4),
        conn=mock_conn
    )

    assert inserted is True, f"Expected inserted=True for brand new setup, got {inserted} ({reason})"
    print("✅ test_adjusted_friday_alert_dedup_allows_new_setup passed!")


if __name__ == "__main__":
    test_adjusted_friday_alert_dedup_suppresses_duplicate()
    test_adjusted_friday_alert_dedup_allows_new_setup()
    print("🎉 ALL UNIT TESTS PASSED!")
