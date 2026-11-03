"""Local stdio MCP bridge. Worker role has no Yan/registration capabilities."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .gateway import MAX_MESSAGE_BYTES, Dispatcher
from .models import Credentials, DomainError, encode
from .protocol import SuiProtocol
from .service import Service

WORKER_TOOLS = {
    "cancel_job": ({"job_id": {"type": "string"}}, ["job_id"]),
    "get_task_context": ({}, []),
    "report_progress": (
        {
            "phase": {"type": "string"},
            "summary": {"type": "string"},
            "job_ids": {"type": "array", "items": {"type": "string"}},
            "next_action": {"type": "string"},
            "blockers": {"type": "array"},
        },
        ["phase", "summary"],
    ),
    "report_blocker": (
        {
            "reason": {"type": "string"},
            "waiting_for": {"type": "string", "enum": ["user", "resource", "external"]},
        },
        ["reason"],
    ),
    "launch_job": ({"logical_key": {"type": "string"}, "spec": {"type": "object"}}, ["logical_key"]),
    "attach_job": ({"job_id": {"type": "string"}}, ["job_id"]),
    "run_command": (
        {"template": {"type": "string"}, "arguments": {"type": "array", "items": {"type": "string"}}},
        ["template"],
    ),
    "publish_artifact": ({"path": {"type": "string"}, "kind": {"type": "string"}}, ["path"]),
    "handoff": ({"summary": {"type": "object"}}, ["summary"]),
    "complete_task": ({"summary": {"type": "string"}, "evidence": {"type": "string"}}, ["summary"]),
}


class MCPBridge:
    def __init__(self, service: Service, credentials: Credentials | None, mode: str = "worker"):
        self.service, self.credentials, self.mode = service, credentials, mode
        self.protocol = SuiProtocol(service)

    def tools(self) -> list[dict]:
        if self.mode == "yan":
            return [
                {
                    "name": "yan_request",
                    "description": "Query or enqueue a local authenticated Yan request. Requests are data; command success is a persisted receipt.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"request": {"type": "object"}},
                        "required": ["request"],
                        "additionalProperties": False,
                    },
                }
            ]
        return [
            {
                "name": name,
                "description": f"Sui Protocol 0.1 {name}; scoped to the current worker lease. Existing logical job keys attach without relaunch.",
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            }
            for name, (properties, required) in WORKER_TOOLS.items()
        ]

    def invoke(self, name: str, arguments: dict):
        if not isinstance(arguments, dict):
            raise DomainError("INVALID_ARGUMENT", "arguments must be an object")
        if self.mode == "yan":
            if name != "yan_request" or set(arguments) != {"request"}:
                raise DomainError("UNKNOWN_METHOD", "Yan exposes only yan_request")
            return Dispatcher(self.service).dispatch(arguments["request"], f"uid:{os.getuid()}")
        if name not in WORKER_TOOLS or self.credentials is None:
            raise DomainError("FORBIDDEN", "worker tool or credentials unavailable")
        properties, required = WORKER_TOOLS[name]
        if set(arguments) - properties.keys() or not set(required) <= arguments.keys():
            raise DomainError("INVALID_ARGUMENT", "tool argument fields do not match schema")
        for key, value in arguments.items():
            expected = properties[key]["type"]
            if expected == "array":
                ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
            else:
                ok = isinstance(value, {"string": str, "array": list, "object": dict}[expected])
            if not ok:
                raise DomainError("INVALID_ARGUMENT", f"invalid type for {key}")
        if name == "run_command":
            from .commands import run_command

            return run_command(self.service, self.credentials, **arguments)
        return getattr(self.protocol, name)(self.credentials, **arguments)

    def handle(self, message: dict) -> dict | None:
        if not isinstance(message, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "object required"}}
        if "id" not in message:
            return None
        rid, method, params = message["id"], message.get("method"), message.get("params", {})
        try:
            if message.get("jsonrpc") != "2.0" or not isinstance(params, dict):
                raise DomainError("INVALID_REQUEST", "invalid JSON-RPC envelope")
            if method == "initialize":
                version = params.get("protocolVersion", "2024-11-05")
                result = {
                    "protocolVersion": version
                    if version in {"2024-11-05", "2025-03-26", "2025-06-18"}
                    else "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sui-regulator", "version": __version__},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                try:
                    value = self.invoke(params.get("name"), params.get("arguments", {}))
                    result = {"content": [{"type": "text", "text": encode(value)}], "isError": False}
                except (DomainError, TypeError, ValueError, KeyError) as exc:
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": encode(
                                    {"code": getattr(exc, "code", "INVALID_ARGUMENT"), "message": str(exc)}
                                ),
                            }
                        ],
                        "isError": True,
                    }
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": "method unavailable"},
                }
            return {"jsonrpc": "2.0", "id": rid, "result": result}
        except DomainError as exc:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32600, "message": str(exc)}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--mode", choices=["worker", "yan"], default="worker")
    args = parser.parse_args()
    credentials = (
        Credentials(**json.loads(os.environ["SUI_WORKER_CREDENTIALS"])) if args.mode == "worker" else None
    )
    bridge = MCPBridge(Service(Config.load(args.home)), credentials, args.mode)
    while raw := sys.stdin.buffer.readline(MAX_MESSAGE_BYTES + 1):
        try:
            if len(raw) > MAX_MESSAGE_BYTES or not raw.endswith(b"\n"):
                break
            result = bridge.handle(json.loads(raw))
        except (ValueError, UnicodeError):
            result = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "invalid JSON"}}
        if result is not None:
            print(encode(result), flush=True)


if __name__ == "__main__":
    main()
