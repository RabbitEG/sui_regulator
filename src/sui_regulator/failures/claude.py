"""Claude wire decoding only. No recovery decisions or arbitrary log keywords."""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field

from ..models import digest, encode
from .detectors import ENV_CODES
from .models import Observation

# Bounded error excerpt kept in incident evidence; stderr tail is 8192 bytes,
# the excerpt stays far smaller so evidence rows remain cheap to read.
EXCERPT_MAX_BYTES = 512


def excerpt(text: str) -> str:
    """Collapse and truncate a raw error frame for durable incident evidence."""
    flat = " ".join(text.split())
    return flat[:EXCERPT_MAX_BYTES]


def classify_error(text: str) -> str:
    """Legacy provider error-text fallback, used only in CLI error envelopes."""
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
    if re.search(r"maximum number of turns|max.turns", text, re.I):
        return "MAX_TURNS_EXHAUSTED"
    return "RUNTIME_ERROR"


@dataclass
class Decoded:
    observations: list[Observation] = field(default_factory=list)
    final: bool = False
    terminal_error: str | None = None
    session_id: str | None = None


class ClaudeNormalizer:
    def __init__(self, expected_session: str | None = None, *, tool_observation_seconds=600):
        self.expected_session = expected_session
        self.final_seen = False
        self.streaming_observed = False
        self.text_delta_seen = False
        # The wrapper has already submitted a prompt, even before system/init.
        self.awaiting_response = True
        self.last_stream_at: float | None = None
        self.message_id = "stream-message"
        self.serial = 0
        self.blocks: dict[int, dict] = {}
        self.seen_calls = deque(maxlen=512)
        self.seen_messages = deque(maxlen=128)
        self.pending: set[str] = set()
        # A past call is a bounded observation, not perpetual proof of execution.
        # Keep completed calls briefly so native background receipts can be matched.
        self.tool_calls: dict[str, dict] = {}
        self.observed_calls: set[str] = set()
        self.tool_observation_seconds = tool_observation_seconds

    def _obs(self, time, kind, **data):
        return Observation(time, kind, data, "claude-wrapper")

    def _call(self, block: dict, time) -> list[Observation]:
        call_id, name, arguments = block.get("id"), block.get("name"), block.get("input")
        if (
            not isinstance(call_id, str)
            or not call_id
            or not isinstance(name, str)
            or not name
            or not isinstance(arguments, dict)
        ):
            return [
                self._obs(
                    time, "malformed_call", reason="missing call identity/name or invalid argument object"
                )
            ]
        if call_id in self.seen_calls:
            return []
        self.seen_calls.append(call_id)
        if name.casefold() in {"bash", "shell"} and not str(arguments.get("command", "")).strip():
            return [self._obs(time, "malformed_call", reason="empty Bash command")]
        if len(self.pending) >= 128:
            self.pending.pop()
        if len(self.tool_calls) >= 512:
            removable = next((key for key in self.tool_calls if key not in self.pending | self.observed_calls),
                             next(iter(self.tool_calls)))
            self.tool_calls.pop(removable)
        self.tool_calls[call_id] = {"call_id": call_id, "name": name, "arguments": arguments,
                                   "started_at": time, "valid_until": time + self.tool_observation_seconds}
        self.pending.add(call_id)
        self.awaiting_response = False
        return [self._obs(time, "tool_call", call_id=call_id, name=name[:256], arguments=arguments)]

    def _result(self, block, time):
        call_id = block.get("tool_use_id")
        self.pending.discard(call_id)
        self.awaiting_response = not self.pending
        content = block.get("content", {})
        value = content
        if isinstance(content, list):
            value = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            if len(value) == 1:
                try:
                    value = json.loads(value[0])
                except (ValueError, TypeError):
                    pass
        code = value.get("code") if isinstance(value, dict) else None
        failed = bool(block.get("is_error"))
        observations = [
            self._obs(
                time,
                "tool_result",
                call_id=call_id,
                is_error=failed,
                error_code=code,
                result=value if isinstance(value, dict) else {"content_hash": digest(value)},
            )
        ]
        if failed and code in ENV_CODES:
            observations.insert(0, self._obs(time, "environment_error", code=code))
        return observations

    def tool_ended(self, call_id, time):
        if call_id not in self.tool_calls or "ended_at" in self.tool_calls[call_id]:
            return
        self.tool_calls[call_id]["ended_at"] = time
        self.pending.discard(call_id)
        if not self.final_seen:
            self.awaiting_response = not self.pending
        self.last_stream_at = time

    def feed(self, line: str, time: float) -> Decoded:
        try:
            return self._decode(line, time)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
            return Decoded(
                [self._obs(time, "malformed_call", reason="unsupported or malformed CLI event shape")]
            )

    def _decode(self, line: str, time: float) -> Decoded:
        self.serial += 1
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            return Decoded([self._obs(time, "malformed_call", reason="invalid CLI JSON")])
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            return Decoded([self._obs(time, "malformed_call", reason="invalid CLI event envelope")])
        if value.get("parent_tool_use_id"):
            return Decoded()
        result = Decoded()
        kind = value["type"]
        if kind == "system" and value.get("subtype") in {"task_started", "task_progress", "task_notification"}:
            call_id = value.get("tool_use_id")
            if (not isinstance(call_id, str) or call_id not in self.tool_calls
                    or (self.expected_session and value.get("session_id") != self.expected_session)):
                return result
            call = self.tool_calls[call_id]
            if value["subtype"] == "task_notification":
                if value.get("status") not in {"completed", "failed", "stopped"}:
                    return result
                self.tool_ended(call_id, time)
            elif "ended_at" not in call:
                call["valid_until"] = max(call["valid_until"], time + self.tool_observation_seconds)
                call["observed_at"] = time
        if kind in {"stream_event", "assistant", "user", "result", "error"} or value.get("subtype") in {
            "init",
            "api_retry",
            "status",
            "compact_boundary",
            "task_notification",
            "task_progress",
            "task_started",
        }:
            self.last_stream_at = time
        if kind == "system":
            if value.get("subtype") == "init":
                result.session_id = value.get("session_id")
                self.awaiting_response = True
                if self.expected_session and result.session_id != self.expected_session:
                    result.observations.append(self._obs(time, "session_state", mismatch=True))
            elif value.get("subtype") == "api_retry":
                code = value.get("error_status")
                if code is None:
                    code = classify_error(encode({"error": value.get("error")}))
                result.observations.append(self._obs(time, "api_error", code=code, terminal=False))
            elif value.get("subtype") in {"compact_error", "compaction_error"}:
                result.observations.append(self._obs(time, "session_state", code="COMPACTION_FAILED"))
        elif kind == "stream_event":
            self.streaming_observed = True
            event = value.get("event", {})
            if not isinstance(event, dict):
                return Decoded([self._obs(time, "malformed_call", reason="invalid stream event")])
            event_type = event.get("type")
            if event_type == "message_start":
                self.final_seen = False
                self.message_id = event.get("message", {}).get("id", f"stream-{self.serial}")
                self.blocks.clear()
                self.awaiting_response = True
            elif event_type == "content_block_start":
                if len(self.blocks) >= 32:
                    self.blocks.clear()
                block = event.get("content_block", {})
                self.blocks[event.get("index", 0)] = {**block, "partial_json": ""}
            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") in {"text_delta", "thinking_delta"}:
                    text = delta.get("text", delta.get("thinking", ""))
                    if isinstance(text, str):
                        self.text_delta_seen |= delta["type"] == "text_delta"
                        result.observations.append(
                            self._obs(
                                time,
                                "text_delta",
                                message_id=str(self.message_id) + ":" + str(event.get("index", 0)),
                                text=text,
                                channel=delta["type"],
                            )
                        )
                elif delta.get("type") == "input_json_delta":
                    block = self.blocks.get(event.get("index", 0))
                    if block:
                        block["partial_json"] = (block["partial_json"] + delta.get("partial_json", ""))[
                            :65537
                        ]
            elif event_type == "content_block_stop":
                block = self.blocks.pop(event.get("index", 0), {})
                if block.get("type") == "tool_use":
                    try:
                        block["input"] = (
                            json.loads(block["partial_json"])
                            if block.get("partial_json")
                            else block.get("input", {})
                        )
                        result.observations.extend(self._call(block, time))
                    except (ValueError, TypeError):
                        result.observations.append(
                            self._obs(time, "malformed_call", reason="invalid completed tool argument JSON")
                        )
        elif kind in {"assistant", "user"}:
            self.final_seen = False
            message = value.get("message", {})
            content = message.get("content", []) if isinstance(message, dict) else None
            if not isinstance(content, list):
                return Decoded([self._obs(time, "malformed_call", reason="invalid message content")])
            message_id = message.get("id", f"message-{self.serial}")
            if kind == "assistant" and message_id in self.seen_messages:
                return result
            if kind == "assistant":
                self.seen_messages.append(message_id)
            for block in content:
                if not isinstance(block, dict):
                    result.observations.append(
                        self._obs(time, "malformed_call", reason="invalid content block")
                    )
                elif block.get("type") == "tool_use":
                    result.observations.extend(self._call(block, time))
                elif block.get("type") == "tool_result":
                    result.observations.extend(self._result(block, time))
                elif kind == "assistant" and block.get("type") in {"text", "thinking"}:
                    text = block.get("text", block.get("thinking", ""))
                    if isinstance(text, str):
                        result.observations.append(
                            self._obs(time, "assistant_text", message_id=message_id, text=text)
                        )
            if (
                kind == "assistant"
                and not value.get("error")
                and not any(item.kind == "malformed_call" for item in result.observations)
            ):
                result.observations.append(self._obs(time, "response_completed"))
            elif (
                kind == "user"
                and any(isinstance(block, dict) and block.get("type") == "text" for block in content)
                and not any(
                    isinstance(block, dict) and block.get("type") == "tool_result" for block in content
                )
            ):
                result.observations.append(self._obs(time, "user_input"))
        elif kind in {"result", "error"}:
            result.final = kind == "result"
            self.final_seen |= result.final
            self.awaiting_response = False
            if kind == "error" or value.get("is_error") or value.get("subtype", "success") != "success":
                wire_error = {
                    "errors": value.get("errors", []),
                    "error": value.get("error", {}),
                    "result": value.get("result", ""),
                }
                code = classify_error(encode(wire_error))
                result.terminal_error = code
                result.observations.append(
                    self._obs(
                        time,
                        # MAX_TURNS_EXHAUSTED is a budget boundary, not an anomaly:
                        # a fresh session resumes the loop with durable context.
                        "api_error" if code != "RUNTIME_ERROR" else "unknown_error",
                        code=code,
                        terminal=True,
                        stderr_excerpt=excerpt(encode(wire_error)),
                    )
                )
            else:
                result.observations.append(self._obs(time, "response_completed"))
        return result

    def clock(self, time: float, live_tools=(), ended_tools=()) -> Observation:
        if self.last_stream_at is None:
            self.last_stream_at = time
        for call_id in live_tools:
            if call_id in self.tool_calls:
                self.tool_calls[call_id]["valid_until"] = time + self.tool_observation_seconds
        for call_id in ended_tools:
            self.tool_ended(call_id, time)
        valid = [call for key, call in self.tool_calls.items()
                 if key in self.pending and call["valid_until"] > time]
        expired = [call["valid_until"] for key, call in self.tool_calls.items()
                   if key in self.pending and call["valid_until"] <= time]
        return self._obs(
            time,
            "clock",
            awaiting_response=self.awaiting_response or bool(expired and not valid and not self.final_seen),
            tool_pending=bool(valid),
            last_stream_at=max([self.last_stream_at, *expired]),
            streaming_observed=self.streaming_observed,
        )

    def end(self, time: float, partial_line=False, authorized_stop=False) -> Observation:
        return self._obs(
            time,
            "stream_end",
            final_seen=self.final_seen,
            partial_line=partial_line,
            authorized_stop=authorized_stop,
        )
