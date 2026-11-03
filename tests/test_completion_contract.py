from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sui_regulator.completion import consume_markers, write_marker
from sui_regulator.processes import identity
from sui_regulator.protocol import SuiProtocol
from sui_regulator.task_prompt import task_prompt
from tests.helpers import ProcessTestCase


class CompletionContractTests(ProcessTestCase):
    def owner(self, **fields):
        tid = self.submit(adapter="unavailable", recovery_mode="persistent", **fields)
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        context = protocol.get_task_context(credentials)
        return tid, protocol, credentials, context

    def test_full_unicode_evidence_survives_manifest_write_and_acceptance(self):
        tid, protocol, credentials, context = self.owner()
        summary = "已完成任务并核验结果。" * 1400
        evidence = "实验记录与结果文件路径。" * 1300
        marker = {
            **context["completion_contract"]["template"],
            "summary": summary,
            "evidence": evidence,
        }
        path = write_marker(self.service, tid, credentials.worker_id, marker)
        self.assertGreater(path.stat().st_size, 65536)
        consume_markers(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["desired"], "COMPLETED")
        self.assertEqual(task["completion"]["summary"], summary)
        self.assertEqual(task["completion"]["evidence"], evidence)

    def test_native_manifest_is_consumed_and_failed_experiment_is_not_session_failure(self):
        tid, protocol, credentials, context = self.owner(objective="Record the negative experimental result")
        job = protocol.launch_job(credentials, "negative-result")
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE jobs SET status='FAILED' WHERE id=?", (job["id"],))
        marker = {
            **context["completion_contract"]["template"],
            "summary": "Negative result documented",
            "evidence": "Output and failed experiment analysis saved",
        }
        path = write_marker(self.service, tid, credentials.worker_id, marker)
        consume_markers(self.controller)
        task = self.service.get_task(tid)
        self.assertEqual(task["desired"], "COMPLETED")
        self.assertTrue(path.exists())
        self.assertFalse(task["completion"]["all_managed_jobs_successful"])
        self.assertEqual(task["completion"]["semantic_result"], "proxy-declared")

    def test_old_requirement_revision_does_not_complete_new_instructions(self):
        tid, protocol, credentials, context = self.owner()
        self.service.command(
            "instruct_task",
            "tester",
            "more-work",
            {"instruction": "Run another experiment before finishing"},
            tid,
        )
        self.service.process_commands()
        write_marker(self.service, tid, credentials.worker_id, context["completion_contract"]["template"])
        consume_markers(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
        self.assertIn("requirements changed", self.service.get_task(tid)["completion_receipts"][0]["error"])
        protocol.complete_task(credentials, "All updated requirements done", evidence="new result paths")
        self.assertEqual(self.service.get_task(tid)["desired"], "COMPLETED")

    def test_hold_and_stale_generation_cannot_be_overridden_by_marker(self):
        tid, protocol, old, context = self.owner()
        self.service.command("hold_task", "tester", "hold-marker", {}, tid)
        self.service.process_commands()
        write_marker(self.service, tid, old.worker_id, context["completion_contract"]["template"])
        consume_markers(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")
        self.service.command("resume_task", "tester", "resume-marker", {}, tid)
        self.service.process_commands()
        fresh = protocol.register(tid)
        write_marker(self.service, tid, fresh.worker_id, context["completion_contract"]["template"])
        consume_markers(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")

    def test_partial_file_and_plain_done_text_never_end_task(self):
        tid, protocol, credentials, context = self.owner()
        path = Path(context["completion_contract"]["path"])
        for text in ("done", '{"status":"completed"', '{"status":"completed"}'):
            path.write_text(text)
            consume_markers(self.controller)
            self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
        protocol.report_progress(credentials, phase="COMPLETED", summary="I am done")
        consume_markers(self.controller)
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")

    def test_prompt_keeps_operator_text_separate_from_historical_notes(self):
        objective = "等待实验完成。\n然后核验、debug、再实验。"
        tid, protocol, credentials, context = self.owner(
            objective=objective, completion_criteria="Report all agreed checks"
        )
        self.service.command(
            "instruct_task", "tester", "instruction", {"instruction": "Keep the original data"}, tid
        )
        self.service.process_commands()
        context = protocol.get_task_context(credentials)
        document = json.loads(task_prompt(context).split("\n", 1)[1])
        self.assertEqual(document["operator_request"]["objective"], objective)
        self.assertEqual(document["operator_updates"][0]["instruction"], "Keep the original data")
        self.assertIn("agent_notes_unverified", document)
        self.assertIn("completion_contract", document)
        self.assertNotIn("lease_token", task_prompt(context))

    def test_old_healthy_session_can_continue_slow_debugging_past_grace(self):
        tid, protocol, credentials, context = self.owner()
        # A live synthetic identity is read-only; never register the test process
        # for cleanup or give the Controller permission to signal it.
        import os

        protocol.handoff(
            credentials, {"summary": "An earlier checkpoint; not a response to the later request"}
        )
        with self.service.store.transaction() as conn:
            conn.execute(
                "UPDATE workers SET status='RUNNING',created_at=?,rotation_at=?,handoff_at=?,identity=? WHERE id=?",
                (
                    time.time() - 7200,
                    time.time() - 1800,
                    time.time() - 1801,
                    json.dumps(identity(os.getpid())),
                    credentials.worker_id,
                ),
            )
        self.controller.config = replace(self.config, max_session_age=0.01, rotation_grace=0.01)
        with patch.object(self.controller, "_stop") as stop:
            self.controller.tick()
            task = self.service.get_task(tid)
            self.assertEqual(task["generation"], 1)
            self.assertEqual(task["current_worker"], credentials.worker_id)
            stop.assert_not_called()
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE workers SET identity=NULL WHERE id=?", (credentials.worker_id,))
