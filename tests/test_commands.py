from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from sui_regulator.commands import load_registry, run_command
from sui_regulator.mcp import MCPBridge
from sui_regulator.models import DomainError
from sui_regulator.protocol import SuiProtocol
from tests.helpers import ProcessTestCase


def write_registry(path: Path, body: str) -> Path:
    # Explicit chmod: mkdir's mode is filtered by umask.
    path.write_text(body)
    os.chmod(path, 0o600)
    return path


class CommandRegistryTests(unittest.TestCase):
    def test_empty_path_is_no_templates(self):
        self.assertEqual(load_registry(""), {})
        self.assertEqual(load_registry(None), {})

    def test_registry_file_must_be_private(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            public = Path(temp) / "public.toml"
            write_registry(public, '[command.echo]\nargv = ["echo"]\npatterns = {}\n')
            os.chmod(public, 0o644)
            with self.assertRaises(DomainError) as caught:
                load_registry(str(public))
            self.assertIn("0700", str(caught.exception))
            os.chmod(public, 0o600)
            self.assertIn("echo", load_registry(str(public)))

    def test_patterns_are_validated_and_normalized(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            file = write_registry(
                Path(temp) / "commands.toml",
                "[command.greet]\n"
                'argv = ["echo", "hi"]\n'
                'patterns = {0 = "[a-z]+"}\n',
            )
            templates = load_registry(str(file))
            self.assertEqual(templates["greet"]["patterns"], {0: "[a-z]+"})
            bad = write_registry(
                Path(temp) / "bad.toml",
                "[command.greet]\nargv = [\"echo\"]\npatterns = {0 = \"[unclosed\"}\n",
            )
            with self.assertRaises(DomainError):
                load_registry(str(bad))


class RunCommandTests(ProcessTestCase):
    def registry(self) -> Path:
        return write_registry(
            Path(self.temp.name) / "commands.toml",
            '[command.patrol_check]\n'
            'argv = ["echo", "patrol-ok"]\n'
            "patterns = {}\n"
            '\n[command.remote_status]\n'
            'argv = ["echo", "remote"]\n'
            'patterns = {0 = "[a-z0-9_.-/]{1,32}"}\n',
        )

    def task_and_bridge(self, template: Path | None):
        tid = self.submit()
        credentials = SuiProtocol(self.service).register(tid)
        config = self.config
        if template is not None:
            object.__setattr__(config, "command_registry", str(template))
        bridge = MCPBridge(self.service, credentials)
        return credentials, bridge

    def test_run_command_executes_approved_template_and_audits(self):
        credentials, bridge = self.task_and_bridge(self.registry())
        reply = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "run_command",
                           "arguments": {"template": "patrol_check", "arguments": []}},
            }
        )
        self.assertFalse(reply["result"]["isError"], reply["result"]["content"][0]["text"])
        result = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("patrol-ok", result["stdout"])
        events = [row for row in self.service.events(task_id=credentials.task_id) if row["kind"] == "COMMAND_EXECUTED"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["data"]["template"], "patrol_check")

    def test_unapproved_template_and_pattern_mismatch_are_rejected(self):
        credentials, bridge = self.task_and_bridge(self.registry())
        for name, arguments in [
            ("not_listed", []),
            ("remote_status", ["; rm -rf /"]),
            ("remote_status", ["ok$ecret"]),
            ("remote_status", []),
        ]:
            reply = bridge.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "run_command", "arguments": {"template": name, "arguments": arguments}},
                }
            )
            self.assertTrue(reply["result"]["isError"], (name, arguments))

    def test_no_registry_means_no_executable_templates(self):
        credentials, bridge = self.task_and_bridge(None)
        reply = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"template": "patrol_check", "arguments": []}},
            }
        )
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("not approved", reply["result"]["content"][0]["text"])

    def test_stale_lease_fenced_after_execution(self):
        registry = self.registry()
        credentials, bridge = self.task_and_bridge(registry)
        # Expire the lease so the post-execution fence fails; the audit trail
        # must still show no COMMAND_EXECUTED event for a fenced call.
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE workers SET lease_expires=0 WHERE id=?", (credentials.worker_id,))
        reply = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"template": "patrol_check", "arguments": []}},
            }
        )
        self.assertTrue(reply["result"]["isError"])
        events = [
            row for row in self.service.events(task_id=credentials.task_id) if row["kind"] == "COMMAND_EXECUTED"
        ]
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
