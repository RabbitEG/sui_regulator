你这个判断更准确。我们现在不需要证明 Sui 能完成多复杂的科研任务，而需要证明：无论 Session 以什么方式失效，Candleholder 都能尽可能及时地发现，并在安全的条件下将任务交给新的 Session。

而且你提出的「问题库」，我认为应该成为司岁台的一个核心模块，而不是在 `ClaudeStreamDetector` 里继续增加一堆 `if` 判断。

我建议正式引入：

Failure Registry（故障模式库）+ Detection Engine（检测引擎）+ Incident Corpus（故障案例库）。

三者各司其职：

- Failure Registry：定义已知的 Session 故障模式、检测条件和应对策略。
- Detection Engine：持续观察流式输出、工具调用、运行状态与任务协议，执行检测规则。
- Incident Corpus：保存真实或合成的故障轨迹，以及正常轨迹，用于回归测试和发现新问题。

这也不完全是从零开始。我查到了两个相当值得借鉴的现成成果。

## 一、最相关的开源项目：Antiloop

Antiloop

[GitHub 仓库](https://github.com/noguerol/antiloop?utm_source=chatgpt.com)

这个项目已经做了七类循环检测，包括：

- 跨消息文本重复。
- 工具调用序列重复。
- 推理内容重复。
- 单次输出内部的 token/词语退化重复。
- 单次输出内部的整段文字循环。
- 多次执行相似操作，却得到相同失败结果。
- 识别合法的批量工具操作，避免误报。

它的处理方式也不是发现重复就立刻杀进程，而是分级警告、尝试打断循环、必要时停止。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



这意味着司岁台没必要自己重新发明所有重复检测算法。 我建议 Astra 先研究 Antiloop 的实现，再决定哪些策略可以移植为不依赖 Pi Runtime 的通用检测规则。

还有一例很贴合你的经历：Claude Code 的公开 Issue [#82803](https://github.com/anthropics/claude-code/issues/82803?utm_source=chatgpt.com)，有人记录了单个 token 在一次响应中重复约 32,000 次，甚至以正常响应形式结束、没有抛错。

报告者后来通过检测输出末尾的重复模式找到了更多案例，还观察到故障有时呈现约 38 分钟的周期性。

[image](https://www.google.com/s2/favicons?domain=https://github.com\&sz=32)

GitHub



这恰好说明：Session 进程存活、API 正常返回，甚至协议层报告成功，都不能证明模型输出是健康的。

## 二、我建议故障库先分成七个类别

| 类别                 | 典型故障                        | 是否需要更换 Session |
| ------------------ | --------------------------- | -------------- |
| F1 · Transport     | 断流、503、连接重置、请求无限等待          | 视情况恢复          |
| F2 · Process       | 进程死亡、僵死、工具挂起、IPC 失效         | 通常需要           |
| F3 · Generation    | 单 token 循环、段落重复、无意义长输出      | 高度怀疑，通常应换新     |
| F4 · Tool Use      | 空 Bash、非法 JSON、重复错误调用、伪造工具帧 | 多次出现后换新        |
| F5 · Session State | 上下文退化、Compaction 异常、会话状态混乱  | 倾向全新 Session   |
| F6 · Progress      | 无效重试循环、长期无任务进展、虚报完成         | 核验后再决定         |
| F7 · Environment   | GPU 不可用、磁盘故障、数据库锁、权限错误      | 不应简单换 Session  |

其中最重要的一个区分是：故障现象和故障原因不能等同。

例如，连续三次 Bash 失败可能是模型失常，也可能是磁盘只读。前者换 Session 有意义，后者换十次也没用。

2026 年的 Agent 故障研究也支持这一点。一项针对 40 个开源 Agent 项目、13,602 条 Issue/PR 进行筛查的研究，专门将故障类型、可观测症状与根因分开建模。另一项 AgentRx 研究提供了九类失败分类及带标注的失败轨迹。

[image](https://www.google.com/s2/favicons?domain=https://arxiv.org\&sz=32)

arxiv.org+1



所以司岁台的故障库不应该只是一个「遇到什么字符串就重启」的 Error Dictionary，而应该是可运行的故障诊断规则集合。

## 三、故障库应该怎么设计？

我建议每个故障模式是一个独立、可版本化的规则，而不是硬编码在主监控循环里。

例如：

```

id: GEN-001
name: Degenerate Repetition
category: generation
version: 1

signals:
  - assistant_stream_delta
  - assistant_message

detector:
  type: repetition
  config:
    min_consecutive_units: 16
    min_repeated_share: 0.40

severity: high
confidence_required: 0.95

recovery:
  action: fresh_session
  preserve_task: true
  preserve_running_jobs: true
  max_attempts: 2

fixtures:
  positive: gen001_repeated_token.jsonl
  negative: gen001_legitimate_repetition.jsonl

```

建议的配置格式，非当前仓库已有实现。示例阈值需要用正反样本校准，并且重复文本单位不一定等于真实模型 token。

每条规则需要定义四件事：观察什么、如何判定、如何处理、如何证明判定正确。

尤其要允许多种恢复动作。

例如 `GEN-001` 是严重的单次生成退化，可以优先考虑创建全新 Session；`NET-003` 是临时 503，应该进行有限退避重试；`ENV-002` 是 GPU 不可用，则应该保持任务等待状态，而不是不断重启 AI。

## 四、故障案例库比单纯积累规则还重要

我建议单独建立 `tests/failure_corpus/`，保存可重放的 Session 事件轨迹。

故障库建议目录

设计方案

检测器、规则、案例与回归测试独立维护

```

failure_registry/
  patterns/
    GEN-001.yaml
    TOOL-001.yaml
    TRANSPORT-001.yaml

  detectors/
    repetition.py
    tool_loop.py
    stream_stall.py
    progress_stall.py

  corpus/
    positive/
      repeated_token.jsonl
      empty_bash_loop.jsonl
      partial_sse.jsonl
    negative/
      long_training.jsonl
      repeated_polling.jsonl
      batch_tool_calls.jsonl

  replay.py
  evaluate.py

```

这里的 `positive` 是已知故障轨迹，`negative` 是看起来有点像故障，但实际上正常的轨迹。

后者非常重要。

例如，一个 AI 每隔 10 秒执行一次 `get_experiment_status`，可能连续执行 500 次，而且工具名称、参数、返回结果都相同。

这显然不能被简单判定为 Tool Loop。

相反，模型连续 10 次尝试执行空 Bash，则可以成为明显的异常证据。

故障检测需要识别的是模式及其上下文，而不是单纯的相似度。

## 五、当前仓库最大的检测缺口

我之前读到的 `ClaudeStreamDetector` 已经实现了一些基础功能，包括错误分类、重复文本、空 Bash、工具调用异常等。

但目前还有一个很重要的技术问题：

它主要根据完整的 Claude Stream JSON 事件检测，而不是对模型正在生成的每一段内容进行持续检测。

这会导致一种情况：

```
模型开始输出
    ↓
重复 token × 10000
    ↓
重复 token × 20000
    ↓
模型到达 max_tokens
    ↓
完整 assistant 消息产生
    ↓
Candleholder 才检测到重复
```

最终虽然发现了故障，但无效生成已经发生。

因此我希望新检测引擎同时支持两个层次：

| 检测层次                         | 作用                               |
| ---------------------------- | -------------------------------- |
| Streaming Detection          | 尽可能在生成过程中发现 token/文本退化、长时间静默等问题  |
| Event / Trajectory Detection | 在完整消息和工具结果产生后，检测跨轮重复、失败循环、异常工具调用 |

Astra 需要先验证 Claude CLI 当前版本是否能提供足够细粒度的流式事件。若无法获取增量输出，就不能声称已实现实时 token 退化检测，只能依赖输出完成后的检查与超时保护。

而且我不建议用一个 LLM 持续读取每个 Session 的聊天记录来做故障分类。这样又回到你最初那个不可靠的 AI 监控 AI 问题。

故障库的检测器应优先是确定性的程序；LLM 诊断最多是可选的辅助功能。

## 六、没有目标环境，反而可以先把故障库做好

这个模块可以完全离线开发。

Astra 可以通过真实开源故障报告建立初始案例，然后使用合成轨迹补足边界条件。

我建议首轮关注以下四个指标：

| 指标           | 作用              | 初步验收目标        |
| ------------ | --------------- | ------------- |
| 故障召回率 Recall | 已知故障中检测到多少      | 高优先级案例尽量 100% |
| 误报率 FPR      | 正常运行被错误识别为故障的比例 | 先建立严格的正常样本集   |
| 检测延迟         | 故障开始到触发告警的时间    | 按故障类别分别设定     |
| 恢复正确率        | 检出故障后是否安全完成接管   | 所有关键故障注入测试通过  |

必须强调：在合成样本上达到 100% Recall，不代表在真实 GLM 环境也能达到这一水平。

而且「未知故障」永远无法靠一个固定问题库全部覆盖。因此还需要基础的开放式异常观察机制，例如心跳异常、长期无有效进展、协议状态冲突。无法确认原因时，应该记录 `UNKNOWN_ANOMALY`，必要时隔离，而不是强行归入某个已知故障。

## 七、我建议直接给 Astra 的下一阶段要求

## Sui Regulator 下一阶段：Failure Registry & Recovery Validation

下一阶段暂时不要扩展真实科研任务执行能力，也不需要证明 Sui 能完成复杂代码任务。

当前目标聚焦于：

**通过可扩展的故障模式库，识别各种 AI Session 异常，并验证 Candleholder 能够根据故障类型安全恢复 Task。**

请完成以下工作：

1. 调研现有 Agent 故障分类和检测实现，重点包括 Antiloop、AgentRx、Claude Code 真实 Issue，以及已有的 Codex Task Watchdog。
2. 建立版本化的 Failure Registry。每个故障模式包含 ID、类别、可观察信号、检测算法、严重程度、建议干预策略和对应测试案例。
3. 将现有 `ClaudeStreamDetector` 的检测逻辑逐步整理为可复用检测器，但避免过度插件化；统一输出结构化 Incident。
4. 建立 Incident Corpus，包含真实报告改编的脱敏案例、合成故障案例及正常行为反例。
5. 建立离线 Replay Harness，能重放 Session 事件，自动评估召回率、误报率、检测延迟和恢复策略。
6. 特别覆盖单次生成内部的重复 token/文本、跨轮重复、重复失败工具调用、空 Bash、非法工具调用、截断流、API 错误和无效进展循环。
7. 严格区分模型故障、Session/CLI 故障、外部工具故障和环境故障。不是所有异常都应该更换 Session。
8. 对不同故障设计分级响应：观察、告警、安全探测、有限重试、创建新 Session、隔离等待人工处理。
9. 验证发生故障后，新 Sui 继承原有 Task，已启动的 Job 不被无故终止或重复执行。
10. 保留未知故障分类机制，使未来发现的真实异常可以快速转化成新规则与回归测试。

验收重点不是增加多少测试数量，而是**新问题能否方便地登记、重放、检测和验证恢复**。

先实现一套包含典型故障与正常反例的小规模 Registry + Corpus + Replay MVP，再逐步扩充。

请尽量复用现有成熟检测算法，并清楚区分已覆盖、部分可观测和无法自动判定的故障。

所有新规则必须提供明确的正反例测试。不要把自然语言日志关键词匹配当成通用故障检测系统。

我的建议是，把这一阶段作为 Sui Regulator v0.3 的主要目标。

v0.2 已经证明了基本的 Worker 故障恢复链路；v0.3 应该着重证明 Candleholder 有能力知道什么时候该恢复、什么时候不该恢复。

这个方向比再增加几十个普通测试更贴合司岁台的核心价值，也更容易形成一个能够长期积累、由社区不断贡献故障案例的开源项目。
