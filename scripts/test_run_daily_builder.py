import logging
import sys
import os

# Set up logging to stdout
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stdout)

# Add app to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "app"))

from daily_builder import main as run_daily_builder
from live_fundamental_scanner import QualityCompounderValueV2Scanner

if __name__ == "__main__":
    print("=== STARTING DAILY BUILDER ===")
    try:
        run_daily_builder(force_rebuild=True)
    except Exception as e:
        print(f"Daily Builder failed: {e}")
        
    print("\n=== STARTING QUALITY COMPOUNDER SCANNER ===")
    scanner = QualityCompounderValueV2Scanner()
    # Mocking required locks or environment variables if any, but execute_scan should handle it
    res = scanner.scan_universe(trigger_type="MANUAL")
    print("=== SCANNER RESULT ===")
    print(res)
