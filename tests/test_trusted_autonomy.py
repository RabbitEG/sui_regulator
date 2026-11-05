from __future__ import annotations

import json
import time
from unittest.mock import patch

from sui_regulator import attempts
from sui_regulator.claude_adapter import build_command
from sui_regulator.models import DomainError
from sui_regulator.protocol import SuiProtocol
from sui_regulator.tool_hook import handle
from tests.helpers import ProcessTestCase


class TrustedAutonomyTests(ProcessTestCase):
    def owner(self):
        tid = self.submit(adapter="unavailable", recovery_mode="persistent", permission_mode="trusted")
        protocol = SuiProtocol(self.service)
        return tid, protocol, protocol.register(tid)

    def uncertain(self, tid, credentials, status="UNKNOWN"):
        with self.service.store.transaction() as conn:
            task = self.service._get(conn, "tasks", tid)
            attempts.start(conn, task, credentials, "lost-reply", "Bash", "old-op", "old-plan")
            conn.execute("UPDATE tool_attempts SET status=? WHERE id='lost-reply'", (status,))

    def test_unknown_and_running_do_not_disable_native_tools(self):
        tid, protocol, credentials = self.owner()
        self.uncertain(tid, credentials)
        for status in ["UNKNOWN", "RUNNING"]:
            with self.service.store.transaction() as conn:
                conn.execute("UPDATE tool_attempts SET status=? WHERE id='lost-reply'", (status,))
            for tool in ["Bash", "Edit", "Write"]:
                reply = handle(self.service, credentials, {
                    "hook_event_name": "PreToolUse", "tool_name": tool,
                    "tool_use_id": status + tool, "tool_input": {"command": "inspect and debug"},
                })
                self.assertNotEqual(reply.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")

    def test_proxy_reconciliation_allows_completion_without_fabricating_success(self):
        tid, protocol, credentials = self.owner()
        self.uncertain(tid, credentials)
        with self.assertRaises(DomainError):
            protocol.complete_task(credentials, "premature")
        result = protocol.reconcile_operation(credentials, "attempt", "lost-reply",
                                              "Inspected the exact execution target and process; no work remains.")
        self.assertEqual(result["observed_status"], "UNKNOWN")
        self.assertEqual(result["reconciliation"]["source"], "proxy-declared")
        protocol.complete_task(credentials, "verified and finished")
        task = self.service.get_task(tid)
        self.assertEqual(task["desired"], "COMPLETED")
        self.assertEqual(task["tool_attempts"][0]["status"], "UNKNOWN")
        self.assertTrue(task["completion"]["proxy_reconciliations"])

    def test_configuration_failures_retry_without_resume(self):
        for reason in ["AUTH_REQUIRED", "CAPABILITY_REQUIRED", "SESSION_ID_MISMATCH"]:
            tid, protocol, credentials = self.owner()
            task = self.service.get_task(tid)
            self.controller._retire(task, task["workers"][0], reason, True)
            after = self.service.get_task(tid)
            self.assertEqual(after["status"], "RETRY_BACKOFF")
            self.assertEqual(after["desired"], "ACTIVE")
            self.assertGreater(after["next_start_at"], time.time())

    def test_trusted_cli_bypasses_permissions_but_keeps_ownership_hooks(self):
        command = build_command(self.config, "synthetic", execution_profile="claude_code", permission_mode="trusted")
        self.assertEqual(command[command.index("--permission-mode") + 1], "bypassPermissions")
        hooks = json.loads(command[command.index("--settings") + 1])["hooks"]
        self.assertIn("PreToolUse", hooks)

    def test_hold_and_budget_still_take_precedence(self):
        tid, protocol, credentials = self.owner()
        self.service.command("hold_task", "tester", "hold", {}, tid)
        self.service.process_commands()
        with self.assertRaises(DomainError):
            protocol.reconcile_operation(credentials, "attempt", "missing", "inspected")
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")
        tid, protocol, credentials = self.owner()
        self.service.command("set_recovery_policy", "tester", "budget", {"max_proxy_seconds": .001}, tid)
        self.service.process_commands()
        from sui_regulator.recovery import monitor
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE workers SET created_at=created_at-10 WHERE id=?", (credentials.worker_id,))
        monitor(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")

    def test_trusted_incident_policy_retries_auth_and_session_conflict(self):
        from sui_regulator.failures.engine import DetectionEngine
        from sui_regulator.failures.models import Observation
        from sui_regulator.failures.persistence import apply_pending, publish
        for observation in [Observation(1, "api_error", {"code": 401, "terminal": True}),
                            Observation(1, "session_state", {"code": "SESSION_ID_MISMATCH", "mismatch": True})]:
            tid, protocol, credentials = self.owner()
            publish(self.service, credentials, DetectionEngine(session_id=credentials.worker_id).feed(observation))
            apply_pending(self.controller)
            self.assertEqual(self.service.get_task(tid)["status"], "RETRY_BACKOFF")

    def test_seven_virtual_days_keep_retrying_then_complete_without_operator(self):
        # Exercise real Controller transactions; elapsed time is simulated, not a seven-day soak.
        from sui_regulator.autonomy import reconcile_operation

        tid = self.submit(adapter="unavailable", recovery_mode="persistent", permission_mode="trusted")
        baseline = time.time()
        for hour in range(169):
            with patch("time.time", return_value=baseline + hour * 3600):
                self.controller.tick()
                task = self.service.get_task(tid)
                self.assertEqual(task["desired"], "ACTIVE")
                self.assertEqual(task["status"], "RETRY_BACKOFF")
                self.assertEqual(task["generation"], hour + 1)
                self.assertLessEqual(task["next_start_at"] - time.time(), self.config.recovery_backoff_max)
        with patch("time.time", return_value=baseline + 604800 + 3600):
            protocol = SuiProtocol(self.service)
            credentials = protocol.register(tid)
            self.uncertain(tid, credentials)
            job = protocol.launch_job(credentials, "one-experiment")
            self.assertEqual(protocol.launch_job(credentials, "one-experiment")["id"], job["id"])
            with self.service.store.transaction() as conn:
                conn.execute("UPDATE jobs SET status='UNKNOWN' WHERE id=?", (job["id"],))
            reconcile_operation(self.service, credentials, "job", job["id"], "Verified original target and archived results.")
            reconcile_operation(self.service, credentials, "attempt", "lost-reply", "Verified no pending remote work.")
            protocol.complete_task(credentials, "Task done after recovery", "Synthetic reconciliation evidence")
            task = self.service.get_task(tid)
            self.assertEqual(task["desired"], "COMPLETED")
            self.assertEqual(len(task["jobs"]), 1)
            self.assertFalse(task["completion"]["managed_jobs_terminal"])

    def test_permission_change_retries_framework_wait_but_preserves_hold(self):
        for desired, status, expected in [("ACTIVE", "WAITING_RESOURCE", "READY"),
                                          ("ACTIVE", "QUARANTINED", "READY"),
                                          ("HOLD", "HOLD", "HOLD")]:
            tid = self.submit(adapter="unavailable")
            with self.service.store.transaction() as conn:
                conn.execute("UPDATE tasks SET desired=?,status=? WHERE id=?", (desired, status, tid))
            receipt = self.service.command("set_recovery_policy", "tester", tid, {"permission_mode": "trusted"}, tid)
            self.service.process_commands()
            self.assertEqual(self.service.get_command(receipt["id"])["status"], "SUCCEEDED")
            self.assertEqual(self.service.get_task(tid)["status"], expected)

    def test_reconciliation_is_scoped_and_does_not_hide_running_work(self):
        tid, protocol, credentials = self.owner()
        self.uncertain(tid, credentials, "RUNNING")
        with self.assertRaises(DomainError):
            protocol.reconcile_operation(credentials, "attempt", "lost-reply", "still running")
        other = self.submit(adapter="unavailable", recovery_mode="persistent")
        guarded = protocol.register(other)
        with self.assertRaises(DomainError):
            protocol.reconcile_operation(guarded, "attempt", "lost-reply", "cannot change policy")
        another, _, other_owner = self.owner()
        with self.assertRaises(DomainError):
            protocol.reconcile_operation(other_owner, "attempt", "lost-reply", "different task")

    def test_schema7_upgrade_preserves_guarded_policy_and_unknown(self):
        from pathlib import Path
        from sui_regulator.config import Config
        from sui_regulator.service import Service

        home = Path(self.temp.name) / "v7"
        with patch("sui_regulator.store.SCHEMA_VERSION", 7):
            old = Service(Config(home))
            receipt = old.command("submit_task", "tester", "old", {"title": "old", "adapter": "unavailable"})
            old.process_commands()
            owner = SuiProtocol(old).register(receipt["task_id"])
            with old.store.transaction() as conn:
                task = old._get(conn, "tasks", owner.task_id)
                attempts.start(conn, task, owner, "legacy", "Bash", "op", "plan")
                conn.execute("UPDATE tool_attempts SET status='UNKNOWN' WHERE id='legacy'")
        new = Service(Config(home))
        task = new.get_task(owner.task_id)
        self.assertEqual(task["permission_mode"], "guarded")
        self.assertEqual(task["tool_attempts"][0]["status"], "UNKNOWN")
        self.assertIsNone(task["tool_attempts"][0]["reconciliation"])

