"""
tests/test_non_market_boot.py — Unit test for non-market hours server restart catch-up execution.
"""

import pytest
import time
from unittest.mock import MagicMock, patch
from app.main import run_all_seven_scanners_non_market_boot


def test_non_market_hours_boot_triggers_all_scanners_in_grid_order(monkeypatch):
    """Verifies that run_all_seven_scanners_non_market_boot executes all scanners in Health Dashboard grid order."""
    executed_scanners = []

    def mock_trigger_daily_builder(*args, **kwargs):
        executed_scanners.append("DAILY_BUILDER")

    def mock_trigger_multi_tf(*args, **kwargs):
        executed_scanners.append("MULTI_TF")

    def mock_trigger_accumulation(*args, **kwargs):
        executed_scanners.append("ACCUMULATION")

    def mock_trigger_eod(*args, **kwargs):
        executed_scanners.append("EOD")

    def mock_trigger_reversal(*args, **kwargs):
        executed_scanners.append("REVERSAL")

    def mock_trigger_pullback(*args, **kwargs):
        executed_scanners.append("PULLBACK")

    def mock_trigger_wealth_engine(*args, **kwargs):
        executed_scanners.append("Wealth Engine")

    def mock_trigger_multibagger(*args, **kwargs):
        executed_scanners.append("MULTIBAGGER")

    monkeypatch.setattr("app.main.block_until_watchlist_ready", lambda: None)
    monkeypatch.setattr("app.main._trigger_daily_builder", mock_trigger_daily_builder)
    monkeypatch.setattr("app.main._trigger_multi_tf", mock_trigger_multi_tf)
    monkeypatch.setattr("app.main._trigger_accumulation", mock_trigger_accumulation)
    monkeypatch.setattr("app.main._trigger_eod", mock_trigger_eod)
    monkeypatch.setattr("app.main._trigger_reversal", mock_trigger_reversal)
    monkeypatch.setattr("app.main._trigger_pullback", mock_trigger_pullback)
    monkeypatch.setattr("app.main._trigger_wealth_engine", mock_trigger_wealth_engine)
    monkeypatch.setattr("app.main._trigger_multibagger", mock_trigger_multibagger)
    monkeypatch.setattr("time.sleep", lambda secs: None)

    # Trigger non-market hours boot
    run_all_seven_scanners_non_market_boot()

    # Wait briefly for daemon thread to complete
    time.sleep(0.5)

    assert len(executed_scanners) == 8
    assert executed_scanners == [
        "DAILY_BUILDER",
        "MULTI_TF",
        "ACCUMULATION",
        "EOD",
        "REVERSAL",
        "PULLBACK",
        "Wealth Engine",
        "MULTIBAGGER"
    ]
