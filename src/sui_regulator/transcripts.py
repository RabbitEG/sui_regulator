"""Bounded, private session observations. Never used as task state or model memory."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from collections import deque

from .models import DomainError, encode

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TEXT = 8000
MAX_LINE_BYTES = 40000
MAX_PAGE_BYTES = 128 * 1024
SECRET_FIELD = re.compile(r"(?i)(api.?key|authorization|password|secret|credential|(?:access|auth|lease|refresh)_?token)")


def redact(text, secrets=()):
    text = str(text)
    for secret in secrets:
        if isinstance(secret, str) and len(secret) >= 8:
            text = text.replace(secret, "<REDACTED>")
    text = re.sub(r"(?i)(bearer\s+)[^\s\"',}]+", r"\1<REDACTED>", text)
    text = re.sub(r"\b(?:sk-ant-|sk-)[A-Za-z0-9_-]{8,}", "<REDACTED>", text)
    return re.sub(
        r'''(?ix)((?:api[_-]?key|authorization|password|secret|(?:access|auth|lease|refresh)[_-]?token)["']?\s*[:=]\s*)["']?[^\s,"'}]+''',
        r"\1<REDACTED>", text,
    )


def _path(home, proxy_id, create=False):
    if not re.fullmatch(r"sui-[a-f0-9]{32}", proxy_id):
        raise DomainError("INVALID_ARGUMENT", "invalid Sui Proxy identity")
    directory = home / "transcripts"
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    if directory.exists() or directory.is_symlink():
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError("session transcript directory must be private and owned")
    return directory / (proxy_id + ".jsonl")


def _open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(fd)
        raise OSError("session transcript must be a private regular file")
    return fd


class SessionRecorder:
    def __init__(self, config, credentials, source="claude-cli"):
        self.home, self.proxy_id, self.source = config.home, credentials.worker_id, source
        self.secrets = [credentials.lease_token] + [
            value for key, value in os.environ.items() if SECRET_FIELD.search(key)
        ]
        self.disabled = False
        self.message_id, self.buffers, self.tools = "", {}, {}
        self.seen, self.streamed = deque(maxlen=512), deque(maxlen=128)
        self.last_flush = time.monotonic()
        self.serial = 0

    def write(self, kind, role, text, **metadata):
        if self.disabled:
            return
        original = str(text)
        row = {
            "time": time.time(), "kind": kind, "role": role, "source": self.source,
            "text": redact(original, self.secrets)[:MAX_TEXT], "truncated": len(original) > MAX_TEXT,
            **{key: value if isinstance(value, bool) else redact(value, self.secrets)[:256]
               for key, value in metadata.items()},
        }
        try:
            path = _path(self.home, self.proxy_id, create=True)
            with os.fdopen(_open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND), "ab") as output:
                fcntl.flock(output, fcntl.LOCK_EX)
                size = os.fstat(output.fileno()).st_size
                row["id"] = size
                data = (encode(row) + "\n").encode()
                if size + len(data) > MAX_FILE_BYTES - 1024:
                    self.disabled = True
                    row = {"id": size, "time": time.time(), "role": "system", "kind": "recording_limit",
                           "source": self.source, "text": "本代际会话记录已达 4 MiB 上限，后续内容未保存。", "truncated": True}
                    data = (encode(row) + "\n").encode()
                    if size + len(data) > MAX_FILE_BYTES:
                        return
                output.write(data)
                output.flush()
        except (OSError, DomainError):
            # Observability must not stop the managed CLI, Controller, or Runner.
            self.disabled = True

    def flush(self, final=True):
        for message_id, text in list(self.buffers.items()):
            # Keep enough trailing context to redact credentials split across SSE frames.
            keep = max([512] + [min(len(value), MAX_TEXT) for value in self.secrets])
            cut = len(text) if final else max(0, len(text) - keep)
            if not final:
                for value in self.secrets:
                    start = text.find(value) if value else -1
                    if start >= 0 and start < cut < start + len(value):
                        cut = start
                for match in re.finditer(r'''(?i)(?:sk-|bearer\s+|["']?(?:api[_-]?key|password|secret|authorization|lease_token)["']?\s*[:=]\s*)[^\s,}]+''', text):
                    if match.start() < cut < match.end():
                        cut = match.start()
            if cut:
                self.write("text", "assistant", text[:cut], message_id=message_id)
            if final:
                self.buffers.pop(message_id, None)
            else:
                self.buffers[message_id] = text[cut:]
        self.last_flush = time.monotonic()

    def feed(self, line):
        try:
            self._feed(json.loads(line))
        except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
            return

    def _feed(self, value):
        self.serial += 1
        kind = value.get("type")
        if value.get("parent_tool_use_id"):
            return
        if kind == "system" and value.get("subtype") == "init":
            self.write("runtime", "system", "CLI 已初始化", model=value.get("model", "未报告"),
                       session_id=value.get("session_id", "未报告"))
        elif kind == "stream_event":
            event = value.get("event", {})
            if event.get("type") == "message_start":
                self.flush()
                self.message_id = str(event.get("message", {}).get("id", "stream"))
                self.tools.clear()
            index = event.get("index", 0)
            if event.get("type") == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use" and len(self.tools) < 32:
                    self.tools[index] = {**block, "partial": ""}
            elif event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                    key = self.message_id + ":" + str(index)
                    self.streamed.append(self.message_id)
                    # Redact on the assembled chunk, before emitting any fragments.
                    self.buffers[key] = self.buffers.get(key, "") + delta["text"]
                    if len(self.buffers[key]) > MAX_TEXT * 2:
                        self.write("text", "assistant", self.buffers[key], message_id=key)
                        self.buffers[key] = ""
                elif delta.get("type") == "input_json_delta" and index in self.tools:
                    tool = self.tools[index]
                    tool["partial"] = (tool["partial"] + delta.get("partial_json", ""))[:65536]
            elif event.get("type") == "content_block_stop":
                self.flush()
                block = self.tools.pop(index, None)
                if block:
                    block["input"] = json.loads(block["partial"]) if block["partial"] else block.get("input", {})
                    self._tool(block)
            elif event.get("type") == "message_stop":
                self.flush()
        elif kind in {"assistant", "user"}:
            self.flush()
            message = value.get("message", {})
            message_id = str(message.get("id", value.get("uuid", "message-" + str(self.serial))))
            for index, block in enumerate(message.get("content", [])):
                if block.get("type") == "text" and not (kind == "assistant" and message_id in self.streamed):
                    key = kind + ":" + message_id + ":" + str(index)
                    if key not in self.seen:
                        self.seen.append(key)
                        self.write("text", kind, block.get("text", ""), message_id=message_id)
                elif block.get("type") == "tool_use":
                    self._tool(block)
                elif block.get("type") == "tool_result":
                    self.write("tool_result", "tool", encode(block.get("content", "")),
                               call_id=block.get("tool_use_id", ""), is_error=bool(block.get("is_error")))
        elif kind in {"result", "error"}:
            self.flush()
            self.write("exit", "system", encode({key: value[key] for key in
                       ("subtype", "is_error", "errors", "error", "num_turns", "duration_ms") if key in value}))
        if time.monotonic() - self.last_flush >= 1:
            self.flush(final=False)

    def _tool(self, block):
        call_id = str(block.get("id", ""))
        key = "tool:" + call_id
        if key in self.seen:
            return
        self.seen.append(key)
        self.write("tool_call", "assistant", encode(block.get("input", {})),
                   tool_name=block.get("name", ""), call_id=call_id)


def read_transcript(service, task_id, proxy_id, after=0, limit=40):
    if type(after) is not int or not 0 <= after <= MAX_FILE_BYTES or type(limit) is not int or not 1 <= limit <= 50:
        raise DomainError("INVALID_ARGUMENT", "invalid transcript cursor or limit")
    with service.store.connection() as conn:
        proxy = dict(service._get(conn, "workers", proxy_id))
    if proxy["task_id"] != task_id:
        raise DomainError("NOT_FOUND", "Sui Proxy does not belong to this Task")
    result = {"task_id": task_id, "proxy_id": proxy_id, "generation": proxy["generation"],
              "adapter": proxy["adapter"], "queried_at": time.time(), "items": [], "next_after": after,
              "has_more": False, "limited": False, "availability": "not_recorded",
              "source": "managed session observations; message content is untrusted, not execution proof"}
    try:
        path = _path(service.config.home, proxy_id)
        with os.fdopen(_open(path, os.O_RDONLY), "rb") as source:
            size = os.fstat(source.fileno()).st_size
            if size > MAX_FILE_BYTES or after > size:
                raise DomainError("INVALID_ARGUMENT", "invalid transcript size or cursor")
            source.seek(max(0, size - 1024))
            tail = source.read(1024).splitlines()
            try:
                result["limited"] = bool(tail and json.loads(tail[-1]).get("kind") == "recording_limit")
            except ValueError:
                pass
            source.seek(after)
            if after:
                source.seek(after - 1)
                if source.read(1) != b"\n":
                    raise DomainError("INVALID_ARGUMENT", "cursor is not a record boundary")
            used = 0
            while len(result["items"]) < limit:
                position = source.tell()
                line = source.readline(MAX_LINE_BYTES + 1)
                if not line or not line.endswith(b"\n"):
                    break
                if len(line) > MAX_LINE_BYTES or used + len(line) > MAX_PAGE_BYTES:
                    break
                row = json.loads(line)
                row["id"] = position
                result["items"].append(row)
                result["next_after"] = source.tell()
                used += len(line)
            result["has_more"] = result["next_after"] < size
            result["availability"] = "recorded"
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        result["availability"] = "unreadable"
    return result
