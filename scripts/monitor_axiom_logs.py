#!/usr/bin/env python3
"""
Axiom Log Monitor & Diagnostic Tool for Elite Breakout System
Directly connects to Axiom REST API (https://api.axiom.co) to query, stream,
and diagnose production logs for the 'elite' dataset.
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.error
import ssl
from datetime import datetime

# Ensure unbuffered stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Load .env natively or via python-dotenv
def load_env_fallback():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("'\"")
                    if k not in os.environ:
                        os.environ[k] = v

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

load_env_fallback()

# Determine SSL context for macOS compatibility
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl._create_unverified_context()

DEFAULT_ORG_ID = os.getenv("AXIOM_ORG_ID", "elite-9xcn")
DEFAULT_DATASET = os.getenv("AXIOM_DATASET", "elite")
DEFAULT_TOKEN = os.getenv("AXIOM_TOKEN") or os.getenv("AXIOM_API_TOKEN", "")


def query_axiom(apl_query: str, token: str, org_id: str = DEFAULT_ORG_ID) -> dict:
    """Executes an APL query against Axiom API."""
    url = "https://api.axiom.co/v1/datasets/_apl?format=legacy"
    payload = json.dumps({"apl": apl_query}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "curl/7.88.1"
    }
    if org_id:
        headers["X-Axiom-Org-Id"] = org_id
        
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, context=SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        if e.code == 403 and "not allowed by policy" in error_body:
            token_type = "API Token (xaat-...)" if token.startswith("xaat-") else "Token"
            raise PermissionError(
                f"\n❌ Axiom 403 Forbidden: {token_type} is NOT permitted to query datasets.\n"
                f"Axiom requires a Personal Access Token (starts with 'xapt-') or an Advanced Query Token to read logs.\n"
                f"('xaat-' tokens are ingest-only forwarder keys).\n\n"
                f"👉 How to get a Query Token in 10 seconds:\n"
                f"1. Open https://app.axiom.co/settings/tokens (or Profile -> Personal Tokens)\n"
                f"2. Click 'New Token' / 'Create Personal Token'\n"
                f"3. Copy the token (it starts with 'xapt-') and paste it here!\n"
            )
        raise RuntimeError(f"Axiom API error ({e.code}): {error_body}")
    except Exception as e:
        raise RuntimeError(f"Axiom connection failed: {e}")


def format_log_entry(match: dict) -> str:
    """Formats a raw Axiom log record into a readable console string."""
    ts = match.get("_time", "")
    data = match.get("data", {})
    
    # Extract message from common log payload keys
    msg = (
        data.get("log")
        or data.get("message")
        or data.get("text")
        or data.get("output")
        or json.dumps(data)
    )
    
    # Strip trailing whitespace/newlines
    msg = str(msg).rstrip()
    
    # Add timestamp prefix if not already present
    if ts and not msg.startswith("202"):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            ts_str = ts[:19]
        return f"{ts_str} | {msg}"
    return msg


def fetch_logs(
    token: str,
    org_id: str,
    dataset: str,
    minutes: int = 15,
    errors_only: bool = False,
    query_filter: str = None,
    limit: int = 100
):
    """Constructs APL and executes the search."""
    apl_parts = [f"['{dataset}']", f"where _time >= ago({minutes}m)"]
    
    if errors_only:
        apl_parts.append(
            "where log contains '| ERROR |' or log contains '| CRITICAL |' or log contains 'Traceback' or log contains '🔴'"
        )
    elif query_filter:
        apl_parts.append(f"where log contains '{query_filter}'")
        
    apl_parts.append("order by _time asc")
    apl_parts.append(f"limit {limit}")
    
    apl = " | ".join(apl_parts)
    result = query_axiom(apl, token, org_id)
    matches = result.get("matches", [])
    return matches


def run_diagnostics(matches: list):
    """Analyzes recent errors and highlights root causes."""
    errors = []
    for m in matches:
        msg = format_log_entry(m)
        if any(err_kw in msg for err_kw in ["ERROR", "Traceback", "Exception", "🔴", "NameError", "KeyError"]):
            errors.append(msg)
            
    print("\n" + "=" * 70)
    print(" 🩺 AXIOM LOG DIAGNOSTICS & SYSTEM HEALTH")
    print("=" * 70)
    
    if not errors:
        print("✅ Zero errors or exceptions found in the inspected window!")
        print("   System is running healthy.")
        return
        
    print(f"⚠️  Detected {len(errors)} error / exception lines:")
    for err in errors[:20]:
        print(f"  • {err}")
    if len(errors) > 20:
        print(f"  ... and {len(errors) - 20} more errors.")
        
    # Check for known patterns
    print("\n🔍 Automated Root-Cause Analysis:")
    combined = "\n".join(errors).lower()
    if "name 'rows' is not defined" in combined or "name_error" in combined:
        print("  💡 [IDENTIFIED ROOT CAUSE]: NameError 'rows' in get_all_push_subscriptions")
        print("     Resolution: Fix committed & pushed in 7960f72e. Once the container redeploys, this error is permanently eliminated.")
    if "ssl: certificate_verify_failed" in combined:
        print("  ⚠️ [IDENTIFIED ROOT CAUSE]: SSL Certificate verification issue in container environment.")
    if "connection refused" in combined or "could not connect to server" in combined:
        print("  ❌ [IDENTIFIED ROOT CAUSE]: PostgreSQL Connection pool exhausted or host unreachable.")
    if "syntaxerror" in combined or "importerror" in combined:
        print("  ❌ [IDENTIFIED ROOT CAUSE]: Python syntax or import resolution failure.")


def main():
    parser = argparse.ArgumentParser(description="Monitor and diagnose Axiom production logs.")
    parser.add_argument("--token", "-t", default=DEFAULT_TOKEN, help="Axiom API Token or PAT")
    parser.add_argument("--org", default=DEFAULT_ORG_ID, help="Axiom Org ID (default: elite-9xcn)")
    parser.add_argument("--dataset", "-d", default=DEFAULT_DATASET, help="Axiom Dataset (default: elite)")
    parser.add_argument("--minutes", "-m", type=int, default=15, help="Minutes to look back (default: 15)")
    parser.add_argument("--errors-only", "-e", action="store_true", help="Filter for ERROR / Tracebacks only")
    parser.add_argument("--filter", "-f", type=str, default=None, help="Text filter (e.g. FILING_WATCHER)")
    parser.add_argument("--limit", "-l", type=int, default=100, help="Max records (default: 100)")
    parser.add_argument("--watch", "-w", type=int, default=0, help="Continuous watch mode interval in seconds (e.g. 10)")
    parser.add_argument("--diagnose", action="store_true", help="Run automated error diagnostics")

    args = parser.parse_args()

    token = args.token
    if not token:
        print("❌ Error: No Axiom token provided.")
        print("Please set AXIOM_TOKEN in .env or pass --token <token>")
        print("Get your token from: https://app.axiom.co/elite-9xcn/settings/tokens")
        sys.exit(1)

    print(f"📡 Connecting to Axiom (Org: {args.org}, Dataset: {args.dataset})...")

    if args.watch > 0:
        print(f"👀 Continuous watch mode active (polling every {args.watch}s). Press Ctrl+C to stop.")
        seen_times = set()
        while True:
            try:
                matches = fetch_logs(
                    token=token,
                    org_id=args.org,
                    dataset=args.dataset,
                    minutes=args.minutes,
                    errors_only=args.errors_only,
                    query_filter=args.filter,
                    limit=args.limit
                )
                new_logs = 0
                for m in matches:
                    t = m.get("_time")
                    if t not in seen_times:
                        seen_times.add(t)
                        print(format_log_entry(m))
                        new_logs += 1
                time.sleep(args.watch)
            except KeyboardInterrupt:
                print("\n⏹️ Stopped log watch.")
                break
            except Exception as e:
                print(f"❌ Error polling logs: {e}")
                time.sleep(args.watch)
    else:
        try:
            matches = fetch_logs(
                token=token,
                org_id=args.org,
                dataset=args.dataset,
                minutes=args.minutes,
                errors_only=args.errors_only,
                query_filter=args.filter,
                limit=args.limit
            )
            print(f"📋 Fetched {len(matches)} log entries (last {args.minutes} min):")
            print("-" * 70)
            for m in matches:
                print(format_log_entry(m))
            print("-" * 70)
            
            if args.diagnose or args.errors_only:
                run_diagnostics(matches)
        except PermissionError as pe:
            print(f"\n{pe}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
