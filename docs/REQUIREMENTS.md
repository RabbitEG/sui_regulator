# Sui Regulator（司岁台）
## 项目需求与实施规范 v0.2

**项目代号：** Sui Regulator
**中文名称：** 司岁台
**项目类型：** 开源、自托管的 AI Agent 任务编排、远程实验控制与故障恢复系统
**主要实现语言：** Python 3.11+
**目标平台：** Linux（x86-64 / aarch64），优先适配持久化容器环境
**当前阶段：** 需求确定、架构评估与 MVP 原型开发

---

# 0. 给 Astra 的总体指令

我希望你作为这个项目的主要架构设计者与工程实现者，协助设计并开发 Sui Regulator。

**这不是一个新的 AI 聊天客户端，也不是一个新的 W&B，更不是一个简单的 Claude Code 自动发送 Continue 的脚本。**

它要解决的问题是：

> 如何在 AI Coding Agent 自身不稳定、远程计算环境复杂且用户无法持续监控的条件下，保证长期实验任务能够被可靠地观察、干预、接续和恢复。

项目要建立一套独立于 AI Session 的持久控制平面，使得：

1. AI Worker 可以被频繁销毁和重建。
2. 真实实验不因 AI Worker 的失效而丢失。
3. 系统可以独立获取实验的客观运行状态。
4. 外部用户或 AI 可以随时查询、追加和调整任务。
5. 故障后的恢复不会无条件重复执行有副作用的操作。
6. 核心控制系统即使没有任何可用的 LLM，也能继续管理已登记的任务。

请将**可靠性、可恢复性、可观测性和 AI 可交互性**作为最高优先级。

对于现有开源项目已经成熟实现的功能，优先评估复用、集成或借鉴，不要为了建立独立项目而重复实现整套通用 Agent Orchestrator。

---

# 1. 背景与真实使用环境

## 1.1 我的使用场景

我目前在远程计算服务器上开展 AI Infra 和 LLM 推理相关研究。

实验可能涉及：

- LLM 模型训练与增量训练。
- 投机解码（Speculative Decoding）。
- Draft Model Adaptation。
- 不同模型、数据集和训练配置的对照实验。
- GPU/NPU 性能测试。
- 长时间训练、评测与结果分析。
- 多轮代码修改、调试、试错和继续实验。

实验环境中有能够使用工具的 AI Coding Agent，例如通过内部部署模型驱动的 Claude Code。

我希望让这些 Agent 长时间承担开发和实验任务，而不需要本人持续在线监工。

但当前环境具有明显的不稳定性。

## 1.2 AI Agent 自身的问题

已经实际观察到的故障包括：

- 后端模型 API 请求失败。
- `ECONNRESET`、`ECONNREFUSED`、HTTP 503、Overloaded。
- Streaming 响应中断。
- Session 运行一段时间后出现重复输出。
- 模型长时间停留在一个很简单的操作上。
- 连续生成异常工具调用，例如空 Bash 参数。
- 工具调用失败后无法合理恢复。
- 定时注入的监控指令被触发，但 Agent 没有正常执行。
- Session 进程仍然存活，但已经不能有效推进任务。
- 上下文增长后 Agent 行为退化。
- Agent 对环境状态的判断错误，导致实验被不必要地终止。

目前的经验是，一个 Session 有时工作约半小时后就会出现退化。

这并不是确定性的故障阈值，但意味着系统应支持主动的 Session 轮换。

## 1.3 远程计算环境的问题

实验服务器还可能存在：

- 多台共享计算机器。
- GPU/NPU 资源被其他任务占用。
- 训练作业运行时间长。
- 用户无法持续观察。
- 容器或计算环境重建。
- SSH 或其他远程连接中断。
- 文件系统或依赖环境异常。
- 实验进程突然退出。
- 模型权重、数据和实验日志分散在不同位置。

因此，AI Agent 的故障与实验运行环境的故障必须分开处理。

例如：

**AI Session 失效，不代表训练应该停止。**

反过来：

**AI Session 正常，也不代表实验一定正在正确推进。**

## 1.4 为什么不能使用 AI 监控 AI

之前尝试过建立一个长期运行的 Claude Code Session，每小时执行巡检。

但这个监控 Session 本身也会出现：

- 上下文增长。
- 重复输出。
- 工具调用异常。
- 后端连接故障。
- 无法持续完成定时任务。

所以不能继续依赖一个长期运行的 AI Session 作为整个系统的可靠性基础。

需要由传统软件构成长期控制主轴。

AI 只能作为可替换的执行者或辅助分析者。

---

# 2. 项目命名与核心角色

项目采用《明日方舟》大炎、司岁台与岁家世界观命名，但不牺牲软件工程语义清晰度。

## 2.1 命名规范

| 名称 | 英文 | 工程含义 |
|---|---|---|
| 司岁台 | Sui Regulator | 整体控制与编排系统 |
| 大炎 | Yan | 外部指令与交互入口 |
| 岁 | Sui | AI Worker |
| 秉烛人 | Candleholder | 独立 Watchdog |
| 任务 | Task | 持久化工作单元 |
| 敕令 | Edict | 用户提交的命令 |
| 试验 | Trial | 单次实验执行的展示名称 |
| 案卷 | Dossier | 任务历史与持久化档案 |
| 移交 | Handoff | Worker 交接信息 |

**Task 必须保留标准英文名称，不使用 Case、Game 等替代。**

类似地，内部数据模型应保留标准工程概念：

- Task
- Command
- Experiment
- Job
- WorkerSession
- Artifact
- Event

世界观术语可以用于模块、用户交互与命令展示，但不要造成概念混乱。

## 2.2 Sui Regulator：司岁台

司岁台是整个系统的核心控制平面。

它应以不依赖 LLM 的长期运行程序为基础，负责：

- 持久化任务。
- 接收和处理指令。
- 管理 Worker 生命周期。
- 维护任务和实验状态。
- 管理任务执行权限。
- 调度实验作业。
- 进行状态协调。
- 执行故障恢复。
- 记录关键事件与操作历史。

司岁台本身不负责进行科研推理或生成实验结论。

这些工作由 Sui 完成。

## 2.3 Yan：大炎

Yan 是外部交互入口。

外部调用者可以是：

- 人类用户。
- ChatGPT。
- Codex。
- Claude。
- 其他获得授权的 AI 或自动化系统。

Yan 应允许调用者：

- 查询任务。
- 查询实验。
- 查询运行指标。
- 查询 Worker 健康状态。
- 查看错误和日志。
- 下达新任务。
- 修改已有任务要求。
- 要求分析实验结果。
- 暂停、恢复或取消任务。
- 查询历史指令的实际执行结果。

Yan 应当是 **AI-friendly Command Gateway**。

它首先是一套标准化接口，而不是一个必须长期运行的 AI 聊天 Session。

## 2.4 Sui：岁

Sui 是负责真实智能工作的 AI Worker。

它可以由不同模型和 Agent Runtime 支持：

- 内部 GLM + Claude Code。
- Claude Code CLI。
- Codex CLI。
- 未来其他 Agent。

一个 Task 可以由多个 Sui Session 先后接手。

Sui 负责：

- 理解任务。
- 修改代码。
- 设计实验。
- 调试环境。
- 调用受管实验接口。
- 观察实验结果。
- 分析问题。
- 提交进展报告。
- 生成 Handoff。
- 完成研究任务。

Sui 不拥有 Task 的生命周期。

**Sui 可以被替换，Task 必须继续存在。**

## 2.5 Candleholder：秉烛人

Candleholder 是非 AI 监控与恢复组件。

主要实现应为 Python 等传统程序。

负责：

- 发现受管 Sui Session。
- 检查 Worker 是否正常运行。
- 检查协议更新情况。
- 检测异常行为。
- 检查实验进程的客观状态。
- 比较任务期望状态与实际状态。
- 回收失效 Worker。
- 启动替代 Worker。
- 协调 Handoff 与任务接管。
- 清理确认归属于系统的失效 Session。
- 检查重启风暴与重复作业风险。

Candleholder 不应依赖完整读取并理解 Session 聊天记录才能工作。

---

# 3. 最核心的设计思想

## 3.1 Task 与 Session 完全分离

一个 Task 可能跨越：

- 多个 Session。
- 多个 Sui Worker。
- 多个 Experiment。
- 多个计算节点。
- 多次失败与恢复。

Task 不应该因为 Session 结束而消失。

## 3.2 Agent 与实验进程分离

Sui 可以启动一个耗时数小时的实验。

但是该实验应由受管 Experiment Runner 维护生命周期，而非依附于 Sui 的交互进程。

例如：

1. Sui 启动训练。
2. 训练运行 20 分钟。
3. Sui 出现异常。
4. Candleholder 确认训练仍然正常。
5. 司岁台回收旧 Sui。
6. 创建新 Sui。
7. 新 Sui 查询原有 Job。
8. 继续监控和分析原来的训练。

不应该重新启动第二份训练。

## 3.3 将任务状态分成三类

**Desired State：期望状态**

由 Yan 和司岁台持久任务系统维护。

例如：

“完成 Math 和 Medical 两组实验，并生成对比分析。”

**Declared State：声明状态**

由 Sui 主动报告。

例如：

“Math 训练完成，正在进行评测。”

**Observed State：观测状态**

由 Candleholder、Runner 和独立监控组件采集。

例如：

- 训练进程仍然存在。
- 最近一次 checkpoint 为 step 1200。
- 评测程序已经启动。
- 评测尚未生成结果文件。

这三种状态必须分开存储。

不能把 Sui 的自述直接当成真实实验结果。

## 3.4 长期稳定性不能依赖提示词自律

凡是能够由 Wrapper、Runner、Hook 或系统程序自动采集的信息，都不应强制要求 Sui 通过自然语言上报。

例如：

- PID。
- 进程退出码。
- Worker 启动时间。
- Job 启动时间。
- 训练进度。
- 日志更新时间。
- 结构化指标。
- Checkpoint 是否存在。

Sui 主要负责报告需要 AI 理解的内容：

- 当前研究思路。
- 重要实验发现。
- 失败原因分析。
- 下一步方案。
- 需要人工决策的问题。

---

# 4. Sui Protocol：Worker 协议

这是第一版必须认真设计的接口。

Sui 不能只是一个被启动后随意操作环境的 Agent。

应当定义明确的身份、任务领取、状态报告、作业管理和交接协议。

## 4.1 Worker 注册

每个受管 Sui 启动时，应具有：

- `worker_id`
- `session_id`
- `generation`
- `task_id`
- `workspace`
- `lease`
- `capabilities`
- `created_at`

其中 `generation` 和 `lease` 用于处理旧 Worker 重新恢复的问题。

例如 Sui-018 失效后，新 Sui-019 获得任务所有权，旧 Worker 不应再被允许修改任务或启动新的受管实验。

## 4.2 基础协议操作

建议设计：

```text
sui.register
sui.claim_task
sui.get_task_context
sui.report_progress
sui.report_blocker
sui.launch_job
sui.attach_job
sui.publish_artifact
sui.handoff
sui.complete_task
```

这些应是实际可调用的接口。

不能只写一份 `SUI_PROTOCOL.md`，然后假设模型会永远严格遵守。

对于不能自动完成的语义报告，可由 Sui 主动调用工具。

## 4.3 进展报告

报告至少包含：

- Task ID。
- Worker ID。
- Generation。
- 当前工作阶段。
- 已完成内容。
- 关联 Job。
- 当前阻塞。
- 下一步计划。
- 更新时间。

结构化消息应有版本号。

例如：

```json
{
  "protocol_version": "0.1",
  "task_id": "task-042",
  "worker_id": "sui-018",
  "generation": 3,
  "event": "PROGRESS_REPORT",
  "phase": "EVALUATING",
  "summary": "Training finished; evaluating Math.",
  "job_ids": ["job-102"],
  "next_action": "Compare acceptance metrics",
  "blockers": []
}
```

该报告是 Sui 的声明，而非系统已经核验的事实。

## 4.4 Handoff

当 Worker 正常轮换时，应尽量生成结构化 Handoff。

包含：

- Task 目标。
- 已完成工作。
- 当前工作阶段。
- 重要文件修改。
- 关联实验和 Job ID。
- 尚未完成的操作。
- 重要设计决策。
- 已发现的风险。
- 推荐的下一步操作。

但必须考虑：

**如果旧 Sui 已经卡死，无法生成 Handoff，系统仍然要能够恢复。**

因此 Handoff 不是唯一可靠状态源。

新 Worker 应同时读取：

- 持久 Task 状态。
- 历史 Event。
- 最近可信 Handoff。
- 当前 Job 状态。
- Git 状态。
- 已登记的 Artifact。

旧 Session Export 可以作为补充，而非强制完整灌入新 Session。

## 4.5 Worker Lease

必须防止旧 Worker 与新 Worker 并发修改同一任务。

建议实现：

- Task Lease。
- Lease Expiration。
- Generation / Fencing Token。
- 所有权校验。
- 过期请求拒绝。

对于启动、取消等真实副作用操作，应在 Runner 执行端同样校验授权，不能只依赖上层 API 的检查。

如果 Sui 仍拥有直接执行任意 Shell 的权限，系统无法完全保证它不会绕过协议。因此至少应将高风险实验操作统一收敛至受管接口，并明确记录尚未受控的操作边界。

---

# 5. Candleholder：健康监控与故障恢复

## 5.1 监控原则

不应只检查：

“Session 是否还活着？”

而应区分：

- 进程是否存活。
- Agent 是否仍然可以正常调用工具。
- Agent 是否有新的有效活动。
- Task 是否取得进展。
- 关联实验是否正常执行。
- Worker 是否仍持有合法任务所有权。

上述条件不等价。

## 5.2 故障类型

至少考虑：

| 类型 | 示例 |
|---|---|
| Process Failure | Worker 进程退出 |
| Transport Failure | SSE 中断、连接重置 |
| Tool Failure | 工具调用异常 |
| Stalled | 长时间没有有效活动 |
| Repetition Loop | 重复输出或重复失败操作 |
| Malformed Tool Call | 连续产生无效工具调用 |
| Degraded | 持续出现低质量或异常行为 |
| Waiting | 正常等待用户或资源 |
| Resource Blocked | GPU/NPU 不可用 |
| Orphaned Worker | 已失去受管任务归属 |
| Controller Recovery | 控制器重启后的状态协调 |

## 5.3 检测策略

优先使用确定性检测：

- 进程存活状态。
- API/CLI 错误类型。
- 工具事件成功率。
- 重复工具调用特征。
- 空参数或非法参数。
- 响应流心跳。
- 重复日志模式。
- Task 进度更新时间。
- Runner 的真实进度。
- 协议报告与客观状态是否矛盾。

对于“模型实际上变蠢了”这种复杂问题，不能假设简单规则能够准确识别。

可以设计可选的短生命周期 AI 诊断任务，但不能把 AI 诊断作为 Watchdog 的必需依赖。

## 5.4 恢复机制

根据故障类型和风险选择：

1. 观察并记录。
2. 执行安全健康探测。
3. 尝试继续当前 Session。
4. 重启 Worker 进程。
5. 创建全新 Session。
6. 读取持久状态并接管 Task。
7. 多次失败后隔离并通知。

不能机械地对所有异常依次执行全部步骤。

尤其不能在工具调用可能产生副作用时，无限发送 Continue。

## 5.5 主动 Session 轮换

支持：

- 最大 Session 年龄。
- 最大上下文或事件数量（可观测时）。
- 最大异常次数。
- 安全轮换窗口。
- Handoff。
- 冷却时间。
- 重启次数限制。

可以把 30 分钟作为首轮测试参数，但不应认定所有 Session 都在 30 分钟后必然退化。

建议优先在任务检查点或已确认无危险操作的时刻轮换。

## 5.6 发现与回收

Candleholder 应：

- 发现司岁台管理的 Session。
- 检测意外退出。
- 发现与任务账本不一致的 Worker。
- 发现已过期但仍运行的 Worker。
- 在 Controller 重启后重新建立管理关系。

对未知进程，默认只能观察和报告。

不能因为它看起来像 Claude Code，就直接杀死。

---

# 6. Experiment Controller：实验执行管理

## 6.1 标准实体

建议明确区分：

**Task**

一个持续的工作目标，例如：

“完成 Math 与 Medical 的 Draft Retargeting 对照实验并分析差异。”

**Experiment**

一个实验定义，包括模型、数据、配置和评估方案。

**Job**

某个 Experiment 的一次实际执行实例。

**Trial**

面向用户展示的单次实验执行名称；在 MVP 中可以与一次 Job 对应，但内部仍使用 Job ID 管理具体进程。

**Artifact**

实验产生的文件或结果，例如：

- Checkpoint。
- Metrics JSON。
- CSV。
- 图表。
- 报告。
- 配置快照。

一个 Task 可以包含多个 Experiment。

一个 Experiment 也可能产生多个 Job，例如失败重试或不同环境运行。

## 6.2 作业状态

至少包括：

```text
CREATED
QUEUED
STARTING
RUNNING
COMPLETED
FAILED
CANCELLED
BLOCKED
UNKNOWN
```

如实际 Runner 支持，可以进一步扩展 PAUSED。

特别注意：

**UNKNOWN 不等于 FAILED。**

例如：

启动 Job 的远程命令已发出，但连接中断，不能立即重新执行。

必须先查询实际进程、Job ID 和输出状态。

## 6.3 作业执行与 Agent 分离

训练进程必须有独立生命周期。

Worker Session 被销毁时，不能自动杀掉正在正常运行的实验。

应通过独立 Runner、受管进程组、作业管理器或其他可靠的执行单元管理。

需要能够：

- 启动作业。
- 查询作业。
- 读取日志。
- 获取退出码。
- 获取指标。
- 检查 Checkpoint。
- 查询运行资源。
- 终止指定受管作业。

## 6.4 实验进度采集

优先支持机器可读的数据：

- JSON/JSONL Metrics。
- TensorBoard Events。
- 训练日志 Parser。
- 退出状态。
- Checkpoint 元数据。
- GPU/NPU 使用情况。

必须允许添加特定实验框架的 Adapter。

不需要在第一版重建完整实验追踪平台。

## 6.5 共享服务器

允许为任务配置：

- 可用机器。
- 资源需求。
- 机器选择优先级。
- 可否迁移。
- 资源不足时的行为。
- 最大重试次数。
- 是否允许中断恢复。

资源不足不等于故障。

默认不得抢占其他用户资源。

用户或管理员明确停止的实验不得被自动重新启动，即使另一台机器还有空闲 GPU。

---

# 7. Yan：远程控制与 AI 交互

这是项目的一级核心功能。

我希望在外部使用 AI 时，可以直接要求 AI 查询远程实验情况，而不必手动登录服务器、阅读大量日志后再复制给 AI。

同时，外部 AI 应能在授权范围内向司岁台下达任务。

## 7.1 核心交互模式

Yan 应同时提供：

1. 机器可调用的结构化 API。
2. 简单的 CLI。
3. 面向外部 AI 的 MCP Adapter。
4. 可选的 HTTP/远程传输接口。

不要求第一版实现复杂 Web UI。

不要求司岁台自己提供一个长期运行的对话模型。

## 7.2 查询接口

建议：

```text
yan.get_overview
yan.list_tasks
yan.get_task
yan.list_experiments
yan.get_experiment
yan.get_metrics
yan.get_logs
yan.list_artifacts
yan.get_worker_health
yan.get_command_status
```

所有查询应尽量返回结构化 JSON。

重要字段应包含：

- ID。
- 状态。
- 状态来源。
- 最后一次观测时间。
- 数据是否过期。
- 相关任务与 Job。
- 最近错误。
- 必要的 Artifact 引用。

不能把几小时前的旧状态误报成实时状态。

## 7.3 控制接口

建议：

```text
yan.submit_task
yan.instruct_task
yan.request_analysis
yan.create_experiment
yan.hold_task
yan.resume_task
yan.cancel_task
yan.request_intervention
```

写操作不能简单地直接执行后只返回一句“成功”。

需要区分：

- 请求已接收。
- 请求正在排队。
- 请求正在执行。
- 请求执行完成。
- 请求被拒绝。
- 请求失败。
- 结果暂时未知。

每条命令应有独立 Command ID。

## 7.4 Edict

Edict 是外部指令的世界观名称。

底层仍应使用标准 Command 模型。

一个 Edict 可以表示：

- 创建任务。
- 修改任务。
- 请求新实验。
- 要求分析数据。
- Hold。
- Cancel。
- Resume。

要求具备：

- Command ID。
- 发起者。
- 目标 Task。
- 请求参数。
- 幂等键。
- 创建时间。
- 授权信息。
- 状态。
- 执行结果。
- 失败原因。

## 7.5 自然语言交互场景

### 场景 A：查询进展

我在外部对 AI 说：

“看看今天 Math 和 Medical 实验的进展。”

AI 通过 Yan 查询。

得到：

- 各 Experiment 状态。
- 当前 Job。
- 已完成的训练步数。
- 最新 Metrics。
- 错误信息。
- 是否需要人工处理。

然后 AI 可以基于真实数据进行分析。

### 场景 B：接续新实验

我说：

“Math 跑完后继续做 Projection-only 的对照实验。”

AI 通过 Yan 提交具有前置依赖的 Task 或 Experiment。

司岁台负责持久保存依赖和执行状态。

即使中途所有 Sui Session 都被重建，这个要求也不能丢失。

### 场景 C：修改当前任务

我说：

“Medical 这一组先别继续扩大训练，检查一下已有结果。”

Yan 应将任务变更持久化。

后续新 Sui 必须读取当前任务要求，而不是只参考旧 Handoff。

### 场景 D：监控故障

我说：

“GLM 怎么又不工作了？训练还在跑吗？”

Yan 应区分：

- Sui 是否健康。
- 最近发生了什么故障。
- Candleholder 是否正在恢复。
- 真实训练进程是否仍运行。
- 是否有重复 Job 风险。
- 是否需要人工干预。

## 7.6 异步交互

Yan 不应该要求外部 AI 在一次长连接中等待数小时。

采用异步指令模式：

```text
Submit Command
      ↓
Return Command ID
      ↓
Queue / Execute
      ↓
Persist Result
      ↓
Query Status / Notification
```

外部 AI 可以在下次对话中查询既有命令的结果。

如果实际部署平台支持回调或通知，可以增加可选通知功能。

第一版优先保证查询和持久化，不必自建复杂消息中间件。

## 7.7 远程通信与安全

应支持将本地控制 API 和远程传输方式解耦。

不同环境可以采用：

- 本地 CLI。
- 授权 SSH。
- 企业批准的网络服务。
- 内部 MCP Server。
- 经过授权的异步命令中继。

**不能默认公司内部环境允许通过公网或 GitHub 传输实验数据与控制指令。**

任何外部 AI 接入必须经过相应网络、数据和安全授权。

在未确认之前，先用本地 Mock Gateway 和测试数据实现完整功能。

默认不暴露公网端口，不上传内部代码、权重、凭据、原始日志或其他敏感资料。

---

# 8. 持久化数据模型

建议至少设计：

```text
Task
Command
WorkerSession
WorkerLease
Experiment
Job
Artifact
Metric
Event
Approval
```

任务关系：

```text
Task
 ├── Commands
 ├── WorkerSessions
 ├── Experiments
 │    └── Jobs
 │         ├── Metrics
 │         └── Artifacts
 └── Events
```

需要明确：

- 实体唯一 ID。
- 状态机。
- 创建和更新时间。
- 当前所有者。
- 历史所有者。
- 幂等键。
- Generation。
- 失败次数。
- 重试策略。
- 父子关系。
- 对应物理资源。
- 审计事件。

## 8.1 Dossier

Dossier 是一个 Task 的完整历史视图。

它可以汇总：

- Task 目标。
- 变更过的指令。
- Sui 工作历史。
- Handoff。
- 关联 Experiment 和 Job。
- 重要事件。
- 实验结果。
- 尚未解决的问题。

但它不应是一份需要不断追加所有聊天内容的超长 Markdown。

建议 Dossier 由结构化数据生成，可以按需导出 Markdown 或 JSON。

## 8.2 存储方案

第一版优先考虑 SQLite。

要求：

- 事务。
- WAL。
- Schema Migration。
- 状态更新的原子性。
- 控制器异常退出后的恢复。
- 事件审计。
- 备份与数据恢复。

不要因为未来可能多机部署就一开始引入复杂分布式数据库。

---

# 9. 关键可靠性机制

## 9.1 幂等操作

必须避免重复执行。

尤其是：

- 创建训练。
- 启动评测。
- 取消作业。
- 重启任务。
- 修改关键配置。

对不能天然保证幂等的远程操作，应先持久记录请求，再执行，并在异常时检查外部实际状态。

## 9.2 Reconciliation

司岁台应不断比较：

- Desired State。
- Declared State。
- Observed State。

并在出现不一致时采取合理操作。

例如：

Task 账本显示 Job 应在运行，但对应 Runner 报告进程已退出。

此时应先判断：

- 是否已经正常结束。
- 是否生成结果。
- 是否可以恢复。
- 是否允许重试。
- 是否已经达到重试上限。

不能立刻创建新的 Job。

## 9.3 任务所有权

Worker 只能操作其当前持有的 Task。

过期 Lease 的操作必须被拒绝。

## 9.4 故障隔离

需要限制：

- 自动恢复次数。
- 连续失败次数。
- 恢复间隔。
- 单任务资源使用。
- 同时运行的 Worker 数量。

防止 API 故障时产生大量无意义的 Session 或训练进程。

## 9.5 人工干预优先级

当 Yan 明确下达暂停或取消指令后，自动恢复不能覆盖该决定。

任务状态必须跨 Session 持久保存。

## 9.6 监控系统自身的恢复

Candleholder 不需要再由另一个长期 AI Session 监控。

由 OS 或容器运行时提供进程级守护。

Python Controller 重启后应进行 Reconciliation，恢复管理状态。

---

# 10. 现有开源项目的复用评估

正式开发前，应评估以下项目：

| 项目 | 重点研究 |
|---|---|
| Gas City | Agent 生命周期、Reconciliation、健康巡检 |
| AgentPulse | 多 Session 监控、远程操控、MCP |
| Ralph | Fresh Context Loop、任务接续 |
| Codex Task Watchdog | 卡死与异常活动检测 |
| OmniSession | Session Export、Fork、Handoff |
| Agent CLI Farm | tmux Session 管理 |
| cc-resume-watchdog | Claude Code 响应中断后的恢复 |
| Temporal | Durable Workflow、Query/Signal/Update |
| SkyPilot / Ray Jobs | 实验 Job 的生命周期管理 |

要求输出明确的复用决策：

1. 哪些功能已被现有项目解决？
2. 哪些实现可以直接引用或依赖？
3. 哪些可以移植部分逻辑？
4. 哪些与内部 GLM 存在兼容性问题？
5. 是否应在 Gas City 等系统上二次开发？
6. 采用独立 Python Controller 的优势和维护成本是什么？

请通过源码、测试和文档判断，而不是只读 README。

同时不要因为某个成熟项目使用非 Python 语言就直接排除。

但也不要引入远超 MVP 所需的大型依赖。

---

# 11. MVP 功能边界

第一版必须完成：

### P0：必须实现

- 独立 Python Controller。
- SQLite 持久化。
- Task / Command / WorkerSession / Job 数据模型。
- 一个真实可用或可测试的 WorkerAdapter。
- Sui Protocol 最小实现。
- 独立 Experiment Runner。
- Candleholder 基础进程与协议健康检测。
- Worker 死亡后的任务接管。
- 安全的 Session 轮换。
- Yan CLI。
- 机器可调用的 Yan 查询与指令接口。
- 关键操作审计。
- 端到端故障注入测试。

### P1：优先扩展

- 内部 GLM 的完整适配。
- MCP Server。
- 重复输出与异常工具调用检测。
- Session 自动 Handoff。
- 多机器实验状态查询。
- 任务依赖调度。
- 经授权的远程交互。
- Artifact 与实验指标解析。
- 故障诊断报告。

### P2：后续考虑

- Web Dashboard。
- 自动科研实验规划。
- 高级语义故障检测。
- 多 Agent 协同工作。
- 多种 AI Runtime 的统一控制。
- 分布式 Controller。
- 复杂资源调度。
- 大规模实验指标分析平台。

**第一版不需要自己实现 W&B、Slurm 或完整 AgentPulse。**

---

# 12. 建议实施阶段

## Phase 0：技术调查与环境核验

确认：

- 内部 GLM 的实际调用入口。
- Claude Code CLI 是否可以非交互使用。
- 是否支持结构化输出。
- 是否能够获取 Session ID。
- 是否可以创建全新 Session。
- 是否可以恢复或导出旧 Session。
- 是否能够可靠终止受管 Worker。
- 现有 VS Code Extension Session 是否能从外部控制。
- 持久容器的进程管理能力。
- 实验任务实际如何启动。
- 训练指标与日志的格式。
- 允许使用的远程访问与数据传输方式。

对无法在当前环境验证的内容明确标记为 Unknown。

不要假设内部 GLM 与官方 Claude API 或 Claude Code 完全兼容。

## Phase 1：可靠任务核心

实现：

- Task 和 Command 状态机。
- 持久数据库。
- 本地 CLI。
- 模拟 Experiment Runner。
- 事件日志。
- 幂等命令处理。
- Controller 重启后的状态恢复。

这一阶段不需要真实 LLM。

## Phase 2：Sui Worker 集成

实现：

- WorkerAdapter。
- WorkerSession 生命周期。
- Task Lease。
- Sui Protocol。
- Worker 状态报告。
- Handoff。
- 任务接管。

先接入最容易稳定控制的 CLI Runtime。

## Phase 3：Candleholder

实现：

- 进程监控。
- 协议心跳。
- 基础异常检测。
- Worker 恢复。
- 主动 Session 轮换。
- 过期 Worker 回收。
- 重启限制与隔离。

## Phase 4：Yan Gateway

实现：

- 状态查询。
- 指令提交。
- Command 回执。
- CLI 与 API。
- MCP Adapter。
- 授权访问控制。

## Phase 5：真实实验环境

验证：

- 内部 GLM。
- 长时间训练。
- GPU/NPU 作业。
- Worker 故障。
- 多次 Session 重建。
- 远程任务指令。
- 训练结果与日志查询。

---

# 13. 验收测试

第一版至少通过以下测试。

| ID | 故障或场景 | 预期结果 |
|---|---|---|
| T01 | Yan 提交任务 | Task 持久化，Worker 可领取 |
| T02 | Sui 正常报告进展 | 状态更新并保留事件历史 |
| T03 | Sui 进程突然死亡 | Task 保留，新 Sui 接管 |
| T04 | Sui 正在管理长训练时死亡 | 训练不被误杀 |
| T05 | 主动 Session 轮换 | 新 Worker 接续任务 |
| T06 | 连续异常工具调用 | 检测并进入正确恢复状态 |
| T07 | Controller 自身崩溃 | 重启后恢复任务关系 |
| T08 | 重复提交相同命令 | 不创建重复训练 |
| T09 | 旧 Worker 恢复提交写操作 | 过期操作被拒绝 |
| T10 | 实验资源暂时不可用 | 等待、阻塞或按策略迁移 |
| T11 | Yan 下达取消命令 | 后续 Worker 不擅自恢复 |
| T12 | Sui 谎报实验完成 | 客观状态仍可正确显示 |
| T13 | 启动作业后网络断开 | 核验实际状态，不盲目重试 |
| T14 | 外部 AI 查询实验 | 返回可机器解析的真实状态 |
| T15 | 外部 AI 追加指令 | 指令持久化，之后可追踪结果 |
| T16 | 所有 Sui 暂时不可用 | Controller、Job 管理和查询仍能工作 |

测试应尽量使用 Mock Worker 与 Mock Runner，使大部分测试不需要真实 GPU/NPU。

需要区分：

- 单元测试通过。
- 模拟故障测试通过。
- 真实环境集成测试通过。

不能用 Mock 通过来证明真实 GLM 一定可以正常恢复。

---

# 14. 安全要求

由于系统允许 AI 操控计算环境，必须认真考虑权限问题。

基本要求：

- 默认最小权限。
- 不默认使用 root。
- 不无条件修改宿主机。
- 不允许远程 AI 获得无限制 Shell 权限。
- 破坏性命令应有审批机制。
- 不得误杀其他用户进程。
- 必须记录命令发起者。
- 必须限制重复操作。
- 任务取消必须持久有效。
- 实验日志与仓库文件中的文本不得被视为高优先级控制指令。
- 远程通信必须具有鉴权。
- 不得默认向第三方服务上传公司内部数据。

开发和测试时优先使用隔离 Workspace、模拟数据和专用测试进程。

---

# 15. 工程交付要求

项目采用 Git 管理。

至少应提供：

```text
README.md
pyproject.toml

docs/
  REQUIREMENTS.md
  ARCHITECTURE.md
  SUI_PROTOCOL.md
  DATA_MODEL.md
  YAN_API.md
  RECOVERY_POLICY.md
  SECURITY.md
  TEST_PLAN.md
  IMPLEMENTATION_PLAN.md
  ADR/

src/
  sui_regulator/

tests/
```

更具体的源码结构可根据实际设计调整。

要求：

1. 代码模块职责清晰。
2. 重要状态使用类型定义。
3. 对外接口具有 Schema。
4. 提供可运行的本地示例。
5. 提供模拟 Worker 和模拟 Runner。
6. 关键操作有测试。
7. 配置与代码分离。
8. 文档与实际实现同步。
9. 每阶段产生明确 Git Commit。
10. 未验证能力必须明确标注。

---

# 16. Astra 的工作方式

我希望你主动完成工程调查、设计、实现和测试。

不要对每一个普通技术细节都反复询问我。

对于可以通过源码检查、文档查询、局部实验或 Mock 验证的问题，优先自主调查和测试。

但以下情况必须明确说明并等待必要授权：

- 涉及公司网络或数据出境。
- 涉及宿主机配置修改。
- 涉及其他用户的资源。
- 涉及真实训练进程的终止。
- 涉及生产环境的不可逆操作。
- 需要额外权限或敏感凭据。

开发时要考虑你自身也可能发生 Session 中断。

请通过仓库文档、Git Commit 和结构化进度记录保留工作状态，不要仅依赖当前对话上下文。

每个阶段完成后，报告：

- 做了什么。
- 对应代码位置。
- 运行了哪些测试。
- 哪些结果已经验证。
- 哪些只是设计或假设。
- 当前主要风险。
- 下一步工作。

---

# 17. 第一轮具体执行任务

现在请开始执行，而不是只讨论这个项目应该如何实现。

第一轮按以下顺序进行：

### Step 1：竞品与复用判断

重点检查 Gas City、AgentPulse、Ralph 和相关 Session 工具。

输出：

- 功能重叠矩阵。
- 可复用的具体模块。
- 与本项目需求的差距。
- 自研与二次开发的成本比较。
- 推荐方案。

### Step 2：环境适配调查

检查当前环境中实际可用的 Agent CLI、容器、进程管理和实验执行方式。

不要提前假设某种接口一定可用。

### Step 3：确定最小架构

明确：

- Controller 结构。
- Task/Command/Job 数据模型。
- WorkerAdapter 接口。
- Sui Protocol。
- Candleholder 检测机制。
- Yan API。
- 任务恢复流程。

输出一份可直接指导开发的技术方案，而不是宽泛的概念图。

### Step 4：创建项目骨架

初始化 Python 项目，建立数据库模型、核心接口、配置和基础测试。

### Step 5：实现最小端到端原型

使用 Mock Worker + Mock Experiment Runner 完成：

1. Yan 提交任务。
2. Sui 领取任务。
3. Sui 启动作业。
4. Job 独立执行。
5. Sui 被强制终止。
6. Candleholder 检测故障。
7. 新 Sui 接管任务。
8. Job 不被重复启动。
9. Yan 查询任务状态和结果。

这是第一轮最重要的可执行成果。

### Step 6：交付报告

提交：

- 架构决策。
- 可运行原型。
- 测试输出。
- 尚未解决的问题。
- 后续 Phase 计划。

如果环境暂时不具备真实 GLM 适配条件，允许先使用 Mock，但必须保留明确的 Adapter 接口和未验证事项。

---

# 18. 最终设计目标

我希望 Sui Regulator 最终能够实现以下工作方式：

我在外部通过 Yan 询问实验进度。

司岁台不需要唤醒一个长期监控 AI，就能够返回真实实验状态。

如果需要复杂分析，司岁台可以派出新的 Sui 执行。

如果 Sui 出现故障，Candleholder 能够回收并重建它。

如果实验仍在正常运行，实验不受 Sui 故障影响。

如果我要求接续新的实验，指令会被持久保存，并在条件满足后得到执行。

如果实验环境发生异常，系统能够检查、报告、等待资源或按授权策略恢复。

整个过程中，**没有任何一个 AI Session 是系统可靠性不可替代的单点依赖。**

最终原则：

**Task 持久，Sui 可替换；Candleholder 独立监控，Yan 随时发令。**

请以此为核心约束，完成 Sui Regulator 的设计与第一阶段实现。
