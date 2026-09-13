# =====================================================================================
# tests/test_universal_stock_intelligence_dossier.py
# UNIT & INTEGRATION TEST SUITE: UNIVERSAL CORPORATE & ANALYST INTELLIGENCE DOSSIER
# =====================================================================================

import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from corporate_event_classifier import classify_announcement_text, evaluate_deterministic_hard_risk_gate, extract_inr_amount_cr
from analyst_consensus_engine import calculate_analyst_consensus
from ai_analyzer import analyze_full_corporate_dossier
from database import (
    init_db,
    save_raw_source_document, save_corporate_event, get_corporate_events_for_symbol,
    save_analyst_research_report, get_analyst_research_for_symbol,
    save_company_intelligence_snapshot, get_intelligence_snapshot_at_time,
    save_company_intelligence_current, get_current_company_intelligence,
    upsert_intelligence_ingestion_health, get_all_intelligence_ingestion_health
)


class TestUniversalStockIntelligenceDossier(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def test_01_deterministic_classification_and_epistemic_evidence_class(self):
        """Verify deterministic classification, epistemic classes, and source tiers."""
        # 1. Exchange Order Win -> FACT / Tier 1
        res1 = classify_announcement_text("Receipt of Order worth Rs. 4,200 Crores from ONGC", "", "NSE")
        self.assertEqual(res1["category"], "ORDER_WIN")
        self.assertEqual(res1["evidence_class"], "FACT")
        self.assertEqual(res1["source_tier"], "SOURCE_TIER_1")
        self.assertEqual(res1["order_value_inr_cr"], 4200.0)
        self.assertEqual(res1["order_quality"], "HIGH")
        self.assertFalse(res1["is_hard_risk"])

        # 2. Credit Rating Downgrade -> VERIFIED_ASSESSMENT / Tier 2
        res2 = classify_announcement_text("CRISIL Downgrades long term debt rating to default", "", "CRISIL")
        self.assertEqual(res2["category"], "CREDIT_DOWNGRADE")
        self.assertEqual(res2["evidence_class"], "VERIFIED_ASSESSMENT")
        self.assertEqual(res2["source_tier"], "SOURCE_TIER_2")
        self.assertTrue(res2["is_hard_risk"])

        # 3. Statutory Auditor Resignation -> FACT / Tier 1 / Severe Hard Risk
        res3 = classify_announcement_text("Resignation of Statutory Auditor BSR & Co with immediate effect", "", "NSE")
        self.assertEqual(res3["category"], "AUDITOR_RESIGNATION")
        self.assertEqual(res3["evidence_class"], "FACT")
        self.assertTrue(res3["is_hard_risk"])

    def test_02_deterministic_hard_risk_safety_gate_precedence(self):
        """Verify deterministic safety gate immediately quarantines default/auditor risks without LLM."""
        events = [
            {
                "category": "ORDER_WIN",
                "source_tier": "SOURCE_TIER_1",
                "status": "OPEN",
                "headline": "Bagged Rs 500 Cr order"
            },
            {
                "category": "DEBT_DEFAULT",
                "source_tier": "SOURCE_TIER_1",
                "status": "OPEN",
                "headline": "Delay in payment of debt interest servicing"
            }
        ]
        gate_status, hazards = evaluate_deterministic_hard_risk_gate(events)
        self.assertEqual(gate_status, "QUARANTINE")
        self.assertEqual(len(hazards), 1)

        # When risk is resolved, gate should PASS
        events[1]["status"] = "RESOLVED"
        gate_status2, hazards2 = evaluate_deterministic_hard_risk_gate(events)
        self.assertEqual(gate_status2, "PASS")
        self.assertEqual(len(hazards2), 0)

    def test_03_analyst_consensus_and_dispersion_engine(self):
        """Verify analyst consensus metrics, target revision momentum, and dispersion calculation."""
        reports = [
            {
                "broker_firm": "Motilal Oswal",
                "analyst_name": "Analyst A",
                "report_date": "2026-09-10",
                "rating": "BUY",
                "target_price": 1450.0,
                "target_change_pct": 15.0
            },
            {
                "broker_firm": "ICICI Direct",
                "analyst_name": "Analyst B",
                "report_date": "2026-09-08",
                "rating": "BUY",
                "target_price": 1400.0,
                "target_change_pct": 10.0
            },
            {
                "broker_firm": "Kotak Securities",
                "analyst_name": "Analyst C",
                "report_date": "2026-09-05",
                "rating": "HOLD",
                "target_price": 1380.0,
                "target_change_pct": 0.0
            }
        ]

        consensus = calculate_analyst_consensus(reports, current_price=1200.0)
        self.assertEqual(consensus["total_covering_analysts"], 3)
        self.assertEqual(consensus["buy_count"], 2)
        self.assertEqual(consensus["hold_count"], 1)
        self.assertEqual(consensus["sell_count"], 0)
        self.assertEqual(consensus["median_target_price"], 1400.0)
        self.assertAlmostEqual(consensus["upside_pct"], 16.67, places=1)
        self.assertLess(consensus["target_dispersion_pct"], 10.0)
        self.assertGreaterEqual(consensus["analyst_revision_score"], 60)

    @patch("gemini_key_manager.get_active_gemini_key", return_value="")
    def test_04_ai_analyzer_fallback_dossier_synthesis(self, mock_key):
        """Verify analyze_full_corporate_dossier functions deterministically when LLM is unavailable."""
        timeline_events = [
            {
                "category": "ORDER_WIN",
                "headline": "Bagged Rs 1,000 Cr EPC Order",
                "source_name": "NSE",
                "source_tier": "SOURCE_TIER_1",
                "evidence_class": "FACT",
                "materiality_score": 9.0,
                "status": "OPEN",
                "event_date": "2026-09-10T10:00:00+05:30"
            }
        ]
        analyst_consensus = {
            "total_covering_analysts": 5,
            "buy_count": 4,
            "hold_count": 1,
            "sell_count": 0,
            "median_target_price": 1500.0,
            "target_dispersion_pct": 6.5,
            "analyst_revision_score": 80
        }

        dossier = analyze_full_corporate_dossier(
            symbol="TESTSTOCK",
            timeline_events=timeline_events,
            analyst_consensus=analyst_consensus,
            concall_text="Management projects 20% volume growth."
        )

        self.assertEqual(dossier["symbol"], "TESTSTOCK")
        self.assertEqual(dossier["hard_gate_status"], "PASS")
        self.assertGreaterEqual(dossier["catalyst_score"], 50)
        self.assertIn("positive_catalysts", dossier)

    def test_05_end_to_end_stock_dossier_multi_surface_consistency(self):
        """
        [CRITICAL SPECIFICATION REQUIREMENT]:
        Verify that Watchlist, Alert, Technical Scanner, Wealth, and Search resolve to the
        exact same symbol -> current snapshot -> event timeline -> evidence chain.
        """
        sym = "CONSISTENCY_TEST_STOCK"
        now_iso = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()

        # 1. Save raw source and structured event
        doc_id = save_raw_source_document(
            document_hash="hash_consistency_test_001",
            source_type="EXCHANGE_FILING",
            source_name="NSE",
            source_tier="SOURCE_TIER_1",
            evidence_class="FACT",
            publication_date=now_iso,
            source_url="https://nseindia.com/doc1",
            headline="Strategic Greenfield Plant Commissioned"
        )
        self.assertGreater(doc_id, 0)

        save_corporate_event(
            symbol=sym,
            document_id=doc_id,
            event_group_id=f"GRP_{sym}_001",
            event_version=1,
            supersedes_event_id=None,
            revision_type="INITIAL",
            evidence_class="FACT",
            category="CAPEX_EXPANSION",
            event_date=now_iso,
            headline="Strategic Greenfield Plant Commissioned",
            materiality_score=8.0,
            status="OPEN"
        )

        # 2. Save snapshot & materialized current state
        snap_id = save_company_intelligence_snapshot(
            symbol=sym,
            as_of_time=now_iso,
            catalyst_score=75,
            risk_score=10,
            governance_score=8.5,
            management_credibility_score=8.0,
            analyst_revision_score=70,
            analyst_dispersion_pct=5.0,
            net_score=60,
            net_verdict="MODERATE_POSITIVE",
            hard_gate_status="PASS",
            active_open_hazards_count=0,
            contradiction_detected=False,
            executive_summary="Commissioning provides 25% revenue expansion headroom.",
            evidence_chain=[{"fact": "Plant Commissioned", "source": "NSE", "evidence_class": "FACT", "materiality": 8.0}],
            consensus_summary={"total_covering_analysts": 3}
        )
        self.assertGreater(snap_id, 0)

        save_company_intelligence_current(
            symbol=sym,
            latest_snapshot_id=snap_id,
            net_verdict="MODERATE_POSITIVE",
            hard_gate_status="PASS",
            catalyst_score=75,
            risk_score=10,
            governance_score=8.5,
            management_credibility_score=8.0,
            analyst_revision_score=70,
            summary_payload={"symbol": sym, "net_verdict": "MODERATE_POSITIVE", "catalyst_score": 75, "hard_gate_status": "PASS"}
        )

        # 3. Simulate multi-surface retrieval: Watchlist, Alerts, Technical Scanner, Wealth, Portfolio
        watchlist_view = get_current_company_intelligence(sym)
        alerts_view = get_current_company_intelligence(sym)
        technical_scanner_view = get_current_company_intelligence(sym)
        wealth_engine_view = get_current_company_intelligence(sym)
        portfolio_view = get_current_company_intelligence(sym)

        # All 5 surfaces MUST resolve to the exact same snapshot id, scores, and verdict
        self.assertEqual(watchlist_view["latest_snapshot_id"], snap_id)
        self.assertEqual(alerts_view["latest_snapshot_id"], snap_id)
        self.assertEqual(technical_scanner_view["latest_snapshot_id"], snap_id)
        self.assertEqual(wealth_engine_view["latest_snapshot_id"], snap_id)
        self.assertEqual(portfolio_view["latest_snapshot_id"], snap_id)

        self.assertEqual(watchlist_view["net_verdict"], "MODERATE_POSITIVE")
        self.assertEqual(wealth_engine_view["catalyst_score"], 75)
        self.assertEqual(technical_scanner_view["hard_gate_status"], "PASS")

        # 4. Point-in-time historical query test
        past_time = (datetime.now(ZoneInfo("Asia/Kolkata")) - timedelta(days=30)).isoformat()
        past_snap = get_intelligence_snapshot_at_time(sym, as_of_time=past_time)
        self.assertIsNone(past_snap, "Historical point-in-time query must never leak future snapshots")


if __name__ == "__main__":
    unittest.main()
