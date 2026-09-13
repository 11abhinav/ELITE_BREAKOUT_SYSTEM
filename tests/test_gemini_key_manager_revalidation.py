# =====================================================================================
# tests/test_gemini_key_manager_revalidation.py
# UNIT TEST SUITE: GEMINI KEY LIVE REVALIDATION & AUTO-UNBLOCKING
# =====================================================================================

import os
import unittest
from unittest.mock import patch, MagicMock

from app.gemini_key_manager import (
    get_active_gemini_key,
    mark_gemini_key_exhausted,
    unblacklist_gemini_key,
    revalidate_single_key_live,
    _is_gemini_key_exhausted,
    _blacklisted_gemini_keys_ram,
    _cache_lock
)

class TestGeminiKeyRevalidation(unittest.TestCase):

    def setUp(self):
        with _cache_lock:
            _blacklisted_gemini_keys_ram.clear()

    def test_01_mark_and_unblacklist_gemini_key(self):
        """Verify mark_gemini_key_exhausted and unblacklist_gemini_key state transitions."""
        test_key = "AIzaSyTestKey_1234567890"
        mark_gemini_key_exhausted(test_key, "Test exhaustion")
        self.assertTrue(_is_gemini_key_exhausted(test_key))

        # Unblacklist
        unblacklist_gemini_key(test_key, "Test recovery")
        self.assertFalse(_is_gemini_key_exhausted(test_key))

    @patch("requests.get")
    def test_02_revalidate_single_key_live(self, mock_get):
        """Verify revalidate_single_key_live returns True on HTTP 200 and False on 429."""
        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_get.return_value = mock_resp_200

        self.assertTrue(revalidate_single_key_live("AIzaSyValidKey_123"))

        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_get.return_value = mock_resp_429

        self.assertFalse(revalidate_single_key_live("AIzaSyExhaustedKey_123"))

    @patch("requests.get")
    @patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyKeyA_111,AIzaSyKeyB_222"})
    def test_03_preflight_probe_auto_recovers_blacklisted_key(self, mock_get):
        """
        Verify that when all keys are blacklisted in cache, get_active_gemini_key probes Google API live,
        finds the working key (HTTP 200), auto-unblocks it, and returns it.
        """
        key_a = "AIzaSyKeyA_111"
        key_b = "AIzaSyKeyB_222"

        # Mark both keys exhausted
        mark_gemini_key_exhausted(key_a, "Quota 429")
        mark_gemini_key_exhausted(key_b, "Quota 429")

        self.assertTrue(_is_gemini_key_exhausted(key_a))
        self.assertTrue(_is_gemini_key_exhausted(key_b))

        # Mock: Key A still fails (429), Key B succeeds (200 OK)
        def mock_fetch(url, headers, timeout):
            resp = MagicMock()
            if key_b in url or headers.get("x-goog-api-key") == key_b:
                resp.status_code = 200
            else:
                resp.status_code = 429
            return resp

        mock_get.side_effect = mock_fetch

        # Call get_active_gemini_key()
        active_key = get_active_gemini_key()

        # Key B should have been recovered and returned!
        self.assertEqual(active_key, key_b)
        self.assertFalse(_is_gemini_key_exhausted(key_b), "Key B must be unblacklisted after live probe success")
        self.assertTrue(_is_gemini_key_exhausted(key_a), "Key A should remain blacklisted")

    @patch("requests.get")
    @patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyKeyA_111,AIzaSyKeyB_222"})
    def test_04_preflight_probe_all_fail_returns_empty(self, mock_get):
        """Verify that if all blacklisted keys genuinely fail the live probe, get_active_gemini_key returns empty string."""
        key_a = "AIzaSyKeyA_111"
        key_b = "AIzaSyKeyB_222"

        mark_gemini_key_exhausted(key_a, "Quota 429")
        mark_gemini_key_exhausted(key_b, "Quota 429")

        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429
        mock_get.return_value = mock_resp_429

        active_key = get_active_gemini_key()
        self.assertEqual(active_key, "")


if __name__ == "__main__":
    unittest.main()
