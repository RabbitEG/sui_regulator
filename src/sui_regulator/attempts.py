"""Durable operation receipts and repetition gates; not a security sandbox."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path

from .models import digest, encode


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


def denial(conn, task, operation_key, approach_key, *, read_only=False, allow_during_background=False):
    if task["max_proxy_seconds"]:
        used = conn.execute(
            "SELECT COALESCE(SUM(MAX(0,COALESCE(ended_at,?)-created_at)),0) FROM workers WHERE task_id=?",
            (time.time(), task["id"]),
        ).fetchone()[0]
        if used >= task["max_proxy_seconds"]:
            return "Proxy runtime budget exhausted; increase the budget and Resume."
    from .autonomy import trusted

    if trusted(task):
        return None
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
        running = conn.execute(
            "SELECT operation_key FROM tool_attempts WHERE task_id=? AND status='RUNNING'", (task["id"],)
        ).fetchall()
        if running and (
            not allow_during_background or any(r["operation_key"] == operation_key for r in running)
        ):
            return "Known background work is running. Read-only diagnostics and operator-approved independent templates remain available; do not duplicate or conflict with it."
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


def finish(conn, attempt_id, status, result, runtime=None):
    return conn.execute(
        "UPDATE tool_attempts SET status=?,result_hash=?,finished_at=?,runtime=COALESCE(?,runtime) WHERE id=? AND status IN ('STARTED','READING')",
        (
            status,
            error_fingerprint(result),
            None if status == "RUNNING" else time.time(),
            encode(runtime) if runtime is not None else None,
            attempt_id,
        ),
    ).rowcount


def observe_background(service, credentials, session_id, message):
    """Persist CLI transport facts, including uncertain restart/stop evidence.

    Only matching recorded calls can change. A terminal receipt never regresses;
    an old generation may report its own result but gains no execution authority.
    """
    if (
        not isinstance(message, dict)
        or message.get("type") != "system"
        or message.get("subtype") != "task_notification"
        or message.get("session_id") != session_id
        or not isinstance(message.get("tool_use_id"), str)
        or not message["tool_use_id"]
        or not isinstance(message.get("status"), str)
        or message["status"] not in {"completed", "failed", "stopped"}
    ):
        return
    attempt_id = "native-" + digest([credentials.worker_id, message["tool_use_id"]])
    with service.store.transaction() as conn:
        worker = service._get(conn, "workers", credentials.worker_id)
        if worker["session_id"] != session_id or worker["task_id"] != credentials.task_id:
            return
        row = conn.execute(
            "SELECT * FROM tool_attempts WHERE id=? AND task_id=? AND worker_id=? AND tool='Bash'",
            (attempt_id, credentials.task_id, credentials.worker_id),
        ).fetchone()
        if not row or row["status"] not in {"STARTED", "RUNNING", "UNKNOWN"}:
            return
        prior = json.loads(row["runtime"])
        handle = message.get("task_id")
        if prior.get("handle") and handle != prior["handle"]:
            return
        if prior.get("reason") == "worker_restart":
            return
        reason = message.get("reason")
        status = (
            "UNKNOWN"
            if reason
            else {"completed": "SUCCEEDED", "failed": "FAILED", "stopped": "STOPPED"}[message["status"]]
        )
        runtime = {
            **prior,
            "source": "claude-task-notification",
            "raw_status": message["status"],
            "session_id": session_id,
            "tool_use_id": message["tool_use_id"],
            "observed_at": time.time(),
        }
        if isinstance(handle, str):
            runtime["handle"] = handle[:256]
        if reason:
            runtime["reason"] = str(reason)[:256]
        conn.execute(
            "UPDATE tool_attempts SET status=?,result_hash=?,finished_at=?,runtime=? WHERE id=?",
            (
                status,
                error_fingerprint(message),
                None if status == "UNKNOWN" else time.time(),
                encode(runtime),
                attempt_id,
            ),
        )
        service.store.event(
            conn,
            credentials.task_id,
            "NATIVE_BACKGROUND_RESULT",
            "wrapper-observed",
            credentials.worker_id,
            {
                "attempt_id": attempt_id,
                "status": status,
                "runtime": runtime,
                "counts_as_verified_progress": False,
            },
        )


def reconcile_background(service):
    """Lost runtime ownership removes RUNNING certainty, never fabricates exit."""
    now = time.time()
    with service.store.transaction() as conn:
        rows = conn.execute(
            "SELECT a.*,w.status AS proxy_status,w.lease_expires FROM tool_attempts a JOIN workers w ON w.id=a.worker_id WHERE a.status='RUNNING'"
        ).fetchall()
        for row in rows:
            if row["proxy_status"] in {"STARTING", "RUNNING", "ROTATING"} and row["lease_expires"] > now:
                continue
            runtime = {
                **json.loads(row["runtime"]),
                "source": "controller",
                "observation_reason": "runtime no longer owned or lease expired",
                "observed_at": now,
            }
            conn.execute(
                "UPDATE tool_attempts SET status='UNKNOWN',runtime=? WHERE id=?", (encode(runtime), row["id"])
            )
            service.store.event(
                conn,
                row["task_id"],
                "BACKGROUND_OBSERVATION_LOST",
                "controller",
                "controller",
                {"attempt_id": row["id"], "status": "UNKNOWN", "runtime": runtime},
            )
