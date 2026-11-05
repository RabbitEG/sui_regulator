"""Independent Git mailbox bridge. All Controller access uses existing Yan RPC."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .gateway import call
from .git_transport import GitTransport
from .models import DomainError, encode
from .processes import FileLock

KINDS = {
    "submit_task",
    "instruct_task",
    "request_analysis",
    "hold_task",
    "resume_task",
    "cancel_task",
    "set_recovery_policy",
}
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
MAX_COMMANDS = 10000


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError("IDs require 1–64 letters, numbers, hyphens or underscores")
    return value


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def redact(value):
    if isinstance(value, dict):
        return {
            k: "[REDACTED]"
            if re.search(r"secret|password|token|api.?key|authorization|cookie|lease_hash", k, re.I)
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    for key, secret in os.environ.items():
        if len(secret) >= 8 and re.search(r"TOKEN|SECRET|PASSWORD|API_KEY", key):
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        value,
        flags=re.S,
    )
    value = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})",
        "[REDACTED]",
        value,
    )
    value = re.sub(r"(https?://)[^/\s]+@", r"\1[REDACTED]@", value)
    value = re.sub(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|password|authorization)\s*[:=]\s*)([^\r\n]+)",
        r"\1[REDACTED]",
        value,
    )
    return value


@dataclass(frozen=True)
class BridgeConfig:
    remote: str
    instance_id: str
    enrollment_epoch: str
    yan_home: str
    workspace_id: str
    uid: int
    poll_seconds: float = 30
    publish_seconds: float = 300
    author_name: str = "Sui Git Bridge"
    author_email: str = "sui-bridge@localhost"
    report_root: str = ""
    reports: dict | None = None

    def __post_init__(self):
        identifier(self.instance_id)
        identifier(self.enrollment_epoch)
        for seconds in (self.poll_seconds, self.publish_seconds):
            if type(seconds) not in (int, float) or not 1 <= seconds <= 86400:
                raise ValueError("poll and publish intervals must be in [1,86400]")
        if not Path(self.yan_home).is_absolute():
            raise ValueError("yan_home must be absolute")

    @property
    def manifest(self):
        return {
            "schema_version": 1,
            "instance_id": self.instance_id,
            "enrollment_epoch": self.enrollment_epoch,
        }


class GitBridge:
    def __init__(self, state_dir: Path, *, rpc=call):
        self.state_dir = state_dir.expanduser().absolute()
        self.config = BridgeConfig(**json.loads((self.state_dir / "config.json").read_text()))
        if self.config.uid != os.getuid():
            raise DomainError("BRIDGE_UID_CHANGED", "run as the enrolled Yan UID")
        self.rpc = rpc
        self.stack = ExitStack()
        self.db = None
        self.dirty = False

    def __enter__(self):
        try:
            self.stack.enter_context(FileLock(self.state_dir / "bridge.lock"))
            self.stack.enter_context(
                FileLock(Path(self.config.yan_home) / ("git-bridge-" + self.config.instance_id + ".lock"))
            )
            self.db = sqlite3.connect(self.state_dir / "bridge.sqlite", isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,hash TEXT NOT NULL,source_sha TEXT NOT NULL,
                    envelope TEXT,request TEXT,receipt TEXT NOT NULL,state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(task_key TEXT PRIMARY KEY,creator TEXT,task_id TEXT UNIQUE,status TEXT);
            """)
            self.git = GitTransport(
                self.state_dir / "cache.git",
                self.config.remote,
                author_name=self.config.author_name,
                author_email=self.config.author_email,
            )
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        if self.db:
            self.db.close()
            self.db = None
        self.stack.close()

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, encode(value)))

    def query(self, method, params):
        response = self.rpc(Path(self.config.yan_home), "yan." + method, params)
        if "error" in response:
            raise DomainError(response["error"]["code"], response["error"]["message"])
        return response["result"]

    def check_controller(self):
        snapshot = self.query("get_console_snapshot", {"section": "overview", "limit": 1})
        if snapshot["workspace_id"] != self.config.workspace_id:
            raise DomainError(
                "LEDGER_CHANGED", "Yan ledger identity changed; restore the matching ledger and Bridge state"
            )
        self.set_meta("controller", snapshot["controller"])
        self.set_meta("last_controller_query", snapshot["queried_at"])

    def _validate(self, command_id, data, entries):
        if not isinstance(data, dict):
            raise ValueError("command must be an object")
        required = {
            "schema_version",
            "instance_id",
            "enrollment_epoch",
            "command_id",
            "task_key",
            "kind",
            "payload",
        }
        if not required <= data.keys() or set(data) - required - {"depends_on", "brief_path", "confirm"}:
            raise ValueError("invalid command envelope fields")
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError("unsupported command schema")
        if any(data[k] != self.config.manifest[k] for k in ("instance_id", "enrollment_epoch")):
            raise ValueError("command belongs to another enrolled instance")
        if data["command_id"] != command_id:
            raise ValueError("command ID must match its immutable directory")
        identifier(data["task_key"])
        dependencies = data.get("depends_on", [])
        if not isinstance(dependencies, list) or len(dependencies) > 50 or command_id in dependencies:
            raise ValueError("invalid command dependencies")
        for dependency in dependencies:
            identifier(dependency)
        kind, payload = data["kind"], data["payload"]
        if kind not in KINDS or not isinstance(payload, dict):
            raise ValueError("unsupported command kind or payload")
        if kind in {"hold_task", "cancel_task"} and data.get("confirm") != kind:
            raise ValueError("Hold/Cancel requires confirm matching kind")
        if "brief_path" in data:
            part = data["brief_path"]
            if (
                kind != "submit_task"
                or not isinstance(part, str)
                or PurePosixPath(part).is_absolute()
                or any(p in {"", ".", ".."} for p in part.split("/"))
            ):
                raise ValueError("brief must be a relative file in the command directory")
            if "objective" in payload:
                raise ValueError("provide brief_path or payload.objective, not both")
            brief = self.git.blob(entries, "commands/" + command_id + "/" + part).decode("utf-8")
            if not brief.strip() or len(brief) > 16384:
                raise ValueError("brief exceeds Yan character limit or is empty; nothing was truncated")
            payload["objective"] = brief
        if len(encode(payload)) > 65536:
            raise ValueError("payload exceeds Yan limit")
        # The existing Controller remains the final authority for payload validation.
        return data

    def ingest(self):
        sha = self.git.fetch("commands/" + self.config.instance_id, "commands")
        previous = self.meta("command_head")
        if previous and not self.git.ancestor(previous, sha):
            raise DomainError(
                "COMMAND_HISTORY_REWRITTEN", "command branch is not a descendant of the last accepted commit"
            )
        entries = self.git.tree(sha)
        manifest = json.loads(self.git.blob(entries, "instance.json"))
        if manifest != self.config.manifest:
            raise DomainError("INSTANCE_MISMATCH", "instance.json differs from local enrollment")
        candidates = sorted(
            path
            for path in entries
            if re.fullmatch(r"commands/[A-Za-z0-9][A-Za-z0-9_-]{0,63}/command.json", path)
        )
        if len(candidates) > MAX_COMMANDS:
            raise DomainError("MAILBOX_LIMIT", "mailbox exceeds 10000 commands")
        for path in candidates:
            command_id = path.split("/")[1]
            # Hash the whole bundle tree so edits to briefs are not silently ignored.
            bundle = {p: v for p, v in entries.items() if p.startswith("commands/" + command_id + "/")}
            fingerprint = hashlib.sha256(encode(bundle).encode()).hexdigest()
            old = self.db.execute("SELECT * FROM messages WHERE id=?", (command_id,)).fetchone()
            if old:
                if old["hash"] != fingerprint:
                    receipt = json.loads(old["receipt"])
                    receipt["content_conflict"] = {
                        "source_sha": sha,
                        "observed_hash": fingerprint,
                        "message": "immutable command changed; original receipt retained",
                    }
                    self.save_receipt(command_id, receipt, old["state"])
                continue
            if self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] >= MAX_COMMANDS:
                raise DomainError("MAILBOX_LIMIT", "local inbox exceeds 10000 commands")
            receipt = {
                **self.config.manifest,
                "command_id": command_id,
                "content_hash": fingerprint,
                "source_sha": sha,
                "received_at": time.time(),
                "transport_state": "RECEIVED",
            }
            data, state = None, "RECEIVED"
            try:
                data = self._validate(command_id, json.loads(self.git.blob(entries, path)), entries)
                receipt.update(kind=data["kind"], task_key=data["task_key"])
            except (ValueError, TypeError, DomainError, UnicodeError) as exc:
                state = "REJECTED"
                receipt.update(transport_state=state, error={"code": "INVALID_BUNDLE", "message": str(exc)})
            self.db.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                (command_id, fingerprint, sha, encode(data) if data else None, None, encode(receipt), state),
            )
            self.dirty = True
        self.set_meta("command_head", sha)
        self.set_meta("last_git_fetch", time.time())

    def save_receipt(self, command_id, receipt, state):
        receipt["transport_state"] = state
        old = self.db.execute("SELECT receipt FROM messages WHERE id=?", (command_id,)).fetchone()
        value = encode(receipt)
        self.dirty |= not old or old[0] != value
        self.db.execute("UPDATE messages SET receipt=?,state=? WHERE id=?", (value, state, command_id))

    def deliver(self):
        # More than one pass permits submit + dependent instruction in one fetch.
        cursor = self.meta("delivery_cursor", 0)
        batch = self.db.execute(
            "SELECT rowid AS position,id FROM messages WHERE rowid>? ORDER BY rowid LIMIT 100", (cursor,)
        ).fetchall()
        if not batch:
            batch = self.db.execute(
                "SELECT rowid AS position,id FROM messages ORDER BY rowid LIMIT 100"
            ).fetchall()
        for _ in range(2):
            for item in batch:
                row = self.db.execute("SELECT * FROM messages WHERE id=?", (item["id"],)).fetchone()
                receipt = json.loads(row["receipt"])
                if row["state"] == "REJECTED":
                    continue
                data = json.loads(row["envelope"])
                if row["state"] == "ACKNOWLEDGED" and (
                    receipt.get("yan_status") == "REJECTED"
                    or data["kind"] not in {"instruct_task", "request_analysis"}
                ):
                    continue
                try:
                    if receipt.get("yan_command_id"):
                        observed = self.query("get_command_status", {"command_id": receipt["yan_command_id"]})
                    else:
                        if receipt.get("content_conflict") and row["request"] is None:
                            receipt["error"] = {
                                "code": "CONTENT_CONFLICT",
                                "message": "unsubmitted command was changed",
                            }
                            self.save_receipt(row["id"], receipt, "REJECTED")
                            continue
                        dependencies = [
                            self.db.execute(
                                "SELECT receipt,state FROM messages WHERE id=?", (key,)
                            ).fetchone()
                            for key in data.get("depends_on", [])
                        ]
                        if any(
                            d
                            and (
                                d["state"] == "REJECTED"
                                or json.loads(d["receipt"]).get("yan_status") == "REJECTED"
                            )
                            for d in dependencies
                        ):
                            receipt["error"] = {
                                "code": "DEPENDENCY_REJECTED",
                                "message": "a required command was rejected",
                            }
                            self.save_receipt(row["id"], receipt, "REJECTED")
                            continue
                        if any(
                            not d or json.loads(d["receipt"]).get("yan_status") != "SUCCEEDED"
                            for d in dependencies
                        ):
                            self.save_receipt(row["id"], receipt, "WAITING_DEPENDENCY")
                            continue
                        task = self.db.execute(
                            "SELECT * FROM tasks WHERE task_key=?", (data["task_key"],)
                        ).fetchone()
                        if data["kind"] == "submit_task":
                            if task and task["creator"] != row["id"]:
                                receipt["error"] = {
                                    "code": "TASK_KEY_CONFLICT",
                                    "message": "task_key is already bound to a different creation",
                                }
                                self.save_receipt(row["id"], receipt, "REJECTED")
                                continue
                            if not task:
                                self.db.execute(
                                    "INSERT INTO tasks(task_key,creator) VALUES(?,?)",
                                    (data["task_key"], row["id"]),
                                )
                        elif not task or not task["task_id"]:
                            self.save_receipt(row["id"], receipt, "WAITING_TASK")
                            continue
                        params = (
                            json.loads(row["request"])
                            if row["request"]
                            else {
                                "idempotency_key": f"git:{self.config.instance_id}:{self.config.enrollment_epoch}:{row['id']}",
                                "payload": data["payload"],
                            }
                        )
                        if data["kind"] != "submit_task":
                            params["task_id"] = task["task_id"]
                        if not row["request"]:
                            # Durable request precedes the first external RPC. Replays never change this request.
                            self.db.execute(
                                "UPDATE messages SET request=? WHERE id=?", (encode(params), row["id"])
                            )
                        observed = self.query(data["kind"], params)
                        receipt.setdefault("submitted_at", time.time())
                    receipt.pop("error", None)
                    receipt.update(
                        yan_command_id=observed["id"],
                        task_id=observed["task_id"],
                        yan_status=observed["status"],
                        result=observed.get("result"),
                        yan_error=observed.get("error"),
                        yan_updated_at=observed.get("updated_at"),
                        delivery=observed.get("delivery", []),
                        execution=observed.get("execution", "unknown"),
                    )
                    if data["kind"] == "submit_task":
                        self.db.execute(
                            "UPDATE tasks SET task_id=? WHERE task_key=?",
                            (observed["task_id"], data["task_key"]),
                        )
                    self.save_receipt(
                        row["id"],
                        receipt,
                        "ACKNOWLEDGED" if observed["status"] in {"SUCCEEDED", "REJECTED"} else "SUBMITTED",
                    )
                except DomainError as exc:
                    receipt["error"] = {"code": exc.code, "message": str(exc)}
                    self.save_receipt(
                        row["id"],
                        receipt,
                        "REJECTED"
                        if exc.code in {"INVALID_ARGUMENT", "IDEMPOTENCY_CONFLICT", "MESSAGE_TOO_LARGE"}
                        else "WAITING_RPC",
                    )
                except (OSError, ValueError) as exc:
                    receipt["error"] = {"code": "RPC_UNAVAILABLE", "message": type(exc).__name__}
                    self.save_receipt(row["id"], receipt, "WAITING_RPC")
                    raise
        if batch:
            self.set_meta("delivery_cursor", batch[-1]["position"])

    def bind_task(self, task_key, task_id):
        identifier(task_key)
        self.check_controller()
        self.query("get_task", {"task_id": task_id})
        old = self.db.execute("SELECT task_id FROM tasks WHERE task_key=?", (task_key,)).fetchone()
        if old and old[0] != task_id:
            raise DomainError("TASK_KEY_CONFLICT", "task_key is already bound")
        self.db.execute("INSERT OR IGNORE INTO tasks(task_key,task_id) VALUES(?,?)", (task_key, task_id))
        self.set_meta("force_publish", True)

    def collect(self):
        for row in self.db.execute("SELECT * FROM tasks WHERE task_id IS NOT NULL").fetchall():
            try:
                task = self.query("get_task", {"task_id": row["task_id"]})
            except DomainError as exc:
                value = (
                    json.loads(row["status"])
                    if row["status"]
                    else {"id": row["task_id"], "status": "UNOBSERVED"}
                )
                value["query_error"] = exc.code
                self.db.execute(
                    "UPDATE tasks SET status=? WHERE task_key=?", (encode(value), row["task_key"])
                )
                continue
            value = {
                key: task.get(key)
                for key in (
                    "id",
                    "title",
                    "desired",
                    "status",
                    "declared",
                    "generation",
                    "current_proxy",
                    "permission_mode",
                    "wait_state",
                )
            }
            value["observed_at"] = time.time()
            value["source"] = "Yan RPC; declared fields remain proxy declarations"
            value["jobs"] = [
                {k: j.get(k) for k in ("id", "logical_key", "status", "exit_code", "observed_at", "stale")}
                for j in task["jobs"]
            ]
            value["proxies"] = [
                {
                    k: w.get(k)
                    for k in (
                        "id",
                        "session_id",
                        "generation",
                        "status",
                        "heartbeat_at",
                        "activity_at",
                        "error",
                    )
                }
                for w in task["workers"]
            ]
            value["incidents"] = [
                {k: i.get(k) for k in ("id", "rule_id", "action", "status", "outcome")}
                for i in task["incidents"]
            ]
            value["completion"] = task.get("completion")
            old = json.loads(row["status"]) if row["status"] else {}
            self.dirty |= any(
                value.get(k) != old.get(k) for k in ("desired", "status", "generation", "completion")
            )
            self.db.execute(
                "UPDATE tasks SET status=? WHERE task_key=?", (encode(redact(value)), row["task_key"])
            )

    def export(self, errors):
        now = time.time()
        snapshot = {
            **self.config.manifest,
            "as_of": now,
            "last_git_fetch_at": self.meta("last_git_fetch"),
            "last_controller_query_at": self.meta("last_controller_query"),
            "command_head": self.meta("command_head"),
            "controller": self.meta("controller", {"state": "UNKNOWN"}),
            "errors": errors,
            "note": "Dated observations, not live availability. Proxy declarations are not verified work.",
        }
        if "controller" in errors:
            snapshot["controller"] = {"state": "UNREACHABLE", "last_observation": snapshot["controller"]}
        files = {"snapshot.json": json_bytes(redact(snapshot))}
        lines = [
            f"# {self.config.instance_id}",
            "",
            f"Snapshot UTC: {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now))}",
            "",
            "This is a dated snapshot, not a live online indicator.",
            "",
            "| Task | Desired | Observed status | Generation |",
            "|---|---|---|---|",
        ]
        for row in self.db.execute("SELECT * FROM tasks ORDER BY task_key"):
            status = (
                json.loads(row["status"]) if row["status"] else {"id": row["task_id"], "status": "UNOBSERVED"}
            )
            files[f"tasks/{row['task_key']}/status.json"] = json_bytes(
                {**status, "query_stale": "controller" in errors or bool(status.get("query_error"))}
            )
            lines.append(
                f"| {row['task_key']} | {status.get('desired', 'unknown')} | {status['status']} | {status.get('generation', 'unknown')} |"
            )
        for row in self.db.execute("SELECT * FROM messages ORDER BY rowid"):
            files[f"receipts/{row['id']}.json"] = json_bytes(redact(json.loads(row["receipt"])))
        report_errors = {}
        root = Path(self.config.report_root).resolve() if self.config.report_root else None
        for task_key, relative in (self.config.reports or {}).items():
            try:
                identifier(task_key)
                if root is None or not isinstance(relative, str):
                    raise ValueError("report_root and relative file are required")
                part = PurePosixPath(relative)
                if part.is_absolute() or any(x in {".", ".."} for x in part.parts):
                    raise ValueError("report must stay inside report_root")
                path = root / relative
                if any(
                    p.is_symlink() for p in [path, *path.parents] if p != root
                ) or not path.resolve().is_relative_to(root):
                    raise ValueError("report symlinks are not supported")
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise ValueError("report must be a regular file")
                    raw = source.read(65537)
                if len(raw) > 65536:
                    raise ValueError("report exceeds 64 KiB; not truncated")
                files[f"tasks/{task_key}/report.md"] = redact(raw.decode("utf-8")).encode()
            except (OSError, ValueError) as exc:
                report_errors[task_key] = type(exc).__name__ + ": report unavailable or outside export limits"
        files["report-errors.json"] = json_bytes(report_errors)
        if errors:
            lines += [
                "",
                "## Transport errors",
                "",
                "```json",
                json.dumps(redact(errors), ensure_ascii=False, indent=2),
                "```",
            ]
        files["summary.md"] = ("\n".join(lines) + "\n").encode()
        return files

    def publish(self, files):
        branch = "status/" + self.config.instance_id
        parent = self.git.fetch(branch, "status", optional=True)
        remove = []
        if parent:
            entries = self.git.tree(parent)
            owner = json.loads(self.git.blob(entries, "bridge.json"))
            if not isinstance(owner, dict) or owner.get("instance") != self.config.manifest:
                raise DomainError("STATUS_OWNER_MISMATCH", "status branch belongs to a different enrollment")
            managed = owner.get("files", [])
            if not isinstance(managed, list):
                raise DomainError("STATUS_MANIFEST_INVALID", "managed files must be a list")
            for path in managed:
                if not isinstance(path, str) or not re.fullmatch(
                    r"(?:snapshot.json|summary.md|report-errors.json|receipts/[\w-]+.json|tasks/[\w-]+/(?:status.json|report.md))",
                    path,
                ):
                    raise DomainError("STATUS_MANIFEST_INVALID", "invalid managed status path")
                if path not in files:
                    remove.append(path)
        files["bridge.json"] = json_bytes({"instance": self.config.manifest, "files": sorted(files)})
        sha = self.git.commit_files(files, parent=parent, remove=remove)
        self.git.push(sha, branch)
        self.set_meta("last_published", time.time())
        self.set_meta("force_publish", False)
        self.dirty = False
        return sha

    def cycle(self, *, force_publish=False):
        errors = {}
        try:
            self.ingest()
        except (DomainError, OSError, ValueError) as exc:
            errors["git"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
        try:
            self.check_controller()
            self.deliver()
            self.collect()
        except (DomainError, OSError, ValueError) as exc:
            errors["controller"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
        self.dirty |= errors != self.meta("last_errors", {})
        self.set_meta("last_errors", errors)
        published = None
        if (
            self.dirty
            or force_publish
            or self.meta("force_publish")
            or time.time() - self.meta("last_published", 0) >= self.config.publish_seconds
        ):
            self.set_meta("force_publish", True)
            try:
                published = self.publish(self.export(errors))
            except (DomainError, OSError, ValueError) as exc:
                errors["publish"] = {"code": getattr(exc, "code", type(exc).__name__), "message": str(exc)}
        self.set_meta("last_errors", errors)
        return {"instance_id": self.config.instance_id, "published": published, "errors": redact(errors)}


def enroll(state_dir, *, remote, instance_id, enrollment_epoch, yan_home, **settings):
    state_dir, yan_home = Path(state_dir).expanduser().absolute(), Path(yan_home).expanduser().absolute()
    if state_dir.exists():
        raise ValueError("Bridge state directory must be new; keep existing inbox state when restarting")
    result = call(yan_home, "yan.get_console_snapshot", {"section": "overview", "limit": 1})
    if "error" in result:
        raise ValueError("Yan must be reachable for local enrollment")
    config = BridgeConfig(
        remote,
        instance_id,
        enrollment_epoch,
        str(yan_home),
        result["result"]["workspace_id"],
        os.getuid(),
        **settings,
    )
    state_dir.mkdir(parents=True, mode=0o700)
    path = state_dir / "config.json"
    path.write_bytes(json_bytes(asdict(config)))
    path.chmod(0o600)
    # Validate remote metadata before any command delivery.
    with GitBridge(state_dir) as bridge:
        sha = bridge.git.fetch("commands/" + instance_id, "commands")
        if json.loads(bridge.git.blob(bridge.git.tree(sha), "instance.json")) != config.manifest:
            raise DomainError("INSTANCE_MISMATCH", "remote manifest differs from enrollment")
    return {"state_dir": str(state_dir), **config.manifest}
