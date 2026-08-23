"""
app/accumulation/admin.py — Isolated Admin CLI for ACCUMULATION_SCANNER_V1.
Provides command-line administrative controls for pause, resume, stop, status, and manual run requests.
Usage:
    python3 -m app.accumulation.admin status
    python3 -m app.accumulation.admin pause --reason "Maintenance"
    python3 -m app.accumulation.admin resume
    python3 -m app.accumulation.admin run-now
"""

import sys
import argparse
import logging
from app.accumulation.control import AccumulationControlPlane

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description="ACCUMULATION_SCANNER_V1 Admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    subparsers.add_parser("status", help="Show accumulation scanner control status")

    # pause
    pause_parser = subparsers.add_parser("pause", help="Pause accumulation scanner execution")
    pause_parser.add_argument("--reason", type=str, default="Paused via Admin CLI", help="Reason for pause")

    # resume
    subparsers.add_parser("resume", help="Resume accumulation scanner execution")

    # stop
    stop_parser = subparsers.add_parser("stop", help="Request emergency stop of accumulation scanner")
    stop_parser.add_argument("--reason", type=str, default="Stop requested via Admin CLI", help="Reason for stop")

    # run-now
    subparsers.add_parser("run-now", help="Trigger an out-of-schedule manual scan pass")

    args = parser.parse_args()

    if args.command == "status":
        state = AccumulationControlPlane.get_control_state()
        print("\n=== ACCUMULATION_SCANNER_V1 Control Status ===")
        print(f"  Enabled:              {state.get('enabled')}")
        print(f"  Paused:               {state.get('paused')}")
        print(f"  Stop Requested:       {state.get('stop_requested')}")
        print(f"  Manual Run Requested: {state.get('manual_run_requested')}")
        print(f"  Reason:               {state.get('reason')}\n")

    elif args.command == "pause":
        res = AccumulationControlPlane.update_control_state(paused=True, reason=args.reason)
        if res:
            print(f"SUCCESS: ACCUMULATION_SCANNER_V1 paused. Reason: '{args.reason}'")
        else:
            print("ERROR: Failed to update control state.")

    elif args.command == "resume":
        res = AccumulationControlPlane.update_control_state(paused=False, stop_requested=False, reason="Resumed via Admin CLI")
        if res:
            print("SUCCESS: ACCUMULATION_SCANNER_V1 resumed.")
        else:
            print("ERROR: Failed to update control state.")

    elif args.command == "stop":
        res = AccumulationControlPlane.update_control_state(stop_requested=True, reason=args.reason)
        if res:
            print(f"SUCCESS: ACCUMULATION_SCANNER_V1 stop requested. Reason: '{args.reason}'")
        else:
            print("ERROR: Failed to update control state.")

    elif args.command == "run-now":
        res = AccumulationControlPlane.update_control_state(manual_run=True, reason="Manual run requested via Admin CLI")
        if res:
            print("SUCCESS: Out-of-schedule manual scan pass requested.")
        else:
            print("ERROR: Failed to request manual run.")

if __name__ == "__main__":
    main()
