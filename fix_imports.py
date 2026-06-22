import re
with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'r') as f:
    content = f.read()

# Add to top level
if "from yf_rate_limiter import CircuitOpenError" not in content[:500]:
    content = content.replace("import yfinance as yf", "import yfinance as yf\nfrom yf_rate_limiter import CircuitOpenError")
    with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'w') as f:
        f.write(content)
