#!/bin/bash
# scripts/auto_start_non_market.sh
# Trigger DailyBuilder and Scanner after system boot in non-market hours.

cd /Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM

export PYTHONPATH="/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM:/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app:$PYTHONPATH"

python3 -c "
import datetime
import pytz
import subprocess
import sys

ist = pytz.timezone('Asia/Kolkata')
now = datetime.datetime.now(ist)

is_weekend = now.weekday() >= 5
market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

is_market_hours = not is_weekend and (market_open <= now <= market_close)

if not is_market_hours:
    print(f'[{now}] Non-market hours detected on boot. Triggering DailyBuilder and Scanner...')
    print('Starting DailyBuilder...')
    try:
        subprocess.run([sys.executable, 'app/daily_builder.py', '--no-wait'], check=True)
    except Exception as e:
        print(f'Error running DailyBuilder: {e}')
        
    print('Starting Live Fundamental Scanner...')
    try:
        subprocess.run([sys.executable, 'app/live_fundamental_scanner.py'], check=True)
    except Exception as e:
        print(f'Error running Live Fundamental Scanner: {e}')
        
    print('Starting Technical Scanner...')
    try:
        subprocess.run([sys.executable, 'app/technical_scanner.py'], check=True)
    except Exception as e:
        print(f'Error running Technical Scanner: {e}')
        
    print('Boot trigger complete.')
else:
    print(f'[{now}] Market hours detected on boot. Deferring to standard scheduler.')
"
