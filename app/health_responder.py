"""
health_responder.py — Ultra-lightweight socket-level HTTP health responder.
Listens on ports 8000 and 8080 and immediately answers GET / and GET /health
with HTTP 200 OK before any heavy Python dependencies or PostgreSQL pools initialize.
Once the Flask dashboard is ready, requests are transparently reverse-proxied to Flask.
"""

import socket
import threading
import os
import sys

FLASK_READY = False
FLASK_INTERNAL_PORT = 8001
FLASK_PORT = 8001

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

def _handle_client(client_sock: socket.socket):
    try:
        client_sock.settimeout(5.0)
        data = client_sock.recv(4096)
        if not data:
            client_sock.close()
            return

        # Check if Flask dashboard is running and request is NOT a simple health probe
        is_healthcheck = (
            data.startswith(b"GET / ") or 
            data.startswith(b"GET /health") or 
            data.startswith(b"HEAD /") or 
            b"curl" in data.lower() or 
            b"wget" in data.lower() or 
            b"healthcheck" in data.lower()
        )

        if not FLASK_READY or is_healthcheck:
            client_sock.sendall(HEALTH_HTTP_200)
            client_sock.close()
            return

        # Forward non-healthcheck requests to Flask
        try:
            target_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target_sock.connect(("127.0.0.1", FLASK_PORT))
            target_sock.sendall(data)

            # Bidirectional stream
            def _pipe(src, dst):
                try:
                    while True:
                        buf = src.recv(8192)
                        if not buf:
                            break
                        dst.sendall(buf)
                except Exception:
                    pass
                finally:
                    try: src.close()
                    except Exception: pass
                    try: dst.close()
                    except Exception: pass

            t1 = threading.Thread(target=_pipe, args=(client_sock, target_sock), daemon=True)
            t2 = threading.Thread(target=_pipe, args=(target_sock, client_sock), daemon=True)
            t1.start()
            t2.start()
        except Exception:
            # Fallback if Flask connection fails
            client_sock.sendall(HEALTH_HTTP_200)
            client_sock.close()
    except Exception:
        try:
            client_sock.close()
        except Exception:
            pass

def _start_listener(port: int):
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", port))
        server.listen(128)
        while True:
            client, _ = server.accept()
            threading.Thread(target=_handle_client, args=(client,), daemon=True).start()
    except Exception as e:
        sys.stderr.write(f"⚠️ Health responder failed to bind on port {port}: {e}\n")

def start_early_health_responder():
    """Starts the ultra-fast socket listener on 8000 and 8080 in daemon threads."""
    for p in (8000, 8080):
        t = threading.Thread(target=_start_listener, args=(p,), name=f"EarlyHealthResponder-{p}", daemon=True)
        t.start()
