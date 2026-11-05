from __future__ import annotations

import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .failures.claude import ClaudeNormalizer, Decoded
from .failures.claude import classify_error as classify_error
from .failures.engine import DetectionEngine
from .models import Credentials, DomainError, encode
from .processes import child_environment, detached
from .protocol import SuiProtocol
from .service import Service
from .task_prompt import SUPERVISOR_PROMPT


@dataclass
class StreamEvent:
    activity: bool = False
    anomaly: bool = False
    successful_tool: bool = False
    final: bool = False
    error: str | None = None
    session_id: str | None = None
    incidents: list = field(default_factory=list)


class ClaudeStreamDetector:
    """Compatibility facade; decoding, rules and policy live in separate modules."""

    def __init__(self, session_id=None, registry=None, recovery_mode="bounded", permission_mode="guarded",
                 response_silence_seconds=600):
        self.normalizer = ClaudeNormalizer(session_id)
        self.permission_mode = permission_mode
        overrides = {"NET-002": {"warn_after": response_silence_seconds / 2,
                                  "act_after": response_silence_seconds}} if permission_mode == "trusted" else None
        self.engine = DetectionEngine(registry, session_id or "offline", config_overrides=overrides)
        self.recovery_mode = recovery_mode
        self.external_tool_pending = False

    def _convert(self, decoded):
        from .autonomy import TRUSTED_ADVISORY_RULES
        incidents = []
        for observed in decoded.observations:
            incidents.extend(self.engine.feed(observed))
        disruptive = next(
            (
                item
                for item in reversed(incidents)
                if item.action in {"fresh_session", "quarantine", "wait"}
                and item.rule_id not in {"TOOL-002", "ENV-001"}
                and not (self.permission_mode == "trusted" and item.rule_id in TRUSTED_ADVISORY_RULES)
            ),
            None,
        )
        reasons = {
            "TOOL-001": "MALFORMED_OR_REPEATED_TOOL_CALLS",
            "GEN-001": "GENERATION_REPETITION",
            "GEN-002": "GENERATION_REPETITION",
            "NET-001": "TRANSPORT_FAILURE",
            "NET-002": "STREAM_STALLED",
            "NET-003": "TRUNCATED_STREAM",
            "STATE-001": "SESSION_ID_MISMATCH",
            "ENV-001": "ENVIRONMENT_BLOCKED",
            "UNKNOWN-001": "UNKNOWN_ANOMALY",
        }
        error = (
            reasons.get(disruptive.rule_id, decoded.terminal_error or disruptive.rule_id)
            if disruptive
            else decoded.terminal_error
        )
        return StreamEvent(
            activity=any(
                item.kind in {"assistant_text", "text_delta", "tool_call", "tool_result"}
                for item in decoded.observations
            ),
            anomaly=any(item.kind == "malformed_call" for item in decoded.observations)
            or any(item.category == "generation" for item in incidents),
            successful_tool=any(
                item.kind == "tool_result" and not item.data.get("is_error") for item in decoded.observations
            ),
            final=decoded.final,
            error=error,
            session_id=decoded.session_id,
            incidents=incidents,
        )

    def feed(self, line, now=None):
        now = max(time.time() if now is None else now, self.engine.last_time)
        return self._convert(self.normalizer.feed(line, now))

    def tick(self, now=None, tool_pending=False):
        now = max(time.time() if now is None else now, self.engine.last_time)
        from .failures.models import Observation

        if self.external_tool_pending and not tool_pending:
            # The model's silence allowance starts after observed background work ends.
            self.normalizer.last_stream_at = now
        self.external_tool_pending = tool_pending
        clock = self.normalizer.clock(now)
        if tool_pending:
            clock = Observation(clock.time, clock.kind, {**clock.data, "tool_pending": True}, clock.source)
        return self._convert(Decoded([clock]))

    def begin_turn(self, now=None):
        now = max(time.time() if now is None else now, self.engine.last_time)
        self.normalizer.final_seen = False
        self.normalizer.awaiting_response = True
        self.normalizer.last_stream_at = now
        self.engine.emitted = {key for key in self.engine.emitted if key[0] != "NET-002"}

    def end(self, now=None, partial_line=False, authorized_stop=False):
        now = max(time.time() if now is None else now, self.engine.last_time)
        return self._convert(Decoded([self.normalizer.end(now, partial_line, authorized_stop)]))


def build_command(config, session_id: str, *, execution_profile: str = "managed", permission_mode: str = "guarded") -> list[str]:
    from .autonomy import validate_mode

    validate_mode(permission_mode)
    if execution_profile not in {"managed", "claude_code"}:
        raise ValueError("unknown execution profile")
    # managed: read-only built-ins so workers can study session records and
    # repositories as context; every action and state change still goes through
    # the managed MCP tools (no Bash/Edit/Write). claude_code: full native
    # capability for explicit operator-approved execution tasks.
    native_tools = {"managed": "Read,Glob,Grep", "claude_code": "Read,Edit,Write,Glob,Grep,Bash"}[
        execution_profile
    ]
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
        "--disable-slash-commands",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--session-id",
        session_id,
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        encode(mcp_config),
        "--tools",
        native_tools,
        "--allowedTools",
        (native_tools + "," if native_tools else "") + "mcp__sui__*",
        "--append-system-prompt",
        SUPERVISOR_PROMPT,
        "--permission-mode",
        "bypassPermissions" if permission_mode == "trusted" else "dontAsk",
        "--permission-prompts",
        "none",
    ]
    if permission_mode == "trusted":
        args.extend(["--input-format", "stream-json"])
    else:
        args.extend(["--max-turns", str(config.claude_max_turns)])
    if config.claude_model:
        args.extend(["--model", config.claude_model])
    # Explicit instruction-delivery hooks are needed for both profiles. The
    # tool allowlists are unchanged; project/user settings remain disabled.
    hook = shlex.join([sys.executable, "-m", "sui_regulator.tool_hook", "--home", str(config.home)])
    hooks = {
        stage: [
            {
                "matcher": ".*",
                "hooks": [{"type": "command", "command": hook, "timeout": 15, "onFailure": "block"}],
            }
        ]
        for stage in ("PreToolUse", "PostToolUse", "PostToolUseFailure")
    }
    hooks["Stop"] = [{"hooks": [{"type": "command", "command": hook, "timeout": 15}]}]
    args.extend(["--settings", encode({"hooks": hooks})])
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
    for key in ["CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE"]:
        env.pop(key, None)
    return env


class ClaudeProxyAdapter:
    def start(self, service: Service, credentials: Credentials) -> subprocess.Popen:
        task = service.get_task(credentials.task_id)
        try:
            version = subprocess.run(
                [service.config.claude_binary, "--version"], capture_output=True, text=True, timeout=5
            )
            match = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
            supported = version.returncode == 0 and match and tuple(map(int, match.groups())) >= (2, 1, 316)
        except (OSError, subprocess.TimeoutExpired):
            supported = False
        if not supported:
            raise DomainError(
                "CAPABILITY_REQUIRED",
                "Claude profiles require Claude Code >= 2.1.316 with instruction-delivery and blocking hooks",
            )
        Path(task["workspace"]).mkdir(parents=True, exist_ok=True, mode=0o700)
        return detached(
            "sui_regulator.claude_process",
            ["--home", str(service.config.home)],
            input_data=SuiProtocol.credentials_json(credentials),
        )


ClaudeCodeAdapter = ClaudeProxyAdapter  # v0.3 import compatibility
