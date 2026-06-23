# Why Positions Still Show Stale Prices After Fix

## Summary

**The fix addressed the data extraction bug, BUT there's a separate architectural issue:**

Position prices are only updated **once per day (at 01:00 IST)** by the wealth_engine.

At 12:18 IST on June 23, position prices are **11+ hours stale** (last updated at 01:00 IST today).

---

## The Real Issue: Data is Fresh But Database Has Old Prices

### What Changed With The Fix:

✅ **BEFORE FIX**: Fresh price data extraction was broken (wrong columns)  
✅ **AFTER FIX**: Fresh price data extraction works perfectly  

### But Now:

❌ **Fresh price data** → Extracted correctly from Yahoo Finance  
❌ **→ Stored in database as current_price** → Only happens at 01:00 IST (daily)  
❌ **→ Dashboard reads from database** → Shows prices from 01:00 IST  
❌ **→ Alerts check against stale price** → Don't trigger until next daily run  

---

## Architecture Flow

```
09:15 - 15:30 IST (Market Hours)
├─ 1H Scanner runs every 5 min
│  ├─ Fetches 1H candlestick data
│  ├─ Generates intraday alerts
│  └─ ❌ DOES NOT update position prices
│
└─ NOTHING updating position prices here!

01:00 IST (Daily)
└─ Wealth Engine runs
   ├─ Fetches fundamental data
   ├─ ✅ Updates open position prices (current_price field)
   └─ Saves new alerts
```

---

## Timeline Example

```
01:00 IST (Today)
├─ Wealth Engine runs
├─ Fetches prices from Yahoo: RELIANCE = 1300
├─ Updates DB: current_price = 1300
└─ Next update: tomorrow at 01:00

12:18 IST (Today - same day)
├─ User checks dashboard
├─ Dashboard queries wealth_buy_alert table
├─ Reads current_price = 1300 (from 01:00 update)
├─ ❌ STALE! Real price is now 1325
└─ Shows 1300 to user

15:30 IST (Today - market close)
├─ Real price: 1325
├─ Database price: 1300 (from 01:00)
├─ Difference: 25 rupees (2% error!)
└─ Next update: tomorrow at 01:00
```

---

## Why This Wasn't A Problem Before

The bug made the system fall back to **yesterday's** cache automatically.  
So it was showing 1+ day old data.

After the fix, it's showing **today's 01:00 data**, which is fresher (11 hours instead of 24+ hours).

But **still not current**!

---

## Solution: Update Position Prices More Frequently

### Option 1: Add Hourly Job During Market Hours (RECOMMENDED)

```python
# Add to main.py scheduler

if 9:15 <= current_time <= 15:30 and current_hour_change:
    # Every hour during market hours
    update_open_position_prices()
```

**Benefit**: Prices updated hourly (max 1 hour stale)  
**Cost**: Low - just 6-7 additional API calls per day

### Option 2: Let 1H Scanner Update Positions

```python
# In live_scanner.py after generating alerts
from database import update_position_real_time_prices

# For each symbol in open_positions that we just fetched:
current_prices = {}
for symbol in open_symbols:
    df = all_ticker_data.get(symbol)
    if df is not None:
        current_prices[symbol] = {"price": df['Close'].iloc[-1]}

update_position_real_time_prices(current_prices)
```

**Benefit**: Prices updated every 5 min during market hours  
**Cost**: Medium - needs to track open positions in live_scanner

### Option 3: Add Manual Refresh Button

```javascript
// In dashboard UI
<button onclick="refreshPositionPrices()">Refresh Prices Now</button>

async function refreshPositionPrices() {
    const response = await fetch('/api/refresh-position-prices', {method: 'POST'});
    location.reload();
}
```

**Benefit**: User can refresh on demand  
**Cost**: Low - manual action required

---

## Recommended Fix

**Implement Option 1 + Option 3:**

1. ✅ Add hourly position price updates during market hours (scheduled job)
2. ✅ Add manual "Refresh Prices" button for immediate updates
3. ✅ This gives both automatic and on-demand updates

---

## Code Changes Needed

### File: app/main.py

Add a new scheduled job:

```python
def update_open_positions_price_hourly():
    """Update current prices for all open positions (runs hourly 9:15-15:30)."""
    try:
        from app.database import get_open_positions, update_position_real_time_prices
        from app.data_provider import YFinanceFetcher
        
        positions = get_open_positions()
        if not positions:
            return
        
        symbols = [p['symbol'] for p in positions]
        fetcher = YFinanceFetcher()
        
        prices_dict = {}
        for symbol in symbols:
            try:
                df = fetcher.get_ohlcv(symbol, interval="1d", period="1d")
                if df is not None and 'Close' in df.columns:
                    prices_dict[symbol] = {
                        "price": float(df['Close'].iloc[-1]),
                        "score": None  # Or recalculate if needed
                    }
            except Exception as e:
                logger.warning(f"Failed to update price for {symbol}: {e}")
        
        if prices_dict:
            updated = update_position_real_time_prices(prices_dict)
            logger.info(f"✅ Updated prices for {updated} positions")
            
    except Exception as e:
        logger.error(f"❌ Hourly position price update failed: {e}")

# In run_system_scheduler():
# Add to jobs list:
jobs.append({
    "name": "Update Open Position Prices",
    "func": update_open_positions_price_hourly,
    "hour": "*",  # Every hour
    "minute": 0,
    "condition": market_hours_9_15_to_15_30,
    "scheduled_for": "Every hour (9:15 AM - 3:30 PM)"
})
```

### File: app/dashboard_server.py

Add a refresh endpoint:

```python
@app.route('/api/refresh-position-prices', methods=['POST'])
@login_required
def refresh_position_prices():
    """Manually refresh current prices for all open positions."""
    try:
        from database import get_open_positions, update_position_real_time_prices
        from data_provider import YFinanceFetcher
        
        positions = get_open_positions()
        if not positions:
            return jsonify({"message": "No open positions"}), 200
        
        symbols = [p['symbol'] for p in positions]
        fetcher = YFinanceFetcher()
        
        prices_dict = {}
        for symbol in symbols:
            try:
                df = fetcher.get_ohlcv(symbol, interval="1d", period="1d")
                if df is not None and 'Close' in df.columns:
                    prices_dict[symbol] = {"price": float(df['Close'].iloc[-1])}
            except Exception:
                pass
        
        updated = update_position_real_time_prices(prices_dict)
        return jsonify({
            "success": True, 
            "message": f"Updated prices for {updated} positions"
        })
    except Exception as e:
        logger.error(f"Refresh prices error: {e}")
        return jsonify({"error": str(e)}), 500
```

---

## Status

- ✅ **Data extraction bug FIXED** (commit 2ae54e7)
- ❌ **Position price update frequency** - NOT ADDRESSED (architectural issue)
- ⏳ **Still TODO**: Implement hourly updates + manual refresh button

---

## Expected Outcome After Implementing Solution

```
Before Fix:
  Position price: 24+ hours stale (using yesterday's cache)
  
After Fix (current):
  Position price: 11-23 hours stale (using today's 01:00 data)
  
After Solution:
  Position price: 0-60 minutes stale (hourly updates)
  + Manual refresh available for immediate updates
```
