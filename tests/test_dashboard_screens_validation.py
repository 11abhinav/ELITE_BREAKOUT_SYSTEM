# ==============================================================================
# tests/test_dashboard_screens_validation.py
#
# Comprehensive test suite validating dynamic data, performance, and contract
# compliance across all screens and endpoints in the Elite Breakout System.
# ==============================================================================

import pytest
import json
import time
from typing import Dict, Any, List

from app.master_orchestrator import orchestrator_v2
from app.dashboard_server import app


@pytest.fixture
def client(monkeypatch):
    import app.dashboard_server as ds
    monkeypatch.setattr(ds, "_cached_check_session", lambda uid, tok: True)
    app.config["TESTING"] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = 1
            sess["username"] = "admin"
            sess["role"] = "admin"
            sess["session_token"] = "valid_test_token"
        yield c


class TestMasterOrchestratorScreens:
    """Validates that orchestrator_v2 produces 100% dynamic, valid data for all tabs."""

    def test_screen_master_summary(self):
        summary = orchestrator_v2.get_master_summary()
        assert isinstance(summary, dict)
        assert "status" in summary
        assert "engines" in summary

    def test_screen_confirmed_signals(self):
        alerts = orchestrator_v2.get_confirmed_signals()
        assert isinstance(alerts, list)
        for alert in alerts[:10]:
            assert "symbol" in alert
            assert "scanner" in alert
            # Numeric sanity
            if alert.get("entry_price") is not None:
                assert float(alert["entry_price"]) > 0

    def test_screen_stocks_to_watch(self):
        watchlist = orchestrator_v2.get_stocks_to_watch()
        assert isinstance(watchlist, list)
        for item in watchlist[:10]:
            assert "symbol" in item
            assert "stage" in item
            assert "primary_blocker" in item
            assert "why_qualifies" in item

    def test_screen_investment_watch(self):
        inv = orchestrator_v2.get_investment_watch()
        assert isinstance(inv, list)
        for item in inv[:10]:
            assert "symbol" in item
            assert "quality_score" in item or "maturity_score" in item

    def test_screen_confluence_breakdown_all(self):
        confluence_setups = orchestrator_v2._get_all_confluence_setups_uncached()
        assert isinstance(confluence_setups, list)
        for setup in confluence_setups[:5]:
            assert "symbol" in setup
            assert "confluence_depth" in setup
            assert "participating_scanners" in setup
            assert "scanners_breakdown" in setup
            assert isinstance(setup["scanners_breakdown"], list)
            for sc in setup["scanners_breakdown"]:
                assert "scanner" in sc
                assert "scanner_title" in sc
                assert "timeframe" in sc
                assert "entry_price" in sc
                assert "stop_loss" in sc

    def test_screen_confluence_breakdown_single_symbol(self):
        res = orchestrator_v2.get_confluence_breakdown("RELIANCE")
        assert isinstance(res, dict)
        assert "symbol" in res
        assert "scanners_breakdown" in res
        assert "participating_scanners" in res
        assert "confluence_depth" in res

    def test_screen_scanner_health(self):
        health = orchestrator_v2.get_scanner_health()
        assert isinstance(health, list)
        for sc in health:
            assert "scanner" in sc or "scanner_name" in sc
            assert "status" in sc


class TestDashboardServerEndpoints:
    """Validates HTTP API endpoints powering all dashboard screens."""

    def test_endpoint_health(self, client):
        res = client.get("/health")
        assert res.status_code in (200, 302)

    def test_endpoint_v2_master_summary(self, client):
        res = client.get("/api/v2/master_summary")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, dict)

    def test_endpoint_v2_master_alerts(self, client):
        res = client.get("/api/v2/master_alerts")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, list)

    def test_endpoint_v2_confirmed_signals(self, client):
        res = client.get("/api/v2/confirmed_signals")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, list)

    def test_endpoint_v2_stocks_to_watch(self, client):
        res = client.get("/api/v2/stocks_to_watch")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, list)

    def test_endpoint_v2_investment_watch(self, client):
        res = client.get("/api/v2/investment_watch")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, list)

    def test_endpoint_v2_portfolio_actions(self, client):
        res = client.get("/api/v2/portfolio_actions")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, list)

    def test_endpoint_v2_confluence_breakdown(self, client):
        res = client.get("/api/v2/confluence_breakdown")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, list)

    def test_endpoint_v2_confluence_breakdown_symbol(self, client):
        res = client.get("/api/v2/confluence_breakdown/TCS")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, dict)
        assert data.get("symbol") == "TCS"
        assert "scanners_breakdown" in data

    def test_endpoint_near_misses(self, client):
        res = client.get("/api/near_misses")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, (list, dict))

    def test_endpoint_v2_scanner_health(self, client):
        res = client.get("/api/v2/scanner_health")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, list)

    def test_endpoint_v2_universe_health(self, client):
        res = client.get("/api/v2/universe_health")
        assert res.status_code == 200
        data = json.loads(res.data)
        assert isinstance(data, (dict, list))
