# 登记、重放和验证新故障

规则在 `src/sui_regulator/failures/patterns/*.json`，随 wheel 安装；`registry.py` 校验配置，`detectors.py` 是固定算法分发表，`engine.py` 统一产生 Incident。没有任意代码插件，也不调用 LLM 做诊断。

## 新问题的最小变更

1. 从原始症状确定可观测信号及作用域。先区分模型疑似、CLI/进程、外部工具、环境与未知；不把观察来源写成已确认根因。
2. 复制最相近的规则，分配唯一 ID；修改已发布规则时增加 version。声明 signals、detector、阈值、严重程度、置信门槛、recovery、正反例、来源与可观测限制。
3. 在 `tests/failure_corpus/positive/` 加入最小故障轨迹，在 negative/ 加入容易被混淆的正常轨迹。使用全新合成文本/ID，注明公开来源；不要提交公司数据、真实 Session 或凭据。
4. 将案例路径登记进规则 fixtures。正例 manifest 必须标注该 rule_id、期望动作、onset、max_delay 与禁止动作；正常案例不得产生任何 Incident。
5. 运行单例 replay、全库 evaluate；涉及副作用的恢复策略再加专用进程验证，确认 Task/Job 保留、无重复执行、预算及人工意图优先。

```bash
PYTHONPATH=src python3.11 -m sui_regulator failures replay tests/failure_corpus/positive/generation.jsonl --summary
PYTHONPATH=src python3.11 -m sui_regulator failures evaluate tests/failure_corpus --summary
PYTHONPATH=src python3.11 -m unittest tests.test_failures tests.test_incident_recovery -v
```

每行使用 `{time,kind,data,source}` 的标准 Observation，或 `{time,wire}` 的原始合成 Claude JSON 事件。首行 case 只提供标签和验收期望，不会交给检测器。time 为显式、非负、递增秒数，重放不使用真实时钟、不启动进程。单行最多 256 KiB、每例最多 20,000 行；检测窗口和证据有上限。

## 规则与策略边界

可以复用 intra_repetition（词/字符/段落）、cross_message、malformed、failed_tools、transport、stream_silence、truncated、process_exit、session_state、progress、environment、unknown 算法。新增阈值或相同算法的新规则不需要修改 Controller；新增算法必须修改明确的分发表和校验集合并提供正反例。

恢复动作：observe、warn、probe、bounded_retry、fresh_session、wait、quarantine。所有规则必须 preserve_task=true、preserve_running_jobs=true，max_attempts 限为 1–10，文本窗口最多 65,536 字符。默认 fresh_session 的规则预算为 2，另有 Controller 全局预算和未决 Job 门槛。

bounded_retry 只容许 CLI 已有的有限传输重试；如果 CLI 已终止，最终动作可能是退避后的 fresh_session。不得自动重发工具或注入 Continue。probe 只读账本和已登记进程；环境问题优先 wait，原因不明的反复工具失败达到阈值后 quarantine。建议动作与最终动作分别保留在 Incident payload 与 action/outcome。

成功工具结果、真实用户新指令及成功响应会按算法结束对应的旧故障累计。正常成功轮询 500 次不算失败循环。跨消息检测不把同一条 partial/full assistant 当成多条；未知 parent_tool_use_id 子 Agent 事件不混入根 Session。

## 私有规则目录

离线可用 `--registry /absolute/path/to/rules` 指定 JSON 规则目录；在线在私有 config.toml 的 `[regulator]` 设置 `failure_registry = "/absolute/path/to/rules"`，然后重启 Controller 并让后续新 Worker 使用该配置。该目录替代默认集合，应保留需要的默认安全规则，不应只放一条试验规则。

`test_new_versioned_rule_can_reuse_algorithm_without_controller_changes` 演示新 ID/version/阈值直接接入。evaluate 会拒绝缺少正反例、重复 ID、缺失标注或未知算法的集合。离线 passed 证明这些案例的检测及建议动作满足期望；不能证明恢复副作用已经执行或真实平台表现相同。
