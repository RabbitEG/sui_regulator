from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sui_regulator.claude_adapter import ClaudeStreamDetector
from sui_regulator.controller import Controller
from sui_regulator.models import digest
from sui_regulator.processes import identity
from sui_regulator.protocol import SuiProtocol
from sui_regulator.tool_hook import handle
from sui_regulator.wait_observation import ToolWaitMonitor
from tests.helpers import ProcessTestCase


class WaitObservationTests(ProcessTestCase):
    def owner(self):
        tid = self.submit(adapter="unavailable", permission_mode="trusted")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        task = protocol.claim_task(credentials)
        session = task["workers"][0]["session_id"]
        detector = ClaudeStreamDetector(session, permission_mode="trusted")
        monitor = ToolWaitMonitor(self.service, credentials, identity(os.getpid()))
        return tid, credentials, detector, monitor

    def start_call(self, credentials, detector, *, background=False, now=None):
        now = time.time() if now is None else now
        event = {"tool_name": "Bash", "tool_use_id": "sleep-call",
                 "tool_input": {"command": "sleep 120"}}
        handle(self.service, credentials, {**event, "hook_event_name": "PreToolUse"})
        detector.feed(json.dumps({"type": "assistant", "message": {"id": "waiting", "content": [
            {"type": "text", "text": "Waiting for the experiment."},
            {"type": "tool_use", "id": event["tool_use_id"], "name": "Bash", "input": event["tool_input"]},
        ]}}), now=now)
        if background:
            handle(self.service, credentials, {**event, "hook_event_name": "PostToolUse",
                                              "tool_response": {"backgroundTaskId": "bg-sleep"}})
            detector.feed(json.dumps({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "sleep-call", "content": "Background task started"},
            ]}}), now=now)
        return event

    def child(self):
        process = subprocess.Popen(["sleep", "120"])
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        self.addCleanup(cleanup)
        return process

    def test_live_sleep_is_observed_then_killed_sleep_loses_exemption_and_wakes_same_proxy(self):
        tid, credentials, detector, monitor = self.owner()
        self.start_call(credentials, detector, background=True)
        child = self.child()
        now = time.time()
        observation = monitor.poll(detector.normalizer, now=now)
        self.assertEqual(observation.live_tools, {"sleep-call"})
        self.assertTrue(observation.background_pending)
        # Hours without stdout are valid while the actual launcher still exists.
        observation = monitor.poll(detector.normalizer, now=now + 7200)
        self.assertEqual(observation.live_tools, {"sleep-call"})
        self.assertIsNone(detector.tick(now=now + 7200, tool_pending=observation.background_pending,
                                       live_tools=observation.live_tools).error)
        SuiProtocol(self.service).wait_task(credentials, "Wait for the existing native operation", 604800)
        child.kill()
        child.wait(timeout=3)
        monitor.poll(detector.normalizer, now=now + 7201)
        observation = monitor.poll(detector.normalizer, now=now + 7207)
        self.assertFalse(observation.background_pending)
        self.assertEqual(observation.ended_tools, {"sleep-call"})
        task = self.service.get_task(tid)
        self.assertEqual(task["tool_attempts"][0]["status"], "UNKNOWN")
        self.assertIsNone(task["tool_attempts"][0]["finished_at"])
        self.assertIsNone(detector.tick(now=now + 7207, ended_tools=observation.ended_tools).error)
        self.assertEqual(detector.tick(now=now + 7807, ended_tools=observation.ended_tools).error, "STREAM_STALLED")
        from sui_regulator.waiting import wake
        wake(self.service)
        task = self.service.get_task(tid)
        self.assertEqual(task["status"], "RUNNING")
        self.assertEqual(task["current_worker"], credentials.worker_id)
        self.assertEqual(task["generation"], 1)
        self.assertEqual(len([e for e in self.service.events(tid) if e["kind"] == "TOOL_WAIT_OBSERVATION_LOST"]), 1)

    def test_stale_running_receipt_is_not_renewed_by_wrapper_heartbeat(self):
        tid, credentials, detector, monitor = self.owner()
        self.start_call(credentials, detector, background=True)
        now = time.time()
        self.assertTrue(monitor.poll(detector.normalizer, now=now).background_pending)
        SuiProtocol(self.service).heartbeat(credentials)
        self.assertFalse(monitor.poll(detector.normalizer, now=now + 601).background_pending)
        self.assertEqual(self.service.get_task(tid)["tool_attempts"][0]["status"], "UNKNOWN")

    def test_late_hook_result_replaces_observation_loss_without_replaying(self):
        tid, credentials, detector, monitor = self.owner()
        event = self.start_call(credentials, detector)
        monitor.poll(detector.normalizer, now=time.time() + 601)
        self.assertEqual(self.service.get_task(tid)["tool_attempts"][0]["status"], "UNKNOWN")
        handle(self.service, credentials, {**event, "hook_event_name": "PostToolUseFailure", "error": "process exited"})
        task = self.service.get_task(tid)
        self.assertEqual(len(task["tool_attempts"]), 1)
        self.assertEqual(task["tool_attempts"][0]["status"], "FAILED")
        self.assertEqual(task["current_worker"], credentials.worker_id)
        self.assertEqual(task["desired"], "ACTIVE")

    def test_post_hook_can_end_pending_wire_call_when_tool_result_is_missing(self):
        _, credentials, detector, monitor = self.owner()
        event = self.start_call(credentials, detector)
        handle(self.service, credentials, {**event, "hook_event_name": "PostToolUse", "tool_response": "done"})
        observation = monitor.poll(detector.normalizer)
        self.assertEqual(observation.ended_tools, {"sleep-call"})
        detector.tick(ended_tools=observation.ended_tools)
        self.assertFalse(detector.normalizer.pending)

    def test_unrelated_child_and_frozen_launcher_cannot_renew_wait(self):
        tid, credentials, detector, monitor = self.owner()
        self.start_call(credentials, detector, background=True)
        unrelated = subprocess.Popen(["sleep", "119"])
        self.addCleanup(lambda: (unrelated.kill(), unrelated.wait(timeout=3)))
        self.assertFalse(monitor.poll(detector.normalizer).live_tools)
        child = self.child()
        self.assertEqual(monitor.poll(detector.normalizer).live_tools, {"sleep-call"})
        os.kill(child.pid, signal.SIGSTOP)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and monitor.poll(detector.normalizer).live_tools:
            time.sleep(.01)
        observation = monitor.poll(detector.normalizer, now=time.time() + 601)
        self.assertFalse(observation.background_pending)
        self.assertEqual(self.service.get_task(tid)["tool_attempts"][0]["status"], "UNKNOWN")

    def test_command_text_in_an_unrelated_process_argument_is_not_execution(self):
        _, credentials, detector, monitor = self.owner()
        self.start_call(credentials, detector)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "sleep 120"])
        self.addCleanup(lambda: (process.kill(), process.wait(timeout=3)))
        self.assertFalse(monitor.poll(detector.normalizer).live_tools)

    def test_duplicate_terminal_observation_does_not_reset_silence_clock(self):
        _, credentials, detector, _ = self.owner()
        self.start_call(credentials, detector, now=0)
        self.assertIsNone(detector.tick(now=30, ended_tools={"sleep-call"}).error)
        self.assertEqual(detector.tick(now=630, ended_tools={"sleep-call"}).error, "STREAM_STALLED")

    def test_lost_observation_cannot_overwrite_an_existing_terminal_receipt(self):
        tid, credentials, detector, monitor = self.owner()
        event = self.start_call(credentials, detector)
        handle(self.service, credentials, {**event, "hook_event_name": "PostToolUse", "tool_response": "done"})
        monitor.poll(detector.normalizer, now=time.time() + 7200)
        row = self.service.get_task(tid)["tool_attempts"][0]
        self.assertEqual(row["status"], "SUCCEEDED")
        self.assertEqual(row["id"], "native-" + digest([credentials.worker_id, "sleep-call"]))


class WaitRuntimeTests(ProcessTestCase):
    def setUp(self):
        super().setUp()
        binary = Path(self.temp.name) / "waiting-claude"
        fixture = Path(__file__).parent / "fixtures" / "waiting_claude.py"
        binary.write_text(f"#!{sys.executable}\n" + fixture.read_text())
        binary.chmod(0o700)
        self.config = replace(self.config, claude_binary=str(binary), startup_grace=1.5,
                              trusted_tool_observation_seconds=.2, trusted_response_silence_seconds=.4,
                              trusted_continuation_seconds=.05)
        (self.config.home / "config.toml").unlink()
        self.config.write()
        self.controller.__exit__(None, None, None)
        self.controller = Controller(self.config).__enter__()
        self.service = self.controller.service

    def test_lost_background_wait_continues_same_cli_without_replaying(self):
        for mode in ("registered", "background"):
            with self.subTest(mode=mode):
                marker = Path(self.temp.name) / (mode + ".json")
                with patch.dict(os.environ, {"SUI_WAIT_CASE": mode, "SUI_WAIT_MARKER": str(marker)}):
                    tid = self.submit(adapter="claude", permission_mode="trusted")
                    self.until(lambda: self.service.get_task(tid)["desired"] == "COMPLETED", timeout=8)
                result = json.loads(marker.read_text())
                task = self.service.get_task(tid)
                self.assertTrue(result["continued"])
                self.assertEqual(task["generation"], 1)
                self.assertEqual(task["workers"][0]["session_id"], result["session_id"])
                self.assertEqual(task["workers"][0]["child_identity"]["pid"], result["pid"])
                self.assertEqual(len(task["tool_attempts"]), 1)
                self.assertEqual(task["tool_attempts"][0]["status"], "UNKNOWN")
                self.assertTrue(task["tool_attempts"][0]["reconciliation"])
                self.assertEqual(result["wake_reason"],
                                 "native_operation_observation_changed" if mode == "registered" else None)

    def test_broken_foreground_wait_gets_response_grace_then_runtime_recovery(self):
        marker = Path(self.temp.name) / "foreground.json"
        with patch.dict(os.environ, {"SUI_WAIT_CASE": "foreground", "SUI_WAIT_MARKER": str(marker)}):
            tid = self.submit(adapter="claude", permission_mode="trusted")
            self.until(lambda: any(e["kind"] == "WORKER_RUNTIME_EXIT" for e in self.service.events(tid)),
                       timeout=8)
        result = json.loads(marker.read_text())
        self.assertTrue(result["launcher_exited"])
        task = self.service.get_task(tid)
        self.assertEqual(task["desired"], "ACTIVE")
        self.assertEqual(len(task["tool_attempts"]), 1)
        self.assertEqual(task["tool_attempts"][0]["status"], "UNKNOWN")
        exits = [e for e in self.service.events(tid) if e["kind"] == "WORKER_RUNTIME_EXIT"]
        self.assertEqual(exits[0]["data"]["error"], "STREAM_STALLED")
        self.assertGreaterEqual(exits[0]["created_at"] - result["exited_at"], .4)
