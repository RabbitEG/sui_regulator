from __future__ import annotations

import json
import subprocess
import time
from typing import Protocol

from .config import Config
from .models import encode
from .processes import alive, detached
from .service import Service


class ExperimentRunner(Protocol):
    def dispatch(self, job_id: str) -> None: ...
    def reconcile(self) -> None: ...


class LocalMockRunner:
    """A real detached process executing only bounded synthetic work."""
    def __init__(self, service: Service):
        self.service, self.config, self.store = service, service.config, service.store
        self.children: dict[int, subprocess.Popen] = {}

    def dispatch(self, job_id: str) -> None:
        with self.store.transaction() as conn:
            job = self.service._get(conn, "jobs", job_id)
            if job["status"] != "QUEUED":
                return
            conn.execute("UPDATE jobs SET status='STARTING',updated_at=? WHERE id=?", (time.time(), job_id))
            self.store.event(conn, job["task_id"], "JOB_DISPATCH_INTENT", "runner", "controller", {"job_id": job_id})
        try:
            process = detached("sui_regulator.job_process", ["--home", str(self.config.home), "--job-id", job_id])
            self.children[process.pid] = process
        except OSError as exc:
            with self.store.transaction() as conn:
                conn.execute("UPDATE jobs SET status='FAILED',error=?,updated_at=? WHERE id=? AND status='STARTING'",
                             (f"spawn failed before execution: {type(exc).__name__}", time.time(), job_id))

    def reconcile(self) -> None:
        for pid, process in list(self.children.items()):
            if process.poll() is not None:
                del self.children[pid]
        for job in self.store.rows("SELECT * FROM jobs WHERE status IN ('QUEUED','STARTING','RUNNING','UNKNOWN')"):
            if job["status"] == "QUEUED":
                self.dispatch(job["id"])
                continue
            process_identity = json.loads(job["identity"]) if job["identity"] else None
            if alive(process_identity) or time.time() - job["updated_at"] < self.config.startup_grace:
                continue
            if job["status"] != "UNKNOWN":
                with self.store.transaction() as conn:
                    current = self.service._get(conn, "jobs", job["id"])
                    if current["status"] not in {"STARTING", "RUNNING"}:
                        continue
                    conn.execute("UPDATE jobs SET status='UNKNOWN',error=?,updated_at=? WHERE id=?",
                                 ("runner missing; execution outcome requires reconciliation, never auto-retry", time.time(), job["id"]))
                    self.store.event(conn, job["task_id"], "JOB_OUTCOME_UNKNOWN", "runner", "controller", {"job_id": job["id"]})
