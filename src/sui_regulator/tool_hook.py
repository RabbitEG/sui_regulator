"""Explicit Claude Code hooks for native tool ownership and repeat protection."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import attempts
from .config import Config
from .models import Credentials, DomainError, digest, encode
from .protocol import fence
from .recovery import advise
from .service import Service


def handle(service, credentials, event):
    name, stage = event.get("tool_name"), event.get("hook_event_name")
    attempt_id = "native-" + digest([credentials.worker_id, event.get("tool_use_id")])
    from .requirements_delivery import reminder

    message = ""
    try:
        message = (
            reminder(service, credentials)
            if stage in {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop"}
            else ""
        )
    except DomainError:
        if stage in {"PreToolUse", "Stop"}:
            raise
    if stage == "Stop":
        return (
            {"hookSpecificOutput": {"hookEventName": stage, "additionalContext": message}} if message else {}
        )
    if stage == "PreToolUse":
        with service.store.connection() as conn:
            fence(conn, credentials)
        task = service.get_task(credentials.task_id)
        data = event.get("tool_input", {})
        operation_key = digest(["Bash", data.get("command")])
        approach_key = (
            digest([operation_key, attempts.workspace_fingerprint(task["workspace"])])
            if name == "Bash"
            else ""
        )
        with service.store.transaction() as conn:
            fence(conn, credentials)
            current = service._get(conn, "tasks", credentials.task_id)
            denied = (
                attempts.denial(conn, current, operation_key, approach_key)
                if name in {"Bash", "Edit", "Write"}
                else None
            )
            note = (
                attempts.repetition_note(conn, current, operation_key, approach_key)
                if name == "Bash"
                else None
            )
            if note:
                advise(conn, service, current, note)
            if denied:
                advise(conn, service, current, denied)
            elif name == "Bash":
                attempts.start(conn, current, credentials, attempt_id, "Bash", operation_key, approach_key)
            guidance = message + "\n" + (note or json.loads(current["recovery_state"]).get("guidance", ""))
        if denied:
            return {
                "hookSpecificOutput": {
                    "hookEventName": stage,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": denied,
                }
            }
        return (
            {"hookSpecificOutput": {"hookEventName": stage, "additionalContext": guidance}}
            if guidance
            else {}
        )
    if name == "Bash" and stage in {"PostToolUse", "PostToolUseFailure"}:
        result = event.get("error") if stage == "PostToolUseFailure" else event.get("tool_response")
        status = "FAILED" if stage == "PostToolUseFailure" else "SUCCEEDED"
        if isinstance(result, dict) and result.get("backgroundTaskId"):
            status = "RUNNING"
        with service.store.transaction() as conn:
            worker = service._get(conn, "workers", credentials.worker_id)
            runtime = {
                "source": "claude-hook",
                "session_id": worker["session_id"],
                "tool_use_id": event.get("tool_use_id"),
                "observed_at": time.time(),
            }
            if status == "RUNNING":
                runtime["handle"] = str(result["backgroundTaskId"])[:256]
            if not attempts.finish(conn, attempt_id, status, result, runtime):
                return {}
            service.store.event(
                conn,
                credentials.task_id,
                "NATIVE_TOOL_RESULT",
                "claude-hook",
                credentials.worker_id,
                {
                    "attempt_id": attempt_id,
                    "tool": name,
                    "status": status,
                    "runtime": runtime,
                    "result_hash": attempts.error_fingerprint(result),
                    "counts_as_verified_progress": False,
                },
            )
    return {"hookSpecificOutput": {"hookEventName": stage, "additionalContext": message}} if message else {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    try:
        credentials = Credentials(**json.loads(os.environ["SUI_WORKER_CREDENTIALS"]))
        event = json.loads(sys.stdin.read(1048576))
        print(encode(handle(Service(Config.load(args.home)), credentials, event)))
    except Exception:
        # PreToolUse exit 2 denies the operation. Do not print credentials/input.
        print(
            "Sui ownership or operation verification failed; re-read task context before retrying.",
            file=sys.stderr,
        )
        raise SystemExit(2)


if __name__ == "__main__":
    main()
