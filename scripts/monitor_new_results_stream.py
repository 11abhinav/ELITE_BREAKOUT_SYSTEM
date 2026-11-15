#!/usr/bin/env python3
"""
scripts/monitor_new_results_stream.py
=====================================
Live streaming monitor for financial filing scanner.
Tracks and displays stocks whose new financial results are fetched, parsed, and pushed to data.
"""

import os
import sys
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

# Load .env
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

AXIOM_API_URL = "https://api.axiom.co/v1/datasets/_apl?format=legacy"
AXIOM_TOKEN = os.getenv("AXIOM_TOKEN", "")
ORG_ID = os.getenv("AXIOM_ORG_ID", "elite-9xcn")
DATASET = os.getenv("AXIOM_DATASET", "elite")

seen_events = set()

def query_axiom(apl_query: str):
    headers = {
        "Authorization": f"Bearer {AXIOM_TOKEN}",
        "X-Axiom-Org-Id": ORG_ID,
        "Content-Type": "application/json",
    }
    payload = json.dumps({"apl": apl_query}).encode("utf-8")
    req = urllib.request.Request(AXIOM_API_URL, data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
            return data.get("matches", [])
    except Exception as e:
        print(f"⚠️ Axiom query error: {e}", file=sys.stderr)
        return []

def poll_and_print():
    query = (
        f"['{DATASET}'] "
        "| where _time > now() - 15m "
        "| where log contains 'filling scanner' or log contains 'PDF_FALLBACK' or log contains 'PDF:DIRECT' or log contains 'NEW FILING DISCOVERED' or log contains 'CANONICAL_PIT_REBUILD' or log contains 'Running Scanner' "
        "| order by _time asc "
        "| limit 150"
    )
    matches = query_axiom(query)
    new_found = 0
    for m in matches:
        t_str = m.get("_time", "")[:19]
        log_line = m.get("data", {}).get("log", "")
        event_id = f"{t_str}_{log_line}"
        if event_id in seen_events:
            continue
        seen_events.add(event_id)

        # Highlight important milestones
        if "PDF_FALLBACK: SUCCESS" in log_line or "Ingested SEBI Reg 33 PDF statement" in log_line:
            print(f"\n🌟 [{t_str}] [RESULT INGESTED - PDF] {log_line}")
            new_found += 1
        elif "Direct sweep attachment ingestion succeeded" in log_line:
            print(f"\n⚡ [{t_str}] [RESULT INGESTED - SWEEP XBRL] {log_line}")
            new_found += 1
        elif "NEW FILING DISCOVERED IN MARKET SWEEP" in log_line:
            print(f"📢 [{t_str}] [NEW FILING DISCOVERED] {log_line}")
            new_found += 1
        elif "Downloaded exchange PDF" in log_line:
            print(f"📥 [{t_str}] [PDF DOWNLOADED] {log_line}")
            new_found += 1
        elif "DB SYNC COMPLETE" in log_line:
            print(f"💾 [{t_str}] [DB PERSISTENCE] {log_line}")
            new_found += 1
        elif "CANONICAL_PIT_REBUILD" in log_line:
            print(f"🚀 [{t_str}] [CANONICAL REBUILD] {log_line}")
            new_found += 1
        elif "Done:" in log_line and "Rate:" in log_line:
            print(f"📊 [{t_str}] {log_line}")
            new_found += 1
        elif "Running Scanner:" in log_line:
            print(f"\n▶️ [{t_str}] {log_line}")
            new_found += 1

    return new_found

if __name__ == "__main__":
    print(f"📡 Connecting to Axiom (Org: {ORG_ID}, Dataset: {DATASET})...")
    print(f"⏰ Monitor start time: {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S IST')}")
    print(f"🔍 Monitoring for newly fetched and ingested financial results...\n")
    poll_and_print()
