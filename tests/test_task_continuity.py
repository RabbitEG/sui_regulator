from __future__ import annotations

import time

from sui_regulator.models import DomainError
from sui_regulator.protocol import SuiProtocol
from sui_regulator.tool_hook import handle
from tests.helpers import ProcessTestCase


class TaskContinuityTests(ProcessTestCase):
    def owner(self):
        tid = self.submit(adapter="unavailable", recovery_mode="persistent")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        protocol.get_task_context(credentials)
        return tid, protocol, credentials

    def instruct(self, tid, text):
        receipt = self.service.command(
            "instruct_task", "tester", str(time.time_ns()), {"instruction": text}, tid
        )
        self.service.process_commands()
        return receipt["id"]

    def test_tool_completion_cannot_silently_refresh_unread_requirements(self):
        tid, protocol, credentials = self.owner()
        self.instruct(tid, "Additional authoritative work remains")
        handle(
            self.service,
            credentials,
            {
                "hook_event_name": "PreToolUse",
                "tool_name": "mcp__sui__complete_task",
                "tool_use_id": "old-completion",
            },
        )
        with self.assertRaises(DomainError):
            protocol.complete_task(credentials, "Old work finished")
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
        updated = protocol.get_task_context(credentials)
        protocol.acknowledge_instructions(credentials, [c["id"] for c in updated["instructions"]])
        protocol.complete_task(credentials, "New work also finished")
        self.assertEqual(self.service.get_task(tid)["desired"], "COMPLETED")

    def test_native_hook_offers_new_instruction_without_claiming_read_ack(self):
        tid, protocol, credentials = self.owner()
        cid = self.instruct(tid, "new operator requirement to inspect report B")
        result = handle(
            self.service,
            credentials,
            {"hook_event_name": "PreToolUse", "tool_name": "Read", "tool_use_id": "read-context"},
        )
        self.assertIn("new operator requirement", result["hookSpecificOutput"]["additionalContext"])
        receipt = self.service.get_command(cid)
        self.assertIsNotNone(receipt["delivery"][0]["offered_at"])
        self.assertIsNone(receipt["delivery"][0]["acknowledged_at"])
        protocol.acknowledge_instructions(credentials, [cid])
        self.assertIsNotNone(self.service.get_command(cid)["delivery"][0]["acknowledged_at"])
        self.assertFalse(self.service.get_command(cid)["result"]["analysis_executed"])

    def test_durable_wait_does_not_spawn_and_hold_wins_wakeup(self):
        tid, protocol, credentials = self.owner()
        protocol.wait_task(credentials, "Waiting for authorized resource", 60)
        self.controller.tick()
        task = self.service.get_task(tid)
        self.assertEqual(task["status"], "WAITING")
        self.assertIsNone(task["current_worker"])
        self.assertEqual(task["wait_state"]["responsible"], "candleholder")
        self.assertGreater(task["wait_state"]["next_check_at"], time.time())
        self.service.command("hold_task", "tester", "hold-wait", {}, tid)
        self.service.process_commands()
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE tasks SET next_start_at=0 WHERE id=?", (tid,))
        self.controller.tick()
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")

    def test_long_instruction_history_has_lossless_pages_and_cannot_be_silently_skipped(self):
        from sui_regulator.models import encode

        tid, protocol, credentials = self.owner()
        expected = []
        for number in range(30):
            text = f"Authoritative constraint {number}: " + "详细要求" * 1200
            expected.append(text)
            self.instruct(tid, text)
        context = protocol.get_task_context(credentials)
        self.assertTrue(context["instruction_page"]["has_more"])
        self.assertLess(len(encode(context).encode()), 450000)
        with self.assertRaises(DomainError):
            protocol.complete_task(credentials, "Unread history cannot be skipped")
        after, texts = 0, []
        while True:
            page = protocol.get_task_records(credentials, "instructions", after=after, limit=7)
            texts.extend(row["payload"]["instruction"] for row in page["items"])
            protocol.acknowledge_instructions(credentials, [c["id"] for c in page["items"]])
            after = page["next_after"]
            if not page["has_more"]:
                break
        self.assertEqual(texts, expected)
        protocol.complete_task(credentials, "All current requirements addressed")
        self.assertEqual(self.service.get_task(tid)["desired"], "COMPLETED")

    def test_wait_survives_controller_restart_and_new_instruction_wakes_same_task(self):
        from sui_regulator.controller import Controller

        tid, protocol, credentials = self.owner()
        protocol.wait_task(credentials, "Wait for dependency", 60)
        self.controller.__exit__(None, None, None)
        self.controller = Controller(self.config).__enter__()
        self.service = self.controller.service
        self.controller.tick()
        self.assertEqual(self.service.get_task(tid)["generation"], 1)
        self.instruct(tid, "Dependency is available; continue")
        self.controller.tick()
        self.assertEqual(self.service.get_task(tid)["generation"], 2)
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")

    def test_wait_wakes_on_timer_or_terminal_job_without_relaunch(self):
        from sui_regulator.waiting import wake

        for terminal_job in [False, True]:
            with self.subTest(terminal_job=terminal_job):
                tid, protocol, credentials = self.owner()
                job = protocol.launch_job(credentials, "original") if terminal_job else None
                protocol.wait_task(
                    credentials, "Wait for external observation", 60, job_id=job["id"] if job else None
                )
                with self.service.store.transaction() as conn:
                    if job:
                        conn.execute("UPDATE jobs SET status='FAILED' WHERE id=?", (job["id"],))
                    else:
                        conn.execute("UPDATE tasks SET next_start_at=0 WHERE id=?", (tid,))
                wake(self.service)
                task = self.service.get_task(tid)
                self.assertEqual(task["status"], "READY")
                self.assertEqual(task["desired"], "ACTIVE")
                self.assertEqual(task["wait_state"]["state"], "woken")
                self.assertEqual(len(task["jobs"]), 1 if job else 0)

    def test_same_failed_experiment_can_continue_or_complete_only_by_proxy_decision(self):
        for exit_code, complete in [(1, False), (1, True), (0, False)]:
            with self.subTest(exit_code=exit_code, complete=complete):
                tid, protocol, credentials = self.owner()
                job = protocol.launch_job(credentials, "experiment")
                with self.service.store.transaction() as conn:
                    conn.execute(
                        "UPDATE jobs SET status=?,exit_code=? WHERE id=?",
                        ("FAILED" if exit_code else "COMPLETED", exit_code, job["id"]),
                    )
                if complete:
                    protocol.complete_task(credentials, "Negative experimental result satisfies this request")
                else:
                    protocol.report_progress(
                        credentials, phase="ANALYSING", summary="Continue model-owned analysis"
                    )
                    self.controller.tick()
                task = self.service.get_task(tid)
                self.assertEqual(task["desired"], "COMPLETED" if complete else "ACTIVE")
                self.assertEqual(task["generation"], 1)

    def test_ack_is_idempotent_and_retired_generation_cannot_ack(self):
        tid, protocol, credentials = self.owner()
        cid = self.instruct(tid, "Read updated instructions")
        protocol.get_task_context(credentials)
        protocol.acknowledge_instructions(credentials, [cid])
        protocol.acknowledge_instructions(credentials, [cid])
        events = [e for e in self.service.events(tid) if e["kind"] == "INSTRUCTIONS_ACKNOWLEDGED"]
        self.assertEqual(len(events), 1)
        self.service.command("hold_task", "tester", "hold-ack", {}, tid)
        self.service.process_commands()
        with self.assertRaises(DomainError):
            protocol.acknowledge_instructions(credentials, [cid])

    def test_history_lookup_is_task_scoped_and_old_proxy_keeps_identity_view(self):
        from sui_regulator.records import page

        tid, protocol, credentials = self.owner()
        other, _, other_credentials = self.owner()
        row = page(self.service, tid, "proxies", record_id=credentials.worker_id)["items"][0]
        self.assertEqual(row["id"], credentials.worker_id)
        self.assertIn("health", row)
        self.assertNotIn("lease_hash", row)
        self.assertEqual(
            page(self.service, tid, "proxies", record_id=other_credentials.worker_id)["items"], []
        )
        with self.assertRaises(DomainError):
            page(self.service, tid, "proxies", limit=51)

    def test_file_completion_cannot_bypass_an_expired_lease_before_health_tick(self):
        from sui_regulator.completion import accept

        tid, protocol, credentials = self.owner()
        marker = protocol.get_task_context(credentials)["completion_contract"]["template"]
        marker.update(summary="Old lease completion", evidence="Ownership must still be current")
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE workers SET lease_expires=0 WHERE id=?", (credentials.worker_id,))
        with self.assertRaises(DomainError):
            accept(self.service, tid, credentials.worker_id, marker)
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")

    def test_queued_user_controls_and_requirements_precede_completion_acceptance(self):
        for kind in ("instruct_task", "hold_task", "cancel_task"):
            with self.subTest(kind=kind):
                tid, protocol, credentials = self.owner()
                command = self.service.command(
                    kind,
                    "tester",
                    "pending-" + kind,
                    {"instruction": "Additional work before completion"} if kind == "instruct_task" else {},
                    tid,
                )
                self.assertEqual(command["status"], "QUEUED")
                with self.assertRaises(DomainError):
                    protocol.complete_task(credentials, "Old completion in flight")
                self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
                self.service.process_commands()
                self.assertEqual(self.service.get_command(command["id"])["status"], "SUCCEEDED")
