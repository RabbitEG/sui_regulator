from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys
import uuid

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
    submit.add_argument("--objective")
    submit.add_argument("--key", default=None)
    submit.add_argument("--adapter", choices=["mock", "claude", "unavailable"], default="mock")
    submit.add_argument("--spec", default="{}", help="bounded mock job JSON")
    commands.add_parser("list")
    for name in ["get", "events", "hold", "resume", "cancel", "instruct"]:
        sub = commands.add_parser(name)
        sub.add_argument("task_id")
        if name in {"hold", "resume", "cancel", "instruct"}:
            sub.add_argument("--key")
        if name == "instruct":
            sub.add_argument("instruction")
    receipt = commands.add_parser("receipt")
    receipt.add_argument("command_id")
    commands.add_parser("drain", help="apply queued commands without starting workers")
    backup = commands.add_parser("backup")
    backup.add_argument("destination", type=Path)
    commands.add_parser("tick", help="run one reconciliation pass")
    commands.add_parser("serve", help="run the independent controller")
    commands.add_parser("demo", help="run an isolated real-process worker-death drill")
    return p


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import run_demo
            print(json.dumps(run_demo(), ensure_ascii=False))
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
            result = service.command("submit_task", actor, key, payload)
        elif args.command == "list":
            result = service.list_tasks()
        elif args.command == "get":
            result = service.get_task(args.task_id)
        elif args.command == "events":
            result = service.events(args.task_id)
        elif args.command == "receipt":
            result = service.get_command(args.command_id)
        elif args.command == "drain":
            result = {"processed": service.process_commands()}
        elif args.command == "backup":
            result = {"path": str(service.store.backup(args.destination))}
        elif args.command in {"serve", "tick"}:
            from .controller import Controller
            with Controller(service.config) as controller:
                if args.command == "tick":
                    controller.tick()
                    result = {"reconciled": True}
                else:
                    try:
                        controller.run()
                    except KeyboardInterrupt:
                        pass
                    result = {"controller": "stopped", "jobs": "retained"}
        else:
            kind = "instruct_task" if args.command == "instruct" else f"{args.command}_task"
            payload = {"instruction": args.instruction} if args.command == "instruct" else {}
            result = service.command(kind, actor, key, payload, args.task_id)
        print(json.dumps({"protocol_version": PROTOCOL_VERSION, "result": result}, ensure_ascii=False))
    except (DomainError, ValueError, OSError) as exc:
        print(json.dumps({"error": {"code": getattr(exc, "code", "LOCAL_ERROR"), "message": str(exc)}}, ensure_ascii=False))
        sys.exit(1)
