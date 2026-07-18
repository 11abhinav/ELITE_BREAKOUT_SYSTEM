import sys
sys.path.append('app')
from surveillance import get_live_blacklist
import logging

logging.basicConfig(level=logging.INFO)
print("Testing get_live_blacklist...")
get_live_blacklist()
print("Done.")
