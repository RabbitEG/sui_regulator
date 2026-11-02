from __future__ import annotations

import json
import signal
import subprocess
import sys
from dataclasses import replace

from sui_regulator.controller import Controller
from sui_regulator.models import DomainError
from sui_regulator.processes import alive, child_environment, detached, signal_owned
from sui_regulator.protocol import SuiProtocol
from tests.helpers import ProcessTestCase


class LifecycleTests(ProcessTestCase):
    def test_T03_T04_worker_sigkill_preserves_job_and_replacement_attaches(self):
        tid = self.submit()
        task = self.until(
            lambda: (
                (t if t["jobs"] and t["jobs"][0]["status"] == "RUNNING" else None)
                if (t := self.service.get_task(tid))
                else None
            )
        )
        first, job = task["workers"][-1], task["jobs"][0]
        self.assertTrue(signal_owned(first["identity"], signal.SIGKILL))
        task = self.until(
            lambda: (t if t["generation"] >= 2 else None) if (t := self.service.get_task(tid)) else None
        )
        self.assertTrue(alive(job["identity"]))
        task = self.until(
            lambda: (t if t["status"] == "COMPLETED" else None) if (t := self.service.get_task(tid)) else None
        )
        self.assertEqual(len(task["jobs"]), 1)
        self.assertEqual(task["jobs"][0]["id"], job["id"])
        self.assertEqual(task["jobs"][0]["pid"], job["pid"])
        self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

    def test_T05_proactive_rotation_handoff_preserves_running_job(self):
        self.controller.config = replace(self.config, max_session_age=0.15)
        tid = self.submit()
        self.until(lambda: self.service.get_task(tid)["generation"] >= 2)
        task = self.service.get_task(tid)
        self.assertTrue(task["workers"][0]["handoff"])
        self.assertEqual(task["workers"][0]["status"], "RETIRED")
        self.assertEqual(len(task["jobs"]), 1)

    def test_T07_controller_reopen_recovers_same_worker_and_job(self):
        tid = self.submit()
        self.until(
            lambda: (
                bool(self.service.get_task(tid)["jobs"])
                and self.service.get_task(tid)["jobs"][0]["status"] == "RUNNING"
            )
        )
        before = self.service.get_task(tid)
        old_children, old_jobs = self.controller.children, self.controller.runner.children
        self.controller.__exit__(None, None, None)
        self.controller = Controller(self.config).__enter__()
        self.controller.children, self.controller.runner.children = old_children, old_jobs
        self.controller.tick()
        after = self.service.get_task(tid)
        self.assertEqual(after["generation"], before["generation"])
        self.assertEqual(after["jobs"][0]["pid"], before["jobs"][0]["pid"])

    def test_T09_old_worker_fenced_and_runner_rechecks_authority(self):
        tid = self.submit(adapter="unavailable")
        protocol = SuiProtocol(self.service)
        old = protocol.register(tid)
        protocol.claim_task(old)
        job = protocol.launch_job(old, "once", {"steps": 5, "interval": 0.02})
        worker = self.service.store.rows("SELECT * FROM workers WHERE id=?", (old.worker_id,))[0]
        self.controller._retire(self.service.get_task(tid), worker, "injected failure", True)
        protocol.register(tid)
        with self.assertRaises(DomainError) as rejected:
            protocol.report_progress(old, phase="RUNNING", summary="stale")
        self.assertEqual(rejected.exception.code, "FENCED")
        self.controller.runner.dispatch(job["id"])
        self.until(lambda: self.service.get_job(job["id"])["status"] == "BLOCKED")
        self.assertEqual(self.service.store.rows("SELECT * FROM executions"), [])

    def test_T10_unavailable_resources_block_without_restart_storm(self):
        tid = self.submit(spec={"steps": 10, "interval": 0.02, "resource_available": False})
        self.until(lambda: self.service.get_task(tid)["declared"].get("phase") == "WAITING_RESOURCE")
        self.assertEqual(self.service.get_task(tid)["jobs"][0]["status"], "BLOCKED")
        self.assertEqual(self.service.get_task(tid)["generation"], 1)
        self.assertEqual(self.service.store.rows("SELECT * FROM executions"), [])

    def test_T11_cancel_prevents_recovery_and_retains_running_job(self):
        tid = self.submit()
        self.until(
            lambda: (
                bool(self.service.get_task(tid)["jobs"])
                and self.service.get_task(tid)["jobs"][0]["status"] == "RUNNING"
            )
        )
        before = self.service.get_task(tid)
        self.service.command("cancel_task", "tester", "cancel", {}, tid)
        self.controller.tick()
        after = self.service.get_task(tid)
        self.assertEqual(after["status"], "CANCELLED")
        self.assertTrue(alive(after["jobs"][0]["identity"]))
        for _ in range(3):
            self.controller.tick()
        self.assertEqual(self.service.get_task(tid)["generation"], before["generation"])

    def test_T12_worker_completion_claim_does_not_change_observation(self):
        tid = self.submit(adapter="unavailable")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        job = protocol.launch_job(credentials, "work", {"steps": 20, "interval": 0.02})
        protocol.report_progress(credentials, phase="COMPLETED", summary="I say it finished")
        self.assertEqual(self.service.get_job(job["id"])["status"], "QUEUED")
        with self.assertRaises(DomainError):
            protocol.complete_task(credentials, "finished")

    def test_T13_lost_launch_receipt_and_duplicate_dispatch_do_not_duplicate_execution(self):
        tid = self.submit(adapter="unavailable")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        job = protocol.launch_job(credentials, "work", {"steps": 20, "interval": 0.02})
        self.controller.runner.dispatch(job["id"])
        # Simulate reply loss: no reply is used; retry same logical request and spawn a duplicate supervisor.
        self.assertEqual(
            protocol.launch_job(credentials, "work", {"steps": 20, "interval": 0.02})["id"], job["id"]
        )
        duplicate = detached(
            "sui_regulator.job_process", ["--home", str(self.config.home), "--job-id", job["id"]]
        )
        self.controller.runner.children[duplicate.pid] = duplicate
        self.until(lambda: self.service.get_job(job["id"])["status"] == "COMPLETED")
        self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

    def test_T16_unavailable_workers_quarantine_budget_survives_controller_reopen(self):
        tid = self.submit(adapter="unavailable")
        self.until(lambda: self.service.get_task(tid)["status"] == "QUARANTINED")
        self.assertEqual(self.service.get_task(tid)["generation"], self.config.max_restarts)
        self.controller.__exit__(None, None, None)
        self.controller = Controller(self.config).__enter__()
        self.controller.tick()
        self.assertEqual(self.service.get_task(tid)["status"], "QUARANTINED")
        self.assertTrue(self.service.events(tid))

    def test_no_second_controller_and_no_unknown_process_cleanup(self):
        with self.assertRaises(BlockingIOError):
            Controller(self.config).__enter__()
        stranger = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(10)"])
        try:
            self.controller.tick()
            self.assertIsNone(stranger.poll())
        finally:
            stranger.terminate()
            stranger.wait(timeout=5)

    def test_cli_demo_outputs_machine_parseable_success(self):
        result = subprocess.run(
            [sys.executable, "-m", "sui_regulator", "demo"],
            env=child_environment(),
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(all(json.loads(result.stdout)["checks"].values()))
