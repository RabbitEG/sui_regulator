from __future__ import annotations

import json
import time

from sui_regulator.claude_adapter import ClaudeStreamDetector, build_command
from sui_regulator.protocol import SuiProtocol
from tests.helpers import ProcessTestCase


class TrustedSessionLifetimeTests(ProcessTestCase):
    def detector(self):
        return ClaudeStreamDetector("session", permission_mode="trusted")

    def feed(self, detector, value, now):
        return detector.feed(json.dumps(value), now=now)

    def test_no_turn_cap_and_streaming_input_for_trusted(self):
        argv = build_command(
            self.config, "session", execution_profile="claude_code", permission_mode="trusted"
        )
        self.assertNotIn("--max-turns", argv)
        self.assertEqual(argv[argv.index("--input-format") + 1], "stream-json")

    def test_silence_needs_ten_minutes_and_tool_execution_is_exempt(self):
        detector = self.detector()
        self.feed(detector, {"type": "system", "subtype": "init", "session_id": "session"}, 0)
        self.assertIsNone(detector.tick(now=599).error)
        self.assertEqual(detector.tick(now=600).error, "STREAM_STALLED")
        detector = self.detector()
        self.feed(
            detector,
            {
                "type": "assistant",
                "message": {
                    "id": "call",
                    "content": [
                        {"type": "tool_use", "id": "sleep", "name": "Bash", "input": {"command": "sleep 600"}}
                    ],
                },
            },
            0,
        )
        self.assertIsNone(detector.tick(now=3600, live_tools={"sleep"}).error)
        self.feed(
            detector,
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "sleep", "content": "finished"}]
                },
            },
            3601,
        )
        self.assertIsNone(detector.tick(now=4200).error)
        self.assertEqual(detector.tick(now=4201).error, "STREAM_STALLED")

    def test_thinking_compact_and_known_background_refresh_or_exempt(self):
        detector = self.detector()
        self.feed(detector, {"type": "system", "subtype": "init", "session_id": "session"}, 0)
        self.feed(
            detector,
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "synthetic"},
                },
            },
            550,
        )
        self.assertIsNone(detector.tick(now=1100).error)
        self.feed(detector, {"type": "system", "subtype": "status", "status": "compacting"}, 1101)
        self.assertIsNone(detector.tick(now=1700).error)
        self.assertIsNone(detector.tick(now=1800, tool_pending=True).error)
        self.assertIsNone(detector.tick(now=1801).error)
        self.assertEqual(detector.tick(now=2401).error, "STREAM_STALLED")

    def test_automatic_cli_turn_after_background_result_restarts_silence_detection(self):
        detector = self.detector()
        self.feed(detector, {"type": "result", "subtype": "success"}, 0)
        self.assertIsNone(detector.tick(now=10000).error)
        self.feed(
            detector,
            {"type": "stream_event", "event": {"type": "message_start", "message": {"id": "resumed"}}},
            10001,
        )
        self.assertEqual(detector.tick(now=10601).error, "STREAM_STALLED")

    def test_missing_sleep_result_does_not_exempt_silence_forever(self):
        detector = self.detector()
        self.feed(detector, {"type": "assistant", "message": {"id": "waiting", "content": [
            {"type": "text", "text": "I will sleep for ten minutes and check the experiment."},
            {"type": "tool_use", "id": "sleep-call", "name": "Bash",
             "input": {"command": "sleep 600", "timeout": 600000}},
        ]}}, 0)
        self.assertIsNone(detector.tick(now=599).error)
        # The CLI transport never delivers a result; the old call is not perpetual proof of life.
        self.assertEqual(detector.tick(now=1800).error, "STREAM_STALLED")

    def test_waiting_words_do_not_grant_an_execution_exemption(self):
        detector = self.detector()
        self.feed(detector, {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "sleep 600; waiting until next week or an external event"},
        ]}}, 0)
        self.assertEqual(detector.tick(now=600).error, "STREAM_STALLED")

    def test_requested_timeout_does_not_replace_fresh_runtime_evidence(self):
        detector = self.detector()
        self.feed(detector, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "lost-call", "name": "Bash",
             "input": {"command": "sleep 604800", "timeout": 604800000}},
        ]}}, 0)
        self.assertEqual(detector.tick(now=1200).error, "STREAM_STALLED")

    def test_only_matching_runtime_progress_renews_tool_observation(self):
        detector = self.detector()
        self.feed(detector, {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "live-call", "name": "Bash", "input": {"command": "sleep 600"}},
        ]}}, 0)
        self.feed(detector, {"type": "system", "subtype": "task_progress", "session_id": "session",
                             "tool_use_id": "live-call"}, 550)
        self.assertIsNone(detector.tick(now=1100).error)
        self.feed(detector, {"type": "system", "subtype": "task_progress", "session_id": "other",
                             "tool_use_id": "live-call"}, 1150)
        self.feed(detector, {"type": "system", "subtype": "task_progress", "session_id": "session",
                             "tool_use_id": "other-call"}, 1300)
        self.assertEqual(detector.tick(now=1750).error, "STREAM_STALLED")

    def test_background_failure_ends_wait_exemption_but_is_not_a_session_failure(self):
        detector = self.detector()
        self.feed(detector, {"type": "assistant", "message": {"id": "waiting", "content": [
            {"type": "tool_use", "id": "sleep-call", "name": "Bash",
             "input": {"command": "sleep 600", "run_in_background": True}},
        ]}}, 0)
        # Even if the usual tool_result is lost, a matching terminal runtime event is evidence.
        ended = self.feed(detector, {"type": "system", "subtype": "task_notification",
                                   "session_id": "session", "tool_use_id": "sleep-call",
                                   "task_id": "background-sleep", "status": "failed"}, 30)
        self.assertIsNone(ended.error)
        self.assertIsNone(detector.tick(now=629).error)
        self.assertEqual(detector.tick(now=630).error, "STREAM_STALLED")

    def test_job_observation_loss_wakes_same_proxy_to_check_instead_of_waiting_a_week(self):
        from sui_regulator.waiting import wake

        tid = self.submit(adapter="unavailable", permission_mode="trusted")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        protocol.claim_task(credentials)
        job = protocol.launch_job(credentials, "existing-experiment")
        protocol.wait_task(credentials, "Wait until experiment ends", 604800, job_id=job["id"])
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE jobs SET status='UNKNOWN' WHERE id=?", (job["id"],))
        wake(self.service)
        task = self.service.get_task(tid)
        self.assertEqual(task["status"], "RUNNING")
        self.assertEqual(task["current_worker"], credentials.worker_id)
        self.assertEqual(task["generation"], 1)
        self.assertEqual(task["jobs"][0]["status"], "UNKNOWN")
        self.assertEqual(task["wait_state"]["wake_reason"], "job_observation_changed")

    def test_trusted_wait_keeps_owner_and_wakes_without_rotation(self):
        from sui_regulator.models import DomainError
        from sui_regulator.waiting import wake

        for trigger in ("timer", "instruct_task", "resume_task", "hold_task"):
            with self.subTest(trigger=trigger):
                tid = self.submit(adapter="unavailable", permission_mode="trusted")
                protocol = SuiProtocol(self.service)
                credentials = protocol.register(tid)
                protocol.claim_task(credentials)
                protocol.wait_task(credentials, "Waiting for experiment", 600)
                self.controller.tick()
                self.assertEqual(self.service.get_task(tid)["current_worker"], credentials.worker_id)
                protocol.heartbeat(credentials)
                if trigger == "timer":
                    with self.service.store.transaction() as conn:
                        conn.execute("UPDATE tasks SET next_start_at=0 WHERE id=?", (tid,))
                    wake(self.service)
                else:
                    payload = (
                        {"instruction": "Continue with the latest experiment"}
                        if trigger == "instruct_task"
                        else {}
                    )
                    self.service.command(trigger, "tester", "wake-" + trigger, payload, tid)
                    self.service.process_commands()
                task = self.service.get_task(tid)
                self.assertEqual(task["generation"], 1)
                if trigger == "hold_task":
                    self.assertEqual(task["desired"], "HOLD")
                    with self.assertRaises(DomainError):
                        protocol.heartbeat(credentials)
                else:
                    self.assertEqual(task["status"], "RUNNING")
                    self.assertEqual(task["current_worker"], credentials.worker_id)

    def test_waiting_trusted_owner_still_recovers_actual_process_failure(self):
        tid = self.submit(adapter="unavailable", permission_mode="trusted")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        protocol.claim_task(credentials)
        protocol.wait_task(credentials, "Waiting for experiment", 600)
        with self.service.store.transaction() as conn:
            conn.execute(
                "UPDATE workers SET status='EXITED',error='PROCESS_FAILURE' WHERE id=?",
                (credentials.worker_id,),
            )
        self.controller.tick()
        task = self.service.get_task(tid)
        self.assertNotEqual(task["current_worker"], credentials.worker_id)
        self.assertIn(task["status"], {"RETRY_BACKOFF", "READY", "RUNNING"})

    def test_age_and_heuristics_do_not_retire_healthy_trusted_session(self):
        import os

        from sui_regulator.failures.engine import DetectionEngine
        from sui_regulator.failures.models import Observation
        from sui_regulator.failures.persistence import apply_pending, publish
        from sui_regulator.models import encode
        from sui_regulator.processes import identity

        tid = self.submit(adapter="unavailable", permission_mode="trusted")
        credentials = SuiProtocol(self.service).register(tid)
        with self.service.store.transaction() as conn:
            conn.execute(
                "UPDATE workers SET created_at=?,identity=?,anomaly_count=100 WHERE id=?",
                (time.time() - 8 * 3600, encode(identity(os.getpid())), credentials.worker_id),
            )
        engine = DetectionEngine(session_id=credentials.worker_id)
        incidents = engine.feed(Observation(1, "assistant_text", {"text": "repeat " * 1000}))
        self.assertTrue(incidents)
        publish(self.service, credentials, incidents)
        apply_pending(self.controller)
        self.controller.tick()
        task = self.service.get_task(tid)
        self.assertEqual(task["current_worker"], credentials.worker_id)
        self.assertIsNone(task["workers"][0]["rotation_at"])
