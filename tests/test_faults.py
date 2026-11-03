from __future__ import annotations

import errno
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from sui_regulator.adapters import UnavailableWorkerAdapter
from sui_regulator.config import Config
from sui_regulator.controller import Controller
from sui_regulator.gateway import Dispatcher, call
from sui_regulator.models import DomainError, encode
from sui_regulator.processes import alive, child_environment, detached, signal_owned
from sui_regulator.protocol import SuiProtocol
from sui_regulator.service import Service
from sui_regulator.store import MIGRATIONS
from tests.helpers import ProcessTestCase


class FaultTests(ProcessTestCase):
    def test_controller_startup_recovers_when_frozen_worker_holds_database_writer(self):
        class HoldingAdapter:
            def start(inner, service, credentials):
                return detached(
                    "tests.fixtures.lock_holder",
                    [str(service.config.home)],
                    input_data=SuiProtocol.credentials_json(credentials),
                )

        self.controller.adapters["unavailable"] = HoldingAdapter()
        tid = self.submit(adapter="unavailable")
        self.controller.tick()
        processes = self.controller.children.copy()
        self.addCleanup(lambda: [process.kill() for process in processes.values() if process.poll() is None])
        deadline = time.monotonic() + 3
        while not (self.config.home / "writer-held.marker").exists():
            if time.monotonic() > deadline:
                self.fail("dedicated worker did not reach injected write lock")
            time.sleep(0.01)
        self.controller.__exit__(None, None, None)
        from dataclasses import replace

        config = replace(self.config, max_session_age=0.01)
        self.controller = Controller(config).__enter__()
        self.controller.children = processes
        self.controller.tick()
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
        with self.service.store.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_T16_all_replacement_workers_unavailable_while_existing_job_finishes(self):
        tid = self.submit()
        self.until(
            lambda: (
                bool(self.service.get_task(tid)["jobs"])
                and self.service.get_task(tid)["jobs"][0]["status"] == "RUNNING"
            )
        )
        before = self.service.get_task(tid)
        self.controller.adapters["mock"] = UnavailableWorkerAdapter()
        signal_owned(before["workers"][-1]["identity"], signal.SIGKILL)
        self.until(lambda: self.service.get_task(tid)["status"] == "QUARANTINED")
        self.until(lambda: self.service.get_task(tid)["jobs"][0]["status"] == "COMPLETED")
        self.assertEqual(self.service.get_task(tid)["jobs"][0]["pid"], before["jobs"][0]["pid"])
        receipt = self.service.command(
            "instruct_task",
            "tester",
            "offline-instruction",
            {"instruction": "inspect results when available"},
            tid,
        )
        self.controller.tick()
        self.assertEqual(self.service.get_command(receipt["id"])["status"], "SUCCEEDED")

    def test_rotation_recovers_without_handoff_from_unresponsive_worker(self):
        tid = self.submit(spec={"steps": 100, "interval": 0.02})
        self.until(
            lambda: (
                bool(self.service.get_task(tid)["jobs"])
                and self.service.get_task(tid)["jobs"][0]["status"] == "RUNNING"
            )
        )
        old = self.service.get_task(tid)["workers"][-1]
        self.addCleanup(lambda: signal_owned(old["identity"], signal.SIGKILL))
        signal_owned(old["identity"], signal.SIGSTOP)
        from dataclasses import replace

        self.controller.config = replace(self.config, max_session_age=0.01)
        self.until(lambda: self.service.get_task(tid)["generation"] >= 2)
        self.until(lambda: not alive(old["identity"]))
        task = self.service.get_task(tid)
        self.assertIsNone(task["workers"][0]["handoff"])
        self.assertEqual(len(task["jobs"]), 1)

    def manual_job(self, **spec):
        tid = self.submit(adapter="unavailable")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        protocol.claim_task(credentials)
        job = protocol.launch_job(credentials, "fault-trial", {"steps": 30, "interval": 0.02, **spec})
        return tid, protocol, credentials, job

    def test_competing_claims_have_one_owner_and_generation(self):
        tid = self.submit(adapter="unavailable")

        def claim(_):
            try:
                return SuiProtocol(self.service).register(tid)
            except DomainError:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(claim, range(16)))
        self.assertEqual(sum(item is not None for item in claims), 1)
        self.assertEqual(self.service.get_task(tid)["generation"], 1)

    def test_expired_lease_cannot_be_renewed_or_write(self):
        tid, protocol, credentials, job = self.manual_job()
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE workers SET lease_expires=0 WHERE id=?", (credentials.worker_id,))
        for action in [
            lambda: protocol.heartbeat(credentials),
            lambda: protocol.cancel_job(credentials, job["id"]),
            lambda: protocol.launch_job(credentials, "new"),
        ]:
            with self.assertRaises(DomainError) as error:
                action()
            self.assertEqual(error.exception.code, "FENCED")
        self.assertEqual(self.service.get_task(tid)["generation"], 1)

    def test_T13_actual_spawn_then_lost_ack_reconciles_without_replay(self):
        _, _, _, job = self.manual_job()

        def spawn_and_lose_ack(*args, **kwargs):
            process = detached(*args, **kwargs)
            self.controller.runner.children[process.pid] = process
            raise ConnectionResetError("injected lost launch acknowledgement")

        with patch("sui_regulator.runner.detached", side_effect=spawn_and_lose_ack) as launch:
            self.controller.runner.dispatch(job["id"])
            self.until(lambda: self.service.get_job(job["id"])["status"] == "COMPLETED")
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

    def test_ambiguous_dispatch_without_process_is_unknown_and_not_retried(self):
        _, _, _, job = self.manual_job()
        with patch(
            "sui_regulator.runner.detached", side_effect=OSError(errno.EAGAIN, "injected ambiguity")
        ) as launch:
            self.controller.runner.dispatch(job["id"])
            for _ in range(3):
                self.controller.runner.reconcile()
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(self.service.get_job(job["id"])["status"], "UNKNOWN")
        self.assertEqual(self.service.store.rows("SELECT * FROM executions"), [])

    def test_dead_runner_is_unknown_not_automatically_restarted(self):
        _, _, _, job = self.manual_job()
        self.until(lambda: self.service.get_job(job["id"])["status"] == "RUNNING")
        running = self.service.get_job(job["id"])
        signal_owned(running["identity"], signal.SIGKILL)
        self.controller.runner.children[running["pid"]].wait(timeout=3)
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE jobs SET updated_at=0 WHERE id=?", (job["id"],))
        self.controller.runner.reconcile()
        self.assertEqual(self.service.get_job(job["id"])["status"], "UNKNOWN")
        self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

    def test_unknown_but_identified_live_execution_is_reconciled(self):
        _, _, _, job = self.manual_job()
        self.until(lambda: self.service.get_job(job["id"])["status"] == "RUNNING")
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE jobs SET status='UNKNOWN' WHERE id=?", (job["id"],))
        self.controller.runner.reconcile()
        self.assertEqual(self.service.get_job(job["id"])["status"], "RUNNING")

    def test_explicit_operator_job_cancel_after_task_cancel_is_idempotent(self):
        tid = self.submit()
        self.until(
            lambda: (
                bool(self.service.get_task(tid)["jobs"])
                and self.service.get_task(tid)["jobs"][0]["status"] == "RUNNING"
            )
        )
        job = self.service.get_task(tid)["jobs"][0]
        self.service.command("cancel_task", "tester", "cancel-task", {}, tid)
        self.controller.tick()
        self.assertTrue(alive(job["identity"]))
        first = self.service.command("cancel_job", "tester", "cancel-job", {"job_id": job["id"]}, tid)
        second = self.service.command("cancel_job", "tester", "cancel-job", {"job_id": job["id"]}, tid)
        self.assertEqual(first["id"], second["id"])
        self.until(lambda: self.service.get_job(job["id"])["status"] == "CANCELLED")
        self.assertEqual(self.service.get_task(tid)["desired"], "CANCELLED")

    def test_runner_rejects_stale_cancel_authorization_then_accepts_current(self):
        tid, protocol, old, job = self.manual_job(interval=0.1)
        self.until(lambda: self.service.get_job(job["id"])["status"] == "RUNNING")
        worker = self.service.store.rows("SELECT * FROM workers WHERE id=?", (old.worker_id,))[0]
        self.controller._retire(self.service.get_task(tid), worker, "injected transfer", False)
        current = protocol.register(tid)
        protocol.claim_task(current)
        # A previously queued message reaches the execution end after transfer.
        with self.service.store.transaction() as conn:
            conn.execute(
                "UPDATE jobs SET cancel_requested=1,cancel_auth=? WHERE id=?",
                (
                    encode({"source": "worker", "worker_id": old.worker_id, "generation": old.generation}),
                    job["id"],
                ),
            )
        self.until(lambda: any(event["kind"] == "RUNNER_CANCEL_FENCED" for event in self.service.events(tid)))
        self.assertEqual(self.service.get_job(job["id"])["status"], "RUNNING")
        protocol.cancel_job(current, job["id"])
        self.until(lambda: self.service.get_job(job["id"])["status"] == "CANCELLED")

    def test_T07_real_controller_sigkill_preserves_worker_and_job_on_restart(self):
        self.controller.__exit__(None, None, None)
        args = [sys.executable, "-m", "sui_regulator", "--home", str(self.config.home), "serve"]
        processes = []

        def start():
            process = subprocess.Popen(
                args,
                env=child_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            processes.append(process)
            return process

        def wait(predicate):
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                if result := predicate():
                    return result
                time.sleep(0.02)
            self.fail("external controller condition timed out")

        try:
            first_controller = start()
            tid = self.submit(spec={"steps": 150, "interval": 0.02})
            wait(
                lambda: (
                    self.service.get_task(tid)["jobs"]
                    and self.service.get_task(tid)["jobs"][0]["status"] == "RUNNING"
                )
            )
            before = self.service.get_task(tid)
            first_controller.kill()
            first_controller.wait(timeout=3)
            self.assertTrue(alive(before["jobs"][0]["identity"]))
            start()

            def queried():
                try:
                    return call(self.config.home, "yan.get_task", {"task_id": tid}).get("result")
                except OSError:
                    return None

            after = wait(queried)
            self.assertEqual(after["generation"], before["generation"])
            self.assertEqual(after["jobs"][0]["pid"], before["jobs"][0]["pid"])
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=3)
            self.controller = Controller(self.config).__enter__()

    def test_v1_database_migrates_without_losing_task_data(self):
        home = Path(self.temp.name) / "v1"
        home.mkdir(mode=0o700)
        with sqlite3.connect(home / "regulator.sqlite3") as conn:
            for statement in MIGRATIONS[1]:
                conn.execute(statement)
            conn.execute("PRAGMA user_version=1")
            conn.execute(
                "INSERT INTO tasks(id,title,objective,workspace,adapter,spec,created_at,updated_at) VALUES('task-v1','persisted','goal','/unused','mock','{}',0,0)"
            )
        upgraded = Service(Config(home))
        self.assertEqual(upgraded.get_task("task-v1")["title"], "persisted")
        with upgraded.store.connection() as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 4)
            self.assertIn("cancel_auth", [row[1] for row in conn.execute("PRAGMA table_info(jobs)")])

    def test_cross_task_job_and_artifact_path_escape_are_rejected(self):
        tid, protocol, credentials, job = self.manual_job()
        other = self.submit(adapter="unavailable")
        other_credentials = protocol.register(other)
        with self.assertRaises(DomainError):
            protocol.attach_job(other_credentials, job["id"])
        root = Path(self.service.get_task(tid)["workspace"])
        root.mkdir(parents=True)
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("synthetic private content")
        (root / "escape").symlink_to(outside)
        with self.assertRaises(DomainError):
            protocol.publish_artifact(credentials, "escape")

    def test_handoff_metadata_cannot_override_authoritative_identity(self):
        _, protocol, credentials, _ = self.manual_job()
        handoff = protocol.handoff(
            credentials, {"protocol_version": "forged", "generation": 9999, "next_action": "inspect"}
        )
        self.assertEqual(handoff["protocol_version"], "0.1")
        self.assertEqual(handoff["generation"], credentials.generation)

    def test_gateway_malformed_query_types_return_structured_errors(self):
        dispatch = Dispatcher(self.service)
        for params in [{}, {"task_id": []}, {"task_id": {"bad": "value"}}, {"task_id": 12}]:
            response = dispatch.dispatch(
                {"id": "test", "protocol_version": "0.1", "method": "yan.get_task", "params": params},
                "tester",
            )
            self.assertIn("error", response)
