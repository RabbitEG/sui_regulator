from __future__ import annotations

import json
import os
import selectors
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from sui_regulator.claude_adapter import runtime_environment
from sui_regulator.config import Config
from sui_regulator.models import Credentials
from tests.fake_api import FakeAnthropic
from tests.test_claude import NO_PROXIES


@unittest.skipUnless(
    os.environ.get("SUI_TEST_REAL_CLAUDE") == "1" and shutil.which("claude"),
    "opt-in installed CLI with loopback fake API",
)
class StreamCapabilityTests(unittest.TestCase):
    def test_partial_text_arrives_before_api_response_finishes(self):
        with (
            tempfile.TemporaryDirectory(prefix="sui-stream-probe-") as tmp,
            FakeAnthropic("streaming") as backend,
        ):
            config = Config(Path(tmp) / "state")
            config.home.mkdir(mode=0o700)
            env = runtime_environment(config, Credentials("synthetic", "synthetic", 1, "synthetic"))
            env.update(
                {
                    **NO_PROXIES,
                    "ANTHROPIC_BASE_URL": backend.url,
                    "ANTHROPIC_API_KEY": "sk-ant-synthetic-local-only",
                    "ANTHROPIC_AUTH_TOKEN": "",
                }
            )
            command = [
                "claude",
                "--print",
                "--bare",
                "--disable-slash-commands",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--tools",
                "",
            ]
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=tmp,
                env=env,
            )
            try:
                process.stdin.write(b"Return a synthetic capability probe only.")
                process.stdin.close()
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 12
                found = False
                while time.monotonic() < deadline:
                    if not selector.select(0.1):
                        continue
                    raw = process.stdout.readline()
                    if not raw:
                        break
                    value = json.loads(raw)
                    if (
                        value.get("type") == "stream_event"
                        and value.get("event", {}).get("delta", {}).get("type") == "text_delta"
                    ):
                        self.assertFalse(
                            backend.stream_finished.is_set(),
                            "delta was buffered until the API response ended",
                        )
                        found = True
                        break
                selector.close()
                self.assertTrue(found, "installed CLI did not expose live text_delta")
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
                process.stdout.close()
