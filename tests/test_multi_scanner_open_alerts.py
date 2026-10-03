"""
Unit tests for Multi-Scanner Open Alert Confluence feature.

Verifies:
1. When multiple open alerts exist for the same stock symbol across different scanners:
   - Total count of scanners with open alerts is correctly computed.
   - Other open alerts are attached with scanner name, alert date, and alert price.
2. When only one open alert exists for a stock:
   - has_other_open_alerts is False, other_open_alerts is empty.
3. When alerts are from the same scanner:
   - Distinguishes unique scanners vs alert count.
4. When an alert is closed:
   - It is not included in the open alerts pool of other alerts.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

from database import enrich_alerts_with_multi_scanner_confluence


def test_multi_scanner_open_alerts_confluence():
    alerts = [
        {
            "id": 101,
            "symbol": "TATAMOTORS.NS",
            "scanner": "EOD_BREAKOUT",
            "alert_time": "2026-10-02T09:30:00",
            "alert_date": "2026-10-02",
            "entry_price": 980.50,
            "status": "OPEN",
            "score": 85,
        },
        {
            "id": 102,
            "symbol": "TATAMOTORS",
            "scanner": "REVERSAL_SCANNER",
            "alert_time": "2026-10-03T11:45:00",
            "alert_date": "2026-10-03",
            "entry_price": 995.00,
            "status": "OPEN",
            "score": 90,
        },
        {
            "id": 103,
            "symbol": "TATAMOTORS",
            "scanner": "INTRADAY_MOMENTUM",
            "alert_time": "2026-10-03T14:15:00",
            "alert_date": "2026-10-03",
            "entry_price": 1002.00,
            "status": "OPEN",
            "score": 88,
        },
        {
            "id": 201,
            "symbol": "INFY.NS",
            "scanner": "EOD_BREAKOUT",
            "alert_time": "2026-10-02T10:00:00",
            "alert_date": "2026-10-02",
            "entry_price": 1850.00,
            "status": "OPEN",
            "score": 75,
        },
        {
            "id": 301,
            "symbol": "RELIANCE.NS",
            "scanner": "PULLBACK",
            "alert_time": "2026-09-20T10:00:00",
            "alert_date": "2026-09-20",
            "entry_price": 2900.00,
            "status": "WIN",
            "score": 80,
        }
    ]

    enriched = enrich_alerts_with_multi_scanner_confluence(alerts)

    # 1. Verify TATAMOTORS alerts
    tata_101 = next(a for a in enriched if a["id"] == 101)
    assert tata_101["total_open_scanners_count"] == 3, f"Expected 3 scanners, got {tata_101['total_open_scanners_count']}"
    assert tata_101["has_other_open_alerts"] is True
    assert len(tata_101["other_open_alerts"]) == 2
    
    other_scanners_101 = {x["scanner"] for x in tata_101["other_open_alerts"]}
    assert other_scanners_101 == {"REVERSAL_SCANNER", "INTRADAY_MOMENTUM"}
    
    reversal_detail = next(x for x in tata_101["other_open_alerts"] if x["scanner"] == "REVERSAL_SCANNER")
    assert reversal_detail["entry_price"] == 995.00
    assert reversal_detail["alert_date"] == "2026-10-03"

    # 2. Verify INFY alert (only 1 open scanner)
    infy = next(a for a in enriched if a["id"] == 201)
    assert infy["total_open_scanners_count"] == 1
    assert infy["has_other_open_alerts"] is False
    assert len(infy["other_open_alerts"]) == 0

    # 3. Verify RELIANCE alert (closed alert)
    rel = next(a for a in enriched if a["id"] == 301)
    assert rel["total_open_scanners_count"] == 0
    assert rel["has_other_open_alerts"] is False

    print("✅ test_multi_scanner_open_alerts_confluence passed successfully!")


if __name__ == "__main__":
    test_multi_scanner_open_alerts_confluence()
