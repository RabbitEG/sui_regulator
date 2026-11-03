# 自主任务、提示词与恢复边界交付

日期：2026-11-04。基于已完成的 Yan Console / Session 阅读器继续实施；合并保留另一台服务器的 `e96154b`（GLM-5.3：managed 模式加入 Read / Glob / Grep）。任务书首阶段为 `d621476`，本阶段包含 Persistent、合作式换代与完成协议。

## 本次解决的问题

创建任务现在可以填写完整任务书、项目目录、验收条件、执行能力和运行预算。`sui-proxy.prompt.v2` 分开组织用户要求、追加指令、执行约束、实际回执与代理历史笔记；初次工作和接续工作有不同提示。固定协议不会把“等待实验 → 核验 → 调试 → 继续实验”替换为 watchdog 巡检。

- 实验失败、测试失败、重复失败、慢速调试和缺少进展证据不触发 Session 恢复；模型决定实验判断和调试方法。重复结果保留观察提示，允许合理复测。
- 新任务默认 Persistent。运行基础设施故障持续恢复并随机指数退避；保留 Bounded 供受限场景使用。资源额度为累计 Proxy 墙钟时间，达到后 HOLD。
- 年龄只请求交接，没有强制换代期限；必须收到请求之后的新 Handoff 才正常换代。真正进程死亡、失联、协议或身份异常仍处理。
- Claude Code 模式执行真实项目 Read / Edit / Write / Bash，Hook 检查所有权、记录操作回执。后台 Bash 的结构化结束通知能自动解除未决回执，失败结果也允许同一 Session 继续工作。
- 完成通过本代 JSON 标记或 `complete_task`；Controller 核对当前用户要求版本、身份、未决操作和可选核验脚本，然后自动结束 Task。模型自述和普通“done”文字不能直接结束任务。
- Console 保留大字号和会话阅读器，增加完整任务、完成标记与回执、恢复策略和命令结果展示。Hold、Resume、Instruct 与策略编辑均走真实 Yan Command。

设计依据、提示词结构与完成字段见 [长期 Agent 设计](PROMPT_AND_LIFECYCLE_DESIGN.md)；包括 Temporal、Anthropic 长任务运行框架、LangGraph 和 Kubernetes 的对应思想及本项目的具体取舍。

## 验证结果

| 检查 | 结果 |
|---|---|
| Python 3.11 全套回归 | 136 项通过，无跳过；[原始输出](autonomy-test-output.txt) |
| InstalledClaudeTests | 7 项通过，真实 Claude Code 2.1.316 + 本地 loopback 假 API，已包含在总数内 |
| Console 浏览器 | 15 项通过 |
| Session 阅读器浏览器 | 15 项通过 |
| 完整任务与恢复策略浏览器 | 7 项通过，HTTP → RPC → SQLite 的真实回执；[报告](task-brief-browser-report.json) |
| 安装包 | 构建 wheel、独立 venv 非源码安装、schema 5、新模块及 Mock 故障恢复演示通过；[报告](autonomy-wheel-verification.json) |
| 静态与接口检查 | Ruff、schema 导出一致性、git diff --check 通过 |

136 项中包含：连续 31 次 Proxy 启动失败仍保持 Task；失败实验不隔离；4 次原样实验失败仍允许执行；30 次修改但结果未改善不会被误算为已核验进展；15/60 分钟观测不换代；慢速健康代理越过年龄与 grace；后台实验失败后继续；完成标记自动验收、旧代 / 新指令 / Hold 冲突拒绝；Unicode 长任务书和证据完整传输；未知副作用保护与人工核验。

修复了一个真实时序问题：终止 Incident 可能在 Controller 获取本轮待处理列表之后入账，随后健康回收抢先撤销所有权，使该 Incident 被标为过时代际。现在事务内先检查待处理恢复决策，避免抢先回收；新增可稳定触发该顺序的测试，另做 20 次真实进程重复验证通过。

浏览器报告汇总：[autonomy-verification.json](autonomy-verification.json)。截图：[创建任务](screenshots/task-brief/create-task.png) · [完整要求、完成标记与恢复策略](screenshots/task-brief/task-requirements.png) · [会话阅读器](SESSION_READER_DELIVERY.md)。截图中的实验数据均为合成。

## 本地复现

仓库虚拟环境安装好以后：

```bash
PYTHONPATH=src .venv/bin/python -m sui_regulator console-demo --port 8766
# 另一终端；Claude Code 已安装时，使用本地假 API，不需要真实模型 key：
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
# 已安装 Playwright 和 Chromium：
PYTHONPATH=src .venv/bin/python scripts/verify_task_brief_browser.py --browser /absolute/path/to/chromium
```

浏览器打开 `http://127.0.0.1:8766`，可查看合成故障与换代，创建 Mock 任务、填写多行需求，再核对任务详情和 Command Receipt。Mock 不理解或执行任务书；完整调试流程由 InstalledClaudeTests 驱动实际 CLI 执行预设工具决策。

## 另一台服务器部署

完整安装、SSH 转发、systemd、备份、升级与回滚按 [部署指南](DEPLOYMENT.md)。升级需要新的 release venv，并同时更新 Controller / Console，不能只 pull 源码而继续运行旧安装包。

本次迁移到 schema 5：**先备份，再升级**；已有 Task 保持 managed + bounded，新建 Task 默认 persistent。扩大代码执行权限需要显式选择 claude_code。恢复策略更新不会自行解除 Hold。真实实验新任务示例：

```bash
SUI_YAN="$HOME/sui-regulator/current/venv/bin/yan"
SUI_STATE="$HOME/.local/state/sui-regulator"
"$SUI_YAN" --home "$SUI_STATE" submit '实验核验、调试与继续' \
  --adapter claude --execution-profile claude_code \
  --workspace /absolute/project --objective-file /absolute/task-brief.md \
  --completion-criteria '填写具体交付条件、证据和结果路径' \
  --recovery-mode persistent
# 使用返回的真实 ID：
"$SUI_YAN" --home "$SUI_STATE" receipt COMMAND_ID
"$SUI_YAN" --home "$SUI_STATE" get TASK_ID
```

任务书应说明已有实验 ID、查询方式、授权范围、等待和调试要求。Console 也可填写相同字段。完成标记路径由 `get_task_context` 提供，模型按模板写入；用户无需手工猜路径或 Generation。可执行的验收脚本通过 `verification_template` 明确配置，见 [任务书指南](TASK_BRIEFS.md)。

## 已知边界

未使用真实 GLM API、正式训练或目标服务器集群做验收。loopback 预设模型决策验证的是运行链路，不能证明真实模型的理解、科研判断或最终任务质量。

原生模式具有服务 UID 的文件和命令权限，不是项目沙箱。Bash / SSH / 外部实验不自动成为托管 Job，不具有任意远程操作 exactly-once 保证；CLI 的后台结束通知只覆盖它所追踪的命令，缺失通知或进程失联仍需核验。累计 Proxy 秒数不是 token、GPU 或账单额度。资源迁移由 Session 内的模型在授权范围内处理，未新增调度器。
