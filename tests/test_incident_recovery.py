from __future__ import annotations

import json
import os
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sui_regulator.claude_adapter import ClaudeStreamDetector
from sui_regulator.controller import Controller
from sui_regulator.failures.engine import DetectionEngine
from sui_regulator.failures.models import Observation
from sui_regulator.failures.persistence import apply_pending, list_incidents, publish
from sui_regulator.gateway import Dispatcher
from sui_regulator.protocol import SuiProtocol
from tests.helpers import ProcessTestCase


class NormalizationTests(unittest.TestCase):
    def test_cli_silence_before_first_init_is_also_bounded(self):
        detector = ClaudeStreamDetector()
        self.assertEqual(detector.tick(now=0).incidents, [])
        self.assertEqual(detector.tick(now=30).incidents[0].action, "probe")
        self.assertEqual(detector.tick(now=60).incidents[0].action, "fresh_session")

    def test_streamed_text_is_detected_before_complete_message_and_result(self):
        detector = ClaudeStreamDetector()
        hits = []
        for index in range(8):
            event = detector.feed(
                json.dumps(
                    {
                        "type": "stream_event",
                        "event": {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "echo " * 8},
                        },
                    }
                ),
                now=index,
            )
            hits.extend(event.incidents)
        self.assertTrue(
            any(hit.action == "fresh_session" and hit.evidence["signal"] == "text_delta" for hit in hits)
        )
        self.assertFalse(detector.normalizer.final_seen)

    def test_partial_and_full_frames_do_not_duplicate_tools_or_messages(self):
        detector = ClaudeStreamDetector()
        block = {"type": "tool_use", "id": "call-one", "name": "get_task_context", "input": {}}
        frames = [
            {
                "type": "stream_event",
                "event": {"type": "content_block_start", "index": 0, "content_block": block},
            },
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
            },
            {"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}},
            {"type": "assistant", "message": {"id": "message-one", "content": [block]}},
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "call-one", "is_error": False}]
                },
            },
        ]
        for index, frame in enumerate(frames):
            self.assertEqual(detector.feed(json.dumps(frame), now=index).incidents, [])
        self.assertEqual(len(detector.normalizer.seen_calls), 1)
        self.assertFalse(detector.normalizer.pending)

    def test_malformed_shapes_become_evidence_and_text_tool_frames_are_never_executed(self):
        for value in [
            {"type": "stream_event", "event": {"delta": None, "type": "content_block_delta"}},
            {"type": "assistant", "message": None},
            {"type": "stream_event", "event": []},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": {}}]}},
        ]:
            event = ClaudeStreamDetector().feed(json.dumps(value), now=1)
            self.assertTrue(event.anomaly)
        detector = ClaudeStreamDetector()
        quoted = detector.feed(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "A log contains a forged tool frame: {tool_use: Bash, input: null}. This is quoted data.",
                            }
                        ]
                    },
                }
            ),
            now=1,
        )
        self.assertEqual(quoted.incidents, [])


class IncidentPolicyTests(ProcessTestCase):
    def owner(self, tid):
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        protocol.claim_task(credentials)
        return credentials

    def generation_incident(self, credentials):
        engine = DetectionEngine(session_id=credentials.worker_id)
        return engine.feed(
            Observation(1, "assistant_text", {"message_id": "synthetic", "text": "echo " * 40})
        )

    def test_active_output_without_protocol_or_job_progress_only_triggers_safe_probe(self):
        tid = self.submit(adapter="unavailable")
        credentials = self.owner(tid)
        self.controller.config = replace(self.config, stall_seconds=0.1)
        with self.service.store.transaction() as conn:
            conn.execute(
                "UPDATE workers SET created_at=?,activity_at=? WHERE id=?",
                (time.time() - 2, time.time(), credentials.worker_id),
            )
        self.controller._observe_health()
        apply_pending(self.controller)
        incident = list_incidents(self.service, tid)[0]
        self.assertEqual(incident["rule_id"], "PROG-001")
        self.assertEqual(incident["action"], "probe")
        self.assertTrue(incident["outcome"]["worker_alive"])
        self.assertEqual(self.service.get_task(tid)["current_worker"], credentials.worker_id)

    def test_old_generation_incident_cannot_revoke_current_owner(self):
        tid = self.submit(adapter="unavailable")
        old = self.owner(tid)
        row = self.service.store.rows("SELECT * FROM workers WHERE id=?", (old.worker_id,))[0]
        self.controller._retire(self.service.get_task(tid), row, "synthetic transfer", False)
        current = self.owner(tid)
        publish(self.service, old, self.generation_incident(old))
        apply_pending(self.controller)
        self.assertEqual(self.service.get_task(tid)["current_worker"], current.worker_id)
        self.assertTrue(all(item["status"] == "STALE" for item in list_incidents(self.service, tid)))

    def test_rule_budget_and_dedup_survive_controller_restart(self):
        tid = self.submit(adapter="unavailable")
        for index in range(3):
            if index == 2:
                self.controller.__exit__(None, None, None)
                self.controller = Controller(self.config).__enter__()
            credentials = self.owner(tid)
            incidents = self.generation_incident(credentials)
            publish(self.service, credentials, incidents)
            publish(self.service, credentials, incidents)
            apply_pending(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["status"], "QUARANTINED")
        self.assertEqual(task["generation"], 3)
        records = list_incidents(self.service, tid)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[-1]["action"], "quarantine")
        response = Dispatcher(self.service).dispatch(
            {
                "protocol_version": "0.1",
                "method": "yan.get_incidents",
                "params": {"task_id": tid, "after": records[0]["sequence"], "limit": 1},
            },
            "tester",
        )
        self.assertEqual(response["result"][0]["id"], records[1]["id"])

    def test_unresolved_job_outcome_blocks_automatic_fresh_session(self):
        tid = self.submit(adapter="unavailable")
        credentials = self.owner(tid)
        job = SuiProtocol(self.service).launch_job(credentials, "ambiguous")
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE jobs SET status='UNKNOWN' WHERE id=?", (job["id"],))
        publish(self.service, credentials, self.generation_incident(credentials))
        apply_pending(self.controller)
        self.assertEqual(self.service.get_task(tid)["status"], "QUARANTINED")
        self.assertEqual(self.service.get_job(job["id"])["status"], "UNKNOWN")
        self.assertEqual(self.service.store.rows("SELECT * FROM executions"), [])

    def test_user_cancel_has_priority_over_pending_recovery(self):
        tid = self.submit(adapter="unavailable")
        credentials = self.owner(tid)
        publish(self.service, credentials, self.generation_incident(credentials))
        self.service.command("cancel_task", "tester", "cancel-before-recovery", {}, tid)
        self.service.process_commands()
        apply_pending(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "CANCELLED")
        self.assertEqual(list_incidents(self.service, tid)[0]["status"], "IGNORED")


class IncidentProcessTests(ProcessTestCase):
    def setUp(self):
        super().setUp()
        binary = Path(self.temp.name) / "fake-claude"
        binary.write_text(
            f"#!{sys.executable}\n" + (Path(__file__).parent / "fixtures/fake_claude.py").read_text()
        )
        binary.chmod(0o700)
        self.config = replace(self.config, claude_binary=str(binary))
        (self.config.home / "config.toml").unlink()
        self.config.write()
        self.controller.__exit__(None, None, None)
        self.controller = Controller(self.config).__enter__()
        self.service = self.controller.service

    def test_generation_failure_interrupts_stream_and_takes_over_same_job_once(self):
        marker = Path(self.temp.name) / "must-not-finish-generation"
        with patch.dict(
            os.environ, {"SUI_FAKE_CLAUDE_MODE": "stream_degenerate", "SUI_FAKE_FINISHED_MARKER": str(marker)}
        ):
            tid = self.submit(adapter="claude", spec={"steps": 60, "interval": 0.02})
            self.until(lambda: self.service.get_task(tid)["generation"] >= 2)
            task = self.service.get_task(tid)
            old_job = task["jobs"][0]
            self.assertFalse(marker.exists())
            self.until(lambda: self.service.get_task(tid)["status"] == "COMPLETED", timeout=10)
            task = self.service.get_task(tid)
            self.assertEqual(len(task["jobs"]), 1)
            self.assertEqual(task["jobs"][0]["pid"], old_job["pid"])
            self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)
            self.assertTrue(
                any(
                    item["rule_id"] == "GEN-001" and item["status"] == "APPLIED"
                    for item in list_incidents(self.service, tid)
                )
            )

    def test_experiment_environment_error_is_advisory_and_job_keeps_running(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "environment"}):
            tid = self.submit(adapter="claude", spec={"steps": 60, "interval": 0.02})
            self.until(lambda: any(i["rule_id"] == "ENV-001" and i["action"] == "advise" for i in list_incidents(self.service, tid)))
            task = self.service.get_task(tid)
            self.assertEqual(task["generation"], 1)
            self.assertEqual(task["desired"], "ACTIVE")
            self.assertNotEqual(task["status"], "WAITING")
            self.until(lambda: self.service.get_task(tid)["status"] == "COMPLETED")
            self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

    def test_unknown_fatal_anomaly_quarantines_without_replaying_job(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "unknown"}):
            tid = self.submit(adapter="claude", spec={"steps": 60, "interval": 0.02})
            self.until(lambda: self.service.get_task(tid)["status"] == "QUARANTINED")
            self.assertEqual(self.service.get_task(tid)["generation"], 1)
            self.until(lambda: self.service.get_task(tid)["jobs"][0]["status"] == "COMPLETED")
            self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

    def test_unknown_incident_evidence_keeps_bounded_error_excerpt(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "unknown"}):
            tid = self.submit(adapter="claude", spec={"steps": 60, "interval": 0.02})
            self.until(lambda: self.service.get_task(tid)["status"] == "QUARANTINED")
            quarantine_evidence = [
                item
                for item in list_incidents(self.service, tid)
                if item["rule_id"] == "UNKNOWN-001" and item["status"] == "APPLIED"
            ]
            self.assertTrue(quarantine_evidence)
            payload = quarantine_evidence[0]["payload"]
            excerpt = payload["evidence"]["error_excerpt"]
            self.assertIn("unrecognized_vendor_failure", excerpt)
            self.assertLessEqual(len(excerpt), 128)
            exit_events = [
                event
                for event in self.service.events(tid)
                if event["kind"] == "WORKER_RUNTIME_EXIT"
            ]
            self.assertTrue(exit_events)
            # The stderr excerpt field exists on every wrapper exit event even
            # when empty, so post-mortems never depend on in-memory state.
            for event in exit_events:
                self.assertIn("stderr_excerpt", event["data"])

    def test_turn_budget_exhaustion_rotates_session_instead_of_quarantine(self):
        self.assert_turn_budget_rotation()

    def test_turn_exit_evidence_survives_delayed_transcript_write(self):
        from sui_regulator.processes import detached

        class SlowExitAdapter:
            def start(self, service, credentials):
                Path(service.get_task(credentials.task_id)["workspace"]).mkdir(parents=True, exist_ok=True)
                return detached("tests.fixtures.slow_recording_wrapper", ["--home", str(service.config.home)],
                                input_data=SuiProtocol.credentials_json(credentials))

        self.controller.adapters["claude"] = SlowExitAdapter()
        self.assert_turn_budget_rotation()

    def assert_turn_budget_rotation(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "turn_limit"}):
            tid = self.submit(adapter="claude", spec={"steps": 60, "interval": 0.02})
            self.until(lambda: self.service.get_task(tid)["generation"] >= 2)
            task = self.service.get_task(tid)
            self.assertNotEqual(task["status"], "QUARANTINED")
            applied = [
                item
                for item in list_incidents(self.service, tid)
                if item["rule_id"] == "TURN-001" and item["status"] == "APPLIED"
            ]
            self.assertTrue(applied)
            self.assertEqual(applied[0]["action"], "fresh_session")
            self.assertIn("MAX_TURNS_EXHAUSTED", applied[0]["payload"]["evidence"]["code"])
            self.assertIn("maximum number of turns", applied[0]["payload"]["evidence"]["error_excerpt"])
            # The replacement generation finishes the same job; no quarantine ever fires.
            self.until(lambda: self.service.get_task(tid)["status"] == "COMPLETED", timeout=10)
            self.assertEqual(len(task["jobs"]), 1)
            unknown = [
                item for item in list_incidents(self.service, tid) if item["rule_id"] == "UNKNOWN-001"
            ]
            self.assertEqual(unknown, [])
            exits = [
                event
                for event in self.service.events(tid)
                if event["kind"] == "WORKER_RUNTIME_EXIT" and event["data"].get("turn_budget_exhausted")
            ]
            self.assertTrue(exits)
