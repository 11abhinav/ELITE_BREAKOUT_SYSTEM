#!/usr/bin/env python3
"""
scripts/run_backward_batches.py
================================
Executes filing scanner on STALE stocks in backward order (from the end of the stale list)
in batches of 10, monitoring logs and recording freshness transitions after each batch.
"""

import os
import sys
import time
import json
import gzip
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# Ensure unbuffered output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

PROD_URL = "https://www.elitebreakout.duckdns.org"
ADMIN_TOKEN = "ELITE_BREAKOUT_SYSTEM_SECURE_PERMANENT_SECRET_KEY_PROD_2026_V10"

HEADERS = {
    "X-Admin-Token": ADMIN_TOKEN,
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip"
}

def load_env():
    env = {}
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.strip().split("=", 1)
                    env[k.strip()] = v.strip("\"'")
    return env

ENV = load_env()
AXIOM_TOKEN = ENV.get("AXIOM_TOKEN", "")
AXIOM_ORG_ID = ENV.get("AXIOM_ORG_ID", "")
AXIOM_DATASET = ENV.get("AXIOM_DATASET", "elite")

def get_stock_health(force=False, timeout=90):
    url = f"{PROD_URL}/api/admin/stock_data_health" + ("?force=1" if force else "")
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode("utf-8"))
        except Exception as e:
            print(f"[get_stock_health] Attempt {attempt+1}/5 failed: {e}", flush=True)
            time.sleep(10)
    return None

def trigger_batch(symbols):
    url = f"{PROD_URL}/api/admin/trigger_filing_watcher"
    payload = json.dumps({"symbols": symbols, "force": False}).encode()
    req = urllib.request.Request(url, data=payload, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip" or (len(raw) >= 2 and raw[:2] == b"\x1f\x8b"):
                raw = gzip.decompress(raw)
            return json.loads(raw.decode("utf-8"))
    except Exception as e:
        print(f"[trigger_batch] Error: {e}", flush=True)
        return {"status": "error", "message": str(e)}

def is_filing_watcher_running():
    try:
        req = urllib.request.Request(f"{PROD_URL}/api/scanner_execution_history?scanner_name=FILING_WATCHER&limit=1", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8"))
            rec = data.get("records", [{}])[0]
            return rec.get("lifecycle_status") == "RUNNING"
    except Exception:
        return False

def query_axiom_logs(minutes=5, limit=50):
    if not AXIOM_TOKEN:
        return []
    headers = {
        "Authorization": f"Bearer {AXIOM_TOKEN}",
        "X-AXIOM-ORG-ID": AXIOM_ORG_ID,
        "Content-Type": "application/json"
    }
    start_time = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = {
        "apl": f"[\"{AXIOM_DATASET}\"] | order by _time desc | take {limit}",
        "startTime": start_time
    }
    req = urllib.request.Request(
        "https://api.axiom.co/v1/datasets/_apl?format=legacy",
        data=json.dumps(query).encode(),
        headers=headers,
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            res = json.loads(raw.decode("utf-8"))
            return res.get("matches", [])
    except Exception as e:
        return []

def run_continuous(max_batches=200):
    attempted_stocks = set()
    attempted_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backward_sweep_attempted.json")
    if os.path.exists(attempted_file):
        try:
            with open(attempted_file) as af:
                saved = json.load(af)
                if isinstance(saved, list):
                    attempted_stocks.update(saved)
                    print(f"📂 Loaded {len(attempted_stocks)} previously attempted stocks from {os.path.basename(attempted_file)}", flush=True)
        except Exception as e:
            print(f"⚠️ Could not load attempted file: {e}", flush=True)

    batch_idx = 0
    pass_number = 1

    while batch_idx < max_batches:
        health = get_stock_health()
        if not health:
            print("❌ Failed to fetch stock health, retrying in 10s...", flush=True)
            time.sleep(10)
            continue

        summary = health.get("summary", {})
        stale_list = health.get("stale_stocks", [])
        fresh_list = health.get("fresh_stocks", [])

        backward_stale = [s["symbol"].strip().upper() for s in stale_list[::-1] if s.get("symbol")]
        unattempted = [sym for sym in backward_stale if sym not in attempted_stocks]

        if not unattempted:
            if not backward_stale:
                print("🎉 All stocks are now FRESH! Universe is 100% recovered.", flush=True)
                break
            else:
                pass_number += 1
                print(f"🔄 Pass {pass_number-1} complete. Starting Pass {pass_number} on {len(backward_stale)} remaining stale stocks...", flush=True)
                attempted_stocks.clear()
                try:
                    if os.path.exists(attempted_file):
                        os.remove(attempted_file)
                except Exception:
                    pass
                unattempted = backward_stale

        batch_idx += 1
        print(f"\n=======================================================", flush=True)
        print(f"🚀 INITIATING BACKWARD BATCH #{batch_idx} (Pass {pass_number})", flush=True)
        print(f"=======================================================", flush=True)
        print(f"📊 PROD STATUS: Total={summary.get('total_universe')}, FRESH={len(fresh_list)}, STALE={len(stale_list)}", flush=True)
        print(f"📋 Stale remaining: {len(backward_stale)}, Unattempted: {len(unattempted)}", flush=True)

        current_batch = unattempted[:10]
        attempted_stocks.update(current_batch)
        try:
            with open(attempted_file, "w") as af:
                json.dump(sorted(list(attempted_stocks)), af)
        except Exception:
            pass

        print(f"👉 TARGETING BATCH #{batch_idx} ({len(current_batch)} stocks): {current_batch}", flush=True)
        trigger_res = trigger_batch(current_batch)
        print(f"📡 Trigger response: {trigger_res}", flush=True)

        if trigger_res.get("status") == "error":
            print(f"⚠️ Trigger returned error. Waiting 10s before retry...", flush=True)
            time.sleep(10)
            continue

        # Monitor execution
        batch_start_utc = datetime.now(timezone.utc)
        batch_start_iso = batch_start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"⏳ Monitoring batch execution in prod container (started at {batch_start_iso})...", flush=True)
        start_wait = time.time()
        completed = False

        # Enforce minimum wait of 20s so container has time to begin processing
        time.sleep(20)

        last_min_report = time.time()
        while time.time() - start_wait < 1200:  # Up to 20 minutes max per batch of 10 with OCR fallback
            now_t = time.time()
            logs = query_axiom_logs(minutes=3, limit=50)
            latest_msg = ""
            for m in logs:
                log_time = m.get("_time", "")
                if log_time < batch_start_iso:
                    continue
                msg = m.get("data", {}).get("message") or m.get("data", {}).get("log") or ""
                if msg and not latest_msg:
                    latest_msg = msg.strip()[:100]
                if ("Completed run" in msg and "FILING_WATCHER" in msg) or \
                   ("transition='RUNNING -> IDLE'" in msg and "FILING_WATCHER" in msg) or \
                   ("transition='RUNNING -> OK'" in msg and "FILING_WATCHER" in msg) or \
                   ("Polling cycle complete" in msg and "filling scanner" in msg) or \
                   ("Canonical PIT published" in msg):
                    completed = True
                    print(f"✅ Production run completed: {msg.strip()[:140]}", flush=True)
                    break
            if completed:
                break

            # Print status every 60 seconds
            if now_t - last_min_report >= 60.0:
                elapsed_min = round((now_t - start_wait) / 60.0, 1)
                fw_running = is_filing_watcher_running()
                print(f"⏱️ [1-MIN STATUS @ {elapsed_min}m]: Batch #{batch_idx} in progress | Watcher Running: {fw_running} | Latest Log: {latest_msg or 'Processing disclosures...'}", flush=True)
                last_min_report = now_t

            # If Axiom log didn't catch event, verify direct scanner health endpoint
            if time.time() - start_wait > 45:
                if not is_filing_watcher_running():
                    completed = True
                    print("✅ Production FILING_WATCHER transitioned from RUNNING -> OK/IDLE.", flush=True)
                    break

            time.sleep(10)

        # Re-audit health to check transitions (force refresh)
        print("🔄 Waiting 5s for caches to settle then querying updated stock health with force=True...", flush=True)
        time.sleep(5)
        new_health = get_stock_health(force=True)
        if new_health:
            new_stale = [s["symbol"].strip().upper() for s in new_health.get("stale_stocks", []) if s.get("symbol")]
            new_fresh = [s["symbol"].strip().upper() for s in new_health.get("fresh_stocks", []) if s.get("symbol")]
            restored = [sym for sym in current_batch if sym in new_fresh]
            still_stale = [sym for sym in current_batch if sym in new_stale]
            print(f"\n🎯 BATCH #{batch_idx} RESULTS:", flush=True)
            print(f"   Newly Restored to FRESH ({len(restored)}): {restored}", flush=True)
            print(f"   Still Stale / Fail-Closed ({len(still_stale)}): {still_stale}", flush=True)
            print(f"   Universe Now: FRESH={len(new_fresh)} | STALE={len(new_stale)}", flush=True)

        time.sleep(2)

if __name__ == "__main__":
    run_continuous()
