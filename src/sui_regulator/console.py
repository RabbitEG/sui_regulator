"""Loopback-only HTTP adapter. Owns no ledger, controller, worker or job."""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .gateway import call
from .models import DomainError, encode

READ_METHODS = {
    "yan.get_console_snapshot",
    "yan.get_task",
    "yan.get_job",
    "yan.get_metrics",
    "yan.get_incidents",
    "yan.get_events",
    "yan.get_command_status",
    "yan.get_logs",
}
WRITE_METHODS = {"yan.hold_task", "yan.resume_task", "yan.instruct_task"}
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/assets/console.css": ("console.css", "text/css; charset=utf-8"),
    "/assets/console.js": ("console.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class Broker:
    """Single-flight, bounded RPC reads; cached timestamps never become fresh."""

    def __init__(self, home: Path, ttl=2.0):
        self.home, self.ttl = home, ttl
        self.lock = threading.Lock()
        self.cache = OrderedDict()
        self.bytes = 0
        self.failed_at = -100.0
        self.failure = None

    @staticmethod
    def wrapped(response, observed_at, available=True, cached=False, error=None):
        return {
            "response": response,
            "connection": {
                "available": available,
                "cached": cached,
                "last_success_at": observed_at,
                "error": error,
            },
        }

    def read(self, method, params):
        if method not in READ_METHODS:
            raise DomainError("METHOD_NOT_ALLOWED", "console read method is not allowed")
        key = encode([method, params])
        with self.lock:
            old = self.cache.get(key)
            now = time.monotonic()
            if self.failure and now - self.failed_at < self.ttl:
                return self.wrapped(
                    old[0] if old else None, old[1] if old else None, False, bool(old), self.failure
                )
            if old and now - old[2] < self.ttl:
                self.cache.move_to_end(key)
                return self.wrapped(old[0], old[1], cached=True)
            try:
                response = call(self.home, method, params, timeout=1.5)
            except (OSError, ValueError, DomainError) as exc:
                self.failed_at = time.monotonic()
                self.failure = {"code": "CONTROLLER_UNREACHABLE", "message": str(exc)}
                return self.wrapped(
                    old[0] if old else None, old[1] if old else None, False, bool(old), self.failure
                )
            observed_at = time.time()
            self.failure = None
            if "error" not in response:
                size = len(encode(response).encode())
                if old:
                    self.bytes -= self.cache.pop(key)[3]
                self.cache[key] = (response, observed_at, time.monotonic(), size)
                self.bytes += size
                while len(self.cache) > 64 or self.bytes > 8 * 1024 * 1024:
                    self.bytes -= self.cache.popitem(last=False)[1][3]
            return self.wrapped(response, observed_at)

    def write(self, request, confirmed=False):
        method = request.get("method")
        if method not in WRITE_METHODS:
            raise DomainError("METHOD_NOT_ALLOWED", "console supports Hold, Resume and Instruct only")
        if method == "yan.hold_task" and confirmed is not True:
            raise DomainError(
                "CONFIRMATION_REQUIRED", "Hold stops the Sui session; explicit confirmation required"
            )
        params = request.get("params")
        if not isinstance(params, dict) or not params.get("idempotency_key"):
            raise DomainError("INVALID_ARGUMENT", "a stable idempotency key is required")
        with self.lock:
            try:
                health = call(
                    self.home, "yan.get_console_snapshot", {"section": "overview", "limit": 1}, timeout=1.5
                )
                if "error" in health or health["result"]["controller"]["state"] != "ONLINE":
                    raise DomainError(
                        "CONTROLLER_NOT_READY", "controller tick is not healthy; command not sent"
                    )
                response = call(self.home, method, params, timeout=1.5)
            except (OSError, ValueError) as exc:
                # The command may have committed before a lost reply. Never make
                # up a receipt or retry with a new idempotency key.
                raise DomainError(
                    "DELIVERY_UNCONFIRMED", "delivery unconfirmed; retain the same idempotency key"
                ) from exc
            self.cache.clear()
            self.bytes = 0
            self.failure = None
            return self.wrapped(response, time.time())


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(0.2)
                request.sendall(b"HTTP/1.0 503 Busy\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Console(AbstractContextManager):
    def __init__(self, home: Path, port: int = 8765):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("invalid console port")
        self.home = home.expanduser().absolute()
        self.broker = Broker(self.home)
        console = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def send(self, code, body, content_type="application/json; charset=utf-8"):
                data = body if isinstance(body, bytes) else encode(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                )
                self.end_headers()
                self.wfile.write(data)

            def guard(self, write=False):
                host = self.headers.get("Host", "")
                try:
                    parsed = urlsplit("http://" + host)
                    valid = (
                        parsed.hostname in {"localhost", "127.0.0.1"}
                        and parsed.port is not None
                        and not parsed.username
                        and not parsed.password
                        and not parsed.path
                        and not parsed.query
                        and not parsed.fragment
                    )
                except ValueError:
                    valid = False
                if not valid:
                    raise DomainError("FORBIDDEN_HOST", "only loopback Host names are accepted")
                origin = self.headers.get("Origin")
                if origin is not None and origin != "http://" + host:
                    raise DomainError("FORBIDDEN_ORIGIN", "cross-origin requests are not allowed")
                if self.headers.get("Sec-Fetch-Site") == "cross-site":
                    raise DomainError("FORBIDDEN_ORIGIN", "cross-site requests are not allowed")
                if write and (origin is None or self.headers.get("X-Yan-Console") != "1"):
                    raise DomainError("FORBIDDEN_ORIGIN", "same-origin console header is required")

            def do_GET(self):
                try:
                    self.guard()
                    url = urlsplit(self.path)
                    if url.path in ASSETS:
                        name, mime = ASSETS[url.path]
                        self.send(200, files("sui_regulator").joinpath("static", name).read_bytes(), mime)
                    elif url.path == "/api/snapshot":
                        query = parse_qs(url.query, keep_blank_values=True)
                        if set(query) - {"section", "offset", "limit"} or any(
                            len(v) != 1 for v in query.values()
                        ):
                            raise ValueError("invalid snapshot parameters")
                        params = {
                            k: int(v[0]) if k in {"offset", "limit"} else v[0] for k, v in query.items()
                        }
                        self.send(200, console.broker.read("yan.get_console_snapshot", params))
                    else:
                        self.send(404, {"error": {"code": "NOT_FOUND", "message": "resource not found"}})
                except (DomainError, ValueError) as exc:
                    self.send(
                        403 if isinstance(exc, DomainError) else 400,
                        {"error": {"code": getattr(exc, "code", "INVALID_ARGUMENT"), "message": str(exc)}},
                    )

            def do_POST(self):
                try:
                    self.guard(write=True)
                    if self.path != "/api/rpc":
                        raise DomainError("METHOD_NOT_ALLOWED", "unknown console endpoint")
                    if self.headers.get_content_type() != "application/json" or self.headers.get(
                        "Transfer-Encoding"
                    ):
                        raise ValueError("application/json and a bounded Content-Length are required")
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1 or not 0 < int(lengths[0]) <= 65536:
                        raise ValueError("request body must be in [1, 65536] bytes")
                    body = json.loads(self.rfile.read(int(lengths[0])))
                    if not isinstance(body, dict) or set(body) - {"request", "confirmed"}:
                        raise ValueError("invalid console request")
                    request = body.get("request")
                    if not isinstance(request, dict) or set(request) - {"method", "params"}:
                        raise ValueError("invalid RPC request")
                    method = request.get("method")
                    result = (
                        console.broker.read(method, request.get("params", {}))
                        if method in READ_METHODS
                        else console.broker.write(request, body.get("confirmed", False))
                    )
                    self.send(200, result)
                except (DomainError, ValueError, TypeError) as exc:
                    code = getattr(exc, "code", "INVALID_ARGUMENT")
                    status = (
                        503
                        if code in {"DELIVERY_UNCONFIRMED", "CONTROLLER_NOT_READY"}
                        else 403
                        if code.startswith("FORBIDDEN") or code == "METHOD_NOT_ALLOWED"
                        else 400
                    )
                    self.send(status, {"error": {"code": code, "message": str(exc)}})

        self.server = _HTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = None

    def __enter__(self):
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        return self

    def __exit__(self, *args):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=4)
        self.server.server_close()


def run_console(home: Path, port: int):
    with Console(home, port) as console:
        print(
            encode(
                {"console": console.url, "controller_home": str(console.home), "binding": "loopback only"}
            ),
            flush=True,
        )
        try:
            console.thread.join()
        except KeyboardInterrupt:
            pass
