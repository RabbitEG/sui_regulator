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
        from .autonomy import reviewed, trusted

        task = service._get(conn, "tasks", credentials.task_id)
        pending = conn.execute(
            "SELECT * FROM tool_attempts WHERE task_id=? AND worker_id=? AND status IN ('STARTED','RUNNING')",
            (credentials.task_id, credentials.worker_id),
        ).fetchall()
        if any(not (trusted(task) and reviewed(row)) for row in pending):
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
            "wake_on": "job terminal, timer, new instruction or explicit Resume",
        }
        conn.execute(
            "UPDATE tasks SET status='WAITING',current_worker=NULL,next_start_at=?,wait_state=?,updated_at=? WHERE id=?",
            (waiting["next_check_at"], encode(waiting), now, credentials.task_id),
        )
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
            due = task["next_start_at"] <= now
            if state.get("job_id"):
                job = service._get(conn, "jobs", state["job_id"])
                due |= job["status"] in {"COMPLETED", "FAILED", "CANCELLED"}
            if not due:
                continue
            state.update(state="woken", woken_at=now)
            conn.execute(
                "UPDATE tasks SET status='READY',next_start_at=?,wait_state=?,updated_at=? WHERE id=?",
                (now, encode(state), now, task["id"]),
            )
            service.store.event(conn, task["id"], "TASK_WAIT_RESUMED", "controller", "candleholder", state)
