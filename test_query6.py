import sys, os, json
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_scanner_execution_history, get_scanner_health_summary

print("Execution History:")
hist = get_scanner_execution_history(limit=20)
for r in hist:
    print(r['scanner_name'], r['started_at'], r.get('stale_count'), r.get('total_symbols'), r.get('provider_stats'))

print("\nScanner Health:")
health = get_scanner_health_summary()
for r in health:
    print(r.get('scanner_name'), r.get('date'), r.get('provider_stats'))
