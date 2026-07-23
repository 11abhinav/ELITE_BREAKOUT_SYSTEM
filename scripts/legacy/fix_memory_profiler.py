with open("app/memory_profiler.py", "r") as f:
    content = f.read()

content = content.replace("getattr(telemetry.cache_stats, 'hits', 0)", "0")
content = content.replace("getattr(telemetry.cache_stats, 'misses', 0)", "0")

with open("app/memory_profiler.py", "w") as f:
    f.write(content)
