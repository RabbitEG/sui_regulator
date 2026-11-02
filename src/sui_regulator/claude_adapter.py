from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import Credentials, digest, encode
from .processes import child_environment, detached
from .protocol import SuiProtocol
from .service import Service


@dataclass
class StreamEvent:
    activity: bool = False
    anomaly: bool = False
    successful_tool: bool = False
    final: bool = False
    error: str | None = None
    session_id: str | None = None


def classify_error(text: str) -> str:
    if re.search(
        r"\b(?:401|403)\b|unauth|authentication|invalid.api.key|not.logged.in|login.required|api.key.*required",
        text,
        re.I,
    ):
        return "AUTH_REQUIRED"
    if re.search(
        r"ECONNRESET|ECONNREFUSED|\b503\b|overloaded|mid.stream|mid.response|connection.lost|timed.out|fetch.failed",
        text,
        re.I,
    ):
        return "TRANSPORT_FAILURE"
    if re.search(r"quota|credit|rate.limit|\b429\b", text, re.I):
        return "RESOURCE_BLOCKED"
    return "RUNTIME_ERROR"


class ClaudeStreamDetector:
    """Bounded deterministic observations; never interprets prose as a control command."""

    def __init__(self):
        self.last_text_hash = self.last_failed_call = None
        self.text_repeats = self.failed_repeats = 0
        self.api_failures = 0
        self.pending_calls: dict[str, str] = {}

    def feed(self, line: str) -> StreamEvent:
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            return StreamEvent(anomaly=True)
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            return StreamEvent(anomaly=True)
        event = StreamEvent()
        if value["type"] == "system" and value.get("subtype") == "init":
            event.session_id = value.get("session_id")
        if value["type"] == "system" and value.get("subtype") == "api_retry":
            self.api_failures += 1
            reason = classify_error(
                encode({"status": value.get("error_status"), "error": value.get("error")})
            )
            # The installed CLI retries even authentication failures. Stop those
            # immediately; bound repeated transient retries independently of age.
            if reason in {"AUTH_REQUIRED", "RESOURCE_BLOCKED"} or self.api_failures >= 3:
                event.error = reason
            return event
        if value["type"] == "result":
            event.final = True
            if value.get("is_error") or value.get("subtype", "success") != "success":
                event.error = classify_error(encode(value))
            return event
        if value["type"] == "error":
            return StreamEvent(error=classify_error(encode(value)))
        if value["type"] not in {"assistant", "user"}:
            return event
        message = value.get("message", {})
        content = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(content, list):
            return StreamEvent(anomaly=True)
        for block in content:
            if not isinstance(block, dict):
                event.anomaly = True
                continue
            if block.get("type") == "tool_use":
                name, arguments = block.get("name"), block.get("input")
                if not isinstance(name, str) or not name or not isinstance(arguments, dict):
                    event.anomaly = True
                    continue
                if name.lower() in {"bash", "shell"} and not str(arguments.get("command", "")).strip():
                    event.anomaly = True
                event.activity = True
                if len(self.pending_calls) >= 128:
                    self.pending_calls.clear()
                self.pending_calls[str(block.get("id", ""))] = digest([name, arguments])
            elif block.get("type") == "tool_result":
                fingerprint = self.pending_calls.pop(str(block.get("tool_use_id", "")), None)
                if block.get("is_error"):
                    self.failed_repeats = (
                        self.failed_repeats + 1 if fingerprint and fingerprint == self.last_failed_call else 1
                    )
                    self.last_failed_call = fingerprint
                    event.anomaly |= self.failed_repeats >= 3
                else:
                    self.failed_repeats = 0
                    event.successful_tool = event.activity = True
            elif block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                fingerprint = digest(text)
                self.text_repeats = self.text_repeats + 1 if fingerprint == self.last_text_hash else 1
                self.last_text_hash = fingerprint
                event.anomaly |= len(text) >= 32 and self.text_repeats >= 4
                event.activity = True
        return event


def build_command(config, session_id: str) -> list[str]:
    mcp_config = {
        "mcpServers": {
            "sui": {
                "command": sys.executable,
                "args": ["-m", "sui_regulator.mcp", "--home", str(config.home)],
            }
        }
    }
    args = [
        config.claude_binary,
        "--print",
        "--bare",
        "--disable-slash-commands",
        "--output-format",
        "stream-json",
        "--verbose",
        "--session-id",
        session_id,
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        encode(mcp_config),
        "--tools",
        "",
        "--allowedTools",
        "mcp__sui__*",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--max-turns",
        str(config.claude_max_turns),
    ]
    if config.claude_model:
        args.extend(["--model", config.claude_model])
    return args


def runtime_environment(config, credentials: Credentials) -> dict:
    env = child_environment()
    env.update(
        {
            "SUI_WORKER_CREDENTIALS": SuiProtocol.credentials_json(credentials),
            "CLAUDE_CONFIG_DIR": str(config.home / "claude-config"),
            "DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
        }
    )
    # Never inherit an outer agent's reentrancy/session marker into a new managed CLI.
    for key in ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID"]:
        env.pop(key, None)
    return env


class ClaudeCodeAdapter:
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen:
        task = service.get_task(credentials.task_id)
        Path(task["workspace"]).mkdir(parents=True, exist_ok=True, mode=0o700)
        return detached(
            "sui_regulator.claude_process",
            ["--home", str(service.config.home)],
            input_data=SuiProtocol.credentials_json(credentials),
        )
