import unittest
import pandas as pd
from scoring_engine import calculate_score

class TestPledgePenalty(unittest.TestCase):
    def setUp(self):
        # Create a mock ticker dataframe
        self.ticker = pd.DataFrame({
            "Close": [104]*21,
            "Open": [96]*21,
            "High": [105]*21,
            "Low": [95]*21,
            "Volume": [100000]*21,
            "EMA20": [90]*21,
            "SMA50": [80]*21,
            "SMA200": [70]*21,
            "RSI": [65]*21,
            "ADX": [35]*21,
            "MACD": [1]*21,
            "MACD_SIGNAL": [0.5]*21,
        })
        self.latest = self.ticker.iloc[-1]
        self.category = "Test Category"
        self.breakout_count = 3
        self.rsi = 65.0
        self.volume_ratio = 5.0
        
        # Mock Bayesian Regime Weights
        self.mock_regime_ctx = {"trend": "BULL"}
        self.mock_weights = {
            "PLEDGE_PENALTY": -10.0,
            "version": "v7.1"
        }

    def _get_mock_weights(self, regime):
        return {"version": "v7.1", "weights": self.mock_weights}

    def test_no_pledge_data(self):
        # When pledge data is None, score should not have a penalty
        
        score_none, _, _ = calculate_score(
            category=self.category,
            breakout_count=self.breakout_count,
            rsi=self.rsi,
            volume_ratio=self.volume_ratio,
            ticker=self.ticker,
            latest=self.latest,
            promoter_pledge_pct=None,
            regime_ctx=self.mock_regime_ctx,
            bayesian_weights=self.mock_weights,
            bayesian_version="v7.1"
        )
        
        score_zero, _, _ = calculate_score(
            category=self.category,
            breakout_count=self.breakout_count,
            rsi=self.rsi,
            volume_ratio=self.volume_ratio,
            ticker=self.ticker,
            latest=self.latest,
            promoter_pledge_pct=0.0,
            regime_ctx=self.mock_regime_ctx,
            bayesian_weights=self.mock_weights,
            bayesian_version="v7.1"
        )
        
        # Both should be identical and not penalized
        self.assertEqual(score_none, score_zero)

    def test_moderate_pledge_penalty(self):
        
        score_base, _, _ = calculate_score(
            category=self.category,
            breakout_count=self.breakout_count,
            rsi=self.rsi,
            volume_ratio=self.volume_ratio,
            ticker=self.ticker,
            latest=self.latest,
            promoter_pledge_pct=0.0,
            regime_ctx=self.mock_regime_ctx
        )
        
        # 30% pledge -> (30-10)/40 = 0.5 scale. Penalty = 0.5 * -10 = -5
        score_moderate, _, _ = calculate_score(
            category=self.category,
            breakout_count=self.breakout_count,
            rsi=self.rsi,
            volume_ratio=self.volume_ratio,
            ticker=self.ticker,
            latest=self.latest,
            promoter_pledge_pct=30.0,
            regime_ctx=self.mock_regime_ctx,
            bayesian_weights=self.mock_weights,
            bayesian_version="v7.1"
        )
        
        self.assertEqual(score_moderate, score_base - 5)

    def test_high_pledge_penalty(self):
        
        score_base, _, _ = calculate_score(
            category=self.category,
            breakout_count=self.breakout_count,
            rsi=self.rsi,
            volume_ratio=self.volume_ratio,
            ticker=self.ticker,
            latest=self.latest,
            promoter_pledge_pct=0.0,
            regime_ctx=self.mock_regime_ctx
        )
        
        # 60% pledge -> (60-10)/40 = 1.25 scale -> capped at 1.0. Penalty = -10
        score_high, _, _ = calculate_score(
            category=self.category,
            breakout_count=self.breakout_count,
            rsi=self.rsi,
            volume_ratio=self.volume_ratio,
            ticker=self.ticker,
            latest=self.latest,
            promoter_pledge_pct=60.0, # High pledge (would normally be gated by gate_engine, but testing max penalty cap)
            regime_ctx=self.mock_regime_ctx,
            bayesian_weights=self.mock_weights,
            bayesian_version="v7.1"
        )
        
        self.assertEqual(score_high, score_base - 10)

if __name__ == '__main__':
    unittest.main()
