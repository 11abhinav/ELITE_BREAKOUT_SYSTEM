with open("app/price_cache.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if "telemetry.cache_stats.record_hit()" in line or "telemetry.cache_stats.record_miss()" in line:
        continue
    new_lines.append(line)

with open("app/price_cache.py", "w") as f:
    f.writelines(new_lines)
