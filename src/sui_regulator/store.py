from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import encode

SCHEMA_VERSION = 2
MIGRATIONS = {
    1: [
        """CREATE TABLE tasks (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL,
        workspace TEXT NOT NULL, adapter TEXT NOT NULL, spec TEXT NOT NULL,
        desired TEXT NOT NULL DEFAULT 'ACTIVE', status TEXT NOT NULL DEFAULT 'READY',
        generation INTEGER NOT NULL DEFAULT 0, current_worker TEXT,
        declared TEXT NOT NULL DEFAULT '{}', declared_at REAL,
        next_start_at REAL NOT NULL DEFAULT 0, recovery_reset_at REAL NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)""",
        """CREATE TABLE commands (
        id TEXT PRIMARY KEY, actor TEXT NOT NULL, idempotency_key TEXT NOT NULL,
        kind TEXT NOT NULL, task_id TEXT NOT NULL, payload TEXT NOT NULL,
        request_hash TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
        error TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(actor, idempotency_key))""",
        """CREATE TABLE workers (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL REFERENCES tasks(id), generation INTEGER NOT NULL,
        adapter TEXT NOT NULL, status TEXT NOT NULL, lease_hash TEXT NOT NULL,
        lease_expires REAL NOT NULL, heartbeat_at REAL NOT NULL, activity_at REAL NOT NULL,
        pid INTEGER, identity TEXT, child_identity TEXT, capabilities TEXT NOT NULL,
        handoff TEXT, rotation_at REAL, error TEXT, anomaly_count INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, ended_at REAL, UNIQUE(task_id, generation))""",
        """CREATE TABLE experiments (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        name TEXT NOT NULL, spec TEXT NOT NULL, created_at REAL NOT NULL)""",
        """CREATE TABLE jobs (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        experiment_id TEXT NOT NULL REFERENCES experiments(id), logical_key TEXT NOT NULL,
        spec TEXT NOT NULL, spec_hash TEXT NOT NULL, status TEXT NOT NULL,
        authorized_worker TEXT NOT NULL REFERENCES workers(id), authorized_generation INTEGER NOT NULL,
        pid INTEGER, identity TEXT, observed_at REAL, metric TEXT NOT NULL DEFAULT '{}',
        exit_code INTEGER, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(task_id, logical_key))""",
        """CREATE TABLE executions (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id), identity TEXT NOT NULL,
        started_at REAL NOT NULL, finished_at REAL)""",
        """CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
        job_id TEXT REFERENCES jobs(id), path TEXT NOT NULL, kind TEXT NOT NULL,
        source TEXT NOT NULL, created_at REAL NOT NULL, UNIQUE(task_id, path))""",
        """CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT NOT NULL,
        source TEXT NOT NULL, actor TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL)""",
        "CREATE INDEX events_task_idx ON events(task_id, id)",
        "CREATE INDEX commands_status_idx ON commands(status, created_at)",
        "CREATE INDEX workers_task_idx ON workers(task_id, created_at)",
    ],
    2: ["ALTER TABLE jobs ADD COLUMN cancel_auth TEXT"],
}


class Store:
    def __init__(self, home: Path):
        self.home = Path(home).expanduser().absolute()
        self.path = self.home / "regulator.sqlite3"
        if self.home.is_symlink():
            raise ValueError("state directory cannot be a symlink")
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.home.stat().st_uid != os.getuid() or self.home.stat().st_mode & 0o077:
            raise PermissionError("state directory must be owned by this UID with mode 0700")
        if self.path.is_symlink():
            raise ValueError("database cannot be a symlink")
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise ValueError("database is newer than this controller; refusing downgrade")
                for number in range(version + 1, SCHEMA_VERSION + 1):
                    for statement in MIGRATIONS[number]:
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version={number}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(sql, args)]

    @staticmethod
    def event(
        conn: sqlite3.Connection, task_id: str | None, kind: str, source: str, actor: str, data: object
    ) -> None:
        conn.execute(
            "INSERT INTO events(task_id,kind,source,actor,data,created_at) VALUES(?,?,?,?,?,?)",
            (task_id, kind, source, actor, encode(data), time.time()),
        )

    def backup(self, destination: Path) -> Path:
        destination = Path(destination).absolute()
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        try:
            with self.connection() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                    if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("backup integrity check failed")
                finally:
                    target.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return destination
