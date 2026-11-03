"""Bounded ledger pages; cursors are immutable row identities, not change feeds."""

from __future__ import annotations

from .models import DomainError, encode, require_text
from .service import unpack

COLLECTIONS = {
    "instructions": ("commands", " AND kind IN ('instruct_task','request_analysis') AND status='SUCCEEDED'"),
    "attempts": ("tool_attempts", ""),
    "jobs": ("jobs", ""),
    "proxies": ("workers", ""),
    "artifacts": ("artifacts", ""),
    "events": ("events", ""),
}


def page(service, task_id, collection, after=0, limit=20, record_id=None):
    if (
        not isinstance(collection, str)
        or collection not in COLLECTIONS
        or type(after) is not int
        or after < 0
        or type(limit) is not int
        or not 1 <= limit <= 50
    ):
        raise DomainError("INVALID_ARGUMENT", "invalid collection, cursor or page size (1–50)")
    table, extra = COLLECTIONS[collection]
    with service.store.connection() as conn:
        task = service._get(conn, "tasks", task_id)
        lookup, params = "", [task_id]
        if record_id is not None:
            require_text(record_id, "record_id", 128)
            lookup = " AND id=?"
            params.append(record_id)
        rows = conn.execute(
            f"SELECT rowid AS sequence,* FROM {table} WHERE task_id=? {extra} {lookup} AND rowid>? ORDER BY rowid LIMIT ?",
            (*params, after, limit + 1),
        ).fetchall()
    items, size = [], 0
    for row in rows[:limit]:
        item = service._job_view(row) if collection == "jobs" else unpack(row)
        if collection == "proxies":
            from .console_read import session_view

            item.update(session_view(item, service.config, task["current_worker"]))
            item["proxy_id"] = item["id"]
            item["stream_observation"] = (
                "text-delta-observed"
                if "claude-text-delta-observed" in item["capabilities"]
                else "event-only"
            )
        length = len(encode(item).encode())
        if size + length > 300000:
            if not items:
                raise DomainError(
                    "RECORD_TOO_LARGE", "record exceeds the page budget; use the private ledger export"
                )
            break
        items.append(item)
        size += length
    return {
        "collection": collection,
        "items": items,
        "next_after": items[-1]["sequence"] if items else after,
        "has_more": len(rows) > len(items),
        "source": "ledger; declarations retain their own source",
    }


def bound_context(task):
    # Keep the operator's complete original request. Reduce only referenced history.
    keys = ["events", "workers", "jobs", "artifacts", "tool_attempts", "incidents"]
    while len(encode(task).encode()) > 450000:
        candidates = [key for key in keys if len(task.get(key, [])) > (1 if key == "workers" else 0)]
        if not candidates:
            break
        key = max(candidates, key=lambda key: len(encode(task[key]).encode()))
        task[key].pop(0)
    task["context_scope"] = (
        "Bounded history preview; use history_refs and instruction_page. Missing preview entries are not absent work."
    )
