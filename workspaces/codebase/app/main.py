# =====================================================================================
# app/main.py  — launches all scanners in parallel threads + health HTTP server
# =====================================================================================

import sys
import os
import threading
import logging
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from zoneinfo import ZoneInfo
from datetime import datetime, time as dt_time

# =====================================================================================
# PATH FIX
# =====================================================================================

APP_DIR = os.path.dirname(os.path.abspath(__file__))

if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# =====================================================================================
# LOGGING
# =====================================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)

logger.info(f"📁 APP_DIR resolved to: {APP_DIR}")

# =====================================================================================
# HEALTH HTTP SERVER
# Provides a minimal /health endpoint for platform health checks on port 8000.
# Started FIRST — before any blocking initialization — so the platform can probe
# the container immediately after startup.
# =====================================================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress default access logs to keep output clean
        pass


def _start_health_server():
    port = int(os.environ.get("PORT", 8000))
    try:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        logger.info(f"🌐 Health server listening on 0.0.0.0:{port}")
        server.serve_forever()
    except Exception as exc:
        logger.exception(f"❌ Health server failed to start: {exc}")


_health_thread = threading.Thread(
    target=_start_health_server,
    name="HealthServer",
    daemon=True
)
_health_thread.start()

# Give the health server a moment to bind before proceeding
time.sleep(1)

# =====================================================================================
# TIMEZONE
# =====================================================================================

IST = ZoneInfo("Asia/Kolkata")

# =====================================================================================
# WINDOW DEFINITIONS
# =====================================================================================

WINDOWS = {

    "intraday": (
        dt_time(9, 32),
        dt_time(15, 30)
    ),

    "live": (
        dt_time(10, 17),
        dt_time(15, 30)
    ),

    "eod": (
        dt_time(15, 45),
        dt_time(16, 15)
    ),
}

# =====================================================================================
# WAIT FOR WINDOW
# =====================================================================================

def wait_for_window(name: str):

    start_time, _ = WINDOWS[name]

    while True:

        now = datetime.now(IST)

        weekday = now.weekday()

        if weekday >= 5:

            logger.info(
                f"[{name}] 📅 Weekend detected | "
                f"Sleeping 1 hour..."
            )

            time.sleep(3600)

            continue

        if now.time() >= start_time:

            logger.info(
                f"[{name}] ✅ Window open | "
                f"{now.strftime('%H:%M:%S')} | "
                f"Launching scanner"
            )

            return

        logger.info(
            f"[{name}] ⏰ Waiting for "
            f"{start_time.strftime('%H:%M')} | "
            f"Current={now.strftime('%H:%M:%S')}"
        )

        time.sleep(60)

# =====================================================================================
# WATCHLIST PRE-FLIGHT
# =====================================================================================

WATCHLIST_PATH = (
    "/app/data/"
    "elite_fundamental_watchlist.parquet"
)

if not os.path.exists(WATCHLIST_PATH):

    logger.info(
        "📋 Watchlist missing | "
        "Running daily builder..."
    )

    try:

        from daily_builder import main as build_watchlist

        build_watchlist()

        logger.info(
            "✅ Watchlist built successfully"
        )

    except Exception:

        logger.exception(
            "❌ Daily builder failed"
        )

else:

    logger.info(
        f"✅ Watchlist found | "
        f"{WATCHLIST_PATH}"
    )

# =====================================================================================
# SCANNER THREADS
# =====================================================================================

def run_intraday_scanner():

    wait_for_window("intraday")

    logger.info(
        "⚡ Starting INTRADAY SCANNER "
        "(15m candles)"
    )

    import intraday


def run_live_scanner():

    wait_for_window("live")

    logger.info(
        "🚀 Starting LIVE SCANNER "
        "(1h candles)"
    )

    import live_scanner


def run_eod_scanner():

    wait_for_window("eod")

    logger.info(
        "📊 Starting EOD SCANNER "
        "(Daily candles)"
    )

    import eod_scanner

# =====================================================================================
# MAIN
# =====================================================================================

if __name__ == "__main__":

    scanner_threads = [

        threading.Thread(
            target=run_intraday_scanner,
            name="IntradayScanner",
            daemon=True
        ),

        threading.Thread(
            target=run_live_scanner,
            name="LiveScanner",
            daemon=True
        ),

        threading.Thread(
            target=run_eod_scanner,
            name="EODScanner",
            daemon=True
        ),
    ]

    for t in scanner_threads:
        t.start()

    logger.info("=" * 70)
    logger.info("✅ ALL SCANNER THREADS STARTED")
    logger.info("⚡ intraday.py      | 15m | Opens 09:32 AM")
    logger.info("🚀 live_scanner.py | 1h  | Opens 10:17 AM")
    logger.info("📊 eod_scanner.py  | 1D  | Opens 03:45 PM")
    logger.info("🌐 health server   | port 8000 | /health")
    logger.info("=" * 70)

    # Keep main thread alive — scanner threads are daemon so we join them
    for t in scanner_threads:
        t.join()
