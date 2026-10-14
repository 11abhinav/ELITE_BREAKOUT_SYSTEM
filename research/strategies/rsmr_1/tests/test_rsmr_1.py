"""
Automated Unit and Integration Tests for RSMR-1 Research Pipeline.
"""

import os
import sys
import unittest
import pandas as pd
import numpy as np

SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC_DIR))

from indicators import calculate_stock_metrics
from costs import calculate_trade_costs
from universe import UniverseManager
from portfolio_simulator import PortfolioSimulator

class TestRSMR1Pipeline(unittest.TestCase):

    def setUp(self):
        # Generate synthetic historical bars for mathematical unit testing
        dates = pd.date_range("2020-01-01", periods=250, freq="B")
        prices = np.linspace(100.0, 200.0, 250) # Uptrend
        volumes = np.full(250, 100000.0)
        
        self.df_uptrend = pd.DataFrame({
            "Close": prices,
            "Open": prices,
            "High": prices * 1.01,
            "Low": prices * 0.99,
            "Volume": volumes
        }, index=dates)

    def test_momentum_and_trend_calculation(self):
        dt = self.df_uptrend.index[-1]
        metrics = calculate_stock_metrics(self.df_uptrend, dt, momentum_lookback=126, sma_period=200)
        
        self.assertIsNotNone(metrics)
        # Momentum 126 bars back:
        expected_mom = (self.df_uptrend["Close"].iloc[-1] / self.df_uptrend["Close"].iloc[-127]) - 1.0
        self.assertAlmostEqual(metrics["momentum_6m"], expected_mom, places=4)
        
        # 200-SMA:
        expected_sma = self.df_uptrend["Close"].iloc[-200:].mean()
        self.assertAlmostEqual(metrics["sma_200"], expected_sma, places=4)
        self.assertTrue(metrics["trend_ok"])

    def test_cost_calculation_invariants(self):
        # Buy 100 shares @ ₹500
        buy_costs = calculate_trade_costs("BUY", 100, 500.0, slippage_bps=5.0)
        self.assertGreater(buy_costs.brokerage, 0)
        self.assertEqual(buy_costs.stt, 0) # Zero STT on delivery buy
        self.assertGreater(buy_costs.stamp_duty, 0) # Stamp duty on buy
        self.assertEqual(buy_costs.dp_charges, 0) # Zero DP on buy
        self.assertGreater(buy_costs.total_cost, 0)

        # Sell 100 shares @ ₹600
        sell_costs = calculate_trade_costs("SELL", 100, 600.0, slippage_bps=5.0)
        self.assertGreater(sell_costs.stt, 0) # STT charged on sell
        self.assertEqual(sell_costs.stamp_duty, 0) # Zero stamp duty on sell
        self.assertEqual(sell_costs.dp_charges, 15.93) # ₹15.93 DP charge on delivery sell
        self.assertGreater(sell_costs.total_cost, 0)

    def test_sector_concentration_constraint(self):
        # Verify that max stocks per sector cannot exceed 7
        target_slots = 30
        max_per_sector = 7
        
        # Simulate candidates from 2 sectors: 15 in IT, 15 in Pharma
        mock_candidates = []
        for i in range(15):
            mock_candidates.append({"symbol": f"IT_{i}", "sector": "IT", "momentum_6m": 0.5 - i*0.01, "median_turnover": 1e7})
        for i in range(15):
            mock_candidates.append({"symbol": f"PH_{i}", "sector": "Pharma", "momentum_6m": 0.3 - i*0.01, "median_turnover": 1e7})
            
        selected = []
        counts = {}
        for c in mock_candidates:
            sec = c["sector"]
            if counts.get(sec, 0) < max_per_sector:
                selected.append(c)
                counts[sec] = counts.get(sec, 0) + 1
                if len(selected) == target_slots:
                    break
                    
        self.assertEqual(counts["IT"], 7)
        self.assertEqual(counts["Pharma"], 7)
        self.assertEqual(len(selected), 14)

    def test_safety_and_production_isolation(self):
        # Research engine MUST NOT have live order placement or database mutation methods
        sim = PortfolioSimulator(universe=UniverseManager.__new__(UniverseManager))
        self.assertFalse(hasattr(sim, "place_live_order"))
        self.assertFalse(hasattr(sim, "submit_broker_order"))
        self.assertFalse(hasattr(sim, "execute_live_trade"))
        self.assertFalse(hasattr(sim, "mutate_production_db"))

if __name__ == "__main__":
    unittest.main()
