import sys
import logging
import os

logging.basicConfig(level=logging.INFO)

os.environ["DATABASE_URL"] = "postgresql://dummy:dummy@localhost:5432/dummy"

# Mock the lock so scanners don't fail trying to connect to Postgres
import lock_utils
class DummyLock:
    def __init__(self, name):
        self.name = name
    def acquire(self, blocking=False):
        return True
    def release(self):
        pass

lock_utils.ProcessLock = DummyLock
import database
database.init_db = lambda: None

print("--- Testing eod_scanner ---")
try:
    import eod_scanner
    sys.argv = ['eod_scanner.py', '--test']
    eod_scanner.start(force=True)
    print("✅ eod_scanner completed successfully.")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"❌ eod_scanner failed: {e}")

print("\n--- Testing live_scanner ---")
try:
    import live_scanner
    sys.argv = ['live_scanner.py', '--test']
    try:
        live_scanner.start(force=True)
    except TypeError:
        live_scanner.start()
    print("✅ live_scanner completed successfully.")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"❌ live_scanner failed: {e}")

print("\n--- Testing multi_tf_scanner ---")
try:
    import multi_tf_scanner
    sys.argv = ['multi_tf_scanner.py', '--test']
    try:
        multi_tf_scanner.start(force=True)
    except TypeError:
        multi_tf_scanner.start()
    print("✅ multi_tf_scanner completed successfully.")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"❌ multi_tf_scanner failed: {e}")

print("\n--- Testing reversal_scanner ---")
try:
    import reversal_scanner
    sys.argv = ['reversal_scanner.py', '--test']
    try:
        reversal_scanner.start(force=True)
    except TypeError:
        reversal_scanner.start()
    print("✅ reversal_scanner completed successfully.")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"❌ reversal_scanner failed: {e}")
