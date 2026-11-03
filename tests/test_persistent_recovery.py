from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

from sui_regulator import attempts
from sui_regulator.commands import run_command
from sui_regulator.config import Config
from sui_regulator.failures.engine import DetectionEngine
from sui_regulator.failures.models import Observation
from sui_regulator.failures.persistence import apply_pending, list_incidents, publish
from sui_regulator.models import DomainError
from sui_regulator.protocol import SuiProtocol
from sui_regulator.recovery import monitor
from tests.helpers import ProcessTestCase
from tests.test_commands import write_registry


class PersistentRecoveryTests(ProcessTestCase):
    def test_background_receipt_requires_matching_transport_identity(self):
        from sui_regulator.models import digest
        from sui_regulator.tool_hook import handle

        tid, protocol, credentials = self.manual()
        tool_id = "synthetic-background-call"
        handle(
            self.service,
            credentials,
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_use_id": tool_id,
                "tool_input": {"command": "synthetic"},
            },
        )
        handle(
            self.service,
            credentials,
            {
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_use_id": tool_id,
                "tool_response": {"backgroundTaskId": "bg-test"},
            },
        )
        session = self.service.get_task(tid)["workers"][0]["session_id"]
        message = {
            "type": "system",
            "subtype": "task_notification",
            "session_id": session,
            "task_id": "bg-test",
            "tool_use_id": tool_id,
            "status": "completed",
        }
        for change in (
            {"type": "assistant"},
            {"session_id": "wrong"},
            {"tool_use_id": "unrelated"},
            {"status": "running"},
            {"status": []},
        ):
            attempts.observe_background(self.service, credentials, session, {**message, **change})
            self.assertEqual(self.service.get_task(tid)["tool_attempts"][0]["status"], "RUNNING")
        attempts.observe_background(self.service, credentials, session, message)
        attempts.observe_background(self.service, credentials, session, message)
        receipt = self.service.get_task(tid)["tool_attempts"][0]
        self.assertEqual(receipt["id"], "native-" + digest([credentials.worker_id, tool_id]))
        self.assertEqual(receipt["status"], "SUCCEEDED")
        self.assertEqual(
            len([e for e in self.service.events(tid) if e["kind"] == "NATIVE_BACKGROUND_RESULT"]), 1
        )

    def test_terminal_incident_arriving_after_batch_snapshot_wins_health_race(self):
        tid, protocol, credentials = self.manual()
        engine = DetectionEngine(session_id=credentials.worker_id)
        incidents = engine.feed(
            Observation(1, "assistant_text", {"message_id": "synthetic", "text": "echo " * 40})
        )

        def publish_after_snapshot(controller):
            apply_pending(controller)
            with self.service.store.transaction() as conn:
                conn.execute(
                    "UPDATE workers SET status='EXITED',error='GENERATION_REPETITION' WHERE id=?",
                    (credentials.worker_id,),
                )
                publish(self.service, credentials, incidents, connection=conn)

        with patch("sui_regulator.controller.apply_pending", side_effect=publish_after_snapshot):
            self.controller.tick()
        self.assertEqual(self.service.get_task(tid)["current_worker"], credentials.worker_id)
        self.controller.tick()
        self.assertTrue(
            any(
                i["rule_id"] == "GEN-001" and i["status"] == "APPLIED"
                for i in list_incidents(self.service, tid)
            )
        )

    def setUp(self):
        super().setUp()
        object.__setattr__(self.config, "recovery_mode", "persistent")
        object.__setattr__(self.config, "recovery_backoff_base", 0.005)
        object.__setattr__(self.config, "recovery_backoff_max", 0.025)
        (self.config.home / "config.toml").unlink()
        self.config.write()

    def test_default_task_survives_thirty_proxy_start_failures(self):
        self.assertEqual(Config(Path(self.temp.name) / "default").recovery_mode, "persistent")
        task_id = self.submit(adapter="unavailable")
        self.until(
            lambda: (
                (task if task["generation"] >= 31 or task["status"] == "QUARANTINED" else None)
                if (task := self.service.get_task(task_id))
                else None
            )
        )
        task = self.service.get_task(task_id)
        self.assertNotEqual(task["status"], "QUARANTINED")
        self.assertEqual(task["generation"], 31)
        self.assertEqual(task["desired"], "ACTIVE")
        self.assertLessEqual(
            task["next_start_at"] - task["updated_at"], self.config.recovery_backoff_max + 0.001
        )

    def manual(self, **fields):
        tid = self.submit(adapter="unavailable", **fields)
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        return tid, protocol, credentials

    def test_unknown_job_blocks_new_execution_but_not_proxy_recovery(self):
        tid, protocol, credentials = self.manual()
        job = protocol.launch_job(credentials, "existing")
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE jobs SET status='UNKNOWN' WHERE id=?", (job["id"],))
        engine = DetectionEngine(session_id=credentials.worker_id)
        incidents = engine.feed(
            Observation(1, "assistant_text", {"message_id": "synthetic", "text": "echo " * 40})
        )
        publish(self.service, credentials, incidents)
        apply_pending(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["status"], "RETRY_BACKOFF")
        self.assertEqual(task["desired"], "ACTIVE")
        next_credentials = protocol.register(tid)
        self.assertEqual(protocol.launch_job(next_credentials, "existing")["id"], job["id"])
        with self.assertRaisesRegex(DomainError, "existing Job"):
            protocol.launch_job(next_credentials, "blind-replay")
        self.assertEqual(len(self.service.get_task(tid)["jobs"]), 1)

    def test_changed_self_reports_do_not_reset_no_progress_timer(self):
        tid, protocol, credentials = self.manual()
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE tasks SET created_at=? WHERE id=?", (time.time() - 901, tid))
        for index in range(3):
            protocol.report_progress(credentials, phase="WORKING", summary=f"claim {index}")
        monitor(self.controller)
        task = self.service.get_task(tid)
        self.assertGreater(task["recovery_state"]["no_progress_seconds"], 900)
        self.assertEqual(task["recovery_state"]["progress_stage"], 1)
        self.assertNotIn("last_verified_progress_at", task["recovery_state"])
        self.assertIsNone(task["workers"][0]["rotation_at"])
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE tasks SET created_at=? WHERE id=?", (time.time() - 3601, tid))
        monitor(self.controller)
        self.assertTrue(any(e["kind"] == "PROGRESS_REVIEW_ESCALATED" for e in self.service.events(tid)))

    def test_budget_pauses_and_policy_update_does_not_resume(self):
        tid, protocol, credentials = self.manual(max_proxy_seconds=1)
        with self.service.store.transaction() as conn:
            conn.execute(
                "UPDATE workers SET created_at=? WHERE id=?", (time.time() - 2, credentials.worker_id)
            )
        monitor(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")
        receipt = self.service.command(
            "set_recovery_policy", "tester", "more-budget", {"max_proxy_seconds": 10}, tid
        )
        self.service.process_commands()
        self.assertEqual(self.service.get_command(receipt["id"])["status"], "SUCCEEDED")
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")

    def test_bounded_mode_also_honors_explicit_runtime_budget(self):
        tid, protocol, credentials = self.manual(recovery_mode="bounded", max_proxy_seconds=1)
        with self.service.store.transaction() as conn:
            conn.execute(
                "UPDATE workers SET created_at=? WHERE id=?", (time.time() - 2, credentials.worker_id)
            )
        monitor(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["desired"], "HOLD")
        self.assertEqual(task["workers"][0]["status"], "REVOKED")
        self.assertIsNone(task["current_worker"])

    def test_repeated_experiment_failures_are_advisory_and_code_change_is_only_attempt(self):
        project = Path(self.temp.name) / "project"
        project.mkdir()
        script = project / "fail.py"
        script.write_text("print('same error');raise SystemExit(1)\n")
        registry = write_registry(
            Path(self.temp.name) / "commands.toml",
            "[command.test]\nargv = "
            + json.dumps([sys.executable, str(script)])
            + '\nprogress_kind = "test"\n',
        )
        object.__setattr__(self.config, "command_registry", str(registry))
        tid, protocol, credentials = self.manual(workspace=str(project))
        for _ in range(3):
            self.assertEqual(run_command(self.service, credentials, "test")["exit_code"], 1)
        self.assertEqual(run_command(self.service, credentials, "test")["exit_code"], 1)
        self.assertTrue(any(e["kind"] == "WORK_OBSERVATION" for e in self.service.events(tid)))
        for kind in ("hold_task", "resume_task"):
            self.service.command(kind, "tester", kind, {}, tid)
            self.service.process_commands()
        credentials = protocol.register(tid)
        self.assertEqual(run_command(self.service, credentials, "test")["exit_code"], 1)
        self.assertTrue(any(e["kind"] == "WORK_OBSERVATION" for e in self.service.events(tid)))
        for index in range(30):
            script.write_text(f"# attempted fix {index}\nprint('same error');raise SystemExit(1)\n")
            self.assertEqual(run_command(self.service, credentials, "test")["exit_code"], 1)
        monitor(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["generation"], 2)
        self.assertIn("last_runtime_evidence_at", task["recovery_state"])
        observed_at = task["recovery_state"]["last_runtime_evidence_at"]
        run_command(self.service, credentials, "test")
        monitor(self.controller)
        self.assertEqual(
            self.service.get_task(tid)["recovery_state"]["last_runtime_evidence_at"], observed_at
        )
        script.write_text("print('verified fix')\n")
        self.assertEqual(run_command(self.service, credentials, "test")["exit_code"], 0)
        monitor(self.controller)
        self.assertIn("last_verified_progress_at", self.service.get_task(tid)["recovery_state"])

    def test_persistent_unknown_anomaly_retries_instead_of_quarantine(self):
        tid, protocol, credentials = self.manual()
        engine = DetectionEngine(session_id=credentials.worker_id)
        incidents = []
        for at in range(3):
            incidents += engine.feed(Observation(at, "unknown_error", {"message": "synthetic unknown"}))
        publish(self.service, credentials, incidents)
        apply_pending(self.controller)
        self.assertEqual(self.service.get_task(tid)["status"], "RETRY_BACKOFF")
        self.assertTrue(
            any(
                i["payload"]["action"] == "quarantine" and i["action"] == "fresh_session"
                for i in list_incidents(self.service, tid)
            )
        )

    def test_unresolved_command_requires_operator_verification_on_hold(self):
        tid, protocol, credentials = self.manual()
        with self.service.store.transaction() as conn:
            task = self.service._get(conn, "tasks", tid)
            attempts.start(conn, task, credentials, "crash-window", "Bash", "operation", "approach")
            self.assertIn("unresolved", attempts.denial(conn, task, "new", "new"))
            self.assertIsNone(attempts.denial(conn, task, "read", "read", read_only=True))
        with self.assertRaisesRegex(DomainError, "unresolved outcome"):
            protocol.complete_task(credentials, "must not finish unresolved work")
        receipt = self.service.command(
            "resolve_attempt",
            "tester",
            "too-early",
            {"attempt_id": "crash-window", "evidence": "checked"},
            tid,
        )
        self.service.process_commands()
        self.assertEqual(self.service.get_command(receipt["id"])["status"], "REJECTED")
        self.service.command("hold_task", "tester", "hold-unresolved", {}, tid)
        self.service.process_commands()
        receipt = self.service.command(
            "resolve_attempt",
            "tester",
            "verified",
            {"attempt_id": "crash-window", "evidence": "inspected process and output; not running"},
            tid,
        )
        self.service.process_commands()
        self.assertEqual(self.service.get_command(receipt["id"])["status"], "SUCCEEDED")
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")
        with self.service.store.connection() as conn:
            task = self.service._get(conn, "tasks", tid)
            self.assertIsNone(attempts.denial(conn, task, "new", "new"))

    def test_rule_retry_window_defers_without_ending_task(self):
        tid, protocol, credentials = self.manual()
        for generation in range(4):
            if generation:
                credentials = protocol.register(tid)
            engine = DetectionEngine(session_id=credentials.worker_id)
            publish(
                self.service,
                credentials,
                engine.feed(
                    Observation(1, "assistant_text", {"message_id": "synthetic", "text": "echo " * 40})
                ),
            )
            apply_pending(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["desired"], "ACTIVE")
        self.assertEqual(task["status"], "RETRY_BACKOFF")
        self.assertGreater(task["next_start_at"], time.time() + 200)

    def test_hold_precedes_pending_persistent_recovery(self):
        tid, protocol, credentials = self.manual()
        engine = DetectionEngine(session_id=credentials.worker_id)
        publish(
            self.service,
            credentials,
            engine.feed(Observation(1, "assistant_text", {"message_id": "synthetic", "text": "echo " * 40})),
        )
        self.service.command("hold_task", "tester", "hold-before-recovery", {}, tid)
        self.service.process_commands()
        apply_pending(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")
        self.assertTrue(all(i["status"] == "IGNORED" for i in list_incidents(self.service, tid)))

    def test_authentication_waits_but_rate_limit_retries(self):
        for code, status in [("AUTH_REQUIRED", "WAITING_RESOURCE"), (429, "RETRY_BACKOFF")]:
            tid, protocol, credentials = self.manual()
            engine = DetectionEngine(session_id=credentials.worker_id)
            publish(
                self.service,
                credentials,
                engine.feed(Observation(1, "api_error", {"code": code, "terminal": True})),
            )
            apply_pending(self.controller)
            self.assertEqual(self.service.get_task(tid)["status"], status)

    def test_normal_turn_budget_rotates_without_counting_a_fault(self):
        tid, protocol, credentials = self.manual()
        engine = DetectionEngine(session_id=credentials.worker_id)
        publish(
            self.service,
            credentials,
            engine.feed(Observation(1, "api_error", {"code": "MAX_TURNS_EXHAUSTED", "terminal": True})),
        )
        apply_pending(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["status"], "READY")
        self.assertFalse(any(e["kind"] == "WORKER_FAILED" for e in self.service.events(tid)))
        self.assertEqual(task["workers"][0]["status"], "RETIRED")
