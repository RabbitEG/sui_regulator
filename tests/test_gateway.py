from __future__ import annotations

import json
import os
import socket
import subprocess
import sys

from sui_regulator.gateway import Dispatcher, Gateway, call
from sui_regulator.mcp import MCPBridge
from sui_regulator.processes import child_environment
from sui_regulator.protocol import SuiProtocol
from tests.helpers import ProcessTestCase


class GatewayTests(ProcessTestCase):
    def test_T01_T14_T15_socket_submit_query_instruct_receipt(self):
        with Gateway(self.service) as gateway:
            self.assertEqual(gateway.path.stat().st_mode & 0o777, 0o600)
            command = call(
                self.config.home,
                "yan.submit_task",
                {
                    "idempotency_key": "gateway-task",
                    "payload": {"title": "socket task", "adapter": "unavailable"},
                },
            )["result"]
            self.service.process_commands()
            task = call(self.config.home, "yan.get_task", {"task_id": command["task_id"]})["result"]
            self.assertEqual(task["status"], "READY")
            receipt = call(
                self.config.home,
                "yan.instruct_task",
                {
                    "idempotency_key": "append",
                    "task_id": task["id"],
                    "payload": {"instruction": "synthetic instruction"},
                },
            )["result"]
            self.service.process_commands()
            status = call(self.config.home, "yan.get_command_status", {"command_id": receipt["id"]})["result"]
            self.assertEqual(status["status"], "SUCCEEDED")
            self.assertEqual(status["actor"], f"uid:{os.getuid()}")

    def test_socket_rejects_bad_json_and_remains_available(self):
        with Gateway(self.service):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(self.config.home / "yan.sock"))
                client.sendall(b"not json\n")
                self.assertIn("INVALID_JSON", client.recv(4096).decode())
            self.assertIn("result", call(self.config.home, "yan.get_overview", {}))

    def test_actor_spoof_and_unknown_methods_and_versions_are_rejected(self):
        dispatch = Dispatcher(self.service)
        for request in [
            {"protocol_version": "wrong", "method": "yan.list_tasks", "params": {}},
            {"protocol_version": "0.1", "method": "sui.register", "params": {}},
            {
                "protocol_version": "0.1",
                "method": "yan.submit_task",
                "params": {"actor": "admin", "idempotency_key": "x", "payload": {}},
            },
            {"protocol_version": "0.1", "method": "yan.shell", "params": {"command": "bad"}},
        ]:
            self.assertIn("error", dispatch.dispatch(request, "uid:tester"))

    def test_mcp_worker_scoped_tools_and_stale_lease_are_errors(self):
        tid = self.submit(adapter="unavailable")
        credentials = SuiProtocol(self.service).register(tid)
        bridge = MCPBridge(self.service, credentials)
        self.assertNotIn("register", [tool["name"] for tool in bridge.tools()])
        self.assertTrue(
            bridge.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "register", "arguments": {}},
                }
            )["result"]["isError"]
        )
        self.service.command("cancel_task", "tester", "cancel", {}, tid)
        self.service.process_commands()
        reply = bridge.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "report_progress", "arguments": {"phase": "BAD", "summary": "stale"}},
            }
        )
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("FENCED", reply["result"]["content"][0]["text"])

    def test_stdio_mcp_initialization_and_json_only_output(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        result = subprocess.run(
            [sys.executable, "-m", "sui_regulator.mcp", "--home", str(self.config.home), "--mode", "yan"],
            input="\n".join(map(json.dumps, messages)) + "\n",
            text=True,
            capture_output=True,
            env=child_environment(),
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        values = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(values), 2)
        self.assertEqual(values[1]["result"]["tools"][0]["name"], "yan_request")
