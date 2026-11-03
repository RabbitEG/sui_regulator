# Sui Proxy Protocol 0.1

可执行实现：`protocol.py:SuiProxyProtocol`。调用身份为 task_id、worker_id、generation、lease_token；注册时只返回一次明文 token，SQLite 只存 SHA-256。令牌不进入进程参数或审计。

| 方法 | 实际行为 |
|---|---|
| register | Controller 可信启动入口，原子注册 + 分配新 generation；不是开放给 Sui Proxy 的提权操作 |
| claim_task | 校验凭据，记录进程身份，进入 RUNNING |
| get_task_context | 查询持久 Task、最近指令、Event、Handoff、Job、Artifact |
| heartbeat | Wrapper 自动续租；单纯心跳不算有效任务活动 |
| report_progress | 版本化声明，保存 phase/summary/job_ids/next_action/blockers 与审计；不更新客观 Job 状态 |
| report_blocker | WAITING_USER/RESOURCE/EXTERNAL，区别正常等待与卡死 |
| launch_job | 先登记独立 Experiment/Job；稳定 task + logical_key 去重，spec 改变时报冲突 |
| cancel_job | 显式请求结束本地合成 Job；Runner 执行前再次校验当前 lease/generation |
| attach_job | 验证任务归属，关联既有 Job，不启动第二份作业 |
| publish_artifact | 只登记受管 workspace 内现有文件，来源为 worker-declared |
| handoff | 保存有界结构化交接；缺失时仍可从账本恢复 |
| complete_task | 所有关联 Job 须被 Runner 观察为 COMPLETED；语义结论仍属 Sui Proxy 声明 |

写操作在 BEGIN IMMEDIATE 内验证 desired、current_worker、generation、lease expiry、token hash 与 Sui Proxy 状态。Runner 在独立执行进程中再次验证已登记授权。Sui Proxy 被接管后，旧请求返回 FENCED 并有拒绝审计。已经 RUNNING 的 Job 不依赖旧 Sui Proxy 后续续租。

MCP/机器 Gateway 的具体调用见 YAN_API.md（随 Gateway 阶段补齐）。首轮的 bootstrap 注册只授权 Controller；Sui Proxy 无权自行抢占或提高 generation。

同 UID 的任意 shell 或数据库写权限可以绕过协议，因此本地进程权限不是恶意 Sui Proxy 的完整沙箱。Claude Adapter 默认只提供受管 MCP 工具，实验执行端只运行合成 Job。
