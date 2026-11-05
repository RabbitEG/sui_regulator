"""Real HTTP/RPC/browser check for trusted task creation and policy receipts."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from sui_regulator.console_demo import ConsoleDemo

out = Path("docs/verification/trusted-autonomy")
out.mkdir(parents=True, exist_ok=True)
with ConsoleDemo(port=0) as demo, sync_playwright() as pw:
    browser = pw.chromium.launch(
        executable_path="/usr/bin/chromium-browser",
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    page = browser.new_page(viewport={"width": 1440, "height": 1100})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(demo.url + "#/tasks")
    expect(page.locator("#controller-badge")).to_contain_text("在线")
    page.locator("[data-create-task]").click()
    page.locator("#task-title").fill("合成无人值守任务")
    page.locator("#task-objective").fill("核验、调试并继续实验；仅演示授权模式。")
    page.locator("#task-permission").select_option("trusted")
    expect(page.locator("#task-profile")).to_have_value("claude_code")
    expect(page.locator("#task-recovery")).to_have_value("persistent")
    page.locator("#task-permission").scroll_into_view_if_needed()
    page.screenshot(path=str(out / "create-trusted-zh.png"), full_page=True)
    page.locator("#task-adapter").select_option("mock")
    page.locator("#task-submit").click()
    expect(page.locator("#task-dialog")).not_to_be_visible()

    def created():
        return next((t for t in demo.service.list_tasks() if t["title"] == "合成无人值守任务"), None)

    demo.wait(created)
    tid = created()["id"]
    demo.service.command("hold_task", "browser-fixture", "hold-created", {}, tid)
    demo.wait(lambda: demo.service.get_task(tid)["desired"] == "HOLD")
    page.locator("#receipt-close").click()
    assert created()["permission_mode"] == "trusted"
    assert created()["recovery_mode"] == "persistent"
    page.goto(demo.url + "#/tasks/" + tid)
    expect(page.locator("[data-permission-mode]")).to_contain_text("信任")
    page.screenshot(path=str(out / "trusted-task-zh.png"), full_page=True)
    page.locator('[data-command="set_recovery_policy"]').click()
    expect(page.locator("#policy-permission")).to_have_value("trusted")
    page.locator("#policy-permission").select_option("guarded")
    page.locator("#dialog-submit").click()
    demo.wait(lambda: demo.service.get_task(tid)["permission_mode"] == "guarded")
    expect(page.locator("#command-dialog")).not_to_be_visible()
    page.locator("#receipt-close").click()
    page.locator("#refresh-now").click()
    page.locator("#language").select_option("en")
    expect(page.locator("[data-permission-mode]")).to_have_text("Guarded", timeout=15000)
    assert not errors, errors
    print("PASS: real browser create/receipt/detail/policy update and English mode; screenshots " + str(out))
    browser.close()
