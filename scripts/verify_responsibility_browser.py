#!/usr/bin/env python3
"""Real local Console/Controller/SQLite; synthetic Proxy declarations, no model API."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sui_regulator.console_demo import ConsoleDemo  # noqa: E402
from sui_regulator.protocol import SuiProtocol  # noqa: E402


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", default="/usr/bin/chromium-browser")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots/responsibility")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/responsibility-browser-report.json")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    errors, checks = [], []
    with ConsoleDemo(port=0) as demo, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=args.browser, headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100}, locale="zh-CN")
        page.on("pageerror", lambda error: errors.append(str(error)))
        tid = demo.tasks["held"]
        for number in range(15):
            demo.service.command(
                "instruct_task",
                "browser-check",
                f"instruction-{number}",
                {"instruction": f"完整追加要求 {number}: 核验实际结果，保留已有运行 ID。"},
                tid,
            )
        demo.wait(lambda: demo.service.get_task(tid)["history_refs"]["instructions"]["total"] == 15)
        page.goto(demo.url + "#/tasks/" + tid)
        expect(page.locator("#controller-badge")).to_contain_text("在线")
        page.get_by_role("tab", name="敕令回执").click()
        expect(page.locator("main")).to_contain_text("入账成功不代表已交付")
        expect(page.locator("main")).to_contain_text("未确认")
        page.get_by_role("tab", name="历史案卷").click()
        page.locator('[data-history="instructions"]').click()
        expect(page.locator("[data-history-panel]")).to_contain_text("当前 10 条")
        page.locator("[data-after]").click()
        expect(page.locator("[data-history-panel]")).to_contain_text("当前 5 条")
        page.locator("[data-history-panel] details").last.locator("summary").click()
        expect(page.locator("[data-history-panel]")).to_contain_text("完整追加要求 14")
        checks.extend(
            [
                "stored_not_delivered_or_acknowledged",
                "real_history_cursor_two_pages",
                "lossless_instruction_body",
            ]
        )
        page.locator("[data-history-panel]").screenshot(path=str(args.output / "instructions-history.png"))
        page.locator('[data-command="resume_task"]').click()
        page.locator("#dialog-submit").click()
        expect(page.locator("#command-dialog")).not_to_be_visible()
        demo.wait(
            lambda: all(r["acknowledged_at"] for r in demo.service.get_task(tid)["instruction_delivery"])
        )
        page.locator("#receipt-close").click()
        page.locator("#refresh-now").click()
        page.get_by_role("tab", name="敕令回执").click()
        expect(page.locator("main")).to_contain_text("已读确认是代理声明")
        assert all(r["acknowledged_at"] for r in demo.service.get_task(tid)["instruction_delivery"])
        checks.append("mock_proxy_explicit_ack_on_same_api_execution_unknown")
        expect(page.locator("[data-instruction-delivery]")).not_to_contain_text("未确认", timeout=15000)
        expect(page.locator("[data-instruction-delivery]")).to_contain_text("已确认读取")
        page.locator("[data-instruction-delivery]").locator("..").screenshot(
            path=str(args.output / "instruction-ack.png")
        )

        # Pause only this disposable Controller to install an owner with no process;
        # the public waiting protocol creates the state, no direct SQL fabrication.
        demo.controller.send_signal(signal.SIGSTOP)
        try:
            command = demo.service.command(
                "submit_task",
                "browser-check",
                "waiting-task",
                {
                    "title": "等待依赖 · 合作式接续",
                    "objective": "等待资源；下次检查后再决定下一步。",
                    "adapter": "unavailable",
                },
            )
            demo.service.process_commands()
            waiting_id = command["task_id"]
            protocol = SuiProtocol(demo.service)
            credentials = protocol.register(waiting_id)
            first_proxy = credentials.worker_id
            for number in range(51):
                demo.service.command("hold_task", "browser-check", f"old-hold-{number}", {}, waiting_id)
                demo.service.process_commands()
                demo.service.command("resume_task", "browser-check", f"old-resume-{number}", {}, waiting_id)
                demo.service.process_commands()
                credentials = protocol.register(waiting_id)
            protocol.wait_task(credentials, "依赖尚未就绪，由秉烛人保留委托并定时唤醒", 600)
        finally:
            demo.controller.send_signal(signal.SIGCONT)
        page.goto(demo.url + "#/tasks/" + waiting_id)
        expect(page.locator("[data-wait-state]")).to_contain_text("依赖尚未就绪")
        expect(page.locator("[data-wait-state]")).to_contain_text("candleholder")
        expect(page.locator("[data-wait-state]")).to_contain_text("暂停与取消优先")
        assert demo.service.get_task(waiting_id)["current_worker"] is None
        checks.append("durable_wait_reason_owner_next_check_without_proxy_churn")
        page.locator("[data-wait-state]").locator("..").screenshot(path=str(args.output / "durable-wait.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        checks.append("new_wait_panel_mobile_no_page_overflow")
        assert first_proxy not in [w["id"] for w in demo.service.get_task(waiting_id)["workers"]]
        page.goto(demo.url + "#/sessions/" + waiting_id + "/" + first_proxy)
        expect(page.locator("h1")).to_contain_text("第 1 代代理", timeout=15000)
        expect(page.locator("main")).to_contain_text(first_proxy)
        checks.append("old_proxy_beyond_preview_opens_by_exact_record_lookup")
        assert not errors, errors
        checks.append("no_browser_errors")
        browser.close()
    report = {
        "passed": True,
        "checks": checks,
        "scope": "Real local control plane and Chromium; synthetic Proxy read declarations; no real GLM",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
