from __future__ import annotations

import json
import time
from contextlib import nullcontext

from ..models import Credentials, DomainError, digest, encode
from ..processes import alive
from ..service import Service, unpack
from .models import Incident


def publish(service: Service, credentials: Credentials, incidents: list[Incident], *, connection=None) -> None:
    if not incidents:
        return
    context = service.store.transaction() if connection is None else nullcontext(connection)
    with context as conn:
        for incident in incidents:
            payload = incident.to_dict()
            payload["id"] = (
                "incident-" + digest([incident.id, credentials.worker_id, credentials.generation])[:32]
            )
            inserted = conn.execute(
                """INSERT OR IGNORE INTO incidents(id,task_id,worker_id,generation,rule_id,payload,action,status,created_at)
                VALUES(?,?,?,?,?,?,?,'PENDING',?)""",
                (
                    payload["id"],
                    credentials.task_id,
                    credentials.worker_id,
                    credentials.generation,
                    incident.rule_id,
                    encode(payload),
                    incident.action,
                    time.time(),
                ),
            ).rowcount
            if inserted:
                service.store.event(
                    conn,
                    credentials.task_id,
                    "INCIDENT_DETECTED",
                    "detection-engine",
                    credentials.worker_id,
                    payload,
                )


def list_incidents(service: Service, task_id: str, after: int = 0, limit: int = 100,
                   proxy_id: str | None = None) -> list[dict]:
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid incident pagination")
    if not service.store.rows("SELECT 1 FROM tasks WHERE id=?", (task_id,)):
        raise DomainError("NOT_FOUND", "tasks: entity not found")
    if proxy_id is not None:
        if not isinstance(proxy_id, str):
            raise DomainError("INVALID_ARGUMENT", "invalid Sui Proxy identity")
        if not service.store.rows("SELECT 1 FROM workers WHERE id=? AND task_id=?", (proxy_id, task_id)):
            raise DomainError("NOT_FOUND", "Sui Proxy does not belong to this Task")
    clause = " AND worker_id=?" if proxy_id is not None else ""
    values = (task_id, after, proxy_id, limit) if proxy_id is not None else (task_id, after, limit)
    return [
        unpack(row)
        for row in service.store.rows(
            "SELECT rowid AS sequence,* FROM incidents WHERE task_id=? AND rowid>?" + clause + " ORDER BY rowid LIMIT ?",
            values,
        )
    ]


def apply_pending(controller) -> None:
    service, config = controller.service, controller.config
    rows = service.store.rows(
        "SELECT id FROM incidents WHERE status='PENDING' ORDER BY CASE action WHEN 'wait' THEN 0 WHEN 'quarantine' THEN 0 ELSE 1 END,created_at,rowid LIMIT 100"
    )
    for row in rows:
        stop = None
        with service.store.transaction() as conn:
            incident = conn.execute(
                "SELECT * FROM incidents WHERE id=? AND status='PENDING'", (row["id"],)
            ).fetchone()
            if incident is None:
                continue
            task = service._get(conn, "tasks", incident["task_id"])
            payload = json.loads(incident["payload"])
            action, state, outcome = incident["action"], "OBSERVED", {}
            if task["desired"] != "ACTIVE":
                state = "IGNORED"
                outcome = {"reason": "persistent user intent or terminal task takes priority"}
            elif (
                task["current_worker"] != incident["worker_id"]
                or task["generation"] != incident["generation"]
            ):
                state = "STALE"
                outcome = {"reason": "incident does not own the current task generation"}
            else:
                worker = service._get(conn, "workers", incident["worker_id"])
                persistent = task["recovery_mode"] == "persistent"
                from ..autonomy import trusted
                if incident["rule_id"] in {"TOOL-002", "ENV-001"} and action in {"quarantine", "wait", "fresh_session"}:
                    action = "advise"
                if persistent and action in {"quarantine", "wait"}:
                    code = payload["evidence"].get("code")
                    if trusted(task):
                        action = "fresh_session"
                    elif code in {401, 403, "AUTH_REQUIRED"}:
                        action = "wait"
                    elif code == "SESSION_ID_MISMATCH":
                        action = "quarantine"
                    else:
                        action = "fresh_session"
                if action == "bounded_retry" and (
                    payload["evidence"].get("terminal") or worker["status"] == "EXITED"
                ):
                    action = "fresh_session"
                if action == "fresh_session":
                    attempts = conn.execute(
                        "SELECT COUNT(*) FROM incidents WHERE task_id=? AND rule_id=? AND status='APPLIED' AND action='fresh_session' AND created_at>=?",
                        (
                            task["id"],
                            incident["rule_id"],
                            max(task["recovery_reset_at"], time.time() - config.restart_window),
                        ),
                    ).fetchone()[0]
                    unresolved = conn.execute(
                        "SELECT 1 FROM jobs WHERE task_id=? AND status IN ('UNKNOWN','STARTING') LIMIT 1",
                        (task["id"],),
                    ).fetchone()
                    if not persistent and (attempts >= payload["max_attempts"] or unresolved):
                        action = "quarantine"
                        outcome = {
                            "reason": "rule recovery budget exhausted"
                            if not unresolved
                            else "job outcome unresolved; do not replay side effects"
                        }
                if action in {"fresh_session", "quarantine", "wait"}:
                    now = time.time()
                    status = {"fresh_session": "READY", "quarantine": "QUARANTINED", "wait": "WAITING"}[
                        action
                    ]
                    delay = (
                        payload["retry_seconds"]
                        if payload["category"] == "transport"
                        else config.restart_cooldown
                    )
                    if action == "fresh_session" and payload["category"] == "transport":
                        delay *= 2 ** min(attempts, 6)
                    if persistent and action == "fresh_session":
                        from ..recovery import backoff

                        normal_turn_end = incident["rule_id"] == "TURN-001" or worker["error"] == "SESSION_FINISHED_WITHOUT_COMPLETION"
                        status = "READY" if normal_turn_end else "RETRY_BACKOFF"
                        delay = config.restart_cooldown if normal_turn_end else backoff(conn, task, config, now)
                        if attempts >= payload["max_attempts"]:
                            # A per-rule request window defers the next attempt;
                            # it never ends a Persistent Task's lifetime.
                            first = conn.execute("SELECT MIN(applied_at) FROM incidents WHERE task_id=? AND rule_id=? AND status='APPLIED' AND action='fresh_session' AND created_at>=?",
                                                 (task["id"], incident["rule_id"], max(task["recovery_reset_at"], now - config.restart_window))).fetchone()[0]
                            if first is not None:
                                delay = max(delay, first + config.restart_window - now)
                        outcome.update(policy="persistent", unresolved_job=bool(unresolved), request_window_limited=attempts >= payload["max_attempts"])
                    elif persistent and action == "wait":
                        status = "WAITING_RESOURCE"
                        recovery = json.loads(task["recovery_state"])
                        recovery.update(phase=status, guidance="Repair rejected credentials, then explicitly Resume.")
                        conn.execute("UPDATE tasks SET recovery_state=? WHERE id=?", (encode(recovery), task["id"]))
                    conn.execute(
                        "UPDATE workers SET status=?,error=?,ended_at=?,lease_expires=0 WHERE id=?",
                        (
                            "RETIRED" if persistent and action == "fresh_session" and normal_turn_end else "FAILED" if action == "fresh_session" else "REVOKED",
                            incident["rule_id"],
                            now,
                            worker["id"],
                        ),
                    )
                    conn.execute(
                        "UPDATE tasks SET current_worker=NULL,status=?,next_start_at=?,updated_at=? WHERE id=?",
                        (status, now + delay, now, task["id"]),
                    )
                    if action == "fresh_session":
                        reason = (
                            "MALFORMED_OR_REPEATED_TOOL_CALLS"
                            if incident["rule_id"] == "TOOL-001"
                            else incident["rule_id"]
                        )
                        service.store.event(
                            conn,
                            task["id"],
                            "WORKER_ROTATED" if persistent and (incident["rule_id"] == "TURN-001" or worker["error"] == "SESSION_FINISHED_WITHOUT_COMPLETION") else "WORKER_FAILED",
                            "candleholder",
                            worker["id"],
                            {"reason": reason, "incident_id": incident["id"]},
                        )
                    elif action == "quarantine":
                        service.store.event(
                            conn,
                            task["id"],
                            "TASK_QUARANTINED",
                            "candleholder",
                            "controller",
                            {"incident_id": incident["id"]},
                        )
                    outcome.update(
                        lease_revoked=True,
                        proxy_id=worker["id"],
                        generation=worker["generation"],
                        task_status=status,
                        jobs_retained=True,
                        next_start_at=now + delay,
                    )
                    stop, state = dict(worker), "APPLIED"
                elif action in {"replan", "advise"}:
                    from ..recovery import advise

                    reason = "Observed a repeated tool error or task environment issue. This is not evidence of Session failure. Inspect it and decide how to debug or continue; no automatic rotation is requested."
                    advise(conn, service, task, reason)
                    outcome = {"guidance": reason, "proxy_retained": True, "jobs_retained": True}
                    state = "APPLIED"
                elif action == "probe":
                    jobs = conn.execute(
                        "SELECT id,status,identity FROM jobs WHERE task_id=?", (task["id"],)
                    ).fetchall()
                    outcome = {
                        "worker_alive": alive(json.loads(worker["identity"]) if worker["identity"] else None),
                        "jobs": [
                            {
                                "id": job["id"],
                                "status": job["status"],
                                "process_alive": alive(
                                    json.loads(job["identity"]) if job["identity"] else None
                                ),
                            }
                            for job in jobs
                        ],
                        "probe": "ledger and owned process identity only; no prompt or tool replay",
                    }
                elif action == "bounded_retry":
                    outcome = {
                        "retry": "permit bounded CLI transport retry; never inject Continue or resubmit tools"
                    }
            conn.execute(
                "UPDATE incidents SET action=?,status=?,outcome=?,applied_at=? WHERE id=?",
                (action, state, encode(outcome), time.time(), incident["id"]),
            )
            service.store.event(
                conn,
                incident["task_id"],
                "INCIDENT_ACTION",
                "candleholder",
                "controller",
                {
                    "incident_id": incident["id"],
                    "rule_id": incident["rule_id"],
                    "action": action,
                    "status": state,
                    "outcome": outcome,
                },
            )
        if stop:
            controller._stop(stop)
