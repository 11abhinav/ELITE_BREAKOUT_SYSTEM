import os
import pandas as pd
import json
import logging

logger = logging.getLogger(__name__)

GOLDEN_DIR = "tests/fixtures/golden/"

class BusinessValidator:
    """
    Validates output data against Golden Snapshots.
    """
    
    @staticmethod
    def compare_dataframe(current_df: pd.DataFrame, snapshot_name: str) -> bool:
        snapshot_path = os.path.join(GOLDEN_DIR, f"{snapshot_name}.parquet")
        
        if not os.path.exists(snapshot_path):
            logger.warning(f"Golden snapshot {snapshot_name}.parquet not found. Cannot validate.")
            return False
            
        golden_df = pd.read_parquet(snapshot_path)
        
        try:
            pd.testing.assert_frame_equal(current_df, golden_df)
            logger.info(f"✅ BusinessValidator: {snapshot_name} matches golden snapshot.")
            return True
        except AssertionError as e:
            logger.error(f"❌ BusinessValidator: {snapshot_name} diverges from golden snapshot! {e}")
            return False

    @staticmethod
    def compare_json(current_data: dict, snapshot_name: str) -> bool:
        snapshot_path = os.path.join(GOLDEN_DIR, f"{snapshot_name}.json")
        
        if not os.path.exists(snapshot_path):
            logger.warning(f"Golden snapshot {snapshot_name}.json not found. Cannot validate.")
            return False
            
        with open(snapshot_path, 'r') as f:
            golden_data = json.load(f)
            
        if current_data == golden_data:
            logger.info(f"✅ BusinessValidator: {snapshot_name} matches golden snapshot.")
            return True
        else:
            logger.error(f"❌ BusinessValidator: {snapshot_name} diverges from golden snapshot!")
            return False

    @staticmethod
    def save_golden_snapshot_df(df: pd.DataFrame, snapshot_name: str):
        """Manually called to establish a baseline contract. NOT called automatically."""
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        snapshot_path = os.path.join(GOLDEN_DIR, f"{snapshot_name}.parquet")
        df.to_parquet(snapshot_path)
        logger.info(f"Saved golden snapshot to {snapshot_path}")

    @staticmethod
    def save_golden_snapshot_json(data: dict, snapshot_name: str):
        """Manually called to establish a baseline contract. NOT called automatically."""
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        snapshot_path = os.path.join(GOLDEN_DIR, f"{snapshot_name}.json")
        with open(snapshot_path, 'w') as f:
            json.dump(data, f, indent=4)
        logger.info(f"Saved golden snapshot to {snapshot_path}")
