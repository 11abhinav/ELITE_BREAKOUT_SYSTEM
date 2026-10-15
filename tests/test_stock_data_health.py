"""
tests/test_stock_data_health.py
Unit tests verifying the Stock Data Health and Filing Completeness Audit subsystem:
- get_stale_stocks_audit()
- get_fresh_stocks_audit()
- get_stock_filings_detail()
- validate_and_restore_stock()
- Dashboard API endpoints
"""

import pytest
import sys
import time
from pathlib import Path

# Ensure paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import (
    get_stale_stocks_audit,
    get_fresh_stocks_audit,
    get_stock_filings_detail,
    validate_and_restore_stock,
    remove_stale_stock_from_cooloff,
)
from dashboard_server import app, _session_cache


def test_get_stale_stocks_audit_structure():
    stale = get_stale_stocks_audit()
    assert isinstance(stale, list)
    if stale:
        item = stale[0]
        assert "symbol" in item
        assert "company_name" in item
        assert "reason" in item
        assert "missing_data_summary" in item
        assert "days_remaining" in item
        assert item["status"] in ("STALE_COOLOFF", "STALE_SNAPSHOT")


def test_get_fresh_stocks_audit_structure():
    fresh = get_fresh_stocks_audit(limit=10)
    assert isinstance(fresh, list)
    assert len(fresh) > 0
    item = fresh[0]
    assert "symbol" in item
    assert "company_name" in item
    assert "latest_annual_period" in item
    assert "filings_count_in_db" in item
    assert item["status"] == "FRESH"


def test_get_stock_filings_detail():
    fresh = get_fresh_stocks_audit(limit=1)
    assert len(fresh) > 0
    test_sym = fresh[0]["symbol"]

    detail = get_stock_filings_detail(test_sym)
    assert isinstance(detail, dict)
    assert detail["symbol"] == test_sym
    assert "total_filings_count" in detail
    assert "filings" in detail
    assert "canonical_snapshot" in detail
    assert "is_stale" in detail


def test_validate_and_restore_stock():
    fresh = get_fresh_stocks_audit(limit=1)
    assert len(fresh) > 0
    test_sym = fresh[0]["symbol"]

    val = validate_and_restore_stock(test_sym)
    assert isinstance(val, dict)
    assert val["symbol"] == test_sym
    assert "is_data_perfect" in val
    assert "verdict" in val
    assert "missing_metrics" in val


def test_api_stock_data_health_endpoints():
    _session_cache[(999, "test_token_sdh")] = (True, time.time() + 9999)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["user_id"] = 999
            sess["session_token"] = "test_token_sdh"
            sess["role"] = "admin"
            sess["logged_in"] = True

        # 1. Health Audit
        res1 = client.get("/api/admin/stock_data_health")
        assert res1.status_code == 200
        data1 = res1.get_json()
        assert data1["status"] == "success"
        assert "summary" in data1
        assert "stale_stocks" in data1
        assert data1["summary"]["total_universe"] > 0

        # Verify strict mutual exclusivity between stale and fresh stocks
        stale_syms = {s["symbol"] for s in data1["stale_stocks"]}
        fresh_syms = {f["symbol"] for f in data1["fresh_stocks"]}
        overlap = stale_syms.intersection(fresh_syms)
        assert len(overlap) == 0, f"Duplicate symbols found in both stale and fresh: {overlap}"
        assert data1["summary"]["total_universe"] == len(stale_syms) + len(fresh_syms)

        # 2. Stock Filings Detail
        res2 = client.get("/api/admin/stock_filings/INFY")
        assert res2.status_code == 200
        data2 = res2.get_json()
        assert data2["status"] == "success"
        assert data2["data"]["symbol"] == "INFY"

        # 3. On-demand validation
        res3 = client.post("/api/admin/validate_stock", json={"symbol": "INFY"})
        assert res3.status_code == 200
        data3 = res3.get_json()
        assert data3["status"] == "success"
        assert "result" in data3

        # 4. Remove stale stock from cool-off table
        res4 = client.post("/api/admin/remove_stale_stock", json={"symbol": "INFY"})
        assert res4.status_code == 200
        data4 = res4.get_json()
        assert data4["status"] == "success"
        assert "INFY" in data4["message"]


def test_remove_stale_stock_from_cooloff():
    res = remove_stale_stock_from_cooloff("TEST_MOCK_SYM")
    assert isinstance(res, dict)
    assert res["ok"] is True
    assert res["symbol"] == "TEST_MOCK_SYM"
    assert "message" in res

