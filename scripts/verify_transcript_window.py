#!/usr/bin/env python3
"""Latest-message previews and lazy full details through real Console / Yan RPC."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sui_regulator.console_demo import ConsoleDemo  # noqa: E402
from sui_regulator.protocol import SuiProtocol  # noqa: E402
from sui_regulator.transcripts import SessionRecorder  # noqa: E402


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="/usr/bin/chromium-browser")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/verification/transcript-window")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    code = "literal **code** <tag> inside a fenced block\n" * 400
    long_text = ("# Record 154\n\n" + "A complete paragraph with **verification evidence**.\n\n" * 4000
                 + "\n```text\n" + code + "```\n\n| Field | Value |\n| --- | --- |\n| Result | Saved |\n\n"
                 + '[full reference][evidence]\n\n[evidence]: https://example.org/evidence\n\n'
                 + '<img src=x onerror="window.__detailUnsafe=1">\n\n'
                 + '![external](https://example.invalid/image.png)\n\nEND-OF-FULL-MESSAGE')
    requests, errors = [], []
    with ConsoleDemo(port=0) as demo, sync_playwright() as pw:
        receipt = demo.service.command("submit_task", "window-fixture", "window-task", {
            "title": "最近 100 条消息与按需全文", "adapter": "unavailable", "objective": "Synthetic reading fixture only."
        })
        demo.wait(lambda: demo.service.get_command(receipt["id"])["status"] == "SUCCEEDED")
        tid = receipt["task_id"]
        credentials = SuiProtocol(demo.service).register(tid)
        recorder = SessionRecorder(demo.service.config, credentials, "synthetic-window-fixture")
        for number in range(154):
            recorder.write("text", "user" if number % 2 == 0 else "assistant",
                           f"Record {number:03d}: recorded conversation preview.", message_id=f"message-{number}")
        for start in range(0, len(long_text), 6000):
            recorder.write("text", "assistant", long_text[start:start + 6000], message_id="message-154")
        demo.service.command("hold_task", "window-fixture", "hold-static-window", {}, tid)
        demo.wait(lambda: demo.service.get_task(tid)["desired"] == "HOLD")
        browser = pw.chromium.launch(executable_path=args.browser, headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.on("pageerror", lambda error: errors.append(str(error)))

        def capture(request):
            if request.method == "POST" and request.url.endswith("/api/rpc"):
                value = request.post_data_json.get("request", {})
                if value.get("method") == "yan.get_proxy_transcript":
                    requests.append(value["params"])

        page.on("request", capture)
        page.goto(demo.url + f"#/sessions/{tid}/{credentials.worker_id}")
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        page.locator("#pause-refresh").click()
        reader = page.locator("[data-transcript-list]")
        text = reader.inner_text()
        assert "Record 154" in text and "Record 000" not in text, "first response must contain latest messages, not the first chronological page"
        expect(reader.locator("[data-message-id]")).to_have_count(100)
        assert "END-OF-FULL-MESSAGE" not in text, "full message must stay unloaded before details are opened"
        assert len(requests) == 1 and requests[0].get("view") == "messages", requests
        def screenshot(name):
            page.locator("[data-session-transcript]").evaluate("el=>{document.activeElement?.blur();window.scrollTo(0,el.getBoundingClientRect().top+scrollY-96);}")
            reader.evaluate("el=>{const last=el.querySelector('[data-message-id]:last-child');if(last) el.scrollTop+=last.getBoundingClientRect().top-el.getBoundingClientRect().top-16;}")
            page.screenshot(path=str(args.output / name))

        screenshot("latest-100-zh.png")
        detail = reader.locator("[data-message-detail]").last
        failed_pages = []

        def fail_second_page(route):
            params = route.request.post_data_json.get("request", {}).get("params", {})
            if params.get("view") == "detail" and params.get("after", 0) > 0:
                failed_pages.append(params["after"])
                route.abort()
            else:
                route.continue_()

        page.route("**/api/rpc", fail_second_page)
        detail.locator(":scope > summary").click()
        expect(detail.locator("[data-message-retry]")).to_be_visible()
        assert len(failed_pages) == 1
        assert len([r for r in requests if r.get("view") == "detail" and not r.get("after")]) == 1
        page.unroute("**/api/rpc", fail_second_page)
        detail.locator("[data-message-retry]").click()
        expect(detail.locator("[data-message-complete]")).to_have_attribute("data-message-complete", "true", timeout=15000)
        expect(detail).to_contain_text("END-OF-FULL-MESSAGE")
        expect(detail.locator(".message-full-text h1")).to_have_text("Record 154")
        assert detail.locator(".message-full-text strong").count() == 4000
        expect(detail.locator(".message-full-text pre code")).to_have_text(code)
        expect(detail.locator(".message-full-text table td")).to_have_count(2)
        expect(detail.get_by_role("link", name="full reference", exact=True)).to_have_attribute("rel", "noopener noreferrer")
        assert not detail.locator("img,script,iframe,input").count()
        assert page.evaluate("window.__detailUnsafe") is None
        assert len([r for r in requests if r.get("view") == "detail" and not r.get("after")]) == 1, "retry must resume the failed page, not restart the full download"
        detail.locator(".raw-message > summary").click()
        expect(detail.locator(".raw-content pre")).to_have_text(long_text)
        detail.locator(".raw-message > summary").click()
        detail_requests = len([r for r in requests if r.get("view") == "detail"])
        assert detail_requests >= 2, "long details must use bounded transport pages"
        detail.locator(".message-detail-scroll").evaluate("el=>el.scrollTop=300")
        page.evaluate("window.__savedDetail=document.querySelector('[data-message-complete]')")
        page.locator("#refresh-now").click()
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        expect(reader.locator("[data-message-detail]").last).to_have_attribute("open", "")
        assert len([r for r in requests if r.get("view") == "detail"]) == detail_requests
        assert page.evaluate("window.__savedDetail===document.querySelector('[data-message-complete]')"), "refresh must reuse the rendered full-message DOM"
        assert detail.locator(".message-detail-scroll").evaluate("el=>el.scrollTop") == 300
        page.locator("#language").select_option("en")
        expect(reader).to_contain_text("END-OF-FULL-MESSAGE")
        assert detail.locator(".message-detail-scroll").evaluate("el=>el.scrollTop") == 300
        detail.locator(".message-detail-scroll").evaluate("el=>el.scrollTop=0")
        screenshot("message-details-en.png")
        # Streaming append updates the preview revision without silently replacing
        # the message the operator is currently reading.
        recorder.write("text", "assistant", "\n\nAFTER-OPENED-SNAPSHOT", message_id="message-154")
        page.wait_for_timeout(2200)
        page.locator("#refresh-now").click()
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        expect(detail.locator("[data-message-retry]")).to_have_text("Read updated details")
        expect(detail.locator(".message-full-text")).not_to_contain_text("AFTER-OPENED-SNAPSHOT")
        detail.locator("[data-message-retry]").click()
        expect(detail.locator("[data-message-complete]")).to_have_attribute("data-message-complete", "true", timeout=15000)
        expect(detail.locator(".message-full-text")).to_contain_text("AFTER-OPENED-SNAPSHOT")
        assert detail.locator(".message-full-text strong").count() == 4000
        # A failed history request must keep the current page and its cursors.
        def fail_history(route):
            params = route.request.post_data_json.get("request", {}).get("params", {})
            if params.get("view") == "messages" and "before" in params:
                route.abort()
            else:
                route.continue_()

        page.route("**/api/rpc", fail_history)
        page.locator("[data-transcript-page=older]").click()
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        expect(page.locator("[data-session-transcript] .notice.warning")).to_contain_text("Transcript not refreshed")
        expect(reader.locator("[data-message-id]")).to_have_count(100)
        expect(page.locator(".transcript-pagination")).to_contain_text("Latest 100")
        expect(detail).to_have_attribute("open", "")
        page.unroute("**/api/rpc", fail_history)
        page.locator("[data-transcript-page=older]").click()
        expect(reader.locator("[data-message-id]")).to_have_count(55)
        expect(reader).to_contain_text("Record 000")
        expect(reader).not_to_contain_text("Record 154")
        page.locator("[data-transcript-page=newer]").click()
        expect(reader.locator("[data-message-id]")).to_have_count(100)
        page.locator("[data-transcript-page=older]").click()
        expect(reader.locator("[data-message-id]")).to_have_count(55)
        page.locator("[data-transcript-page=latest]").click()
        expect(reader.locator("[data-message-id]")).to_have_count(100)
        expect(reader).to_contain_text("Record 154")
        navigated = []

        def navigate_during_detail(route):
            params = route.request.post_data_json.get("request", {}).get("params", {})
            if params.get("view") == "detail" and not navigated:
                navigated.append(True)
                page.evaluate("location.hash='#/sessions'")
                expect(page.locator("[data-session-transcript]")).to_have_count(0)
            route.continue_()

        page.route("**/api/rpc", navigate_during_detail)
        reader.locator("[data-message-detail]").last.locator(":scope > summary").click()
        expect(page.locator("[data-session-transcript]")).to_have_count(0)
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        assert navigated
        assert not page.locator("[data-message-complete]").count()
        page.unroute("**/api/rpc", navigate_during_detail)
        page.evaluate(f"location.hash='#/sessions/{tid}/{credentials.worker_id}'")
        expect(reader.locator("[data-message-id]")).to_have_count(100)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        screenshot("latest-100-mobile.png")
        # Controller loss keeps the last preview window and changes the health display.
        demo.controller.terminate()
        demo.controller.wait(timeout=5)
        page.wait_for_timeout(2200)  # Console's read cache expires after two seconds.
        page.locator("#refresh-now").click()
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        expect(page.locator("#controller-badge")).to_contain_text("Controller offline")
        expect(reader.locator("[data-message-id]")).to_have_count(100)
        assert not errors, errors
        browser.close()
    report = {"passed": True, "scope": "Real local browser / HTTP / RPC, synthetic 155-message transcript",
              "checks": ["latest 100 logical messages in one initial response", "no eager full detail requests",
                         "complete long Markdown via bounded detail pages", "exact full source",
                         "retry resumes failed page without downloading the prefix again",
                         "Markdown fences, tables and reference links survive storage boundaries",
                         "full detail sanitization", "refresh preserves rendered DOM and reading position",
                         "language switch preserves content and scroll", "history failure retains current page",
                         "stream append is explicit and snapshot-consistent", "late detail response cannot populate another page",
                         "older, newer and latest navigation", "mobile width", "offline preview snapshot",
                         "no browser errors"]}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
