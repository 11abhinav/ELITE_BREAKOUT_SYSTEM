"""
tests/short_covering/test_sc_data_health.py

Unit + integration tests for SCDataHealthGate.

Coverage matrix (all must pass before production promotion):
  ┌─────────────────────────────────────────────────────────────┐
  │ GROUP A  Decision matrix — all 9 core rows                  │
  │ GROUP B  OI validation — data_valid vs movement_valid       │
  │ GROUP C  Probe deduplication invariant                      │
  │ GROUP D  Systemic-data threshold guard                      │
  │ GROUP E  Scanner early-return on BLOCKED                    │
  └─────────────────────────────────────────────────────────────┘

Run:
    pytest tests/short_covering/test_sc_data_health.py -v
"""

import sys
import os
import unittest
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

# ── Path bootstrap (allow running from repo root without install) ─────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.short_covering.sc_data_health import (
    # enums
    ProviderStatus,
    SCDataHealth,
    CacheStatus,
    # dataclasses
    OIValidResult,
    ProviderHealthResult,
    ParquetCacheResult,
    SCDataHealthResult,
    # helpers under test
    _validate_oi_series,
    _select_probe_symbols,
    _PROVIDER_DECISION_MATRIX,
    # gate
    SCDataHealthGate,
    SYSTEMIC_DATA_THRESHOLD,
    MIN_SYSTEMIC_SAMPLE,
    PROBE_COUNT,
)

import pandas as pd
import numpy as np

IST = ZoneInfo("Asia/Kolkata")


# ══════════════════════════════════════════════════════════════════════════════
# GROUP A — Decision matrix: all 9 core provider-state rows
# ══════════════════════════════════════════════════════════════════════════════
class TestProviderDecisionMatrix(unittest.TestCase):
    """
    The matrix is the single source of truth.
    Every cell must produce the exact documented outcome.
    """

    MATRIX_CASES = [
        # (fyers_key,  upstox_key,  expected_outcome)
        ("GREEN",    "GREEN",    SCDataHealth.GREEN),
        ("GREEN",    "RED",      SCDataHealth.DEGRADED_REDUNDANCY),
        ("RED",      "GREEN",    SCDataHealth.DEGRADED_REDUNDANCY),
        ("GREEN",    "DEGRADED", SCDataHealth.DEGRADED),
        ("DEGRADED", "GREEN",    SCDataHealth.DEGRADED),
        ("DEGRADED", "DEGRADED", SCDataHealth.DEGRADED),
        ("DEGRADED", "RED",      SCDataHealth.DEGRADED),
        ("RED",      "DEGRADED", SCDataHealth.DEGRADED),
        ("RED",      "RED",      SCDataHealth.BLOCKED),
    ]

    def test_all_9_core_rows_present_in_matrix(self):
        for f, u, expected in self.MATRIX_CASES:
            with self.subTest(fyers=f, upstox=u):
                actual = _PROVIDER_DECISION_MATRIX.get((f, u))
                self.assertIsNotNone(
                    actual,
                    f"Matrix missing key ({f!r}, {u!r})"
                )
                self.assertEqual(
                    actual, expected,
                    f"Matrix({f},{u}) = {actual} — expected {expected}"
                )

    def test_skipped_rows_present(self):
        """SKIPPED must be handled — it must not fall through to the default."""
        skipped_keys = [
            ("SKIPPED", "GREEN"),
            ("SKIPPED", "RED"),
            ("SKIPPED", "DEGRADED"),
            ("GREEN",   "SKIPPED"),
            ("RED",     "SKIPPED"),
            ("DEGRADED","SKIPPED"),
            ("SKIPPED", "SKIPPED"),
        ]
        for key in skipped_keys:
            with self.subTest(key=key):
                self.assertIn(key, _PROVIDER_DECISION_MATRIX,
                              f"SKIPPED row {key} missing from matrix")

    def test_matrix_drives_assess_aggregator(self):
        """
        assess() must use the matrix — mock both providers and verify the
        returned status matches the matrix for every core row.
        """
        gate = SCDataHealthGate.__new__(SCDataHealthGate)
        gate._provider_check = MagicMock()
        gate._parquet_check  = MagicMock()
        gate._parquet_check.classify.return_value = ParquetCacheResult(
            symbol="RELIANCE", parquet_path="/fake/RELIANCE.parquet",
            exists=True, status=CacheStatus.FRESH
        )

        for f_key, u_key, expected in self.MATRIX_CASES:
            with self.subTest(fyers=f_key, upstox=u_key):
                f_res = ProviderHealthResult(
                    provider="FYERS",
                    status=ProviderStatus(f_key if f_key != "BLOCKED" else "RED"),
                )
                u_res = ProviderHealthResult(
                    provider="UPSTOX",
                    status=ProviderStatus(u_key if u_key != "BLOCKED" else "RED"),
                )
                # Map BLOCKED back (BLOCKED is not a ProviderStatus)
                if f_key in ProviderStatus._value2member_map_:
                    f_res.status = ProviderStatus(f_key)
                if u_key in ProviderStatus._value2member_map_:
                    u_res.status = ProviderStatus(u_key)

                gate._provider_check.probe_fyers.return_value  = f_res
                gate._provider_check.probe_upstox.return_value = u_res

                result = gate.assess(
                    target_date=date(2025, 9, 15),
                    probe_symbols=["RELIANCE"],
                )
                self.assertEqual(
                    result.status, expected,
                    f"assess() with Fyers={f_key}, Upstox={u_key} → {result.status}, "
                    f"expected {expected}. Reason: {result.reason}"
                )


# ══════════════════════════════════════════════════════════════════════════════
# GROUP B — OI validation: data_valid vs movement_valid boundary
# ══════════════════════════════════════════════════════════════════════════════
class TestOIValidation(unittest.TestCase):
    """
    _validate_oi_series must enforce the architectural boundary:
      data_valid   — gate-blocking (health layer)
      movement_valid — informational only (signal layer)

    Flat OI is valid data; it is NOT a gate-blocking condition.
    """

    # ── data_valid = True cases ────────────────────────────────────────────────
    def test_normal_series_data_valid(self):
        s = pd.Series([1_000_000, 1_050_000, 1_100_000, 1_080_000])
        r = _validate_oi_series(s)
        self.assertTrue(r.data_valid, r.data_reason)
        self.assertTrue(r.movement_valid, r.movement_reason)

    def test_flat_oi_data_valid_movement_false(self):
        """CRITICAL: flat OI must NOT block the health gate."""
        s = pd.Series([500_000] * 10)
        r = _validate_oi_series(s)
        self.assertTrue(
            r.data_valid,
            f"Flat OI wrongly blocked health gate: {r.data_reason}"
        )
        self.assertFalse(
            r.movement_valid,
            "Flat OI should have movement_valid=False"
        )
        self.assertIn("flat", r.movement_reason.lower(),
                      "movement_reason should describe flat OI")

    def test_two_distinct_values_movement_valid(self):
        s = pd.Series([800_000, 800_001])
        r = _validate_oi_series(s)
        self.assertTrue(r.data_valid)
        self.assertTrue(r.movement_valid)

    # ── data_valid = False cases ───────────────────────────────────────────────
    def test_empty_series_not_valid(self):
        r = _validate_oi_series(pd.Series([], dtype=float))
        self.assertFalse(r.data_valid)

    def test_none_not_valid(self):
        r = _validate_oi_series(None)
        self.assertFalse(r.data_valid)

    def test_non_numeric_not_valid(self):
        r = _validate_oi_series(pd.Series(["a", "b", "c"]))
        self.assertFalse(r.data_valid)
        self.assertIn("non-numeric", r.data_reason.lower())

    def test_insufficient_obs_not_valid(self):
        """Single non-NaN value is below OI_MIN_OBSERVATIONS=2."""
        r = _validate_oi_series(pd.Series([1_000_000, np.nan]))
        self.assertFalse(r.data_valid)
        self.assertIn("insufficient", r.data_reason.lower())

    def test_inf_values_not_valid(self):
        r = _validate_oi_series(pd.Series([1_000_000, np.inf, 1_050_000]))
        self.assertFalse(r.data_valid)
        self.assertIn("non-finite", r.data_reason.lower())

    def test_negative_values_not_valid(self):
        r = _validate_oi_series(pd.Series([1_000_000, -1, 1_050_000]))
        self.assertFalse(r.data_valid)
        self.assertIn("negative", r.data_reason.lower())

    def test_all_zero_not_valid(self):
        """All-zero is placeholder data — not real OI."""
        r = _validate_oi_series(pd.Series([0.0, 0.0, 0.0]))
        self.assertFalse(r.data_valid)
        self.assertIn("zero", r.data_reason.lower())

    def test_nan_only_not_valid(self):
        r = _validate_oi_series(pd.Series([np.nan, np.nan, np.nan]))
        self.assertFalse(r.data_valid)

    # ── OIValidResult field contract ───────────────────────────────────────────
    def test_result_never_has_old_field_names(self):
        """Guard against regression to the old .valid/.reason field names."""
        r = _validate_oi_series(pd.Series([1_000_000, 1_050_000]))
        self.assertTrue(hasattr(r, "data_valid"),    "data_valid field missing")
        self.assertTrue(hasattr(r, "data_reason"),   "data_reason field missing")
        self.assertTrue(hasattr(r, "movement_valid"), "movement_valid field missing")
        self.assertFalse(hasattr(r, "valid"),   "stale .valid field still present")
        self.assertFalse(hasattr(r, "reason"),  "stale .reason field still present")
        self.assertFalse(hasattr(r, "has_change"), "stale .has_change field still present")


# ══════════════════════════════════════════════════════════════════════════════
# GROUP C — Probe deduplication invariant
# ══════════════════════════════════════════════════════════════════════════════
class TestProbeSymbolDeduplication(unittest.TestCase):
    """
    Invariant: len(probe_symbols) == min(PROBE_COUNT, available_symbols)
               and all elements are unique.
    SC candidate must not create a duplicate.
    """

    def _call_with_universe(self, universe, sc_candidate=None):
        """
        Patch fno_universe_manager and the DB to inject a controlled environment.
        """
        mock_mgr = MagicMock()
        mock_mgr.get_fno_symbols.return_value = universe

        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__  = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = (sc_candidate,) if sc_candidate else None
        mock_conn.cursor.return_value = mock_cur

        with patch("app.short_covering.sc_data_health.fno_universe_manager",
                   mock_mgr, create=True), \
             patch("app.short_covering.sc_data_health.get_connection",
                   return_value=mock_conn, create=True):
            return _select_probe_symbols(PROBE_COUNT)

    def test_no_duplicates_without_sc_candidate(self):
        universe = [f"SYM{i:03d}" for i in range(50)]
        probe, sc_cand, sc_included = self._call_with_universe(universe)
        self.assertEqual(len(probe), len(set(probe)),
                         f"Duplicates in probe: {probe}")
        self.assertEqual(len(probe), min(PROBE_COUNT, len(universe)))

    def test_no_duplicates_when_sc_candidate_already_in_base(self):
        """SC candidate that matches a base-sample symbol must not be duplicated."""
        universe = [f"SYM{i:03d}" for i in range(50)]
        # Evenly-spaced step = 50//5 = 10, so base = [SYM000,010,020,030,040]
        # Inject the first base symbol as the SC candidate
        first_base = universe[0]  # SYM000
        probe, sc_cand, sc_included = self._call_with_universe(universe, sc_candidate=first_base)
        self.assertEqual(len(probe), len(set(probe)),
                         f"Duplicate SC candidate not deduplicated: {probe}")
        self.assertEqual(len(probe), PROBE_COUNT)
        # sc_candidate was a duplicate — it should not be marked as included
        self.assertFalse(sc_included,
                         "sc_candidate_included should be False when it is a duplicate of a base symbol")

    def test_sc_candidate_included_when_unique(self):
        universe = [f"SYM{i:03d}" for i in range(50)]
        sc_candidate = "UNIQUE_CANDIDATE"
        probe, sc_cand, sc_included = self._call_with_universe(universe, sc_candidate=sc_candidate)
        self.assertEqual(len(probe), len(set(probe)))
        self.assertEqual(len(probe), PROBE_COUNT)
        self.assertIn(sc_candidate, probe)
        self.assertTrue(sc_included)
        self.assertEqual(sc_cand, sc_candidate)

    def test_small_universe_does_not_exceed_available(self):
        """Universe smaller than PROBE_COUNT — probe must not exceed universe size."""
        universe = ["HDFC", "INFY"]
        probe, _, _ = self._call_with_universe(universe)
        self.assertEqual(len(probe), len(set(probe)))
        self.assertLessEqual(len(probe), len(universe))

    def test_empty_universe_returns_empty_gracefully(self):
        """Empty universe must not raise — returns empty probe gracefully."""
        mock_mgr = MagicMock()
        mock_mgr.get_fno_symbols.return_value = []
        mock_conn = MagicMock()
        mock_cur  = MagicMock()
        mock_cur.__enter__ = lambda s: s
        mock_cur.__exit__  = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = None
        mock_conn.cursor.return_value = mock_cur
        with patch("app.short_covering.sc_data_health.fno_universe_manager",
                   mock_mgr, create=True), \
             patch("app.short_covering.sc_data_health.get_connection",
                   return_value=mock_conn, create=True):
            probe, sc_cand, sc_included = _select_probe_symbols(PROBE_COUNT)
        self.assertEqual(len(probe), 0, "Empty universe should produce empty probe")
        self.assertIsNone(sc_cand)
        self.assertFalse(sc_included)


# ══════════════════════════════════════════════════════════════════════════════
# GROUP D — Systemic-data threshold guard
# ══════════════════════════════════════════════════════════════════════════════
class TestSystemicDataThreshold(unittest.TestCase):
    """
    is_systemic_data_failure() must enforce both conditions simultaneously:
      (1) DATA_INSUFFICIENT / total >= 0.90
      (2) total >= MIN_SYSTEMIC_SAMPLE (=20)
    """

    def test_below_min_sample_never_systemic(self):
        """<20 candidates + 100% bad data → NOT systemic (avoid false positives)."""
        for n in range(0, MIN_SYSTEMIC_SAMPLE):
            with self.subTest(total=n):
                result = SCDataHealthGate.is_systemic_data_failure(
                    data_insufficient_count=n,
                    total_candidates=n,
                )
                self.assertFalse(
                    result,
                    f"Wrongly classified as systemic with total={n} (< {MIN_SYSTEMIC_SAMPLE})"
                )

    def test_exactly_min_sample_at_threshold_is_systemic(self):
        """Exactly 20 candidates, exactly 90% bad → SYSTEMIC."""
        n   = MIN_SYSTEMIC_SAMPLE          # 20
        bad = int(n * SYSTEMIC_DATA_THRESHOLD)  # 18
        result = SCDataHealthGate.is_systemic_data_failure(
            data_insufficient_count=bad,
            total_candidates=n,
        )
        self.assertTrue(result,
                        f"Expected systemic at {bad}/{n} >= {SYSTEMIC_DATA_THRESHOLD:.0%}")

    def test_above_threshold_is_systemic(self):
        """≥20 candidates, 95% bad → SYSTEMIC."""
        result = SCDataHealthGate.is_systemic_data_failure(
            data_insufficient_count=19,
            total_candidates=20,
        )
        self.assertTrue(result)

    def test_below_threshold_not_systemic(self):
        """≥20 candidates but only 50% bad → NOT systemic."""
        result = SCDataHealthGate.is_systemic_data_failure(
            data_insufficient_count=10,
            total_candidates=20,
        )
        self.assertFalse(result)

    def test_zero_bad_not_systemic(self):
        result = SCDataHealthGate.is_systemic_data_failure(
            data_insufficient_count=0,
            total_candidates=100,
        )
        self.assertFalse(result)

    def test_100_percent_bad_below_min_sample_not_systemic(self):
        """Edge: 19/19 = 100% but below MIN_SYSTEMIC_SAMPLE — NOT systemic."""
        result = SCDataHealthGate.is_systemic_data_failure(
            data_insufficient_count=19,
            total_candidates=19,
        )
        self.assertFalse(result,
                         "19 candidates below MIN_SYSTEMIC_SAMPLE=20 must not be classified systemic")

    def test_custom_threshold_respected(self):
        """Caller can pass a custom threshold — must override default."""
        result = SCDataHealthGate.is_systemic_data_failure(
            data_insufficient_count=6,
            total_candidates=20,
            threshold=0.30,   # 30%
        )
        self.assertTrue(result, "6/20 = 30% should trigger at threshold=0.30")

    def test_custom_min_sample_respected(self):
        """Caller can pass a custom min_sample."""
        result = SCDataHealthGate.is_systemic_data_failure(
            data_insufficient_count=10,
            total_candidates=10,
            min_sample=5,   # allow smaller samples
        )
        self.assertTrue(result, "10/10 >= 0.90 with min_sample=5 should be systemic")


# ══════════════════════════════════════════════════════════════════════════════
# GROUP E — Scanner early-return on BLOCKED
# ══════════════════════════════════════════════════════════════════════════════
class TestScannerEarlyReturn(unittest.TestCase):
    """
    When SCDataHealth = BLOCKED, the scanner must return [] immediately.
    No scoring, no evaluation loop, no candidate construction must occur.

    Tests import and call the actual scanner method so runtime integration
    is verified, not just static structure.
    """

    def _make_blocked_result(self):
        return SCDataHealthResult(
            status        = SCDataHealth.BLOCKED,
            reason        = "Both providers RED",
            recommendation= "Restore tokens",
            fyers_result  = ProviderHealthResult(
                provider="FYERS", status=ProviderStatus.RED, error="token missing"
            ),
            upstox_result = ProviderHealthResult(
                provider="UPSTOX", status=ProviderStatus.RED, error="token missing"
            ),
            probe_symbols = ["RELIANCE"],
        )

    def test_scanner_returns_empty_list_when_blocked(self):
        """
        Mock SCDataHealthGate.assess() to return BLOCKED.
        Verify the scanner returns [] without entering the candidate evaluation loop.
        """
        try:
            from app.short_covering.short_covering_scanner import ShortCoveringScanner
        except ImportError:
            self.skipTest("ShortCoveringScanner not importable in this environment")

        blocked = self._make_blocked_result()

        # Patch the gate's assess method at module level
        with patch(
            "app.short_covering.short_covering_scanner.sc_data_health_gate.assess",
            return_value=blocked
        ):
            scanner = ShortCoveringScanner.__new__(ShortCoveringScanner)
            # Stub out any DB / config init the constructor normally does
            scanner.db_connection   = MagicMock()
            scanner.config          = MagicMock()
            scanner.logger          = MagicMock()

            # Find and call the main scan method
            scan_method = None
            for name in ("scan", "run_scan", "scan_candidates", "execute"):
                if hasattr(scanner, name):
                    scan_method = getattr(scanner, name)
                    break

            if scan_method is None:
                self.skipTest("Could not locate scanner entry-point method")

            result = scan_method()
            self.assertIsInstance(result, list)
            self.assertEqual(result, [],
                             "Scanner must return [] when health gate is BLOCKED")

    def test_blocked_result_is_blocked(self):
        """SCDataHealthResult.is_blocked() must return True for BLOCKED status."""
        r = self._make_blocked_result()
        self.assertTrue(r.is_blocked())

    def test_non_blocked_results_are_not_blocked(self):
        for status in (SCDataHealth.GREEN,
                       SCDataHealth.DEGRADED_REDUNDANCY,
                       SCDataHealth.DEGRADED):
            r = SCDataHealthResult(status=status, reason="", recommendation="")
            self.assertFalse(r.is_blocked(),
                             f"{status} should not be is_blocked()")


# ══════════════════════════════════════════════════════════════════════════════
# GROUP F — SCDataHealthResult field contract
# ══════════════════════════════════════════════════════════════════════════════
class TestSCDataHealthResultContract(unittest.TestCase):
    """
    Verify the dataclass fields introduced by the four safeguards exist
    and have the correct types / defaults.
    """

    def test_sc_candidate_included_field_exists(self):
        r = SCDataHealthResult(
            status=SCDataHealth.GREEN, reason="", recommendation=""
        )
        self.assertTrue(hasattr(r, "sc_candidate_included"))
        self.assertIsInstance(r.sc_candidate_included, bool)
        self.assertFalse(r.sc_candidate_included)   # default

    def test_sc_candidate_sym_defaults_to_none(self):
        r = SCDataHealthResult(
            status=SCDataHealth.GREEN, reason="", recommendation=""
        )
        self.assertIsNone(r.sc_candidate_sym)

    def test_assessed_at_is_ist_aware(self):
        r = SCDataHealthResult(
            status=SCDataHealth.GREEN, reason="", recommendation=""
        )
        self.assertIsNotNone(r.assessed_at.tzinfo, "assessed_at must be timezone-aware (IST)")

    def test_as_log_lines_returns_list_of_strings(self):
        r = SCDataHealthResult(
            status=SCDataHealth.BLOCKED,
            reason="Both RED",
            recommendation="Restore tokens",
            probe_symbols=["RELIANCE"],
            sc_candidate_sym="HDFCBANK",
            sc_candidate_included=True,
        )
        lines = r.as_log_lines()
        self.assertIsInstance(lines, list)
        self.assertTrue(all(isinstance(l, str) for l in lines))
        # Blocked result log must mention BLOCKED
        full_log = "\n".join(lines)
        self.assertIn("BLOCKED", full_log)
        # SC candidate line must report inclusion status
        self.assertIn("included", full_log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
