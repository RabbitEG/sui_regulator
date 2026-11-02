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
| yan.get_job / yan.get_metrics | job_id |
| yan.get_logs | job_id，limit（1–500，默认 50） |
| yan.get_experiment | experiment_id |
| yan.get_command_status | command_id |
| yan.get_events | 可选 task_id、after（Event ID）、limit（1–1000） |
| yan.get_incidents | task_id、可选 after（sequence 游标，默认 0）、limit（1–1000，默认 100） |

Job/指标查询返回 source、observed_at、stale；Task 包含三个状态来源。日志只是数据，不作为控制指令。没有数据时不伪造观测时间。

v0.3 的 get_task 还包含最近 20 条 incidents，完整历史用 get_incidents 分页。Incident payload 保存规则建议及版本，action/status/outcome 保存 Candleholder 最终处理结果，可区分发现、只读探测、恢复、隔离及过时告警。环境故障的 WAITING 与隔离状态都可通过 resume_task 明确解除；运行 Job 保留。

## 指令

`yan.submit_task / instruct_task / request_analysis / hold_task / resume_task / cancel_task` 的 params 包含 idempotency_key、payload，后五者另有 task_id。提交 payload 为 title、可选 objective、adapter（mock/claude/unavailable）、合成 spec；追加/分析 payload 为 instruction；其余 payload 为 {}。不接收任意启动命令、shell 或远程地址。

接收返回 QUEUED Command（含预分配 task_id）。Controller 在纯数据库事务中完成操作，然后回执 SUCCEEDED/REJECTED。`request_analysis` 的成功表示分析要求已记录，`analysis_executed:false`，不伪称模型已完成分析。参数不合法的已接受请求会有 REJECTED 回执；幂等键冲突立即报 IDEMPOTENCY_CONFLICT。查询无需可用 LLM。

HOLD 停止 Worker、保留运行 Job；CANCEL 是持久终态，同样保留运行 Job。`yan.cancel_job` 需 task_id 和 payload.job_id，是单独的合成 Job 取消指令；Runner 检查 Command 授权后才改变客观状态。此原型不提供终止真实训练的接口。

## MCP

外部本地 AI 使用 owner 角色：

```json
{"mcpServers":{"yan":{"command":"/absolute/path/to/python3.11","args":["-m","sui_regulator.mcp","--home","/absolute/private/state","--mode","yan"],"env":{"PYTHONPATH":"/absolute/repo/src"}}}}
```

工具为 `yan_request`，arguments.request 是上述完整 Yan 请求。它继承本地 UID 权限，不是远程鉴权服务。

受管 Sui 的 MCP 启动由 Adapter 自动配置，默认 worker 角色，只公开 Sui Protocol 工具；没有 register 或 Yan 管理能力。Worker 凭据仅经私有进程输入/环境传递，不进入模型提示词或命令行。

MCP 实现支持 stdio JSON-RPC、initialize、ping、tools/list、tools/call 以及初始化通知；没有宣称支持 MCP 全套扩展或远程传输。Schema 见 `schemas/` 和 `yan schema`。
