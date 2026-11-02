# Incident Corpus

每个 JSONL 文件第一行是 `{ "case": ... }` 标签与期望，后续每行是版本 0.1 的 Observation：time、kind、data、source；也可用 time + wire 保存合成的 Claude JSON 事件，通过生产使用的同一 Normalizer 解码。Replay 不将标签传给检测器，不需要睡眠、模型、数据库或进程。

```bash
PYTHONPATH=src python3.11 -m sui_regulator failures list
PYTHONPATH=src python3.11 -m sui_regulator failures replay tests/failure_corpus/positive/generation.jsonl
PYTHONPATH=src python3.11 -m sui_regulator failures evaluate tests/failure_corpus --summary
```

标签包含 `id`、positive/negative、合成或公开报告改编来源、期望 rule_id/action、故障 onset、最大检测延迟和禁止动作。negative 必须完全无 Incident；不能把“没有重启”代替“没有误报”。延迟依据案例虚拟时间计算，不是本机测试耗时。

新增案例时：使用全新合成 ID/文本；注明来源但不复制真实 Session、用户名、凭据或业务数据；为关联规则配置正反例；运行 replay 与全库 evaluate。新增规则可复用现有算法，无需修改 Controller；新算法须有独立代码审查和正反例。

当前集合为开发/回归集，不是独立随机测试集。13 条规则、23 条故障案例与 17 条正常案例的分数只描述这组测试。环境错误的反复失败不得被升级成模型循环；正常轮询 500 次、批量成功写入、日志中的错误示例、用户要求重复输出和训练等待均须保持安静。已成功恢复的零星错误仍可告警，但不得累计成重启或隔离。

`offline_policy_match_rate` 是建议动作匹配，`executed_recovery_correctness` 在离线报告中为 null；真实接管正确性必须另外运行专用进程故障注入测试。
