from __future__ import annotations

import argparse
import getpass
import json
import sys
import uuid
from pathlib import Path

from . import PROTOCOL_VERSION
from .config import Config
from .models import DomainError
from .service import Service


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yan", description="Sui Regulator local JSON command gateway")
    p.add_argument("--home", type=Path, default=Path(".sui"))
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    submit = commands.add_parser("submit")
    submit.add_argument("title")
    brief = submit.add_mutually_exclusive_group()
    brief.add_argument("--objective")
    brief.add_argument("--objective-file", type=Path, help="UTF-8 task brief, preserved verbatim across generations")
    submit.add_argument("--completion-criteria", default="")
    submit.add_argument("--verification-template", default="", help="approved no-argument command required to pass before completion")
    submit.add_argument("--workspace", type=Path, help="existing project directory")
    submit.add_argument("--execution-profile", choices=["managed", "claude_code"])
    submit.add_argument("--permission-mode", choices=["guarded", "trusted"], default="guarded",
                        help="trusted grants autonomous execution and automatic runtime recovery")
    submit.add_argument("--recovery-mode", choices=["persistent", "bounded"])
    submit.add_argument("--max-proxy-seconds", type=float, default=0, help="total Proxy wall-time allowance; 0 is unlimited")
    submit.add_argument("--key", default=None)
    submit.add_argument("--adapter", choices=["mock", "claude", "unavailable"], default="mock")
    submit.add_argument("--spec", default="{}", help="bounded mock job JSON")
    commands.add_parser("list")
    policy = commands.add_parser("policy", help="change durable recovery policy; does not Resume")
    policy.add_argument("task_id")
    policy.add_argument("--recovery-mode", choices=["persistent", "bounded"])
    policy.add_argument("--permission-mode", choices=["guarded", "trusted"])
    policy.add_argument("--max-proxy-seconds", type=float)
    policy.add_argument("--key")
    resolve = commands.add_parser("resolve-attempt", help="record operator verification of an unresolved command while Task is on Hold")
    resolve.add_argument("task_id")
    resolve.add_argument("attempt_id")
    resolve.add_argument("--evidence", required=True)
    resolve.add_argument("--key")
    for name in ["get", "events", "hold", "resume", "cancel", "instruct"]:
        sub = commands.add_parser(name)
        sub.add_argument("task_id")
        if name in {"hold", "resume", "cancel", "instruct"}:
            sub.add_argument("--key")
        if name == "instruct":
            sub.add_argument("instruction")
    receipt = commands.add_parser("receipt")
    receipt.add_argument("command_id")
    cancel_job = commands.add_parser("cancel-job", help="explicitly cancel a managed synthetic job")
    cancel_job.add_argument("job_id")
    cancel_job.add_argument("--key")
    commands.add_parser("drain", help="apply queued commands without starting workers")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    commands.add_parser("tick", help="run one reconciliation pass")
    commands.add_parser("serve", help="run the independent controller")
    console = commands.add_parser("console", help="run the independent localhost Yan Console")
    console.add_argument("--port", type=int, default=8765)
    console_demo = commands.add_parser("console-demo", help="run an isolated synthetic Yan Console drill")
    console_demo.add_argument(
        "--state-dir", type=Path, help="must not already exist; defaults to a private temporary directory"
    )
    console_demo.add_argument("--port", type=int, default=8765)
    commands.add_parser("demo", help="run an isolated real-process worker-death drill")
    rpc = commands.add_parser("rpc", help="call the authenticated local Unix gateway")
    rpc.add_argument("method")
    rpc.add_argument("--params", default="{}")
    commands.add_parser("schema", help="emit the versioned Yan request schema")
    from .instance_cli import add_parser

    add_parser(commands)
    failures = commands.add_parser("failures", help="offline failure registry and trajectory replay")
    failure_commands = failures.add_subparsers(dest="failure_command", required=True)
    for name in ["list", "replay", "evaluate"]:
        sub = failure_commands.add_parser(name)
        sub.add_argument("--registry", type=Path)
        if name != "list":
            sub.add_argument("path", type=Path)
            sub.add_argument("--summary", action="store_true")
            sub.add_argument("--output", type=Path)
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.command == "instance":
            from .instance_cli import run

            run(args)
            return
        if args.command == "console-demo":
            from .console_demo import run_console_demo

            run_console_demo(args.state_dir, args.port)
            return
        if args.command == "console":
            from .console import run_console

            run_console(args.home, args.port)
            return
        if args.command == "failures":
            from .failures.registry import FailureRegistry
            from .failures.replay import evaluate, replay

            registry = FailureRegistry(args.registry)
            result = (
                registry.catalog()
                if args.failure_command == "list"
                else (replay if args.failure_command == "replay" else evaluate)(args.path, registry)
            )
            if getattr(args, "output", None):
                args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            displayed = (
                {key: value for key, value in result.items() if key != "results"}
                if getattr(args, "summary", False)
                else result
            )
            print(json.dumps(displayed, ensure_ascii=False))
            if isinstance(result, dict) and result.get("passed") is False:
                sys.exit(1)
            return
        if args.command == "demo":
            from .demo import run_demo

            print(json.dumps(run_demo(), ensure_ascii=False))
            return
        if args.command in {"rpc", "schema"}:
            from .gateway import call, request_schema

            result = (
                request_schema()
                if args.command == "schema"
                else call(args.home, args.method, json.loads(args.params))
            )
            print(json.dumps(result, ensure_ascii=False))
            if "error" in result:
                sys.exit(1)
            return
        if args.command in {"serve", "tick"}:
            from .controller import Controller
            from .gateway import Gateway

            with Controller(Config.load(args.home)) as controller:
                if args.command == "tick":
                    controller.tick()
                    result = {"reconciled": True}
                else:
                    try:
                        with Gateway(controller.service):
                            controller.run()
                    except KeyboardInterrupt:
                        pass
                    result = {"controller": "stopped", "jobs": "retained"}
            print(json.dumps({"protocol_version": PROTOCOL_VERSION, "result": result}, ensure_ascii=False))
            return
        service = Service(Config.load(args.home))
        actor = f"local:{getpass.getuser()}"
        key = getattr(args, "key", None) or uuid.uuid4().hex
        if args.command == "init":
            result = {"home": str(service.config.home), "protocol_version": PROTOCOL_VERSION}
        elif args.command == "submit":
            payload = {"title": args.title, "adapter": args.adapter, "spec": json.loads(args.spec)}
            if args.objective:
                payload["objective"] = args.objective
            if args.objective_file:
                payload["objective"] = args.objective_file.read_text(encoding="utf-8")
            payload.update(permission_mode=args.permission_mode, completion_criteria=args.completion_criteria,
                           verification_template=args.verification_template)
            if args.execution_profile:
                payload["execution_profile"] = args.execution_profile
            if args.workspace:
                payload["workspace"] = str(args.workspace.expanduser().resolve())
            payload["max_proxy_seconds"] = args.max_proxy_seconds
            if args.recovery_mode:
                payload["recovery_mode"] = args.recovery_mode
            result = service.command("submit_task", actor, key, payload)
        elif args.command == "resolve-attempt":
            result = service.command("resolve_attempt", actor, key, {"attempt_id": args.attempt_id, "evidence": args.evidence}, args.task_id)
        elif args.command == "policy":
            payload = {field: getattr(args, field) for field in ("recovery_mode", "max_proxy_seconds", "permission_mode") if getattr(args, field) is not None}
            result = service.command("set_recovery_policy", actor, key, payload, args.task_id)
        elif args.command == "list":
            result = service.list_tasks()
        elif args.command == "get":
            result = service.get_task(args.task_id)
        elif args.command == "events":
            result = service.events(args.task_id)
        elif args.command == "receipt":
            result = service.get_command(args.command_id)
        elif args.command == "cancel-job":
            job = service.get_job(args.job_id)
            result = service.command("cancel_job", actor, key, {"job_id": job["id"]}, job["task_id"])
        elif args.command == "drain":
            result = {"processed": service.process_commands()}
        elif args.command == "backup":
            result = {"path": str(service.store.backup(args.destination))}
        else:
            kind = "instruct_task" if args.command == "instruct" else f"{args.command}_task"
            payload = {"instruction": args.instruction} if args.command == "instruct" else {}
            result = service.command(kind, actor, key, payload, args.task_id)
        print(json.dumps({"protocol_version": PROTOCOL_VERSION, "result": result}, ensure_ascii=False))
    except (DomainError, ValueError, OSError) as exc:
        print(
            json.dumps(
                {"error": {"code": getattr(exc, "code", "LOCAL_ERROR"), "message": str(exc)}},
                ensure_ascii=False,
            )
        )
        sys.exit(1)
