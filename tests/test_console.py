from __future__ import annotations

import http.client
import json
import time
from pathlib import Path
from unittest.mock import patch

from sui_regulator.console import Console
from sui_regulator.console_read import snapshot
from sui_regulator.gateway import Gateway
from sui_regulator.models import DomainError
from sui_regulator.processes import alive
from sui_regulator.protocol import SuiProtocol
from tests.helpers import ProcessTestCase


class ConsoleTests(ProcessTestCase):
    def setUp(self):
        super().setUp()
        self.gateway = Gateway(self.service).__enter__()
        self.addCleanup(self.gateway.__exit__, None, None, None)
        self.console = Console(self.config.home, 0).__enter__()
        self.console.broker.ttl = 0
        self.addCleanup(self.console.__exit__, None, None, None)

    def request(self, path="/api/snapshot", body=None, headers=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.console.port, timeout=4)
        defaults = {"Content-Type": "application/json", "Origin": self.console.url, "X-Yan-Console": "1"}
        connection.request(
            method or ("POST" if body is not None else "GET"),
            path,
            json.dumps(body) if body is not None else None,
            {**defaults, **(headers or {})},
        )
        response = connection.getresponse()
        raw, status, received = response.read(), response.status, dict(response.getheaders())
        connection.close()
        return status, json.loads(raw), received

    def rpc(self, method, params, confirmed=False):
        return self.request(
            "/api/rpc", {"request": {"method": "yan." + method, "params": params}, "confirmed": confirmed}
        )

    def test_proxy_conversation_is_a_real_read_only_rpc_with_offline_snapshot(self):
        from sui_regulator.transcripts import SessionRecorder

        tid = self.submit()
        credentials = SuiProtocol(self.service).register(tid)
        recorder = SessionRecorder(self.config, credentials)
        recorder.write("text", "assistant", "actual recorded <script> text")
        params = {"task_id": tid, "proxy_id": credentials.worker_id}
        status, response, _ = self.rpc("get_proxy_transcript", params)
        self.assertEqual(status, 200)
        self.assertEqual(response["response"]["result"]["items"][0]["text"], "actual recorded <script> text")
        self.gateway.__exit__(None, None, None)
        status, cached, _ = self.rpc("get_proxy_transcript", params)
        self.assertEqual(status, 200)
        self.assertFalse(cached["connection"]["available"])
        self.assertEqual(cached["response"], response["response"])

    def test_snapshot_paginates_all_sessions_and_distinguishes_lost_lease(self):
        for _ in range(3):
            tid = self.submit(adapter="unavailable")
            credentials = SuiProtocol(self.service).register(tid)
            SuiProtocol(self.service).claim_task(credentials)
        with self.service.store.transaction() as conn:
            conn.execute("UPDATE workers SET lease_expires=0 WHERE id=?", (credentials.worker_id,))
        first = snapshot(self.service, "sessions", limit=1)
        second = snapshot(self.service, "sessions", limit=1, offset=1)
        self.assertEqual(first["total"], 3)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(first["items"][0]["health"], "LOST")
        self.assertNotIn("lease_hash", first["items"][0])
        self.assertEqual(first["controller"]["state"], "STARTING")

    def test_hold_resume_instruct_use_real_commands_and_receipts(self):
        tid = self.submit(spec={"steps": 80, "interval": 0.02})
        self.until(lambda: bool(self.service.get_task(tid)["jobs"]))
        for method, payload, confirmed in [
            ("hold_task", {}, True),
            ("resume_task", {}, False),
            ("instruct_task", {"instruction": "inspect original metrics"}, False),
        ]:
            params = {"task_id": tid, "idempotency_key": "console-" + method, "payload": payload}
            if method == "hold_task":
                self.assertEqual(self.rpc(method, params)[0], 400)
            code, data, _ = self.rpc(method, params, confirmed)
            self.assertEqual(code, 200, data)
            receipt = data["response"]["result"]
            self.assertEqual(receipt["status"], "QUEUED")
            self.controller.tick()
            code, result, _ = self.rpc("get_command_status", {"command_id": receipt["id"]})
            self.assertEqual(result["response"]["result"]["status"], "SUCCEEDED")
        task = self.service.get_task(tid)
        self.assertEqual(len(task["jobs"]), 1)
        self.assertEqual(task["desired"], "ACTIVE")

    def test_stale_controller_tick_is_not_online_and_cannot_receive_console_commands(self):
        tid = self.submit()
        self.controller.tick()
        self.service.controller_last_tick_at = time.time() - 60
        _, data, _ = self.request()
        self.assertEqual(data["response"]["result"]["controller"]["state"], "STALE")
        code, data, _ = self.rpc(
            "hold_task", {"task_id": tid, "idempotency_key": "stale", "payload": {}}, True
        )
        self.assertEqual(code, 503)
        self.assertEqual(data["error"]["code"], "CONTROLLER_NOT_READY")
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")

    def test_gateway_offline_returns_only_last_successful_snapshot_and_original_time(self):
        self.submit()
        self.controller.tick()
        _, first, _ = self.request()
        self.gateway.__exit__(None, None, None)
        _, second, _ = self.request()
        self.assertFalse(second["connection"]["available"])
        self.assertTrue(second["connection"]["cached"])
        self.assertEqual(second["connection"]["last_success_at"], first["connection"]["last_success_at"])
        self.assertEqual(
            second["response"]["result"]["queried_at"], first["response"]["result"]["queried_at"]
        )

    def test_console_without_controller_never_initializes_or_writes_ledger(self):
        absent = Path(self.temp.name) / "does-not-exist"
        from sui_regulator.console import Broker

        data = Broker(absent).read("yan.get_console_snapshot", {})
        self.assertFalse(data["connection"]["available"])
        self.assertIsNone(data["response"])
        self.assertFalse(absent.exists())

    def test_lost_command_reply_is_not_replayed_or_replaced_with_fake_receipt(self):
        from sui_regulator.console import Broker

        health = {"result": {"controller": {"state": "ONLINE"}}}
        request = {
            "method": "yan.instruct_task",
            "params": {
                "task_id": "synthetic",
                "idempotency_key": "stable",
                "payload": {"instruction": "inspect"},
            },
        }
        with patch(
            "sui_regulator.console.call", side_effect=[health, ConnectionResetError("lost reply")]
        ) as rpc:
            with self.assertRaises(DomainError) as error:
                Broker(self.config.home).write(request)
            self.assertEqual(error.exception.code, "DELIVERY_UNCONFIRMED")
            self.assertEqual(rpc.call_count, 2)
            self.assertEqual(rpc.call_args.args[2]["idempotency_key"], "stable")

    def test_query_size_error_is_not_reported_as_controller_offline(self):
        from sui_regulator.console import Broker

        with patch(
            "sui_regulator.console.call", side_effect=DomainError("MESSAGE_TOO_LARGE", "narrow query")
        ):
            result = Broker(self.config.home).read("yan.get_task", {"task_id": "synthetic"})
        self.assertTrue(result["connection"]["available"])
        self.assertEqual(result["response"]["error"]["code"], "MESSAGE_TOO_LARGE")

    def test_http_security_rejects_cross_origin_rebinding_and_unsupported_operations(self):
        for headers in [
            {"Origin": "http://evil.test"},
            {"Host": "evil.test", "Origin": "http://evil.test"},
            {"Sec-Fetch-Site": "cross-site"},
        ]:
            self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(
            self.rpc("cancel_job", {"task_id": "fake", "payload": {}, "idempotency_key": "nope"}, True)[0],
            403,
        )
        self.assertEqual(self.request("/../config.toml")[0], 404)
        status, _, headers = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.console.server.server_address[0], "127.0.0.1")
        # SSH local forwarding may preserve a different loopback Host port.
        self.assertEqual(
            self.request(headers={"Host": "localhost:9999", "Origin": "http://localhost:9999"})[0], 200
        )

    def test_stopping_console_does_not_stop_worker_controller_or_job(self):
        tid = self.submit(spec={"steps": 60, "interval": 0.02})
        self.until(
            lambda: (
                bool(self.service.get_task(tid)["jobs"])
                and self.service.get_task(tid)["jobs"][0]["status"] == "RUNNING"
            )
        )
        before = self.service.get_task(tid)
        self.console.__exit__(None, None, None)
        self.assertTrue(alive(before["jobs"][0]["identity"]))
        self.assertTrue(alive(before["workers"][0]["identity"]))
        self.until(lambda: self.service.get_task(tid)["status"] == "COMPLETED")
        self.assertEqual(self.service.get_task(tid)["jobs"][0]["pid"], before["jobs"][0]["pid"])
        self.assertEqual(len(self.service.store.rows("SELECT * FROM executions")), 1)
