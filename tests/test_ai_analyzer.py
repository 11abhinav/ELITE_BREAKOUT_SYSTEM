"""
tests/test_ai_analyzer.py
=========================
Unit tests for AI Concall Analyzer (app/ai_analyzer.py).
Validates:
  1. Header injection with 'x-goog-api-key' for 'AQ.' and 'AIza' keys.
  2. Dynamic Gemini model discovery via /v1beta/models with proper priority sorting.
  3. Non-swallowed error observability on 404/NOT_FOUND responses.
  4. Resilient multi-provider fallback to OpenAI (gpt-4o-mini).
"""

import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from ai_analyzer import (
    _try_gemini_model,
    _try_openai_model,
    _discover_supported_models,
    analyze_concall_text,
    _discovered_models_cache
)


class TestAIAnalyzer(unittest.TestCase):

    def setUp(self):
        _discovered_models_cache.clear()

    @patch("ai_analyzer.requests.post")
    def test_gemini_header_injection_with_aq_key(self, mock_post):
        """Verifies x-goog-api-key header and Content-Type are sent to Google."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps({"management_confidence": 7, "guidance_delta": "Positive"})}
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = fake_response

        aq_key = "AQ.TestAuthKeySecret123"
        result = _try_gemini_model("gemini-2.0-flash", aq_key, "Sample earnings concall transcript")

        self.assertEqual(result.get("management_confidence"), 7)
        self.assertEqual(result.get("model_used"), "gemini-2.0-flash")

        # Verify header presence
        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        headers = kwargs.get("headers", {})
        self.assertIn("x-goog-api-key", headers)
        self.assertEqual(headers["x-goog-api-key"], aq_key)
        self.assertEqual(headers.get("Content-Type"), "application/json")

    @patch("ai_analyzer.requests.get")
    def test_dynamic_gemini_model_discovery(self, mock_get):
        """Verifies dynamic model discovery queries /v1beta/models and sorts by capability."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "models": [
                {"name": "models/gemini-1.5-pro", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/gemini-2.0-flash", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding-001", "supportedGenerationMethods": ["embedContent"]},
                {"name": "models/gemini-2.0-flash-lite", "supportedGenerationMethods": ["generateContent"]}
            ]
        }
        mock_get.return_value = fake_response

        discovered = _discover_supported_models("AQ.SampleKey")

        self.assertNotIn("embedding-001", discovered)
        self.assertEqual(discovered[0], "gemini-2.0-flash")
        self.assertEqual(discovered[1], "gemini-2.0-flash-lite")
        self.assertEqual(discovered[2], "gemini-1.5-pro")

    @patch("ai_analyzer.requests.post")
    def test_openai_fallback_handler(self, mock_post):
        """Verifies OpenAI gpt-4o-mini is invoked with Authorization Bearer header."""
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"management_confidence": 6, "guidance_delta": "Stable"})
                    }
                }
            ]
        }
        mock_post.return_value = fake_response

        openai_key = "sk-proj-sample123456789"
        res = _try_openai_model(openai_key, "Test concall text")

        self.assertEqual(res.get("management_confidence"), 6)
        self.assertEqual(res.get("model_used"), "gpt-4o-mini")

        _, kwargs = mock_post.call_args
        headers = kwargs.get("headers", {})
        self.assertEqual(headers.get("Authorization"), f"Bearer {openai_key}")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-proj-fallback123"})
    @patch("gemini_key_manager.get_active_gemini_key", return_value="AQ.FailingKey")
    @patch("ai_analyzer._try_gemini_model", side_effect=Exception("API Error (404): NOT_FOUND"))
    @patch("ai_analyzer._try_openai_model")
    def test_full_fallback_chain_gemini_to_openai(self, mock_openai, mock_gemini, mock_key):
        """Verifies that when all Gemini models fail with 404, fallback to OpenAI triggers seamlessly."""
        mock_openai.return_value = {
            "management_confidence": 8,
            "guidance_delta": "Upgraded guidance",
            "model_used": "gpt-4o-mini",
            "key_used": "sk-p...e123"
        }

        # Text >= 100 characters to pass validation
        sample_text = "Good afternoon everyone and welcome to the Q1 earnings conference call. Our revenue grew by 24% year on year."
        result = analyze_concall_text(sample_text)

        self.assertEqual(result.get("management_confidence"), 8)
        self.assertEqual(result.get("model_used"), "gpt-4o-mini")
        mock_openai.assert_called_once()


if __name__ == "__main__":
    unittest.main()
