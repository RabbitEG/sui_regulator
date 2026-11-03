from __future__ import annotations

import json
import sys
from pathlib import Path

from sui_regulator.claude_adapter import build_command
from sui_regulator.commands import run_command
from sui_regulator.models import DomainError
from sui_regulator.protocol import SuiProtocol
from tests.helpers import ProcessTestCase
from tests.test_commands import write_registry


class TaskBriefTests(ProcessTestCase):
    def test_failed_verification_requires_debugging_before_completion(self):
        project = Path(self.temp.name) / "project"
        project.mkdir()
        check = project / "check.py"
        check.write_text("raise SystemExit(1)\n")
        registry = write_registry(Path(self.temp.name) / "commands.toml",
            '[command.verify]\nargv = ' + json.dumps([sys.executable, str(check)]) + '\n')
        object.__setattr__(self.config, "command_registry", str(registry))
        tid = self.submit(workspace=str(project), completion_criteria="check.py passes", verification_template="verify")
        protocol = SuiProtocol(self.service)
        credentials = protocol.register(tid)
        with self.assertRaisesRegex(DomainError, "verification command failed"):
            protocol.complete_task(credentials, "done", evidence="claimed success")
        self.assertEqual(self.service.get_task(tid)["desired"], "ACTIVE")
        check.write_text("raise SystemExit(0)\n")
        protocol.complete_task(credentials, "fixed and verified", evidence="check.py exits zero")
        events = self.service.events(tid)
        self.assertEqual([e["data"]["exit_code"] for e in events if e["kind"] == "COMMAND_EXECUTED"], [1, 0])
        self.assertEqual(self.service.get_task(tid)["desired"], "COMPLETED")

    def test_revoked_proxy_cannot_execute_any_command(self):
        marker = Path(self.temp.name) / "must-not-exist"
        registry = write_registry(Path(self.temp.name) / "commands.toml",
            '[command.effect]\nargv = ' + json.dumps([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])+ '\n')
        object.__setattr__(self.config, "command_registry", str(registry))
        tid = self.submit()
        credentials = SuiProtocol(self.service).register(tid)
        self.service.command("hold_task", "tester", "hold-effect", {}, tid)
        self.service.process_commands()
        with self.assertRaises(DomainError):
            run_command(self.service, credentials, "effect")
        self.assertFalse(marker.exists())

    def test_complete_brief_and_project_survive_generation_change(self):
        workspace = Path(self.temp.name) / "experiment"
        workspace.mkdir()
        objective = "等现有实验结束。\n核验指标、修复失败，再做下一轮；不要只巡检。"
        receipt = self.service.command("submit_task", "tester", "brief", {
            "title": "完整实验闭环", "objective": objective, "adapter": "claude",
            "workspace": str(workspace), "execution_profile": "claude_code",
            "completion_criteria": "测试通过且验证集指标达到约定阈值；附结果路径。",
        })
        self.service.process_commands()
        applied = self.service.get_command(receipt["id"])
        self.assertEqual(applied["status"], "SUCCEEDED", applied)
        protocol = SuiProtocol(self.service)
        first = protocol.register(receipt["task_id"])
        context = protocol.get_task_context(first)
        self.assertEqual(context["objective"], objective)
        self.assertEqual(context["workspace"], str(workspace))
        self.assertEqual(context["execution_profile"], "claude_code")
        self.service.command("hold_task", "tester", "hold", {}, first.task_id)
        self.service.process_commands()
        self.service.command("resume_task", "tester", "resume", {}, first.task_id)
        self.service.process_commands()
        second = protocol.register(first.task_id)
        restored = protocol.get_task_context(second)
        for field in ("objective", "workspace", "completion_criteria", "execution_profile"):
            self.assertEqual(restored[field], context[field])
        argv = build_command(self.config, "session", execution_profile=restored["execution_profile"])
        self.assertIn("Bash", argv[argv.index("--tools") + 1].split(","))
        self.assertIn("Edit", argv[argv.index("--tools") + 1].split(","))
