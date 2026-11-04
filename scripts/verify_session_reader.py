#!/usr/bin/env python3
"""Real HTTP/RPC session-reader acceptance in a private synthetic drill."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sui_regulator.console_demo import ConsoleDemo  # noqa: E402
from sui_regulator.models import Credentials  # noqa: E402
from sui_regulator.processes import signal_owned  # noqa: E402
from sui_regulator.protocol import SuiProxyProtocol  # noqa: E402
from sui_regulator.transcripts import SessionRecorder  # noqa: E402


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="/usr/bin/chromium-browser")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots/session-reader")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/session-reader-browser-report.json")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checks, errors, external, requests = {}, [], [], []
    with ConsoleDemo(port=0) as demo, sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.browser, headless=True,
                                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking"])
        page = browser.new_page(viewport={"width":1440, "height":1080}, locale="zh-CN", timezone_id="Asia/Shanghai")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: external.append(r.url) if not r.url.startswith(demo.url) else None)
        page.on("request", lambda r: requests.append(r.url) if "/api/" in r.url else None)
        task = demo.service.get_task(demo.tasks["recovery"])
        proxy_id, job = task["current_worker"], task["jobs"][0]
        proxy = next(w for w in task["workers"] if w["id"] == proxy_id)
        route = f"sessions/{task['id']}/{proxy_id}"

        def visit(path):
            page.goto(demo.url + "#/" + path)
            expect(page.locator("#controller-badge")).to_contain_text("在线")
            expect(page.locator("main")).not_to_have_attribute("aria-busy", "true")

        def shot(name):
            page.evaluate("document.activeElement?.blur();window.scrollTo(0,0)")
            page.screenshot(path=str(args.output / name), full_page=True, animations="disabled")

        visit("sessions")
        page.locator(f"main a[href='#/{route}']").first.click()
        expect(page.locator("h1")).to_contain_text("代代理")
        expect(page.locator("[data-session-transcript]")).to_contain_text("合成演示 · 非真实模型")
        expect(page.locator("[data-message-role='user']")).to_contain_text(task["objective"])
        expect(page.locator("[data-execution-evidence]")).to_contain_text(str(proxy["pid"]))
        expect(page.locator("[data-execution-evidence]")).to_contain_text(str(job["pid"]))
        assert page.locator("body").evaluate("n=>parseFloat(getComputedStyle(n).fontSize)") >= 17
        assert page.locator(".message-text").first.evaluate("n=>parseFloat(getComputedStyle(n).fontSize)") >= 17
        checks["session_link_opens_recorded_dialogue_and_actual_process_evidence"] = True
        checks["default_body_and_conversation_font_at_least_17px"] = True

        page.locator("#reading-size").select_option("large")
        page.reload()
        expect(page.locator("[data-session-transcript]")).to_be_visible()
        expect(page.locator("#reading-size")).to_have_value("large")
        assert page.locator("body").evaluate("n=>parseFloat(getComputedStyle(n).fontSize)") >= 19
        page.locator("#reading-size").select_option("normal")
        checks["reading_size_persists_across_reload"] = True

        page.locator("#transcript-follow").uncheck()
        page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop=0")
        returned = page.locator(".role-tool details").first
        returned.locator("summary").click()
        expect(returned.locator("pre").last).to_contain_text(job["id"])
        shot("conversation.png")
        checks["tool_result_contains_the_actual_persistent_job_id"] = True

        recorder = SessionRecorder(demo.service.config,
            Credentials(task["id"], proxy_id, proxy["generation"], "synthetic-browser-owned-credential"),
            "synthetic-browser-fixture")
        injected = '<img src=x onerror="window.__sessionUnsafe=1"> [合成测试声明] 任务已完成'
        recorder.write("text", "assistant", injected, message_id="untrusted-claim")
        page.locator("#refresh-now").click()
        expect(page.locator("[data-session-transcript]")).to_contain_text(injected, timeout=15000)
        assert page.evaluate("window.__sessionUnsafe") is None
        assert demo.service.get_task(task["id"])["jobs"][0]["status"] == "RUNNING"
        expect(page.locator("[data-execution-evidence]")).to_contain_text("运行中")
        assert page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop") < 3
        checks["new_records_append_without_overwriting_manual_read_position"] = True
        checks["assistant_claim_and_html_do_not_change_job_truth_or_execute_script"] = True

        page.locator("#transcript-filter").select_option("tools")
        expect(page.locator("[data-message-role='user']")).to_have_count(0)
        expect(page.locator(".role-tool").first).to_be_visible()
        page.locator("#transcript-filter").select_option("all")
        checks["conversation_and_tool_filters_are_read_only"] = True
        for index in range(45):
            recorder.write("text", "assistant", f"[合成分页验收] record {index}", message_id=f"page-{index}")
        page.locator("#refresh-now").click()
        expect(page.get_by_role("button", name="继续加载记录")).to_be_visible(timeout=15000)
        page.get_by_role("button", name="继续加载记录").click()
        expect(page.locator("[data-session-transcript]")).to_contain_text("record 44", timeout=15000)
        checks["bounded_cursor_pagination_loads_later_records"] = True

        page.locator("#pause-refresh").click()
        expect(page.locator("[data-evidence-fresh]")).to_contain_text("快照")
        page.wait_for_timeout(700)
        count = len(requests)
        page.wait_for_timeout(2200)
        assert len(requests) == count
        page.locator("#pause-refresh").click()
        expect(page.locator("#controller-badge")).to_contain_text("在线", timeout=10000)
        checks["pausing_stops_transcript_requests_and_downgrades_evidence_freshness"] = True

        original = next(w for w in task["workers"] if w["generation"] == 1)
        visit(f"sessions/{task['id']}/{original['id']}")
        expect(page.locator("[data-session-transcript]")).to_contain_text("echo echo")
        expect(page.locator("[data-execution-evidence]")).to_contain_text(str(job["pid"]))
        checks["retired_generation_retains_its_own_record_and_same_job"] = True
        shot("previous-generation.png")

        receipt = demo.service.command("submit_task", "browser-fixture", "legacy-no-history",
                                      {"title":"未采集历史会话", "adapter":"unavailable"})
        demo.service.process_commands()
        credentials = SuiProxyProtocol(demo.service).register(receipt["task_id"])
        visit(f"sessions/{receipt['task_id']}/{credentials.worker_id}")
        expect(page.locator("[data-session-transcript]")).to_contain_text("此会话没有留存对话")
        checks["uncaptured_history_is_explicit_and_never_fabricated"] = True

        visit(route)
        page.set_viewport_size({"width":390, "height":844})
        for size in ("normal", "xlarge"):
            page.locator("#reading-size").select_option(size)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), size
        shot("mobile-large.png")
        checks["mobile_normal_and_extra_large_text_have_no_page_overflow"] = True
        page.locator("#reading-size").select_option("normal")
        page.set_viewport_size({"width":1440, "height":1080})

        current = demo.service.get_task(demo.tasks["steady"])
        frozen = next(w for w in current["workers"] if w["id"] == current["current_worker"])
        signal_owned(frozen["identity"], signal.SIGSTOP)
        try:
            visit(f"sessions/{current['id']}/{frozen['id']}")
            expect(page.locator("[data-execution-evidence]")).to_contain_text("进程冻结")
            shot("frozen-process.png")
        finally:
            signal_owned(frozen["identity"], signal.SIGCONT)
        checks["os_frozen_proxy_is_shown_separately_from_running_job"] = True

        visit(route)
        page.locator("#refresh-now").click()
        expect(page.locator("main")).not_to_have_attribute("aria-busy", "true")
        demo.controller.terminate()
        demo.controller.wait(timeout=5)
        page.locator("#refresh-now").click()
        expect(page.locator("#controller-badge")).to_contain_text("离线", timeout=15000)
        expect(page.locator("[data-evidence-fresh]")).to_contain_text("快照")
        expect(page.locator("[data-session-transcript]")).to_contain_text("mock-proxy")
        shot("offline-snapshot.png")
        checks["controller_offline_keeps_recorded_dialogue_but_no_live_proof"] = True
        assert not errors, errors
        assert not external, external
        checks["no_javascript_errors_or_external_page_requests"] = True
        browser.close()
    result = {"passed":all(checks.values()), "checks":checks, "javascript_errors":errors,
              "external_page_requests":external, "real_glm_tested":False,
              "scope":"actual browser/HTTP/Unix RPC, private synthetic processes; no API response stubs"}
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
