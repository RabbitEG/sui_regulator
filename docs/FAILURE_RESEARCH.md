# v0.3 故障检测调查

本轮只研究 Session 故障观察与恢复，不扩大科研执行能力。以下是固定版本源码、测试和原始研究的判断。

| 来源 | 实际检查 | 复用决定 |
|---|---|---|
| [Antiloop 4c163bf](https://github.com/noguerol/antiloop/tree/4c163bf5c350962a98f5bc99111f627a5b2b32b0) | `src/detect.ts` 的词频/连续重复、无空格周期、n-gram 块重复、结果指纹、task stream/snapshot 豁免、`runSelfTest`；MIT | 移植有界纯算法，保留版权；不引入 Pi Runtime，不照搬其自动提示注入。上游在完整 message_end 检测，本项目另做增量窗口。没有 Node，未执行上游 TypeScript self-test |
| [AgentRx 7a18c79](https://github.com/microsoft/AgentRx/tree/7a18c79708e7671be15124460f4f7296107c2a55) | `agentrx/ir/trajectory_ir.py`、`judge/judge.py`、`tests/test_judge_encoding.py`、依赖声明 | 借鉴统一轨迹、失败步骤/类别与证据分离；不引入需要模型的约束生成/judge。未运行其模型流水线或宣称复现论文指标 |
| [Codex Task Watchdog d14ce83](https://github.com/TanChuping/codex-task-watchdog/tree/d14ce8325ccd9751045dd18a5a542da7fbd22a47) | request/stream/tool/terminal 状态转换与测试 | 借鉴阶段超时、并行工具与不可观测状态的保守处理；本轮重跑 38 项测试通过 |
| [Claude Issue #82803](https://github.com/anthropics/claude-code/issues/82803) | 原报告及后续更正、尾部重复扫描建议 | 只改编故障机制，使用新造文本、合成 ID；不搬运真实 Session 或组织信息。报告的周期性不作为固定 38 分钟故障规则 |

[AgentRx v2](https://arxiv.org/html/2602.02475v2)使用九类根因标签、轨迹与关键失败步骤，并明确包含 LLM 判读环节；论文版本的样本数量有变化，不能把其指标直接算作本项目检测能力。[故障分类研究 v2](https://arxiv.org/abs/2603.06847)从 40 个仓库的 13,602 条 issue/PR 中抽取 385 个故障，分别建立症状、故障类型与根因分类。这支持本项目将可观测证据与推测原因分开，不能证明简单检测器能自动归因。

## CLI 可观测性实测

已安装版本为 Claude Code 2.1.316；help 确认 partial 参数。[官方 headless 文档](https://code.claude.com/docs/en/headless)说明 `stream-json + verbose + include-partial-messages` 提供增量事件。本轮新增 `tests/test_stream_capability.py`，用真实 CLI 连接 loopback 合成 Messages/SSE 服务，在服务响应尚未结束时断言已收到 `stream_event / text_delta`，测试通过。

这是该 CLI + 假服务的接口验证，不证明内部 GLM 会发送同样粒度的数据。未出现 delta 时必须显示为 event-only，不能把心跳当作 token 流。thinking 只能检测实际公开的块；语义上的上下文退化、科研判断质量及隐式 compaction 错误不可确定性判定。

## 初始实现范围

首批规则覆盖单次词/字符/段落重复、跨轮重复、格式异常、结果感知失败循环、传输重试、断流、流静默、进程死亡、结构化 Session 冲突、无进展/完成冲突、环境错误和未知异常。每条规则必须有明确正例与正常反例。

算法以有界窗口处理，证据默认只保存计数/摘要/hash，不收集真实原始聊天。相似度与 confidence 是启发式分数，不是经过校准的故障概率。正常轮询、批量成功调用、实验等待和工具输出中的错误关键词必须有反例保护。
