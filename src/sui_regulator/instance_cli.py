"""CLI for enrolling and operating an independent Git Bridge."""

from __future__ import annotations

import json
import random
import time
from pathlib import Path

from .git_bridge import GitBridge, enroll, identifier, json_bytes
from .git_transport import GitTransport
from .models import DomainError


def add_parser(commands):
    parser = commands.add_parser("instance", help="Git instance mailbox and independent bridge")
    sub = parser.add_subparsers(dest="instance_command", required=True)
    init = sub.add_parser(
        "init-mailbox", help="create local command/status branches; does not push or start tasks"
    )
    init.add_argument("--repo", type=Path, required=True)
    init.add_argument("--instance", required=True)
    init.add_argument("--epoch", required=True)
    init.add_argument("--author-name", default="Sui Git Bridge")
    init.add_argument("--author-email", default="sui-bridge@localhost")
    register = sub.add_parser(
        "enroll", help="bind Git metadata to the reachable Yan ledger without delivering commands"
    )
    register.add_argument("--state", type=Path, required=True)
    register.add_argument("--remote", required=True)
    register.add_argument("--instance", required=True)
    register.add_argument("--epoch", required=True)
    register.add_argument("--poll-seconds", type=float, default=30)
    register.add_argument("--publish-seconds", type=float, default=300)
    register.add_argument("--author-name", default="Sui Git Bridge")
    register.add_argument("--author-email", default="sui-bridge@localhost")
    register.add_argument("--report-root", default="")
    register.add_argument(
        "--report", action="append", default=[], help="explicit task-key=relative-report-path"
    )
    for name in ("once", "serve", "inspect", "bind-task"):
        command = sub.add_parser(name)
        command.add_argument("--state", type=Path, required=True)
        if name == "bind-task":
            command.add_argument("task_key")
            command.add_argument("task_id")


def init_mailbox(repo, instance, epoch, author_name="Sui Git Bridge", author_email="sui-bridge@localhost"):
    identifier(instance)
    identifier(epoch)
    repo = repo.expanduser().absolute()
    if not (repo / ".git").is_dir():
        raise ValueError("repo must be an ordinary local clone with a .git directory")
    git = GitTransport(repo / ".git", str(repo), author_name=author_name, author_email=author_email)
    parent = git.run("rev-parse", "HEAD")[1].decode().strip()
    branches = ["commands/" + instance, "status/" + instance]
    for branch in branches:
        if git.run("show-ref", "--verify", "--quiet", "refs/heads/" + branch, allowed=(0, 1))[0] == 0:
            raise ValueError("instance branch already exists; refusing to replace it")
    manifest = {"schema_version": 1, "instance_id": instance, "enrollment_epoch": epoch}
    commands = git.commit_files(
        {"instance.json": json_bytes(manifest)}, parent=parent, message="Enroll command mailbox " + instance
    )
    status = git.commit_files(
        {
            "bridge.json": json_bytes({"instance": manifest, "files": ["summary.md"]}),
            "summary.md": b"# Awaiting the first Bridge observation\n\nNo live Controller status has been published.\n",
        },
        message="Initialize status mailbox " + instance,
    )
    # One transaction: neither branch may overwrite an existing local branch.
    transaction = f"start\ncreate refs/heads/{branches[0]} {commands}\ncreate refs/heads/{branches[1]} {status}\nprepare\ncommit\n"
    git.run("update-ref", "--stdin", data=transaction.encode())
    return {
        "instance": manifest,
        "branches": branches,
        "next": "review and push both branches; then enroll a Bridge",
    }


def run(args):
    if args.instance_command == "init-mailbox":
        print(
            json.dumps(
                init_mailbox(args.repo, args.instance, args.epoch, args.author_name, args.author_email)
            )
        )
        return
    if args.instance_command == "enroll":
        reports = {}
        for item in args.report:
            key, separator, path = item.partition("=")
            if not separator or not path:
                raise ValueError("report must be task-key=relative-path")
            reports[identifier(key)] = path
        result = enroll(
            args.state,
            remote=args.remote,
            instance_id=args.instance,
            enrollment_epoch=args.epoch,
            yan_home=args.home,
            poll_seconds=args.poll_seconds,
            publish_seconds=args.publish_seconds,
            author_name=args.author_name,
            author_email=args.author_email,
            report_root=str(Path(args.report_root).expanduser().absolute()) if args.report_root else "",
            reports=reports,
        )
        print(json.dumps(result))
        return
    with GitBridge(args.state) as bridge:
        if args.instance_command == "bind-task":
            bridge.bind_task(args.task_key, args.task_id)
            print(
                json.dumps(
                    {"task_key": args.task_key, "task_id": args.task_id, "effect": "local mapping recorded"}
                )
            )
        elif args.instance_command == "inspect":
            print(
                json.dumps(
                    {
                        "instance_id": bridge.config.instance_id,
                        "command_head": bridge.meta("command_head"),
                        "last_errors": bridge.meta("last_errors", {}),
                        "last_published": bridge.meta("last_published"),
                        "receipts": [
                            json.loads(r[0])
                            for r in bridge.db.execute(
                                "SELECT receipt FROM messages ORDER BY rowid DESC LIMIT 100"
                            )
                        ],
                    },
                    ensure_ascii=False,
                )
            )
        elif args.instance_command == "once":
            result = bridge.cycle(force_publish=True)
            print(json.dumps(result, ensure_ascii=False))
            if result["errors"]:
                raise DomainError(
                    "BRIDGE_CYCLE_INCOMPLETE", "check the reported transport errors; Tasks were not paused"
                )
        else:
            failures = 0
            try:
                while True:
                    result = bridge.cycle()
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                    failures = min(failures + 1, 5) if result["errors"] else 0
                    delay = min(300, bridge.config.poll_seconds * 2**failures)
                    time.sleep(random.uniform(delay * 0.9, delay))
            except KeyboardInterrupt:
                pass
