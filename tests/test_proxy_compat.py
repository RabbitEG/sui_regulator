from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from sui_regulator.adapters import MockSuiProxyAdapter, MockWorkerAdapter
from sui_regulator.config import Config
from sui_regulator.console_demo import ConsoleDemo
from sui_regulator.gateway import Dispatcher
from sui_regulator.models import Credentials, ProxyCredentials
from sui_regulator.registry import ProxyRegistry, WorkerRegistry


class ProxyCompatibilityTests(unittest.TestCase):
    def test_legacy_config_imports_credentials_and_health_query_remain_compatible(self):
        self.assertIs(MockSuiProxyAdapter, MockWorkerAdapter)
        self.assertIs(ProxyRegistry, WorkerRegistry)
        credentials = ProxyCredentials("task", "sui", 1, "synthetic")
        self.assertIsInstance(credentials, Credentials)
        self.assertEqual(credentials.proxy_id, credentials.worker_id)
        self.assertEqual(set(asdict(credentials)), {"task_id", "worker_id", "generation", "lease_token"})
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for field in ("max_workers", "max_proxies"):
                (home / "config.toml").write_text(f"[regulator]\n{field} = 6\n")
                self.assertEqual(Config.load(home).max_proxies, 6)
            (home / "config.toml").write_text("[regulator]\nmax_workers = 6\nmax_proxies = 7\n")
            with self.assertRaises(ValueError):
                Config.load(home)
            (home / "config.toml").unlink()
            Config(home).write()
            self.assertIn("max_proxies = 4", (home / "config.toml").read_text())
            self.assertEqual(Config.load(home).max_workers, 4)

    def test_owned_demo_recovers_once_keeps_job_and_exposes_unknown_result(self):
        with ConsoleDemo(port=0) as demo:
            task = demo.service.get_task(demo.tasks["recovery"])
            self.assertGreaterEqual(task["generation"], 2)
            self.assertEqual(len(task["jobs"]), 1)
            self.assertEqual(task["execution_count"], 1)
            self.assertEqual(task["current_proxy"], task["current_worker"])
            self.assertTrue(
                any(i["rule_id"] == "GEN-001" and i["status"] == "APPLIED" for i in task["incidents"])
            )
            dispatcher = Dispatcher(demo.service)
            old = dispatcher.query("get_worker_health", {"task_id": task["id"]})
            new = dispatcher.query("get_proxy_health", {"task_id": task["id"]})
            self.assertEqual([p["id"] for p in old], [p["proxy_id"] for p in new])
            unknown = demo.service.get_task(demo.tasks["unknown"])
            self.assertEqual(unknown["jobs"][0]["status"], "UNKNOWN")
            self.assertEqual(unknown["execution_count"], 1)
            with self.assertRaises(ValueError):
                ConsoleDemo(demo.home, port=0).__enter__()
            self.assertEqual(
                json.loads((demo.home / "console-demo.json").read_text())["recovery"], task["id"]
            )
