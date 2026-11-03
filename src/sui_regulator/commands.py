"""Operator-approved bounded command execution for worker sessions.

Workers still have no arbitrary shell. The deployment operator lists fixed argv
templates in a private registry file (never in Git); each entry names a binary,
its static prefix, and optional positional arguments validated per template
with bounded regex patterns. Only template names are accepted from workers.
"""

from __future__ import annotations

import os
import re
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
    finished = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=MAX_RUNTIME_SECONDS,
        shell=False,
    )
    result = {
        "template": template,
        "argv": argv,
        "exit_code": finished.returncode,
        "stdout": finished.stdout[:MAX_OUTPUT_BYTES].decode(errors="replace"),
        "stderr": finished.stderr[:MAX_OUTPUT_BYTES].decode(errors="replace"),
        "seconds": round(time.time() - started, 3),
    }
    if len(encode(result)) > 65536:
        result = {**result, "stdout": result["stdout"][:8192]}
    with service.store.transaction() as conn:
        worker = fence(conn, credentials)
        conn.execute("UPDATE workers SET activity_at=? WHERE id=?", (time.time(), credentials.worker_id))
        service.store.event(
            conn,
            credentials.task_id,
            "COMMAND_EXECUTED",
            "worker-declared",
            credentials.worker_id,
            {"template": template, "argv": argv, "exit_code": result["exit_code"], "seconds": result["seconds"]},
        )
    return result
