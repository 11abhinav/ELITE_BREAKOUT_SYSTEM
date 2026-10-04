import os, sys, time, traceback
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "app"))

print("Testing direct import...", flush=True)
try:
    import live_fundamental_scanner as s
    print("live_fundamental_scanner imported successfully!", flush=True)
except Exception:
    traceback.print_exc()
