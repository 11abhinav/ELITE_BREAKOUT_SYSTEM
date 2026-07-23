with open("app/price_cache.py", "r") as f:
    content = f.read()

content = content.replace("telemetry.cache_stats.record_hit()", "pass")
content = content.replace("telemetry.cache_stats.record_miss()", "pass")

with open("app/price_cache.py", "w") as f:
    f.write(content)
