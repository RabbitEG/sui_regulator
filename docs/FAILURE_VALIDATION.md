# v0.3 故障检测与安全恢复验收

已交付可安装的 Failure Registry、确定性 Detection Engine、Incident Corpus、离线 Replay，以及 Wrapper → 持久 Incident → Candleholder 的分级恢复链路。本轮没有扩大科研任务执行能力，也没有使用真实模型凭据。

## 验收结果

| 验证 | 结果 | 证据 |
|---|---|---|
| 版本化规则 | 13 条，全部有正反例；调整阈值无需修改 Controller | `src/sui_regulator/failures/patterns/` |
| 离线案例 | 24 条故障、18 条正常，共 42 条 | `tests/failure_corpus/` |
| 案例召回率 | 23/23 = 100% | `failure-corpus-report.json` |
| 正常案例误报率 | 0/17 = 0% | 同上，包括 500 次相同成功轮询 |
| 离线建议动作匹配 | 23/23 = 100% | 同上；不代表实际接管成功率 |
| Python 3.11 | 77 项通过，包含真实 CLI 的 4 项本地假 API 测试 | `failure-test-output.txt` |
| Python 3.12 | 73 项通过，4 项 CLI 测试明确跳过 | `failure-verification.json` |
| 构建与安装 | v0.3.0 wheel、随包规则/许可证、安装后的 CLI 演示与离线评估 | 同上 |

这是开发/回归集，不是随机抽样或独立留出的测试集。100% 与 0% 只描述这些案例，**不估计真实 GLM 的召回率或误报率**。其中报告改编案例只复现故障机制，文本与 ID 都是重新合成的，没有原始客户 Session。

指标分别计算：case_recall 要求案例中每个期望规则都产生 Incident，不要求动作相同；false_positive_rate 是产生任何 Incident 的正常案例比例；offline_policy_match_rate 要求期望动作出现且没有禁止动作。每条期望另检查动作是否在 max_delay 内产生，超时会使整体 passed=false。测试特意改变恢复动作，验证召回率与策略匹配不会混为一项。

## 检测延迟

报告的 `per_rule.delays` 是从案例标注的 onset 到该规则首次 Incident 的虚拟秒数；`action_delays` 是到期望动作的虚拟秒数。离线回放不睡眠，因此这些数值不是运行耗时、吞吐量或真实平台延迟。

典型案例：流式单词循环在 0.1 秒告警、0.3 秒建议新 Session；字符循环在积累到足够样本后约 0.3 秒检出；跨轮重复在第 4 条相似消息告警、第 6 条升级；连续相同失败工具结果在第 3 次告警、第 5 次隔离；期待响应但无流事件在 30 秒探测、60 秒建议新 Session；无进展样本在首次超过 120 秒的健康观察时探测。

这些阈值是明确、可调整的初始配置。观察采样间隔决定实际触发时刻，故障可能在足够证据形成之前已开始。置信分数是启发式门槛，不是校准的概率。

## 真实 CLI 与实际进程验证

安装版本为 Claude Code 2.1.316。真实二进制连接 `127.0.0.1` 的合成 Messages/SSE 服务，使用合成 key，清除测试进程代理变量；未调用真实模型服务。

- 能力探针：在后端结束响应前收到 `stream_event / text_delta`。
- 故障注入：同一个生产 Wrapper 在后端仍生成重复内容时写入 `GEN-001`，Candleholder 已应用 fresh_session；证据 signal=text_delta，没有等待完整 assistant/result。
- Worker 查询的 `stream_observation` 初始为 event-only，真正收到 text_delta 后才变为 text-delta-observed，并有 `WORKER_STREAM_CAPABILITY` 审计。文本片段不等于 tokenizer token，工具参数 delta 也不冒充文本 delta。
- 实际 CLI 的 MCP 正常闭环与 401 隔离继续通过。

另用专用 CLI 模拟进程与独立合成 Runner 验证恢复副作用：

| 注入场景 | 实际结果 |
|---|---|
| 流式退化，旧 Session 尚未输出完 | 提前中断；新 generation 接管原 Task，Job ID/PID 不变，只有一次 execution，最终完成 |
| 结构化 EROFS | Task WAITING，不能直接重新领取；原 Job 继续完成；明确 resume 后新 Session 完成 Task，无第二次执行 |
| 未知致命异常 | UNKNOWN_ANOMALY + 隔离；不不断重启，原 Job 完成一次 |
| 活动输出存在、协议/Job 无进展 | 只读探测，原 Worker 所有权不变 |
| 旧 generation Incident | STALE，不撤销新 Worker |
| 同一 Incident 重发、Controller 重启 | 不重复应用；规则预算保持，耗尽则隔离 |
| Job 结果 UNKNOWN | 阻止自动换 Session，不重放副作用 |
| 用户取消先落盘 | 恢复被忽略，持久取消意图不被覆盖 |

这是一组关键场景的断言验证，不是从真实故障总体抽样得到的“恢复正确率”。离线报告将 executed_recovery_correctness 保持为 null；实际执行证据由这些进程测试独立提供。原有 Controller SIGKILL、冻结持 SQLite 写锁 Worker、启动回执丢失与 fencing 测试也继续通过。

## 覆盖边界

| 类别 | 已覆盖 | 部分可观测或不能自动判定 |
|---|---|---|
| Transport | 结构化错误、有限重试、EOF 截断、期待响应时静默，包括 init 前静默 | 未公开的服务端内部原因；工具正在运行时不凭缺少模型 delta 判死 |
| Process | 已登记 PID 消失、lease 超时；专用冻结进程恢复 | 任意外部 IPC/工具挂起只能结合心跳、进展与探测，不能准确归因 |
| Generation | 有界词/字符/句段/n-gram 重复、跨消息重复；公开的 thinking 块 | 隐藏推理、非重复但无意义的内容、幻觉、科研质量无法确定性评判；合法的长篇重复文本仍需真实样本校准 |
| Tool Use | 空 Bash、非法结构/JSON、相似调用的相同失败结果、partial/full 去重 | 默认 MCP 不开放 Bash，空 Bash 用结构化合成轨迹验证；没有执行真实 Shell；文字中的伪造工具帧只当数据 |
| Session State | UUID 冲突、显式 compaction_error | 普通 compact 通知不等于失败；未公开的上下文损坏与语义退化无法自动判定 |
| Progress | 完成声明与 Job 冲突；协议报告/Job 变化长期缺失时安全探测 | 协议声明仍属未核实信息；持续变换说法不证明实际进展，不能据此自动归因为模型故障 |
| Environment | ENOSPC/EROFS/权限/GPU 不可用/数据库锁等明确结构化信号进入等待 | 不读取自然语言日志关键词猜环境；未明确编码的错误先保持未知或工具原因未确认 |
| Unknown | 结构化未知异常先探测、持续/致命则隔离 | 没有任何外部可观测信号的故障无法保证发现 |

子 Agent 的 parent_tool_use_id 事件暂不参与根 Session 的工具关联，避免跨作用域误计数；尚未验证并行子 Agent 的完整恢复。当前是单 UID、本地 SQLite/单 Controller。企业 GLM、真实训练、GPU/NPU、远程 Runner 和容器重建均待真实平台验证，原有安全边界见 SECURITY.md。

## 复现与接续

```bash
PYTHONPATH=src python3.11 -m sui_regulator failures list
PYTHONPATH=src python3.11 -m sui_regulator failures evaluate tests/failure_corpus --summary
PYTHONPATH=src SUI_TEST_REAL_CLAUDE=1 python3.11 -m unittest discover -s tests -v
python3.11 scripts/export_schemas.py --check
```

真实平台先固定 CLI 并运行本地合约测试，再在批准的端点上核验 delta、工具、错误及重试格式。通过 `yan.get_incidents` 和 `yan.get_events` 收集脱敏结构化证据，将新异常登记为带来源的规则/正反例，然后重放回归。贡献步骤见 FAILURE_CONTRIBUTING.md；恢复策略见 RECOVERY_POLICY.md。
