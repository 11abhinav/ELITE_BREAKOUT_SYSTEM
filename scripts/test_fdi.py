import os, sys
print("Step 1: start", flush=True)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "app"))

print("Step 2: sys.path set", flush=True)
import app
print("Step 3: imported app:", app.__file__, flush=True)

try:
    from app import financial_data_integrity as fdi
    print("Step 4: imported fdi!", flush=True)
except Exception as e:
    print("Step 4 error:", e, flush=True)
