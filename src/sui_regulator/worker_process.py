from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import Config
from .models import Credentials, DomainError
from .protocol import SuiProtocol
from .service import Service


def run(config: Config, credentials: Credentials) -> None:
    service = Service(config)
    protocol = SuiProtocol(service)
    context = protocol.claim_task(credentials)
    job = protocol.launch_job(credentials, "primary-trial", context["spec"])
    protocol.attach_job(credentials, job["id"])
    protocol.report_progress(
        credentials,
        phase="WAITING_JOB",
        summary="Attached to the persistent synthetic job",
        job_ids=[job["id"]],
        next_action="Observe the existing job; never restart it",
    )
    previous_status = None
    while True:
        protocol.heartbeat(credentials)
        context = protocol.get_task_context(credentials)
        if context["rotation_requested"]:
            protocol.handoff(
                credentials,
                {
                    "goal": context["objective"],
                    "completed": [],
                    "phase": "WAITING_JOB",
                    "job_ids": [job["id"]],
                    "modified_files": [],
                    "decisions": ["reuse the existing logical job"],
                    "risks": [],
                    "pending": ["wait for the existing job"],
                    "next_action": "attach existing job",
                },
            )
            return
        job = service.get_job(job["id"])
        if job["status"] == "COMPLETED":
            protocol.complete_task(
                credentials, "Synthetic job observed completed; no scientific conclusion asserted"
            )
            return
        if job["status"] in {"FAILED", "UNKNOWN", "BLOCKED"} and job["status"] != previous_status:
            protocol.report_blocker(
                credentials,
                f"Existing job is {job['status']}; do not relaunch",
                "resource" if job["status"] == "BLOCKED" else "user",
            )
        previous_status = job["status"]
        time.sleep(config.heartbeat_seconds)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    credentials = Credentials(**json.loads(sys.stdin.read(16384)))
    try:
        run(Config.load(args.home), credentials)
    except DomainError:
        # Revocation is an expected boundary, not a reason to retry a side effect.
        return


if __name__ == "__main__":
    main()
