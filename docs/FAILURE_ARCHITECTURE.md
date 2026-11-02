# v0.3 Failure Registry & Recovery Validation

状态：实施方案，完成证据记录于 PROGRESS.json 和后续验收报告。

```text
CLI bytes → bounded JSON decoder → normalized Observation
                                    ↓
versioned JSON rules → DetectionEngine ← health / protocol / runner facts
                                    ↓
                       structured Incident + evidence
                                    ↓
                      durable ledger → recovery policy
                                    ↓
          observe / warn / probe / bounded retry / fresh / wait / quarantine

JSONL corpus → same engine → replay evaluation (no sleeps, no processes)
```

规则放在可随 wheel 安装的 `sui_regulator.failures.patterns`，使用 JSON 以保持标准库运行。固定算法分发表，新增阈值/案例无需改 Controller；新增算法需要明确代码与测试，不加载任意插件。

Incident 包含 schema/rule 版本、类别、观察来源、症状、推测原因及不确定性、证据、置信分数、建议动作、发现时间、稳定去重键和 Task/Worker/generation 上下文。观察来源不等于根因。原始聊天与凭据不进审计。

增量检测使用实际 stream_event 的文本窗口；完整事件检测处理工具调用与结果、跨消息行为。CLI 随后输出的完整 assistant 消息不能把已观察的 delta 再计为第二次生成。thinking 未公开时不宣称可检测。缺失结果、截断、静默由 EOF 和虚拟/实时 clock 事件补充。

恢复由 Candleholder 根据持久 Incident 执行，不由模型输出自然语言触发。所有动作保留 Task 和已开始 Job，不重放工具；受管旧 generation 的 Incident 不能影响新 Worker。环境故障进入等待、未知/证据不足优先探测或隔离。有限重试仅允许已有 CLI 的有界传输重试或延后新 Session，不盲目 Continue。每条规则的恢复预算和全局预算同时生效。

Replay 共用生产引擎，JSONL 中时间为显式虚拟时间。案例 manifest 记录合成/报告改编来源、onset、期望规则/动作、最大延迟与禁止动作。评价分别给出规则召回率、负例误报、延迟、策略匹配；真实进程接管正确率由故障注入测试独立给出，不能用离线策略匹配冒充执行成功。

阶段：A 源码调查与增量能力验证；B Registry/Corpus/Replay MVP；C 接入 Wrapper、持久 Incident 与 Candleholder 分级恢复；D 正反例、真实 CLI 流式故障与恢复验收、打包和文档。每阶段验证后 commit + push，Author 为 GPT-6.1 Sol。
