# =====================================================================================
# tests/test_concall_4q_and_model_priority.py
# UNIT TEST SUITE: 4-QUARTER CONCALL HISTORY CHECKS & GEMINI MODEL PRIORITIZATION
# =====================================================================================

import os
import sys
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from dashboard_server import check_has_concall_in_past_4_quarters
from ai_analyzer import _discover_supported_models, _discovered_models_cache


class TestConcall4QAndModelPriority(unittest.TestCase):

    def setUp(self):
        _discovered_models_cache.clear()

    def test_01_check_has_concall_positive_recent(self):
        """Verify check_has_concall_in_past_4_quarters returns True for transcript within 365 days."""
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        recent_date_str = (now_ist - timedelta(days=60)).strftime("%d-%b-%Y %H:%M:%S")

        sample_announcements = [
            {
                "desc": "Transcript of Conference Call held on Q3 FY25",
                "an_dt": recent_date_str,
                "attchmntText": "Transcript"
            },
            {
                "desc": "Outcome of Board Meeting",
                "an_dt": recent_date_str,
                "attchmntText": "Outcome"
            }
        ]
        self.assertTrue(check_has_concall_in_past_4_quarters(sample_announcements))

    def test_02_check_has_concall_negative_no_transcripts(self):
        """Verify check_has_concall_in_past_4_quarters returns False when no concalls/presentations exist."""
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        recent_date_str = (now_ist - timedelta(days=30)).strftime("%d-%b-%Y %H:%M:%S")

        sample_announcements = [
            {
                "desc": "Outcome of Board Meeting",
                "an_dt": recent_date_str,
                "attchmntText": "Outcome"
            },
            {
                "desc": "Newspaper Publication",
                "an_dt": recent_date_str,
                "attchmntText": "Newspaper"
            }
        ]
        self.assertFalse(check_has_concall_in_past_4_quarters(sample_announcements))

    def test_03_check_has_concall_negative_older_than_4_quarters(self):
        """Verify check_has_concall_in_past_4_quarters returns False when transcripts are older than 365 days."""
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))
        old_date_str = (now_ist - timedelta(days=400)).strftime("%d-%b-%Y %H:%M:%S")

        sample_announcements = [
            {
                "desc": "Transcript of Conference Call",
                "an_dt": old_date_str,
                "attchmntText": "Transcript"
            }
        ]
        self.assertFalse(check_has_concall_in_past_4_quarters(sample_announcements))

    @patch("requests.get")
    def test_04_gemini_model_priority_orders_frontier_models_first(self, mock_get):
        """Verify _discover_supported_models prioritizes 3.x (3.7/3.8/3.0), 2.5-pro, 2.5-flash, 2.0-pro, and 2.0-flash at the top."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "models/gemini-1.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-3.7-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.5-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.0-pro-exp-02-05", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-1.5-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.0-flash-lite", "supportedGenerationMethods": ["generateContent"]},
            ]
        }
        mock_get.return_value = mock_resp

        models = _discover_supported_models("AIzaSyTestPriorityKey")

        # Top models should place 3.7 at #1, followed by 2.5-pro, 2.5-flash, 2.0-pro, 2.0-flash
        self.assertEqual(models[0], "gemini-3.7-pro")
        self.assertEqual(models[1], "gemini-2.5-pro")
        self.assertEqual(models[2], "gemini-2.5-flash")
        self.assertEqual(models[3], "gemini-2.0-pro-exp-02-05")
        self.assertEqual(models[4], "gemini-2.0-flash")
        self.assertEqual(models[5], "gemini-2.0-flash-lite")
        self.assertEqual(models[6], "gemini-1.5-pro")
        self.assertEqual(models[7], "gemini-1.5-flash")


if __name__ == "__main__":
    unittest.main()
