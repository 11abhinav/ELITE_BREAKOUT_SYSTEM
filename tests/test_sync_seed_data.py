import json
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from scripts.sync_seed_data import sync_seed_data

def test_sync_seed_data_anti_downgrade(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        seed_dir = tmp_path / "data_seed"
        data_dir = tmp_path / "data"
        seed_raw = seed_dir / "pit_raw_filings"
        data_raw = data_dir / "pit_raw_filings"
        seed_raw.mkdir(parents=True)
        data_raw.mkdir(parents=True)

        monkeypatch.setenv("ELITE_BASE_DIR", str(tmp_path))

        # Case 1: Seed has newer filing (2026-09-30) vs Target (2026-06-30) -> Should copy seed to target
        with open(seed_raw / "TEST_STOCK.json", "w") as f:
            json.dump([
                {"period_end_date": "2026-06-30", "statement_type": "QUARTERLY"},
                {"period_end_date": "2026-09-30", "statement_type": "QUARTERLY"}
            ], f)
        with open(data_raw / "TEST_STOCK.json", "w") as f:
            json.dump([
                {"period_end_date": "2026-06-30", "statement_type": "QUARTERLY"}
            ], f)

        # Case 2: Target has more filings than Seed -> Should preserve target
        with open(seed_raw / "TARGET_NEWER.json", "w") as f:
            json.dump([
                {"period_end_date": "2026-06-30", "statement_type": "QUARTERLY"}
            ], f)
        with open(data_raw / "TARGET_NEWER.json", "w") as f:
            json.dump([
                {"period_end_date": "2026-06-30", "statement_type": "QUARTERLY"},
                {"period_end_date": "2026-09-30", "statement_type": "QUARTERLY"}
            ], f)

        sync_seed_data()

        with open(data_raw / "TEST_STOCK.json", "r") as f:
            res_test = json.load(f)
        assert len(res_test) == 2
        assert res_test[-1]["period_end_date"] == "2026-09-30"

        with open(data_raw / "TARGET_NEWER.json", "r") as f:
            res_target = json.load(f)
        assert len(res_target) == 2
        assert res_target[-1]["period_end_date"] == "2026-09-30"

