"""Task-level trust, granted by Yan once and inherited across Sui Proxy generations."""

from __future__ import annotations

import json
import time

from .models import DomainError, digest, encode, require_text


def trusted(task):
    # Older-schema migration tests and recovery of pre-upgrade records remain guarded.
    return "permission_mode" in task.keys() and task["permission_mode"] == "trusted"


def validate_mode(value):
    if not isinstance(value, str) or value not in {"guarded", "trusted"}:
        raise DomainError("INVALID_ARGUMENT", "permission_mode must be guarded or trusted")
    return value


def observation_digest(row):
    snapshot = {key: row[key] for key in ("status", "runtime", "result_hash", "identity", "updated_at")
                if key in row.keys()}
    for key in ("runtime", "identity"):
        if isinstance(snapshot.get(key), str):
            snapshot[key] = json.loads(snapshot[key])
    return digest(snapshot)


def reviewed(row):
    value = row["reconciliation"]
    if isinstance(value, str):
        value = json.loads(value)
    return bool(value and value.get("observation_digest") == observation_digest(row))


def reconcile_operation(service, credentials, kind, operation_id, evidence):
    """Record the trusted Proxy's judgment separately from runtime observations."""
    from .protocol import fence

    if kind not in {"attempt", "job"}:
        raise DomainError("INVALID_ARGUMENT", "kind must be attempt or job")
    require_text(operation_id, "operation_id", 128)
    require_text(evidence, "reconciliation evidence")
    table = "tool_attempts" if kind == "attempt" else "jobs"
    with service.store.transaction() as conn:
        fence(conn, credentials)
        task = service._get(conn, "tasks", credentials.task_id)
        if not trusted(task):
            raise DomainError("FORBIDDEN", "Proxy reconciliation requires Yan's trusted permission mode")
        row = conn.execute(f"SELECT * FROM {table} WHERE task_id=? AND id=?",
                           (credentials.task_id, operation_id)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "operation does not belong to this Task")
        allowed = {"STARTED", "UNKNOWN"} if kind == "attempt" else {"STARTING", "UNKNOWN", "BLOCKED"}
        if row["status"] not in allowed:
            raise DomainError("INVALID_STATE", "operation is terminal or still observed running; inspect its receipt")
        old = json.loads(row["reconciliation"]) if row["reconciliation"] else None
        if old and old.get("evidence") == evidence and reviewed(row):
            review = old
        else:
            review = {"source": "proxy-declared", "proxy_id": credentials.worker_id,
                      "generation": credentials.generation, "observed_status": row["status"],
                      "observation_digest": observation_digest(row),
                      "evidence": evidence, "reviewed_at": time.time(),
                      "conclusion": "Proxy has reconciled this operation and accepts responsibility for continuing"}
            conn.execute(f"UPDATE {table} SET reconciliation=? WHERE id=?", (encode(review), operation_id))
            service.store.event(conn, credentials.task_id, "OPERATION_RECONCILED", "proxy-declared",
                                credentials.worker_id, {"kind": kind, "operation_id": operation_id, **review})
        return {"kind": kind, "operation_id": operation_id, "observed_status": row["status"],
                "reconciliation": review}
