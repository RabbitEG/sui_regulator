# 第一轮交付报告

已完成需求中第一轮的源码调查、环境核验、最小架构、Python 项目骨架和可执行故障恢复原型，并增加用户要求的 Claude Code 安装/适配。每阶段独立提交并同步到 origin/main；结构化状态见 PROGRESS.json。

## 已交付

- 独立 Python Controller / Candleholder、SQLite WAL + 两版事务迁移、Task/Command/WorkerSession/Experiment/Job/Artifact/Event。
- 实际 Sui Protocol、lease hash/到期校验、generation fencing、执行端启动/取消授权、逻辑 Job 幂等。
- 真正独立进程的合成 Experiment Runner，JSONL 指标/结果、退出状态、UNKNOWN reconciliation、明确的合成 Job 取消。
- Mock/Claude WorkerAdapter、Worker 强杀接管、正常/无 Handoff 轮换、恢复冷却/持久预算/隔离。
- 私有 Worker 进程登记：在 SQLite 因冻结 Worker 锁住时，Watchdog 仍可在数据库访问前核验身份、处理到期受管 Worker，随后恢复审计。
- JSON CLI、同 UID 鉴权的 Unix RPC、owner/worker 分离的 MCP stdio、有版本的导出 Schema。
- 官方原生 Claude Code 2.1.316、可复现安装器、实际 CLI + 本地假 API 的 MCP 联调。
- 一致性备份、需求/设计/安全/恢复/测试文档和本地演示。

## 关键验证

完整 Python 3.11 套件包含 **54 项测试**：52 项标准库/合成进程测试，2 项真实安装 CLI + 本地假 API 测试；原始输出见 `test-output.txt`。默认执行明确跳过后两项。另执行 Python 3.12、Schema 同步、Ruff、wheel 构建及安装后的 console script 演示，具体结果记录于 `verification.json`。

`demo` 中第一代 Worker 被 SIGKILL，第二代接管；Job ID 与 Runner PID 不变，execution 记录只有一条，Job 达到 COMPLETED 并返回合成指标。Controller SIGKILL 后也能重新建立管理关系。启动回执丢失被核验，不会无条件重发。未知 Runner 的结局保留 UNKNOWN。

上游调查检查七个公开项目的固定源码/测试版本；其中 Codex Task Watchdog 38 项与 Agent CLI Farm manifest 18 项通过。这 56 项不计入本项目 54 项。

## 未验证及边界

1. **真实 GLM/模型 API、GPU/NPU、长训练、远程机器与容器重建尚未验证。** 假服务验证接口行为，不验证模型能力或企业平台兼容性。
2. Runner 只执行合成实验，不执行任意 shell、真实训练或抢占资源；Claude 默认仅受管 MCP。完整代码编辑和真实训练生命周期留给真实平台 Adapter。
3. 当前是 Linux 单 UID、单 Controller；没有远程 TLS/RBAC、多租户、分布式 Controller、完整审批对象、依赖调度、原生 Session 导出/恢复或指标平台。
4. 状态依赖可用且正确持久化的本地文件系统与 SQLite；生产须验证持久卷和锁语义，配置 OS/容器守护、备份、磁盘保留策略。进程登记只提供清理证据，不授予任务所有权。
5. 老内核没有 pidfd；身份检查到 signal 的末端仍有窄的 PID 复用竞态。真实平台建议受管 cgroup/作业服务或支持 pidfd 的内核。
6. Task 完成包含 Worker 语义声明；系统核验受管 Job 状态，不自动证明科研结论。指令回执成功只代表要求已记录或控制状态已应用。

## 真实平台接续顺序

1. 在批准的 Python 3.11+ / Linux 环境运行无 API 测试与 demo，确认状态卷、flock、进程身份和重启策略。
2. 安装/固定 Claude CLI，运行本地假 API 合约测试；再通过私有环境设置批准的 GLM endpoint/模型名/鉴权，先验证小任务与工具调用。
3. 为实际平台实现 ExperimentRunner，提供稳定 operation ID、客观查询、授权取消和 checkpoint/指标 Adapter，不把训练挂在 AI Shell 下。
4. 使用短小、专用实验做故障注入，确认不会重复启动或误杀，再开展长训练。公司网络、数据或真实训练终止需相应授权。
5. 之后接入 Ray Jobs/SkyPilot、经授权远程 Yan、多机和依赖调度；成熟工作流需求出现后重新评估 Temporal。

仓库保存接续依据，不依赖本次聊天上下文。
