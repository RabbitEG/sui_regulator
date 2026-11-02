from __future__ import annotations

import json
import signal
import sqlite3
import subprocess
import time
from contextlib import AbstractContextManager

from .adapters import MockWorkerAdapter, UnavailableWorkerAdapter, WorkerAdapter
from .config import Config
from .models import DomainError, encode
from .processes import FileLock, alive, identity, process_state, signal_owned
from .protocol import SuiProtocol
from .registry import WorkerRegistry
from .runner import LocalMockRunner
from .service import Service


class Controller(AbstractContextManager):
    def __init__(self, config: Config, adapters: dict[str, WorkerAdapter] | None = None):
        self.config, self.registry = config, WorkerRegistry(config.home)
        self.watchdog_actions: list[dict] = []
        self.adapters = adapters or {"mock": MockWorkerAdapter(), "unavailable": UnavailableWorkerAdapter()}
        if adapters is None:
            from .claude_adapter import ClaudeCodeAdapter

            self.adapters["claude"] = ClaudeCodeAdapter()
        self.children: dict[int, subprocess.Popen] = {}
        self.lock = FileLock(config.home / "controller.lock")
        self.acquired = False

    def __enter__(self):
        self.lock.__enter__()
        try:
            self._sweep_frozen()
            self.service = Service(self.config)
            self.protocol = SuiProtocol(self.service)
            self.runner = LocalMockRunner(self.service)
            with self.service.store.transaction() as conn:
                self.service.store.event(conn, None, "CONTROLLER_STARTED", "controller", "controller", {})
            self._audit_watchdog()
            self.acquired = True
        except BaseException:
            self.lock.__exit__(None, None, None)
            raise
        return self

    def _sweep_frozen(self):
        for record in self.registry.records():
            overdue = (
                time.time() - record["heartbeat_at"] >= self.config.lease_seconds
                or time.time() - record["created_at"] >= self.config.max_session_age
            )
            if overdue and process_state(record["identity"]) in {"T", "t"}:
                if signal_owned(record["identity"], signal.SIGKILL):
                    self.watchdog_actions.append(record)

    def _audit_watchdog(self):
        if self.watchdog_actions:
            with self.service.store.transaction() as conn:
                for record in self.watchdog_actions:
                    self.service.store.event(
                        conn,
                        record["task_id"],
                        "FROZEN_WORKER_TERMINATED",
                        "candleholder",
                        "controller",
                        {
                            "worker_id": record["worker_id"],
                            "identity": record["identity"],
                            "reason": "stopped worker exceeded lease or session age",
                        },
                    )
            self.watchdog_actions.clear()

    def __exit__(self, *args):
        self.acquired = False
        self.lock.__exit__(*args)
        # Detached workers and jobs intentionally survive a controller exit.

    def _stop(self, worker: dict) -> None:
        sig = (
            signal.SIGKILL
            if worker.get("ended_at") and time.time() - worker["ended_at"] >= self.config.rotation_grace
            else signal.SIGTERM
        )
        for column in ["child_identity", "identity"]:
            value = worker.get(column)
            if isinstance(value, str):
                value = json.loads(value)
            signal_owned(value, sig)

    def _retire(self, task: dict, worker: dict, reason: str, failure: bool) -> None:
        now = time.time()
        with self.service.store.transaction() as conn:
            current = self.service._get(conn, "tasks", task["id"])
            if current["current_worker"] != worker["id"]:
                return
            conn.execute(
                "UPDATE workers SET status=?,error=?,ended_at=?,lease_expires=0 WHERE id=?",
                ("FAILED" if failure else "RETIRED", reason, now, worker["id"]),
            )
            conn.execute(
                "UPDATE tasks SET current_worker=NULL,status='READY',next_start_at=?,updated_at=? WHERE id=?",
                (now + self.config.restart_cooldown, now, task["id"]),
            )
            self.service.store.event(
                conn,
                task["id"],
                "WORKER_FAILED" if failure else "WORKER_ROTATED",
                "candleholder",
                worker["id"],
                {"reason": reason},
            )
            if reason in {"AUTH_REQUIRED", "RESOURCE_BLOCKED", "SESSION_ID_MISMATCH"}:
                conn.execute("UPDATE tasks SET status='QUARANTINED' WHERE id=?", (task["id"],))
                self.service.store.event(
                    conn, task["id"], "TASK_QUARANTINED", "candleholder", "controller", {"reason": reason}
                )
        self._stop(worker)

    def _health_reason(self, worker: dict) -> str | None:
        now = time.time()
        if worker["status"] == "STARTING" and now - worker["created_at"] < self.config.startup_grace:
            return None
        value = json.loads(worker["identity"]) if worker["identity"] else None
        if worker["status"] not in {"STARTING", "RUNNING", "ROTATING"} or not alive(value):
            return worker["error"] or "PROCESS_FAILURE"
        if worker["lease_expires"] <= now:
            return "LEASE_EXPIRED"
        if worker["anomaly_count"] >= 3:
            return "MALFORMED_OR_REPEATED_TOOL_CALLS"
        if now - worker["activity_at"] > self.config.stall_seconds:
            task = self.service.get_task(worker["task_id"])
            phase = task["declared"].get("phase", "")
            waiting = phase in {"WAITING_USER", "WAITING_RESOURCE", "WAITING_EXTERNAL"}
            running_job = phase == "WAITING_JOB" and any(
                job["status"] == "RUNNING" and not job["stale"] for job in task["jobs"]
            )
            if not waiting and not running_job:
                return "STALLED"
        return None

    def tick(self) -> None:
        if not self.acquired:
            raise RuntimeError("controller must hold its singleton lock")
        self._sweep_frozen()
        self._audit_watchdog()
        self.service.process_commands()
        self.runner.reconcile()
        for pid, process in list(self.children.items()):
            if process.poll() is not None:
                del self.children[pid]
        for task in self.service.store.rows("SELECT * FROM tasks ORDER BY created_at,id"):
            if task["desired"] != "ACTIVE" or task["status"] == "QUARANTINED":
                continue
            worker = None
            if task["current_worker"]:
                rows = self.service.store.rows("SELECT * FROM workers WHERE id=?", (task["current_worker"],))
                worker = rows[0] if rows else None
            if worker:
                if worker["rotation_at"] is not None and (
                    worker["handoff"] or time.time() - worker["rotation_at"] >= self.config.rotation_grace
                ):
                    self._retire(task, worker, "SESSION_ROTATION", False)
                else:
                    reason = self._health_reason(worker)
                    if reason:
                        self._retire(task, worker, reason, True)
                    elif (
                        time.time() - worker["created_at"] >= self.config.max_session_age
                        and worker["rotation_at"] is None
                    ):
                        with self.service.store.transaction() as conn:
                            conn.execute(
                                "UPDATE workers SET status='ROTATING',rotation_at=? WHERE id=?",
                                (time.time(), worker["id"]),
                            )
                            self.service.store.event(
                                conn, task["id"], "HANDOFF_REQUESTED", "candleholder", worker["id"], {}
                            )
                continue
            if time.time() < task["next_start_at"]:
                continue
            failures = self.service.store.rows(
                "SELECT COUNT(*) AS n FROM events WHERE task_id=? AND kind='WORKER_FAILED' AND created_at>=?",
                (task["id"], max(time.time() - self.config.restart_window, task["recovery_reset_at"])),
            )[0]["n"]
            if failures >= self.config.max_restarts:
                with self.service.store.transaction() as conn:
                    conn.execute(
                        "UPDATE tasks SET status='QUARANTINED',updated_at=? WHERE id=?",
                        (time.time(), task["id"]),
                    )
                    self.service.store.event(
                        conn,
                        task["id"],
                        "TASK_QUARANTINED",
                        "candleholder",
                        "controller",
                        {"failures": failures},
                    )
                continue
            active = self.service.store.rows(
                "SELECT COUNT(*) AS n FROM workers WHERE status IN ('STARTING','RUNNING','ROTATING')"
            )[0]["n"]
            if active >= self.config.max_workers:
                continue
            credentials = self.protocol.register(task["id"])
            try:
                adapter = self.adapters.get(task["adapter"], UnavailableWorkerAdapter())
                process = adapter.start(self.service, credentials)
                self.children[process.pid] = process
                self.registry.publish(
                    credentials.worker_id, credentials.task_id, credentials.generation, identity(process.pid)
                )
                with self.service.store.transaction() as conn:
                    conn.execute(
                        "UPDATE workers SET pid=?,identity=? WHERE id=? AND identity IS NULL",
                        (process.pid, encode(identity(process.pid)), credentials.worker_id),
                    )
            except (OSError, DomainError) as exc:
                worker = self.service.store.rows(
                    "SELECT * FROM workers WHERE id=?", (credentials.worker_id,)
                )[0]
                self._retire(task, worker, getattr(exc, "code", type(exc).__name__), True)
        # Only rows positively registered by this controller are eligible for cleanup.
        for worker in self.service.store.rows(
            "SELECT * FROM workers WHERE status IN ('REVOKED','RETIRED','FAILED')"
        ):
            self._stop(worker)

    def run(self, stop=None) -> None:
        while stop is None or not stop.is_set():
            try:
                self.tick()
            except sqlite3.OperationalError as exc:
                if "lock" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
            if stop is None:
                time.sleep(self.config.tick_seconds)
            else:
                stop.wait(self.config.tick_seconds)
