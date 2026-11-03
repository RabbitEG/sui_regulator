"""Operator-approved bounded command execution for worker sessions.

Workers still have no arbitrary shell. The deployment operator lists fixed argv
templates in a private registry file (never in Git); each entry names a binary,
its static prefix, and optional positional arguments validated per template
with bounded regex patterns. Only template names are accepted from workers.
"""

from __future__ import annotations

import os
import re
import selectors
import signal
import subprocess
import time
import tomllib
from pathlib import Path

from .models import DomainError, encode
from .protocol import fence

MAX_OUTPUT_BYTES = 65536
MAX_RUNTIME_SECONDS = 120.0
MAX_PATTERN_LENGTH = 256
MAX_ARGUMENTS = 4


def load_registry(path: str | None) -> dict[str, dict]:
    """Read templates from an operator-owned TOML file; empty is the safe default."""
    if not path:
        return {}
    file = Path(path).expanduser().absolute()
    try:
        info = file.stat()
    except OSError:
        raise DomainError("INVALID_ARGUMENT", "command registry file is missing")
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise DomainError("INVALID_ARGUMENT", "command registry must be owned by this UID with mode 0700")
    data = tomllib.loads(file.read_text())
    templates = {}
    for name, body in data.get("command", {}).items():
        argv = body.get("argv", [])
        raw_patterns = body.get("patterns", {})
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 64
            or not isinstance(argv, list)
            or not argv
            or len(argv) > 16
            or not all(isinstance(part, str) and part for part in argv)
            or not isinstance(raw_patterns, dict)
            or len(raw_patterns) > MAX_ARGUMENTS
        ):
            raise DomainError("INVALID_ARGUMENT", f"invalid command template: {name}")
        patterns = {}
        for key, pattern in raw_patterns.items():
            # TOML keys are strings; slot indexes arrive as "0", "1", ...
            try:
                slot = int(key)
            except (TypeError, ValueError):
                raise DomainError("INVALID_ARGUMENT", f"invalid pattern slot in template {name}")
            if slot < 0 or slot >= MAX_ARGUMENTS:
                raise DomainError("INVALID_ARGUMENT", f"invalid pattern slot in template {name}")
            if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN_LENGTH or not pattern:
                raise DomainError("INVALID_ARGUMENT", f"invalid pattern in template {name}")
            try:
                re.compile(pattern)
            except re.error:
                raise DomainError("INVALID_ARGUMENT", f"invalid pattern in template {name}")
            patterns[slot] = pattern
        templates[name] = {"argv": argv, "patterns": patterns}
    return templates


def run_command(service, credentials, template: str, arguments: list[str] | None = None) -> dict:
    """Execute an approved template as this worker, fenced by the lease and audited."""
    if not isinstance(template, str) or not template or len(template) > 64:
        raise DomainError("INVALID_ARGUMENT", "template must be a bounded name")
    arguments = arguments or []
    if not isinstance(arguments, list) or len(arguments) > MAX_ARGUMENTS or any(
        not isinstance(value, str) or len(value) > 256 or not value for value in arguments
    ):
        raise DomainError("INVALID_ARGUMENT", "arguments must be a bounded list of short strings")
    if any("{" in value or "}" in value for value in arguments):
        # No TOML interpolation attempts.
        raise DomainError("INVALID_ARGUMENT", "arguments must not contain braces")
    registry = load_registry(service.config.command_registry)
    if template not in registry:
        raise DomainError("FORBIDDEN", "command template is not approved for this deployment")
    spec = registry[template]
    if len(arguments) != len(spec["patterns"]):
        raise DomainError("INVALID_ARGUMENT", "argument count does not match template")
    if len(spec["argv"]) + len(arguments) > 20:
        raise DomainError("INVALID_ARGUMENT", "command line is too long")
    for position, value in enumerate(arguments):
        pattern = spec["patterns"].get(position)
        if pattern is None or not re.fullmatch(pattern, value):
            raise DomainError("INVALID_ARGUMENT", f"argument {position} does not match the approved pattern")
    argv = list(spec["argv"]) + list(arguments)
    started = time.time()
    # Fence and launch under the same write lock as Hold/takeover. Never hold
    # that lock while waiting for a command to finish.
    with service.store.transaction() as conn:
        fence(conn, credentials)
        task = service._get(conn, "tasks", credentials.task_id)
        workspace = Path(task["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            argv, cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, start_new_session=True,
        )
        service.store.event(conn, credentials.task_id, "COMMAND_STARTED", "command-runner",
                            credentials.worker_id, {"template": template, "argv": argv, "pid": process.pid})
    output = {"stdout": bytearray(), "stderr": bytearray()}
    truncated, timed_out = False, False
    deadline = time.monotonic() + MAX_RUNTIME_SECONDS
    with selectors.DefaultSelector() as selector:
        for name in output:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        try:
            while selector.get_map() or process.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    os.killpg(process.pid, signal.SIGKILL)
                    break
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        remaining = MAX_OUTPUT_BYTES - len(output[key.data])
                        output[key.data].extend(chunk[:remaining])
                        truncated |= len(chunk) > remaining
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
    result = {
        "template": template, "argv": argv,
        "exit_code": 124 if timed_out else process.returncode,
        "stdout": output["stdout"].decode(errors="replace"),
        "stderr": output["stderr"].decode(errors="replace"),
        "seconds": round(time.time() - started, 3),
        "timed_out": timed_out, "output_truncated": truncated,
    }
    # JSON escaping and Unicode can expand bounded byte output.
    while len(encode(result).encode()) > 60000:
        for name in output:
            result[name] = result[name][:len(result[name]) // 2]
        result["output_truncated"] = True
    with service.store.transaction() as conn:
        # Always preserve the actual result, even if ownership ended during execution.
        # Only the still-current Proxy may update its activity timestamp.
        try:
            fence(conn, credentials)
        except DomainError:
            result["ownership_current"] = False
        else:
            result["ownership_current"] = True
            conn.execute("UPDATE workers SET activity_at=? WHERE id=?", (time.time(), credentials.worker_id))
        service.store.event(conn, credentials.task_id, "COMMAND_EXECUTED", "command-runner",
                            credentials.worker_id, result)
    return result
