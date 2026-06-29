import requests
import json

r = requests.get('https://elitebreakoutsystem-production.up.railway.app/data/performance_data.json')
data = r.json()
wealth = [t for t in data['trades'] if t.get('scanner') == 'WEALTH']
for t in wealth[:5]:
    print(t['symbol'], t['entry_date'], t['alert_time'])
