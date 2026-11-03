"""Durable operation receipts and repetition gates; not a security sandbox."""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from .models import digest


def workspace_fingerprint(workspace):
    """Bounded code/config snapshot. Changes permit attempts, never prove progress."""
    root, entries, used, scanned = Path(workspace), [], 0, 0
    ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".sui", "logs", "results"}
    suffixes = {
        ".py",
        ".sh",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".js",
        ".ts",
        ".c",
        ".cpp",
        ".h",
        ".rs",
        ".go",
    }
    for directory, dirs, files in os.walk(root, followlinks=False):
        scanned += len(files) + len(dirs) + 1
        if scanned > 4096:
            return digest(entries)
        dirs[:] = sorted(d for d in dirs if d not in ignored and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix not in suffixes or path.is_symlink():
                continue
            try:
                with path.open("rb") as source:
                    content = source.read(65536)
            except OSError:
                continue
            used += len(content)
            entries.append([str(path.relative_to(root)), hashlib.sha256(content).hexdigest()])
            if len(entries) >= 256 or used >= 2 * 1024 * 1024:
                return digest(entries)
    return digest(entries)


def error_fingerprint(value):
    # Volatile timestamps/addresses do not make an unchanged error a new plan.
    text = str(value)[:32768]
    text = re.sub(r"0x[0-9a-fA-F]+|\b\d{4}-\d\d-\d\d[T ][0-9:.+Z-]+", "<volatile>", text)
    return digest(text)


def denial(conn, task, operation_key, approach_key, *, read_only=False):
    if task["max_proxy_seconds"]:
        used = conn.execute(
            "SELECT COALESCE(SUM(MAX(0,COALESCE(ended_at,?)-created_at)),0) FROM workers WHERE task_id=?",
            (time.time(), task["id"]),
        ).fetchone()[0]
        if used >= task["max_proxy_seconds"]:
            return "Proxy runtime budget exhausted; increase the budget and Resume."
    if not read_only:
        unknown = conn.execute(
            "SELECT 1 FROM jobs WHERE task_id=? AND status IN ('STARTING','UNKNOWN') LIMIT 1", (task["id"],)
        ).fetchone()
        pending = conn.execute(
            "SELECT 1 FROM tool_attempts WHERE task_id=? AND status IN ('STARTED','UNKNOWN') LIMIT 1",
            (task["id"],),
        ).fetchone()
        if unknown or pending:
            return "An earlier Job or command has an unresolved outcome. Inspect it; do not replay side effects. Yan can resolve a command receipt after verification."
    return None


def repetition_note(conn, task, operation_key, approach_key):
    rows = conn.execute(
        "SELECT status,result_hash,approach_key FROM tool_attempts WHERE task_id=? AND operation_key=? ORDER BY created_at DESC,rowid DESC LIMIT 3",
        (task["id"], operation_key),
    ).fetchall()
    if len(rows) == 3 and all(
        row["status"] == "FAILED"
        and row["approach_key"] == approach_key
        and row["result_hash"] == rows[0]["result_hash"]
        for row in rows
    ):
        return "The last three executions had the same observed error and code snapshot. This is task evidence, not a Session fault. Decide whether to retry, gather evidence or change the approach."
    return None


def start(conn, task, credentials, attempt_id, tool, operation_key, approach_key, *, read_only=False):
    conn.execute(
        "INSERT INTO tool_attempts(id,task_id,worker_id,tool,operation_key,approach_key,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (
            attempt_id,
            task["id"],
            credentials.worker_id,
            tool,
            operation_key,
            approach_key,
            "READING" if read_only else "STARTED",
            time.time(),
        ),
    )


def finish(conn, attempt_id, status, result):
    conn.execute(
        "UPDATE tool_attempts SET status=?,result_hash=?,finished_at=? WHERE id=? AND status IN ('STARTED','READING')",
        (status, error_fingerprint(result), time.time(), attempt_id),
    )


def observe_background(service, credentials, session_id, message):
    """Accept terminal CLI transport evidence, never a model-written tool result.

    A missing notification remains UNKNOWN across generations. Runtime exit says
    nothing about scientific correctness; it only resolves this Bash invocation.
    """
    if (
        not isinstance(message, dict)
        or message.get("type") != "system"
        or message.get("subtype") != "task_notification"
        or message.get("session_id") != session_id
        or message.get("reason")
        or not isinstance(message.get("tool_use_id"), str)
        or not message["tool_use_id"]
        or not isinstance(message.get("status"), str)
        or message.get("status") not in {"completed", "failed", "stopped"}
    ):
        return
    attempt_id = "native-" + digest([credentials.worker_id, message["tool_use_id"]])
    status = "SUCCEEDED" if message["status"] == "completed" else "FAILED"
    with service.store.transaction() as conn:
        changed = conn.execute(
            "UPDATE tool_attempts SET status=?,result_hash=?,finished_at=? WHERE id=? AND task_id=? AND worker_id=? AND tool='Bash' AND status IN ('STARTED','UNKNOWN')",
            (status, error_fingerprint(message), time.time(), attempt_id, credentials.task_id, credentials.worker_id),
        ).rowcount
        if changed:
            service.store.event(
                conn, credentials.task_id, "NATIVE_BACKGROUND_RESULT", "wrapper-observed", credentials.worker_id,
                {"attempt_id": attempt_id, "status": status, "counts_as_verified_progress": False},
            )
