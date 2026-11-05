"""Read-only message windows over the existing bounded transcript, without a second store."""

from __future__ import annotations

import json

from .models import DomainError
from .transcripts import MAX_LINE_BYTES, MAX_PAGE_BYTES

PREVIEW_CHARS = 320
METADATA = ("time", "kind", "role", "source", "message_id", "input_field", "tool_name", "call_id", "model")


def _preview(row, offset):
    return {**{key: row[key] for key in METADATA if key in row},
            "id": offset, "text": row["text"][:PREVIEW_CHARS], "text_chars": len(row["text"]),
            "preview_truncated": len(row["text"]) > PREVIEW_CHARS,
            "truncated": bool(row.get("truncated")), "is_error": bool(row.get("is_error"))}


def _index(source, size):
    """Index at most the recorder's 4 MiB cap; keep previews and offsets, not full bodies."""
    source.seek(0)
    groups, calls, previous = [], {}, None
    snapshot_end = 0
    while source.tell() < size:
        offset = source.tell()
        line = source.readline(min(MAX_LINE_BYTES + 1, size - offset))
        if len(line) > MAX_LINE_BYTES:
            raise ValueError("oversized session record")
        if not line.endswith(b"\n"):
            break  # A live writer may have an incomplete final line. Retry on the next refresh.
        row = json.loads(line)
        if not isinstance(row, dict) or not all(isinstance(row.get(key), str) for key in ("role", "kind", "text")):
            raise ValueError("invalid session record")
        end = source.tell()
        snapshot_end = end
        linked = row["kind"] == "tool_result" and row.get("call_id") in calls
        continued = (row["kind"] == "text" and row.get("message_id") and previous
                     and previous["kind"] == "text" and previous.get("message_id") == row["message_id"]
                     and previous["role"] == row["role"])
        if linked:
            group = calls[row["call_id"]]
            group["result_count"] += 1
            if len(group["results"]) < 2:
                group["results"].append(_preview(row, offset))
            group["is_error"] |= bool(row.get("is_error"))
            previous = None
        elif continued:
            group = previous
            group["text"] = (group["text"] + row["text"])[:PREVIEW_CHARS]
            group["text_chars"] += len(row["text"])
            group["preview_truncated"] = group["text_chars"] > len(group["text"])
        else:
            group = _preview(row, offset)
            group.update(results=[], result_count=0, _parts=[])
            groups.append(group)
            if row["kind"] == "tool_call" and row.get("call_id"):
                calls[row["call_id"]] = group
            previous = group
        group["_parts"].append((offset, end))
        group.update(revision=end, last_offset=offset, endTime=row.get("time"), parts_count=len(group["_parts"]))
        group["truncated"] |= bool(row.get("truncated"))
    return groups, snapshot_end


def read_messages(source, size, result, *, view, limit, before, message_offset, after, through):
    if through is not None:
        if through > size:
            raise DomainError("INVALID_ARGUMENT", "snapshot exceeds transcript size")
        if through:
            source.seek(through - 1)
            if source.read(1) != b"\n":
                raise DomainError("INVALID_ARGUMENT", "snapshot is not a record boundary")
        size = through
    groups, result["snapshot_end"] = _index(source, size)
    if view == "messages":
        if before is not None and before not in {g["last_offset"] for g in groups}:
            raise DomainError("INVALID_ARGUMENT", "invalid message window cursor")
        window = sorted((g for g in groups if before is None or g["last_offset"] < before),
                        key=lambda g: g["last_offset"])
        result["has_more"] = len(window) > limit
        result["items"] = [{key: value for key, value in group.items() if key != "_parts"}
                           for group in window[-limit:]]
        result["next_before"] = result["items"][0]["last_offset"] if result["items"] else None
        return
    group = next((g for g in groups if g["id"] == message_offset), None)
    if group is None:
        raise DomainError("NOT_FOUND", "message does not exist in this transcript snapshot")
    if after not in {0, message_offset, *(end for _, end in group["_parts"])}:
        raise DomainError("INVALID_ARGUMENT", "invalid message detail cursor")
    remaining = [(start, end) for start, end in group["_parts"] if start >= after]
    used = 0
    for start, end in remaining:
        if len(result["items"]) >= limit or used + end - start > MAX_PAGE_BYTES:
            break
        source.seek(start)
        row = json.loads(source.read(end - start))
        row["id"] = start
        result["items"].append(row)
        used += end - start
        result["next_after"] = end
    result.update(message_offset=message_offset, revision=group["revision"], parts_count=group["parts_count"],
                  has_more=len(result["items"]) < len(remaining))
