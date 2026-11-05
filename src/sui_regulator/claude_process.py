from __future__ import annotations

import argparse
import ctypes
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

from .claude_adapter import ClaudeStreamDetector, build_command, classify_error, runtime_environment
from .config import Config
from .failures.claude import excerpt
from .failures.persistence import publish
from .failures.registry import FailureRegistry
from .models import Credentials, DomainError, encode
from .processes import identity
from .protocol import SuiProtocol
from .service import Service
from .task_prompt import SUPERVISOR_PROMPT, task_prompt
from .transcripts import SessionRecorder

MAX_LINE_BYTES = 131072


def _parent_death(expected_parent: int):
    def setup():
        # Only the CLI child follows wrapper death. Experiment Runner never uses this.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != expected_parent:
            os._exit(125)

    return setup


def run(config: Config, credentials: Credentials) -> None:
    service, last_heartbeat = Service(config), 0.0
    protocol = SuiProtocol(service)
    context = protocol.claim_task(credentials)
    continuous = context["permission_mode"] == "trusted"
    worker = next(w for w in context["workers"] if w["id"] == credentials.worker_id)
    prompt = task_prompt(context)
    recorder = SessionRecorder(config, credentials)
    recorder.write("text", "system", SUPERVISOR_PROMPT, message_id="supervisor-protocol")
    recorder.record_input(prompt, context)
    process = subprocess.Popen(
        build_command(
            config,
            worker["session_id"],
            execution_profile=context["execution_profile"],
            permission_mode=context["permission_mode"],
        ),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=context["workspace"],
        env=runtime_environment(config, credentials),
        start_new_session=True,
        close_fds=True,
        preexec_fn=_parent_death(os.getpid()),
    )
    with service.store.transaction() as conn:
        conn.execute(
            "UPDATE workers SET child_identity=? WHERE id=?",
            (encode(identity(process.pid)), credentials.worker_id),
        )
    assert process.stdin and process.stdout and process.stderr

    def send_input(text):
        payload = (
            (
                encode(
                    {
                        "type": "user",
                        "session_id": worker["session_id"],
                        "message": {"role": "user", "content": text},
                    }
                )
                + "\n"
            )
            if continuous
            else text
        )
        process.stdin.write(payload.encode())
        process.stdin.flush()

    send_input(prompt)
    if not continuous:
        process.stdin.close()
    from .requirements_delivery import offer

    with service.store.transaction() as conn:
        offer(
            conn,
            service,
            credentials,
            context["instructions"],
            "initial-stdin-written",
            advance_revision=True,
        )
    detector = ClaudeStreamDetector(
        worker["session_id"],
        FailureRegistry(Path(config.failure_registry)) if config.failure_registry else None,
        recovery_mode=context["recovery_mode"],
        permission_mode=context["permission_mode"],
        response_silence_seconds=config.trusted_response_silence_seconds,
    )
    final_seen, error, stderr_tail = False, None, b""
    next_continuation = None
    stream_capability_recorded = False

    def record_exit(reason: str, return_code=None, turn_limit=False, incidents=()):
        recorder.flush()
        recorder.write("exit", "system", reason, exit_code=return_code)
        # Keep a bounded excerpt of the CLI stderr tail in the durable ledger;
        # the raw buffer dies with the wrapper and this is the only post-mortem.
        stderr_excerpt = excerpt(stderr_tail.decode(errors="replace"))
        with service.store.transaction() as conn:
            conn.execute(
                "UPDATE workers SET status='EXITED',error=? WHERE id=? AND status IN ('RUNNING','STARTING','ROTATING')",
                (reason, credentials.worker_id),
            )
            data = {
                "error": reason,
                "exit_code": return_code,
                "final_result_seen": final_seen,
                "stderr_excerpt": stderr_excerpt,
            }
            if turn_limit:
                # Turn exhaustion ends this CLI Session, not the durable Task.
                # Persistent recovery continues without counting a runtime fault.
                data["turn_budget_exhausted"] = True
            service.store.event(
                conn,
                credentials.task_id,
                "WORKER_RUNTIME_EXIT",
                "wrapper-observed",
                credentials.worker_id,
                data,
            )
            # Recovery may terminate this wrapper as soon as an Incident is visible.
            # Commit the exit evidence and its terminal Incident together.
            publish(service, credentials, incidents, connection=conn)

    buffers, dropping = {process.stdout.fileno(): b"", process.stderr.fileno(): b""}, set()
    selector = selectors.DefaultSelector()
    for stream in [process.stdout, process.stderr]:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    try:
        while selector.get_map():
            if time.monotonic() - last_heartbeat >= config.heartbeat_seconds:
                protocol.heartbeat(credentials)
                recorder.flush(final=False)
                last_heartbeat = time.monotonic()
                background_pending = (
                    bool(
                        service.store.rows(
                            "SELECT 1 FROM tool_attempts WHERE task_id=? AND worker_id=? AND status='RUNNING' LIMIT 1",
                            (credentials.task_id, credentials.worker_id),
                        )
                    )
                    if continuous
                    else False
                )
                clock_event = detector.tick(tool_pending=background_pending)
                publish(service, credentials, clock_event.incidents)
                if clock_event.error:
                    record_exit(clock_event.error)
                    return
                if continuous and next_continuation is not None and time.monotonic() >= next_continuation:
                    if background_pending:
                        next_continuation = time.monotonic() + config.trusted_continuation_seconds
                    else:
                        # A successful conversational turn is not a Session failure.
                        # Keep the same process, session identity and in-memory history.
                        current = protocol.get_task_context(credentials, offer_context=False)
                        if (
                            current["status"] == "WAITING"
                            and current["wait_state"]
                            and current["wait_state"]["next_check_at"] > time.time()
                        ):
                            # Recheck on the heartbeat so new instructions can wake the same Session.
                            continue
                        message = (
                            "[Sui runtime continuation] Your previous turn ended normally and the Task is still active. "
                            "Read current task context and continue the operator's full workflow in this same Session. "
                            "If genuinely waiting, use wait_task with a concrete recheck time. If finished, submit complete_task. "
                            "This reminder grants no new task scope and does not request replay of previous operations."
                        )
                        send_input(message)
                        recorder.write("runtime", "system", message, message_id="runtime-continuation")
                        with service.store.transaction() as conn:
                            service.store.event(
                                conn,
                                credentials.task_id,
                                "SESSION_CONTINUED",
                                "wrapper-observed",
                                credentials.worker_id,
                                {
                                    "session_id": worker["session_id"],
                                    "reason": "normal turn ended; Task still active",
                                },
                            )
                        detector.begin_turn()
                        final_seen, next_continuation = False, None
            for key, _ in selector.select(timeout=min(config.heartbeat_seconds, 0.1)):
                fd = key.fd
                chunk = os.read(fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    if fd == process.stdout.fileno():
                        ended = detector.end(partial_line=bool(buffers[fd]))
                        publish(service, credentials, ended.incidents)
                        error = error or ended.error
                    continue
                if fd == process.stderr.fileno():
                    stderr_tail = (stderr_tail + chunk)[-8192:]
                    continue
                buffers[fd] += chunk
                while b"\n" in buffers[fd]:
                    line, buffers[fd] = buffers[fd].split(b"\n", 1)
                    if fd in dropping:
                        dropping.remove(fd)
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        oversized = detector.feed("{}")
                        publish(service, credentials, oversized.incidents)
                        if oversized.error:
                            record_exit(oversized.error)
                            return
                        with service.store.transaction() as conn:
                            conn.execute(
                                "UPDATE workers SET anomaly_count=anomaly_count+1 WHERE id=?",
                                (credentials.worker_id,),
                            )
                        continue
                    decoded_line = line.decode(errors="replace")
                    if '"task_notification"' in decoded_line:
                        from .attempts import observe_background

                        try:
                            message = json.loads(decoded_line)
                        except ValueError:
                            message = None
                        observe_background(service, credentials, worker["session_id"], message)
                    recorder.feed(decoded_line)
                    event = detector.feed(decoded_line)
                    if event.error:
                        final_seen |= event.final
                        record_exit(
                            event.error,
                            turn_limit=event.error == "MAX_TURNS_EXHAUSTED",
                            incidents=event.incidents,
                        )
                        return
                    publish(service, credentials, event.incidents)
                    if detector.normalizer.text_delta_seen and not stream_capability_recorded:
                        with service.store.transaction() as conn:
                            capabilities = json.loads(
                                service._get(conn, "workers", credentials.worker_id)["capabilities"]
                            )
                            conn.execute(
                                "UPDATE workers SET capabilities=? WHERE id=?",
                                (
                                    encode(sorted(set(capabilities) | {"claude-text-delta-observed"})),
                                    credentials.worker_id,
                                ),
                            )
                            service.store.event(
                                conn,
                                credentials.task_id,
                                "WORKER_STREAM_CAPABILITY",
                                "wrapper-observed",
                                credentials.worker_id,
                                {"text_delta_observed": True, "unit": "text fragment, not tokenizer token"},
                            )
                        stream_capability_recorded = True
                    if event.session_id and event.session_id != worker["session_id"]:
                        error = "SESSION_ID_MISMATCH"
                    if event.activity:
                        # The CLI may resume itself after a background notification.
                        # Do not queue an extra user turn into ongoing model/tool work.
                        next_continuation = None
                        protocol.heartbeat(credentials, effective_activity=not event.anomaly)
                    if event.anomaly or event.successful_tool:
                        with service.store.transaction() as conn:
                            conn.execute(
                                "UPDATE workers SET anomaly_count=? WHERE id=?",
                                (
                                    service._get(conn, "workers", credentials.worker_id)["anomaly_count"] + 1
                                    if event.anomaly
                                    else 0,
                                    credentials.worker_id,
                                ),
                            )
                    final_seen |= event.final
                    if continuous and event.final and not event.error:
                        next_continuation = time.monotonic() + config.trusted_continuation_seconds
                    error = error or event.error
                    if error:
                        record_exit(error, turn_limit=error == "MAX_TURNS_EXHAUSTED")
                        return
                if len(buffers[fd]) > MAX_LINE_BYTES:
                    buffers[fd] = b""
                    dropping.add(fd)
                    oversized = detector.feed("{}")
                    publish(service, credentials, oversized.incidents)
                    if oversized.error:
                        record_exit(oversized.error)
                        return
                    with service.store.transaction() as conn:
                        conn.execute(
                            "UPDATE workers SET anomaly_count=anomaly_count+1 WHERE id=?",
                            (credentials.worker_id,),
                        )
        return_code = process.wait(timeout=5)
        context = protocol.get_task_context(credentials, offer_context=False)
        if continuous and error is None and return_code == 0:
            # With open streaming input, an actual CLI exit is distinct from a result frame.
            error = "CLI_EXITED_BEFORE_TASK_COMPLETION"
        if context["rotation_requested"]:
            protocol.handoff(
                credentials,
                {
                    "source": "wrapper",
                    "summary": context["declared"],
                    "job_ids": [job["id"] for job in context["jobs"]],
                    "next_action": "read durable context",
                },
            )
            return
        if (
            error is None
            and return_code == 0
            and final_seen
            and context["declared"].get("phase") == "WAITING_JOB"
        ):
            while any(job["status"] in {"QUEUED", "STARTING", "RUNNING"} for job in context["jobs"]):
                protocol.heartbeat(credentials)
                if context["rotation_requested"]:
                    break
                time.sleep(config.heartbeat_seconds)
                context = protocol.get_task_context(credentials, offer_context=False)
            protocol.handoff(
                credentials,
                {
                    "source": "wrapper",
                    "summary": "CLI turn ended while observing job; fresh session must inspect results",
                    "job_ids": [job["id"] for job in context["jobs"]],
                },
            )
            with service.store.transaction() as conn:
                conn.execute(
                    "UPDATE workers SET status='ROTATING',rotation_at=COALESCE(rotation_at,handoff_at) WHERE id=?",
                    (credentials.worker_id,),
                )
            return
        error = error or (
            classify_error(stderr_tail.decode(errors="replace"))
            if return_code
            else "SESSION_FINISHED_WITHOUT_COMPLETION"
            if final_seen
            else "TRUNCATED_STREAM"
        )
        record_exit(error, return_code, turn_limit=error == "MAX_TURNS_EXHAUSTED")
    except DomainError:
        # complete_task, hold, cancel and takeover revoke the lease. No replay follows.
        return
    finally:
        recorder.flush()
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()
        if not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    credentials = Credentials(**json.loads(sys.stdin.read(16384)))
    try:
        run(Config.load(args.home), credentials)
    except (DomainError, OSError):
        return


if __name__ == "__main__":
    main()
