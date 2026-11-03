from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .config import Config
from .models import DomainError, digest, encode, identifier, mock_spec, require_text
from .store import Store

COMMAND_KINDS = {
    "submit_task",
    "instruct_task",
    "request_analysis",
    "hold_task",
    "resume_task",
    "cancel_task",
    "cancel_job",
    "set_recovery_policy",
    "resolve_attempt",
}
JSON_COLUMNS = {
    "runtime",
    "completion",
    "recovery_state",
    "outcome",
    "payload",
    "result",
    "spec",
    "declared",
    "capabilities",
    "handoff",
    "metric",
    "data",
    "identity",
    "child_identity",
    "cancel_auth",
}


def unpack(row: sqlite3.Row | dict) -> dict:
    value = dict(row)
    for key in JSON_COLUMNS & value.keys():
        if value[key] is not None:
            value[key] = json.loads(value[key])
    value.pop("lease_hash", None)
    return value


class Service:
    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config.home)

    @staticmethod
    def _get(conn: sqlite3.Connection, table: str, entity_id: str) -> sqlite3.Row:
        if table not in {"tasks", "commands", "workers", "jobs", "experiments"}:
            raise ValueError("invalid entity table")
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", f"{table}: entity not found")
        return row

    def command(self, kind: str, actor: str, key: str, payload: dict, task_id: str | None = None) -> dict:
        if kind not in COMMAND_KINDS:
            raise DomainError("UNKNOWN_METHOD", "unsupported Yan command")
        require_text(actor, "actor", 256)
        require_text(key, "idempotency_key", 256)
        if not isinstance(payload, dict) or len(encode(payload)) > 65536:
            raise DomainError("INVALID_ARGUMENT", "payload must be a bounded object")
        request_hash = digest({"kind": kind, "task_id": task_id, "payload": payload})
        with self.store.transaction() as conn:
            old = conn.execute(
                "SELECT * FROM commands WHERE actor=? AND idempotency_key=?", (actor, key)
            ).fetchone()
            if old is not None:
                if old["request_hash"] != request_hash:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "key already used for a different request")
                return unpack(old)
            task_id = identifier("task") if kind == "submit_task" else require_text(task_id, "task_id", 128)
            command_id, now = identifier("command"), time.time()
            conn.execute(
                """INSERT INTO commands(id,actor,idempotency_key,kind,task_id,payload,
                request_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'QUEUED',?,?)""",
                (command_id, actor, key, kind, task_id, encode(payload), request_hash, now, now),
            )
            self.store.event(
                conn, task_id, "COMMAND_ACCEPTED", "yan", actor, {"command_id": command_id, "kind": kind}
            )
            return unpack(self._get(conn, "commands", command_id))

    def process_commands(self, limit: int = 100) -> int:
        count = 0
        for _ in range(limit):
            with self.store.transaction() as conn:
                command = conn.execute(
                    "SELECT * FROM commands WHERE status='QUEUED' ORDER BY created_at,id LIMIT 1"
                ).fetchone()
                if command is None:
                    break
                cid, tid, now = command["id"], command["task_id"], time.time()
                conn.execute("SAVEPOINT apply_command")
                try:
                    result = self._apply_command(conn, command, now)
                    status, error = "SUCCEEDED", None
                    conn.execute("RELEASE apply_command")
                except (DomainError, ValueError, TypeError) as exc:
                    conn.execute("ROLLBACK TO apply_command")
                    conn.execute("RELEASE apply_command")
                    result, status, error = {}, "REJECTED", str(exc)
                conn.execute(
                    "UPDATE commands SET status=?,result=?,error=?,updated_at=? WHERE id=?",
                    (status, encode(result), error, now, cid),
                )
                self.store.event(
                    conn,
                    tid,
                    f"COMMAND_{status}",
                    "controller",
                    command["actor"],
                    {"command_id": cid, "result": result, "error": error},
                )
                count += 1
        return count

    def _apply_command(self, conn: sqlite3.Connection, command: sqlite3.Row, now: float) -> dict:
        kind, tid, payload = command["kind"], command["task_id"], json.loads(command["payload"])
        if kind == "submit_task":
            if set(payload) - {"title", "objective", "adapter", "spec", "workspace", "execution_profile", "completion_criteria", "verification_template", "recovery_mode", "max_proxy_seconds"}:
                raise DomainError("INVALID_ARGUMENT", "unexpected submit_task fields")
            title = require_text(payload.get("title"), "title", 512)
            objective = require_text(payload.get("objective", title), "objective")
            adapter = payload.get("adapter", "mock")
            if adapter not in {"mock", "claude", "unavailable"}:
                raise DomainError("INVALID_ARGUMENT", "unsupported adapter")
            spec = mock_spec(payload.get("spec", {}))
            workspace = self.config.home / "workspaces" / tid
            profile = payload.get("execution_profile", "managed")
            if not isinstance(profile, str) or profile not in {"managed", "claude_code"} or (profile == "claude_code" and adapter != "claude"):
                raise DomainError("INVALID_ARGUMENT", "claude_code execution requires the claude adapter")
            if "workspace" in payload:
                workspace = Path(require_text(payload["workspace"], "workspace", 4096)).expanduser()
                if not workspace.is_absolute() or not workspace.is_dir():
                    raise DomainError("INVALID_ARGUMENT", "workspace must be an existing absolute directory")
                workspace = workspace.resolve()
            criteria = payload.get("completion_criteria", "")
            if not isinstance(criteria, str) or len(criteria) > 16384:
                raise DomainError("INVALID_ARGUMENT", "completion_criteria must be text of at most 16384 characters")
            verifier = payload.get("verification_template", "")
            from .recovery import validate_policy

            mode, budget = validate_policy(payload.get("recovery_mode", self.config.recovery_mode), payload.get("max_proxy_seconds", 0))
            if not isinstance(verifier, str) or len(verifier) > 64:
                raise DomainError("INVALID_ARGUMENT", "verification_template must be a bounded template name")
            if verifier:
                from .commands import load_registry

                template = load_registry(self.config.command_registry).get(verifier)
                if template is None or template["patterns"]:
                    raise DomainError("INVALID_ARGUMENT", "verification requires an approved template with no arguments")
            conn.execute(
                """INSERT INTO tasks(id,title,objective,workspace,adapter,spec,created_at,updated_at,
                execution_profile,completion_criteria,verification_template,recovery_mode,max_proxy_seconds)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, title, objective, str(workspace), adapter, encode(spec), now, now, profile, criteria, verifier, mode, budget),
            )
            return {"task_id": tid, "effect": "task created"}
        task = self._get(conn, "tasks", tid)
        if kind == "cancel_job":
            if set(payload) != {"job_id"}:
                raise DomainError("INVALID_ARGUMENT", "cancel_job requires exactly job_id")
            job = self._get(conn, "jobs", require_text(payload["job_id"], "job_id", 128))
            if job["task_id"] != tid:
                raise DomainError("FORBIDDEN", "job belongs to another task")
            if job["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                conn.execute(
                    "UPDATE jobs SET cancel_requested=1,cancel_auth=? WHERE id=?",
                    (encode({"source": "yan", "command_id": command["id"]}), job["id"]),
                )
            return {
                "task_id": tid,
                "job_id": job["id"],
                "effect": "synthetic job cancellation requested",
                "observed_status": job["status"],
            }
        if task["desired"] in {"CANCELLED", "COMPLETED"}:
            if kind == "cancel_task" and task["desired"] == "CANCELLED":
                return {"task_id": tid, "effect": "already cancelled; jobs retained"}
            raise DomainError("INVALID_STATE", "terminal task cannot be modified or resumed")
        if kind == "set_recovery_policy":
            from .recovery import validate_policy

            if not payload or set(payload) - {"recovery_mode", "max_proxy_seconds"}:
                raise DomainError("INVALID_ARGUMENT", "expected recovery_mode and/or max_proxy_seconds")
            mode, budget = validate_policy(payload.get("recovery_mode", task["recovery_mode"]),
                                           payload.get("max_proxy_seconds", task["max_proxy_seconds"]))
            conn.execute("UPDATE tasks SET recovery_mode=?,max_proxy_seconds=?,updated_at=? WHERE id=?", (mode, budget, now, tid))
            return {"effect": "policy updated; does not resume a paused task", "recovery_mode": mode, "max_proxy_seconds": budget}
        if kind == "resolve_attempt":
            if set(payload) != {"attempt_id", "evidence"}:
                raise DomainError("INVALID_ARGUMENT", "attempt_id and verification evidence are required")
            attempt_id = require_text(payload["attempt_id"], "attempt_id", 128)
            evidence = require_text(payload["evidence"], "evidence")
            if task["desired"] != "HOLD":
                raise DomainError("INVALID_STATE", "Hold the Task and verify external effects before resolving an attempt")
            attempt = conn.execute("SELECT * FROM tool_attempts WHERE task_id=? AND id=?", (tid, attempt_id)).fetchone()
            if attempt is None or attempt["status"] not in {"STARTED", "UNKNOWN", "READING"}:
                raise DomainError("INVALID_STATE", "attempt is absent or already has a known outcome")
            conn.execute("UPDATE tool_attempts SET status='RESOLVED',finished_at=? WHERE id=?", (now, attempt_id))
            self.store.event(conn, tid, "ATTEMPT_RESOLVED", "yan", command["actor"], {"attempt_id": attempt_id, "evidence": evidence, "verification_source": "operator-declared"})
            return {"effect": "operator verification recorded; task remains on Hold", "attempt_id": attempt_id}
        if kind in {"instruct_task", "request_analysis"}:
            if set(payload) != {"instruction"}:
                raise DomainError("INVALID_ARGUMENT", "instruction is required")
            instruction = require_text(payload["instruction"], "instruction")
            self.store.event(
                conn,
                tid,
                "INSTRUCTION_RECORDED",
                "yan",
                command["actor"],
                {"command_id": command["id"], "instruction": instruction, "kind": kind},
            )
            return {"task_id": tid, "effect": "instruction recorded", "analysis_executed": False}
        if payload:
            raise DomainError("INVALID_ARGUMENT", "control command payload must be empty")
        desired = {"hold_task": "HOLD", "resume_task": "ACTIVE", "cancel_task": "CANCELLED"}[kind]
        status = "READY" if desired == "ACTIVE" else desired
        if (
            kind == "resume_task"
            and task["desired"] == "ACTIVE"
            and task["status"] not in {"QUARANTINED", "WAITING", "WAITING_RESOURCE"}
        ):
            return {"task_id": tid, "effect": "already active"}
        conn.execute(
            "UPDATE workers SET status='REVOKED',ended_at=?,lease_expires=0 WHERE id=?",
            (now, task["current_worker"]),
        )
        conn.execute(
            "UPDATE tasks SET desired=?,status=?,current_worker=NULL,updated_at=?,recovery_reset_at=? WHERE id=?",
            (desired, status, now, now if desired == "ACTIVE" else task["recovery_reset_at"], tid),
        )
        return {"task_id": tid, "effect": desired, "running_jobs": "retained"}

    def get_command(self, command_id: str) -> dict:
        with self.store.connection() as conn:
            return unpack(self._get(conn, "commands", command_id))

    def list_tasks(self) -> list[dict]:
        return [unpack(row) for row in self.store.rows("SELECT * FROM tasks ORDER BY created_at,id")]

    def get_task(self, task_id: str) -> dict:
        with self.store.connection() as conn:
            task = unpack(self._get(conn, "tasks", task_id))
            task["execution_count"] = conn.execute(
                "SELECT COUNT(*) FROM executions e JOIN jobs j ON j.id=e.job_id WHERE j.task_id=?",
                (task_id,),
            ).fetchone()[0]
            task["current_proxy"] = task["current_worker"]
            task["incidents"] = [
                unpack(row)
                for row in conn.execute(
                    "SELECT rowid AS sequence,* FROM incidents WHERE task_id=? ORDER BY rowid DESC LIMIT 20",
                    (task_id,),
                )
            ]
            task["jobs"] = [
                self._job_view(row)
                for row in conn.execute(
                    "SELECT * FROM jobs WHERE task_id=? ORDER BY created_at,id", (task_id,)
                )
            ]
            task["workers"] = [
                unpack(row)
                for row in conn.execute(
                    "SELECT * FROM workers WHERE task_id=? ORDER BY generation", (task_id,)
                )
            ]
            for worker in task["workers"]:
                from .console_read import session_view

                worker.update(session_view(worker, self.config, task["current_worker"]))
                worker["proxy_id"] = worker["id"]
                worker["stream_observation"] = (
                    "text-delta-observed"
                    if "claude-text-delta-observed" in worker["capabilities"]
                    else "event-only"
                    if worker["adapter"] == "claude"
                    else "not-applicable"
                )
            task["artifacts"] = [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM artifacts WHERE task_id=? ORDER BY created_at", (task_id,)
                )
            ]
            task["tool_attempts"] = [unpack(row) for row in conn.execute(
                "SELECT * FROM tool_attempts WHERE task_id=? ORDER BY created_at DESC,rowid DESC LIMIT 100", (task_id,))]
            task["completion_receipts"] = [dict(row) for row in conn.execute(
                "SELECT * FROM completion_receipts WHERE task_id=? ORDER BY created_at DESC LIMIT 10", (task_id,))]
            completion_owner = task["current_worker"] or (task["completion"] or {}).get("proxy_id")
            task["completion_marker_path"] = str(self.config.home / "completions" / task_id / (completion_owner + ".json")) if completion_owner else None
            task["state_sources"] = {
                "desired": "yan/controller",
                "declared": "worker; unverified",
                "jobs": "runner observations",
            }
            task["queried_at"] = time.time()
            return task

    def _job_view(self, row: sqlite3.Row | dict) -> dict:
        from .processes import alive

        value = unpack(row)
        value["process_alive"] = alive(value["identity"]) if value.get("identity") else None
        terminal = value["status"] in {"COMPLETED", "FAILED", "CANCELLED"}
        value["source"] = "runner"
        value["stale"] = not terminal and (
            value["observed_at"] is None or time.time() - value["observed_at"] > self.config.stale_seconds
        )
        return value

    def get_job(self, job_id: str) -> dict:
        with self.store.connection() as conn:
            return self._job_view(self._get(conn, "jobs", job_id))

    def events(self, task_id: str | None = None, after: int = 0, limit: int = 100) -> list[dict]:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise DomainError("INVALID_ARGUMENT", "invalid event pagination")
        if task_id:
            rows = self.store.rows(
                "SELECT * FROM events WHERE task_id=? AND id>? ORDER BY id LIMIT ?", (task_id, after, limit)
            )
        else:
            rows = self.store.rows("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after, limit))
        return [unpack(row) for row in rows]
