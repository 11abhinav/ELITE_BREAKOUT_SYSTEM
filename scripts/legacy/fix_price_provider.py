with open("app/price_provider.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "from telemetry_manager import telemetry" in line:
        continue
    if "telemetry.network_stats.record_call" in line:
        continue
    new_lines.append(line)

with open("app/price_provider.py", "w") as f:
    f.writelines(new_lines)
