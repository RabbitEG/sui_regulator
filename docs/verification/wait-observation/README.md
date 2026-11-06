# 等待失效回归验证

日期：2026-11-06。使用本地合成任务、真实 Linux 子进程、持续输入 CLI fixture，以及本机 Claude Code 2.1.316 + loopback 合成 Messages API。没有连接真实 GLM 或提交远端实验。

结果：全部 **215 项 Python 测试通过，无跳过**，其中真实安装 CLI 测试 11 项；Ruff 与 `git diff --check` 通过。[完整测试输出](python-tests.txt)。浏览器完成中文、英文、移动端无横向溢出及同代保留四项检查，无脚本错误：[浏览器结果](browser.json)、[中文截图](wake-reason-zh.png)、[英文截图](wake-reason-en.png)。

## 原因及修复

旧实现以未返回的 tool_use 和账本中 RUNNING 原生回执长期豁免静默检测。调用已结束但返回丢失、后台执行消失且终态通知丢失时，这些旧状态仍可让 Wrapper 认为工具一直在运行。登记等待也未在关联 Job 变成 UNKNOWN 时唤醒。

现在由 Wrapper 持续观察同一 CLI 的本地启动进程和匹配的运行事件；只有新观测可以续期。旧消息、Wrapper 心跳和请求的 timeout 不会续期。观测丢失只生成 UNKNOWN 及审计，不伪造实验失败。正常结束一轮的 trusted 会话被唤醒核验；仍悬挂的一轮在完整响应静默窗口后才恢复。

## 覆盖场景

| 场景 | 预期与断言 |
| --- | --- |
| 只有“sleep / 等到某时或事件”的文本 | 不提供工具运行豁免，不创建持久定时器 |
| 工具返回一直丢失 | 旧调用的观测窗口过期后可进入响应静默检测 |
| 请求了极长工具 timeout | 参数本身不续期运行观测 |
| 真实 sleep 仍在运行 | 超过观测及响应窗口仍保留原 Session |
| sleep 被杀，工具返回及终态通知均丢失 | 账本转 UNKNOWN；后台路径继续原会话核验，无重复提交 |
| 悬挂前台调用的启动进程退出 | 给足响应时间；之后的静默按 NET-002 恢复，Task 仍 ACTIVE |
| 启动进程被冻结或无法继续观测 | 不能凭旧 PID 记录永久续期 |
| 不相关进程的参数含相同命令文本 | 不作为该调用仍运行的证据 |
| 收到终态 Hook，但 stream tool_result 丢失 | 移除旧调用的等待豁免 |
| 观测丢失后迟到真实 Hook | 接受迟到结果；已有真实终态不被 UNKNOWN 覆盖 |
| 重复检查同一失效调用 | 不反复重置响应静默时间 |
| 其他 Session／其他调用的进度事件 | 不续期当前调用 |
| 关联 Job 变成 UNKNOWN | 提前唤醒同代代理；Job 保持 UNKNOWN，不启动第二份 |
| Hold、Cancel、实验失败、慢调试 | 保留既有用户控制和职责边界回归 |

重点测试：`tests/test_wait_observation.py`、`tests/test_trusted_session_lifetime.py`、`tests/test_claude.py::InstalledClaudeTests.test_real_cli_sleep_outlives_observation_lease_without_rotation`。

运行全部 Python 回归（含真实 CLI、本地模拟 API）：

```bash
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python scripts/verify_wait_browser.py
```

CLI 必须可通过 PATH 找到；真实 CLI 用例会将端点覆盖为本地合成 API。测试缩短部分时间窗口，并用虚拟时间验证十分钟和多小时边界，不代表已完成一周实测。

## 可观察边界

进程关联使用有界 Linux `/proc` 读取、同 CLI 子进程范围、调用时间及命令执行参数，保存 PID/启动时间/boot ID 身份。不是通用 Shell 解析器，也不推断实验进度；不支持的启动方式回退为有限观测窗口。完整命令行仅用于瞬时匹配，不另行写入观测事件。

本地 SSH 或事件监听进程仍活着时，框架无法据此断言远端工作健康。外部事件没有新增订阅／调度接口；代理必须用有限的 `wait_task` 间隔复查实际目标。该限制已写入提示词 v6 和部署文档。

Claude Code 的 Bash timeout 可能将命令转为后台运行，因此超时参数不等于实际退出；实现以 Hook、运行事件和本地进程观测为依据。参见 [官方工具说明](https://code.claude.com/docs/en/tools-reference) 和 [官方流事件说明](https://code.claude.com/docs/en/agent-sdk/streaming-output)。
