import time
import requests

def test_endpoints():
    base_url = "http://127.0.0.1:5000"
    endpoints = [
        "/api/user_info",
        "/data/performance_data.json",
        "/api/v2/master_summary",
        "/api/v2/stocks_to_watch",
        "/api/v2/master_alerts",
        "/api/v2/scanner_health"
    ]
    
    print("Profiling Dashboard Endpoints...")
    print("-" * 50)
    for ep in endpoints:
        start = time.perf_counter()
        try:
            resp = requests.get(base_url + ep)
            duration = time.perf_counter() - start
            status = resp.status_code
            size_kb = len(resp.content) / 1024
            print(f"{ep:<35} | {status} | {duration:.3f}s | {size_kb:.1f} KB")
        except Exception as e:
            print(f"{ep:<35} | ERROR: {e}")
            
if __name__ == "__main__":
    test_endpoints()
