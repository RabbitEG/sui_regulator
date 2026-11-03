# Sui Proxy Protocol 0.1

可执行实现：`protocol.py:SuiProxyProtocol`。调用身份为 task_id、worker_id、generation、lease_token；注册时只返回一次明文 token，SQLite 只存 SHA-256。令牌不进入进程参数或审计。

| 方法 | 实际行为 |
|---|---|
| register | Controller 可信启动入口，原子注册 + 分配新 generation；不是开放给 Sui Proxy 的提权操作 |
| claim_task | 校验凭据，记录进程身份，进入 RUNNING |
| get_task_context | 查询完整任务书、全部已应用追加指令、近期回执、Handoff、Job、可用模板与本代完成协议 |
| heartbeat | Wrapper 自动续租；单纯心跳不算有效任务活动 |
| report_progress | 版本化声明，保存 phase/summary/job_ids/next_action/blockers 与审计；不更新客观 Job 状态 |
| report_blocker | WAITING_USER/RESOURCE/EXTERNAL，区别正常等待与卡死 |
| launch_job | 先登记独立 Experiment/Job；稳定 task + logical_key 去重，spec 改变时报冲突 |
| cancel_job | 显式请求结束本地合成 Job；Runner 执行前再次校验当前 lease/generation |
| attach_job | 验证任务归属，关联既有 Job，不启动第二份作业 |
| publish_artifact | 只登记受管 workspace 内现有文件，来源为 worker-declared |
| handoff | 保存有界结构化交接；缺失时仍可从账本恢复 |
| run_command | 执行部署者批准的固定模板，持久记录意图、所有权检查和真实结果 |
| complete_task | 提交总结与证据，执行可选核验模板，写入完成标记并通过同一完成验收入口 |

写操作在 BEGIN IMMEDIATE 内验证 desired、current_worker、generation、lease expiry、token hash 与 Sui Proxy 状态。Runner 在独立执行进程中再次验证已登记授权。Sui Proxy 被接管后，旧请求返回 FENCED 并有拒绝审计。已经 RUNNING 的 Job 不依赖旧 Sui Proxy 后续续租。

MCP/机器 Gateway 的具体调用见 YAN_API.md（随 Gateway 阶段补齐）。首轮的 bootstrap 注册只授权 Controller；Sui Proxy 无权自行抢占或提高 generation。

同 UID 的任意 shell 或数据库写权限可以绕过协议，因此本地进程权限不是恶意 Sui Proxy 的完整沙箱。Claude Adapter 的 managed 模式提供 Read / Glob / Grep、受管 MCP 和批准的模板；claude_code 模式需显式选择，增加原生编辑与 Bash。托管 Runner 仍只执行合成 Job。

完成要求全部托管 Job 进入已知终态、命令没有未决副作用、本代身份和用户要求版本匹配。历史失败实验不阻止模型完成用户要求；科学结论仍是代理声明。原生文件和 complete_task 共用验收门，详见 [提示词与完成协议](PROMPT_AND_LIFECYCLE_DESIGN.md)。
