import sys, os, json
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_scanner_execution_history, get_scanner_health

print("Scanner Execution History:")
hist = get_scanner_execution_history(limit=50)
for r in hist:
    print(r['scanner_name'], r['started_at'], r.get('stale_count'), r.get('total_symbols'), r.get('provider_stats'))

print("\nScanner Health Status:")
for name in ["EOD", "REVERSAL", "MULTI_TF", "MULTIBAGGER", "PULLBACK"]:
    health = get_scanner_health(name)
    if health:
        print(health.get('scanner_name'), health.get('date'), health.get('provider_stats'), health.get('error_msg'))
