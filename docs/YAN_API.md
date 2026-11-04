# Yan API 0.1

首轮提供本地 JSON CLI、Unix socket RPC 与 MCP stdio。没有默认 TCP/公网监听。Socket 使用状态目录 0700、socket 0600、Linux SO_PEERCRED 同 UID 校验。Command actor 由传输生成 `uid:<uid>`，请求不能指定 actor。

## 启动与调用

```bash
export PYTHONPATH="$PWD/src"
python3.11 -m sui_regulator --home .sui serve
# 在另一终端，同一个目录与 UID：
python3.11 -m sui_regulator --home .sui rpc yan.submit_task --params '{"idempotency_key":"trial-1","payload":{"title":"synthetic trial","spec":{"steps":50,"interval":0.1}}}'
python3.11 -m sui_regulator --home .sui rpc yan.list_tasks
python3.11 -m sui_regulator schema
```

```json
{"id":"request-1","protocol_version":"0.1","method":"yan.get_task","params":{"task_id":"task-..."}}
```

响应带相同 id、protocol_version，以及 result 或 error `{code,message}`。每条连接处理一个有换行的 JSON 请求，输入上限 256 KiB；CLI 客户端有超时和输出上限。Events/日志有 limit；Task 查询本身会包含全部关联实体，大任务应使用专门查询并在后续版本增加完整分页。

## 查询

| 方法 | params |
|---|---|
| yan.get_overview / yan.list_tasks | {} |
| yan.get_task / yan.get_worker_health / yan.list_artifacts / yan.list_experiments | task_id |
| yan.get_task_records | task_id、collection（instructions/attempts/jobs/proxies/artifacts/events）；可选 after（稳定 rowid 游标，默认 0）、limit（1–50）、record_id（精确查询同一 Task 的旧记录） |
| yan.get_job / yan.get_metrics | job_id |
| yan.get_logs | job_id，limit（1–500，默认 50） |
| yan.get_experiment | experiment_id |
| yan.get_command_status | command_id |
| yan.get_events | 可选 task_id、after（Event ID）、limit（1–1000） |
| yan.get_incidents | task_id、可选 proxy_id（只查本任务指定代理）、after（sequence 游标，默认 0）、limit（1–1000，默认 100） |
| yan.get_console_snapshot | 可选 section（overview/sessions/tasks/incidents）、offset、limit（1–100，默认 50） |
| yan.get_proxy_health | task_id；规范角色名为 Sui Proxy，get_worker_health 是兼容别名 |
| yan.get_proxy_transcript | task_id、proxy_id；可选 after（文件字节游标，默认 0）、limit（1–50，默认 40） |

Job/指标查询返回 source、observed_at、stale；Task 包含三个状态来源。日志只是数据，不作为控制指令。没有数据时不伪造观测时间。

会话查询返回 `items`、`next_after`、`has_more`、`availability`、`limited` 与原始采集时间。消息分为任务输入、助手文本、工具调用/返回和运行时事件，均为只读观测；不参与 Task 状态机或恢复上下文。`not_recorded` 表示该会话没有留存，不能回溯构造；`unreadable` 表示记录当前无法安全读取。新代理将记录保存到私有 `transcripts/<proxy_id>.jsonl`，每代最多 4 MiB，单条和单页均有限额，截断会显式标注。Mock 记录标记为合成；Claude 记录来自实际 CLI 输出，不包含思维链。Job 的 `process_alive` 和代理的 `runtime_process_alive` 是查询时按登记进程身份核验的观测，不取自模型声明。

v0.3 的 get_task 还包含最近 20 条 incidents，完整历史用 get_incidents 分页。Incident payload 保存规则建议及版本，action/status/outcome 保存 Candleholder 最终处理结果，可区分发现、只读探测、恢复、隔离及过时告警。配置缺失的 WAITING_RESOURCE、历史 WAITING 与隔离状态可通过 resume_task 明确解除；运行 Job 保留。普通工具 / 实验错误仅提供观察建议，不暂停代理。

## 指令

`yan.submit_task / instruct_task / request_analysis / hold_task / resume_task / cancel_task` 的 params 包含 idempotency_key、payload，后五者另有 task_id。提交 payload 为 title、可选 objective、adapter（mock/claude/unavailable）、合成 spec，以及 workspace、execution_profile、completion_criteria、verification_template、recovery_mode、max_proxy_seconds；字段含义与权限边界见 [任务书](TASK_BRIEFS.md)。追加/分析 payload 为 instruction；Hold / Resume / Cancel 的 payload 为 {}。Yan 不提供任意 Shell 执行端点。

`yan.set_recovery_policy` 使用 task_id、idempotency_key 和 payload；payload 可包含 recovery_mode（persistent/bounded）、max_proxy_seconds（有限非负数，0 不限），至少一项。它不会自动 Resume。`yan.resolve_attempt` 使用相同命令信封，payload 为 attempt_id、evidence；只在 Task HOLD 时允许操作员对未决命令登记已核验证据，结果为 RESOLVED，不伪装成执行成功。

接收返回 QUEUED Command（含预分配 task_id）。Controller 在纯数据库事务中完成操作，然后回执 SUCCEEDED/REJECTED。`request_analysis` 的成功表示分析要求已记录，`analysis_executed:false`，不伪称模型已完成分析。参数不合法的已接受请求会有 REJECTED 回执；幂等键冲突立即报 IDEMPOTENCY_CONFLICT。查询无需可用 LLM。

HOLD 停止 Sui Proxy、保留运行 Job；CANCEL 是持久终态，同样保留运行 Job。`yan.cancel_job` 需 task_id 和 payload.job_id，是单独的合成 Job 取消指令；Runner 检查 Command 授权后才改变客观状态。此原型不提供终止真实训练的接口。

## MCP

外部本地 AI 使用 owner 角色：

```json
{"mcpServers":{"yan":{"command":"/absolute/path/to/python3.11","args":["-m","sui_regulator.mcp","--home","/absolute/private/state","--mode","yan"],"env":{"PYTHONPATH":"/absolute/repo/src"}}}}
```

工具为 `yan_request`，arguments.request 是上述完整 Yan 请求。它继承本地 UID 权限，不是远程鉴权服务。

受管 Sui 的 MCP 启动由 Adapter 自动配置，默认 worker 角色，只公开 Sui Protocol 工具；没有 register 或 Yan 管理能力。Sui Proxy 凭据仅经私有进程输入/环境传递，不进入模型提示词或命令行。

MCP 实现支持 stdio JSON-RPC、initialize、ping、tools/list、tools/call 以及初始化通知；没有宣称支持 MCP 全套扩展或远程传输。Schema 见 `schemas/` 和 `yan schema`。

v0.4 初版新增跨任务只读分页投影；后续任务书阶段扩展 submit_task，Persistent 阶段增加上述两个最小 Yan Command。get_task 另给出 current_proxy、每个代理的 proxy_id/心跳健康和实际 execution_count；旧 workers/worker_id 字段保持兼容。Console 的 HTTP 只代理 Unix RPC，不初始化账本。详情见 [Yan Console](YAN_CONSOLE.md)。

## 指令交付与有界任务详情

`get_command_status` 对 instruct / request_analysis 额外返回 `delivery[]`：每代的 `worker_id`、`offered_at`、`channel`、`acknowledged_at`，另有 `execution="unknown"`。SUCCEEDED 仍只代表入账。投递准备不等于收到；明确 ACK 仍是代理的已读声明。历史各代的 ACK 保留，不会自动变成新代已读。

`get_task` 保留完整原始任务书，但 workers/jobs/artifacts 各最多 50、操作回执最多 100、Incident 最近 20，并有响应大小预算。`history_refs` 给出完整数量和分页方法，`outstanding_operations` 基于全账本计数；`instruction_delivery` 是当前代最近 20 条摘要，`wait_state` 是可选持久等待对象。精确旧代理详情通过 get_task_records 的 record_id 读取；Incident 继续用现有 get_incidents。Console 直接复用这些查询，不另写业务状态机。
