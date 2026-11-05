# Persistent 自主任务恢复

最终职责边界与提示词方案见 [长期 Agent 设计](PROMPT_AND_LIFECYCLE_DESIGN.md)：实验判断、调试循环与完成判断由模型负责；缺少运行证据更新不强制换代。

Task 生命周期、Session 生命周期与单次请求预算分别管理。新建 Task 默认 `persistent`，可在 Console、CLI 或 Yan API 选择 `bounded`；升级前已存在的 Task 保持 `bounded`，避免静默改变正在运行的恢复策略。项目与执行能力见 [完整任务书](TASK_BRIEFS.md)。

## 已实现的行为

| 情况 | Persistent 行为 |
|---|---|
| Proxy 崩溃、断流、一般未知异常 | 不设累计重启次数上限；随机指数退避，默认从 8–10 秒起，最多 300 秒 |
| 同一规则在请求窗口内触发太多恢复 | 推迟到窗口允许后重试，不因达到次数而结束 Task |
| 普通工具失败、环境错误 | 保留 Task / Proxy，写入观察提示；原检测建议和最终动作分别保留 |
| 相同命令、相同代码快照、相同错误连续 3 次 | 留下重复结果提示，继续、重试或换方案由模型决定；记录跨 Session 保存 |
| 代码变化后再测试，但错误未改善 | 允许新尝试；只记录新尝试，不推断业务进展 |
| 15 分钟没有配置范围内的运行证据更新 | 记录运行观察提示，不强制换代 |
| 60 分钟没有配置范围内的运行证据更新 | 写入 Yan 可查询的观察事件；之后每小时再提醒，不强制改变调试方式 |
| Proxy 累计运行额度用尽 | HOLD，保留独立 Job；用户提高额度后显式 Resume |
| 身份冲突 | 保留隔离保护，不盲目接管 |
| 凭据拒绝、原生工具版本不支持 | WAITING_RESOURCE，修复配置后 Resume |
| 429 / 暂时资源拒绝 | 退避恢复，不把资源暂时不足当成任务终止 |
| UNKNOWN / STARTING 的 Job | 可更换 Proxy 做诊断；禁止用新 logical_key 重复启动工作 |
| 命令已开始但结果未确认 | 阻止新的潜在副作用；允许已批准的只读诊断 |

`bounded` 保留运行故障的全局及规则恢复上限；实验 / 工具结果不触发隔离。Hold / Cancel 的持久意图始终优先。资源选择、环境处理和迁移由 Session 中的 AI 在用户授权内完成；Controller 没有新增调度器。所谓“通知 Yan”是持久事件与 Console 展示，没有擅自发送邮件、消息或外部告警。

## 配置与切换

私有 `config.toml` 的 `[regulator]` 可设置：

```toml
recovery_mode = "persistent"
recovery_backoff_base = 10.0
recovery_backoff_max = 300.0
no_progress_seconds = 900.0
escalation_seconds = 3600.0
```

`max_restarts` 的生命周期隔离只用于 Bounded；规则的窗口预算在 Persistent 中用于延迟。建议保留请求级限流：[Google SRE 对级联故障的分析](https://sre.google/sre-book/addressing-cascading-failures/)说明重试可能放大故障，采用退避与重试预算能控制请求压力；长期 Task 不必因此终止。

已有任务可在详情页点击“恢复策略”，或执行：

```bash
yan --home /absolute/private/state policy TASK_ID --recovery-mode persistent --max-proxy-seconds 86400
yan --home /absolute/private/state receipt COMMAND_ID
# 只有原 Task 已暂停时才需要显式恢复：
yan --home /absolute/private/state resume TASK_ID
```

Proxy 运行额度对 Persistent 和 Bounded 都有效。`max_proxy_seconds=0` 表示不限制，非零是所有 Generation 累计的 Proxy 会话墙钟秒数。**它不是 GPU 计算量、模型 token 或实际账单额度。** 当前没有可靠的真实 GLM 账单与远端训练资源计量接口，不提供伪造的成本硬上限。正在运行的外部实验也不会因 Proxy 额度用完被自动终止。

## 运行证据与尝试

运行证据更新来自 Runner 的实际 step 变化、已知终态，以及部署者指定的命令结果。这些事实不代表业务进展或工作价值。心跳、AI 自述、文本输出、修改文件、普通原生 Bash 成功退出均不自动当成实验进展。

命令注册表可声明：

```toml
[command.verify_tests]
argv = ["/absolute/venv/bin/python", "/absolute/verify_tests.py"]
patterns = {}
progress_kind = "test"  # none（默认）、test、check、diagnostic
read_only = true        # 仅在脚本确实只读时声明；不改变操作系统权限
```

`test` / `check` / `diagnostic` 和 verification_template 的新结果指纹可提供运行观察，非零退出码也可记录；重复完全相同结果不会持续刷新计时。部署者必须正确选择诊断模板；会变化的时间戳或无意义输出不应注册为诊断证据。此处不声称能从任意自然语言自动判断科学进展。

重复结果观察使用有界的代码 / 配置快照和实际错误指纹。最多读取 256 个相关文件、每文件 64 KiB、总计 2 MiB；跳过常见产物和依赖目录。快照变化只代表新尝试。快照范围外的修复可能无法被观测到，但不会因此阻止模型重试；未看到的变化也不会被记为观测到的运行变化。错误中的常见时间戳与地址会归一化；这不是完整语义去重。

## 原生 Claude Code 与命令回执

两种 Claude 执行配置均要求 Claude Code ≥ 2.1.316，当前实测版本为 2.1.316。两种模式均禁用用户 / 项目 / local settings 来源，但不使用 `--bare`，因为 bare 也会关闭显式的 Hooks。Sui 通过参数注入自己的 PreToolUse / PostToolUse / PostToolUseFailure：执行前检查所有权，Bash 额外检查未决操作，重复失败仅提供观察提示，执行后记录结果；Hook 失败或超时按 `onFailure: block` 阻止执行。[官方 Hook 协议](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails)说明这些回调的执行与拒绝语义。

这些 Hook 是协作运行保护，不是隔离同 UID 恶意代码的安全边界。PreToolUse 检查和原生工具实际执行之间仍有很短的竞态，已发生的外部副作用无法撤回。项目上下文可由原生 CLI 发现；不应把项目文件里的指令当成新增授权。`managed` 同样启用 Sui 显式 Hooks（不使用 bare），工具仍仅 Sui MCP 和 Read / Glob / Grep，没有新增 Bash / Edit / Write 授权。

受管命令先提交 STARTED 意图，再次 fence 后启动；最终结果持久化。原生 Bash 也有执行回执。原生 Bash 返回后台 handle 时记录 RUNNING，与提交回执丢失的 STARTED / UNKNOWN 分开。收到匹配 Session、调用与后台 handle 的 CLI `task_notification` 后自动记录 SUCCEEDED / FAILED / STOPPED；worker_restart 保留 UNKNOWN 和原始原因。迟到 Hook 和重复通知不会倒退终态。已失去运行时观测的 RUNNING 降为 UNKNOWN，旧代理仍可补充其原操作的可信终态，但不恢复执行权。它只证明该 Bash 调用已结束，不证明实验结论。缺少通知、Session 丢失或 worker_restart 都不会伪造终态。若中途崩溃，未决记录跨代保留，不能用新 Session 绕过。Console 展示最近 100 条回执；暂停任务并核验实际进程、产物和远端提交状态后，可记录人工核验：

```bash
yan --home /absolute/private/state hold TASK_ID
# 等待 Hold 回执成功，并核验外部效果。
yan --home /absolute/private/state resolve-attempt TASK_ID ATTEMPT_ID \
  --evidence '具体检查的进程身份、外部实验 ID、产物与结论'
# 核对回执，再决定是否 Resume。
```

RESOLVED 表示操作员已核验并解除未决拦截，不伪装成 Runner 或模型验证通过。不会自动重启任何实验。没有证据、Task 未 Hold、操作不属于该 Task，均拒绝处理。

## 合成验收

```bash
SUI_TEST_REAL_CLAUDE=1 PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/verify_task_brief_browser.py
```

包含连续 31 次启动失败、跨代操作回执、30 次不同修改仍失败、15/60 分钟时间推进、预算暂停、请求窗口延迟、未知 Job 防重复、未决命令人工核验及真实 Claude Code Hook 所有权检查与重复结果提示。时间推进测试修改私有测试账本的时间，不让用户等待一小时。

原有 `console-demo` 明确使用 Bounded 来保持历史故障样例；从页面创建的新任务可选择 Persistent。后台实验失败后同一 Session 自动继续的实际 CLI 路径也有回归，依据 [Claude 后台通知协议](https://code.claude.com/docs/en/agent-sdk/typescript#sdktasknotificationmessage)。所有验收使用临时私有目录、合成任务与 loopback 假 API，没有调用真实 GLM 或正式训练。当前数据库升级到 schema 7；部署与回滚须遵循 [部署指南](DEPLOYMENT.md) 的备份要求。

## 已知后台工作下的独立操作

Read / Glob / Grep、Sui 查询和部署者批准的 `read_only=true` 模板始终是诊断路径（仍受所有权与用户预算约束）。部署者确认某个固定模板与当前后台工作不冲突时，可设置 `allow_during_background = true`。这是部署配置授权，不接受模型自行声明，只放行已知 RUNNING 期间的独立模板，不允许重放相同操作，也不能越过 STARTED / UNKNOWN。任意 Bash / Edit / Write 的冲突范围无法可靠确定时保守拦截。通过原生工具产生的外部工作仍不是托管 Job。

操作阶段先迁移至 schema 6，接续阶段再至 schema 7，旧 UNKNOWN 不自动改为 RUNNING；新增 runtime 来源与身份字段。真实 CLI 完成 / 失败已覆盖，普通 stopped 与 worker_restart 的原始协议分支使用确定性回放；不能把回放声明为目标平台实测。

运行证据事件为 `RUNTIME_EVIDENCE_UPDATED`，时间为 `last_runtime_evidence_at`，间隔为 `runtime_evidence_gap_seconds`。旧 `last_verified_progress_at` / `no_progress_seconds` 仅作为兼容别名保留；旧事件也不改写。指纹缓存最多 512 项，达到上限后不新增命令指纹证据；这不是评估全部任务活动的计分器，始终不触发仅凭缺少证据的强制轮换。

## Trusted 的恢复差异

以上需要操作员解锁的未决操作规则属于 guarded 模式。Schema 8 的 [trusted 模式](TRUSTED_AUTONOMY.md) 将自主核验交给代理，不再用未决/后台记录禁用整个任务的执行能力，并对认证、能力与身份异常持续退避恢复。现有任务不会自动变为 trusted。
