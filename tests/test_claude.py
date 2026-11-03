from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sui_regulator.claude_adapter import ClaudeStreamDetector, classify_error
from sui_regulator.controller import Controller
from sui_regulator.failures.persistence import list_incidents
from tests.fake_api import FakeAnthropic
from tests.helpers import ProcessTestCase


class StreamTests(unittest.TestCase):
    def test_detector_distinguishes_malformed_and_final_error(self):
        detector = ClaudeStreamDetector()
        self.assertTrue(detector.feed("not json").anomaly)
        self.assertTrue(
            detector.feed(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [{"type": "tool_use", "name": "Bash", "input": {"command": ""}}]
                        },
                    }
                )
            ).anomaly
        )
        self.assertEqual(
            detector.feed(
                json.dumps({"type": "result", "is_error": True, "errors": ["503 overloaded"]})
            ).error,
            "TRANSPORT_FAILURE",
        )

    def test_repetition_detected_but_successful_polling_is_not_a_loop(self):
        detector = ClaudeStreamDetector()
        for number in range(10):
            event = detector.feed(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": str(number),
                                    "name": "get_task_context",
                                    "input": {},
                                }
                            ]
                        },
                    }
                )
            )
            self.assertFalse(event.anomaly)
            detector.feed(
                json.dumps(
                    {
                        "type": "user",
                        "message": {"content": [{"type": "tool_result", "tool_use_id": str(number)}]},
                    }
                )
            )
        for _ in range(4):
            event = detector.feed(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "This is a sufficiently long identical synthetic answer.",
                                }
                            ]
                        },
                    }
                )
            )
        self.assertTrue(event.anomaly)

    def test_error_classification(self):
        self.assertEqual(classify_error("401 invalid API key"), "AUTH_REQUIRED")
        self.assertEqual(classify_error("429 quota exceeded"), "RESOURCE_BLOCKED")
        self.assertEqual(classify_error("ECONNRESET mid-response"), "TRANSPORT_FAILURE")


class ClaudeContractTests(ProcessTestCase):
    def setUp(self):
        super().setUp()
        fixture = Path(__file__).parent / "fixtures" / "fake_claude.py"
        binary = Path(self.temp.name) / "fake-claude"
        binary.write_text(f"#!{sys.executable}\n" + fixture.read_text())
        binary.chmod(0o700)
        self.config = replace(self.config, claude_binary=str(binary), startup_grace=1.5)
        (self.config.home / "config.toml").unlink()
        self.config.write()
        self.controller.__exit__(None, None, None)
        self.controller = Controller(self.config).__enter__()
        self.service = self.controller.service

    def test_cli_adapter_mcp_job_wait_and_fresh_session_completion(self):
        tid = self.submit(adapter="claude", spec={"steps": 15, "interval": 0.02})
        self.until(lambda: self.service.get_task(tid)["status"] == "COMPLETED", timeout=10)
        task = self.service.get_task(tid)
        self.assertGreaterEqual(task["generation"], 2)
        self.assertEqual(len(task["jobs"]), 1)
        self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

        from sui_regulator.transcripts import read_transcript

        history = read_transcript(self.service, tid, task["workers"][0]["id"])
        self.assertEqual(history["availability"], "recorded")
        self.assertTrue(any(row["kind"] == "tool_call" for row in history["items"]))
        self.assertTrue(any(row["kind"] == "tool_result" for row in history["items"]))

    def test_T06_malformed_tools_cause_bounded_worker_recovery(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "malformed"}):
            tid = self.submit(adapter="claude")
            self.until(
                lambda: any(
                    event["kind"] == "WORKER_FAILED"
                    and event["data"]["reason"] == "MALFORMED_OR_REPEATED_TOOL_CALLS"
                    for event in self.service.events(tid)
                ),
                timeout=5,
            )

    def test_auth_failure_quarantines_without_repeated_api_attempts(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "auth"}):
            tid = self.submit(adapter="claude")
            self.until(lambda: self.service.get_task(tid)["status"] == "QUARANTINED")
            self.assertEqual(self.service.get_task(tid)["generation"], 1)

    def test_partial_stream_is_not_mistaken_for_success(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "partial"}):
            tid = self.submit(adapter="claude")
            self.until(
                lambda: any(event["kind"] == "WORKER_RUNTIME_EXIT" for event in self.service.events(tid))
            )
            events = [event for event in self.service.events(tid) if event["kind"] == "WORKER_RUNTIME_EXIT"]
            self.assertEqual(events[0]["data"]["error"], "TRUNCATED_STREAM")

    def test_stderr_backpressure_does_not_deadlock_stdout(self):
        with patch.dict(os.environ, {"SUI_FAKE_CLAUDE_MODE": "stderr"}):
            tid = self.submit(adapter="claude", spec={"steps": 3, "interval": 0.01})
            self.until(lambda: self.service.get_task(tid)["status"] == "COMPLETED", timeout=10)


@unittest.skipUnless(
    os.environ.get("SUI_TEST_REAL_CLAUDE") == "1" and shutil.which("claude"),
    "opt-in real installed CLI + loopback fake API only; no provider credentials",
)
class InstalledClaudeTests(ProcessTestCase):
    def test_native_background_failure_allows_verified_followup_in_same_session(self):
        project = Path(self.temp.name) / "background"
        project.mkdir()
        (project / "experiment.py").write_text(
            "from pathlib import Path\nimport time\ntime.sleep(.6)\nPath('observed.txt').write_text('negative result')\nraise SystemExit(1)\n"
        )
        tid, followup = None, False

        def steps(body, index):
            nonlocal followup
            if index == 0:
                return "get_task_context", {}
            if index == 1:
                return "Bash", {"command": f"{sys.executable} experiment.py", "run_in_background": True}
            receipts = self.service.get_task(tid)["tool_attempts"]
            if not receipts or any(r["status"] in {"STARTED", "UNKNOWN", "RUNNING"} for r in receipts):
                time.sleep(.05)
                return "get_task_context", {}
            if not followup:
                followup = True
                return "Bash", {"command": "printf 'followup completed' > followup.txt"}
            return "complete_task", {"summary": "Background result checked and followup finished", "evidence": "observed.txt and followup.txt"}

        with FakeAnthropic(steps=steps) as backend, patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": backend.url, "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
            "ANTHROPIC_AUTH_TOKEN": "", "CLAUDE_CODE_DISABLE_THINKING": "1", **NO_PROXIES,
        }):
            tid = self.submit(adapter="claude", workspace=str(project), execution_profile="claude_code", recovery_mode="persistent")
            self.until(lambda: self.service.get_task(tid)["desired"] == "COMPLETED", timeout=30)
            task = self.service.get_task(tid)
            self.assertEqual(task["generation"], 1)
            self.assertEqual((project / "followup.txt").read_text(), "followup completed")
            self.assertEqual([r["status"] for r in reversed(task["tool_attempts"])], ["FAILED", "SUCCEEDED"])
            self.assertTrue(any(e["kind"] == "NATIVE_BACKGROUND_RESULT" for e in self.service.events(tid)))

    def test_native_written_completion_marker_automatically_ends_task(self):
        from sui_regulator.completion import contract

        task_id = None
        def steps(body, index):
            if index == 0:
                return 'get_task_context', {}
            task = self.service.get_task(task_id)
            if not task['current_worker']:
                return 'get_task_context', {}
            worker = next(w for w in task['workers'] if w['id'] == task['current_worker'])
            document = contract(self.service, task, worker)
            marker = {**document['template'], 'summary': 'Synthetic completion manifest', 'evidence': 'Local marker acceptance test'}
            return 'Write', {'file_path': document['path'], 'content': json.dumps(marker)}

        with FakeAnthropic(steps=steps) as backend, patch.dict(os.environ, {
            'ANTHROPIC_BASE_URL': backend.url, 'ANTHROPIC_API_KEY': 'sk-ant-synthetic-local-only',
            'ANTHROPIC_AUTH_TOKEN': '', 'CLAUDE_CODE_DISABLE_THINKING': '1', **NO_PROXIES,
        }):
            task_id = self.submit(adapter='claude', execution_profile='claude_code', completion_criteria='Write completion marker')
            self.until(lambda: self.service.get_task(task_id)['desired'] == 'COMPLETED', timeout=30)
            task = self.service.get_task(task_id)
            self.assertEqual(task['completion']['summary'], 'Synthetic completion manifest')
            self.assertEqual(task['completion_receipts'][0]['status'], 'ACCEPTED')

    def test_native_cli_verifies_edits_and_runs_followup_experiment(self):
        self.controller.config = replace(self.config, max_session_age=.1, rotation_grace=.01)
        project = Path(self.temp.name) / "research"
        project.mkdir()
        program = project / "experiment.py"
        program.write_text("from pathlib import Path\nvalue = 1\nassert value == 2\nPath('result.txt').write_text('verified next experiment')\n")
        command = f"{sys.executable} experiment.py"
        waiting = project / "wait_existing.py"
        waiting.write_text("from pathlib import Path\nimport time\nwhile not Path('existing.done').exists(): time.sleep(.02)\nprint('existing experiment finished')\n")
        existing = subprocess.Popen([sys.executable, "-c", "from pathlib import Path;import time;time.sleep(2);Path('existing.done').write_text('one existing run')"], cwd=project)
        self.addCleanup(lambda: existing.wait(timeout=5))
        steps = [
            ("get_task_context", {}),
            ("Bash", {"command": f"{sys.executable} wait_existing.py", "description": "Wait for the existing experiment"}),
            ("Bash", {"command": command, "description": "Run experiment validation"}),
            ("Read", {"file_path": str(program)}),
            ("Edit", {"file_path": str(program), "old_string": "value = 1", "new_string": "value = 2"}),
            ("Bash", {"command": command, "description": "Verify fix and run followup experiment"}),
            ("complete_task", {"summary": "Validated and continued", "evidence": "result.txt produced after fixing assertion"}),
        ]
        with FakeAnthropic(steps=steps) as backend, patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": backend.url, "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
            "ANTHROPIC_AUTH_TOKEN": "", "CLAUDE_CODE_DISABLE_THINKING": "1", **NO_PROXIES,
        }):
            tid = self.submit(adapter="claude", workspace=str(project), execution_profile="claude_code",
                              objective="Verify experiment, debug failure and continue", completion_criteria="result.txt exists")
            self.until(lambda: self.service.get_task(tid)["desired"] == "COMPLETED", timeout=40)
            self.assertEqual((project / "result.txt").read_text(), "verified next experiment")
            self.assertIn("value = 2", program.read_text())
            self.assertGreaterEqual(len(backend.requests), 7)
            self.assertEqual(existing.wait(timeout=5), 0)
            self.assertEqual(self.service.get_task(tid)["generation"], 1)
            attempts = self.service.store.rows("SELECT status FROM tool_attempts WHERE task_id=? ORDER BY created_at", (tid,))
            self.assertEqual([row["status"] for row in attempts], ["SUCCEEDED", "FAILED", "SUCCEEDED"])

    def test_native_hook_leaves_experiment_retries_to_model(self):
        project = Path(self.temp.name) / "repeated"
        project.mkdir()
        program = project / "fail.py"
        program.write_text("from pathlib import Path\np=Path('attempt-count.txt')\np.write_text(str(int(p.read_text())+1 if p.exists() else 1))\nprint('fixed error');raise SystemExit(1)\n")
        steps = [("get_task_context", {})] + [("Bash", {"command": f"{sys.executable} fail.py", "description": "Attempt test"})] * 4 + [
            ("complete_task", {"summary": "synthetic experiment retry observation checked"})]
        with FakeAnthropic(steps=steps) as backend, patch.dict(os.environ, {
            "ANTHROPIC_BASE_URL": backend.url, "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
            "ANTHROPIC_AUTH_TOKEN": "", "CLAUDE_CODE_DISABLE_THINKING": "1", **NO_PROXIES,
        }):
            tid = self.submit(adapter="claude", workspace=str(project), execution_profile="claude_code", recovery_mode="persistent")
            self.until(lambda: self.service.get_task(tid)["desired"] == "COMPLETED", timeout=40)
            self.assertEqual((project / "attempt-count.txt").read_text(), "4")
            self.assertEqual(len(self.service.store.rows("SELECT * FROM tool_attempts WHERE task_id=?", (tid,))), 4)

    def test_installed_cli_repetition_is_interrupted_before_api_response_finishes(self):
        with (
            FakeAnthropic("degenerate") as backend,
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_BASE_URL": backend.url,
                    "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
                    "ANTHROPIC_AUTH_TOKEN": "",
                    **NO_PROXIES,
                },
            ),
        ):
            tid = self.submit(adapter="claude")
            self.until(
                lambda: any(
                    item["rule_id"] == "GEN-001" and item["status"] == "APPLIED"
                    for item in list_incidents(self.service, tid)
                ),
                timeout=15,
            )
            self.assertFalse(backend.stream_finished.is_set())
            incident = next(item for item in list_incidents(self.service, tid) if item["status"] == "APPLIED")
            self.assertEqual(incident["payload"]["evidence"]["signal"], "text_delta")
            self.assertEqual(incident["action"], "fresh_session")
            self.assertEqual(
                self.service.get_task(tid)["workers"][0]["stream_observation"], "text-delta-observed"
            )
            self.assertTrue(
                any(event["kind"] == "WORKER_STREAM_CAPABILITY" for event in self.service.events(tid))
            )
            # End this dedicated probe without allowing another synthetic response.
            self.service.command("hold_task", "tester", "end-native-stream-probe", {}, tid)
            self.controller.tick()

    def test_installed_cli_calls_our_mcp_against_loopback_api(self):
        with (
            FakeAnthropic() as backend,
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_BASE_URL": backend.url,
                    "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
                    "ANTHROPIC_AUTH_TOKEN": "",
                    "CLAUDE_CODE_DISABLE_THINKING": "1",
                    **NO_PROXIES,
                },
            ),
        ):
            tid = self.submit(adapter="claude")
            self.until(lambda: self.service.get_task(tid)["status"] == "COMPLETED", timeout=25)
            self.assertGreaterEqual(len(backend.requests), 3)
            task = self.service.get_task(tid)
            self.assertEqual(task["declared"]["summary"], "from loopback Messages API")

            from sui_regulator.transcripts import read_transcript

            transcript = read_transcript(self.service, tid, task["workers"][0]["id"])
            self.assertEqual(transcript["availability"], "recorded")
            self.assertTrue(any(row["kind"] == "tool_call" for row in transcript["items"]))
            self.assertNotIn("sk-ant-synthetic-local-only", json.dumps(transcript))

    def test_installed_cli_auth_error_is_a_runtime_error_not_a_task_success(self):
        with (
            FakeAnthropic("auth") as backend,
            patch.dict(
                os.environ,
                {
                    "ANTHROPIC_BASE_URL": backend.url,
                    "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
                    "ANTHROPIC_AUTH_TOKEN": "",
                    **NO_PROXIES,
                },
            ),
        ):
            tid = self.submit(adapter="claude")
            self.until(lambda: self.service.get_task(tid)["status"] == "QUARANTINED", timeout=25)
            self.assertEqual(self.service.get_task(tid)["generation"], 1)


NO_PROXIES = {
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "ALL_PROXY": "",
    "http_proxy": "",
    "https_proxy": "",
    "all_proxy": "",
    "NO_PROXY": "127.0.0.1,localhost,::1",
    "no_proxy": "127.0.0.1,localhost,::1",
}
