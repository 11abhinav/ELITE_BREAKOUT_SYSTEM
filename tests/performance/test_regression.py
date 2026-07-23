import unittest
import pandas as pd
from tools.performance.validators.business_validator import BusinessValidator
import os

class TestBusinessRegression(unittest.TestCase):
    """
    Validates that the current pipeline output exactly matches the Golden Snapshots.
    """
    
    def test_watchlist_regression(self):
        # Mock load of current run's watchlist
        # In a real environment, this would run the DailyBuilder on frozen data.
        current_watchlist_path = "data/watchlist.parquet"
        if not os.path.exists(current_watchlist_path):
            self.skipTest("No current watchlist generated to compare.")
            
        current_df = pd.read_parquet(current_watchlist_path)
        self.assertTrue(BusinessValidator.compare_dataframe(current_df, "watchlist"))
        
    def test_rankings_regression(self):
        current_rankings_path = "data/rankings.parquet"
        if not os.path.exists(current_rankings_path):
            self.skipTest("No current rankings generated to compare.")
            
        current_df = pd.read_parquet(current_rankings_path)
        self.assertTrue(BusinessValidator.compare_dataframe(current_df, "rankings"))

if __name__ == '__main__':
    unittest.main()
