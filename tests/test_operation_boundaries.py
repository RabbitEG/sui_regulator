from __future__ import annotations

import json
import sys
from pathlib import Path

from sui_regulator import attempts
from sui_regulator.commands import run_command
from sui_regulator.models import DomainError
from sui_regulator.protocol import SuiProtocol
from sui_regulator.tool_hook import handle
from tests.helpers import ProcessTestCase
from tests.test_commands import write_registry


class OperationBoundaryTests(ProcessTestCase):
    def owner(self):
        tid = self.submit(adapter="unavailable", recovery_mode="persistent")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        worker = self.service.get_task(tid)["workers"][0]
        return tid, protocol, credentials, worker["session_id"]

    def pre(self, credentials):
        return handle(
            self.service,
            credentials,
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_use_id": "call-1",
                "tool_input": {"command": "synthetic submission"},
            },
        )

    def background(self, credentials):
        return handle(
            self.service,
            credentials,
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_use_id": "call-1",
                "tool_response": {"backgroundTaskId": "background-1"},
            },
        )

    def notification(self, credentials, session_id, status="completed", **extra):
        attempts.observe_background(
            self.service,
            credentials,
            session_id,
            {
                "type": "system",
                "subtype": "task_notification",
                "session_id": session_id,
                "task_id": "background-1",
                "tool_use_id": "call-1",
                "status": status,
                **extra,
            },
        )

    def receipt(self, tid):
        return self.service.get_task(tid)["tool_attempts"][0]

    def test_background_handle_is_running_and_terminal_notification_is_idempotent(self):
        tid, protocol, credentials, session = self.owner()
        self.pre(credentials)
        self.background(credentials)
        self.assertEqual(self.receipt(tid)["status"], "RUNNING")
        self.notification(credentials, session)
        self.notification(credentials, session)
        self.background(credentials)  # late Hook must not regress or emit a false status
        self.assertEqual(self.receipt(tid)["status"], "SUCCEEDED")
        facts = [
            e["data"]["status"]
            for e in self.service.events(tid)
            if e["kind"] in {"NATIVE_TOOL_RESULT", "NATIVE_BACKGROUND_RESULT"}
        ]
        self.assertEqual(facts, ["RUNNING", "SUCCEEDED"])

    def test_normal_stop_is_separate_and_restart_does_not_claim_known_exit(self):
        for reason, expected in [(None, "STOPPED"), ("worker_restart", "UNKNOWN")]:
            with self.subTest(reason=reason):
                tid, protocol, credentials, session = self.owner()
                self.pre(credentials)
                self.background(credentials)
                self.notification(credentials, session, "stopped", **({"reason": reason} if reason else {}))
                self.assertEqual(self.receipt(tid)["status"], expected)
                self.assertEqual(self.receipt(tid)["runtime"]["raw_status"], "stopped")

    def test_unknown_still_allows_actual_approved_diagnostics(self):
        tid, protocol, credentials, session = self.owner()
        self.pre(credentials)  # lost reply: STARTED does not mean not submitted
        registry = write_registry(
            Path(self.temp.name) / "commands.toml",
            "[command.inspect]\nargv = "
            + json.dumps([sys.executable, "-c", 'print("observed locally")'])
            + "\nread_only = true\n",
        )
        object.__setattr__(self.config, "command_registry", str(registry))
        self.assertEqual(
            run_command(self.service, credentials, "inspect")["stdout"].strip(), "observed locally"
        )
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
        with self.assertRaises(DomainError):
            protocol.complete_task(credentials, "not justified yet")

    def test_operator_approved_independent_work_runs_alongside_known_background_only(self):
        tid, protocol, credentials, session = self.owner()
        self.pre(credentials)
        self.background(credentials)
        registry = write_registry(
            Path(self.temp.name) / "commands.toml",
            "[command.independent]\nargv = "
            + json.dumps([sys.executable, "-c", 'print("independent work")'])
            + "\nallow_during_background = true\n",
        )
        object.__setattr__(self.config, "command_registry", str(registry))
        self.assertEqual(run_command(self.service, credentials, "independent")["exit_code"], 0)
        self.notification(credentials, session, "stopped", reason="worker_restart")
        with self.assertRaises(DomainError):
            run_command(self.service, credentials, "independent")

    def test_terminal_before_hook_wrong_handle_and_old_owner(self):
        tid, protocol, credentials, session = self.owner()
        self.pre(credentials)
        self.notification(credentials, session, "failed")
        self.background(credentials)
        self.assertEqual(self.receipt(tid)["status"], "FAILED")
        tid, protocol, credentials, session = self.owner()
        self.pre(credentials)
        self.background(credentials)
        self.notification(credentials, session, task_id="wrong-handle")
        self.assertEqual(self.receipt(tid)["status"], "RUNNING")
        self.service.command("hold_task", "tester", "old-owner", {}, tid)
        self.service.process_commands()
        attempts.reconcile_background(self.service)
        self.assertEqual(self.receipt(tid)["status"], "UNKNOWN")
        self.notification(credentials, session)
        self.assertEqual(self.receipt(tid)["status"], "SUCCEEDED")
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")
        with self.assertRaises(DomainError):
            protocol.report_progress(credentials, phase="WORKING", summary="revoked")

    def test_unknown_denies_native_mutation_but_keeps_read_tools(self):
        tid, protocol, credentials, session = self.owner()
        self.pre(credentials)
        for tool in ["Read", "Glob", "Grep"]:
            result = handle(
                self.service,
                credentials,
                {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_use_id": tool},
            )
            self.assertNotEqual(result.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        for tool in ["Edit", "Write", "Bash"]:
            result = handle(
                self.service,
                credentials,
                {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_use_id": tool},
            )
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_unknown_command_cannot_be_bypassed_with_a_new_job_key(self):
        tid, protocol, credentials, session = self.owner()
        self.pre(credentials)
        with self.assertRaisesRegex(DomainError, "unresolved"):
            protocol.launch_job(credentials, "new-side-effect")
        self.assertEqual(self.service.get_task(tid)["jobs"], [])

    def test_schema5_migration_preserves_unknown_and_permissions(self):
        from unittest.mock import patch

        from sui_regulator.config import Config
        from sui_regulator.service import Service

        home = Path(self.temp.name) / "old-release"
        with patch("sui_regulator.store.SCHEMA_VERSION", 5):
            old = Service(Config(home))
            command = old.command(
                "submit_task",
                "tester",
                "old-task",
                {
                    "title": "old",
                    "adapter": "unavailable",
                    "recovery_mode": "bounded",
                    "execution_profile": "managed",
                },
            )
            old.process_commands()
            credentials = SuiProtocol(old).register(command["task_id"])
            with old.store.transaction() as conn:
                task = old._get(conn, "tasks", command["task_id"])
                attempts.start(conn, task, credentials, "old-attempt", "Bash", "op", "approach")
                conn.execute("UPDATE tool_attempts SET status='UNKNOWN' WHERE id='old-attempt'")
        new = Service(Config(home))
        task = new.get_task(command["task_id"])
        self.assertEqual(task["execution_profile"], "managed")
        self.assertEqual(task["recovery_mode"], "bounded")
        self.assertEqual(task["tool_attempts"][0]["status"], "UNKNOWN")
        self.assertEqual(task["tool_attempts"][0]["runtime"], {})
