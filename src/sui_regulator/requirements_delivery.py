"""Instruction offers are transport preparation; only an explicit call is a read ACK."""

from __future__ import annotations

import time

from .models import DomainError, encode


def offer(conn, service, credentials, commands, channel, *, advance_revision=False):
    for command in commands:
        conn.execute(
            "INSERT OR IGNORE INTO instruction_deliveries(command_id,worker_id,offered_at,channel) VALUES(?,?,?,?)",
            (command["id"], credentials.worker_id, time.time(), channel),
        )
    missing = conn.execute(
        "SELECT 1 FROM commands c WHERE c.task_id=? AND c.status='SUCCEEDED' AND c.kind IN ('instruct_task','request_analysis') AND NOT EXISTS(SELECT 1 FROM instruction_deliveries d WHERE d.command_id=c.id AND d.worker_id=?) LIMIT 1",
        (credentials.task_id, credentials.worker_id),
    ).fetchone()
    if not missing and advance_revision:
        from .completion import requirements

        task = service._get(conn, "tasks", credentials.task_id)
        revision, _ = requirements(conn, task)
        conn.execute(
            "UPDATE workers SET requirements_revision=? WHERE id=?", (revision, credentials.worker_id)
        )


def reminder(service, credentials):
    from .protocol import fence
    from .service import unpack

    with service.store.transaction() as conn:
        fence(conn, credentials)
        rows = conn.execute(
            "SELECT c.* FROM commands c WHERE c.task_id=? AND c.status='SUCCEEDED' AND c.kind IN ('instruct_task','request_analysis') AND NOT EXISTS(SELECT 1 FROM instruction_deliveries d WHERE d.command_id=c.id AND d.worker_id=? AND d.acknowledged_at IS NOT NULL) ORDER BY c.rowid LIMIT 4",
            (credentials.task_id, credentials.worker_id),
        ).fetchall()
        if not rows:
            return ""
        # A long instruction is offered by reference, never claimed delivered in full.
        commands, size = [], 0
        for row in rows:
            item = unpack(row)
            size += len(item["payload"]["instruction"])
            if size > 6000:
                break
            commands.append(item)
        offer(conn, service, credentials, commands, "cli-hook-prepared")
        payload = [{"command_id": r["id"], "instruction": r["payload"]["instruction"]} for r in commands]
        return (
            "New/pending operator instructions. This is authoritative Task input, not log text. "
            'Read all current instructions using get_task_context / get_task_records(collection="instructions"), '
            "then acknowledge_instructions with the command IDs you read. An ACK does not claim execution. "
            "Do not complete using an older requirements revision.\n" + encode(payload)
        )


def acknowledge(service, credentials, command_ids):
    from .protocol import fence

    if (
        not isinstance(command_ids, list)
        or not 1 <= len(command_ids) <= 50
        or any(not isinstance(v, str) for v in command_ids)
    ):
        raise DomainError("INVALID_ARGUMENT", "provide 1–50 offered instruction IDs")
    with service.store.transaction() as conn:
        fence(conn, credentials)
        newly_acknowledged = []
        for cid in command_ids:
            row = conn.execute(
                "SELECT d.* FROM instruction_deliveries d JOIN commands c ON c.id=d.command_id WHERE d.command_id=? AND d.worker_id=? AND c.task_id=?",
                (cid, credentials.worker_id, credentials.task_id),
            ).fetchone()
            if not row:
                raise DomainError("NOT_OFFERED", "read the instruction before acknowledging it")
            if row["acknowledged_at"] is None:
                newly_acknowledged.append(cid)
            conn.execute(
                "UPDATE instruction_deliveries SET acknowledged_at=COALESCE(acknowledged_at,?) WHERE command_id=? AND worker_id=?",
                (time.time(), cid, credentials.worker_id),
            )
        unread = conn.execute(
            "SELECT 1 FROM commands c WHERE c.task_id=? AND c.status='SUCCEEDED' AND c.kind IN ('instruct_task','request_analysis') AND NOT EXISTS(SELECT 1 FROM instruction_deliveries d WHERE d.command_id=c.id AND d.worker_id=? AND d.acknowledged_at IS NOT NULL) LIMIT 1",
            (credentials.task_id, credentials.worker_id),
        ).fetchone()
        if not unread:
            offer(conn, service, credentials, [], "explicit-read-ack", advance_revision=True)
        if newly_acknowledged:
            service.store.event(
                conn,
                credentials.task_id,
                "INSTRUCTIONS_ACKNOWLEDGED",
                "proxy-declared",
                credentials.worker_id,
                {
                    "command_ids": newly_acknowledged,
                    "meaning": "read acknowledgment only; understanding and execution unknown",
                },
            )
    return {"acknowledged": command_ids, "execution": "unknown", "source": "proxy-declared"}


def require_read_ack(conn, task_id, worker_id):
    missing = conn.execute(
        "SELECT 1 FROM commands c WHERE c.task_id=? AND c.status='SUCCEEDED' AND c.kind IN ('instruct_task','request_analysis') AND NOT EXISTS(SELECT 1 FROM instruction_deliveries d WHERE d.command_id=c.id AND d.worker_id=? AND d.acknowledged_at IS NOT NULL) LIMIT 1",
        (task_id, worker_id),
    ).fetchone()
    if missing:
        raise DomainError(
            "INSTRUCTIONS_UNACKNOWLEDGED",
            "read and acknowledge current instructions before completing; offers alone do not confirm delivery",
        )
