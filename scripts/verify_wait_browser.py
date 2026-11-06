"""Read the real wake receipt in both Console languages, using a disposable ledger."""

import json
import signal
import threading
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from sui_regulator.console_demo import ConsoleDemo
from sui_regulator.protocol import SuiProtocol
from sui_regulator.tool_hook import handle

out = Path("docs/verification/wait-observation")
out.mkdir(parents=True, exist_ok=True)
with ConsoleDemo(port=0) as demo, sync_playwright() as pw:
    demo.controller.send_signal(signal.SIGSTOP)
    protocol = SuiProtocol(demo.service)
    try:
        receipt = demo.service.command("submit_task", "browser-fixture", "wait-observation", {
            "title": "等待失效后继续核验", "objective": "仅验证等待回执，不提交真实实验。",
            "adapter": "unavailable", "permission_mode": "trusted",
        })
        demo.service.process_commands()
        tid = receipt["task_id"]
        credentials = protocol.register(tid)
        protocol.claim_task(credentials)
        event = {"tool_name": "Bash", "tool_use_id": "fixture-call", "tool_input": {"command": "sleep 600"}}
        handle(demo.service, credentials, {**event, "hook_event_name": "PreToolUse"})
        protocol.wait_task(credentials, "检查原操作结果，再决定是否继续实验", 600)
        handle(demo.service, credentials, {**event, "hook_event_name": "PostToolUseFailure",
                                          "error": "synthetic terminal observation"})
    finally:
        demo.controller.send_signal(signal.SIGCONT)

    stop = threading.Event()

    def heartbeat():
        while not stop.wait(.2):
            protocol.heartbeat(credentials)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    browser = pw.chromium.launch(executable_path="/usr/bin/chromium-browser", headless=True,
                                 args=["--no-sandbox", "--disable-dev-shm-usage"])
    try:
        demo.wait(lambda: demo.service.get_task(tid)["wait_state"].get("state") == "woken")
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(demo.url + "#/tasks/" + tid)
        panel = page.locator("[data-wait-state]")
        expect(panel).to_contain_text("本次唤醒原因")
        expect(panel).to_contain_text("原生操作已结束或失去观测，请核验回执")
        expect(panel).to_contain_text("保留原会话等待")
        panel.locator("..").screenshot(path=str(out / "wake-reason-zh.png"))
        page.locator("#language").select_option("en")
        expect(panel).to_contain_text("Native operation ended or observation was lost; inspect its receipt")
        panel.locator("..").screenshot(path=str(out / "wake-reason-en.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert not errors, errors
        task = demo.service.get_task(tid)
        assert task["generation"] == 1 and task["current_worker"] == credentials.worker_id
        report = {"checks": ["wake_reason_zh", "wake_reason_en", "mobile_no_overflow", "same_proxy"],
                  "browser_errors": errors, "wake_reason": task["wait_state"]["wake_reason"]}
        (out / "browser.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        browser.close()
        stop.set()
        thread.join(timeout=2)
