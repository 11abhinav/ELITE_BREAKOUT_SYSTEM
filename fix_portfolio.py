with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/portfolio_engine.py', 'r') as f:
    content = f.read()

old_logic = """    try:
        entry_price = float(entry_price)
        stop_loss = float(stop_loss)
    except Exception:
        return 0.0, 0

    if entry_price <= 0 or stop_loss <= 0:
        return 0.0, 0"""

new_logic = """    try:
        entry_price = float(entry_price)
        stop_loss = float(stop_loss)
    except Exception:
        stop_loss = 0.0

    if entry_price <= 0:
        return 0.0, 0
        
    if stop_loss <= 0:
        # Fallback for trades with no SL (e.g. bulk deals): assume 10% risk
        stop_loss = entry_price * 0.90"""

content = content.replace(old_logic, new_logic)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/portfolio_engine.py', 'w') as f:
    f.write(content)
