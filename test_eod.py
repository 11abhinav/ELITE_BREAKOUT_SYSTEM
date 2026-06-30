import sys
import logging
logging.basicConfig(level=logging.INFO)
from app.eod_scanner import start
print("Starting EOD scan...")
try:
    # Just run a quick dry run if possible or see where it crashes
    start()
except Exception as e:
    import traceback
    traceback.print_exc()
