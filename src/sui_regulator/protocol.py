from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from . import PROTOCOL_VERSION
from .models import Credentials, DomainError, digest, encode, identifier, mock_spec, require_text
from .processes import identity
from .service import Service, unpack


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def fence(conn: sqlite3.Connection, credentials: Credentials, now: float | None = None) -> sqlite3.Row:
    now = time.time() if now is None else now
    task = Service._get(conn, "tasks", credentials.task_id)
    worker = Service._get(conn, "workers", credentials.worker_id)
    if (task["desired"] != "ACTIVE" or task["current_worker"] != worker["id"]
        or task["generation"] != credentials.generation or worker["task_id"] != task["id"]
        or worker["generation"] != credentials.generation or worker["lease_expires"] <= now
        or worker["status"] not in {"STARTING", "RUNNING", "ROTATING"}
        or not secrets.compare_digest(worker["lease_hash"], token_hash(credentials.lease_token))):
        raise DomainError("FENCED", "worker ownership or lease is no longer valid")
    return worker


def execution_authorized(conn: sqlite3.Connection, job: sqlite3.Row) -> bool:
    task = Service._get(conn, "tasks", job["task_id"])
    worker = Service._get(conn, "workers", job["authorized_worker"])
    return (task["desired"] == "ACTIVE" and task["current_worker"] == worker["id"]
            and task["generation"] == job["authorized_generation"] == worker["generation"]
            and worker["lease_expires"] > time.time()
            and worker["status"] in {"STARTING", "RUNNING", "ROTATING"})


class SuiProtocol:
    def __init__(self, service: Service):
        self.service, self.store, self.config = service, service.store, service.config

    def register(self, task_id: str) -> Credentials:
        """Controller-only bootstrap: register and atomically claim a free task."""
        now, token = time.time(), secrets.token_urlsafe(32)
        with self.store.transaction() as conn:
            task = self.service._get(conn, "tasks", task_id)
            if task["desired"] != "ACTIVE" or task["status"] == "QUARANTINED" or task["current_worker"]:
                raise DomainError("NOT_CLAIMABLE", "task is not available for a new worker")
            worker_id, generation = identifier("sui"), task["generation"] + 1
            conn.execute("""INSERT INTO workers(id,session_id,task_id,generation,adapter,status,lease_hash,
                lease_expires,heartbeat_at,activity_at,capabilities,created_at)
                VALUES(?,?,?,?,?,'STARTING',?,?,?,?,?,?)""",
                         (worker_id, str(uuid.uuid4()), task_id, generation, task["adapter"], token_hash(token),
                          now + self.config.lease_seconds, now, now,
                          encode(["sui-protocol-0.1", "managed-mock-jobs"]), now))
            conn.execute("UPDATE tasks SET generation=?,current_worker=?,status='RUNNING',updated_at=? WHERE id=?",
                         (generation, worker_id, now, task_id))
            self.store.event(conn, task_id, "WORKER_CLAIMED", "controller", worker_id, {"generation": generation})
        return Credentials(task_id, worker_id, generation, token)

    @contextmanager
    def _write(self, credentials: Credentials):
        try:
            with self.store.transaction() as conn:
                worker = fence(conn, credentials)
                yield conn, worker
        except DomainError as exc:
            with self.store.transaction() as conn:
                self.store.event(conn, credentials.task_id, "PROTOCOL_REJECTED", "protocol", credentials.worker_id,
                                 {"code": exc.code, "generation": credentials.generation})
            raise

    def claim_task(self, credentials: Credentials) -> dict:
        with self._write(credentials) as (conn, _):
            conn.execute("UPDATE workers SET status='RUNNING',pid=?,identity=? WHERE id=?",
                         (os.getpid(), encode(identity(os.getpid())), credentials.worker_id))
        return self.get_task_context(credentials)

    def heartbeat(self, credentials: Credentials, *, effective_activity: bool = False) -> None:
        now = time.time()
        with self._write(credentials) as (conn, _):
            conn.execute("UPDATE workers SET heartbeat_at=?,lease_expires=? WHERE id=?",
                         (now, now + self.config.lease_seconds, credentials.worker_id))
            if effective_activity:
                conn.execute("UPDATE workers SET activity_at=? WHERE id=?", (now, credentials.worker_id))

    def get_task_context(self, credentials: Credentials) -> dict:
        with self.store.connection() as conn:
            worker = fence(conn, credentials)
            rotation = worker["rotation_at"] is not None
        task = self.service.get_task(credentials.task_id)
        task["events"] = self.service.events(credentials.task_id)
        task["instructions"] = [unpack(row) for row in self.store.rows(
            "SELECT * FROM commands WHERE task_id=? AND kind IN ('instruct_task','request_analysis') AND status='SUCCEEDED' ORDER BY created_at,id",
            (credentials.task_id,))]
        task["rotation_requested"] = rotation
        task["protocol_version"] = PROTOCOL_VERSION
        workspace = Path(task["workspace"])
        task["workspace_exists"] = workspace.is_dir()
        # Git is optional evidence, never an instruction source or mandatory recovery input.
        task["git_status"] = "unavailable: no repository in this managed workspace"
        return task

    def report_progress(self, credentials: Credentials, *, phase: str, summary: str,
                        job_ids: list[str] | None = None, next_action: str = "", blockers: list | None = None) -> dict:
        require_text(phase, "phase", 128)
        require_text(summary, "summary")
        if not isinstance(job_ids or [], list) or not isinstance(blockers or [], list) or not isinstance(next_action, str):
            raise DomainError("INVALID_ARGUMENT", "invalid progress fields")
        report = {"protocol_version": PROTOCOL_VERSION, "task_id": credentials.task_id,
                  "worker_id": credentials.worker_id, "generation": credentials.generation,
                  "phase": phase, "summary": summary, "job_ids": job_ids or [],
                  "next_action": next_action, "blockers": blockers or [], "updated_at": time.time()}
        if len(encode(report)) > 65536:
            raise DomainError("INVALID_ARGUMENT", "report is too large")
        with self._write(credentials) as (conn, _):
            for job_id in report["job_ids"]:
                job = self.service._get(conn, "jobs", job_id)
                if job["task_id"] != credentials.task_id:
                    raise DomainError("FORBIDDEN", "job belongs to another task")
            conn.execute("UPDATE tasks SET declared=?,declared_at=?,updated_at=? WHERE id=?",
                         (encode(report), report["updated_at"], report["updated_at"], credentials.task_id))
            conn.execute("UPDATE workers SET activity_at=? WHERE id=?", (time.time(), credentials.worker_id))
            self.store.event(conn, credentials.task_id, "PROGRESS_REPORT", "worker-declared", credentials.worker_id, report)
        return report

    def report_blocker(self, credentials: Credentials, reason: str, waiting_for: str = "user") -> dict:
        if waiting_for not in {"user", "resource", "external"}:
            raise DomainError("INVALID_ARGUMENT", "waiting_for must be user/resource/external")
        return self.report_progress(credentials, phase=f"WAITING_{waiting_for.upper()}", summary=reason,
                                    blockers=[{"reason": reason, "waiting_for": waiting_for}])

    def launch_job(self, credentials: Credentials, logical_key: str, spec: dict | None = None) -> dict:
        require_text(logical_key, "logical_key", 256)
        spec = mock_spec({} if spec is None else spec)
        with self._write(credentials) as (conn, _):
            old = conn.execute("SELECT * FROM jobs WHERE task_id=? AND logical_key=?", (credentials.task_id, logical_key)).fetchone()
            if old:
                if old["spec_hash"] != digest(spec):
                    raise DomainError("IDEMPOTENCY_CONFLICT", "logical job key already has a different specification")
                return self.service._job_view(old)
            job_id, exp_id, now = identifier("job"), identifier("experiment"), time.time()
            conn.execute("INSERT INTO experiments(id,task_id,name,spec,created_at) VALUES(?,?,?,?,?)",
                         (exp_id, credentials.task_id, logical_key, encode(spec), now))
            status = "QUEUED" if spec["resource_available"] else "BLOCKED"
            conn.execute("""INSERT INTO jobs(id,task_id,experiment_id,logical_key,spec,spec_hash,status,
                authorized_worker,authorized_generation,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                         (job_id, credentials.task_id, exp_id, logical_key, encode(spec), digest(spec), status,
                          credentials.worker_id, credentials.generation, now, now))
            self.store.event(conn, credentials.task_id, "JOB_REQUESTED", "protocol", credentials.worker_id,
                             {"job_id": job_id, "logical_key": logical_key, "status": status})
            return self.service._job_view(self.service._get(conn, "jobs", job_id))

    def attach_job(self, credentials: Credentials, job_id: str) -> dict:
        with self._write(credentials) as (conn, _):
            job = self.service._get(conn, "jobs", job_id)
            if job["task_id"] != credentials.task_id:
                raise DomainError("FORBIDDEN", "job belongs to another task")
            self.store.event(conn, credentials.task_id, "JOB_ATTACHED", "protocol", credentials.worker_id, {"job_id": job_id})
            return self.service._job_view(job)

    def handoff(self, credentials: Credentials, summary: dict) -> dict:
        if not isinstance(summary, dict) or not summary or len(encode(summary)) > 65536:
            raise DomainError("INVALID_ARGUMENT", "handoff must be a bounded nonempty object")
        document = {"protocol_version": PROTOCOL_VERSION, "generation": credentials.generation, **summary}
        with self._write(credentials) as (conn, _):
            conn.execute("UPDATE workers SET handoff=? WHERE id=?", (encode(document), credentials.worker_id))
            self.store.event(conn, credentials.task_id, "HANDOFF", "worker-declared", credentials.worker_id, document)
        return document

    def publish_artifact(self, credentials: Credentials, path: str, kind: str = "report") -> dict:
        require_text(path, "path", 4096)
        require_text(kind, "kind", 128)
        with self._write(credentials) as (conn, _):
            task = self.service._get(conn, "tasks", credentials.task_id)
            root = Path(task["workspace"]).resolve()
            artifact = (root / path).resolve()
            if not artifact.is_relative_to(root) or not artifact.is_file():
                raise DomainError("INVALID_ARGUMENT", "artifact must be an existing file within managed workspace")
            aid = identifier("artifact")
            conn.execute("INSERT OR IGNORE INTO artifacts(id,task_id,path,kind,source,created_at) VALUES(?,?,?,?,?,?)",
                         (aid, credentials.task_id, str(artifact), kind, "worker-declared", time.time()))
            self.store.event(conn, credentials.task_id, "ARTIFACT_DECLARED", "worker-declared", credentials.worker_id, {"path": str(artifact)})
            return dict(conn.execute("SELECT * FROM artifacts WHERE task_id=? AND path=?", (credentials.task_id, str(artifact))).fetchone())

    def complete_task(self, credentials: Credentials, summary: str) -> dict:
        require_text(summary, "summary")
        with self._write(credentials) as (conn, _):
            jobs = conn.execute("SELECT status FROM jobs WHERE task_id=?", (credentials.task_id,)).fetchall()
            if any(job["status"] != "COMPLETED" for job in jobs):
                raise DomainError("UNVERIFIED_COMPLETION", "all managed jobs must be observed COMPLETED")
            now = time.time()
            conn.execute("UPDATE tasks SET desired='COMPLETED',status='COMPLETED',current_worker=NULL,updated_at=? WHERE id=?",
                         (now, credentials.task_id))
            conn.execute("UPDATE workers SET status='COMPLETED',lease_expires=0,ended_at=? WHERE id=?", (now, credentials.worker_id))
            self.store.event(conn, credentials.task_id, "TASK_COMPLETED", "protocol", credentials.worker_id,
                             {"summary": summary, "semantic_result": "worker-declared", "managed_jobs_verified": True})
        return {"task_id": credentials.task_id, "status": "COMPLETED"}

    @staticmethod
    def credentials_json(credentials: Credentials) -> str:
        return encode(asdict(credentials))
