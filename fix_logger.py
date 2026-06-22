with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/main.py', 'r') as f:
    code = f.read()

code = code.replace(
"""def ist_converter(timestamp):
    return datetime.fromtimestamp(timestamp, IST).timetuple()""",
"""def ist_converter(*args):
    timestamp = args[-1] if args else None
    if timestamp is None:
        import time
        timestamp = time.time()
    return datetime.fromtimestamp(timestamp, IST).timetuple()""")

with open('/Users/abhinavmaheshwari/Documents/ELITE_BREAKOUT_SYSTEM/app/main.py', 'w') as f:
    f.write(code)

print("Fixed ist_converter in main.py")
