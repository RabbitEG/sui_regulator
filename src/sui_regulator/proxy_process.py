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
from .transcripts import SessionRecorder


def run(config: Config, credentials: Credentials) -> None:
    service = Service(config)
    protocol = SuiProtocol(service)
    context = protocol.claim_task(credentials)
    recorder = SessionRecorder(config, credentials, source="mock-proxy")
    recorder.write("text", "user", context["objective"], message_id="initial-input")
    recorder.write(
        "text",
        "assistant",
        "[合成演示] 我是 Mock Sui Proxy，正在查询持久 Task 并连接已有 Job。无需模型 API。",
    )
    recorder.write("tool_call", "assistant", '{"logical_key":"primary-trial"}', tool_name="launch_job",
                   call_id="mock-launch-job")
    job = protocol.launch_job(credentials, "primary-trial", context["spec"])
    protocol.attach_job(credentials, job["id"])
    recorder.write(
        "tool_result",
        "tool",
        json.dumps({"job_id": job["id"], "status": job["status"]}),
        tool_name="launch_job",
        call_id="mock-launch-job",
    )
    recorder.write(
        "text",
        "assistant",
        "[合成演示] 已连接独立 Runner。下面的心跳和我的声明均不代表 Job 完成，请核对 Runner 观测与指标。",
    )
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
        instruction_page = protocol.get_task_records(credentials, "instructions")
        while True:
            if instruction_page["items"]:
                protocol.acknowledge_instructions(
                    credentials, [row["id"] for row in instruction_page["items"]]
                )
            if not instruction_page["has_more"]:
                break
            instruction_page = protocol.get_task_records(
                credentials, "instructions", after=instruction_page["next_after"]
            )
        if context["rotation_requested"]:
            recorder.write("text", "assistant", "[合成演示] 收到换代请求，提交 Handoff；原有 Job 保持运行。")
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
            recorder.write(
                "tool_result",
                "tool",
                json.dumps({"job_id": job["id"], "status": job["status"], "metric": job["metric"]}),
                tool_name="get_job",
            )
            protocol.complete_task(
                credentials, "Synthetic job observed completed; no scientific conclusion asserted"
            )
            return
        if (
            job["status"] in {"FAILED", "UNKNOWN", "BLOCKED", "CANCELLED"}
            and job["status"] != previous_status
        ):
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
