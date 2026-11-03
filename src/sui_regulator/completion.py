"""A model-written completion manifest is consumed through one ownership gate.

The model judges task semantics. The Controller checks identity, current user
requirements, explicit verification receipts and unresolved side effects only.
"""

from __future__ import annotations

import json
import os
import stat
import time

from .models import DomainError, digest, encode, identifier, require_text

# Match the RPC byte budget: two permitted 16K-character fields can exceed
# 64 KiB in UTF-8 even though neither exceeds its character limit.
LIMIT = 262144


def requirements(conn, task):
    updates = conn.execute(
        "SELECT id,payload,updated_at FROM commands WHERE task_id=? AND status='SUCCEEDED' AND kind IN ('instruct_task','request_analysis') ORDER BY created_at,id",
        (task["id"],),
    ).fetchall()
    revision = digest(
        [
            task["objective"],
            task["completion_criteria"],
            task["verification_template"],
            [[r["id"], r["payload"]] for r in updates],
        ]
    )
    changed_at = max([task["created_at"], *[r["updated_at"] for r in updates]])
    return revision, changed_at


def marker_path(service, task_id, proxy_id, create=False):
    path = service.config.home / "completions" / task_id
    for directory in [service.config.home / "completions", path]:
        if directory.is_symlink():
            raise DomainError("INVALID_COMPLETION", "completion directories must not be symlinks")
        if create:
            directory.mkdir(mode=0o700, exist_ok=True)
    return path / (proxy_id + ".json")


def contract(service, task, worker, updates=None):
    if updates is None:
        with service.store.connection() as conn:
            revision, _ = requirements(conn, task)
    else:
        # Bind the token to the exact instruction snapshot delivered to the
        # model, never to a newer command that arrived during context assembly.
        revision = digest(
            [
                task["objective"],
                task["completion_criteria"],
                task["verification_template"],
                [[r["id"], encode(r["payload"])] for r in updates],
            ]
        )
    return {
        "path": str(marker_path(service, task["id"], worker["id"], create=True)),
        "writer": "complete_task writes this manifest; native tools may also write it atomically",
        "template": {
            "submission_id": identifier("completion"),
            "schema_version": 1,
            "task_id": task["id"],
            "proxy_id": worker["id"],
            "generation": worker["generation"],
            "requirements_revision": revision,
            "status": "completed",
            "summary": "Describe what was completed",
            "evidence": "Concrete results, paths, tests and limitations",
            "verification_attempt_id": None,
        },
        "retained_running_work": "Not supported: no verified responsibility-transfer contract. Keep the Task active or use wait_task until managed work is terminal.",
        "verification_template": task["verification_template"],
        "acceptance": "Model decides semantic completion; Controller checks ownership, current requirements, known terminal operations and any explicitly configured verification command.",
    }


def write_marker(service, task_id, proxy_id, payload):
    path = marker_path(service, task_id, proxy_id, create=True)
    temporary = path.with_name(identifier("completion") + ".tmp")
    data = encode(payload).encode()
    if len(data) > LIMIT:
        raise DomainError("INVALID_COMPLETION", "completion marker is too large")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def accept(service, task_id, proxy_id, payload, receipt_id=None):
    receipt_id = receipt_id or digest([task_id, proxy_id, payload])
    error, accepted = None, False
    with service.store.transaction() as conn:
        task = service._get(conn, "tasks", task_id)
        prior = conn.execute("SELECT * FROM completion_receipts WHERE id=?", (receipt_id,)).fetchone()
        if prior:
            if prior["status"] == "ACCEPTED":
                return {"task_id": task_id, "status": "COMPLETED", "completion_receipt": receipt_id}
            raise DomainError("INVALID_COMPLETION", prior["error"])
        try:
            if conn.execute(
                "SELECT 1 FROM commands WHERE task_id=? AND status='QUEUED' AND kind IN ('instruct_task','request_analysis','hold_task','cancel_task') LIMIT 1",
                (task_id,),
            ).fetchone():
                raise DomainError(
                    "CONTROL_PENDING",
                    "an accepted user instruction or stop command must be applied before completion",
                )
            if not isinstance(payload, dict) or set(payload) != {
                "submission_id",
                "schema_version",
                "task_id",
                "proxy_id",
                "generation",
                "requirements_revision",
                "status",
                "summary",
                "evidence",
                "verification_attempt_id",
            }:
                raise DomainError(
                    "INVALID_COMPLETION", "completion marker does not match the supplied template"
                )
            if (
                type(payload["schema_version"]) is not int
                or payload["schema_version"] != 1
                or payload["status"] != "completed"
            ):
                raise DomainError("INVALID_COMPLETION", "unsupported completion marker version or state")
            if (
                task["desired"] != "ACTIVE"
                or task["current_worker"] != proxy_id
                or payload["task_id"] != task_id
                or payload["proxy_id"] != proxy_id
                or type(payload["generation"]) is not int
                or payload["generation"] != task["generation"]
            ):
                raise DomainError("STALE_COMPLETION", "completion does not own the active task generation")
            worker = service._get(conn, "workers", proxy_id)
            if worker["lease_expires"] <= time.time() or worker["status"] not in {
                "STARTING",
                "RUNNING",
                "ROTATING",
            }:
                raise DomainError("STALE_COMPLETION", "completion owner no longer has a valid lease")
            revision, changed_at = requirements(conn, task)
            offered = worker["requirements_revision"]
            has_instructions = conn.execute(
                "SELECT 1 FROM commands WHERE task_id=? AND status='SUCCEEDED' AND kind IN ('instruct_task','request_analysis') LIMIT 1",
                (task_id,),
            ).fetchone()
            if offered != revision and has_instructions:
                raise DomainError(
                    "STALE_COMPLETION",
                    "requirements changed or full instruction history has not been offered to this Proxy",
                )
            if payload["requirements_revision"] != revision:
                raise DomainError(
                    "STALE_COMPLETION",
                    "user requirements changed; re-read task context and verify completion again",
                )
            from .requirements_delivery import require_read_ack

            require_read_ack(conn, task_id, proxy_id)
            require_text(payload["submission_id"], "submission_id", 128)
            require_text(payload["summary"], "summary")
            require_text(payload["evidence"], "completion evidence")
            jobs = conn.execute("SELECT status FROM jobs WHERE task_id=?", (task_id,)).fetchall()
            if any(job["status"] not in {"COMPLETED", "FAILED", "CANCELLED"} for job in jobs):
                raise DomainError("UNVERIFIED_COMPLETION", "managed jobs must have known terminal outcomes")
            if conn.execute(
                "SELECT 1 FROM tool_attempts WHERE task_id=? AND status IN ('STARTED','UNKNOWN','RUNNING') LIMIT 1",
                (task_id,),
            ).fetchone():
                raise DomainError(
                    "UNVERIFIED_COMPLETION",
                    "an earlier command has an unresolved outcome; verify it before completing",
                )
            verification = None
            if task["verification_template"]:
                attempt_id = require_text(payload["verification_attempt_id"], "verification_attempt_id", 128)
                verification = conn.execute(
                    "SELECT id,tool,status,finished_at,created_at,worker_id FROM tool_attempts WHERE task_id=? AND id=?",
                    (task_id, attempt_id),
                ).fetchone()
                if (
                    not verification
                    or verification["tool"] != task["verification_template"]
                    or verification["status"] != "SUCCEEDED"
                    or verification["worker_id"] != proxy_id
                    or verification["created_at"] < changed_at
                ):
                    raise DomainError(
                        "UNVERIFIED_COMPLETION",
                        "configured verification requires a current successful command receipt",
                    )
            now = time.time()
            completion = {
                **payload,
                "accepted_at": now,
                "receipt_id": receipt_id,
                "semantic_result": "proxy-declared",
                "managed_jobs_terminal": True,
                "managed_jobs_verified": all(job["status"] == "COMPLETED" for job in jobs),
                "all_managed_jobs_successful": all(job["status"] == "COMPLETED" for job in jobs),
                "verification": dict(verification) if verification else None,
            }
            conn.execute(
                "UPDATE tasks SET desired='COMPLETED',status='COMPLETED',current_worker=NULL,completion=?,updated_at=? WHERE id=?",
                (encode(completion), now, task_id),
            )
            conn.execute(
                "UPDATE workers SET status='COMPLETED',lease_expires=0,ended_at=? WHERE id=?", (now, proxy_id)
            )
            service.store.event(conn, task_id, "TASK_COMPLETED", "completion-protocol", proxy_id, completion)
            accepted = True
        except DomainError as exc:
            error = str(exc)
        conn.execute(
            "INSERT INTO completion_receipts(id,task_id,worker_id,status,error,created_at) VALUES(?,?,?,?,?,?)",
            (receipt_id, task_id, proxy_id, "ACCEPTED" if accepted else "REJECTED", error, time.time()),
        )
        service.store.event(
            conn,
            task_id,
            "COMPLETION_ACCEPTED" if accepted else "COMPLETION_REJECTED",
            "completion-protocol",
            proxy_id,
            {"receipt_id": receipt_id, "error": error},
        )
    if error:
        raise DomainError("UNVERIFIED_COMPLETION", error)
    return {"task_id": task_id, "status": "COMPLETED", "completion_receipt": receipt_id}


def consume_markers(controller):
    service = controller.service
    for task in service.store.rows(
        "SELECT id,current_worker FROM tasks WHERE desired='ACTIVE' AND current_worker IS NOT NULL"
    ):
        try:
            path = marker_path(service, task["id"], task["current_worker"])
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
                    continue
                raw = source.read(LIMIT + 1)
            if len(raw) > LIMIT:
                continue
            payload = json.loads(raw)
            worker = service.store.rows("SELECT * FROM workers WHERE id=?", (task["current_worker"],))[0]
            accept(service, task["id"], task["current_worker"], payload)
            controller._stop(worker)
        except (OSError, ValueError, DomainError):
            # A partial/invalid candidate is never a Proxy fault. The model may
            # replace it; valid rejected manifests have queryable receipts.
            continue
