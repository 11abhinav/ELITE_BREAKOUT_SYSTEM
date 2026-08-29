import os
import sys
import time

sys.path.insert(0, os.path.join(os.getcwd(), 'app'))

def profile_functions():
    print("Profiling Backend Functions...")
    print("-" * 50)
    
    # 1. Performance Tracker rebuild
    try:
        from performance_tracker import build_performance_data
        start = time.perf_counter()
        build_performance_data(force_live_fetch=False)
        dur = time.perf_counter() - start
        print(f"build_performance_data() | {dur:.3f}s")
    except Exception as e:
        print(f"build_performance_data() | ERROR: {e}")

    # 2. Instant Fallback
    try:
        from dashboard_server import _build_instant_performance_fallback
        start = time.perf_counter()
        res = _build_instant_performance_fallback()
        dur = time.perf_counter() - start
        print(f"_build_instant_performance_fallback() | {dur:.3f}s")
    except Exception as e:
        print(f"_build_instant_performance_fallback() | ERROR: {e}")

    # 3. Master Alerts
    try:
        from dashboard_server import _build_master_alerts
        start = time.perf_counter()
        res = _build_master_alerts()
        dur = time.perf_counter() - start
        print(f"_build_master_alerts() | {dur:.3f}s")
    except Exception as e:
        print(f"_build_master_alerts() | ERROR: {e}")

if __name__ == "__main__":
    profile_functions()
