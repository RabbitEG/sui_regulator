"""Cooperative durable waits; user intent wins over all wake conditions."""

from __future__ import annotations

import math
import time

from .models import DomainError, encode, require_text


def request(service, credentials, reason, check_after_seconds, job_id=None):
    from .protocol import fence

    require_text(reason, "waiting reason", 4096)
    if (
        type(check_after_seconds) not in (int, float)
        or not math.isfinite(check_after_seconds)
        or not 1 <= check_after_seconds <= 604800
    ):
        raise DomainError("INVALID_ARGUMENT", "check_after_seconds must be in [1,604800]")
    now = time.time()
    with service.store.transaction() as conn:
        fence(conn, credentials)
        from .autonomy import trusted

        task = service._get(conn, "tasks", credentials.task_id)
        retain_runtime = trusted(task)
        pending = conn.execute(
            "SELECT * FROM tool_attempts WHERE task_id=? AND worker_id=? AND status IN ('STARTED','RUNNING')",
            (credentials.task_id, credentials.worker_id),
        ).fetchall()
        if not retain_runtime and pending:
            raise DomainError(
                "RUNTIME_STILL_REQUIRED", "finish or verify native operations before yielding their runtime"
            )
        if job_id is not None:
            job = service._get(conn, "jobs", require_text(job_id, "job_id", 128))
            if job["task_id"] != credentials.task_id:
                raise DomainError("FORBIDDEN", "waiting Job belongs to another Task")
        waiting = {
            "reason": reason,
            "job_id": job_id,
            "next_check_at": now + check_after_seconds,
            "responsible": "candleholder",
            "requested_by": credentials.worker_id,
            "state": "waiting",
            "runtime_retained": retain_runtime,
            "requested_at": now,
            "job_status_at_registration": job["status"] if job_id else None,
            "tool_attempt_ids": [row["id"] for row in pending],
            "wake_on": "job terminal or observation lost, native operation ended or observation lost, timer, new instruction or explicit Resume",
        }
        conn.execute(
            "UPDATE tasks SET status='WAITING',current_worker=?,next_start_at=?,wait_state=?,updated_at=? WHERE id=?",
            (credentials.worker_id if retain_runtime else None, waiting["next_check_at"], encode(waiting), now,
             credentials.task_id),
        )
        if not retain_runtime:
            conn.execute(
                "UPDATE workers SET status='RETIRED',lease_expires=0,ended_at=? WHERE id=?",
                (now, credentials.worker_id),
            )
        service.store.event(
            conn,
            credentials.task_id,
            "TASK_WAIT_REGISTERED",
            "proxy-declared",
            credentials.worker_id,
            waiting,
        )
    return waiting


def wake(service):
    now = time.time()
    with service.store.transaction() as conn:
        from .service import unpack

        for raw in conn.execute(
            "SELECT * FROM tasks WHERE desired='ACTIVE' AND status='WAITING' AND wait_state IS NOT NULL"
        ).fetchall():
            task = unpack(raw)
            state = task["wait_state"]
            reason = "timer" if task["next_start_at"] <= now else None
            if state.get("job_id"):
                job = service._get(conn, "jobs", state["job_id"])
                if job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                    reason = "job_terminal"
                elif (job["status"] in {"UNKNOWN", "BLOCKED"}
                      and job["status"] != state.get("job_status_at_registration")):
                    reason = "job_observation_changed"
            for attempt_id in state.get("tool_attempt_ids", []):
                attempt = conn.execute("SELECT status FROM tool_attempts WHERE id=? AND task_id=?",
                                       (attempt_id, task["id"])).fetchone()
                if attempt and attempt["status"] not in {"STARTED", "RUNNING"}:
                    reason = "native_operation_observation_changed"
                    break
            if not reason:
                continue
            state.update(state="woken", woken_at=now, wake_reason=reason)
            conn.execute(
                "UPDATE tasks SET status=?,next_start_at=?,wait_state=?,updated_at=? WHERE id=?",
                ("RUNNING" if task["current_worker"] else "READY", now, encode(state), now, task["id"]),
            )
            service.store.event(conn, task["id"], "TASK_WAIT_RESUMED", "controller", "candleholder", state)
