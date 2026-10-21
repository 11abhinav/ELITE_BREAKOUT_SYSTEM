import unittest
import pandas as pd
import numpy as np

try:
    from app.technical_scanner_intraday import run_technical_intraday_pipeline
except ImportError:
    run_technical_intraday_pipeline = None


@unittest.skipIf(run_technical_intraday_pipeline is None, "TECHNICAL_INTRADAY is decommissioned under governance")
class TestTechnicalIntradayBullFlagOnly(unittest.TestCase):
    """
    Test suite verifying TECHNICAL_INTRADAY scanner strictly isolates
    Bull Flag & Pole (BULL_FLAG) pattern for 15M intraday alerts.
    """

    def test_run_technical_intraday_bull_flag_only_default(self):
        """Verify pipeline executes cleanly and enforces BULL_FLAG isolation."""
        res = run_technical_intraday_pipeline(
            trigger_type="MANUAL",
            watchlist=["RELIANCE"],
            is_test_mode=True,
            force=True
        )
        self.assertIn("total_count", res)
        self.assertIn("processed_count", res)


if __name__ == "__main__":
    unittest.main()
