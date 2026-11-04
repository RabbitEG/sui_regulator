#!/usr/bin/env python3
"""Optional real-browser, real-RPC console acceptance drill. Synthetic data only."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sui_regulator.console_demo import ConsoleDemo  # noqa: E402
from sui_regulator.processes import alive, signal_owned  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="/usr/bin/chromium-browser")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/console-browser-report.json")
    args = parser.parse_args()
    from playwright.sync_api import expect, sync_playwright

    args.output.mkdir(parents=True, exist_ok=True)
    checks, errors, external, requests = {}, [], [], []
    with ConsoleDemo(port=0) as demo, sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=args.browser,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking"],
        )
        page = browser.new_page(
            viewport={"width": 1440, "height": 1080}, locale="zh-CN", timezone_id="Asia/Shanghai"
        )
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
        page.on(
            "request",
            lambda request: external.append(request.url) if not request.url.startswith(demo.url) else None,
        )

        def visit(route, heading):
            page.goto(demo.url + "#/" + route)
            expect(page.locator("h1")).to_contain_text(heading)
            expect(page.locator("#controller-badge")).to_contain_text("在线")

        def screenshot(name):
            page.screenshot(path=str(args.output / name), full_page=True, animations="disabled")

        visit("overview", "工作台")
        expect(page.locator("main")).to_contain_text("GEN-001")
        screenshot("overview.png")
        checks["overview_real_incident_and_tick"] = True
        visit("sessions", "代理会话")
        expect(page.locator("main")).to_contain_text("Sui Proxy")
        screenshot("proxies.png")
        steady = demo.service.get_task(demo.tasks["steady"])
        steady_proxy = next(p for p in steady["workers"] if p["id"] == steady["current_worker"])
        signal_owned(steady_proxy["identity"], signal.SIGSTOP)
        try:
            page.wait_for_timeout(2100)
            page.locator("#refresh-now").click()
            expect(page.locator("main")).to_contain_text("进程冻结", timeout=5000)
            screenshot("proxy-unresponsive.png")
            checks["owned_proxy_freeze_is_not_reported_healthy"] = True
        finally:
            signal_owned(steady_proxy["identity"], signal.SIGCONT)
        recovery = demo.service.get_task(demo.tasks["recovery"])
        original_job = recovery["jobs"][0]
        active_proxy = recovery["current_worker"]
        visit(f"sessions/{recovery['id']}/{active_proxy}", "Generation")
        expect(page.locator("main")).to_contain_text("最后活动")
        expect(page.locator("main")).to_contain_text("同一 Task 的代际历史")
        screenshot("proxy-detail.png")
        checks["proxy_identity_generation_heartbeat_history"] = True
        visit("incidents", "故障事件")
        screenshot("incidents.png")
        applied = next(
            i for i in recovery["incidents"] if i["rule_id"] == "GEN-001" and i["status"] == "APPLIED"
        )
        visit(f"incidents/{recovery['id']}/{applied['sequence']}", "GEN-001")
        expect(page.locator("main")).to_contain_text("Detection Engine 建议")
        expect(page.locator("main")).to_contain_text("Candleholder 最终动作")
        expect(page.locator("main")).to_contain_text("text_delta")
        screenshot("incident-detail.png")
        checks["incident_recommendation_final_action_outcome"] = True
        visit(f"tasks/{recovery['id']}", recovery["title"])
        for label in ("DESIRED STATE", "DECLARED STATE", "OBSERVED STATE"):
            expect(page.locator("main")).to_contain_text(label)
        page.get_by_role("tab", name="执行核验").click()
        page.locator("[data-job]").first.click()
        expect(page.locator(".metric-chart")).to_be_visible(timeout=10000)
        screenshot("task-detail.png")
        checks["separate_states_real_metrics_jobs_events"] = True

        page.locator("[data-command='hold_task']").click()
        page.get_by_role("button", name="取消", exact=True).click()
        assert demo.service.get_task(recovery["id"])["desired"] == "ACTIVE"
        page.locator("[data-command='hold_task']").click()
        page.get_by_role("button", name="确认 Hold", exact=True).click()
        expect(page.locator("#dialog-error")).to_be_visible()
        page.locator("#hold-confirm").check()
        page.get_by_role("button", name="确认 Hold", exact=True).click()
        demo.wait(lambda: demo.service.get_task(recovery["id"])["desired"] == "HOLD")
        expect(page.locator("#receipt-list")).to_contain_text("已应用", timeout=15000)
        assert alive(original_job["identity"])
        page.locator("#receipt-list details").first.locator("summary").click()
        with page.expect_response(lambda r: "/api/snapshot" in r.url, timeout=15000):
            pass  # The normal polling interval must not close the result being read.
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#receipt-list details").first).to_have_attribute("open", "")
        checks["automatic_refresh_preserves_expanded_command_result"] = True
        page.locator("#receipt-close").click()
        page.locator("[data-command='resume_task']").click()
        page.get_by_role("button", name="确认 Resume", exact=True).click()
        demo.wait(lambda: demo.service.get_task(recovery["id"])["desired"] == "ACTIVE")
        expect(page.locator("#receipt-list .receipt-item").first).to_contain_text("已应用", timeout=15000)
        page.locator("#receipt-close").click()
        page.locator("[data-command='instruct_task']").click()
        instruction = "核验原 Job 指标；<script>window.__unsafe=1</script> 应作为普通数据。"
        page.locator("#instruction").fill(instruction)
        page.get_by_role("button", name="提交指令", exact=True).click()
        expect(page.locator("#receipt-list .receipt-item").first).to_contain_text("已应用", timeout=15000)
        screenshot("command-receipts.png")
        assert page.evaluate("window.__unsafe") is None
        persisted = demo.service.store.rows(
            "SELECT * FROM commands WHERE kind='instruct_task' ORDER BY created_at DESC LIMIT 1"
        )[0]
        assert json.loads(persisted["payload"])["instruction"] == instruction
        after = demo.service.get_task(recovery["id"])
        assert after["jobs"][0]["id"] == original_job["id"] and after["jobs"][0]["pid"] == original_job["pid"]
        assert len(after["jobs"]) == 1 and after["execution_count"] == 1
        checks["hold_explicit_confirmation_cancel_resume_instruct_real_receipts"] = True
        checks["same_task_job_pid_single_execution_after_recovery_and_controls"] = True
        checks["untrusted_instruction_is_text_not_script"] = True
        page.reload()
        expect(page.locator("h1")).to_contain_text(recovery["title"])
        expect(page.locator("#receipt-count")).to_have_text("3")
        keys = page.evaluate("Object.keys(localStorage).filter(k => k.startsWith('yan-console-receipts:'))")
        assert len(keys) == 1
        checks["receipts_persist_per_ledger_workspace"] = True

        if page.locator("#receipts").is_visible():
            page.locator("#receipt-close").click()
        page.locator("#refresh-interval").select_option("2000")
        page.locator("#pause-refresh").click()
        expect(page.locator("#controller-badge")).to_contain_text("暂停")
        page.wait_for_timeout(300)
        count = len(requests)
        page.wait_for_timeout(4300)
        assert len(requests) == count, "paused page continued polling"
        page.locator("#pause-refresh").click()
        expect(page.locator("#controller-badge")).to_contain_text("在线", timeout=10000)
        checks["refresh_pause_stops_requests_and_resume_restores_reads"] = True
        visit(f"tasks/{demo.tasks['unknown']}", "未知作业结果核验")
        page.get_by_role("tab", name="执行核验").click()
        expect(page.locator("main")).to_contain_text("结果未知")
        expect(page.locator("main")).to_contain_text("禁止自动重启")
        checks["unknown_job_is_not_success_or_live_training"] = True
        screenshot("job-unknown.png")

        page.set_viewport_size({"width": 390, "height": 844})
        visit("overview", "工作台")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        screenshot("mobile.png")
        checks["mobile_has_no_page_overflow"] = True
        page.set_viewport_size({"width": 1440, "height": 1080})
        visit(f"tasks/{recovery['id']}", recovery["title"])
        stamp = page.locator("#updated-at").inner_text().split(" · ")[0]
        demo.controller.terminate()
        demo.controller.wait(timeout=5)
        page.wait_for_timeout(2200)
        page.locator("#refresh-now").click()
        expect(page.locator("#controller-badge")).to_contain_text("离线", timeout=10000)
        expect(page.locator("#connection-banner")).to_contain_text("最后成功")
        assert page.locator("#updated-at").inner_text().split(" · ")[0] == stamp
        assert page.locator("[data-command='instruct_task']").is_disabled()
        assert alive(original_job["identity"])
        screenshot("controller-offline.png")
        checks["controller_offline_keeps_original_timestamp_disables_controls"] = True
        checks["independent_console_and_job_survive_controller_exit"] = True
        assert not errors, errors
        assert not external, external
        checks["no_browser_errors_or_external_page_requests"] = True
        report = {
            "browser": browser.version,
            "checks": checks,
            "passed": all(checks.values()),
            "screenshots": sorted(p.name for p in args.output.glob("*.png")),
            "page_errors": errors,
            "external_page_requests": external,
            "synthetic": True,
            "real_model_api": False,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        print(json.dumps(report, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
