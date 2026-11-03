# 提示词、Session 边界与任务完成协议

本设计参考成熟的持久执行和长期 Agent 实践，但继续使用现有 SQLite、Controller、Yan RPC 与 Claude Code 适配，不引入大型编排框架。

## 参考与取舍

| 参考 | 采用的思想 | 司岁台的具体实现 |
|---|---|---|
| [Temporal：Tasks](https://docs.temporal.io/tasks) | 区分运行基础设施故障与业务逻辑结果；持久历史随工作保留 | 进程、连接、协议与身份故障触发恢复；实验成败交给模型判断。Task 与 Session 生命周期独立 |
| [Anthropic：Effective harnesses for long-running agents](https://www.anthropic.com/research/effective-harnesses-for-long-running-agents) | 首次建立任务与验收上下文，后续会话读取进度、检查清单和历史证据再继续 | `initialize` / `continue` 两种注入结构，保留用户原文、追加指令、Handoff、命令回执和结果路径 |
| [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | 区分会话范围状态与跨会话持久信息 | Session 的心跳和进程信息单独记录；Task 要求、指令、操作回执与完成标记跨代保留 |
| [Kubernetes：Probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/) | 存活、就绪和启动观测具有不同用途 | 借鉴其职责分离，不把实验质量检查接到 Session 存活判定上 |

上述最后一列是本项目的设计选择，并不意味着这些项目提供了同名能力，也不声称 Sui 实现了 Temporal 的确定性重放或 LangGraph 的模型状态检查点。Claude CLI 的上下文不能原样迁移；换代依赖持久材料重建任务理解。

## 谁负责判断什么

- **模型**：理解任务、判断实验结果、选择调试方法、决定是否重试、继续实验和选择授权资源。
- **Candleholder**：观察进程 / 心跳 / 连接 / 协议身份，维护所有权，保存回执，处理真实运行故障。
- **用户**：定义目标、约束、验收条件、执行权限及资源预算，必要时 Hold / Resume。

测试失败、实验失败、指标不达标、重复失败和缺少新的可核验证据都不是 Session 故障。TOOL-002 / ENV-001 的检测证据可展示，但最终动作是 `advise`，不隔离或停止代理。相同失败的多次尝试会留下提示，模型仍可决定再次执行；这覆盖对偶发错误或不确定结果的合理复测。

副作用的未知状态属于另一个问题：未决 Job / 命令不能被盲目重放。该保护保留，但不是对实验结论的评价。

## 提示词注入结构

源码：[task_prompt.py](../src/sui_regulator/task_prompt.py)。版本：`sui-proxy.prompt.v3`。固定监督协议通过 `--append-system-prompt` 追加，完整任务输入使用有缩进的 JSON，Session 阅读器分别记录两者。

| 区块 | 输入来源与用途 |
|---|---|
| `operator_request` | 用户原始标题、完整任务书、验收条件，保留多行内容 |
| `operator_updates` | 已应用的 Instruct / Request Analysis Command，带指令 ID；不会把日志文字当作用户指令 |
| `execution_contract` | Task / Generation、目录、执行模式、批准的模板、预算、可延后的交接要求 |
| `completion_contract` | 本代完成标记路径、字段模板、要求版本、可选核验模板 |
| `observed_state` | Job 观测、命令执行回执、完成标记处理结果 |
| `agent_notes_unverified` | 代理自述和历史 Handoff，明确是未核验笔记 |
| `supervisor_observations` | 退避、资源额度与运行证据观测；不是对实验方法的命令 |

初代代理建立工作清单与记录；接手代理先核验已有实验身份和工作状态，再继续未完成部分。用户在任务书里定义真实工作流程，固定协议不会把它改成巡检，也不强制每个任务都采用某一科研流程。可用工具不足时，代理须说明缺口，而不是假装已执行。

## 合作式换代

`max_session_age` 到达后只请求交接。健康代理可以完成正在进行的慢速动作、继续调试，并在安全边界提交 Handoff；`rotation_grace` 不再用于年龄触发的强制终止。该参数仍用于已撤销 / 已失败代理的进程清理。

Controller 只有在收到**交接请求之后的新 Handoff**时才按正常换代结束代理；早先的进度笔记不会被误当作当前交接同意。进程死亡、失联、协议错误等客观运行故障仍走恢复。CLI 自然结束或用尽回合预算可以创建下一 Session，但不因此宣告 Task 失败。新配置默认每个 CLI Session 100 回合，已有配置保持原值。

15 / 60 分钟未观测到新的配置范围内运行证据只产生检查提示和 Yan 可见事件，不强制换代，不要求模型停止当前调试。长工具调用期间，已有流检测器区分“工具正在执行”与“等待上游模型响应”，不会仅因工具慢而认定断流。

## 完成标记

代理通过 `get_task_context.completion_contract` 获取模板和路径：

```text
<state>/completions/<task_id>/<proxy_id>.json
```

使用 `complete_task(summary, evidence)` 时，后端执行明确配置的核验模板，写入同一 JSON 标记并提交验收。Claude Code 模式也可以把填好的模板原子写入该路径；Controller 在下一轮读取并结束 Task。

模板字段包括 `schema_version`、`submission_id`、`task_id`、`proxy_id`、`generation`、`requirements_revision`、`status=completed`、`summary`、`evidence` 和 `verification_attempt_id`。复制当前模板中的身份字段，填入真实总结与证据；不要手工猜测 ID。如果配置了完成前核验模板，先通过 `run_command` 执行，并填写返回的成功 attempt ID。

Controller 检查：

1. 当前 Task 仍为 ACTIVE，标记属于当前 Generation。
2. 用户要求版本未变化；新追加的指令不能被旧完成标记忽略。
3. 所有托管 Job 已有已知终态，命令没有未决副作用。
4. 如果用户明确配置验收脚本，存在当前代、当前要求版本之后的成功执行回执。

**这些检查不取代模型对实验和任务的判断。** FAILED 的实验可以是合法的研究结果；只要模型完成用户要求并提交证据，终态失败的 Job 本身不会阻止 Task 结束。核验脚本失败也只是拒绝此次完成申请，不是 Session 故障。

不完整 JSON、普通文字“done”、AI 的 `phase=COMPLETED` 声明、旧代标记或过期要求不能结束任务。完整但被拒绝的标记有持久回执；代理修正后重新读取模板，生成新 submission_id 再提交。Hold / Cancel 优先，不能用文件覆盖用户意图。

Console 显示完成标记路径、处理回执与已接受的证据。语义结果明确标为 `proxy-declared`；通过某个验证脚本不等于自动证明全部科学结论。

## 验收范围

测试包括原生 Claude Code 的等待现有实验 → 运行失败 → Read / Edit → 复测 → 后续产物流程，并将换代年龄设得远小于慢动作耗时，确认同一 Session 可继续工作。另有原生后台 Bash 失败后的终态通知与同 Session 后续操作、原生 Write 完成标记自动结束 Task、旧要求 / 旧代拒绝、无效文件、失败实验终态、慢代理不强制换代等回归。

模型输出由 loopback 假 API 给定。这验证实际 CLI、Hook、文件、SQLite 和 Controller 效果，不验证真实 GLM 的理解或科研判断能力。

## 本轮接续协议（schema 7）

规范依据是 [职责基线](RESPONSIBILITY_BASELINE.md)，实现和验收见 [本轮报告](RESPONSIBILITY_ITERATION.md)。固定提示词只规定身份、来源、授权、接续与协议；任务内容和实验结论仍归代理。首代建立适合该任务的工作上下文，接续代先读旧工作和未知操作；没有强制训练或固定调试流程。

原始 `operator_request` 保留全文。历史是有界预览，`history_refs` 指向 `get_task_records`；追加指令有 `instruction_page` 稳定游标，必须读到 `has_more=false`。全账本 `outstanding_operations` 防止未决工作被近期预览隐藏。摘要不能覆盖用户原文，历史页面也不假装是实时变更订阅；新状态需重新查询对象或读取事件。

追加指令入账后，通过 Pre/PostToolUse 和 Stop 的 `additionalContext` 提醒当前 CLI；不会为此中断正在运行的慢工具。提醒最多包含约 6000 字符，长要求通过分页读取完整原文。CLI Hook 的响应准备和初始 stdin 写入只有 `offered_at`，不是已交付的证明；只有代理调用 `acknowledge_instructions(command_ids)` 才记为明确已读声明，理解与执行仍未知。Hook 注入通常在该次工具结束后才进入下一次模型请求，故 PreToolUse 不能替当前旧完成调用偷偷更新要求版本。[Claude Hooks](https://code.claude.com/docs/en/hooks)

完成工具和文件标记都要求最新要求版本、本代完整指令已提供且明确 ACK。并发新指令仍会使旧标记失效；已入队的新要求或 Hold / Cancel 必须先应用，完成不能抢先吞掉它们；文件式标记同样检查 lease 尚未过期。ACK 不证明任务完成，语义决定仍由代理承担。没有追加指令的旧任务保持原完成协议兼容。

`wait_task(reason, check_after_seconds, job_id?)` 是代理主动交出当前 Session 的持久等待：记录原因、可选受管 Job、下次检查、Candleholder 责任及发起者。定时器、登记 Job 的已知终态、新指令或 Resume 可唤醒同一 Task；Hold / Cancel 阻止自动恢复。外部资源目前只支持定时再检查，没有自动订阅或迁移。仍依赖当前 CLI 观察的原生后台工作不能用此接口交出运行时。

**启动长期服务后将 Task 标为完成且继续托管该服务，目前不支持。** 尚无可靠的运行责任转移契约，保留“受管 Job 必须有已知终态、命令副作用不能未决”的完成保护。可以保持 Task ACTIVE 或按可用等待契约接续，不能伪造一个 handoff 字段绕过。直到用户取消的持续任务应在任务书中明确写明；单轮成功不是整个任务完成。
