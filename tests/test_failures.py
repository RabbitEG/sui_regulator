from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from sui_regulator.failures.engine import DetectionEngine
from sui_regulator.failures.models import Observation
from sui_regulator.failures.registry import FailureRegistry, Rule
from sui_regulator.failures.replay import evaluate, replay
from sui_regulator.processes import child_environment

CORPUS = Path(__file__).parent / "failure_corpus"


class FailureReplayTests(unittest.TestCase):
    def test_entire_corpus_detects_faults_without_interrupting_normal_trajectories(self):
        report = evaluate(CORPUS)
        self.assertTrue(report["passed"], [result for result in report["results"] if not result["passed"]])
        self.assertEqual(report["case_recall"], 1.0)
        self.assertEqual(report["false_positive_rate"], 0)
        self.assertIsNone(report["executed_recovery_correctness"])

    def test_each_rule_declares_effective_positive_and_negative_fixtures(self):
        for rule in FailureRegistry().rules:
            for path in rule.fixtures["positive"]:
                result = replay(CORPUS / path)
                self.assertTrue(result["passed"])
                self.assertIn(rule.id, [expected["rule_id"] for expected in result["expectations"]])
            for path in rule.fixtures["negative"]:
                self.assertEqual(replay(CORPUS / path)["incidents"], [])

    def test_new_versioned_rule_can_reuse_algorithm_without_controller_changes(self):
        original = asdict(next(rule for rule in FailureRegistry().rules if rule.id == "GEN-001"))
        original.update(id="GEN-099", version=2, name="Synthetic custom threshold")
        original["detector"]["config"].update(warn_after=16, act_after=24)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / "GEN-099.json").write_text(json.dumps(original))
            registry = FailureRegistry(path)
            hits = DetectionEngine(registry).feed(
                Observation(1, "assistant_text", {"text": "synthetic " * 24})
            )
            self.assertEqual(hits[0].rule_id, "GEN-099")
            self.assertEqual(hits[0].rule_version, 2)
            self.assertEqual(hits[0].action, "fresh_session")

    def test_invalid_plugin_and_unbounded_recovery_are_rejected(self):
        for change in [
            {"detector": {"type": "os.system", "config": {}}},
            {"recovery": {"action": "shell", "max_attempts": 999}},
        ]:
            value = asdict(FailureRegistry().rules[0])
            value.update(change)
            with self.assertRaises(ValueError):
                Rule.parse(value)

    def test_stream_window_and_evidence_are_bounded(self):
        engine = DetectionEngine()
        incidents = []
        for index in range(100):
            incidents.extend(
                engine.feed(
                    Observation(index, "text_delta", {"message_id": "bounded", "text": "synthetic " * 100})
                )
            )
        self.assertLessEqual(len(engine.state["GEN-001"]["text"]), 8192)
        self.assertTrue(incidents)
        self.assertNotIn("synthetic synthetic", json.dumps([item.to_dict() for item in incidents]))
        self.assertLessEqual(len(incidents), 2)

    def test_replay_uses_explicit_time_and_rejects_backwards_clock(self):
        engine = DetectionEngine()
        engine.feed(Observation(10, "note"))
        with self.assertRaises(ValueError):
            engine.feed(Observation(9, "note"))
        with self.assertRaises(ValueError):
            Observation(float("nan"), "note")

    def test_cli_evaluation_is_offline_and_machine_readable(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "sui_regulator",
                    "--home",
                    str(Path(temporary) / "must-not-create"),
                    "failures",
                    "evaluate",
                    str(CORPUS),
                    "--summary",
                ],
                env=child_environment(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue(json.loads(result.stdout)["passed"])
            self.assertFalse((Path(temporary) / "must-not-create").exists())
