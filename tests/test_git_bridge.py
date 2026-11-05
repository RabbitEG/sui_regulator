from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from sui_regulator.gateway import Gateway, call
from sui_regulator.git_bridge import GitBridge, enroll, json_bytes
from sui_regulator.git_transport import GitTransport
from sui_regulator.instance_cli import init_mailbox
from sui_regulator.models import DomainError
from tests.helpers import ProcessTestCase


class GitBridgeTests(ProcessTestCase):
    def setUp(self):
        super().setUp()
        self.gateway = Gateway(self.service).__enter__()
        self.addCleanup(lambda: self.gateway.__exit__(None, None, None))
        self.remote = Path(self.temp.name) / "remote.git"
        self.git = GitTransport(self.remote, str(self.remote))
        self.manifest = {"schema_version": 1, "instance_id": "test", "enrollment_epoch": "installation-1"}
        self.head = self.git.commit_files({"instance.json": json_bytes(self.manifest)})
        self.initial = self.head
        self.git.run("update-ref", "refs/heads/commands/test", self.head)
        self.state = Path(self.temp.name) / "bridge"
        enroll(
            self.state,
            remote=str(self.remote),
            instance_id="test",
            enrollment_epoch="installation-1",
            yan_home=self.config.home,
        )

    def append(self, files, remove=()):
        self.head = self.git.commit_files(files, parent=self.head, remove=remove)
        self.git.run("update-ref", "refs/heads/commands/test", self.head)
        return self.head

    def command(self, cid, kind="submit_task", task_key="task-one", **extra):
        data = {
            **self.manifest,
            "command_id": cid,
            "task_key": task_key,
            "kind": kind,
            "payload": {"title": "Git fixture", "adapter": "unavailable", "permission_mode": "trusted"}
            if kind == "submit_task"
            else {},
            **extra,
        }
        return {f"commands/{cid}/command.json": json_bytes(data)}

    def advance(self, cycles=3):
        with GitBridge(self.state) as bridge:
            for _ in range(cycles):
                result = bridge.cycle(force_publish=True)
                self.assertFalse(result["errors"], result)
                self.service.process_commands()

    def published(self, path):
        raw = self.git.run("show", "refs/heads/status/test:" + path)[1]
        return json.loads(raw) if path.endswith(".json") else raw.decode()

    def test_complete_brief_submit_dependent_instruction_receipts_and_restart_dedup(self):
        brief = "# 完整目标\n等待实验，核验、debug、继续实验。\n"
        self.append(
            {
                **self.command("a-create", brief_path="brief.md"),
                "commands/a-create/brief.md": brief.encode(),
                **self.command(
                    "b-update",
                    "instruct_task",
                    depends_on=["a-create"],
                    payload={"instruction": "核验对照组"},
                ),
            }
        )
        self.advance()
        self.advance()
        tasks = self.service.list_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["objective"], brief)
        rows = self.service.store.rows("SELECT * FROM commands WHERE actor=?", (f"uid:{os.getuid()}",))
        self.assertEqual(len(rows), 2)
        receipt = self.published("receipts/b-update.json")
        self.assertEqual(receipt["yan_status"], "SUCCEEDED")
        self.assertEqual(receipt["execution"], "unknown")
        status = self.published("tasks/task-one/status.json")
        self.assertEqual(status["id"], tasks[0]["id"])
        self.assertNotIn("objective", status)

    def test_rpc_accepted_then_reply_lost_replays_original_key_after_bridge_restart(self):
        self.append(self.command("create"))
        lost = False

        def rpc(home, method, params):
            nonlocal lost
            result = call(home, method, params)
            if method == "yan.submit_task" and not lost:
                lost = True
                raise ConnectionResetError("synthetic reply lost")
            return result

        with GitBridge(self.state, rpc=rpc) as bridge:
            self.assertIn("controller", bridge.cycle()["errors"])
        self.service.process_commands()
        self.advance()
        self.assertEqual(len(self.service.list_tasks()), 1)
        self.assertEqual(len(self.service.store.rows("SELECT * FROM commands")), 1)
        self.assertEqual(self.published("receipts/create.json")["yan_status"], "SUCCEEDED")

    def test_publish_failure_does_not_repeat_commands_or_hold_task(self):
        self.append(self.command("create"))
        with patch.object(GitTransport, "push", side_effect=DomainError("GIT_FAILED", "synthetic offline")):
            with GitBridge(self.state) as bridge:
                self.assertIn("publish", bridge.cycle()["errors"])
            self.service.process_commands()
        self.advance()
        self.assertEqual(len(self.service.store.rows("SELECT * FROM commands")), 1)
        self.assertEqual(self.service.list_tasks()[0]["desired"], "ACTIVE")

    def test_changed_brief_and_deleted_bundle_keep_original_effect_and_conflict_receipt(self):
        self.append(
            {**self.command("create", brief_path="brief.md"), "commands/create/brief.md": b"original task"}
        )
        self.advance()
        self.append({"commands/create/brief.md": b"changed task"})
        self.advance()
        receipt = self.published("receipts/create.json")
        self.assertIn("content_conflict", receipt)
        self.assertEqual(receipt["yan_status"], "SUCCEEDED")
        self.assertEqual(self.service.list_tasks()[0]["objective"], "original task")
        self.append({}, remove=["commands/create/brief.md", "commands/create/command.json"])
        self.advance()
        self.assertEqual(len(self.service.list_tasks()), 1)
        self.assertEqual(self.service.list_tasks()[0]["desired"], "ACTIVE")

    def test_invalid_bundles_and_missing_confirmation_do_not_block_other_tasks(self):
        self.append(
            {
                **self.command("wrong-epoch", enrollment_epoch="old-installation"),
                **self.command("escape", brief_path="../outside.md"),
                **self.command("large", brief_path="brief.md"),
                "commands/large/brief.md": b"x" * 16385,
                **self.command("good", task_key="good"),
                **self.command("hold", "hold_task", task_key="good"),
            }
        )
        self.advance()
        self.assertEqual(len(self.service.list_tasks()), 1)
        for cid in ("wrong-epoch", "escape", "large", "hold"):
            self.assertEqual(self.published(f"receipts/{cid}.json")["transport_state"], "REJECTED")
        self.assertEqual(self.service.list_tasks()[0]["desired"], "ACTIVE")

    def test_explicit_hold_resume_and_existing_task_binding(self):
        tid = self.submit(adapter="unavailable", permission_mode="trusted")
        with GitBridge(self.state) as bridge:
            bridge.bind_task("existing", tid)
        self.append(self.command("hold", "hold_task", task_key="existing", confirm="hold_task"))
        self.advance()
        self.assertEqual(self.service.get_task(tid)["desired"], "HOLD")
        self.append(self.command("resume", "resume_task", task_key="existing", depends_on=["hold"]))
        self.advance()
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
        self.assertEqual(len(self.service.list_tasks()), 1)

    def test_controller_offline_publishes_stale_observations_without_changing_task(self):
        self.append(self.command("create"))
        self.advance()
        before = self.published("tasks/task-one/status.json")["observed_at"]
        self.gateway.__exit__(None, None, None)
        with GitBridge(self.state) as bridge:
            self.assertIn("controller", bridge.cycle(force_publish=True)["errors"])
        self.assertEqual(self.published("snapshot.json")["controller"]["state"], "UNREACHABLE")
        task = self.published("tasks/task-one/status.json")
        self.assertTrue(task["query_stale"])
        self.assertEqual(task["observed_at"], before)
        self.assertEqual(self.service.list_tasks()[0]["desired"], "ACTIVE")

    def test_replaced_ledger_never_receives_old_instance_commands(self):
        self.append(self.command("create"))

        def replaced(home, method, params):
            result = call(home, method, params)
            if method == "yan.get_console_snapshot":
                result["result"]["workspace_id"] = "another-ledger"
            return result

        with GitBridge(self.state, rpc=replaced) as bridge:
            result = bridge.cycle()
        self.assertEqual(result["errors"]["controller"]["code"], "LEDGER_CHANGED")
        self.assertFalse(self.service.store.rows("SELECT * FROM commands"))

    def test_report_export_is_explicit_bounded_and_redacted(self):
        root = Path(self.temp.name) / "reports"
        root.mkdir()
        (root / "report.md").write_text(
            "Useful result\npassword=secret-fixture-value\nsk-ant-fixture-123456789abcdef\n"
        )
        (root / "large.md").write_bytes(b"x" * 65537)
        (root / "link.md").symlink_to(root / "report.md")
        os.mkfifo(root / "pipe.md")
        p = self.state / "config.json"
        config = json.loads(p.read_text())
        config.update(
            report_root=str(root),
            reports={
                "report-task": "report.md",
                "large": "large.md",
                "link": "link.md",
                "escape": "../outside.md",
                "pipe": "pipe.md",
            },
        )
        p.write_bytes(json_bytes(config))
        self.advance(1)
        text = self.published("tasks/report-task/report.md")
        self.assertIn("Useful result", text)
        self.assertNotIn("secret-fixture-value", text)
        self.assertNotIn("sk-ant-fixture", text)
        self.assertEqual(set(self.published("report-errors.json")), {"large", "link", "escape", "pipe"})
        config["reports"] = {}
        p.write_bytes(json_bytes(config))
        self.advance(1)
        sha = self.git.run("rev-parse", "refs/heads/status/test")[1].decode().strip()
        self.assertNotIn("tasks/report-task/report.md", self.git.tree(sha))

    def test_only_one_bridge_and_command_history_rewrite_does_not_replay(self):
        self.append(self.command("first"))
        self.advance()
        with GitBridge(self.state):
            with self.assertRaises(BlockingIOError):
                with GitBridge(self.state):
                    pass
        self.head = self.initial
        self.append(self.command("new", task_key="another"))
        with GitBridge(self.state) as bridge:
            self.assertEqual(
                bridge.cycle(force_publish=True)["errors"]["git"]["code"], "COMMAND_HISTORY_REWRITTEN"
            )
        self.assertEqual(len(self.service.list_tasks()), 1)
        self.assertEqual(self.service.list_tasks()[0]["desired"], "ACTIVE")

    def test_status_push_race_preserves_unknown_remote_files_on_retry(self):
        self.advance(1)
        original = GitTransport.push
        changed = False

        def race(transport, sha, branch):
            nonlocal changed
            if not changed:
                changed = True
                parent = self.git.run("rev-parse", "refs/heads/status/test")[1].decode().strip()
                remote_commit = self.git.commit_files({"operator-note.md": b"Keep this note"}, parent=parent)
                self.git.run("update-ref", "refs/heads/status/test", remote_commit)
            return original(transport, sha, branch)

        with patch.object(GitTransport, "push", race), GitBridge(self.state) as bridge:
            self.assertIn("publish", bridge.cycle(force_publish=True)["errors"])
        self.advance(1)
        self.assertEqual(self.published("operator-note.md"), "Keep this note")

    def test_mock_runtime_completes_through_actual_git_and_yan_without_duplicate_job(self):
        self.append(
            self.command(
                "mock",
                payload={
                    "title": "Synthetic Git acceptance",
                    "adapter": "mock",
                    "permission_mode": "trusted",
                    "spec": {"steps": 4, "interval": 0.01},
                },
            )
        )
        self.advance()
        tid = self.service.list_tasks()[0]["id"]
        self.until(lambda: self.service.get_task(tid)["desired"] == "COMPLETED")
        self.advance()
        task = self.published("tasks/task-one/status.json")
        self.assertEqual(task["desired"], "COMPLETED")
        self.assertTrue(task["completion"])
        self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)

    def test_rejected_dependency_does_not_block_independent_submit(self):
        self.append(
            {
                **self.command("bad", payload={"title": ""}),
                **self.command(
                    "dependent",
                    "instruct_task",
                    depends_on=["bad"],
                    payload={"instruction": "Never delivered"},
                ),
                **self.command("independent", task_key="another"),
            }
        )
        self.advance()
        self.assertEqual(self.published("receipts/dependent.json")["error"]["code"], "DEPENDENCY_REJECTED")
        self.assertEqual(len(self.service.list_tasks()), 1)

    def test_duplicate_task_key_is_not_rebound(self):
        self.append({**self.command("a-create"), **self.command("b-create")})
        self.advance()
        self.assertEqual(len(self.service.list_tasks()), 1)
        self.assertEqual(self.published("receipts/b-create.json")["error"]["code"], "TASK_KEY_CONFLICT")

    def test_mailbox_bootstrap_is_local_atomic_and_does_not_replace_branches(self):
        clone = Path(self.temp.name) / "operator"
        self.git.run("clone", "--quiet", "--branch", "commands/test", str(self.remote), str(clone))
        local = GitTransport(clone / ".git", str(clone))
        result = init_mailbox(clone, "new-instance", "new-install")
        self.assertEqual(result["branches"], ["commands/new-instance", "status/new-instance"])
        manifest = json.loads(local.run("show", "commands/new-instance:instance.json")[1])
        self.assertEqual(manifest["instance_id"], "new-instance")
        self.assertNotIn(b"commands/new-instance", self.git.run("show-ref")[1])
        before = local.run("show-ref")[1]
        with self.assertRaises(ValueError):
            init_mailbox(clone, "new-instance", "replacement")
        self.assertEqual(local.run("show-ref")[1], before)

    def test_symlink_brief_is_rejected_without_reading_filesystem_target(self):
        self.append(self.command("linked", brief_path="brief.md"))
        self.git.run("read-tree", self.head)
        oid = self.git.run("hash-object", "-w", "--stdin", data=b"/etc/passwd")[1].decode().strip()
        self.git.run("update-index", "--add", "--cacheinfo", f"120000,{oid},commands/linked/brief.md")
        tree = self.git.run("write-tree")[1].decode().strip()
        self.head = (
            self.git.run("commit-tree", tree, "-p", self.head, data=b"Symlink fixture\n")[1].decode().strip()
        )
        self.git.run("update-ref", "refs/heads/commands/test", self.head)
        self.advance(1)
        self.assertEqual(self.published("receipts/linked.json")["transport_state"], "REJECTED")
        self.assertFalse(self.service.list_tasks())
