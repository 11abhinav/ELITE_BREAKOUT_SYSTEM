import sys
import os
import logging
logging.basicConfig(level=logging.INFO)

sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
from app.multibagger import start as multi_start

if __name__ == "__main__":
    print("\nStarting Multibagger dry-run...")
    try:
        multi_start()
    except Exception as e:
        print(f"Multibagger Error: {e}")
