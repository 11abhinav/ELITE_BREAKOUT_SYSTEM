import sys, os, json
sys.path.append('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app')
from database import get_scanner_execution_history, get_scanner_health

print("Scanner Execution History:")
res = get_scanner_execution_history(page_size=50)
hist = res.get('data', [])
for r in hist:
    print(r.get('scanner_name'), r.get('started_at'), "Stale:", r.get('stale_count'), "Total:", r.get('total_symbols'), "Stats:", r.get('provider_stats'))

print("\nScanner Health Status:")
for name in ["EOD", "REVERSAL", "MULTI_TF", "MULTIBAGGER", "PULLBACK"]:
    health = get_scanner_health(name)
    if health:
        print(health.get('scanner_name'), health.get('date'), "Stats:", health.get('provider_stats'), "Error:", health.get('error_msg'))
