import logging
import sys
import os

# Set up logging to stdout
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stdout)

# Add app to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "app"))

from live_fundamental_scanner import QualityCompounderValueV2Scanner

if __name__ == "__main__":
    print("\n=== STARTING QUALITY COMPOUNDER SCANNER ===")
    scanner = QualityCompounderValueV2Scanner()
    res = scanner.scan_universe(trigger_type="MANUAL")
    print("=== SCANNER RESULT ===")
    print(res)
