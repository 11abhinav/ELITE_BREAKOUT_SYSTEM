import time
import os
import sys

# Change to app directory so imports work
os.chdir(os.path.join(os.getcwd(), 'app'))
sys.path.insert(0, os.getcwd())

def test_funcs():
    from dashboard_server import _build_instant_performance_fallback, app
    from master_orchestrator import orchestrator_v2
    from multiprocessing_scanner import MultiprocessingScanner

    # Create dummy app context if needed
    with app.app_context():
        start = time.perf_counter()
        res = _build_instant_performance_fallback()
        print(f"Fallback generation: {(time.perf_counter()-start)*1000:.1f}ms")

        start = time.perf_counter()
        res2 = orchestrator_v2.get_confirmed_signals()
        print(f"Orchestrator confirmed signals: {(time.perf_counter()-start)*1000:.1f}ms")

        start = time.perf_counter()
        res3 = orchestrator_v2.get_stocks_to_watch()
        print(f"Orchestrator stocks to watch: {(time.perf_counter()-start)*1000:.1f}ms")

        from config import AVAILABLE_SCANNERS
        start = time.perf_counter()
        for name, cls in AVAILABLE_SCANNERS.items():
            pass # Just a dummy check, we don't need to instantiate scanners
        print(f"Scanners iteration: {(time.perf_counter()-start)*1000:.1f}ms")

if __name__ == '__main__':
    test_funcs()
