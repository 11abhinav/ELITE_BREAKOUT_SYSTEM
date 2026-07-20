import os
import pandas as pd

DATASET_DIR = os.path.dirname(os.path.abspath(__file__))

class DatasetRegistry:
    """
    Registry for loading canonical golden datasets.
    These datasets are immutable CSV files representing specific business scenarios.
    """
    
    @classmethod
    def load(cls, dataset_name: str) -> pd.DataFrame:
        """Loads a canonical dataset as a pandas DataFrame."""
        filepath = os.path.join(DATASET_DIR, f"{dataset_name}.csv")
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Canonical dataset '{dataset_name}' not found at {filepath}")
            
        df = pd.read_csv(filepath, index_col="Date", parse_dates=True)
        return df

    @classmethod
    def get_healthy_breakout(cls): return cls.load("healthy_breakout")
    
    @classmethod
    def get_failed_breakout(cls): return cls.load("failed_breakout")
    
    @classmethod
    def get_false_breakout(cls): return cls.load("false_breakout")
    
    @classmethod
    def get_gap_up_continuation(cls): return cls.load("gap_up_continuation")
    
    @classmethod
    def get_gap_up_failure(cls): return cls.load("gap_up_failure")
    
    @classmethod
    def get_low_volume_breakout(cls): return cls.load("low_volume_breakout")
    
    @classmethod
    def get_corporate_action(cls): return cls.load("corporate_action")
    
    @classmethod
    def get_ipo_limited_history(cls): return cls.load("ipo_limited_history")
    
    @classmethod
    def get_provider_failure(cls): return cls.load("provider_failure")
    
    @classmethod
    def get_missing_fundamentals(cls): return cls.load("missing_fundamentals")
    
    @classmethod
    def get_delisted_symbol(cls): return cls.load("delisted_symbol")
    
    @classmethod
    def get_stale_cache(cls): return cls.load("stale_cache")
    
    @classmethod
    def get_multi_tf_confirmation(cls): return cls.load("multi_tf_confirmation")
    
    @classmethod
    def get_reversal_candidate(cls): return cls.load("reversal_candidate")
    
    @classmethod
    def get_holiday_schedule(cls): return cls.load("holiday_schedule")
    
    @classmethod
    def get_split_adjusted_data(cls): return cls.load("split_adjusted_data")
