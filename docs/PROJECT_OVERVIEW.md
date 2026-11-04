# 项目概览与本地验证

[文档目录](README.md) · [项目首页](../README.md)

本文提供详细的工程介绍、运行与验证命令。以下源码命令均在仓库根目录执行；跨服务器的持久部署使用独立的部署指南。

**持久 Task、可替换的 Sui Proxy、独立运行的 Job。**

司岁台是独立于 AI 会话的本地任务控制平面。它把用户意图、AI 声明和 Runner 的真实观测分别记录，让秉烛人在代理失效时按证据与预算恢复任务，让大炎入口随时可查询、可干预。

v0.4 提供 **Yan Console**：四个本地 Web 页面，用来观察代理身份与换代、Task/Job、故障证据、自动恢复及人工指令回执。Python 3.11+ / Linux，运行时仅用标准库；前端为原生 HTML/CSS/JavaScript，无框架、CDN 或 npm 构建依赖。当前托管 Runner 只实现合成 Job；`claude_code` 执行能力允许代理使用原生工具运行真实命令，但外部工作不会自动成为托管 Job。真实 GLM、GPU/NPU 与远程执行仍需在目标环境验收，详见 [任务书与执行能力](TASK_BRIEFS.md)。

**另一台服务器从克隆到部署：** 见 [部署与验收指南](DEPLOYMENT.md)，包括固定版本安装、持久状态、systemd 用户服务、SSH 访问、Mock/GLM 验收、备份与升级回滚；[服务模板](../deploy/systemd/user/sui-regulator-controller.service) 随仓库提供。

## 核心角色

Sui Proxy（岁兽代理人）是可替换的 AI 会话角色，Task 和独立 Job 跨其代际持续存在。完整职责表、Session / Generation 含义和旧字段兼容说明见 [命名体系](NAMING.md)。进程边界、账本和副作用授权见 [整体架构](ARCHITECTURE.md)。

## 先看一个完整演示

在仓库根目录运行，无需模型 API 或 Claude：

```bash
PYTHONPATH=src python3.11 -m sui_regulator console-demo --port 8765
```

打开 **http://127.0.0.1:8765**。演示创建隔离状态目录、独立 Controller、独立 Console 和专用合成进程，可观察：

- 第一代 Sui Proxy 出现 GEN-001 文本退化，Candleholder 检测并撤销其 lease，新代际接管同一 Task。
- 原 Job ID/PID 保留，execution 仍为一条；独立 Runner 继续产生真实的合成 step/loss 指标。
- Job UNKNOWN、历史完成、暂停任务及正常 Handoff/换代。
- 从 Task 详情 Hold、Resume、追加指令，查看真实 Command Receipt；Hold 需要明确确认。

演示工具退出时清理自己登记的专用进程，拒绝使用任何已有状态目录。普通 Console 退出不影响 Controller、代理或 Job。可用 `--state-dir /tmp/yan-console-new-demo` 保留演示账本，目录必须不存在。

## Yan Console

| 页面 | 可查看与操作 |
|---|---|
| 值守总览 / Overview | 全局计数、最近故障、控制事件、Candleholder tick 和最后数据时间 |
| 代理名册 / Sui Proxies | 所有代际分页与会话实录，另有身份、Generation、心跳、最后活动、冻结/失联、Task、故障和 Handoff |
| 任务案卷 / Tasks | Desired、Declared（未核验 AI 声明）、Observed（Runner 观测）分别显示；Job、指标、执行记录和人工干预 |
| 故障案录 / Incidents | 规则版本、证据、检测建议、Candleholder 最终动作、应用状态及回执 |

自动刷新可限频、暂停和手动触发，隐藏标签页停止轮询，错误时退避。Controller 离线或 tick 过期时显示最后快照及原时间戳，不维持绿色实时状态，停用人工操作。Console 通过已有 Unix RPC 工作，不打开 SQLite，不直接杀进程。没有聊天、任意 Shell、公网控制端口或新的身份体系。

<details>
<summary>查看真实浏览器截图</summary>

![Yan Console 总览](screenshots/yan-console/overview-zh.png)

![GEN-001 在会话实录中的位置](screenshots/yan-reader/incident-in-conversation.png)

更多：[角色区分与工具折叠](screenshots/yan-reader/conversation-roles-zh.png) · [英文界面](screenshots/yan-console/overview-en.png) · [Markdown 任务书](screenshots/yan-reader/task-markdown-zh.png) · [手机阅读](screenshots/yan-reader/conversation-mobile.png) · [本轮验收](YAN_CONSOLE_READING.md)

</details>

## 连接自己的本地账本

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator --home .sui init
python3.11 -m sui_regulator --home .sui serve
# 另一终端：
python3.11 -m sui_regulator --home .sui console --port 8765
# 再提交一个合成任务：
python3.11 -m sui_regulator --home .sui submit '合成指标观测' --key trial-1 --spec '{"steps":1000,"interval":0.1}'
```

只绑定 127.0.0.1。经授权的 SSH 访问可在本机使用 `ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 YOUR_AUTHORIZED_HOST`，然后打开本机端口。详见 [运行、演示与 SSH 指南](YAN_CONSOLE.md)。同源保护不等于多用户鉴权，部署边界为可信本机与经授权的端口转发。

## 故障检测与恢复

14 条版本化规则、42 条正反例轨迹可完全离线重放，覆盖生成重复、非法工具结构、相同失败循环、传输错误/断流/静默、进程与 lease、会话冲突、进展和未知异常。建议动作包括告警、只读探测、有限重试、新代理、等待和隔离；最终决策受人工意图、规则/全局预算与未决 Job 约束。

```bash
PYTHONPATH=src python3.11 -m sui_regulator failures list
PYTHONPATH=src python3.11 -m sui_regulator failures evaluate tests/failure_corpus --summary
PYTHONPATH=src python3.11 -m sui_regulator --home .sui rpc yan.get_incidents --params '{"task_id":"TASK_ID","after":0,"limit":100}'
```

当前回归集 23 条故障全部检出，17 条正常轨迹无告警；这不估计真实 GLM 的召回率或误报率。真实 Claude CLI 已在本地假 API 上验证 text_delta 观察和生成结束前的重复拦截；未收到增量时只显示 event-only，不宣称逐 tokenizer token 监控或语义质量判断。

## 安装与验证

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/yan console-demo --port 8765

# 标准库 / SQLite / 专用进程测试，无 API：
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
# 安装真实 CLI 并测试本地假 Messages API：
python3.11 scripts/install_claude.py
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src python3.11 -m unittest discover -s tests -v
# 可选浏览器验收工具，不是 Console 运行依赖：
.venv/bin/python -m pip install playwright
PYTHONPATH=src .venv/bin/python scripts/verify_console_browser.py --browser /usr/bin/chromium-browser
python3.11 scripts/export_schemas.py --check
```

Claude 安装器校验官方原生包 integrity，不需要 Node、npm 或 root；已验证版本 2.1.316。真实平台的端点、鉴权、GLM dialect、长时间行为、训练生命周期、持久卷和远程授权仍需接续验证。默认 `managed` 能力使用受管 MCP、读取工具与经配置的固定命令；真实代码修改和命令执行需明确选择 `claude_code`。

## 当前边界

当前是 Linux 单 UID、本地 SQLite、单 Controller 的可运行原型；没有远程 SaaS、分布式控制、多租户或 GPU 调度。Task 的完成声明不自动证明科研结论，指令回执成功只证明该指令定义的效果已应用。历史需求与验收证据保留原术语，当前角色约定以 Sui Proxy 为准。

开发与演示仅操作隔离状态目录、专用进程和合成数据，不连接公司数据或操作现有训练。更多限制见 [安全边界](SECURITY.md)；逐项验收证据见 [v0.4 交付报告](YAN_CONSOLE_DELIVERY.md)。
