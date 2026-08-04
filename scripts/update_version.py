#!/usr/bin/env python3
import json
import subprocess
import os
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ver_file = os.path.join(BASE_DIR, "app", "version.json")

commit_sha = "unknown"
try:
    res = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=BASE_DIR)
    if res.returncode == 0 and res.stdout:
        commit_sha = res.stdout.strip()
except Exception:
    pass

version_str = f"v1-{commit_sha}"

data = {
    "version": version_str,
    "commit": commit_sha,
    "timestamp": datetime.now(IST).isoformat()
}

os.makedirs(os.path.dirname(ver_file), exist_ok=True)
with open(ver_file, "w") as f:
    json.dump(data, f, indent=2)

print(f"✅ Version updated in {ver_file}: {version_str}")
