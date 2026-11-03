"""Durable recovery policy: task lifetime is independent of retry rate limits."""

from __future__ import annotations

import json
import math
import random
import time

from .models import DomainError, digest, encode
from .processes import process_state


def validate_policy(mode, budget):
    if not isinstance(mode, str) or mode not in {"persistent", "bounded"}:
        raise DomainError("INVALID_ARGUMENT", "recovery_mode must be persistent or bounded")
    if type(budget) not in (int, float) or not math.isfinite(budget) or budget < 0:
        raise DomainError(
            "INVALID_ARGUMENT", "max_proxy_seconds must be finite and nonnegative; 0 is unlimited"
        )
    return mode, budget


def backoff(conn, task, config, now):
    state = json.loads(task["recovery_state"])
    streak = state.get("failure_streak", 0) + 1
    state.update(failure_streak=streak, phase="RETRY_BACKOFF")
    ceiling = min(config.recovery_backoff_max, config.recovery_backoff_base * 2 ** min(streak - 1, 20))
    delay = random.uniform(0.8 * ceiling, ceiling)
    conn.execute("UPDATE tasks SET recovery_state=? WHERE id=?", (encode(state), task["id"]))
    return delay


def advise(conn, service, task, reason, now=None):
    """Deliver durable guidance without classifying a failed test as a dead Proxy."""
    now = time.time() if now is None else now
    state = json.loads(task["recovery_state"])
    state.update(guidance=reason, guidance_at=now)
    conn.execute("UPDATE tasks SET recovery_state=? WHERE id=?", (encode(state), task["id"]))
    service.store.event(
        conn, task["id"], "WORK_OBSERVATION", "candleholder", "controller", {"reason": reason}
    )


def monitor(controller):
    service, config, now = controller.service, controller.config, time.time()
    stop = []
    with service.store.transaction() as conn:
        for task in conn.execute(
            "SELECT * FROM tasks WHERE desired='ACTIVE' AND (recovery_mode='persistent' OR max_proxy_seconds>0)"
        ).fetchall():
            state = json.loads(task["recovery_state"])
            if task["current_worker"]:
                proxy = service._get(conn, "workers", task["current_worker"])
                owned_identity = json.loads(proxy["identity"]) if proxy["identity"] else None
                if (
                    proxy["lease_expires"] > now
                    and now - proxy["created_at"] >= config.restart_window
                    and process_state(owned_identity) in {"R", "S", "D", "I"}
                ):
                    state.update(failure_streak=0, last_stable_session_at=now)
            last = state.get(
                "last_runtime_evidence_at", state.get("last_verified_progress_at", task["created_at"])
            )
            markers = state.get("job_markers", {})
            progress, evidence = False, []
            for job in conn.execute("SELECT * FROM jobs WHERE task_id=?", (task["id"],)):
                metric = json.loads(job["metric"])
                marker = {"status": job["status"], "step": metric.get("step", 0)}
                previous = markers.get(job["id"], {"status": "", "step": 0})
                if (marker["step"] > previous["step"]) or (
                    marker["status"] in {"COMPLETED", "FAILED", "CANCELLED"}
                    and previous["status"] != marker["status"]
                ):
                    progress = True
                    evidence.append({"source": "runner", "job_id": job["id"], **marker})
                markers[job["id"]] = marker
            state["job_markers"] = markers
            fingerprints = state.get("evidence_fingerprints", [])
            events = conn.execute(
                "SELECT id,data FROM events WHERE task_id=? AND kind='COMMAND_EXECUTED' AND id>? ORDER BY id LIMIT 200",
                (task["id"], state.get("command_cursor", 0)),
            ).fetchall()
            for event in events:
                state["command_cursor"] = event["id"]
                data = json.loads(event["data"])
                kind = data.get("progress_kind", "none")
                is_verifier = (
                    bool(task["verification_template"])
                    and data.get("template") == task["verification_template"]
                )
                if data.get("timed_out") or not data.get("ownership_current", True):
                    continue
                if not (kind in {"check", "test", "diagnostic"} or is_verifier):
                    continue
                fingerprint = digest(
                    [data.get("template"), data.get("exit_code"), data.get("stdout"), data.get("stderr")]
                )
                if fingerprint not in fingerprints and len(fingerprints) < 512:
                    fingerprints.append(fingerprint)
                    progress = True
                    evidence.append({"source": "command-runner", "event_id": event["id"], "kind": kind})
            state["evidence_fingerprints"] = fingerprints
            if progress:
                last = now
                state.update(
                    last_runtime_evidence_at=now,
                    last_verified_progress_at=now,
                    progress_stage=0,
                    phase="OBSERVING",
                )
                service.store.event(
                    conn,
                    task["id"],
                    "RUNTIME_EVIDENCE_UPDATED",
                    "candleholder",
                    "controller",
                    {
                        "evidence": evidence,
                        "scope": "configured runtime observations; not task value or semantic progress",
                    },
                )
            elapsed = max(0, now - max(last, task["recovery_reset_at"]))
            state["no_progress_seconds"] = elapsed  # Legacy API alias only.
            state["runtime_evidence_gap_seconds"] = elapsed
            used = conn.execute(
                "SELECT COALESCE(SUM(MAX(0,COALESCE(ended_at,?)-created_at)),0) FROM workers WHERE task_id=?",
                (now, task["id"]),
            ).fetchone()[0]
            state["proxy_seconds_used"] = used
            if task["max_proxy_seconds"] and used >= task["max_proxy_seconds"]:
                if task["current_worker"]:
                    worker = service._get(conn, "workers", task["current_worker"])
                    stop.append(dict(worker))
                    conn.execute(
                        "UPDATE workers SET status='REVOKED',ended_at=?,lease_expires=0 WHERE id=?",
                        (now, worker["id"]),
                    )
                conn.execute(
                    "UPDATE tasks SET desired='HOLD',status='HOLD',current_worker=NULL,updated_at=? WHERE id=?",
                    (now, task["id"]),
                )
                state.update(
                    phase="WAITING_RESOURCE",
                    guidance="Proxy wall-time budget exhausted. Increase the budget and explicitly Resume.",
                )
                service.store.event(
                    conn,
                    task["id"],
                    "RESOURCE_BUDGET_EXHAUSTED",
                    "candleholder",
                    "controller",
                    {"proxy_seconds_used": used, "limit": task["max_proxy_seconds"], "jobs_retained": True},
                )
            elif task["status"] in {"QUARANTINED", "WAITING_RESOURCE"}:
                state["phase"] = task["status"]
            elif task["recovery_mode"] == "persistent":
                stage = state.get("progress_stage", 0)
                due = (
                    elapsed >= config.no_progress_seconds
                    and stage == 0
                    or elapsed >= config.escalation_seconds
                    and (stage < 2 or now - state.get("last_escalation_at", 0) >= config.escalation_seconds)
                )
                if due:
                    escalated = elapsed >= config.escalation_seconds
                    guidance = (
                        "No new configured runtime evidence has been observed; business progress is unknown. This is not a Session failure. "
                        "Continue active debugging or waiting as appropriate to the task. Record useful evidence "
                        "or a checkpoint when convenient; experimental correctness and the next approach are your decisions."
                    )
                    state.update(guidance=guidance, guidance_at=now, progress_stage=2 if escalated else 1)
                    if escalated:
                        state["last_escalation_at"] = now
                    service.store.event(
                        conn,
                        task["id"],
                        "PROGRESS_REVIEW_ESCALATED" if escalated else "PROGRESS_REVIEW_REQUESTED",
                        "candleholder",
                        "controller",
                        {
                            "reason": guidance,
                            "no_progress_seconds": elapsed,
                            "session_failure": False,
                            "scope": "runtime evidence only; no business judgment",
                            "rotation_requested": False,
                        },
                    )
            conn.execute("UPDATE tasks SET recovery_state=? WHERE id=?", (encode(state), task["id"]))
    for worker in stop:
        controller._stop(worker)
