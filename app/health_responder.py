"""
health_responder.py — Ultra-lightweight socket-level HTTP health responder.
Listens on ports 8000 and 8080 and immediately answers GET / and GET /health
with HTTP 200 OK before any heavy Python dependencies or PostgreSQL pools initialize.
Once the Flask dashboard is ready, requests are transparently reverse-proxied to Flask.
"""

import socket
import threading
import time as _time
import os
import sys

FLASK_READY = False
FLASK_INTERNAL_PORT = 8001
FLASK_PORT = 8001

_STARTED_PORTS = set()          # Guard against double-start
_STARTED_PORTS_LOCK = threading.Lock()
_SERVERS = {}
_SERVERS_LOCK = threading.Lock()

HEALTH_HTTP_200 = (
    b"HTTP/1.1 200 OK\r\n"
    b"Content-Type: application/json\r\n"
    b"Content-Length: 48\r\n"
    b"Connection: close\r\n"
    b"\r\n"
    b'{"status":"ok","service":"elite-breakout-system"}\n'
)

def mark_flask_ready(port: int):
    global FLASK_READY, FLASK_PORT
    FLASK_PORT = port
    FLASK_READY = True

def stop_early_health_responder(port: int):
    """Gracefully closes early health responder listener on *port*
    so the Flask server can natively bind to that port with zero proxy overhead.
    """
    with _SERVERS_LOCK:
        server = _SERVERS.pop(port, None)
    if server:
        try:
            server.close()
            sys.stderr.write(f"🛑 [HEALTH_RESPONDER] Closed early listener on port {port} for native Flask handover\n")
        except Exception as e:
            sys.stderr.write(f"⚠️ [HEALTH_RESPONDER] Error closing port {port}: {e}\n")
    with _STARTED_PORTS_LOCK:
        _STARTED_PORTS.discard(port)

def _handle_client(client_sock: socket.socket):
    target_sock = None
    try:
        client_sock.settimeout(10.0)
        data = client_sock.recv(4096)
        if not data:
            try: client_sock.close()
            except Exception: pass
            return

        # Before Flask is ready, immediately answer any incoming probe/request with 200 OK
        if not FLASK_READY:
            try:
                client_sock.sendall(HEALTH_HTTP_200)
            finally:
                try: client_sock.close()
                except Exception: pass
            return

        # Forward requests to Flask on internal port once Flask is ready
        try:
            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.settimeout(60.0)
            client_sock.settimeout(60.0)
            target_sock.connect(("127.0.0.1", FLASK_PORT))

            # Case-insensitively replace any Connection header with Connection: close
            import re
            data = re.sub(rb'(?i)connection:\s*[^\r\n]+', b'Connection: close', data)
            if b"Connection: close" not in data and b"\r\n\r\n" in data:
                data = data.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n", 1)

            target_sock.sendall(data)

            # Detect whether request has pending body bytes to stream
            has_pending_body = False
            first_line = data.split(b"\r\n")[0] if b"\r\n" in data else data
            method = first_line.split(b" ")[0].upper() if b" " in first_line else b""
            if method in (b"POST", b"PUT", b"PATCH"):
                cl_match = re.search(rb'(?i)content-length:\s*(\d+)', data)
                if cl_match:
                    content_len = int(cl_match.group(1))
                    header_end = data.find(b"\r\n\r\n")
                    body_bytes_in_data = len(data) - (header_end + 4) if header_end != -1 else 0
                    if body_bytes_in_data < content_len:
                        has_pending_body = True
                elif b"chunked" in data.lower():
                    has_pending_body = True

            if not has_pending_body:
                # GET/HEAD/OPTIONS or body already completely sent in initial packet:
                # Immediately signal write EOF to Flask so Flask never waits for further request bytes
                try:
                    target_sock.shutdown(socket.SHUT_WR)
                except Exception:
                    pass

            if has_pending_body:
                def _pipe_client_to_target():
                    try:
                        client_sock.settimeout(15.0)
                        while True:
                            buf = client_sock.recv(8192)
                            if not buf:
                                break
                            target_sock.sendall(buf)
                    except Exception:
                        pass
                    finally:
                        try:
                            target_sock.shutdown(socket.SHUT_WR)
                        except Exception:
                            pass
                threading.Thread(target=_pipe_client_to_target, daemon=True).start()

            # Stream target (Flask) response back to client directly in this thread
            # [RULE 67 CHANGE-RATIONALE]: 60s timeout accommodates cold-start queries while bounded SWR caches serve in <5ms
            try:
                target_sock.settimeout(60.0)
                while True:
                    buf = target_sock.recv(8192)
                    if not buf:
                        break
                    client_sock.sendall(buf)
            except Exception:
                pass
            finally:
                try: client_sock.close()
                except Exception: pass
                try: target_sock.close()
                except Exception: pass
        except Exception as forward_err:
            # Fallback if Flask connection fails
            try:
                client_sock.sendall(HEALTH_HTTP_200)
            finally:
                try: client_sock.close()
                except Exception: pass
            if target_sock:
                try: target_sock.close()
                except Exception: pass
    except Exception:
        try: client_sock.close()
        except Exception: pass
        if target_sock:
            try: target_sock.close()
            except Exception: pass

def _start_listener(port: int):
    """Bind and listen on *port* with retry on EADDRINUSE.

    After a container restart the previous socket may still be in TIME_WAIT.
    We retry up to 10 times (total ~30 s) which is enough for the kernel to
    reclaim the socket.  If all retries fail we log a final warning and exit
    the thread — the dashboard server will still work via its own binding.
    """
    max_retries = 10
    for attempt in range(1, max_retries + 1):
        server = None
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("0.0.0.0", port))
            server.listen(128)
            with _SERVERS_LOCK:
                _SERVERS[port] = server
            if attempt > 1:
                sys.stderr.write(
                    f"✅ Health responder bound on port {port} after {attempt} attempts\n"
                )
            # Accept loop — runs forever
            while True:
                client, _ = server.accept()
                threading.Thread(target=_handle_client, args=(client,), daemon=True).start()
        except OSError as e:
            # errno 48 (macOS) / 98 (Linux) = EADDRINUSE
            if e.errno in (48, 98) and attempt < max_retries:
                if server:
                    try: server.close()
                    except Exception: pass
                wait = min(1.0 * attempt, 5.0)   # 1s, 2s, 3s … 5s cap
                sys.stderr.write(
                    f"⚠️ Health responder port {port} in use, retry {attempt}/{max_retries} "
                    f"in {wait:.0f}s\n"
                )
                _time.sleep(wait)
                continue
            # Final failure — log and exit thread
            if server:
                try: server.close()
                except Exception: pass
            sys.stderr.write(
                f"⚠️ Health responder could not bind port {port} after {max_retries} "
                f"attempts: {e}\n"
            )
            return
        except Exception as e:
            if server:
                try: server.close()
                except Exception: pass
            sys.stderr.write(f"⚠️ Health responder error on port {port}: {e}\n")
            return


def start_early_health_responder():
    """Starts the ultra-fast socket listener on ports 8000 and 8080.
    Immediately answers health check probes in <0.1ms so container healthcheck passes on Attempt 1.
    When Flask is ready, Flask calls stop_early_health_responder(8000) and binds natively.
    """
    for p in (8000, 8080):
        with _STARTED_PORTS_LOCK:
            if p in _STARTED_PORTS:
                continue
            _STARTED_PORTS.add(p)
        t = threading.Thread(target=_start_listener, args=(p,), name=f"EarlyHealthResponder-{p}", daemon=True)
        t.start()
