#!/usr/bin/env python3
"""Task creation through real HTTP, Yan RPC, Controller and SQLite; no model API."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sui_regulator.console_demo import ConsoleDemo  # noqa: E402


def main():
    from playwright.sync_api import expect, sync_playwright

    output = ROOT / 'docs/screenshots/task-brief'
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    with ConsoleDemo(port=0) as demo, sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/chromium-browser', headless=True,
                                   args=['--no-sandbox', '--disable-dev-shm-usage'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1200}, locale='zh-CN')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(demo.url + '#/tasks')
        expect(page.locator('#controller-badge')).to_contain_text('在线')
        page.locator('[data-create-task]').click()
        page.locator('#task-title').fill('实验闭环 · 浏览器合成验收')
        objective = '等待实验完成。\n核验结果；若失败则定位原因、修改代码、复测，再继续实验。\n保留现有实验 ID，状态不明时先核验。'
        page.locator('#task-objective').fill(objective)
        page.locator('#task-criteria').fill('记录测试结果与下一轮实验产物；这是合成界面验收。')
        page.locator('#task-adapter').select_option('mock')
        page.screenshot(path=str(output / 'create-task.png'), full_page=True)
        page.locator('#task-submit').click()
        expect(page.locator('#task-dialog')).not_to_be_visible()
        demo.wait(lambda: any(t['title'] == '实验闭环 · 浏览器合成验收' for t in demo.service.list_tasks()))
        task = next(t for t in demo.service.list_tasks() if t['title'] == '实验闭环 · 浏览器合成验收')
        assert task['objective'] == objective
        demo.service.command('hold_task', 'browser-drill', 'hold-created-task', {}, task['id'])
        demo.wait(lambda: demo.service.get_task(task['id'])['desired'] == 'HOLD')
        page.goto(demo.url + '#/tasks/' + task['id'])
        expect(page.locator('.task-brief').first).to_have_text(objective)
        expect(page.locator('#receipt-list')).to_contain_text('创建任务')
        page.locator('#receipt-close').click()
        page.screenshot(path=str(output / 'task-requirements.png'), full_page=True)
        assert not errors, errors
        browser.close()
    report = {'passed': True, 'checks': ['real_submit_receipt', 'full_multiline_brief_persisted',
              'criteria_displayed', 'hold_preserves_created_task', 'no_browser_errors'],
              'scope': 'Mock UI and real local control plane; no real model or training'}
    (ROOT / 'docs/task-brief-browser-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
