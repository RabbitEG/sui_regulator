from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from . import PROTOCOL_VERSION
from .models import Credentials, DomainError, digest, encode, identifier, mock_spec, require_text
from .processes import identity
from .registry import ProxyRegistry
from .service import Service, unpack


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def fence(conn: sqlite3.Connection, credentials: Credentials, now: float | None = None) -> sqlite3.Row:
    now = time.time() if now is None else now
    task = Service._get(conn, "tasks", credentials.task_id)
    worker = Service._get(conn, "workers", credentials.worker_id)
    if (
        task["desired"] != "ACTIVE"
        or task["current_worker"] != worker["id"]
        or task["generation"] != credentials.generation
        or worker["task_id"] != task["id"]
        or worker["generation"] != credentials.generation
        or worker["lease_expires"] <= now
        or worker["status"] not in {"STARTING", "RUNNING", "ROTATING"}
        or not secrets.compare_digest(worker["lease_hash"], token_hash(credentials.lease_token))
    ):
        raise DomainError("FENCED", "worker ownership or lease is no longer valid")
    return worker


def execution_authorized(conn: sqlite3.Connection, job: sqlite3.Row) -> bool:
    task = Service._get(conn, "tasks", job["task_id"])
    worker = Service._get(conn, "workers", job["authorized_worker"])
    return (
        task["desired"] == "ACTIVE"
        and task["current_worker"] == worker["id"]
        and task["generation"] == job["authorized_generation"] == worker["generation"]
        and worker["lease_expires"] > time.time()
        and worker["status"] in {"STARTING", "RUNNING", "ROTATING"}
    )


def consume_cancellation(service: Service, conn: sqlite3.Connection, job: sqlite3.Row) -> bool:
    """Runner-side authorization at the point of the cancellation side effect."""
    if not job["cancel_requested"]:
        return False
    auth = json.loads(job["cancel_auth"] or "{}")
    authorized = False
    if auth.get("source") == "yan":
        command = conn.execute("SELECT * FROM commands WHERE id=?", (auth.get("command_id"),)).fetchone()
        authorized = bool(
            command
            and command["kind"] == "cancel_job"
            and command["status"] == "SUCCEEDED"
            and command["task_id"] == job["task_id"]
            and json.loads(command["payload"]).get("job_id") == job["id"]
        )
    elif auth.get("source") == "worker":
        authorized = execution_authorized(
            conn,
            {
                "task_id": job["task_id"],
                "authorized_worker": auth.get("worker_id"),
                "authorized_generation": auth.get("generation"),
            },
        )
    if not authorized:
        conn.execute("UPDATE jobs SET cancel_requested=0,cancel_auth=NULL WHERE id=?", (job["id"],))
        service.store.event(
            conn, job["task_id"], "RUNNER_CANCEL_FENCED", "runner-observed", "runner", {"job_id": job["id"]}
        )
        return False
    now = time.time()
    conn.execute(
        "UPDATE jobs SET status='CANCELLED',exit_code=130,observed_at=?,updated_at=? WHERE id=?",
        (now, now, job["id"]),
    )
    conn.execute("UPDATE executions SET finished_at=? WHERE job_id=?", (now, job["id"]))
    service.store.event(
        conn,
        job["task_id"],
        "JOB_CANCELLED",
        "runner-observed",
        "runner",
        {"job_id": job["id"], "authorization": auth},
    )
    return True


class SuiProtocol:
    def __init__(self, service: Service):
        self.service, self.store, self.config = service, service.store, service.config

    def register(self, task_id: str) -> Credentials:
        """Controller-only bootstrap: register and atomically claim a free task."""
        now, token = time.time(), secrets.token_urlsafe(32)
        with self.store.transaction() as conn:
            task = self.service._get(conn, "tasks", task_id)
            if (
                task["desired"] != "ACTIVE"
                or task["status"] in {"QUARANTINED", "WAITING", "WAITING_RESOURCE"}
                or task["current_worker"]
            ):
                raise DomainError("NOT_CLAIMABLE", "task is not available for a new worker")
            worker_id, generation = identifier("sui"), task["generation"] + 1
            conn.execute(
                """INSERT INTO workers(id,session_id,task_id,generation,adapter,status,lease_hash,
                lease_expires,heartbeat_at,activity_at,capabilities,created_at)
                VALUES(?,?,?,?,?,'STARTING',?,?,?,?,?,?)""",
                (
                    worker_id,
                    str(uuid.uuid4()),
                    task_id,
                    generation,
                    task["adapter"],
                    token_hash(token),
                    now + self.config.lease_seconds,
                    now,
                    now,
                    encode(["sui-protocol-0.1", "managed-mock-jobs"]),
                    now,
                ),
            )
            conn.execute(
                "UPDATE tasks SET generation=?,current_worker=?,status='RUNNING',updated_at=? WHERE id=?",
                (generation, worker_id, now, task_id),
            )
            self.store.event(
                conn, task_id, "WORKER_CLAIMED", "controller", worker_id, {"generation": generation}
            )
        return Credentials(task_id, worker_id, generation, token)

    @contextmanager
    def _write(self, credentials: Credentials):
        try:
            with self.store.transaction() as conn:
                worker = fence(conn, credentials)
                yield conn, worker
        except DomainError as exc:
            with self.store.transaction() as conn:
                self.store.event(
                    conn,
                    credentials.task_id,
                    "PROTOCOL_REJECTED",
                    "protocol",
                    credentials.worker_id,
                    {"code": exc.code, "generation": credentials.generation},
                )
            raise

    def claim_task(self, credentials: Credentials) -> dict:
        with self._write(credentials) as (conn, worker):
            conn.execute(
                "UPDATE workers SET status='RUNNING',pid=?,identity=? WHERE id=?",
                (os.getpid(), encode(identity(os.getpid())), credentials.worker_id),
            )
            created_at = worker["created_at"]
        ProxyRegistry(self.config.home).publish(
            credentials.worker_id,
            credentials.task_id,
            credentials.generation,
            identity(os.getpid()),
            created_at,
        )
        return self.get_task_context(credentials)

    def heartbeat(self, credentials: Credentials, *, effective_activity: bool = False) -> None:
        now = time.time()
        with self._write(credentials) as (conn, _):
            conn.execute(
                "UPDATE workers SET heartbeat_at=?,lease_expires=? WHERE id=?",
                (now, now + self.config.lease_seconds, credentials.worker_id),
            )
            if effective_activity:
                conn.execute("UPDATE workers SET activity_at=? WHERE id=?", (now, credentials.worker_id))
        ProxyRegistry(self.config.home).heartbeat(credentials.worker_id)

    def get_task_context(self, credentials: Credentials) -> dict:
        with self.store.connection() as conn:
            worker = fence(conn, credentials)
            rotation = worker["rotation_at"] is not None
        task = self.service.get_task(credentials.task_id)
        recent = self.store.rows(
            "SELECT * FROM events WHERE task_id=? ORDER BY id DESC LIMIT 100", (credentials.task_id,)
        )
        task["events"] = [unpack(row) for row in reversed(recent)]
        task["instructions"] = [
            unpack(row)
            for row in self.store.rows(
                "SELECT * FROM commands WHERE task_id=? AND kind IN ('instruct_task','request_analysis') AND status='SUCCEEDED' ORDER BY created_at,id",
                (credentials.task_id,),
            )
        ]
        from .completion import contract

        task["completion_contract"] = contract(self.service, task, worker, updates=task["instructions"])
        task["rotation_policy"] = {"requested": rotation, "can_defer": True, "forced_deadline": None,
                                   "instruction": "Continue slow or active debugging. Handoff only at a safe boundary; age alone never forces retirement."}
        task["rotation_requested"] = rotation
        task["protocol_version"] = PROTOCOL_VERSION
        from .commands import load_registry

        task["command_templates"] = {
            name: {key: entry[key] for key in ("patterns", "read_only", "progress_kind", "allow_during_background")}
            for name, entry in load_registry(self.config.command_registry).items()
        }
        task["execution_capabilities"] = {
            "native_code_tools": task["execution_profile"] == "claude_code",
            "native_read_tools": task["adapter"] == "claude",
            "managed_job_runner": "synthetic only",
            "external_experiments": "Proxy-operated; persist identifiers and verify before replay",
            "resource_migration": "Proxy responsibility; no Controller scheduler",
        }
        workspace = Path(task["workspace"])
        task["workspace_exists"] = workspace.is_dir()
        # Git is optional evidence, never an instruction source or mandatory recovery input.
        task["git_status"] = "unavailable: no repository in this managed workspace"
        return task

    def report_progress(
        self,
        credentials: Credentials,
        *,
        phase: str,
        summary: str,
        job_ids: list[str] | None = None,
        next_action: str = "",
        blockers: list | None = None,
    ) -> dict:
        require_text(phase, "phase", 128)
        require_text(summary, "summary")
        if job_ids is not None and (
            not isinstance(job_ids, list)
            or len(job_ids) > 100
            or any(not isinstance(value, str) or not value or len(value) > 128 for value in job_ids)
        ):
            raise DomainError("INVALID_ARGUMENT", "job_ids must be a bounded list of job IDs")
        if (
            (blockers is not None and (not isinstance(blockers, list) or len(blockers) > 50))
            or not isinstance(next_action, str)
            or len(next_action) > 16384
        ):
            raise DomainError("INVALID_ARGUMENT", "invalid progress fields")
        report = {
            "protocol_version": PROTOCOL_VERSION,
            "task_id": credentials.task_id,
            "worker_id": credentials.worker_id,
            "generation": credentials.generation,
            "phase": phase,
            "summary": summary,
            "job_ids": job_ids or [],
            "next_action": next_action,
            "blockers": blockers or [],
            "updated_at": time.time(),
        }
        if len(encode(report)) > 65536:
            raise DomainError("INVALID_ARGUMENT", "report is too large")
        with self._write(credentials) as (conn, _):
            for job_id in report["job_ids"]:
                job = self.service._get(conn, "jobs", job_id)
                if job["task_id"] != credentials.task_id:
                    raise DomainError("FORBIDDEN", "job belongs to another task")
            conn.execute(
                "UPDATE tasks SET declared=?,declared_at=?,updated_at=? WHERE id=?",
                (encode(report), report["updated_at"], report["updated_at"], credentials.task_id),
            )
            conn.execute("UPDATE workers SET activity_at=? WHERE id=?", (time.time(), credentials.worker_id))
            self.store.event(
                conn, credentials.task_id, "PROGRESS_REPORT", "worker-declared", credentials.worker_id, report
            )
        return report

    def report_blocker(self, credentials: Credentials, reason: str, waiting_for: str = "user") -> dict:
        if waiting_for not in {"user", "resource", "external"}:
            raise DomainError("INVALID_ARGUMENT", "waiting_for must be user/resource/external")
        return self.report_progress(
            credentials,
            phase=f"WAITING_{waiting_for.upper()}",
            summary=reason,
            blockers=[{"reason": reason, "waiting_for": waiting_for}],
        )

    def launch_job(self, credentials: Credentials, logical_key: str, spec: dict | None = None) -> dict:
        require_text(logical_key, "logical_key", 256)
        spec = mock_spec({} if spec is None else spec)
        with self._write(credentials) as (conn, _):
            old = conn.execute(
                "SELECT * FROM jobs WHERE task_id=? AND logical_key=?", (credentials.task_id, logical_key)
            ).fetchone()
            if old:
                if old["spec_hash"] != digest(spec):
                    raise DomainError(
                        "IDEMPOTENCY_CONFLICT", "logical job key already has a different specification"
                    )
                return self.service._job_view(old)
            if conn.execute("SELECT 1 FROM jobs WHERE task_id=? AND status IN ('STARTING','UNKNOWN') LIMIT 1", (credentials.task_id,)).fetchone():
                raise DomainError("UNRESOLVED_JOB", "inspect the existing Job before launching a new logical key")
            from .attempts import denial

            task = self.service._get(conn, "tasks", credentials.task_id)
            denied = denial(conn, task, digest(["launch_job", logical_key]), "")
            if denied:
                raise DomainError("UNRESOLVED_OPERATION", denied)
            job_id, exp_id, now = identifier("job"), identifier("experiment"), time.time()
            conn.execute(
                "INSERT INTO experiments(id,task_id,name,spec,created_at) VALUES(?,?,?,?,?)",
                (exp_id, credentials.task_id, logical_key, encode(spec), now),
            )
            status = "QUEUED" if spec["resource_available"] else "BLOCKED"
            conn.execute(
                """INSERT INTO jobs(id,task_id,experiment_id,logical_key,spec,spec_hash,status,
                authorized_worker,authorized_generation,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id,
                    credentials.task_id,
                    exp_id,
                    logical_key,
                    encode(spec),
                    digest(spec),
                    status,
                    credentials.worker_id,
                    credentials.generation,
                    now,
                    now,
                ),
            )
            self.store.event(
                conn,
                credentials.task_id,
                "JOB_REQUESTED",
                "protocol",
                credentials.worker_id,
                {"job_id": job_id, "logical_key": logical_key, "status": status},
            )
            return self.service._job_view(self.service._get(conn, "jobs", job_id))

    def attach_job(self, credentials: Credentials, job_id: str) -> dict:
        with self._write(credentials) as (conn, _):
            job = self.service._get(conn, "jobs", job_id)
            if job["task_id"] != credentials.task_id:
                raise DomainError("FORBIDDEN", "job belongs to another task")
            self.store.event(
                conn,
                credentials.task_id,
                "JOB_ATTACHED",
                "protocol",
                credentials.worker_id,
                {"job_id": job_id},
            )
            return self.service._job_view(job)

    def cancel_job(self, credentials: Credentials, job_id: str) -> dict:
        with self._write(credentials) as (conn, _):
            job = self.service._get(conn, "jobs", require_text(job_id, "job_id", 128))
            if job["task_id"] != credentials.task_id:
                raise DomainError("FORBIDDEN", "job belongs to another task")
            if job["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                conn.execute(
                    "UPDATE jobs SET cancel_requested=1,cancel_auth=? WHERE id=?",
                    (
                        encode(
                            {
                                "source": "worker",
                                "worker_id": credentials.worker_id,
                                "generation": credentials.generation,
                            }
                        ),
                        job_id,
                    ),
                )
                self.store.event(
                    conn,
                    credentials.task_id,
                    "JOB_CANCELLATION_REQUESTED",
                    "protocol",
                    credentials.worker_id,
                    {"job_id": job_id},
                )
            return {"job_id": job_id, "requested": True, "observed_status": job["status"]}

    def handoff(self, credentials: Credentials, summary: dict) -> dict:
        if not isinstance(summary, dict) or not summary or len(encode(summary)) > 65536:
            raise DomainError("INVALID_ARGUMENT", "handoff must be a bounded nonempty object")
        document = {**summary, "protocol_version": PROTOCOL_VERSION, "generation": credentials.generation}
        with self._write(credentials) as (conn, _):
            conn.execute("UPDATE workers SET handoff=?,handoff_at=? WHERE id=?", (encode(document), time.time(), credentials.worker_id))
            self.store.event(
                conn, credentials.task_id, "HANDOFF", "worker-declared", credentials.worker_id, document
            )
        return document

    def publish_artifact(self, credentials: Credentials, path: str, kind: str = "report") -> dict:
        require_text(path, "path", 4096)
        require_text(kind, "kind", 128)
        with self._write(credentials) as (conn, _):
            task = self.service._get(conn, "tasks", credentials.task_id)
            root = Path(task["workspace"]).resolve()
            artifact = (root / path).resolve()
            if not artifact.is_relative_to(root) or not artifact.is_file():
                raise DomainError(
                    "INVALID_ARGUMENT", "artifact must be an existing file within managed workspace"
                )
            aid = identifier("artifact")
            conn.execute(
                "INSERT OR IGNORE INTO artifacts(id,task_id,path,kind,source,created_at) VALUES(?,?,?,?,?,?)",
                (aid, credentials.task_id, str(artifact), kind, "worker-declared", time.time()),
            )
            self.store.event(
                conn,
                credentials.task_id,
                "ARTIFACT_DECLARED",
                "worker-declared",
                credentials.worker_id,
                {"path": str(artifact)},
            )
            return dict(
                conn.execute(
                    "SELECT * FROM artifacts WHERE task_id=? AND path=?", (credentials.task_id, str(artifact))
                ).fetchone()
            )

    def complete_task(self, credentials: Credentials, summary: str, evidence: str = "") -> dict:
        from .completion import accept, write_marker

        require_text(summary, "summary")
        context = self.get_task_context(credentials)
        if context["completion_criteria"]:
            require_text(evidence, "completion evidence")
        elif not isinstance(evidence, str) or len(evidence) > 16384:
            raise DomainError("INVALID_ARGUMENT", "invalid completion evidence")
        verification_id = None
        if context["verification_template"]:
            from .commands import run_command

            verification = run_command(self.service, credentials, context["verification_template"])
            if verification["exit_code"] != 0:
                raise DomainError("UNVERIFIED_COMPLETION", "the operator's verification command failed; inspect and debug")
            verification_id = verification["attempt_id"]
        payload = {**context["completion_contract"]["template"], "summary": summary,
                   "evidence": evidence or summary, "verification_attempt_id": verification_id}
        # The tool writes the same durable manifest that native file writers use.
        # A concurrent Controller consumption is idempotent by receipt hash.
        with self.store.connection() as conn:
            fence(conn, credentials)
        write_marker(self.service, credentials.task_id, credentials.worker_id, payload)
        return accept(self.service, credentials.task_id, credentials.worker_id, payload)

    @staticmethod
    def credentials_json(credentials: Credentials) -> str:
        return encode(asdict(credentials))


SuiProxyProtocol = SuiProtocol
