import sys
import logging

logging.basicConfig(level=logging.INFO)
sys.path.append("/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app")

import multibagger
import traceback

try:
    print("Starting manual run of Multibagger scanner to update scores...")
    multibagger.start()
    print("Done!")
except Exception as e:
    print("Error:")
    traceback.print_exc()
