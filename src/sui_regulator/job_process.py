"""Independent, fenced, synthetic experiment process. No worker parent dependency."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .config import Config
from .models import encode, identifier
from .processes import FileLock, identity
from .protocol import execution_authorized
from .service import Service


def run(config: Config, job_id: str) -> None:
    service = Service(config)
    directory = config.home / "jobs" / job_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        lock = FileLock(directory / "runner.lock")
        lock.__enter__()
    except BlockingIOError:
        return
    try:
        with service.store.transaction() as conn:
            job = service._get(conn, "jobs", job_id)
            if conn.execute("SELECT 1 FROM executions WHERE job_id=?", (job_id,)).fetchone():
                return
            if job["status"] not in {"STARTING", "QUEUED"}:
                return
            if not execution_authorized(conn, job):
                conn.execute(
                    "UPDATE jobs SET status='BLOCKED',error='launch authorization expired',updated_at=? WHERE id=?",
                    (time.time(), job_id),
                )
                service.store.event(
                    conn, job["task_id"], "RUNNER_FENCED", "runner", "runner", {"job_id": job_id}
                )
                return
            process_identity, now = identity(os.getpid()), time.time()
            conn.execute(
                "INSERT INTO executions(job_id,identity,started_at) VALUES(?,?,?)",
                (job_id, encode(process_identity), now),
            )
            conn.execute(
                "UPDATE jobs SET status='RUNNING',pid=?,identity=?,observed_at=?,updated_at=? WHERE id=?",
                (os.getpid(), encode(process_identity), now, now, job_id),
            )
            service.store.event(
                conn,
                job["task_id"],
                "JOB_STARTED",
                "runner-observed",
                "runner",
                {"job_id": job_id, "pid": os.getpid()},
            )
        spec = json.loads(job["spec"])
        log_fd = os.open(
            directory / "metrics.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
        )
        try:
            for step in range(1, spec["steps"] + 1):
                time.sleep(spec["interval"])
                metric = {"step": step, "total_steps": spec["steps"], "loss": round(1 / step, 8)}
                os.write(log_fd, (encode(metric) + "\n").encode())
                with service.store.transaction() as conn:
                    conn.execute(
                        "UPDATE jobs SET metric=?,observed_at=?,updated_at=? WHERE id=?",
                        (encode(metric), time.time(), time.time(), job_id),
                    )
                if step == spec["fail_at"]:
                    raise RuntimeError("injected synthetic training failure")
        finally:
            os.close(log_fd)
        result = directory / "result.json"
        fd = os.open(result, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(encode({"job_id": job_id, "metric": metric, "synthetic": True}) + "\n")
            output.flush()
            os.fsync(output.fileno())
        with service.store.transaction() as conn:
            now = time.time()
            conn.execute(
                "UPDATE jobs SET status='COMPLETED',exit_code=0,observed_at=?,updated_at=? WHERE id=?",
                (now, now, job_id),
            )
            conn.execute("UPDATE executions SET finished_at=? WHERE job_id=?", (now, job_id))
            conn.execute(
                "INSERT INTO artifacts(id,task_id,job_id,path,kind,source,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    identifier("artifact"),
                    job["task_id"],
                    job_id,
                    str(result),
                    "metrics",
                    "runner-observed",
                    now,
                ),
            )
            service.store.event(
                conn,
                job["task_id"],
                "JOB_COMPLETED",
                "runner-observed",
                "runner",
                {"job_id": job_id, "metric": metric},
            )
    except Exception as exc:
        with service.store.transaction() as conn:
            now = time.time()
            conn.execute(
                "UPDATE jobs SET status='FAILED',exit_code=1,error=?,observed_at=?,updated_at=? WHERE id=? AND status='RUNNING'",
                (str(exc)[:1000], now, now, job_id),
            )
            service.store.event(
                conn,
                None,
                "RUNNER_ERROR",
                "runner-observed",
                "runner",
                {"job_id": job_id, "error_type": type(exc).__name__},
            )
        raise
    finally:
        lock.__exit__(None, None, None)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    run(Config.load(args.home), args.job_id)


if __name__ == "__main__":
    main()
