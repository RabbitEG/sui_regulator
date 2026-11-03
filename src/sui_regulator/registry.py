"""Private process ownership evidence for watchdog use while SQLite is locked.

This registry never grants task ownership; generation and leases remain in SQLite.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path


class ProxyRegistry:
    def __init__(self, home: Path):
        if home.is_symlink():
            raise ValueError("state directory cannot be a symlink")
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if home.stat().st_uid != os.getuid() or home.stat().st_mode & 0o077:
            raise PermissionError("state directory must be private and owned by this UID")
        self.directory = home / "worker-registry"
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if self.directory.is_symlink():
            raise ValueError("proxy registry cannot be a symlink")

    def _path(self, worker_id: str) -> Path:
        if not re.fullmatch(r"sui-[0-9a-f]{32}", worker_id):
            raise ValueError("invalid proxy registry ID")
        return self.directory / f"{worker_id}.json"

    def read(self, worker_id: str) -> dict | None:
        path = self._path(worker_id)
        try:
            if path.is_symlink() or path.stat().st_uid != os.getuid() or path.stat().st_size > 4096:
                return None
            value = json.loads(path.read_text())
            if value.get("worker_id") == worker_id and value.get("origin") == "sui-regulator-worker":
                return value
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def _write(self, worker_id: str, value: dict) -> None:
        fd, temporary = tempfile.mkstemp(prefix=".worker-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(value, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path(worker_id))
        finally:
            Path(temporary).unlink(missing_ok=True)

    def publish(
        self,
        worker_id: str,
        task_id: str,
        generation: int,
        process_identity: dict | None,
        created_at: float | None = None,
    ) -> None:
        if process_identity is None:
            return
        previous = self.read(worker_id)
        self._write(
            worker_id,
            {
                "origin": "sui-regulator-worker",
                "worker_id": worker_id,
                "task_id": task_id,
                "generation": generation,
                "identity": process_identity,
                "created_at": previous["created_at"] if previous else created_at or time.time(),
                "heartbeat_at": time.time(),
            },
        )

    def heartbeat(self, worker_id: str) -> None:
        value = self.read(worker_id)
        if value:
            value["heartbeat_at"] = time.time()
            self._write(worker_id, value)

    def records(self) -> list[dict]:
        result = []
        for path in self.directory.glob("sui-*.json"):
            try:
                value = self.read(path.stem)
                if value:
                    result.append(value)
            except ValueError:
                continue
        return result


# The on-disk worker-registry path remains compatible with v0.3.
WorkerRegistry = ProxyRegistry
