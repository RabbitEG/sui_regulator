# Sui Proxy Protocol 0.1

可执行实现：`protocol.py:SuiProxyProtocol`。调用身份为 task_id、worker_id、generation、lease_token；注册时只返回一次明文 token，SQLite 只存 SHA-256。令牌不进入进程参数或审计。

| 方法 | 实际行为 |
|---|---|
| register | Controller 可信启动入口，原子注册 + 分配新 generation；不是开放给 Sui Proxy 的提权操作 |
| claim_task | 校验凭据，记录进程身份，进入 RUNNING |
| get_task_context | 查询完整任务书、追加指令首个有界页、历史引用、近期回执、Handoff、Job、可用模板与本代完成协议 |
| get_task_records | collection、after、limit、可选 record_id；稳定游标读取 instructions / attempts / jobs / proxies / artifacts / events 的原始记录 |
| acknowledge_instructions | command_ids（1–50）；本代已提供指令的幂等已读声明，不声称执行 |
| wait_task | reason、check_after_seconds（1–604800 秒）、可选 job_id；保留 Task，释放当前代并登记可唤醒等待 |
| heartbeat | Wrapper 自动续租；单纯心跳不算有效任务活动 |
| report_progress | 版本化声明，保存 phase/summary/job_ids/next_action/blockers 与审计；不更新客观 Job 状态 |
| report_blocker | WAITING_USER/RESOURCE/EXTERNAL，区别正常等待与卡死 |
| launch_job | 先登记独立 Experiment/Job；稳定 task + logical_key 去重，spec 改变时报冲突 |
| cancel_job | 显式请求结束本地合成 Job；Runner 执行前再次校验当前 lease/generation |
| attach_job | 验证任务归属，关联既有 Job，不启动第二份作业 |
| publish_artifact | 只登记受管 workspace 内现有文件，来源为 worker-declared |
| handoff | 保存有界结构化交接；缺失时仍可从账本恢复 |
| run_command | 执行部署者批准的固定模板，持久记录意图、所有权检查和真实结果；可选 operation_id 对同一次逻辑请求重送返回原回执，有意的新尝试应换 ID |
| complete_task | 提交总结与证据，执行可选核验模板，写入完成标记并通过同一完成验收入口 |

写操作在 BEGIN IMMEDIATE 内验证 desired、current_worker、generation、lease expiry、token hash 与 Sui Proxy 状态。Runner 在独立执行进程中再次验证已登记授权。Sui Proxy 被接管后，旧请求返回 FENCED 并有拒绝审计。已经 RUNNING 的 Job 不依赖旧 Sui Proxy 后续续租。

MCP/机器 Gateway 的具体调用见 YAN_API.md（随 Gateway 阶段补齐）。首轮的 bootstrap 注册只授权 Controller；Sui Proxy 无权自行抢占或提高 generation。

同 UID 的任意 shell 或数据库写权限可以绕过协议，因此本地进程权限不是恶意 Sui Proxy 的完整沙箱。Claude Adapter 的 managed 模式提供 Read / Glob / Grep、受管 MCP 和批准的模板；claude_code 模式需显式选择，增加原生编辑与 Bash。托管 Runner 仍只执行合成 Job。

完成要求全部托管 Job 进入已知终态、命令没有未决副作用、本代身份和用户要求版本匹配。历史失败实验不阻止模型完成用户要求；科学结论仍是代理声明。原生文件和 complete_task 共用验收门，详见 [提示词与完成协议](PROMPT_AND_LIFECYCLE_DESIGN.md)。

历史页默认 20 条，最多 50 条；约 300 KB 记录预算会提前分页。返回 items / next_after / has_more / source；逐页读取到末尾，不能把预览缺失当成无历史。超大单记录显式报 RECORD_TOO_LARGE，不截断用户要求。instruction ACK 与完成规则、运行服务尚不支持移交后完成的限制，见 [接续协议](PROMPT_AND_LIFECYCLE_DESIGN.md#本轮接续协议schema-7)。

## 自主核验（Schema 8）

`reconcile_operation(kind, operation_id, evidence)` 仅对操作员授予 trusted 的 Task 开放。kind 是 attempt/job，作用域为当前有效代理所属任务。它将核验记录为 proxy-declared，保留原始 UNKNOWN 等状态，不伪造执行成功；迟到运行观察发生变化后须重新核验。核验可供完成与等待协议使用，不能把已知 RUNNING 工作直接抹为终态。[参数、限制与示例](TRUSTED_AUTONOMY.md#代理核验与完成)。
