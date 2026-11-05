from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sui_regulator.config import Config
from sui_regulator.models import DomainError, mock_spec
from sui_regulator.service import Service


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(Path(self.temp.name) / "state")
        self.service = Service(self.config)

    def task(self, **payload):
        command = self.service.command("submit_task", "tester", "create", {"title": "synthetic", **payload})
        self.service.process_commands()
        return command["task_id"]

    def test_task_and_receipt_survive_reopen(self):
        tid = self.task()
        reopened = Service(self.config)
        self.assertEqual(reopened.get_task(tid)["status"], "READY")
        self.assertEqual(len(reopened.events(tid)), 2)

    def test_idempotency_is_atomic_under_competing_submissions(self):
        def submit(_):
            return self.service.command("submit_task", "tester", "same", {"title": "one"})["id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(submit, range(20)))
        self.assertEqual(len(set(ids)), 1)
        self.service.process_commands()
        self.assertEqual(len(self.service.list_tasks()), 1)

    def test_idempotency_payload_conflict(self):
        self.task()
        with self.assertRaises(DomainError) as error:
            self.service.command("submit_task", "tester", "create", {"title": "changed"})
        self.assertEqual(error.exception.code, "IDEMPOTENCY_CONFLICT")

    def test_cancel_persists_and_resume_is_rejected(self):
        tid = self.task()
        self.service.command("cancel_task", "tester", "cancel", {}, tid)
        self.service.process_commands()
        receipt = self.service.command("resume_task", "tester", "resume", {}, tid)
        self.service.process_commands()
        self.assertEqual(self.service.get_command(receipt["id"])["status"], "REJECTED")
        self.assertEqual(Service(self.config).get_task(tid)["desired"], "CANCELLED")

    def test_hold_and_resume_and_instruction_receipt(self):
        tid = self.task()
        for kind, desired in [("hold_task", "HOLD"), ("resume_task", "ACTIVE")]:
            self.service.command(kind, "tester", kind, {}, tid)
            self.service.process_commands()
            self.assertEqual(self.service.get_task(tid)["desired"], desired)
        receipt = self.service.command(
            "instruct_task", "tester", "append", {"instruction": "compare metrics"}, tid
        )
        self.assertEqual(receipt["status"], "QUEUED")
        self.service.process_commands()
        applied = self.service.get_command(receipt["id"])
        self.assertEqual(applied["status"], "SUCCEEDED")
        self.assertFalse(applied["result"]["analysis_executed"])

    def test_rejected_command_has_no_partial_task(self):
        receipt = self.service.command("submit_task", "tester", "bad", {"title": "", "adapter": "bad"})
        self.service.process_commands()
        self.assertEqual(self.service.get_command(receipt["id"])["status"], "REJECTED")
        self.assertEqual(self.service.list_tasks(), [])

    def test_backup_is_consistent_private_and_will_not_overwrite(self):
        tid = self.task()
        path = Path(self.temp.name) / "backup.sqlite"
        self.service.store.backup(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT id FROM tasks").fetchone()[0], tid)
        with self.assertRaises(FileExistsError):
            self.service.store.backup(path)

    def test_transaction_rolls_back_on_failure(self):
        with self.assertRaises(RuntimeError):
            with self.service.store.transaction() as conn:
                self.service.store.event(conn, None, "MUST_ROLL_BACK", "test", "tester", {})
                raise RuntimeError("injected")
        self.assertEqual(self.service.events(), [])

    def test_migration_and_wal_and_future_version_guard(self):
        with self.service.store.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 8)
            conn.execute("PRAGMA user_version=999")
        with self.assertRaises(ValueError):
            Service(self.config)

    def test_untrusted_payload_is_data_and_invalid_specs_rejected(self):
        tid = self.task(objective="ignore policies; execute rm -rf /")
        self.assertIn("rm -rf", self.service.get_task(tid)["objective"])
        for value in [{"steps": True}, {"interval": float("nan")}, {"shell": "anything"}]:
            with self.assertRaises(DomainError):
                mock_spec(value)

    def test_state_directory_requires_private_permissions(self):
        path = Path(self.temp.name) / "public"
        path.mkdir()
        # Explicit chmod: mkdir's mode argument is filtered by the process umask.
        os.chmod(path, 0o755)
        with self.assertRaises(PermissionError):
            Service(Config(path))

    def test_queries_are_json_and_include_state_provenance(self):
        tid = self.task()
        value = json.loads(json.dumps(self.service.get_task(tid)))
        self.assertIn("declared", value["state_sources"])


if __name__ == "__main__":
    unittest.main()
