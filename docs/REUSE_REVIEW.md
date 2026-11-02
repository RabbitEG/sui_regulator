# 复用调查（2026-11-02）

结论：首轮采用小型独立 Python 控制平面，复用操作系统进程隔离、SQLite 事务和现有 Agent CLI；暂不 fork 通用编排器。保留 WorkerAdapter、ExperimentRunner 接口，后续按实际集群接入 Ray Jobs / SkyPilot。这里的判断来自以下固定版本的源码、测试和文档；没有把未执行的上游测试写成已通过。

## 功能重叠矩阵

✓：源码包含相应机制；△：部分或不同语义；—：本次没有发现，不表示项目永久不支持。

| 项目 | Worker 生命周期 | 故障巡检 | 跨 Session 接续 | 机器查询/控制 | 独立实验 Job | 本项目执行端 fencing |
|---|---|---|---|---|---|---|
| Gas City | ✓ | ✓ | ✓ | ✓ | △ | 需额外实现 |
| AgentPulse（jstuart0） | ✓ | △ | △ | ✓ / MCP | — | 需额外实现 |
| Ralph（snarktank） | ✓ / 循环 | △ / 退出 | ✓ / 文件 | — | — | — |
| Codex Task Watchdog | 观察 | ✓ | 恢复建议 | CLI | 观察 | — |
| OmniSession | 启动/恢复 | — | ✓ / export/fork/handoff | CLI | — | — |
| Agent CLI Farm | ✓ / tmux | ✓ | ✓ / manifest | CLI | △ | 需额外实现 |
| cc-resume-watchdog | turn 恢复 | ✓ / 流错误 | 同 Session | hooks | — | — |
| Temporal | ✓ / Workflow | ✓ | ✓ | Query/Signal/Update | Activity 抽象 | 外部副作用仍须幂等 |
| Ray Jobs / SkyPilot | 作业管理 | 作业巡检 | 作业恢复 | API/CLI | ✓ | Adapter 须映射授权 |

同名项目较多：AgentPulse 选择与需求所述「远程操控、MCP」一致的 jstuart0/agentpulse；Ralph 选择 snarktank/ralph，不将其他同名实现的功能混入结论。

## 固定版本与具体证据

- **Gas City**：[efbf990](https://github.com/gastownhall/gascity/tree/efbf990016307cee6d67fa99e6f6b74fc9db1ce1)。检查 `cmd/gc/crash_tracker.go`、对应测试、`engdocs/architecture/health-patrol.md`、`go.mod`。可借鉴 reconciliation、窗口限流和隔离。该版本的 crash tracker 明确只驻内存，重启即清空；司岁台必须将恢复预算持久化。依赖 Beads、Go 及较大的运行生态。未安装 Go，没有执行其测试。
- **AgentPulse**：[933e335](https://github.com/jstuart0/agentpulse/tree/933e335d406f2c6d62bace4f3ba8a4af6fa952ec)。检查 `src/supervisor/providers/claude-headless.ts`、对应测试、`packages/agentpulse-mcp/src/tools/session-actions.ts`。现成的 headless/provider 分层、流事件和 managed-only 操作值得参考；测试覆盖 reporting 失败与受管子进程终止。MCP 确认提示不等于执行端授权。其 Bun/TypeScript 服务和 Web 体系超出首轮范围；未执行 Bun 测试。
- **Ralph**：[6c53cb0](https://github.com/snarktank/ralph/tree/6c53cb0b831ebe8739c6a003e22af14902d8b0b5)。完整检查 `ralph.sh`、提示词与 progress 文件机制。借鉴 fresh-context 与文件接续，不复用以输出完成标记判定成功的逻辑，也不复用默认跳过权限的启动参数。脚本没有任务 lease、执行账本或独立实验状态。本次未发现主循环的自动化测试。
- **Codex Task Watchdog**：[d14ce83](https://github.com/TanChuping/codex-task-watchdog/tree/d14ce8325ccd9751045dd18a5a542da7fbd22a47)。检查 `skills/codex-watchdog/scripts/codex_watchdog.py` 及其测试。可借鉴「无进展证据不代表允许重试」和有界采集。运行 `python3.11 skills/codex-watchdog/scripts/test_codex_watchdog.py`：**38/38 通过**。其 Windows/Codex 私有日志布局不能直接假定兼容 Linux/GLM。
- **OmniSession**：[a7f5763](https://github.com/bvolpato/omnisession/tree/a7f57633217a5851103eeebd1f80a3d2e542b6e6)。检查 `crates/omnis-cli/src/handoff.rs`、`tests/handoff.rs`、RFC 004。可选通过 CLI 集成导出；借鉴历史工具调用只作历史、私有文件、版本检查、失败回滚。没有采用其私有 Session 写入器作为控制平面基础。未安装 Rust，没有执行其测试。
- **Agent CLI Farm**：[44b264d](https://github.com/waskosky/agent-cli-farm/tree/44b264dc87a3abdfd9b2e2cb3fcc171321cdbf47)。检查 `bin/codex-manifest.py`、`tests/test_manifest.py`、`tests/test_startup_recovery.py` 与 looper 测试。manifest 的精确 ID、锁与恢复回执可借鉴。运行 `python3.11 -m unittest discover -s tests -p test_manifest.py -v`：**18/18 通过**。tmux 不能代替 Job 所有权和副作用账本。
- **cc-resume-watchdog**：[3ac82a2](https://github.com/nnemirovsky/cc-resume-watchdog/tree/3ac82a2e0e3cd453cc66721243cfcf547ca9519b)。检查 `bin/cc-resume-watch` 与 `tests/resume-watch.bats`、`tests/arm.bats`、`tests/subagents.bats`。借鉴 transient 分类、消息去重和跨 watcher 限流。它恢复 turn，不接管独立 Task/Job；不作为默认自动 Continue 机制。缺少 bats，未执行其测试。

上述七个仓库均克隆到工作区外的临时目录；仅参考设计，没有复制其源码。相关许可证已检查为 MIT；未来移植时须保留原始版权和许可证。

## 较大依赖的取舍

[Temporal 消息机制](https://docs.temporal.io/encyclopedia/workflow-message-passing)已提供持久 Workflow 和 Query/Signal/Update 语义，但 Activity 的外部副作用仍需幂等。首轮仅文档级评估，未运行其服务或源码测试。适合后续多 Controller/跨机可靠工作流，不为单机 MVP 增加服务集群。

[Ray JobSubmissionClient](https://docs.ray.io/en/latest/cluster/running-applications/job-submission/doc/ray.job_submission.JobSubmissionClient.submit_job.html)可提供稳定 submission ID 和异步作业生命周期，但作业依赖 Ray 集群，重试由提交方负责。[SkyPilot managed jobs](https://docs.skypilot.ai/en/latest/examples/managed-jobs.html)适合资源与作业恢复。两者本轮仅接口文档评估；没有安装、访问真实集群或宣称集成通过。

| 路径 | 可节省工作 | 仍需开发 | 成本判断（工程估计） |
|---|---|---|---|
| fork Gas City | 生命周期、现有巡检、编排生态 | Python/GLM 边界、Experiment Controller、fencing、Yan 语义 | 对既有 Gas City 用户有价值；本场景需长期维护上游融合与领域改造 |
| 扩展 AgentPulse | provider、观测、远程/MCP/UI | 独立任务账本、Job、恢复和权限 | 如果优先远程 UI 很合适；当前会承担两种语言和较大服务面 |
| 小型 Python Controller | 只需本地 SQLite 与进程 | 事务、迁移、所有权、进程识别、故障测试自行维护 | 首轮范围最小；必须投入竞争、崩溃窗口和恢复测试，不能包装成成熟调度器 |

GLM 兼容性无法由这些上游支持 Claude 推导出来。需要真实平台验证 CLI 参数、API dialect、SSE/tool schema、MCP、模型名、鉴权和故障模式。第一轮保留 Mock 和 Claude CLI Adapter，以模拟测试建立证据。
