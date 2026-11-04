#!/usr/bin/env python3
"""Reader, Markdown and bilingual UI checks using real local HTTP/RPC/SQLite."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sui_regulator.console_demo import ConsoleDemo  # noqa: E402
from sui_regulator.protocol import SuiProtocol  # noqa: E402
from sui_regulator.transcripts import SessionRecorder  # noqa: E402

BRIEF = """# 合成阅读验收

等待已有实验结束，**核验结果**，再决定下一步。English stays in the original text.

1. 核对运行编号，避免重复启动。
2. 如果实验失败，由代理定位问题、修改代码并复测。

> 这是界面验收用的合成任务，不是真实模型运行结果。

| 检查 | 要求 |
| --- | --- |
| 产物 | 保存核验记录 |
| 回归 | 测试通过后继续实验 |

```python
print("verify existing run")
```
"""
REPLY = """## 合成记录：已连接原有作业

我会先**等待并核验**，然后根据证据决定是否调试。

- 原有作业保持运行。
- 工具报错本身不代表会话失效。

```bash
python -m unittest
```
"""
UNSAFE = """<img src=x onerror="window.__readerUnsafe=1"><script>window.__readerUnsafe=2</script>

[unsafe](javascript:alert(1)) ![external](https://example.invalid/private-image.png)

[reference](https://example.org/research) <form id=main><input name=location></form>
"""


def main():
    from playwright.sync_api import expect, sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="/usr/bin/chromium-browser")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/screenshots/yan-reader")
    parser.add_argument("--report", type=Path, default=ROOT / "docs/verification/yan-reader/reading.json")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    errors, external, checks, layouts, fonts = [], [], [], [], []
    with ConsoleDemo(port=0) as demo, sync_playwright() as pw:
        receipt = demo.service.command("submit_task", "reader-fixture", "reader-markdown",
            {"title":"合成阅读验收", "objective":BRIEF, "completion_criteria":"## 交付\n\n**保存**测试结果与证据。", "adapter":"unavailable"})
        demo.wait(lambda: demo.service.get_command(receipt["id"])["status"] == "SUCCEEDED")
        tid = receipt["task_id"]
        credentials = SuiProtocol(demo.service).register(tid)
        recorder = SessionRecorder(demo.service.config, credentials, "synthetic-reader-fixture")
        recorder.write("text", "system", "Synthetic supervisor protocol: inspect the durable ledger.", message_id="supervisor-protocol")
        recorder.record_input(json.dumps({"operator_request":{"objective":BRIEF},"execution_contract":{"synthetic":True}}),
                              {"objective":BRIEF})
        recorder.write("text", "assistant", REPLY, message_id="reply")
        recorder.write("tool_call", "assistant", '{"command":"python -m unittest"}', tool_name="Bash", call_id="paired-call")
        recorder.write("tool_result", "tool", "test_example FAILED: expected 2, received 1", call_id="paired-call", is_error=True)
        recorder.write("text", "assistant", UNSAFE, message_id="untrusted")
        recorder.write("text", "assistant", "```text\n" + "long-command-"*80 + "\n```", message_id="long-code")
        # This is a static captured-output fixture, not a running model. Hold it before
        # the intentionally absent process could produce an unrelated startup incident.
        demo.service.command("hold_task", "reader-fixture", "hold-reader-fixture", {}, tid)
        demo.wait(lambda: demo.service.get_task(tid)["desired"] == "HOLD")
        browser = pw.chromium.launch(executable_path=args.browser, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking"])
        context = browser.new_context(viewport={"width":1440,"height":1080}, permissions=["clipboard-read","clipboard-write"])
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: external.append(r.url) if not r.url.startswith(demo.url) else None)

        def visit(route):
            page.goto(demo.url + "#/" + route)
            expect(page.locator("main")).to_have_attribute("aria-busy", "false")
            page.evaluate("document.fonts.ready")

        def shot(name):
            page.evaluate("document.activeElement?.blur()")
            page.screenshot(path=str(args.output / name), animations="disabled")

        def refresh():
            with page.expect_response(lambda r: "/api/snapshot" in r.url):
                page.locator("#refresh-now").click()
            expect(page.locator("main")).to_have_attribute("aria-busy", "false")

        visit("tasks/" + tid)
        brief = page.locator("[data-task-brief] > .markdown")
        expect(brief.locator("h1")).to_have_text("合成阅读验收")
        expect(brief.locator("strong")).to_have_text("核验结果")
        expect(brief.locator("table tbody tr")).to_have_count(2)
        expect(brief.locator("ol > li")).to_have_count(2)
        expect(brief.locator("blockquote")).to_contain_text("合成任务")
        expect(brief.locator("pre code")).to_have_text('print("verify existing run")\n')
        assert demo.service.get_task(tid)["objective"] == BRIEF
        page.locator("[data-task-brief] > details > summary").click()
        expect(page.locator("[data-task-brief] .raw-content pre")).to_have_text(BRIEF)
        page.locator("[data-task-brief] > details > summary").click()
        shot("task-markdown-zh.png")
        checks.append("brief_markdown_headings_lists_tables_quotes_code_and_exact_source")

        # Measure fonts actually used by the browser, including Chinese and Latin glyphs.
        cdp=context.new_cdp_session(page)
        cdp.send("DOM.enable")
        cdp.send("CSS.enable")
        root=cdp.send("DOM.getDocument")["root"]["nodeId"]
        for selector in ("[data-task-brief] .markdown p", "[data-task-brief] pre code"):
            node=cdp.send("DOM.querySelector",{"nodeId":root,"selector":selector})["nodeId"]
            fonts.extend(cdp.send("CSS.getPlatformFontsForNode",{"nodeId":node})["fonts"])
        names={f["familyName"].removesuffix(" (Fontations)") for f in fonts if f["glyphCount"]}
        assert {"Inter Variable","Source Han Sans CN VF","JetBrains Mono"} <= names, names
        assert all(f["isCustomFont"] for f in fonts if f["glyphCount"]), fonts
        assert page.locator(".page-heading h1").evaluate("n=>parseInt(getComputedStyle(n).fontWeight)") >= 700
        checks.append("local_inter_source_han_sans_and_jetbrains_mono_actually_rendered")

        route=f"sessions/{tid}/{credentials.worker_id}"
        visit(route)
        expect(page.locator("#transcript-filter")).to_have_value("reader")
        page.locator("#transcript-follow").uncheck()
        page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop=0")
        assert page.locator(".compact-activity details[open]").count() == 0
        expect(page.locator(".role-tool")).to_have_count(1)
        expect(page.locator(".role-tool summary")).to_contain_text("工具返回错误")
        assert not re.search(r"\d+\s*(秒|ms|s)\b",page.locator(".role-tool summary").inner_text())
        expect(page.locator("[data-activity=thinking]")).to_have_count(0)
        colors=[page.locator(".role-"+role).first.evaluate("n=>getComputedStyle(n).backgroundColor") for role in ("user","assistant","tool","system")]
        assert len(set(colors)) == 4, colors
        expect(page.locator(".role-assistant .message-text h2")).to_have_text("合成记录：已连接原有作业")
        expect(page.locator("[data-incident-marker]")).to_have_count(0)
        checks.extend(["distinct_role_surfaces_and_collapsed_technical_records", "tool_pair_uses_call_id_and_error_does_not_become_session_failure", "no_invented_thinking_or_tool_duration"])
        page.locator(".role-tool summary").click()
        expect(page.locator(".role-tool pre").last).to_contain_text("test_example FAILED")
        page.locator(".role-tool summary").focus()
        refresh()
        expect(page.locator(".role-tool details")).to_have_attribute("open", "")
        # Refresh button intentionally took focus; details stay open and read position remains unchanged.
        page.locator("#language").select_option("en")
        expect(page.locator(".role-tool details")).to_have_attribute("open", "")
        expect(page.locator(".role-tool summary")).to_contain_text("Tool returned an error")
        expect(page.locator(".role-user .message-text")).to_contain_text("核验结果")
        page.locator(".role-tool summary").click()
        checks.append("language_switch_preserves_expanded_records_and_original_content")

        reader=page.locator("[data-transcript-list]")
        expect(reader.locator("img,script,form,input,iframe,svg")).to_have_count(0)
        assert page.evaluate("window.__readerUnsafe") is None
        assert not reader.locator("a[href^='javascript:'],a[href^='data:']").count()
        link=reader.get_by_role("link",name="reference",exact=True)
        expect(link).to_have_attribute("rel","noopener noreferrer")
        expect(link).to_have_attribute("referrerpolicy","no-referrer")
        expect(reader).to_contain_text('<img src=x onerror="window.__readerUnsafe=1">')
        assert not external, external
        checks.append("untrusted_markdown_has_no_html_execution_or_remote_image_fetch")
        page.locator(".role-assistant .code-block [data-copy-code]").first.click()
        expect(page.locator("#toast")).to_have_text("Copied")
        assert page.evaluate("navigator.clipboard.readText()") == "python -m unittest\n"
        checks.append("copy_code_uses_original_text")
        page.locator("#transcript-search").fill("FAILED")
        expect(page.locator(".role-tool")).to_have_count(1)
        expect(page.locator(".role-user")).to_have_count(0)
        refresh()
        expect(page.locator("#transcript-search")).to_have_value("FAILED")
        page.locator("#transcript-search").fill("")
        page.locator("#transcript-filter").select_option("all")
        page.locator(".role-tool summary").click()
        refresh()
        assert not page.locator(".role-tool details").evaluate("n=>n.open")
        page.locator("#transcript-filter").select_option("reader")
        assert page.locator(".compact-activity details[open]").count() == 0
        checks.append("search_and_closed_disclosures_survive_refresh")
        page.locator("#language").select_option("zh")
        page.locator("[data-jump=proxy-transcript]").click()
        page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop=0")
        shot("conversation-zh.png")
        page.locator("#language").select_option("en")
        shot("conversation-en.png")

        visit("tasks/"+tid)
        page.locator("[data-command=instruct_task]").click()
        draft="## 不要丢失这份草稿\n核验后继续。"
        page.locator("#instruction").fill(draft)
        # Language control is outside the modal; dispatch the same change without closing the draft.
        page.locator("#language").evaluate("n=>{n.value='zh';n.dispatchEvent(new Event('change',{bubbles:true}));}")
        expect(page.locator("#instruction")).to_have_value(draft)
        expect(page.locator("#dialog-title")).to_have_text("追加指令")
        page.locator("#language").evaluate("n=>{n.value='en';n.dispatchEvent(new Event('change',{bubbles:true}));}")
        expect(page.locator("#dialog-title")).to_have_text("Add instructions")
        page.locator("#dialog-submit").click()
        expect(page.locator("#command-dialog")).not_to_be_visible()
        expect(page.locator("#receipt-list")).to_contain_text("Applied",timeout=15000)
        rows=demo.service.store.rows("SELECT payload FROM commands WHERE task_id=? AND kind='instruct_task'",(tid,))
        assert len(rows)==1 and json.loads(rows[0]["payload"])["instruction"]==draft
        page.locator("#receipt-close").click()
        page.reload()
        expect(page.locator("#language")).to_have_value("en")
        expect(page.locator("html")).to_have_attribute("lang","en")
        checks.extend(["switching_language_keeps_unsent_instruction_and_real_receipt", "language_persists_across_reload"])

        visit("tasks")
        page.locator("[data-create-task]").click()
        page.locator("#task-objective").fill(BRIEF)
        page.locator("#language").evaluate("n=>{n.value='zh';n.dispatchEvent(new Event('change',{bubbles:true}));}")
        expect(page.locator("#task-objective")).to_have_value(BRIEF)
        expect(page.locator("#task-dialog-title")).to_have_text("创建任务")
        page.locator("#task-close").click()
        checks.append("task_creation_draft_survives_language_switch")

        recovery=demo.service.get_task(demo.tasks["recovery"])
        old=next(p for p in recovery["workers"] if p["generation"]==1)
        old_route=f"sessions/{recovery['id']}/{old['id']}"
        visit(old_route)
        expect(page.locator("[data-incident-marker]").first).to_be_attached()
        expect(page.locator(".in-detection-window").first).to_be_attached()
        page.locator("[data-next-incident]").click()
        expect(page.locator("[data-incident-marker]:focus")).to_have_count(1)
        expect(page.locator("#transcript-follow")).not_to_be_checked()
        shot("incident-in-conversation.png")
        first=page.locator("[data-incident-marker]").first
        first.get_by_role("link").click()
        expect(page.locator(".incident-flow")).to_contain_text("检测建议")
        expect(page.locator(".incident-flow")).to_contain_text("秉烛人最终动作")
        assert demo.service.get_task(recovery["id"])["execution_count"]==1
        checks.append("ledger_incidents_mark_detection_window_and_link_to_recovery_without_job_replay")
        current=demo.service.get_task(recovery["id"])["current_worker"]
        visit(f"sessions/{recovery['id']}/{current}")
        page.locator("#transcript-follow").uncheck()
        page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop=0")
        page.locator("[data-jump=proxy-transcript]").click()
        shot("conversation-roles-zh.png")
        page.locator("#language").select_option("en")
        shot("conversation-roles-en.png")

        # Both locales, every primary page and long-code conversation, three text sizes.
        for language in ("zh","en"):
            page.locator("#language").select_option(language)
            for target in ("overview","sessions","tasks","incidents","tasks/"+tid,route):
                visit(target)
                if language=='en':
                    chrome=page.locator(".sidebar nav, .panel-head, [role=tab], main button").all_text_contents()
                    assert not any(re.search(r"[\u4e00-\u9fff]",text) for text in chrome), chrome
                for width in (360,390,1024,1440):
                    page.set_viewport_size({"width":width,"height":900})
                    for size in ("normal","large","xlarge"):
                        page.locator("#reading-size").select_option(size)
                        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"),(language,target,width,size)
                        layouts.append(f"{language}:{target.split('/')[0]}:{'detail' if '/' in target else 'list'}:{width}:{size}")
            page.locator("#reading-size").select_option("normal")
        checks.append("both_languages_all_primary_pages_and_markdown_fit_four_widths_three_text_sizes")
        page.locator("#language").select_option("zh")
        page.set_viewport_size({"width":390,"height":844})
        visit(route)
        page.locator("#reading-size").select_option("large")
        page.locator("#transcript-follow").uncheck()
        page.locator("[data-transcript-list]").evaluate("n=>n.scrollTop=0")
        page.locator("[data-jump=proxy-transcript]").click()
        expect(page.locator("#transcript-search")).not_to_be_visible()
        page.locator("[data-reader-options]").click()
        expect(page.locator("#transcript-search")).to_be_visible()
        page.locator("[data-reader-options]").click()
        expect(page.locator("#transcript-search")).not_to_be_visible()
        checks.append("mobile_reading_options_expand_without_crowding_the_default_reader")
        shot("conversation-mobile.png")
        page.set_viewport_size({"width":1440,"height":1080})
        page.locator("#reading-size").select_option("normal")
        visit("overview")
        shot("overview-zh.png")
        page.locator("#language").select_option("en")
        shot("overview-en.png")
        assert not errors,errors
        assert not external,external
        checks.append("no_javascript_errors_or_external_asset_requests")
        browser.close()
    report={"passed":True,"checks":checks,"layout_cases":layouts,"rendered_fonts":fonts,"page_errors":errors,"external_requests":external,
            "scope":"Real browser/HTTP/RPC/SQLite; explicit synthetic reader fixtures and existing GEN-001 demo. No model API or invented timings."}
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"passed":True,"checks":checks,"layout_cases":len(layouts)},ensure_ascii=False))


if __name__ == "__main__":
    main()
