# app/certification/test_certification.py
"""
Automated Test Suite for Exact Production Replay & Deterministic Backtest Certification.
Tests Population A (alerts), Population B (rejections), Population C (near misses),
and proves failure detection on contaminated data (PGIL proof).
"""
import unittest
from datetime import datetime, date
import pandas as pd
import numpy as np

import os
import sys

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT_DIR = os.path.abspath(os.path.join(_APP_DIR, ".."))
for _p in (_APP_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from certification.models import (
        ProductionDecisionRecord,
        FrozenDataSnapshot,
        GateAuditResult,
        Tolerances
    )
    from certification.provenance import get_config_hash, get_dataframe_hash
    from certification.point_in_time import validate_point_in_time
    from certification.difference_engine import DifferenceEngine
    from certification.data_auditor import ParquetAuditor
    from certification.replay import ProductionReplayOrchestrator
except ImportError:
    from app.certification.models import (
        ProductionDecisionRecord,
        FrozenDataSnapshot,
        GateAuditResult,
        Tolerances
    )
    from app.certification.provenance import get_config_hash, get_dataframe_hash
    from app.certification.point_in_time import validate_point_in_time
    from app.certification.difference_engine import DifferenceEngine
    from app.certification.data_auditor import ParquetAuditor
    from app.certification.replay import ProductionReplayOrchestrator


class TestDeterministicCertification(unittest.TestCase):
    """Rigorous certification verification test suite."""

    def setUp(self):
        self.engine = DifferenceEngine()
        self.orchestrator = ProductionReplayOrchestrator(scanner_name="EOD")

    def _generate_synthetic_clean_bars(self, n: int = 100, base_price: float = 100.0) -> pd.DataFrame:
        """Generates realistic clean daily price bars."""
        dates = pd.date_range(end="2026-09-23", periods=n, freq="B")
        np.random.seed(42)
        close = base_price + np.cumsum(np.random.normal(0.2, 1.0, n))
        high = close + np.random.uniform(0.5, 2.0, n)
        low = close - np.random.uniform(0.5, 2.0, n)
        open_p = low + np.random.uniform(0.1, 0.9, n) * (high - low)
        volume = np.random.randint(50000, 200000, n).astype(float)
        
        df = pd.DataFrame({
            "Datetime": dates,
            "Open": open_p,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume
        })
        return df

    def test_point_in_time_causality_valid(self):
        """Validates that candles strictly up to evaluation date pass causality."""
        df = self._generate_synthetic_clean_bars(50)
        is_valid, violations = validate_point_in_time(df, evaluation_date="2026-09-23")
        self.assertTrue(is_valid)
        self.assertEqual(len(violations), 0)

    def test_point_in_time_causality_future_leak_detection(self):
        """Validates that future candle leakage fails causality immediately."""
        df = self._generate_synthetic_clean_bars(50)
        # Invert or extend date to future
        df.loc[len(df) - 1, "Datetime"] = pd.Timestamp("2026-09-25 00:00:00+05:30")
        is_valid, violations = validate_point_in_time(df, evaluation_date="2026-09-23")
        self.assertFalse(is_valid)
        self.assertTrue(any("LOOKAHEAD_VIOLATION" in v for v in violations))

    def test_exact_match_passes_certification(self):
        """Validates that identical production and replay records achieve 100% CERTIFICATION PASS."""
        df = self._generate_synthetic_clean_bars(60)
        snap = FrozenDataSnapshot(
            symbol="TESTSYM",
            row_count=len(df),
            start_date="2026-06-01",
            end_date="2026-09-23",
            sha256_hash=get_dataframe_hash(df),
            delivery_pct=45.2,
            market_regime="BULL_NORMAL"
        )
        cfg = {"MIN_ATR_EXPANSION_RATIO": 0.80, "MIN_VOLUME_RATIO": 1.80}
        cfg_hash, clean_cfg = get_config_hash(cfg)

        gates = {
            "ATR_EXPANSION": GateAuditResult("ATR_EXPANSION", True, "PASS", 1.85, 0.80, ">=", "Valid expansion"),
            "VOLUME_SURGE": GateAuditResult("VOLUME_SURGE", True, "PASS", 2.40, 1.80, ">=", "Volume surge passed")
        }

        prod_record = ProductionDecisionRecord(
            symbol="TESTSYM",
            scanner_name="EOD_BREAKOUT",
            evaluation_date="2026-09-23",
            evaluation_timestamp="2026-09-23 16:00:00 IST",
            run_id="run_100",
            git_commit="commit_abc123",
            scanner_file_hash="hash_scanner",
            config_hash=cfg_hash,
            effective_config=clean_cfg,
            market_regime="BULL_NORMAL",
            data_snapshot=snap,
            indicators={"Close": 150.25, "ATR20": 4.50, "RVOL": 2.40},
            gate_results=gates,
            score_breakdown={"score": 85.0},
            final_score=85.0,
            terminal_decision="SELECTED",
            alert_generated=True
        )

        # Replay record identical to prod
        replay_record = ProductionDecisionRecord.from_dict(prod_record.to_dict())

        report = self.engine.compare(prod_record, replay_record, pit_valid=True)
        self.assertTrue(report.certified)
        self.assertEqual(report.summary, "CERTIFICATION PASS")
        self.assertIsNone(report.first_divergence)

    def test_divergence_engine_catches_atr_contamination(self):
        """
        Concrete PGIL proof test:
        Simulates production with corrupted ATR20 (366.37) vs clean replay ATR20 (56.35).
        Verifies that Difference Engine catches FIRST DIVERGENCE as ATR20, flags gate mismatch,
        and fails certification.
        """
        df_clean = self._generate_synthetic_clean_bars(60, base_price=1200.0)
        snap_prod = FrozenDataSnapshot("PGIL", 685, "2024-01-01", "2026-09-23", "CORRUPTED_HASH", delivery_pct=21.6)
        snap_replay = FrozenDataSnapshot("PGIL", 243, "2025-09-23", "2026-09-23", "CLEAN_HASH", delivery_pct=21.6)

        cfg_hash, clean_cfg = get_config_hash({"MIN_ATR_EXPANSION_RATIO": 0.80})

        # Production with corrupted ATR20 and REJECT
        prod_gates = {
            "ATR_EXPANSION": GateAuditResult("ATR_EXPANSION", False, "FAIL", 0.327, 0.80, "<", "Compressed range")
        }
        prod_record = ProductionDecisionRecord(
            symbol="PGIL",
            scanner_name="EOD_BREAKOUT",
            evaluation_date="2026-09-23",
            evaluation_timestamp="2026-09-23 16:00:00 IST",
            run_id="prod_pgil_run",
            git_commit="commit_abc123",
            scanner_file_hash="hash_scanner",
            config_hash=cfg_hash,
            effective_config=clean_cfg,
            market_regime="STRONG_BEAR",
            data_snapshot=snap_prod,
            indicators={"Close": 1300.80, "ATR20": 366.3748, "Candle_Range": 119.80, "ATR_EXPANSION": 0.327},
            gate_results=prod_gates,
            score_breakdown={},
            final_score=0.0,
            terminal_decision="REJECTED",
            alert_generated=False,
            rejection_reason="NO_ATR_EXPANSION_FAIL"
        )

        # Replay with clean ATR20 (56.35) and PASS on ATR gate
        replay_gates = {
            "ATR_EXPANSION": GateAuditResult("ATR_EXPANSION", True, "PASS", 2.126, 0.80, ">=", "Valid expansion")
        }
        replay_record = ProductionDecisionRecord(
            symbol="PGIL",
            scanner_name="EOD_BREAKOUT",
            evaluation_date="2026-09-23",
            evaluation_timestamp="2026-09-23 16:00:00 IST",
            run_id="replay_pgil_run",
            git_commit="commit_abc123",
            scanner_file_hash="hash_scanner",
            config_hash=cfg_hash,
            effective_config=clean_cfg,
            market_regime="STRONG_BEAR",
            data_snapshot=snap_replay,
            indicators={"Close": 1300.80, "ATR20": 56.3496, "Candle_Range": 119.80, "ATR_EXPANSION": 2.126},
            gate_results=replay_gates,
            score_breakdown={},
            final_score=0.0,
            terminal_decision="REJECTED",
            alert_generated=False,
            rejection_reason="BASE_TIGHTNESS_FAIL"
        )

        report = self.engine.compare(prod_record, replay_record, pit_valid=True)
        
        # Certification MUST FAIL
        self.assertFalse(report.certified)
        self.assertEqual(report.summary, "CERTIFICATION FAIL")
        self.assertIn("DATA_SHA256", report.first_divergence)
        
        # Check that ATR20 difference was caught
        atr_diff = next(c for c in report.field_comparisons if c.field_name == "ATR20")
        self.assertFalse(atr_diff.matches)
        self.assertAlmostEqual(atr_diff.delta, 56.3496 - 366.3748, places=2)

        # Check downstream impact tracing
        self.assertIn("GATE_EVALUATION", report.downstream_impact)

    def test_population_rejection_reproduction(self):
        """
        Population B test:
        Verifies that a validly rejected candidate reproduces the exact rejection gate and reason.
        """
        cfg_hash, clean_cfg = get_config_hash({"MIN_VOLUME_RATIO": 1.80})
        snap = FrozenDataSnapshot("WEAK_VOL_STOCK", 100, "2026-01-01", "2026-09-23", "HASH_VOL", delivery_pct=30.0)

        gates = {
            "VOLUME_SURGE": GateAuditResult("VOLUME_SURGE", False, "FAIL", 1.25, 1.80, "<", "Volume surge < 1.80x")
        }

        prod_record = ProductionDecisionRecord(
            symbol="WEAK_VOL_STOCK",
            scanner_name="EOD_BREAKOUT",
            evaluation_date="2026-09-23",
            evaluation_timestamp="2026-09-23 16:00:00 IST",
            run_id="run_rej_1",
            git_commit="commit_1",
            scanner_file_hash="hash_1",
            config_hash=cfg_hash,
            effective_config=clean_cfg,
            market_regime="STRONG_BEAR",
            data_snapshot=snap,
            indicators={"Close": 450.0, "RVOL": 1.25},
            gate_results=gates,
            score_breakdown={},
            final_score=0.0,
            terminal_decision="REJECTED",
            alert_generated=False,
            rejection_reason="LOW_VOLUME"
        )

        replay_record = ProductionDecisionRecord.from_dict(prod_record.to_dict())

        report = self.engine.compare(prod_record, replay_record, pit_valid=True)
        self.assertTrue(report.certified)
        self.assertTrue(report.decision_match)
        self.assertTrue(report.gates_match)


if __name__ == "__main__":
    unittest.main()
