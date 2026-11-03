# Console 阅读与会话核验交付

日期：2026-11-03。基于 GLM 实测更新后的 `c0b15ad`，解决默认文字过小、会话详情缺少可读对话，以及难以区分代理自述与实际运行的问题。

## 使用

1. 顶部选择“字号”：舒适、大字、特大；默认正文与对话约 18px，选择跨刷新保留。
2. 进入“代理会话”，点击代理 ID 或“查看对话”。
3. 在“实际运行证据”查看包装进程、CLI 进程、Job 与 Runner 指标的来源、身份和查询时间。
4. 阅读任务输入、助手可见输出和运行时事件；展开工具参数与实际返回，核对具体操作。可筛选对话/工具，关闭跟随后手动回看。
5. 从代际历史进入旧 Sui Proxy，查看其独立记录；同一 Task 的 Job 不因切换阅读对象而重新执行。

源码 Mock 演示仍可直接运行：

```bash
PYTHONPATH=src python3.11 -m sui_regulator console-demo --port 8765
```

Mock 记录明确标注为合成，不能作为真实 GLM 已运行的证据。巡检 Task 可能只有工具调用而没有 Job，应核对工具返回与对应进程，而不是仅看 Job 数。

## 实现范围

- 新增只读 `yan.get_proxy_transcript`，复用 Controller 的 Unix RPC；Console 仍为独立进程，不直接读账本或会话文件。
- CLI 主会话可见输出按流采集，支持中途查看文本、完整消息去重、工具调用与返回关联；忽略 thinking 块。Mock 与故障演示走各自显式标记的记录来源。
- 会话文件位于私有状态目录，每代最多 4 MiB、单条最多 8000 字符、单次读取最多 128 KiB；使用字节游标，页面最多保留 400 条。限额、截断、缺失与不可读均有提示。
- 记录不进入恢复上下文或 Incident；已知凭据与常见秘密字段脱敏。普通文件与目录权限检查、符号链接拒绝、非阻塞文件类型检查和损坏记录处理均有测试。
- 会话刷新沿用限流、全局暂停和离线缓存；回看时保留阅读位置。离线或暂停后证据标为快照，不继续表示当前已核验。
- 新增的进程存活字段按 boot ID / PID / start ticks 核验；对话内容不修改 Desired、Declared 或 Runner 观测。

另修复全套回归中暴露的退出记录竞态：终止类 Incident 先发布时，Controller 可能在包装进程写完退出证据前将其终止。现在退出证据与终止类 Incident 在同一事务提交。专用测试将退出记录延迟 350ms，已验证修复前失败、修复后通过。

## 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| Python 3.11 全套测试 | 107 项通过，无 skip；含 4 项已安装 Claude CLI + 本地假 API | [测试输出](session-reader-test-output.txt) |
| 原有浏览器验收 | 15 项通过，含真实回执、Job/PID 不变、UNKNOWN 与离线 | [原有浏览器报告](console-browser-report.json) |
| 会话阅读器浏览器验收 | 15 项通过；真实 Chromium / HTTP / Unix RPC / 专用进程，无 API 返回替身 | [阅读器报告](session-reader-browser-report.json) |
| 原始复现 | 字号从 14px 提升到 18.02px，会话阅读区与运行证据均可见 | `scripts/verify_session_reader.py` |
| Wheel | 包含 5 个静态资源与 transcripts 模块；隔离安装后演示、CSS、会话 RPC 通过 | [结构化验证](session-reader-verification.json) |
| 静态检查 | 修改的 Python 文件 Ruff 通过，公开 schema 与实现一致 | 同上 |

复现命令（浏览器工具为开发依赖）：

```bash
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src python3.11 -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python scripts/verify_session_reader.py --browser /usr/bin/chromium-browser
PYTHONPATH=src .venv/bin/python scripts/verify_console_browser.py --browser /usr/bin/chromium-browser
python3.11 scripts/export_schemas.py --check
```

## 截图

- [桌面会话、任务输入与实际工具返回](screenshots/session-reader/conversation.png)
- [故障退出的上一代会话](screenshots/session-reader/previous-generation.png)
- [手机特大字号](screenshots/session-reader/mobile-large.png)
- [代理进程冻结与独立 Job](screenshots/session-reader/frozen-process.png)
- [Controller 离线时的会话快照](screenshots/session-reader/offline-snapshot.png)

## 部署与历史边界

需按 [部署指南](DEPLOYMENT.md#9-备份升级回滚和完整停机) 更新并重启 Controller 与 Console。旧 release 启动的代理不会自动取得新采集代码；等待正常换代，或通过明确确认的 Hold/Resume 换代后才能开始采集。未留存的旧会话无法补齐。

此轮未访问另一台服务器的真实 GLM、企业数据、GPU/NPU 或训练任务；验证范围是本地原生 CLI + 假 API，以及专用合成进程。运行记录只保存在部署状态目录，不能当作仓库源文件回传。完整含义见 [Console 指南](YAN_CONSOLE.md) 与 [安全边界](SECURITY.md)。
