"""Bounded, read-only console projection over the existing ledger."""

from __future__ import annotations

import time

from .models import DomainError, digest
from .processes import alive, process_state
from .service import unpack

SECTIONS = {"overview", "sessions", "tasks", "incidents"}
TERMINAL_WORKERS = {"FAILED", "RETIRED", "REVOKED", "EXITED"}


def controller_status(service, now=None):
    now = time.time() if now is None else now
    last = getattr(service, "controller_last_tick_at", None)
    started = getattr(service, "controller_started_at", None)
    process = getattr(service, "controller_identity", None)
    threshold = max(3.0, service.config.tick_seconds * 5)
    age = max(0, now - last) if last is not None else None
    state = "OFFLINE"
    if process and alive(process):
        state = "STARTING" if last is None else "ONLINE" if age <= threshold else "STALE"
    return {
        "state": state,
        "pid": process["pid"] if process else None,
        "started_at": started,
        "last_tick_at": last,
        "tick_age_seconds": age,
        "stale_after_seconds": threshold,
        "source": "completed controller tick; RPC availability alone is not health",
    }


def session_view(value, config, current_worker=None, now=None):
    now = time.time() if now is None else now
    value = dict(value)
    value["is_current"] = value["id"] == current_worker
    value["process_alive"] = alive(value.get("identity")) if value.get("identity") else None
    value["process_state"] = process_state(value.get("identity")) if value.get("identity") else None
    runtime = value.get("child_identity")
    value["runtime_process_alive"] = alive(runtime) if runtime else None
    value["runtime_process_state"] = process_state(runtime) if runtime else None
    value["heartbeat_age_seconds"] = max(0, now - value["heartbeat_at"])
    value["runtime_seconds"] = max(0, (value.get("ended_at") or now) - value["created_at"])
    if value["status"] in TERMINAL_WORKERS:
        health = "HISTORICAL"
    elif value["process_state"] in {"T", "t"}:
        health = "FROZEN"
    elif value["status"] == "STARTING" and now - value["created_at"] < config.startup_grace:
        health = "STARTING"
    elif value["process_alive"] is False or now >= value["lease_expires"]:
        health = "LOST"
    elif value["process_alive"] is None:
        health = "UNVERIFIED"
    elif value["heartbeat_age_seconds"] > max(3, config.heartbeat_seconds * 3):
        health = "DELAYED"
    else:
        health = "HEALTHY"
    value["health"] = health
    value["observed_at"] = now
    value["health_source"] = "owned process identity and heartbeat lease"
    return value


def snapshot(service, section="overview", offset=0, limit=50):
    if section not in SECTIONS or type(offset) is not int or not 0 <= offset <= 10000000:
        raise DomainError("INVALID_ARGUMENT", "invalid console section or offset")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise DomainError("INVALID_ARGUMENT", "console limit must be in [1, 100]")
    now = time.time()
    with service.store.connection() as conn:
        conn.execute("BEGIN")
        counts = {
            name: {
                row["status"]: row["n"]
                for row in conn.execute(f"SELECT status,COUNT(*) AS n FROM {name} GROUP BY status")
            }
            for name in ("tasks", "workers", "jobs", "incidents")
        }
        counts["executions"] = conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]
        total = sum(counts[{"sessions": "workers", "overview": "tasks"}.get(section, section)].values())
        if section in {"overview", "tasks"}:
            rows = conn.execute(
                """SELECT t.id,t.title,t.adapter,t.desired,t.status,t.generation,t.current_worker,
                t.declared,t.created_at,t.updated_at,
                (SELECT COUNT(*) FROM jobs j WHERE j.task_id=t.id) AS job_count,
                EXISTS(SELECT 1 FROM jobs j WHERE j.task_id=t.id AND j.status='UNKNOWN') AS has_unknown_job,
                (SELECT COUNT(*) FROM executions e JOIN jobs j ON j.id=e.job_id WHERE j.task_id=t.id) AS execution_count
                FROM tasks t ORDER BY t.created_at DESC,t.id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            items = [unpack(row) for row in rows]
        elif section == "sessions":
            rows = conn.execute(
                """SELECT w.*,t.title AS task_title,t.current_worker AS task_current_worker,
                (SELECT COUNT(*) FROM incidents i WHERE i.worker_id=w.id) AS incident_count
                FROM workers w JOIN tasks t ON t.id=w.task_id
                ORDER BY w.created_at DESC,w.id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            )
            items = []
            for row in rows:
                value = unpack(row)
                value["has_handoff"] = bool(value.pop("handoff"))
                items.append(session_view(value, service.config, value.pop("task_current_worker"), now))
        else:
            items = [
                unpack(row)
                for row in conn.execute(
                    """SELECT i.rowid AS sequence,i.*,t.title AS task_title
                    FROM incidents i JOIN tasks t ON t.id=i.task_id
                    ORDER BY i.rowid DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                )
            ]
        recent_events = []
        recent_incidents = []
        if section == "overview":
            recent_events = [
                unpack(row) for row in conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 12")
            ]
            recent_incidents = [
                unpack(row)
                for row in conn.execute(
                    "SELECT rowid AS sequence,* FROM incidents ORDER BY rowid DESC LIMIT 5"
                )
            ]
    return {
        "section": section,
        "workspace_id": digest(
            [str(service.config.home), service.store.path.stat().st_dev, service.store.path.stat().st_ino]
        )[:24],
        "queried_at": now,
        "source": "sqlite ledger + owned process observations",
        "controller": controller_status(service, now),
        "counts": counts,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
        "recent_events": recent_events,
        "recent_incidents": recent_incidents,
    }
