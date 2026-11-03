"""Owned-process console drill. Never attaches to an existing state directory."""

from __future__ import annotations

import argparse
import json
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path

from .config import Config
from .models import Credentials, DomainError, encode
from .processes import child_environment, detached, signal_owned
from .protocol import SuiProxyProtocol
from .service import Service


class DemoProxyAdapter:
    def start(self, service, credentials):
        return detached(
            "sui_regulator.console_demo",
            ["proxy", "--home", str(service.config.home)],
            input_data=SuiProxyProtocol.credentials_json(credentials),
        )


def run_proxy(config, credentials):
    from .proxy_process import run

    manifest = json.loads((config.home / "console-demo.json").read_text())
    if credentials.task_id != manifest["recovery"] or credentials.generation != 1:
        run(config, credentials)
        return
    from .failures.engine import DetectionEngine
    from .failures.models import Observation
    from .failures.persistence import publish

    service, engine = Service(config), DetectionEngine(session_id=credentials.worker_id)
    protocol = SuiProxyProtocol(service)
    task = protocol.claim_task(credentials)
    job = protocol.launch_job(credentials, "primary-trial", task["spec"])
    protocol.attach_job(credentials, job["id"])
    protocol.report_progress(
        credentials,
        phase="WAITING_JOB",
        summary="合成故障演示：已连接独立 Runner",
        job_ids=[job["id"]],
        next_action="原 Job 继续运行，等待流式故障注入",
    )
    started = time.monotonic()
    while service.get_job(job["id"])["status"] != "RUNNING" or time.monotonic() - started < 2:
        protocol.heartbeat(credentials)
        time.sleep(0.1)
    for _ in range(8):
        protocol.heartbeat(credentials)
        incidents = engine.feed(
            Observation(
                time.time(),
                "text_delta",
                {"message_id": "console-synthetic-stream", "text": "echo " * 8},
                "synthetic-console-drill",
            )
        )
        publish(service, credentials, incidents)
        time.sleep(0.04)
    while True:
        protocol.heartbeat(credentials)
        time.sleep(0.1)


class ConsoleDemo(AbstractContextManager):
    def __init__(self, home: Path | None = None, port=8765):
        self.temp = tempfile.TemporaryDirectory(prefix="yan-console-demo-") if home is None else None
        self.home = Path(self.temp.name) / "state" if self.temp else home.expanduser().absolute()
        self.port, self.controller, self.console = port, None, None
        self.service = None

    def wait(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        raise TimeoutError("dedicated console drill did not reach the expected state")

    def __enter__(self):
        if self.home.exists():
            raise ValueError("console demo requires a new state directory; existing ledgers are never used")
        try:
            config = replace(
                Config(self.home),
                heartbeat_seconds=0.2,
                lease_seconds=6,
                tick_seconds=0.05,
                startup_grace=2,
                restart_cooldown=0.4,
                max_workers=6,
                max_session_age=30,
                rotation_grace=0.5,
            )
            config.write()
            self.service = Service(config)
            manifest = {}
            for key, title, steps in [
                ("recovery", "跨代际连续性验证", 3000),
                ("steady", "合成指标稳态观测", 3000),
                ("unknown", "未知作业结果核验", 3000),
                ("completed", "历史完成回归", 5),
                ("held", "等待人工指令的任务", 3000),
            ]:
                receipt = self.service.command(
                    "submit_task",
                    "console-demo",
                    key,
                    {
                        "title": title,
                        "objective": "本地合成演示；核验代理会话、执行账本与恢复行为，不产生科研结论。",
                        "adapter": "mock",
                        "spec": {"steps": steps, "interval": 0.1},
                    },
                )
                manifest[key] = receipt["task_id"]
            self.service.process_commands()
            self.service.command("hold_task", "console-demo", "initial-hold", {}, manifest["held"])
            self.service.process_commands()
            (self.home / "console-demo.json").write_text(encode(manifest))
            self.tasks = manifest
            self.controller = subprocess.Popen(
                [sys.executable, "-m", "sui_regulator.console_demo", "controller", "--home", str(self.home)],
                env=child_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.console = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "sui_regulator",
                    "--home",
                    str(self.home),
                    "console",
                    "--port",
                    str(self.port),
                ],
                env=child_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            with selectors.DefaultSelector() as selector:
                selector.register(self.console.stdout, selectors.EVENT_READ)
                if not selector.select(6):
                    raise TimeoutError("console did not start")
                self.url = json.loads(self.console.stdout.readline())["console"]
            self.wait(lambda: self.service.get_task(manifest["recovery"])["generation"] >= 2)
            self.wait(
                lambda: (
                    bool(self.service.get_task(manifest["unknown"])["jobs"])
                    and self.service.get_task(manifest["unknown"])["jobs"][0]["status"] == "RUNNING"
                )
            )
            unknown_job = self.service.get_task(manifest["unknown"])["jobs"][0]
            signal_owned(unknown_job["identity"], signal.SIGKILL)
            self.wait(lambda: self.service.get_job(unknown_job["id"])["status"] == "UNKNOWN")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        for process in (self.console, self.controller):
            if process:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
        if self.console and self.console.stdout:
            self.console.stdout.close()
        if self.service:
            for table in ("workers", "jobs"):
                for row in self.service.store.rows("SELECT * FROM " + table):
                    for key in ("child_identity", "identity"):
                        value = row.get(key)
                        signal_owned(json.loads(value) if value else None, signal.SIGKILL)
        if self.temp:
            self.temp.cleanup()


def run_console_demo(home=None, port=8765):
    with ConsoleDemo(home, port) as demo:
        print(
            encode(
                {
                    "console": demo.url,
                    "home": str(demo.home),
                    "synthetic": True,
                    "scenarios": demo.tasks,
                    "exit": "Ctrl+C cleans up only this drill's owned processes",
                }
            ),
            flush=True,
        )
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["controller", "proxy"])
    parser.add_argument("--home", required=True, type=Path)
    args = parser.parse_args()
    if args.role == "controller":
        from .controller import Controller
        from .gateway import Gateway

        with Controller(Config.load(args.home), adapters={"mock": DemoProxyAdapter()}) as controller:
            with Gateway(controller.service):
                controller.run()
    else:
        try:
            run_proxy(Config.load(args.home), Credentials(**json.loads(sys.stdin.read(16384))))
        except DomainError:
            pass


if __name__ == "__main__":
    main()
