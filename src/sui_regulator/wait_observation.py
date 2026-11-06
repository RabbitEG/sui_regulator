"""Renewable native-tool observations. Never infer liveness from conversational text."""

from __future__ import annotations

import json
import os
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import digest, encode
from .processes import identity, process_state


def descendants(root):
    """Bounded Linux walk of this CLI's descendants, including children of its threads.

    Command lines are used transiently to associate a Bash launcher with a call;
    they are not executed or persisted. An unreadable tree is not proof of exit.
    """
    if not root or identity(root["pid"]) != root:
        return []
    queue, seen, found = [root["pid"]], {root["pid"]}, []
    try:
        boot_time = time.time() - float(Path("/proc/uptime").read_text().split()[0])
        ticks = os.sysconf("SC_CLK_TCK")
        while queue and len(seen) < 256:
            pid = queue.pop(0)
            try:
                threads = list(Path(f"/proc/{pid}/task").iterdir())[:128]
            except OSError:
                continue
            for thread in threads:
                try:
                    children = (thread / "children").read_text().split()
                except OSError:
                    continue
                for value in children:
                    child = int(value)
                    if child in seen or len(seen) >= 256:
                        continue
                    seen.add(child)
                    current = identity(child)
                    if not current:
                        continue
                    queue.append(child)
                    try:
                        with Path(f"/proc/{child}/cmdline").open("rb") as source:
                            argv = source.read(65536).decode(errors="replace").split("\0")
                    except OSError:
                        continue
                    if "sui_regulator.mcp" in argv or "sui_regulator.tool_hook" in argv:
                        continue  # An idle MCP server or heartbeat helper is not a live Bash command.
                    found.append({"identity": current, "argv": argv,
                                  "started_at": boot_time + current["start_ticks"] / ticks})
    except (OSError, ValueError):
        return found
    return found


def matches_command(argv, command):
    """Exact executable/arguments or the CLI's quoted eval payload, never a text substring."""
    argv = [part for part in argv if part]
    try:
        if shlex.join(shlex.split(command)) == shlex.join(argv):
            return True  # The shell may exec a simple command without keeping its launcher.
        if not argv or Path(argv[0]).name not in {"bash", "sh", "dash", "zsh", "ksh"}:
            return False
        flag = next((i for i, arg in enumerate(argv[1:], 1) if arg in {"-c", "-lc"}), None)
        if flag is None or flag + 1 >= len(argv):
            return False
        script = argv[flag + 1]
        if script == command:
            return True
        words = shlex.split(script)
        # Claude Code wraps Bash in shell initialization followed by eval '<command>'.
        return any(word == "eval" and words[i + 1] == command
                   and (i == 0 or words[i - 1] in {"&&", ";", "||"})
                   for i, word in enumerate(words[:-1]))
    except ValueError:
        return False


class ToolProcesses:
    def __init__(self, root):
        self.root, self.known = root, {}

    def observe(self, calls):
        live, ended = set(), set()
        self.known = {key: value for key, value in self.known.items() if key in calls}
        candidates = None
        claimed = {value["pid"] for value in self.known.values()}
        for call_id, call in calls.items():
            if call["name"].casefold() not in {"bash", "shell"}:
                continue
            known = self.known.get(call_id)
            if known:
                state = process_state(known)
                if state and state not in {"T", "t"}:
                    live.add(call_id)
                elif identity(known["pid"]) != known:
                    ended.add(call_id)
                continue
            command = call["arguments"].get("command")
            if not isinstance(command, str) or not command.strip():
                continue
            if candidates is None:
                candidates = descendants(self.root)
            for candidate in candidates:
                # Do not associate an unrelated older command or share one PID between calls.
                if (candidate["identity"]["pid"] in claimed
                        or candidate["started_at"] < call["started_at"] - 1):
                    continue
                if not matches_command(candidate["argv"], command):
                    continue
                self.known[call_id] = candidate["identity"]
                claimed.add(candidate["identity"]["pid"])
                if process_state(candidate["identity"]) not in {None, "T", "t"}:
                    live.add(call_id)
                break
        if process_state(self.root) in {None, "T", "t"}:
            live.clear()
        return live, ended


@dataclass
class WaitObservation:
    live_tools: set[str] = field(default_factory=set)
    ended_tools: set[str] = field(default_factory=set)
    background_pending: bool = False


class ToolWaitMonitor:
    def __init__(self, service, credentials, root):
        self.service, self.credentials = service, credentials
        self.processes = ToolProcesses(root)
        self.missing_since = {}

    def poll(self, normalizer, now=None):
        now = time.time() if now is None else now
        service, owner = self.service, self.credentials
        rows = service.store.rows(
            "SELECT * FROM tool_attempts WHERE task_id=? AND worker_id=? AND tool='Bash' "
            "AND status IN ('STARTED','RUNNING') ORDER BY created_at LIMIT 512",
            (owner.task_id, owner.worker_id),
        )
        by_call = {json.loads(row["runtime"]).get("tool_use_id"): row for row in rows}
        normalizer.observed_calls = {key for key in by_call if isinstance(key, str)}
        calls = {key: call for key, call in normalizer.tool_calls.items()
                 if key in normalizer.pending or key in by_call}
        live, ended = self.processes.observe(calls)
        self.missing_since = {key: value for key, value in self.missing_since.items() if key in ended}
        for key in ended:
            self.missing_since.setdefault(key, now)
        # A Hook can finish an operation even if its corresponding stream frame was lost.
        pending_ids = {"native-" + digest([owner.worker_id, key]): key for key in normalizer.pending}
        if pending_ids:
            marks = ",".join("?" for _ in pending_ids)
            for row in service.store.rows(
                f"SELECT id,status FROM tool_attempts WHERE id IN ({marks})", tuple(pending_ids)
            ):
                if row["status"] not in {"STARTED", "RUNNING", "READING"}:
                    ended.add(pending_ids[row["id"]])
        pending = False
        for row in rows:
            runtime = json.loads(row["runtime"])
            call_id = runtime.get("tool_use_id")
            call = normalizer.tool_calls.get(call_id, {})
            observed = max(row["created_at"], runtime.get("observed_at", row["created_at"]))
            deadline = max(observed + normalizer.tool_observation_seconds, call.get("valid_until", 0))
            if call_id in ended and now - self.missing_since.setdefault(call_id, now) < min(5, normalizer.tool_observation_seconds):
                # Let normal PostToolUse delivery settle after a launcher exits.
                pending |= row["status"] == "RUNNING"
                continue
            if call_id in live or (call_id not in ended and now < deadline):
                pending |= row["status"] == "RUNNING"
                continue
            reason = "launcher identity no longer observable" if call_id in ended else "tool observation expired"
            runtime.update(source="wrapper-wait-observer", observation_reason=reason,
                           observed_at=now, observation_state="lost")
            if call_id in self.processes.known:
                runtime["execution_identity"] = self.processes.known[call_id]
            with service.store.transaction() as conn:
                changed = conn.execute(
                    "UPDATE tool_attempts SET status='UNKNOWN',runtime=? WHERE id=? AND status IN ('STARTED','RUNNING')",
                    (encode(runtime), row["id"]),
                ).rowcount
                if changed:
                    service.store.event(conn, owner.task_id, "TOOL_WAIT_OBSERVATION_LOST", "wrapper-observed",
                                        owner.worker_id, {"attempt_id": row["id"], "status": "UNKNOWN",
                                                          "runtime": runtime, "counts_as_verified_progress": False})
                    if isinstance(call_id, str):
                        ended.add(call_id)
        return WaitObservation(live - ended, ended, pending)
