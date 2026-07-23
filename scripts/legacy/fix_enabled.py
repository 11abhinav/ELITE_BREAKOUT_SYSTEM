with open("app/telemetry_manager.py", "r") as f:
    content = f.read()

content = content.replace("        if not self.enabled: return\n", "")

with open("app/telemetry_manager.py", "w") as f:
    f.write(content)
