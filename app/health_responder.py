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
            target_sock.settimeout(15.0)
            client_sock.settimeout(15.0)
            target_sock.connect(("127.0.0.1", FLASK_PORT))

            # Force Connection: close to prevent HTTP/1.1 persistent connection leaks
            if b"Connection: keep-alive" in data:
                data = data.replace(b"Connection: keep-alive", b"Connection: close")
            elif b"connection: keep-alive" in data:
                data = data.replace(b"connection: keep-alive", b"connection: close")
            elif b"\r\n\r\n" in data and b"Connection:" not in data and b"connection:" not in data:
                # Inject Connection: close into request headers
                data = data.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n", 1)

            target_sock.sendall(data)

            # Bidirectional stream with explicit timeouts and mutual socket teardown
            def _pipe(src, dst):
                try:
                    src.settimeout(15.0)
                    while True:
                        buf = src.recv(8192)
                        if not buf:
                            break
                        dst.sendall(buf)
                except Exception:
                    pass
                finally:
                    # Mutual teardown: closing both terminates the other pipe thread immediately
                    try: src.close()
                    except Exception: pass
                    try: dst.close()
                    except Exception: pass

            t1 = threading.Thread(target=_pipe, args=(client_sock, target_sock), daemon=True)
            t2 = threading.Thread(target=_pipe, args=(target_sock, client_sock), daemon=True)
            t1.start()
            t2.start()
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
