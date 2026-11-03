# 数据模型

Schema v7（v1→v2 增加取消授权快照；v2→v3 增加 incidents；v4–v7 增量见下文），使用 SQLite WAL、foreign_keys、FULL synchronous 与 BEGIN IMMEDIATE。`PRAGMA user_version` 驱动逐版本事务迁移；拒绝打开未来版本。禁止直接复制运行中的主数据库文件作为备份，使用 `yan backup` 的 SQLite backup API。

| 表 | 主键/约束 | 含义 |
|---|---|---|
| tasks | id；generation 单调递增 | 目标、desired、status、workspace、declared、恢复冷却 |
| commands | id；UNIQUE(actor,idempotency_key) | 原始请求、参数摘要、队列状态、执行回执、拒绝原因 |
| workers | id；UNIQUE(task_id,generation)，session UUID | Sui Proxy Session + lease hash/expiry、进程身份、心跳、活动、Handoff |
| experiments | id → task | 不随 Sui Proxy 变化的实验定义 |
| jobs | id；UNIQUE(task_id,logical_key) | 具体一次运行、启动/取消授权快照、独立进程、observed、退出码 |
| executions | job_id 唯一 | Runner 已开始执行的持久去重记录 |
| artifacts | id；UNIQUE(task_id,path) | 路径、来源、种类；Sui Proxy 声明与 Runner 产物有来源区别 |
| events | 自增 id，task 索引 | 结构化审计，支持 after/limit 游标 |
| incidents | id 唯一，task/worker 外键，generation | 规则版本、脱敏证据、建议动作、最终动作、处理状态与回执 |

Sui Proxy Lease 合并在 workers 中，以 lease hash、到期时间及 Task current_worker/generation 实现。首轮 Metric 保存在 Job 最新观测 JSON；历史合成日志与结果落在私有 Job 目录，不自建指标平台。Approval 尚未实现：首轮接口不提供真实 shell/真实训练终止等需要审批的能力。

Task desired：ACTIVE/HOLD/CANCELLED/COMPLETED；执行 status 另有 READY/RUNNING/WAITING/QUARANTINED/RETRY_BACKOFF/WAITING_RESOURCE。WAITING 支持代理主动登记的持久等待；没有 wait_state 的旧 WAITING 继续按旧语义处理；WAITING_RESOURCE 用于需要修复凭据或能力配置的暂停，RETRY_BACKOFF 表示等待下一次运行恢复。普通实验 / 工具错误不暂停 Session。终态任务不能 resume。HOLD/CANCEL 撤销 Sui Proxy lease，但保留运行 Job。

Incident：PENDING → OBSERVED/APPLIED/STALE/IGNORED。payload 是不可变的规则建议；action/outcome 记录最终决策和原因。created_at/applied_at 为数据库墙钟；payload 的 onset_at/detected_at 在离线重放中使用轨迹的显式时间。置信分数是确定性启发式阈值，不是经实测校准的概率。原始聊天、完整工具参数和凭据不写入 Incident。

Command：QUEUED → SUCCEEDED 或 REJECTED。本地纯数据库命令在一个事务中完成，外部看不到半个执行结果；预留 EXECUTING/FAILED/UNKNOWN 给后续远端操作。SUCCEEDED 只代表该命令定义的效果，例如「指令已记录」，不代表模型已执行分析。

Job：CREATED/QUEUED/STARTING/RUNNING/COMPLETED/FAILED/CANCELLED/BLOCKED/UNKNOWN。UNKNOWN 必须保留，不能等同 FAILED。已经开始的物理执行不能因 Sui Proxy 更替而重新 dispatch。

时间为 UTC Unix 秒，查询返回 queried_at、observed_at、source 和 stale。终态观测是历史结果，不标为实时运行观测。声明内容绝不直接改写 Job 的客观状态。

## Schema 4 / 5 增量

Task 新增 execution_profile、completion_criteria、verification_template、recovery_mode、recovery_state 和 max_proxy_seconds。tool_attempts 保存跨代操作身份、方案 / 结果指纹与 STARTED、FAILED、SUCCEEDED、UNKNOWN、RESOLVED 等回执状态。旧任务迁移为 managed + bounded，用户须显式选择扩大能力或切换策略。原生 Claude Code 可以执行真实项目命令，其权限和托管 Job 边界见 [任务书](TASK_BRIEFS.md)。

Schema 5 还包含 Task completion、workers.handoff_at 与 completion_receipts，记录结构化完成证据、交接请求之后的明确交接时间和完成处理结果。

## Schema 6 / 7 增量

- v6：tool_attempts.runtime 保存观察来源、Session、调用、后台 handle 和原始通知原因。旧 UNKNOWN 不擅自提升为 RUNNING。
- v7：tool_attempts.result 保存可重送的完整命令结果；稳定 operation_id 派生持久 attempt ID，冲突请求拒绝，未决结果禁止自动重放。
- v7：tasks.wait_state 保存等待原因、对象、下次检查、责任方及唤醒状态；沿用 next_start_at 和现有 WAITING，无额外调度服务。
- v7：workers.requirements_revision 和 instruction_deliveries（command_id / worker_id 复合主键、offered_at / channel / acknowledged_at）绑定当前代要求与明确已读声明。它不记录虚构的“已执行”。

操作状态包括 STARTED（已记录意图、回执未明确）、RUNNING（已获得后台运行句柄）、UNKNOWN（结果不能确认）、SUCCEEDED / FAILED / STOPPED（有终态观察）和 RESOLVED（操作员已登记核验）。工具失败不能推出 Session 失败。数据库备份、旧程序拒绝新 schema 和匹配备份回滚见 [部署指南](DEPLOYMENT.md#9-备份升级回滚和完整停机)。
