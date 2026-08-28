import sys
import os
import logging
logging.basicConfig(level=logging.INFO)

sys.path.append(os.path.join(os.path.dirname(__file__), "app"))

from app.eod_scanner import start as eod_start
from app.multibagger import start as multi_start

if __name__ == "__main__":
    print("Starting EOD dry-run...")
    try:
        eod_start(force=True)
    except Exception as e:
        print(f"EOD Error: {e}")
        
    print("\nStarting Multibagger dry-run...")
    try:
        multi_start(force=True)
    except Exception as e:
        print(f"Multibagger Error: {e}")
