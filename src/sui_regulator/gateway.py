from __future__ import annotations

import json
import os
import socket
import socketserver
import struct
import threading
import uuid
from contextlib import AbstractContextManager
from pathlib import Path

from . import PROTOCOL_VERSION
from .models import DomainError, encode, require_text
from .processes import FileLock
from .service import COMMAND_KINDS, Service, unpack

MAX_MESSAGE_BYTES = 262144

QUERY_FIELDS = {
    "get_console_snapshot": {"section", "offset", "limit"},
    "get_incidents": {"task_id", "after", "limit"},
    "get_overview": set(),
    "list_tasks": set(),
    "get_task": {"task_id"},
    "get_job": {"job_id"},
    "get_metrics": {"job_id"},
    "get_logs": {"job_id", "limit"},
    "get_worker_health": {"task_id"},
    "get_proxy_health": {"task_id"},
    "get_command_status": {"command_id"},
    "get_events": {"task_id", "after", "limit"},
    "list_artifacts": {"task_id"},
    "list_experiments": {"task_id"},
    "get_experiment": {"experiment_id"},
}


def request_schema() -> dict:
    identifier = {"type": "string", "minLength": 1, "maxLength": 128}
    variants = []
    for name in sorted(COMMAND_KINDS | QUERY_FIELDS.keys()):
        if name in COMMAND_KINDS:
            properties = {
                "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 256},
                "payload": {"type": "object"},
            }
            required = ["idempotency_key", "payload"]
            if name != "submit_task":
                properties["task_id"] = identifier
                required.append("task_id")
            if name in {"hold_task", "resume_task", "cancel_task"}:
                properties["payload"] = {"type": "object", "maxProperties": 0}
            elif name in {"instruct_task", "request_analysis"}:
                properties["payload"] = {
                    "type": "object",
                    "properties": {"instruction": {"type": "string", "minLength": 1, "maxLength": 16384}},
                    "required": ["instruction"],
                    "additionalProperties": False,
                }
            elif name == "cancel_job":
                properties["payload"] = {
                    "type": "object",
                    "properties": {"job_id": identifier},
                    "required": ["job_id"],
                    "additionalProperties": False,
                }
            else:
                properties["payload"] = {
                    "type": "object",
                    "required": ["title"],
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string", "minLength": 1, "maxLength": 512},
                        "objective": {"type": "string", "minLength": 1, "maxLength": 16384},
                        "adapter": {"enum": ["mock", "claude", "unavailable"]},
                        "spec": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "steps": {"type": "integer", "minimum": 1, "maximum": 100000},
                                "interval": {"type": "number", "minimum": 0.001, "maximum": 60},
                                "fail_at": {"type": ["integer", "null"], "minimum": 1},
                                "resource_available": {"type": "boolean"},
                            },
                        },
                    },
                }
        else:
            properties = {field: identifier for field in QUERY_FIELDS[name] if field.endswith("_id")}
            if name == "get_console_snapshot":
                properties.update(
                    section={"enum": ["overview", "sessions", "tasks", "incidents"]},
                    offset={"type": "integer", "minimum": 0, "maximum": 10000000},
                )
            if "after" in QUERY_FIELDS[name]:
                properties["after"] = {"type": "integer", "minimum": 0}
            if "limit" in QUERY_FIELDS[name]:
                properties["limit"] = {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100 if name == "get_console_snapshot" else 500 if name == "get_logs" else 1000,
                }
            required = (
                [] if name == "get_events" else sorted(field for field in properties if field.endswith("_id"))
            )
        variants.append(
            {
                "properties": {
                    "method": {"const": "yan." + name},
                    "params": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                }
            }
        )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["protocol_version", "method", "params"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": ["string", "integer", "null"]},
            "protocol_version": {"const": PROTOCOL_VERSION},
            "method": {"enum": sorted("yan." + kind for kind in COMMAND_KINDS | QUERY_FIELDS.keys())},
            "params": {"type": "object"},
        },
        "oneOf": variants,
    }


class Dispatcher:
    def __init__(self, service: Service):
        self.service = service

    def dispatch(self, request: dict, actor: str) -> dict:
        rid = request.get("id") if isinstance(request, dict) else None
        try:
            if not isinstance(request, dict) or set(request) - {"id", "protocol_version", "method", "params"}:
                raise DomainError("INVALID_REQUEST", "invalid envelope fields")
            if request.get("protocol_version") != PROTOCOL_VERSION:
                raise DomainError("PROTOCOL_VERSION", "supported protocol_version is 0.1")
            if rid is not None and (type(rid) not in (str, int) or (isinstance(rid, str) and len(rid) > 256)):
                raise DomainError("INVALID_REQUEST", "invalid request id")
            method, params = request.get("method"), request.get("params")
            if not isinstance(method, str) or not method.startswith("yan.") or not isinstance(params, dict):
                raise DomainError("INVALID_REQUEST", "expected yan method and object params")
            kind = method[4:]
            if kind in COMMAND_KINDS:
                if (
                    set(params) - {"idempotency_key", "task_id", "payload"}
                    or not {"idempotency_key", "payload"} <= params.keys()
                ):
                    raise DomainError(
                        "INVALID_ARGUMENT",
                        "command requires idempotency_key and payload; actor is transport-derived",
                    )
                if kind == "submit_task" and "task_id" in params:
                    raise DomainError("INVALID_ARGUMENT", "submit_task allocates its own task_id")
                result = self.service.command(
                    kind, actor, params["idempotency_key"], params["payload"], params.get("task_id")
                )
            else:
                if kind not in QUERY_FIELDS:
                    raise DomainError("UNKNOWN_METHOD", "method is not available")
                if set(params) - QUERY_FIELDS[kind]:
                    raise DomainError("INVALID_ARGUMENT", "unexpected query parameters")
                required = (
                    {field for field in QUERY_FIELDS[kind] if field.endswith("_id")}
                    if kind != "get_events"
                    else set()
                )
                if not required <= params.keys():
                    raise DomainError("INVALID_ARGUMENT", "missing query identifier")
                for key, value in params.items():
                    if key.endswith("_id"):
                        require_text(value, key, 128)
                result = self.query(kind, params)
            return {"id": rid, "protocol_version": PROTOCOL_VERSION, "result": result}
        except (DomainError, KeyError, TypeError, ValueError) as exc:
            return {
                "id": rid,
                "protocol_version": PROTOCOL_VERSION,
                "error": {"code": getattr(exc, "code", "INVALID_ARGUMENT"), "message": str(exc)},
            }

    def query(self, method: str, params: dict):
        service = self.service
        if method == "get_console_snapshot":
            from .console_read import snapshot

            return snapshot(service, **params)
        if method == "get_incidents":
            from .failures.persistence import list_incidents

            return list_incidents(service, **params)
        if method == "get_overview":
            return {
                "tasks": len(service.list_tasks()),
                "jobs": service.store.rows("SELECT status,COUNT(*) AS count FROM jobs GROUP BY status"),
                "source": "sqlite ledger",
            }
        if method == "list_tasks":
            return service.list_tasks()
        if method == "get_task":
            return service.get_task(params["task_id"])
        if method in {"get_job", "get_metrics"}:
            job = service.get_job(params["job_id"])
            return (
                job
                if method == "get_job"
                else {
                    key: job[key]
                    for key in ["id", "task_id", "status", "metric", "source", "observed_at", "stale"]
                }
            )
        if method == "get_command_status":
            return service.get_command(params["command_id"])
        if method == "get_events":
            return service.events(**params)
        if method in {"get_worker_health", "get_proxy_health", "list_artifacts"}:
            task = service.get_task(params["task_id"])
            return task["artifacts" if method == "list_artifacts" else "workers"]
        if method == "list_experiments":
            service.get_task(params["task_id"])
            return [
                unpack(row)
                for row in service.store.rows(
                    "SELECT * FROM experiments WHERE task_id=? ORDER BY created_at,id", (params["task_id"],)
                )
            ]
        if method == "get_experiment":
            with service.store.connection() as conn:
                experiment = unpack(service._get(conn, "experiments", params["experiment_id"]))
                experiment["jobs"] = [
                    service._job_view(row)
                    for row in conn.execute("SELECT * FROM jobs WHERE experiment_id=?", (experiment["id"],))
                ]
                return experiment
        if method == "get_logs":
            job = service.get_job(params["job_id"])
            limit = params.get("limit", 50)
            if type(limit) is not int or not 1 <= limit <= 500:
                raise DomainError("INVALID_ARGUMENT", "log limit must be in [1, 500]")
            path = service.config.home / "jobs" / job["id"] / "metrics.jsonl"
            lines = []
            if path.exists():
                with path.open("rb") as source:
                    source.seek(0, 2)
                    size = source.tell()
                    source.seek(max(0, size - 131072))
                    if size > 131072:
                        source.readline()
                    lines = source.read(131072).decode(errors="replace").splitlines()[-limit:]
            return {
                "job_id": job["id"],
                "source": "runner log; untrusted data",
                "lines": lines,
                "observed_at": job["observed_at"],
                "stale": job["stale"],
            }
        raise DomainError("UNKNOWN_METHOD", "query is unavailable")


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 16


class Gateway(AbstractContextManager):
    def __init__(self, service: Service):
        self.service = service
        self.path = service.config.home / "yan.sock"
        self.lock = FileLock(service.config.home / "gateway.lock")
        self.server = self.thread = None

    def __enter__(self):
        self.lock.__enter__()
        gateway = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.connection.settimeout(3)
                try:
                    _, uid, _ = struct.unpack(
                        "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    )
                    if uid != os.getuid():
                        return
                    raw = self.rfile.readline(MAX_MESSAGE_BYTES + 1)
                    if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                        response = {
                            "error": {
                                "code": "MESSAGE_TOO_LARGE",
                                "message": "bounded newline-terminated JSON required",
                            }
                        }
                    else:
                        response = Dispatcher(gateway.service).dispatch(json.loads(raw), f"uid:{uid}")
                except (ValueError, UnicodeError):
                    response = {"error": {"code": "INVALID_JSON", "message": "invalid JSON request"}}
                except (TimeoutError, ConnectionError, OSError):
                    return
                self.wfile.write((encode(response) + "\n").encode())

        try:
            if self.path.is_symlink() or (self.path.exists() and not self.path.is_socket()):
                raise ValueError("refusing to replace a non-socket gateway path")
            self.path.unlink(missing_ok=True)
            self.server = _Server(str(self.path), Handler)
            self.path.chmod(0o600)
            self.thread = threading.Thread(
                target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
            )
            self.thread.start()
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            if self.thread:
                self.thread.join(timeout=3)
            self.path.unlink(missing_ok=True)
        self.lock.__exit__(*args)


def call(home: Path, method: str, params: dict, *, timeout: float = 5) -> dict:
    request = {
        "id": uuid.uuid4().hex,
        "protocol_version": PROTOCOL_VERSION,
        "method": method,
        "params": params,
    }
    encoded = (encode(request) + "\n").encode()
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise DomainError("MESSAGE_TOO_LARGE", "request is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(home.absolute() / "yan.sock"))
        client.sendall(encoded)
        with client.makefile("rb") as source:
            raw = source.readline(4 * MAX_MESSAGE_BYTES + 1)
    if len(raw) > 4 * MAX_MESSAGE_BYTES:
        raise DomainError("MESSAGE_TOO_LARGE", "response is too large; narrow the query")
    return json.loads(raw)
