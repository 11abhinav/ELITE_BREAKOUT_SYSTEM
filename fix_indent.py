with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'r') as f:
    content = f.read()

bad_str = "        from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError\nfrom yf_rate_limiter import CircuitOpenError"
good_str = "        from yf_rate_limiter import acquire as yf_acquire, release as yf_release, record_rate_limit, CircuitOpenError"

content = content.replace(bad_str, good_str)

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/dashboard_server.py', 'w') as f:
    f.write(content)
