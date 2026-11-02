from __future__ import annotations

import json
import signal
import tempfile
import time
from pathlib import Path

from .config import Config
from .controller import Controller
from .processes import signal_owned


def run_demo() -> dict:
    with tempfile.TemporaryDirectory(prefix="sui-demo-") as temporary:
        config = Config(
            Path(temporary) / "state",
            heartbeat_seconds=0.05,
            lease_seconds=2,
            restart_cooldown=0.05,
            startup_grace=2,
            tick_seconds=0.02,
        )
        config.write()
        with Controller(config) as controller:
            service = controller.service
            receipt = service.command(
                "submit_task",
                "demo",
                "single-task",
                {"title": "Synthetic recovery drill", "spec": {"steps": 60, "interval": 0.025}},
            )
            task_id = receipt["task_id"]
            killed, job_id, first_generation, job_pid = False, None, None, None
            deadline = time.monotonic() + 15
            try:
                while time.monotonic() < deadline:
                    controller.tick()
                    task = service.get_task(task_id)
                    running = [job for job in task["jobs"] if job["status"] == "RUNNING"]
                    if running and not killed:
                        worker = task["workers"][-1]
                        first_generation, job_id, job_pid = (
                            worker["generation"],
                            running[0]["id"],
                            running[0]["pid"],
                        )
                        killed = signal_owned(worker["identity"], signal.SIGKILL)
                    if killed and task["status"] == "COMPLETED":
                        executions = service.store.rows("SELECT * FROM executions WHERE job_id=?", (job_id,))
                        events = service.events(task_id)
                        checks = {
                            "worker_sigkill": killed,
                            "replacement_generation": task["generation"] > first_generation,
                            "single_job": len(task["jobs"]) == 1,
                            "single_execution": len(executions) == 1,
                            "same_runner_pid": task["jobs"][0]["pid"] == job_pid,
                            "job_completed": task["jobs"][0]["status"] == "COMPLETED",
                        }
                        if not all(checks.values()):
                            raise AssertionError(checks)
                        return {
                            "synthetic": True,
                            "task_id": task_id,
                            "job_id": job_id,
                            "status": task["status"],
                            "generations": task["generation"],
                            "checks": checks,
                            "metric": task["jobs"][0]["metric"],
                            "events": [event["kind"] for event in events],
                        }
                    time.sleep(config.tick_seconds)
                raise TimeoutError("synthetic recovery drill did not complete")
            finally:
                for table in ["workers", "jobs"]:
                    for row in service.store.rows(f"SELECT identity FROM {table}"):
                        signal_owned(json.loads(row["identity"]) if row["identity"] else None, signal.SIGKILL)
                for process in [*controller.children.values(), *controller.runner.children.values()]:
                    process.wait(timeout=5)
