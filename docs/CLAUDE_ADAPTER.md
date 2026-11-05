# Claude Code Adapter

已安装并检查官方原生 Claude Code **2.1.316**，执行 `claude --version` / `--help` 确认参数。官方安装脚本在当前网络返回 403，使用 [官方 npm 平台包](https://www.npmjs.com/package/@anthropic-ai/claude-code-linux-x64)的原始二进制并校验 manifest SHA-512 integrity。安装脚本为 `scripts/install_claude.py`；不需要 npm、Node 或 root，不修改 shell rc/宿主机服务，不将二进制提交到 Git。

```bash
python3.11 scripts/install_claude.py
claude --version
```

版本文件位于 `~/.local/share/sui-regulator/tools/claude-code/2.1.316/`，`~/.local/bin/claude` 在不存在时建立符号链接。可在私有 config.toml 的 claude_binary 指定其他版本绝对路径。

Adapter 使用 [非交互 CLI](https://code.claude.com/docs/en/headless) 的 print + verbose stream-json + include-partial-messages、显式 session UUID、空 setting-sources、自有 Hooks、strict-mcp-config、显式工具 allowlist、permission-prompts none 和独立配置目录。guarded 使用 dontAsk 和配置的 max-turns；trusted 使用 bypassPermissions、持续打开的 stream-json 输入，不设 max-turns。managed 档只开放 Read/Glob/Grep，claude_code 档开放原生 Edit/Write/Bash。创建 trusted 任务即包含 claude_code 授权。启动参数以安装版本的 help 和实际假服务测试为准；后续版本须重跑合约测试。

Wrapper 自动采集进程身份、真实 session_id、结构化流事件、API retry、工具结果与结束状态，独立续租。stdout/stderr 同时排空，单行与 stderr 内存有上限。没有最终 result 或末行截断不视为成功。guarded + Persistent 下 401/403 等凭据问题进入显式 WAITING_RESOURCE；Bounded 保留隔离策略。trusted 的运行故障持续退避，正常一轮结束则在同一 CLI 会话中继续，不重放工具调用。

v0.3 的 ClaudeStreamDetector 仅作兼容入口；ClaudeNormalizer 解码并去重 partial/full，DetectionEngine 执行版本规则，持久 Incident 由 Candleholder 决策。Sui Proxy 查询的 stream_observation 在真正收到 text_delta 前是 event-only，之后为 text-delta-observed，同时记录能力审计。thinking 只处理公开块，子 Agent 事件暂不混入根 Session。

guarded 的 CLI turn 在 WAITING_JOB 结束后，Wrapper 可继续低成本观测原 Job；Job 到达终态后移交给 fresh Session。trusted 的正常轮次与 wait_task 保留同一个 CLI 进程、Session 和 Generation。模型响应静默阈值为 600 秒；thinking／compact 等事件刷新活动，已知工具执行期间免于该超时，详见 [信任模式](TRUSTED_AUTONOMY.md#会话寿命与静默)。控制器不把模型的一句成功当作 Task/Job 完成。

CLI 子进程设置 Linux parent-death signal，Wrapper 被强杀时 CLI 同时退出；独立 Job 从不设置该依附关系。

## 已验证

- CLI 模拟器：实际 stdio MCP、稳定 Job key、等待后 fresh Session、无重复 Job、401 隔离、截断 JSON、空 Bash 调用、大量 stderr。
- **真实安装的 CLI + 本地 FakeAnthropic**：Messages SSE → Claude CLI → 本项目 MCP → get_task_context/report_progress/complete_task 全链路；401 API retry 观察与隔离。
- **真实 CLI 的增量与故障测试**：后端结束前收到 text_delta；重复生成结束前产生 GEN-001 并应用恢复，不等待完整消息。
- **真实 CLI 的信任模式测试**：UNKNOWN 不阻止原生调试工具；代理核验后可完成；正常一轮及定时等待后，进程、会话、代际和历史上下文保持不变。
- 测试仅使用 `127.0.0.1` 假服务与合成 API key；测试子进程清除代理设置，避免回环请求被环境代理转发。

运行：

```bash
PYTHONPATH=src SUI_TEST_REAL_CLAUDE=1 python3.11 -m unittest tests.test_claude.InstalledClaudeTests tests.test_stream_capability -v
```

默认无 API 测试不需要安装 Claude；上述 CLI 测试需主动设置 SUI_TEST_REAL_CLAUDE=1，否则明确 skip。

## 真实平台仍需验证

内部 GLM 的地址、模型名、鉴权、Messages/SSE dialect、工具格式、CLI 限制、企业网络和长时间行为都未验证。Adapter 不会自动猜测 endpoint 或读取公司凭据。实际接入由操作员通过批准的运行环境设置 ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY 等配置。

managed 默认提供只读原生工具、Sui MCP 与批准的模板；完整代码开发需在创建任务时选择 claude_code，允许原生 Edit / Write / Bash。真实实验可以由模型通过这些工具操作，但不会自动成为托管 Job。真实训练 Runner、检查点、TensorBoard、远程资源与训练终止仍需真实平台集成。磁盘 resume/export、VS Code Extension 控制没有验证；实际进程故障后的恢复依赖持久账本创建新 Session。trusted 的健康续接使用同一在运行的 CLI 内存上下文，不是磁盘恢复。

完整任务书、原生代码调试与完成前核验见 [任务书与执行能力](TASK_BRIEFS.md)。固定协议通过 append-system-prompt 追加，用户任务书作为独立初始输入完整传入，不再以巡检替代任务目标。

两个模式均使用显式的执行前 / 后 Hooks，并取消 bare（bare 会屏蔽 Hooks）。Hook 失败阻止操作，版本要求与测试详见 [Persistent 恢复](PERSISTENT_RECOVERY.md)。

当前版本要求 Claude Code ≥ 2.1.316，两个 profile 都启用 Sui 显式 Pre/PostToolUse 和 Stop Hooks，不能用 bare 禁用提醒。managed 工具权限仍为 Read / Glob / Grep 与 Sui MCP。长 Session 指令投递、已读 ACK 和完成版本保护见 [接续协议](PROMPT_AND_LIFECYCLE_DESIGN.md#本轮接续协议schema-7)。
