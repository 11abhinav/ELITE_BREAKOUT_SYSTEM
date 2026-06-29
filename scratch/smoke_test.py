import sys
import os
sys.path.insert(0, os.path.abspath('app'))

import logging
logging.basicConfig(level=logging.INFO)

print("--- SMOKE TEST 1: MODULE IMPORT ---")
try:
    import multibagger
    print("Module import successful.")
except Exception as e:
    print(f"Module import failed: {e}")
    sys.exit(1)

print("\n--- SMOKE TEST 2: RUN START WITH DEBUG_LIMIT=5 ---")
try:
    multibagger.start(debug_limit=5)
    print("start(debug_limit=5) completed.")
except Exception as e:
    print(f"start() failed: {e}")
    import traceback
    traceback.print_exc()

print("\n--- SMOKE TEST 3: STANDALONE EXIT MONITOR ---")
try:
    multibagger.run_standalone_exit_monitor()
    print("run_standalone_exit_monitor() completed.")
except Exception as e:
    print(f"run_standalone_exit_monitor() failed: {e}")
    import traceback
    traceback.print_exc()
