#!/usr/bin/env python3
"""Readability, keyboard navigation and layout checks against a real local Console."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sui_regulator.console_demo import ConsoleDemo  # noqa: E402

# Composite ancestor backgrounds rather than assuming every surface is white.
CONTRAST = """node => {
  const rgba=s=>s.match(/[\\d.]+/g).map(Number);
  const layers=[];
  for(let n=node;n;n=n.parentElement) layers.unshift(rgba(getComputedStyle(n).backgroundColor));
  let bg=[255,255,255];
  for(const c of layers) bg=bg.map((v,i)=>c[i]*(c[3] ?? 1)+v*(1-(c[3] ?? 1)));
  const fg=rgba(getComputedStyle(node).color);
  const rgb=bg.map((v,i)=>fg[i]*(fg[3] ?? 1)+v*(1-(fg[3] ?? 1)));
  const lum=c=>c.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4)
    .reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
  const a=lum(bg),b=lum(rgb);
  return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
}"""


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="/usr/bin/chromium-browser")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots/yan-redesign")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/verification/yan-redesign/design.json")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    checks, errors, external, contrasts, layout_cases = [], [], [], {}, []
    with ConsoleDemo(port=0) as demo, sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=args.browser, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1080}, locale="zh-CN")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: external.append(r.url) if not r.url.startswith(demo.url) else None)

        def visit(route):
            page.goto(demo.url + "#/" + route)
            expect(page.locator("main")).to_have_attribute("aria-busy", "false")
            expect(page.locator("#controller-badge")).to_contain_text("在线")

        def shot(name, full=False):
            page.evaluate("document.activeElement?.blur();window.scrollTo(0,0)")
            page.screenshot(path=str(args.output / name), full_page=full, animations="disabled")

        def contrast(*selectors):
            for selector in selectors:
                for node in page.locator(selector).all():
                    if node.is_visible():
                        ratio = node.evaluate(CONTRAST)
                        contrasts[selector] = round(min(ratio, contrasts.get(selector, ratio)), 2)
                        assert ratio >= 4.5, (selector, ratio)

        def no_overflow(case):
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), case
            assert page.locator(".sidebar").evaluate("n=>n.scrollWidth<=n.clientWidth+1"), case
            assert page.locator(".topbar").evaluate("n=>n.scrollHeight<=n.clientHeight+1"), case
            layout_cases.append(case)

        visit("overview")
        expect(page.locator(".watch-heading")).to_contain_text("诸相可散，案卷长存")
        assert page.locator("body").evaluate("n=>parseFloat(getComputedStyle(n).fontSize)") == 18
        contrast("body", ".watch-caption", ".watch-motto", ".stat-foot", ".incident-body p",
                 ".sidebar nav small", ".side-health", ".button.primary", ".button.secondary", ".badge")
        shot("overview-desktop.png")
        checks.append("18px_readable_overview")

        task = demo.service.get_task(demo.tasks["recovery"])
        task_route = "tasks/" + task["id"]
        incident = next(i for i in task["incidents"] if i["rule_id"] == "GEN-001" and i["status"] == "APPLIED")
        incident_route = f"incidents/{task['id']}/{incident['sequence']}"
        visit(task_route)
        expect(page.locator("#pane-summary")).to_be_visible()
        expect(page.locator("[role=tabpanel]:visible")).to_have_count(1)
        contrast(".state-card h3", ".state-card .summary", ".state-card p")
        shot("task-summary.png", full=True)
        page.get_by_role("tab", name="任务总览").focus()
        page.keyboard.press("ArrowRight")
        expect(page.get_by_role("tab", name="执行核验")).to_be_focused()
        expect(page.locator("#pane-execution")).to_be_visible()
        page.locator("[data-job]").first.click()
        expect(page.locator(".metric-chart")).to_be_visible()
        shot("task-execution.png", full=True)
        page.get_by_role("tab", name="执行核验").focus()
        page.keyboard.press("End")
        expect(page.get_by_role("tab", name="历史案卷")).to_be_focused()
        expect(page.locator("#pane-archive")).to_be_visible()
        page.keyboard.press("Home")
        expect(page.get_by_role("tab", name="任务总览")).to_be_focused()
        page.keyboard.press("ArrowLeft")
        expect(page.get_by_role("tab", name="历史案卷")).to_be_focused()
        page.get_by_role("tab", name="敕令回执").click()
        with page.expect_response(lambda r: "/api/snapshot" in r.url):
            page.locator("#refresh-now").evaluate("n=>n.click()")
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        expect(page.get_by_role("tab", name="敕令回执")).to_be_focused()
        expect(page.locator("#pane-commands")).to_be_visible()
        expect(page.locator("[role=tabpanel]:visible")).to_have_count(1)
        checks.extend(["four_real_sections_and_job_metrics", "tab_keyboard_arrows_home_end_and_wrap",
                       "refresh_preserves_selected_section_and_keyboard_focus"])

        page.locator(".skip-link").focus()
        page.keyboard.press("Enter")
        expect(page.locator("main")).to_be_focused()
        assert page.url.endswith("#/" + task_route)
        checks.append("skip_to_content_preserves_current_route")
        page.locator(".proxy-entry").click()
        expect(page.locator("[data-session-transcript]")).to_be_visible()
        expect(page.locator("main")).to_have_attribute("aria-busy", "false")
        proxy_route = page.url.split("#/")[1]
        assert page.locator("[data-session-transcript]").evaluate(
            "n=>!!(n.compareDocumentPosition(document.querySelector('[data-execution-evidence]')) & "
            "Node.DOCUMENT_POSITION_FOLLOWING)"
        )
        assert page.locator(".message-text").first.evaluate("n=>parseFloat(getComputedStyle(n).fontSize)") >= 19
        page.locator("#transcript-follow").uncheck()
        page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop=0")
        page.locator(".role-tool details").first.locator("summary").click()
        expect(page.locator(".role-tool pre").last).to_contain_text(task["jobs"][0]["id"])
        contrast(".message-text", ".session-message time", ".source-note", ".message-tool pre")
        shot("session-reader.png")
        page.locator("[data-jump=proxy-evidence]").click()
        expect(page.locator("#proxy-evidence")).to_be_focused()
        expect(page.locator("[data-execution-evidence]")).to_contain_text(str(task["jobs"][0]["pid"]))
        page.locator("[data-jump=proxy-identity]").click()
        expect(page.locator("#proxy-identity")).to_be_focused()
        page.locator("[data-jump=proxy-transcript]").click()
        expect(page.locator("#proxy-transcript")).to_be_focused()
        checks.append("task_to_conversation_and_real_evidence_section_navigation")

        visit(incident_route)
        contrast(".flow-step small", ".flow-step p", ".notice.info")
        shot("incident-detail.png", full=True)
        checks.append("sampled_body_metadata_controls_and_status_contrast_at_least_4_5")

        # Check every page and detail surface at real responsive breakpoints and all reading sizes.
        routes = ["overview", "sessions", "tasks", "incidents", task_route, proxy_route, incident_route]
        for route in routes:
            visit(route)
            for width in (360, 390, 1024, 1440):
                page.set_viewport_size({"width": width, "height": 900})
                for size, pixels in (("normal", 18), ("large", 20), ("xlarge", 22)):
                    page.locator("#reading-size").select_option(size)
                    assert page.locator("body").evaluate("n=>parseFloat(getComputedStyle(n).fontSize)") == pixels
                    no_overflow(f"{route.split('/')[0]}:{'detail' if '/' in route else 'list'}:{width}:{size}")
                    if route == task_route:
                        for tab in ("任务总览", "执行核验", "敕令回执", "历史案卷"):
                            page.get_by_role("tab", name=tab).click()
                            no_overflow(f"task-tab:{tab}:{width}:{size}")
                    if width <= 390:
                        for link in page.locator(".sidebar nav a").all():
                            assert link.bounding_box()["height"] >= 44
                            assert link.inner_text().strip()
            page.locator("#reading-size").select_option("normal")
        checks.extend(["all_routes_and_task_tabs_fit_360_390_1024_1440_at_all_three_sizes",
                       "mobile_bottom_navigation_has_labels_and_44px_targets"])
        page.set_viewport_size({"width": 390, "height": 844})
        visit("overview")
        shot("overview-mobile.png")
        visit(proxy_route)
        page.locator("#reading-size").select_option("xlarge")
        page.locator("#transcript-follow").uncheck()
        page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop=0")
        page.locator("[data-jump=proxy-transcript]").click()
        page.screenshot(path=str(args.output / "session-mobile-large.png"), animations="disabled")
        assert not errors, errors
        assert not external, external
        checks.append("no_browser_errors_or_external_requests")
        report = {"passed": True, "browser": browser.version, "checks": checks,
                  "layout_cases": layout_cases, "sampled_contrast_ratios": contrasts,
                  "page_errors": errors, "external_requests": external,
                  "scope": "Real localhost HTTP/RPC/SQLite/Chromium; synthetic tasks, no model API"}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
