from __future__ import annotations

import errno
import json
import signal
import tempfile
import time
import unittest
from pathlib import Path

from sui_regulator.config import Config
from sui_regulator.controller import Controller
from sui_regulator.processes import signal_owned


class ProcessTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sui-test-")
        self.config = Config(
            Path(self.temp.name) / "state",
            heartbeat_seconds=0.03,
            lease_seconds=2,
            restart_cooldown=0.025,
            startup_grace=0.8,
            tick_seconds=0.015,
            rotation_grace=0.1,
            recovery_mode="bounded",  # Existing tests exercise the conservative policy explicitly.
        )
        self.config.write()
        self.controller = Controller(self.config).__enter__()
        self.service = self.controller.service
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for table in ["workers", "jobs"]:
            for row in self.service.store.rows(
                f"SELECT identity,{'child_identity' if table == 'workers' else 'NULL AS child_identity'} FROM {table}"
            ):
                for key in ["child_identity", "identity"]:
                    signal_owned(json.loads(row[key]) if row[key] else None, signal.SIGKILL)
        for process in [*self.controller.children.values(), *self.controller.runner.children.values()]:
            process.wait(timeout=5)
        self.controller.__exit__(None, None, None)
        # CLI PostToolUse helpers can finish just after their owning CLI exits.
        # Retry only the temporary-directory removal race, with a strict bound.
        deadline = time.monotonic() + 2
        while True:
            try:
                self.temp.cleanup()
                break
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def submit(self, **payload):
        receipt = self.service.command(
            "submit_task",
            "tester",
            f"task-{time.time_ns()}",
            {"title": "Synthetic task", "spec": {"steps": 50, "interval": 0.02}, **payload},
        )
        self.service.process_commands()
        return receipt["task_id"]

    def until(self, predicate, timeout=7):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.controller.tick()
            value = predicate()
            if value:
                return value
            time.sleep(self.config.tick_seconds)
        self.fail("condition not reached before timeout")
